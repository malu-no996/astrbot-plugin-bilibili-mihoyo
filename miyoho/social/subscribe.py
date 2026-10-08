"""社交命令 · 群订阅（「订阅米哈游服务」命令 + 面板群开关）。

「订阅米哈游服务」是一个**独立管理命令**（不在 DEFAULT_COMMANDS 功能命令表里），
群主（或 AstrBot 管理员）在群里发，把「本群」记到「处理这条命令的机器人」名下；
之后这个机器人只在该群**已订阅且开关打开**时，才响应米哈游功能命令
（危局 / 防卫战 / 抽卡 / 签到…）。私聊不受影响（扫码登录还要用）。

存储 data/zzz/subscribe.json：
    {services: {self_id(平台实例): {group_id: {enabled, group_name,
                                          handler_id, handler_name, bound_by, bound_at}}}}
—— key 用平台实例 ID（event.get_platform_id），和社交命令的 bots / bot_cmds 同口径。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from loguru import logger

from ..paths import zzz_path


_SUBS_FILE = zzz_path("subscribe.json")

# 「订阅米哈游服务」触发词（固定，不允许页面改）
SUBSCRIBE_TRIGGERS = [
    "订阅米哈游服务", "订阅米哈游", "米哈游订阅", "米哈游服务订阅",
]
# 「取消订阅米哈游服务」：取消本群订阅
UNSUBSCRIBE_TRIGGERS = [
    "取消订阅米哈游服务", "取消订阅米哈游", "米哈游退订", "退订米哈游",
]

_lock = threading.RLock()
_data: dict | None = None


def _load() -> dict:
    global _data
    try:
        if _SUBS_FILE.exists():
            d = json.loads(_SUBS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                _data = d
                return _data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"miyoho 群订阅配置读取失败：{exc}")
    _data = {"services": {}}
    return _data


def _get() -> dict:
    global _data
    with _lock:
        if _data is None:
            _data = _load()
        return _data


def _save(d: dict) -> None:
    try:
        _SUBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SUBS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_SUBS_FILE)
    except OSError as exc:
        logger.warning(f"miyoho 群订阅配置写盘失败：{exc}")
        raise


def match(head: str) -> str | None:
    """命令头 → 'sub' / 'unsub' / None。"""
    h = str(head or "").strip()
    if h in SUBSCRIBE_TRIGGERS:
        return "sub"
    if h in UNSUBSCRIBE_TRIGGERS:
        return "unsub"
    return None


def allowed(self_id: str, group_id: str) -> bool:
    """功能命令门槛：该机器人 + 该群是否已订阅且开关打开。

    私聊（group_id 空）→ True（不受限）；没记录 → False。
    """
    sid, gid = str(self_id or ""), str(group_id or "")
    if not gid:
        return True                       # 私聊不受订阅门槛限制
    if not sid:
        return False
    with _lock:
        rec = ((_get().get("services") or {}).get(sid) or {}).get(gid)
    return bool(rec and rec.get("enabled"))


def groups_of(self_id: str) -> list[dict]:
    with _lock:
        inner = (_get().get("services") or {}).get(str(self_id or "")) or {}
        return [dict(r, gid=gid) for gid, r in inner.items()]


def snapshot() -> dict:
    """面板全量：{self_id: [{gid, name, enabled, handler_id, handler_name, bound_by, bound_at}]}。"""
    with _lock:
        out: dict[str, list[dict]] = {}
        for sid, inner in (_get().get("services") or {}).items():
            lst: list[dict] = []
            for gid, r in inner.items():
                lst.append(
                    {
                        "gid": gid,
                        "name": str(r.get("group_name") or ""),
                        "enabled": bool(r.get("enabled", True)),
                        "handler_id": str(r.get("handler_id") or ""),
                        "handler_name": str(r.get("handler_name") or ""),
                        "bound_by": str(r.get("bound_by") or ""),
                        "bound_at": int(r.get("bound_at") or 0),
                    }
                )
            if lst:
                out[str(sid)] = lst
        return out


def apply_snapshot(subs: dict) -> dict:
    """面板保存：{self_id: [{gid, enabled, group_name?, handler_name?}]} → 落盘。"""
    with _lock:
        new: dict[str, dict] = {}
        for sid, lst in (subs or {}).items():
            sid = str(sid)
            inner: dict = {}
            for r in lst or []:
                if not isinstance(r, dict):
                    continue
                gid = str(r.get("gid") or "")
                if not gid:
                    continue
                inner[gid] = {
                    "enabled": bool(r.get("enabled", True)),
                    # 面板 snapshot() 出的字段名是 name；这里两种都认，避免保存时把群名抹掉
                    "group_name": str(r.get("group_name") or r.get("name") or ""),
                    "handler_id": str(r.get("handler_id") or ""),
                    "handler_name": str(r.get("handler_name") or ""),
                    "bound_by": str(r.get("bound_by") or ""),
                    "bound_at": int(r.get("bound_at") or 0),
                }
            if inner:
                new[sid] = inner
        d = {"services": new}
        try:
            _save(d)
        except OSError:
            raise
        _data = d
    return snapshot()


# ---- 从消息原始 payload 里直接读「发送者在本群的角色」 ----
#
# 各平台的消息里其实都带着发送者角色，不需要额外调接口，也不受平台限制：
#   * QQ 官方（botpy）：群消息事件的 author 里就有 member_role
#       owner=群主 / admin=管理员 / member=普通成员
#     （官方文档 api-v2 · group_at_message_create 的 User 字段）
#     ⚠️ botpy 1.2.1 的 GroupMessage._User 只解析了 member_openid，
#        **没有**把 member_role 放进对象；但 AstrBot 给官方适配器打了补丁
#        （qqofficial_platform_adapter.PatchedGroupMessage + _set_raw_message_fields），
#        把整份原始 payload 存进了 message.raw_data → author.member_role 可读。
#   * OneBot v11（aiocqhttp）：raw_message 的 sender.role（owner/admin/member）。


def _pick_role(obj: Any) -> str:
    """从 dict / 对象里取角色字段（member_role 优先，其次 role）。"""
    if obj is None:
        return ""
    if isinstance(obj, dict):
        for k in ("member_role", "role"):
            v = obj.get(k)
            if v:
                return str(v)
        return ""
    for k in ("member_role", "role"):
        v = getattr(obj, k, None)
        if v:
            return str(v)
    return ""


def _dig_role(obj: Any) -> str:
    """在「原始消息」里挖角色：先看 author/sender，再看自己（兼容不同适配器形态）。"""
    if isinstance(obj, dict):
        for key in ("author", "sender"):
            role = _pick_role(obj.get(key))
            if role:
                return role
        return _pick_role(obj)
    for attr in ("author", "sender"):
        role = _pick_role(getattr(obj, attr, None))
        if role:
            return role
    return _pick_role(obj)


def raw_role(event: Any) -> str:
    """消息自带的群内角色（owner/admin/member）；读不到返回 ""。"""
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if raw is None:
        return ""
    # QQ 官方补丁把原始 payload 存在 raw_data；OneBot 的 raw_message 本身就是事件
    return _dig_role(getattr(raw, "raw_data", None)) or _dig_role(raw)


async def _group_owner_allowed(event: Any) -> bool:
    """「订阅米哈游服务」权限：固定只能群主（owner）发。

    判定顺序：
      1) 消息自带角色（QQ 官方 member_role / OneBot sender.role）→ 必须是 owner；
      2) 角色读不到时，退回 AstrBot 管理员（机器人主人）放行 —— 防止出现
         「谁都没法订阅」的死锁；
      3) 再退回 OneBot get_group_member_info 查角色（老路子，双保险）。
    """
    role = raw_role(event)
    if role:
        logger.debug(f"miyoho 群订阅权限：消息自带角色 role={role}（仅 owner 放行）")
        return role == "owner"
    try:
        if event.is_admin():
            return True
    except Exception:  # noqa: BLE001 —— AstrBot 管理员（机器人主人）放行
        pass
    gid = str(getattr(event, "get_group_id", lambda: "")() or "")
    uid = str(getattr(event, "get_sender_id", lambda: "")() or "")
    bot = getattr(event, "bot", None)
    if bot is None or not gid.isdigit() or not uid.isdigit():
        # 没有可查群角色的机器人（官方机器人）且非 AstrBot 管理员 → 拒绝
        return False
    try:
        info = await bot.call_action(
            "get_group_member_info",
            group_id=int(gid),
            user_id=int(uid),
            no_cache=True,
        )
        return str((info or {}).get("role") or "") == "owner"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"miyoho 查询群成员角色失败（群 {gid}/{uid}）：{exc}")
        return False


async def handle_sub(event: Any, arg: str) -> str:
    """「订阅米哈游服务 [自定义群名]」：群 ID + 群名 + 处理者持久化，默认开启。"""
    try:
        gid = str(event.get_group_id() or "")
    except Exception:  # noqa: BLE001
        gid = ""
    if not gid:
        return "订阅失败：请在本群里发送这条命令（私聊无法订阅群服务）"
    try:
        sid = str(event.get_platform_id() or "")
    except Exception:  # noqa: BLE001
        sid = ""
    if not sid:
        return "订阅失败：拿不到机器人实例信息"
    # 权限：固定群主（AstrBot 管理员放行后门）
    if not await _group_owner_allowed(event):
        return "无权限：订阅米哈游服务仅限群主（或 AstrBot 管理员）使用"
    # 群名：命令尾巴可自定义（QQ 官方拿不到群名，主要靠它）；
    # 否则 OneBot 自带 group_name，都没有就退用发令人昵称
    custom_name = str(arg or "").strip()
    group_name = ""
    msg_obj = getattr(event, "message_obj", None)
    g = getattr(msg_obj, "group", None) if msg_obj is not None else None
    if g is not None:
        group_name = str(getattr(g, "group_name", "") or "")
    if not group_name:
        try:
            group_name = str(event.get_sender_name() or "")
        except Exception:  # noqa: BLE001
            group_name = ""
    if custom_name:
        group_name = custom_name
    # 处理者（处理这条命令的机器人）
    handler_id = ""
    try:
        handler_id = str(event.get_self_id() or "")
    except Exception:  # noqa: BLE001
        handler_id = ""
    handler_name = ""
    bot = getattr(event, "bot", None)
    if bot is not None and gid.isdigit():
        try:
            login = await bot.call_action("get_login_info")
            handler_name = str((login or {}).get("nickname") or "")
        except Exception:  # noqa: BLE001
            pass
    try:
        uid = str(event.get_sender_id() or "")
    except Exception:  # noqa: BLE001
        uid = ""
    with _lock:
        services = _get().get("services") or {}
        inner = services.setdefault(sid, {})
        rec = inner.get(gid)
        now = int(time.time())
        if rec:
            rec["enabled"] = True
            if group_name:
                rec["group_name"] = group_name
            if handler_id:
                rec["handler_id"] = handler_id
            if handler_name:
                rec["handler_name"] = handler_name
            if uid:
                rec["bound_by"] = uid
            rec["bound_at"] = rec.get("bound_at") or now
        else:
            inner[gid] = {
                "enabled": True,
                "group_name": group_name,
                "handler_id": handler_id,
                "handler_name": handler_name,
                "bound_by": uid,
                "bound_at": now,
            }
        try:
            _save(_get())
        except OSError:
            return "订阅失败：写盘异常"
    return (
        "已订阅米哈游服务 ✅\n"
        "本群现在可以使用米哈游功能命令（危局 / 防卫战 / 抽卡 / 签到…）。\n"
        "关闭某个群的使用权限：到面板「米哈游功能命令 → QQ机器人配置」里把对应群的开关关掉即可。"
    )


async def handle_unsub(event: Any, arg: str) -> str:
    """「取消订阅米哈游服务」：取消本群订阅。"""
    try:
        gid = str(event.get_group_id() or "")
    except Exception:  # noqa: BLE001
        gid = ""
    if not gid:
        return "取消订阅失败：请在本群里发送这条命令"
    try:
        sid = str(event.get_platform_id() or "")
    except Exception:  # noqa: BLE001
        sid = ""
    if not sid:
        return "取消订阅失败：拿不到机器人实例信息"
    if not await _group_owner_allowed(event):
        return "无权限：取消订阅仅限群主（或 AstrBot 管理员）使用"
    with _lock:
        services = _get().get("services") or {}
        inner = services.get(sid) or {}
        if gid not in inner:
            return "本群还没有订阅米哈游服务"
        del inner[gid]
        if not inner:
            services.pop(sid, None)
        try:
            _save(_get())
        except OSError:
            return "取消订阅失败：写盘异常"
    return "已取消本群的米哈游服务订阅。该群将不再响应米哈游功能命令。"
