"""米游社（miyoushe）账号层：扫码登录 / 凭证 / 绑定角色 / 请求签名与公共头。

2026-10-04 从原 `src/client.py` 拆出来的**公共层**：只留与具体游戏无关的那部分。
绝区零各功能的接口搬到 `zzz/` 下各自目录里（见 `src/__init__.py` 的目录说明）：

    zzz/record/api.py   危局强袭战 / 式舆防卫战 / 零号空洞 / 迷宫诡域 / 实时便笺
    zzz/avatar/api.py   已拥有角色（avatar/basic）/ 角色详情（avatar/info）/ Enka 备用源
    zzz/sign/api.py     每日签到（luna 活动接口）
    zzz/gacha/api.py    调频（抽卡）记录

本文件对外提供：扫码登录、账号凭证（store）、DS 签名、公共请求头（含设备指纹）、
绑定角色查询、服务器名映射。
鉴权要点（2026-09 实测 & 官方 API 收集文档）：
- **绝区零战绩类接口不再需要 DS 签名**，只要 Cookie 有效即可（带上 x-rpc-* 头更稳）。
- **绑定角色接口要 DS**，本项目按「多套伪装依次重试」实现：
  ① 安卓 App（client_type=5，DS1/K2 salt，`salt&t&r&b&q`）→ ② 安卓 App（DS2，`salt&t&r`）
  → ③ 网页（client_type=4，web salt，浏览器 UA）。命中风控/签名类 retcode 就换下一套。
- 米游社接口有风控：**UA 必须是完整的移动端 UA**（Chrome + `miHoYoBBS/版本号` 后缀），
  只发 `miHoYoBBS/2.73.1` 这种裸 UA 会被降级处理（表现为返回数据字段全空）。
- **扫码登录必须用 HYP 容器版**（`passport-api.mihoyo.com/account/ma-cn-passport/app`
  + `x-rpc-app_id: ddxf5dufpuyo` + UA `HYPContainer/1.3.3.182` + client_type=3）。
  只有这一套确认后会返回 `data.tokens`（含 stoken）；旧的 web 版
  （`ma-cn-passport/web` + 米游社 App 的 app_id）**永远只给 ltoken / cookie_token**，
  重登多少次都拿不到 stoken —— 抽卡（genAuthKey）只认它，缺了会一直 retcode -100。
  **一次扫码就够了**：HYP 拿到 stoken 后，登录流程会自动
    ① 用 stoken 换 cookie_token（HYP 的 Set-Cookie 里没有它，战绩/角色接口要用）；
    ② `_fill_cookie_aliases` 按 aid/mid 补全 account_id / ltuid_v2 / account_mid_v2 等
       等价键名 —— takumi 系接口习惯从这些键读身份，不补会被判成「未登录」。
  两件事做完，角色列表 / 战绩 / 抽卡三样都能用。web 版只在这条路失败时兜底。
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

敏感信息（ltoken / cookie_token）只写入本机 data/，绝不回传前端。
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import string
import time
import urllib.parse
import uuid
from email.utils import parsedate_to_datetime

import asyncio
import httpx
from loguru import logger

# ⚠️ device 反过来要用本模块的 get_client() / _base_headers()（拿 fp 时要发请求），
#    所以它在**函数内部**延迟 import 本模块；这里顶层 import 它不会成环。
from . import device, store

# ⚠️ 下面这些下划线开头的函数看着像「私有」，但被 zzz/* 各功能目录 import 使用
#    （get_client / _base_headers / _payload / _record_cookie / _dict …）——
#    跨模块共享，别当成没人用给删了。
PASSPORT = "https://passport-api.miyoushe.com"
PASSPORT_ACCOUNT = PASSPORT + "/account/ma-cn-passport/web"
BIND_API = "https://api-takumi.mihoyo.com/binding/api"
RECORD_API = "https://api-takumi-record.mihoyo.com/event/game_record_zzz/api/zzz"


# HYP 容器版扫码登录（gsuid_core 配方）—— ★ 唯一能拿到 stoken 的登录方式
# 与 web 版的差异（任何一项不对都拿不到 tokens）：
#   host   : passport-api.mihoyo.com（不是 .miyoushe.com）
#   path   : ma-cn-passport/app（不是 /web）
#   app_id : ddxf5dufpuyo（不是米游社 App 的 bll8iq97cem8）
#   UA     : HYPContainer/1.3.3.182
#   client_type: 3，device_id 为 64 位
PASSPORT_HYP = "https://passport-api.mihoyo.com/account/ma-cn-passport/app"
APP_ID_HYP = "ddxf5dufpuyo"
HYP_VERSION = "1.3.3.182"
UA_HYP = f"HYPContainer/{HYP_VERSION}"

# 用 stoken 换 cookie_token（HYP 登录的 Set-Cookie 里没有 cookie_token，战绩接口要用）
COOKIE_TOKEN_API = "https://passport-api.mihoyo.com/account/auth/api/getCookieAccountInfoBySToken"

# 米游社 App 的 app_id（web 版扫码登录用；拿不到 stoken，仅作兜底）
APP_ID = "bll8iq97cem8"

# DS2（web 端）与 DS（客户端 K2）签名 salt
SALT_DS2 = "WGtruoQrwczmsjLOPXzJLnaAYycsLavx"
SALT_DS = "xV8v4Qu54lUKrEYFZkJhB8cuOh9Asafs"
SALT_WEB = "JwYDpKvLj6MrMqqYU6jBCFh3dVkVQX2b"  # client_type=4（网页）的 DS salt
# LK2 salt（对应 gsuid_core 2.102.1 的 "LK2"）：genAuthKey 专用，注意与 DS2 的 salt 不同
SALT_LK2 = "yBh10ikxtLPoIhgwgPZSv5dmfaOTSJ6a"

APP_VERSION = "2.73.1"
# genAuthKey 用的客户端版本（与 gsuid_core 保持一致：2.102.1）
APP_VERSION_TAKUMI = "2.102.1"
# 米游社 App 原生网络库 UA —— genAuthKey 只认这个，发 miHoYoBBS/... 会被判 -100
UA_OKHTTP = "okhttp/4.8.0"

UA = (
    "Mozilla/5.0 (Linux; Android 12; MI 6 Build/REL; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/111.0.5563.116 Mobile Safari/537.36 "
    f"miHoYoBBS/{APP_VERSION}"
)
UA_APP = f"miHoYoBBS/{APP_VERSION}"
UA_WEB = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 绝区零 game_biz
ZZZ_BIZ = "nap_cn"

# Cookie 里要保留的字段（登录成功时从 Set-Cookie 挑）
LOGIN_COOKIE_KEYS = (
    "ltuid", "ltuid_v2", "ltoken", "ltoken_v2", "ltmid", "ltmid_v2",
    "account_id", "account_id_v2", "account_mid", "account_mid_v2",
    "cookie_token", "cookie_token_v2", "login_uid", "login_ticket",
    # stoken / stuid / mid：抽卡 genAuthKey 的必需凭证（老账号没有，需重新扫码登录）
    "stoken", "stoken_v2", "stuid", "stuid_v2", "mid",
)


class MysError(Exception):
    """米游社接口调用失败。"""


# ---------------- 取值兜底 ----------------


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


# ---------------- HTTP 基础 ----------------

_device_id = uuid.uuid4().hex
_device_fp = "".join(random.choices(string.ascii_lowercase + string.digits, k=13))
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10),
            follow_redirects=True,
            headers={"User-Agent": UA},
        )
    return _client


def device_id() -> str:
    """登录 / 二维码用的设备号：配了默认设备就用它（整台「手机」前后一致），
    没配就是启动时随机那个。"""
    dft = device.for_account("")
    if isinstance(dft, dict) and dft.get("device_id"):
        return str(dft["device_id"])
    return _device_id


def _base_headers(
    referer: str = "https://act.mihoyo.com/", *, ua: str = "", origin: str = "",
    account_id=None,
) -> dict:
    """通用伪装头。

    UA 必须是完整移动端 UA（Chrome + miHoYoBBS 后缀）——裸 `miHoYoBBS/x` 会被风控降级。

    `account_id`：给了就用「设备配置」里那个账号（或默认）的设备头覆盖下面这
    几个 x-rpc-device_* —— 角色类接口（avatar/basic 等）就靠这个过设备指纹校验
    （详见 src/device.py 的说明）。没配设备就用启动时随机那套，行为与以前一致。
    """
    return {
        "User-Agent": ua or UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-device_id": _device_id,
        "x-rpc-device_fp": _device_fp,
        "x-rpc-device_name": "MI 6",
        "x-rpc-device_model": "MI 6",
        "x-rpc-sys_version": "12",
        "x-rpc-channel": "mihoyo",
        "x-rpc-platform": "android",
        "Referer": referer,
        "Origin": origin or "https://act.mihoyo.com",
        **device.headers_for(account_id),
    }


def _ds(query: str = "", body: str = "") -> str:
    """客户端 K2 salt 的 DS1 签名（salt&t&r&b&q）。"""
    t = int(time.time())
    r = random.randint(100000, 999999)
    raw = f"salt={SALT_DS}&t={t}&r={r}&b={body}&q={query}"
    return f"{t},{r},{hashlib.md5(raw.encode()).hexdigest()}"


def _ds2() -> str:
    """DS2 签名（salt&t&r，安卓 client_type=5 部分接口用）。"""
    t = int(time.time())
    r = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    raw = f"salt={SALT_DS2}&t={t}&r={r}"
    return f"{t},{r},{hashlib.md5(raw.encode()).hexdigest()}"


def _ds_web() -> str:
    """网页端 DS 签名（client_type=4，salt&t&r）。"""
    t = int(time.time())
    r = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    raw = f"salt={SALT_WEB}&t={t}&r={r}"
    return f"{t},{r},{hashlib.md5(raw.encode()).hexdigest()}"


def _ds_lk2() -> str:
    """genAuthKey 用的 DS：**LK2 salt + 不含 body/query**（即 DS2 形式）。

    对照 gsuid_core：`get_web_ds_token(True)` → `_random_str_ds(LK2)`，
    而 `_random_str_ds` 默认 `with_body=False`，所以拼的是 `salt={LK2}&t={t}&r={r}`。
    （早期这里误用 DS1 把 body 拼进去、且 salt 用错，是 -100 的直接原因之一。）
    """
    t = int(time.time())
    r = "".join(random.sample(string.ascii_lowercase + string.digits, 6))
    raw = f"salt={SALT_LK2}&t={t}&r={r}"
    return f"{t},{r},{hashlib.md5(raw.encode()).hexdigest()}"


def _random_hex(n: int) -> str:
    """gsuid_core tools.random_hex 的等价实现（大写、左补零）。"""
    import random as _r

    s = hex(_r.randint(0, 16**n)).replace("0x", "").upper()
    return s.rjust(n, "0")


def _payload(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise MysError(f"接口返回非 JSON（HTTP {resp.status_code}）")
    if not isinstance(data, dict):
        raise MysError(f"接口返回格式异常（HTTP {resp.status_code}）")
    code = _int(data.get("retcode"), 0)
    if code != 0:
        raise MysError(f"{data.get('message') or '接口调用失败'}（retcode={code}）")
    return data


def _retcode_payload(resp: httpx.Response) -> tuple[int, dict]:
    """同 `_payload`，但**不抛业务异常**，把 retcode 交回调用方。

    给「可降级」的接口用：如 avatar/basic 的 10041 风控要转走备用源，
    调用方需要拿到码而不是异常。
    """
    try:
        data = resp.json()
    except ValueError:
        raise MysError(f"接口返回非 JSON（HTTP {resp.status_code}）")
    if not isinstance(data, dict):
        raise MysError(f"接口返回格式异常（HTTP {resp.status_code}）")
    return _int(data.get("retcode"), 0), _dict(data.get("data"))


# ---------------- 二维码登录 ----------------


def _qr_ready() -> bool:
    try:
        import segno  # noqa: F401

        return True
    except ImportError:
        return False


def qr_png(text: str) -> bytes | None:
    """把登录链接渲染成二维码 PNG（依赖 segno，缺失返回 None）。"""
    try:
        import segno
    except ImportError:
        return None
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=8, border=2)
    return buf.getvalue()


def _hyp_headers(device_id: str) -> dict:
    """HYP 容器扫码登录专用头（对照 gsuid_core AccountMysApi._hyp_qrcode_header）。

    服务端靠 app_id + UA + client_type 判断「这是 HYP 容器登录」，只有这种情况下
    扫码确认后的 `data` 里才会带 `tokens`（含 stoken）。换成 web 路径或米游社 App
    的 app_id，一律只给 ltoken / cookie_token —— 抽卡就会一直 retcode -100。
    """
    return {
        "x-rpc-device_id": device_id,
        "User-Agent": UA_HYP,
        "x-rpc-app_id": APP_ID_HYP,
        "x-rpc-client_type": "3",
        "x-rpc-app_version": HYP_VERSION,
        "Accept": "application/json, text/plain, */*",
    }


# ticket → {mode, device}。二维码只有 3 分钟有效期，内存缓存足够（重启后重新扫码即可）
_qr_sessions: dict[str, dict] = {}


def _qr_headers() -> dict:
    return {
        "User-Agent": UA_APP,
        "x-rpc-app_id": APP_ID,
        "x-rpc-device_id": _device_id,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://user.mihoyo.com/",
        "Origin": "https://user.mihoyo.com",
    }


async def login_start(mode: str = "hyp") -> dict:
    """申请登录二维码。返回 {ticket, url, expires_in, mode}。

    **默认 mode="hyp"（HYP 容器版），一次扫码拿全所有凭证**：
      它确认后会在 `data.tokens` 里返回 stoken（抽卡 genAuthKey 必需），
      登录流程再自动用 stoken 换 cookie_token，并用 aid/mid 补全等价键名
      （account_id / ltuid_v2 / cookie_token 等）—— 角色列表、战绩、抽卡三样都能用。
      这就是上游 gsuid_core 的做法（cookie_manager/qrlogin.py），扫一次即够。

    mode="web" 仅作为**兜底**：HYP 接口不可用/返回异常时自动回退。
      web 版只发 ltoken_v2 / cookie_token_v2，**永远不发 stoken**，
      落到这条分支时抽卡会提示需要「抽卡授权」补一次。

    两种 mode 的凭证都按 aid 合并保存（store.save_account），不会互相冲掉。
    """
    if mode == "hyp":
        device = uuid.uuid4().hex + uuid.uuid4().hex
        try:
            resp = await get_client().post(
                PASSPORT_HYP + "/createQRLogin", headers=_hyp_headers(device), json={}
            )
            data = _dict(_payload(resp).get("data"))
            ticket = str(data.get("ticket") or "")
            url = str(data.get("url") or "")
            if ticket and url:
                _qr_sessions[ticket] = {"mode": "hyp", "device": device}
                return {"ticket": ticket, "url": url, "expires_in": 180, "mode": "hyp"}
            logger.warning("[miyoushe] HYP 二维码返回缺少 ticket/url，回退 web 版")
        except (httpx.HTTPError, MysError) as exc:
            logger.warning(f"[miyoushe] HYP 二维码申请失败，回退 web 版：{exc}")

    resp = await get_client().post(
        PASSPORT_ACCOUNT + "/createQRLogin", headers=_qr_headers()
    )
    data = _dict(_payload(resp).get("data"))
    ticket = str(data.get("ticket") or "")
    url = str(data.get("url") or "")
    if not ticket or not url:
        raise MysError("申请登录二维码失败：返回缺少 ticket / url")
    _qr_sessions[ticket] = {"mode": "web", "device": _device_id}
    return {"ticket": ticket, "url": url, "expires_in": 180, "mode": "web"}


def _parse_set_cookies(headers: list[str]) -> dict:
    out: dict = {}
    for raw in headers or []:
        first = raw.split(";", 1)[0].strip()
        if "=" not in first:
            continue
        name, _, value = first.partition("=")
        name, value = name.strip(), value.strip().strip('"')
        if name in LOGIN_COOKIE_KEYS and value and value != "deleted":
            out[name] = value
    return out


def _cookie_expires(headers: list[str], name: str = "ltoken_v2") -> int:
    for raw in headers or []:
        first = raw.split(";", 1)[0].strip()
        if not first.lower().startswith(name.lower() + "="):
            continue
        for part in raw.split(";")[1:]:
            key, _, value = part.partition("=")
            if key.strip().lower() in ("expires", "max-age"):
                if key.strip().lower() == "max-age":
                    try:
                        return int(time.time()) + int(value.strip())
                    except (TypeError, ValueError):
                        return 0
                try:
                    return int(parsedate_to_datetime(value.strip()).timestamp())
                except (TypeError, ValueError, OverflowError):
                    return 0
    return 0


async def cookie_token_by_stoken(stoken: str, mys_id: str, full_sk: str = "") -> str:
    """用 stoken 换 cookie_token（对照 gsuid_core get_cookie_token_by_stoken_cn）。

    HYP 扫码登录只给 stoken / stuid / mid，Set-Cookie 里没有 cookie_token，
    而绝区零战绩类接口要它，所以登录成功后补这一步。失败只记日志，不影响登录。
    """
    params = {"stoken": stoken, "uid": mys_id}
    try:
        resp = await get_client().get(
            COOKIE_TOKEN_API,
            params=params,
            headers={
                **_base_headers(
                    "https://user.mihoyo.com/", origin="https://user.mihoyo.com"
                ),
                "Cookie": full_sk or f"stuid={mys_id};stoken={stoken}",
            },
        )
        data = _dict(_payload(resp).get("data"))
    except (httpx.HTTPError, MysError) as exc:
        logger.debug(f"[miyoushe] stoken 换 cookie_token 失败：{exc}")
        return ""
    return str(data.get("cookie_token") or "")


async def login_poll(ticket: str) -> dict:
    """轮询扫码状态；确认后落盘凭证并返回 status=success。"""
    sess = _qr_sessions.get(ticket) or {}
    mode = str(sess.get("mode") or "hyp")
    device = str(sess.get("device") or _device_id)
    if mode == "hyp":
        url = PASSPORT_HYP + "/queryQRLoginStatus"
        headers = {**_hyp_headers(device), "Content-Type": "application/json"}
    else:
        url = PASSPORT_ACCOUNT + "/queryQRLoginStatus"
        headers = {**_qr_headers(), "Content-Type": "application/json"}

    resp = await get_client().post(url, headers=headers, json={"ticket": ticket})
    try:
        payload = resp.json()
    except ValueError:
        raise MysError(f"轮询接口返回非 JSON（HTTP {resp.status_code}）")
    retcode = _int(payload.get("retcode"), 0)
    message = str(payload.get("message") or "")
    if retcode == -3501:
        return {"status": "expired", "message": "二维码已失效，请重新获取"}
    if retcode == -3505:
        return {"status": "canceled", "message": "已取消扫码，请重新获取"}
    if retcode != 0:
        # HYP 版二维码过期也走 retcode != 0（gsuid_core 就是据此判断失效的），
        # 不该让前端弹错误，直接按「已失效」处理
        if mode == "hyp" and ("过期" in message or "失效" in message or retcode in (-3506, -3507)):
            _qr_sessions.pop(ticket, None)
            return {"status": "expired", "message": f"{message or '二维码已失效'}，请重新获取"}
        raise MysError(f"{message or '轮询失败'}（retcode={retcode}）")

    data = _dict(payload.get("data"))
    status = str(data.get("status") or "")
    if status in ("Created", "New", ""):
        return {"status": "waiting", "message": "等待扫码…"}
    if status == "Scanned":
        return {"status": "scanned", "message": "已扫码，请在手机上确认"}
    if status in ("Expired", "Timeout"):
        _qr_sessions.pop(ticket, None)
        return {"status": "expired", "message": "二维码已失效，请重新获取"}
    if status == "Canceled":
        return {"status": "canceled", "message": "已取消扫码，请重新获取"}
    if status != "Confirmed":
        return {"status": "error", "message": f"未知扫码状态：{status}"}

    # 已确认：web 版凭证在 Set-Cookie，HYP 版的 stoken 在 data.tokens
    set_cookies = resp.headers.get_list("Set-Cookie")
    cookie = _parse_set_cookies(set_cookies)

    user_info = _dict(data.get("user_info"))
    aid = str(
        user_info.get("aid") or user_info.get("uid") or user_info.get("account_id")
        or cookie.get("account_id_v2") or cookie.get("account_id") or ""
    )
    mid = str(user_info.get("mid") or cookie.get("account_mid_v2") or aid)
    if not aid:
        raise MysError("登录成功但取不到 account_id，请重试")

    # 该 aid 之前是否已存在（决定这次是「正式登录」还是「给已有账号补 stoken」）
    existed = store.get(aid) is not None
    prev_ck = store.cookie(aid) if existed else {}

    # ★ stoken：抽卡（genAuthKey）**必须要它**，ltoken/cookie_token 不够 —— 缺了会一直 -100。
    # HYP 版扫码确认后的 data.tokens 是 [{name, token}, ...]，挑 name 为 stoken / stoken_v2 的那条。
    # （对照 gsuid_core/cookie_manager/qrlogin.py：app_cookie = stuid=..;stoken=..;mid=..）
    stoken = ""
    tokens = _list(data.get("tokens"))
    for item in tokens:
        item = _dict(item)
        if str(item.get("name") or "") in ("stoken", "stoken_v2"):
            stoken = str(item.get("token") or "")
            break
    if not stoken and tokens:
        stoken = str(_dict(tokens[0]).get("token") or "")

    if not stoken and not (cookie.get("ltoken_v2") or cookie.get("cookie_token")):
        logger.debug(f"[miyoushe] 扫码确认但没有任何凭证，data keys={list(data.keys())}")
        raise MysError("扫码已确认，但没拿到任何凭证：请重新扫码")

    if stoken:
        cookie["stoken"] = stoken
        # HYP 版的 Set-Cookie 里没有 cookie_token，而角色列表 / 战绩接口要它 → 用 stoken 换。
        # 已有账号里存着的话就不重复换（多半是之前正式登录拿到的）
        has_ct = any(
            ck.get(k) for ck in (cookie, prev_ck)
            for k in ("cookie_token", "cookie_token_v2")
        )
        if not has_ct:
            ct = await cookie_token_by_stoken(
                stoken, aid, f"stuid={aid};stoken={stoken};mid={mid}"
            )
            if ct:
                cookie["cookie_token"] = ct
            else:
                logger.warning(
                    "[miyoushe] stoken 换 cookie_token 失败（aid=%s）："
                    "角色列表 / 战绩可能因缺 cookie_token 不可用", aid
                )
    else:
        logger.warning(
            "扫码登录未取到 stoken（mode=%s，data keys=%s，tokens=%d）："
            "抽卡记录将无法查询，其余功能不受影响",
            mode, list(data.keys()), len(tokens),
        )
    # ★ 补全等价键名（两种 mode 都做）：takumi 系接口习惯从 account_id / ltuid_v2 /
    #   account_mid_v2 / cookie_token_v2 这些键读身份，而 HYP 只给 stoken / stuid / mid。
    #   不补就会出现「明明有凭证，接口却认为没登录」—— 这正是当初误判成
    #   「HYP 不能查角色、必须扫两次」的真正原因。
    _fill_cookie_aliases(cookie, aid, mid)
    _qr_sessions.pop(ticket, None)

    profile = {
        "mid": mid,
        "nickname": str(user_info.get("nickname") or ""),
        "face": str(user_info.get("head_icon") or user_info.get("face") or ""),
        "level": _int(user_info.get("level"), 0),
    }
    store.save_account(aid, cookie, profile, _cookie_expires(set_cookies))

    # 登录后补一次米游社用户信息（拿昵称/头像，失败不影响登录）
    try:
        info = await fetch_user_info(aid)
        if info:
            store.update_profile(aid, info)
    except Exception as exc:
        logger.debug(f"米游社登录后补充用户信息失败：{exc}")

    acc = store.get(aid) or {}
    ok_stoken = bool(stoken)
    name = acc.get("nickname") or aid
    ck_now = store.cookie(aid)
    ok_ct = bool(ck_now.get("cookie_token") or ck_now.get("cookie_token_v2"))
    if ok_stoken and ok_ct:
        message = f"已登录：{name}（凭证完整，角色 / 战绩 / 抽卡都可直接查）"
    elif ok_stoken:
        message = (
            f"已登录：{name}，但没换到 cookie_token —— 角色列表 / 战绩可能查不到，"
            "建议重新扫码登录一次"
        )
    else:
        message = (
            f"已登录：{name}（本次二维码未返回 stoken，抽卡需点「抽卡授权」补一次；"
            "角色 / 战绩不受影响）"
        )
    return {
        "status": "success",
        "message": message,
        "account_id": aid,
        "has_stoken": ok_stoken,
        "login_mode": mode,
        # 换不到 cookie_token 的账号：角色 / 战绩会不可用，提醒前端重登一次
        "need_login": bool(not ok_ct),
        "user": {
            "account_id": aid,
            "mid": acc.get("mid") or mid,
            "nickname": acc.get("nickname") or "",
            "face": acc.get("face") or "",
            "level": acc.get("level") or 0,
        },
    }


async def fetch_user_info(account_id) -> dict:
    """取米游社用户昵称 / 头像（bbs-api 的 user info）。"""
    ck = store.cookie(account_id)
    if not ck:
        return {}
    headers = {
        **_base_headers("https://www.miyoushe.com/"),
        "Cookie": "; ".join(f"{k}={v}" for k, v in ck.items()),
        "x-rpc-client_type": "2",
        "DS": _ds(),
    }
    try:
        resp = await get_client().get(
            "https://bbs-api.miyoushe.com/user/api/getUserFullInfo",
            params={"uid": account_id},
            headers=headers,
        )
        data = _dict(_payload(resp).get("data"))
    except Exception:
        return {}
    user = _dict(data.get("user_info"))
    if not user:
        return {}
    return {
        "mid": str(user.get("uid") or account_id),
        "nickname": str(user.get("nickname") or ""),
        "face": str(user.get("head_icon") or ""),
        "level": _int(user.get("level_exp", {}).get("level") if isinstance(user.get("level_exp"), dict) else user.get("level"), 0),
    }


# ---------------- 绑定角色（绝区零） ----------------

# 命中这些 retcode 时换下一套伪装重试（签名/登录态/风控类错误）
_RETRY_RETCODES = {-1, -100, -106, 1009, 1034, 10035}


def _parse_roles(data) -> list[dict]:
    """解析绑定角色列表（兼容 data 直接是列表 / data.list / data.role_list）。"""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("list") or data.get("role_list") or []
    else:
        items = []
    if not isinstance(items, list):
        items = []
    roles: list[dict] = []
    for item in items:
        item = item if isinstance(item, dict) else {}
        biz = str(item.get("game_biz") or "")
        if biz and biz != ZZZ_BIZ:
            continue
        roles.append(
            {
                "game_biz": biz,
                "region": str(item.get("region") or ""),
                "game_uid": str(item.get("game_uid") or ""),
                "nickname": str(item.get("nickname") or ""),
                "level": _int(item.get("level"), 0),
                "region_name": str(item.get("region_name") or ""),
                "is_chosen": bool(item.get("is_chosen")),
            }
        )
    return roles


async def bind_roles(account_id=None) -> list[dict]:
    """拉取该账号绑定的绝区零角色列表。

    返回 [{game_biz, region, game_uid, nickname, level, region_name, is_chosen}]。

    风控对策：同一接口按「安卓 App(DS1/K2) → 安卓 App(DS2) → 网页(web salt)」
    三套伪装依次尝试；命中风控/签名类 retcode 或「retcode=0 但列表为空」时换下一套。
    """
    ck = store.cookie(account_id)
    _require_combo_cookie(ck)
    cookie = "; ".join(f"{k}={v}" for k, v in ck.items())
    params = {"game_biz": ZZZ_BIZ}
    qs = urllib.parse.urlencode(params)
    url = BIND_API + "/getUserGameRolesByCookie?" + qs

    attempts: list[dict] = [
        {
            **_base_headers("https://app.mihoyo.com/"),
            "Cookie": cookie,
            "x-rpc-client_type": "5",
            "DS": _ds(qs, ""),
        },
        {
            **_base_headers("https://app.mihoyo.com/"),
            "Cookie": cookie,
            "x-rpc-client_type": "5",
            "DS": _ds2(),
        },
        {
            **_base_headers(
                "https://user.mihoyo.com/", ua=UA_WEB, origin="https://user.mihoyo.com"
            ),
            "Cookie": cookie,
            "x-rpc-client_type": "4",
            "DS": _ds_web(),
        },
    ]

    last_payload: dict = {}
    for idx, headers in enumerate(attempts, 1):
        resp = await get_client().get(url, headers=headers)
        try:
            payload = resp.json()
        except ValueError:
            raise MysError(f"接口返回非 JSON（HTTP {resp.status_code}）")
        if not isinstance(payload, dict):
            raise MysError(f"接口返回格式异常（HTTP {resp.status_code}）")
        last_payload = payload
        code = _int(payload.get("retcode"), 0)
        logger.debug(
            f"[miyoushe] bind_roles 伪装#{idx} retcode={code} "
            f"body={resp.text[:200]!r}"
        )
        if code == 0:
            roles = _parse_roles(payload.get("data"))
            if roles or idx == len(attempts):
                if roles and all(not (r["nickname"] or r["game_uid"]) for r in roles):
                    # 字段全空 = 典型的风控降级响应，别让页面显示一排空行
                    raise MysError(
                        "角色列表返回内容异常（字段全空，疑似被风控降级）："
                        + json.dumps(payload, ensure_ascii=False)[:300]
                    )
                return roles
            continue  # retcode=0 但空列表也可能是降级响应，换下一套
        if code in _RETRY_RETCODES:
            continue
        raise MysError(f"{payload.get('message') or '接口调用失败'}（retcode={code}）")

    code = _int(last_payload.get("retcode"), 0)
    raise MysError(
        f"{last_payload.get('message') or '读取角色失败'}（retcode={code}，已尝试 {len(attempts)} 套请求伪装）"
    )


# ---------------- 绝区零战绩 ----------------

# 服务器名映射（region → 中文）
REGION_NAMES = {
    "prod_gf_cn": "国服",
    "prod_gf_us": "美服",
    "prod_gf_eu": "欧服",
    "prod_gf_jp": "日服",
    "prod_gf_sg": "亚服",
}


def region_name(server: str) -> str:
    return REGION_NAMES.get(str(server or ""), str(server or ""))


def _fill_cookie_aliases(cookie: dict, aid: str, mid: str) -> None:
    """把登录拿到的少量凭证补全成接口认识的**等价键名集合**。

    HYP 扫码只给 stoken / stuid / mid（cookie_token 还要用 stoken 换），而 takumi 系
    接口习惯从 account_id / ltuid_v2 / account_mid_v2 / cookie_token_v2 这些键读身份。
    不补齐就会出现「明明有凭证，接口却认为没登录」——这正是当初误判成
    「HYP 无法查角色、必须扫两次」的真正原因（gsuid_core 就是靠补齐后的这套跑的）。

    cookie_token 的 v1/v2 由 token 自身前缀决定，不重复写，避免两份值互相打架。
    """
    if aid:
        for key in ("stuid", "account_id", "account_id_v2", "ltuid", "ltuid_v2"):
            cookie.setdefault(key, aid)
    if mid:
        for key in ("mid", "account_mid_v2", "ltmid_v2"):
            cookie.setdefault(key, mid)

    ct = cookie.get("cookie_token") or cookie.get("cookie_token_v2") or ""
    if not ct:
        return
    if ct.startswith("v2_"):
        cookie["cookie_token_v2"] = ct
        cookie.pop("cookie_token", None)
    else:
        cookie.setdefault("cookie_token", ct)


def _require_combo_cookie(ck: dict) -> None:
    """角色列表 / 战绩接口的前置凭证检查。

    只要有 cookie_token（v1/v2 均可）或 ltoken 之一就算完整 —— 无论它来自
    正式登录（web）还是 HYP 扫码后换来的。**只有 stoken 才是真残缺**，
    那种情况多半是 HYP 换 cookie_token 那步失败了。
    """
    if not ck:
        raise MysError("该账号未登录或凭证缺失")
    if not (
        ck.get("ltoken_v2") or ck.get("ltoken")
        or ck.get("cookie_token_v2") or ck.get("cookie_token")
    ):
        raise MysError(
            "该账号只有抽卡凭证（stoken），缺少 cookie_token / ltoken："
            "请重新扫码登录一次（原有 stoken 会自动保留）"
        )


def _record_cookie(account_id) -> str:
    _require_combo_cookie(store.cookie(account_id))
    return "; ".join(f"{k}={v}" for k, v in store.cookie(account_id).items())
