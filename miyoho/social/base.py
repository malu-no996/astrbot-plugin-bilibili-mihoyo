"""社交命令 · 公共工具层：输出方式选项 / 战绩存档 / 查询目标解析 / 发消息与发图。

2026-10-04 从原 `social_apis.py` 拆出来的第一段（不含任何 @interface）：
账号类接口在 `account.py`、绝区零战绩类接口在 `record.py`，两边共用这里的工具。

「文字 / 图片」三件套
--------------------
危局 / 防卫战 / 抽卡这三个接口都有「输出方式」选项（text / image）：

  · text  → 走默认文本或用户配的回复模板；
  · image → 用各自的渲染器画成 PNG 发出去（危局 image_gen.render_deadly、
            防卫战 shiyu_image.render_shiyu、抽卡 gacha_image.render_summary），
            发成功后返回 `{"text": "", "silent": True}` —— 发送端据此**不再跟发文字**；
  · 发图失败（渲染炸 / 适配器不支持 / 平台拒收）→ 退回文字，并在末尾**附上原因**，
    否则用户只看到文字、完全不知道图为什么没来。

渲染器只吃「已经归一化好的数据」：危局直接吃官方 JSON（它的渲染器一直如此），
防卫战吃 `_shiyu_view()` 转出来的视图 —— 官方那套 `fitfh_layer_detail` 之类的
字段名只在 `_shiyu_view` 里出现一次。
"""

from __future__ import annotations

import asyncio
import inspect
import time
from io import BytesIO
from typing import Any

from loguru import logger

from ..core import bind
from ..core import mys as client
from ..zzz.avatar import char_map
from ..zzz.codex import data as codex_data
from ..zzz.record import store as record_store
from .core import Ctx

# 「输出方式」选项：文字 / 图片。三个带图的接口共用（抽卡、危局、防卫战），
# 所以放在这里而不是各自复制一份。
OUTPUT_OPTIONS = [
    {"value": "text", "label": "文字消息"},
    {"value": "image", "label": "图片消息"},
]

# 「是否显示通关时刻」：网页上危局/防卫战都有一个「通关时间：开/关」的开关，
# 命令这边做成详细设置里的开关项，渲染图时传给渲染器。
SHOW_TIME_OPTION = {
    "key": "show_time", "label": "显示通关时刻", "type": "switch", "default": False,
}

# 「是否显示第四防线」：第四防线在官方数据里**没有得分、没有评级、没有 BOSS 立绘**，
# 只有出战头像，信息量很低，默认**不显示**（用户明确要求）。关掉后文字与图片同时生效：
# 默认文本不再提第四防线，items 只剩第五防线的小队，战报图也不画第四防线卡
# （`show_fourth` 变量仍在，想自己拼模板的话还能用）。
SHOW_FOURTH_OPTION = {
    "key": "show_fourth", "label": "显示第四防线", "type": "switch", "default": False,
    "hint": "第四防线官方只给头像（没有得分 / 评级 / 立绘），默认不显示；"
            "打开后文字与战报图都会带上。",
}

# 带参数切换「账号 / 角色」时要把「名字」回显给用户，而这个参数是**用户随手打的**：
# 可能是乱码、超长串、带换行/制表符的东西。原样回显会把提示顶下去、甚至刷屏，
# 所以统一过一遍 —— 去掉换行与不可打印字符、限长 24 字（超出加省略号）、
# 空的显示「（空）」。正常昵称远短于 24 字，不受影响。
# （账号类接口 account.py 与战绩类接口 record.py 都要用，所以放公共层。）
_SWITCH_NAME_MAX = 24


def _clean_name(raw: Any) -> str:
    """把要回显的名字洗干净（见 _SWITCH_NAME_MAX 的说明）。"""
    s = "".join(ch for ch in str(raw or "") if ch.isprintable() and ch not in "\r\n\t").strip()
    if not s:
        return "（空）"
    return s[:_SWITCH_NAME_MAX] + "…" if len(s) > _SWITCH_NAME_MAX else s


def _save_record(kind: str, uid: str, server: str, data: dict, source: str = "cmd") -> dict:
    """按赛期存一份战绩快照（同 __init__ 里那份的薄封装）。

    存档失败**绝不影响**命令本身：宁可这次没存上，也不能让命令报错。
    """
    try:
        return record_store.save(kind, uid, server, data, source=source)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"miyoushe 战绩存档失败（{kind}/{uid}）：{exc}")
        return {}


