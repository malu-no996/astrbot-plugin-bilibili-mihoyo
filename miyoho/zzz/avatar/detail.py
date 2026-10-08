"""角色详情（avatar/info）的归一化：官方 / Enka 两套 → 前端同一份模型。

前端「角色查询」里点一张卡片就弹详情：音擎、驱动盘、技能加点、影画、面板属性。
数据源有两套，字段长得完全不一样，所以在这里统一成一份（详见 `DETAIL_SHAPE`）：

    米游社官方  /avatar/info    —— 字段最全（含驱动盘评分、影画解锁状态），
                                  但受「角色详情」风控（retcode 10041）限制。
    Enka        /api/zzz/uid    —— 只有展示栏角色，也不带名字（只有数字 id），
                                  名字 / 图标靠本地图鉴 data/zzz/*.json 补。

本文件只做「翻译」，不发请求、不碰路由。
"""

from __future__ import annotations

from ..codex import data as codex_data
from . import card_assets, char_map

# 前端拿到的模型（写在这里当文档用，改结构时记得同步前端 frag/zzz-avatar.js 的注释）
DETAIL_SHAPE = """
{
  source, id, name, full_name, rarity, element, profession, camp,
  level, rank, icon,
  element_icon, profession_icon,             # 图标文件名，前端拼 texture2d/icon 下的路径
  weapon: { name, rarity, level, star, icon, main, sub, talent_title, talent_content } | null,
  discs:  [{ slot, name, suit, level, rarity, icon,
             main:{name,value}, subs:[{name,value,valid,times}],
             invalid_cnt, all_hit }],
  valid_cnt: int, plan_tags: [str],        # 官方评分方案：共命中几次 / 有效词条名
  skills: [{ name, short, pos, level, max }],  # pos=skill_bar.png 雪碧图的第几格
  ranks:  [{ name, desc, unlocked }],
  props:  [{ name, base, add, final, icon }],  # icon = 属性图标文件名（可为空）
  note
}
"""

# 驱动盘六个位置（equipment_type 1~6）。官方只给数字，这里补个位次名。
SLOT_NAMES = {1: "壹", 2: "贰", 3: "叁", 4: "肆", 5: "伍", 6: "陆"}

# 技能等级上限：绝区零目前是 12（影画会再 +?，但接口给的 level 已含）
SKILL_MAX = 12

# 技能图标：官方接口不给图标，但 skill_type 是固定的，拿它去查雪碧图
# web/asset/skill_bar.png（350×70、6 格，抄自 ZZZeroUID 的 texture2d）。
# 映射表原样抄 ZZZeroUID/zzzerouid_char_detail/utils.py 的 SKILL_MAP：
#   0=普攻  2=闪避  6=支援  1=特殊技  3=连携/终结  5=第六格
# 前端按 pos × 每格宽切 background-position。
SKILL_POS = {0: 0, 2: 1, 6: 2, 1: 3, 3: 4, 5: 5}

# 官方接口和本地图鉴都**没有技能图标**，图上那种「一个图标一个技能」只能用
# 类型短字顶上。映射按技能名首段推断（抄 ZZZeroUID dmg_cal.py 的 type_dict 分类）。
SKILL_SHORTS = (
    ("普通攻击", "普攻"), ("闪避", "闪避"), ("冲刺", "冲刺"),
    ("特殊技", "特殊"), ("连携技", "连携"), ("终结技", "终结"), ("支援", "支援"),
)


def _skill_short(name: str) -> str:
    for key, short in SKILL_SHORTS:
        if key in name:
            return short
    return ""


# ---------------- 属性 id → 名字 / 百分比 ----------------
# （Enka 只给数字 id，米游社直接给 property_name；这套表只服务 Enka 降级源。
#   抄自 ZZZeroUID/utils/enka_to_mys.py，别自己翻译，id 与名字的对应是官方定的。）

