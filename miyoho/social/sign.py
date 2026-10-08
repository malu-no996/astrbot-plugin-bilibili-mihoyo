"""社交命令 · 绝区零每日签到（luna 活动接口）。

从原 `social_apis.py` 分出来的第七块：接口一多，单文件就奔 300 行去了，
按「功能」拆开、每块自带说明（清单见门面 `social.py`）。

命令行为（就「签到」这一条命令，两个分支）：

    「签到」        → 只签**绑定的默认账号**
    「签到 all」    → 签这个人绑定的**全部账号**

回复文案（用户要求）：

    · 只有**一个角色**时 —— 整条回复就是那一行，**不带标题**：
        `绫华 签到成功（累计12天）`
    · **多个角色**时 —— 标题 + 一行一个（带序号；**不显示 UID**）：
        绝区零签到 · 2 个账号（成功 3）
        1. 绫华/小号 签到成功（累计12天）
        2. 绫华 今日已签到（累计7天）
    （页面模板变量 `items[].text` 就是这一行；自己配了模板则以模板为准，
      想显示 UID 可以在模板里写 `{uid}`。）

两条铁律（用户明确要求，别改成并发）：

1. **一个账号可能绑了多个绝区零角色**（不同 uid / 服务器的号），签到要把
   该账号的**每个角色都签一遍**，不是只签第一个。
2. **所有请求一律串行、一个一个来**，且**相邻两个请求之间隔 ~1 秒（带随机浮动）**。
   串行是**跨账号**的：A 账号的角色全签完，才开始 B 账号，绝不并发。

「一个一个来」的粒度是**米游社 HTTP 请求**：拉角色 1 个、读签到面板 2 个
（info + home）、执行签到 1 个。`_Pacer` 就负责按「上一批实际发了几个请求」
把间隔累计出来（面板那批算 2 个请求的等待），并且间隔带浮动 —— 固定 1.000 秒
的节奏最像脚本，随机一点更像人工。

⚠️ 命令签到这条线用的是 ~1 秒；**「自动签到」（`autosign.py`）用的是页面上配的更大间隔**
（默认 4s ±1.5s、下限 3s），同一份 `_Pacer`、构造时传参即可。`_sign_role` / `_items`
也被 `autosign.py` 复用（逐角色签 + 结果格式化只有这一份实现）。

签到本身是**纯动作**：不落盘、不缓存，每次读米游社当前状态（先读状态再决定
打不打签到接口，避免对「今天已经签过」的角色白发一次 sign 请求）。
"""
from __future__ import annotations

import asyncio
import random
import time

from ..core import bind
from ..core import mys as client
from ..zzz.sign import api as sign_api
from .core import Ctx, interface

# 「全部账号」的写法：`签到 all`（大小写都认），另容忍中文同义词。
_ALL_WORDS = ("all", "全部", "所有", "全部账号", "所有账号")

# 请求节流：相邻两个请求之间 = 基准 × 随机系数（0.7~1.4），即约 0.7~1.4 秒。
# 下限不低于 0.7 秒，是留给米游社活动接口风控的余量；上限别太夸张，
# 否则账号/角色一多，整条命令要等很久。
_SIGN_GAP = 1.0
_SIGN_GAP_FACTOR = (0.7, 1.4)


class _Pacer:
    """米游社请求节流器：相邻请求之间隔 ~1 秒，且**每次长度都不一样**。

    用法是「下一批请求前 tick()」+「刚发完的这批有几个请求就 paid(n)」：

        await pacer.tick()                      # 等够了再发
        roles = await client.bind_roles(aid)    # 1 个请求（默认 paid 就是 1）
        await pacer.tick()
        board = await sign_api.zzz_sign_boards(...)
        pacer.paid(2)                           # 面板内部是 info + home 两个请求

    等待按「请求数 × 单请求间隔」累计，所以一个账号多角色时也不会连成一串。
    首次 tick 不等待（还没发过请求，等一秒纯属浪费）。
    间隔按「上一批请求**开始**的时刻」计算 —— 也就是请求之间 start-to-start 隔 1 秒。

    节流参数可覆盖（三个都给默认值，命令签到那条线不用传）：
    「自动签到」（`autosign.py`）在页面上配了更大的间隔（默认 4s ±1.5s、下限 3s），
    就靠构造时传 `gap` / `factor` / `floor` 实现 —— 节流机制只有这一份，别再抄一遍。
    """

    def __init__(self, gap: float = _SIGN_GAP,
                 factor: tuple[float, float] = _SIGN_GAP_FACTOR,
                 floor: float = 0.0) -> None:
        self._gap = float(gap)                          # 单请求间隔基准（秒）
        self._factor = (float(factor[0]), float(factor[1]))   # 随机系数区间
        self._floor = float(floor)                      # 间隔硬下限（秒），0 = 不限
        self._last = 0.0        # 上一批请求发出的时刻（0 = 还没发过）
        self._cost = 1          # 上一批实际发了几个请求

    async def tick(self) -> None:
        """要发下一批请求了：先把该等的间隔等掉。"""
        if self._last:
            gap = max(self._floor,
                      self._gap * self._cost * random.uniform(*self._factor))
            left = gap - (time.monotonic() - self._last)
            if left > 0:
                await asyncio.sleep(left)
        self._last = time.monotonic()
        self._cost = 1

    def paid(self, n: int) -> None:
        """声明刚发出的那批实际是 n 个请求（读签到面板 = info + home = 2）。"""
        self._cost = max(1, int(n))


