"""绝区零 · 绳网月报 的管理页路由（原 src/zzz/month/routes.py 的 AstrBot 移植）。

  GET  zzz/month       绳网月报（month=YYYYMM 查历史，留空 = 当月）

与 QQ 命令那份（`social/month.py` 的 `@interface("zzz_month")`）共用同一个
`api.month_info`，只是入口不同：这里给页面 JSON，那边给聊天一句话。
"""
from __future__ import annotations

from astrbot.api.web import json_response, request

from ...core.web import call, fail
from .api import month_info


def _qstr(key: str, default: str = "") -> str:
    return str(request.query.get(key, default) or default)


async def api_zzz_month():
    """绳网月报（nap_ledger /month_info）。"""
    uid, server = _qstr("uid"), _qstr("server", "prod_gf_cn")
    month, account_id = _qstr("month"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        month_info(uid, server, month=month, account_id=account_id or None),
        "读取绳网月报失败",
    )
    if err:
        return err
    return json_response({"ok": True, "data": data})


ROUTES = [
    ("zzz/month", "GET", api_zzz_month, "绳网月报"),
]
