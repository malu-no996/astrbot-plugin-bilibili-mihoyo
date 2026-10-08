"""种子数据与面板资源同步（保证「从 GitHub 更新插件」不丢运行数据）。

为什么需要
----------
AstrBot 更新插件（star/updater + core/zip_updater `_finalize_extracted_archive`）对压缩包里
出现的**每个目录**都是先 `shutil.rmtree(目标)` 再整体覆盖 —— 也就是说：

  · 仓库里有 data/     → 每次更新都会把 data/ 整个删掉（账号凭证 / 抽卡记录 / 图标缓存全没）
  · 仓库里有 pages/    → 每次更新都会把 pages/ 整个删掉（面板图标镜像没了，图鉴图全裂）

所以本插件分成三层：

  仓库里的 code（miyoho/、pages/panel/*.html|js|css、main.py）
      ↑ 更新时被替换，这是正常的
  seed/（随仓库发的**只读种子数据**：图鉴快照 JSON + 多语言表 + 配装方案）
      ↑ 更新时被替换；只在 data/ 里缺文件时补进去，绝不覆盖已有数据
  data/（**运行数据**，整目录已 gitignore，不进压缩包 → 更新不会碰它）
      ↑ 账号凭证、绑定、设备指纹、抽卡记录、战绩存档、图标缓存

面板图标镜像是 data/zzz/assets/ 的副本（Page 静态目录必须放真实文件），
而 pages/ 每次更新都会被清空，所以启动时按需重建镜像（resync_page_assets）。
"""
from __future__ import annotations

import shutil

from loguru import logger

from .paths import DATA_DIR, PAGES_ASSETS, SEED_DIR, zzz_path


def ensure_seed_data() -> int:
    """把 seed/ 里缺的数据补进 data/（只补缺，不覆盖）。

    返回补进去的文件数。种子数据是「首次安装即可离线看图鉴」用的，
    用户在面板上点过「刷新图鉴」以后 data/ 里的那份就比 seed/ 新，绝不能反向覆盖。
    """
    if not SEED_DIR.exists():
        return 0
    copied = 0
    for src in sorted(SEED_DIR.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(SEED_DIR)
        dst = DATA_DIR / rel
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            copied += 1
        except OSError as exc:  # noqa: PERF203
            logger.warning(f"种子数据写入失败：{rel}（{exc}）")
    if copied:
        logger.info(f"米游社种子数据已补齐 {copied} 个文件 → {DATA_DIR}")
    return copied


def resync_page_assets() -> int:
    """按需重建面板图标镜像：data/zzz/assets/* → pages/panel/assets/zzz/*。

    插件的 pages/ 每次更新都会被清空重建（见模块 docstring），镜像跟着一起没，
    所以启动时把缺失的补回来。已存在的不重复拷贝（453 个 / 50MB 量级，全量拷太浪费）。
    """
    src_dir = zzz_path("assets")
    dst_dir = PAGES_ASSETS / "zzz"
    if not src_dir.exists():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in src_dir.iterdir():
        if not src.is_file():
            continue
        dst = dst_dir / src.name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        try:
            shutil.copy2(src, dst)
            copied += 1
        except OSError as exc:  # noqa: PERF203
            logger.warning(f"面板图标镜像失败：{src.name}（{exc}）")
    if copied:
        logger.info(f"面板图标镜像已补齐 {copied} 个文件 → {dst_dir}")
    return copied


def bootstrap() -> None:
    """插件启动时调一次：先补数据、再补镜像。"""
    try:
        ensure_seed_data()
        resync_page_assets()
    except Exception as exc:  # noqa: BLE001  —— 启动阶段的 I/O 问题不该拦住插件加载
        logger.warning(f"米游社数据初始化失败（不影响插件加载）：{exc}")


# 供外部（如 asset_cache）复用的路径常量
__all__ = ["bootstrap", "ensure_seed_data", "resync_page_assets", "DATA_DIR", "PAGES_ASSETS"]
