"""AstrBot Web API 公共件（原 FastAPI 版 `src/core/web.py` 的移植替身）。

原插件的接口都挂在 NoneBot 的 FastAPI app 上，用 `_allowed(request)` 做「仅本机」
鉴权；AstrBot 插件 API 由 Dashboard 统一鉴权（bridge 自动带身份），所以这里不再有
_allowed/_denied —— 只保留「统一异常收敛」和「读 JSON body」两个帮手。

路由注册方式（与原插件不同）：
  每个功能目录的 routes.py 导出 `ROUTES: list[tuple[suffix, method, handler, desc]]`，
  main.py 启动时遍历全部模块统一 `context.register_web_api(f"/miyoho/{suffix}", …)`。
  suffix 不带插件名前缀（前缀在 main.py 的 PREFIX 里）。
"""
from __future__ import annotations

import httpx
from loguru import logger

from astrbot.api.web import error_response, json_response, request

from . import mys

# 插件名（register_web_api 的路由必须带它做前缀；main.py 会用）
PLUGIN_NAME = "astrbot_plugin_miyoho"
PREFIX = f"/{PLUGIN_NAME}"


def fail(message: str, status: int = 400):
    """业务失败 → 400 JSON（前端 bridge 会 reject，message 直接可显示）。"""
    return error_response(message, status_code=status)


def ok(**kwargs):
    return json_response({"ok": True, **kwargs})


async def call(coro, error_prefix: str, status: int = 400):
    """统一兜异常：业务错误 → error_response，其余 → 500 JSON（避免纯文本 500）。"""
    try:
        return None, await coro
    except (mys.MysError, httpx.HTTPError) as exc:
        logger.warning(f"miyoho 接口失败 · {error_prefix}：{exc}")
        return fail(f"{error_prefix}：{exc}", status), None
    except Exception as exc:
        logger.exception(f"miyoho 接口异常：{error_prefix}")
        return fail(f"{error_prefix}：{type(exc).__name__}: {exc}", 500), None


async def body() -> dict:
    """读 JSON body，坏请求当空 dict。"""
    try:
        data = await request.json(default={})
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}
