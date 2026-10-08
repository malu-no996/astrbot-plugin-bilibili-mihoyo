"""式舆防卫战总结图：把防卫战结果渲染成 PNG（纯 Pillow，版面照抄网页 frag/zzz-shiyu.css）。

网页端的防卫战是 HTML/CSS 画的（`.sy-*` 那套），QQ 里没有 DOM，所以这里用 Pillow
把**同一套版面**重画一遍：颜色 / 圆角 / 字号逐条对着 CSS 抄，统一 2x 渲染。

  · 顶部：统计周期说明（与网页 `.sy-period` 同一句话）；
  · 头部卡：圆形头像（金环）+ 昵称 + 右侧大号综合评级；
  · 第五防线卡：标题 + 大号总分 + 金色排名胶囊 + 右侧评级，
    每个出战小队一条：右侧 BOSS 立绘（**原色**、高度贴合小队条、宽度按原图比例、
    左侧渐隐融入卡片）、「0N TEAM.」小标（带字距）+ 评级 + 得分
    + 防线效果胶囊 +（可选）通关时刻 + 代理人头像 + 邦布；
  · 第四防线卡：标题 + 评级 + 防线效果，小队同上（没有得分行）。

⚠️ 只认 `social_apis._shiyu_view()` 给的**视图**，不碰官方那套
`hadal_info_v2 / fitfh_layer_detail` 的字段名 —— 字段映射只在一个地方维护。

字体选择 / 文本绘制 / 图片加载全部复用 image_gen（抽卡总结图也是这么干的），
所以三张图的观感天然一致。
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw

from ...core import image_gen as _ig
from ..avatar import card_assets as _ca

S = _ig.S
PAD = _ig.PAD
R_CARD = _ig.R_CARD
R_TEAM = 10 * S               # 小队条圆角（CSS .sy-team 10px）
AV = 46 * S                   # 出战代理人头像边长（CSS .sy-ava 46px）
BUDDY = 40 * S                # 邦布（CSS 40px，矮 6px 贴底对齐）

# 防卫战**自己一套宽度**：在原先的 780px 上再砍「1.5 个邦布宽」→ 780 - 1.5×80 = 660px。
# （`BUDDY * 3 // 2` 就是 1.5 个邦布宽；改邦布尺寸时画布会跟着一起变。）
# 代价：顶部说明行在 12S 下要 717px，装不进收窄后的 CW（660-48 = 612），
# 所以说明行**自动降字号**保证仍然一行 —— 见 _note_size()。
# ⚠️ 只在这里覆盖 W / CW，模块内其它函数用的都是这两个名字，跟着一起变。
W = 390 * S - BUDDY * 3 // 2
CW = W - 2 * PAD
GAP_TEAM = 10 * S             # 小队条之间的间距（CSS .sy-team margin-top 10px）
PAD_X = 16 * S                # 卡片左右内边距（CSS .sy-layer padding 16px）
PAD_Y = 14 * S                # 卡片上下内边距

# ---- 调色板（逐条对应 CSS 里的色值，别随手调）----
_BG = _ig._BG
_CARD = _ig._CARD
_HEAD_G = [(0.0, (42, 20, 48)), (0.55, (63, 18, 38)), (1.0, (38, 16, 28))]
_LAYER_G = [(0.0, (36, 20, 40)), (0.60, (24, 16, 32)), (1.0, (21, 13, 24))]
_TEAM_FILL = (13, 10, 16, 191)          # rgba(13,10,16,.75)
_TEAM_BRD = (255, 255, 255, 13)         # rgba(255,255,255,.05)
_TXT = (236, 234, 242, 255)             # #eceaf2（卡片正文）
_WHITE = _ig._WHITE
_LABEL = (143, 132, 153, 255)           # #8f8499（「0N TEAM.」）
_BUFFER = (231, 200, 134, 255)          # #e7c886（防线效果那行字）
_FX_BG = (255, 255, 255, 20)            # rgba(255,255,255,.08)
_FX_BRD = (255, 255, 255, 31)           # rgba(255,255,255,.12)
_FX_TXT = (207, 196, 221, 255)          # #cfc4dd
_TIME_TXT = (183, 166, 198, 255)        # #b7a6c6
_GOLD_RING = (202, 165, 74, 255)        # 头像金环
_RATING_G = [(0.0, (255, 233, 168)), (1.0, (232, 163, 61))]   # S+：金渐变（CSS background-clip:text）
_RATING_COLOR = {"s": (255, 215, 110, 255), "a": (201, 179, 255, 255),
                 "b": (158, 195, 255, 255)}


# ---------------- 评级 ----------------

def _rating_key(r) -> str:
    """评级归类：S+ → sp（金渐变）/ S → s / A → a / 其余 → b（蓝）。

    口径与网页 syRatingCls 完全一致（`S+0` 这种也算 sp）。
    """
    s = str(r or "").upper()
    if s.startswith("S+"):
        return "sp"
    if s.startswith("S"):
        return "s"
    if s.startswith("A"):
        return "a"
    return "b"


def _grad_text(img, x, top, s, size, stops):
    """渐变字（S+ 用）：先把字画成蒙版，再把渐变贴上去（对应 CSS 的 background-clip:text）。"""
    w = int(_ig._tw(s, size, True)) + 2
    h = int(_ig._lh(size, True)) + 4
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    base = _ig._font(size, True).getmetrics()[0]
    cx = 0
    for ch in str(s):
        f = _ig._chfont(ch, size, True)
        md.text((cx, base + 2), ch, font=f, fill=255, anchor="ls")
        cx += f.getlength(ch)
    img.paste(_ig._grad(w, h, stops).convert("RGBA"), (int(x), int(top) - 2), mask)
    return x + cx


def _rating_text(img, x_right, cy, text, size):
    """评级文字：**右对齐**到 x_right、竖直以 cy 居中。S+ 走金色渐变，其余纯色。"""
    text = str(text or "")
    if not text:
        return
    x = x_right - _ig._tw(text, size, True)
    top = cy - _ig._lh(size, True) / 2
    key = _rating_key(text)
    if key == "sp":
        _grad_text(img, x, top, text, size, _RATING_G)
    else:
        _ig._text(ImageDraw.Draw(img, "RGBA"), x, top, text, size, True, _RATING_COLOR[key])


# ---------------- 小构件 ----------------

def _team_label(d, x, y, text):
    """「01 TEAM.」：CSS 上有 letter-spacing:2px，逐字画出来加字距。"""
    base = _ig._font(11 * S, True).getmetrics()[0]
    cx = x
    for ch in str(text):
        f = _ig._chfont(ch, 11 * S, True)
        d.text((cx, y + base), ch, font=f, fill=_LABEL, anchor="ls")
        cx += f.getlength(ch) + 2 * S


def _pill(img, x, cy, text, size, fill, border, txt_color, pad_x, h):
    """胶囊标签（排名 / 防线效果共用）；返回右边缘 x。"""
    d = ImageDraw.Draw(img, "RGBA")
    w = int(_ig._tw(text, size, True)) + pad_x * 2
    d.rounded_rectangle([x, cy - h // 2, x + w, cy + h // 2], radius=h // 2,
                        fill=fill, outline=border, width=S)
    _ig._text_c(d, x + w // 2, cy, text, size, True, txt_color)
    return x + w


def _rank_pill(img, x, cy, text):
    """金色排名胶囊（#ffe08a → #e0b23c 渐变 + 深棕字）。"""
    text = str(text or "")
    if not text:
        return x
    d = ImageDraw.Draw(img, "RGBA")
    w = int(_ig._tw(text, 12 * S, True)) + 20 * S
    h = 20 * S
    _ig._grad_round(img, (x, cy - h // 2, x + w, cy + h // 2), _ig._GOLD_G, h // 2)
    _ig._text_c(d, x + w // 2, cy, text, 12 * S, True, _ig._GOLD_TXT)
    return x + w


def _fx_pill(img, x, cy, text):
    """得分旁边那个「防线效果 »」小胶囊（半透明白底 + 灰字）。"""
    return _pill(img, x, cy, text, 11 * S, _FX_BG, _FX_BRD, _FX_TXT, 8 * S, 18 * S)


def _monster(layer, w, h, url):
    """右侧 BOSS 立绘：**原色显示** + 左侧渐隐，高度贴合小队条、宽度按原图比例。

    与网页 `.sy-monster` 的差别（用户明确要求）：网页是 `height:100% + width:200px +
    object-fit:cover + opacity:.45` —— 固定宽度、按盒子裁切、整体压暗到 45%；
    这里改成：
      · **高度 = 小队条高度**，宽度按原图宽高比算（`h * iw / ih`），不拉伸不变形；
      · **保留原色**（不再整体乘 0.45）；
      · 左边缘 → 自身 55% 处由透明渐变到不透明（对应 CSS 的 mask-image），
        让立绘左半边自然融进卡片、不压住文字。
    只有算出来的宽度超过卡片宽度时才退回 cover 裁切（极宽的横幅立绘）。
    立绘画在独立图层再贴回小队条：直接往条上贴会把圆角外也涂上。
    """
    img = _ig._load(url)
    if img is None:
        return
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return
    tw = max(1, int(round(h * iw / ih)))
    if tw > w:                                  # 极宽横幅：裁到卡片宽（横向取中间）
        tw = w
        tile = _ig._cover(img, tw, h, pos=0.5)
    else:
        tile = img.convert("RGBA").resize((tw, h), Image.LANCZOS)

    m = Image.new("L", (tw, h), 0)
    md = ImageDraw.Draw(m)
    for xx in range(tw):
        t = xx / max(1, tw - 1)
        md.line([(xx, 0), (xx, h)], fill=int(min(1.0, t / 0.55) * 255))
    # 圆角：右边缘与卡片右边缘对齐，圆角半径也一致，贴上去正好吻合卡片的圆角
    m = ImageChops.multiply(m, _ig._rmask(tw, h, R_TEAM))
    layer.paste(tile, (w - tw, 0), m)


def _draw_avatars(card, x, y, t, weapons=None):
    """出战代理人 + 邦布（矮 6px、贴底对齐），都走 image_gen._one_ava。

    · 代理人：左上 S/A 稀有度标 + 右上影画数角标（**0 命也画**：传数字就画，
      只有 None 才不画）；
    · 邦布：同样带左上稀有度标（S 金 / A 紫）+ 按稀有度区分的边框
      （`_S_BRD` 灰棕 / `_A_BRD` 紫）—— 与危局完全一致。以前这里只贴图 + 固定
      灰框，S 和 A 长得一模一样（用户报的「邦布不显示 A、S 级」就是这个），
      稀有度来自 social_apis 视图的 `buddy_rarity`。
      邦布不算命座，rank 传 None；底垫**纯黑**（bg=_BUDDY_BG），
      因为邦布素材带透明通道，不垫底会被卡片渐变透出来、显得「浮」。

    ⚠️ 左上的 S/A 标自 2026-10-05 起是**图片徽章**（`{S,A,B}RANK.png`，
    取图走 `card_assets.rank_badge_img`），尺寸 `image_gen.RANK_CHIP`
    —— 与右上角影画数角标一样大（用户要求）。取不到素材时 `_one_ava`
    自动退回原来的「渐变小牌 + 字母」。

    weapons：{角色id: 音擎图标本地路由}（「全面」版才有）。命中时在代理人头像**左下角**
    叠一枚音擎小徽章（尺寸同 A/S 徽章），与左上 S/A 徽章上下呼应；邦布不画音擎。
    """
    cx = x
    for a in t.get("avatars") or []:
        rarity = str(a.get("rarity") or "S").upper()
        _ig._one_ava(card, cx, y, AV, _ig._load(a.get("icon") or ""), rarity,
                     int(a.get("rank") or 0),
                     _ig._A_BRD if rarity == "A" else _ig._S_BRD, _ig.AVA_RADIUS,
                     rarity_img=_ca.rank_badge_img(rarity, _ig.RANK_CHIP))
        if weapons:
            wurl = weapons.get(str(a.get("id") or ""))
            if wurl:
                wimg = _ca.weapon_icon_img(wurl, _ig.RANK_CHIP)
                if wimg is not None:
                    card.alpha_composite(wimg, (cx, y + AV - _ig.RANK_CHIP))
        cx += AV + 8 * S
    if t.get("buddy_icon"):
        by = y + (AV - BUDDY)
        br = str(t.get("buddy_rarity") or "S").upper()
        _ig._one_ava(card, cx, by, BUDDY, _ig._load(t["buddy_icon"]), br, None,
                     _ig._A_BRD if br == "A" else _ig._BUDDY_BRD, _ig.AVA_RADIUS,
                     bg=_ig._BUDDY_BG,
                     rarity_img=_ca.rank_badge_img(br, _ig.RANK_CHIP))


# ---------------- 小队条 ----------------

def _team_height(t, show_time, with_score) -> int:
    """小队条的占用高度（必须与 _draw_team 的推进顺序一一对应）。"""
    h = 12 * S
    h += max(_ig._lh(11 * S, True), _ig._lh(18 * S, True))       # 顶行：0N TEAM. + 评级
    if with_score:
        h += 2 * S + _ig._lh(22 * S, True)                        # 得分行
    if show_time and t.get("time"):
        h += 4 * S + _ig._lh(12 * S)                              # 通关时刻
    h += 8 * S + AV + 12 * S                                      # 头像行 + 下内边距
    return h


def _draw_team(canvas, x, y, w, h, t, show_time, with_score, weapons=None):
    box = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(box).rounded_rectangle([0, 0, w - 1, h - 1], radius=R_TEAM, fill=_TEAM_FILL)

    monster = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    _monster(monster, w, h, t.get("monster") or "")
    box.alpha_composite(monster)

    d = ImageDraw.Draw(box, "RGBA")
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=R_TEAM, outline=_TEAM_BRD, width=S)

    px = 14 * S
    cy = 12 * S
    row_h = max(_ig._lh(11 * S, True), _ig._lh(18 * S, True))
    _team_label(d, px, cy + (row_h - _ig._lh(11 * S, True)) // 2,
                f"0{int(t.get('no') or 0)} TEAM.")
    _rating_text(box, w - 14 * S, cy + row_h // 2, t.get("rating") or "", 18 * S)
    cy += row_h

    if with_score:
        cy += 2 * S
        end = _ig._text(d, px, cy, str(int(t.get("score") or 0)), 22 * S, True, _WHITE)
        if t.get("buffer"):
            _fx_pill(box, end + 10 * S, cy + _ig._lh(22 * S, True) // 2, "防线效果 »")
        cy += _ig._lh(22 * S, True)

    if show_time and t.get("time"):
        cy += 4 * S
        _ig._text(d, px, cy, "通关时刻：" + str(t["time"]), 12 * S, False, _TIME_TXT)
        cy += _ig._lh(12 * S)

    cy += 8 * S
    _draw_avatars(box, px, cy, t, weapons)
    canvas.alpha_composite(box, (x, y))


# ---------------- 卡片 ----------------

def _head_card(canvas, y, view) -> int:
    """头部卡：圆头像 + 昵称。

    ⚠️ 这里**不画综合评级**（原来右侧有个大号 S+）：用户要求最上面角色名后面
    不要显示 S+ 之类的评级 —— 评级在下面的防线卡右侧已经有了，头卡留白更干净。
    """
    h = 64 * S
    card = Image.new("RGBA", (CW, h), (0, 0, 0, 0))
    card.paste(Image.new("RGB", (CW, h), _CARD), (0, 0), _ig._rmask(CW, h, R_CARD))
    _ig._grad_round(card, (0, 0, CW, h), _HEAD_G, R_CARD)
    d = ImageDraw.Draw(card, "RGBA")

    av = 40 * S
    ax, ay = 16 * S, (h - av) // 2
    img = _ig._load(view.get("icon") or "")
    if img is not None:
        card.alpha_composite(_ig._circle_img(img, av), (ax, ay))
    else:
        d.ellipse([ax, ay, ax + av, ay + av], fill=_ig._AVA_BG)
    d.ellipse([ax, ay, ax + av - 1, ay + av - 1], outline=_GOLD_RING, width=2 * S)

    cy = h // 2
    _ig._text(d, ax + av + 12 * S, cy - _ig._lh(14 * S, True) // 2,
              view.get("nick_name") or "代理人", 14 * S, True, _WHITE)

    canvas.alpha_composite(card, (PAD, y))
    return h + 10 * S


def _layer_card(canvas, y, title, layer, show_time, score=None, rank="", rating="",
                weapons=None) -> int:
    """防线卡：标题 +（可选）大号总分 + 排名胶囊 + 右侧评级 + 若干小队条。"""
    teams = [t for t in (layer.get("teams") or []) if isinstance(t, dict)]
    buffer = str(layer.get("buffer") or "")

    title_h = _ig._lh(15 * S, True)
    score_h = int(28 * S) if score is not None else 0
    buf_h = (8 * S + _ig._lh(12 * S)) if buffer else 0
    heights = [_team_height(t, show_time, score is not None) for t in teams]
    H = (PAD_Y + title_h + score_h + buf_h
         + sum(heights) + GAP_TEAM * max(0, len(teams) - 1) + PAD_Y)

    card = Image.new("RGBA", (CW, H), (0, 0, 0, 0))
    card.paste(Image.new("RGB", (CW, H), _CARD), (0, 0), _ig._rmask(CW, H, R_CARD))
    _ig._grad_round(card, (0, 0, CW, H), _LAYER_G, R_CARD)
    d = ImageDraw.Draw(card, "RGBA")

    cy = PAD_Y
    _ig._text(d, PAD_X, cy, title, 15 * S, True, _WHITE)
    # 标题行的评级与分数同字号（都 22S），这样「分数 + 评级」这条看起来是一套的
    _rating_text(card, CW - PAD_X, cy + title_h // 2, rating, 22 * S)
    cy += title_h

    if score is not None:
        # 分数（大号总分）字号：26S → 22S（用户要求小一点）。行高 score_h 不变，
        # 所以只是字变小、整行仍居中，不影响下面的防线效果与小队条位置。
        end = _ig._text(d, PAD_X, cy + (score_h - _ig._lh(22 * S, True)) // 2,
                        str(int(score or 0)), 22 * S, True, _WHITE)
        if rank:
            _rank_pill(card, end + 10 * S, cy + score_h // 2, rank)
        cy += score_h

    if buffer:
        cy += 8 * S
        _ig._text(d, PAD_X, cy, f"防线效果 » {buffer}", 12 * S, False, _BUFFER)
        cy += _ig._lh(12 * S)

    for t, th in zip(teams, heights):
        cy += GAP_TEAM
        _draw_team(card, PAD_X, cy, CW - PAD_X * 2, th, t, show_time, score is not None,
                   weapons)
        cy += th

    canvas.alpha_composite(card, (PAD, y))
    return H + 12 * S


# ---------------- 顶部说明行 ----------------

def _note_size(line) -> int:
    """顶部说明行的字号：画布收窄后 12S 装不下这行，于是从 12S 往下逐档试，
    返回第一个能**一行**放下的字号（下限 8S，正常落在 10S）。

    用户明确要求说明行不许折行，所以这里宁可缩字号也不折；真到 8S 还放不下
    （period 格式以后变超长）才由 render_shiyu 的折行分支兜底。
    """
    for s in range(12 * S, 8 * S - 1, -S):
        if _ig._tw(line, s) <= CW:
            return s
    return 8 * S


# ---------------- 对外接口 ----------------

def render_shiyu(view: dict, show_time: bool = False, show_fourth: bool = True,
                weapons: dict | None = None) -> Image.Image:
    """渲染一张式舆防卫战总结图。

    view：social_apis._shiyu_view(data) 给的视图（字段对齐前端 syBuild）。
    show_time：是否画「通关时刻」（对应网页上那个开关，命令里是详细设置的选项）。
    show_fourth：是否画「剧变节点第四防线」卡（默认画；命令那边对应
                 「显示第四防线」选项，**默认关** —— 第四防线官方只给头像，
                 没有得分/评级/立绘，信息量太低）。
    weapons：{角色id: 音擎图标本地路由}（「全面」版才有），在代理人头像左下角叠音擎标。
    """
    H_MAX = 6000 * S
    canvas = Image.new("RGBA", (W, H_MAX), _BG)
    d = ImageDraw.Draw(canvas, "RGBA")
    y = PAD

    period = str(view.get("period") or "")
    if period:
        scope = "剧变节点 4-5 层" if show_fourth else "剧变节点第五防线"
        line = f"仅展示{scope}数据，统计周期: {period}"
        ns = _note_size(line)          # 收窄后的画布上会自动降到 10S，仍是一行
        # 正常情况下一行放得下；这段折行只是**极端兜底**，
        # 防止以后 period 格式变长时整行溢出画布 —— 不要用它来当「窄画布」的借口。
        if _ig._tw(line, ns) <= CW:
            _ig._text(d, PAD, y, line, ns, False, _ig._MUTED)
            y += _ig._lh(ns) + 8 * S
        else:
            _ig._text(d, PAD, y, f"仅展示{scope}数据", ns, False, _ig._MUTED)
            y += _ig._lh(ns)
            _ig._text(d, PAD, y, f"统计周期: {period}", ns, False, _ig._MUTED)
            y += _ig._lh(ns) + 8 * S

    y += _head_card(canvas, y, view)
    y += _layer_card(canvas, y, "剧变节点第五防线", view.get("fifth") or {}, show_time,
                     score=int(view.get("score") or 0), rank=view.get("rank") or "",
                     rating=view.get("rating") or "", weapons=weapons)
    fourth = view.get("fourth") or {}
    if show_fourth and fourth.get("teams"):
        y += _layer_card(canvas, y, "剧变节点第四防线", fourth, show_time,
                         rating=fourth.get("rating") or "", weapons=weapons)

    return canvas.crop((0, 0, W, min(H_MAX, y + PAD))).convert("RGB")
