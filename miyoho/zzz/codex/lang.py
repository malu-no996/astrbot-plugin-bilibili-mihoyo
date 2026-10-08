"""生成图鉴的**多语言文本映射**（plugins/_vendor/miyoushe/data/zzz/lang/ 下分段文件）。

落盘格式：每个「类别 × 语言」一个文件（4 类 × 6 语言 = 24 个），外加一个 meta.json：

    data/zzz/lang/agents-ja.json    = {条目id: {name:日文, element:日文, skills:[…], …}}
    data/zzz/lang/agents-ko.json    = {条目id: {name:韩文, …}}
    data/zzz/lang/wengines-ja.json  = …
    …（discs-* / bangboo-* 同理）
    data/zzz/lang/meta.json         = {version, updated, langs, lang_names, source}

翻译对象的字段名与普通 json（agents.json 等）完全一致，外层按条目 id 索引——即多语言文件的
结构和 key 都和普通 json 对齐。简中即原文，不重复存。运行时 codex_data.lang_data() 把这 24
个文件读回 `{类别: {id: {lang: 翻译对象}}}`，前端按 id + 字段取译文即可。

分类依据：targets() 产出的 kind 前缀（agent.* / wengine.* / disc.* / bangboo.*，
其中 enum.* 是代理人属性 → 归 agents）。

数据来源
--------
ZenlessData（Dimbreath）的 TextMap 文本表，镜像在 git.mero.moe（GitHub 上的仓库 2024-07
被米哈游 DMCA 封了，只有这个 Gitea 镜像还活着）。表里 **key 是路径式标识符**
（如 `Chat_PartnerName_1011`），同一 key 在 13 种语言表里一一对应。

但我们的图鉴数据（codex_data.json）来自参考项目，**只存了中文、丢了 key**，所以要「反查」：
    中文文本 → 在中文表里找出所有同文的 key → 用这些 key 去取其他语言的译文。

三个层次的取 key 策略（越靠前越可靠）
--------------------------------------
① **模板**：能按 id 直接拼出 key 的。实测零歧义，覆盖驱动盘全部、邦布全部、代理人全名大部分。
② **命名空间**：同一类文本在游戏里落在固定前缀下，用它把候选压到 1 个。
   例：元素=`ElementType_*`、职业=`ProfessionName_*`、伤害类型=`HitType_*`、
   音擎名=`Item_Weapon_*_Name`、音擎效果=`Weapon_TalentTitle_*` / `Weapon_TalentDes_*`。
③ **签名投票**：还是多个候选时，把每个候选 key 的「四语言译文组合」当成签名投票，
   取票数最高的那组。依据是同一句话在不同场景里翻译一般一致（实测绝大多数文本
   各候选译文完全相同，投票只用来压掉少数歧义）。

取不到的文本**不写入映射**，前端会原样回退中文 —— 宁缺毋错。

一次性成本
----------
7 张文本表（简中做基准 + 繁/英/日/韩/泰/俄）合计约 360MB，只在**生成时**下载，缓存在
data/zzz/textmap/（该目录已在 .gitignore 里）。裁完落盘的映射只有 ~2MB，
打开图鉴不访问任何外部地址。想换源改 MIRROR 即可。
"""
from __future__ import annotations

import json
import pathlib
import re
import time

import httpx
from loguru import logger

TEXTMAP_DIR = pathlib.Path("data/zzz/textmap")
MIRROR = "https://git.mero.moe/dimbreath/ZenlessData/raw/branch/master/TextMap"
SOURCE_URL = "https://git.mero.moe/dimbreath/ZenlessData"

# 基准表（简中，无语言后缀）：反查用的就是它
BASE_FILE = "TextMapTemplateTb.json"
# 目标语言 → 文件名。想加语言（德/西/法/印尼/葡/越）在这里补一行即可，
# 同时要在 codex_data.LANGS 里登记（那边的顺序 = 前端下拉框顺序）。
LANG_FILES = {
    "zh-tw": "TextMap_CHTTemplateTb.json",
    "en": "TextMap_ENTemplateTb.json",
    "ja": "TextMap_JATemplateTb.json",
    "ko": "TextMap_KOTemplateTb.json",
    "th": "TextMap_THTemplateTb.json",
    "ru": "TextMap_RUTemplateTb.json",
}
LANG_NAMES = {
    "zh-cn": "简体中文", "zh-tw": "繁體中文", "en": "English", "ja": "日本語",
    "ko": "한국어", "th": "ไทย", "ru": "Русский",
}

