"""后台绘图**公共工具箱**：字体回退 / 文本量算 / 渐变圆角 / 图片裁切。

2026-10-04 从原 `src/image_gen.py` 拆开：这里只留**各功能共用**的绘制件，
三张绝区零结果图各自那一套搬到了：

    zzz/record/image.py        危局强袭战（卡片版面 + render_deadly）
    zzz/record/shiyu_image.py  式舆防卫战
    zzz/gacha/image.py         调频总结

它们一律 `from ...core import image_gen as _ig` 后复用这里的工具，
所以本文件里**不要放任何单一功能专用的版面常量**（画布宽度之类归各自那份）。

文本按字符做字体回退（中文→微软雅黑，韩文→Malgun Gothic，泰文/老挝文→Leelawadee UI），
避免昵称等出现豆腐块。依赖只有 Pillow（纯二进制 wheel，无需系统库、无需 Chromium）。
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from . import asset_cache

S = 2                       # 渲染倍率（三张图共用，改这里等于三张一起改）
PAD = 12 * S                # 左右/上下留白（防卫战图也复用，见 shiyu_image）
R_CARD = 12 * S             # 卡片圆角（防卫战图也复用）
# ---- 调色板（对应 CSS 里的颜色/变量）----
_BG = (15, 12, 20, 255)
_CARD = (23, 18, 29)
_HEAD_G = [(0.0, (43, 26, 42)), (0.55, (67, 24, 43)), (1.0, (38, 16, 28))]
_HARD_G = [(0.0, (64, 21, 38)), (0.55, (42, 16, 36)), (1.0, (28, 15, 30))]
_ENTRY_G = [(0.0, (51, 24, 42)), (0.55, (32, 20, 35)), (1.0, (26, 17, 32))]
_GOLD_G = [(0.0, (255, 224, 138)), (1.0, (224, 178, 60))]
_PURPLE_G = [(0.0, (201, 179, 255)), (1.0, (138, 99, 217))]
_WHITE = (255, 255, 255, 255)
_MUTED = (154, 146, 168, 255)
_MUTED2 = (183, 166, 198, 255)
_GOLD_TXT = (36, 23, 3, 255)
_GOLD_LT = (231, 200, 134, 255)
_STAR = (255, 215, 110, 255)
_AVA_BG = (13, 10, 16, 255)
_S_BRD = (124, 90, 46, 255)
_A_BRD = (138, 107, 209, 255)
_BUDDY_BRD = (76, 69, 86, 255)
_BUDDY_BG = (0, 0, 0, 255)        # 邦布底：纯黑。邦布素材是带透明通道的 PNG，
                                  # 不垫底的话卡片渐变 / 立绘会从透明处透出来
_BOSS_BRD = (255, 255, 255, 77)   # BOSS 立绘四周的灰描边（网页 .hd-portrait img 的 rgba(255,255,255,.3)）
_RANK_BG = (0, 0, 0, 140)         # 影画数角标底：网页 .hd-ava-rank 的 rgba(0,0,0,.55)

# ---- 字体：CJK（微软雅黑）+ 韩文（Malgun Gothic）+ 泰文（Leelawadee UI），逐级回退 ----
_CJK_REG = ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc")
_CJK_BOLD = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/simhei.ttf")
_KR_REG = ("C:/Windows/Fonts/malgun.ttf", "C:/Windows/Fonts/gulim.ttc", "C:/Windows/Fonts/batang.ttc")
_KR_BOLD = ("C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf")
# 泰文 / 老挝文：微软雅黑、黑体里**一个字形都没有**（实测 U+0E01 / U+0E31 / U+0E48 / U+0E81
# 全部落到 .notdef），所以昵称里出现泰文就画成一排方框。微软雅黑也不含韩文 ——
# 韩文早有自己一条链，泰文照抄这个做法：
#   · Leelawadee UI（Win8.1+ 自带）含泰文 + 老挝文，常规/粗体都有；
#   · Tahoma 兜底（含泰文，但**不含老挝文**）。
# 逐字符绘制对泰文是安全的：Leelawadee UI 里声调 / 上标元音这类 mark 字符的
# advance 是 0（实测 "กั" 与 "ก" 同宽），叠加位置正确；前引元音 เ 在码点顺序上
# 本身就在辅音前面，不需要重排。
# ⚠️ 非 Windows（或系统精简掉了这两个字体）时仍会退到雅黑 → 依旧方框，
# 那种情况得往插件里内置一份泰文字体才能根治。
_TH_REG = ("C:/Windows/Fonts/LeelawUI.ttf", "C:/Windows/Fonts/tahoma.ttf")
_TH_BOLD = ("C:/Windows/Fonts/LeelaUIb.ttf", "C:/Windows/Fonts/tahomabd.ttf",
            "C:/Windows/Fonts/LeelawUI.ttf", "C:/Windows/Fonts/tahoma.ttf")
_FCACHE: dict = {}


def _pick(cands, size):
    for p in cands:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _font(size, bold=False):
    size = max(8, int(size))
    key = ("cjk", size, bold)
    f = _FCACHE.get(key)
    if f is None:
        f = _pick(_CJK_BOLD if bold else _CJK_REG, size)
        _FCACHE[key] = f
    return f


def _krfont(size, bold=False):
    size = max(8, int(size))
    key = ("kr", size, bold)
    f = _FCACHE.get(key)
    if f is None:
        f = _pick(_KR_BOLD if bold else _KR_REG, size)
        _FCACHE[key] = f
    return f


def _thfont(size, bold=False):
    size = max(8, int(size))
    key = ("th", size, bold)
    f = _FCACHE.get(key)
    if f is None:
        f = _pick(_TH_BOLD if bold else _TH_REG, size)
        _FCACHE[key] = f
    return f


def _is_hangul(ch) -> bool:
    o = ord(ch)
    return 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


def _is_thai(ch) -> bool:
    """泰文（U+0E00–U+0E7F）+ 老挝文（U+0E80–U+0EFF）—— 都由 Leelawadee UI 覆盖。

    连**标点 / 数字**也算进来：泰文数字 U+0E50–U+0E59、泰文句号 U+0E2F 等，
    雅黑同样没有。范围给整块，省得漏。
    """
    o = ord(ch)
    return 0x0E00 <= o <= 0x0EFF


def _chfont(ch, size, bold):
    if _is_hangul(ch):
        return _krfont(size, bold)
    if _is_thai(ch):
        return _thfont(size, bold)
    return _font(size, bold)


def _lh(size, bold=False) -> int:
    a, d = _font(size, bold).getmetrics()
    return a + d


def _tw(s, size, bold=False) -> float:
    return sum(_chfont(ch, size, bold).getlength(ch) for ch in str(s))


def _text(draw, x, top, s, size, bold, fill):
    """按「顶部坐标」绘制（内部换算 baseline），逐字符字体回退；返回结束 x。"""
    base = _font(size, bold).getmetrics()[0]
    cx = x
    for ch in str(s):
        f = _chfont(ch, size, bold)
        draw.text((cx, top + base), ch, font=f, fill=fill, anchor="ls")
        cx += f.getlength(ch)
    return cx


def _text_c(draw, cx, cy, s, size, bold, fill):
    """以 (cx, cy) 为中心绘制。"""
    x = cx - _tw(s, size, bold) / 2
    _text(draw, x, cy - _lh(size, bold) / 2, s, size, bold, fill)


# ---------------- 渐变 / 圆角 / 图片工具 ----------------

def _lerp(c0, c1, t):
    return tuple(int(round(c0[i] + (c1[i] - c0[i]) * t)) for i in range(3))


def _sample(stops, t):
    if t <= stops[0][0]:
        return stops[0][1]
    if t >= stops[-1][0]:
        return stops[-1][1]
    for i in range(len(stops) - 1):
        p0, c0 = stops[i]
        p1, c1 = stops[i + 1]
        if p0 <= t <= p1:
            f = (t - p0) / (p1 - p0) if p1 > p0 else 0.0
            return _lerp(c0, c1, f)
    return stops[-1][1]


def _grad(w, h, stops):
    w, h = max(1, int(w)), max(1, int(h))
    strip = Image.new("RGB", (w, 1))
    px = strip.load()
    for x in range(w):
        px[x, 0] = _sample(stops, x / (w - 1) if w > 1 else 0.0)
    return strip.resize((w, h))


def _rmask(w, h, radius, top=False, bottom=False, left=False, right=False):
    w, h, radius = max(1, int(w)), max(1, int(h)), int(radius)
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    x0 = -radius if (left and not right) else 0
    x1 = (w - 1 + radius) if (right and not left) else (w - 1)
    y0 = -radius if (top and not bottom) else 0
    y1 = (h - 1 + radius) if (bottom and not top) else (h - 1)
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=255)
    return m


def _grad_round(img, box, stops, radius, **kw):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    img.paste(_grad(w, h, stops), (x0, y0), _rmask(w, h, radius, **kw))


def _load(url):
    """本地路由 → PIL 图片；未缓存则返回 None（由 _cover 兜底成占位）。"""
    if not url:
        return None
    if url.startswith("/miyoho-asset/"):
        p = asset_cache.asset_path(url.rsplit("/", 1)[-1])
    elif url.startswith("http"):
        p = asset_cache.asset_path(asset_cache._name(url))
    else:
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _cover(img, w, h, pos=0.2):
    if img is None:
        return Image.new("RGBA", (w, h), _AVA_BG)
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGBA", (w, h), _AVA_BG)
    sc = max(w / iw, h / ih)
    nw, nh = max(1, int(iw * sc + 0.5)), max(1, int(ih * sc + 0.5))
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = int((nh - h) * pos)
    return img.crop((left, top, left + w, top + h))


def _round_img(img, w, h, radius, pos=0.2, **mask_kw):
    tile = _cover(img, w, h, pos)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(tile, (0, 0), _rmask(w, h, radius, **mask_kw))
    return out


def _circle_img(img, d, pos=0.12):
    tile = _cover(img, d, d, pos)
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    out.paste(tile, (0, 0), _rmask(d, d, d // 2))
    return out


def _boss_pic(img, w, h, radius, pos=0.20):
    """BOSS 立绘：铺满整块 + **四周一圈灰描边** + 左侧两角圆角。

    对齐网页 `.hd-portrait img`：`border: 1px solid rgba(255,255,255,.3)`，
    只圆左侧两角（右边接文字区，直角）。

    ⚠️ `_rmask` 的 left/right 语义是反的（传 left=True 圆的是**右**角）：
    这里要圆左角，所以传 `right=True`。
    """
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(_cover(img, w, h, pos), (0, 0), _rmask(w, h, radius, right=True))

    d = ImageDraw.Draw(out, "RGBA")
    wd = max(1, S)
    r = int(radius)
    if r > 0:
        # 左边两角画圆弧（圆的半径 = 圆角半径），上/下边从圆弧切点拉到右端
        d.arc([0, 0, 2 * r, 2 * r], 180, 270, fill=_BOSS_BRD, width=wd)
        d.arc([0, h - 1 - 2 * r, 2 * r, h - 1], 90, 180, fill=_BOSS_BRD, width=wd)
        d.line([(r, 0), (w - 1, 0)], fill=_BOSS_BRD, width=wd)
        d.line([(r, h - 1), (w - 1, h - 1)], fill=_BOSS_BRD, width=wd)
    else:
        d.rectangle([0, 0, w - 1, h - 1], outline=_BOSS_BRD, width=wd)
    d.line([(w - 1, 0), (w - 1, h - 1)], fill=_BOSS_BRD, width=wd)
    return out



# ---- 头像格：危局卡片与防卫战卡片共用（防卫战通过 _ig._one_ava 调）----

# 头像格**右上角影画数角标**的边长（2x 下 = 30 物理像素）。头像本身随版面变尺寸
# （40S / 44S / 46S），角标**不跟着缩放** —— 和网页 `.hd-ava-rank` 一样是固定大小。
# 头像左上那枚稀有度**图片徽章**也按它取尺寸（用户在头像上角要求两者一样大），
# 危局（record/image.py）、防卫战（record/shiyu_image.py）、绝境群排行
# （record/rank_image.py）都引用这里，**别再各自写一份 15*S**。
RANK_CHIP = 15 * S
# 影画数角标的圆角半径（只圆右上 / 左下两角，另外两角是直角）。
# 2026-10-05 用户要求「不要太圆」：7S → 4S（15S 的方块给 4S ≈ 27%）。
# 对应网页 `.hd-ava-rank` / `.sy-ava-rank` 的 `border-radius: 0 4px 0 4px`。
RANK_CHIP_R = 4 * S
# 代理人 / 邦布图块的圆角半径。对应网页 `.hd-ava` / `.sy-ava` 的 `border-radius: 6px`
# （2026-10-05 用户要求「不要太圆」，原 9px / 8px）—— 四个调用方一律引用它，别再写死。
AVA_RADIUS = 6 * S


def _one_ava(card, x, y, size, img, rarity, rank, border, radius, bg=None,
             rarity_img=None):
    """代理人 / 邦布图块（左上稀有度标 + 右上影画数角标）。

    bg：先垫一层不透明圆角底色。邦布素材带透明通道，垫纯黑底后才不会被卡片
        渐变透出来（传 `_BUDDY_BG`）；代理人不需要，传 None（保持原样）。
    rank：影画数。**None = 不画角标**（邦布用），数字（**含 0**）= 画 ——
        零命现在也要显示「0」（用户要求，之前 0 不画）。
    rarity_img：**图片版**稀有度标（`texture2d/icon/{S,A,B}RANK.png` 那种带
        「S RANK」字样的方徽章），贴在图块左上角；尺寸由调用方自己定，取图统一走
        `zzz/avatar/card_assets.rank_badge_img()`。
        **默认 None → 还是画「渐变小牌 + 字母」**（等级不在 S/A/B 或素材缺失时的兜底）；
        危局 / 防卫战 / 绝境群排行这三处出图都传它（用户要求 S/A 级用图片，2026-10-05）。
    """
    d = ImageDraw.Draw(card, "RGBA")
    if bg is not None:
        card.paste(Image.new("RGBA", (size, size), bg), (x, y), _rmask(size, size, radius))
    if img is not None:
        card.alpha_composite(_round_img(img, size, size, radius, pos=0.12), (x, y))
    else:
        d.rounded_rectangle([x, y, x + size, y + size], radius=radius, fill=_AVA_BG)
    d.rounded_rectangle([x, y, x + size - 1, y + size - 1], radius=radius,
                        outline=border, width=2 * S)
    # 稀有度标（左上）：默认画「渐变小牌 + 字母」；传了 rarity_img 就贴图片素材
    if rarity_img is not None:
        card.alpha_composite(rarity_img, (x, y))
    else:
        lw = int(_tw(rarity, 10 * S, True)) + 10 * S
        lh = 15 * S
        rr = 5 * S
        _grad_round(card, (x, y, x + lw, y + lh), _PURPLE_G if rarity == "A" else _GOLD_G, rr)
        _text_c(d, x + lw // 2, y + lh // 2, rarity, 10 * S, True,
                (29, 16, 48, 255) if rarity == "A" else _GOLD_TXT)
    # 影画数角标（右上角）：**黑色半透明底 + 不透明白字**，
    # 与网页 .hd-ava-rank / .sy-ava-rank 完全一致（`border-radius: 0 4px 0 4px`
    # → 只圆右上 / 左下两角，另两角直角；半径 = 上面的 RANK_CHIP_R）。
    # 用 alpha_composite 而不是直接填色：角标要「压暗」底下的头像，不能把它盖死。
    # rank=None 才不画（邦布）；0 也要画（零命显示 0，用户要求）。
    if rank is not None:
        b = RANK_CHIP
        rr = RANK_CHIP_R
        m = Image.new("L", (b, b), 0)
        md = ImageDraw.Draw(m)
        md.rounded_rectangle([0, 0, b - 1, b - 1], radius=rr, fill=255)
        md.rectangle([0, 0, rr, rr], fill=255)                            # 左上补直角
        md.rectangle([b - 1 - rr, b - 1 - rr, b - 1, b - 1], fill=255)    # 右下补直角
        m = m.point(lambda v: 255 if v > 127 else 0)                      # 硬边，别让圆角发虚
        chip = Image.new("RGBA", (b, b), (0, 0, 0, 0))
        chip.paste(Image.new("RGBA", (b, b), _RANK_BG), (0, 0), m)
        card.alpha_composite(chip, (x + size - b, y))
        _text_c(d, x + size - b // 2, y + b // 2, str(rank), 10 * S, True, _WHITE)


