"""社交命令 · 群员绑定账号的「状态表」（面板「群订阅 → 群员绑定」用）。

为什么要单独一份
----------------
「群员 + 他绑的米游社账号」这张表**不是存出来的，是算出来的**：
    · 群员  = 在这个群发过言的人（seen.py 的足迹）
    · 账号  = 这个人自己扫码绑的米游社账号（core/bind.py）
每次打开面板重新算一遍，永远跟真实情况一致。

但页面上还要能「禁用 / 删除」某一行 —— 那是**管理动作**，没地方存就会刷新一下又冒出来。
所以这里只存「对某一行的态度」，不存行本身：

data/zzz/member_binds.json
    {"version": 1,
     "rows": {"<群ID>|<群员ID>|<米游社账号ID>": {"enabled": false,
                                                 "removed": true}}}

  · enabled=false → 该行显示「禁用」（同一成员的多条账号记录会一起切，见 set_enabled）
  · removed=true  → 该行从表里消失（「删除」，页面提供「恢复已删除」）

行键 `gid|member_id|account_id`：三个 ID 都不含 `|`，所以前缀 `gid|member_id|`
天然就是「这个人在这个群的全部记录」，切换状态 / 判定放行都靠它。

放行口径（dispatch 用）
----------------------
`allowed(gid, member_id)`：**没有记录 = 放行**；只有「这个人在这个群里所有记录都被
显式禁用」才拦。一条误标不会把人挡在门外（这是刻意的：禁用是管理动作，宁松勿紧）。
"""
from __future__ import annotations

import json
import os
import threading

from loguru import logger

from ..paths import zzz_path

_FILE = zzz_path("member_binds.json")

_lock = threading.RLock()
_data: dict | None = None


def key(gid, member_id, account_id) -> str:
    """一行记录的主键：群 + 群员 + 米游社账号。"""
    return f"{gid}|{member_id}|{account_id}"


def prefix(gid, member_id) -> str:
    """「这个人在这个群」的全部记录所共有的前缀。"""
    return f"{gid}|{member_id}|"


def _norm(raw) -> dict:
    rows = (raw or {}).get("rows") if isinstance(raw, dict) else None
    rows = rows if isinstance(rows, dict) else {}
    out: dict[str, dict] = {}
    for k, v in rows.items():
        if not isinstance(v, dict):
            continue
        rec = {
            "enabled": v.get("enabled") is not False,   # 缺省 = 启用
            "removed": bool(v.get("removed")),
        }
        if rec["enabled"] is not True or rec["removed"]:
            out[str(k)] = rec
    return {"version": 1, "rows": out}


def _load() -> dict:
    try:
        if _FILE.exists():
            return _norm(json.loads(_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"miyoho 群员绑定状态读取失败，按空处理：{exc}")
    return _norm({})


def _get() -> dict:
    global _data
    with _lock:
        if _data is None:
            _data = _load()
        return _data


def _save(d: dict) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_FILE)
    except OSError as exc:
        logger.warning(f"miyoho 群员绑定状态写盘失败：{exc}")
        raise


def state(row_key: str) -> dict:
    """某一行当前的状态（没设置过 → 启用、未删除）。"""
    with _lock:
        rec = (_get().get("rows") or {}).get(str(row_key)) or {}
    return {"enabled": rec.get("enabled") is not False, "removed": bool(rec.get("removed"))}


def set_enabled(keys, on: bool) -> int:
    """把这**几行**的启用状态一起设成 on，返回改动的行数。

    为什么一次传好几行：同一个人可能绑了好几个米游社账号（表里就是好几行），
    只禁其中一行会出现「一半禁用一半启用」，看图的人没法判断这人到底还能不能用。
    调用方（subscribe_routes）负责把「同一个人 + 同一个群」的全部行键一起传进来
    —— 行本身是算出来的，只有它才知道当前有哪几行。
    """
    n = 0
    with _lock:
        rows = _get().setdefault("rows", {})
        for k in keys or []:
            k = str(k or "")
            if not k or "|" not in k:
                continue
            rec = rows.get(k) or {}
            rec["enabled"] = bool(on)
            rec.setdefault("removed", False)
            rows[k] = rec
            n += 1
        if n:
            _save(_get())
    return n


def drop(row_key: str) -> bool:
    """「删除」一行：标记 removed（行本身是算出来的，删不掉，只能标记不看）。"""
    k = str(row_key or "")
    if not k or "|" not in k:
        return False
    with _lock:
        rows = _get().setdefault("rows", {})
        rec = rows.get(k) or {"enabled": True}
        rec["removed"] = True
        rows[k] = rec
        _save(_get())
    return True


def restore_all() -> int:
    """恢复全部「已删除」的行（页面上那个「恢复已删除的 N 条」按钮）。"""
    with _lock:
        rows = _get().setdefault("rows", {})
        n = 0
        for k in list(rows.keys()):
            if rows[k].get("removed"):
                rows[k]["removed"] = False
                n += 1
        if n:
            _save(_get())
    return n


def allowed(gid, member_id) -> bool:
    """该成员在这个群还能不能用米哈游功能命令（分发器用）。

    没有记录 → True（默认放行）；有记录 → 只要还有一条是启用的就放行。
    """
    g, u = str(gid or ""), str(member_id or "")
    if not g or not u:
        return True
    pre = prefix(g, u)
    with _lock:
        rows = _get().get("rows") or {}
    any_row = False
    for k, v in rows.items():
        if not k.startswith(pre):
            continue
        if v.get("removed"):
            continue
        any_row = True
        if v.get("enabled") is not False:
            return True
    return not any_row
