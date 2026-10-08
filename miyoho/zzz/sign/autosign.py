"""绝区零 · 自动签到（页面勾选目标 + 每天定时 + 页面/命令手动触发）。

解决什么问题
------------
「签到」命令（`social_sign.py`）只能签**发命令那个人自己**绑定的账号，且必须有人主动发消息。
这里把「要签哪些角色」做成一份**机器人级别的配置**（页面上勾选，可横跨多个米游社账号），
于是有了两种触发方式：

  · **定时**（默认）—— 每天在设定时刻（带浮动）自动把勾选的角色全签一遍；
  · **手动** —— 页面上点「一键签到」，或在 QQ 里发「一键签到」命令（接口在 `social_autosign.py`，
    谁能触发由那条命令「详细设置 → 允许触发的人」决定）。

三条铁律（沿用 `social_sign`，别改成并发）
------------------------------------------
1. **所有请求串行**：一个角色的面板读完才签它，签完才轮到下一个角色；
2. **相邻请求 start-to-start 间隔 ≥ `GAP_FLOOR`（3 秒）且带浮动** —— 这是用户对这个功能的
   明确要求（命令签到那条线是 ~1 秒，别混用）；
3. 一个账号有多个角色时逐个签，**不并发**。

为什么定时要带浮动、而且必须落盘
--------------------------------
每天 08:30:00 整点发一串请求最像脚本。所以把「触发时刻」在 ±jitter 分钟内随机取一个点，
并且**每天只算一次、算完立刻落盘**（`next_run_at`）—— 后台循环每 30 秒只做
「到点没有」这一个判断。若每次 tick 重新随机，那个时刻会一直往后跳，永远等不到。
同理，「今天跑过没有」用 `last_run_date` 记住：跑过就跳过今天的窗口，直接排明天。

通知（可选）
------------
签到结果可以私聊推给指定 QQ（配置里的 `notify` 字段）。**只认 OneBot 实例** ——
用该实例的 `send_private_msg` 发，所以那些 QQ 得是机器人的好友；QQ 官方机器人
没法按 QQ 号主动私聊。`when=auto` 只推「定时」那一轮，`always` 连页面 / QQ 命令
触发的那轮也推。通知失败只记日志，**绝不**影响签到结果。

落盘
----
data/zzz/autosign.json —— 与抽卡记录 / 战绩快照 / 社交命令配置同属**用户数据**，
放项目根 data/ 下而不是插件里（插件可被市场卸载重装，配置不能跟着走）。
写法与 record_store / social_cfg 一致：tmp + os.replace 原子替换。

入口
----
`start()` 由插件根 `__init__.py` 在 on_startup 里调一次（起后台循环）；
路由在 `autosign_routes.py`，QQ 命令在 `social_autosign.py`，三者都只读/写本模块。
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from ... import send as _send_shim
from ...core import store
from ...core import mys as client
from ...social.sign import _Pacer, _items, _sign_role


from ...paths import zzz_path

_FILE = zzz_path("autosign.json")

# 相邻请求间隔的硬下限（秒）。用户要求「每个请求间隔超过 3 秒」——
# 页面上填的基准值再小也会被抬到这个数，别删。
GAP_FLOOR = 3.0
DEFAULT_GAP = 4.0
DEFAULT_GAP_JITTER = 1.5
DEFAULT_TIME = "08:30"
DEFAULT_JITTER = 10

# 后台循环节奏：每 30 秒看一眼「到点没有」。定时精度本来就是分钟级，够了。
_TICK = 30.0
# 启动后首次检查前的延迟：错开启动高峰（与战绩存档循环同样的思路）
_FIRST_DELAY = 45.0

# 目标的字段（勾选时由页面写进来，够重放一次签到即可）
TARGET_KEYS = ("account_id", "account", "uid", "server", "role")

MODE_AUTO = "auto"
MODE_MANUAL = "manual"

# 通知的两种时机：auto = 只在定时那一轮发；always = 每次执行（含页面 / QQ 命令）都发。
NOTIFY_AUTO = "auto"
NOTIFY_ALWAYS = "always"
NOTIFY_WHEN = (NOTIFY_AUTO, NOTIFY_ALWAYS)

_lock = threading.RLock()
_cfg: dict | None = None

# 执行锁：定时与手动（页面 / 命令）同时来的时候只跑一份
_run_lock = asyncio.Lock()
# 「已经排上但协程还没被调度」的空窗标记 —— 见 is_running() 的说明
_pending = False


# ================= 配置读写 =================


def _clamp_num(raw: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if v != v:                                          # NaN
        return float(default)
    return max(lo, min(hi, v))


def _parse_hhmm(text: Any) -> tuple[int, int]:
    """`"08:30"` / `"8:5"` → (8, 30)；解析不了给默认 08:30。"""
    try:
        hh, _, mm = str(text or "").partition(":")
        h, m = int(hh), int(mm)
    except (TypeError, ValueError):
        return 8, 30
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return 8, 30
    return h, m


def _clean_targets(raw: Any) -> list[dict]:
    """目标列表清洗：只留认得的字段、按 (账号, 角色) 去重、按原顺序排。"""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict):
            continue
        aid, uid = str(t.get("account_id") or ""), str(t.get("uid") or "")
        if not aid or not uid:
            continue
        key = (aid, uid)
        if key in seen:
            continue
        seen.add(key)
        row = {k: str(t.get(k) or "") for k in TARGET_KEYS}
        row["server"] = row["server"] or "prod_gf_cn"
        out.append(row)
    return out


def _digits(raw: Any, limit: int = 12) -> str:
    """只留数字、掐长度 —— QQ 号 / 实例号统一走这里清洗（页面上常粘贴出脏字符）。"""
    return "".join(ch for ch in str(raw or "") if ch.isdigit())[:limit]


def _clean_notify(raw: Any) -> dict:
    """通知配置清洗：接收者 QQ 只留数字并去重；`when` 只认 auto / always。

    `self_id` 是「用哪个机器人发」——OneBot 实例的 QQ 号（页面下拉选）。
    """
    raw = raw if isinstance(raw, dict) else {}
    targets: list[str] = []
    for t in raw.get("targets") if isinstance(raw.get("targets"), list) else []:
        s = _digits(t)
        if s and s not in targets:
            targets.append(s)
    when = str(raw.get("when") or NOTIFY_AUTO)
    return {
        "enabled": bool(raw.get("enabled", False)),
        "self_id": _digits(raw.get("self_id")),
        "targets": targets,
        "when": when if when in NOTIFY_WHEN else NOTIFY_AUTO,
    }


def _normalize(raw: Any) -> dict:
    """把磁盘上（可能是旧版 / 手改坏的）内容整成完整可用的配置。"""
    raw = raw if isinstance(raw, dict) else {}
    h, m = _parse_hhmm(raw.get("time"))
    targets = _clean_targets(raw.get("targets"))
    last = raw.get("last_result")
    return {
        "version": 1,
        "enabled": bool(raw.get("enabled", True)),
        "mode": MODE_MANUAL if str(raw.get("mode") or MODE_AUTO) == MODE_MANUAL else MODE_AUTO,
        "time": f"{h:02d}:{m:02d}",
        "jitter": int(_clamp_num(raw.get("jitter"), 0, 120, DEFAULT_JITTER)),
        # 间隔下限就是 GAP_FLOOR（3 秒）：填 0 也会被抬到 3，满足「超过 3 秒」
        "gap": _clamp_num(raw.get("gap"), GAP_FLOOR, 120.0, DEFAULT_GAP),
        "gap_jitter": _clamp_num(raw.get("gap_jitter"), 0.0, 60.0, DEFAULT_GAP_JITTER),
        "targets": targets,
        "notify": _clean_notify(raw.get("notify")),
        "next_run_at": int(_clamp_num(raw.get("next_run_at"), 0, 4e9, 0)),
        "last_run_at": int(_clamp_num(raw.get("last_run_at"), 0, 4e9, 0)),
        "last_run_date": str(raw.get("last_run_date") or ""),
        "last_source": str(raw.get("last_source") or ""),
        "last_result": last if isinstance(last, dict) else {},
    }


def _load_from_disk() -> dict:
    try:
        return _normalize(json.loads(_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return _normalize({})
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"miyoushe 自动签到配置读取失败，改用默认值：{exc}")
        return _normalize({})


def load() -> dict:
    """读配置（内存缓存；首次读盘）。后台循环每轮都读，别每次读盘。"""
    global _cfg
    with _lock:
        if _cfg is None:
            _cfg = _load_from_disk()
        return _cfg


def _write(cfg: dict) -> None:
    """原子落盘（tmp + os.replace）：写坏一半不会毁掉旧配置。"""
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _FILE.with_suffix(_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _FILE)


def _commit(cfg: dict) -> dict:
    global _cfg
    with _lock:
        _cfg = cfg
        try:
            _write(cfg)
        except OSError as exc:
            logger.warning(f"miyoushe 自动签到配置保存失败（内存已生效）：{exc}")
        return _cfg


# ================= 下一次触发时刻 =================


def _date_str(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _ts_label(ts: Any) -> str:
    try:
        ts = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""


def compute_next_run(cfg: dict, now: float | None = None) -> int:
    """算下一次该跑的时刻（epoch 秒）；返回 0 = 不需要定时（关着 / 仅手动）。

    规则（每一条都是有原因的）：

    · 触发时刻 = 当天的 HH:MM **± jitter 分钟**里随机取一个点 —— 所以每天都不一样；
    · **今天已经跑过**（`last_run_date` 是今天）→ 今天的窗口作废，直接排明天；
    · 今天的窗口**整个过去了**（现在已过 HH:MM+jitter）→ 排明天；
    · 现在**正落在窗口里**（例：08:35 打开页面点了保存）→ 几秒后就跑，别把今天漏掉；
    · 否则 → 窗口内随机取点。

    随机值在**算这一下**的时候定死、由调用方落盘 —— 见模块说明。
    """
    now = time.time() if now is None else float(now)
    if not cfg.get("enabled") or str(cfg.get("mode") or MODE_AUTO) != MODE_AUTO:
        return 0
    h, m = _parse_hhmm(cfg.get("time"))
    jit = int(_clamp_num(cfg.get("jitter"), 0, 120, DEFAULT_JITTER)) * 60
    ran_today = str(cfg.get("last_run_date") or "") == _date_str(now)
    lt = time.localtime(now)
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
    for day in (0, 1, 2):
        if day == 0 and ran_today:
            continue                                    # 今天签过了 → 看明天
        t = base + day * 86400
        lo, hi = t - jit, t + jit
        if hi <= now:
            continue                                    # 这天的窗口整个过去了
        if now <= lo:
            return int(lo + random.uniform(0, max(1.0, hi - lo)))   # 窗口内随机一点
        return int(now + random.uniform(2, 10))         # 已在窗口里 → 稍后就跑
    return int(base + 86400)                            # 兜底：明天同一时刻


def _reschedule(cfg: dict) -> dict:
    """按当前配置重算 `next_run_at` 并返回（不落盘，交给 _commit）。"""
    cfg["next_run_at"] = compute_next_run(cfg)
    return cfg


def save(patch: dict | None = None) -> dict:
    """保存配置（只覆盖传进来的字段），并**立刻重排下一次触发时刻**。

    页面「保存」走这里；`enabled` / `mode` / `time` / `jitter` 任一改动都会让
    `next_run_at` 重新随机 —— 这正是用户要的「定时时间带浮动」。
    """
    with _lock:
        merged = dict(load())
        for k, v in (patch or {}).items():
            if k in merged:
                merged[k] = v
        return _commit(_reschedule(_normalize(merged)))


def _record_run(items: list[dict], source: str, now: float | None = None) -> dict:
    """跑完（或跳过）之后写一笔：上次时刻 / 日期 / 结果，并排下一次。"""
    now = time.time() if now is None else float(now)
    with _lock:
        cfg = dict(load())
        rows = _items(items) if items else []
        ok_n = sum(1 for r in items if r.get("signed"))
        summary = _summary_text(rows, len(items), ok_n)
        cfg["last_run_at"] = int(now)
        cfg["last_run_date"] = _date_str(now)
        cfg["last_source"] = str(source or "manual")
        cfg["last_result"] = {
            "at": int(now),
            "when": _ts_label(now),
            "source": str(source or "manual"),
            "source_name": _SOURCE_NAMES.get(str(source or "manual"), str(source or "manual")),
            "count": len(items),
            "ok": ok_n,
            "text": summary,
        }
        return _commit(_reschedule(cfg))


_SOURCE_NAMES = {"auto": "定时", "web": "页面一键签到", "cmd": "QQ 命令", "manual": "手动"}


def _summary_text(rows: list[dict], total: int, ok_n: int) -> str:
    """一段人看的汇总文本（页面「最近一次」和命令回复都用它）。"""
    if not rows:
        return "没有签到任何角色（目标列表是空的）"
    body = [it["text"] for it in rows]
    if len(rows) == 1:
        return body[0]
    title = f"自动签到 · {total} 个角色（成功 {ok_n}）"
    return "\n".join([title, *body])


# ================= 目标清单（给弹窗勾选） =================


async def list_targets() -> list[dict]:
    """所有本地米游社账号 × 它们绑定的绝区零角色。

    打开「自动签到」弹窗时调一次（**每个账号一个网络请求**，所以是按需触发，
    不做后台定时刷新）。单个账号失败只在该账号行里带一句 `error`，不影响别的。
    """
    out: list[dict] = []
    for acc in store.public_accounts():
        aid = str(acc.get("account_id") or "")
        row = {
            "account_id": aid,
            "account": str(acc.get("nickname") or "") or aid,
            "logged": bool(acc.get("logged")),
            "error": "",
            "roles": [],
        }
        if not row["logged"]:
            row["error"] = "该账号未登录或凭证缺失，请重新扫码"
        else:
            try:
                roles = await client.bind_roles(aid)
            except Exception as exc:  # noqa: BLE001 —— 单个账号失败不影响其它账号
                row["error"] = f"读取角色失败：{exc}"
                roles = []
            for r in roles if isinstance(roles, list) else []:
                r = r if isinstance(r, dict) else {}
                uid = str(r.get("game_uid") or "")
                if not uid:
                    continue
                server = str(r.get("region") or "prod_gf_cn")
                row["roles"].append({
                    "uid": uid,
                    "server": server,
                    "role": str(r.get("nickname") or ""),
                    "region": client.region_name(server),
                })
            if not row["error"] and not row["roles"]:
                row["error"] = "该账号没有绑定绝区零角色"
        out.append(row)
    return out


# ================= 执行 =================


def is_running() -> bool:
    """现在有没有一轮在跑（页面靠它显示「签到中…」）。

    `_pending` 那部分专门处理空窗：后台任务用 `spawn()` 起，`create_task` 之后
    协程还没被调度、`_run_lock` 自然还没锁上 —— 只看锁会让页面第一次轮询
    显示成「没在跑」，于是轮询提前结束、结果要刷新才看得到。
    """
    return _pending or _run_lock.locked()


async def run_once(source: str = "manual") -> dict:
    """把配置里勾选的角色**串行**签一遍，返回汇总。

    `source` 只用于记录与展示（auto / web / cmd）。已经在跑时立刻返回 busy —— 
    这时若来源是定时，说明手动那次已经把今天这份签完了，顺手把今天标记成「已跑」，
    免得循环 30 秒后又来一遍。
    """
    if _run_lock.locked():
        if source == "auto":
            _record_run([], source)
        return {"ok": False, "busy": True, "message": "已经有签到任务在进行中", "items": [], "text": ""}

    async with _run_lock:
        cfg = load()
        if not cfg.get("enabled"):
            _record_run([], source)
            return {"ok": False, "message": "自动签到当前是关闭的（在「自动签到」里启用）",
                    "items": [], "text": ""}
        targets = cfg.get("targets") or []
        if not targets:
            _record_run([], source)
            return {"ok": False, "message": "还没有选要签到的角色（打开「自动签到」勾选）",
                    "items": [], "text": ""}

        gap = float(cfg.get("gap") or DEFAULT_GAP)
        gj = float(cfg.get("gap_jitter") or 0.0)
        # 间隔 ≈ gap ± gj 秒，再由 _Pacer 的 floor 兜到 GAP_FLOOR（3 秒）以上
        lo = max(0.05, (gap - gj) / gap)
        hi = max(lo, (gap + gj) / gap)
        pacer = _Pacer(gap=gap, factor=(lo, hi), floor=GAP_FLOOR)

        # 同一账号勾了几个角色 → 多角色时展示名写成「账号/角色」，便于区分
        per_acc: dict[str, int] = {}
        for t in targets:
            per_acc[t["account_id"]] = per_acc.get(t["account_id"], 0) + 1

        rows: list[dict] = []
        for t in targets:
            aid = t["account_id"]
            raw_role = {
                "game_uid": t["uid"],
                "region": t.get("server") or "prod_gf_cn",
                "nickname": t.get("role") or t.get("account") or "",
            }
            try:
                rows.append(await _sign_role(aid, t.get("account") or aid, raw_role,
                                             pacer, per_acc.get(aid, 1) > 1))
            except Exception as exc:  # noqa: BLE001 —— 单个角色失败不影响其它角色
                logger.warning(f"miyoushe 自动签到：角色 {t.get('uid')} 异常：{exc}")
                rows.append({
                    "account": t.get("account") or aid, "role": t.get("role") or "",
                    "label": t.get("account") or aid, "uid": t["uid"],
                    "server": t.get("server") or "prod_gf_cn",
                    "region": client.region_name(t.get("server") or "prod_gf_cn"),
                    "days": 0, "status": f"签到失败：{exc}",
                    "signed": False, "multi": per_acc.get(aid, 1) > 1, "text": "",
                })

        items = _items(rows)
        ok_n = sum(1 for r in rows if r.get("signed"))
        cfg = _record_run(rows, source)
        text = _summary_text(items, len(rows), ok_n)
        logger.info(f"miyoushe 自动签到（{source}）：{ok_n}/{len(rows)} 个角色签上")
        await _notify_run(source, text)      # 私聊通知（内部全包异常，不影响上面的结果）
        return {
            "ok": True,
            "busy": False,
            "count": len(rows),
            "ok_count": ok_n,
            "items": items,
            "text": text,
            "finished_at": int(cfg.get("last_run_at") or 0),
        }


async def _spawned(source: str) -> None:
    """`spawn()` 起的那个任务：跑完**无论成败**都要把 `_pending` 摘掉。

    摘在 finally 里是必须的：万一 `run_once` 半路抛了，`_pending` 留在 True 会让
    页面永远显示「正在签到」、`spawn()` 也永远返回 False（= 一键签到再也点不动，
    只能重启）。宁可多这几行。
    """
    global _pending
    try:
        await run_once(source)
    finally:
        _pending = False


def spawn(source: str = "web") -> bool:
    """后台起一轮（页面「一键签到」用），返回是否真的起了。

    立刻把 `_pending` 标上，页面第一次轮询就能看到「正在签到」—— 详见 `is_running()`。
    """
    global _pending
    if is_running():
        return False
    _pending = True
    asyncio.create_task(_spawned(source))
    return True


# ================= 通知（把签到结果私聊推给指定 QQ） =================
#
# 为什么要**限定 OneBot**：QQ 官方机器人没法按 QQ 号主动发私聊（要用 openid +
# msg_seq，且拿不到「某人的 QQ 号」），而 OneBot 一条 `send_private_msg` 就够。
# 用户也明确说「仅 onebot 机器」。所以实例下拉里只能选 OneBot。


async def list_onebot_instances() -> list[dict]:
    """在线的 OneBot 平台实例（通知里「用哪个机器人发」那个下拉框的数据源）。

    实际逻辑在 miyoho/send.py（AstrBot platform_manager），这里转发一下。
    """
    return await _send_shim.list_onebot_instances()


async def _notify_run(source: str, text: str) -> None:
    """把这一轮结果私聊推给配置里的接收者。

    **无论如何都不往外抛** —— 通知只是附加动作，发失败绝不能影响签到本身
    （调用点是 `run_once` 的结尾，一抛就把整轮结果变成异常）。
    """
    cfg = load().get("notify") or {}
    if not cfg.get("enabled"):
        return
    when = str(cfg.get("when") or NOTIFY_AUTO)
    if when != NOTIFY_ALWAYS and source != "auto":
        return                                                  # 只通知定时那一轮
    targets = cfg.get("targets") or []
    sid = str(cfg.get("self_id") or "")
    if not targets:
        logger.warning("miyoushe 自动签到：通知开着但没填接收者 QQ，跳过")
        return
    if not sid:
        logger.warning("miyoushe 自动签到：通知开着但没选机器人实例，跳过")
        return
    body = f"绝区零自动签到 · {_SOURCE_NAMES.get(source, source)}\n{text}"
    for uid in targets:
        try:
            await _send_shim.send_private(sid, str(uid), body)
            logger.info(f"miyoho 自动签到结果已私聊通知 {uid}")
        except Exception as exc:  # noqa: BLE001 —— 一个人发失败（如不是好友）不影响其它人
            logger.warning(f"miyoho 自动签到通知 {uid} 失败：{exc}")


# ================= 状态（给页面） =================


def status() -> dict:
    """页面要的全部信息：配置 + 是否在跑 + 下次 / 上次时刻的给人看的写法。"""
    with _lock:
        cfg = load()
        return {
            "config": cfg,
            "running": is_running(),
            "next_label": _ts_label(cfg.get("next_run_at")),
            "last_label": _ts_label(cfg.get("last_run_at")),
            "count": len(cfg.get("targets") or []),
        }


# ================= 后台循环 =================


async def loop() -> None:
    """每 30 秒看一次「到点没有」。是否真跑由模块开关 + 配置共同决定。"""
    await asyncio.sleep(_FIRST_DELAY)
    while True:
        try:
            if True:  # 原 gate.guard_task(MODULE)：AstrBot 由插件开关统一控制
                cfg = load()
                if cfg.get("enabled") and cfg.get("mode") == MODE_AUTO:
                    nxt = int(cfg.get("next_run_at") or 0)
                    if not nxt:
                        save({})                    # 老配置 / 刚开启 → 补排一次
                    elif time.time() >= nxt:
                        logger.info(f"miyoushe 自动签到到点（预约 {_ts_label(nxt)}），开始执行")
                        await run_once("auto")
        except Exception:  # noqa: BLE001 —— 单轮出错不影响后续（下一轮继续）
            logger.exception("miyoushe 自动签到循环出错（下一轮继续）")
        await asyncio.sleep(_TICK)


def start() -> None:
    """启动后台循环（插件入口在 on_startup 里调一次）。"""
    asyncio.create_task(loop())
    cfg = load()
    if cfg.get("enabled") and cfg.get("mode") == MODE_AUTO and not int(cfg.get("next_run_at") or 0):
        cfg = save({})                              # 从没排过 → 立刻排上
    n = len(cfg.get("targets") or [])
    if cfg.get("enabled") and cfg.get("mode") == MODE_AUTO:
        logger.info(
            f"米游社自动签到已启动：每天 {cfg['time']} 前后（±{cfg['jitter']} 分钟）· "
            f"{n} 个角色 · 下次 {_ts_label(cfg.get('next_run_at'))} · "
            f"请求间隔 {cfg['gap']:g}s ± {cfg['gap_jitter']:g}s（下限 {GAP_FLOOR:g}s）"
        )
    else:
        why = "已关闭" if not cfg.get("enabled") else "仅手动触发"
        logger.info(f"米游社自动签到已加载：当前不跑定时（{why}）· {n} 个角色")


# ---------------- 启动：把后台循环拉起来 ----------------
#
# 循环本身在上面 `loop()`，这里只负责在 bot 启动时拉起它 —— 与插件里其它后台任务
# 的写法保持一致（模块被禁用时空转，不发任何请求）。

# AstrBot：后台循环由 main.py 在 initialize 里调 start() 拉起（原 nonebot on_startup 钩子已移除）。
