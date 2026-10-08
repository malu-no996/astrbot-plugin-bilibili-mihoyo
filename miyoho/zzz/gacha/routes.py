"""绝区零 · 调频（抽卡） 的管理页路由（原 src/zzz/gacha/routes.py 的 AstrBot 移植）。

  GET  zzz/gacha/sync    同步抽卡记录到 data/zzz/{uid}.csv 并返回统计汇总
  GET  zzz/gacha/local   只读本地 CSV 的统计（**不发任何网络请求**，秒开）
  GET  zzz/gacha         直连官方查某一频段的记录
  POST zzz/gacha/url     直接查「游戏内复制的抽卡链接」（绕过 genAuthKey）
  GET  zzz/gacha/types   可查询的调频类型列表
"""
from __future__ import annotations

from ...core.web import json_response, request

from ...core import mys as client              # 只用它的 region_name
from ...core.web import body, call, fail
from . import stats as gacha_stats
from . import store as gacha_store
from .api import GACHA_TYPES, full_gacha_log, full_gacha_log_by_url, sync_gacha


def _qstr(key: str, default: str = "") -> str:
    return str(request.query.get(key, default) or default)


# ---------------- 绝区零：抽卡（调频）查询 ----------------


async def api_zzz_gacha_sync():
    """同步抽卡记录到 data/zzz/{uid}.csv 并返回统计汇总。

    - 首次（无存档）或 force=1 → 全量翻页（可能较慢，页间隔 1s）；
    - 之后 → 增量（每频段以本地最大 id 为游标，只拉新记录，秒级）；
    - 六个频段（常驻/独家/音擎/邦布/独家重映/音擎回响）一次同步完。
    """
    uid, server = _qstr("uid"), _qstr("server", "prod_gf_cn")
    account_id = _qstr("account_id")
    force = _qstr("force") in ("1", "true")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        sync_gacha(uid, server, account_id=account_id or None, force=force),
        "同步抽卡记录失败",
    )
    if err:
        return err
    return json_response({"ok": True, **(data if isinstance(data, dict) else {})})


async def api_zzz_gacha_local():
    """只读本地 CSV（data/zzz/{uid}.csv）的统计汇总，**不发起任何网络请求**。

    - 点「查询抽卡记录」时调用：默认从本地文件拿数据，秒开；
    - 本地没有该角色的存档 → ok:false + 提示先「增量更新」或「强制全量」。
    """
    uid = _qstr("uid")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    data = gacha_store.load(uid)
    if not data or not data.get("items"):
        return json_response(
            {"ok": False, "message": "本地还没有该角色的抽卡记录，请先点「增量更新」或「强制全量」获取。"}
        )
    summary = gacha_stats.summarize(data["items"])
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": data.get("server", "prod_gf_cn"),
            "region_name": client.region_name(data.get("server", "prod_gf_cn")),
            "updated_at": data.get("updated_at"),
            "count": data.get("count"),
            **summary,
        }
    )


async def api_zzz_gacha():
    """抽卡（调频）记录查询。gacha_type：1 常驻 / 2 独家 / 3 音擎 / 5 邦布。"""
    uid, server = _qstr("uid"), _qstr("server", "prod_gf_cn")
    gacha_type, account_id = _qstr("gacha_type", "2"), _qstr("account_id")
    try:
        pages = int(_qstr("pages", "30"))
    except ValueError:
        pages = 30
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        full_gacha_log(
            uid, server, gacha_type=gacha_type, limit_pages=pages,
            account_id=account_id or None,
        ),
        "读取抽卡记录失败",
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    items = data.get("list") or []
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": server,
            "region_name": client.region_name(server),
            "gacha_type": gacha_type,
            "gacha_type_name": GACHA_TYPES.get(str(gacha_type), str(gacha_type)),
            "count": int(data.get("count") or len(items)),
            "list": items,
        }
    )


async def api_zzz_gacha_url():
    """直接查「游戏内复制的抽卡链接」。

    绕过 genAuthKey（官方那条路可能收紧），body：{url, gacha_type?}。
    链接里的原始参数（win_mode / plat_type / init_log_* 等）会原样带上。
    """
    payload = await body()
    url = str(payload.get("url") or "").strip()
    if not url:
        return fail("缺少抽卡链接：请在游戏内调频记录页复制")
    gacha_type = str(payload.get("gacha_type") or "")
    err, data = await call(
        full_gacha_log_by_url(url, gacha_type=gacha_type), "读取抽卡记录失败"
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    items = data.get("list") or []
    return json_response(
        {
            "ok": True,
            "uid": "",
            "server": data.get("region") or "prod_gf_cn",
            "region_name": client.region_name(data.get("region") or "prod_gf_cn"),
            "gacha_type": data.get("gacha_type") or gacha_type,
            "gacha_type_name": GACHA_TYPES.get(
                str(data.get("gacha_type") or gacha_type), str(data.get("gacha_type") or gacha_type)
            ),
            "count": int(data.get("count") or len(items)),
            "list": items,
        }
    )


async def api_zzz_gacha_types():
    """可查询的调频类型列表。"""
    return json_response(
        {
            "ok": True,
            "types": [{"value": k, "label": v} for k, v in GACHA_TYPES.items()],
        }
    )


ROUTES = [
    ("zzz/gacha/sync", "GET", api_zzz_gacha_sync, "同步抽卡记录"),
    ("zzz/gacha/local", "GET", api_zzz_gacha_local, "本地抽卡统计"),
    ("zzz/gacha", "GET", api_zzz_gacha, "抽卡记录查询"),
    ("zzz/gacha/url", "POST", api_zzz_gacha_url, "按抽卡链接查询"),
    ("zzz/gacha/types", "GET", api_zzz_gacha_types, "调频类型列表"),
]
