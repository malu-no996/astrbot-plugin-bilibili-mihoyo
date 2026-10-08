"""AstrBot 发送适配层（替代原插件里 nonebot 的 get_bots / send_private_msg）。

原项目里「自动签到结果通知」要按 QQ 号主动发私聊，走 OneBot 的
`send_private_msg`；AstrBot 里对应的是：

    context.send_message("{platform_id}:FriendMessage:{user_id}", MessageChain)

- 平台实例列表：`context.platform_manager.get_insts()`，类型取 `inst.meta().name`
  （Platform 实例没有 .id/.type 属性，实例 ID 在 meta().id —— B 站移植时踩过）。
- 只列 **aiocqhttp**（OneBot）实例：QQ 官方机器人没法按 QQ 号主动私聊。

`bind_context(context)` 由 main.py 在插件初始化时调一次。

⚠️ **消息格式保持原样**：这里**不做**任何 markdown/纯文本转换 —— 官方机器人上
AstrBot 平台默认 `use_markdown=True`，回复就以 markdown 卡片形式发出（与用户在
AstrBot 里对官方平台的配置一致），NapCat 那边就是普通文本。不要在这里加「强制纯文本」
之类的改写。
"""
from __future__ import annotations

from typing import Any

from loguru import logger

_CONTEXT: Any = None


def bind_context(context: Any) -> None:
    global _CONTEXT
    _CONTEXT = context


def _ctx():
    if _CONTEXT is None:
        raise RuntimeError("miyoho.send 尚未绑定 AstrBot Context")
    return _CONTEXT


def _insts() -> list[Any]:
    try:
        return list(_ctx().platform_manager.get_insts() or [])
    except Exception:  # noqa: BLE001
        return []


def _inst_meta(inst: Any) -> tuple[str, str]:
    """(实例 ID, 适配器类型名)。"""
    try:
        meta = inst.meta()
        return str(meta.id or ""), str(meta.name or "")
    except Exception:  # noqa: BLE001
        cfg = getattr(inst, "config", None) or {}
        return str(cfg.get("id") or ""), ""


async def list_onebot_instances() -> list[dict]:
    """**已加载**的 OneBot（aiocqhttp）平台实例（通知里「用哪个机器人发」的下拉数据源）。

    ⚠️ `platform_manager.get_insts()` 只含**在 AstrBot 里已启用并加载**的平台 ——
    没启用的实例（比如配好但关掉的 napcat）不会出现在这里。要让用户知道
    「你配了、但当前不能用」，用 list_platform_instances() 补全原因。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for inst in _insts():
        pid, ptype = _inst_meta(inst)
        if not pid or pid in seen:
            continue
        if "aiocqhttp" not in ptype.lower():
            continue
        seen.add(pid)
        # AstrBot 没有统一「取机器人昵称」的接口，显示实例 ID 即可
        out.append({"id": pid, "name": pid, "type": ptype})
    return out


# QQ 官方（qq_official）只能按 openid 发 C2C 私聊，而页面上的通知对象是**QQ 号**；
# 官方协议不给「QQ 号 → openid」的映射（openid 只出现在它自己推来的事件里），
# 所以按 QQ 号主动私聊这件事只有 OneBot 能做。这里把原因写清楚给页面显示。
_TYPE_HINT = {
    "qq_official": "QQ 官方机器人只能按 openid 私聊，拿不到「QQ 号 → openid」，所以按 QQ 号通知只能走 OneBot",
    "qq_official_webhook": "QQ 官方（Webhook）同上：只能按 openid 私聊，无法按 QQ 号通知",
    "webchat": "网页聊天平台，发不到 QQ",
}


def list_platform_instances() -> list[dict]:
    """AstrBot 里**配置了的全部平台实例**（含未启用 / 不支持私聊的），带不可用原因。

    数据来自 `context.get_config()["platform"]`（= WebUI「平台」页那份配置）——
    没启用的实例在 `platform_manager` 里根本没有对象，只能从这里拿。
    返回 [{id, type, loaded, usable, reason}]：
      · loaded：该实例当前是否真的加载了（= 在跑）
      · usable：能不能用来按 QQ 号发私聊（只有已加载的 OneBot 能）
      · reason：不能用的原因（页面直接显示这句话）
    """
    try:
        rows = _ctx().get_config().get("platform") or []
    except Exception:  # noqa: BLE001
        return []
    loaded: set[str] = set()
    for inst in _insts():                 # 已加载的实例（只有这些才真的能发消息）
        pid, _ = _inst_meta(inst)
        if pid:
            loaded.add(pid)
    out: list[dict] = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("id") or "").strip()
        ptype = str(it.get("type") or "").strip()
        if not pid:
            continue
        live = pid in loaded
        is_onebot = "aiocqhttp" in ptype.lower()
        if live and is_onebot:
            reason = ""
        elif not it.get("enable", True):
            reason = "未在 AstrBot 里启用"
        elif not is_onebot:
            reason = _TYPE_HINT.get(ptype.lower(), f"{ptype or '该'} 适配器发不了 QQ 私聊")
        else:
            reason = "未加载（可能启动失败）"
        out.append({"id": pid, "type": ptype, "loaded": live,
                    "usable": live and is_onebot, "reason": reason})
    return out


async def send_private(platform_id: str, user_id: str, text: str) -> None:
    """用指定平台实例给某个 QQ 号发私聊文本。

    ⚠️ 必须传 **MessageChain**，不能传 `[Plain(...)]` 列表：
    `context.send_message` 会把它交给 `platform.send_by_session` →
    `AiocqhttpMessageEvent.send_message`，那边直接读 `message_chain.chain`
    （列表没有 `.chain` → AttributeError，通知永远发不出去）。

    失败会抛异常（调用方自己兜），这里不打日志 —— 让调用方决定怎么提示。
    """
    from astrbot.api.message_components import Plain
    from astrbot.core.message.message_event_result import MessageChain

    chain = MessageChain(chain=[Plain(text)])
    origin = f"{platform_id}:FriendMessage:{user_id}"
    await _ctx().send_message(origin, chain)
    logger.info(f"miyoho 私聊已发送：{origin}（{len(text)} 字）")
