"""米游社图片资源后台缓存。

为何需要：
- 前台用 html-to-image 截图、或后台用无头浏览器生成图片时，米游社 CDN（act-webstatic.mihoyo.com）
  的图片跨域（CORS）会导致画布被污染 / 立绘画不出来。
- 解决：后台把用到的图片（BOSS 立绘、代理人头像、邦布、增益图标、玩家头像）下载到本机
  `plugins/_vendor/miyoushe/data/zzz/assets/`，接口返回「本地路由」URL；同一份图只下载一次（按 URL 哈希去重）。
- 后续后台生成图片时，直接把这些本地文件以 data URI 内联进 HTML，完全离线、不受跨域限制。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import mimetypes
import pathlib

import httpx
from loguru import logger

from . import mys as _client

# 缓存目录：插件 data/zzz/assets/（用 paths 定位，别依赖 CWD）。
# ⚠️ 前端 Page 里的 <img> 不能带鉴权头，但相对路径静态文件由 AstrBot 直接服务，
#    所以每次落盘都同步拷贝一份到 pages/panel/assets/zzz/（sync_to_page）。
from ..paths import page_asset_path

ASSET_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "zzz" / "assets"
PAGE_ASSET_DIR = page_asset_path("zzz")
ASSET_DIR.mkdir(parents=True, exist_ok=True)
PAGE_ASSET_DIR.mkdir(parents=True, exist_ok=True)


def sync_to_page(name: str) -> None:
    """把一个缓存图标同步到 Page 静态目录（前端 <img src="./assets/zzz/<name>">）。"""
    try:
        src = asset_path(name)
        if src.exists():
            (PAGE_ASSET_DIR / name).write_bytes(src.read_bytes())
    except OSError as exc:  # noqa: BLE001
        logger.warning("同步图标到 Page 静态目录失败 {}: {}", name, exc)

# 图片下载用【专属于本模块】的客户端，不复用 client.get_client() 的共享单例。
# 原因：共享 _client 在别处会被 async with 打开/关闭，并发下载时套 async with 会触发
# "Cannot open a client instance more than once"。本客户端只发 .get()（不套上下文管理器），
# httpx 原生支持对同一实例并发请求。
_DL_CLIENT: httpx.AsyncClient | None = None


def _dl_client() -> httpx.AsyncClient:
    global _DL_CLIENT
    if _DL_CLIENT is None or _DL_CLIENT.is_closed:
        _DL_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=10),
            follow_redirects=True,
            headers={"User-Agent": _client.UA},
        )
    return _DL_CLIENT

# 这些字段在战绩 JSON 里是图片 URL
# ⚠️ monster_pic 是**防卫战**的小队 BOSS 立绘 —— 网页端直接用 CDN 地址就行，
# 但命令的图片模式要用它的像素（本地存档里没有就画不出），所以也得一并缓存。
# ⚠️ camp_icon 是**代理人详情**的阵营 LOGO（官方 group_icon_path）—— 名字由
# zzz/avatar/detail._camp_icon() 下发，改那边就要同步改这里，否则不会被缓存成本地路由。
IMAGE_KEYS = {"role_square_url", "bangboo_rectangle_url", "bg_icon", "avatar_icon",
              "icon", "monster_pic", "camp_icon"}


def _ext(url: str) -> str:
    p = url.split("?")[0].lower()
    for suf in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if p.endswith(suf):
            return "jpg" if suf in (".jpg", ".jpeg") else suf.lstrip(".")
    return "png"


def _name(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest() + "." + _ext(url)


def asset_path(name: str) -> pathlib.Path:
    """本地落盘路径（name 是哈希+扩展名，天然防目录穿越）。"""
    return ASSET_DIR / name


async def download(url: str) -> str:
    """下载（已存在则跳过）并返回本地路由 /zzz/asset/<name>；失败则回退原 URL。"""
    if not url or not url.startswith("http"):
        return url
    name = _name(url)
    path = asset_path(name)
    if not path.exists():
        try:
            c = _dl_client()
            r = await c.get(url)
            r.raise_for_status()
            path.write_bytes(r.content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("缓存米游社图片失败 {}: {}", url, exc)
            return url
    sync_to_page(name)
    return f"/miyoho-asset/{name}"


def _collect(node, out: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in IMAGE_KEYS and isinstance(v, str) and v.startswith("http"):
                out.append(v)
            else:
                _collect(v, out)
    elif isinstance(node, list):
        for x in node:
            _collect(x, out)


def _replace(node, mapping: dict) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in IMAGE_KEYS and isinstance(v, str) and v in mapping:
                node[k] = mapping[v]
            else:
                _replace(v, mapping)
    elif isinstance(node, list):
        for x in node:
            _replace(x, mapping)


async def rewrite_assets(data: dict) -> dict:
    """把战绩 JSON 里所有米游社图片 URL 改写成后台缓存的本地路由（危局 / 防卫战通用）。"""
    urls: list[str] = []
    _collect(data, urls)
    if not urls:
        return data
    uniq = list(dict.fromkeys(urls))
    results = await asyncio.gather(*[download(u) for u in uniq], return_exceptions=True)
    mapping = {u: (r if not isinstance(r, Exception) else u) for u, r in zip(uniq, results)}
    _replace(data, mapping)
    return data


def embed_src(url: str) -> str:
    """本地路由 → data URI（供后台生成图片时把图内联进 HTML，完全离线）。非本地 URL 原样返回。"""
    if not url or not url.startswith("/miyoho-asset/"):
        return url
    name = url.rsplit("/", 1)[-1]
    path = asset_path(name)
    if not path.exists():
        return url
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"
