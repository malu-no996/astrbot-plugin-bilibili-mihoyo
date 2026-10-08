"""社交命令 · 绝区零绳网月报（本月 / 指定月份的资源收入构成）。

对应 ZZZeroUID 的「绳网月报 / 月历 / 札记」命令：这个月菲林等资源从哪些渠道来、
各占多少。查的是 `zzz/month/api.py`（nap_ledger 域，和签到同源）。

月份怎么定
----------
优先用**命令参数**（「绳网月报 202610」「绳网月报 上月」），没有就用详细设置里的
「默认月份」，再没有就是当月。支持 `202610` / `2026-10` / `2026年10月` 三种写法，
懒得记格式时直接说「上月」。
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..core import mys as client
from ..zzz.month import api as month_api
from .base import _zzz_target, _zzz_title
from .core import Ctx, interface

# 收入渠道的中文名（官方只给 action 英文键）
_ACTION_NAMES = {
    "daily_activity_rewards": "日常活跃奖励",
    "mail_rewards": "邮件奖励",
    "growth_rewards": "成长奖励",
    "event_rewards": "活动奖励",
    "hollow_rewards": "零号空洞奖励",
    "shiyu_rewards": "式舆防卫战奖励",
    "other_rewards": "其他奖励",
}


def _norm_month(raw: str) -> str:
    """把用户写的月份归一成官方要的 `YYYYMM`；认不出来返回空串（= 当月）。

    支持：`202610` / `2026-10` / `2026年10月` / `上月`。
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if s in ("上月", "上个月", "last"):
        t = time.localtime()
        y, m = (t.tm_year, t.tm_mon - 1) if t.tm_mon > 1 else (t.tm_year - 1, 12)
        return f"{y}{m:02d}"
    digits = re.findall(r"\d+", s)
    if not digits:
        return ""
    if len(digits[0]) == 6:                     # 202610
        return digits[0]
    if len(digits) >= 2:                        # 2026-10 / 2026年10月
        y, m = int(digits[0]), int(digits[1])
        if 2000 <= y <= 2999 and 1 <= m <= 12:
            return f"{y}{m:02d}"
    return ""


def _pretty_month(raw: str) -> str:
    """`202610` → `2026-10`（官方数据里的月份都长这样）。"""
    s = str(raw or "")
    return f"{s[:4]}-{s[4:6]}" if len(s) == 6 else s


def _sub(d: Any, key: str) -> dict:
    v = (d or {}).get(key) if isinstance(d, dict) else None
    return v if isinstance(v, dict) else {}


@interface(
    "zzz_month", "绝区零 · 绳网月报",
    "本月（或指定月份）菲林等资源的收入构成；命令后加月份可查历史，如「绳网月报 202610」「绳网月报 上月」",
    options=[
        {
            "key": "month", "label": "默认查哪个月", "type": "text", "default": "",
            "hint": "留空 = 当月；填 202610 这种六位数字就是固定查那个月"
                    "（命令里带了月份时以命令为准）",
        },
    ],
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "nick_name", "desc": "游戏内昵称"},
        {"name": "month", "desc": "这次查到的月份（2026-10）"},
        {"name": "month_raw", "desc": "月份原值（202610）"},
        {"name": "current_month", "desc": "官方认为的「当前月」"},
        {"name": "count", "desc": "收入明细条数"},
        {"name": "items", "desc": "收入明细，每条含 index / name（资源名）/ type（官方 data_type）/ "
                                  "count（数量）/ text（整行）"},
        {"name": "income_count / incomes", "desc": "渠道条数与列表，每条含 index / name（渠道中文名）/ "
                                                   "action（官方键）/ num（数量）/ percent（百分比整数）"},
        {"name": "optional_months", "desc": "可查的月份列表（YYYYMM 字符串数组）"},
    ],
    sample=(
        "绳网月报 · {month} · {nick_name}\n"
        "{#items}{name} {count}\n{/items}"
        "{#incomes}{name} {num}（{percent}%）\n{/incomes}"
    ),
)
async def _api_zzz_month(ctx: Ctx) -> dict:
    """绳网月报：默认文本 + 模板变量 vars + 官方原始数据 data。"""
    aid, uid, server, err = await _zzz_target(ctx)
    if err:
        return {"text": err}
    month = _norm_month(ctx.arg) or _norm_month(str(ctx.opt("month", "") or ""))
    try:
        data = await month_api.month_info(uid, server, month=month, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取绳网月报失败：{exc}"}
    data = data if isinstance(data, dict) else {}

    md = _sub(data, "month_data")
    items = []
    for i, it in enumerate(md.get("list") or [], 1):
        it = it if isinstance(it, dict) else {}
        name = str(it.get("data_name") or "")
        cnt = int(it.get("count") or 0)
        items.append({"index": i, "name": name, "type": str(it.get("data_type") or ""),
                      "count": cnt, "text": f"{name} {cnt}"})
    incomes = []
    for i, c in enumerate(md.get("income_components") or [], 1):
        c = c if isinstance(c, dict) else {}
        action = str(c.get("action") or "")
        num = int(c.get("num") or 0)
        pct = int(c.get("percent") or 0)
        incomes.append({
            "index": i,
            "action": action,
            "name": _ACTION_NAMES.get(action, action),
            "num": num,
            "percent": pct,
            "text": f"{_ACTION_NAMES.get(action, action)} {num}（{pct}%）",
        })

    role = _sub(data, "role_info")
    nick = str(role.get("nickname") or "")
    shown = _pretty_month(data.get("data_month"))
    lines = [await _zzz_title(aid, uid, server, f"绳网月报 · {shown or '本月'}", nick)]
    if items:
        lines.append("收入明细：" + " · ".join(it["text"] for it in items))
    if incomes:
        lines += ["渠道构成："] + [f"· {c['text']}" for c in incomes]
    if not (items or incomes):
        lines.append("（这个月没有可统计的收入）")

    vars_ = {
        "uid": uid,
        "region": server,
        "region_name": client.region_name(server),
        "nick_name": nick,
        "month": shown,
        "month_raw": str(data.get("data_month") or ""),
        "current_month": str(data.get("current_month") or ""),
        "count": len(items),
        "items": items,
        "income_count": len(incomes),
        "incomes": incomes,
        "optional_months": [str(m) for m in (data.get("optional_month") or [])],
    }
    return {"text": "\n".join(lines), "vars": vars_, "data": data}
