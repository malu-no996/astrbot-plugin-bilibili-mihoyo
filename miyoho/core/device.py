"""设备指纹（device_fp）配置：把「真机信息」换成米游社认的一台设备。

干什么用
--------
绝区零的**角色类接口**（`/avatar/basic` 拥有代理人、`/avatar/info` 角色详情、`/index`、
`/note`）带设备指纹风控：请求不像「这个账号真实登录过的设备」时一律 `retcode=10041`
（危局 / 防卫战 / 邦布不受影响，所以那些一直能查）。

米游社的规矩是：拿真机信息去 `device-fp/api/getFp` 换一个 `device_fp`，再用账号 Cookie
把这个设备**登记到账号名下**（`deviceLogin` + `saveDevice`），之后带
`x-rpc-device_id` + `x-rpc-device_fp` 请求，风控才放行。

本文件就是这件事的**存储 + 生成 + 登记**：

    parse_input()     页面/命令粘贴的七字段 JSON → 规范化
    fetch_fp()        七字段 → getFp 接口 → (device_id, device_fp)
    register_device() 用账号 Cookie 把这台设备登记到该账号（deviceLogin + saveDevice）
    headers_for()     请求时该带哪些 x-rpc-device_* 头（按账号取，没有就用默认那份）

字段怎么变成参数的（照抄早柚核心 gsuid_core）
--------------------------------------------
`gsuid_core/utils/cookie_manager/add_fp.py` 的 `deal_fp()` 是入口，它把七字段喂给
`base_request.py` 的 `generate_fp()`；对照关系（**参数名对不上，别按字面猜**）：

    deviceModel        → ext_fields.model          （型号，也用于 x-rpc-device_model）
    deviceProduct      → ext_fields.productName    （产品名）
    deviceName         → ext_fields.deviceType     （注意：是型号代号，不是「设备名」）
    deviceBoard        → ext_fields.board
    oaid               → ext_fields.oaid
    deviceFingerprint  → ext_fields.deviceInfo     （brand/manufacturer 取它 split("/")[0]）
    androidVersion     → 上游没用（osVersion 写死 "14"）；我们拿它填 x-rpc-sys_version

    device_id = 本地 uuid4()（**不是**手机给的，谁都可以随机造）
    seed_id   = 本地 uuid4()   seed_time = 毫秒时间戳
    body 里另有一个 device_id = 16 位随机 hex、device_fp = 13 位随机 hex（请求前的临时值）
    platform  = "2"（安卓）  app_name = "bbs_cn"  bbs_device_id = 上面那个 uuid4

剩下二十来个 ext_fields 字段（romCapacity / screenSize / vendor / accelerometer …）上游全是
**硬编码或随机数**，不是真机值 —— 照抄，别自作聪明改成「更真实」的值。

数据归属
--------
`data/miyoushe_device.json`：

    {"default": 设备对象|null,          ← 一份数据给所有账号用
     "accounts": {"<aid>": 设备对象}}   ← 某个米游社账号单独一份（优先于 default）

取用时先查 accounts[aid]，没有再退回 default —— 所以「先配一份默认的试试，某个账号
不行再单独配」这个顺序是天然支持的。
"""
from __future__ import annotations

import hashlib
import json
import random
import string
import threading
import time
import uuid
from pathlib import Path

from loguru import logger

from ..paths import data_path

_FILE = data_path("miyoushe_devices.json")

_lock = threading.RLock()
_state: dict = {"default": None, "accounts": {}}

# 页面上要人填的七个字段（顺序 = 表单顺序，与手机端导出的一致）
FIELDS: tuple[str, ...] = (
    "deviceModel",
    "androidVersion",
    "deviceFingerprint",
    "deviceName",
    "deviceBoard",
    "deviceProduct",
    "oaid",
)

GET_FP_URL = "https://public-data-api.mihoyo.com/device-fp/api/getFp"
DEVICE_LOGIN_URL = "https://bbs-api.miyoushe.com/apihub/api/deviceLogin"
SAVE_DEVICE_URL = "https://bbs-api.miyoushe.com/apihub/api/saveDevice"

# passport 系 DS 的 salt（gsuid_core tools.py 里的 "PD"）—— 与战绩接口的 K2 salt **不是**一个
SALT_PASSPORT = "JwYDpKvLj6MrMqqYU6jTKF17KNO2PXoS"