PROP_NAMES = {
    "11101": "生命值", "11102": "生命值", "11103": "生命值",
    "12101": "攻击力", "12102": "攻击力", "12103": "攻击力",
    "13101": "防御力", "13102": "防御力", "13103": "防御力",
    "12201": "冲击力", "12202": "冲击力", "12203": "冲击力",
    "20103": "暴击率", "21103": "暴击伤害",
    "31401": "异常掌控", "31402": "异常掌控", "31403": "异常掌控",
    "31201": "异常精通", "31202": "异常精通", "31203": "异常精通",
    "23103": "穿透率", "23203": "穿透值",
    "30501": "能量自动回复", "30502": "能量自动回复", "30503": "能量自动回复",
    "31503": "物理伤害加成", "31603": "火属性伤害加成", "31703": "冰属性伤害加成",
    "31803": "雷属性伤害加成", "31903": "以太伤害加成", "32003": "风属性伤害加成",
}

# 这些属性的值是「×100 存整数」（1234 = 12.34%），展示要除以 100
PERCENT_IDS = {
    "11102", "12102", "13102", "12202", "12203", "20103", "21103",
    "23103", "30502", "31503", "31603", "31703", "31803", "31903", "32003",
}


def _prop_name(prop_id, fallback: str = "") -> str:
    return PROP_NAMES.get(str(prop_id or ""), fallback or str(prop_id or ""))


