"""绝区零战绩接口：危局强袭战 / 式舆防卫战 / 零号空洞 / 迷宫诡域 / 临界推演 / 实时便笺。

2026-10-04 从原 `src/client.py` 的「绝区零战绩」段整体搬来，逻辑未改。
**战绩类接口不需要 DS 签名**，只要 Cookie 有效即可（带上 x-rpc-* 头更稳）。

请求件与账号凭证从 `core.mys` 取；签到在 `zzz/sign/api.py`、抽卡在 `zzz/gacha/api.py`。
"""

from __future__ import annotations

from ...core.mys import (
    RECORD_API,
    MysError,
    _base_headers,
    _dict,
    _int,
    _payload,
    _record_cookie,
    get_client,
)
async def _record_get(path: str, params: dict, account_id=None) -> dict:
    """绝区零战绩通用请求（无需 DS，只需 Cookie + 基础头）。

    带上 account_id 是为了让「设备配置」里那台设备生效（角色类接口要靠它过
    设备指纹校验），传不进来就退回随机设备头。
    """
    headers = {
        **_base_headers("https://act.mihoyo.com/", account_id=account_id),
        "Cookie": _record_cookie(account_id),
        "x-rpc-client_type": "5",
    }
    resp = await get_client().get(
        RECORD_API + path, params=params, headers=headers
    )
    return _dict(_payload(resp).get("data"))


async def shiyu_defense(uid: str, server: str, previous: bool = False, account_id=None) -> dict:
    """式舆防卫战（hadal_info_v2）。previous=True 取往期。"""
    return await _record_get(
        "/hadal_info_v2",
        {
            "lang": "zh-cn",
            "role_id": str(uid),
            "server": str(server or "prod_gf_cn"),
            "schedule_type": 2 if previous else 1,
        },
        account_id,
    )


async def deadly_assault(uid: str, server: str, previous: bool = False, account_id=None) -> dict:
    """危局强袭战（hadal_mem_detail_v2）。previous=True 取往期。"""
    return await _record_get(
        "/hadal_mem_detail_v2",
        {
            "lang": "zh-cn",
            "uid": str(uid),
            "region": str(server or "prod_gf_cn"),
            "schedule_type": 2 if previous else 1,
        },
        account_id,
    )


async def abyss_abstract(uid: str, server: str, account_id=None) -> dict:
    """零号空洞摘要（abyss_abstract）。

    调查等级 / 鸣徽等级 / 悬赏委托 / 调查点数 / 数据收集 / 阶段解锁（枯败花圃、刀耕火焚）。

    ⚠️ 这个接口的参数名必须是 **`role_id` + `server`** —— 传 `uid` + `region` 会直接返回
    `-400005 网页异常`（和 /hadal_mem_detail_v2 的 uid+region 不一致，别"统一"掉）。
    """
    return await _record_get(
        "/abyss_abstract",
        {"lang": "zh-cn", "role_id": str(uid), "server": str(server or "prod_gf_cn")},
        account_id,
    )


async def zenkov_abstract(uid: str, server: str, account_id=None) -> dict:
    """迷宫诡域摘要（zenkov_abstract_info）。

    赛季数据 / 幻境地图（撤离度、最高收益、地狱·硬核解锁）/ 收藏（奖章 + 藏品）。
    与 /abyss_abstract 相反，这个接口用的是 uid + region。
    """
    return await _record_get(
        "/zenkov_abstract_info",
        {"lang": "zh-cn", "uid": str(uid), "region": str(server or "prod_gf_cn")},
        account_id,
    )


async def void_front_brief(uid: str, server: str, previous: bool = False,
                           account_id=None) -> dict:
    """临界推演 · 本期摘要（void_front_battle_period_abstract_info）。

    `previous=True` 取上期（schedule_type=2）。返回里
    `void_front_battle_abstract_info_brief` 含**本期 `void_front_id`** —— 明细接口要它。
    """
    return await _record_get(
        "/void_front_battle_period_abstract_info",
        {
            "lang": "zh-cn",
            "uid": str(uid),
            "region": str(server or "prod_gf_cn"),
            "schedule_type": 2 if previous else 1,
        },
        account_id,
    )


async def void_front_battle(uid: str, server: str, void_front_id=None,
                            previous: bool = False, account_id=None) -> dict:
    """临界推演 · 明细（void_front_battle_period_detail）。

    ⚠️ 2026-10 踩坑记录：老的 `/void_front_battle_detail`（`void_front_id` 写死 102）
    **已经下线**，米游社直接回 HTTP 404（页面表现为「接口返回非 JSON（HTTP 404）」）。
    新流程两步走：先 `void_front_battle_period_abstract_info` 拿**本期** id，再拿明细；
    期号官方已经不再固定，别写死。

    返回剥掉了外层 `void_front_battle_detail` 包装，形状与老接口一致
    （`void_front_battle_abstract_info_brief` / `boss_challenge_record` /
    `main_challenge_record_list` / `role_basic_info`）。
    """
    if not void_front_id:
        brief = await void_front_brief(uid, server, previous=previous, account_id=account_id)
        brief = brief.get("void_front_battle_abstract_info_brief")
        brief = brief if isinstance(brief, dict) else {}
        void_front_id = brief.get("void_front_id")
        if not void_front_id:
            return {}
    data = await _record_get(
        "/void_front_battle_period_detail",
        {
            "lang": "zh-cn",
            "uid": str(uid),
            "region": str(server or "prod_gf_cn"),
            "void_front_id": str(void_front_id),
            "schedule_type": 2 if previous else 1,
        },
        account_id,
    )
    inner = data.get("void_front_battle_detail")
    return inner if isinstance(inner, dict) else data


async def zzz_note(uid: str, server: str, account_id=None) -> dict:
    """实时便笺（体力 / 委托等）。"""
    return await _record_get(
        "/note",
        {"role_id": str(uid), "server": str(server or "prod_gf_cn")},
        account_id,
    )