# 本文件在 src/ 下，被 codex_build 用 importlib 按路径单独加载（此时没有包上下文），
# 所以**不能用相对导入**，定位目录一律靠 Path(__file__).resolve().parents[3]
#（本文件在 src/zzz/codex/ 下：codex → zzz → src → 插件根）。
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]
LANG_DIR = PLUGIN_ROOT / "data" / "zzz" / "lang"

# kind 前缀 → 图鉴类别（enum.* 是代理人属性，归 agents）。
_KIND_CAT = {
    "agent": "agents", "enum": "agents",
    "wengine": "wengines", "disc": "discs", "bangboo": "bangboo",
}
_CATS = ("agents", "wengines", "discs", "bangboo")

_TAG = re.compile(r"<[^>]+>")


def _strip(text) -> str:
    return _TAG.sub("", text or "").strip()


# ---------------- 取 key 的规则 ----------------

# ① 模板：按 id 拼 key（零歧义）。值必须和中文原文一致才采用。
_TEMPLATES = {
    "disc.name": "EquipmentSuit_{id}_name",
    "disc.desc1": "EquipmentSuit_{id}_2_des",
    "disc.desc2": "EquipmentSuit_{id}_4_des",
    "bangboo.name": "Bangboo_Name_{id}",
    "agent.full_name": "Partner_Name_{id}",
}

# ② 命名空间白名单：候选 key 必须落在这里面（值仍须与中文原文一致）
_NAMESPACES = {
    "agent.name": r"^(Chat_PartnerName_\d+|Avatar_[A-Za-z]+_Size\d+_[A-Za-z]+|Partner_Name_\d+)$",
    "agent.full_name": r"^(Partner_Name_\d+|Avatar_.*_FullName)$",
    "agent.skill.name": r"^[A-Za-z][A-Za-z0-9]*_Skill(List)?_",
    "agent.skill.param": r"(Property_.*|^Common_Property_)",
    "agent.extra.item": r"^(AttriName_\d+|BKProperty_\d+_Name|.*_Base|.*_Ratio)$",
    "wengine.name": r"^Item_Weapon_.*_Name$",
    "wengine.talent.name": r"^Weapon_TalentTitle_",
    "wengine.talent.desc": r"^Weapon_TalentDes_",
    "wengine.prop": r"^(AttriName_\d+|BKProperty_\d+_Name|.*_Base|.*_Ratio)$",
    "enum.element": r"^ElementType_",
    "enum.profession": r"^ProfessionName_",
    "enum.hit_type": r"^HitType_",
}

_PROP_SPLIT = re.compile(r"^(.*?)\s*([\d.]+)\s*$")


def _split_prop(text: str) -> str:
    """音擎的「基础攻击力 46」→ 只取属性名部分（数值不需要翻译）。"""
    m = _PROP_SPLIT.match(text or "")
    return (m.group(1) if m else (text or "")).strip()


def targets(data: dict):
    """产出 (kind, id, text)：kind 决定用哪套规则，id 用于拼模板 key。"""
    for a in data.get("agents") or []:
        aid = str(a.get("id") or "")
        yield "agent.name", aid, a.get("name") or ""
        yield "agent.full_name", aid, a.get("full_name") or ""
        yield "enum.element", aid, a.get("element") or ""
        yield "enum.profession", aid, a.get("profession") or ""
        yield "enum.hit_type", aid, a.get("hit_type") or ""
        yield "agent.camp", aid, a.get("camp") or ""
        for s in a.get("skills") or []:
            yield "agent.skill.name", aid, s.get("name") or ""
            for p in s.get("params") or []:
                yield "agent.skill.param", aid, p.get("k") or ""
        for e in a.get("extras") or []:
            for x in e.get("items") or []:
                yield "agent.extra.item", aid, x.get("k") or ""
    for w in data.get("wengines") or []:
        wid = str(w.get("id") or "")
        yield "wengine.name", wid, w.get("name") or ""
        yield "wengine.prop", wid, _split_prop(w.get("main") or "")
        yield "wengine.prop", wid, _split_prop(w.get("sub") or "")
        for t in w.get("talents") or []:
            yield "wengine.talent.name", wid, t.get("name") or ""
            yield "wengine.talent.desc", wid, t.get("desc") or ""
    for d in data.get("discs") or []:
        did = str(d.get("id") or "")
        yield "disc.name", did, d.get("name") or ""
        yield "disc.desc1", did, d.get("desc1") or ""
        yield "disc.desc2", did, d.get("desc2") or ""
    for b in data.get("bangboo") or []:
        yield "bangboo.name", str(b.get("id") or ""), b.get("name") or ""


