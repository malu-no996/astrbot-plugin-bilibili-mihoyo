"""社交命令 · 命令匹配与分发（原 src/social/dispatch.py 的 AstrBot 移植）。

原版是 nonebot `on_message` 响应器；AstrBot 版改成**纯函数**：
main.py 的事件监听器把 AstrMessageEvent 交进来，本模块完成
「匹配命令 → 查开关与权限 → 执行接口 → 返回要回复的文本」。

返回值约定：
  None        没命中（或该机器人没开这条命令）→ 调用方什么都不做
  ""          接口自己把消息发完了（silent）→ 调用方不再发文字
  "文本"      调用方把它回复出去
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ..core import bind
from . import seen
from .cfg import load_cfg
from .core import Ctx, apply_tpl, run_interface


# ================= 命令匹配 =================


def _strip_prefix(text: str) -> str:
    """去掉命令前缀（AI 说的 `/` 之类人工前缀）。

    和原版一致：**没带前缀也认**（`绝区零危局` 与 `/绝区零危局` 等效）。
    """
    for s in ("/", "！", "!", "？", "?"):
        if s and text.startswith(s):
            return text[len(s):].lstrip()
    return text


def _raw_text(event: Any) -> str:
    """取「用户真正打出来的原文」，用于命令匹配。

    ⚠️ **不要用 `event.message_str`**：AstrBot 的唤醒检查阶段
    （`core/pipeline/waking_check/stage.py`）会把**唤醒前缀**从 message_str 里剥掉，
    而这个用户配的 `wake_prefix` 里就有「绝区零」：
        ['/', '绝区零', '猫又', '小九', '晚安', '早安', '虚狩', '歪了', '臭机器人']
    于是「绝区零菜单」进来时 message_str 已经变成「菜单」，命令表里没这一条 →
    **静默失配**（现象：发命令毫无反应，日志里连「命中但未放行」的 INFO 都没有）。
    本插件大多数命令名都以「绝区零」开头，所以这条必须绕开。

    消息链（`event.get_messages()`）里的 Plain 段不受唤醒阶段改动，从这里拼原文最稳。
    拼不到（结构不符 / 非文本消息）就退回 message_str，绝不抛异常。
    """
    try:
        msgs = event.get_messages()
    except Exception:  # noqa: BLE001
        msgs = None
    if msgs:
        try:
            from astrbot.api.message_components import Plain

            parts = [
                str(m.text)
                for m in msgs
                if isinstance(m, Plain) and getattr(m, "text", None)
            ]
            joined = "".join(parts).strip()
            if joined:
                return joined
        except Exception:  # noqa: BLE001 —— 拿不到就用 message_str 兜底
            pass
    try:
        return str(getattr(event, "message_str", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def match_command(commands: list[dict], text: str) -> tuple[dict | None, str]:
    """一条消息 → (命中的命令配置, 后面的参数字符串)；没命中返回 (None, '')。"""
    body = _strip_prefix(text.strip())
    if not body:
        return None, ""
    parts = body.split(maxsplit=1)
    head = parts[0].strip()
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not head:
        return None, ""
    for c in commands:
        if not c.get("enabled", True):
            continue                                  # 停用的命令不参与匹配
        if head == c.get("cmd") or head in (c.get("aliases") or []):
            return c, arg
    return None, ""


def bot_allows(cfg: dict, self_id: str, cmd: dict) -> bool:
    """该机器人此刻是否允许执行这条命令。

    总开关**默认关闭**；未单独设置过的命令跟随总开关；设置过的以单独设置优先。
    （AstrBot 里 self_id 用平台实例 ID。）
    """
    sid = str(self_id or "")
    if not sid:
        return False
    if not (cfg.get("bots") or {}).get(sid, False):
        return False
    per = (cfg.get("bot_cmds") or {}).get(sid) or {}
    key = str(cmd.get("id") or "")
    if key in per:
        return bool(per[key])
    return True


def _protocol(platform_name: str) -> str:
    """平台适配器类型 → 回复模板口径：qq_official* → "qq"，其余 → "onebot"。"""
    return "qq" if str(platform_name or "").lower().startswith("qq_official") else "onebot"


# ================= 唤醒标记的还原 =================
#
# 背景（AstrBot 的机制，藏在 core/pipeline/waking_check/stage.py）：
#   「只要有任意一个插件的 handler filter 通过，就把事件标成 is_wake /
#     is_at_or_wake_command」。
# 我们的监听器是 EventMessageType.ALL（必须如此：命令名可在页面上改，没法注册成
# 静态的 @filter.command），于是**群里任何一句话都会通过 filter** → 都被当成
# 「已唤醒」→ 送进 LLM。等于把机器人的唤醒范围放大到了全群。
#
# 所以：没命中我们的命令时，把这两个标记还原回「消息自身是否真的唤醒了机器人」，
# 让行为退回「没有本插件时」的样子；命中命令时由调用方 stop_event 收尾。


def _components(event: Any) -> list[Any]:
    try:
        return list(event.get_messages() or [])
    except Exception:  # noqa: BLE001
        return []


def truly_woke(event: Any, raw: str = "") -> bool:
    """消息**自身**是否真的唤醒了机器人（At / AtAll / 引用机器人 / 私聊 / 唤醒前缀）。

    判定故意偏宽松：拿不准就返回 True（= 保持现状），避免把正常的 LLM 回复也压掉。
    """
    try:
        if event.is_private_chat():
            return True
    except Exception:  # noqa: BLE001
        return True
    try:
        from astrbot.api.message_components import At, AtAll, Reply

        sid = str(event.get_self_id() or "")
        for m in _components(event):
            if isinstance(m, AtAll):
                return True
            if isinstance(m, At) and sid and str(getattr(m, "qq", "")) == sid:
                return True
            if (
                isinstance(m, Reply)
                and sid
                and str(getattr(m, "sender_id", "")) == sid
            ):
                return True
    except Exception:  # noqa: BLE001
        return True
    # message_str 比原文短，说明 AstrBot 从它里面剥掉了唤醒前缀 → 命中了唤醒词
    try:
        cur = str(getattr(event, "message_str", "") or "").strip()
    except Exception:  # noqa: BLE001
        return True
    body = str(raw or "").strip()
    return bool(cur) and len(cur) < len(body) and body.endswith(cur)


def restore_wake_state(event: Any, raw: str = "") -> None:
    """未命中命令时，把被本插件放大成 True 的唤醒标记还原回去。"""
    if truly_woke(event, raw or _raw_text(event)):
        return
    for attr in ("is_wake", "is_at_or_wake_command"):
        try:
            setattr(event, attr, False)
        except Exception:  # noqa: BLE001
            pass


# ================= 分发入口（main.py 的事件监听调用） =================


async def handle_message(event: Any) -> str | None:
    """AstrMessageEvent → 匹配命令 → 检查开关/权限 → 执行接口 → 返回回复文本。

    任何一步不满足都返回 None（调用方静默放过）。
    """
    # 取用户原文（**不是** message_str：唤醒前缀会被 AstrBot 剥掉，见 _raw_text）
    text = _raw_text(event)
    if not text:
        return None

    # 顺手记一笔「谁在这个群发过话」：群排行用它圈定候选人（见 seen.py，
    # 热路径 + 节流落盘，失败绝不影响分发）。
    try:
        gid = str(event.get_group_id() or "")
    except Exception:  # noqa: BLE001
        gid = ""
    if gid:
        try:
            seen.remember(gid, str(event.get_sender_id() or ""))
        except Exception:  # noqa: BLE001
            pass

    cfg = load_cfg()
    cmd, cmd_arg = match_command(cfg.get("commands") or [], text)
    if not cmd:
        # 未命中是最难排查的情况（完全静默）：开 DEBUG 就能看到实际拿到的原文
        logger.debug(f"社交命令未命中：text={text!r}")
        return None

    # AstrBot 里「机器人身份」= **平台实例 ID**（get_platform_id，与面板「平台实例」
    # 列表、通知下拉同源，如 `napcat` / `280-Eous`）。
    # ⚠️ 别用 get_self_id：官方适配器给的是 "qq_official"（或 mention id），
    # aiocqhttp 给的是真实 QQ 号 —— 都跟面板上配的实例名对不上，
    # 于是「总开关没开」→ 所有命令静默失配。
    try:
        sid = str(event.get_platform_id() or "")
    except Exception:  # noqa: BLE001
        sid = ""
    if not bot_allows(cfg, sid, cmd):
        # 用 info：走到这里说明**命令确实命中了**只是开关没放行，这正是最难排查的情况。
        logger.info(
            f"社交命令命中但未放行：bot={sid} cmd={cmd.get('cmd')} "
            f"（该机器人总开关或这条命令的单独开关没开）"
        )
        return None                                   # 该机器人没开这条命令 → 完全静默

    if cmd.get("admin_only"):
        try:
            is_admin = bool(event.is_admin)
        except Exception:  # noqa: BLE001
            is_admin = False
        if not is_admin:
            return "无权限：该命令仅管理员可用"

    try:
        user_id = str(event.get_sender_id() or "")
        platform_name = str(event.get_platform_name() or "")
    except Exception:  # noqa: BLE001
        user_id, platform_name = "", ""

    ctx = Ctx(
        event=event, arg=cmd_arg, self_id=sid, cmd=cmd,
        bot=None,                                     # AstrBot 版不再有适配器 bot 实例
        user_id=user_id,                              # 查「这个人的绑定账号」用
        protocol=_protocol(platform_name),            # 挑哪套回复模板
    )
    text = await run_interface(cmd.get("api") or "", ctx)
    if not text.strip():
        return ""                                     # 接口自己发完了（silent）
    # 配了回复模板就用模板渲染（模板渲染为空则退回接口默认文本）
    return apply_tpl(cmd, ctx.protocol, ctx.vars, text)
