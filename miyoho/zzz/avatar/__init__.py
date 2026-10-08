"""绝区零 · 角色：已拥有角色列表 / 单个角色详情。

    api.py      官方 avatar/basic、avatar/info、/index（玩家概览）+ Enka 备用源
    detail.py   把官方 / Enka 两种返回归一化成前端要的模型
    char_map.py 角色 id → 名字 / 全称 / 稀有度 / 元素 / 职业 / 阵营 / 图标
    routes.py   管理页路由 /zzz/roles、/zzz/avatars、/zzz/avatar/info

出图（QQ 命令「代理人详细」用，版面照网页版浮层 frag/zzz-avatar.*）
    panel.py         主渲染器 render_agent_detail（吃 detail.py 归一化后的模型）
    card_assets.py   素材 / 字体 / 图标 / 远程图缓存（网页版拼图标 URL 也用它）
    texture2d/ fonts/   贴图与字体（来自 ZZZeroUID，坐标按这套素材调的）

⚠️ 这几个官方接口带**「角色详情」风控**（未公开或设备不受信 → retcode=10041），
网页侧命中时自动降级 Enka（只在游戏内「展示栏」的那几个角色）。
"""
