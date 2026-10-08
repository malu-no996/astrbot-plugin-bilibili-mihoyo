"""绝区零绳网月报（nap_ledger / month_info）：本月菲林等资源的收入构成。

对应 ZZZeroUID 的「绳网月报 / 月历 / 札记」：列出这个月各渠道拿了多少菲林、
各占多少百分比（日常活跃 / 邮件 / 成长 / 活动 / 零号空洞 / 式舆防卫战 / 其他）。

与战绩接口的关键区别
--------------------
1. **域名不同**：不是 `api-takumi-record`，而是 `api-takumi.mihoyo.com/event/nap_ledger`
   （和签到那个 luna 接口同属「活动域」）；
2. **不用 DS 签名**：只要 Cookie + 基础 x-rpc-* 头（对照 gsuid_core 的实现确认过，
   它那边也只是 deepcopy 一份头、没加 DS）；
3. **可以查历史月份**：`month` 参数传 `YYYYMM`（空 = 当月）。

返回值里 `optional_month` 是「哪些月份可查」，`data_month` 是这次实际查到的月份。
"""

from __future__ import annotations

from ...core.mys import (
    MysError,
    _base_headers,
    _dict,
    _list,
    _payload,
    _record_cookie,
    get_client,
)

# 月报走「活动域」（和签到同域），不是战绩域
MONTH_API = "https://api-takumi.mihoyo.com/event/nap_ledger"


def _headers(cookie: str) -> dict:
    return {
        **_base_headers("https://act.mihoyo.com/"),
        "Cookie": cookie,
        "x-rpc-client_type": "5",
        "x-rpc-signgame": "zzz",
    }


async def month_info(uid: str, server: str, month: str = "", account_id=None) -> dict:
    """绳网月报：`month` 形如 `202610`，留空 = 当月。

    返回官方那坨 `{uid, region, current_month, data_month, month_data, optional_month,
    role_info}`；`month_data.list` 是收入明细、`income_components` 是各渠道占比。
    """
    params = {
        "uid": str(uid),
        "region": str(server or "prod_gf_cn"),
        "month": str(month or ""),
    }
    resp = await get_client().get(
        MONTH_API + "/month_info", params=params, headers=_headers(_record_cookie(account_id)),
    )
    data = _dict(_payload(resp).get("data"))
    if not data:
        raise MysError("官方没有返回月报数据（该角色可能未开启札记）")
    return data
