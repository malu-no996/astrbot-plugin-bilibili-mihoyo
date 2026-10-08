"""QQ 官方「按钮消息」公共机制（原 src/social/qq.py 的 AstrBot 移植 · 真实现）。

原项目在 nonebot 里发 `QQMessage([QQMsg.markdown(正文), QQMsg.keyboard(键盘)])`；
AstrBot 的**插件消息链没有 keyboard 组件**，但官方适配器底层就是 botpy：

    event.bot            → botpy.Client（AstrBot 的 QQOfficialMessageEvent.__init__ 存进去的）
    event.bot.api.post_group_message(group_openid, msg_type, markdown, keyboard, msg_id, msg_seq)
    event.bot.api.post_c2c_message(openid, …)

两个接口都接受 `markdown` 与 `keyboard`（botpy 的类型全是 TypedDict，**用普通 dict 构造**即可），
所以这里绕过消息链直接发 —— 和 AstrBot 自己的 `_post_send` 用的是同一个 API、同样的参数形状。
（AstrBot 事件路径自己不带 keyboard，所以插件想发按钮只能这样走。）

两档降级（与原项目一致，原因逐档带回给调用方）：
  ① **markdown 正文 + 按钮**（官方唯一确认能渲染按钮的通道；正文不能为空，否则 40034030）
  ② **纯文本 + 按钮**（msg_type=0，不吃 markdown 权限的备份档）
  都不行 → (False, 原因)，调用方退回纯文字菜单并附上原因。

踩过的坑（原项目记录，照抄别删）
  · 带按钮的正文**不能为空**：官方报 `40034030 消息content字段不能为空`；空正文连按钮也不出来。
  · 按钮 id 要求「同一机器人 30 天内不重复」→ 每次随机生成。
  · 官方限制：每行 ≤5 个、最多 5 行（=25 个）、文案 1~10 字（超了整条被拒）。
  · 「自定义按钮」是官方**内邀能力**：没开通时官方可能**不报错也不渲染**按钮（用户只看到一屏文字）
    → 所以发送成功也要记一行日志，用来区分「代码没走通」和「平台没渲染」。
"""
from __future__ import annotations

import random
import uuid
from typing import Any

from loguru import logger

from .core import Ctx

# QQ 官方按钮的硬限制（超了整条消息会被拒）：每行最多 5 个、最多 5 行 = 25 个。
ROWS_MAX = 5
COLS_MAX = 5
# 按钮文案官方限 1~10 个字，超了会报错。
LABEL_MAX = 10
# 发送失败时，附加给用户的报错最多留这么长（官方报错常是一大段 JSON）。
ERR_MAX = 120
# 零宽空格：正文留空时的最后兜底（渲染出来什么都没有，用来试「纯按钮」）。
ZWSP = "\u200b"

# 布局默认值：一行 3 个、最多 9 个（用户对「米游社切换」按钮的指定值）。
DEFAULT_PER_ROW = 3
DEFAULT_MAX_BUTTONS = 9


def short_err(exc: BaseException) -> str:
    """把官方报错压成一行短句（常是一大段 JSON），塞进回复里给用户看。"""
    s = " ".join(str(exc).split())
    return s[:ERR_MAX] + "…" if len(s) > ERR_MAX else s


def is_official(event: Any) -> bool:
    """事件所属平台是不是 QQ 官方机器人（含 webhook 版）。

    用 `get_platform_name()`（适配器类型名，如 `qq_official`）—— **不是**
    `get_platform_id()`（平台实例 ID，如 `280-Eous`）。
    """
    try:
        return str(event.get_platform_name() or "").strip().lower().startswith("qq_official")
    except Exception:  # noqa: BLE001
        return False


