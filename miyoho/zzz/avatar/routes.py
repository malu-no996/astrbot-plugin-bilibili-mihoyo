"""角色详情接口（原 src/zzz/avatar/routes.py 的 AstrBot 移植）。

「角色查询」页点一张角色卡片时调它，返回音擎 / 驱动盘 / 技能加点 / 影画 / 面板属性。
两套数据源（米游社官方 avatar/info、Enka 展示栏），归一化在 detail.py。

官方接口同样带「角色详情」风控（retcode 10041），命中时**自动降级 Enka**；
两边都没有就在响应里说清楚原因（配设备 or 开角色详情公开），由页面展示。
"""

from __future__ import annotations

import httpx

from astrbot.api.web import json_response, request

from ...core import asset_cache, store
from ...core import mys as client
from ...core.web import call, fail
from . import char_map, detail as avatar_detail
from .api import avatar_basic, avatar_info, enka_showcase, index


def _qstr(key: str, default: str = "") -> str:
    return str(request.query.get(key, default) or default)


def _ok(**kwargs):
    return json_response({"ok": True, **kwargs})


async def api_zzz_avatar_info():
    """单个角色的详情。

    `id` 是角色 id（`avatar/basic` 里那个）；一次只查一个 —— 点一张卡片拉一次，
    比起进页面就把十几个角色全拉下来，这样风控时也不会全部失败得莫名其妙。
    """
    uid, server = _qstr("uid"), _qstr("server", "prod_gf_cn")
    avatar_id, account_id = _qstr("id"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（先在左侧选一个绝区零角色）")
    if not avatar_id:
        return fail("缺少角色 id（点角色卡片打开详情）")

    try:
        code, rows = await avatar_info(
            uid, server, [avatar_id], account_id=account_id or None
        )
    except (client.MysError, httpx.HTTPError) as exc:
        return await _fallback(uid, avatar_id, f"官方接口异常（{exc}）")

    for row in rows:
        if str(row.get("id") or "") == str(avatar_id):
            detail = avatar_detail.from_official(row)
            detail["source_name"] = "米游社"
            # 角色 / 音擎 / 驱动盘图标统一走后台本地缓存（米游社 CDN 转本地路由）
            detail = await asset_cache.rewrite_assets(detail)
            # raw = 官方 avatar/info 这一条的**原始数据**（原样返回，连图片也是官方远程 URL），
            # 给页面上的「查看 JSON 数据」按钮用 —— 看的就是官方究竟返回了什么。
            return _ok(detail=detail, raw=row)

    if code == 0:
        return fail(f"官方没返回这个角色的详情（角色 id {avatar_id}）")
    return await _fallback(uid, avatar_id, f"米游社未开放角色详情（retcode={code}）")


async def _fallback(uid: str, avatar_id: str, why: str):
    """官方拿不到 → 改用 Enka 展示栏（只有放进展示栏的角色才有）。"""
    try:
        enka = await enka_showcase(uid)
    except (client.MysError, httpx.HTTPError) as exc:
        return fail(
            f"{why}；Enka 备用源也不可用（{exc}）。"
            "可到「设备配置」登记真机设备，或在米游社开启角色详情公开后重试。"
        )
    detail = (enka.get("PlayerInfo") or {}).get("ShowcaseDetail") or {}
    for char in detail.get("AvatarList") or []:
        if not isinstance(char, dict):
            continue
        if str(char.get("Id") or "") == str(avatar_id):
            out = avatar_detail.from_enka(char)
            out["source_name"] = "Enka"
            out["note"] = why + "；" + out["note"]
            # raw = Enka 展示栏里这个角色的原始数据（同样原样给，供「查看 JSON 数据」）
            return _ok(detail=await asset_cache.rewrite_assets(out), raw=char)
    return fail(
        f"{why}；这个角色也不在 Enka 展示栏里（游戏内「展示角色」只有展示栏那几个）。"
        "把角色放进展示栏，或到「设备配置」登记真机设备后重试。"
    )


# ---------------- 绝区零：绑定角色 ----------------


async def api_zzz_roles():
    """某个账号绑定的绝区零角色列表（uid / 服务器 / 昵称）。"""
    aid = _qstr("account_id") or store.current_id()
    if not aid:
        return fail("没有可用的米游社账号，请先扫码登录")
    if not store.logged_in(aid):
        return fail("该账号未登录或凭证已失效，请重新扫码")
    err, roles = await call(client.bind_roles(aid), "读取绝区零角色列表失败")
    if err:
        return err
    roles = roles if isinstance(roles, list) else []
    for r in roles:
        r["region_name"] = client.region_name(r.get("region"))
    return json_response({"ok": True, "account_id": aid, "roles": roles})


# ---------------- 绝区零：角色查询（账号已拥有角色） ----------------


def _norm_official(a: dict) -> dict:
    """官方 avatar/basic 的 avatar_list[] → 前端模型。

    字段对照 SIMNet / ZZZeroUID 的 `ZZZAvatarBasic`：id / level / name_mi18n /
    full_name_mi18n / element_type / camp_name_mi18n / avatar_profession / rarity /
    icon_paths{group_icon_path,hollow_icon_path} / rank(影画数) / is_chosen。
    这里对每个字段都留了别名兜底，避免上游改名就整块空白。
    """
    av_id = a.get("id") or a.get("avatar_id") or a.get("role_id") or 0
    # ⚠️ 头像必须用官方**方头像**（square_avatar）：avatar/basic **不返回** role_square_url，
    # 只给 icon_paths{hollow_icon_path, group_icon_path} —— 那两个是 180×64 的横版横幅
    # （空洞 / 编队用的「脸」），塞进方形头像框就是「角色对、图不对」。
    # char_map.square_avatar 会在接口没给 role_square_url 时**按角色 id 拼官方 CDN 地址**，
    # 和「角色详情」浮层、危局/防卫战战绩里用的一定是同一张图，两边显示才一致。
    icon = char_map.square_avatar(a)
    name, full, rarity, element, profession, camp, _sprite = char_map.lookup(av_id) or ("",) * 7
    return {
        "id": str(av_id),
        "name": a.get("name_mi18n") or name or f"角色 {av_id}",
        "full_name": a.get("full_name_mi18n") or full or "",
        "rarity": str(a.get("rarity") or rarity or "").upper(),
        "element": char_map.element_name(a.get("element_type")) or element,
        "profession": char_map.profession_name(a.get("avatar_profession")) or profession,
        "camp": a.get("camp_name_mi18n") or camp or "",
        "level": int(a.get("level") or 0),
        "rank": int(a.get("rank") or 0),
        "icon": icon,
        "chosen": bool(a.get("is_chosen")),
        "source": "official",
    }


def _norm_enka(c: dict) -> dict:
    """Enka ShowcaseDetail.AvatarList[] → 前端模型（名字/元素/职业由 char_map 补）。

    Enka 只给 Id / Level / PromotionLevel（突破）等，**不提供影画数（rank）**，
    所以降级源的 rank 恒为 0（前端不渲染角标）。
    """
    av_id = c.get("Id") or 0
    name, full, rarity, element, profession, camp, sprite = char_map.lookup(av_id) or ("",) * 7
    return {
        "id": str(av_id),
        "name": name or f"角色 {av_id}",
        "full_name": full or "",
        "rarity": str(rarity or "").upper(),
        "element": element or "",
        "profession": profession or "",
        "camp": camp or "",
        "level": int(c.get("Level") or 0),
        "rank": 0,
        "promotion": int(c.get("PromotionLevel") or 0),
        # 头像同样走官方**方头像**：Enka 的 IconRole 是另一套画法，和角色详情 / 战绩页
        # 用的官方 role_square_avatar 不是同一张 —— 前端列表就会「角色对、图不对」。
        # 拼不出来（连 id 都没有）才退回 Enka 那张。
        "icon": char_map.square_avatar({"id": av_id}) or char_map.enka_icon(sprite),
        "chosen": False,
        "source": "enka",
    }


async def api_zzz_avatars():
    """账号已拥有的角色列表（官方 avatar/basic）。

    绝区零该接口带「角色详情」风控：未开启公开时返回 retcode=10041（危局 / 邦布不受影响）。
    命中风控或官方异常时，**自动降级到 Enka 的展示角色**，响应里的 source / note 说明来源。
    """
    uid, server, account_id = _qstr("uid"), _qstr("server", "prod_gf_cn"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")

    avatars: list[dict] = []
    source = "official"
    note = ""
    try:
        code, data = await avatar_basic(uid, server, account_id=account_id or None)
    except (client.MysError, httpx.HTTPError) as exc:
        note = f"官方接口异常（{exc}），已改用 Enka 展示数据"
    else:
        rows = data.get("avatar_list") if isinstance(data, dict) else None
        if code == 0 and rows:
            avatars = [_norm_official(a) for a in rows if isinstance(a, dict)]
        else:
            note = f"米游社未开放该角色的详情（retcode={code}），已改用 Enka 展示数据"

    if not avatars:
        source = "enka"
        try:
            enka = await enka_showcase(uid)
        except (client.MysError, httpx.HTTPError) as exc:
            tip = (note + "；" if note else "") + f"Enka 备用源也不可用（{exc}）"
            return fail(
                "读取角色列表失败：" + tip
                + "。可在米游社「我的 → 设置 → 隐私设置」开启角色详情公开后重试。"
            )
        detail = (enka.get("PlayerInfo") or {}).get("ShowcaseDetail") or {}
        avatars = [_norm_enka(c) for c in (detail.get("AvatarList") or []) if isinstance(c, dict)]
        if not avatars:
            return fail("该账号没有可展示的角色（游戏内「展示栏」为空，官方接口也未开放详情）")
        note = (note or "米游社未开放角色详情，当前为 Enka 展示数据") + "（仅展示栏角色，非全部拥有角色）"

    # 图片统一走后台本地缓存（Enka / 米游社 CDN 都转本地路由，前台生图无跨域）
    avatars = await asset_cache.rewrite_assets(avatars)

    # 排序：主要角色 → S → A → 等级高
    tier = {"S": 0, "A": 1}
    avatars.sort(
        key=lambda a: (
            0 if a.get("chosen") else 1,
            tier.get(a.get("rarity") or "", 2),
            -int(a.get("level") or 0),
        )
    )
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": server,
            "region_name": client.region_name(server),
            "source": source,
            "source_name": "米游社" if source == "official" else "Enka",
            "count": len(avatars),
            "avatars": avatars,
            "note": note,
        }
    )


