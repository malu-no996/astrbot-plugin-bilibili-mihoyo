"""抽卡总结图：把单个频段的统计渲染成 PNG（纯 Pillow，风格与 image_gen 一致）。

数据口径与网页端完全一致（gacha_stats.summarize 里对应频段的 pool 条目）：

- 顶部：频段名 + **绝区零角色名**（不再显示 UID），下面一行总览
  （总抽数 · 金 · A    平均每金，中间用多个空格隔开）；
- 紧接总览：「当前已垫」横条（**在出金列表上方**，有垫才显示）；
- 中间：每根横条 = 一次出金 —— 名字 + 距上次出金的抽数（pity）+ UP/歪 角标。

**横条颜色统一按该条抽数分档**（与网页端一致，越接近硬保底越警示）：
<50 绿、<70 黄、≥70 红 —— 出金条、已垫条同一规则。UP/歪 不再影响条色，
只在行尾保留文字角标（能判定才画）。
"""

from __future__ import annotations

import time

from PIL import Image, ImageDraw

from ...core import image_gen as _ig      # 复用字体选择 / 文本绘制 / 渲染倍率

S = _ig.S
W = 500 * S
PAD = 12 * S
CW = W - 2 * PAD
R_CARD = 12 * S

# ---- 调色板（深色，与 image_gen / 网页端观感一致）----
_BG = (15, 12, 20, 255)
_CARD = (23, 18, 29)
_TRACK = (42, 36, 50)
_WHITE = (255, 255, 255, 255)
_MUTED = (154, 146, 168, 255)
_UP_GREEN = (92, 194, 116, 255)
_OFF_RED = (222, 85, 90, 255)
_WARN_YELLOW = (232, 186, 74, 255)   # 已垫过半、逼近保底时的警告黄

BAR_MAX_PITY = 90      # ZZZ 硬保底 90 抽，横条按它归一
NAME_W = 92 * S        # 名字列宽（超出截断加 …）
BAR_W = 250 * S        # 横条轨道宽
ROW_H = 30 * S

_OVERVIEW_GAP = "      "   # 总览与「平均」之间的分隔空格（用户要求拉开，别只用一个 · ）


def _pity_color(n: int) -> tuple:
    """「当前已垫」条的填充色：已垫越多越接近硬保底，颜色越警示。

    <50 绿（安全） / <70 黄（过半） / ≥70 红（快保底了）。
    """
    if n < 50:
        return _UP_GREEN
    if n < 70:
        return _WARN_YELLOW
    return _OFF_RED


def _tag_of(g: dict) -> tuple[str, tuple]:
    """一条出金的角标文案与颜色：UP（绿）/ 歪（红），只做行尾文字标注。

    ⚠️ 官方 getGachaLog **不返回**「是否 UP」字段，只有记录带 up_ids 或
    gacha_stats 里配了常驻 S 名单才判得出来；判不出来就**不给角标**
    （画个灰「未知」只是噪音，用户明确要求去掉）。
    """
    if not g.get("has_up"):
        return "", (0, 0, 0, 0)
    if g.get("is_up") is True:
        return "UP", _UP_GREEN
    if g.get("is_up") is False:
        return "歪", _OFF_RED
    return "", (0, 0, 0, 0)


def _clip(s: str, size: int, width: int, bold: bool = False) -> str:
    """按像素宽截断（超出部分用 … 结尾）。"""
    if _ig._tw(s, size, bold) <= width:
        return s
    while s and _ig._tw(s + "…", size, bold) > width:
        s = s[:-1]
    return s + "…"


def _bar(d, y: int, tx0: int, tx1: int, pity: int, color: tuple,
         num_color: tuple) -> None:
    """画一根横条：轨道 + 按 ZZZ 硬保底 90 抽归一的填充 + 右侧「N抽」数字。"""
    d.rounded_rectangle((tx0, y + 9 * S, tx1, y + 19 * S), radius=5 * S, fill=_TRACK)
    frac = max(0.0, min(1.0, pity / BAR_MAX_PITY)) if BAR_MAX_PITY else 0.0
    bw = max(4 * S, int((tx1 - tx0) * frac))
    d.rounded_rectangle((tx0, y + 9 * S, tx0 + bw, y + 19 * S), radius=5 * S, fill=color)
    _ig._text(d, tx1 + 8 * S, y + 8 * S, f"{pity}抽", 12 * S, True, num_color)


