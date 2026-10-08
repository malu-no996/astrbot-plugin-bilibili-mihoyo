"""抽卡记录本地持久化：data/zzz/{uid}.csv，每个游戏 uid 一个 CSV 文件。

为什么是 CSV（2026-09-30 改）：原始 json 落盘没法直接拿 Excel 看，也 diff 不出一眼的差异；
CSV 带表头、能用 Excel/WPS 直接打开筛选排序。

表头顺序见 `FIELDS`。文件用 **UTF-8 with BOM** 写（Excel 双击打开不乱码），
读取时用 `utf-8-sig`，老一辈的无 BOM 文件也能正常读。

同步策略（见 client.sync_gacha）：
  - 首次同步（文件不存在）或 force=True → **全量**翻页拉取；
  - 之后 → **增量**：以已存的最大 item id 为游标，只拉比它新的记录；
  - 每个请求处理完隔 1 秒再发下一个（GACHA_PAGE_INTERVAL，在 client.full_gacha_log 里）。

旧格式迁移：如果只有 {uid}.json 而没有 {uid}.csv，load() 会自动读老 json（不丢数据），
save() 写完 CSV 后把老 json 挪到 data/zzz/legacy/{uid}.json.bak（**不删除**）。
"""

from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path


# 项目根 data/zzz/ —— 抽卡记录是**用户数据**，不随插件走（插件可被市场卸载/重装）
from ...paths import zzz_path

DATA_DIR = zzz_path()
LEGACY_DIR = DATA_DIR / "legacy"

# CSV 表头（原始 item 的字段，外加 sync_uid 便于多角色合并到一起看）
FIELDS = [
    "id", "uid", "gacha_type", "gacha_id", "item_id", "item_type",
    "name", "rank_type", "time", "count", "lang",
]


def csv_path(uid: str) -> Path:
    return DATA_DIR / f"{uid}.csv"


def json_path(uid: str) -> Path:
    return DATA_DIR / f"{uid}.json"


def _row(item: dict) -> dict:
    """把一条原始 item 摊平成一行（缺字段补空串，值统一转字符串）。"""
    return {k: "" if item.get(k) is None else str(item.get(k)) for k in FIELDS}


def load(uid: str) -> dict | None:
    """读一个角色的存档；找不到或读不出来返回 None（CSV 优先，兼容老 json）。"""
    data = _load_csv(uid)
    if data is None:
        data = _load_json(json_path(uid))
    return data


def _load_csv(uid: str) -> dict | None:
    path = csv_path(uid)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f)]
    except (OSError, ValueError, csv.Error):
        return None
    if not rows:
        return None
    return {
        "uid": str(uid),
        "server": "prod_gf_cn",
        "updated_at": int(path.stat().st_mtime),
        "count": len(rows),
        "items": rows,
    }


def _load_json(path: Path) -> dict | None:
    """读 2026-09-30 之前的 json 存档（迁移用，读完不主动删）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    return data


def max_id(items: list) -> str:
    """items（任意顺序）里最大的 item id（增量同步的游标）。"""
    best = ""
    for it in items:
        if not isinstance(it, dict):
            continue
        iid = str(it.get("id") or "")
        if iid and (not best or id_ge(iid, best)):
            best = iid
    return best


def id_ge(a: str, b: str) -> bool:
    """比较两个 item id（正常是等长的纯数字串；长度不齐时先比位数再比字典序）。"""
    a, b = str(a), str(b)
    if a.isdigit() and b.isdigit() and len(a) != len(b):
        return len(a) > len(b)
    return a >= b


def merge(old_items: list, new_items: list) -> list:
    """按 id 去重合并，返回按 id 倒序（时间倒序）的列表。同 id 保留旧记录。"""
    seen: set[str] = set()
    out: list[dict] = []
    for it in list(old_items) + list(new_items):
        if not isinstance(it, dict):
            continue
        key = str(it.get("id") or f"{it.get('time')}|{it.get('name')}|{it.get('gacha_id')}")
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda it: str(it.get("id") or ""), reverse=True)
    return out


def save(uid: str, server: str, items: list) -> dict:
    """落盘为 CSV（items 会先去重排序）。返回落盘后的完整数据。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    items = merge([], items)

    tmp = csv_path(uid).with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for it in items:
            row = _row(it)
            if not row.get("uid"):            # 老数据可能没带 uid，补上当前角色的
                row["uid"] = str(uid)
            w.writerow(row)
    os.replace(tmp, csv_path(uid))           # 原子换文，写一半失败不会毁掉旧存档

    _retire_legacy_json(uid)

    return {
        "uid": str(uid),
        "server": str(server or "prod_gf_cn"),
        "updated_at": int(time.time()),
        "count": len(items),
        "items": items,
    }


def _retire_legacy_json(uid: str) -> None:
    """CSV 写成功后，把同名的老 json 挪到 legacy/（改名不删除，随时能捞回来）。"""
    old = json_path(uid)
    if not old.exists():
        return
    try:
        LEGACY_DIR.mkdir(parents=True, exist_ok=True)
        os.replace(old, LEGACY_DIR / f"{uid}.json.bak")
    except OSError:
        pass                                  # 挪不动就算了，不影响主流程
