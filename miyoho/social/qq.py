"""QQ 官方「按钮消息」公共机制（原 src/social/qq.py 的 AstrBot 移植 · 降级版）。

原项目在 QQ 官方机器人上把账号列表 / 角色列表做成按钮（markdown + keyboard 两档降级）。
AstrBot 的消息链暂不支持构造 QQ 官方 keyboard 组件，所以这一版**全部退回纯文字**：

  · `layout_buttons` 保留（纯数据整形，按钮数据照常进 data/vars，模板还能用）；
  · `send_buttons` 恒返回 (False, "")，调用方按「没发出去且无原因」走纯文字兜底
    （调用方的约定是 `if why:` 才附加原因，所以不会给用户塞一句多余的报错）；
  · `can_use_buttons` 恒 False，`build_keyboard` 恒 None，`scene_name` 返回通用文案。

哪天 AstrBot 支持了 keyboard，再把这三个函数填回真实现即可，调用方不用动。
"""
from __future__ import annotations

from typing import Any

from .core import Ctx

# QQ 官方按钮的硬限制（保留给 layout_buttons 夹紧用）：每行最多 5 个、最多 5 行 = 25 个。
ROWS_MAX = 5
COLS_MAX = 5
# 按钮文案官方限 1~10 个字，超了会报错。
LABEL_MAX = 10
# 发送失败时，附加给用户的报错最多留这么长。
ERR_MAX = 120
# 零宽空格：正文留空时的最后兜底。
ZWSP = "\u200b"

# 布局默认值：一行 3 个、最多 9 个（用户对「米游社切换」按钮的指定值）。
DEFAULT_PER_ROW = 3
DEFAULT_MAX_BUTTONS = 9


def short_err(exc: BaseException) -> str:
    """把报错压成一行短句。"""
    s = " ".join(str(exc).split())
    return s[:ERR_MAX] + "…" if len(s) > ERR_MAX else s


def scene_name(event: Any) -> str:
    """场景的中文名（给用户看）。AstrBot 侧不再区分适配器，给通用文案。"""
    try:
        gid = event.get_group_id() if event is not None else ""
        return "群聊" if gid else "私聊"
    except Exception:  # noqa: BLE001
        return "会话"


def can_use_buttons(event: Any) -> bool:
    """AstrBot 版暂不支持 QQ 官方按钮，恒 False（调用方走纯文字）。"""
    return False


def build_keyboard(btn_rows: list[list[dict]]) -> Any:
    """AstrBot 版暂不支持，恒 None（调用方退回纯文字）。"""
    return None


def layout_buttons(items: list[dict], per_row: int = DEFAULT_PER_ROW,
                   max_buttons: int = DEFAULT_MAX_BUTTONS) -> list[list[dict]]:
    """`[{"label": 文案, "data": 点下去发的值}]` → 按钮行。

    **从左到右排满 `per_row` 个就换行**，最多取前 `max_buttons` 个。
    两个值都会被夹到官方上限（每行 ≤5、总 25）以内。
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


async def send_buttons(ctx: Ctx, text: str, btn_rows: list[list[dict]],
                       label: str = "") -> tuple[bool, str]:
    """恒返回 (False, "")：按钮未适配，调用方退回纯文字（且不附加原因）。"""
    return False, ""
