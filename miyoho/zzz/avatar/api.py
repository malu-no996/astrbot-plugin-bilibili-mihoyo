"""绝区零角色接口：已拥有角色（avatar/basic）/ 角色详情（avatar/info）/ Enka 备用源。

2026-10-04 从原 `src/client.py` 的「绝区零战绩」段里把角色这几条单独拆出来。

⚠️ 这几个接口带**「角色详情」风控**：未在米游社开启角色详情公开、或设备不受信时
返回 `retcode=10041`（危局 / 邦布等接口不受影响），所以这里刻意**不抛业务异常**、
把 retcode 交回调用方（页面据此降级到 Enka）。
"""

from __future__ import annotations

from ...core.mys import (
    RECORD_API,
    MysError,
    _base_headers,
    _list,
    _payload,
    _record_cookie,
    _retcode_payload,
    get_client,
)

# Enka.Network：绝区零角色展示数据的备用源（官方 avatar/basic 被 10041 风控时的降级通道）。
# 只含玩家在游戏内设置的「展示角色」（通常 ≤6 个），不是全部拥有角色。
ENKA_API = "https://enka.network/api/zzz/uid"
ENKA_UA = "MaluBot/1.0 (workbuddy; zzz avatar query)"
async def avatar_basic(uid: str, server: str, account_id=None) -> tuple[int, dict]:
    """账号已拥有角色列表（avatar/basic）。

    绝区零这个接口带**「角色详情」风控**：未在米游社开启角色详情公开（或设备不受信）时
    返回 `retcode=10041`；危局 / 邦布等接口不受影响，所以那两个一直能查。

    这里刻意**不抛业务异常**，把 retcode 交回调用方（`__init__.py` 里据此降级到 Enka）。

    `account_id` 一定要传：这个接口的放行与否取决于请求里那台设备是不是该账号
    登录过的（见 src/device.py），传了才会带上对应设备的 x-rpc-device_* 头。
    """
    headers = {
        **_base_headers("https://act.mihoyo.com/", account_id=account_id),
        "Cookie": _record_cookie(account_id),
        "x-rpc-client_type": "5",
    }
    resp = await get_client().get(
        RECORD_API + "/avatar/basic",
        params={
            "lang": "zh-cn",
            "role_id": str(uid),
            "server": str(server or "prod_gf_cn"),
        },
        headers=headers,
    )
    return _retcode_payload(resp)


async def avatar_info(
    uid: str, server: str, id_list, account_id=None
) -> tuple[int, list]:
    """角色详情（avatar/info）：音擎 / 驱动盘 / 技能加点 / 影画 / 面板属性。

    与 avatar/basic 同一套「角色详情」风控（未开放或设备不受信 → 10041），
    所以同样不抛业务异常、把 retcode 交回调用方。

    参数是 `id_list[]`（**可重复、一次查多个**）+ `need_wiki=false`；
    业务数据在 `data.avatar_list`（列表，每个角色一份详情）。
    """
    ids = [str(i) for i in (_list(id_list)) if str(i).strip()]
    if not ids:
        return -1, []
    params = [
        ("lang", "zh-cn"),
        ("role_id", str(uid)),
        ("server", str(server or "prod_gf_cn")),
        ("need_wiki", "false"),
    ]
    params += [("id_list[]", i) for i in ids]
    headers = {
        **_base_headers("https://act.mihoyo.com/", account_id=account_id),
        "Cookie": _record_cookie(account_id),
        "x-rpc-client_type": "5",
    }
    resp = await get_client().get(
        RECORD_API + "/avatar/info", params=params, headers=headers,
    )
    code, data = _retcode_payload(resp)
    rows = data.get("avatar_list") if isinstance(data, dict) else None
    return code, [r for r in _list(rows) if isinstance(r, dict)]


async def index(uid: str, server: str, account_id=None) -> tuple[int, dict]:
    """玩家概览（/index）：活跃天数 / 角色数 / 邦布数 / 层数 + 展示角色与邦布列表。

    就是网页端「我的绝区零」那一屏：stats（活跃天数、拥有角色数、邦布数、当前层数）、
    avatar_list（展示角色，含等级 / 影画 / 阵营 / 属性）、buddy_list（邦布）。

    ⚠️ 与 avatar/basic 同一套**「角色详情」风控**（未开放或设备不受信 → 10041），
    所以同样**不抛业务异常**、把 retcode 交回调用方（命令层据此给友好提示）。
    """
    headers = {
        **_base_headers("https://act.mihoyo.com/", account_id=account_id),
        "Cookie": _record_cookie(account_id),
        "x-rpc-client_type": "5",
    }
    resp = await get_client().get(
        RECORD_API + "/index",
        params={
            "lang": "zh-cn",
            "role_id": str(uid),
            "server": str(server or "prod_gf_cn"),
        },
        headers=headers,
    )
    return _retcode_payload(resp)


async def enka_showcase(uid: str) -> dict:
    """Enka 展示角色（备用源）。

    返回原始 JSON（`{PlayerInfo, uid, ttl, region}`）。只含游戏内「展示角色」，
    通常 ≤6 个；用于官方接口被风控时的降级展示。
    """
    resp = await get_client().get(
        f"{ENKA_API}/{uid}",
        headers={"User-Agent": ENKA_UA, "Accept": "application/json"},
    )
    if resp.status_code == 404:
        raise MysError("Enka 未收录该角色（该账号可能从未把角色放进展示栏）")
    if resp.status_code == 429:
        raise MysError("Enka 接口限频，请稍后再试")
    try:
        data = resp.json()
    except ValueError:
        raise MysError(f"Enka 返回非 JSON（HTTP {resp.status_code}）")
    if not isinstance(data, dict) or not data.get("PlayerInfo"):
        raise MysError(f"Enka 未返回展示数据（HTTP {resp.status_code}）")
    return data
