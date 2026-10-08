"""社交命令 · 群成员足迹：谁在哪个群里说过话（data/zzz/group_seen.json）。

为什么需要
----------
「危局群排行 / 绝境群排行 / 防卫战群排行」要把范围限定在**同一个 Q 群**里。
OneBot（第三方）能直接拉群成员列表（get_group_member_list），但 **QQ 官方机器人
没有任何「群成员列表」接口** —— 官方适配器只封装了频道 API，群相关只有禁言 / 踢人。
所以官方协议下只能退化为「在本群里发过言的人」，这就要靠这里边收边记。

记什么（v2 起连名字一起记）
---------------------------
    {群号: {"name": 群名（取不到就是空）,
            "members": {用户ID: {"ts": 最后说话时间戳, "name": 群昵称（取不到就是空）}}}}

**名字是 2026-10-08 加的**：面板新增的「群订阅 → 群员绑定」要显示「群名 / 群员名」，
只剩一串 openid 根本没法看。老文件（v1：`{群号: {用户ID: 时间戳}}`）读到也能用，
名字按空处理，下次落盘自动升级成 v2 —— 不用手工迁移，也不会丢人。

⚠️ QQ 官方协议下事件里**经常没有**发送者昵称（日志里长期是 `null/<openid>`），
所以名字可能一直是空的 —— 页面显示成「—」。这是平台限制，不是这里没记；
只要哪一次事件带了昵称就会被补上（同名覆盖，取最新一次非空值）。

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

from ..paths import zzz_path

_FILE = zzz_path("group_seen.json")

_FLUSH_SECONDS = 60          # 两次落盘之间至少隔这么久（热路径，不能每条消息都写）
_MAX_PER_GROUP = 500         # 每个群最多记这么多人（超出丢掉最久没说话的）
_MAX_AGE = 90 * 24 * 3600    # 超过这么久没说话的不再算「在这个群里」

_lock = threading.RLock()
_state: dict | None = None
_dirty = False
_last_flush = 0.0


def _norm_members(raw) -> dict[str, dict]:
    """{用户ID: 时间戳} / {用户ID: {"ts":…, "name":…}} → 统一成后者。"""
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for uid, val in raw.items():
        name = ""
        if isinstance(val, dict):
            ts = val.get("ts")
            name = str(val.get("name") or "")
        else:
            ts = val
        try:
            ts = int(ts)
        except (TypeError, ValueError):
            continue
        if ts:
            out[str(uid)] = {"ts": ts, "name": name}
    return out


def _load() -> dict:
    """从磁盘读；文件缺失 / 读坏 / 结构不对都按空处理（绝不抛）。

    兼容两种格式：v1 直接 `{uid: ts}`，v2 `{"name":…, "members": {uid: {…}}}`。
    """
    try:
        raw = json.loads(_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    groups = raw.get("groups") if isinstance(raw, dict) else None
    groups = groups if isinstance(groups, dict) else {}
    out: dict[str, dict] = {}
    for gid, g in groups.items():
        if not isinstance(g, dict):
            continue
        if "members" in g:                       # v2
            members = _norm_members(g.get("members"))
            gname = str(g.get("name") or "")
        else:                                    # v1（老文件）→ 群名未知
            members = _norm_members(g)
            gname = ""
        if members:
            out[str(gid)] = {"name": gname, "members": members}
    return {"version": 2, "groups": out}


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


def remember(group_id, user_id, name: str = "", group_name: str = "") -> None:
    """记一笔「这个人在这个群里说过话」（顺带记昵称 / 群名，取得到才覆盖）。

    名字只在**非空**时覆盖旧值：官方协议经常给空，别把上次记到的好名字抹掉。
    任何异常都不该影响消息处理。
    """
    gid, uid = str(group_id or ""), str(user_id or "")
    if not gid or not uid or uid == "unknown":
        return
    global _dirty
    try:
        st = _get()
        with _lock:
            g = st["groups"].setdefault(gid, {"name": "", "members": {}})
            if group_name and not g.get("name"):
                g["name"] = str(group_name)
            members = g.setdefault("members", {})
            rec = members.get(uid) or {}
            members[uid] = {
                "ts": int(time.time()),
                "name": str(name) if name else str(rec.get("name") or ""),
            }
            if len(members) > _MAX_PER_GROUP:                 # 群太大 → 丢掉最久没说话的
                for k in sorted(members, key=lambda k: members[k].get("ts") or 0)[
                    : len(members) - _MAX_PER_GROUP
                ]:
                    members.pop(k, None)
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
        g = dict((st["groups"].get(gid) or {}).get("members") or {})
    cutoff = int(time.time()) - _MAX_AGE
    rows = [(uid, int((v or {}).get("ts") or 0)) for uid, v in g.items()]
    rows = [r for r in rows if r[1] >= cutoff]
    rows.sort(key=lambda x: x[1], reverse=True)
    return [uid for uid, _ in rows]


def members(group_id) -> list[dict]:
    """这个群里「最近说过话」的人，每条 `{id, ts, name}`，按时间倒序。

    面板「群员绑定」用它圈定候选人（和 rank.py 的 `users()` 同源）。
    """
    gid = str(group_id or "")
    if not gid:
        return []
    st = _get()
    with _lock:
        g = dict((st["groups"].get(gid) or {}).get("members") or {})
    cutoff = int(time.time()) - _MAX_AGE
    rows = [
        {"id": uid, "ts": int((v or {}).get("ts") or 0), "name": str((v or {}).get("name") or "")}
        for uid, v in g.items()
    ]
    rows = [r for r in rows if r["ts"] >= cutoff]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows


def name_of(group_id) -> str:
    """这个群记下来的群名（没记到过 → 空串）。"""
    gid = str(group_id or "")
    if not gid:
        return ""
    st = _get()
    with _lock:
        return str((st["groups"].get(gid) or {}).get("name") or "")


def groups() -> list[dict]:
    """记下来过足迹的群：`{gid, name, members, last_at}`，按最近活跃倒序。"""
    st = _get()
    with _lock:
        snapshot = {gid: dict(g) for gid, g in (st["groups"] or {}).items()}
    out: list[dict] = []
    for gid, g in snapshot.items():
        mem = g.get("members") or {}
        if not mem:
            continue
        out.append(
            {
                "gid": gid,
                "name": str(g.get("name") or ""),
                "members": len(mem),
                "last_at": max((int((v or {}).get("ts") or 0) for v in mem.values()), default=0),
            }
        )
    out.sort(key=lambda r: r["last_at"], reverse=True)
    return out
