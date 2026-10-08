"""插件内路径与数据目录（astrbot-plugin-miyoho 专用）。

原项目（malu_qq_bot）的数据都落在仓库根 `data/` 下；AstrBot 插件没有"仓库根"的
概念（插件装在 AstrBot 的 plugins 目录里，用户数据应随插件走、卸载可清理），所以
统一改成**插件目录下的 data/**：

    <插件根>/data/
      miyoushe_accounts.json     账号凭证（securestore 加密）
      miyoushe_binds.json        QQ 用户 ↔ 米游社账号绑定
      miyoushe_devices.json      设备指纹配置
      zzz/social.json            社交命令配置
      zzz/{uid}.csv              抽卡记录
      zzz/records/…              战绩自动存档
      zzz/assets/…               图鉴/角色图标缓存（哈希名）
      zzz/codex/…                图鉴快照

⚠️ 前端（pages/panel）需要直接 <img> 引用图标。AstrBot 的 Page 静态资源按真实
文件路径服务，所以 asset_cache 每次落盘都会**同步拷贝一份**到
`pages/panel/assets/zzz/`（见 asset_cache.sync_to_page）。
"""
from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PLUGIN_ROOT / "data"
PAGES_ASSETS = PLUGIN_ROOT / "pages" / "panel" / "assets"


def data_path(*parts: str) -> Path:
    """插件 data/ 下的相对路径（自动建父目录的事由调用方决定）。"""
    return DATA_DIR.joinpath(*parts)


def zzz_path(*parts: str) -> Path:
    return data_path("zzz", *parts)


def page_asset_path(*parts: str) -> Path:
    return PAGES_ASSETS.joinpath(*parts)
