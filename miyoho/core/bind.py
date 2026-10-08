"""QQ 用户 ↔ 米游社账号 的绑定关系。

为什么要有这份文件
------------------
`social.py` 里的查询命令（危局 / 防卫战 / 抽卡）早期一律用 `store.current_id()`
—— 那是**管理页上选中的那个账号**，全机器人共用一份。放在 QQ 里就是：
任何人发「绝区零危局」查到的都是管理员自己的账号，属于信息泄露。

现在改成「谁发命令就查谁绑定的账号」：
    QQ 用户 ID  →  这个用户自己扫码登录的米游社账号（可多个，其中一个为默认）
没有绑定的用户**查不到任何东西**（只能提示去绑定）；
管理页依旧走 `store.current_id()`，不受影响。

用户 ID 的口径
--------------
用 `plugins.libs.utils.sender_id(event)`：OneBot = QQ 号，QQ 官方 = member_openid / openid。
⚠️ QQ 官方的 openid 是**按机器人应用**分配的，同一个人在两个官方机器人里 openid 不同
（OneBot 的 QQ 号则是全局的）。所以「在 A 机器人绑定、在 B 机器人查询」在官方协议下
不会命中。真要打通得换成 unionid 或按「机器人 + 用户」存两份，目前没这个需求。

落盘
----
data/miyoushe_binds.json（用户数据放项目根 data/ 下，不进插件仓库），
写法与 store / record_store 一致：tmp + os.replace 原子替换。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from . import store


from ..paths import data_path

_FILE = data_path("miyoushe_binds.json")

_lock = threading.RLock()
_cache: dict | None = None


# ================= 一、读写 =================


def _normalize(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, dict] = {}
    users = raw.get("users") if isinstance(raw.get("users"), dict) else raw.get("binds")
    if not isinstance(users, dict):
        users = {}
    for uid, item in users.items():
        uid = str(uid)
        item = item if isinstance(item, dict) else {}
        accounts: dict[str, dict] = {}
        accs = item.get("accounts")
        if isinstance(accs, dict):                     # 新格式：{aid: {…}}
            for aid, meta in accs.items():
                meta = meta if isinstance(meta, dict) else {}
                accounts[str(aid)] = {
                    "nickname": str(meta.get("nickname") or ""),
                    "bound_at": int(meta.get("bound_at") or 0),
                    # 一个米游社账号下可能绑了多个绝区零角色，
                    # 这里记「默认查哪个」（「切换角色」命令写入，空 = 用角色列表第一个）
                    "default_role": str(meta.get("default_role") or ""),
                }
        elif isinstance(accs, list):                   # 旧格式（兼容）：[aid, …]
            for aid in accs:
                accounts[str(aid)] = {"nickname": "", "bound_at": 0, "default_role": ""}
        if not accounts:
            continue
        default = str(item.get("default") or "")
        if default not in accounts:
            default = next(iter(accounts))              # 默认指向一个真实存在的账号
        out[uid] = {"default": default, "accounts": accounts}
    return {"version": 1, "users": out}


def _load_from_disk() -> dict:
    try:
        return _normalize(json.loads(_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return _normalize({})
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"miyoushe 绑定关系读取失败，按空处理：{exc}")
        return _normalize({})


def load() -> dict:
    """读全量绑定（内存缓存，首次读盘）。"""
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load_from_disk()
        return _cache


def _write(cfg: dict) -> None:
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _FILE)


def _commit(cfg: dict) -> None:
    global _cache
    with _lock:
        _cache = cfg
        try:
            _write(cfg)
        except OSError as exc:
            logger.warning(f"miyoushe 绑定关系保存失败（内存已生效）：{exc}")


# ================= 二、查询 =================


def user(uid: str) -> dict:
    """某个用户那一份；没有绑定时返回空壳（accounts 为空）。"""
    u = str(uid or "")
    if not u:
        return {"default": "", "accounts": {}}
    return (load().get("users") or {}).get(u) or {"default": "", "accounts": {}}


def accounts(uid: str) -> list[dict]:
    """该用户绑定的账号列表（默认账号排最前，其余按绑定时间）。"""
    item = user(uid)
    accs = item.get("accounts") or {}
    default = str(item.get("default") or "")
    rows: list[dict] = []
    for aid, meta in accs.items():
        meta = meta if isinstance(meta, dict) else {}
        # 昵称以 store 里的为准（管理页改过昵称也能同步过来）
        prof = store.get(aid) or {}
        rows.append(
            {
                "account_id": str(aid),
                "nickname": str(prof.get("nickname") or meta.get("nickname") or ""),
                "bound_at": int(meta.get("bound_at") or 0),
                "is_default": str(aid) == default,
            }
        )
    rows.sort(key=lambda r: (not r["is_default"], r["bound_at"], r["account_id"]))
    return rows


def default_account(uid: str) -> str:
    """该用户默认查询的米游社 account_id；没绑定返回空串。"""
    item = user(uid)
    default = str(item.get("default") or "")
    if default and default in (item.get("accounts") or {}):
        return default
    return ""


def find(uid: str, key: str) -> str:
    """按「序号 / 昵称 / account_id」找账号，找不到返回空串。

    序号是 `米游社账号` 列表里的 1、2、3…（列表已把默认账号排在最前）。

    ⚠️ 只在这位用户**自己**的绑定里找 —— 填别人的 account_id 一定找不到，
    所以「米游社切换」「米游社解绑」不会误伤（也切不到）别人的账号。

    ⚠️ 昵称的**模糊包含**匹配（最后那圈）对「像账号 ID 的输入」直接跳过：
    有人会随手乱敲一串数字，那串数字不该因为某个昵称里恰好含它就被切/解绑。
    阈值取 5 位（米游社 account_id 是 9 位上下，短数字更可能是序号或昵称片段）。
    """
    key = str(key or "").strip()
    if not key:
        return ""
    rows = accounts(uid)
    if key.isdigit():
        idx = int(key)
        if 1 <= idx <= len(rows):
            return rows[idx - 1]["account_id"]
    for r in rows:                                     # 先精确匹配 aid / 昵称
        if key == r["account_id"] or (r["nickname"] and key == r["nickname"]):
            return r["account_id"]
    if key.isdigit() and len(key) >= 5:
        return ""                                      # 像账号 ID 的乱输入 → 不模糊匹配昵称
    for r in rows:                                     # 再退化为昵称包含匹配
        if r["nickname"] and key in r["nickname"]:
            return r["account_id"]
    return ""


def user_ids() -> list[str]:
    """所有「绑定过米游社账号」的用户 ID（按落盘顺序）。

    给管理页「详细设置 → 预览」挑一个身份用：管理页自己没有 QQ 用户的概念，
    但危局 / 防卫战 / 抽卡 / 签到这些接口全靠 `ctx.user_id` 找账号 ——
    不给一个身份，预览永远只能看到「未绑定米游社账号」。
    """
    return [str(u) for u in (load().get("users") or {})]


def role_default(uid: str, account_id: str) -> str:
    """某个米游社账号下**选定的默认绝区零角色**（game_uid）；没选过返回空串。

    空 = 用米游社返回的角色列表第一个（也就是原来的行为）。
    """
    item = user(uid)
    meta = (item.get("accounts") or {}).get(str(account_id or "")) or {}
    meta = meta if isinstance(meta, dict) else {}
    return str(meta.get("default_role") or "")


# ================= 三、变更 =================


def add(uid: str, account_id: str, nickname: str = "") -> dict:
    """绑定（或刷新）一个账号。新绑的账号自动成为默认 —— 登录完立刻能查。

    返回该用户绑定后的完整信息（{"default": aid, "accounts": {...}}）。
    """
    u = str(uid or "")
    aid = str(account_id or "")
    if not u or not aid:
        return {"default": "", "accounts": {}}
    cfg = json.loads(json.dumps(load()))                # 浅拷贝够用（下面只改一层）
    users = cfg.setdefault("users", {})
    item = users.setdefault(u, {"default": "", "accounts": {}})
    accs = item.setdefault("accounts", {})
    prev = accs.get(aid) if isinstance(accs.get(aid), dict) else {}
    accs[aid] = {
        "nickname": str(nickname or ""),
        "bound_at": int(prev.get("bound_at") or time.time()),
        "default_role": str(prev.get("default_role") or ""),   # 重新登录同一账号不清掉已选角色
    }
    item["default"] = aid                               # 刚登录的设为默认
    _commit(cfg)
    return item


def set_role_default(uid: str, account_id: str, game_uid: str) -> bool:
    """记住某个米游社账号下要默认查询的绝区零角色（「切换角色」命令写入）。

    账号不在该用户名下、或参数不全时返回 False（调用方给提示）。
    """
    u, aid, gid = str(uid or ""), str(account_id or ""), str(game_uid or "")
    if not u or not aid or not gid:
        return False
    cfg = json.loads(json.dumps(load()))
    accounts_ = ((cfg.get("users") or {}).get(u) or {}).get("accounts") or {}
    meta = accounts_.get(aid)
    if not isinstance(meta, dict):                      # 该账号不属于这个用户
        return False
    meta["default_role"] = gid
    _commit(cfg)
    return True


def set_default(uid: str, account_id: str) -> bool:
    """切换默认账号。账号不在该用户名下返回 False。"""
    u, aid = str(uid or ""), str(account_id or "")
    if not u or not aid:
        return False
    cfg = json.loads(json.dumps(load()))
    item = (cfg.get("users") or {}).get(u)
    if not item or aid not in (item.get("accounts") or {}):
        return False
    item["default"] = aid
    _commit(cfg)
    return True


def remove(uid: str, account_id: str) -> bool:
    """解绑一个账号；删掉的是默认账号时，默认自动落到剩下的第一个。"""
    u, aid = str(uid or ""), str(account_id or "")
    if not u or not aid:
        return False
    cfg = json.loads(json.dumps(load()))
    item = (cfg.get("users") or {}).get(u)
    if not item or aid not in (item.get("accounts") or {}):
        return False
    item["accounts"].pop(aid, None)
    if item.get("default") == aid:
        item["default"] = next(iter(item["accounts"]), "")
    if not item["accounts"]:
        cfg["users"].pop(u, None)                       # 该用户一个都不剩 → 整条删掉
    _commit(cfg)
    return True


def drop_account(account_id: str) -> int:
    """账号在管理页被删除时，把所有用户身上的这条绑定一并清掉，返回清理条数。

    不然会出现「绑定还在、账号没了」→ 查询报一堆莫名错误。
    """
    aid = str(account_id or "")
    if not aid:
        return 0
    cfg = json.loads(json.dumps(load()))
    n = 0
    for uid, item in list((cfg.get("users") or {}).items()):
        accs = item.get("accounts") or {}
        if aid not in accs:
            continue
        accs.pop(aid, None)
        n += 1
        if item.get("default") == aid:
            item["default"] = next(iter(accs), "")
        if not accs:
            cfg["users"].pop(uid, None)
    if n:
        _commit(cfg)
    return n
