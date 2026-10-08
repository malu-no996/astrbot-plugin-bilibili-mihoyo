"""社交命令 · 群成员足迹：谁在哪个群里说过话（data/zzz/group_seen.json）。

为什么需要
----------
「危局群排行 / 绝境群排行 / 防卫战群排行」要把范围限定在**同一个 Q 群**里。
OneBot（第三方）能直接拉群成员列表（get_group_member_list），但 **QQ 官方机器人
没有任何「群成员列表」接口** —— 官方适配器只封装了频道 API，群相关只有禁言 / 踢人。
所以官方协议下只能退化为「在本群里发过言的人」，这就要靠这里边收边记。

记什么
------
群号 → {用户ID: 最后一次说话的时间戳}。**不记昵称**：用户明确说了群排行先不要
QQ 群名，官方事件里也拿不到稳定的群名片。
OneBot 侧其实用不上这份记录（成员列表更全），但也一并记着，图个口径统一。

怎么落盘
--------
内存里攒着（分发器热路径，每条群消息都会碰它，绝不能每条都写盘），
距上次落盘超过 _FLUSH_SECONDS 才写一次。进程被强杀时最多丢最近这一小段，
对「谁在这个群里」这种粗略用途没有影响。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


from ..paths import zzz_path

_FILE = zzz_path("group_seen.json")

_FLUSH_SECONDS = 60          # 两次落盘之间至少隔这么久（热路径，不能每条消息都写）
_MAX_PER_GROUP = 500         # 每个群最多记这么多人（超出丢掉最久没说话的）
_MAX_AGE = 90 * 24 * 3600    # 超过这么久没说话的不再算「在这个群里」

_lock = threading.RLock()
_state: dict | None = None
_dirty = False
_last_flush = 0.0


def _load() -> dict:
    """从磁盘读；文件缺失 / 读坏 / 结构不对都按空处理（绝不抛）。"""
    try:
        raw = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    groups = raw.get("groups") if isinstance(raw, dict) else None
    groups = groups if isinstance(groups, dict) else {}
    out: dict[str, dict] = {}
    for gid, users in groups.items():
        users = users if isinstance(users, dict) else {}
        rows: dict[str, int] = {}
        for uid, ts in users.items():
            try:
                rows[str(uid)] = int(ts)
            except (TypeError, ValueError):
                continue
        if rows:
            out[str(gid)] = rows
    return {"version": 1, "groups": out}


def _get() -> dict:
    global _state
    with _lock:
        if _state is None:
            _state = _load()
        return _state


def _flush(force: bool = False) -> None:
    """把内存里的足迹落盘（默认按 _FLUSH_SECONDS 节流）。写失败只当没发生。"""
    global _dirty, _last_flush
    with _lock:
        if not _dirty:
            return
        now = time.time()
        if not force and now - _last_flush < _FLUSH_SECONDS:
            return
        st = _get()
        try:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, _FILE)
            _dirty = False
            _last_flush = now
        except OSError:
            pass            # 只是粗略足迹，写不进去不致命


def remember(group_id, user_id) -> None:
    """记一笔「这个人在这个群里说过话」。任何异常都不该影响消息处理。"""
    gid, uid = str(group_id or ""), str(user_id or "")
    if not gid or not uid or uid == "unknown":
        return
    global _dirty
    try:
        st = _get()
        with _lock:
            g = st["groups"].setdefault(gid, {})
            g[uid] = int(time.time())
            if len(g) > _MAX_PER_GROUP:                 # 群太大 → 丢掉最久没说话的
                for k in sorted(g, key=lambda k: g[k])[: len(g) - _MAX_PER_GROUP]:
                    g.pop(k, None)
            _dirty = True
        _flush()
    except Exception:                                   # noqa: BLE001
        pass            # 足迹记不上，绝不能连累消息分发


def users(group_id) -> list[str]:
    """这个群里「最近说过话」的人，按最近说话时间倒序（QQ 官方退化的候选来源）。"""
    gid = str(group_id or "")
    if not gid:
        return []
    st = _get()
    with _lock:
        g = dict(st["groups"].get(gid) or {})
    cutoff = int(time.time()) - _MAX_AGE
    rows = [(uid, ts) for uid, ts in g.items() if ts >= cutoff]
    rows.sort(key=lambda x: x[1], reverse=True)
    return [uid for uid, _ in rows]