# ---------------- 绝区零：玩家概览（/index） ----------------


def _pv_avatar(a: dict) -> dict:
    """官方 /index 的 avatar_list[] → 前端模型（名字/元素/职业由 char_map 补）。"""
    a = a if isinstance(a, dict) else {}
    info = char_map.lookup(a.get("id"))
    name = str(a.get("name_mi18n") or a.get("full_name_mi18n")
               or (info[0] if info else "") or "")
    return {
        "id": str(a.get("id") or ""),
        "name": name,
        "level": int(a.get("level") or 0),
        "rank": int(a.get("rank") or 0),
        "rarity": str(a.get("rarity") or (info[2] if len(info) > 2 else "") or "").upper(),
        "element": char_map.element_name(a.get("element_type")) or (info[3] if len(info) > 3 else ""),
        "profession": char_map.profession_name(a.get("avatar_profession"))
                      or (info[4] if len(info) > 4 else ""),
        "camp": str(a.get("camp_name_mi18n") or ""),
        # 图标字段命名成 `icon` 才能被 asset_cache.rewrite_assets 命中（IMAGE_KEYS 含 "icon"）
        # ⚠️ 必须取官方**方头像**：/index 的 avatar_list 里本来就有 role_square_url，
        # 以前却只用了 hollow_icon_path（180×64 横版横幅）→ 展示角色头像是错的图。
        "icon": char_map.square_avatar(a),
    }


