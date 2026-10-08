"""绝区零危局强袭战 · 结果图渲染（纯 Pillow 绘制，不吃浏览器）。

2026-10-04 从原 `src/image_gen.py` 拆出来的后半段：危局专属的版面常量
（画布宽 / 条目圆角 / BOSS 立绘宽）+ 卡片组件绘制 + 对外接口
（`render_deadly` / `output_path` / `render_deadly_png`）。

通用绘制件（字体 / 渐变 / 圆角 / 图片裁切）在 `core/image_gen.py`，
抽卡图（zzz/gacha/image.py）与防卫战图（zzz/record/shiyu_image.py）用的也是那一套。
"""

from __future__ import annotations

import pathlib
import time

from PIL import Image, ImageDraw

from ...core import image_gen as _ig
from ..avatar import card_assets as _ca
from ...core.image_gen import (
    PAD,
    R_CARD,
    S,
    _A_BRD,
    _AVA_BG,
    _BG,
    _BOSS_BRD,
    _BUDDY_BG,
    _BUDDY_BRD,
    _CARD,
    _ENTRY_G,
    _GOLD_G,
    _GOLD_LT,
    _GOLD_TXT,
    _HARD_G,
    _HEAD_G,
    _MUTED,
    _MUTED2,
    _PURPLE_G,
    _RANK_BG,
    _S_BRD,
    _STAR,
    _WHITE,
    _boss_pic,
    _circle_img,
    _cover,
    _font,
    _grad,
    _grad_round,
    _lh,
    _load,
    _one_ava,
    _rmask,
    _round_img,
    _text,
    _text_c,
    _tw,
)

IMG_DIR = pathlib.Path("data/zzz/img")
IMG_DIR.mkdir(parents=True, exist_ok=True)

# 画布总宽：390S = 780px，**只给危局用**（防卫战在 shiyu_image 里另有一套更窄的宽度）。
# 来龙去脉：原本 500S = 1000px，用户反馈「太宽」→ 收到 420S → 仍觉略宽，再收到 390S。
# QQ 会把图缩到聊天气泡宽，图越窄同样的字号看起来越大。
# ⚠️ 抽卡图（gacha_image）有自己的 W，不跟随这里。
W = 390 * S
CW = W - 2 * PAD            # 卡片宽度
R_ENTRY = 10 * S            # 条目圆角
PORTRAIT_W = 132 * S        # BOSS 立绘宽度（网页 .hd-portrait 是 104px，图里按卡片比例取）
GAP_ENTRY = 10 * S          # 常规模式相邻条目之间的间隔（网页 .hd-entry 下 10px + 上 8px 折叠成 10px）
# ---------------- 各组件绘制 ----------------

