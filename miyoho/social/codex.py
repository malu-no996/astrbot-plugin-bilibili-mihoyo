"""社交命令 · 图鉴查询（不需要登录，读本地快照 data/zzz/ 下的分段文件）。

从 social.py 拆出来的第三块。图鉴是**静态资料**（代理人 / 音擎 / 驱动盘 / 邦布），
所以这个接口不查绑定账号、不需要扫码登录。

不能「查全部」（会刷屏）：列表一律截到「列表条数」（默认 6，可在详细设置里改），
数字参数取列表第几条时也是先截再取，所以第 7 条起就是「超出不显示」。

参数**四种同时可用、不用先去详细设置切换**（用户明确要求）：
  <空>          按详细设置里的「查询方式」出列表（默认 = 最新几位）
  <序号>        该列表的第 n 条详情
  <名字>        按名字搜（中文名 / 全名 / 英文名都匹配）
  <属性 / 职业> 出该属性或职业的列表；再跟序号就是其中第 n 条的详情（如「火」「异常 2」）
"""

from __future__ import annotations

import re
from typing import Any

from ..zzz.codex import data as codex_data
from .core import Ctx, interface


# ================= 图鉴查询（不需要登录） =================
#
# 图鉴是**静态资料**（代理人 / 音擎 / 驱动盘 / 邦布），来自本地快照 data/zzz/ 下的四个分段文件，
# 所以这个接口不需要绑定米游社账号、不需要登录。
# ⚠️ 不能「查全部」：代理人 60+ 条全量刷在群里是刷屏，所以任何查询方式都只给列表前 N 条
#   （N = 详细设置里的「列表条数」，默认 6），要具体某一条就用参数（序号或名字）指定。


CODEX_CATS: list[dict] = [
    {"key": "agents", "label": "代理人"},
    {"key": "wengines", "label": "音擎"},
    {"key": "discs", "label": "驱动盘"},
    {"key": "bangboo", "label": "邦布"},
]
CODEX_MODES: list[dict] = [
    # ⚠️ 这四个只是「命令**不带参数**时的默认查询方式」（下面 mode 变量的来源）。
    # 带参数时命令行自己认属性 / 职业 / 名字 / 序号，跟这里选什么无关 —— 用户要「同时都能用」。
    {"value": "latest", "label": "按最新（不带参数时给最新几位）"},
    {"value": "name", "label": "按名字（不带参数时只用参数查名字）"},
    {"value": "profession", "label": "按职业（如「异常」）"},
    {"value": "element", "label": "按属性（如「火」）"},
]


# 数据还没抓到时下拉框的兜底值（正常情况会被图鉴数据里的实际值覆盖）。
# 提到模块级是因为「命令行关键词识别」也要用同一份 —— 写两遍早晚会不一致。
_PROFS_FALLBACK = ["异常", "强攻", "击破", "支援", "防护", "命破", "锋御"]
_ELEMS_FALLBACK = ["火", "电", "冰", "物理", "以太", "风"]


def _codex_distinct(field: str, fallback: list[str]) -> list[str]:
    """从图鉴数据里去重取出某字段的可选值。

    动态取而不是写死枚举：新角色 / 新职业 / 新属性上线后自动出现在下拉框里。
    读不到（数据还没抓）时退回 fallback，保证页面起码有得选。
    """
    out: list[str] = []
    try:
        for it in codex_data.section("agents"):
            v = str((it or {}).get(field) or "").strip()
            if v and v not in out:
                out.append(v)
    except Exception:  # noqa: BLE001
        pass
    return out or list(fallback)


def _codex_kw_values() -> list[tuple[str, str]]:
    """命令行能识别的「(字段, 值)」候选：属性在前、职业在后（仅代理人有这两维）。

    值全部从图鉴数据里动态取，新属性 / 新职业上线后**自动认得**，不用改代码。
    """
    out: list[tuple[str, str]] = []
    for v in _codex_distinct("element", _ELEMS_FALLBACK):
        out.append(("element", v))
    for v in _codex_distinct("profession", _PROFS_FALLBACK):
        out.append(("profession", v))
    return out


