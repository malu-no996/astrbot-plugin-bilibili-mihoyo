"""社交命令配置：把 QQ 命令从「写死在代码里」变成「页面上可配置」。

结构（与原项目一致，本文件只是门面）：

1. **接口注册表（INTERFACES）** —— 「纯后台接口」都在 `@interface(...)` 里注册。
   每个接口是一个 `async def handler(ctx) -> str`，返回值统一由 `run_interface` 处理成
   「要发出去的文本」。接口与「怎么触发」完全解耦：页面上配哪条命令绑哪个接口都行。
2. **配置（data/zzz/social.json）** —— 命令 / 别名 / 绑定接口 / 是否仅管理员 / 是否启用，
   外加「每个平台实例的总开关」与「实例 × 命令」的逐命令开关。
3. **分发（dispatch.handle_message）** —— main.py 的事件监听把 AstrMessageEvent 交进来，
   匹配命令 → 检查开关与权限 → 执行接口 → 返回要回复的文本。

文件布局
--------
  core.py      机制层：Ctx / INTERFACES / @interface / 回复模板 / run_interface
  base.py      接口公共工具：输出方式选项 / 战绩存档 / 查询目标解析 / 发消息与发图
  qq.py        QQ 官方按钮消息（AstrBot 版降级：全部退回纯文字，接口签名不变）
  account.py   账号类接口：米游社登录 / 我的绑定 / 切换账号 / 解绑
  record.py    绝区零战绩类接口：切换角色 / 危局强袭战 / 式舆防卫战
  rank.py      群排行接口：危局 / 绝境 / 防卫战 的本群排行榜（只读本地存档）
  seen.py      群成员足迹（谁在哪个群发过话）
  explore.py   绝区零探索类接口：零号空洞 / 迷宫诡域 / 临界推演
  note.py      接口：实时便笺
  profile.py   接口：绝区零玩家概览
  card.py      接口：代理人面板（出图）
  month.py     接口：绳网月报
  gacha.py     接口：调频（抽卡）总结（文字 / 图片）
  codex.py     接口：图鉴（不需要登录，读本地快照）
  sign.py      接口：每日签到（默认账号 / 全部账号）
  autosign.py  接口：一键签到
  help.py      接口：命令菜单
  cfg.py       配置读写（data/zzz/social.json）
  dispatch.py  命令匹配与分发（handle_message，供 main.py 调用）
  routes.py    管理页路由（social/config、social/preview）
"""
from __future__ import annotations

# ⚠️ 顺序有意义：接口按导入顺序进注册表，页面下拉框就是按注册顺序列的。
from . import account          # noqa: F401 —— 米游社账号类接口
from . import record           # noqa: F401 —— 绝区零战绩类接口（紧接账号类，保持原顺序）
from . import rank             # noqa: F401 —— 群排行（危局 / 绝境 / 防卫战，只读存档）
from . import explore          # noqa: F401 —— 探索类：零号空洞 / 迷宫诡域 / 临界推演
from . import note             # noqa: F401 —— 实时便笺（体力）
from . import profile          # noqa: F401 —— 玩家概览
from . import card             # noqa: F401 —— 代理人面板（出图）
from . import month            # noqa: F401 —— 绳网月报
from . import gacha            # noqa: F401 —— 调频总结
from . import codex            # noqa: F401 —— 图鉴接口
from . import sign             # noqa: F401 —— 签到接口
from . import autosign         # noqa: F401 —— 一键签到
from . import help             # noqa: F401 —— 命令菜单（它就是「帮助」，排在菜单末尾）

from .cfg import (  # noqa: F401
    DEFAULT_COMMANDS,
    _clean_options,
    _clean_tpl,
    load_cfg,
    save_cfg,
)
from .core import (  # noqa: F401
    Ctx,
    INTERFACES,
    apply_tpl,
    interface,
    render_template,
    run_interface,
)
from .dispatch import bot_allows, handle_message, match_command  # noqa: F401
from .routes import ROUTES  # noqa: F401 —— main.py 统一注册
