"""astrbot-plugin-miyoho —— 米哈游（米游社）插件（AstrBot）。

由 malu_qq_bot 的 miyoushe 插件移植，功能与原插件对齐：

- **扫码登录**（passport-api.miyoushe.com，hyp 优先 / web 兜底），多账号管理，
  凭证（ltoken_v2 / stoken / cookie_token 等）经 securestore 加密落盘
  <插件>/data/miyoushe_accounts.json
- **面板（pages/panel，Vue 3）**：账号侧边栏 / 绝区零（抽卡 · 危局 · 防卫战 · 空洞 ·
  诡域 · 便笺 · 临界推演 · 概览 · 图鉴 · 签到 · 自动签到）/ 社交命令配置 /
  **群订阅（订阅的群 · 群员绑定账号）** / 设备配置
- **QQ 群社交命令**：「指令组 + 子命令」两段式 —— `mhy login` / `mhy account` / `mhy switch` /
  `mhy unbind` / `mhy role`（米游社账号类）、`zzz deadly` / `zzz shiyu` / `zzz note` / `zzz sign`
  ……（绝区零查询类）。**命令词用英文，别名照旧全中文**；命令表在面板上可自由增删改，
  **每个平台实例一个总开关、默认关闭**
- **后台任务**：战绩自动存档（每天抓一轮「本期 + 上期」，官方会丢历史）；
  自动签到（定时 + 浮动 + 结果私聊通知，仅 OneBot 实例）

二维码图片不落路由（Page 的 <img> 带不上鉴权头）：login/start 直接返回 base64 data URI。
图鉴/角色图标：后端 asset_cache 每次落盘都同步一份到 pages/panel/assets/zzz/，
前端用相对路径 <img src="./assets/zzz/<name>">（AstrBot 会自动加 asset_token）。
"""
import asyncio
import base64

from loguru import logger

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from .miyoho.core.web import error_response, json_response, request

from . import miyoho
from .miyoho import securestore, seed
from .miyoho.core import asset_cache, bind, device, mys as client, store
from .miyoho.core import device_routes
from .miyoho.core.web import PLUGIN_NAME, PREFIX, body, call, fail
from .miyoho import send as send_shim
from .miyoho.social import dispatch, routes as social_routes
from .miyoho.social import subscribe_routes
from .miyoho.zzz.avatar import routes as avatar_routes
from .miyoho.zzz.codex import routes as codex_routes
from .miyoho.zzz.gacha import routes as gacha_routes
from .miyoho.zzz.month import routes as month_routes
from .miyoho.zzz.record import capture, routes as record_routes
from .miyoho.zzz.sign import autosign, autosign_routes, routes as sign_routes


class MiyohoPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        send_shim.bind_context(context)
        self._tasks: list[asyncio.Task] = []
        self._register_apis()

    # ==================================================================
    # 生命周期
    # ==================================================================

    async def initialize(self):
        """启动后台循环：战绩自动存档（每天）+ 自动签到（每 30s 看一次到点没有）。

        启动前先 bootstrap：把 seed/ 里缺的种子数据补进 data/、把缺失的面板图标镜像
        补齐（AstrBot 更新插件会清空 pages/ 与仓库里出现过的目录，见 miyoho/seed.py）。
        """
        seed.bootstrap()
        self._tasks.append(capture.start_capture_loop())
        self._tasks.append(asyncio.create_task(autosign.loop()))
        cfg = autosign.load()
        if cfg.get("enabled") and cfg.get("mode") == autosign.MODE_AUTO and not int(cfg.get("next_run_at") or 0):
            autosign.save({})
        logger.info("米游社后台任务已启动（战绩自动存档 + 自动签到）")

    async def terminate(self):
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()

    # ==================================================================
    # Web API：账号 / 登录（原插件入口 __init__.py 那部分）
    # ==================================================================

    async def api_state(self):
        """当前登录状态 + 全部账号（脱敏）。"""
        return json_response(
            {
                "ok": True,
                **store.masked(),
                "qr_ready": client._qr_ready(),
            }
        )

    async def api_login_start(self):
        """申请登录二维码；返回 base64 data URI 形式的二维码图。

        body 可传 {"mode": "hyp" | "web"}：
          hyp（**默认**）= 一次扫码拿全：stoken（抽卡要用）+ 自动换 cookie_token，
            并补全 account_id / ltuid_v2 等等价键名，角色列表 / 战绩 / 抽卡都能用。
          web = 兜底：HYP 接口不可用时自动回退，只发 ltoken / cookie_token，
            **不含 stoken**，落到这条分支时抽卡需要再补一次授权。
        """
        payload = await body()
        mode = str((payload or {}).get("mode") or "hyp").lower()
        if mode not in ("web", "hyp"):
            mode = "hyp"
        err, info = await call(client.login_start(mode), "申请登录二维码失败")
        if err:
            return err
        info = info if isinstance(info, dict) else {}
        ticket = str(info.get("ticket") or "")
        url = str(info.get("url") or "")
        if not ticket:
            return fail("申请登录二维码失败：米游社未返回 ticket")
        # Page 里的 <img> 带不上鉴权头，二维码直接给 base64（不再走 /qr.png 路由）
        png = client.qr_png(url)
        data_uri = ""
        if png:
            data_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        return json_response(
            {
                "ok": True,
                "ticket": ticket,
                "url": url,
                "png": data_uri,
                "expires_in": int(info.get("expires_in") or 180),
                "mode": str(info.get("mode") or mode),
                "qr_ready": client._qr_ready(),
            }
        )

    async def api_login_poll(self):
        """轮询扫码结果：waiting / scanned / expired / canceled / success / error。"""
        t = str(request.query.get("t", "") or "")
        try:
            result = await client.login_poll(t)
        except Exception as exc:
            return json_response({"ok": False, "status": "error", "message": str(exc)})
        return json_response({"ok": True, **(result if isinstance(result, dict) else {})})

    async def api_accounts_select(self):
        """切换当前账号。body: {account_id}。"""
        payload = await body()
        aid = str(payload.get("account_id") or "")
        if not aid:
            return fail("缺少 account_id")
        done = store.select(aid)
        return json_response(
            {"ok": done, "message": "已切换账号" if done else "账号不存在", **store.masked()}
        )

    async def api_accounts_delete(self):
        """删除账号（仅清本机凭证）。body: {account_id}。"""
        payload = await body()
        aid = str(payload.get("account_id") or "")
        if not aid:
            return fail("缺少 account_id")
        done = store.remove_account(aid)
        if done:
            # QQ 侧可能还有人绑着这个账号，一并清掉：
            # 留着会出现「绑定还在、账号没了」→ 查询时报一堆莫名错误。
            try:
                n = bind.drop_account(aid)
                if n:
                    logger.info(f"miyoho 账号 {aid} 已删除，同步清理了 {n} 条 QQ 绑定")
            except Exception as exc:  # noqa: BLE001 —— 清绑定失败不回滚删账号
                logger.warning(f"miyoho 清理 QQ 绑定失败（账号 {aid}）：{exc}")
        return json_response(
            {"ok": done, "message": "已删除账号" if done else "账号不存在", **store.masked()}
        )

    async def api_logout(self):
        """退出登录：清空全部账号凭证。"""
        store.clear_all()
        return json_response(
            {"ok": True, "message": "已退出米游社登录（本地凭证已清除）", **store.masked()}
        )

    # ==================================================================
    # QQ 群社交命令（事件监听，不出现在 /help）
    # ==================================================================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def social_cmd_listener(self, event: AstrMessageEvent):
        """可配置的社交命令分发：命令表 / 触发词 / 机器人开关都在面板上配。"""
        try:
            text = await dispatch.handle_message(event)
        except Exception as exc:  # noqa: BLE001 —— 分发器自己的异常绝不能打断事件流
            logger.exception("miyoho 社交命令分发异常")
            text = f"执行失败：{type(exc).__name__}: {exc}"
        if text is None:
            # 没命中：把「本插件 ALL 监听器」放大出来的唤醒标记还原，
            # 免得群里随便一句话都被当成唤醒送进 LLM（详见 dispatch 顶部注释）
            dispatch.restore_wake_state(event)
            return
        if text:
            # 原样发送：格式交给平台（官方机器人按 AstrBot 的 use_markdown 配置走
            # markdown，NapCat 走纯文本），这里不做任何 markdown/纯文本改写
            yield event.plain_result(text)
        # 命令已处理完：停掉事件，别让 AstrBot 接着把这条也交给 LLM（会重复回一条）
        event.stop_event()

    # ==================================================================
    # 路由注册
    # ==================================================================

    def _register_apis(self) -> None:
        reg = self.context.register_web_api

        # ---------------- 账号 / 登录 ----------------
        reg(f"{PREFIX}/state", self.api_state, ["GET"], "米游社登录状态与账号列表")
        reg(f"{PREFIX}/login/start", self.api_login_start, ["POST"], "申请登录二维码")
        reg(f"{PREFIX}/login/poll", self.api_login_poll, ["GET"], "轮询扫码结果")
        reg(f"{PREFIX}/accounts/select", self.api_accounts_select, ["POST"], "切换当前账号")
        reg(f"{PREFIX}/accounts/delete", self.api_accounts_delete, ["POST"], "删除账号")
        reg(f"{PREFIX}/logout", self.api_logout, ["POST"], "退出登录")

        # ---------------- 各功能模块（ROUTES 注册表） ----------------
        modules = (
            device_routes,          # 设备配置（core）
            social_routes,          # 社交命令配置
            subscribe_routes,       # 群订阅（订阅的群 + 群员绑定）
            record_routes,          # 绝区零战绩（危局/防卫战/空洞/诡域/便笺/临界推演/存档）
            gacha_routes,           # 调频（抽卡）
            codex_routes,           # 图鉴
            avatar_routes,          # 角色
            month_routes,           # 绳网月报
            sign_routes,            # 每日签到
            autosign_routes,        # 自动签到
        )
        n = 0
        for mod in modules:
            for suffix, method, handler, desc in getattr(mod, "ROUTES", []):
                reg(f"{PREFIX}/{suffix}", handler, [method], desc)
                n += 1
        logger.info(f"miyoho 已注册 {n} + 6 个面板接口（{PLUGIN_NAME}）")
