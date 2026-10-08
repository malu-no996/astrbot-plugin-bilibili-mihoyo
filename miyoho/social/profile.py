"""社交命令 · 绝区零玩家概览（/index）：活跃天数 / 角色数 / 邦布数 / 层数。

对应 ZZZeroUID 的「查询」命令那一屏：不是某一期的成绩，而是**这个账号的整体档案**
（玩了多少天、有多少角色和邦布、爬到第几层），外加展示栏的角色与邦布清单。

⚠️ 这个接口（/index）和 avatar/basic 一样带**「角色详情」风控**：米游社里没开
「角色详情公开」或设备不受信 → `retcode=10041`。这里不把它当异常抛，
而是给一句「去开启角色详情」的提示（解法见 `core/device.py`）。
"""

from __future__ import annotations

from typing import Any

from ..core import mys as client
from ..zzz.avatar import api as avatar_api, char_map
from .base import _bangboo_name, _zzz_nick, _zzz_target, _zzz_title
from .core import Ctx, interface


def _sub(d: Any, key: str) -> dict:
    v = (d or {}).get(key) if isinstance(d, dict) else None
    return v if isinstance(v, dict) else {}


@interface(
    "zzz_profile", "绝区零 · 玩家概览",
    "活跃天数 / 拥有角色数 / 邦布数 / 当前层数 + 展示角色与邦布（可跟 UID 参数）",
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "nick_name", "desc": "游戏内昵称（这个接口不返回，从角色列表取）"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "active_days", "desc": "活跃天数"},
        {"name": "avatar_num", "desc": "拥有角色数"},
        {"name": "buddy_num", "desc": "邦布数"},
        {"name": "world_level_name", "desc": "当前层数 / 等级名（官方 world_level_name）"},
        {"name": "zone_layer", "desc": "本期零号空洞层数（官方 cur_period_zone_layer_count）"},
        {"name": "icon", "desc": "当前头像图片地址"},
        {"name": "count", "desc": "展示角色条数"},
        {"name": "items", "desc": "展示角色，每条含 index / name / level / rank（影画）/ "
                                  "rarity（S/A）/ element / profession / camp / "
                                  "icon / text（整行）"},
        {"name": "buddy_count / buddies", "desc": "邦布条数与列表（每条含 index / name / "
                                                  "rarity / level / star）"},
    ],
    sample=(
        "绝区零档案 · {nick_name}（{region_name}）\n"
        "活跃 {active_days} 天 · 角色 {avatar_num} · 邦布 {buddy_num} · {world_level_name}\n"
        "{#items}{name} Lv{level}（{rank} 影画）\n{/items}"
    ),
)
async def _api_zzz_profile(ctx: Ctx) -> dict:
    """玩家概览：默认文本 + 模板变量 vars + 官方原始数据 data。"""
    aid, uid, server, err = await _zzz_target(ctx)
    if err:
        return {"text": err}
    try:
        code, data = await avatar_api.index(uid, server, account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取玩家概览失败：{exc}"}
    if code == 10041:
        return {"text": "读取玩家概览失败：角色详情未公开 / 设备不受信（retcode=10041）。"
                        "请在米游社「我的角色」里开启角色详情，或在管理页「米游社 → 设备配置」"
                        "里登记设备后重试"}
    if code != 0:
        return {"text": f"读取玩家概览失败（retcode={code}）"}
    data = data if isinstance(data, dict) else {}
    if not data:
        return {"text": "没有玩家概览数据"}

    stats = _sub(data, "stats")
    items = []
    for i, a in enumerate(data.get("avatar_list") or [], 1):
        a = a if isinstance(a, dict) else {}
        info = char_map.lookup(a.get("id"))
        rank = int(a.get("rank") or 0)
        name = str(a.get("name_mi18n") or a.get("full_name_mi18n")
                   or (info[0] if info else "") or "")
        level = int(a.get("level") or 0)
        items.append({
            "index": i,
            "id": str(a.get("id") or ""),
            "name": name,
            "level": level,
            "rank": rank,
            "rarity": str(a.get("rarity") or (info[2] if len(info) > 2 else "") or ""),
            "element": char_map.element_name(a.get("element_type")) or (info[3] if len(info) > 3 else ""),
            "profession": char_map.profession_name(a.get("avatar_profession"))
                          or (info[4] if len(info) > 4 else ""),
            "camp": str(a.get("camp_name_mi18n") or ""),
            # ⚠️ 必须取官方**方头像**（/index 里本来就有 role_square_url）；hollow/group_icon_path
            # 是 180×64 的横版横幅，当头像用就是「角色对、图不对」。
            "icon": char_map.square_avatar(a),
            "text": f"{name} Lv{level}" + (f"（{rank} 影画）" if rank else ""),
        })
    buddies = []
    for i, b in enumerate(data.get("buddy_list") or [], 1):
        b = b if isinstance(b, dict) else {}
        star = int(b.get("star") or 0)
        name = str(b.get("name") or _bangboo_name(b.get("id")) or "")
        buddies.append({
            "index": i,
            "id": str(b.get("id") or ""),
            "name": name,
            "rarity": str(b.get("rarity") or ""),
            "level": int(b.get("level") or 0),
            "star": star,
            "text": f"{name}" + (f" {star}★" if star else ""),
        })

    days = int(stats.get("active_days") or 0)
    avatars = int(stats.get("avatar_num") or 0)
    buddies_num = int(stats.get("buddy_num") or 0)
    level_name = str(stats.get("world_level_name") or "")
    zone = int(stats.get("cur_period_zone_layer_count") or 0)

    # 档案接口不返回昵称 → 从角色列表取（`_zzz_target` 不带参数时已拉过，命中缓存）
    nick = await _zzz_nick(aid, uid)
    lines = [await _zzz_title(aid, uid, server, "绝区零档案", nick)]
    head = [f"活跃 {days} 天" if days else "",
            f"角色 {avatars}" if avatars else "",
            f"邦布 {buddies_num}" if buddies_num else ""]
    if level_name:
        head.append(level_name)
    head = [h for h in head if h]
    if head:
        lines.append(" · ".join(head))
    if items:
        lines.append("展示角色：" + " · ".join(it["text"] for it in items))
    if buddies:
        lines.append("邦布：" + " · ".join(b["text"] for b in buddies if b["text"]))

    vars_ = {
        "uid": uid,
        "region": server,
        "region_name": client.region_name(server),
        "nick_name": nick,
        "active_days": days,
        "avatar_num": avatars,
        "buddy_num": buddies_num,
        "world_level_name": level_name,
        "zone_layer": zone,
        "icon": str(data.get("cur_head_icon_url") or ""),
        "count": len(items),
        "items": items,
        "buddy_count": len(buddies),
        "buddies": buddies,
    }
    return {"text": "\n".join(lines), "vars": vars_, "data": data}
