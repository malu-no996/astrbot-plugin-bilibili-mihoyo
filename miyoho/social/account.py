"""社交命令 · 米游社账号类接口：扫码登录 / 我的绑定 / 切换账号 / 解绑。

2026-10-04 从原 `social_apis.py` 拆出来：账号相关的 4 个 @interface 在这里，
绝区零战绩类（切换角色 / 危局 / 防卫战）在 `record.py`，公共工具在 `base.py`。

⚠️ 导入即注册（@interface），`social/__init__.py` 里的 import 顺序决定页面下拉框顺序。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from ..core import bind
from ..core import mys as client
from .base import _clean_name, _qr_image_segment, _send
from .core import Ctx, interface
from .qq import layout_buttons, send_buttons
# 二维码最长轮询时间：官方给的有效期是 180 秒，留一点余量
_QR_WAIT = 170
_QR_INTERVAL = 2.0


@interface("mys_login", "米游社扫码登录", "发二维码，扫码后把米游社账号绑到你的 QQ")
async def _api_mys_login(ctx: Ctx) -> str:
    if not ctx.user_id:
        return "取不到你的用户 ID，无法绑定（换个方式发一次试试）"
    try:
        info = await client.login_start("hyp")
    except Exception as exc:  # noqa: BLE001
        return f"申请登录二维码失败：{type(exc).__name__}: {exc}"
    info = info if isinstance(info, dict) else {}
    ticket = str(info.get("ticket") or "")
    url = str(info.get("url") or "")
    if not ticket or not url:
        return "申请登录二维码失败：米游社没返回 ticket / url，请稍后再试"

    head = (
        "请用米游社 App 扫描二维码（3 分钟内有效，扫码后要在手机上点「确认登录」）。\n"
        "登录成功后这个米游社账号会自动绑到你的 QQ 上。"
    )
    png = client.qr_png(url)
    seg = png  # AstrBot：_send 里统一包成 Image 组件（bytes → Image.fromBytes）
    if seg is None:
        # 发不了图（没装 segno / 适配器不支持）就退化成发链接，至少还能用
        return f"{head}\n扫码链接：{url}"
    # 图文**一条**消息：MessageChain 里 Image 在前、Plain 在后 → 图在上、文字在下。
    # 混合发送失败（个别适配器不支持图文混排会抛错）才退回老路：文字、图各发一条。
    if not await _send(ctx, [png, head]):
        if not await _send(ctx, head) or not await _send(ctx, png):
            return f"{head}\n（二维码图片发送失败）扫码链接：{url}"

    # 发完二维码开始轮询：这里**不用** matcher.finish，因为还要继续等结果，
    # 最终文本由分发器统一发出（接口只管返回要发什么）。
    deadline = time.monotonic() + _QR_WAIT
    told_scanned = False
    while time.monotonic() < deadline:
        await asyncio.sleep(_QR_INTERVAL)
        try:
            res = await client.login_poll(ticket)
        except Exception as exc:  # noqa: BLE001 —— 单次轮询失败不该直接放弃
            logger.warning(f"miyoushe 扫码轮询失败：{type(exc).__name__}: {exc}")
            continue
        res = res if isinstance(res, dict) else {}
        status = str(res.get("status") or "")
        if status == "success":
            aid = str(res.get("account_id") or "")
            if not aid:
                return "登录成功但取不到账号 ID，请重新扫码"
            user = res.get("user") if isinstance(res.get("user"), dict) else {}
            name = str(user.get("nickname") or aid)
            bind.add(ctx.user_id, aid, nickname=name)
            n = len(bind.accounts(ctx.user_id))
            tail = "" if n <= 1 else "（已设为默认查询账号；「米游社切换」可换）"
            return f"绑定成功：{name}{tail}"
        if status == "scanned" and not told_scanned:
            told_scanned = True                          # 只提示一次，避免刷屏
            await _send(ctx, "已扫码，请在米游社 App 里点「确认登录」")
            continue
        if status in ("expired", "canceled", "error"):
            return str(res.get("message") or "扫码未完成，请重新发送「米游社登录」")
        # waiting → 继续轮询
    return "二维码已超时（3 分钟），请重新发送「米游社登录」"


@interface(
    "mys_binds", "米游社账号", "列出你绑定的米游社账号",
    tpl_vars=[
        {"name": "count", "desc": "绑定的米游社账号数量"},
        {"name": "hint", "desc": "结尾的用法提示"},
        {"name": "items", "desc": "账号列表，每条含 index / name / account_id / "
                                  "default（是否默认）/ mark（默认账号的后缀）/ text（整行）"},
    ],
    sample="{count} 个米游社账号：\n{#items}{index}. {name}{mark}\n{/items}{hint}",
)
async def _api_mys_binds(ctx: Ctx) -> dict:
    """列出绑定账号：默认文本 + vars（模板变量）+ data（原始列表）。"""
    rows = bind.accounts(ctx.user_id)
    if not rows:
        return {"text": "你还没有绑定米游社账号：发送「米游社登录」扫码绑定"}
    items = [
        {
            "index": i,
            "name": r["nickname"] or r["account_id"],
            "account_id": r["account_id"],
            "bound_at": int(r.get("bound_at") or 0),
            "default": bool(r["is_default"]),
            "mark": "（默认）" if r["is_default"] else "",
            "text": f"{i}. {r['nickname'] or r['account_id']} · id {r['account_id']}"
                    + ("（默认）" if r["is_default"] else ""),
        }
        for i, r in enumerate(rows, 1)
    ]
    hint = "切换默认：「米游社切换 账号ID」；解绑：「米游社解绑 账号ID」"
    lines = [f"你绑定了 {len(rows)} 个米游社账号：", *[it["text"] for it in items], hint]
    return {
        "text": "\n".join(lines),
        "vars": {"count": len(items), "hint": hint, "items": items},
        "data": {"accounts": items},
    }


# 「米游社切换」列表的标题 —— 也是 QQ 官方按钮消息上方那行正文（官方不允许
# 带按钮的消息正文为空，实测报 40034030 消息content字段不能为空）。
_DEFAULT_SWITCH_TITLE = "账号列表"

@interface(
    "mys_switch", "米游社切换",
    "不带参数列出你绑定的米游社账号（一行一个「序号、昵称：账号ID」）；命令后加账号 ID"
    "切换默认账号，切换结果只回一句「【昵称】切换成功 / 切换失败」。QQ 官方机器人"
    "不用文字列表，改成把这些账号做成按钮，点一下就切过去",
    options=[
        {
            "key": "kb_text", "label": "按钮上方那行字（QQ 官方）", "type": "text",
            "default": _DEFAULT_SWITCH_TITLE,
            "hint": "QQ 官方要求带按钮的消息必须有正文（空正文直接报 40034030），"
                    "给一句短的即可",
        },
        {
            "key": "per_row", "label": "按钮每行几个", "type": "number",
            "default": 3, "min": 1, "max": 5,
            "hint": "按钮**从左到右**排，排满这个数就换行"
                    "（官方限制：每行最多 5 个、最多 5 行）",
        },
        {
            "key": "max_buttons", "label": "最多几个按钮", "type": "number",
            "default": 9, "min": 1, "max": 25,
            "hint": "账号多于这个数时，只有前 N 个做成按钮（其余的用「米游社账号」查看）",
        },
    ],
    tpl_vars=[
        {"name": "count", "desc": "绑定的米游社账号数量"},
        {"name": "title", "desc": "列表标题（默认「账号列表」，也是 QQ 按钮消息的正文）"},
        {"name": "command", "desc": "本命令的命令词 —— 按钮点下去发的就是「命令词 + 账号 ID」"},
        {"name": "ok", "desc": "带参数（切换）时：这次切换成功没有（true / false）；"
                              "不带参数（列表）时恒为 false"},
        {"name": "name", "desc": "带参数时=要切换的那个账号的昵称（找不到就用你输入的原文）；"
                                "列表调用为空"},
        {"name": "account_id", "desc": "带参数且切换成功时的账号 ID；列表调用为空"},
        {"name": "hint", "desc": "补充语：切换失败的原因（成功为空）/ 列表时的用法提示"},
        {"name": "items", "desc": "账号列表，每条含 index / name / account_id / default（是否默认）/ "
                                  "mark（默认账号的后缀「（默认）」，默认的 text 里没带，"
                                  "想显示就在模板里写它）/ "
                                  "text（整行，形如「1、名字：123456」）/ "
                                  "data（QQ 按钮点下去会发的命令）"},
    ],
    sample="{title}\n{#items}{text}\n{/items}{hint}",
)
async def _api_mys_switch(ctx: Ctx) -> dict:
    """切换默认查询的米游社账号（一个 QQ 可以绑多个米游社账号）。

    三种用法：
      · **不带参数** → 列出账号：文字版一行一个「序号、昵称：账号ID」（用户指定的格式）；
        **QQ 官方**不文字列表，改成把这些账号做成**按钮**（文案 = 昵称，点下去发的
        是「<命令词> <账号ID>」），按 `per_row` 从左到右排、满行换行，最多 `max_buttons` 个。
      · **带参数** → 按账号 ID 切换默认账号（也认序号 / 昵称，见 bind.find），
        写进绑定关系、重启也在。之后危局 / 防卫战 / 抽卡 / 签到 都查这个账号。
        带参数就是一次「切换」调用，**这里提前返回**、只回一句
        `【昵称】切换成功` / `【昵称】切换失败`：
        QQ 官方点按钮也走这条（所以点完不会又甩一遍按钮列表）。
      · 管理页预览 → 走不带参数那条（预览没有机器人与事件，不会真的发按钮），
        `data.buttons` 里能看到按钮会排成什么样。

    返回 text（默认文案）+ vars（模板变量）+ data（原始数据，管理页预览里显示成 JSON）。
    """
    cmd = str(((ctx.cmd or {}).get("cmd")) or "米游社切换")
    key = (ctx.arg or "").strip()
    rows = bind.accounts(ctx.user_id)
    if not rows:
        return {"text": "你还没有绑定米游社账号：发送「米游社登录」扫码绑定"}

    def _name_of(aid: str) -> str:
        """账号 ID → 昵称（列表里没有就退回 ID 本身，绝不返回空）。"""
        aid = str(aid or "")
        return next((str(r["nickname"] or r["account_id"])
                     for r in bind.accounts(ctx.user_id)
                     if str(r["account_id"]) == aid), aid)

    def _switch_reply(ok: bool, name: str, account_id: str, hint: str) -> dict:
        """带参数（切换）那一路的统一返回：一句【名字】切换成功 / 失败 + 可选补充。

        名字**必须**过 `_clean_name`：失败那次回显的就是用户随手输的原文（可能是
        乱码 / 超长 / 带换行），不清洗会把提示顶乱。
        """
        name = _clean_name(name)
        text = f"【{name}】{'切换成功' if ok else '切换失败'}"
        if hint:
            text += f"\n{hint}"
        return {
            "text": text,
            "vars": {
                "title": "", "command": cmd, "count": len(rows),
                "ok": ok, "name": name, "account_id": account_id,
                "hint": hint, "items": [],
            },
            "data": {"protocol": ctx.protocol, "switched": account_id if ok else "",
                     "command": cmd, "ok": ok, "name": name, "account_id": account_id},
        }

    if key:
        aid_found = bind.find(ctx.user_id, key)
        if not aid_found:
            # 乱输入（或者填了别人的账号 ID）：自己名下找不到就一定失败，绝不回落到
            # 别人的账号。名字取不到，就用你输入的原文（洗过一遍再回显）。
            return _switch_reply(
                False, key, "",
                f"没找到这个账号。要切换得填「{cmd}」列出来的账号 ID（序号 / 昵称也行）",
            )
        if not bind.set_default(ctx.user_id, aid_found):
            return _switch_reply(False, _name_of(aid_found), aid_found,
                                 "该账号不在你的绑定列表里")
        # 切换成功 → 立刻回一句确认，**不再往下走**（不甩列表、QQ 官方也不发按钮）。
        return _switch_reply(True, _name_of(aid_found), aid_found, "")

    title = str(ctx.opt("kb_text", _DEFAULT_SWITCH_TITLE) or "").strip() or "账号列表"
    items = []
    for i, r in enumerate(rows, 1):
        aid = str(r["account_id"])
        name = str(r["nickname"] or aid)
        items.append({
            "index": i,
            "name": name,
            "account_id": aid,
            "default": bool(r["is_default"]),
            "mark": "（默认）" if r["is_default"] else "",
            "text": f"{i}、{name}：{aid}",                # 用户指定的文字版格式
            "data": f"{cmd} {aid}",                       # QQ 按钮点下去发的命令
        })
    # 文字版正文：标题 + 一行一个账号（不带任何多余说明，用户指定的格式）。
    text = "\n".join([title, *[it["text"] for it in items]])
    # QQ 官方：同一批账号排成按钮（不必等协议判断，管理页预览的 data 里也要能看到）。
    btn_rows = layout_buttons(items, ctx.opt("per_row", 3), ctx.opt("max_buttons", 9))
    shown = sum(len(r) for r in btn_rows)

    # 走到这里只剩「不带参数列账号」这一条路：带参数的切换在上面已经提前 return 了。
    # 举例用**第一个非默认**账号：默认那个排在最前，拿它当例子等于「让你切到你现在这个」。
    other = next((it for it in items if not it["default"]), items[0])
    hint = f"要切换默认账号：在这个命令后面加账号 ID，例如：{cmd} {other['account_id']}"

    vars_ = {
        "title": title,
        "command": cmd,
        "count": len(items),
        "ok": False,                                      # 列表调用不是「切换」，见 tpl_vars
        "name": "",
        "account_id": "",
        "hint": hint,
        "items": items,
    }
    data = {
        "protocol": ctx.protocol,
        "switched": "",
        "title": title,
        "command": cmd,
        "count": len(items),
        "accounts": items,
        "buttons": btn_rows,
        "button_layout": {"per_row": ctx.opt("per_row", 3), "max_buttons": ctx.opt("max_buttons", 9)},
    }

    if ctx.protocol == "qq" and btn_rows:
        # 官方版：正文只有那行标题，账号全做成按钮；点一下 = 发「米游社切换 <账号ID>」。
        body = title
        if shown < len(items):
            body += f"\n（共 {len(items)} 个账号，按钮只放了前 {shown} 个）"
        sent, why = await send_buttons(ctx, body, btn_rows)
        if sent:
            # 已经自己发完了 → 返回 silent，让分发器别再发一遍文字。
            return {"silent": True, "vars": vars_, "data": data}
        if why:
            # 按钮没发出去：退回文字列表 + 原因（绝不静默）。
            data["button_error"] = why
            vars_["button_error"] = why
            text = f"{text}\n{why}"
    return {"text": text, "vars": vars_, "data": data}


@interface(
    "mys_unbind", "米游社解绑", "解绑一个米游社账号（参数：序号或昵称）",
    tpl_vars=[
        {"name": "name", "desc": "被解绑的账号昵称"},
        {"name": "account_id", "desc": "被解绑的账号 ID"},
        {"name": "left", "desc": "解绑后还剩几个账号"},
        {"name": "default", "desc": "解绑后新的默认账号昵称（没有了就是空）"},
    ],
    sample="已解绑：{name}，当前还剩 {left} 个账号",
)
async def _api_mys_unbind(ctx: Ctx) -> dict:
    key = (ctx.arg or "").strip()
    if not key:
        return {"text": "要解绑哪个？用法：米游社解绑 1（序号见「米游社账号」）"}
    aid = bind.find(ctx.user_id, key)
    if not aid:
        return {"text": f"没找到「{key}」对应的绑定账号，发「米游社账号」看看序号"}
    rows = bind.accounts(ctx.user_id)
    name = next((r["nickname"] or r["account_id"] for r in rows if r["account_id"] == aid), aid)
    if not bind.remove(ctx.user_id, aid):
        return {"text": "解绑失败：该账号不在你的绑定列表里"}
    left = bind.accounts(ctx.user_id)
    new_default = (left[0]["nickname"] or left[0]["account_id"]) if left else ""
    tail = f"，当前默认：{new_default}" if left else "，你已没有绑定账号"
    return {
        "text": f"已解绑：{name}{tail}",
        "vars": {"name": name, "account_id": aid, "left": len(left), "default": new_default},
        "data": {"unbound": {"name": name, "account_id": aid},
                 "left": [r["account_id"] for r in left], "default": new_default},
    }


