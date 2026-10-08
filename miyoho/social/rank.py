"""社交命令 · 群排行：危局强袭战 / 绝境 / 式舆防卫战 的本群排行榜。

数据来源（用户明确要求，2026-10-05）
------------------------------------
**只读本地存档**：`data/zzz/records/{kind}/{uid}.json`。
存档由「用户查询时顺手存一份」+「后台每天自动抓一轮」两个人攒出来 —— 用户原话
「群排行的成绩改为只查存档的（就是有查过记录的）」，所以本模块**不对任何人实时
拉取战绩**：没查过、没被存过档的人不会出现在榜上。

成员范围
--------
**当前 Q 群**里绑定了米游社账号的人：
  · OneBot  → `get_group_member_list` 拉群成员名单（最准）；
  · QQ 官方 → 拿不到群成员列表，退化为「在本群发过言的人」（见 seen.py）。

榜单单位 = **角色（game_uid），不是一个 QQ 用户**（2026-10-05 用户当场纠正）
-----------------------------------------------------------------------
这才是需求本意：原话「列出统计同一个 Q 群危局 / 防卫战**高的角色**」，
列的字段里也有「角色名」。所以**一个人名下有几个绝区零角色就出几行** ——
遍历 TA 绑定的**每个**米游社账号（`bind.accounts`）+ 每个账号的角色列表
（`bind_roles`），凡是**有存档**的角色都上榜。

⛔ 第一版只取「默认账号下的默认角色」→ 一个人三个角色只出一条，用户报
   「查出来只有一个角色的记录，明明该有三条」。**别再退回那种写法。**

⚠️ 只有**绑定到 QQ** 的账号里的角色才算（否则任何人都能把别人的角色拉进榜）。
⚠️ 同一个人名下、甚至不同人绑的同一个角色（game_uid 相同）只出一行（按 uid 去重）。

一个榜的赛期怎么定
------------------
同一个群里不同人查的时间不一样，存档可能落在不同赛期。为了公平，**只排同一个赛期**：
取所有候选里最新的那个赛期 key，只把赛期等于它的人排进去。本期没打过、或者还停在
旧赛期的人，自然就不在榜上。

取分口径
--------
  deadly（危局群排行）   → 危局强袭战 普通难度总分（`data.total_score`）
  hard  （绝境群排行）   → 危局强袭战 绝境难度得分（`data.hard_list` 各条 score 之和；
                          绝境通常只有 1 条，求和与「那一条」等价）
  shiyu （防卫战群排行） → 式舆防卫战总分（存档 `summary.score`，即 `brief.score`）

出图（三个榜都支持）
------------------------------
用户点名（2026-10-05）：绝境先加了图片版，随后要求危局 / 防卫战也加。所以三个榜都声明了
「输出方式」选项（文字 / 图片，默认文字），选图片时走 `zzz/record/rank_image.py`：
  · 绝境（hard）：`render_group_rank` —— 一行一队（排名 · 分数 · 出战队伍头像（右上角影画数）
    · 角色名），队伍头像从 `data.hard_list[].avatar_list` 取；
  · 危局 / 防卫战：`render_teams_rank` —— **单行**布局「排名 · 角色名 · 分数 |
    队伍1(三头像) · 队伍2(三头像) · 队伍3(三头像)」，分数在角色名之后、队伍之前。
    队伍头像：危局取 `data.list[].avatar_list`、防卫战取
    `data.hadal_info_v2.fitfh_layer_detail.layer_challenge_info_list`（见 `_team_groups`）。

⚠️ 导入即注册（@interface），`social/__init__.py` 里的 import 顺序决定页面下拉框顺序。
"""
from __future__ import annotations

import time

from loguru import logger


from ..core import bind
from ..core import mys as client
from ..zzz.record import rank_image
from ..zzz.record import store as record_store
from . import seen
from .base import OUTPUT_OPTIONS, _image_reply
from .core import Ctx, interface


# 三个榜各自的取分口径 + 标题（数据都来自存档，kind 只决定读哪个存档目录）
_MEASURES: dict[str, dict] = {
    "deadly": {"kind": "deadly", "title": "危局强袭战", "noun": "危局强袭战"},
    "hard": {"kind": "deadly", "title": "危局强袭战 · 绝境", "noun": "危局绝境"},
    "shiyu": {"kind": "shiyu", "title": "式舆防卫战", "noun": "式舆防卫战"},
}

_LIMIT_OPTION = {
    "key": "limit", "label": "显示前几名", "type": "number", "default": 10,
    "min": 1, "max": 50,
    "hint": "按分数从高到低取前 N 名；本群上榜的角色不够就显示几个。",
}

