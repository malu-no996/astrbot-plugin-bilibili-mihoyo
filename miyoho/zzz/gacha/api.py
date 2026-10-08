"""绝区零调频（抽卡）记录接口。

2026-10-04 从原 `src/client.py` 的「抽卡」段整体搬来，逻辑未改。
- **抽卡记录是「两步走」**，配方对照 gsuid_core（ZZZeroUID 的上游，实测可用）：
  ① 申请 authkey：POST `https://api-takumi.mihoyo.com/binding/api/genAuthKey`
     （注意是 **mihoyo.com**，不是 miyoushe.com）；
     Cookie = `stuid=..;stoken=..;mid=..`；UA = **`okhttp/4.8.0`**；
     DS = **LK2 salt 的 DS2 形式（`salt&t&r`，不带 body）**；
     body = {auth_appid: webview_gacha, game_biz, game_uid, region}。
     retcode 1016 = 该游戏 UID 不属于当前登录账号；-100 = 登录态失效（多因缺 stoken）。
  ② 拉记录：GET `https://public-operation-nap.mihoyo.com/common/gacha_record/api/getGachaLog`
     （绝区零国服专用域）。参数两套别混：
       - 登录凭证路线（本文件默认）：带 `gacha_id`、`plat_type=ios`、
         `device_type=mobile`、end_id 起始 `"0"`（对照 ZZZeroUID）。
       - 游戏内 webview 链接路线：参数全部来自链接本身（plat_type=android、
         无 gacha_id、end_id 空串），见 `parse_gacha_url`。
     频段编号两套：`real_gacha_type` 用基础类型 1/2/3/5/102/103，
     `gacha_type` 用池子编号 1001/2001/3001/5001/12002/13002（见 GACHA_POOL_CODE）。
  两步都实现了多套组合重试，失败信息里会带上每一步的 retcode/HTTP 状态。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import string
import time
import urllib.parse
from typing import Any

import httpx
from loguru import logger

from ...core import store
from ...core.mys import (
    MysError,
    UA,
    ZZZ_BIZ,
    _base_headers,
    _ds2,
    _dict,
    _ds,
    _ds_lk2,
    _int,
    _list,
    _payload,
    _random_hex,
    APP_VERSION_TAKUMI,
    UA_OKHTTP,
    get_client,
    region_name,
)
from . import stats as gacha_stats
from . import store as gacha_store
# ---------------- 抽卡（调频）记录 ----------------

# 绝区零调频类型 → 中文
# 频段「基础类型」→ 中文名。基础类型就是请求里的 real_gacha_type / init_log_gacha_base_type。
# 对照 ZZZeroUID：1001=常驻 / 2001=独家 / 3001=音擎 / 5001=邦布 / 12002=独家重映 / 13002=音擎回响
GACHA_TYPES = {
    "1": "常驻频段",
    "2": "独家频段",
    "3": "音擎频段",
    "5": "邦布频段",
    "102": "独家重映",
    "103": "音擎回响",
}

# 翻页间隔（秒）：一个请求处理完，隔 1 秒再发下一个（用户要求从 0.5s 提到 1s，
# 同时这是「请求之间」的最小间隔，避免米游社风控/限频）。
GACHA_PAGE_INTERVAL = 1.0

# 抽卡 authkey 缓存：genAuthKey 米游社有限频，连续两次申请（间隔仅 1s）会被拒 → 整次 400。
# authkey 本身有效期数分钟，按 (uid,server,account_id) 缓存复用，快速重复点击直接命中缓存、
# 不再打 genAuthKey，从根上消除「第一个点完马上点第二个就 400」的问题。
_GACHA_AUTHKEY_TTL = 300.0          # 复用窗口（秒）
_GACHA_AUTHKEY_CACHE: dict[str, tuple] = {}

# 基础类型 → 池子编号（gacha_type / init_log_gacha_type 用的是这一套）
GACHA_POOL_CODE = {
    "1": "1001",
    "2": "2001",
    "3": "3001",
    "5": "5001",
    "102": "12002",
    "103": "13002",
}

# 绝区零 gacha_id（固定值，ZZZeroUID 与早期实现一致）
ZZZ_GACHA_ID = "2c1f5692fdfbb733a08733f9eb69d32aed1d37"

# 抽卡 authkey（Auth Key B）接口 host：UIGF 文档给的是 miyoushe.com，
# mihoyo.com 作为兜底（两者历史上都可用过）
# genAuthKey 的候选 host。**mihoyo.com 是正解**（gsuid_core / ZZZeroUID 用的就是它）；
# miyoushe.com 留作兜底。
_GENAUTHKEY_HOSTS = (
    "https://api-takumi.mihoyo.com",
    "https://api-takumi.miyoushe.com",
)

# 抽卡记录（getGachaLog）端点：绝区零国服是 public-operation-nap
# （参考 hoyo-rs 路由表 (Region::Chinese, Game::ZZZ)）。旧的 common 域名留作兜底。
_GACHA_HOSTS = (
    "https://public-operation-nap.mihoyo.com/common/gacha_record/api",
    "https://public-operation-nap.mihoyo.com/gacha_record/api",
    "https://public-operation-common.mihoyo.com/common/gacha_record/api",
)
_gacha_host_ok = ""
_gacha_variant_ok = ""


def stoken_cookie(account_id=None) -> str:
    """拼 genAuthKey 要的 Cookie：**stuid / stoken / mid** 三件套。

    这是「登录凭证查抽卡」的关键 —— 该接口只认 stoken，拿 ltoken / cookie_token
    去请求一律 retcode -100「登录状态失效」。stoken 在扫码登录确认后由
    `data.tokens`（name = stoken / stoken_v2）给出，见 login_poll。
    老账号（2026-09 之前登录的）没有存这一项，返回空串 → 需要重新扫码登录。
    """
    ck = store.cookie(account_id)
    stoken = str(ck.get("stoken") or ck.get("stoken_v2") or "")
    if not stoken:
        return ""
    stuid = str(ck.get("stuid") or ck.get("stuid_v2") or ck.get("account_id_v2")
                or ck.get("account_id") or account_id or "")
    mid = str(ck.get("mid") or ck.get("account_mid_v2") or ck.get("account_mid") or "")
    parts = [f"stuid={stuid}", f"stoken={stoken}"]
    if mid:
        parts.append(f"mid={mid}")
    return ";".join(parts)


def has_stoken(account_id=None) -> bool:
    """该账号是否已保存 stoken（决定「登录凭证」查抽卡这条路能不能走）。"""
    return bool(stoken_cookie(account_id))


async def gen_auth_key(uid: str, server: str, account_id=None) -> str:
    """申请抽卡 authkey（Auth Key B / webview_gacha）。

    对照 **gsuid_core**（ZZZeroUID 的上游）里经过验证的实现，要点：
      POST https://api-takumi.mihoyo.com/binding/api/genAuthKey
      - Cookie 必须是 **stuid;stoken;mid**（不是 ltoken / cookie_token）
      - User-Agent 必须是 **okhttp/4.8.0**（米游社 App 的原生网络库 UA）
      - DS = **LK2 salt 的 DS2 形式**（`salt&t&r`，**不带 body/query**）
      - 头：x-rpc-client_type=5 / app_version=2.102.1 / sys_version=12 /
            channel=mihoyo / device_id(32 位随机) / device_name / device_model=Mi 10
            Referer=https://app.mihoyo.com，Host=api-takumi.mihoyo.com
      - body = {auth_appid: webview_gacha, game_biz, game_uid, region}

    常见 retcode：1016 游戏账号未绑定该 Cookie 对应的账号；-100 登录态失效（多半缺 stoken）。

    为防官方再调整，这里按「stoken 全套伪装 → 退化为 ltoken 的旧组合」依次尝试。
    """
    # 复用近期申请过的 authkey（米游社对 genAuthKey 有限频，连续点击会 400）。
    # 缓存只是加速 & 防限频，过期后该申请还是会正常打接口。
    cache_key = f"{uid}|{server}|{account_id or ''}"
    cached = _GACHA_AUTHKEY_CACHE.get(cache_key)
    if cached and (time.time() - cached[0]) < _GACHA_AUTHKEY_TTL:
        logger.debug(f"[miyoushe] genAuthKey 复用缓存（{int(time.time() - cached[0])}s 内）")
        return cached[1]
    ck = store.cookie(account_id)
    if not ck:
        raise MysError("该账号未登录或凭证缺失")
    stoken_ck = stoken_cookie(account_id)
    if not stoken_ck and not (
        ck.get("cookie_token") or ck.get("cookie_token_v2")
        or ck.get("ltoken_v2") or ck.get("ltoken")
    ):
        raise MysError("凭证不完整（既无 stoken 也无 ltoken），无法申请抽卡凭证，请重新扫码登录")
    full_ck = "; ".join(f"{k}={v}" for k, v in ck.items())
    body = {
        "auth_appid": "webview_gacha",
        "game_biz": ZZZ_BIZ,
        "game_uid": _int(uid),
        "region": str(server or "prod_gf_cn"),
    }
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    # ① stoken + okhttp UA + LK2-DS2（gsuid_core 配方，首选）
    # ② 退化为旧组合：完整 Cookie + 移动端 miHoYoBBS UA + DS1(含 body)
    # 每项 = (标签, Cookie, UA, DS)
    attempts: list[tuple[str, str, str, str]] = []
    if stoken_ck:
        attempts.append(("stoken·okhttp·DS(LK2)", stoken_ck, UA_OKHTTP, _ds_lk2()))
        attempts.append(("stoken·okhttp·DS1(b=body)", stoken_ck, UA_OKHTTP, _ds("", body_str)))
    else:
        attempts.append(("ltoken·miHoYoBBS·DS1(b=body)", full_ck, UA, _ds("", body_str)))
        attempts.append(("ltoken·miHoYoBBS·DS2", full_ck, UA, _ds2()))

    tried: list[str] = []
    for host in _GENAUTHKEY_HOSTS:
        url = host + "/binding/api/genAuthKey"
        for tag, cookie, ua, ds in attempts:
            headers = {
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "X-Requested-With": "com.mihoyo.hyperion",
                "x-rpc-app_version": APP_VERSION_TAKUMI,
                "x-rpc-client_type": "5",
                "x-rpc-sys_version": "12",
                "x-rpc-channel": "mihoyo",
                "x-rpc-device_id": _random_hex(32),
                "x-rpc-device_name": "".join(
                    random.sample(string.ascii_lowercase + string.digits, random.randint(1, 10))
                ),
                "x-rpc-device_model": "Mi 10",
                "Referer": "https://app.mihoyo.com",
                "Origin": "https://app.mihoyo.com",
                "Host": host.split("//", 1)[-1],
                "Cookie": cookie,
                "DS": ds,
            }
            try:
                resp = await get_client().post(url, content=body_str.encode(), headers=headers)
            except httpx.HTTPError as exc:
                tried.append(f"{host} · {tag} 请求失败：{type(exc).__name__}")
                continue
            if resp.status_code >= 400:
                tried.append(f"{host} · {tag} HTTP {resp.status_code}")
                continue
            try:
                payload = resp.json()
            except ValueError:
                tried.append(f"{host} · {tag} 返回非 JSON")
                continue
            if not isinstance(payload, dict):
                tried.append(f"{host} · {tag} 返回格式异常")
                continue
            code = _int(payload.get("retcode"), 0)
            logger.debug(
                f"[miyoushe] genAuthKey {host} {tag} retcode={code} "
                f"body={resp.text[:160]!r}"
            )
            if code == 0:
                key = str(_dict(payload.get("data")).get("authkey") or "")
                if key:
                    _GACHA_AUTHKEY_CACHE[cache_key] = (time.time(), key)
                    return key
                tried.append(f"{host} · {tag} retcode=0 但返回里没有 authkey")
            else:
                note = "游戏账号未绑定该 Cookie 对应的账号" if code == 1016 else ""
                if code == -100 and not stoken_ck:
                    note = "缺 stoken（该账号是旧版登录存的，请删除后重新扫码登录）"
                elif not note:
                    note = "请求体/签名不被接受"
                tried.append(
                    f"{host} · {tag} {payload.get('message') or '失败'}（retcode={code}"
                    + (f"，{note}" if note else "")
                    + "）"
                )

    raise MysError(
        f"申请抽卡凭证（authkey）失败，已尝试 {len(_GENAUTHKEY_HOSTS) * len(attempts)} 种组合："
        + " ｜ ".join(tried)
    )


def _gacha_headers() -> dict:
    """抽卡记录接口的伪装头（UA 必须是完整移动端 UA，裸 miHoYoBBS/x 会被降级）。

    Origin 与 Referer 保持一致（部分网关会校验同源）。
    """
    return {
        **_base_headers(
            "https://webstatic.mihoyo.com/", origin="https://webstatic.mihoyo.com"
        ),
        "Accept": "application/json, text/plain, */*",
    }


def _gacha_hosts() -> list[str]:
    """候选 getGachaLog 端点；上一次成功过的排到最前（进程内记忆）。"""
    hosts = list(_GACHA_HOSTS)
    if _gacha_host_ok in hosts:
        hosts.remove(_gacha_host_ok)
        hosts.insert(0, _gacha_host_ok)
    return hosts


def _gacha_base_params(server: str, authkey: str, end_id: str, size: int) -> dict:
    """抽卡请求的公共参数。

    两套样本，别混用：
    - **A. 登录凭证路线（genAuthKey 拿到的 authkey，本函数复刻这套）**
      对照 ZZZeroUID `get_zzz_gacha_log_by_authkey`：带 `gacha_id`、
      `plat_type=ios`、`device_type=mobile`、`end_id` 起始为 **"0"**。
    - **B. 游戏内 webview 链接路线**（MihoyoQBot 里抓到的样本）：
      `plat_type=android`、**没有 gacha_id**、`end_id` 为空串，还带
      win_mode / font_thickness_mode / is_gacha 等一堆 UI 参数。
      B 的参数全部来自链接本身，走 parse_gacha_url 原样保留，不进这里。
    """
    return {
        "authkey_ver": "1",
        "sign_type": "2",
        "auth_appid": "webview_gacha",
        "gacha_id": ZZZ_GACHA_ID,
        "timestamp": str(int(time.time())),
        "lang": "zh-cn",
        "device_type": "mobile",
        "plat_type": "ios",
        "region": str(server or "prod_gf_cn"),
        "authkey": authkey,
        "game_biz": ZZZ_BIZ,
        "page": "1",
        "size": str(max(1, min(int(size or 20), 20))),
        "end_id": str(end_id if end_id not in (None, "") else "0"),
    }


def _gacha_type_variants(gacha_type: str) -> list[tuple[str, dict]]:
    """频段编号的几种写法，按置信度排序，第一个返回非空列表的胜出。

    ZZZ 有「两套编号」：`real_gacha_type` / `init_log_gacha_base_type` 用基础类型
    1/2/3/5/102/103，而 `gacha_type` / `init_log_gacha_type` 用池子编号
    1001/2001/3001/5001/12002/13002。权威对照来自 ZZZeroUID 的
    gacha_type_meta_data + GACHA_BASE_TYPE_MAP：
      ① 池子编号 + 基础类型（正解）
      ② 两边都用基础类型（部分服务端这样也能过）
      ③ 两边都用池子编号（兜底）
    """
    t = str(gacha_type or "2")
    pool = GACHA_POOL_CODE.get(t, t)
    return [
        (f"{pool}/{t}(池子+基础)", {
            "gacha_type": pool, "real_gacha_type": t,
            "init_log_gacha_type": pool, "init_log_gacha_base_type": t,
        }),
        (f"{t}/{t}(纯基础)", {
            "gacha_type": t, "real_gacha_type": t,
            "init_log_gacha_type": t, "init_log_gacha_base_type": t,
        }),
        (f"{pool}/{pool}(纯池子)", {
            "gacha_type": pool, "real_gacha_type": pool,
            "init_log_gacha_type": pool, "init_log_gacha_base_type": pool,
        }),
    ]


async def _gacha_page(
    endpoint: str, base_params: dict, gacha_type: str, end_id: str, size: int = 20
) -> dict:
    """拉一页调频记录：按频段编号写法依次尝试，第一个返回非空列表的胜出。"""
    global _gacha_variant_ok

    tried: list[str] = []
    empty: list[dict] = []          # retcode=0 但空列表：编号写法可能不对，先留着
    variants = _gacha_type_variants(gacha_type)
    if _gacha_variant_ok:           # 上次成功过的写法排到最前
        hit = [v for v in variants if v[0] == _gacha_variant_ok]
        variants = hit + [v for v in variants if v[0] != _gacha_variant_ok]

    for tag, extra in variants:
        params = {
            **base_params, **extra,
            "page": "1",
            "size": str(max(1, min(int(size or 20), 20))),
            "end_id": str(end_id or ""),
            "timestamp": str(int(time.time())),
        }
        try:
            resp = await get_client().get(endpoint, params=params, headers=_gacha_headers())
        except httpx.HTTPError as exc:
            tried.append(f"{tag} 请求失败：{type(exc).__name__}")
            continue
        if resp.status_code >= 400:
            tried.append(f"{tag} HTTP {resp.status_code}")
            continue
        try:
            payload = resp.json()
        except ValueError:
            tried.append(f"{tag} 返回非 JSON")
            continue
        if not isinstance(payload, dict):
            tried.append(f"{tag} 返回格式异常")
            continue
        code = _int(payload.get("retcode"), 0)
        logger.debug(
            f"[miyoushe] getGachaLog {endpoint} {tag} retcode={code} "
            f"body={resp.text[:160]!r}"
        )
        if code == 0:
            data = _dict(payload.get("data"))
            if _list(data.get("list")):
                _gacha_variant_ok = tag
                return data
            empty.append(data)
            tried.append(f"{tag} 返回空列表（频段编号写法可能不对）")
            continue
        message = str(payload.get("message") or "失败")
        tried.append(f"{tag} {message}（retcode={code}）")
        # authkey 本身无效/过期 → 换任何写法都必然失败，直接止损
        if "authkey" in message.lower() or code in (-1, -102, -108):
            raise MysError(f"抽卡凭证无效或已过期（{message}，retcode={code}）")

    if empty:
        return empty[0]
    raise MysError("读取抽卡记录失败：" + " ｜ ".join(tried or ["无有效响应"]))


async def gacha_log(
    authkey: str,
    gacha_type: str = "2",
    uid: str = "",
    server: str = "prod_gf_cn",
    end_id: str = "",
    size: int = 20,
) -> dict:
    """拉取一页调频记录（authkey 由 gen_auth_key 申请而来）。

    gacha_type：1 常驻 / 2 独家 / 3 音擎 / 5 邦布。
    返回 {list:[...], page, region, ...}。
    """
    global _gacha_host_ok

    last_err = ""
    for host in _gacha_hosts():
        try:
            data = await _gacha_page(
                host + "/getGachaLog",
                _gacha_base_params(server, authkey, end_id, size),
                gacha_type, end_id, size,
            )
            _gacha_host_ok = host
            return data
        except MysError as exc:
            last_err = str(exc)
            if "expired" in last_err or "已过期" in last_err:
                raise
            continue  # 换下一个候选端点
    raise MysError(
        f"读取抽卡记录失败（已试 {len(_GACHA_HOSTS)} 个端点）：{last_err or '无可用端点'}"
    )


# ---------------- 抽卡：直接贴「游戏内复制的抽卡链接」 ----------------


def parse_gacha_url(url: str) -> dict:
    """校验并拆开一条抽卡链接（用户在游戏里复制的那种完整 URL）。

    返回 {endpoint, params, region, gacha_type}。链接里的所有原始参数都会保留，
    因为服务端认的就是这一整套（含 win_mode / plat_type / init_log_* 等 UI 参数）。
    """
    raw = str(url or "").strip()
    if not raw:
        raise MysError("抽卡链接为空")
    parsed = urllib.parse.urlparse(raw)
    host = parsed.netloc.lower()
    if "public-operation-nap" not in host or "getGachaLog" not in parsed.path:
        raise MysError(
            "不是绝区零的抽卡链接（应为 public-operation-nap.../getGachaLog?...）："
            "请在游戏内「调频记录」页面复制，或用自动查询"
        )
    params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    if not str(params.get("authkey") or ""):
        raise MysError("链接里没有 authkey：请在游戏内重新打开调频记录页后复制完整链接")
    return {
        "endpoint": f"https://{parsed.netloc}{parsed.path}",
        "params": params,
        "region": str(params.get("region") or "prod_gf_cn"),
        "gacha_type": str(params.get("real_gacha_type") or params.get("gacha_type") or "2"),
    }


async def full_gacha_log_by_url(
    url: str, gacha_type: str = "", limit_pages: int = 30
) -> dict:
    """直接查「游戏内复制的抽卡链接」（不经过 genAuthKey，最适合官方收紧后的场景）。

    gacha_type 留空则沿用链接里自带的频段编号。
    """
    info = parse_gacha_url(url)
    want = str(gacha_type or info["gacha_type"])
    items: list = []
    end_id = ""
    for _ in range(max(1, int(limit_pages))):
        data = await _gacha_page(info["endpoint"], info["params"], want, end_id)
        page_list = _list(data.get("list"))
        if not page_list:
            break
        items.extend(page_list)
        end_id = str(_dict(page_list[-1]).get("id") or "")
        if not end_id or len(page_list) < 20:
            break
        await _sleep(0.5)
    return {"list": items, "count": len(items), "gacha_type": want, "region": info["region"]}


async def full_gacha_log(
    uid: str, server: str, gacha_type: str = "2", limit_pages: int = 200,
    account_id=None, authkey: str = "", stop_id: str = "",
) -> dict:
    """拉取某个频段的完整调频记录（翻页直到没有更多 / 遇到已存数据）。

    authkey：调用方已有凭证时直接传入，避免每个频段重复申请一次。
    stop_id：**增量模式游标**（本地已存的最大 item id）。逐页从新到旧拉，
      遇到 id <= stop_id 的记录就地截断并停止翻页 —— 页与页之间 1 秒间隔。

    **判断「到底了」的唯一可靠条件：下一页返回空列表**（见 2026-09-30 的血泪：
    绝区零这个接口某一页只回 5 条是完全正常的，靠 `len(page) < size` 判结束会漏掉
    几百条历史记录。实测 uid=37294090 独家频段首页 5 条 → 判定到底 → 只存了 5 条，
    而实际有 392 条。所以现在一路翻到空页为止，另加两道防呆：
      · end_id 不再前进 → 停（避免死循环）
      · 整页 id 全都见过 → 停（服务端没认 end_id，重复吐同一页）
    返回 {list:[...], count:int, gacha_type:str}。列表按时间倒序。
    """
    if not authkey:
        # 缺 stoken 时这条路必挂（genAuthKey 只认 stoken），先给一句人话，别打一堆必然失败的请求
        if not has_stoken(account_id):
            raise MysError(
                "该账号的登录凭证里没有 stoken，而米游社的 genAuthKey 只认 stoken —— "
                "请删除该账号后重新扫码登录（新登录一次扫码就会带回 stoken）。"
            )
        authkey = await gen_auth_key(uid, server, account_id)
    items: list = []
    end_id = "0"   # 登录凭证路线（ZZZeroUID 同款）首页 end_id 是 "0"
    seen_ids: set[str] = set()
    for _ in range(max(1, int(limit_pages))):
        data = await gacha_log(
            authkey, gacha_type=gacha_type, uid=uid, server=server, end_id=end_id
        )
        page_list = _list(data.get("list"))
        if not page_list:
            break                              # 唯一可靠的「到底」信号

        page_ids = [str(_dict(it).get("id") or "") for it in page_list]
        # 防呆 ①：end_id 没前进（服务端忽略了游标）→ 停止，别原地转圈
        last_id = str(_dict(page_list[-1]).get("id") or "")
        if not last_id or last_id == end_id:
            break
        # 防呆 ②：整页都是已经收过的 id → 服务端在重复吐同一页，停止
        if page_ids and all(pid in seen_ids for pid in page_ids):
            break

        if stop_id:
            # 增量：只留比游标新的；本页出现旧数据就到此为止
            fresh = []
            for it in page_list:
                iid = str(_dict(it).get("id") or "")
                if iid and gacha_store.id_ge(stop_id, iid):
                    break
                fresh.append(it)
            items.extend(fresh)
            if len(fresh) < len(page_list):
                break
        else:
            items.extend(page_list)

        page_ids and seen_ids.update(pid for pid in page_ids if pid)
        end_id = last_id
        await _sleep(GACHA_PAGE_INTERVAL)      # 一个请求处理完，隔 1 秒再发下一个
    return {"list": items, "count": len(items), "gacha_type": gacha_type}


async def sync_gacha(
    uid: str, server: str, account_id=None, force: bool = False
) -> dict:
    """同步某角色全部频段的抽卡记录到 data/zzz/{uid}.json，并返回统计汇总。

    - 首次同步（无存档）或 force=True → **全量**（每个频段翻页到头）；
    - 之后 → **增量**（每频段以本地最大 item id 为游标，只拉新记录）；
    - 页间隔 1s；6 个频段共用一次 gen_auth_key 申请的凭证（authkey 会缓存复用，快速重复点击不会重复申请）。
    返回 {uid, total, fresh, updated_at, **gacha_stats.summarize(...)}，
    另有 per_pool（每频段新增条数）与 errors（失败的频段，可能为空）。

    **单个频段失败不拖垮整次同步**：米游社时不时抽风返回一个频段的风控/签名错误，
    整次失败等于白等好几分钟。这里每段独立兜错 + 失败重试一次（间隔 1.5s），
    全部频段都挂了才抛异常；部分失败时照样落盘 + 出统计，把 errors 交给前端提示。
    """
    if not has_stoken(account_id):
        raise MysError(
            "该账号的登录凭证里没有 stoken，而米游社的 genAuthKey 只认 stoken —— "
            "请删除该账号后重新扫码登录（新登录一次扫码就会带回 stoken）。"
        )
    authkey = await gen_auth_key(uid, server, account_id)

    stored = gacha_store.load(uid) or {"items": []}
    old_items = list(stored.get("items") or [])
    all_new: list[dict] = []
    per_pool: dict[str, int] = {}
    errors: list[str] = []
    for base in GACHA_TYPES:                       # 1 / 2 / 3 / 5 / 102 / 103
        pool_old = [it for it in old_items if gacha_stats.base_type(it) == base]
        # 全量：force 或该频段本地还没有任何数据；增量：以该频段最大 id 为游标
        stop_id = "" if (force or not pool_old) else gacha_store.max_id(pool_old)
        fresh: list = []
        last_err = ""
        for attempt in range(2):                   # 第一次失败 → 歇 1.5s 再试一遍
            try:
                res = await full_gacha_log(
                    uid, server, gacha_type=base, account_id=account_id,
                    authkey=authkey, stop_id=stop_id,
                )
                fresh = res.get("list") or []
                last_err = ""
                break
            except (MysError, httpx.HTTPError) as exc:
                last_err = str(exc)
                # 凭证本身作废 → 再试多少遍都一样，直接止损
                if "authkey" in last_err.lower() or "auth_key" in last_err.lower():
                    break
                if attempt == 0:
                    logger.warning(
                        f"[miyoushe] uid={uid} 频段 {base} 拉取失败，1.5s 后重试：{last_err}"
                    )
                    await _sleep(1.5)
        if last_err:
            per_pool[base] = 0
            errors.append(f"{gacha_stats.POOL_NAMES.get(base, base)}：{last_err}")
        else:
            per_pool[base] = len(fresh)
            all_new.extend(fresh)

    # 一段都没拉到还报错 → 这次同步整体失败（不要把空数据写回去覆盖掉旧存档）
    if errors and len(errors) == len(GACHA_TYPES):
        raise MysError("全部频段拉取失败 ｜ " + " ｜ ".join(errors))
    if not all_new and not old_items:
        raise MysError("这次没有拉到任何抽卡记录 ｜ " + " ｜ ".join(errors or ["无数据"]))

    merged = gacha_store.save(uid, server, old_items + all_new)
    summary = gacha_stats.summarize(merged["items"])
    return {
        "uid": uid,
        "server": server,
        "region_name": region_name(server),
        "fresh": len(all_new),
        "per_pool": per_pool,
        "errors": errors,
        "partial": bool(errors),
        "updated_at": merged.get("updated_at"),
        **summary,
    }


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(max(0.0, seconds))