async def api_zzz_profile():
    """玩家概览（/index）：活跃天数 / 角色数 / 邦布数 / 层数 + 展示角色与邦布。"""
    uid, server, account_id = _qstr("uid"), _qstr("server", "prod_gf_cn"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    try:
        code, data = await index(uid, server, account_id=account_id or None)
    except (client.MysError, httpx.HTTPError) as exc:
        return fail(f"读取玩家概览失败：{exc}")
    if code == 10041:
        return fail("读取玩家概览失败：角色详情未公开 / 设备不受信（retcode=10041）。"
                    "请在米游社「我的角色」里开启角色详情，或在面板「米游社 → 设备配置」里登记设备后重试")
    if code != 0:
        return fail(f"读取玩家概览失败（retcode={code}）")
    data = data if isinstance(data, dict) else {}
    if not data:
        return fail("没有玩家概览数据")
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    avatars = [_pv_avatar(a) for a in (data.get("avatar_list") or []) if isinstance(a, dict)]
    buddies = []
    for b in (data.get("buddy_list") or []):
        if not isinstance(b, dict):
            continue
        star = int(b.get("star") or 0)
        buddies.append({
            "id": str(b.get("id") or ""),
            "name": str(b.get("name") or ""),
            "rarity": str(b.get("rarity") or "").upper(),
            "level": int(b.get("level") or 0),
            "star": star,
        })
    view = {
        "active_days": int(stats.get("active_days") or 0),
        "avatar_num": int(stats.get("avatar_num") or 0),
        "buddy_num": int(stats.get("buddy_num") or 0),
        "world_level_name": str(stats.get("world_level_name") or ""),
        "zone_layer": int(stats.get("cur_period_zone_layer_count") or 0),
        "head_icon": str(data.get("cur_head_icon_url") or ""),
        "avatars": avatars,
        "buddies": buddies,
    }
    # 头像 / 角色图标走后台本地缓存（CDN 转本地路由，页面无跨域问题）
    await asset_cache.rewrite_assets(view)
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": server,
            "region_name": client.region_name(server),
            "view": view,
            "data": data,
        }
    )


# ⚠️ 原来的「角色面板出图」接口（`GET /zzz/card`）已于 2026-10-05 删除（网页版页签下掉）。
# 出图能力由 QQ 命令承担 —— `social/card.py`（接口 key 仍是 `zzz_card`）
# 用 `detail.from_official` + `panel.render_agent_detail` 画「代理人详细」面板。


ROUTES = [
    ("zzz/avatar/info", "GET", api_zzz_avatar_info, "单个角色的详情"),
    ("zzz/roles", "GET", api_zzz_roles, "账号绑定的绝区零角色列表"),
    ("zzz/avatars", "GET", api_zzz_avatars, "账号已拥有角色列表"),
    ("zzz/profile", "GET", api_zzz_profile, "玩家概览"),
]