# 「输出方式」选项 —— 三个榜都支持（用户要求：危局 / 防卫战在文字版基础上也加图片版，
# 绝境之前就有）。**默认图片版**（2026-10-09：有图片版就不要文字版回复）；
# 选「文字消息」仍可退回文字。渲染器另在
# zzz/record/rank_image.py（绝境走 `render_group_rank` 单队行布局；危局 / 防卫战走
# `render_teams_rank`：排名 · 角色名 · 分数 + 队伍1/2/3 各三个头像）。
_OUTPUT_OPTION = {
    "key": "output", "label": "输出方式", "type": "select",
    "options": OUTPUT_OPTIONS, "default": "image",
    "hint": "选「图片消息」发一张榜单图：每行一个角色，含排名 / 角色名 / 分数 / 出战队伍头像"
            "（右上角带影画数）；发送失败会退回文字并附上原因。",
}

_TPL_VARS = [
    {"name": "title", "desc": "榜名（如「危局强袭战 · 绝境」）"},
    {"name": "period", "desc": "榜的赛期（2026-09-25 ~ 2026-10-09）"},
    {"name": "count", "desc": "本群上榜角色数（一个角色一行；不是 top 的条数）"},
    {"name": "limit", "desc": "本次取的前几名"},
    {"name": "items", "desc": "榜单条目（一个角色一行），每条含 index / rank（名次）/ "
                              "score（分数）/ name（角色名）/ uid（绝区零角色 UID）"},
]

# 角色列表按账号缓存，避免一次排行里同一个账号被反复打接口（只是**身份解析**，不取战绩）
_ROLE_CACHE: dict[str, tuple[float, list]] = {}
_ROLE_TTL = 600


async def _roles_cached(aid: str) -> list:
    """米游社账号 → 它名下的绝区零角色列表（10 分钟缓存）。

    群里人一多，同一个账号会被问很多次，所以缓存。**只解析身份，不取战绩**。
    失败不抛、返回空列表（这个账号这次就不贡献角色）。
    """
    hit = _ROLE_CACHE.get(str(aid))
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    try:
        roles = await client.bind_roles(aid)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"miyoushe 群排行：读账号 {aid} 的角色列表失败（{exc}）")
        return []
    rows = [r for r in roles or [] if isinstance(r, dict) and r.get("game_uid")]
    _ROLE_CACHE[str(aid)] = (now + _ROLE_TTL, rows)
    return rows


async def _user_uids(user_id: str) -> list[str]:
    """这个人名下**所有**绝区零角色 UID（榜单是按角色排的，不是按人）。

    遍历 TA 绑定的**每一个**米游社账号（`bind.accounts`，默认账号排最前），
    每个账号取角色列表 —— 有几个角色就返回几个，按 uid 去重。
    没绑定账号 → 空列表。

    ⚠️ 不能用「只取默认账号的默认角色」那种写法（那是单个查询命令的口径）：
    一个人三个角色只会出一条，用户已经当场报过这个 bug。
    """
    out: list[str] = []
    seen: set[str] = set()
    for acc in bind.accounts(user_id):
        aid = str(acc.get("account_id") or "")
        if not aid:
            continue
        for r in await _roles_cached(aid):
            u = str(r.get("game_uid") or "")
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    return out


async def _group_members(ctx: Ctx, gid: str) -> list[str]:
    """本群候选成员 ID 列表（OneBot 拉名单；官方机器人只能靠「发过言的人」）。"""
    # AstrBot 版：ctx.bot 不再暴露适配器 call_api，群排行恒用「在本群发过言的人」
    # （seen 足迹；分发器每条群消息都会记，口径与原官方机器人路径一致）。
    return seen.users(gid)


def _score_of(measure: str, period: dict) -> int:
    """从一条存档里取这个榜要的分数。"""
    data = (period or {}).get("data") or {}
    if measure == "hard":
        return sum(
            int(x.get("score") or 0)
            for x in (data.get("hard_list") or []) if isinstance(x, dict)
        )
    score = (period.get("summary") or {}).get("score")
    if score in (None, ""):
        return int(data.get("total_score") or 0)
    return int(score)


def _nick_of(period: dict) -> str:
    """存档里的角色名（游戏内昵称）。"""
    nick = (period.get("summary") or {}).get("nick")
    if nick:
        return str(nick)
    return str(((period or {}).get("data") or {}).get("nick_name") or "")


def _avatars_of(node: dict | None) -> list[dict]:
    """从一条「队伍记录」里抽出出战代理人头像列表。

    适用对象（字段同构）：危局 `data.list` 项 / 绝境 `hard_list` 项 /
    防卫战 `hadal_info_v2.*_layer_detail.layer_challenge_info_list` 项。每条 `avatar_list`
    带 `role_square_url`（头像）、
    `rarity`（S/A）、`rank`（**影画数**，画在头像右上角）。抽成画图用的
    `{icon, rarity, rank}`（和绝境图片版一致的口径）。
    """
    if not isinstance(node, dict):
        return []
    rows: list[dict] = []
    for a in node.get("avatar_list") or []:
        if not isinstance(a, dict):
            continue
        rows.append({
            "icon": str(a.get("role_square_url") or a.get("icon") or ""),
            "rarity": str(a.get("rarity") or "S").upper(),
            "rank": int(a.get("rank") or 0),
        })
    return rows


