"""「群订阅」面板页的后端接口（米游社面板 → 一级分类「群订阅」）。

页面上有两块**不同**的东西（用户明确要求分开）：

  ① **订阅的群**  —— data/zzz/subscribe.json 里「哪个机器人 + 哪个群」订阅了米游社服务。
       群里发一次「订阅米哈游服务」就会写进来（见 subscribe.py）。
       这里能逐条：启用 / 停用（保留记录，只是不再放行功能命令）、退订（删掉这一条）。

  ② **群员绑定**  —— 「本群发过言的人」× 「他自己扫码绑的米游社账号」。
       两个来源都是现成的（seen.py 足迹 + core/bind.py 绑定表），本接口把它们**算**成一张表：
       群ID / 群名 / 群友ID / 群员名 / 米游社账号ID / 米游社账号名 / 绑定时间 / 状态。
       行本身不落盘，只有「禁用 / 删除」这两个态度存在 member_binds.py 里
       （不存的话刷新一下又会冒出来）。

为什么不用「社交命令配置 → 保存」那条整份覆盖的老路
--------------------------------------------------
实测事故（2026-10-08）：13:54 群里订阅成功，23:26 面板保存社交配置时前端带回来的 subs
是空的（标签页开得早、状态里没有订阅），`apply_snapshot` 把 subscribe.json 抹成
`{"services": {}}` —— 用户看到的就是「面板上一个订阅都没有」。
所以订阅的增删改一律走本文件：**服务端为准，逐条改**，任何一个操作都不会连带清空别的。

每个写操作的回包都是**整份最新总览**（和 device_routes 的 _snapshot 一个套路），
前端直接覆盖本地状态，页面永远显示服务端的真相。
"""
from __future__ import annotations

from loguru import logger

from ..core import bind
from ..core.web import body, fail, ok
from . import member_binds, seen, subscribe
from .cfg import load_cfg
from .routes import _list_instances

# 群员绑定表最多算多少行（群多、人多时别把页面和接口拖死）
_MAX_MEMBER_ROWS = 500


async def _bot_meta() -> tuple[list[str], dict[str, dict]]:
    """机器人一览：在线实例 + 「配置里出现过、此刻不在线」的，合成一份 {id: 元信息}。"""
    insts = await _list_instances()
    info: dict[str, dict] = {}
    order: list[str] = []
    for i in insts:
        pid = str(i.get("id") or "")
        if not pid or pid in info:
            continue
        order.append(pid)
        info[pid] = {
            "id": pid,
            "name": str(i.get("name") or pid),
            "protocol": str(i.get("protocol") or "onebot"),
            "online": True,
        }
    cfg = load_cfg()
    for pid in list(subscribe.snapshot().keys()) + list((cfg.get("bots") or {}).keys()):
        pid = str(pid or "")
        if pid and pid not in info:
            order.append(pid)
            info[pid] = {"id": pid, "name": pid, "protocol": "onebot", "online": False}
    return order, info


