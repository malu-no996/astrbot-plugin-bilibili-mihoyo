"""社交命令 · 一键签到（把「自动签到」里勾选的角色一次签完）。

为什么单独一条命令，而不是给「签到」加参数
------------------------------------------
「签到」命令签的是**发命令那个人自己**绑定的账号（`bind.default_account`）；
这条命令签的是**页面上配好的那份目标清单**（`autosign.py`，可横跨多个米游社账号）——
两者查的目标根本不同，混在一条命令里只会让人分不清「这次签的到底是谁的号」。

谁能触发（用户要求「得指定人员」）
----------------------------------
由这条命令的**「详细设置 → 允许触发的人」**决定（`allow_users`，一行一个 QQ 号 /
官方 openid）；留空 = 不限制（所有能用这条命令的人都能触发）。
另有一个「仅管理员」的总闸在命令行上，两道过滤都生效。

为什么不做「先回一句、签完再推一条」
------------------------------------
推送第二条要自己处理各适配器的主动发消息（QQ 官方那套尤其麻烦），而签到本身
就是「点一下等结果」的场景 —— 直接等完再回，逻辑简单、结果也完整。
"""
from __future__ import annotations

from ..zzz.sign import autosign
from .core import Ctx, interface


def _allow_list(raw) -> list[str]:
    """把「允许触发的人」那栏文本切成 ID 列表（逗号 / 空格 / 换行 / 顿号都行）。"""
    text = str(raw or "").replace(",", " ").replace("，", " ").replace("、", " ")
    return [x for x in (p.strip() for p in text.split()) if x]


@interface(
    "zzz_sign_all", "绝区零 · 一键签到",
    "把「自动签到」里勾选的角色一次签完（可跨多个米游社账号，逐角色串行、"
    "请求间隔按页面配的来）。触发人由「详细设置 → 允许触发的人」限定，留空 = 不限制",
    options=[
        {
            "key": "allow_users", "label": "允许触发的人", "type": "textarea", "rows": 3,
            "default": "",
            "hint": "一行一个 QQ 号 / QQ 官方 openid，逗号或空格分隔也行；"
                    "留空 = 不限（谁能用这条命令谁就能触发）",
        },
    ],
    tpl_vars=[
        {"name": "ok", "desc": "这次是否真的执行了（false = 没权限 / 没目标 / 正在跑）"},
        {"name": "count", "desc": "签到的角色数"},
        {"name": "ok_count", "desc": "签上的角色数"},
        {"name": "next", "desc": "下一次定时签到的时刻（仅手动模式为空）"},
        {"name": "items[].name", "desc": "展示名（账号名，或「账号/角色」）"},
        {"name": "items[].status", "desc": "状态：签到成功 / 今日已签到 / 失败原因"},
        {"name": "items[].days", "desc": "累计签到天数（含本次）"},
    ],
    sample="{#items}{name} {status}（累计{days}天）\n{/items}",
)
async def _api_zzz_sign_all(ctx: Ctx) -> dict:
    """一键签到：执行「自动签到」页面上勾选的那批角色。"""
    allow = _allow_list(ctx.opt("allow_users", ""))
    who = str(ctx.user_id or "")
    if allow and who not in allow:
        return {
            "text": f"无权限：这条命令只允许指定的人触发（当前授权 {len(allow)} 人）",
            "vars": {"ok": False, "count": 0, "ok_count": 0, "next": "", "items": []},
            "data": {"ok": False, "reason": "forbidden", "allow": len(allow)},
        }

    res = await autosign.run_once("cmd")
    st = autosign.status()
    text = res.get("text") or res.get("message") or "签到没有执行"
    vars_ = {
        "ok": bool(res.get("ok")),
        "count": int(res.get("count") or 0),
        "ok_count": int(res.get("ok_count") or 0),
        "next": str(st.get("next_label") or ""),
        "items": res.get("items") or [],
    }
    return {
        "text": text,
        "vars": vars_,
        "data": {"busy": bool(res.get("busy")), "message": res.get("message") or "", **vars_},
    }
