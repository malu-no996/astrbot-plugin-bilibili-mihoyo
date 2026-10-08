"""绝区零 · 战绩 的管理页路由（原 src/zzz/record/routes.py 的 AstrBot 移植）。

  GET  zzz/shiyu          式舆防卫战（previous=true 取往期）
  GET  zzz/deadly         危局强袭战
  GET  zzz/abyss          零号空洞摘要
  GET  zzz/zenkov         迷宫诡域摘要
  GET  zzz/void           临界推演（void_front_battle_period_detail）
  GET  zzz/note           实时便笺（电量 / 活跃度 / 委托 / 周常）
  GET  zzz/records        本地存档的赛期列表（官方只有本期/上期，历史全靠本地攒）
  GET  zzz/records/item   某一期的完整存档
  POST zzz/records/drop   删掉某一期存档
  GET  zzz/asset/<name>   后台缓存的米游社图片（name 是 URL 哈希，天然防目录穿越）
  GET  zzz/deadly/image   后台把危局结果渲染成 PNG（下载用，页面截图走 html-to-image）

每次查询都会**顺手按赛期存档一份**（`capture._save_record`），存档失败绝不影响查询本身。

与原版的差异：FastAPI 装饰器 → ROUTES 注册表；query 参数用 astrbot request 读；
`/zzz/asset/<name>` 改 file_response（前端 <img> 走 Page 静态目录镜像，不走这里）。
"""
from __future__ import annotations

import time

from loguru import logger

from astrbot.api.web import file_response, json_response, request

from ...core import asset_cache
from ...core import mys as client              # 战绩接口搬去 api.py 了，这里只留 region_name
from ...core.web import body, call, fail
from . import image as record_image
from . import store as record_store
from . import weapon as record_weapon   # 「全面」查询：头像左下角音擎图标（实时查+缓存）
from .api import (
    abyss_abstract,
    deadly_assault,
    shiyu_defense,
    void_front_battle,
    zenkov_abstract,
    zzz_note,
)
from .capture import _save_record


def _num(*vals: object) -> int:
    """按顺序取第一个「像数字」的值 → int（都取不到给 0）。

    官方同一类字段键名不统一（`current` / `cur` / `num`），这里兜一下，
    免得写 `d.get("cur")` 结果官方给的是 `current` 而静默变 0。
    """
    for v in vals:
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def _qbool(key: str) -> bool:
    """query 里的布尔参数（"1" / "true" 都算真）。"""
    return str(request.query.get(key, "") or "").strip().lower() in ("1", "true", "yes")


def _qstr(key: str, default: str = "") -> str:
    return str(request.query.get(key, default) or default)


# ---------------- 绝区零：式舆防卫战 ----------------


async def api_zzz_shiyu():
    """式舆防卫战查询（hadal_info_v2）。previous=true 取往期。

    `full=true` = 网页查询页勾了「全面」：顺手给每个出战代理人查音擎图标
    （`data.weapons` = {角色id: 图标本地路由}，frag 在头像左下角画）。音擎要打
    「角色详情」接口（有设备指纹风控），账号没绑设备就跳过并给 `weapon_warn` 提醒；
    查询本身不受影响（战绩接口不吃这套风控）。
    """
    uid, server = _qstr("uid"), _qstr("server", "prod_gf_cn")
    previous, account_id, full = _qbool("previous"), _qstr("account_id"), _qbool("full")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        shiyu_defense(uid, server, previous=previous, account_id=account_id or None),
        "读取式舆防卫战失败",
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    data = await asset_cache.rewrite_assets(data)  # 立绘/头像改后台本地路由，规避 CDN 跨域
    weapons, warn = {}, ""
    if full:
        weapons, warn = await record_weapon.resolve(data, "shiyu", uid, server, account_id or "")
        if weapons:
            data["weapons"] = weapons
        if warn:
            data["weapon_warn"] = warn
    record = _save_record("shiyu", uid, server, data)   # 顺手按赛期存档（同赛期只留最新）
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": server,
            "region_name": client.region_name(server),
            "previous": bool(previous),
            "record": record,          # 这期的存档 meta（赛期 / 更新时刻）；空 dict = 没存上
            "data": data,
        }
    )


# ---------------- 绝区零：危局强袭战 ----------------


