# astrbot-plugin-miyoho · 项目长期笔记

## 目录与部署
- 仓库/工作区：`G:/AI/malu_qq_bot/astrbot-plugin-miyoho`
- **线上运行目录：`C:\Users\Administrator\.astrbot\data\plugins\astrbot_plugin_miyoho`**
  （不是软链，是独立副本；改完工作区要 `cp` 过去 + WebUI 重载插件才生效）
- 运行数据全在线上目录的 `data/`（不进仓库）：`miyoushe_accounts.json`（加密凭证）、
  `miyoushe_binds.json`（QQ↔米游社绑定）、`zzz/social.json`、`zzz/subscribe.json`、
  `zzz/group_seen.json`、`zzz/member_binds.json`、`zzz/{uid}.csv`、`zzz/records/`
- `tools/port_miyoho_panel.py`（在 malu_qq_bot 仓库里）会把 `pages/panel` **全量重建**，
  源是 `plugins/_vendor/miyoushe/web`。AstrBot 版前端**已与那个源分叉**
  （AstrBot 侧多了「群订阅」等改动），**重跑该工具会冲掉这些改动**。

## 面板前端约定（pages/panel）
- **HTML 全内联在 `index.html`**（没有 partial/FRAG 装配 —— 那是 NoneBot 侧 config_web 的玩法）；
  逻辑与样式才是独立文件：`js/frag/<名字>.js`（`window.MysXxx = { init(mys), setup(ctx, mys) }`）
  + `css/frag/<名字>.css`（前缀化类名），在 index.html 里用 `<link>`/`<script>` 引入。
- 状态挂在 `mys.<域>` 上，由该 frag 的 `init()` 建；`module.js` 里 `init` → `setup` → `ctx.expose`。
- **`ctx.expose` 是白名单**：模板里用到的每个标识符都必须在 `module.js` 的 expose 里登记，
  漏了就是 undefined / “is not a function”。expose 对象里**不要写注释**。
- 一级分类（zone）在 `index.html` 的 `.mys-zones`，主区用
  `<template v-if="mys.zone==='zzz'"> / v-else-if / v-else` 三选一；
  新增一个 zone = ① nav 加按钮 ② 末尾加 `<template v-else>` ③ `mysZoneTo` 白名单 + 懒加载
  ④ module.js 注册 init/setup ⑤ expose。
- 插入新 `<template>` 时注意：`</template>` 是**与前一个共用**的，pasting 时容易漏一个
  （用标签配对脚本查一遍最保险）。
- 危险操作用页内 `.modal/.warn-box`，不用 `window.confirm`；浮层不要放进 `v-show` 容器里。
- **`v-if` / `v-else-if` 别把「辅助信息块」和「主体列表」串成一条链**：真事故 ——
  「机器人一览」(v-if) 与「订阅的群列表」(v-else-if) 写在一起，前者几乎恒真（机器人列表一定非空），
  结果群卡片永远不渲染，现象是「有 chip、但一个订阅都看不到」。两块要各自独立 `v-if`。
- **`pages/panel/*` 是静态文件、刷新页面即生效；但后端路由要插件重载才注册**。
  只改前端时用户刷新就能看到，容易误以为「已经生效了」；改 `.py` 后不重载就是 404。

## Python 取可变容器的坑（2026-10-09 实锤）
- **绝不要写 `x = d.get("k") or {}` 然后往 x 里写**：空 dict 是 falsy，`or {}` 会造一个
  **新对象**，改动全落在临时对象上，存盘时还是那个空表 —— 表现为「操作回成功、文件 mtime 更新、
  内容却没变」。取容器一律 `d.setdefault("k", {})`（返回真实对象）。
  只读取值做兜底时 `or {}` 才安全。

## 群订阅相关（2026-10-08）
- 「订阅米哈游服务」= 独立管理命令，群主（或 AstrBot 管理员）在群里发；
  数据落 `subscribe.json`，key = **平台实例 ID**（`event.get_platform_id()`，与 bots/bot_cmds 同口径）。
- 面板「群订阅」页 = `js/frag/subscribe.js`（gs* 方法）+ `subscribe_routes.py`；
  **写操作一律服务端为准、逐条改**，绝不再走「整份覆盖」——
  整份覆盖 + 前端状态过期 = 把订阅清空（真实事故）。
- 群员绑定表 = `seen.members(gid)`（足迹，含昵称）× `bind.accounts(uid)` 算出来的；
  只有「禁用/删除」的态度存在 `member_binds.py`（key = `gid|member_id|account_id`）。
- QQ 官方协议拿不到群成员名单，也常常给不出昵称 → 这一页只能按「在本群说过话的人」算，
  名字可能为空（页面显示「—」）。