# ---------------- 存取 ----------------


def _load() -> None:
    global _state
    try:
        if _FILE.exists():
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                accs = data.get("accounts")
                _state = {
                    "default": data.get("default") if isinstance(data.get("default"), dict) else None,
                    "accounts": accs if isinstance(accs, dict) else {},
                }
    except (OSError, json.JSONDecodeError):
        pass


def _save() -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        logger.warning(f"[miyoushe] 设备配置写入失败：{exc}")


_load()


def load_all() -> dict:
    """整份配置（default + accounts）。"""
    with _lock:
        return json.loads(json.dumps(_state, ensure_ascii=False))


def _entry_of(scope: str, account_id: str = "") -> dict | None:
    """`entry()` 的别名：`client.device_id()` 那边按这个名字调的，两个名字一个意思。"""
    return entry(scope, account_id)


def entry(scope: str, account_id: str = "") -> dict | None:
    """取一份设备配置。scope='account' 时按 account_id 取，否则取 default。"""
    with _lock:
        if scope == "account" and str(account_id or "").strip():
            got = _state["accounts"].get(str(account_id))
            return got if isinstance(got, dict) else None
        got = _state.get("default")
        return got if isinstance(got, dict) else None


def set_entry(scope: str, dev: dict, account_id: str = "") -> None:
    with _lock:
        if scope == "account" and str(account_id or "").strip():
            _state["accounts"][str(account_id)] = dev
        else:
            _state["default"] = dev
        _save()


def del_entry(scope: str, account_id: str = "") -> bool:
    with _lock:
        if scope == "account" and str(account_id or "").strip():
            existed = _state["accounts"].pop(str(account_id), None) is not None
        else:
            existed = _state.get("default") is not None
            _state["default"] = None
        if existed:
            _save()
        return existed


def for_account(account_id: str = "") -> dict | None:
    """请求时该用哪份：先账号专属，再默认那份；`on` 为 False 的那份当没有。

    `on` 是页面上的「启用」开关：关掉就退回随机设备头（用来对比「配了设备到底
    有没有用」，不用删掉重配）。
    """
    own = entry("account", account_id)
    if isinstance(own, dict) and own.get("on", True):
        return own
    dft = entry("default")
    if isinstance(dft, dict) and dft.get("on", True):
        return dft
    return None


# ---------------- 七字段解析 ----------------


def parse_input(raw) -> tuple[dict | None, str]:
    """把粘贴进来的东西规范化成七字段字典。

    接受 JSON 字符串、对象、或 `k=v` 一行一个的文本；缺字段不报错（上游全靠 deviceFingerprint
    取 brand，其它空着也能发出去，只是不像真机）。
    """
    data: dict = {}
    if isinstance(raw, dict):
        data = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        text = raw.strip()
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            for line in text.replace(",", "\n").splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    data[k.strip()] = v.strip().strip('"').strip("'")
    if not data:
        return None, "内容为空：把手机端导出的那串 JSON 粘进来"

    info = {k: str(data.get(k) or "").strip() for k in FIELDS}
    if not info["deviceFingerprint"]:
        return None, "缺少 deviceFingerprint：厂家/型号就是从它里取的，没有它这台设备等于没填"
    return info, ""


# ---------------- 生成 fp ----------------


def _rand_hex(n: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=n))


