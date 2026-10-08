"""社交命令 · 绝区零实时便笺（体力 / 活跃度 / 刮刮乐 / 录像店 / 委托 / 周常）。

为什么单独成文件
----------------
这块查的是 `/note`（实时便笺），跟「危局 / 防卫战」那种按赛期出成绩的战绩不是一回事：
它反映的是**当前这一刻的状态**（电量还剩多少、周常刷没刷），没有赛期、也不进战绩存档。
所以不放 `record.py`，单独一块，以后要加「体力满了定时推送」也在这儿加。

`zzz/record/api.py::zzz_note` 那个请求函数早就写好了，本文件是它的**第一个使用者**
（之前全仓零调用）。

展示口径
--------
官方给的是一堆枚举字符串（`SaleStateDoing` / `CardSignDone` 之类）和秒数，
直接发出去没人看得懂，所以这里统一转成人话：
「录像店：营业中」「电量 180/240（3 小时 20 分钟后回满）」。
"""

from __future__ import annotations

from typing import Any

from ..core import mys as client
from ..zzz.record import api as record_api
from .base import _duration, _pair, _zzz_nick, _zzz_target, _zzz_title
from .core import Ctx, interface


def _state_text(raw: Any, word: str, yes: str, no: str) -> str:
    """官方状态枚举串 → 中文（枚举里含关键词就算「是」）。

    官方给的是 `SaleStateDoing` / `CardSignDone` 这种，用「是否包含关键词」判断
    比逐个枚举稳妥：版本加了新枚举值也不会显示成未知。
    """
    s = str(raw or "")
    return yes if word.lower() in s.lower() else no


