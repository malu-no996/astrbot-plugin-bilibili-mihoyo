"""设备指纹配置页的后端接口（原 src/core/device_routes.py 的 AstrBot 移植）。

页面在「米游社面板 → 设备配置」：贴真机 JSON → 生成 device_fp → 把设备登记到
某个米游社账号 → 顺手试一下 avatar/basic 还返不返 10041。存取/生成/登记全在
`device.py`，本文件只做「验参 + 调它 + 说人话」。

鉴权由 AstrBot Dashboard 统一处理（bridge 自动带身份），这里不再有 _allowed。
"""

from __future__ import annotations

from astrbot.api.web import request

from . import device, mys as client, store
from .web import body, fail, ok


def _snapshot() -> dict:
    """整份配置的安全视图（写操作后回它，前端直接覆盖本地状态）。"""
    data = device.load_all()
    accounts = data.get("accounts")
    accounts = accounts if isinstance(accounts, dict) else {}
    return {
        "default": device.public(data.get("default")),
        "accounts": {aid: device.public(dev) for aid, dev in accounts.items()},
    }


# 页面上「真机信息」表单的七个字段（顺序 = 手机端导出的顺序）
FIELD_LABELS = {
    "deviceModel": "型号",
    "androidVersion": "安卓版本",
    "deviceFingerprint": "设备指纹（最关键）",
    "deviceName": "设备代号",
    "deviceBoard": "主板",
    "deviceProduct": "产品名",
    "oaid": "OAID",
}

SAMPLE = (
    '{"deviceModel":"","androidVersion":"","deviceFingerprint":"",'
    '"deviceName":"","deviceBoard":"","deviceProduct":"","oaid":""}'
)


def _account_rows() -> list[dict]:
    """登录过的米游社账号 + 各自有没有单独配设备（页面下拉直接用）。"""
    data = device.load_all()
    own = data.get("accounts")
    own = own if isinstance(own, dict) else {}
    rows: list[dict] = []
    for acc in store.public_accounts():
        aid = str(acc.get("account_id") or "")
        if not aid:
            continue
        rows.append(
            {
                "account_id": aid,
                "nickname": str(acc.get("nickname") or aid),
                "device": device.public(own.get(aid)),
            }
        )
    return rows


def _scope(payload: dict) -> str:
    return "account" if payload.get("scope") == "account" else "default"


# ---------------- 路由 ----------------


async def device_state():
    """首屏：默认设备 + 账号列表（各自带没带设备）+ 七个字段的说明 + 示例 JSON。"""
    return ok(
        default=device.public(device.load_all().get("default")),
        accounts=_account_rows(),
        fields=[{"key": k, "label": FIELD_LABELS.get(k, k)} for k in device.FIELDS],
        sample=SAMPLE,
    )


async def device_save():
    """保存一份设备：贴的七字段 JSON → getFp 换 fp → 落盘（**还没登记到账号**）。"""
    payload = await body()
    scope = _scope(payload)
    aid = str(payload.get("account_id") or "")
    if scope == "account" and not aid:
        return fail("按账号配置时得先选一个米游社账号")

    info, err = device.parse_input(payload.get("raw"))
    if info is None:
        return fail(err)

    okv, msg, dev = await device.fetch_fp(info)
    if not okv:
        return fail(msg)

    device.set_entry(scope, dev, aid)
    return ok(message=msg + "（还没登记到账号，下面那步别漏）", **_snapshot())


async def device_register():
    """把这台账号当前生效的设备登记到指定米游社账号（deviceLogin + saveDevice）。"""
    payload = await body()
    aid = str(payload.get("account_id") or "")
    if not aid:
        return fail("先选一个米游社账号（登记要用它的 Cookie）")

    dev = device.for_account(aid)
    if not dev:
        return fail("这个账号还没有可用设备：先在上面保存一份（或启用默认设备）")

    okv, msg = await device.register_device(aid, dev)
    return ok(message=msg) if okv else fail(msg)


async def device_toggle():
    """启用 / 停用一份设备（停用后请求头回到随机默认值，用来对比配了有没有用）。"""
    payload = await body()
    scope = _scope(payload)
    aid = str(payload.get("account_id") or "")
    dev = device.entry(scope, aid)
    if not isinstance(dev, dict):
        return fail("这份设备还不存在：先保存一份")

    dev["on"] = bool(payload.get("on"))
    device.set_entry(scope, dev, aid)
    return ok(message="已启用" if dev["on"] else "已停用（请求回到随机设备）", **_snapshot())


async def device_delete():
    """删掉一份设备（默认 / 指定账号）。"""
    payload = await body()
    scope = _scope(payload)
    aid = str(payload.get("account_id") or "")
    device.del_entry(scope, aid)
    return ok(message="已删除", **_snapshot())


async def device_test():
    """拿这个账号的第一个绝区零角色试一次 avatar/basic，看还返不返 10041。"""
    payload = await body()
    aid = str(payload.get("account_id") or "")
    if not aid:
        return fail("先选一个米游社账号")

    used = device.for_account(aid)
    try:
        roles = await client.bind_roles(aid)
    except Exception as exc:  # noqa: BLE001
        return fail(f"读取这个账号的绝区零角色失败：{exc}")
    if not roles:
        return fail("这个账号下没有绝区零角色，换个账号试")

    role = roles[0]
    uid = str(role.get("game_uid") or "")
    server = str(role.get("region") or "prod_gf_cn")
    try:
        # 延迟导入：设备登记与否要靠「角色类接口」验证，而角色接口是绝区零专属的
        # （zzz/avatar/api.py）—— core 层不该在顶层依赖 zzz，所以放到函数里引。
        from ..zzz.avatar.api import avatar_basic

        code, _payload = await avatar_basic(uid, server, aid)
    except Exception as exc:  # noqa: BLE001
        return fail(f"查询失败：{exc}")

    tip = {
        0: "通过了！角色列表不再返回 10041 —— 去「绝区零 → 角色」看全部代理人",
        10041: "还是 10041：这台设备没被账号认下来 —— 确认已点「登记到这个账号」，或换台真机数据",
        10035: "10035（风控）：刚登记可能有延迟，过几分钟再试",
    }.get(code, f"返回 retcode={code}")
    if not used:
        tip += "（注意：这个账号现在没有生效的设备，刚才是用随机设备头试的）"
    return ok(retcode=code, count=len(roles), message=tip, uid=uid)


# (suffix, method, handler, desc) —— main.py 统一注册
ROUTES = [
    ("device/state", "GET", device_state, "设备配置首屏"),
    ("device/save", "POST", device_save, "保存一份设备指纹"),
    ("device/register", "POST", device_register, "把设备登记到米游社账号"),
    ("device/toggle", "POST", device_toggle, "启用/停用设备"),
    ("device/delete", "POST", device_delete, "删除设备"),
    ("device/test", "POST", device_test, "试查角色列表验证设备是否生效"),
]