def _codex_kw_match(category: str, kw: str, loose: bool = False) -> tuple[str, str] | None:
    """把命令参数里的关键词认成「属性」或「职业」。认不出返回 None。

    两档：**精确**（「火」= 火属性、「异常」= 异常职业）永远先试；**宽松**（互相包含，
    认「火属性」「异常职业」「冰系」这种带后缀的说法）只在名字搜失败后再试 —— 顺序不能反，
    否则名字里带「电 / 冰 / 风」的角色会被属性抢走。
    """
    if category != "agents":
        return None                       # 音擎 / 驱动盘 / 邦布没有属性职业这两维
    k = str(kw or "").strip()
    if not k:
        return None
    cands = _codex_kw_values()
    for field, val in cands:              # 精确：先说属性，再说职业
        if k == val:
            return field, val
    if not loose:
        return None
    for field, val in cands:              # 宽松：双向包含（「火属性」「异常职业」…）
        if k in val or val in k:
            return field, val
    return None


def _codex_split_arg(arg: str) -> tuple[str, int | None]:
    """把命令参数拆成「关键词 + 序号」。

    支持 `3` / `火` / `火 3` / `星见雅` / `星见雅 2`；
    逗号（中英文）、顿号、空格都当分隔符 —— 用户手打时这几种最容易混。
    """
    s = re.sub(r"[,，、\s]+", " ", str(arg or "")).strip()
    if not s:
        return "", None
    if s.isdigit():
        return "", int(s)
    m = re.fullmatch(r"(.+?)\s+(\d+)", s)
    if m:
        return m.group(1).strip(), int(m.group(2))
    return s, None


def _codex_usage(cmd_name: str, category: str) -> str:
    """参数认不出来时的用法提示 —— 把能用的几种形态一次说全，免得用户去翻文档。"""
    line1 = f"用法：{cmd_name} <序号> ｜ <名字> ｜ <属性 / 职业>[ 序号]"
    if category == "agents":
        elems = "/".join(_codex_distinct("element", _ELEMS_FALLBACK))
        profs = "/".join(_codex_distinct("profession", _PROFS_FALLBACK))
        line2 = (f"例：{cmd_name} 星见雅（名字）｜ {cmd_name} 火（{elems}）"
                 f"｜ {cmd_name} 异常 2（{profs}）")
    else:
        line2 = f"例：{cmd_name} 名字 ｜ {cmd_name} 1（列表第 1 条）"
    return line1 + "\n" + line2


def _codex_options() -> list[dict]:
    """图鉴接口的「详细设置」表单 schema（注册时算一次）。"""
    profs = _codex_distinct("profession", _PROFS_FALLBACK)
    elems = _codex_distinct("element", _ELEMS_FALLBACK)
    return [
        {
            "key": "category", "label": "图鉴类别", "type": "select", "default": "agents",
            # ⚠️ schema 里下拉框的键是 value/label（CODEX_CATS 用的是 key/label，要转一次），
            # 不转的话前端 <option :value="o.value"> 全是 undefined、后端 allowed 也判不中。
            "options": [{"value": c["key"], "label": c["label"]} for c in CODEX_CATS],
            "hint": "只有「代理人」有职业 / 属性，选别的类别时那两种方式会自动按「最新」查",
        },
        {
            # 默认「按最新」：不带参数发命令就给最新的 6 条列表，比提示「要给个名字」直观。
            # 注意：**只在不带参数时生效**，带参数时命令行自己会认属性 / 职业 / 名字 / 序号，
            # 不需要先来这里切换（用户明确要「同时都能用」）。
            "key": "mode", "label": "查询方式", "type": "select", "default": "latest",
            "options": [dict(x) for x in CODEX_MODES],
            "hint": "只在命令**不带参数**时生效；带参数时自动识别（数字 = 第几条，"
                    "「火 / 异常」= 属性 / 职业，其它 = 名字）",
        },
        {
            "key": "profession", "label": "默认职业", "type": "select",
            "default": "异常" if "异常" in profs else profs[0],
            "options": [{"value": v, "label": v} for v in profs],
            "hint": "只在「查询方式 = 按职业」且命令不带参数时用；想临时查别的职业直接发"
                    "「图鉴 强攻」就行，不用改这里",
        },
        {
            "key": "element", "label": "默认属性", "type": "select",
            "default": "火" if "火" in elems else elems[0],
            "options": [{"value": v, "label": v} for v in elems],
            "hint": "只在「查询方式 = 按属性」且命令不带参数时用；想临时查别的属性直接发"
                    "「图鉴 冰」就行，不用改这里",
        },
        {
            "key": "limit", "label": "列表条数", "type": "number", "default": 6,
            "min": 1, "max": 20,
            "hint": "列表最多显示几条（默认 6，和「最新代理人只给 6 条」一致）；序号超出就提示不显示",
        },
    ]