def scene_of(event: Any) -> tuple[str, str]:
    """(场景, 目标 id)：`group` = 群聊（group_openid）/ `c2c` = 单聊（user_openid）。

    频道消息（`botpy.message.Message` / `DirectMessage`）走的是另一套接口，
    这里**不支持**，返回 `("", "")` 让调用方退回纯文字。

    ⚠️ 取值口径来自 botpy 的消息模型（也是 AstrBot 适配器解析的那两个字段）：
    群 = `raw.group_openid`；单聊 = `raw.author.user_openid`。
    """
    raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
    if raw is None:
        return "", ""
    gid = getattr(raw, "group_openid", None)
    if gid:
        return "group", str(gid)
    author = getattr(raw, "author", None)
    for attr in ("user_openid", "member_openid"):
        uid = getattr(author, attr, None)
        if uid:
            return "c2c", str(uid)
    return "", ""


def scene_name(event: Any) -> str:
    """场景的中文名（给用户看）：群聊 / 单聊 / 频道。"""
    if not is_official(event):
        return "会话"
    scene, _ = scene_of(event)
    return {"group": "群聊", "c2c": "单聊"}.get(scene) or "官方机器人"


def can_use_buttons(event: Any) -> bool:
    """这个事件能不能挂按钮：官方平台 + 群聊 / 单聊。"""
    return is_official(event) and bool(scene_of(event)[0])


def build_keyboard(btn_rows: list[list[dict]]) -> dict | None:
    """把按钮行做成 QQ 官方的 keyboard 结构；做不出来返回 None。

    按钮行形如 `[[{"label": "危局", "data": "绝区零危局"}, …], …]`：`label` 是按钮上
    显示的文字、`data` 是**点下去实际发出去的内容**（通常是原命令词）。

    botpy 的 `Keyboard` / `Button` 等全是 TypedDict（不是 dataclass），所以**直接用
    dict 构造**；字段与 nonebot 版（原项目用的）一致 —— 那条路已实测能渲染。

    ⚠️ 按钮 id 要求「同一机器人下 30 天内不重复」→ 每次用随机串，写死第二次就被拒。
    """
    rows: list[dict] = []
    for row in btn_rows[:ROWS_MAX]:
        buttons: list[dict] = []
        for b in row[:COLS_MAX]:
            data = str(b.get("data") or "")
            if not data:
                continue
            # label 缺了就退回 data：曾经因为调用方只给了 name/data，按钮凭空消失。
            label = (str(b.get("label") or "") or data)[:LABEL_MAX]
            buttons.append({
                "id": uuid.uuid4().hex[:12],
                "render_data": {"label": label, "visited_label": label, "style": 1},
                "action": {
                    "type": 2,                        # 2 = 指令按钮：点了发消息
                    "permission": {"type": 2},        # 2 = 所有人可操作
                    "data": data,
                    "enter": True,                    # 直接发出，不用再按发送
                    "reply": False,
                    "unsupport_tips": "请升级到最新版 QQ 后使用按钮",
                },
            })
        if buttons:
            rows.append({"buttons": buttons})
    return {"content": {"rows": rows}} if rows else None


def layout_buttons(items: list[dict], per_row: int = DEFAULT_PER_ROW,
                   max_buttons: int = DEFAULT_MAX_BUTTONS) -> list[list[dict]]:
    """`[{"label": 文案, "data": 点下去发的值}]` → 按钮行。

    **从左到右排满 `per_row` 个就换行**，最多取前 `max_buttons` 个。
    两个值都会被夹到官方上限（每行 ≤5、总 25）以内，传超了也不会把消息发炸。
    """
    try:
        per = int(per_row)
    except (TypeError, ValueError):
        per = DEFAULT_PER_ROW
    try:
        cap = int(max_buttons)
    except (TypeError, ValueError):
        cap = DEFAULT_MAX_BUTTONS
    per = max(1, min(COLS_MAX, per))
    cap = max(1, min(ROWS_MAX * COLS_MAX, cap))

    picked: list[dict] = []
    for b in items:
        if not isinstance(b, dict):
            continue
        data = str(b.get("data") or "")
        if not data:
            continue
        picked.append({"label": (str(b.get("label") or b.get("name") or "") or data)[:LABEL_MAX],
                       "data": data})
        if len(picked) >= cap:
            break
    return [picked[i:i + per] for i in range(0, len(picked), per)][:ROWS_MAX]


