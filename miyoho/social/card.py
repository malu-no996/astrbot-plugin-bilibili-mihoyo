"""社交命令 · 绝区零「代理人面板」（出图 / 文字两用）。

数据链路和网页「角色查询 → 点卡片 → 代理人详细」浮层**完全一致**：
`avatar/info` 官方详情 → `zzz/avatar/detail.py::from_official` 归一化 →
`zzz/avatar/panel.py::render_agent_detail` 用 Pillow 画成面板图。
（以前的「角色面板」是从 ZZZeroUID 搬来的另一套版面，已按要求去掉。）

命令词 2026-10-05 由「角色面板」改为**代理人面板**（菜单里也是这个名字），
「角色面板 / 代理人详细 / 面板」都留作别名。

怎么指定角色
------------
命令后面跟**角色名**（「代理人面板 雅」「面板 浅羽」），支持：
  · 全名 / 简称（「星见雅」/「雅」—— 简称是包含匹配，多个命中时取第一个）；
  · 直接写角色 id（四位数字，如 1211）。
不写参数时给一句提示。查的是**发命令的人自己绑定的米游社账号**下、那个默认角色
（「切换角色」设的）的 uid，和网页侧边栏选中的角色无关。

⚠️ 两个坑（都踩过，别改回去）：
  1. 命令参数是**角色名**，不是 uid。以前直接把它当 uid 塞给官方接口 →
     官方回 retcode=-502「传递的参数错误」，表现是「网页正常、QQ 必挂」。
     所以这里**不用** `base._zzz_target`（它按 `ctx.arg` 取 uid），自己解析目标 uid。
  2. 「角色详情」带风控（retcode=10041）：米游社里没开「角色详情公开」或设备不受信
     就查不到 —— 提示里直接给出路（去开公开 / 去设备配置登记）。
"""

from __future__ import annotations

from ..core import bind
from ..core import mys as client
from ..zzz.avatar import api as avatar_api, char_map
from ..zzz.avatar import detail as avatar_detail, panel as avatar_panel
from .base import OUTPUT_OPTIONS, _image_reply, _pick_role
from .core import Ctx, interface

_ID_BY_NAME: dict[str, str] | None = None


def _name_index() -> dict[str, str]:
    """角色名 / 全名 → id 的反查表（本地静态表，不受风控影响）。

    懒加载一次就够（CHARS 是模块级常量，运行期不会变）。
    """
    global _ID_BY_NAME
    if _ID_BY_NAME is None:
        idx: dict[str, str] = {}
        for cid, info in char_map.CHARS.items():
            for name in (info[0] if len(info) > 0 else "", info[1] if len(info) > 1 else ""):
                name = str(name or "").strip()
                if name:
                    idx.setdefault(name, str(cid))
        _ID_BY_NAME = idx
    return _ID_BY_NAME


def _find_char(arg: str) -> tuple[str, str]:
    """按名字 / id 找角色，返回 (id, 显示名)；找不到返回 ("", "")。"""
    key = str(arg or "").strip()
    if not key:
        return "", ""
    idx = _name_index()
    if key.isdigit():                                   # 直接给 id
        info = char_map.lookup(key)
        return key, (info[0] if info else key)
    if key in idx:                                      # 精确：全名或简称
        cid = idx[key]
        info = char_map.lookup(cid)
        return cid, (info[0] if info else key)
    for name, cid in idx.items():                       # 包含匹配（「雅」→「星见雅」）
        if key in name:
            info = char_map.lookup(cid)
            return cid, (info[0] if info else name)
    return "", ""


async def _zzz_target(ctx: Ctx) -> tuple[str | None, str, str, str, str | None]:
    """解析要查询的 (account_id, uid, server, 玩家昵称, err)。

    ⚠️ **不能复用 `base._zzz_target`**：那个把 `ctx.arg` 当 uid 用，而本命令的 arg 是
    角色名 —— 之前就是这么把「叶瞬光」当 uid 发给官方的（官方回 -502 参数错误）。
    这里固定取「发命令的人绑定的账号 → 默认角色」。

    账号来源：**谁发命令就查谁绑定的**（bind.default_account(ctx.user_id)）。
    没绑定的用户查不到任何东西（只提示去绑定），不回落到管理页选中的那个账号。
    """
    aid = bind.default_account(ctx.user_id)
    if not aid:
        return None, "", "", "", "未绑定米游社账号：请先发送「米游社登录」扫码绑定你的米游社账号"
    try:
        roles = await client.bind_roles(aid)
    except Exception as exc:  # noqa: BLE001
        return None, "", "", "", f"读取绝区零角色失败：{exc}"
    role = _pick_role(roles, bind.role_default(ctx.user_id, aid))
    if not role:
        return None, "", "", "", "当前账号没有绑定绝区零角色"
    return (aid, str(role.get("game_uid") or ""), role.get("region") or "prod_gf_cn",
            str(role.get("nickname") or ""), None)


