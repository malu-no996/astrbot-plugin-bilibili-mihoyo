"""社交命令 · 绝区零战绩类接口：切换角色 / 危局强袭战 / 式舆防卫战。

2026-10-04 从原 `social_apis.py` 拆出来的第三段（战绩部分）。
账号类接口在 `account.py`、公共工具在 `base.py`。

⚠️ 导入即注册（@interface），`social/__init__.py` 里的 import 顺序决定页面下拉框顺序。
"""

from __future__ import annotations

import time
from typing import Any

from loguru import logger

from ..core import asset_cache, bind
from ..core import mys as client
from ..zzz.avatar import char_map
from ..zzz.codex import data as codex_data
from ..zzz.record import api as record_api, image as record_image
from ..zzz.record import shiyu_image, store as record_store
from ..zzz.record import weapon as zzz_weapon
# 官方字段 → 人话的小工具（_pt / _pct_plus / _avatars / _bangboo_name …）已下沉到
# base.py：探索类接口（零号空洞 / 迷宫诡域 / 临界推演）要用同一套口径，
# 放公共层才不至于每个接口各写一份。
from .base import (
    OUTPUT_OPTIONS,
    SHOW_FOURTH_OPTION,
    SHOW_TIME_OPTION,
    _avatars,
    _bangboo_name,
    _buffs,
    _clean_name,
    _find_role,
    _image_reply,
    _period,
    _pick_role,
    _pt,
    _pct_plus,
    _save_record,
    _send,
    _zzz_target,
)
from .core import Ctx, interface
from .qq import layout_buttons, send_buttons
# 「切换角色」列表的标题 —— 也是 QQ 官方那条按钮消息的正文（官方不允许带按钮的
# 消息正文为空，见 social_qq 里的 40034030 踩坑记录）。
_DEFAULT_ROLE_TITLE = "角色列表"

# 「全面」= 危局 / 防卫战战报的**另一个版本**：图片战报 + 每个代理人头像**左下角**
# 再叠一枚音擎图标（大小同 A/S 徽章）。触发入口有两处，**都不在命令配置详情里**：
#   · 网页：危局 / 防卫战查询页面上的「全面」勾选框（zzz-deadly / zzz-shiyu frag，
#     走 /zzz/deadly、/zzz/shiyu 路由，`full=1`）；
#   · QQ：命令带参数 `full`（`_arg_full`）。
# 音擎数据 / 设备绑定检查 / 提醒文案统一在 `zzz/record/weapon.py`（两条入口共用）。


