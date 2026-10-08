"""社交命令 · 绝区零「探索玩法」战绩：零号空洞 / 迷宫诡域 / 临界推演。

为什么单独成文件
----------------
这三个和 `record.py` 里的危局 / 防卫战不是一类东西：

  · 危局 / 防卫战 —— **按赛期出成绩**，有本期 / 上期之分，要进战绩存档；
  · 零号空洞 / 迷宫诡域 —— 常驻探索的**累计进度**（等级、点数、收集度、解锁状态），
    没有「本期结束」的概念；
  · 临界推演 —— 有期数（`void_front_id`）但按关卡给分，结构更像「多张关卡卡」。

所以拆出来单独一块，彼此的字段转换互不干扰。
官方字段 → 人话的通用工具（_pt / _pct_plus / _avatars / _bangboo_name / _pair）在
`base.py`，与 record.py 共用同一套口径。
"""

from __future__ import annotations

import time

from typing import Any

from ..core import mys as client
from ..zzz.record import api as record_api
from .base import (_avatars, _bangboo_name, _pair, _pct_plus, _pt,
                    _stamp, _zzz_nick, _zzz_target, _zzz_title)
from .core import Ctx, interface

# 零号空洞「数据收集」的类别名（官方只给 type 数字，网页端也是这么映射的）
_ABYSS_COLLECT = {
    1: "鸣徽图鉴",
    2: "特殊区域记录",
    3: "哨站课题",
    4: "侵蚀研究",
    5: "旧都失物",
}


def _sub(d: Any, key: str) -> dict:
    """取一个子结构；不是 dict 就给空 dict（后面所有取值都靠它兜底，绝不抛）。"""
    v = (d or {}).get(key) if isinstance(d, dict) else None
    return v if isinstance(v, dict) else {}


def _flag(d: Any, *keys: str) -> bool:
    """取解锁标记：官方字段名在不同版本里换过（is_nest / is_throne / is_unlock…），
    这里按顺序找第一个 bool 值。"""
    d = d if isinstance(d, dict) else {}
    for k in keys:
        if isinstance(d.get(k), bool):
            return d[k]
    return False


def _text_of(d: Any, *keys: str) -> str:
    """按顺序取第一个非空字符串字段（不同版本键名不同时的兜底读取）。"""
    d = d if isinstance(d, dict) else {}
    for k in keys:
        v = str(d.get(k) or "").strip()
        if v:
            return v
    return ""


