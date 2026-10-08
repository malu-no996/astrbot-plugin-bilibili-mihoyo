"""社交命令 · 绝区零调频（抽卡）总结。

从原 social_apis.py 拆出来的接口块：原来的「最近 5 抽」升级成「总结」——

  · 文字消息：角色名 → 频段 → 总览 → 平均 → 已垫，再逐行列每次出金
    （不带序号，名字按显示宽度补空格对齐）；详见 _summary_lines 的 docstring；
  · 图片消息：gacha_image.render_summary 渲染成总结图（「详细设置」里选输出方式）——
    标题写**角色名**（不写 UID），「当前已垫」条画在出金列表上方、颜色按已垫抽数分档。

**频段词 ↔ 频段代码的对应表放在「详细设置」里可改**（选项 pool_aliases）：
页面上每行写 `代码 = 词1, 词2`，改完点「确定」立即生效；
解析见 _parse_aliases，清空或全写错就回退内置默认表（_ALIASES_DEFAULT）。

数据源：本地存档（gacha_store，data/zzz/{uid}.csv）+ 只拉这个频段的增量 ——
本地已有该频段数据时以最大 item id 为游标只拉新记录（通常 1 页，几秒返回）；
首次查该频段才全量翻页（页间隔 1 秒，历史越长越慢）。统计口径与网页端一致
（gacha_stats.summarize），不带参数默认查「独家频段」。
"""

from __future__ import annotations

import re
import unicodedata

from loguru import logger

from ..core import bind
from ..core import mys as client
from ..zzz.gacha import image as gacha_image, stats as gacha_stats, store as gacha_store
from ..zzz.gacha.api import GACHA_TYPES, full_gacha_log
from .base import OUTPUT_OPTIONS, _image_reply, _pick_role
from .core import Ctx, interface

# 内置默认的「频段词 → 频段代码」表（页面「详细设置 → 频段别名」的初始值，
# 也是页面填错时的兜底）。频段代码是官方的固定值：1 常驻 / 2 独家 / 3 音擎 /
# 5 邦布 / 102 独家重映 / 103 音擎回响。
_ALIASES_DEFAULT = "\n".join([
    "2 = 独家, 独家调频, 独家频段",
    "1 = 常驻, 常驻频段",
    "3 = 音擎, 音擎频段",
    "5 = 邦布, 邦布频段",
    "102 = 重映, 独家重映, 复刻, 独家复刻",
    "103 = 回响, 音擎回响, 音擎复刻",
])


