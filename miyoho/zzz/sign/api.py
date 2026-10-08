"""绝区零每日签到（luna 活动接口）。

2026-10-04 从原 `src/client.py` 的「签到」段整体搬来，逻辑未改。

与战绩接口的关键区别：**必须带 DS 签名**（LUNA salt），参数固定 act_id + region + uid。
"""

from __future__ import annotations

import hashlib
import json
import random
import time

from ...core.mys import (
    _base_headers,
    _dict,
    _int,
    _list,
    _payload,
    _record_cookie,
    get_client,
)

# 绝区零签到走「luna」活动接口（api-takumi.mihoyo.com/event/luna/zzz）。
#   - GET  /info、/home：DS = md5(salt&t&r&q)，q = query 串（不含 b）
#   - POST /sign        ：DS = md5(salt&t&r&b&q)，b = JSON body（q 为空）
# 实测：DS 正确时用一个不存在的 uid 会返回业务码 -10002（未绑定角色），
# DS 错误则返回签名类错误码 —— 以此确认签名格式无误（不会真的改动账号）。
LUNA_API = "https://api-takumi.mihoyo.com/event/luna/zzz"
ZZZ_SIGN_ACT_ID = "e202406242138391"
SALT_LUNA = "t0qEgfub6cvueAPgR5m9aQWWVciEer7v"
def _ds_luna(query: str = "", body: str = "") -> str:
    """luna 活动接口的 DS：GET 用 `salt&t&r&q`，POST 用 `salt&t&r&b&q`。"""
    t = int(time.time())
    r = random.randint(100001, 200000)
    if body:
        raw = f"salt={SALT_LUNA}&t={t}&r={r}&b={body}&q={query}"
    else:
        raw = f"salt={SALT_LUNA}&t={t}&r={r}&q={query}"
    return f"{t},{r},{hashlib.md5(raw.encode()).hexdigest()}"


def _luna_headers(cookie: str, query: str = "", body: str = "") -> dict:
    return {
        **_base_headers("https://act.mihoyo.com/"),
        "Cookie": cookie,
        "x-rpc-client_type": "5",
        "x-rpc-signgame": "zzz",
        "DS": _ds_luna(query, body),
    }


def _luna_query(uid: str, server: str) -> str:
    """签到接口固定 query（顺序即签名顺序，别改）。"""
    return f"act_id={ZZZ_SIGN_ACT_ID}&lang=zh-cn&region={server or 'prod_gf_cn'}&uid={uid}"


async def zzz_sign_boards(uid: str, server: str, account_id=None) -> dict:
    """签到面板：累计签到天数 / 今日是否已签（/info）+ 本月奖励表（/home）。

    两个子请求都**只读**，不触发签到。
    """
    cookie = _record_cookie(account_id)
    query = _luna_query(str(uid), str(server or "prod_gf_cn"))
    http = get_client()
    info_resp = await http.get(LUNA_API + "/info?" + query, headers=_luna_headers(cookie, query=query))
    home_resp = await http.get(LUNA_API + "/home?" + query, headers=_luna_headers(cookie, query=query))
    info = _dict(_payload(info_resp).get("data"))
    home = _dict(_payload(home_resp).get("data"))
    return {
        "info": info,
        "awards": _list(home.get("awards")),
        "month": home.get("month"),
        "total_sign_day": _int(info.get("total_sign_day")),
        "is_sign": bool(info.get("is_sign")),
    }


async def zzz_sign_do(uid: str, server: str, account_id=None) -> dict:
    """执行签到（POST /sign）。"""
    body = json.dumps(
        {"act_id": ZZZ_SIGN_ACT_ID, "region": str(server or "prod_gf_cn"), "uid": str(uid)},
        separators=(",", ":"),
    )
    headers = {**_luna_headers(_record_cookie(account_id), body=body), "Content-Type": "application/json"}
    resp = await get_client().post(LUNA_API + "/sign", content=body, headers=headers)
    return _dict(_payload(resp).get("data"))

