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
    """在线的 OneBot（aiocqhttp）平台实例（通知里「用哪个机器人发」的下拉数据源）。"""
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
        out.append({"id": pid, "name": pid})
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