async def _overview(message: str = "") -> dict:
    """整份总览：机器人 + 订阅的群（扁平）+ 群员绑定（扁平）+ 筛选用的群列表。"""
    order, info = await _bot_meta()
    subs = subscribe.snapshot()          # {sid: [{gid, name, enabled, handler_*, bound_by, bound_at}]}

    # ---- ① 订阅的群：摊平成一行一个 ----
    group_rows: list[dict] = []
    for sid in order:
        meta = info.get(sid) or {}
        for r in subs.get(sid) or []:
            uid = str(r.get("bound_by") or "")
            group_rows.append(
                {
                    "sid": sid,
                    "bot": meta.get("name") or sid,
                    "bot_id": sid,
                    "bot_protocol": meta.get("protocol") or "onebot",
                    "bot_online": bool(meta.get("online")),
                    "gid": str(r.get("gid") or ""),
                    "name": str(r.get("name") or ""),
                    "enabled": bool(r.get("enabled", True)),
                    "handler_id": str(r.get("handler_id") or ""),
                    "handler_name": str(r.get("handler_name") or ""),
                    "bound_by": uid,
                    "bound_by_name": "",
                    "bound_at": int(r.get("bound_at") or 0),
                    # 订阅人自己绑的米游社账号（页面直接列出来，不用再点进去查）
                    "accounts": bind.accounts(uid) if uid else [],
                }
            )
    group_rows.sort(key=lambda r: (-r["bound_at"], r["sid"], r["gid"]))

    # ---- ② 群员绑定：足迹里「说过话」的人 × 他绑的账号 ----
    seen_rows = seen.groups()                                   # [{gid, name, members, last_at}]
    gname = {str(g["gid"]): str(g["name"] or "") for g in seen_rows}
    gcount = {str(g["gid"]): int(g["members"] or 0) for g in seen_rows}
    glast = {str(g["gid"]): int(g["last_at"] or 0) for g in seen_rows}
    # 每个群的成员只读一次（下面「群员名」和「订阅人是谁」都要用）
    mem_of: dict[str, list[dict]] = {}
    for gid in set(list(gcount.keys()) + [r["gid"] for r in group_rows]):
        mem_of[str(gid)] = seen.members(str(gid))
    # 订阅人昵称：他本人也在足迹里，顺手带上（官方协议拿不到昵称就空着，页面退回显示 ID）
    for r in group_rows:
        hit = next((m for m in mem_of.get(r["gid"], []) if m.get("id") == r["bound_by"]), None)
        r["bound_by_name"] = str((hit or {}).get("name") or "")
    # 群名三处兜底：订阅时记的 > 足迹里记的（OneBot 群名）
    for r in group_rows:
        if r["name"] and not gname.get(r["gid"]):
            gname[r["gid"]] = r["name"]
    # 群 → 订阅它的机器人（一个群可能被多个机器人订阅）
    bots_of: dict[str, list[str]] = {}
    for r in group_rows:
        bots_of.setdefault(r["gid"], []).append(r["bot"] or r["sid"])

    member_rows: list[dict] = []
    removed = 0
    truncated = False
    for gid in dict.fromkeys(list(gcount.keys()) + [r["gid"] for r in group_rows]):
        gid = str(gid or "")
        if not gid:
            continue
        for m in mem_of.get(gid, []):
            uid = str(m.get("id") or "")
            if not uid:
                continue
            for a in bind.accounts(uid):
                aid = str(a.get("account_id") or "")
                if not aid:
                    continue
                rk = member_binds.key(gid, uid, aid)
                st = member_binds.state(rk)
                if st.get("removed"):
                    removed += 1
                    continue
                if len(member_rows) >= _MAX_MEMBER_ROWS:
                    truncated = True
                    continue
                member_rows.append(
                    {
                        "key": rk,
                        "gid": gid,
                        "group_name": gname.get(gid) or "",
                        "group_bot": " / ".join(dict.fromkeys(bots_of.get(gid) or [])),
                        "group_subscribed": bool(bots_of.get(gid)),
                        "member_id": uid,
                        "member_name": str(m.get("name") or ""),
                        "account_id": aid,
                        "account_name": str(a.get("nickname") or ""),
                        "bound_at": int(a.get("bound_at") or 0),
                        "enabled": bool(st.get("enabled", True)),
                    }
                )
    member_rows.sort(key=lambda r: (-r["bound_at"], r["gid"], r["member_id"], r["account_id"]))

    # 群员绑定里出现过的群 + 足迹里记过的群 → 筛选下拉
    cnt: dict[str, int] = {}
    for r in member_rows:
        cnt[r["gid"]] = cnt.get(r["gid"], 0) + 1
    seen_groups = [
        {
            "gid": gid,
            "name": gname.get(gid) or "",
            "subscribed": bool(bots_of.get(gid)),
            "rows": cnt.get(gid, 0),
            "members": gcount.get(gid, 0),
            "last_at": glast.get(gid, 0),
        }
        for gid in dict.fromkeys(list(gcount.keys()) + [r["gid"] for r in group_rows])
    ]
    seen_groups.sort(key=lambda r: (-int(r["subscribed"]), -r["last_at"]))

    return {
        "bots": [dict(info[sid], groups=len(subs.get(sid) or [])) for sid in order],
        "groups": group_rows,
        "members": member_rows,
        "seen_groups": seen_groups,
        "removed": removed,
        "truncated": truncated,
        "message": message,
    }


