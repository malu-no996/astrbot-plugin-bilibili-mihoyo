"""绝区零战绩的**本地存档**：查询时顺手存一份 + 后台每天自动抓一轮。

2026-10-04 从插件入口 `__init__.py` 拆出来，逻辑未改。

为什么必须有后台抓取：官方只给「本期 / 上期」两份数据，**不主动抓就会永久丢**。
只靠「用户点一下查询才存」的话，某期结束到下一期之间没人打开页面，那一期就没了。
这里每天把每个账号 × 每个绝区零角色的「本期 + 上期」都抓一遍：本期结束时它还会以
「上期」的身份再被刷新一次，于是能拿到那期的最终成绩。

节奏基准是「上次抓取时刻」（记在 data/zzz/records/_capture.json），**不是**进程启动时刻：
否则频繁重启会把「每天一次」变成「每次重启一次」，也测不准到底隔了多久。
"""
from __future__ import annotations

import asyncio
import time

from loguru import logger

from ...core import asset_cache, store
from ...core import mys as client
from . import store as record_store
from .api import deadly_assault, shiyu_defense


# ---------------- 战绩存档（危局 / 防卫战通用） ----------------


def _save_record(kind: str, uid: str, server: str, data: dict, source: str = "web") -> dict:
    """把这次查询结果按赛期存档（同赛期只留最新一版）。

    官方只有「本期 / 上期」两份，历史全靠这里一份份攒（见 record_store 模块说明）。
    **存档失败绝不影响查询本身** —— 宁可这次没存上，也不能让页面报错。
    """
    try:
        return record_store.save(kind, uid, server, data, source=source)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"miyoushe 战绩存档失败（{kind}/{uid}）：{exc}")
        return {}


#
# 节奏基准是「上次抓取时刻」（记在 data/zzz/records/_capture.json），**不是**进程启动时刻：
# 否则频繁重启会把「每天一次」变成「每次重启一次」，也测不准到底隔了多久。
RECORD_CAPTURE_INTERVAL = 24 * 3600   # 抓取间隔（秒）。赛期以周计，每天一次足够兜住末端变化。
RECORD_CAPTURE_FIRST_DELAY = 90       # 启动后首次补抓前的延迟（错开启动时的初始化高峰）
RECORD_CAPTURE_DISABLED_WAIT = 600    # 模块被禁用时的空转间隔（秒）；不抓也不记，等下次再看
RECORD_CAPTURE_GAP = 1.5             # 每个请求之间的间隔（秒），别打太快


async def _capture_records_once() -> int:
    """抓一轮：返回成功写入的赛期条数。凭证失效 / 网络问题都只记日志，不抛。"""
    saved = 0
    for aid in store.account_ids():
        if not store.logged_in(aid):
            continue
        try:
            roles = await client.bind_roles(aid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"miyoushe 战绩自动存档：读账号 {aid} 的角色列表失败：{exc}")
            continue
        for r in roles if isinstance(roles, list) else []:
            r = r if isinstance(r, dict) else {}
            uid = str(r.get("game_uid") or "")
            if not uid:
                continue
            server = str(r.get("region") or "prod_gf_cn")
            for kind, fetch in (("deadly", deadly_assault), ("shiyu", shiyu_defense)):
                for previous in (False, True):
                    try:
                        data = await fetch(uid, server, previous=previous, account_id=aid)
                        data = await asset_cache.rewrite_assets(data)
                        if _save_record(kind, uid, server, data, source="auto"):
                            saved += 1
                    except Exception as exc:  # noqa: BLE001 —— 单个角色/赛期失败不影响其余的
                        logger.debug(f"miyoushe 自动存档：{kind}/{uid} previous={previous} 失败：{exc}")
                    await asyncio.sleep(RECORD_CAPTURE_GAP)
    return saved


async def _run_record_capture() -> None:
    """跑一轮并打上「已完成」标记。

    失败也照样打标记（否则会一直重试）：单次失败不致命 —— 赛期以周计，
    下一轮抓的又总是「本期 + 上期」，落下的一天能在后续轮次里补回来。
    """
    try:
        n = await _capture_records_once()
        if n:
            logger.info(f"miyoushe 战绩自动存档完成：本轮写入 {n} 期")
    except Exception:  # noqa: BLE001
        logger.exception("miyoushe 战绩自动存档出错（下一轮继续）")
    finally:
        record_store.mark_capture()


def start_capture_loop() -> "asyncio.Task":
    """启动后台存档循环：**每天一轮**，且不受重启影响（main.py 在 initialize 里调）。

    每一轮都按「距上次抓取是否满一天」判断：满了就抓，没满就睡到满。
    因此首次启动（从未抓过）会在开始的 90 秒后补抓一次，之后稳定每天一次；
    不管中间重启多少次，节奏都跟着**上次抓取时刻**走。
    """
    async def loop() -> None:
        await asyncio.sleep(RECORD_CAPTURE_FIRST_DELAY)
        while True:
            left = RECORD_CAPTURE_INTERVAL - (time.time() - record_store.last_capture_at())
            if left > 0:                         # 还没到下次该抓的点 → 睡到点再醒
                await asyncio.sleep(left)
                continue
            await _run_record_capture()

    task = asyncio.create_task(loop())
    last = record_store.last_capture_at()
    logger.info(
        "米游社战绩自动存档已启动（每天抓一次「本期 + 上期」；上次抓取："
        + (time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "从未")
        + "，距满一天时自动开抓）"
    )
    return task