def render_summary(pool: dict, role_name: str = "") -> Image.Image:
    """渲染单个频段的抽卡总结图。

    pool：gacha_stats.summarize(...) 里对应频段的条目
          （name/total/s_count/a_count/avg_per_s/pity_now/golden）。
    role_name：绝区零角色名，画在标题右侧（**不显示 UID**；拿不到名字就留空）。

    版式（自上而下）：
      1. 频段名 + 角色名；
      2. 总览行：`N 抽 · N 金 · N A` + 多个空格 + `平均 X 抽/金`
         （**不再写「当前已垫 N 抽」文字**，已垫只由下面的条表达）；
      3. 「当前已垫」横条（**挪到出金列表上方**），颜色按已垫抽数分档：
         <50 绿、<70 黄、≥70 红；
      4. 逐次出金的横条列表。

    **不截行**：有多少次出金就画多少条（用户明确要求「总结多少就是多少行」）。
    图高按行数算，纯色深底压缩后体积很小；出金次数本身通常也就几十条。
    """
    shown = [g for g in (pool.get("golden") or []) if isinstance(g, dict)]
    pity_now = int(pool.get("pity_now") or 0)

    head_h = 58 * S                 # 标题 + 总览行
    rows_h = (len(shown) + (1 if pity_now > 0 else 0)) * ROW_H
    foot_h = 26 * S
    H = PAD + head_h + rows_h + foot_h + PAD

    img = Image.new("RGBA", (W, H), _BG)
    d = ImageDraw.Draw(img)
    card = (PAD, PAD, W - PAD, H - PAD)
    d.rounded_rectangle(card, radius=R_CARD, fill=_CARD)

    # ---- 标题：频段名（粗）+ 角色名（弱色，右对齐；不显示 UID）----
    y = PAD + 10 * S
    _ig._text(d, PAD + 14 * S, y, str(pool.get("name") or "调频记录"), 15 * S, True, _WHITE)
    if role_name:
        _ig._text(d, W - PAD - 14 * S - _ig._tw(role_name, 12 * S), y + 2 * S,
                  role_name, 12 * S, False, _MUTED)

    # ---- 总览行：`N 抽 · N 金 · N A` + 多个空格 + `平均 X 抽/金` ----
    y += 24 * S
    total = int(pool.get("total") or 0)
    s_cnt = int(pool.get("s_count") or 0)
    a_cnt = int(pool.get("a_count") or 0)
    overview = f"{total} 抽 · {s_cnt} 金 · {a_cnt} A"
    avg = pool.get("avg_per_s")
    if avg:
        overview += f"{_OVERVIEW_GAP}平均 {avg} 抽/金"
    _ig._text(d, PAD + 14 * S, y, overview, 12 * S, False, _MUTED)

    # ---- 横条列表：先「当前已垫」（有垫才显示），再逐次出金 ----
    y = PAD + head_h + 6 * S
    x0 = PAD + 14 * S
    tx0, tx1 = x0 + NAME_W, x0 + NAME_W + BAR_W

    if pity_now > 0:
        _ig._text(d, x0, y + 8 * S, _clip("当前已垫", 12 * S, NAME_W, True),
                  12 * S, True, _MUTED)
        _bar(d, y, tx0, tx1, pity_now, _pity_color(pity_now), _pity_color(pity_now))
        y += ROW_H

    for g in shown:
        name = str(g.get("name") or "未知")
        pity = int(g.get("pity") or 0)
        _ig._text(d, x0, y + 8 * S, _clip(name, 12 * S, NAME_W), 12 * S, False, _WHITE)
        _bar(d, y, tx0, tx1, pity, _pity_color(pity), _WHITE)

        tag, color = _tag_of(g)
        if tag:
            _ig._text(d, W - PAD - 14 * S - _ig._tw(tag, 11 * S), y + 9 * S,
                      tag, 11 * S, False, color)
        y += ROW_H

    # ---- 页脚 ----
    _ig._text(
        d, PAD + 14 * S, H - PAD - 20 * S,
        f"共 {len(shown)} 次出金 · {time.strftime('%Y-%m-%d %H:%M')} 生成",
        10 * S, False, _MUTED,
    )
    return img
