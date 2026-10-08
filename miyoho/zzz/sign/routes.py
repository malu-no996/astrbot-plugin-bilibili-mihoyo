"""绝区零 · 每日签到 的管理页路由（原 src/zzz/sign/routes.py 的 AstrBot 移植）。

  GET  zzz/sign       签到状态 + 本月奖励表（**只读**，不触发签到）
  POST zzz/sign/do    执行今日签到

接口实现在同目录 `api.py`（luna 活动接口，带 DS 签名）。
"""
from __future__ import annotations

from astrbot.api.web import json_response, request

from ...core import mys as client            # 只用它的 region_name
from ...core.web import body, call, fail
from .api import zzz_sign_boards, zzz_sign_do


def _qstr(key: str, default: str = "") -> str:
    return str(request.query.get(key, default) or default)


# ---------------- 绝区零：签到（luna 活动接口） ----------------


async def api_zzz_sign_info():
    """签到状态 + 本月奖励表（/info + /home，**只读**，不触发签到）。"""
    uid, server, account_id = _qstr("uid"), _qstr("server", "prod_gf_cn"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        zzz_sign_boards(uid, server, account_id=account_id or None), "读取签到状态失败"
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": server,
            "region_name": client.region_name(server),
            **data,
        }
    )


async def api_zzz_sign_do():
    """执行今日签到（POST /sign）。body: {uid, server, account_id}。"""
    payload = await body()
    uid = str(payload.get("uid") or "")
    server = str(payload.get("server") or "prod_gf_cn")
    account_id = str(payload.get("account_id") or "")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        zzz_sign_do(uid, server, account_id=account_id or None), "签到失败"
    )
    if err:
        return err
    return json_response(
        {"ok": True, "message": "签到成功", "data": data if isinstance(data, dict) else {}}
    )


ROUTES = [
    ("zzz/sign", "GET", api_zzz_sign_info, "签到状态与奖励表"),
    ("zzz/sign/do", "POST", api_zzz_sign_do, "执行今日签到"),
]