async def _post(event: Any, scene: str, target: str, payload: dict) -> None:
    """调 botpy 的发送接口（与 AstrBot `_post_send` 同一个 API）。"""
    api = getattr(getattr(event, "bot", None), "api", None)
    if api is None:
        raise RuntimeError("拿不到 QQ 官方 client（event.bot.api）")
    if scene == "group":
        await api.post_group_message(group_openid=target, **payload)
    else:
        await api.post_c2c_message(openid=target, **payload)


async def send_buttons(ctx: Ctx, text: str, btn_rows: list[list[dict]],
                       label: str = "") -> tuple[bool, str]:
    """QQ 官方发「一行短文字 + 按钮」→ (发出去了没, 失败原因)。

    逐档降级，原因合并带回：① markdown + 按钮 → ② 纯文本 + 按钮 → ③ 都不行返回 False，
    **调用方**退回纯文字并把原因附上（别在这里吞掉原因：上一版静默 fallback 的后果
    就是「用户只看到文字，不知道按钮为什么没来」）。

    `text` 为空时退到零宽空格（官方不允许空正文）；但空正文实测客户端只显示空白消息，
    所以调用方最好始终给一句短文字（菜单那边给的是 `kb_text`，默认「绝区零菜单」）。
    """
    ev = ctx.event if ctx is not None else None
    who = str(label or ((ctx.cmd or {}).get("cmd") if ctx is not None else "") or "QQ 按钮")
    if ev is None:
        return False, "按钮没挂上：调用时拿不到事件"
    if not is_official(ev):
        # 非官方平台：静默退回纯文字（调用方本来就该先判协议，这里只是保底）
        return False, ""
    scene, target = scene_of(ev)
    if not scene or not target:
        # 场景本身挂不了按钮。**必须说清楚**：静默的结果是「为什么没按钮」永远查不出来。
        return False, f"按钮没挂上：这个场景不支持按钮（{type(ev).__name__}）"
    keyboard = build_keyboard(btn_rows)
    if keyboard is None:
        return False, "按钮构造失败（后台日志有详情）"

    msg_id = str(getattr(getattr(ev, "message_obj", None), "message_id", "") or "")
    body = str(text or "").strip() or ZWSP
    n_btn = sum(len(r) for r in btn_rows)
    # ⚠️ 同一个 msg_id 的 msg_seq 只能出现一次（最多 5 次），重复会失败 → 每次随机
    #    （AstrBot 自己的 _post_send 也是 random 1..10000）。
    base = {"msg_id": msg_id, "msg_seq": random.randint(1, 10000)}
    reasons: list[str] = []

    for mode, payload in (
        ("markdown+按钮", {**base, "msg_type": 2,
                           "markdown": {"content": body}, "keyboard": keyboard}),
        ("纯文本+按钮", {**base, "msg_type": 0,
                         "content": body, "keyboard": keyboard}),
    ):
        try:
            await _post(ev, scene, target, payload)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"{who}：QQ「{mode}」失败（scene={scene}）：{exc}")
            reasons.append(f"{mode}：{short_err(exc)}")
            continue
        # ⚠️「请求成功」≠「按钮会显示」：自定义按钮属于官方内邀能力，没开通时官方可能
        #    不报错地忽略 keyboard —— 用户只看到一屏文字，而这里什么异常都没有。
        #    所以这行日志把话说全，让人一眼能区分「代码没走通」和「平台没渲染」。
        logger.info(f"{who}：按钮请求已发出（{mode}，{n_btn} 个，正文={body!r}，scene={scene}）。"
                    f"若 QQ 里只看到文字没有按钮，多为该机器人未开通「自定义按钮」（官方内邀能力）")
        return True, ""
    return False, f"按钮没发出去（{scene_name(ev)}）：" + "；".join(reasons)
