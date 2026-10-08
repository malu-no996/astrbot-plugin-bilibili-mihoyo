"""绝区零 · 群排行图片版渲染（纯 Pillow，QQ 里发出去的那张榜单）。

为什么单独一份
--------------
危局 / 防卫战那两张图是「**一个人**的战报」（头部卡 + 若干 BOSS 条目），
群排行完全不同：它是「**一个群**的榜单」，一行一个角色。所以版面另起一份，
不去改 image.py / shiyu_image.py。

每一行的内容（用户明确要求，2026-10-05）：
  · 绝境（render_group_rank）：排名 · 分数 · [出战队伍头像（右上角影画数）] · 角色名；
  · 危局 / 防卫战（render_teams_rank）：**单行**「排名 · 角色名 · 分数 |
    （深色方框包住的三头像）×N 队」，分数在角色名之后、队伍之前。方框外**不画**
    「队伍N」文字标签（用户要求，2026-10-05）。
头像格走 `core/image_gen.py` 的 `_one_ava`（右上角影画数角标），和危局 /
防卫战两张图同源；**只有左上的稀有度标换成图片**（见下）。

稀有度用图片而不是字母（用户要求，2026-10-05）
--------------------------------------------
S / A 级标直接用 ZZZeroUID 那套 `texture2d/icon/{rank}RANK.png`（带「S RANK」
字样的方徽章），取图口是 `zzz/avatar/card_assets.py` 的 `rank_badge_img()`
（裁掉透明边 + LANCZOS 缩到目标边长）—— 那套贴图本来就归 avatar 模块管，
这里只是借用，不再复制一份素材；危局 / 防卫战两张战报出图共用同一个口。
B 级代理人用不上，但素材在（同一个口就能取到）。
没有素材时自动退回原来的「渐变小牌 + 字母」，不会画成空白。

⚠️ 绝境 / 危局 / 防卫战三个群排行都会调用本模块（用户要求三个榜都加图片版）。
行数据由 social/rank.py 组装好再传进来，这里不碰存档格式。

字体 / 文本绘制 / 头像加载全部复用 image_gen（危局 / 防卫战 / 抽卡三张图也是
这么干的），所以观感天然一致。
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from ...core import image_gen as _ig
from ..avatar import card_assets as _ca

S = _ig.S
PAD = _ig.PAD
R_CARD = _ig.R_CARD

# 画布宽度：一行要并排放「排名 + 分数 + 三个头像 + 名字」，名字是最后一项，
# 前面的列吃掉的越多、留给名字的越少（见 _draw_row 末尾的 _fit：放不下就省略号）。
# 2026-10-09 加宽 400S → 500S（800px → 1000px）：原来留给名字只有 ~136px，
# 13S 的粗体大概 5 个汉字就截断了（「浅羽悠真」还行，「星见雅·朱鸢」这种必断）。
# 加宽后名字列多 ~200px，约 8~9 个汉字，和危局/防卫战那张（动态算宽，~1100px）也接近。
W = 500 * S
CW = W - 2 * PAD

ROW_H = 58 * S              # 一行的高度（头像 44S 居中后上下各留 7S）
R_ROW = R_CARD              # 行圆角
GAP_ROW = 8 * S             # 行与行之间的间隔
PAD_X = 16 * S              # 行内左右留白
RANK_W = 26 * S             # 「排名」列宽（各行的分数 / 头像因此对齐成表）
SCORE_W = 74 * S            # 「分数」列宽（留够 6 位数）
COL_GAP = 14 * S            # 列间距
AV = 44 * S                 # 出战队伍的头像边长
GAP_AVA = 8 * S             # 头像之间的间隔
MAX_AVA = 3                 # 绝境队伍是三人，最多画三个（多了会挤到名字）
# 头像左上的 S / A 级图片徽章：**边长与右上角影画数容器一致**（用户要求，2026-10-05）
# —— 那个容器的边长就是 `image_gen.RANK_CHIP`（15S），直接引用，别另写一个数。
RAR_BADGE = _ig.RANK_CHIP

_RANK_TOP = _ig._STAR       # 前三名的名次用金色


def _fit(s, size, bold, max_w) -> str:
    """放不下就截断加省略号（保证名字不会压出头像区、溢出卡片）。"""
    s = str(s or "")
    if _ig._tw(s, size, bold) <= max_w:
        return s
    out = ""
    for ch in s:
        if _ig._tw(out + ch + "…", size, bold) > max_w:
            break
        out += ch
    return (out + "…") if out else ""


def _draw_row(canvas, x, y, w: int, row: dict) -> None:
    """画一行：排名 · 分数 · 队伍头像（含影画数）· 角色名。"""
    h = ROW_H
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    card.paste(Image.new("RGB", (w, h), _ig._CARD), (0, 0), _ig._rmask(w, h, R_ROW))
    _ig._grad_round(card, (0, 0, w, h), _ig._ENTRY_G, R_ROW)
    d = ImageDraw.Draw(card, "RGBA")
    cy = h // 2

    # 1) 排名（前三名金色）
    rank = int(row.get("rank") or 0)
    _ig._text(d, PAD_X, cy - _ig._lh(17 * S, True) // 2, str(rank), 17 * S, True,
              _RANK_TOP if rank <= 3 else _ig._MUTED2)

    # 2) 分数
    sx = PAD_X + RANK_W
    _ig._text(d, sx, cy - _ig._lh(22 * S, True) // 2, str(int(row.get("score") or 0)),
              22 * S, True, _ig._WHITE)

    # 3) 出战队伍的头像（右上角影画数；左上是 S/A 级**图片**徽章）
    ax = sx + SCORE_W + COL_GAP
    ay = (h - AV) // 2
    for a in (row.get("avatars") or [])[:MAX_AVA]:
        if not isinstance(a, dict):
            continue
        rarity = str(a.get("rarity") or "S").upper()
        _ig._one_ava(card, ax, ay, AV, _ig._load(a.get("icon") or ""), rarity,
                     int(a.get("rank") or 0),
                     _ig._A_BRD if rarity == "A" else _ig._S_BRD, _ig.AVA_RADIUS,
                     rarity_img=_ca.rank_badge_img(rarity, RAR_BADGE))
        ax += AV + GAP_AVA

    # 4) 角色名（紧贴头像右侧；放不下就省略号）
    nx = ax + COL_GAP
    name = _fit(row.get("name") or "", 13 * S, True, w - PAD_X - nx)
    if name:
        _ig._text(d, nx, cy - _ig._lh(13 * S, True) // 2, name, 13 * S, True, _ig._WHITE)

    canvas.alpha_composite(card, (x, y))


def render_group_rank(rows: list, title: str, period: str = "", count: int = 0) -> Image.Image:
    """渲染一张群排行图。

    rows：已排好序的榜单，每条 {rank（从 1 开始）, score, name, avatars
    [{icon, rarity, rank}]}（social/rank.py 组装）。
    title：榜名（如「危局强袭战 · 绝境」）。
    period / count：赛期与上榜角色数，画在标题下面那行。
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    H_MAX = 6000 * S
    canvas = Image.new("RGBA", (W, H_MAX), _ig._BG)
    d = ImageDraw.Draw(canvas, "RGBA")
    y = PAD

    _ig._text(d, PAD, y, f"{title} · 群排行", 16 * S, True, _ig._WHITE)
    y += _ig._lh(16 * S, True) + 4 * S
    sub = f"统计周期 {period} · 共 {count} 个角色上榜" if period \
        else f"共 {count} 个角色上榜"
    _ig._text(d, PAD, y, sub, 11 * S, False, _ig._MUTED)
    y += _ig._lh(11 * S) + 8 * S

    for r in rows:
        _draw_row(canvas, PAD, y, CW, r)
        y += ROW_H + GAP_ROW

    return canvas.crop((0, 0, W, min(H_MAX, y - GAP_ROW + PAD))).convert("RGB")


