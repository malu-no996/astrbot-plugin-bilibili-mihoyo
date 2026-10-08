"""绝区零「代理人详细」面板图（Pillow 版）。

网页版那份浮层（`web/frag/zzz-avatar.{html,css,js}`）靠浏览器排版，QQ 命令发不了
网页，所以这里用 Pillow **照着重画一份**：整卡底色 / 属性两列 / 技能条 / 音擎小卡 /
驱动盘区 + 三列盘卡的配色、尺寸、圆角，全部按 `frag/zzz-avatar.css` 定稿的那套数值来
（CSS px × SCALE）。改版面时两边要一起改，避免「网页一个样、QQ 另一个样」。

数据吃 `detail.py` 归一化后的模型（`from_official` / `from_enka` 是同一份形状），
所以网页和 QQ 用的是同一套字段 —— 不会再出现「网页正常、QQ 用错字段」。
素材与字体沿用 `card_assets`（本地 texture2d 图标 + 远程头像走 asset_cache）。

⚠️ 别在这里做 `asset_cache.rewrite_assets`：那是把远程 URL 换成**网页能访问的本地路由**，
Pillow 要的是原 URL（`card_assets.fetch_img` 自己会落缓存）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

from . import card_assets as _a

# 渲染倍率：网页那套数值全是 CSS px，乘这个倍数画大图（像素密度高一点，QQ 里更清楚）
SCALE = 1.6

# 技能条素材：网页版用的是 350×70 那张（6 个圆 + 图标烙在图里），
# 就放在面板静态资源里 pages/panel/assets/skill_bar.png（git 跟踪）。
# ⚠️ 别指向 texture2d/skill_bar.png —— 那是另一张 1100×130 的原图，圆心位置对不上，
# 等级数字会全部错位。原先指向 NoneBot 原项目的 web/asset/，AstrBot 版没有这个目录，
# 结果就是技能条底图永远贴不上、只剩一排悬空的等级数字（2026-10-09 修）。
_PLUGIN_ROOT = Path(__file__).resolve().parents[3]
SKILL_BAR_PATH = _PLUGIN_ROOT / "pages" / "panel" / "assets" / "skill_bar.png"

# ---------------- 配色（逐个取自 web/frag/zzz-avatar.css）----------------
C_CARD = (22, 24, 29)        # #16181d 整卡底（音擎小卡也用这个 = 最深那一档）
C_PANEL = (35, 38, 44)       # #23262c 属性面板 / 技能条 / 驱动盘区（＝浅色区）
C_ROW = (26, 28, 33)         # 属性表奇数行：rgba(0,0,0,.26) 压在 #23262c 上
C_SUNK = (27, 30, 36)        # #1b1e24 驱动盘卡里的主词条 / 副词条面板
C_CHIP = (44, 48, 55)        # #2c3037 小标签底
C_BORDER = (58, 63, 72)      # #3a3f48 图标圆底 / 头像框描边

T_MAIN = (244, 242, 238)     # #f4f2ee
T_BODY = (232, 230, 225)     # #e8e6e1
T_DIM = (185, 190, 199)      # #b9bec7
T_MUTE = (111, 117, 126)     # #6f757e
T_SUB = (139, 145, 160)      # #8b91a0
T_TAG = (154, 160, 168)      # #9aa0a8
T_LV = (223, 227, 234)       # #dfe3ea

ORANGE = (255, 159, 46)      # #ff9f2e
ORANGE_T = (255, 178, 94)    # #ffb25e
ORANGE_P = (255, 207, 125)   # #ffcf7d
GREEN = (126, 195, 111)      # #7ec36f
GOLD = (240, 177, 62)        # #f0b13e 技能 11+ 的数字色
SBLUE = (111, 168, 255)      # #6fa8ff 技能 6+ 的数字色

RAR = {"S": (217, 166, 58), "A": (168, 132, 216), "B": (127, 178, 232)}

# 驱动盘总分评级的字母配色：S / SS（带不带 +）一律橙，A 紫、B/C 浅蓝
# —— 色值和网页版 .av-rate / .av-rate.a / .av-rate.b 一致（#ff9f2e / #a884d8 / #7fb2e8）。
# **SSS / SSS+ 不走这里**，走下面的 RATING_GRAD 彩虹渐变；表里没列到的一律回退橙色。
RATING_COLOR = {
    "S": ORANGE, "SS": ORANGE,
    "S+": ORANGE, "SS+": ORANGE,
    "A": (168, 132, 216), "B": (127, 178, 232), "C": (127, 178, 232),
}

# 最高档 SSS / SSS+ 的**彩虹渐变**（网页 .av-rate.rb 用的同一串色，横向 90deg）。
RATING_GRAD = [(0.00, (255, 110, 199)), (0.25, (255, 209, 102)), (0.50, (110, 231, 183)),
               (0.75, (111, 177, 255)), (1.00, (192, 132, 252))]

# 半透明色的「压完底」结果（Pillow 直接填 RGBA 会替换像素、抠出洞，所以预先算好等效实色）
C_NOTE_BG = (44, 40, 34)     # rgba(255,159,46,.1) over #16181d
C_NOTE_BD = (99, 80, 45)     # rgba(255,159,46,.35) over #16181d
C_DWRAP_BD = (35, 36, 40)    # rgba(255,255,255,.05) over #16181d
C_DMAIN_BD = (43, 46, 51)    # rgba(255,255,255,.07) over #1b1e24
C_TAG_BG = (61, 57, 50)      # rgba(255,159,46,.12) over #23262c
C_TAG_BD = (101, 86, 58)     # rgba(255,159,46,.3)  over #23262c
C_SUBBG = (58, 52, 42)       # rgba(255,159,46,.14) over #1b1e24

# ---------------- 版面常量 ----------------
W = 660
PAD = 16
GAP = 10
AG_W = 168
AG_PAD = 10
AG_TAB_H = 13
PIC_W, PIC_H, PIC_BW = 148, 181, 2
AG_NAME_H = 22
AG_LV_H = 18
AG_H = AG_PAD + AG_TAB_H + 8 + (PIC_H + 2 * PIC_BW) + 10 + AG_NAME_H + 6 + AG_LV_H + AG_PAD

INNER = W - 2 * PAD
RIGHT_X = PAD + AG_W + GAP
RIGHT_W = INNER - AG_W - GAP
SB_IMG_W = 326
SB_PANEL_H = 6 + SB_IMG_W * 70 / 350      # 技能条面板高（图 350×70）

DWRAP_PAD = 12
DWRAP_IN = INNER - 2 * (DWRAP_PAD + 1)    # 驱动盘区内容宽（扣掉 1px 描边 + padding）
DTOP_H = 68                               # 上排（音擎 / 命中 / 评分）高 = 音擎小卡高
DISC_GAP = 10
DISC_W = (DWRAP_IN - 2 * DISC_GAP) / 3
DISC_PAD_X, DISC_PAD_Y = 11, 9

CAMP_H = 53                                # 阵营 LOGO 高度（＝网页 .av-camp 的 53px）

# 头像左上「S / A 级」图片徽章的**可见边长**（CSS px）。
# 口径 = **和原来那枚圆形稀有度标一样大**（Rarity_*.png 实心圆 56/80 × 35 = 24.5）。
# 用户 2026-10-05 明确：这一列（稀有度 → 属性 → 职业）原本是**中心轴对齐**的，
# 而且**A/S 这张图本来就比属性/职业图标大** —— 之前按「18px（= 影画数容器高）」改小后
# 轴心歪了、图也比图标小了，所以按原尺寸回来。
# ⚠️ 那个 18px 的口径只属于**绝境群排行的队伍头像**（record/rank_image.py::RAR_BADGE），
# 别拿过来套这里。
RAR_BADGE = 24.5

# 头像框圆角（CSS px）。用户 2026-10-05：「头像框……圆角太圆了，也需要改」→ 10 → 6。
# 和危局 / 防卫战战报图的头像（`core/image_gen.py::AVA_RADIUS = 6 * S`）、
# 网页 `.av-pic` 的 `border-radius:6px` 是同一个数，**三处要一起改**。
# 三层的关系保持原样：外框 = 内框 + 2（那边正好是一圈 2px 边框），图像裁切再小 2。
PIC_RADIUS = 6

# 右上角「影画数（几命）」小方块的圆角（CSS px）。它原来是 5px（画在 18px 的方块上，
# 27.8% 显得很圆）；2026-10-05 用户要求「影画边的圆角不要太圆」→ 4px。
# 与危局 / 防卫战 `.hd-ava-rank` / `.sy-ava-rank`（15px 方块给 4px）取同一个值，
# 网页侧 `.av-rk` 也是 4px。
RK_RADIUS = 4


# ================= 多语言字体回退 =================
# 昵称是玩家自己起的，语言不可控。zzz 那两份字体实测只覆盖 **中 / 日 / 英(拉丁) / 俄(西里尔)**，
# 韩文、泰文一个字都没有 —— 直接画就是一排方框（.notdef）。
# 需求范围就锁这六种语言，所以回退链只接两个 Windows 自带字体：
#   韩文 → Malgun Gothic；泰文（U+0E00–U+0EFF，连老挝文一起）→ Leelawadee UI / Tahoma。
# ⚠️ 加新语言时**先按下面的 `_glyph_sig` 诊断法验一遍主字体到不到**，别凭感觉加分支。
_KR_FONTS = ("C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf")
_TH_FONTS = ("C:/Windows/Fonts/LeelaUIb.ttf", "C:/Windows/Fonts/LeelawUI.ttf",
             "C:/Windows/Fonts/tahomabd.ttf", "C:/Windows/Fonts/tahoma.ttf")

_FONT_CACHE: dict = {}     # (kind, px_size, thin) → FreeTypeFont / None（系统没装该字体）
_KIND_CACHE: dict = {}     # (thin, px_size, ch) → "base" / "kr" / "th"

# 私用区字符：任何字体都不可能给它字形，拿它当「没覆盖」的对照组
_NOTO_CH = "\uE000"


def _is_hangul(ch: str) -> bool:
    """韩文：谚文音节 + 字母 + 兼容字母。"""
    o = ord(ch)
    return 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F


def _is_thai(ch: str) -> bool:
    """泰文块 U+0E00–U+0E7F（连同块的老挝文 U+0E80–U+0EFF 一起接住）。"""
    return 0x0E00 <= ord(ch) <= 0x0EFF


def _pick_font(kind: str, size_px: int, thin: bool):
    """取字体对象（按 种类/字号/粗细 缓存）。

    ⚠️ 一定要缓存：`card_assets.font/thin` 每次调用都 `ImageFont.truetype` 重新读盘，
    而这里会被**逐字符**调用（一张图几百次），不缓存等于每张图加载几百次字体文件。
    回退字体一个都没装 → None（调用方退回主字体，最多是方框，不至于崩）。
    """
    key = (kind, size_px, thin)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    if kind == "base":
        try:
            font = _a.thin(size_px) if thin else _a.font(size_px)
        except Exception:  # noqa: BLE001
            font = None
    else:
        for p in (_KR_FONTS if kind == "kr" else _TH_FONTS):
            try:
                font = ImageFont.truetype(p, size=max(8, int(size_px)))
                break
            except Exception:  # noqa: BLE001 —— 该字体没装就试下一个
                continue
    _FONT_CACHE[key] = font
    return font


def _glyph_sig(font, ch: str):
    """字形的粗略指纹（光栅尺寸 + 前若干字节），用来判断字体到底有没有这个字形。"""
    m = font.getmask(ch)
    return (m.size, bytes(m)[:64])


def _font_kind(ch: str, size_px: int, thin: bool) -> str:
    """某个字符该用哪套字体 → "base" / "kr" / "th"。

    先看主字体有没有这个字形（和私用区字符比 mask，别靠「某某字体应该有吧」猜），
    没有才按 Unicode 区块挑回退字体；系统没装回退字体就只能照旧（方框）。
    结果按 (thin, 字号, 字符) 缓存 —— 一次出图几百个字符也只探测一轮。
    """
    key = (thin, size_px, ch)
    cached = _KIND_CACHE.get(key)
    if cached is not None:
        return cached
    base = _pick_font("base", size_px, thin)
    kind = "base"
    if base is not None and _glyph_sig(base, ch) == _glyph_sig(base, _NOTO_CH):
        cand = "kr" if _is_hangul(ch) else ("th" if _is_thai(ch) else "base")
        if cand != "base" and _pick_font(cand, size_px, thin) is not None:
            kind = cand
    _KIND_CACHE[key] = kind
    return kind


def _fade(im: Image.Image, alpha: float) -> Image.Image:
    """整图乘一层不透明度（对应网页 .av-camp 的 opacity）。"""
    im = im.convert("RGBA")
    im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
    return im


# ================= 画布 =================


class _Canvas:
    """一层 RGBA 画布 + 一组「按 CSS px 下笔」的辅助方法。

    所有对外坐标都是 CSS px（和 frag/zzz-avatar.css 里写的数字一一对应），
    内部统一乘 SCALE —— 这样对着 CSS 抄版面时不用心算倍数。
    """

    def __init__(self, w_css: float, h_css: float, bg=C_CARD):
        self.img = Image.new("RGBA", (self.px(w_css), self.px(h_css)), bg + (255,))
        self.d = ImageDraw.Draw(self.img)

    @staticmethod
    def px(v: float) -> int:
        return int(round(v * SCALE))

    def _box(self, x, y, w, h):
        return (self.px(x), self.px(y), self.px(x + w), self.px(y + h))

    # ---- 形状 ----
    def rrect(self, x, y, w, h, r, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(self._box(x, y, w, h), radius=self.px(r), fill=fill,
                                 outline=outline, width=self.px(width) if outline else 0)

    def alpha_rrect(self, x, y, w, h, r, fill):
        """半透明填充：叠一层临时画布再合并（直接填 RGBA 会把底下的像素替换掉）。"""
        ov = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        ImageDraw.Draw(ov).rounded_rectangle(self._box(x, y, w, h), radius=self.px(r), fill=fill)
        self.img = Image.alpha_composite(self.img, ov)
        self.d = ImageDraw.Draw(self.img)

    def line(self, x1, y1, x2, y2, color, width=1):
        self.d.line((self.px(x1), self.px(y1), self.px(x2), self.px(y2)),
                    fill=color, width=self.px(width))

    def paste(self, im, x, y, w=None, h=None):
        if im is None:
            return
        if w and h:
            im = im.convert("RGBA").resize((self.px(w), self.px(h)), Image.Resampling.LANCZOS)
        self.img.paste(im.convert("RGBA"), (self.px(x), self.px(y)), im.convert("RGBA"))

    # ---- 文字 ----
    # ⚠️ 默认用**细体**（zzz_thins.ttf）：原来正文走 zzz_fonts.ttf，出图看着整体偏粗
    # （用户 2026-10-05 反馈）。要某处粗一点就显式传 thin=False。
    def font(self, size, thin=True):
        return _pick_font("base", self.px(size), thin)

    def _pick(self, ch, size, thin=True):
        """单个字符的字体对象：主字体盖得住就用主字体，否则走韩 / 泰回退字体。"""
        return _pick_font(_font_kind(ch, self.px(size), thin), self.px(size), thin)

    def _mixed(self, txt, size, thin=True) -> bool:
        """串里有没有主字体画不出来的字符。

        有才走逐字符绘制 —— **普通文本（中英日俄）的绘制路径一点没变**，
        出的图跟以前逐像素一样，不会因为加了回退就整体跑版。
        """
        return any(_font_kind(c, self.px(size), thin) != "base" for c in str(txt))

    def measure(self, txt, size, thin=True):
        s = str(txt)
        if not self._mixed(s, size, thin):
            return self.d.textlength(s, font=self.font(size, thin)) / SCALE
        return sum(self._pick(c, size, thin).getlength(c) for c in s) / SCALE

    def text(self, x, y, txt, size, color, anchor="la", thin=True):
        s = str(txt)
        if not self._mixed(s, size, thin):
            self.d.text((self.px(x), self.px(y)), s, font=self.font(size, thin),
                        fill=color + (255,), anchor=anchor)
            return
        self._text_mixed(x, y, s, size, color, anchor, thin)

    def _text_mixed(self, x, y, s, size, color, anchor, thin):
        """逐字符绘制（含韩 / 泰的混排文本）。

        Pillow 没有浏览器的 `font-family` 回退链，一个字符串只能用一种字体，所以按
        字符挑字体、自己累加 advance。垂直基线统一取**主字体**的 metrics ——
        各画各的话不同字体的 ascent 不一样，混排会高低不齐。

        ⚠️ anchor → baseline 的换算**实测**过（40px 主字体，ascent=38 / descent=11）：
          · 'a'（ascender 顶）→ baseline = y + ascent
          · 'm'（行中线）     → baseline = y + (ascent − descent) / 2   ← 不是 (asc+desc)/2！
          · 's'（基线）       → baseline = y
        拿 (ascent+descent)/2 当 'm' 会整体下坠半个 descent（实测差 10px），别改。
        """
        anchor = (anchor + "a")[:2]
        fill = color if len(color) == 4 else color + (255,)
        base = self.font(size, thin)
        asc, desc = base.getmetrics()
        px_x, px_y = self.px(x), self.px(y)
        total = sum(self._pick(c, size, thin).getlength(c) for c in s)
        if anchor[0] == "m":
            px_x -= total / 2
        elif anchor[0] == "r":
            px_x -= total
        if anchor[1] == "a":          # ascender 顶
            px_y += asc
        elif anchor[1] == "m":        # 行中线（实测 = round((ascent − descent) / 2)，和 Pillow 逐像素对齐）
            px_y += round((asc - desc) / 2)
        # "s" → px_y 本来就是基线
        cx = px_x
        for c in s:
            f = self._pick(c, size, thin)
            self.d.text((cx, px_y), c, font=f, fill=fill, anchor="ls")
            cx += f.getlength(c)

    def shadow_text(self, x, y, txt, size, color, anchor="mm"):
        """带 1px 投影的文字（技能条上压在圆底上的等级数字）。"""
        s = str(txt)
        if self._mixed(s, size):
            # 混排时投影也要逐字符画：底层直接走主字体会画出方框、从彩色字底下透出来
            self._text_mixed(x + 1 / SCALE, y + 1 / SCALE, s, size, (0, 0, 0, 150), anchor, True)
        else:
            self.d.text((self.px(x) + 1, self.px(y) + 1), s, font=self.font(size),
                        fill=(0, 0, 0, 150), anchor=anchor)
        self.text(x, y, s, size, color, anchor=anchor)

    def chip(self, x, y, txt, size, fg, bg, pad_x=10, h=None, r=6):
        """小圆角标签（LV.x / 影画 / 词条标签都用它）；返回宽度。"""
        w = self.measure(txt, size) + pad_x * 2
        h = h or size * 1.35 + 2
        self.rrect(x, y, w, h, r, fill=bg)
        self.text(x + w / 2, y + h / 2, txt, size, fg, anchor="mm")
        return w, h

    def text_grad(self, x, y, txt, size, stops, anchor="mm", glow=0.7):
        """彩虹渐变文字（最高档 SSS / SSS+ 用）。

        Pillow 没有 CSS 的 `background-clip:text`：把文字先画成一张 L mask，
        再拿一条水平线性渐变按这张 mask 抠上去。外发光对应网页的 `filter:drop-shadow`
        —— 同一个渐变配「模糊过的 mask」当 alpha 再压一层。
        """
        mask = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(mask).text((self.px(x), self.px(y)), str(txt),
                                  font=self.font(size), fill=255, anchor=anchor)
        bb = mask.getbbox()
        if not bb:
            return
        pad = max(1, self.px(3))
        x0, y0 = max(0, bb[0] - pad), max(0, bb[1] - pad)
        x1, y1 = min(self.img.width, bb[2] + pad), min(self.img.height, bb[3] + pad)
        grad = _hgrad(x1 - x0, y1 - y0, stops)

        glow_layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        glow_layer.paste(grad, (x0, y0))
        glow_layer.putalpha(mask.filter(ImageFilter.GaussianBlur(pad))
                            .point(lambda v: int(v * glow)))
        body = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        body.paste(grad, (x0, y0))
        body.putalpha(mask)

        self.img = Image.alpha_composite(Image.alpha_composite(self.img, glow_layer), body)
        self.d = ImageDraw.Draw(self.img)

    def ellipsis(self, txt, size, max_w, thin=True):
        """超宽就截断加省略号（Pillow 没有 text-overflow）。"""
        s = str(txt)
        if self.measure(s, size, thin) <= max_w:
            return s
        while s and self.measure(s + "…", size, thin) > max_w:
            s = s[:-1]
        return (s + "…") if s else ""


# ================= 小工具 =================


def _hgrad(w: int, h: int, stops: list[tuple[float, tuple[int, int, int]]]) -> Image.Image:
    """水平线性渐变图（stops = [(位置 0~1, RGB), …]，按位置升序）。"""
    w, h = max(1, w), max(1, h)
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for i in range(w):
        d.line([(i, 0), (i, h)], fill=_stop_color(stops, i / max(1, w - 1)))
    return img


def _stop_color(stops, t: float) -> tuple[int, int, int, int]:
    """在 stops 之间线性插值取色（超出两端就取端点色）。"""
    t = min(1.0, max(0.0, t))
    if t <= stops[0][0]:
        return stops[0][1] + (255,)
    if t >= stops[-1][0]:
        return stops[-1][1] + (255,)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if p0 <= t <= p1:
            k = (t - p0) / max(1e-6, p1 - p0)
            return tuple(int(round(a + (b - a) * k)) for a, b in zip(c0, c1)) + (255,)
    return stops[-1][1] + (255,)


def _icon_raw(name: str, sub: str = "") -> Image.Image | None:
    """取本地图标的**原图**（texture2d/icon[/sub]/<name>.png）；没有对应素材返回 None。

    `name` 就是 `card_assets.*_icon_name()` 下发的那个文件名（IconAttack / Rarity_S /
    火属性 …），和网页版拼 URL 用的是同一套名字。
    """
    if not name:
        return None
    p = _a.ICON_PATH / sub / f"{name}.png" if sub else _a.ICON_PATH / f"{name}.png"
    if not p.exists():
        return None
    return Image.open(p).convert("RGBA")


def _icon(name: str, size_px: int, sub: str = "") -> Image.Image | None:
    """取图标并**直接拉成 size_px 见方**（不变形是不要的，见下）。

    这是给「稀有度徽章」那种素材用的：Rarity_*.png 画布 80×80、实心圆只占中间 56×56，
    留白必须**跟着一起缩放**，再靠负偏移把留白抵掉，可见圆才和旁边的小圆牌一样大。
    → 非正方形素材（prop 里有 64×59）会被拉变形；要「按原比例 + 居中」请用 `_icon_fit`。
    """
    im = _icon_raw(name, sub)
    return None if im is None else im.resize((size_px, size_px), Image.Resampling.LANCZOS)


def _icon_fit(name: str, box_px: int, sub: str = "") -> Image.Image | None:
    """按**原比例**缩到 box_px 见方以内（= CSS 的 object-fit:contain），由调用方自己居中贴。

    ⚠️ 属性 / 职业图标素材不一定是正方形，用 `_icon` 拉方会既变形又不在圆心 ——
    这两处必须用它（网页那边也是 `object-fit:contain`，两边才对得上）。
    """
    im = _icon_raw(name, sub)
    if im is None:
        return None
    im.thumbnail((box_px, box_px), Image.Resampling.LANCZOS)
    return im


def _rank_badge_img(rank: str, size_px: int) -> Image.Image | None:
    """S / A / B 级方徽章（`texture2d/icon/{rank}RANK.png`）→ 裁透明边后缩到 size_px 见方。

    实现统一在 `card_assets.rank_badge_img()` —— 危局 / 防卫战两张战报出图
    （`record/image.py` / `record/shiyu_image.py`）和绝境群排行（`record/rank_image.py`）
    都走同一个口，别再在这里复制一份裁边 + 缩放的逻辑。
    这里只保留「本面板只认 S / A / B」这一层收口；没素材返回 None，
    调用方退回原来的圆形稀有度标或文字小标签。
    """
    rank = str(rank or "").upper()
    return _a.rank_badge_img(rank, size_px) if rank in ("S", "A", "B") else None


def _cover(im: Image.Image, w: int, h: int, top: bool = False) -> Image.Image:
    """等比铺满 + 居中裁切（对应 CSS 的 object-fit:cover）。"""
    return ImageOps.fit(im.convert("RGBA"), (w, h), method=Image.Resampling.LANCZOS,
                        centering=(0.5, 0.0 if top else 0.5))


def _rounded(im: Image.Image, w: int, h: int, r: int, top=False) -> Image.Image:
    """圆角裁切（头像框用）。"""
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    out.paste(_cover(im, w, h, top), (0, 0), mask)
    return out


def _circle(im: Image.Image | None, d_px: int, bg=C_CARD, outline=C_BORDER, bw: int = 2) -> Image.Image:
    """圆形牌：底色 + 描边 +（可选）圆形裁切的图标（驱动盘 / 元素 / 职业都用它）。"""
    out = Image.new("RGBA", (d_px, d_px), (0, 0, 0, 0))
    dr = ImageDraw.Draw(out)
    dr.ellipse((0, 0, d_px - 1, d_px - 1), fill=bg + (255,))
    if im is not None:
        mask = Image.new("L", (d_px, d_px), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, d_px - 1, d_px - 1), fill=255)
        out.paste(_cover(im, d_px, d_px), (0, 0), mask)
        dr = ImageDraw.Draw(out)
    dr.ellipse((0, 0, d_px - 1, d_px - 1), outline=outline + (255,), width=bw)
    return out


def _wrap(cv: _Canvas, txt: str, size: float, max_w: float) -> list[str]:
    """按宽度折行（中文按字折就行）。

    ⚠️ 用 `cv.measure` 而不是 `d.textlength`：前者对韩 / 泰会逐字符累加回退字体的
    宽度，后者只认主字体 —— 混排文本按 textlength 折行会算错位置。
    """
    lines: list[str] = []
    cur = ""
    for ch in str(txt):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if cur and cv.measure(cur + ch, size) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


# ================= 主渲染 =================


async def render_agent_detail(detail: dict, nick: str = "", uid: str = "") -> Image.Image:
    """把归一化后的角色详情画成一张「代理人详细」面板图。

    `detail` = `detail.py::from_official` / `from_enka` 的返回值；
    `nick` / `uid` = 侧边栏那个绝区零角色的昵称与 UID（顶栏那一栏）。
    """
    detail = detail if isinstance(detail, dict) else {}
    props = [p for p in (detail.get("props") or []) if isinstance(p, dict)]
    discs = [d for d in (detail.get("discs") or []) if isinstance(d, dict)]
    skills = [s for s in (detail.get("skills") or []) if isinstance(s, dict)]
    weapon = detail.get("weapon") if isinstance(detail.get("weapon"), dict) else None
    note = str(detail.get("note") or "")

    # ---- 远程图一次并发取（头像 / 音擎 / 六张盘 / 阵营 LOGO），失败各自降级为空 ----
    urls = [str(detail.get("icon") or "")]
    if weapon:
        urls.append(str(weapon.get("icon") or ""))
    urls += [str(d.get("icon") or "") for d in discs]
    urls.append(str(detail.get("camp_icon") or ""))
    got = list(await asyncio.gather(*[_a.fetch_img(u) for u in urls]))
    av_img = got[0]
    w_img = None
    cur = 1
    if weapon:
        w_img = got[cur]
        cur += 1
    disc_imgs = got[cur:-1]
    camp_img = got[-1]

    cv = _Canvas(W, 1700)
    y = PAD

    # ---------------- ⓪ 顶栏：玩家名字 + UID ----------------
    if nick:
        cv.text(PAD, y, nick, 19, T_MAIN)
        y += 26
    if uid:
        cv.text(PAD, y, f"UID: {uid}", 11.5, T_SUB)
        y += 17
    if nick or uid:
        y += 10

    # ---------------- 降级说明（Enka 源才有）----------------
    if note:
        lines = _wrap(cv, note, 12, INNER - 20)
        nh = len(lines) * 12 * 1.7 + 12
        cv.rrect(PAD, y, INNER, nh, 8, fill=C_NOTE_BG, outline=C_NOTE_BD)
        ty = y + 6
        for ln in lines:
            cv.text(PAD + 10, ty, ln, 12, ORANGE_P)
            ty += 12 * 1.7
        y += nh + 12

    # ---------------- ① 顶部：立绘卡 + 面板属性 + 技能条 ----------------
    top_y = y
    _draw_agent_card(cv, PAD, top_y, detail, av_img, camp_img)

    # 右列：先画技能条（高度固定），属性面板吃掉剩下的高度 —— 和 CSS 的
    # 「立绘卡定行高、属性面板 flex:1 填满」是同一个账。
    right_x = RIGHT_X
    sb_y = top_y + AG_H - SB_PANEL_H
    stats_h = AG_H - 8 - SB_PANEL_H
    _draw_skillbar(cv, right_x, sb_y, skills, discs, disc_imgs)
    _draw_stats(cv, right_x, top_y, RIGHT_W, stats_h, props, detail)

    # ---------------- ②③ 驱动盘区（音擎嵌左上角 + 三列六张盘）----------------
    dwrap_y = top_y + AG_H + 10
    dh = _draw_disc_area(cv, dwrap_y, detail, weapon, w_img, discs, disc_imgs)

    return cv.img.crop((0, 0, cv.px(W), cv.px(dwrap_y + dh + PAD)))


# ---------------- ① 立绘卡 ----------------


def _draw_agent_card(cv: _Canvas, x: float, y: float, detail: dict, av_img,
                     camp_img=None) -> None:
    rarity = str(detail.get("rarity") or "").upper()
    border = RAR.get(rarity, C_BORDER)

    cv.rrect(x, y, AG_W, AG_H, 12, fill=C_PANEL)

    ax = x + AG_PAD
    cy = y + AG_PAD
    # 「AGENT INFO」小标签
    cv.chip(ax, cy, "AGENT INFO", 9, T_MUTE, C_CHIP, pad_x=7, h=AG_TAB_H, r=4)
    cy += AG_TAB_H + 8

    # 头像框（尺寸 = 素材真实比例 152×186 → 148×181，别写回正方形）
    # 圆角 = PIC_RADIUS（6，用户 2026-10-05 嫌原来 12/10/8 太圆）；三层关系保持原样：
    # 外框比内框大 2（正好一圈 2px 边框），图像裁切再小 2。
    cv.rrect(ax, cy, PIC_W + 2 * PIC_BW, PIC_H + 2 * PIC_BW, PIC_RADIUS + 2, fill=border)
    cv.rrect(ax + PIC_BW, cy + PIC_BW, PIC_W, PIC_H, PIC_RADIUS, fill=(26, 28, 33))
    if av_img is not None:
        cv.paste(_rounded(av_img, cv.px(PIC_W), cv.px(PIC_H), cv.px(PIC_RADIUS - 2), top=True),
                 ax + PIC_BW, cy + PIC_BW)
    else:
        cv.text(ax + PIC_BW + PIC_W / 2, cy + PIC_BW + PIC_H / 2,
                (str(detail.get("name") or "?")[:1]), 44, (90, 95, 104), anchor="mm")

    _draw_badges(cv, ax + PIC_BW, cy + PIC_BW, detail)

    # 名字 / 等级（左对齐）
    ny = cy + PIC_H + 2 * PIC_BW + 10
    cv.text(ax, ny + AG_NAME_H / 2, cv.ellipsis(detail.get("name") or "", 17, PIC_W),
            17, T_MAIN, anchor="lm")
    ly = ny + AG_NAME_H + 6
    cv.chip(ax, ly, f"LV.{int(detail.get('level') or 0)}", 12, T_LV, C_CHIP, pad_x=10, h=AG_LV_H)

    # 阵营 LOGO（官方 group_icon_path）：压在立绘卡**右下角** —— 和网页 `.av-camp`
    # 同一个位置、同一个高度（CAMP_H=53）、同一个不透明度（.75）。
    # 它是**最后画**的、绝对坐标直接算，所以不参与上面任何排布（网页那边也是 absolute）。
    if camp_img is not None and camp_img.width and camp_img.height:
        cw = min(CAMP_H * camp_img.width / camp_img.height, AG_W - 2 * AG_PAD)
        cv.paste(_fade(camp_img, 0.75), x + AG_W - AG_PAD - cw,
                 y + AG_H - AG_PAD - CAMP_H, cw, CAMP_H)


def _draw_badges(cv: _Canvas, px_: float, py_: float, detail: dict) -> None:
    """头像框左上角竖排：稀有度 → 属性 → 职业；右上角影画数。

    稀有度徽章自 2026-10-05 起换成**图片徽章**：`texture2d/icon/{S,A,B}RANK.png`
    （带「S RANK」字样的方徽章，与绝境群排行出图同一套素材），按 `RAR_BADGE`=24.5px 画。
    取不到素材时依次退回旧的圆形稀有度标 → 文字小标签。

    ⚠️ **这一列是「中心轴对齐」的**（用户要求，2026-10-05）：三件套水平中轴必须重合。
    下面属性 / 职业两张圆牌是 24px 的方盒子、图形在其中居中 → 它们的轴心在 `bx + 12`。
    所以徽章不能直接从 `bx` 起画（左对齐就歪了 3px 多），要按
    `bx + (24 − RAR_BADGE) / 2` **居中**（和 `_draw_agent_card` 里那两张圆牌同一个数）。
    徽章图本身已按 `getbbox()` 裁过透明边，所以它的「可见图形」就是 `RAR_BADGE` 见方。

    ⚠️ 退回**圆形稀有度标**（Rarity_*.png）时仍要**按 35px 画并往左上各挪 6px**：
    它画布 80×80、实心圆只占中间 56×56，不这样处理可见圆就落不到那条中轴上。

    ⚠️⚠️ 无论用哪种样式画稀有度，**都要把 `by` 往下推一格**：不推的话下面那张
    「属性」圆牌会正好画在同一个位置上，把稀有度整个盖住（页面上就「S/A 级没显示」）。

    ⚠️ 驱动盘卡片上的稀有度标**不走这里**（`_draw_disc` 自己画，仍是圆形
    `Rarity_*.png`）—— 用户明确说过驱动的 S/A 不变，别顺手一起换。
    """
    bx, by = px_ + 2, py_ + 2
    rarity = str(detail.get("rarity") or "").upper()
    badge = _rank_badge_img(rarity, cv.px(RAR_BADGE))
    if badge is not None:
        # 24 = 下面属性/职业圆牌的盒子边长；这样画出来的可见方块和那两个圆的**中轴**重合
        cv.paste(badge, bx + (24 - RAR_BADGE) / 2, by)
        by += RAR_BADGE + 3                   # ← 别删！见上面第二条 ⚠️
    else:
        rar = _icon(f"Rarity_{rarity}", cv.px(35))
        if rar is not None:
            cv.paste(rar, bx - 6, by - 6)
            by += 27                          # ← 别删！见上面第二条 ⚠️
        elif rarity:
            cv.chip(bx, by, rarity, 11, (36, 23, 3), RAR.get(rarity, C_CHIP), pad_x=7, h=18, r=5)
            by += 21                          # ← 别删！见上面第二条 ⚠️

    for icon_name, label, sub, pad in (
        (detail.get("element_icon"), detail.get("element"), "", 2),
        (detail.get("profession_icon"), detail.get("profession"), "pro", 0),
    ):
        # ⚠️ `sub` 一定要传进去：职业图标在 icon/**pro**/ 子目录下，不传就找不到，
        # 页面会退回文字标签（「强攻」「支援」…），看起来像「职业图标没做」。
        # 内容区宽 = 24 − 2×1(边框) − 2×pad，和网页 .av-mi 的 padding 对齐；
        # 按原比例 contain + 居中（网页是 object-fit:contain）。
        box = cv.px(24 - 2 * pad - 2)
        ic = _icon_fit(str(icon_name or ""), box, sub)
        if ic is not None:
            plate = Image.new("RGBA", (cv.px(24), cv.px(24)), (0, 0, 0, 0))
            d = ImageDraw.Draw(plate)
            d.ellipse((0, 0, cv.px(24) - 1, cv.px(24) - 1), fill=(21, 23, 29, 255))
            iw, ih = ic.size
            plate.paste(ic, ((cv.px(24) - iw) // 2, (cv.px(24) - ih) // 2), ic)
            d.ellipse((0, 0, cv.px(24) - 1, cv.px(24) - 1), outline=(255, 255, 255, 51), width=1)
            cv.paste(plate, bx, by)
        elif label:
            cv.chip(bx, by + 3, label, 10, T_BODY, (56, 58, 66), pad_x=6, h=16, r=4)
        by += 27

    # 影画数（右上角）：方块边长 = 稀有度徽章（RAR_BADGE = 24.5），和头像左上角
    # 的 S/A 方徽章一样大（用户 2026-10-05 要求「影画标 需要改成和 A/S 等级图标一样大小」）；
    # 圆角 RK_RADIUS（4）—— 原来 18px/5px 太小、太圆。右 / 上各 2px 贴头像框内沿，
    # 与左上角 .av-tags 对称。
    rank = detail.get("rank")
    if rank is not None:
        txt = str(int(rank or 0))
        sz = RAR_BADGE
        cv.alpha_rrect(px_ + PIC_W - sz - 2, py_ + 2, sz, sz, RK_RADIUS, (0, 0, 0, 140))
        cv.text(px_ + PIC_W - 2 - sz / 2, py_ + 2 + sz / 2, txt, 15, (255, 255, 255), anchor="mm")


# ---------------- 右列：面板属性 ----------------


def _draw_stats(cv: _Canvas, x: float, y: float, w: float, h: float,
                props: list[dict], detail: dict) -> None:
    cv.rrect(x, y, w, h, 12, fill=C_PANEL)
    if not props:
        tip = "Enka 备用源没有面板属性" if detail.get("source") == "enka" else "没有面板属性"
        cv.text(x + w / 2, y + h / 2, tip, 12, T_MUTE, anchor="mm")
        return

    cols, rows = 2, (len(props) + 1) // 2
    cell_w = w / cols
    row_h = (h - 12 - (rows - 1) * 2) / rows        # 6 行平分面板高（≈30px/行），天然不溢出
    for r in range(rows):                    # 4n+1 / 4n+2 → 隔行铺一整条底纹（跨两列不断开）
        if r % 2 == 0:
            cv.rrect(x, y + 6 + r * (row_h + 2), w, row_h, 6, fill=C_ROW)
    for i, p in enumerate(props):
        c, r = i % cols, i // cols
        _draw_stat_cell(cv, x + c * cell_w, y + 6 + r * (row_h + 2), cell_w, row_h, p)


def _draw_stat_cell(cv: _Canvas, x: float, y: float, w: float, h: float, p: dict) -> None:
    cy = y + h / 2
    icon = _icon(str(p.get("icon") or ""), cv.px(16), "prop")
    tx = x + 10
    if icon is not None:
        cv.paste(icon, tx, cy - 8, 16, 16)
        tx += 22
    cv.text(tx, cy, cv.ellipsis(p.get("name") or "", 14, x + w - 10 - tx), 14, T_DIM, anchor="lm")

    final = str(p.get("final") or p.get("base") or "")
    right = x + w - 10
    cv.text(right, cy, final, 15, T_MAIN, anchor="rm")
    base, add = str(p.get("base") or ""), str(p.get("add") or "")
    if add and add != base:                  # base 与「+add」两行绿字（照 .av-sv em）
        ex = right - cv.measure(final, 15) - 6
        cv.text(ex, cy + 7, f"+{add}", 10.5, GREEN, anchor="rm", thin=True)
        cv.text(ex, cy - 7, base, 10.5, GREEN, anchor="rm", thin=True)


# ---------------- 右列：技能条 ----------------


def _draw_skillbar(cv: _Canvas, x: float, y: float, skills: list[dict],
                   discs: list[dict], disc_imgs: list) -> None:
    """技能条：整张 350×70 的图铺开，等级数字按百分比叠到对应那个圆上。"""
    cv.rrect(x, y, RIGHT_W, SB_PANEL_H, 12, fill=C_PANEL)
    if skills:
        iw = min(RIGHT_W - 18, SB_IMG_W)
        ih = iw * 70 / 350
        ix, iy = x + 9, y + 3 + (SB_PANEL_H - 6 - ih) / 2
        if SKILL_BAR_PATH.exists():
            cv.paste(Image.open(SKILL_BAR_PATH).convert("RGBA"), ix, iy, iw, ih)
        for s in skills:
            pos = int(s.get("pos") or 0)
            lv = int(s.get("level") or 0)
            color = GOLD if lv >= 11 else (SBLUE if lv >= 6 else (T_BODY if lv >= 3 else T_MUTE))
            cv.shadow_text(ix + (32 + pos * 50.3) / 350 * iw, iy + 50 / 70 * ih, lv, 15, color)
        return

    # 没有技能数据时退回「驱动盘图标条」（Enka 源偶尔缺技能）
    if discs:
        for i, (d, im) in enumerate(zip(discs[:6], disc_imgs[:6])):
            cx = x + RIGHT_W * (i + 0.5) / 6
            cv.paste(_circle(im, cv.px(36), bg=(26, 28, 33)), cx - 18, y + (SB_PANEL_H - 36) / 2)


# ---------------- ②③ 驱动盘区 ----------------


def _draw_disc_area(cv: _Canvas, y: float, detail: dict, weapon: dict | None,
                    w_img, discs: list[dict], disc_imgs: list) -> float:
    """画驱动盘区，返回这一整块的高度（调用方靠它裁掉画布下面的空白）。"""
    h = 13 + DTOP_H + 12 + (_grid_h(discs) if discs else 20) + 13
    cv.rrect(PAD, y, INNER, h, 12, fill=C_PANEL, outline=C_DWRAP_BD)

    dx = PAD + DWRAP_PAD + 1
    dw = DWRAP_IN
    top_y = y + 13

    # 左上角：音擎小卡（自己一块**更深**的底，压在浅色驱动盘区上）
    # ⚠️ `_draw_weapon` 返回的是**宽度**不是绝对坐标，必须自己加 dx 起点；
    # 直接拿返回值当 x 用的话整块会左移一个 dx，正好压在音擎黑卡上。
    nx = dx
    if weapon:
        nx = dx + _draw_weapon(cv, dx, top_y, weapon, w_img) + 16

    # 最右：驱动盘评分（S+ / S / A / B …）。字母与配色：
    #   S+ / S / SS / SS+ → 橙；A → 紫；B / C → 浅蓝；
    #   **SSS / SSS+ → 彩虹渐变**（最高档，网页用 .av-rate.rb）。
    #   评级不到 A 时官方给的是 DEFAULT 占位，那不是真评分 → 整块不画
    #   （detail._clean_rating 已先过滤过，这里再兜一层）。
    rating = str(detail.get("rating") or "").upper().replace("ER_", "")
    if rating in ("DEFAULT", "NONE", "NULL"):
        rating = ""
    side_w = 0.0
    if rating:
        side_w = max(56, cv.measure(rating, 24) + 8)
        sx = dx + dw - side_w
        tx = sx + side_w / 2
        if rating.startswith("SSS"):
            cv.text_grad(tx, top_y + DTOP_H / 2 - 8, rating, 24, RATING_GRAD)
        else:
            cv.text(tx, top_y + DTOP_H / 2 - 8, rating, 24,
                    RATING_COLOR.get(rating, ORANGE), anchor="mm")
        cv.text(tx, top_y + DTOP_H / 2 + 14, "驱动盘", 10, T_MUTE, anchor="mm")

    # 中间：有效副属性命中 + 词条标签（透明底，直接坐在驱动盘区的底上）
    valid = int(detail.get("valid_cnt") or 0)
    tags = [str(t) for t in (detail.get("plan_tags") or []) if t]
    if valid > 0 or tags:
        _draw_plan(cv, nx, top_y, (dx + dw - side_w - 16 if side_w else dx + dw) - nx, valid, tags)

    # 下面：三列六张盘
    gy = top_y + DTOP_H + 12
    if not discs:
        cv.text(dx, gy + 4, "没读到驱动盘（该角色可能未装备，或数据源没给）。", 12, T_MUTE)
        return h
    _draw_disc_grid(cv, dx, gy, discs, disc_imgs)
    return h


def _draw_weapon(cv: _Canvas, x: float, y: float, w: dict, w_img) -> float:
    """音擎小卡。

    返回值是它**占的宽度**（不是右边缘的绝对坐标！）—— 后面排「有效副属性」的
    调用方要自己加上自己的起点，别直接把返回值当 x 用。
    """
    wrow_h = 6 + 52 + 2 * 2 + 6
    name = str(w.get("name") or "")
    lv_txt = f"LV.{int(w.get('level') or 0)}"
    lv_w = cv.measure(lv_txt, 12) + 20
    max_w = 290
    name_w = min(cv.measure(name, 15), max_w - (6 + 56 + 10 + 10 + lv_w + 12))
    wrow_w = 6 + 56 + 10 + name_w + 10 + lv_w + 12

    cv.rrect(x, y + (DTOP_H - wrow_h) / 2, wrow_w, wrow_h, 10, fill=C_CARD)
    iy = y + (DTOP_H - wrow_h) / 2 + 6
    rarity = str(w.get("rarity") or "").upper()
    cv.rrect(x + 6, iy, 52 + 4, 52 + 4, 12, fill=RAR.get(rarity, RAR["B"]))
    cv.rrect(x + 8, iy + 2, 52, 52, 10, fill=(26, 28, 33))
    if w_img is not None:
        cv.paste(_rounded(w_img, cv.px(52), cv.px(52), cv.px(9)), x + 8, iy + 2)
    # 进阶星：压在图标下沿，前 N 颗点亮（★ 用 zzz 字体画，不是图标素材）
    star = min(5, max(1, int(w.get("star") or 0)))
    crown_y = iy + 2 + 52 - 13
    cv.alpha_rrect(x + 8, crown_y, 52, 13, 0, (0, 0, 0, 158))
    for n in range(5):
        # 未点亮的星用半透明白压在图标上 ≈ #575757；Pillow 的文字没法上 alpha，用实色近似
        cv.text(x + 8 + 52 * (n + 0.5) / 5, crown_y + 6.5, "★", 10,
                (255, 255, 255) if n < star else (104, 106, 112), anchor="mm")
    cv.text(x + 6 + 56 + 10, y + DTOP_H / 2, cv.ellipsis(name, 15, name_w), 15, T_MAIN, anchor="lm")
    cv.chip(x + wrow_w - 12 - lv_w, y + DTOP_H / 2 - 10, lv_txt, 12, T_LV, C_CHIP, pad_x=10, h=20)
    return wrow_w


def _draw_plan(cv: _Canvas, x: float, y: float, w: float, valid: int, tags: list[str]) -> None:
    head = 18
    tag_h = 17
    gap = 6
    block = head + gap + tag_h
    ty = y + (DTOP_H - block) / 2

    tx = x
    cv.text(tx, ty + head / 2, "驱动盘有效副属性共命中", 12.5, T_DIM, anchor="lm")
    tx += cv.measure("驱动盘有效副属性共命中", 12.5)
    cv.text(tx + 3, ty + head / 2, valid, 17, ORANGE, anchor="lm")
    tx += cv.measure(valid, 17) + 3
    cv.text(tx, ty + head / 2, "次", 12.5, T_DIM, anchor="lm")

    bx = x
    by = ty + head + gap
    for t in tags:
        tw = cv.measure(t, 11) + 14
        if bx + tw > x + w:
            cv.text(bx + 4, by + tag_h / 2, "…", 11, ORANGE_T, anchor="lm")
            break
        cv.rrect(bx, by, tw, tag_h, 4, fill=C_TAG_BG, outline=C_TAG_BD)
        cv.text(bx + tw / 2, by + tag_h / 2, t, 11, ORANGE_T, anchor="mm")
        bx += tw + 4


# ---------------- 六张驱动盘 ----------------


def _disc_block_h(d: dict) -> float:
    """单张盘卡「主词条 + 副词条」深色面板的高度。"""
    subs = len([s for s in (d.get("subs") or []) if isinstance(s, dict)])
    return (7 + 23 + 7) + ((3 + subs * 21 + 6) if subs else 0)


def _disc_h(d: dict) -> float:
    head = 17.5 + 4 + 15
    return DISC_PAD_Y * 2 + head + 9 + _disc_block_h(d) + 2


def _grid_h(discs: list[dict]) -> float:
    """三列网格的总高（同一行的三张按最高的那张算）。"""
    if not discs:
        return 0
    rows = [discs[i:i + 3] for i in range(0, len(discs), 3)]
    return sum(max(_disc_h(d) for d in row) for row in rows) + (len(rows) - 1) * DISC_GAP


def _draw_disc_grid(cv: _Canvas, x: float, y: float, discs: list[dict], imgs: list) -> None:
    rows = [list(range(i, min(i + 3, len(discs)))) for i in range(0, len(discs), 3)]
    cy = y
    for row in rows:
        row_h = max(_disc_h(discs[i]) for i in row)
        for j, i in enumerate(row):
            _draw_disc(cv, x + j * (DISC_W + DISC_GAP), cy, DISC_W, row_h, discs[i], imgs[i])
        cy += row_h + DISC_GAP


def _draw_disc(cv: _Canvas, x: float, y: float, w: float, h: float, d: dict, im) -> None:
    """一张驱动盘卡：头部那条跟区域底同色，主/副词条另垫深色面板，整卡 1px 黑描边。"""
    cv.rrect(x, y, w, h, 12, fill=C_PANEL, outline=(0, 0, 0), width=1)
    ix = x + 1 + DISC_PAD_X
    iw = w - 2 * (1 + DISC_PAD_X)
    cy = y + 1 + DISC_PAD_Y

    # ---- 头部：名字[位] + 稀有度/LV/未命中 + 盘图标 ----
    disc_icon_w = 44
    title_w = iw - disc_icon_w - 8
    slot = d.get("slot")
    name = str(d.get("suit") or d.get("name") or "")
    nx = ix
    nm = cv.ellipsis(name, 13, title_w - cv.measure(f"[{slot}]", 11))
    cv.text(nx, cy + 8.75, nm, 13, T_MAIN, anchor="lm")
    cv.text(nx + cv.measure(nm, 13), cy + 9.5, f"[{slot}]", 11, T_TAG, anchor="lm")

    my = cy + 17.5 + 4
    rarity = str(d.get("rarity") or "").upper()
    rar = _icon(f"Rarity_{rarity}", cv.px(20))
    mx = ix
    if rar is not None:
        cv.paste(rar, mx - 1.5, my - 0.5, 20, 20)
        mx += 18
    elif rarity:
        cw, _ = cv.chip(mx, my, rarity, 10, (36, 23, 3), (240, 177, 62), pad_x=4, h=15, r=3)
        mx += cw + 5
    lv_txt = f"LV{int(d.get('level') or 0)}"
    lw = cv.measure(lv_txt, 10) + 10
    cv.chip(mx, my, lv_txt, 10, (207, 211, 218), C_CHIP, pad_x=5, h=15, r=3)
    mx += lw + 5
    miss = int(d.get("invalid_cnt") if d.get("invalid_cnt") is not None else -1)
    if miss == 0:
        cv.text(mx, my + 7.5, "全命中", 10, GREEN, anchor="lm")
    elif miss > 0:
        cv.text(mx, my + 7.5, f"未命中{miss}次", 10, T_SUB, anchor="lm")

    cv.paste(_circle(im, cv.px(disc_icon_w), bg=(26, 28, 33)),
             x + w - 1 - DISC_PAD_X - disc_icon_w, cy + 1)

    # ---- 主词条 + 副词条：一整块深色面板（主词条圆上两角、副词条圆下两角）----
    by = cy + 17.5 + 4 + 15 + 9
    block_h = _disc_block_h(d)
    cv.rrect(ix, by, iw, block_h, 8, fill=C_SUNK)

    main = d.get("main") if isinstance(d.get("main"), dict) else {}
    subs = [s for s in (d.get("subs") or []) if isinstance(s, dict)]
    if main.get("name"):
        cv.text(ix + 8, by + 7 + 11.5, cv.ellipsis(main.get("name"), 12, iw - 24), 12, T_DIM, anchor="lm")
        cv.text(ix + iw - 8, by + 7 + 11.5, str(main.get("value") or ""), 17, T_MAIN, anchor="rm")
        sy = by + 7 + 23
        # 分隔线只在下面还有副词条时画（没有副词条时主词条就是面板最后一块，不该有条线）
        if subs:
            cv.line(ix + 1, sy, ix + iw - 1, sy, C_DMAIN_BD)
    else:
        sy = by

    if not subs:
        return
    sy += 3
    for s in subs:
        valid = bool(s.get("valid"))
        fg = ORANGE if valid else T_DIM
        cv.d.ellipse((cv.px(ix + 8), cv.px(sy + 7.5 - 2.5), cv.px(ix + 13), cv.px(sy + 7.5 + 2.5)),
                     fill=(ORANGE if valid else (90, 95, 104)) + (255,))
        tx = ix + 13 + 6
        cv.text(tx, sy + 10.5, cv.ellipsis(s.get("name") or "", 11, iw - 90), 11, fg, anchor="lm")
        tx += cv.measure(s.get("name") or "", 11) + 5
        times = int(s.get("times") or 0)
        if times > 1:
            bt = f"+{times - 1}"
            bw = cv.measure(bt, 9) + 8
            cv.rrect(tx, sy + 3.5, bw, 14, 3, fill=C_SUBBG)
            cv.text(tx + bw / 2, sy + 10.5, bt, 9, ORANGE_T, anchor="mm")
        cv.text(ix + iw - 8, sy + 10.5, str(s.get("value") or ""), 11,
                ORANGE_P if valid else T_BODY, anchor="rm")
        sy += 21