async def api_zzz_deadly():
    """危局强袭战查询（hadal_mem_detail_v2）。previous=true 取往期。

    `full=true` = 网页查询页勾了「全面」：语义同 zzz/shiyu 的 full（见上）。
    """
    uid, server = _qstr("uid"), _qstr("server", "prod_gf_cn")
    previous, account_id, full = _qbool("previous"), _qstr("account_id"), _qbool("full")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        deadly_assault(uid, server, previous=previous, account_id=account_id or None),
        "读取危局强袭战失败",
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    data = await asset_cache.rewrite_assets(data)  # 立绘/头像改后台本地路由，规避 CDN 跨域
    weapons, warn = {}, ""
    if full:
        weapons, warn = await record_weapon.resolve(data, "deadly", uid, server, account_id or "")
        if weapons:
            data["weapons"] = weapons
        if warn:
            data["weapon_warn"] = warn
    record = _save_record("deadly", uid, server, data)  # 顺手按赛期存档（同赛期只留最新）
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": server,
            "region_name": client.region_name(server),
            "previous": bool(previous),
            "record": record,          # 这期的存档 meta（赛期 / 更新时刻）；空 dict = 没存上
            "data": data,
        }
    )


# ---------------- 绝区零：战绩本地存档（历史） ----------------


async def api_zzz_records():
    """本地存档的赛期列表（官方只有本期/上期，往前的历史全在这里）。

    数据来源：每次查询（页面 / 命令 / 后台定时）都会把结果按赛期存一份，
    同一赛期只留最新版本，见 record_store 模块说明。
    """
    kind, uid = _qstr("kind", "deadly"), _qstr("uid")
    if kind not in record_store.KINDS:
        return fail(f"未知战绩类型：{kind}")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    return json_response({"ok": True, "kind": kind, **record_store.list_periods(kind, uid)})


async def api_zzz_records_item():
    """取某一期的完整存档（前端用同一套渲染逻辑直接画出来）。"""
    kind, uid, key = _qstr("kind", "deadly"), _qstr("uid"), _qstr("key")
    if kind not in record_store.KINDS:
        return fail(f"未知战绩类型：{kind}")
    if not uid or not key:
        return fail("缺少 uid / 赛期 key")
    entry = record_store.get_period(kind, uid, key)
    if not entry:
        return fail("本地没有这一期的存档", 404)
    return json_response(
        {
            "ok": True,
            "kind": kind,
            "uid": uid,
            "key": key,
            "region_name": client.region_name(entry.get("server") or ""),
            "previous": False,
            "record": {k: v for k, v in entry.items() if k != "data"},
            "data": entry.get("data") or {},
        }
    )


async def api_zzz_records_drop():
    """删掉某一期存档（存错了 / 测试数据兜底）。body: {kind, uid, key}。"""
    payload = await body()
    kind = str(payload.get("kind") or "")
    uid = str(payload.get("uid") or "")
    key = str(payload.get("key") or "")
    if kind not in record_store.KINDS or not uid or not key:
        return fail("参数不全（需要 kind / uid / key）")
    done = record_store.drop_period(kind, uid, key)
    return json_response({"ok": bool(done), "message": "已删除该期存档" if done else "没有找到这一期"})


# ---------------- 绝区零：零号空洞 ----------------


async def api_zzz_abyss():
    """零号空洞摘要（abyss_abstract）。"""
    uid, server, account_id = _qstr("uid"), _qstr("server", "prod_gf_cn"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        abyss_abstract(uid, server, account_id=account_id or None),
        "读取零号空洞失败",
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    data = await asset_cache.rewrite_assets(data)  # 图标改后台本地路由，规避 CDN 跨域
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": server,
            "region_name": client.region_name(server),
            "data": data,
        }
    )


# ---------------- 绝区零：迷宫诡域 ----------------


