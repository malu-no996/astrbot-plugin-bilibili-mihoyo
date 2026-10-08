"""绝区零 · 图鉴：代理人 / 音擎 / 驱动盘 / 邦布（本地静态快照，无需登录）。

    data.py    读本地快照 + 多语言映射（4 类 × 6 语言合并回「中文原文 → 译文」）
    build.py   抓取脚本：拉官方数据 + 把图标下载到本机，落盘 data/zzz/
    lang.py    多语言：从官方 TextMap 文本表反查生成翻译（落盘 data/zzz/lang/）
    routes.py  管理页路由 /zzz/codex、/zzz/codex/lang、/zzz/codex/refresh

⚠️ build.py / lang.py **不能有相对导入**：它们会被 `tools/gen_zzz_codex.py`
用 importlib **按文件路径**单独加载（为了在 NoneBot 没初始化时也能跑命令行）。
定位目录一律用 `Path(__file__).resolve().parents[3]`（本目录在 src/zzz/codex/ 下）。
"""