def _ext_fields(info: dict) -> str:
    """七字段 → ext_fields（getFp 请求体里那坨「设备信息」JSON 字符串）。

    ⚠️ 除了七个字段，其余全部照抄 gsuid_core 的硬编码/随机值：这批值米游社只做
    「像不像一台安卓机」的判断，改动反而可能触发别的风控。
    """
    model = info.get("deviceModel") or ""
    product = info.get("deviceProduct") or ""
    dev_type = info.get("deviceName") or ""
    board = info.get("deviceBoard") or ""
    oaid = info.get("oaid") or ""
    device_info = info.get("deviceFingerprint") or ""
    brand = device_info.split("/")[0] if device_info else ""
    os_version = info.get("androidVersion") or "14"

    rnd1 = random.randint(400000, 600000)
    rnd2 = random.randint(150000, 300000)
    now_ms = int(time.time() * 1000)

    return json.dumps(
        {
            "proxyStatus": 0,
            "isRoot": 1,
            "romCapacity": "512",
            "deviceName": "私人手机",
            "productName": product,
            "romRemain": "491",
            "hostname": "dg02-pool06-kvm82",
            "screenSize": "1264x2640",
            "isTablet": 0,
            "aaid": _rand_id(64),
            "model": model,
            "brand": brand,
            "hardware": "qcom",
            "deviceType": dev_type,
            "devId": "REL",
            "serialNumber": "unknown",
            "sdCapacity": rnd1,
            "buildTime": "1717740969000",
            "buildUser": "root",
            "simState": 5,
            "ramRemain": f"{rnd2}",
            "appUpdateTimeDiff": now_ms,
            "deviceInfo": device_info,
            "vaid": _rand_id(64),
            "buildType": "user",
            "sdkVersion": "34",
            "ui_mode": "UI_MODE_TYPE_NORMAL",
            "isMockLocation": 0,
            "cpuType": "arm64-v8a",
            "isAirMode": 0,
            "ringMode": 1,
            "chargeStatus": 1,
            "manufacturer": brand,
            "emulatorStatus": 0,
            "appMemory": "512",
            "osVersion": os_version,
            "vendor": "中国联通",
            "accelerometer": "-1.3004991x6.38764x7.19103",
            "sdRemain": rnd2,
            "buildTags": "release-keys",
            "packageName": "com.mihoyo.hyperion",
            "networkType": "WiFi",
            "oaid": oaid,
            "debugStatus": 1,
            "ramCapacity": f"{rnd1}",
            "magnetometer": "27.1084x-48.5804x-24.8758",
            "display": f"{model}_14.0.0.810(CN01)",
            "appInstallTimeDiff": f"{now_ms}",
            "packageVersion": "2.20.2",
            "gyroscope": "-0.02543317x0.005725792x0.003195791",
            "batteryStatus": 50,
            "hasKeyboard": 0,
            "board": board,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _rand_id(length: int = 64) -> str:
    """aaid / vaid：gsuid 里叫 generate_ID，大写字母+数字的 64 位串。"""
    return "".join(random.choices(string.digits + string.ascii_uppercase, k=length))


def _ds_passport(body: dict) -> str:
    """passport 系 DS（salt&t&r&b&q）—— deviceLogin / saveDevice 用。

    与战绩接口的 DS 不是同一套：r 是 **6 位字母**（不重复取样），b 是 json.dumps(body)
    的默认格式。照抄 gsuid_core `_random_str_ds(with_body=True)`。
    """
    t = int(time.time())
    r = "".join(random.sample(string.ascii_letters, 6))
    raw = f"salt={SALT_PASSPORT}&t={t}&r={r}&b={json.dumps(body)}&q="
    return f"{t},{r},{hashlib.md5(raw.encode()).hexdigest()}"


async def fetch_fp(info: dict) -> tuple[bool, str, dict]:
    """七字段 → 调 getFp 换 device_id + device_fp。

    返回 (成功没, 人话, 设备对象)。失败时设备对象里 fp 为空 —— **不要**拿随机串顶替，
    随机 fp 请求风控更狠（这正是我们原来一直 10041 的原因）。
    """
    from . import mys as client  # 延迟导入：mys 顶层要 import 本模块拿请求头，会成环

    device_id = str(uuid.uuid4()).lower()
    seed_id = str(uuid.uuid4()).lower()
    seed_time = str(int(time.time() * 1000))

    body = {
        "device_id": _rand_hex(16),
        "seed_id": seed_id,
        "platform": "2",
        "seed_time": seed_time,
        "ext_fields": _ext_fields(info),
        "app_name": "bbs_cn",
        "bbs_device_id": device_id,
        "device_fp": _rand_hex(13),
    }
    headers = {
        **client._base_headers("https://app.mihoyo.com/"),
        "Content-Type": "application/json",
    }
    try:
        resp = await client.get_client().post(GET_FP_URL, json=body, headers=headers)
        data = resp.json() if resp.content else {}
    except Exception as exc:  # noqa: BLE001
        return False, f"请求设备指纹接口失败：{exc}", {}

    if not isinstance(data, dict):
        return False, "设备指纹接口返回了看不懂的内容", {}
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    fp = str(inner.get("device_fp") or "")
    if int(data.get("retcode") or 0) != 0 or not fp:
        msg = str(inner.get("msg") or data.get("message") or "未知错误")
        return False, f"设备指纹没拿到（{msg}）：检查七字段是不是照着手机端原样填的", {}

    dev = {
        "on": True,
        "device_id": device_id,
        "fp": fp,
        "info": dict(info),
        "updated_at": int(time.time()),
    }
    return True, "设备指纹已生成", dev


async def register_device(account_id: str, dev: dict) -> tuple[bool, str]:
    """把这台设备登记到某个米游社账号（deviceLogin + saveDevice）。

    **必须先登录该账号** —— 要拿它的 Cookie。登记成功后，这个账号再带这套
    x-rpc-device_* 头请求，风控才会认「这是本人设备」。
    """
    from . import mys as client, store  # 延迟导入，理由同上

    ck = store.cookie(account_id)
    if not ck:
        return False, "这个米游社账号没有可用 Cookie：先扫码登录一次"
    cookie = "; ".join(f"{k}={v}" for k, v in ck.items())

    info = dev.get("info") if isinstance(dev.get("info"), dict) else {}
    fingerprint = str(info.get("deviceFingerprint") or "")
    parts = fingerprint.split("/")
    brand = parts[0] if parts else ""
    model = str(info.get("deviceModel") or (parts[1] if len(parts) > 1 else ""))

    body = {
        "app_version": client.APP_VERSION,
        "device_id": str(dev.get("device_id") or ""),
        "device_name": f"{brand}{model}",
        "os_version": str(info.get("androidVersion") or "33"),
        "platform": "Android",
        "registration_id": _rand_hex(19),
    }
    headers = {
        **client._base_headers("https://app.mihoyo.com/"),
        "x-rpc-device_id": str(dev.get("device_id") or ""),
        "x-rpc-device_fp": str(dev.get("fp") or ""),
        "x-rpc-device_name": f"{brand} {model}",
        "x-rpc-device_model": model,
        "x-rpc-csm_source": "myself",
        "Referer": "https://app.mihoyo.com",
        "Host": "bbs-api.miyoushe.com",
        "DS": _ds_passport(body),
        "Cookie": cookie,
        "Content-Type": "application/json",
    }

    last = ""
    for url in (DEVICE_LOGIN_URL, SAVE_DEVICE_URL):
        try:
            resp = await client.get_client().post(url, json=body, headers=headers)
            data = resp.json() if resp.content else {}
        except Exception as exc:  # noqa: BLE001
            return False, f"登记设备失败：{exc}"
        if isinstance(data, dict) and int(data.get("retcode") or 0) != 0:
            last = str(data.get("message") or "")
    if last:
        return False, f"登记设备被拒（{last}）：换个账号重试，或确认 Cookie 没过期"
    return True, "设备已登记到该账号"


# ---------------- 请求头 ----------------


def headers_for(account_id: str = "") -> dict:
    """该账号请求时该带的 x-rpc-device_* 头（没有配置就返回空 dict，用回随机默认）。"""
    dev = for_account(account_id)
    if not dev:
        return {}
    info = dev.get("info") if isinstance(dev.get("info"), dict) else {}
    fingerprint = str(info.get("deviceFingerprint") or "")
    parts = fingerprint.split("/")
    brand = parts[0] if parts else ""
    model = str(info.get("deviceModel") or "")
    return {
        "x-rpc-device_id": str(dev.get("device_id") or ""),
        "x-rpc-device_fp": str(dev.get("fp") or ""),
        "x-rpc-device_name": (f"{brand} {model}").strip(),
        "x-rpc-device_model": model,
        "x-rpc-sys_version": str(info.get("androidVersion") or ""),
    }


def public(dev: dict | None) -> dict | None:
    """给页面的安全视图（不吐任何凭证，设备信息本身不敏感，全给）。"""
    if not isinstance(dev, dict):
        return None
    info = dev.get("info") if isinstance(dev.get("info"), dict) else {}
    return {
        "on": dev.get("on", True) is not False,
        "device_id": str(dev.get("device_id") or ""),
        "fp": str(dev.get("fp") or ""),
        "model": str(info.get("deviceModel") or ""),
        "sys_version": str(info.get("androidVersion") or ""),
        "updated_at": int(dev.get("updated_at") or 0),
        "info": {k: str(info.get(k) or "") for k in FIELDS},
    }
