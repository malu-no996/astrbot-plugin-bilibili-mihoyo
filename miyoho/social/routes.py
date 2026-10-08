"""社交命令 · 管理页路由（原 src/social/routes.py 的 AstrBot 移植）。

  GET  social/config   一次拿全：命令表 + 可用接口清单 + 平台实例 + 各种开关
  POST social/preview  模板预览：按当前弹窗里的设置真跑一次接口
  POST social/config   整份保存（命令表 + 总开关 + 逐命令开关），保存即刻生效
"""

from __future__ import annotations

from loguru import logger

from ..core.web import json_response

from ..core import bind
from ..core.web import body, fail
from .. import send as send_shim
from .cfg import _clean_options, _clean_tpl, load_cfg, save_cfg
from .core import Ctx, INTERFACES, apply_tpl, run_interface


async def _list_instances() -> list[dict]:
    """当前配置的平台实例（含适配器类型）。AstrBot：从 platform_manager 取。"""
    out: list[dict] = []
    seen: set[str] = set()
    for inst in send_shim._insts():
        pid, ptype = send_shim._inst_meta(inst)
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(
            {
                "id": pid,
                "name": pid,
                "protocol": "qq_official" if ptype.lower().startswith("qq_official") else "onebot",
            }
        )
    return out


async def api_social_config():
    """一次拿全：命令表 + 可用接口清单 + 平台实例 + 各种开关。"""
    cfg = load_cfg()
    return json_response(
        {
            "ok": True,
            "commands": cfg.get("commands") or [],
            "bots": cfg.get("bots") or {},
            "bot_cmds": cfg.get("bot_cmds") or {},
            "interfaces": [
                {
                    "key": k, "label": v["label"], "desc": v["desc"],
                    # 「详细设置」弹窗靠这三份渲染：表单 schema / 变量说明 / 示例模板
                    "options": v.get("options") or [],
                    "tpl_vars": v.get("tpl_vars") or [],
                    "sample": v.get("sample") or "",
                }
                for k, v in INTERFACES.items()
            ],
            "instances": await _list_instances(),
        }
    )


async def api_social_preview():
    """模板预览：按当前弹窗里的设置真跑一次接口，把模板渲染结果返回给页面看。

    为什么真跑一次而不是造假数据：模板变量是接口自己填的，造假数据就得在两处维护
    同一套变量清单，早晚对不上。图鉴不需要登录，跑一次很便宜。
    （真拿不到数据时 —— 比如这个身份根本没绑定米游社账号 —— 接口会返回「请先扫码绑定」
    这类提示文本，vars 是空的，页面提示「没取到数据」并原样显示那句提示。）
    """
    payload = await body()
    api = str(payload.get("api") or "")
    if api not in INTERFACES:
        return fail(f"接口不存在：{api}")
    # 走一遍和保存时同样的清洗：脏值在这里就被纠正，预览看到的 = 真正发出去的
    cmd = {
        "cmd": str(payload.get("cmd") or "图鉴"),
        "api": api,
        "options": _clean_options(api, payload.get("options")),
        "tpl": _clean_tpl(payload.get("tpl")),
    }
    # 预览身份：管理页自己**没有**「QQ 用户」这个概念，可危局 / 防卫战 / 抽卡 / 签到 /
    # 角色这些接口全靠 ctx.user_id 去查「这个人绑的米游社账号」—— 不给身份，
    # 预览永远只能看到一句「未绑定米游社账号」，模板没法调。
    # 所以：页面可以填一个 QQ 号 / openid（看别人的结果），留空就自动取第一个已绑定用户。
    user_id = str(payload.get("user") or "").strip()
    if not user_id:
        ids = bind.user_ids()
        user_id = ids[0] if ids else ""
    ctx = Ctx(cmd=cmd, arg=str(payload.get("arg") or ""), user_id=user_id)
    text = await run_interface(api, ctx)
    out = {}
    for proto in ("onebot", "qq"):
        rendered = apply_tpl(cmd, proto, ctx.vars, text)
        out[proto] = rendered
    return json_response(
        {
            "ok": True,
            "default": text,                 # 不配模板时的默认文本
            "preview": out,                  # 两套模板各自的渲染结果
            "vars": ctx.vars,                # 顺带给页面看一眼真实变量（排查模板很有用）
            "data": ctx.data,                # 接口返回的原始数据（页面展示成 JSON）
            "user": ctx.user_id,             # 这次实际用的「预览身份」（页面显示出来）
            "empty": not ctx.vars,           # 没取到数据（多半是没绑定账号 / 没登录）
        }
    )


async def api_social_save():
    """整份保存（命令表 + 总开关 + 逐命令开关）。保存即刻生效，不用重启。"""
    payload = await body()
    try:
        cfg = save_cfg(payload.get("commands"), payload.get("bots"), payload.get("bot_cmds"))
    except OSError as exc:
        return fail(f"保存失败：{exc}", 500)
    logger.info(
        f"miyoho 社交命令配置已更新：{len(cfg['commands'])} 条命令 · "
        f"{sum(1 for v in cfg['bots'].values() if v)} 个机器人已开启"
    )
    return json_response(
        {
            "ok": True,
            "commands": cfg["commands"],
            "bots": cfg["bots"],
            "bot_cmds": cfg["bot_cmds"],
            "instances": await _list_instances(),
        }
    )


ROUTES = [
    ("social/config", "GET", api_social_config, "社交命令配置读取"),
    ("social/config", "POST", api_social_save, "社交命令配置保存"),
    ("social/preview", "POST", api_social_preview, "回复模板预览"),
]