# ---------------- 文本表下载 / 读取 ----------------

def _path(fname: str) -> pathlib.Path:
    return TEXTMAP_DIR / fname


def ensure_textmaps(force: bool = False) -> list[str]:
    """确保本地有基准表 + 各目标语言表，缺的从镜像下载。返回缺失（下载失败）的文件名。"""
    TEXTMAP_DIR.mkdir(parents=True, exist_ok=True)
    need = [BASE_FILE] + list(LANG_FILES.values())
    failed = []
    with httpx.Client(timeout=httpx.Timeout(600, connect=15), follow_redirects=True) as c:
        for fname in need:
            p = _path(fname)
            if p.exists() and not force and p.stat().st_size > 1024:
                continue
            url = f"{MIRROR}/{fname}"
            logger.info("下载官方文本表 {} …", fname)
            try:
                r = c.get(url)
                r.raise_for_status()
                p.write_bytes(r.content)
                logger.info("已保存 {}（{:.1f} MB）", p, len(r.content) / 1024 / 1024)
            except Exception as exc:  # noqa: BLE001
                logger.warning("文本表下载失败 {}：{}", fname, exc)
                failed.append(fname)
    return failed


def _read_table(fname: str) -> dict:
    with open(_path(fname), encoding="utf-8") as f:
        d = json.load(f)
    return d if isinstance(d, dict) else {}


# ---------------- 生成映射 ----------------

def _tr_lookup(out: dict, text) -> dict | None:
    """查某条中文文本的译文映射 {lang: 译文}（取不到返回 None）。"""
    if not text:
        return None
    return out.get(_strip(text))


def _translate_item(item: dict, cat: str, out: dict) -> dict:
    """把普通 json 的一个条目翻译成「同构翻译骨架」：字段名与普通 json 完全一致，
    叶子是 {lang: 译文}（取不到译文的字段为 None）。这样落盘后多语言文件的外层按 id
    索引、内部字段逐一对上普通 json，结构与 key 完全对齐。"""
    if cat == "agents":
        return {
            "name": _tr_lookup(out, item.get("name")),
            "full_name": _tr_lookup(out, item.get("full_name")),
            "element": _tr_lookup(out, item.get("element")),
            "profession": _tr_lookup(out, item.get("profession")),
            "hit_type": _tr_lookup(out, item.get("hit_type")),
            "camp": _tr_lookup(out, item.get("camp")),
            "skills": [
                {"name": _tr_lookup(out, s.get("name")),
                 "params": [{"k": _tr_lookup(out, p.get("k"))} for p in (s.get("params") or [])]}
                for s in (item.get("skills") or [])
            ],
            "extras": [
                {"items": [{"k": _tr_lookup(out, x.get("k"))} for x in (e.get("items") or [])]}
                for e in (item.get("extras") or [])
            ],
        }
    if cat == "wengines":
        return {
            "name": _tr_lookup(out, item.get("name")),
            "main": _tr_lookup(out, _split_prop(item.get("main"))),
            "sub": _tr_lookup(out, _split_prop(item.get("sub"))),
            "talents": [
                {"name": _tr_lookup(out, t.get("name")), "desc": _tr_lookup(out, t.get("desc"))}
                for t in (item.get("talents") or [])
            ],
        }
    if cat == "discs":
        return {
            "name": _tr_lookup(out, item.get("name")),
            "desc1": _tr_lookup(out, item.get("desc1")),
            "desc2": _tr_lookup(out, item.get("desc2")),
        }
    # bangboo
    return {"name": _tr_lookup(out, item.get("name"))}


