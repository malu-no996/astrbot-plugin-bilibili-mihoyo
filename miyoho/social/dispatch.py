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
    """去掉命令前缀（AstrBot 的唤醒前缀 / `/`）。

    和原版一致：**没带前缀也认**（`绝区零危局` 与 `/绝区零危局` 等效）。
    """
    for s in ("/", "！", "!", "？", "?"):
        if s and text.startswith(s):
            return text[len(s):].lstrip()
    return text


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


# ================= 分发入口（main.py 的事件监听调用） =================


async def handle_message(event: Any) -> str | None:
    """AstrMessageEvent → 匹配命令 → 检查开关/权限 → 执行接口 → 返回回复文本。

    任何一步不满足都返回 None（调用方静默放过）。
    """
    # 取消息里的纯文本（AstrBot 的 message_str 就是纯文本串）
    try:
        text = str(event.message_str or "").strip()
    except Exception:  # noqa: BLE001
        text = ""
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
        return None

    # AstrBot 里「机器人身份」= 平台实例 ID（get_self_id）
    try:
        sid = str(event.get_self_id() or "")
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