def _parse_aliases(text: str) -> dict:
    """把页面上的「频段别名」文本解析成 `{词: 代码}`。

    每行一条，`=` 两边分别是代码和词，两边哪边是代码都认（代码必须在
    GACHA_TYPES 里，否则整行忽略）：
        `2 = 独家, 独家调频`   ← 页面默认、推荐写法（一行一个频段）
        `复刻=102`             ← 也认
    `#` 之后是注释；词之间用中英文逗号 / 分号 / 斜杠 / 空白分隔都行。
    解析结果为空（清空 / 全写错）时由调用方回退内置默认表。
    """
    out: dict = {}
    for raw in str(text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        left, right = (p.strip() for p in line.split("=", 1))
        if not left or not right:
            continue
        if left in GACHA_TYPES:              # `代码 = 词...`
            code, words = left, right
        elif right in GACHA_TYPES:           # `词... = 代码`
            code, words = right, left
        else:
            logger.warning(f"miyoushe 抽卡频段别名忽略（代码不合法）：{line}")
            continue
        for w in re.split(r"[,，;；/、\s]+", words):
            w = w.strip()
            if w:
                out[w] = code
    return out


_DEFAULT_ALIASES = _parse_aliases(_ALIASES_DEFAULT)


def _cmd_word(ctx: Ctx) -> str:
    """当前命令的写法（用于提示里的示例，命令名改了也跟着变）。"""
    return str(((ctx.cmd or {}).get("cmd")) or "gacha")


def _pool_usage(cmd: str, aliases: dict, bad: list[str]) -> str:
    """参数认不出来时的提示：把当前生效的频段词 / 代码全列出来。

    用户并不知道「2 / 102」这些数字是什么，输错了还静默按独家频段查会让人以为查错了，
    所以这里**直接回一条可用参数清单**（频段词来自页面「详细设置」里正在生效的那份）。

    ⚠️ 命令后面**只认频段一个参数**，不认 UID —— 查谁永远由「切换角色」决定
    （用户明确要求，2026-10-09：UID 能被人拿去直接查自己的抽卡记录，等于泄露）。
    """
    groups: dict[str, list[str]] = {}
    for word, code in aliases.items():
        groups.setdefault(str(code), []).append(word)
    lines = []
    for code in sorted(groups, key=lambda c: int(c) if str(c).isdigit() else 10 ** 9):
        lines.append(
            f"  {GACHA_TYPES.get(code, '频段 ' + code)}："
            + " / ".join(groups[code]) + f"（也可直接写 {code}）"
        )
    return (
        f"参数看不懂：{'、'.join(bad)}\n"
        f"抽卡命令后面只能跟**频段**（可省，不写就是独家频段）；"
        f"查哪个角色由「切换角色」决定，不用也不能填 UID。\n\n"
        f"可用的频段：\n" + "\n".join(lines) + "\n\n"
        f"用法示例：{cmd}　·　{cmd} 常驻　·　{cmd} 音擎"
    )

_SUMMARY_SAMPLE = (
    "{role}\n"
    "绝区零调频 · {pool_name}\n"
    "{total} 抽 · {s_total} 金 · {a_total} A\n"
    "平均 {avg_per_s} 抽/金\n"
    "当前已垫 {pity_now} 抽\n"
    "{#items}{text}\n{/items}"
)


@interface(
    "zzz_gacha", "绝区零 · 调频记录",
    "抽卡总结（参数只要一个频段，可省；不带参数=独家频段，"
    "查谁由「切换角色」决定，不接受 UID）。"
    "频段可打：独家/常驻/音擎/邦布/重映(复刻)/回响，或代码 1/2/3/5/102/103）",
    options=[
        {"key": "output", "label": "输出方式", "type": "select",
         "options": OUTPUT_OPTIONS, "default": "image"},
        {"key": "pool_aliases", "label": "频段词 ↔ 频段代码", "type": "textarea", "rows": 7,
         "default": _ALIASES_DEFAULT,
         "hint": "每行一条：「代码 = 词1, 词2」。代码是官方固定值 —— "
                 "1 常驻 / 2 独家 / 3 音擎 / 5 邦布 / 102 独家重映 / 103 音擎回响；"
                 "词随便改（中英文逗号分隔），改完点「确定」立即生效。清空则用内置默认。"},
    ],
    tpl_vars=[
        {"name": "role", "desc": "绝区零角色名（取不到昵称时退回显示 UID）"},
        {"name": "pool_name", "desc": "频段名（如 独家频段）"},
        {"name": "uid", "desc": "游戏 UID"},
        {"name": "total", "desc": "统计范围内的总抽数"},
        {"name": "s_total", "desc": "S 级（金）数量"},
        {"name": "a_total", "desc": "A 级数量"},
        {"name": "avg_per_s", "desc": "平均多少抽出金（没有金时为空）"},
        {"name": "pity_now", "desc": "当前已垫抽数"},
        {"name": "items", "desc": "出金列表，每条含 name（已带【】）、pity、tag、is_up，"
                                  "以及已经对齐好的整行 text（tag 只在能判定 UP/歪 时才有值）"},
    ],
    sample=_SUMMARY_SAMPLE,
)
async def _api_zzz_gacha(ctx: Ctx) -> dict:
    text = (ctx.arg or "").strip()
    gacha_type = "2"                      # 不带参数 → 独家频段
    # 别名表取自「详细设置 → 频段词 ↔ 频段代码」（页面可改）；清空 / 全写错时回退内置默认。
    aliases = _parse_aliases(str(ctx.opt("pool_aliases", "") or "")) or _DEFAULT_ALIASES
    bad: list[str] = []                   # 认不出来的参数（后面统一提示，别静默忽略）
    for token in text.split():
        t = aliases.get(token) or (token if token in GACHA_TYPES else "")
        if t:
            gacha_type = t
        else:
            # ⚠️ 不再接受 UID 参数（2026-10-09）：谁都能拿一个 UID 查到别人的抽卡记录，
            # 等于把隐私递出去。查谁一律看「切换角色」，所以任何非频段 token 都是错参数。
            bad.append(token)
    if bad:
        # ⚠️ 必须在发任何请求之前回提示：原来这类 token 被悄悄丢掉、按独家频段查，
        # 用户只会以为「查出来的是错的」，根本不知道自己参数写错了。
        return {"text": _pool_usage(_cmd_word(ctx), aliases, bad)}
    aid = bind.default_account(ctx.user_id)
    if not aid:
        return {"text": "未绑定米游社账号：请先发送「米游社登录」扫码绑定你的米游社账号"}

    # 角色列表：靠它定位「当前角色」（「切换角色」设过的优先，没设过取列表第一个）。
    try:
        roles = await client.bind_roles(aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取绝区零角色失败：{exc}"}

    server = "prod_gf_cn"
    role_name = ""
    role = _pick_role(roles, bind.role_default(ctx.user_id, aid))
    if not role:
        return {"text": "当前账号没有绑定绝区零角色"}
    uid = str(role.get("game_uid") or "")
    server = role.get("region") or server
    role_name = str(role.get("nickname") or "")

    # 本地存档 + 增量：该频段有存档就以最大 item id 为游标只拉新的，没有才全量翻页
    stored = gacha_store.load(uid) or {}
    all_old = list(stored.get("items") or [])
    pool_old = [it for it in all_old if gacha_stats.base_type(it) == gacha_type]
    stop_id = gacha_store.max_id(pool_old) if pool_old else ""
    try:
        res = await full_gacha_log(
            uid, server, gacha_type=gacha_type, account_id=aid, stop_id=stop_id
        )
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取抽卡记录失败：{exc}"}
    fresh = res.get("list") or []
    if fresh:
        try:
            gacha_store.save(uid, server, gacha_store.merge(all_old, fresh))
        except Exception as exc:  # noqa: BLE001 —— 落盘失败不挡着出结果
            logger.warning(f"miyoushe 抽卡记录落盘失败（uid={uid}）：{exc}")

    items = gacha_store.merge(pool_old, fresh)          # 时间倒序
    if not items:
        label = GACHA_TYPES.get(gacha_type, gacha_type)
        return {"text": f"没有拉到{label}的调频记录"}

    s = gacha_stats.summarize(items)
    p = (s.get("pools") or [{}])[0]
    lines, rows = _summary_lines(p, uid, role_name)
    # 给管理页「预览」看的原始数据：**不放整份抽卡记录**（老号动辄几千条，
    # 塞进 JSON 会把页面卡住），只给频段统计 p —— 出金列表、已垫、均值都在它里面。
    data_ = {
        "uid": uid,
        "role": role_name,
        "gacha_type": gacha_type,
        "pool_name": p.get("name") or "",
        "records": len(items),          # 本地这份频段的记录总数
        "stats": p,                     # gacha_stats.summarize 的频段条目
    }
    vars_ = {
        "role": lines[0],
        "pool_name": p.get("name") or "",
        "uid": uid,
        "total": p.get("total") or 0,
        "s_total": p.get("s_count") or 0,
        "a_total": p.get("a_count") or 0,
        "avg_per_s": p.get("avg_per_s") or "",
        "pity_now": p.get("pity_now") or 0,
        "items": rows,
    }
    # 输出方式**默认图片**（用户要求：有图片版就不要文字版）→ 渲染总结图发出；
    # 发图成功后返回 silent：**不再跟发一条文字**。
    # 退回时**必须把原因写进消息里**：否则用户只看到文字，完全不知道图片为什么没来
    # （渲染报错 / 适配器不支持 / 发送被平台拒 都可能，只有控制台日志里有）。
    if str(ctx.opt("output", "image")) == "image":
        return await _image_reply(ctx, lambda: gacha_image.render_summary(p, role_name),
                                  lines, vars_, data_)
    return {"text": "\n".join(lines), "vars": vars_, "data": data_}


def _disp_w(s: str) -> int:
    """字符串的显示宽度：全角/宽字符算 2 列，其余算 1 列（补空格对齐用）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    """按**显示宽度**在右边补半角空格，让后面的「 · 」对齐成一列。"""
    return s + " " * max(0, width - _disp_w(s))


def _summary_lines(p: dict, uid: str, role_name: str = "") -> tuple[list[str], list[dict]]:
    """把 pool 条目变成（默认文本行, 模板 items）——**有多少条出金就写多少行，不做截断**。

    版式（用户定的）：

        重霄
        绝区零调频 · 独家频段
        216 抽 · 5 金 · 27 A
        平均 43.2 抽/金
        当前已垫 30 抽
        【格莉丝】   · 68抽
        【蕾米埃尔】 · 67抽
        【「11号」】 · 42抽

      · 第一行是**绝区零角色名**（拿不到就退回 `UID xxxxx`，绝不空着）；
      · 总览拆三行：抽数总览 / 平均值（没出过金时这行省掉）/ 当前已垫；
      · 出金行**不带序号**：名字统一加【】，按显示宽度补空格对齐，
        抽数按位数右对齐（「抽」对齐成一列）；
      · UP/歪 只在**能判定**时才追在行尾 —— 官方 getGachaLog 不返回是否 UP，
        判不出来硬写「未知」只是噪音，所以什么都不写。

    返回的 items 每条除了 pity/tag/is_up，还带 `name`（**已带【】的展示名**）
    与**已经对齐好的整行** `text`，方便回复模板直接 `{#items}{text}` 用
    （自己拼 {name}/{pity} 就不带对齐空格）。
    """
    head = role_name or f"UID {uid}"
    lines = [head, f"绝区零调频 · {p.get('name') or '调频记录'}"]
    lines.append(
        f"{p.get('total') or 0} 抽 · {p.get('s_count') or 0} 金 · {p.get('a_count') or 0} A"
    )
    if p.get("avg_per_s"):
        lines.append(f"平均 {p['avg_per_s']} 抽/金")
    lines.append(f"当前已垫 {p.get('pity_now') or 0} 抽")

    golden = [g for g in (p.get("golden") or []) if isinstance(g, dict)]
    # 名字统一加【】显示（用户定的版式）；对齐宽度按**加完括号后**的显示宽度算，
    # 每条都加了同样两个全角括号，所以对齐关系不变。
    disp_names = [f"【{str(g.get('name') or '未知')}】" for g in golden]
    name_w = max((_disp_w(n) for n in disp_names), default=0)
    pity_w = max((len(str(int(g.get("pity") or 0))) for g in golden), default=0)

    rows: list[dict] = []
    for g, disp in zip(golden, disp_names):
        up = g.get("is_up")
        tag = ""
        if g.get("has_up") and up is not None:
            tag = "UP" if up else "歪"
        pity = int(g.get("pity") or 0)
        line = f"{_pad(disp, name_w)} · {str(pity).rjust(pity_w)}抽"
        if tag:
            line += f" · {tag}"
        lines.append(line)
        rows.append({
            "name": disp,                      # 已带【】（与展示一致）
            "pity": pity,
            "tag": tag,
            "is_up": "" if up is None else bool(up),
            "text": line,
        })
    return lines, rows