async def api_zzz_zenkov():
    """迷宫诡域摘要（zenkov_abstract_info）。"""
    uid, server, account_id = _qstr("uid"), _qstr("server", "prod_gf_cn"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        zenkov_abstract(uid, server, account_id=account_id or None),
        "读取迷宫诡域失败",
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    data = await asset_cache.rewrite_assets(data)
    return json_response(
        {
            "ok": True,
            "uid": uid,
            "server": server,
            "region_name": client.region_name(server),
            "data": data,
        }
    )


# ---------------- 绝区零：实时便笺（体力） ----------------


async def api_zzz_note():
    """实时便笺（/note）：电量 / 活跃度 / 刮刮乐 / 录像店 / 悬赏委托 / 周常。"""
    uid, server, account_id = _qstr("uid"), _qstr("server", "prod_gf_cn"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        zzz_note(uid, server, account_id=account_id or None),
        "读取实时便笺失败",
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    energy = data.get("energy") if isinstance(data.get("energy"), dict) else {}
    prog = energy.get("progress") if isinstance(energy.get("progress"), dict) else {}
    vitality = data.get("vitality") if isinstance(data.get("vitality"), dict) else {}
    bounty = data.get("s2_bounty_commission") or data.get("bounty_commission") or {}
    bounty = bounty if isinstance(bounty, dict) else {}
    weekly = data.get("weekly_task") if isinstance(data.get("weekly_task"), dict) else {}
    card = str(data.get("card_sign") or "")
    sale = str((data.get("vhs_sale") or {}).get("sale_state") or "")
    # ⚠️ 电量 / 活跃度官方给的是 `current`（不是 `cur`），写错会静默变 0。
    # 周常 `refresh_time` 是**距下次刷新的剩余秒数**（不是秒级时间戳！），
    # 当时间戳渲染会出来「1970.01.01」这种鬼东西。
    view = {
        "energy_cur": _num(prog.get("current"), prog.get("cur")),
        "energy_max": _num(prog.get("max")),
        "energy_restore": _num(energy.get("restore")),
        "vitality_cur": _num(vitality.get("current"), vitality.get("cur")),
        "vitality_max": _num(vitality.get("max")),
        "card_sign": "已刮" if "done" in card.lower() else "未刮",
        "vhs_sale": "营业中" if "doing" in sale.lower() else "未营业",
        "bounty_cur": _num(bounty.get("num"), bounty.get("cur")),
        "bounty_max": _num(bounty.get("total"), bounty.get("max")),
        "weekly_cur": _num(weekly.get("cur_point")),
        "weekly_max": _num(weekly.get("max_point")),
        "weekly_refresh_sec": _num(weekly.get("refresh_time")),
    }
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


# ---------------- 绝区零：临界推演 ----------------


async def api_zzz_void():
    """临界推演（void_front_battle_period_detail）。

    `void_front_id` 留空 = 取本期（先查摘要拿官方当前期号；老接口写死 102 已经 404）。
    `previous=true` 取上期。
    """
    uid, server = _qstr("uid"), _qstr("server", "prod_gf_cn")
    void_front_id = _qstr("void_front_id")
    previous, account_id = _qbool("previous"), _qstr("account_id")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        void_front_battle(
            uid, server, void_front_id=void_front_id or None,
            previous=previous, account_id=account_id or None,
        ),
        "读取临界推演失败",
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    data = await asset_cache.rewrite_assets(data)  # 出战代理人头像改后台本地路由
    brief = data.get("void_front_battle_abstract_info_brief")
    brief = brief if isinstance(brief, dict) else {}
    role = data.get("role_basic_info")
    role = role if isinstance(role, dict) else {}
    boss = data.get("boss_challenge_record")
    boss = boss if isinstance(boss, dict) else {}
    boss_rec = boss.get("main_challenge_record")
    boss_rec = boss_rec if isinstance(boss_rec, dict) else {}
    boss_info = boss.get("boss_info")
    boss_info = boss_info if isinstance(boss_info, dict) else {}

    def _void_time(t):
        if not isinstance(t, dict) or not t.get("year"):
            return ""
        p = lambda n: str(int(t.get(n) or 0)).zfill(2)
        return f"{t['year']}.{p('month')}.{p('day')} {p('hour')}:{p('minute')}:{p('second')}"

    def _row(rec):
        rec = rec if isinstance(rec, dict) else {}
        avs = rec.get("avatar_list") or []
        avatars = [
            {
                "icon": a.get("role_square_url") or a.get("icon") or "",
                "rarity": str(a.get("rarity") or "S").upper(),
                "rank": int(a.get("rank") or 0),
                "level": int(a.get("level") or 0),
            }
            for a in avs if isinstance(a, dict)
        ]
        buddy = rec.get("buddy")
        buddy = buddy if isinstance(buddy, dict) else {}
        return {
            "name": str(rec.get("name") or ""),
            "score": int(rec.get("score") or 0),
            "max_score": int(rec.get("max_score") or 0),
            "star": str(rec.get("star") or ""),
            "ratio": str(rec.get("score_ratio") or ""),
            "time": _void_time(rec.get("challenge_time")),
            "avatars": avatars,
            "buddy": str(buddy.get("bangboo_rectangle_url") or buddy.get("icon") or ""),
            "buddy_rarity": str(buddy.get("rarity") or "S").upper(),
        }

    items = [_row(r) for r in (data.get("main_challenge_record_list") or []) if isinstance(r, dict)]
    end_ts = _num(brief.get("end_ts"))
    left_ts = _num(brief.get("left_ts"))
    if not left_ts and end_ts and not brief.get("end_ts_over_42_days"):
        # 新摘要不一定给 left_ts（只给 end_ts），自己算一次剩余秒数。
        # `end_ts_over_42_days` 为真 = 超长/常驻赛期，这种不给倒计时（免得显示「还剩 90 天」）。
        left_ts = max(0, end_ts - int(time.time()))
    view = {
        "nick": str(role.get("nickname") or ""),
        "total_score": int(brief.get("total_score") or 0),
        "max_score": int(brief.get("max_score") or 0),
        "rank_percent": int(brief.get("rank_percent") or 0),
        "end_ts": end_ts,
        "left_ts": left_ts,
        "boss": {
            "name": str(boss_info.get("name") or ""),
            "score": int(boss_rec.get("score") or 0),
            "star": str(boss_rec.get("star") or ""),
        },
        "items": items,
    }
    # 本期没成绩（没参与）时给前端一个空 view，让它走「没有临界推演数据」分支，
    # 而不是显示一排「0 分」。
    if not (items or view["total_score"] or view["boss"]["score"]):
        view = None
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


# ---------------- 危局 / 防卫战图片资源 + 后台生成图片 ----------------


async def api_zzz_asset(name: str):
    """读取后台缓存的米游社图片（name 是 URL 哈希，天然防目录穿越）。

    前端 <img> 一般不走这里（走 Page 静态目录 ./assets/zzz/<name>），
    这个接口留给 bridge.download 之类需要带身份拿原始文件的场景。
    """
    path = asset_cache.asset_path(name)
    if not path.exists():
        return fail("图片不存在", 404)
    return file_response(path, filename=name)


async def api_zzz_deadly_image():
    """后台把危局结果渲染成 PNG（不经前台；纯 Pillow 绘制，立绘走本地缓存，无跨域问题）。"""
    uid, server = _qstr("uid"), _qstr("server", "prod_gf_cn")
    previous, account_id, show_time = _qbool("previous"), _qstr("account_id"), _qbool("show_time")
    if not uid:
        return fail("缺少角色 uid（可先在「角色」里选择）")
    err, data = await call(
        deadly_assault(uid, server, previous=previous, account_id=account_id or None),
        "读取危局强袭战失败",
    )
    if err:
        return err
    data = data if isinstance(data, dict) else {}
    data = await asset_cache.rewrite_assets(data)
    out = record_image.output_path(uid, bool(previous))
    try:
        await record_image.render_deadly_png(data, str(out), show_time=show_time)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"miyoho 危局出图失败：{exc}")
        return fail(f"生成图片失败：{exc}")
    return file_response(out, filename=out.name, content_type="image/png")


# (suffix, method, handler, desc) —— main.py 统一注册
ROUTES = [
    ("zzz/shiyu", "GET", api_zzz_shiyu, "式舆防卫战查询"),
    ("zzz/deadly", "GET", api_zzz_deadly, "危局强袭战查询"),
    ("zzz/records", "GET", api_zzz_records, "本地存档赛期列表"),
    ("zzz/records/item", "GET", api_zzz_records_item, "某一期完整存档"),
    ("zzz/records/drop", "POST", api_zzz_records_drop, "删除某一期存档"),
    ("zzz/abyss", "GET", api_zzz_abyss, "零号空洞摘要"),
    ("zzz/zenkov", "GET", api_zzz_zenkov, "迷宫诡域摘要"),
    ("zzz/note", "GET", api_zzz_note, "实时便笺"),
    ("zzz/void", "GET", api_zzz_void, "临界推演"),
    ("zzz/asset/<name>", "GET", api_zzz_asset, "后台缓存的米游社图片"),
    ("zzz/deadly/image", "GET", api_zzz_deadly_image, "危局结果渲染成 PNG"),
]