def _pick_role(roles: list, prefer_uid: str = "") -> dict:
    """从绝区零角色列表里挑「要查的那个」。

    优先用「切换角色」命令设过的默认角色（bind.role_default）；
    没设过、或那个角色已不在列表里，就退回列表第一个（= 米游社返回的顺序）。
    """
    rows = [r for r in roles if isinstance(r, dict) and r.get("game_uid")]
    if prefer_uid:
        for r in rows:
            if str(r.get("game_uid")) == str(prefer_uid):
                return r
    return rows[0] if rows else {}


def _find_role(rows: list, key: str) -> dict:
    """按「UID / 序号 / 角色名」在角色列表里找一个，找不到返回空 dict。

    ⚠️ 角色名的**模糊包含**匹配（最后那圈）对「像 UID 的输入」直接跳过：乱敲一串
    数字不该因为某个角色名里恰好含它就被切过去（口径与 `bind.find` 一致，5 位起算）。
    """
    key = str(key or "").strip()
    if not key:
        return {}
    for r in rows:
        if key == str(r.get("game_uid") or ""):
            return r
    if key.isdigit():
        idx = int(key)
        if 1 <= idx <= len(rows):
            return rows[idx - 1]
    for r in rows:                                       # 先精确匹配角色名
        name = str(r.get("nickname") or "")
        if name and key == name:
            return r
    if key.isdigit() and len(key) >= 5:
        return {}                                        # 像 UID 的乱输入 → 不模糊匹配角色名
    for r in rows:                                       # 再退化为包含匹配
        name = str(r.get("nickname") or "")
        if name and key in name:
            return r
    return {}


# 命令回复的**第一行标题**里显示角色名，不显示 UID
# ------------------------------------------------
# 用户明确要求（2026-10-05）：除了「代理人面板」（那张面板图里要 UID），其它命令的
# 回复内容一律**不显示 UID、改显示角色名**。
#
# 名字的取法（优先级从高到低）：
#   ① 接口响应自带的昵称 —— 危局的 `nick_name`、防卫战 view 的 `nick_name`、
#      月报的 `role_info.nickname`、临界推演的 `role_basic_info.nickname`，
#      这些是最准的，调用方直接传进来；
#   ② 角色列表（`bind_roles`）里的 `nickname` —— 便笺 / 档案 / 零号空洞这几个接口
#      不给昵称，只能这么补；
#   ③ 都取不到才退回 `UID xxxxx`：宁可难看，也不能让标题开天窗。
#
# ⚠️ ②要走网络请求，而 `_zzz_target` 在「命令没带 UID」时**已经**拉过一次角色列表，
# 所以这里按 (账号, UID) 缓存 5 分钟 + 由 `_zzz_target` 顺手把已知昵称塞进缓存：
# 常见路径（不带参数）零额外请求，带参数的也只多一次、且 5 分钟内不再重复。
_NICK_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_NICK_TTL = 300


def _nick_remember(aid: Any, uid: Any, nick: Any) -> None:
    """把已知的角色名塞进缓存（接口自带的 / 角色列表拿到的都走这里）。"""
    key = (str(aid or ""), str(uid or ""))
    name = str(nick or "").strip()
    if all(key) and name:
        _NICK_CACHE[key] = (time.time() + _NICK_TTL, name)


async def _role_nick(aid: Any, uid: Any) -> str:
    """按 UID 查角色名（带 5 分钟缓存）；查不到返回空串。

    缓存**连空结果一起存**：同一跳里 `_zzz_nick` 与 `_zzz_title` 会各问一次，
    不缓存空值的话「查不到名字」这条路径要白打两次接口。
    网络异常**不缓存**（下次还能再试），且绝不让它影响命令本身。
    """
    key = (str(aid or ""), str(uid or ""))
    if not all(key):
        return ""
    hit = _NICK_CACHE.get(key)
    now = time.time()
    if hit and hit[0] > now:
        return hit[1]
    try:
        roles = await client.bind_roles(aid)
    except Exception as exc:  # noqa: BLE001 —— 取名字失败不该让查询失败
        logger.debug(f"miyoushe 取角色名失败（uid={uid}）：{exc}")
        return ""
    nick = ""
    for r in roles or []:
        if isinstance(r, dict) and str(r.get("game_uid") or "") == key[1]:
            nick = str(r.get("nickname") or "").strip()
            break
    _NICK_CACHE[key] = (now + _NICK_TTL, nick)
    return nick