@interface(
    "zzz_abyss", "绝区零 · 零号空洞",
    "调查等级 / 鸣徽等级 / 调查点数 / 数据收集 / 阶段解锁（可跟 UID 参数）",
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "nick_name", "desc": "游戏内昵称（这个接口不返回，从角色列表取）"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "level / level_max", "desc": "调查等级 / 上限"},
        {"name": "talent / talent_max", "desc": "鸣徽等级 / 上限"},
        {"name": "point / point_max", "desc": "调查点数 / 上限"},
        {"name": "duty / duty_max", "desc": "悬赏委托：已完成 / 总数"},
        {"name": "unlock", "desc": "零号空洞是否已解锁（true / false）"},
        {"name": "nest / throne", "desc": "枯败花圃 / 刀耕火焚 是否解锁（已解锁 / 未解锁）"},
        {"name": "refresh_text", "desc": "数据刷新时刻（如 2026.10.06 04:00）"},
        {"name": "items", "desc": "数据收集清单，每条含 index / name（类别名）/ "
                                  "type（官方类别号）/ cur / max / text（整行）"},
    ],
    sample=(
        "零号空洞 · {nick_name}（{region_name}）\n"
        "调查等级 {level}/{level_max} · 鸣徽等级 {talent}/{talent_max}\n"
        "{#items}{name} {cur}/{max}\n{/items}"
    ),
)
async def _api_zzz_abyss(ctx: Ctx) -> dict:
    """零号空洞：常驻探索的累计进度。

    ⚠️ 这个接口（abyss_abstract）的参数是 `role_id` + `server`，不是 uid + region
    （传错会直接 -400005），封装在 `zzz/record/api.py::abyss_abstract` 里，这里不碰。
    """
    aid, uid, server, err = await _zzz_target(ctx)
    if err:
        return {"text": err}
    try:
        data = await record_api.abyss_abstract(uid, server, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取零号空洞失败：{exc}"}
    data = data if isinstance(data, dict) else {}
    if not data:
        return {"text": "没有零号空洞数据（该角色可能还没解锁零号空洞）"}

    lvl, lvl_max = _pair(_sub(data, "abyss_level"), ("cur_level", "cur"), ("max_level", "max"))
    tal, tal_max = _pair(_sub(data, "abyss_talent"), ("cur_talent", "cur"), ("max_talent", "max"))
    pt, pt_max = _pair(_sub(data, "abyss_point"), ("cur_point", "cur"), ("max_point", "max"))
    duty, duty_max = _pair(_sub(data, "abyss_duty"), ("cur_duty", "cur"), ("max_duty", "max"))

    items = []
    for i, c in enumerate(data.get("abyss_collect") or [], 1):
        c = c if isinstance(c, dict) else {}
        cur, mx = int(c.get("cur_collect") or 0), int(c.get("max_collect") or 0)
        name = _ABYSS_COLLECT.get(int(c.get("type") or 0), f"收集 {c.get('type')}")
        items.append({"index": i, "type": int(c.get("type") or 0), "name": name,
                      "cur": cur, "max": mx, "text": f"{name} {cur}/{mx}"})

    nest = _flag(_sub(data, "abyss_nest"), "is_nest", "is_unlock")
    throne = _flag(_sub(data, "abyss_throne"), "is_throne", "is_unlock")

    # 这个接口不返回昵称 → 从角色列表取（`_zzz_target` 不带参数时已拉过，命中缓存）
    nick = await _zzz_nick(aid, uid)
    lines = [await _zzz_title(aid, uid, server, "零号空洞", nick)]
    if lvl_max:
        lines.append(f"调查等级：{lvl}/{lvl_max}")
    if tal_max:
        lines.append(f"鸣徽等级：{tal}/{tal_max}")
    mid = []
    if pt_max:
        mid.append(f"调查点数 {pt}/{pt_max}")
    if duty_max:
        mid.append(f"悬赏委托 {duty}/{duty_max}")
    if mid:
        lines.append(" · ".join(mid))
    if items:
        lines.append("数据收集：" + " · ".join(it["text"] for it in items))
    lines.append(f"枯败花圃 {'已解锁' if nest else '未解锁'}"
                 f" · 刀耕火焚 {'已解锁' if throne else '未解锁'}")

    vars_ = {
        "uid": uid,
        "region": server,
        "region_name": client.region_name(server),
        "nick_name": nick,
        "level": lvl, "level_max": lvl_max,
        "talent": tal, "talent_max": tal_max,
        "point": pt, "point_max": pt_max,
        "duty": duty, "duty_max": duty_max,
        "unlock": bool(data.get("unlock", True)),
        "nest": "已解锁" if nest else "未解锁",
        "throne": "已解锁" if throne else "未解锁",
        "refresh": int(data.get("refresh_time") or 0),
        "refresh_text": _stamp(data.get("refresh_time")),
        "count": len(items),
        "items": items,
    }
    return {"text": "\n".join(lines), "vars": vars_, "data": data}


@interface(
    "zzz_zenkov", "绝区零 · 迷宫诡域",
    "赛季等级 / 赛季币 / 幻境地图撤离度 / 收藏进度（可跟 UID 参数）",
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "nick_name", "desc": "游戏内昵称"},
        {"name": "season_level", "desc": "赛季等级"},
        {"name": "coin / coin_max", "desc": "赛季币：当前 / 上限（取不到为 0）"},
        {"name": "quest / quest_max", "desc": "赛季任务：当前 / 总数"},
        {"name": "duty / duty_max", "desc": "本期委托：已完成 / 总数"},
        {"name": "medal / medal_max", "desc": "奖章收藏：已获得 / 总数"},
        {"name": "goods / goods_max", "desc": "藏品收藏：已获得 / 总数"},
        {"name": "evacuations", "desc": "累计撤离次数（官方 big_red_num / millions_evacuations）"},
        {"name": "refresh_text", "desc": "赛季刷新时刻"},
        {"name": "items", "desc": "幻境地图，每条含 index / name / name_align（短名插空格对齐） / "
                                  "leave（撤离度 %，两位小数）/ "
                                  "max_price（最高收益）/ hell（地狱·硬核是否解锁，true/false）/ "
                                  "challenged（是否挑战过）/ text（整行）"},
    ],
    sample=(
        "迷宫诡域 · {nick_name}（{region_name}）\n"
        "赛季等级 {season_level} · 赛季币 {coin}\n"
        "{#items}{name_align}：撤离 {leave}%\n{/items}"
    ),
)
async def _api_zzz_zenkov(ctx: Ctx) -> dict:
    """迷宫诡域（zenkov_abstract_info）：赛季进度 + 幻境地图 + 收藏。

    这个接口的收藏（collection_data.medal_data / goods_data）字段在不同版本里长得
    不一样，所以统一走 `_pair` 兼容取值，取不到就是 0 —— 不影响其它字段。
    """
    aid, uid, server, err = await _zzz_target(ctx)
    if err:
        return {"text": err}
    try:
        data = await record_api.zenkov_abstract(uid, server, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取迷宫诡域失败：{exc}"}
    data = data if isinstance(data, dict) else {}
    if not data:
        return {"text": "没有迷宫诡域数据（该角色可能还没解锁迷宫诡域）"}

    season = _sub(data, "season_data")
    coin, coin_max = _pair(_sub(season, "season_coin"))
    quest, quest_max = _pair(_sub(season, "season_quest"))
    duty, duty_max = _pair(_sub(data, "abyss_duty"))
    medal, medal_max = _pair(_sub(_sub(data, "collection_data"), "medal_data"))
    goods, goods_max = _pair(_sub(_sub(data, "collection_data"), "goods_data"))

    items = []
    for i, m in enumerate(data.get("map_list") or [], 1):
        m = m if isinstance(m, dict) else {}
        name = str(m.get("map_name") or f"地图 {m.get('map_id') or i}")
        # 短名插空格对齐 5 字名（「城郊幻境」→「城 郊 幻 境」），5 字名（雅努斯幻境）不动
        disp = " ".join(name) if 1 < len(name) < 5 else name
        leave = round(int(m.get("leave_percent") or 0) / 100, 2)  # 官方 7778 → 77.78%
        items.append({
            "index": i,
            "name": name,
            "name_align": disp,
            "map_id": int(m.get("map_id") or 0),
            "leave": leave,
            "max_price": str(m.get("max_price") or ""),
            "hell": bool(m.get("hell_unlock")),
            "challenged": bool(m.get("is_challenge")),
            "text": f"{disp}：撤离 {leave:.2f}%" + (f" · 最高收益 {m['max_price']}"
                                                   if m.get("max_price") else ""),
        })

    lines = [await _zzz_title(aid, uid, server, "迷宫诡域", data.get("nick_name"))]
    head = []
    if season.get("season_level"):
        head.append(f"赛季等级：{season['season_level']}")
    if coin or coin_max:
        head.append(f"赛季币：{coin}" + (f"/{coin_max}" if coin_max else ""))
    if quest_max:
        head.append(f"赛季任务：{quest}/{quest_max}")
    if duty_max:
        head.append(f"本期委托：{duty}/{duty_max}")
    if head:
        lines.append(" · ".join(head))
    if medal_max or goods_max:
        lines.append("收藏：" + " · ".join(
            [f"奖章 {medal}/{medal_max}" if medal_max else "",
             f"藏品 {goods}/{goods_max}" if goods_max else ""]
        ).strip(" · "))
    if items:
        lines.append("幻境地图：")
        lines.extend("· " + it["text"] for it in items)

    vars_ = {
        "uid": uid,
        "region": server,
        "region_name": client.region_name(server),
        "nick_name": str(data.get("nick_name") or ""),
        "season_level": int(season.get("season_level") or 0),
        "coin": coin, "coin_max": coin_max,
        "quest": quest, "quest_max": quest_max,
        "duty": duty, "duty_max": duty_max,
        "medal": medal, "medal_max": medal_max,
        "goods": goods, "goods_max": goods_max,
        "evacuations": _text_of(data, "millions_evacuations", "big_red_num"),
        "refresh": int(season.get("refresh_time") or data.get("refresh_time") or 0),
        "refresh_text": _stamp(season.get("refresh_time") or data.get("refresh_time")),
        "count": len(items),
        "items": items,
    }
    return {"text": "\n".join(lines), "vars": vars_, "data": data}


@interface(
    "zzz_void", "绝区零 · 临界推演",
    "本期总分 / 排名 / BOSS 关与各关卡成绩（可跟 UID 参数）",
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "nick_name", "desc": "游戏内昵称"},
        {"name": "total_score", "desc": "本期总分"},
        {"name": "max_score", "desc": "本期满分"},
        {"name": "rank", "desc": "排名百分比（如 12.34%+）"},
        {"name": "rank_percent", "desc": "排名原值（官方口径 = 百分比 ×100）"},
        {"name": "end_text / left_text", "desc": "本期结束时刻 / 剩余时间（文本）"},
        {"name": "boss_name", "desc": "BOSS 关的名字（没有 BOSS 记录时为空）"},
        {"name": "boss_score / boss_star", "desc": "BOSS 关得分 / 星级"},
        {"name": "count", "desc": "关卡条数（不含 BOSS 关）"},
        {"name": "items", "desc": "关卡记录，每条含 index / name / score / max_score / star / "
                                  "ratio（得分比，官方给的字符串）/ time / "
                                  "avatar_text（出战代理人顿号串）/ avatars（列表）/ "
                                  "buddy（邦布名）/ text（整行）"},
    ],
    sample=(
        "临界推演 · {nick_name}（{region_name}）\n"
        "总分 {total_score} · 前 {rank}\n"
        "{#items}{name} · {score} 分 · {star}★\n{/items}"
    ),
)
async def _api_zzz_void(ctx: Ctx) -> dict:
    """临界推演：本期总览 + BOSS 关 + 普通关。

    ⚠️ 期号 `void_front_id` **不再写死** —— 老接口写死 102 已下线（HTTP 404）。
    `record_api.void_front_battle` 会先查摘要拿本期 id 再取明细。
    """
    aid, uid, server, err = await _zzz_target(ctx)
    if err:
        return {"text": err}
    try:
        data = await record_api.void_front_battle(uid, server, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取临界推演失败：{exc}"}
    data = data if isinstance(data, dict) else {}

    brief = _sub(data, "void_front_battle_abstract_info_brief")
    role = _sub(data, "role_basic_info")
    items = [_void_row(r, i) for i, r in enumerate(data.get("main_challenge_record_list") or [], 1)]
    boss_rec = _sub(data, "boss_challenge_record")
    boss_row = _void_row(_sub(boss_rec, "main_challenge_record"), 0,
                         name=str(_sub(boss_rec, "boss_info").get("name") or ""))

    if not (items or boss_row["score"] or brief.get("total_score")):
        return {"text": "没有临界推演数据（本期可能未参与）"}

    nick = str(role.get("nickname") or "")
    end_ts = int(brief.get("end_ts") or 0)
    left_ts = int(brief.get("left_ts") or 0)
    if not left_ts and end_ts and not brief.get("end_ts_over_42_days"):
        # 新摘要不一定给 left_ts（只给 end_ts），自己算一次剩余秒数；
        # `end_ts_over_42_days` 为真 = 超长/常驻赛期，不给倒计时。
        left_ts = max(0, end_ts - int(time.time()))
    pct = brief.get("rank_percent")
    lines = [await _zzz_title(aid, uid, server, "临界推演", nick)]
    head = [f"总分：{int(brief.get('total_score') or 0)}"]
    if brief.get("max_score"):
        head.append(f"满分：{int(brief['max_score'])}")
    if pct:
        head.append(f"排名：前 {float(pct) / 100:.2f}%")
    lines.append(" · ".join(head))
    if boss_row["name"]:
        lines.append(f"BOSS {boss_row['name']} · {boss_row['score']} 分 · {boss_row['star']}★")
    lines += [it["text"] for it in items]

    vars_ = {
        "uid": uid,
        "region": server,
        "region_name": client.region_name(server),
        "nick_name": nick,
        "total_score": int(brief.get("total_score") or 0),
        "max_score": int(brief.get("max_score") or 0),
        "rank_percent": int(pct or 0),
        "rank": _pct_plus(pct),
        "end": end_ts,
        "left": left_ts,
        "end_text": _stamp(end_ts),
        "left_text": _left_text(left_ts),
        "boss_name": boss_row["name"],
        "boss_score": boss_row["score"],
        "boss_star": boss_row["star"],
        "count": len(items),
        "items": items,
    }
    return {"text": "\n".join(lines), "vars": vars_, "data": data}


def _void_row(rec: dict, index: int, name: str = "") -> dict:
    """临界推演的一条关卡记录 → 模板条目。

    BOSS 关与普通关是同一套结构（都在 `main_challenge_record` 下），区别只是
    BOSS 关的名字要从外面的 `boss_info.name` 取，所以这里留了 name 参数。
    """
    rec = rec if isinstance(rec, dict) else {}
    buddy = _sub(rec, "buddy")
    avatars, ava_text = _avatars(rec.get("avatar_list"))
    score = int(rec.get("score") or 0)
    star = str(rec.get("star") or "")
    name = str(name or rec.get("name") or "")
    return {
        "index": index,
        "name": name,
        "score": score,
        "max_score": int(rec.get("max_score") or 0),
        "star": star,
        "ratio": str(rec.get("score_ratio") or ""),
        "time": _pt(rec.get("challenge_time")),
        "avatars": avatars,
        "avatar_text": ava_text,
        "buddy": str(buddy.get("name") or _bangboo_name(buddy.get("id")) or ""),
        "text": f"{name} · {score} 分" + (f" · {star}★" if star else ""),
    }


def _left_text(seconds: Any) -> str:
    """剩余秒数 → '还剩 3 天 4 小时'（0 / 非数字 → 空串）。

    与 base.py 的 _duration（「3小时20分钟」）不是一个口径：这个用于「本期还剩多久」，
    以天为单位更直观。
    """
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    d, rem = divmod(total, 86400)
    h = rem // 3600
    if d and h:
        return f"还剩 {d} 天 {h} 小时"
    return f"还剩 {d} 天" if d else f"还剩 {h} 小时"