def _team_groups(measure: str, period: dict) -> list[list[dict]]:
    """这个角色在本榜**所有**出战队伍（每队 = 一个 avatar_list）。

    ⚠️ 一个角色在危局 / 防卫战里**不止一队**（危局 3 个 boss 各一队、防卫战上下半各一队），
    群排行图片版要把每一队都画出来（用户要求：队伍1 / 队伍2 / 队伍3 各三个代理人头像）。
    返回 `[[头像,头像,头像], [...], ...]`（外层 = 队伍，内层 = 该队代理人）；没队伍时返回空。

    deadly（危局强袭战普通难度）：`data.list` 每条一个队（3 个 boss → 3 队）；
    hard （绝境）：`data.hard_list` 每条一个队（通常只 1 条）；
    shiyu（式舆防卫战）：`data.hadal_info_v2` 的 第五 / 第四防线
           `layer_challenge_info_list`（第五层 3 队、第四层 2 队；层数名是官方 typo fitfh）。
    """
    data = (period or {}).get("data") or {}
    groups: list[list[dict]] = []
    if measure == "deadly":
        # ⚠️ 危局普通难度的每队在 `data.list`（**不是** main_challenge_record_list，
        # 那个字段官方根本没给；存档实测就是 `list`，3 个 boss 各一条 → 3 队）。
        for c in data.get("list") or []:
            g = _avatars_of(c)
            if g:
                groups.append(g)
    elif measure == "hard":
        for c in data.get("hard_list") or []:
            g = _avatars_of(c)
            if g:
                groups.append(g)
    elif measure == "shiyu":
        # 防卫战每层带一队：`hadal_info_v2.fitfh_layer_detail`（第五层，3 队）优先；
        # 没打第五层时退回 `fourth_layer_detail`（第四层，2 队）。最多取 3 队，
        # 凑成「队伍1 / 队伍2 / 队伍3」（用户要的格式；第四层只有 2 队就只画 2 行）。
        info = data.get("hadal_info_v2")
        info = info if isinstance(info, dict) else {}
        for key in ("fitfh", "fourth"):
            layer = info.get(f"{key}_layer_detail")
            layer = layer if isinstance(layer, dict) else {}
            for n in layer.get("layer_challenge_info_list") or []:
                g = _avatars_of(n)
                if g:
                    groups.append(g)
            if groups:
                break
        groups = groups[:3]
    return groups