async def _zzz_nick(aid: Any, uid: Any, nick: Any = "") -> str:
    """角色名：调用方给的（接口自带）优先，其次角色列表；都没有返回空串。

    需要在 `vars` 里放 `nick_name` 的命令（便笺 / 档案 / 零号空洞）先调它一次，
    再把同一个值传给 `_zzz_title` —— 第二次是缓存命中，不会再发请求。
    """
    name = str(nick or "").strip()
    if name:
        _nick_remember(aid, uid, name)
        return name
    return await _role_nick(aid, uid)


async def _zzz_title(aid: Any, uid: Any, server: Any, title: str, nick: Any = "") -> str:
    """命令回复的第一行：「标题 · 角色名（国服）」。

    （以前是「标题 · UID xxxxx（国服）」，2026-10-05 按要求改成角色名。）
    """
    name = await _zzz_nick(aid, uid, nick) or f"UID {uid}"
    return f"{title} · {name}（{client.region_name(server)}）"


async def _zzz_target(ctx: Ctx) -> tuple[str | None, str | None, str | None, str | None]:
    """解析要查询的 (account_id, uid, server, err)。

    ⚠️ 账号来源：**谁发命令就查谁绑定的**（bind.default_account(ctx.user_id)），
    不再用 `store.current_id()` —— 那是管理页选中的账号、全机器人共用一份，
    放到 QQ 里就成了「谁发命令查到的都是管理员自己的账号」。
    没绑定的用户查不到任何东西（只提示去绑定），管理页不受影响。

    命令里带了 uid 就查那个 uid，但凭证仍取该用户自己绑定的账号；
    没带就取该账号下的**默认角色**（「切换角色」设的），没设过则取列表第一个。
    """
    aid = bind.default_account(ctx.user_id)
    if not aid:
        return None, None, None, "未绑定米游社账号：请先发送「米游社登录」扫码绑定你的米游社账号"
    uid = (ctx.arg or "").strip()
    server = "prod_gf_cn"
    if not uid:
        try:
            roles = await client.bind_roles(aid)
        except Exception as exc:  # noqa: BLE001
            return None, None, None, f"读取绝区零角色失败：{exc}"
        role = _pick_role(roles, bind.role_default(ctx.user_id, aid))
        if not role:
            return None, None, None, "当前账号没有绑定绝区零角色"
        uid = str(role.get("game_uid") or "")
        server = role.get("region") or server
        # 角色列表刚拉过，昵称顺手存进缓存 —— 标题要用它，别再打一次接口
        _nick_remember(aid, uid, role.get("nickname"))
    return aid, uid, server, None





def _qr_image_segment(bot: Any, png: bytes | None) -> Any:
    """二维码 PNG 原样返回（AstrBot：`_send` 里统一包成 Image.fromBytes 组件）。

    保留「None = 发不了图，调用方退化成发链接」的约定。
    """
    return png if png else None


async def _send(ctx: Ctx, message: Any) -> bool:
    """接口在执行过程中主动发一条消息（文本 str / 图片 bytes）；失败返回 False。"""
    if ctx.event is None:
        return False
    try:
        from astrbot.api.message_components import Image, Plain
        from astrbot.core.message.message_event_result import MessageChain

        from ..send import as_plain

        if isinstance(message, (bytes, bytearray)):
            chain = MessageChain(chain=[Image.fromBytes(bytes(message))])
        else:
            chain = MessageChain(chain=[Plain(str(message))])
        # QQ 官方机器人上强制纯文本（AstrBot 平台默认 use_markdown=True 会包成
        # markdown，逐行清单会被挤成一段；详见 miyoho/send.py 顶部说明）
        await ctx.event.send(as_plain(chain, event=ctx.event))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"miyoho 社交命令主动发消息失败：{type(exc).__name__}: {exc}")
        return False