def _is_all(arg: str) -> bool:
    """参数是不是「全部账号」。"""
    return (arg or "").strip().lower() in _ALL_WORDS


def _blank(account: str, role: str, uid: str, server: str, multi: bool) -> dict:
    """一条「角色级」结果，字段一次给全（上层只填 status / days）。"""
    role = role or ""
    account = account or ""
    if multi and role and role != account:
        label = f"{account or '未命名账号'}/{role}"      # 多角色：账号/角色，便于区分
    else:
        label = account or role or (f"UID {uid}" if uid else "未命名角色")
    return {
        "account": account, "role": role, "label": label,
        "uid": uid, "server": server, "region": client.region_name(server),
        "days": 0, "status": "", "signed": False, "multi": multi, "text": "",
    }


async def _sign_role(aid: str, account: str, raw_role: dict, pacer: _Pacer, multi: bool) -> dict:
    """签**一个角色**（一个 uid）：读状态 → 没签过才打签到接口。

    pacer 是**全程共用**的那一个：角色之间、账号之间都排在同一条线上。
    """
    role = raw_role if isinstance(raw_role, dict) else {}
    uid = str(role.get("game_uid") or "")
    server = str(role.get("region") or "prod_gf_cn")
    out = _blank(account, str(role.get("nickname") or ""), uid, server, multi)
    if not uid:
        out["status"] = "取不到角色 UID"
        return out

    try:
        await pacer.tick()
        board = await sign_api.zzz_sign_boards(uid, server, account_id=aid)
        pacer.paid(2)                                    # 面板 = info + home
    except Exception as exc:  # noqa: BLE001 —— 单个角色失败不影响其它角色
        out["status"] = f"读取签到状态失败：{exc}"
        return out
    board = board if isinstance(board, dict) else {}
    days = int(board.get("total_sign_day") or 0)
    out["days"] = days
    if board.get("is_sign"):
        out.update(signed=True, status="今日已签到")
        return out

    try:
        await pacer.tick()
        await sign_api.zzz_sign_do(uid, server, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        out["status"] = f"签到失败：{exc}"
        return out
    # 签上了：累计天数 +1（与页面「签完重读状态」的展示一致）
    out.update(signed=True, days=days + 1, status="签到成功")
    return out


async def _sign_account(aid: str, account: str, pacer: _Pacer) -> list[dict]:
    """签**一个账号**：把它绑定的每个绝区零角色依次签掉，返回每个角色一条结果。"""
    try:
        await pacer.tick()
        roles = await client.bind_roles(aid)
    except Exception as exc:  # noqa: BLE001
        out = _blank(account, "", "", "prod_gf_cn", False)
        out["status"] = f"读取角色失败：{exc}"
        return [out]
    if not roles:
        out = _blank(account, "", "", "prod_gf_cn", False)
        out["status"] = "该账号没有绑定绝区零角色"
        return [out]
    multi = len(roles) > 1
    return [await _sign_role(aid, account, r, pacer, multi) for r in roles]


def _line(i: int, res: dict) -> str:
    """一条展示行：`绫华 签到成功（累计12天）`。

    `i=0`（整批只有一个角色时）省掉序号，也不留开头的分隔符 —— 单角色时整条回复
    就是这一行（**不带标题**，用户要求「回复就显示『名字 签到成功（累计N天）』」）。
    多角色时才补序号。

    ⚠️ **不显示 UID**（用户明确要求）：UID 只在 `items[].uid` 里留着，想显示就自己
    配回复模板。同名角色靠「账号/角色」这个展示名区分。
    """
    head = (f"{i}. {res.get('label')}" if i else str(res.get("label") or "")).strip()
    tail = res.get("status") or ""
    days = int(res.get("days") or 0)
    if res.get("signed") and days:
        tail += f"（累计{days}天）"
    return f"{head} {tail}" if head else tail


def _item(i: int, res: dict) -> dict:
    """给回复模板用的单条变量（配了模板就只有这些字段能用）。"""
    return {
        "index": i,
        "account": res.get("account") or "",
        "role": res.get("role") or "",
        "name": res.get("label") or "",
        "uid": res.get("uid") or "",
        "server": res.get("server") or "",
        "region": res.get("region") or "",
        "days": int(res.get("days") or 0),
        "status": res.get("status") or "",
        "signed": bool(res.get("signed")),
        "text": _line(i, res),
    }


def _items(rows: list[dict]) -> list[dict]:
    """整批结果 → 模板变量列表（整批只有一条时不编号）。"""
    if len(rows) == 1:
        return [_item(0, rows[0])]
    return [_item(i, r) for i, r in enumerate(rows, 1)]


@interface(
    "zzz_sign", "绝区零 · 每日签到",
    "签到：直接发「签到」签默认账号；「签到 all」签全部绑定账号（逐角色串行）。"
    "只有一个角色时回复就是「名字 签到成功（累计N天）」这一句",
    tpl_vars=[
        {"name": "title", "desc": "标题（如「绝区零签到 · 绫华」）"},
        {"name": "count", "desc": "账号数量"},
        {"name": "ok", "desc": "签上的角色数（含今日已签到）"},
        {"name": "items[].index", "desc": "序号（整批多个角色时从 1 开始）"},
        {"name": "items[].name", "desc": "展示名（账号名，或「账号/角色」）"},
        {"name": "items[].account", "desc": "账号昵称"},
        {"name": "items[].role", "desc": "角色昵称"},
        {"name": "items[].uid", "desc": "绝区零角色 UID"},
        {"name": "items[].region", "desc": "服务器中文名（如 国服）"},
        {"name": "items[].days", "desc": "累计签到天数（含本次）"},
        {"name": "items[].status", "desc": "状态：签到成功 / 今日已签到 / 失败原因"},
        {"name": "items[].text", "desc": "该角色的整行文案"},
    ],
    sample="{#items}{name} {status}（累计{days}天）\n{/items}",
)
async def _api_zzz_sign(ctx: Ctx) -> dict:
    """签到：默认账号 / 全部账号；每个角色依次签，请求之间隔 ~1 秒。"""
    arg = (ctx.arg or "").strip()
    binds = bind.accounts(ctx.user_id)
    if not binds:
        return "未绑定米游社账号：请先发送「米游社登录」扫码绑定你的米游社账号"

    # ⚠️ 全程只有一个 pacer：所有账号、所有角色排在同一条线上，逐个请求。
    pacer = _Pacer()

    if not _is_all(arg):
        aid = bind.default_account(ctx.user_id)
        if not aid:
            return "取不到默认账号：发「米游社账号」看看绑定列表，或用「米游社切换 账号ID」重设默认"
        name = next((b["nickname"] or b["account_id"] for b in binds if b["account_id"] == aid), aid)
        rows = await _sign_account(aid, name, pacer)
        title = f"绝区零签到 · {name}" + (f"（{len(rows)} 个角色）" if len(rows) > 1 else "")
        ok_n = sum(1 for r in rows if r["signed"])
        if len(rows) == 1:
            # 只签一个角色（最常见）：整条回复就是那一句「名字 签到成功（累计N天）」，
            # 不套标题 —— 用户明确要求（标题在只有一个角色时纯属噪音）。
            text = _line(0, rows[0])
        else:
            # 多角色：一行一个，配上标题说明是哪个账号。
            text = "\n".join([title, *[_line(i, r) for i, r in enumerate(rows, 1)]])
        if not ok_n:
            text += "\n（没签上可能是登录过期，重新发「米游社登录」扫码即可）"
        first = rows[0]
        return {
            "text": text,
            "vars": {
                "title": title, "name": name, "count": 1, "ok": ok_n,
                "uid": first.get("uid") or "", "region": first.get("region") or "",
                "days": int(first.get("days") or 0), "status": first.get("status") or "",
                "items": _items(rows),
            },
            "data": {
                "mode": "default", "title": title,
                "accounts": 1, "roles": len(rows), "ok": ok_n,
                "items": _items(rows),
            },
        }

    rows: list[dict] = []
    for b in binds:
        aid = b["account_id"]
        # 一个账号的角色全部签完，才轮到下一个账号（_sign_account 内部是顺序 await）
        rows += await _sign_account(aid, b["nickname"] or aid, pacer)

    n_acc, n_role = len(binds), len(rows)
    ok_n = sum(1 for r in rows if r["signed"])
    title = (
        f"绝区零签到 · {n_acc} 个账号（成功 {ok_n}）"
        if n_role == n_acc
        else f"绝区零签到 · {n_acc} 个账号 / {n_role} 个角色（成功 {ok_n}）"
    )
    if n_role == 1:
        # 全签下来只有一个角色（常见：只绑了一个号）：也只回那一句，不套标题。
        text = _line(0, rows[0])
    else:
        text = "\n".join([title, *[_line(i, r) for i, r in enumerate(rows, 1)]])
    return {
        "text": text,
        "vars": {
            "title": title, "count": n_acc, "roles": n_role, "ok": ok_n,
            "items": _items(rows),
        },
        "data": {
            "mode": "all", "title": title,
            "accounts": n_acc, "roles": n_role, "ok": ok_n,
            "items": _items(rows),
        },
    }