# ---------------- 路由 ----------------


async def subscribe_overview():
    """首屏 / 重新读取：订阅的群 + 群员绑定，一份全给。"""
    return ok(**await _overview())


async def subscribe_group_enabled():
    """启用 / 停用一个订阅群。body: {sid, gid, enabled}。"""
    payload = await body()
    sid, gid = str(payload.get("sid") or ""), str(payload.get("gid") or "")
    if not sid or not gid:
        return fail("缺少 sid / gid")
    on = bool(payload.get("enabled"))
    try:
        done = subscribe.set_group_enabled(sid, gid, on)
    except OSError as exc:
        return fail(f"保存失败：{exc}", 500)
    if not done:
        return fail("这个群不在订阅列表里（可能已经被退订）")
    logger.info(f"miyoho 群订阅开关：bot={sid} group={gid} → {'启用' if on else '停用'}")
    return ok(**await _overview("已启用该群" if on else "已停用该群（记录保留）"))


async def subscribe_group_drop():
    """退订一个群（删掉这条订阅）。body: {sid, gid}。"""
    payload = await body()
    sid, gid = str(payload.get("sid") or ""), str(payload.get("gid") or "")
    if not sid or not gid:
        return fail("缺少 sid / gid")
    try:
        done = subscribe.drop_group(sid, gid)
    except OSError as exc:
        return fail(f"保存失败：{exc}", 500)
    if not done:
        return fail("这个群不在订阅列表里（可能已经被退订）")
    logger.info(f"miyoho 群退订：bot={sid} group={gid}")
    return ok(**await _overview("已退订：该群不再响应米哈游功能命令"))


async def subscribe_member_enabled():
    """启用 / 禁用群员绑定。body: {keys: [...], enabled}。

    keys 是「同一个人 + 同一个群」的全部行键（页面按当前表算好一起传）——
    只发一行的话，那人名下别的账号行还是启用状态，等于没禁掉（见 member_binds.set_enabled）。
    """
    payload = await body()
    keys = payload.get("keys")
    if not isinstance(keys, list) or not keys:
        return fail("缺少 keys")
    on = bool(payload.get("enabled"))
    try:
        n = member_binds.set_enabled(keys, on)
    except OSError as exc:
        return fail(f"保存失败：{exc}", 500)
    if not n:
        return fail("记录键不合法")
    return ok(**await _overview("已启用该群员的记录" if on else "已禁用：他在这个群发米哈游功能命令不会被响应"))


async def subscribe_member_drop():
    """删除一条群员绑定记录。body: {key}。"""
    payload = await body()
    rk = str(payload.get("key") or "")
    if not rk:
        return fail("缺少 key")
    try:
        done = member_binds.drop(rk)
    except OSError as exc:
        return fail(f"保存失败：{exc}", 500)
    if not done:
        return fail("记录键不合法")
    return ok(**await _overview("已删除这条记录（他的米游社账号本身没解绑）"))


async def subscribe_member_restore():
    """恢复全部被删掉的群员绑定记录。"""
    try:
        n = member_binds.restore_all()
    except OSError as exc:
        return fail(f"保存失败：{exc}", 500)
    return ok(**await _overview(f"已恢复 {n} 条记录" if n else "没有需要恢复的记录"))


# (suffix, method, handler, desc) —— main.py 统一注册
ROUTES = [
    ("subscribe/overview", "GET", subscribe_overview, "群订阅总览（订阅的群 + 群员绑定）"),
    ("subscribe/group/enabled", "POST", subscribe_group_enabled, "订阅群：启用/停用"),
    ("subscribe/group/drop", "POST", subscribe_group_drop, "订阅群：退订"),
    ("subscribe/member/enabled", "POST", subscribe_member_enabled, "群员绑定：启用/禁用"),
    ("subscribe/member/drop", "POST", subscribe_member_drop, "群员绑定：删除记录"),
    ("subscribe/member/restore", "POST", subscribe_member_restore, "群员绑定：恢复已删除"),
]
