"""绝区零 · 图鉴 的管理页路由（原 src/zzz/codex/routes.py 的 AstrBot 移植）。

图鉴是**本地静态快照**，不需要登录：

  GET  zzz/codex          代理人 / 音擎 / 驱动盘 / 邦布（+ 语言清单 + 更新流程说明）
  GET  zzz/codex/lang     多语言映射（~1.5MB，只在切到非简中时拉一次）
  POST zzz/codex/refresh  重新抓取并落盘到 data/zzz/
"""
from __future__ import annotations

from loguru import logger

from ...core.web import json_response

from ...core.web import fail
from . import build as codex_build
from . import data as codex_data


async def api_zzz_codex():
    """绝区零图鉴：代理人 / 音擎 / 驱动盘 / 邦布（静态快照，无需登录）。

    响应里带 `lang_meta`（可选语言清单），但**不带**多语言映射本身 ——
    映射有 ~1.5MB，只有真的切了语言才拉 zzz/codex/lang。

    另外带一份 `pipeline`：「更新数据」的完整流程说明（源地址 / 目录 / 文件名），
    由 codex_build.pipeline() 从真实常量拼出来，给界面上那个「更新方式」小链接看。
    """
    data = codex_data.all_data()
    try:
        how = codex_build.pipeline()
    except Exception as exc:  # noqa: BLE001  说明取不到不影响图鉴本身
        logger.warning("图鉴更新流程说明生成失败：{}", exc)
        how = None
    return json_response({
        "ok": True, "counts": codex_data.counts(), "lang_meta": codex_data.lang_meta(),
        "pipeline": how, **data,
    })


async def api_zzz_codex_lang():
    """图鉴多语言映射：by_cat[类别][id][语言] = 与普通 json 同构的翻译对象。

    数据来自官方 TextMap 文本表，由 codex_lang.py 在抓取阶段反查生成并落盘。
    前端只在「切到非简中」时拉一次，压成「中文原文→译文」扁平表后走内存。
    """
    d = codex_data.lang_data()
    return json_response({
        "ok": True,
        "langs": d.get("langs") or [],
        "lang_names": d.get("lang_names") or {},
        "version": d.get("version") or "",
        "updated": d.get("updated") or "",
        # 按类别×id 组织的「与普通 json 同构」翻译对象：by_cat[类别][id][语言] = 翻译对象。
        # 前端切语言时拉一次，压成「中文原文→译文」扁平表（langText）供 cxT 查。
        "by_cat": d.get("by_cat") or {},
    })


async def api_zzz_codex_refresh():
    """重新抓取图鉴数据（文本 + 图标本地化）并落盘到 data/zzz/ 下的分段文件。

    抓完就持久化：之后前台只读本地快照与本地图标，不再访问外部站点。
    """
    try:
        data = await codex_build.build_and_save()
    except Exception as exc:  # noqa: BLE001
        return fail(f"更新图鉴数据失败：{exc}")
    codex_data.reload()
    return json_response({
        "ok": True,
        "message": f"已更新并落盘（{data.get('updated') or ''}）",
        "version": data.get("version") or "",
        "counts": codex_data.counts(),
    })


ROUTES = [
    ("zzz/codex", "GET", api_zzz_codex, "图鉴静态数据"),
    ("zzz/codex/lang", "GET", api_zzz_codex_lang, "图鉴多语言映射"),
    ("zzz/codex/refresh", "POST", api_zzz_codex_refresh, "重新抓取图鉴"),
]
