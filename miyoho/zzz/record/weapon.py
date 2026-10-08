"""音擎（W-Engine）图标：给危局 / 防卫战的「全面」版战报用。

战绩存档 `avatar_list` 里**没有**音擎字段，音擎图标只活在「角色详情」接口
（`zzz/avatar/api.py::avatar_info`）—— 也就是详情面板取音擎的同一套。

⚠️ 2026-10-05 实测：这个接口对**本设备的请求一次只能查 1 个角色** —— `id_list[]`
传 2 个及以上会返回 `retcode=-400005`（"参数错误"），业务数据为空。**别再想批量**，
下面 `weapon_map` 是一个 id 一次地查的（和详情面板点一张卡片拉一次完全一致）。

这里把它抽成公共模块，供两条独立入口共用：

  · QQ 命令层 `social/record.py`（危局 / 防卫战「全面」版出图）
  · 网页接口层 `zzz/record/routes.py`（网页勾选「全面」查询）

放这一层是为了**依赖方向**：social → zzz 是单向的，zzz 层不能反过来 import social
（会成环）。所以「取音擎」这种游戏相关的公共逻辑下沉到这里，两边都从这 import。

⚠️ `avatar_info` 属「角色详情」设备指纹风控：设备没登记到账号 → 10041。
所以对外统一先 `device_bound(aid)` 判断，没绑设备就别去查、由调用方提醒用户
（`FULL_NO_DEVICE`）。战绩接口本身不受这套风控，普通查询用不到本模块。
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from ...core import asset_cache, device
# ⚠️ 音擎来自「角色详情」接口 avatar_info，它在 **avatar 包**里（不是 record/api.py）。
# 2026-10-05 踩坑：这里原来写 `from . import api as record_api` 再调
# `record_api.avatar_info` → AttributeError，被宽泛 except 吞掉（只 debug 日志），
# 表现为「网页/QQ 勾了全面也永远没有音擎图标、缓存文件从不生成」。
from ..avatar import api as avatar_api

# 音擎图标缓存：{角色 UID: {角色id: 音擎图标本地路由}}。
# 按「阵容拥有者的角色 UID」存，同一玩家多队共享、跨查询复用 —— 避免每次都打
# avatar_info（同一套角色详情风控，未开放 / 设备不受信 → 10041，还有官方限频）。
_WEAPON_CACHE = Path(__file__).resolve().parents[3] / "data" / "zzz" / "weapon_icons.json"

# 「全面」版但账号没绑定设备配置时的提醒（QQ 命令 / 网页共用同一条文案）。
# 音擎图标要实时打「角色详情」接口（avatar/info），那套有设备指纹风控：设备没登记
# → 10041。所以没绑设备就直接跳过音擎、并明确告诉用户去哪里绑。
# 普通（非全面）战报走的是战绩接口，不受这套风控影响，照常直接查。
FULL_NO_DEVICE = (
    "⚠️ 你请求了「全面」版，但该账号还没绑定米游社设备配置 —— "
    "音擎图标要实时查「角色详情」接口（有设备指纹风控，设备没登记会被拦），"
    "所以本次战报不显示音擎。请到「米游社 → 设备配置」绑定设备后再试；"
    "不勾「全面」的普通查询不受影响、可直接查。"
)

# 「全面」版、设备配了但仍被风控拦（avatar/info 返回 10041）时的提醒。
# 10041 = 「角色详情」风控：米游社没开角色详情公开，或设备没登记到该账号。
FULL_RISK_CTRL = (
    "⚠️ 「全面」版需要实时查「角色详情」接口取音擎，但本次被风控拦住了（retcode=10041）。"
    "请检查：① 在米游社「我的-设置-隐私设置」里把**角色详情**设为公开；"
    "② 到「米游社 → 设备配置」里确认设备已绑定并「保存到账号」（只配不发会算陌生设备）。"
    "本页其它内容不受影响；不勾「全面」的普通查询也照常。"
)

# 「全面」版、设备配置已绑但取音擎这一步整体失败（非 10041 的其它 retcode）时的提醒。
# 之前这类失败既不写 weapons 也不写 warn，页面上「什么都没发生」——正是它把问题藏住了，
# 所以这里兜一条，任何取音擎失败都要让用户看得见（{code} 会填上实际 retcode）。
FULL_FAILED = (
    "⚠️ 「全面」版查询音擎失败（retcode={code}），本次不显示音擎图标。"
    "可稍后重试，或到「米游社 → 设备配置」确认设备已绑定并「保存到账号」。"
    "不勾「全面」的普通查询不受影响。"
)


def _load_cache() -> dict:
    try:
        return json.loads(_WEAPON_CACHE.read_text(encoding="utf-8")) if _WEAPON_CACHE.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(cache: dict) -> None:
    try:
        _WEAPON_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _WEAPON_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"miyoushe 音擎缓存写入失败（忽略）{exc}")


def collect_char_ids(view_or_data: dict, measure: str) -> list:
    """从战报数据里收齐所有出战代理人的角色 id（含队友），供一次性批量查音擎。

    deadly 收 `list` + `hard_list` 每项的 avatar_list（那里是**原始**结构，id 是数字）；
    shiyu 收 view 里 fifth/fourth 各队的 avatars（那里是 `base._avatars` 归一过的，id 是字符串）。
    两种都行 —— weapon_map 内部统一转字符串做 key。
    """
    ids: list = []
    if measure == "deadly":
        for c in (view_or_data.get("list") or []) + (view_or_data.get("hard_list") or []):
            if isinstance(c, dict):
                ids += [a.get("id") for a in c.get("avatar_list") or [] if isinstance(a, dict)]
    elif measure == "shiyu":
        # 视图形态（social 链路）：fifth / fourth 各队的 avatars（base._avatars 归一过）
        if "fifth" in view_or_data or "fourth" in view_or_data:
            for layer in ("fifth", "fourth"):
                for t in (view_or_data.get(layer, {}) or {}).get("teams") or []:
                    if isinstance(t, dict):
                        ids += [a.get("id") for a in t.get("avatars") or [] if isinstance(a, dict)]
        else:
            # 原始形态（网页路由 /zzz/shiyu）：hadal_info_v2 两层的 layer_challenge_info_list
            info = view_or_data.get("hadal_info_v2") or {}
            for key in ("fitfh_layer_detail", "fourth_layer_detail"):
                node = info.get(key)
                for n in (node.get("layer_challenge_info_list") if isinstance(node, dict) else None) or []:
                    if isinstance(n, dict):
                        ids += [a.get("id") for a in n.get("avatar_list") or [] if isinstance(a, dict)]
    return ids


def device_bound(aid: str) -> bool:
    """该账号是否已绑定**可用**的米游社设备配置（device_fp）。

    「全面」版要实时打「角色详情」（avatar/info）拿音擎，而那套接口有设备指纹风控：
    设备没登记到账号 → 10041。所以「全面」查询前先看这个账号有没有一份启用的设备配置
    （判据与请求时一致：`device.for_account(aid)` 能取到带 fp 且未停用的那份）。
    没绑 → 跳过音擎并提醒用户（见 FULL_NO_DEVICE）；普通查询根本不用管它。
    """
    try:
        dev = device.for_account(aid)
    except Exception:  # noqa: BLE001 —— 设备配置读取异常一律当「没绑」，降级不报错
        return False
    return bool(dev and str(dev.get("fp") or "").strip())


async def weapon_map(uid: str, server: str, aid: str, char_ids: list) -> tuple[dict, int]:
    """阵容角色 id → 音擎图标本地路由；返回 (映射, 最后一次 avatar/info 的 retcode)。

    ⚠️ **逐个查**：`avatar_info` 对本设备一次只能查 1 个角色，`id_list[]` 传 ≥2 个会
    返回 `retcode=-400005`（2026-10-05 实测），所以这里一个 id 一次地调（缺哪个查哪个，
    和你点角色卡片看详情是同一套调用）。命中的图标经 `rewrite_assets` 落本地后，
    weapon.icon 变成后台路由，渲染器/前端直接引用。
    风控 / 限频 / 角色没音擎都不阻断战报 —— 只是那个角色不显示音擎标。
    retcode 交回调用方（resolve 据此决定要不要提示）；全缓存命中时给 0。
    """
    ids = sorted({int(c) for c in (char_ids or []) if str(c).strip()})
    if not ids:
        return {}, 0
    cache = _load_cache()
    owned = cache.get(str(uid)) or {}
    owned = owned if isinstance(owned, dict) else {}
    need = [i for i in ids if str(i) not in owned]
    code = 0
    if need:
        for i in need:
            try:
                cur, details = await avatar_api.avatar_info(uid, server, [i], aid)
            except Exception as exc:  # noqa: BLE001 —— 音擎标是「顺手」加的，绝不能拖累战报
                logger.warning(f"miyoushe 取音擎异常（uid={uid} id={i}）：{exc}")
                code = code or -1
                continue
            if cur:
                # 10041 = 角色详情风控；其它 = 参数 / 网络。用 warning 才看得见
                # （以前用 debug，出问题时日志里一片安静，白白排查了很久）。
                logger.warning(f"miyoushe 取音擎失败 retcode={cur}（uid={uid} aid={aid} id={i}）")
                code = code or cur      # 记住首个错误码，别被后面的成功覆盖
                continue
            # 单查成功：取这条的音擎图标（没有音擎就记空串，下次不再重复查它）
            row = next(
                (d for d in (details or [])
                 if isinstance(d, dict) and str(d.get("id") or "") == str(i)),
                None,
            )
            if row is None:
                continue
            try:
                wrapped = {"avatar_list": [row]}
                await asset_cache.rewrite_assets(wrapped)   # CDN 图标 → 后台本地路由
                row = wrapped["avatar_list"][0]
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"miyoushe 音擎图标转本地失败（uid={uid} id={i}）：{exc}")
            w = row.get("weapon")
            owned[str(i)] = str((w.get("icon") if isinstance(w, dict) else "") or "")
        cache[str(uid)] = owned
        _save_cache(cache)
    return {str(i): owned[str(i)] for i in ids if owned.get(str(i))}, code


async def resolve(data: dict, measure: str, uid: str, server: str, aid: str) -> tuple[dict, str]:
    """给一份「全面」战报数据补 `weapons` / `weapon_warn` 两个字段，返回 (weapons, warn)。

    - 账号已绑设备：实时（走缓存）取音擎，weapons = {角色id: 图标}，warn = ""。
    - 账号没绑设备：weapons = {}，warn = FULL_NO_DEVICE。
    - 绑了设备但被风控拦（10041）且一个都没取到：warn = FULL_RISK_CTRL。
    - 绑了设备但取音擎整体失败（其它 retcode）：warn = FULL_FAILED（不再静默）。
    调用方拿到后自行决定怎么呈现（QQ 出图 / 网页横幅）。
    """
    if not device_bound(aid):
        return {}, FULL_NO_DEVICE
    weapons, code = await weapon_map(uid, server, aid, collect_char_ids(data, measure))
    if weapons:
        return weapons, ""
    if code == 10041:
        return {}, FULL_RISK_CTRL
    if code:
        # 非 0 就说明取音擎这一步整体没过（如 -400005）：必须让用户看见，别又「什么都没发生」。
        return {}, FULL_FAILED.format(code=code)
    return {}, ""
