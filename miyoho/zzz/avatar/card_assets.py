"""代理人详细面板图的素材与字体（从 ZZZeroUID 搬来的贴图 + 我们自己的取图方式）。

素材都在 `texture2d/`：
  · `texture2d/*.png`        版面底图（title / weapon_bar / equip_bg / skill_bar …）
  · `texture2d/icon/*`       小图标（属性 / 职业 / 元素 / 稀有度 / 评级字母 / 背景图）
  · `fonts/zzz_fonts.ttf`    正文（粗一点）
  · `fonts/zzz_thins.ttf`    数值与副属性（细一点）

⚠️ 这两份 TTF 是 ZZZeroUID 项目里的字体文件（随它的仓库一起分发），
版面坐标都是按这套字体的度量算的 —— 换成别的字体会让文字宽度变化、可能溢出，
所以**别顺手改成系统字体**。

图片（角色立绘 / 音擎 / 驱动盘）走 `core.asset_cache`：官方接口给的 icon 是远程
URL，先落到本地缓存再打开 —— 和危局 / 防卫战战报图同一套做法。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...core import asset_cache, image_gen as _ig

_HERE = Path(__file__).resolve().parent
TEXT_PATH = _HERE / "texture2d"
ICON_PATH = TEXT_PATH / "icon"
STAR_PATH = TEXT_PATH / "star"
_FONT = _HERE / "fonts" / "zzz_fonts.ttf"
_FONT_THIN = _HERE / "fonts" / "zzz_thins.ttf"

# 属性 id → 图标文件名（官方给的是数字 id，素材是名字）
PROP_ID = {
    "111": "IconHpMax",
    "121": "IconAttack",
    "131": "IconDef",
    "122": "IconBreakStun",
    "201": "IconCrit",
    "211": "IconCritDam",
    "314": "IconElementAbnormalPower",
    "312": "IconElementMystery",
    "231": "IconPenRatio",
    "232": "IconPenValue",
    "305": "IconSpRecover",
    "310": "IconSpGetRatio",
    "115": "IconSpMax",
    "315": "IconPhysDmg",
    "316": "IconFire",
    "317": "IconIce",
    "318": "IconThunder",
    "319": "IconDungeonBuffEther",
}

# 职业 id → 图标（1 强攻 / 2 击破 / 3 异常 / 4 支援 / 5 防御 / 6 命破 / 7 锋御）
# 7 = 锋御（官方英文 Armorer，3.2 版本新增的第 7 个职业，首位代理人克拉蕾 1611）。
PRO_ID = {
    "1": "IconAttack", "2": "IconStun", "3": "IconAnomaly",
    "4": "IconSupport", "5": "IconDefense", "6": "IconRupture",
    "7": "IconArmorer",
}

ELEMENT_TYPE = {203: "电属性", 205: "以太属性", 202: "冰属性", 200: "物理属性", 201: "火属性",
                204: "风属性",
                # 206 = 玄墨（命破系）按元素顺序推定，官方接口若另有编号改这里即可
                206: "玄墨属性",
                # 300 = 流明（Lumen / Lumiflux，3.1 新属性，目前仅蕾米埃尔）；
                # 官方给的是 300 而不是 20x —— 见 genshin.py `ZZZElementType.LUMIFLUX = 300`
                300: "流明属性"}

# 元素图标文件名与元素名一致（texture2d/icon/<元素名>.png，中文文件名）
ELEMENT_ICON = ELEMENT_TYPE

# 面板区（左上那张长条）用的 prop_id → 图标，与上面的键不是同一套（官方在这里给的是简 id）
PANEL_PROP_ID = {
    "1": "IconHpMax", "2": "IconAttack", "3": "IconDef", "4": "IconBreakStun",
    "5": "IconCrit", "6": "IconCritDam", "7": "IconElementAbnormalPower",
    "8": "IconElementMystery", "9": "IconPenRatio", "10": "IconPenValue",
    "11": "IconSpRecover", "12": "IconSpGetRatio", "13": "IconSpMax",
    "19": "IconSheerForce", "232": "IconPenValue", "315": "IconPhysDmg",
    "316": "IconFire", "317": "IconIce", "318": "IconThunder",
    "319": "IconDungeonBuffEther",
}


def font(size: int) -> ImageFont.FreeTypeFont:
    """正文（略粗）：标题、名字、等级。"""
    return ImageFont.truetype(str(_FONT), size=max(8, int(size)))


def thin(size: int) -> ImageFont.FreeTypeFont:
    """数值 / 副属性（细体）：版面按它的宽度排的，别换成粗体。"""
    return ImageFont.truetype(str(_FONT_THIN), size=max(8, int(size)))


def _blank(w: int, h: int) -> Image.Image:
    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def _icon(name: str, w: int, h: int, sub: str = "") -> Image.Image:
    """取图标。`sub` 是子目录：属性图标在 `icon/prop/`、职业图标在 `icon/pro/`，
    元素 / 稀有度 / 评级 / 背景图直接放在 `icon/` 根下（`sub=""`）。"""
    if not name:
        return _blank(w, h)
    p = ICON_PATH / sub / f"{name}.png" if sub else ICON_PATH / f"{name}.png"
    if not p.exists():
        return _blank(w, h)
    return Image.open(p).convert("RGBA").resize((w, h))


def _icon_name_of_prop(_id) -> str:
    """属性 id → 图标文件名。官方在不同接口给两套 id：面板是简 id（1~13、19），
    其余是 3 位数（111/121/…，五位数时取前三位）。"""
    key = str(_id)
    name = PANEL_PROP_ID.get(key)
    if not name and key.isdigit():
        name = PROP_ID.get(key[:3])
    return name or key


def get_prop_img(_id, w: int = 40, h: int = 40) -> Image.Image:
    """属性图标：先按面板那套简 id 查，再退回三位数 id 那套。"""
    return _icon(_icon_name_of_prop(_id), w, h, "prop")


def get_pro_img(_id, w: int = 50, h: int = 50) -> Image.Image:
    return _icon(PRO_ID.get(str(_id), ""), w, h, "pro")


def get_element_img(element_id, w: int = 40, h: int = 40) -> Image.Image:
    name = ELEMENT_TYPE.get(int(element_id or 0))
    return _icon(name or "", w, h)


# ---------------- 图标「文件名」查询（不画图，给网页版拼 URL 用） ----------------
# 网页版（frag/zzz-avatar.*）拿到的属性 / 元素 / 职业图标是**文件名**，
# 前面拼 `/admin/plugin-static/_vendor/miyoushe/src/zzz/avatar/texture2d/icon/`。
# 文件不存在就返回空串，前端便退回纯文字 —— 免得页面上出现裂图。


def _exists(name: str, sub: str = "") -> bool:
    if not name:
        return False
    p = ICON_PATH / sub / f"{name}.png" if sub else ICON_PATH / f"{name}.png"
    return p.exists()


def prop_icon_name(_id) -> str:
    """面板属性 id → 图标名（如 121 → IconAttack）；没有对应图标返回 ""。"""
    name = _icon_name_of_prop(_id)
    return name if _exists(name, "prop") else ""


# 职业 / 属性：官方源给的是数字 id，Enka 源给的是中文名 —— 两种都认。
# 流明在 Enka 侧叫 "Lumen"（官方中文「流明」，英文亦作 Lumiflux），别名一并收。
PRO_NAME = {"强攻": "IconAttack", "击破": "IconStun", "异常": "IconAnomaly",
            "支援": "IconSupport", "防护": "IconDefense", "命破": "IconRupture",
            "锋御": "IconArmorer"}
ELEMENT_NAME = {"物理": "物理属性", "火": "火属性", "冰": "冰属性", "电": "电属性",
                "风": "风属性", "以太": "以太属性", "玄墨": "玄墨属性",
                "流明": "流明属性", "Lumen": "流明属性", "Lumiflux": "流明属性"}


def pro_icon_name(_id) -> str:
    """职业（id 1~6 或「强攻/击破/…」）→ 图标名；没有对应图标返回 ""。"""
    key = str(_id)
    name = PRO_ID.get(key) or PRO_NAME.get(key, "")
    return name if _exists(name, "pro") else ""


def element_icon_name(_id) -> str:
    """属性（id 200~206 或「物理/火/…/风/以太」）→ 元素图标名（中文文件名）。"""
    name = ""
    try:
        name = ELEMENT_ICON.get(int(_id), "")
    except (TypeError, ValueError):
        name = ""
    if not name:
        name = ELEMENT_NAME.get(str(_id), "")
    return name if _exists(name) else ""


def get_rarity_img(rank: str, w: int = 80, h: int = 80) -> Image.Image:
    rank = str(rank or "").upper()
    return _icon(f"Rarity_{rank}", w, h) if rank in ("S", "A", "B", "C") else _blank(w, h)


def get_rank_img(rank: str, w: int = 40, h: int = 40) -> Image.Image:
    rank = str(rank or "").upper()
    return _icon(f"{rank}RANK", w, h) if rank in ("S", "A", "B", "S+") else _blank(w, h)


# ---------------- 稀有度「方徽章」图片：QQ 出图贴在头像左上角的那一枚 ----------------
# `{S,A,B,S+}RANK.png`（带「S RANK」字样的方形徽章）原尺寸都是 128×128。
RANK_SRC = 128
_rank_badge_cache: dict = {}


def rank_badge_img(rank: str, size: int) -> Image.Image | None:
    """S / A / B 级**方徽章**（`texture2d/icon/{rank}RANK.png`）→ 裁掉透明边后缩到 size 见方。

    给 QQ 出图里「贴在头像左上角的稀有度标」用（危局战报 / 防卫战战报 / 绝境群排行），
    和网页浮层上那枚 `SRANK.png` 是**同一套素材**。没有素材（等级不在 S/A/B/S+，
    或文件缺失）返回 None，调用方退回文字/图形小牌，不会开天窗。结果按 (等级, 尺寸) 缓存。

    两个关键细节（改动前先看这里）：
    · **必须先裁掉素材自带的透明边**：实测内容只占 (9,7,119,121)，四周各留约 7%，
      不裁就缩的话外框虽然也是 size，可见图形只有 ≈0.86×size，贴在头像上会比
      右上角的影画数角标小一圈、看着像没对齐。
    · 缩放走 LANCZOS：`_icon` 内部默认 NEAREST，128→30 这种大倍率缩小会丢像素、
      边缘发锯，所以先按原尺寸取回来自己缩。
    """
    key = (str(rank or "").upper(), int(size))
    if key not in _rank_badge_cache:
        im = get_rank_img(key[0], RANK_SRC, RANK_SRC)
        box = im.getbbox()
        if not box:
            _rank_badge_cache[key] = None
        else:
            im = im.crop(box)
            if im.size != (key[1], key[1]):
                im = im.resize((key[1], key[1]), Image.LANCZOS)
            _rank_badge_cache[key] = im
    return _rank_badge_cache[key]


def weapon_icon_img(url: str, size: int) -> Image.Image | None:
    """音擎图标小徽章：深色圆角底 + 音擎图标内缩，size×size，供头像角落叠加。

    给危局 / 防卫战「全面」版战报用 —— 贴在代理人头像**左下角**，大小和 A/S 徽章一致
    （调用方传 `image_gen.RANK_CHIP`）。url 是已本地化的路由
    （`/miyoho-asset/...`，由调用方先 `rewrite_assets` 落本地），
    取不到返回 None，调用方就不画音擎角标、不会开天窗。

    为什么自带深色圆角底：音擎素材是带透明通道的整图（不是 {S,A}RANK 那种不透明方徽章），
    直接贴到头像角落会和头像混在一起看不清；垫一层半透明深色底框，读起来是「又一枚小徽章」，
    与左上角的 A/S 徽章视觉上对齐。
    """
    if not url:
        return None
    im = _ig._load(url)
    if im is None:
        return None
    size = int(size)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(out, "RGBA")
    r = max(2, size // 6)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=(12, 9, 16, 235))
    ins = max(1, int(size * 0.12))
    w = im.resize((size - 2 * ins, size - 2 * ins), Image.LANCZOS)
    out.alpha_composite(w, (ins, ins))
    return out


def get_bg(w: int, h: int, name: str = "bg3") -> Image.Image:
    """整张图的底：把背景 jpg 裁到画布尺寸（原实现是 `get_zzz_bg`）。"""
    p = ICON_PATH / f"{name}.jpg"
    if not p.exists():
        return Image.new("RGBA", (w, h), (18, 16, 24, 255))
    bg = Image.open(p).convert("RGBA")
    bw, bh = bg.size
    scale = max(w / bw, h / bh)
    bg = bg.resize((max(1, int(bw * scale)), max(1, int(bh * scale))))
    # 居中裁一块（背景是高瘦的竖图，直接压扁会变形）
    left = (bg.width - w) // 2
    top = (bg.height - h) // 2
    return bg.crop((left, top, left + w, top + h))


def add_footer(img: Image.Image) -> Image.Image:
    """底部那条装饰（原实现 `add_footer`）。"""
    p = ICON_PATH / "footer.png"
    if not p.exists():
        return img
    footer = Image.open(p).convert("RGBA")
    if footer.width != img.width:
        footer = footer.resize((img.width, int(footer.height * img.width / footer.width)))
    canvas = Image.new("RGBA", (img.width, img.height + footer.height), (0, 0, 0, 0))
    canvas.paste(img, (0, 0))
    canvas.paste(footer, (0, img.height), footer)
    return canvas


def _local_name(url: str) -> str:
    tail = url.split("?")[0].rsplit(".", 1)[-1].lower()
    ext = tail if tail in ("png", "jpg", "jpeg", "webp") else "png"
    return hashlib.md5(url.encode("utf-8")).hexdigest() + "." + ext


async def fetch_img(url: str) -> Image.Image | None:
    """取一张远程图（走插件的本地缓存），失败返回 None —— 调用方自行降级。"""
    if not url or not url.startswith("http"):
        return None
    try:
        await asset_cache.download(url)
        p = asset_cache.asset_path(_local_name(url))
        if not p.exists():
            return None
        return Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