def _lang_only(node, lang: str, lang_set: set):
    """把翻译骨架（叶子是 {lang: 译文}）抽成单一语言：叶子变译文字符串，
    中间层保持与普通 json 同构的 list/dict。None / 空字段直接丢弃。"""
    if isinstance(node, dict):
        if node and all(k in lang_set for k in node):   # 叶子：{lang: 译文}
            return node.get(lang)
        out = {}
        for k, v in node.items():
            if v is None:
                continue
            x = _lang_only(v, lang, lang_set)
            if x not in (None, {}, []):
                out[k] = x
        return out
    if isinstance(node, list):
        return [_lang_only(v, lang, lang_set) for v in node]
    return node


def build_lang_map(data: dict, langs: list[str] | None = None) -> dict:
    """按 codex 数据生成 {中文: {lang: 译文}}，再按类别×id 重建为「与普通 json 同构」的翻译骨架。
    缺表/取不到 key 的文本直接不写（对应字段留 None，落盘时该语言下丢弃）。"""
    langs = [x for x in (langs or list(LANG_FILES)) if x in LANG_FILES]

    # 1) 需要翻译的唯一文本；同时记「每条文本出现在哪些类别」（用于落盘时按类别拆分）
    items = [(k, i, _strip(t)) for k, i, t in targets(data)]
    items = [(k, i, t) for k, i, t in items if t]
    want = {t for _, _, t in items}
    logger.info("图鉴需要对齐的文本 {} 条（唯一 {}）", len(items), len(want))

    # 2) 基于简中表反查：中文文本 → 候选 key。同时用它做「原文校验」，
    #    因为模板拼出来的 key 也可能不存在（新角色/换过规则），必须核对值是否真的是原文。
    base = _read_table(BASE_FILE)
    rev: dict[str, list[str]] = {}
    for k, v in base.items():
        if isinstance(v, str):
            s = _strip(v)
            if s in want:
                rev.setdefault(s, []).append(k)

    # 3) 定候选：模板（若有）排在最前 → 命名空间收窄 → 原文校验
    picked: dict[str, list[str]] = {}
    for kind, cid, text in items:
        if text in picked:
            continue
        cands: list[str] = []
        tmpl = _TEMPLATES.get(kind)
        if tmpl and cid:
            cands.append(tmpl.format(id=cid))
        cands.extend(rev.get(text) or [])
        ns = _NAMESPACES.get(kind)
        if ns:
            hit = [k for k in cands if re.search(ns, k)]
            if hit:                       # 白名单没命中就退回全部候选，宁可投票也不丢
                cands = hit
        # 原文校验：key 对应的简中文本必须和我们的原文一字不差
        cands = [k for k in dict.fromkeys(cands)
                 if isinstance(base.get(k), str) and _strip(base[k]) == text]
        if cands:
            picked[text] = cands
    del rev, base
    logger.info("其中 {} 条定位到候选 key", len(picked))

    # 4) 抽各语言译文（一张一张来，避免 7 张合计 360MB 的表同时驻留）
    all_keys = {k for ks in picked.values() for k in ks}
    vals: dict[str, dict[str, str]] = {k: {} for k in all_keys}
    for lang in langs:
        tb = _read_table(LANG_FILES[lang])
        for k in all_keys:
            v = tb.get(k)
            if isinstance(v, str):
                vals[k][lang] = _strip(v)
        del tb
        logger.info("已抽取 {} 译文", LANG_NAMES.get(lang, lang))

    # 5) 投票 + 输出
    out: dict[str, dict[str, str]] = {}
    stat = {"single": 0, "voted": 0, "partial": 0, "drop": 0}

    def sig(k: str) -> tuple:
        """候选 key 的「译文组合」签名（按语言排序，投票用）。"""
        return tuple(sorted(vals[k].items()))

    for text, keys in picked.items():
        full = [k for k in keys if len(vals.get(k) or {}) == len(langs)]
        if full:
            usable = full
        else:
            # 没有「全部语言都齐」的候选：退而取覆盖最广的那批（至少 2 种语言），
            # 缺的语言不缺内容 —— 前端查不到会自动回退中文原文，总比整条丢掉强。
            best_n = max((len(vals.get(k) or {}) for k in keys), default=0)
            usable = [k for k in keys if len(vals.get(k) or {}) == best_n] if best_n >= 2 else []
        if not usable:
            stat["drop"] += 1
            continue
        if len(usable) == 1:
            best = usable[0]
            stat["single"] += 1
        else:
            cnt: dict[tuple, int] = {}
            for k in usable:
                cnt.setdefault(sig(k), []).append(k)
            top = max(cnt.values(), key=len)
            best = top[0]
            stat["voted"] += 1
        if len(vals[best]) < len(langs):
            stat["partial"] += 1
        out[text] = dict(vals[best])

    logger.info("多语言映射：唯一定位 {} / 投票消歧 {} / 放弃 {}（其中 {} 条只有部分语言），共 {} 条",
                stat["single"], stat["voted"], stat["drop"], stat["partial"], len(out))

    # 6) 按类别×id 重建「与普通 json 同构」的翻译骨架：外层 key=id，内部字段名与普通 json
    #    （agents.json 等）逐一对齐，叶子是 {lang: 译文}。这样落盘后多语言文件的结构/key
    #    和普通 json 完全一致，前端按 id + 字段取译文即可（不再依赖中文原文反查）。
    lang_set = set(langs)
    per_cat = {c: {} for c in _CATS}
    for cat in _CATS:
        for item in data.get(cat) or []:
            tid = str(item.get("id") or "")
            if not tid:
                continue
            translated = _translate_item(item, cat, out)
            # 只要任意一种语言有译文就保留这个 id
            if any(_lang_only(translated, lang, lang_set) for lang in langs):
                per_cat[cat][tid] = translated
    logger.info("多语言按 id 重建：agents {} / wengines {} / discs {} / bangboo {} 条有译文",
                *[len(per_cat[c]) for c in _CATS])
    return {
        "version": data.get("version") or "",
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "langs": langs,
        "lang_names": {k: LANG_NAMES[k] for k in langs},
        "source": SOURCE_URL,
        "per_cat": per_cat,
    }