# —— 危局 / 防卫战群排行图片版（用户要求，2026-10-05；**单行**布局）——
# 每行一个角色，整行：**排名 · 角色名 · 分数 | （深色方框包住的三头像）×N 队**。
# 分数在角色名之后、队伍头像之前（用户明确）。队伍是绝区零「每个 boss / 每层」带的 3 人队，
# 危局 3 个 boss → 3 队、防卫战第五层 → 3 队（第四层只有 2 队，不足就少画）。
# ⚠️ 不画「队伍N」文字标签，每个队三个头像外面用**深色方框**包住（用户要求，2026-10-05）。
TEAM_AV = 40 * S             # 队伍里单个代理人头像的边长
TEAM_GAP_AVA = 7 * S         # 同一队内头像间距
TEAM_BLOCK = 3 * TEAM_AV + 2 * TEAM_GAP_AVA   # 一队头像的总宽（3 个头像）
BOX_PAD = 5 * S              # 深色方框内边距（头像四周留白）
TEAM_BOX_W = TEAM_BLOCK + 2 * BOX_PAD         # 包住一队头像的深色方框宽
TEAM_BOX_H = TEAM_AV + 2 * BOX_PAD            # 深色方框高
TEAM_GAP = 16 * S            # 队伍方框之间的间隔
TEAM_BOX_R = 6 * S           # 深色方框圆角
TEAM_BOX_BG = (12, 9, 16)    # 深色方框底色：比卡片渐变更深，头像块「浮」在上面
RANK_W = 34 * S              # 排名列宽
NAME_W = 100 * S             # 角色名列宽（超出省略号）
SCORE_W = 82 * S             # 分数列宽
INNER = 8 * S                # 左块内列间距
LEFT_W = RANK_W + NAME_W + SCORE_W + 2 * INNER
COL_GAP = 16 * S             # 左块与队伍区的间隔
PAD_Y = 8 * S                # 行上下留白
# 注意：用独立的 TROW_H，**不要**复用上面的 ROW_H（那是绝境 render_group_rank 用的，
# 两者同处模块顶层、名字会互相覆盖；这里另起名字避免改绝境的版面）。
TROW_H = PAD_Y * 2 + TEAM_BOX_H