def _fmt(value, prop_id="", percent: bool = False) -> str:
    """数值 → 展示串：百分比类除以 100 带 %，其余原样（去掉多余的 .0）。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value or "")
    if percent or str(prop_id or "") in PERCENT_IDS:
        return f"{n / 100:g}%"
    return f"{n:g}"


# ---------------- 本地图鉴：给 Enka 那套纯数字 id 补名字 ----------------


def _codex_index(section: str) -> dict:
    """{id: 条目}（图鉴数据是 list，按 id 建索引方便查）。"""
    out: dict = {}
    for it in codex_data.section(section):
        if isinstance(it, dict) and it.get("id"):
            out[str(it["id"])] = it
    return out


def _codex_name(section: str, cid, default: str = "") -> str:
    it = _codex_index(section).get(str(cid or ""))
    return str(it.get("name") or default or "") if it else default


# ---------------- 官方源 ----------------


def _official_props(rows) -> list[dict]:
    out = []
    for p in rows or []:
        if not isinstance(p, dict):
            continue
        out.append({
            "name": str(p.get("property_name") or ""),
            "base": str(p.get("base") or ""),
            "add": str(p.get("add") or ""),
            "final": str(p.get("final") or ""),
            # 属性图标（如 IconAttack）；素材里没有的属性会是空串，前端就只显示文字
            "icon": card_assets.prop_icon_name(p.get("property_id")),
        })
    return out


def _official_weapon(w) -> dict | None:
    if not isinstance(w, dict) or not w.get("id"):
        return None
    main = (w.get("main_properties") or [])
    sub = (w.get("properties") or [])
    return {
        "name": str(w.get("name") or f"音擎 {w.get('id')}"),
        "rarity": str(w.get("rarity") or "").upper(),
        "level": int(w.get("level") or 0),
        "star": int(w.get("star") or 0),          # 精炼（音擎的「星级」= 叠影层数）
        "icon": str(w.get("icon") or ""),
        "main": _fmt((main[0] or {}).get("base"), (main[0] or {}).get("property_id"))
        if main else "",
        "main_name": str((main[0] or {}).get("property_name") or "") if main else "",
        "sub": _fmt((sub[0] or {}).get("base"), (sub[0] or {}).get("property_id"))
        if sub else "",
        "sub_name": str((sub[0] or {}).get("property_name") or "") if sub else "",
        "talent_title": str(w.get("talent_title") or ""),
        "talent_content": str(w.get("talent_content") or ""),
    }


def _official_discs(rows, plan) -> list[dict]:
    """驱动盘：按 equipment_type 排序，词条与官方评分一起带出来。

    `plan`（equip_plan_info）里是这个角色的「有效词条」方案，命中词条会标 valid=True；
    官方给的是整体评分（equip_rating / valid_property_cnt），按盘分摊不了，所以只在
    每件盘上标它自己那几个词条有没有被算进有效 —— 具体判定见 `_mark_valid`。
    """
    discs = []
    for e in rows or []:
        if not isinstance(e, dict):
            continue
        suit = e.get("equip_suit") if isinstance(e.get("equip_suit"), dict) else {}
        main = [m for m in (e.get("main_properties") or []) if isinstance(m, dict)]
        subs = [s for s in (e.get("properties") or []) if isinstance(s, dict)]
        discs.append({
            "slot": int(e.get("equipment_type") or 0),
            "slot_name": SLOT_NAMES.get(int(e.get("equipment_type") or 0), ""),
            "name": str(e.get("name") or ""),
            "suit": str(suit.get("name") or ""),
            "suit_desc1": str(suit.get("desc1") or ""),
            "suit_desc2": str(suit.get("desc2") or ""),
            "level": int(e.get("level") or 0),
            "rarity": str(e.get("rarity") or "").upper(),
            "icon": str(e.get("icon") or ""),
            "main": {
                "name": str((main[0] or {}).get("property_name") or ""),
                "value": str((main[0] or {}).get("base") or ""),
            } if main else {"name": "", "value": ""},
            "subs": [
                {
                    "name": str(s.get("property_name") or ""),
                    "value": str(s.get("base") or ""),      # base 就是强化完的最终值
                    "valid": bool(s.get("valid")),
                    "times": int(s.get("level") or 0),       # 强化次数（副词条 +N）
                }
                for s in subs
            ],
            # 官方回算的逐盘命中：invalid_property_cnt=没被方案算中的词条数，all_hit=全中
            "invalid_cnt": int(e.get("invalid_property_cnt") or 0),
            "all_hit": bool(e.get("all_hit")),
        })
    discs.sort(key=lambda d: d["slot"])
    _mark_valid(discs, plan)
    return discs


def _mark_valid(discs: list[dict], plan) -> None:
    """把 equip_plan_info 里的「有效副属性」标到对应词条上。

    官方只给一张「该角色算有效的属性名」清单（`plan_effective_property_list`），
    所以判据是「词条名在清单里」；命中就置 valid=True，前端显示成高亮。
    没给 plan 时保持原样（Enka 源没有这套）。
    """
    if not isinstance(plan, dict):
        return
    wanted: set[str] = set()
    for grp in ("game_default", "custom_info"):
        g = plan.get(grp)
        if isinstance(g, dict):
            for p in (g.get("property_list") or []):
                if isinstance(p, dict) and p.get("is_select"):
                    wanted.add(str(p.get("name") or ""))
    for p in (plan.get("plan_effective_property_list") or []):
        if isinstance(p, dict):
            wanted.add(str(p.get("name") or ""))
    if not wanted:
        return
    for d in discs:
        for s in d.get("subs") or []:
            if str(s.get("name") or "") in wanted:
                s["valid"] = True


def _plan_tag_name(p: dict) -> str:
    """「有效词条」标签要显示的文本（取自官方 equip_plan_info 的一条）。

    官方每条词条同时给 `name`（简称）和 `full_name`（全称），而**大小词条在简称里同名**：

        id 11103 → name「生命值」  /  id 11102 → name「生命值」     ← 名字看不出区别
        id 12103 → name「攻击力」  /  id 12102 → name「攻击力」
        id 13103 → name「防御力」  /  id 13102 → name「防御力」

    只有 `full_name` 才分得开（「生命值百分比」「攻击力百分比」「防御力百分比」；其余
    词条 full_name 与 name 相同）。所以标签一律用 full_name —— 否则大生命 / 大攻击 /
    大防御会被显示成小词条的名字。（样本见 data/official_equip_plan.json）
    """
    return str(p.get("full_name") or p.get("name") or "")


def _official_skills(rows) -> list[dict]:
    out = []
    for s in rows or []:
        if not isinstance(s, dict):
            continue
        items = [i for i in (s.get("items") or []) if isinstance(i, dict)]
        out.append({
            # 官方不单独给技能名，第一条 item 的 title 就是（如「普通攻击：裁生」）
            "name": str((items[0] or {}).get("title") or f"技能 {s.get('skill_type')}"),
            "short": _skill_short(str((items[0] or {}).get("title") or "")),
            "pos": SKILL_POS.get(int(s.get("skill_type") or 0), 0),
            "level": int(s.get("level") or 0),
            "max": SKILL_MAX,
            "desc": str((items[0] or {}).get("text") or "") if items else "",
        })
    # 图标条要按官方图标顺序排（普攻→闪避→…），不能按等级排
    out.sort(key=lambda s: s.get("pos", 0))
    return out


def _official_ranks(rows, rank: int) -> list[dict]:
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({
            "name": str(r.get("name") or ""),
            "desc": str(r.get("desc") or ""),
            "unlocked": bool(r.get("is_unlocked")) or int(r.get("pos") or 0) <= int(rank or 0),
        })
    return out


def _clean_rating(v) -> str:
    """驱动盘总分评级（官方 equip_rating）→ 页面要显示的字母。

    官方取值带 `ER_` 前缀，「+」在接口里有 `+` / `_PLUS` / `PLUS` 三种写法
    （ZZZeroUID 的 EQUIP_RATING_MAP 也是这么兜的）。**S 的个数不固定**：
    S / SS / SSS 都可能再带一个 `+`（S+ / SS+ / SSS+），所以不能写死成固定清单 ——
    这里统一normalize 成「若干个 S + 可选一个加号」或 A / B / C，原样透传。

    评级不到 A 时官方回的是 **DEFAULT 占位**（页面直接显示成「DEFAULT 驱动盘」），
    那不是真评分，一律丢掉 —— 前端与出图拿到空串就整块不显示。
    """
    s = str(v or "").strip().upper()
    if s.startswith("ER_"):
        s = s[3:]
    s = s.replace("_PLUS", "+").replace("PLUS", "+")   # S_PLUS / SPLUS → S+
    if s == "SP":                                      # ER_SP 也是「S+」的写法
        s = "S+"
    while s.endswith("++"):                            # SSS_PLUS_PLUS 之类，收成一个 +
        s = s[:-1]
    return "" if s in ("", "DEFAULT", "NONE", "NULL") else s


def _camp_icon(raw: dict) -> str:
    """阵营图标 URL（官方 `group_icon_path`）—— 阵营 LOGO，不是头像。

    两处结构都兜：`avatar/info`（角色详情）顶层直接有 `group_icon_path`；
    `avatar/basic`（角色列表）那份塞在 `icon_paths{group_icon_path,hollow_icon_path}` 里。
    ⚠️ 别拿它当头像：`hollow_icon_path` / `group_icon_path` 都是 180×64 的横版条
    （hollow 是角色的脸、group 是阵营 LOGO），塞进方头像框就是「角色对、图不对」。
    取不到返回空串（前端 v-if 不渲染），不要退回别的图。

    该字段名同时登记在 `core/asset_cache.IMAGE_KEYS` 里 —— 那套只在字段名命中时才把
    URL 缓存成本地路由，所以这里的 key 不能随手改名。
    """
    if not isinstance(raw, dict):
        return ""
    url = str(raw.get("group_icon_path") or "").strip()
    if url:
        return url
    paths = raw.get("icon_paths")
    if isinstance(paths, dict):
        return str(paths.get("group_icon_path") or "").strip()
    return ""


def from_official(raw: dict) -> dict:
    """米游社 avatar/info 里的一个角色 → 前端模型。"""
    av_id = raw.get("id") or 0
    name, full, rarity, element, profession, camp, _sprite = (
        char_map.lookup(av_id) or ("",) * 7
    )
    # 头像一律取官方的**方头像**（char_map.square_avatar：优先接口的 role_square_url，
    # 接口没给就按角色 id 拼官方 CDN 地址）。hollow_icon_path / group_icon_path 是 180×64
    # 的横版长条（空洞 / 编队用的横幅），塞进方头像框只能「裁切」或「两侧大片留白」——
    # 之前网页版「头像显示成一条长图」就是这个原因，**别再退回它们**。
    icon = char_map.square_avatar(raw)
    plan = raw.get("equip_plan_info") if isinstance(raw.get("equip_plan_info"), dict) else {}
    return {
        "source": "official",
        "id": str(av_id),
        "name": str(raw.get("name_mi18n") or name or f"角色 {av_id}"),
        "full_name": str(raw.get("full_name_mi18n") or full or ""),
        "rarity": str(raw.get("rarity") or rarity or "").upper(),
        "element": char_map.element_name(raw.get("element_type")) or element or "",
        "element_icon": card_assets.element_icon_name(raw.get("element_type") or element),
        "profession": char_map.profession_name(raw.get("avatar_profession")) or profession or "",
        "profession_icon": card_assets.pro_icon_name(raw.get("avatar_profession") or profession),
        "camp": str(raw.get("camp_name_mi18n") or camp or ""),
        "camp_icon": _camp_icon(raw),          # 阵营 LOGO（group_icon_path），浮层立绘卡右下角
        "level": int(raw.get("level") or 0),
        "rank": int(raw.get("rank") or 0),
        "icon": icon,
        "weapon": _official_weapon(raw.get("weapon")),
        "discs": _official_discs(raw.get("equip"), plan),
        "skills": _official_skills(raw.get("skills")),
        "ranks": _official_ranks(raw.get("ranks"), raw.get("rank")),
        "props": _official_props(raw.get("properties")),
        "rating": _clean_rating(plan.get("equip_rating")),
        # 评分方案：有效副属性总共命中几次 + 命中的词条名（页面顶部那排小标签）
        # 标签名用 full_name（name 是简称，大/小生命在其上同名，见 _plan_tag_name）
        "valid_cnt": int(plan.get("valid_property_cnt") or 0),
        "plan_tags": [
            _plan_tag_name(p)
            for p in (plan.get("plan_effective_property_list") or [])
            if isinstance(p, dict) and _plan_tag_name(p)
        ],
        "note": "",
    }


# ---------------- Enka 源（降级） ----------------


def _enka_discs(char: dict) -> list[dict]:
    """Enka 的 EquippedList → 驱动盘；名字靠本地图鉴（套装 id = 盘 id 前 3 位 + "00"）。"""
    discs: list[dict] = []
    for relic in char.get("EquippedList") or []:
        if not isinstance(relic, dict):
            continue
        eq = relic.get("Equipment") if isinstance(relic.get("Equipment"), dict) else {}
        eid = str(eq.get("Id") or "")
        slot = int(relic.get("Slot") or 0)
        suit_id = eid[:3] + "00" if len(eid) >= 3 else ""
        suit = _codex_index("discs").get(suit_id) or {}
        level = int(eq.get("Level") or 0)

        # 主词条：只显示基础值。Enka 给的是「0 级时的值 + 每档成长」，要算准得再引
        # 一份官方成长表（MAIN_PROP_VALUE），这里不做 —— 详情页的主要用途是看配了什么
        # 词条，不是算最终面板，所以保持简单、并注明是基础值。
        main: dict = {"name": "", "value": ""}
        for m in (eq.get("MainPropertyList") or []):
            if not isinstance(m, dict):
                continue
            pid = m.get("PropertyId")
            main = {
                "name": _prop_name(pid),
                "value": _fmt(m.get("PropertyValue"), pid),
            }
            break

        subs = []
        for s in (eq.get("RandomPropertyList") or []):
            if not isinstance(s, dict):
                continue
            pid = s.get("PropertyId")
            times = int(s.get("PropertyLevel") or 0) or 1
            subs.append({
                "name": _prop_name(pid),
                "value": _fmt(float(s.get("PropertyValue") or 0) * times, pid),
                "valid": False,
                "times": times,
            })

        discs.append({
            "slot": slot,
            "slot_name": SLOT_NAMES.get(slot, ""),
            "name": str(suit.get("name") or f"驱动盘 {eid}"),
            "suit": str(suit.get("name") or ""),
            "suit_desc1": str(suit.get("desc1") or ""),
            "suit_desc2": str(suit.get("desc2") or ""),
            "level": level,
            # 驱动盘本身没有 S/A/B 之分（那是角色/音擎的），这里留空，前端只显示等级
            "rarity": "",
            "icon": str(suit.get("icon") or ""),
            "main": main,
            "subs": subs,
            # Enka 没有官方评分方案，命中次数未知 → -1 让前端整块隐藏
            "invalid_cnt": -1,
            "all_hit": False,
        })
    discs.sort(key=lambda d: d["slot"])
    return discs


def _enka_weapon(char: dict) -> dict | None:
    w = char.get("Weapon") if isinstance(char.get("Weapon"), dict) else {}
    if not w.get("Id"):
        return None
    wid = str(w.get("Id") or "")
    meta = _codex_index("wengines").get(wid) or {}
    return {
        "name": str(meta.get("name") or f"音擎 {wid}"),
        "rarity": str(meta.get("rarity") or "").upper(),
        "level": int(w.get("Level") or 0),
        "star": int(w.get("UpgradeLevel") or 0),
        "icon": "",
        "main": str(meta.get("main") or ""),
        "main_name": "",
        "sub": str(meta.get("sub") or ""),
        "sub_name": "",
        "talent_title": "",
        "talent_content": "",
    }


def _enka_skills(char: dict, av_id) -> list[dict]:
    """Enka 只给等级列表（Index 即官方 skill_type）；技能名从本地图鉴 agents.json 按位取。"""
    rows = [
        (SKILL_POS.get(int(s.get("Index") or 0), 0), int(s.get("Level") or 0))
        for s in (char.get("SkillLevelList") or []) if isinstance(s, dict)
    ]
    agent = _codex_index("agents").get(str(av_id or "")) or {}
    names = [
        str(s.get("name") or "")
        for s in (agent.get("skills") or []) if isinstance(s, dict)
    ]
    out = []
    for i, (pos, lv) in enumerate(rows):
        nm = names[pos] if pos < len(names) else f"技能 {i + 1}"
        out.append({
            "name": nm,
            "short": _skill_short(nm),
            "pos": pos,
            "level": lv,
            "max": SKILL_MAX,
            "desc": "",
        })
    out.sort(key=lambda s: s.get("pos", 0))
    return out


def from_enka(char: dict) -> dict:
    """Enka 展示栏里的一个角色 → 前端模型（字段比官方少，缺的留空）。"""
    av_id = char.get("Id") or 0
    name, full, rarity, element, profession, camp, sprite = (
        char_map.lookup(av_id) or ("",) * 7
    )
    return {
        "source": "enka",
        "id": str(av_id),
        "name": name or f"角色 {av_id}",
        "full_name": full or "",
        "rarity": str(rarity or "").upper(),
        "element": element or "",
        "element_icon": card_assets.element_icon_name(element),
        "profession": profession or "",
        "profession_icon": card_assets.pro_icon_name(profession),
        "camp": camp or "",
        "camp_icon": "",        # Enka 不提供阵营图标（官方 group_icon_path 才有），恒为空
        "level": int(char.get("Level") or 0),
        "rank": int(char.get("TalentLevel") or 0),
        # 头像统一走官方**方头像**（Enka 的 IconRole 是另一套画法，和官方 / 战绩页不是同一张图）。
        # 详情页现在自带角色 id，所以能直接拼官方 role_square_avatar；拼不出才退回 Enka 那张。
        "icon": char_map.square_avatar({"id": av_id}) or char_map.enka_icon(sprite),
        "weapon": _enka_weapon(char),
        "discs": _enka_discs(char),
        "skills": _enka_skills(char, av_id),
        "ranks": [],
        "props": [],
        "rating": "",
        "valid_cnt": 0,
        "plan_tags": [],
        "note": "Enka 备用数据：只有游戏内「展示栏」的角色，且不含面板属性 / 影画文案",
    }