def _draw_head(card, w, hh, nick, avatar_img, tag, rank_text):
    d = ImageDraw.Draw(card, "RGBA")
    av = 46 * S
    ax, ay = 16 * S, (hh - av) // 2
    if avatar_img is not None:
        card.alpha_composite(_circle_img(avatar_img, av), (ax, ay))
    else:
        d.ellipse([ax, ay, ax + av, ay + av], fill=_AVA_BG)
    d.ellipse([ax, ay, ax + av - 1, ay + av - 1], outline=(202, 165, 74, 255), width=2 * S)

    tx = ax + av + 12 * S
    cy = hh // 2
    end = _text(d, tx, cy - _lh(14 * S, True) // 2, nick, 14 * S, True, _WHITE)
    cx = end + 8 * S

    if tag:
        pw = int(_tw(tag, 11 * S, True)) + 16 * S
        ph = 18 * S
        _grad_round(card, (cx, cy - ph // 2, cx + pw, cy + ph // 2), _GOLD_G, ph // 2)
        _text_c(d, cx + pw // 2, cy, tag, 11 * S, True, _GOLD_TXT)

    if rank_text:
        pw = int(_tw(rank_text, 13 * S, True)) + 24 * S
        ph = 24 * S
        x1 = w - 16 * S
        x0 = x1 - pw
        _grad_round(card, (x0, cy - ph // 2, x1, cy + ph // 2), _GOLD_G, ph // 2)
        _text_c(d, (x0 + x1) // 2, cy, rank_text, 13 * S, True, _GOLD_TXT)


def _draw_avas(card, x, y, c, weapons=None):
    """出战代理人 + 邦布头像行（都走 `image_gen._one_ava`）。

    左上角的 S / A 级标自 2026-10-05 起换成**图片徽章**（`{S,A,B}RANK.png`，
    与绝境群排行 / 代理人详情浮层同一套素材），尺寸取 `image_gen.RANK_CHIP`
    —— 和头像右上角那枚影画数角标一样大（用户要求两者在头像上角对称）。
    等级不在 S/A/B（或素材缺失）时 `rank_badge_img` 返回 None，`_one_ava`
    自动退回原来的「渐变小牌 + 字母」，不会开天窗。

    weapons：{角色id: 音擎图标本地路由}（「全面」版才有）。命中时在代理人头像**左下角**
    叠一枚音擎小徽章（尺寸同 A/S 徽章，由 `card_assets.weapon_icon_img` 画深色圆角底），
    和左上角的 S/A 徽章上下呼应；邦布不画音擎。
    """
    cx = x
    av = 46 * S
    for a in c.get("avatar_list") or []:
        img = _load(a.get("role_square_url") or a.get("icon") or "")
        rarity = str(a.get("rarity") or "S").upper()
        _one_ava(card, cx, y, av, img, rarity, int(a.get("rank") or 0),
                 _A_BRD if rarity == "A" else _S_BRD, _ig.AVA_RADIUS,
                 rarity_img=_ca.rank_badge_img(rarity, _ig.RANK_CHIP))
        if weapons:
            wurl = weapons.get(str(a.get("id") or ""))
            if wurl:
                wimg = _ca.weapon_icon_img(wurl, _ig.RANK_CHIP)
                if wimg is not None:
                    card.alpha_composite(wimg, (cx, y + av - _ig.RANK_CHIP))
        cx += av + 8 * S
    buddy = c.get("buddy") or {}
    if buddy:
        bimg = _load(buddy.get("bangboo_rectangle_url") or "")
        br = str(buddy.get("rarity") or "S").upper()
        # rank=None：邦布不画影画数角标；bg 垫纯黑底，邦布不再受卡片背景影响
        # 稀有度标同样用图片徽章（邦布头像也是「头像」，跟代理人那一列统一）
        _one_ava(card, cx, y + 6 * S, 40 * S, bimg, br, None,
                 _A_BRD if br == "A" else _BUDDY_BRD, _ig.AVA_RADIUS, bg=_BUDDY_BG,
                 rarity_img=_ca.rank_badge_img(br, _ig.RANK_CHIP))


def _draw_buffs(card, x, y, c):
    d = ImageDraw.Draw(card, "RGBA")
    lh = 21 * S
    cx = x
    for b in c.get("buffer") or []:
        name = b.get("name") if isinstance(b, dict) else None
        if not name:
            continue
        w = int(_tw(name, 11 * S)) + 18 * S
        d.rounded_rectangle([cx, y, cx + w, y + lh], radius=lh // 2,
                            fill=(202, 165, 74, 32), outline=(202, 165, 74, 96), width=S)
        _text_c(d, cx + w // 2, y + lh // 2, name, 11 * S, False, _GOLD_LT)
        cx += w + 6 * S


def _item_height(c, show_time, is_hard):
    pad_y = 15 * S if is_hard else 13 * S
    h = (pad_y + _lh(15 * S, True) + 2 * S + _lh(26 * S, True)
         + 8 * S + 46 * S + pad_y)
    if show_time and _time(c.get("challenge_time")):
        h += _lh(12 * S) + 5 * S
    if any((b or {}).get("name") for b in c.get("buffer") or []):
        h += 8 * S + 21 * S
    return max(118 * S, h)


def _draw_item(card, x, y, w, c, show_time, is_hard, weapons=None):
    d = ImageDraw.Draw(card, "RGBA")
    h = _item_height(c, show_time, is_hard)
    boss = (c.get("boss") or [{}])[0] if c.get("boss") else {}
    pimg = _load(boss.get("icon") or boss.get("bg_icon") or "")
    # 立绘：绝境条目贴卡片左边（圆角 = 卡片圆角），常规条目在条目框内（圆角 = 条目圆角）
    card.alpha_composite(
        _boss_pic(pimg, PORTRAIT_W, h, R_CARD if is_hard else R_ENTRY), (x, y))

    mx = x + PORTRAIT_W + (16 * S if is_hard else 14 * S)
    my = y + (15 * S if is_hard else 13 * S)

    _text(d, mx, my, boss.get("name") or "", 15 * S, True, _WHITE)
    my += _lh(15 * S, True) + 2 * S

    score = str(c.get("score", 0))
    _text(d, mx, my, score, 26 * S, True, _WHITE)
    _text(d, mx + _tw(score, 26 * S, True) + 10 * S, my + 6 * S,
          "★" * int(c.get("star") or 0), 13 * S, True, _STAR)
    my += _lh(26 * S, True) + 5 * S

    if show_time:
        t = _time(c.get("challenge_time"))
        if t:
            _text(d, mx, my, "通关时刻：" + t, 12 * S, False, _MUTED2)
            my += _lh(12 * S) + 5 * S

    _draw_avas(card, mx, my, c, weapons)
    my += 46 * S
    if any((b or {}).get("name") for b in c.get("buffer") or []):
        my += 8 * S
        _draw_buffs(card, mx, my, c)


def _build_card(canvas, y, kind, nick, avatar_img, rank_text, entries, total=None,
                show_time=False, weapons=None):
    is_hard = kind == "hard"
    hh = 70 * S
    heights = [_item_height(c, show_time, is_hard) for c in entries]
    tr_h = 0 if (is_hard or total is None) else (12 * S + _lh(28 * S, True) + 8 * S)
    # 常规条目的 BOSS 是一张张独立的卡片，相邻之间要留间隔（绝境是整片连着排，不加）
    gap_h = 0 if is_hard else GAP_ENTRY * max(0, len(entries) - 1)
    H = hh + tr_h + sum(heights) + gap_h

    card = Image.new("RGBA", (CW, H), (0, 0, 0, 0))
    card.paste(Image.new("RGB", (CW, H), _CARD), (0, 0), _rmask(CW, H, R_CARD))
    _grad_round(card, (0, 0, CW, hh), _HEAD_G, R_CARD, top=True)
    d = ImageDraw.Draw(card, "RGBA")

    # 1) 头部（头像 / 昵称 / 排名）置顶——与绝境卡、前台结构一致
    _draw_head(card, CW, hh, nick, avatar_img, "绝境模式" if is_hard else None, rank_text)

    # 2) 总分（仅常规卡），位于头部渐变带之下
    if tr_h:
        big = str(total[0])
        _text(d, 16 * S, hh + 12 * S, big, 28 * S, True, _WHITE)
        ux = 16 * S + _tw(big, 28 * S, True) + 8 * S
        _text(d, ux, hh + 12 * S + _lh(28 * S, True) - _lh(12 * S), "总分", 12 * S, False, _MUTED2)
        st = "★ × " + str(total[1])
        _text(d, CW - 16 * S - _tw(st, 13 * S, True), hh + 12 * S + 8 * S, st, 13 * S, True, _STAR)

    # 3) BOSS 条目
    yy = hh + tr_h
    for i, c in enumerate(entries):
        ih = heights[i]
        if is_hard:
            _grad_round(card, (0, yy, CW, yy + ih), _HARD_G, R_CARD, bottom=(i == len(entries) - 1))
            _draw_item(card, 0, yy, CW, c, show_time, True, weapons)
        else:
            if i:
                yy += GAP_ENTRY
            ex, ew = 10 * S, CW - 20 * S
            _grad_round(card, (ex, yy, ex + ew, yy + ih), _ENTRY_G, R_ENTRY)
            _draw_item(card, ex, yy, ew, c, show_time, False, weapons)
        yy += ih

    canvas.alpha_composite(card, (PAD, y))
    return H + 14 * S


# ---------------- 对外接口 ----------------

def _pct(p) -> str:
    return f"{((p or 0) / 100):.2f}%+"


def _pad2(n) -> str:
    return f"{int(n or 0):02d}"


def _time(t) -> str:
    if not t or not t.get("year"):
        return ""
    return (f"{t['year']}.{_pad2(t.get('month'))}.{_pad2(t.get('day'))} "
            f"{_pad2(t.get('hour'))}:{_pad2(t.get('minute'))}:{_pad2(t.get('second'))}")


def _day(t) -> str:
    s = _time(t)
    return s[:10] if s else ""


def render_deadly(data: dict, show_time: bool = False, weapons: dict | None = None) -> Image.Image:
    H_MAX = 6000 * S
    canvas = Image.new("RGBA", (W, H_MAX), _BG)
    d = ImageDraw.Draw(canvas, "RGBA")
    y = PAD

    p1, p2 = _day(data.get("start_time")), _day(data.get("end_time"))
    if p1 or p2:
        _text(d, PAD, y, f"统计周期 {p1} - {p2}", 12 * S, False, _MUTED)
        y += _lh(12 * S) + 3 * S
    _text(d, PAD, y, "* 总得分/排名和挑战详情并非同时刷新，挑战详情存在 2 小时左右延迟",
          11 * S, False, _MUTED)
    y += _lh(11 * S) + 10 * S

    nick = data.get("nick_name") or "代理人"
    avatar = _load(data.get("avatar_icon") or "")
    hard = data.get("hard_list") or []
    normal = data.get("list") or []

    if hard:
        y += _build_card(canvas, y, "hard", nick, avatar, _pct(data.get("hard_rank_percent")),
                         hard, show_time=show_time, weapons=weapons)
    if normal or not hard:
        y += _build_card(canvas, y, "normal", nick, avatar, _pct(data.get("rank_percent")),
                         normal, total=(data.get("total_score", 0), data.get("total_star", 0)),
                         show_time=show_time, weapons=weapons)

    return canvas.crop((0, 0, W, min(H_MAX, y + PAD))).convert("RGB")


def output_path(uid: str, previous: bool) -> pathlib.Path:
    return IMG_DIR / f"deadly_{uid}_{'prev' if previous else 'cur'}.png"


async def render_deadly_png(data: dict, out_path: str, show_time: bool = False,
                          weapons: dict | None = None) -> None:
    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    render_deadly(data, show_time=show_time, weapons=weapons).save(out, "PNG")