def render_teams_rank(rows: list, title: str, period: str = "", count: int = 0) -> Image.Image:
    """渲染一张「每角色一行、单行带多队头像」的群排行图（危局 / 防卫战用）。

    rows：已排好序的榜单，每条 {rank（从 1 开始）, score, name, teams
    [[头像,头像,头像], ...]}（social/rank.py 组装；teams 外层 = 队伍、内层 = 该队代理人）。
    title：榜名（如「危局强袭战」）。period / count：赛期与上榜角色数。
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    # 画布宽度按「最多几队」算（危局 3 / 防卫战通常 3，封顶 3），所有行对齐成一张表。
    team_cnt = min(3, max((len(r.get("teams") or []) for r in rows), default=0))
    teams_area = team_cnt * TEAM_BOX_W + max(0, team_cnt - 1) * TEAM_GAP
    W = 2 * PAD + 2 * PAD_X + LEFT_W + COL_GAP + teams_area
    CW = W - 2 * PAD
    H_MAX = 6000 * S
    canvas = Image.new("RGBA", (W, H_MAX), _ig._BG)
    d = ImageDraw.Draw(canvas, "RGBA")
    y = PAD

    _ig._text(d, PAD, y, f"{title} · 群排行", 16 * S, True, _ig._WHITE)
    y += _ig._lh(16 * S, True) + 4 * S
    sub = f"统计周期 {period} · 共 {count} 个角色上榜" if period \
        else f"共 {count} 个角色上榜"
    _ig._text(d, PAD, y, sub, 11 * S, False, _ig._MUTED)
    y += _ig._lh(11 * S) + 10 * S

    for r in rows:
        teams = [t for t in (r.get("teams") or []) if isinstance(t, list) and t][:3]
        card = Image.new("RGBA", (CW, TROW_H), (0, 0, 0, 0))
        card.paste(Image.new("RGB", (CW, TROW_H), _ig._CARD), (0, 0),
                   _ig._rmask(CW, TROW_H, R_ROW))
        _ig._grad_round(card, (0, 0, CW, TROW_H), _ig._ENTRY_G, R_ROW)
        cd = ImageDraw.Draw(card, "RGBA")
        cy = TROW_H // 2                              # 左块文本垂直居中
        box_y = (TROW_H - TEAM_BOX_H) // 2            # 深色方框上下垂直居中

        # 左块：排名（前三名金色）· 角色名 · 分数
        rank = int(r.get("rank") or 0)
        _ig._text(cd, PAD_X, cy - _ig._lh(17 * S, True) // 2, str(rank), 17 * S, True,
                  _RANK_TOP if rank <= 3 else _ig._MUTED2)
        name = _fit(r.get("name") or "", 13 * S, True, NAME_W)
        if name:
            _ig._text(cd, PAD_X + RANK_W + INNER, cy - _ig._lh(13 * S, True) // 2,
                      name, 13 * S, True, _ig._WHITE)
        # 分数紧跟在名字后面、队伍头像之前（用户明确要求）
        _ig._text(cd, PAD_X + RANK_W + NAME_W + 2 * INNER,
                  cy - _ig._lh(22 * S, True) // 2, str(int(r.get("score") or 0)),
                  22 * S, True, _ig._WHITE)

        # 队伍区：每队三个头像，外面包一个深色方框（**不画**「队伍N」标签，用户要求）。
        # 头像左上 S/A 图片徽章、右上影画数角标同其他出图。
        tx = PAD_X + LEFT_W + COL_GAP
        for team in teams:
            # 1) 深色方框底 + 细白色边线（用户要求，2026-10-05）
            cd.rounded_rectangle([tx, box_y, tx + TEAM_BOX_W - 1, box_y + TEAM_BOX_H - 1],
                                 radius=TEAM_BOX_R, fill=TEAM_BOX_BG,
                                 outline=_ig._WHITE, width=max(1, S))
            # 2) 框内的三个头像
            ax = tx + BOX_PAD
            ay = box_y + BOX_PAD
            for a in team[:3]:
                rarity = str(a.get("rarity") or "S").upper()
                _ig._one_ava(card, ax, ay, TEAM_AV, _ig._load(a.get("icon") or ""), rarity,
                             int(a.get("rank") or 0),
                             _ig._A_BRD if rarity == "A" else _ig._S_BRD, _ig.AVA_RADIUS,
                             rarity_img=_ca.rank_badge_img(rarity, RAR_BADGE))
                ax += TEAM_AV + TEAM_GAP_AVA
            tx += TEAM_BOX_W + TEAM_GAP

        canvas.alpha_composite(card, (PAD, y))
        y += TROW_H + GAP_ROW

    return canvas.crop((0, 0, W, min(H_MAX, y - GAP_ROW + PAD))).convert("RGB")