async def _send_image(ctx: Ctx, make: Any) -> tuple[bool, str]:
    """渲染并发送一张图片。返回 (是否发出, 失败原因)。

    给「输出方式 = 图片」的几个接口共用（抽卡总结 / 危局 / 防卫战）。传的是
    **无参可调用**而不是画好的图片，是为了让「渲染」和「发送」的异常在同一个
    try 里收敛 —— 渲染炸了、适配器不支持、平台拒收，对调用方都只是「一句原因」。

    ⚠️ 管理页「预览」里 ctx.bot 是 None（没有真实机器人）：这时**不算失败**，
    返回 (False, "")，由调用方原样给文字 —— 别把预览报成红的。
    """
    if ctx.event is None:
        return False, ""
    try:
        img = make()
        if inspect.isawaitable(img):         # 渲染器要下载素材时是协程（如代理人详细面板图）
            img = await img
        buf = BytesIO()
        img.save(buf, "PNG")
        seg = _qr_image_segment(ctx.bot, buf.getvalue())
        if seg is None:
            return False, "当前适配器不支持发送图片"
        if await _send(ctx, seg):
            return True, ""
        return False, "发送图片消息失败（细节见机器人控制台日志）"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"miyoushe 发送图片失败：{type(exc).__name__}: {exc}")
        return False, f"{type(exc).__name__}: {exc}"


async def _image_reply(ctx: Ctx, make: Any, lines: list[str],
                       vars_: dict, data: Any, note: str = "") -> dict:
    """「输出方式 = 图片」的统一收口（三个带图接口共用，保证行为一致）：

      · 发图成功       → {"text": "", "silent": True, …}：发送端据此**不再跟发文字**；
      · 发图失败       → 文字兜底，末尾**附上原因**（不然用户不知道图为什么没来）；
      · 没机器人可发   → 管理页「预览」就是这种，原样给文字，不算失败。

    note：图之外**还要再补一句文字**时用（例如「全面」版缺设备配置的提醒）。
    默认空 → 发图成功后返回 silent，行为不变；给了 note → 图照发，再让发送端
    补发这句话（否则图一发就 silent，提醒会被吞掉、用户看不到）。
    """
    sent, reason = await _send_image(ctx, make)
    if sent:
        if note:
            return {"text": str(note), "vars": vars_, "data": data}
        return {"text": "", "silent": True, "vars": vars_, "data": data}
    text = "\n".join(lines)
    if reason:
        text += f"\n（图片消息发送失败，已退回文字：{reason}）"
    if note:
        text = f"{note}\n{text}"
    return {"text": text, "vars": vars_, "data": data}


# ================= 官方字段 → 人话 =================
#
# 战绩类接口（危局 / 防卫战 / 零号空洞 / 临界推演 …）的 JSON 里全是
# `hadal_mem_detail_v2`、`PartialTime`、`rank_percent` 这种东西：时间戳是拆开的
# 六个字段、排名是「百分比 ×100」、出战角色只给 id 不给名字。
# 这里统一做转换，保证**所有战绩接口口径一致**（危局里叫 rank 的，临界推演里也叫 rank）。
# 2026-10-05 从 record.py 下沉到公共层：裂缝（探索类）接口也要用同一套。


def _pt(t: Any) -> str:
    """官方 PartialTime（{year,month,day,hour,minute,second}）→ 'YYYY.MM.DD HH:MM:SS'。

    缺字段 / 不是 dict 时返回空串（模板里 `{start}` 取不到就是空，不会漏出 None）。
    """
    if not isinstance(t, dict) or not t.get("year"):
        return ""
    return "{:04d}.{:02d}.{:02d} {:02d}:{:02d}:{:02d}".format(
        int(t.get("year") or 0), int(t.get("month") or 0), int(t.get("day") or 0),
        int(t.get("hour") or 0), int(t.get("minute") or 0), int(t.get("second") or 0),
    )


def _period(start: Any, end: Any) -> str:
    """赛期起止 → '2026.09.18 - 2026.10.02'（只有一边就只写一边）。"""
    a, b = _pt(start)[:10], _pt(end)[:10]
    return f"{a} - {b}" if (a and b) else (a or b)


def _pct_plus(p: Any) -> str:
    """rank_percent → '52.84%+'。

    ⚠️ 官方口径是**百分比 ×100**（5146 → 51.46%），和网页端 dzPct / syPct 一样的算法。
    """
    if not p:
        return ""
    return f"{float(p) / 100:.2f}%+"