async def _rank_reply(ctx: Ctx, measure: str) -> dict:
    """三个榜共用的主线：筛人 → 读存档 → 同赛期排序 → 出文本 + 模板变量。"""
    meta = _MEASURES[measure]
    title, noun, kind = meta["title"], meta["noun"], meta["kind"]

    try:
        gid = str(ctx.event.get_group_id() or "") if ctx.event is not None else ""
    except Exception:  # noqa: BLE001
        gid = ""
    if not gid:
        return {"text": "这个命令要在群里发：私聊里没有「同一个群」的概念。"}

    members = await _group_members(ctx, gid)
    if not members:
        return {"text": f"没能拿到本群成员名单，暂时排不了{noun}的榜。"}

    rows: list[dict] = []
    seen_uids: set[str] = set()            # 同一个角色被多人绑定时只出一条
    for qq in members:
        for uid in await _user_uids(qq):
            if uid in seen_uids:
                continue
            periods = record_store.load(kind, uid).get("periods") or []
            if not periods:
                continue                   # 没查过（没存档）→ 不参与
            seen_uids.add(uid)
            p = periods[0]                 # load 已按赛期 key 倒序，[0] 就是最新那期
            rows.append({
                "uid": uid,
                "key": str(p.get("key") or ""),
                "period": str(p.get("period") or ""),
                "score": _score_of(measure, p),
                "name": _nick_of(p),
                # 出图用的队伍（一个角色的所有出战队伍，每队是头像列表）
                "teams": _team_groups(measure, p),
            })

    if not rows:
        return {"text": (
            f"本群还没有人查过{noun}，暂时没有排行。\n"
            f"（先发一次查询命令把成绩存下来，榜上就会有人了）"
        )}

    # 只排最新的那个赛期（不同人查的时间不同，混着排不公平），并丢掉 0 分（本期没打）
    board_key = max(r["key"] for r in rows)
    board = [r for r in rows if r["key"] == board_key and r["score"] > 0]
    if not board:
        return {"text": f"本群{noun}这个赛期还没有有效成绩，暂时没有排行。"}
    board.sort(key=lambda r: (-r["score"], r["name"]))
    board_period = next((r["period"] for r in board if r["period"]), "")
    limit = int(ctx.opt("limit", 10) or 10)
    top = [
        {"index": i, "rank": i, "score": r["score"],
         "name": r["name"] or "未知角色", "uid": r["uid"]}
        for i, r in enumerate(board[:limit], 1)
    ]

    lines = [f"{title} · 群排行" + (f"（{board_period}）" if board_period else "")]
    lines += [f"{it['rank']}. {it['score']} · {it['name']}" for it in top]
    lines.append(f"共 {len(board)} 个角色上榜")
    text = "\n".join(lines)

    vars_ = {
        "title": title,
        "period": board_period,
        "count": len(board),
        "limit": limit,
        "items": top,
    }
    data = {
        "measure": measure,
        "group": gid,
        "period": board_period,
        "members": len(members),
        "board": len(board),
        "protocol": ctx.protocol,
        "top": top,
    }
    # 「输出方式 = 图片」—— 三个榜都支持，**默认图片**（有图就不发文字）。
    # 发图成功后返回 silent，发送端据此不再跟发文字。
    if str(ctx.opt("output", "image")) == "image":
        img_rows = [
            {"rank": i, "score": r["score"], "name": r["name"] or "未知角色",
             "teams": r.get("teams") or []}
            for i, r in enumerate(board[:limit], 1)
        ]
        if measure == "hard":
            # 绝境：维持原「一行一队」布局（排名 · 分数 · 队伍头像 · 角色名）
            single = [{"rank": x["rank"], "score": x["score"], "name": x["name"],
                       "avatars": (x["teams"][0] if x["teams"] else [])}
                      for x in img_rows]
            return await _image_reply(
                ctx,
                lambda: rank_image.render_group_rank(single, title, board_period, len(board)),
                lines, vars_, data,
            )
        # 危局 / 防卫战：新布局（排名 · 角色名 · 分数 + 队伍1/2/3 各三个头像）
        return await _image_reply(
            ctx,
            lambda: rank_image.render_teams_rank(img_rows, title, board_period, len(board)),
            lines, vars_, data,
        )
    return {"text": text, "vars": vars_, "data": data}


@interface(
    "zzz_deadly_rank", "绝区零 · 危局群排行",
    "本群危局强袭战普通难度**角色**排行（一个角色一行；只读本地存档，不实时拉取）。"
    "可选「输出方式 = 图片消息」发一张榜单图（排名 / 角色名 / 分数 / 队伍1-3 各三个头像）",
    options=[_LIMIT_OPTION, _OUTPUT_OPTION],
    tpl_vars=_TPL_VARS,
    sample=(
        "{title} · 群排行（{period}）\n"
        "{#items}{rank}. {score} · {name}\n{/items}"
        "共 {count} 个角色上榜"
    ),
)
async def _api_zzz_deadly_rank(ctx: Ctx) -> dict:
    """危局群排行：本群危局强袭战**普通难度**总分榜（只读存档）。"""
    return await _rank_reply(ctx, "deadly")


@interface(
    "zzz_hard_rank", "绝区零 · 绝境群排行",
    "本群危局强袭战绝境难度**角色**排行（一个角色一行；只读本地存档，不实时拉取）。"
    "可选「输出方式 = 图片消息」发一张榜单图（排名 / 分数 / 出战队伍头像（右上角影画数）/ 角色名）",
    options=[_LIMIT_OPTION, _OUTPUT_OPTION],
    tpl_vars=_TPL_VARS,
    sample=(
        "{title} · 群排行（{period}）\n"
        "{#items}{rank}. {score} · {name}\n{/items}"
        "共 {count} 个角色上榜"
    ),
)
async def _api_zzz_hard_rank(ctx: Ctx) -> dict:
    """绝境群排行：本群危局强袭战**绝境难度**得分榜（只读存档）。"""
    return await _rank_reply(ctx, "hard")


@interface(
    "zzz_shiyu_rank", "绝区零 · 防卫战群排行",
    "本群式舆防卫战**角色**排行（一个角色一行；只读本地存档，不实时拉取）。"
    "可选「输出方式 = 图片消息」发一张榜单图（排名 / 角色名 / 分数 / 队伍1-2 各三个头像）",
    options=[_LIMIT_OPTION, _OUTPUT_OPTION],
    tpl_vars=_TPL_VARS,
    sample=(
        "{title} · 群排行（{period}）\n"
        "{#items}{rank}. {score} · {name}\n{/items}"
        "共 {count} 个角色上榜"
    ),
)
async def _api_zzz_shiyu_rank(ctx: Ctx) -> dict:
    """防卫战群排行：本群式舆防卫战总分榜（只读存档）。"""
    return await _rank_reply(ctx, "shiyu")