def build_and_save(data: dict) -> dict:
    """下载（如需）→ 生成 → 落盘（data/zzz/lang/<类别>-<语言>.json + meta.json）。

    任何失败都只警告，不影响图鉴本身。
    """
    try:
        failed = ensure_textmaps()
        if BASE_FILE in failed:
            logger.warning("基准文本表不可用，跳过图鉴多语言生成")
            return {}
        langs = [x for x in LANG_FILES if LANG_FILES[x] not in failed]
        if not langs:
            logger.warning("所有目标语言表都不可用，跳过多语言生成")
            return {}
        m = build_lang_map(data, langs)
        LANG_DIR.mkdir(parents=True, exist_ok=True)
        meta = {
            "version": m["version"], "updated": m["updated"],
            "langs": m["langs"], "lang_names": m["lang_names"], "source": m["source"],
        }
        (LANG_DIR / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        n_files = 0
        lang_set = set(langs)
        for cat in _CATS:
            idmap = m["per_cat"].get(cat) or {}
            if not idmap:
                continue
            for lang in langs:
                if lang == "zh-cn":
                    continue
                d = {tid: _lang_only(translated, lang, lang_set)
                     for tid, translated in idmap.items()}
                d = {tid: obj for tid, obj in d.items() if obj}   # 丢掉该语言下为空的 id
                if not d:
                    continue
                (LANG_DIR / f"{cat}-{lang}.json").write_text(
                    json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                n_files += 1
        logger.info("图鉴多语言已落盘 data/zzz/lang/（{} 个文件，覆盖 {} 类 × {} 语言）",
                    n_files, len(_CATS), len(langs))
        return m
    except Exception as exc:  # noqa: BLE001
        logger.warning("图鉴多语言生成失败（不影响图鉴本身）：{}", exc)
        return {}