def _codex_idnum(value: Any) -> int:
    """图鉴 id → 数值（用于排序）。id 是字符串且可能带字母，取不到数字当 0。"""
    s = re.sub(r"[^0-9]", "", str(value or ""))
    return int(s) if s else 0


def _codex_sorted(items: Any) -> list[dict]:
    """排序：稀有度 S 在前，同稀有度内**新的在前**（图鉴 id 随时间递增，故 id 降序）。

    与前端 frag/zzz-codex.js 的 cxSorted 同一规则，两边看到的新旧顺序才一致。
    """
    rows = [x for x in (items or []) if isinstance(x, dict)]
    return sorted(
        rows,
        key=lambda it: (0 if str(it.get("rarity") or "").upper() == "S" else 1,
                        -_codex_idnum(it.get("id"))),
    )


def _codex_row(it: dict, index: int = 0) -> dict:
    """一条记录的「模板变量」：所有类别都用同一套键，缺的字段给空串（模板里不会显示 undefined）。"""
    return {
        "index": index,
        "id": str(it.get("id") or ""),
        "name": str(it.get("name") or ""),
        "rarity": str(it.get("rarity") or ""),
        "element": str(it.get("element") or ""),
        "profession": str(it.get("profession") or ""),
        "camp": str(it.get("camp") or ""),
        "hit_type": str(it.get("hit_type") or ""),
    }


def _codex_detail(category: str, it: dict, index: int = 0) -> dict:
    """详情的模板变量：在行变量基础上补各类别自己的字段。"""
    d = _codex_row(it, index)
    if category == "agents":
        d["full_name"] = str(it.get("full_name") or "")
        d["en_name"] = str(it.get("en_name") or "")
    elif category == "wengines":
        d["main"] = str(it.get("main") or "")
        d["sub"] = str(it.get("sub") or "")
        talents = [t for t in (it.get("talents") or []) if isinstance(t, dict)]
        d["talent"] = str((talents[0] or {}).get("desc") or "") if talents else ""
    elif category == "discs":
        d["desc1"] = str(it.get("desc1") or "")
        d["desc2"] = str(it.get("desc2") or "")
    return d


def _codex_line(category: str, row: dict) -> str:
    """默认（没配模板时）列表里一行的文本。"""
    name = row.get("name") or "?"
    if category == "agents":
        return f"{row['index']}. {name}（{row['rarity']}·{row['element']}·{row['profession']}）"
    if category == "wengines":
        return f"{row['index']}. {name}（{row['rarity']}）"
    if category == "bangboo":
        return f"{row['index']}. {name}（{row['rarity']}）"
    return f"{row['index']}. {name}"


def _codex_detail_text(category: str, d: dict) -> str:
    """默认（没配模板时）详情的文本。"""
    if category == "agents":
        lines = [
            f"{d['name']}（{d['rarity']}）· {d['element']}属性 · {d['profession']}",
            f"阵营：{d['camp']}　伤害类型：{d['hit_type']}",
        ]
        if d.get("en_name"):
            lines.append(f"英文名：{d['en_name']}")
        return "\n".join(lines)
    if category == "wengines":
        lines = [f"{d['name']}（{d['rarity']}）", f"{d['main']}　{d['sub']}".strip()]
        if d.get("talent"):
            lines.append(f"音擎效果：{d['talent']}")
        return "\n".join(lines)
    if category == "discs":
        return "\n".join([d["name"], f"二件套：{d['desc1']}", f"四件套：{d['desc2']}"])
    return f"{d['name']}（{d['rarity']}）"