def _text_summary(d: dict) -> list[str]:
    """不出图时的文字版：把面板里最值钱的几项挑出来。"""
    lines = [
        f"代理人详细 · {d.get('name') or ''} Lv.{int(d.get('level') or 0)}"
        f"（{int(d.get('rank') or 0)} 影画）",
    ]
    w = d.get("weapon") or {}
    if w:
        lines.append(f"音擎：{w.get('name') or ''} Lv.{int(w.get('level') or 0)} "
                     f"{int(w.get('star') or 0)}★")
    bits = []
    rating = str(d.get("rating") or "").upper()   # 已是 S/A/B…，DEFAULT 占位在 detail 里就清空了
    if rating:
        bits.append(f"驱动盘评级 {rating}")
    if int(d.get("valid_cnt") or 0):
        bits.append(f"有效词条 {int(d['valid_cnt'])} 次")
    if bits:
        lines.append(" · ".join(bits))
    props = [p for p in (d.get("props") or []) if isinstance(p, dict)]
    if props:
        lines.append("面板：" + " · ".join(
            f"{p.get('name')} {p.get('final') or p.get('base')}" for p in props[:6]
        ))
    if d.get("note"):
        lines.append(str(d["note"]))
    return lines


@interface(
    "zzz_card", "绝区零 · 代理人面板",
    "把某个角色的「代理人详细」面板画成一张图（方头像 / 面板属性 / 技能 / 音擎 / "
    "驱动盘 / 评分）；命令后加角色名，如「代理人面板 雅」。不出图时给文字摘要",
    options=[
        {"key": "output", "label": "输出方式", "type": "select",
         "options": OUTPUT_OPTIONS, "default": "image",
         "hint": "选「图片消息」就发那张面板图（纯 Pillow 绘制，版面同网页版浮层）；"
                 "发送失败会退回文字并附上原因。"},
    ],
    tpl_vars=[
        {"name": "uid", "desc": "绝区零角色 UID"},
        {"name": "region_name", "desc": "服务器中文名（如 国服）"},
        {"name": "name", "desc": "角色名"},
        {"name": "id", "desc": "角色 id"},
        {"name": "level", "desc": "角色等级"},
        {"name": "rank", "desc": "影画数"},
        {"name": "weapon", "desc": "音擎名"},
        {"name": "weapon_level / weapon_star", "desc": "音擎等级 / 进阶星数"},
        {"name": "equip_rating", "desc": "驱动盘总分评级（S / SS / SSS 可带 +，或 A / B / C）；"
                                        "评级不到 A（官方 DEFAULT 占位）时为空串"},
        {"name": "valid_hits", "desc": "有效词条命中次数"},
        {"name": "props", "desc": "面板属性列表，每条含 name / value"},
    ],
    sample=(
        "{name} Lv.{level}（{rank} 影画）\n"
        "音擎 {weapon} Lv.{weapon_level} {weapon_star}★\n"
        "有效词条 {valid_hits} 次"
    ),
)
async def _api_zzz_card(ctx: Ctx) -> dict:
    """代理人面板：图片 / 文字两用。"""
    cmd = str(((ctx.cmd or {}).get("cmd")) or "代理人面板")
    aid, uid, server, nick, err = await _zzz_target(ctx)
    if err:
        return {"text": err}

    cid, name = _find_char(ctx.arg)
    if not cid:
        return {"text": f"没找到角色「{(ctx.arg or '').strip() or '（空）'}」，"
                        f"在命令后面加上角色名，例如：{cmd} 雅"}

    try:
        code, rows = await avatar_api.avatar_info(uid, server, [cid], account_id=aid)
    except Exception as exc:  # noqa: BLE001
        return {"text": f"读取角色详情失败：{exc}"}
    if code == 10041:
        return {"text": "读取角色详情失败：角色详情未公开 / 设备不受信（retcode=10041）。"
                        "请在米游社「我的角色」里开启角色详情，或在管理页「米游社 → 设备配置」"
                        "里登记设备后重试"}
    if code != 0:
        return {"text": f"读取角色详情失败（retcode={code}）"}
    row = next((r for r in rows if isinstance(r, dict)), None)
    if not row:
        return {"text": f"没有这个角色的详情（{name}）—— 该账号可能未拥有该角色"}

    # 归一化用**网页版同一套**（detail.from_official），两边字段口径一致
    detail = avatar_detail.from_official(row)
    lines = _text_summary(detail)
    w = detail.get("weapon") or {}
    props = [{"name": str(p.get("name") or ""),
              "value": str(p.get("final") or p.get("base") or "")}
             for p in (detail.get("props") or []) if isinstance(p, dict)]
    vars_ = {
        "uid": uid,
        "region": server,
        "region_name": client.region_name(server),
        "name": str(detail.get("name") or name),
        "id": str(detail.get("id") or cid),
        "level": int(detail.get("level") or 0),
        "rank": int(detail.get("rank") or 0),
        "weapon": str(w.get("name") or ""),
        "weapon_level": int(w.get("level") or 0),
        "weapon_star": int(w.get("star") or 0),
        "equip_rating": str(detail.get("rating") or ""),
        "valid_hits": int(detail.get("valid_cnt") or 0),
        "props": props,
    }

    if str(ctx.opt("output", "image")) == "image":
        return await _image_reply(
            ctx,
            lambda: avatar_panel.render_agent_detail(detail, nick=nick, uid=uid),
            lines, vars_, detail,
        )
    return {"text": "\n".join(lines), "vars": vars_, "data": detail}
