"""绝区零图鉴静态数据（读取 plugins/_vendor/miyoushe/data/zzz/ 下的分段文件）。

数据由同目录 `build.py` 抓取并落盘（后台接口 /zzz/codex/refresh，
或命令行 tools/gen_zzz_codex.py），包含四类，**每类一个文件**（已拆开，不再混在一个 json 里）：
  data/zzz/agents.json  ·  wengines.json  ·  discs.json  ·  bangboo.json

多语言映射（codex_lang.py 生成）放在 data/zzz/lang/ 下，**每个「类别×语言」一个文件**：
  agents-ja.json / agents-ko.json / wengines-ja.json ……（4 类 × 6 语言 = 24 个），
  外加一个 meta.json（版本 / 更新时间 / 语言清单）。每个文件是 `{条目id: 翻译对象}`——
  翻译对象的字段名与普通 json（agents.json 等）完全一致，只是文本值换成对应语言译文，
  所以多语言文件的结构/key 和普通 json 对齐，前端按 id + 字段取译文即可。

- 纯展示用，**不需要登录**（邦布是抓取时的全量快照）。
- 图标已经在抓取阶段**下载到本机**，json 里存的是本地路由
  /miyoho-asset/<name> —— 打开图鉴不访问任何外部地址。
- 抓取回来的文本里有米游社富文本 `<color=...>` 标签，这里统一剥掉只留纯文本。
"""
from __future__ import annotations

import json
import pathlib
import re

# 本文件在 src/zzz/codex/ 下 → parents[3] 才是插件根。
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]
DATA_DIR = PLUGIN_ROOT / "data" / "zzz"
LANG_DIR = DATA_DIR / "lang"
# 图鉴四类：每个类一个文件（不再混在一个 codex_data.json 里）。
SECTION_FILES = {name: DATA_DIR / f"{name}.json" for name in ("agents", "wengines", "discs", "bangboo")}
# 多语言：每个「类别×语言」一个文件（codex_lang.py 生成）。
SECTIONS = tuple(SECTION_FILES)
# 图鉴支持的语言，顺序即前端下拉框的顺序。zh-cn 是原文（图鉴数据本身就是简中），不需要映射。
LANGS = ["zh-cn", "zh-tw", "en", "ja", "ko", "th", "ru"]
LANG_CN_NAMES = {"zh-cn": "简体中文"}
_TAG = re.compile(r"<[^>]+>")

# 需要剥富文本的字段（desc1/desc2 = 驱动盘 2/4 件套，talents[].desc = 音擎效果，
# skills[].params[].k / levels / extras 里的名字一般不带标签，但统一走一遍更保险）
_TEXT_KEYS = {"desc", "desc1", "desc2", "main", "sub", "name", "k", "title", "text"}

_cache: dict | None = None
_lang_cache: dict | None = None


def _strip(text: str) -> str:
    return _TAG.sub("", text or "").strip()


def _clean(node) -> None:
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if isinstance(v, str) and k in _TEXT_KEYS:
                node[k] = _strip(v)
            else:
                _clean(v)
    elif isinstance(node, list):
        for x in node:
            _clean(x)


def reload() -> dict:
    """丢弃内存缓存并重新读盘（图鉴数据被刷新后调用）。多语言映射一并失效。"""
    global _cache, _lang_cache
    _cache = None
    _lang_cache = None
    return all_data()


def _load_json(p: pathlib.Path):
    """读一个 json 文件，失败（不存在 / 坏掉）返回 None，不抛。"""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def all_data() -> dict:
    """全部图鉴数据（首次读取时剥掉富文本标签，之后走内存缓存）。

    四类各读一个文件（data/zzz/<类>.json），合并成 {类: [...]}；再把 meta.json 里的
    version / updated 也带进来 —— 这俩字段前端图鉴页要显示，合并后和拆分前单文件的形状一致，
    所以 /zzz/codex 接口（**data）和 social_codex 都不用改。
    """
    global _cache
    if _cache is None:
        raw: dict = {}
        for name in SECTIONS:
            items = _load_json(SECTION_FILES[name])
            if not isinstance(items, list):
                items = []
            _clean(items)
            raw[name] = items
        meta = _load_json(DATA_DIR / "meta.json") or {}
        raw["version"] = meta.get("version") or ""
        raw["updated"] = meta.get("updated") or ""
        _cache = raw
    return _cache


def section(name: str) -> list:
    data = all_data()
    items = data.get(name)
    return items if isinstance(items, list) else []


def counts() -> dict:
    return {name: len(section(name)) for name in SECTIONS}


# ---------------- 多语言（codex_lang.json） ----------------

def lang_data() -> dict:
    """多语言映射：按类别×id 组织的「与普通 json 同构」翻译对象。首次读盘后走内存缓存。

    磁盘上是 24 个「类别×语言」文件（data/zzz/lang/<类>-<语言>.json），每个文件是
    `{条目id: 翻译对象}`——翻译对象的字段名与普通 json（agents.json 等）完全一致，
    只是文本值换成了对应语言的译文。这样多语言文件的结构/key 都和普通 json 对齐
    （外层按 id 索引，内部字段逐一对上），前端按 id + 字段取译文即可，不再依赖中文原文反查。

    只存非简中的语言（zh-cn 即原文，不重复存）。默认简中时前端根本不会拉这个接口，
    所以图鉴的日常使用不受影响。
    """
    global _lang_cache
    if _lang_cache is None:
        meta = _load_json(LANG_DIR / "meta.json") or {}
        by_cat: dict = {cat: {} for cat in SECTIONS}
        for cat in SECTIONS:
            for lang in LANGS:
                if lang == "zh-cn":
                    continue
                d = _load_json(LANG_DIR / f"{cat}-{lang}.json")
                if not isinstance(d, dict):
                    continue
                for tid, obj in d.items():
                    if isinstance(obj, dict):
                        by_cat[cat].setdefault(tid, {})[lang] = obj
        _lang_cache = {
            "langs": meta.get("langs") or [x for x in LANGS if x != "zh-cn"],
            "lang_names": meta.get("lang_names") or {},
            "version": meta.get("version") or "",
            "updated": meta.get("updated") or "",
            "by_cat": by_cat,
        }
    return _lang_cache


def lang_meta() -> dict:
    """给前端的语言清单：只列出**真的有映射数据**的语言（没生成过就只有简中）。

    这样万一官方文本表拉不下来、映射是空的，界面上也不会出现切了没反应的空选项。
    """
    d = lang_data()
    names = d.get("lang_names") or {}
    have = [x for x in (d.get("langs") or []) if x in names and x in LANGS]
    out = [{"id": x, "name": LANG_CN_NAMES.get(x) or names.get(x) or x} for x in LANGS if x == "zh-cn" or x in have]
    return {
        "langs": out,
        "version": d.get("version") or "",
        "updated": d.get("updated") or "",
        "count": sum(len((d.get("by_cat") or {}).get(c, {})) for c in SECTIONS),
    }