@interface(
    "zzz_codex", "绝区零 · 图鉴",
    "按名字 / 属性 / 职业 / 序号查图鉴（不用登录）。"
    "参数自动识别：「星见雅」按名字、「火」按属性、「异常 2」按职业取第 2 条、「1」按最新取第 1 条，"
    "不带参数出默认列表",
    options=_codex_options(),
    tpl_vars=[
        {"name": "category", "desc": "类别名（代理人 / 音擎 / 驱动盘 / 邦布）"},
        {"name": "kind", "desc": "本次是列表还是详情：list / detail"},
        {"name": "title", "desc": "本次查询的标题（如「最新 6 位代理人」）"},
        {"name": "total", "desc": "结果条数"},
        {"name": "items", "desc": "结果列表，配合 {#items}…{/items} 循环输出"},
        {"name": "index / name / rarity", "desc": "循环内每条的字段（还有 element / profession / camp）"},
        {"name": "detail", "desc": "详情对象，配合 {#detail}…{/detail}；子字段如 {detail.name}"},
    ],
    sample=(
        "【绝区零图鉴 · {category}】{title}\n"
        "{#items}\n"
        "{index}. {name}｜{rarity}｜{element}{profession}\n"
        "{/items}\n"
        "{#detail}\n"
        "{detail.name}（{detail.rarity}）\n"
        "属性：{detail.element}　职业：{detail.profession}\n"
        "{/detail}"
    ),
)
async def _api_zzz_codex(ctx: Ctx):
    category = str(ctx.opt("category", "agents") or "agents")
    if category not in {c["key"] for c in CODEX_CATS}:
        category = "agents"
    cat_label = next((c["label"] for c in CODEX_CATS if c["key"] == category), category)
    mode = str(ctx.opt("mode", "latest") or "latest")   # 默认按最新（见 schema 的 default）
    if category != "agents" and mode in ("profession", "element"):
        mode = "latest"                                   # 只有代理人有职业 / 属性
    try:
        limit = int(ctx.opt("limit", 6) or 6)
    except (TypeError, ValueError):
        limit = 6
    limit = max(1, min(20, limit))
    arg = (ctx.arg or "").strip()

    items = _codex_sorted(codex_data.section(category))
    if not items:
        return f"图鉴里还没有{cat_label}数据（到管理页「绝区零 → 图鉴」点一次「更新数据」）"

    cmd_name = str((ctx.cmd or {}).get("cmd") or "图鉴")

    def base_data(kind: str, shown: int, total: int, raw: list[dict]) -> dict:
        """给管理页「预览」看的原始数据（图鉴不需要登录，这份永远拿得到）。

        raw 是**没加工的图鉴记录**（图标 URL、描述全文等都在里面），
        比 vars 里那份「挑过、转过」的字段全 —— 配模板时想知道还有什么能写就看它。
        """
        return {
            "category": category, "category_label": cat_label, "mode": mode,
            "arg": arg, "limit": limit, "kind": kind,
            "count": shown, "total": total, "items": raw,
        }

    def as_list(pool: list[dict], title: str) -> dict:
        picked = pool[:limit]
        rows = [_codex_row(it, i) for i, it in enumerate(picked, 1)]
        lines = [f"【图鉴·{cat_label}】{title}（{len(rows)} 条）"]
        lines += [_codex_line(category, r) for r in rows]
        if len(pool) > limit:
            lines.append(f"（还有 {len(pool) - limit} 条未显示，带序号或名字可查具体某条）")
        else:
            dim = "、属性 / 职业（如「火」「异常」）" if category == "agents" else ""
            lines.append(f"带名字{dim}或序号可以看具体某条，例：{cmd_name} 1")
        return {
            "text": "\n".join(lines),
            "vars": {"kind": "list", "category": cat_label, "title": title,
                     "total": len(rows), "items": rows, "detail": {}},
            "data": base_data("list", len(rows), len(pool), picked),
        }

    def as_detail(pool: list[dict], n: int) -> dict | None:
        if n < 1 or n > len(pool):
            return None
        it = pool[n - 1]
        d = _codex_detail(category, it, n)
        return {
            "text": _codex_detail_text(category, d),
            # 详情也给一份 items（就这一条）：这样同一套模板里 {#items} 块在详情时也能渲染
            "vars": {"kind": "detail", "category": cat_label,
                     "title": d.get("name") or "", "total": 1,
                     "items": [_codex_row(it, n)], "detail": d},
            "data": base_data("detail", 1, len(pool), [it]),
        }

    def query_dim(field: str, val: str, n: int | None):
        """按属性 / 职业查：不带序号出列表，带序号出该列表第 n 条的详情。"""
        label = f"{val}属性" if field == "element" else f"{val}职业"
        pool = [x for x in items if str(x.get(field) or "") == val]
        if not pool:
            return f"没有{label}的{cat_label}"
        if n is None:
            return as_list(pool, f"{label} · 共 {len(pool)} 位")
        got = as_detail(pool[:limit], n)
        if got:
            return got
        return (f"{label}只显示前 {min(limit, len(pool))} 条，"
                f"第 {n} 条超出范围（把「列表条数」调大可以显示更多）")

    def find_by_name(text: str) -> list[dict]:
        """按名字搜：一个关键词同时匹配 中文名 / 全名 / 英文名。"""
        t = text.lower()
        return [
            it for it in items
            if t in str((it.get("name") or "") + " " + (it.get("full_name") or "")
                        + " " + (it.get("en_name") or "")).lower()
        ]

    # ============ 参数解析：属性 / 职业 / 名字 / 序号 **四种同时可用** ============
    #   图鉴 3        → 当前查询方式那个列表的第 3 条（默认 = 最新）
    #   图鉴 火       → 火属性代理人列表
    #   图鉴 异常 2   → 异常职业列表的第 2 条详情
    #   图鉴 星见雅   → 按名字搜（命中 1 条直接出详情）
    # ⚠️ 识别顺序：属性 / 职业「精确」→ 名字 → 属性 / 职业「宽松」。
    #    名字排在前面，是为了别把「名字里带电 / 冰 / 风」的角色抢走。
    kw, num = _codex_split_arg(arg)

    # ① 只有序号：按「详细设置」里的查询方式取第 n 条（老行为，保留）
    if not kw and num is not None:
        if mode == "latest":
            got = as_detail(items[:limit], num)
            if got:
                return got
            if num < 1:
                return "序号要从 1 开始"
            return (f"只显示前 {min(limit, len(items))} 位{cat_label}，第 {num} 条超出范围"
                    f"（把「列表条数」调大可以显示更多）")
        if mode in ("profession", "element"):
            val = str(ctx.opt("profession" if mode == "profession" else "element", "") or "")
            return query_dim(mode, val, num)
        # mode = name 时数字当名字搜（基本搜不到），统一走下面的用法提示
        return _codex_usage(cmd_name, category)

    # ② 有关键词：属性 / 职业（精确）→ 名字 → 属性 / 职业（宽松）
    if kw:
        hit = _codex_kw_match(category, kw)
        if hit:
            return query_dim(hit[0], hit[1], num)
        hits = find_by_name(kw)
        # 名字里多打了空格（「星见 雅」「火 鸟」）也认：图鉴名字本身不含空格，去掉再搜一次。
        # ⚠️ 必须排在「宽松认属性」**之前** —— 否则「火 鸟」会先被当成火属性关键词。
        if not hits and " " in kw:
            hits = find_by_name(kw.replace(" ", ""))
        if hits:
            if num is None:
                return as_detail(hits, 1) if len(hits) == 1 else as_list(hits, f"名字含「{kw}」的{cat_label}")
            got = as_detail(hits[:limit], num)
            if got:
                return got
            return f"「{kw}」的结果只显示前 {min(limit, len(hits))} 条，第 {num} 条超出范围"
        hit = _codex_kw_match(category, kw, loose=True)
        if hit:
            return query_dim(hit[0], hit[1], num)
        return f"没找到「{kw}」：既不是名字，也不是属性 / 职业。\n" + _codex_usage(cmd_name, category)

    # ③ 不带参数 = 按配置的查询方式出列表
    if mode == "latest":
        return as_list(items, f"最新 {min(limit, len(items))} 位{cat_label}")
    if mode in ("profession", "element"):
        val = str(ctx.opt("profession" if mode == "profession" else "element", "") or "")
        if not val:
            return f"「详细设置」里还没选默认{'职业' if mode == 'profession' else '属性'}"
        return query_dim(mode, val, None)
    # name 方式不带参数没有意义（= 查全部，会被刷屏），所以提示用法
    return _codex_usage(cmd_name, category)