@interface(
    "zzz_note", "绝区零 · 实时便笺",
    "电量 / 活跃度 / 刮刮乐 / 录像店 / 悬赏委托 / 周常任务（可跟 UID 参数）",
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "nick_name", "desc": "游戏内昵称（这个接口不返回，从角色列表取）"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "energy / energy_max", "desc": "当前电量 / 电量上限"},
        {"name": "energy_percent", "desc": "电量百分比（整数，如 75）"},
        {"name": "energy_restore", "desc": "回满还需多少秒（已回满为 0）"},
        {"name": "energy_restore_text", "desc": "回满还需多久（如 3小时20分钟；已回满为空）"},
        {"name": "energy_full", "desc": "电量是否已满（true / false）"},
        {"name": "vitality / vitality_max", "desc": "今日活跃度 / 上限"},
        {"name": "vitality_full", "desc": "活跃度是否已满（true / false）"},
        {"name": "card_sign", "desc": "今日刮刮乐：已刮 / 未刮"},
        {"name": "vhs_sale", "desc": "录像店：营业中 / 未营业"},
        {"name": "bounty_num / bounty_total", "desc": "悬赏委托：已完成 / 总数（没有数据时为 0）"},
        {"name": "bounty_done", "desc": "悬赏委托是否已做满（true / false）"},
        {"name": "weekly_cur / weekly_max", "desc": "周常任务：当前点数 / 上限"},
        {"name": "weekly_done", "desc": "周常是否已做满（true / false）"},
        {"name": "weekly_refresh", "desc": "距周常刷新还剩多少秒（0 = 接口没给）"},
        {"name": "weekly_refresh_text", "desc": "距周常刷新还剩多久（如 2小时25分钟；没给为空）"},
    ],
    sample=(
        "实时便笺 · {nick_name}（{region_name}）\n"
        "电量 {energy}/{energy_max}（{energy_restore_text}后回满）\n"
        "活跃度 {vitality}/{vitality_max}\n"
        "刮刮乐：{card_sign} · 录像店：{vhs_sale}\n"
        "悬赏委托 {bounty_num}/{bounty_total} · 周常 {weekly_cur}/{weekly_max}"
    ),
)
async def _api_zzz_note(ctx: Ctx) -> dict:
    """实时便笺：默认文本 + 模板变量 vars + 官方原始数据 data。"""
    aid, uid, server, err = await _zzz_target(ctx)
    if err:
        return {"text": err}
    try:
        data = await record_api.zzz_note(uid, server, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取实时便笺失败：{exc}"}
    data = data if isinstance(data, dict) else {}
    if not data:
        return {"text": "没有实时便笺数据（该角色可能未开启）"}

    energy = data.get("energy") if isinstance(data.get("energy"), dict) else {}
    prog = energy.get("progress") if isinstance(energy.get("progress"), dict) else {}
    e_cur, e_max = _pair(prog)
    restore = int(energy.get("restore") or 0)
    v_cur, v_max = _pair(data.get("vitality"))
    # 悬赏委托：新版接口给 `s2_bounty_commission`，旧版是 `bounty_commission`，
    # 两个都在（值不同）时以新版为准。
    bounty = data.get("s2_bounty_commission") or data.get("bounty_commission") or {}
    b_num, b_total = _pair(bounty)
    weekly = data.get("weekly_task") if isinstance(data.get("weekly_task"), dict) else {}
    w_cur, w_max = int(weekly.get("cur_point") or 0), int(weekly.get("max_point") or 0)
    # ⚠️ `refresh_time` 是**距下次刷新的剩余秒数**（不是秒级时间戳）。
    # 当时间戳渲染会得到「1970.01.01 10:25」这种鬼东西，所以走 _duration 而不是 _stamp。
    w_refresh = int(weekly.get("refresh_time") or 0)

    card = _state_text(data.get("card_sign"), "done", "已刮", "未刮")
    sale = _state_text((data.get("vhs_sale") or {}).get("sale_state"), "doing", "营业中", "未营业")
    restore_text = _duration(restore)

    # 便笺接口不返回昵称 → 从角色列表取（`_zzz_target` 不带参数时已拉过，命中缓存）
    nick = await _zzz_nick(aid, uid)
    lines = [await _zzz_title(aid, uid, server, "实时便笺", nick)]
    e_line = f"电量：{e_cur}/{e_max}"
    if restore_text:
        e_line += f"（{restore_text}后回满）"
    lines.append(e_line)
    if v_max:
        lines.append(f"活跃度：{v_cur}/{v_max}")
    bits = [f"刮刮乐：{card}" if data.get("card_sign") else "",
            f"录像店：{sale}" if data.get("vhs_sale") else ""]
    bits = [b for b in bits if b]
    if bits:
        lines.append(" · ".join(bits))
    tail = []
    if b_total:
        tail.append(f"悬赏委托 {b_num}/{b_total}")
    if w_max:
        seg = f"周常任务 {w_cur}/{w_max}"
        if w_refresh:
            seg += f"（{_duration(w_refresh)}后刷新）"
        tail.append(seg)
    if tail:
        lines.append(" · ".join(tail))

    vars_ = {
        "uid": uid,
        "region": server,
        "region_name": client.region_name(server),
        "nick_name": nick,
        "energy": e_cur,
        "energy_max": e_max,
        "energy_percent": int(e_cur * 100 / e_max) if e_max else 0,
        "energy_restore": restore,
        "energy_restore_text": restore_text,
        "energy_full": bool(e_max) and e_cur >= e_max,
        "vitality": v_cur,
        "vitality_max": v_max,
        "vitality_full": bool(v_max) and v_cur >= v_max,
        "card_sign": card,
        "vhs_sale": sale,
        "bounty_num": b_num,
        "bounty_total": b_total,
        "bounty_done": bool(b_total) and b_num >= b_total,
        "weekly_cur": w_cur,
        "weekly_max": w_max,
        "weekly_done": bool(w_max) and w_cur >= w_max,
        "weekly_refresh": w_refresh,
        "weekly_refresh_text": _duration(w_refresh),
    }
    return {"text": "\n".join(lines), "vars": vars_, "data": data}
