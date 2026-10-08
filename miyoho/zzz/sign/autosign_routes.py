"""绝区零 · 自动签到 的管理页路由（原 src/zzz/sign/autosign_routes.py 的 AstrBot 移植）。

  GET  zzz/autosign           读配置 + 运行状态（不发网络请求，秒回）
  GET  zzz/autosign/targets   列出「账号 × 角色」供弹窗勾选（**每个账号一个网络请求**）
  GET  zzz/autosign/instances 列出在线的 OneBot 平台实例（「签到通知」选机器人用）
  POST zzz/autosign           保存配置（改完即时生效，定时时刻会重排）
  POST zzz/autosign/run       立刻签一轮（后台跑，页面轮询 zzz/autosign 看进度）
"""
from __future__ import annotations

from loguru import logger

from astrbot.api.web import json_response

from ...core.web import body, fail
from ... import send as send_shim
from . import autosign


async def api_zzz_autosign_get():
    """当前配置 + 是否正在签到 + 下次/上次时刻（页面每次进来、轮询进度都调它）。"""
    return json_response({"ok": True, **autosign.status()})


async def api_zzz_autosign_targets():
    """可勾选的「账号 × 绝区零角色」清单（弹窗打开时才调，逐账号拉角色列表）。"""
    rows = await autosign.list_targets()
    return json_response({"ok": True, "accounts": rows})


async def api_zzz_autosign_instances():
    """在线的 OneBot 平台实例（「签到通知」里选哪个机器人发私聊）。

    只列 OneBot：QQ 官方机器人没法按 QQ 号主动私聊，列出来只会让人白选。
    """
    return json_response({"ok": True, "instances": await send_shim.list_onebot_instances()})


async def api_zzz_autosign_save():
    """保存配置。只覆盖传进来的字段（enabled / mode / time / jitter / gap / gap_jitter / targets）。"""
    payload = await body()
    try:
        cfg = autosign.save(payload)
    except OSError as exc:
        return fail(f"保存失败：{exc}", 500)
    logger.info(
        f"miyoho 自动签到配置已更新：{'启用' if cfg['enabled'] else '关闭'} · "
        f"{'每天 ' + cfg['time'] + ' 前后（±' + str(cfg['jitter']) + ' 分钟）' if cfg['mode'] == 'auto' else '仅手动'} · "
        f"{len(cfg['targets'])} 个角色 · 间隔 {cfg['gap']:g}s ± {cfg['gap_jitter']:g}s"
    )
    return json_response({"ok": True, "message": "已保存", **autosign.status()})


async def api_zzz_autosign_run():
    """立刻签一轮（后台跑）。

    不在这里 await：目标一多就是好几分钟（每个请求至少隔 3 秒），HTTP 一直挂着
    既难看也容易超时。页面传完就拿到响应，然后轮询 zzz/autosign 看 `running`。
    """
    if not autosign.spawn("web"):
        return json_response({"ok": False, "message": "已经有签到任务在进行中", **autosign.status()})
    return json_response({"ok": True, "message": "已开始一键签到", **autosign.status()})


ROUTES = [
    ("zzz/autosign", "GET", api_zzz_autosign_get, "自动签到配置与状态"),
    ("zzz/autosign/targets", "GET", api_zzz_autosign_targets, "可勾选的账号×角色"),
    ("zzz/autosign/instances", "GET", api_zzz_autosign_instances, "在线 OneBot 实例"),
    ("zzz/autosign", "POST", api_zzz_autosign_save, "保存自动签到配置"),
    ("zzz/autosign/run", "POST", api_zzz_autosign_run, "立刻签一轮"),
]