def _pair(d: Any, cur_keys: tuple = ("cur", "current", "num", "value"),
          max_keys: tuple = ("max", "total")) -> tuple[int, int]:
    """从 {cur, max} 这类结构里取 (当前, 上限)；取不到给 (0, 0)。

    官方这类字段键名不统一（cur/max、current/max、num/total 都见过），
    统一在这里兼容，免得每个接口各写一遍。
    """
    d = d if isinstance(d, dict) else {}
    cur = next((d[k] for k in cur_keys if isinstance(d.get(k), (int, float))), 0)
    mx = next((d[k] for k in max_keys if isinstance(d.get(k), (int, float))), 0)
    return int(cur), int(mx)


_BANGBOO_NAMES: dict[str, str] | None = None


def _bangboo_name(bid: Any) -> str:
    """邦布 id → 中文名（查本地邦布图鉴；查不到返回空串）。

    ⚠️ 战绩里的 `buddy` **只有 id / 稀有度 / 图片**，没有名字；名字只能靠图鉴反查。
    图鉴还没抓下来时退化成空名（不影响其它字段）。
    """
    global _BANGBOO_NAMES
    key = str(bid or "")
    if not key:
        return ""
    if _BANGBOO_NAMES is None:
        try:
            _BANGBOO_NAMES = {
                str(it.get("id") or ""): str(it.get("name") or "")
                for it in codex_data.section("bangboo") if isinstance(it, dict)
            }
        except Exception:  # noqa: BLE001
            logger.debug("miyoushe 邦布名表读取失败（忽略，邦布名留空）")
            _BANGBOO_NAMES = {}
    return _BANGBOO_NAMES.get(key, "")


def _avatars(raw: Any) -> tuple[list[dict], str]:
    """出战代理人 → (模板列表, 顿号串)。

    顿号串是给「一行写完」的模板用的（`{avatar_text}`），列表给要逐条渲染的用。
    ⚠️ 战绩里的 `avatar_list` **只给 id**（没有名字），名字 / 属性 / 职业都靠
    `char_map` 那张静态表反查；查不到就留空，不会漏出「角色 1611」这种噪音。
    """
    rows: list[dict] = []
    for a in (raw or []):
        if not isinstance(a, dict):
            continue
        info = char_map.lookup(a.get("id"))
        rows.append({
            "id": str(a.get("id") or ""),
            "name": str(a.get("name") or a.get("role_name")
                        or (info[0] if info else "") or ""),
            "rarity": str(a.get("rarity") or (info[2] if len(info) > 2 else "") or ""),
            "element": char_map.element_name(a.get("element_type")) or (info[3] if len(info) > 3 else ""),
            "profession": char_map.profession_name(a.get("avatar_profession"))
                          or (info[4] if len(info) > 4 else ""),
            "rank": int(a.get("rank") or 0),          # 影画（命座）数，0 = 没点
            # 官方**方头像**（战绩里本来就带 role_square_url；没带就按 id 拼官方地址）
            "icon": char_map.square_avatar(a),
        })
    return rows, "、".join(r["name"] for r in rows if r["name"])


def _buffs(raw: Any) -> str:
    """增益列表 → '霜刃、冰结'（取名字，详情去 data 里看）。"""
    return "、".join(
        str(b.get("name") or "") for b in (raw or [])
        if isinstance(b, dict) and b.get("name")
    )


def _stamp(ts: Any) -> str:
    """秒级时间戳 → '2026.10.06 04:00'（0 / 不是数字 → 空串）。

    官方的刷新时间、周常刷新、赛期结束时刻都是秒级时间戳，直接拼进消息没法看。
    """
    try:
        v = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if v <= 0:
        return ""
    return time.strftime("%Y.%m.%d %H:%M", time.localtime(v))


def _duration(seconds: Any) -> str:
    """秒 → '3小时20分钟'（不足一小时只写分钟；0 / 非数字 → 空串）。

    电量回满时间、剩余时间这类「还有多久」用这个（**不是**时刻，别和 _stamp 混）。
    """
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    h, m = divmod(total // 60, 60)
    if h and m:
        return f"{h}小时{m}分钟"
    return f"{h}小时" if h else f"{m}分钟"


