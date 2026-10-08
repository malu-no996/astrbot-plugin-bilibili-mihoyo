"""AstrBot 发送适配层（替代原插件里 nonebot 的 get_bots / send_private_msg）。

原项目里「自动签到结果通知」要按 QQ 号主动发私聊，走 OneBot 的
`send_private_msg`；AstrBot 里对应的是：

    context.send_message("{platform_id}:FriendMessage:{user_id}", MessageChain)

- 平台实例列表：`context.platform_manager.get_insts()`，类型取 `inst.meta().name`
  （Platform 实例没有 .id/.type 属性，实例 ID 在 meta().id —— B 站移植时踩过）。
- 只列 **aiocqhttp**（OneBot）实例：QQ 官方机器人没法按 QQ 号主动私聊。

`bind_context(context)` 由 main.py 在插件初始化时调一次。

另外这里是**所有出站文本的统一收口**（见下面「官方机器人 → 强制纯文本」一段）：
插件的回复 / 主动发消息 / 通知，凡是要发出去的文本，都得先过 `as_plain()`，
免得在 QQ 官方机器人上被 AstrBot 包成 markdown。
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


# ================= 官方机器人 → 强制纯文本 =================
#
# 为什么要有这一层（踩过，别删）：
#
# AstrBot 的 QQ 官方适配器有一个**平台级**开关 `use_markdown`
# （qqofficial_platform_adapter.py: `platform_config.get("use_markdown", True)`），
# **默认 True** —— 插件 `yield` 出去的纯文本会被包成 `msg_type=2` 的 **markdown 消息**。
# 于是同一条命令，NapCat 那边是纯文本、官方机器人那边是一条 markdown 卡片。
#
# 而 markdown 的换行语义和纯文本**不同**：单个 `\n` 只是「同一段里的软换行」，
# 官方渲染出来两行会挤在一段里；行首的 `-` / `#` / `*` 还会被当语法吃掉、整行符号的
# 分隔线会变成 setext 标题下划线把上一行吞掉（后者 langstudy 那边实测过）。
# 本项目的推送类消息是**按块排版**才敢走 markdown 的（bilibili 卡片 `"\n\n".join(...)`、
# langstudy `_to_markdown_headline`），而本插件的回复是**逐行清单**
# ——菜单一行一条命令、危局一行一队、图鉴一行一件——单换行发出去会散成一片。
#
# 原项目（nonebot-qq）的文本走 `msg_type=0` 纯文本（适配器只在带 markdown/keyboard
# 段时才用 msg_type=2），所以这里显式把消息链标成纯文本：官方适配器看到
# `use_markdown_ is False` 就发 `msg_type=0`（qqofficial_message_event.py 与
# qqofficial_platform_adapter.py 各有一处同样的判断），**与迁移前行为完全一致**。
# 其它平台（aiocqhttp / webchat 等）忽略这个字段，行为不受影响 —— 所以只在官方上动它。


def is_official(platform_name: Any) -> bool:
    """平台适配器类型名 → 是不是 QQ 官方机器人（含 webhook 版）。"""
    return str(platform_name or "").strip().lower().startswith("qq_official")


def platform_name_of(event: Any) -> str:
    """事件所属平台**类型**名（如 `qq_official` / `aiocqhttp`）。

    ⚠️ 用 `get_platform_name()`（适配器类型），**不是** `get_platform_id()`
    （平台实例 ID，如 `280-Eous`）—— 后者在官方/OneBot 上都不等于类型名。
    """
    try:
        return str(event.get_platform_name() or "")
    except Exception:  # noqa: BLE001 —— 事件形态各异，取不到就当非官方
        return ""


def as_plain(chain: Any, event: Any = None, platform_name: str = "") -> Any:
    """官方平台上把消息链标成「纯文本」（就地改，原样返回）。

    `platform_name` 给了就用它，否则从 `event` 取 —— 面板预览那类没有真实事件，
    或者是「按会话主动发消息」（只有平台实例 ID）的场景，就直接传名字。
    非官方平台**一个字都不动**（保持 AstrBot 的默认行为）。
    """
    name = platform_name or platform_name_of(event)
    if not is_official(name):
        return chain
    try:
        # ⚠️ `use_markdown_` 是 MessageChain 的**字段**（`use_markdown()` 才是方法）：
        # 写成 `chain.use_markdown_(False)` 会 `'bool' object is not callable`。
        chain.use_markdown_ = False
    except Exception as exc:  # noqa: BLE001 —— 标不上也不该让消息发不出去
        logger.debug(f"miyoho 标记纯文本失败（{name}）：{exc}")
    return chain


def text_result(event: Any, text: str) -> Any:
    """构造一条「纯文本回复」——官方机器人上强制 `msg_type=0`（见上面一段说明）。"""
    return as_plain(event.plain_result(text), event)


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


def _inst_type(platform_id: str) -> str:
    """按平台实例 ID 反查适配器类型名（找不到返回空串）。"""
    pid = str(platform_id or "")
    for inst in _insts():
        iid, itype = _inst_meta(inst)
        if iid and iid == pid:
            return itype
    return ""


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

    chain = as_plain(
        MessageChain(chain=[Plain(text)]),
        platform_name=_inst_type(platform_id),
    )
    origin = f"{platform_id}:FriendMessage:{user_id}"
    await _ctx().send_message(origin, chain)
    logger.info(f"miyoho 私聊已发送：{origin}（{len(text)} 字）")