@interface(
    "zzz_role", "绝区零 · 切换角色",
    "列出当前米游社账号下的绝区零角色；命令后加 UID 即可切换默认角色，"
    "切换结果只回一句「【角色名】切换成功 / 切换失败」。QQ 官方机器人不列文字，"
    "改成把这些角色做成按钮，点一下就切过去",
    options=[
        {
            "key": "kb_text", "label": "按钮上方那行字（QQ 官方）", "type": "text",
            "default": _DEFAULT_ROLE_TITLE,
            "hint": "QQ 官方要求带按钮的消息必须有正文（空正文直接报 40034030），"
                    "给一句短的即可",
        },
        {
            "key": "per_row", "label": "按钮每行几个", "type": "number",
            "default": 3, "min": 1, "max": 5,
            "hint": "角色按钮**从左到右**排，排满这个数就换行"
                    "（官方限制：每行最多 5 个、最多 5 行）",
        },
        {
            "key": "max_buttons", "label": "最多几个按钮", "type": "number",
            "default": 9, "min": 1, "max": 25,
            "hint": "角色多于这个数时，只有前 N 个做成按钮（其余的看文字列表）",
        },
    ],
    tpl_vars=[
        {"name": "account", "desc": "米游社账号昵称"},
        {"name": "count", "desc": "角色数量"},
        {"name": "current", "desc": "当前默认角色的 UID"},
        {"name": "current_name", "desc": "当前默认角色名"},
        {"name": "once", "desc": "是否只有一个角色（true / false）"},
        {"name": "ok", "desc": "带参数时：这次切换成功没有（true / false）；"
                              "不带参数（列表）时恒为 false"},
        {"name": "hint", "desc": "补充语：切换失败的原因（成功为空）/ 列表时的用法提示"},
        {"name": "name", "desc": "带参数时=要切换的那个角色的名字（找不到就用你输入的原文）；"
                                "列表调用为空"},
        {"name": "uid", "desc": "带参数且切换成功时的角色 UID；列表调用为空"},
        {"name": "items", "desc": "角色列表，每条含 index / name / uid / region / region_name / "
                                  "current（是否默认）/ mark（默认角色的打勾后缀）/ "
                                  "data（QQ 按钮点下去会发的命令，形如「命令词 UID」）"},
    ],
    sample=(
        "绝区零角色（米游社账号：{account}）：\n"
        "{#items}{name} : {uid}{mark}\n{/items}"
        "{hint}"
    ),
)
async def _api_zzz_role(ctx: Ctx) -> dict:
    """一个米游社账号可能绑了多个绝区零角色，这里负责「看有哪些、切默认查哪个」。

    不带参数 → 列出角色：文字版「名字 : UID」（当前默认那个打勾）+ 用法提示；
                 **QQ 官方**不列文字，改成把这些角色做成**按钮**（文案 = 角色名，
                 点下去发「<命令词> <UID>」），按 `per_row` 从左到右排、最多 `max_buttons` 个。
    带参数   → 按 UID / 序号 / 角色名切换默认角色（写进绑定关系，重启也在）。
                 这就是一次「切换」调用，**这里提前返回**、只回一句
                 `【角色名】切换成功` / `【角色名】切换失败`；QQ 官方点按钮也走这条
                 （所以点完不会又甩一遍按钮列表）。

    返回 text（默认文案）+ vars（模板变量）+ data（原始数据，管理页预览里显示成 JSON）。
    """
    cmd = str(((ctx.cmd or {}).get("cmd")) or "切换角色")
    aid = bind.default_account(ctx.user_id)
    if not aid:
        return {"text": "未绑定米游社账号：请先发送「米游社登录」扫码绑定你的米游社账号"}
    try:
        roles = await client.bind_roles(aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取绝区零角色失败：{exc}"}
    rows = [r for r in roles if isinstance(r, dict) and r.get("game_uid")]
    if not rows:
        return {"text": "当前账号没有绑定绝区零角色"}

    acc_name = next(
        (r["nickname"] or r["account_id"] for r in bind.accounts(ctx.user_id)
         if r["account_id"] == aid),
        aid,
    )
    arg = (ctx.arg or "").strip()
    cur = bind.role_default(ctx.user_id, aid) or str(rows[0].get("game_uid") or "")
    target = _find_role(rows, arg) if arg else {}       # 参数指定的目标角色（可能没找到）

    def _role_name(role: dict) -> str:
        """角色 → 用来回显的名字（拿不到就退「未知角色」，与列表口径一致）。"""
        return str((role or {}).get("nickname") or "未知角色")

    def _switch_reply(ok: bool, name: str, uid: str, hint: str) -> dict:
        """带参数（切换）那一路的统一返回：一句【角色名】切换成功 / 失败 + 可选补充。

        名字**必须**过 `_clean_name`：失败那次回显的就是用户随手输的原文
        （可能是乱码 / 超长 / 带换行），不清洗会把提示顶乱。
        """
        name = _clean_name(name)
        text = f"【{name}】{'切换成功' if ok else '切换失败'}"
        if hint:
            text += f"\n{hint}"
        return {
            "text": text,
            "vars": {
                "account": acc_name, "count": len(rows), "current": uid or cur,
                "current_name": name if ok else "", "once": len(rows) == 1,
                "ok": ok, "hint": hint,
                "name": name if ok else "", "uid": uid if ok else "",
                "items": [],
            },
            "data": {"account": {"id": aid, "nickname": acc_name},
                     "current_uid": uid or cur, "switched": ok, "role": name},
        }

    if arg and not target:
        # 乱输入（或者填了别的角色 UID）：找不到就一定失败，绝不猜。
        return _switch_reply(False, arg, "", f"没找到这个角色，发「{cmd}」看看有哪些")
    if target:
        gid = str(target.get("game_uid") or "")
        if not bind.set_role_default(ctx.user_id, aid, gid):
            return _switch_reply(False, _role_name(target), gid,
                                 "绑定关系里没有这个米游社账号（重新发「米游社登录」试试）")
        # 切换成功 → 立刻回一句确认，**不再往下走**（不甩列表、QQ 官方也不发按钮）。
        return _switch_reply(True, _role_name(target), gid, "")

    title = str(ctx.opt("kb_text", _DEFAULT_ROLE_TITLE) or "").strip() or "角色列表"
    items = []
    for i, r in enumerate(rows, 1):
        gid = str(r.get("game_uid") or "")
        region = str(r.get("region") or "")
        items.append({
            "index": i,
            "name": _role_name(r),
            "uid": gid,
            "region": region,
            "region_name": client.region_name(region) if region else "",
            "current": gid == cur,
            "mark": " ✅" if gid == cur else "",
            "data": f"{cmd} {gid}",                       # QQ 按钮点下去发的命令
        })
    cur_name = next((it["name"] for it in items if it["current"]), "")
    once = len(items) == 1
    if once:
        hint = "你只绑定了 1 个绝区零角色，不用切换。"
    else:
        hint = f"要切换默认角色，在这个命令后面加上 UID，例如：{cmd} {cur}"
    # 文字版正文：标题 + 一行一个角色（不带任何多余说明）。
    text = "\n".join([f"绝区零角色（米游社账号：{acc_name}）："]
                     + [f"{it['name']} : {it['uid']}{it['mark']}" for it in items]
                     + [hint])
    # QQ 官方：同一批角色排成按钮（不必等协议判断，管理页预览的 data 里也要能看到）。
    btn_rows = layout_buttons(items, ctx.opt("per_row", 3), ctx.opt("max_buttons", 9))
    shown = sum(len(r) for r in btn_rows)

    vars_ = {
        "account": acc_name,
        "count": len(items),
        "current": cur,
        "current_name": cur_name,
        "once": once,
        "ok": False,                                      # 列表调用不是「切换」，见 tpl_vars
        "hint": hint,
        "name": "",
        "uid": "",
        "items": items,
    }
    data = {
        "account": {"id": aid, "nickname": acc_name},
        "roles": items,
        "current_uid": cur,
        "switched": False,
        "protocol": ctx.protocol,
        "title": title,
        "buttons": btn_rows,
        "button_layout": {"per_row": ctx.opt("per_row", 3), "max_buttons": ctx.opt("max_buttons", 9)},
    }

    if ctx.protocol == "qq" and btn_rows:
        # 官方版：正文只有那行标题，角色全做成按钮；点一下 = 发「切换角色 <UID>」。
        body = title
        if shown < len(items):
            body += f"\n（共 {len(items)} 个角色，按钮只放了前 {shown} 个）"
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


# ================= 危局 / 防卫战自己的字段转换 =================
#
# 官方字段 → 人话的通用工具（_pt / _pct_plus / _avatars / _bangboo_name）在 base.py，
# 这里只留**只服务于这两个接口**的转换：危局的挑战记录、防卫战的分层小队。


def _deadly_rows(rows: Any) -> list[dict]:
    """危局的挑战记录（list / hard_list 同构）→ 模板条目列表。"""
    out: list[dict] = []
    for i, c in enumerate(rows or [], 1):
        c = c if isinstance(c, dict) else {}
        boss = c.get("boss")
        boss = (boss[0] if isinstance(boss, list) and boss else {}) or {}
        boss = boss if isinstance(boss, dict) else {}
        buddy = c.get("buddy") if isinstance(c.get("buddy"), dict) else {}
        avatars, ava_text = _avatars(c.get("avatar_list"))
        score, star = int(c.get("score") or 0), int(c.get("star") or 0)
        boss_name = str(boss.get("name") or "")
        out.append({
            "index": i,
            "boss": boss_name,
            "boss_icon": str(boss.get("icon") or ""),
            "score": score,
            "star": star,
            "time": _pt(c.get("challenge_time")),
            "avatars": avatars,
            "avatar_text": ava_text,
            "buddy": str(buddy.get("name") or _bangboo_name(buddy.get("id")) or ""),
            "buffs": _buffs(c.get("buffer")),
            "text": f"{boss_name} · {score} 分 · {star}★",
        })
    return out


def _shiyu_layer(info: dict, key: str) -> dict:
    """取某一层的明细：`first/second/third/fourth/fitfh_layer_detail`。

    ⚠️ 官方把「第五层」拼成了 **fitfh**（fifth 的历史 typo），接口里就叫这个名字，
    网页端也跟着用 —— 别"顺手"改成 fifth，会直接取不到数据。
    """
    layer = info.get(f"{key}_layer_detail") if isinstance(info, dict) else None
    return layer if isinstance(layer, dict) else {}


def _buf_title(raw: Any) -> str:
    """防线效果（`buffer`）→ 标题串。

    ⚠️ 危局里 `buffer` 是**列表**（每条 {name,…}），防卫战里是**单个 dict**（{title,text}），
    两种都在这儿收敛；取不到就是空串。
    """
    if isinstance(raw, dict):
        return str(raw.get("title") or "")
    return ""


def _shiyu_teams(layer: dict, label: str, start: int) -> list[dict]:
    """一层里的出战小队 → 模板条目列表（index 从上往下连着编，不按层重来）。

    除了给人看的字段，还带两个**给画图用**的图片地址：`monster`（BOSS 立绘）
    与 `buddy_icon`（邦布方图）—— 图片模式下渲染器就照这些画。
    """
    out: list[dict] = []
    for i, n in enumerate(layer.get("layer_challenge_info_list") or [], 1):
        n = n if isinstance(n, dict) else {}
        buddy = n.get("buddy") if isinstance(n.get("buddy"), dict) else {}
        avatars, ava_text = _avatars(n.get("avatar_list"))
        score = int(n.get("score") or 0)
        rating = str(n.get("rating") or "")
        out.append({
            "index": start + i - 1,
            "no": i,
            "layer": label,
            "rating": rating,
            "score": score,
            "time": _pt(n.get("challenge_time")),
            "avatars": avatars,
            "avatar_text": ava_text,
            "buddy": str(buddy.get("name") or _bangboo_name(buddy.get("id")) or ""),
            "buddy_icon": str(buddy.get("bangboo_rectangle_url") or buddy.get("icon") or ""),
            # 邦布稀有度（S / A）：画图和网页都要用它来决定角标与边框颜色，
            # 与危局的 buddy_rarity 对齐（官方那层字段名就叫 rarity）
            "buddy_rarity": str(buddy.get("rarity") or "S").upper() if buddy else "",
            "monster": str(n.get("monster_pic") or ""),
            "buffer": _buf_title(n.get("buffer")),
            "text": f"{label}第 {i} 队 · {rating} {score} 分".strip(),
        })
    return out


# 五层里只有这两层在接口里有明细（第五层是 fitfh，官方 typo），前三层没明细
_SHIYU_LAYERS = (("fitfh", "第五防线"), ("fourth", "第四防线"))


def _shiyu_info(data: dict) -> tuple[dict, dict]:
    """取 (hadal_info_v2, brief)，两边都不是 dict 时给空 dict（不会抛）。"""
    info = data.get("hadal_info_v2")
    info = info if isinstance(info, dict) else {}
    brief = info.get("brief")
    return info, (brief if isinstance(brief, dict) else {})


def _shiyu_view(data: dict) -> dict:
    """防卫战原始数据 → **图片渲染用的视图**（字段对齐前端 frag/zzz-shiyu.js 的 syBuild）。

    渲染器（shiyu_image）只认这份视图，不去摸官方那些 `hadal_info_v2 / fitfh_layer_detail`
    的字段名 —— 字段映射只在这一个地方维护，跟前端 syBuild 是对称的两份。
    """
    info, brief = _shiyu_info(data)
    layers = {key: _shiyu_layer(info, key) for key, _label in _SHIYU_LAYERS}
    fifth = _shiyu_teams(layers["fitfh"], "第五防线", 1)
    fourth = _shiyu_teams(layers["fourth"], "第四防线", len(fifth) + 1)
    return {
        "nick_name": str(data.get("nick_name") or ""),
        "icon": str(data.get("icon") or ""),
        "rating": str(brief.get("rating") or layers["fourth"].get("rating") or ""),
        "score": int(brief.get("score") or sum(t["score"] for t in fifth) or 0),
        "rank": _pct_plus(brief.get("rank_percent")),
        "period": _period(info.get("hadal_begin_time"), info.get("hadal_end_time")),
        "fifth": {"rating": str(layers["fitfh"].get("rating") or "") or
                            str(brief.get("rating") or ""),
                  "buffer": _buf_title(layers["fitfh"].get("buffer")),
                  "teams": fifth},
        "fourth": {"rating": str(layers["fourth"].get("rating") or ""),
                   "buffer": _buf_title(layers["fourth"].get("buffer")),
                   "teams": fourth},
    }


def _arg_full(ctx: Ctx) -> bool:
    """QQ 命令参数里带 `full` → 触发全面版；同时从 ctx.arg 里剥掉这个 token，
    免得它混进后面的 UID 解析（UID 是纯数字，正常不会是 full，但保险起见只剥独立词）。
    """
    toks = (ctx.arg or "").split()
    if "full" not in [t.lower() for t in toks]:
        return False
    ctx.arg = " ".join(t for t in toks if t.lower() != "full").strip()
    return True


@interface(
    "zzz_deadly", "绝区零 · 危局强袭战", "本期总分 / 星数 / 排名（可跟 UID 参数）",
    options=[
        {"key": "output", "label": "输出方式", "type": "select",
         "options": OUTPUT_OPTIONS, "default": "text",
         "hint": "选「图片消息」就发一张和网页上一样的战报图（纯 Pillow 绘制，"
                 "立绘走本地缓存）；发送失败会退回文字并附上原因。"},
        SHOW_TIME_OPTION,
    ],
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "nick_name", "desc": "游戏内昵称"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "total_score", "desc": "本期总分"},
        {"name": "total_star", "desc": "本期星数"},
        {"name": "rank", "desc": "排名百分比（如 52.84%+；没有排名数据时为空）"},
        {"name": "rank_percent", "desc": "排名原值（官方口径 = 百分比 ×100，如 5284）"},
        {"name": "period", "desc": "赛期（2026.09.18 - 2026.10.02）"},
        {"name": "start / end", "desc": "赛期起 / 止（含时分秒）"},
        {"name": "count", "desc": "普通模式挑战记录条数"},
        {"name": "items", "desc": "普通模式记录，每条含 index / boss / boss_icon / score / star / "
                                  "time / avatar_text（出战代理人顿号串）/ avatars（列表，含 name / "
                                  "rarity / element / profession / rank / icon）/ buddy（邦布名）/ "
                                  "buffs（增益名顿号串）/ text（整行）"},
        {"name": "hard_count / hard_items", "desc": "绝境模式的条数与记录（结构同 items）"},
        {"name": "hard_rank", "desc": "绝境模式排名百分比"},
    ],
    sample=(
        "危局强袭战 · {nick_name}（{region_name}）\n"
        "总分 {total_score} · ★ × {total_star} · 前 {rank}\n"
        "{#items}{boss} · {score} 分 · {star}★ · {avatar_text}\n{/items}"
    ),
)
async def _api_zzz_deadly(ctx: Ctx) -> dict:
    """危局强袭战：默认文本 + 模板变量 vars + 官方原始数据 data（后两个给页面配模板用）。"""
    full_mode = _arg_full(ctx) or bool(ctx.opt("full", False))   # 先剥参数再解析 UID
    aid, uid, server, err = await _zzz_target(ctx)
    if err:
        return {"text": err}
    try:
        data = await record_api.deadly_assault(uid, server, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取危局强袭战失败：{exc}"}
    try:
        data = await asset_cache.rewrite_assets(data)   # 存档格式与页面查询保持一致（图片走本地）
    except Exception as exc:  # noqa: BLE001 —— 图片本地化失败不影响文字输出
        logger.debug(f"miyoushe 命令查询：图片本地化失败（忽略）{exc}")
    data = data if isinstance(data, dict) else {}
    _save_record("deadly", uid, server, data)
    if not data.get("has_data"):
        return {"text": "没有危局强袭战数据（本期可能未参与）"}

    pct = data.get("rank_percent")
    items = _deadly_rows(data.get("list"))
    hard_items = _deadly_rows(data.get("hard_list"))
    lines = [
        f"危局强袭战 · UID {uid}（{client.region_name(server)}）",
        f"总分：{data.get('total_score') or 0} · 星数：{data.get('total_star') or 0}",
    ]
    if pct:
        lines.append(f"排名：前 {float(pct) / 100:.2f}%")

    vars_ = {
        "uid": uid,
        "nick_name": str(data.get("nick_name") or ""),
        "region": server,
        "region_name": client.region_name(server),
        "has_data": True,
        "total_score": int(data.get("total_score") or 0),
        "total_star": int(data.get("total_star") or 0),
        "rank_percent": int(pct or 0),
        "rank": _pct_plus(pct),
        "period": _period(data.get("start_time"), data.get("end_time")),
        "start": _pt(data.get("start_time")),
        "end": _pt(data.get("end_time")),
        "count": len(items),
        "items": items,
        "hard_count": len(hard_items),
        "hard_items": hard_items,
        "hard_rank": _pct_plus(data.get("hard_rank_percent")),
    }
    # 「全面」版（勾选 全面 / QQ 带参数 full）：图片战报 + 头像左下角音擎图标。
    # 音擎实时查「角色详情」拿、按角色缓存，查不到的角色不画（优雅降级）。
    # ⚠️ 该接口有设备指纹风控：账号没绑设备就跳过音擎、并在图后补一句提醒（图照发）。
    if full_mode:
        weapons, warn = await zzz_weapon.resolve(data, "deadly", uid, server, aid)
        data["weapons"] = weapons             # 网页 frag 在头像左下角画音擎用
        if warn:
            data["weapon_warn"] = warn        # 网页 frag 顶部横幅也用这条
        show_time = bool(ctx.opt("show_time", False))
        return await _image_reply(
            ctx, lambda: record_image.render_deadly(data, show_time=show_time, weapons=weapons),
            lines, vars_, data, note=warn,
        )
    # 「输出方式 = 图片」→ 复用网页端那张图的渲染器（zzz/record/image.py 的
    # render_deadly，纯 Pillow、立绘走本地缓存）；发成功后返回 silent，不再跟发文字。
    if str(ctx.opt("output", "text")) == "image":
        show_time = bool(ctx.opt("show_time", False))
        return await _image_reply(
            ctx, lambda: record_image.render_deadly(data, show_time=show_time),
            lines, vars_, data,
        )
    return {"text": "\n".join(lines), "vars": vars_, "data": data}


@interface(
    "zzz_shiyu", "绝区零 · 式舆防卫战", "防线评级 / 总分 / 排名（可跟 UID 参数）",
    options=[
        {"key": "output", "label": "输出方式", "type": "select",
         "options": OUTPUT_OPTIONS, "default": "text",
         "hint": "选「图片消息」就发一张和网页上一样的防卫战战报图"
                 "（纯 Pillow 绘制，版面照抄网页）；发送失败会退回文字并附上原因。"},
        SHOW_TIME_OPTION,
        SHOW_FOURTH_OPTION,
    ],
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "nick_name", "desc": "游戏内昵称"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "rating", "desc": "本期总评级（S+ / S / A…）"},
        {"name": "score", "desc": "本期总分（没给总分时按第五防线各队累加）"},
        {"name": "max_score", "desc": "总分上限"},
        {"name": "rank", "desc": "排名百分比（如 51.46%+）"},
        {"name": "rank_percent", "desc": "排名原值（官方口径 = 百分比 ×100，如 5146）"},
        {"name": "period / start / end", "desc": "本期赛期（起止）"},
        {"name": "fifth_rating / fifth_buffer", "desc": "第五防线的评级 / 增益名"},
        {"name": "fourth_rating / fourth_buffer", "desc": "第四防线的评级 / 增益名"},
        {"name": "count", "desc": "出战小队条数（= items 的长度）"},
        {"name": "items", "desc": "出战小队，每条含 index / no（本层第几队）/ layer（第五防线…）/ "
                                  "rating / score / time / avatar_text / avatars（列表，字段同危局）/ "
                                  "buddy（邦布名）/ buddy_icon / buddy_rarity（S/A）/ monster（BOSS 立绘）/ "
                                  "buffer（防线效果名）/ text（整行）。"
                                  "**第四防线的条目只有「显示第四防线」打开时才在里面**"},
        {"name": "show_fourth", "desc": "本次是否带上了第四防线的小队（true/false）"},
    ],
    sample=(
        "式舆防卫战 · {nick_name}（{region_name}）\n"
        "评级 {rating} · 总分 {score} · 前 {rank}\n"
        "{#items}{layer}第 {no} 队 · {rating} · {score} 分 · {avatar_text}\n{/items}"
    ),
)
async def _api_zzz_shiyu(ctx: Ctx) -> dict:
    """式舆防卫战：默认文本 + 模板变量 vars + 官方原始数据 data。

    ⚠️ 官方**没有**「第一 / 第二 / 第三防线」的明细（历史版本有，现在只剩第四、第五层），
    所以默认文本改成「总评级 / 总分 / 排名 + 四五两层的概况」——
    老代码循环 first/second/third 永远取不到，只会输出「没有可展示的防线数据」。
    """
    full_mode = _arg_full(ctx) or bool(ctx.opt("full", False))   # 先剥参数再解析 UID
    aid, uid, server, err = await _zzz_target(ctx)
    if err:
        return {"text": err}
    try:
        data = await record_api.shiyu_defense(uid, server, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取式舆防卫战失败：{exc}"}
    try:
        data = await asset_cache.rewrite_assets(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"miyoushe 命令查询：图片本地化失败（忽略）{exc}")
    data = data if isinstance(data, dict) else {}
    _save_record("shiyu", uid, server, data)

    info, brief = _shiyu_info(data)
    view = _shiyu_view(data)
    # 第四防线要不要发出去：官方那份明细**没有得分/评级/立绘**，只有出战头像，
    # 默认关掉（详细设置里的「显示第四防线」）。view 本身始终是完整的，
    # 这里只决定「发什么」—— 这样 fourth_* 变量仍然可用，想拼模板也拿得到。
    show_fourth = bool(ctx.opt("show_fourth", False))
    fifth_teams = view["fifth"]["teams"]
    teams = fifth_teams + view["fourth"]["teams"] if show_fourth else list(fifth_teams)
    rating, score = view["rating"], view["score"]
    pct = brief.get("rank_percent")
    # 本期没打（brief 全是 0 / 空串、也没有小队）时跟「没参与」一个口径，
    # 不要发一条只有标题、看着像坏掉的消息。
    if not (teams or rating or score or pct):
        return {"text": "没有式舆防卫战数据（本期可能未参与）"}

    parts = []
    if rating:
        parts.append(f"评级：{rating}")
    if score:
        parts.append(f"总分：{score}")
    if pct:
        parts.append(f"排名：前 {float(pct) / 100:.2f}%")
    lines = [f"式舆防卫战 · UID {uid}（{client.region_name(server)}）"]
    if parts:
        lines.append(" · ".join(parts))
    layer_bits = [
        f"{label} {len(view[key]['teams'])} 队"
        for key, label in ((("fifth", "第五防线"), ("fourth", "第四防线"))
                           if show_fourth else (("fifth", "第五防线"),))
        if view[key]["teams"]
    ]
    if layer_bits:
        lines.append(" · ".join(layer_bits))
    if len(lines) == 1:
        lines.append("（没有可展示的防线数据）")

    vars_ = {
        "uid": uid,
        "nick_name": view["nick_name"],
        "region": server,
        "region_name": client.region_name(server),
        "rating": rating,
        "score": score,
        "max_score": int(brief.get("max_score") or 0),
        "rank_percent": int(pct or 0),
        "rank": _pct_plus(pct),
        "period": view["period"],
        "start": _pt(info.get("hadal_begin_time")),
        "end": _pt(info.get("hadal_end_time")),
        "fifth_rating": view["fifth"]["rating"],
        "fifth_buffer": view["fifth"]["buffer"],
        "fourth_rating": view["fourth"]["rating"],
        "fourth_buffer": view["fourth"]["buffer"],
        "show_fourth": show_fourth,
        "count": len(teams),
        "items": teams,
    }
    # 「全面」版（勾选 全面 / QQ 带参数 full）：图片战报 + 头像左下角音擎图标。
    # ⚠️ 同危局：账号没绑设备就跳过音擎、图后补一句提醒（图照发，普通查询不受影响）。
    if full_mode:
        weapons, warn = await zzz_weapon.resolve(view, "shiyu", uid, server, aid)
        data["weapons"] = weapons             # 网页 frag 在头像左下角画音擎用
        if warn:
            data["weapon_warn"] = warn        # 网页 frag 顶部横幅也用这条
        show_time = bool(ctx.opt("show_time", False))
        return await _image_reply(
            ctx,
            lambda: shiyu_image.render_shiyu(view, show_time=show_time,
                                             show_fourth=show_fourth, weapons=weapons),
            lines, vars_, data, note=warn,
        )
    # 「输出方式 = 图片」→ shiyu_image.render_shiyu 把网页那套版面用 Pillow 重画一遍
    # （网页是 HTML/CSS，QQ 里发不了 DOM）；发成功后返回 silent，不再跟发文字。
    if str(ctx.opt("output", "text")) == "image":
        show_time = bool(ctx.opt("show_time", False))
        return await _image_reply(
            ctx,
            lambda: shiyu_image.render_shiyu(view, show_time=show_time,
                                             show_fourth=show_fourth),
            lines, vars_, data,
        )
    return {"text": "\n".join(lines), "vars": vars_, "data": data}


# 注：调频（抽卡）总结接口在 social_gacha.py —— 这边只留战绩类接口，
# 抽卡总结带图片渲染 + 本地存档增量同步，单独成块（见 social.py 的导入顺序）。
