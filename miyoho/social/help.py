"""社交命令 · 命令菜单（帮助）。

从 social.py 拆出来的第八块：把「当前有哪些命令能用」做成一条命令发出去。
（清单见门面 `social.py`，本文件排在最后 —— 它就是「帮助」，放菜单末尾最自然。）

两个平台的呈现**刻意不同**（用户要求）：

  · **OneBot（第三方）** → 纯文字菜单，**只列命令词、一行一个**（不要标题 / 别名 /
    说明 / 尾巴 —— 用户原话「就只显示命令就好了」）；
  · **QQ 官方** → 发**排好版的按钮**（一行 = 一排，文案可自定义），点一下等于客户端
    替用户发了这条命令，照常进社交命令分发，**开关 / 权限 / 别名全都一样生效**
    （不会绕开 `bot_allows`）。按钮上方必须带**一行短文字**（`kb_text`，默认
    「绝区零菜单」）—— 官方**不允许正文为空**，实测报 `40034030 消息content字段不能为空`。
    按钮的显示文案与分行布局在「详细设置 → 按钮布局」里配。
  · 按钮发不出去时 → 退回纯文字菜单，并把**失败原因**附在正文末尾
    （`social_qq.send_buttons` 返回的 why）。上一版是静默 fallback，结果就是
    「用户只看到文字，不知道按钮为什么没来」—— 别再回到那种写法。

⛔ **别再试「空正文 + 按钮」**：官方会收下请求、客户端却只显示一条空白消息
（或者直接 400 报错），按钮也出不来 —— 用户实测过，这条弯路不要再走。

⚠️ 按钮的**机制**（键盘构造 / 两档降级发送 / 场景判定）已经抽到 `social_qq.py`，
那边还有一份更全的踩坑记录（官方硬限制、内邀能力、空正文报错…）——
米游社切换（account.mys_switch）发按钮走的是同一套，改一处两边都变。

OneBot 侧正文两套模板（onebot / qq）都能配；**qq 留空时自动借用 onebot 那套**，
见 `core.apply_tpl`。默认正文之外的内容（标题 / 别名 / 说明 / 是否开启）
都还在 vars 里，想要就自己配模板。

⚠️⚠️ **「自定义按钮」是 QQ 官方平台的内邀能力**（官方文档「消息按钮」：
「【申请使用】按钮模版 /【内邀开通】自定义按钮」，且「在 markdown 消息的基础上
挂载按钮」）。未开通的机器人发出去往往**不报错**，官方直接把 keyboard 忽略掉 ——
表现就是「只看到一屏文字，什么异常都没有」。所以：

  · 看不到按钮 ≠ 代码有问题，先看后台那行
    `绝区零帮助：按钮请求已发出（markdown + 按钮…）`（social_qq 记的，日志前缀是命令词）
    —— 有它就说明请求被官方收下了，剩下的是平台侧没渲染，要去 QQ 开放平台申请；
  · 反过来，没有那行、而有 `按钮…失败` 的 ERROR，才是代码/权限报错，
    失败原因会一并附在菜单末尾给用户看。
"""
from __future__ import annotations

import re
from typing import Any

from loguru import logger

from .cfg import load_cfg
from .core import INTERFACES, Ctx, interface
from .dispatch import bot_allows
# 按钮机制在 social_qq（米游社切换也用同一套）：这里只按需取用，不再自带一份。
from .qq import (
    COLS_MAX,
    LABEL_MAX,
    ROWS_MAX,
    ZWSP,
    build_keyboard,
    can_use_buttons,
    scene_name,
    send_buttons,
)

# 说明文字截断长度：菜单要一眼看完，长描述只取第一句、再截到这里。
_DESC_MAX = 26


# 菜单标题（也是模板变量 {title}）。
_TITLE = "绝区零 · 命令菜单"
# 「命令菜单 诊断」的触发词：把「协议 → 场景 → 按钮构造 → 发送结果」逐条回报。
# 之所以要留这个口子：按钮不出现时，用户手上只有「一屏文字」，连代码有没有
# 尝试发送都看不出来（静默 fallback 的老问题），诊断是唯一能自证的手段。
_DIAG_WORDS = ("诊断", "自检", "测试", "debug")

# QQ 官方按钮的**默认布局**（详细设置里可改）：一行 = 一排按钮，按钮之间用
# 空格 / 逗号 / 顿号都行；`命令词=显示文字` = 按钮上显示的文字（可与命令词无关），
# 只写命令词 = 显示文字自动取「去掉 绝区零/米游社 前缀」的短词。
# 默认值就是用户要的布局（2026-10-01）：三列开头，签到独占一排的原因是
# 「账号类」和「查询类」分组。⚠️ 没写进来的命令**不出按钮**（如 空洞 / 诡域 / 推演）。
# 高频的那几个才写进来（官方限制每行最多 5 个、最多 5 行，塞太满反而难按）。
# 2026-10-05：代理人面板加在**第四行第一个**（用户指定），这也是唯一一排满 5 个的。
# 2026-10-05：三条群排行（危局 / 绝境 / 防卫战）加在**第五行**（用户指定）。
# 2026-10-07：第四行末尾的「帮助」按钮换成「抽卡查询」（用户要求 —— 抽卡是高频查询，
#   帮助本身还能用「绝区零菜单 / zzz帮助」打出来，不缺入口）。
# 注意：**运行时用的是 `data/zzz/social.json` 里那份**，这里只是新装默认值 ——
# 改按钮布局两处都要动，只改这里不生效。
_DEFAULT_BUTTONS = (
    "米游社登录=扫码登录 米游社账号=账号查看 米游社解绑=账号删除\n"
    "米游社切换=账号切换 签到 实时便笺=体力\n"
    "切换角色 绝区零危局=危局 绝区零防卫战=防卫战\n"
    "代理人面板 绝区零档案=档案 绳网月报=月报 绝区零图鉴=图鉴 绝区零抽卡=抽卡查询\n"
    "危局群排行 绝境群排行 防卫战群排行"
)
# 自动生成布局时（「按钮布局」留空）用的前缀剥离表：按钮文案要短。
_LABEL_STRIP = ("绝区零", "米游社")
# 按钮消息里那行正文（可配，见选项 kb_text）：QQ 官方的按钮必须挂在 markdown 消息上，
# 正文又不能空（官方直接报 40034030 消息content字段不能为空），所以给一句最短的、
# 有意义的话 —— 默认就是命令词本身。留空才退到 zero-width 空格（见 social_qq.ZWSP）。
_DEFAULT_KB_TEXT = "绝区零菜单"


def _first_sentence(text: Any) -> str:
    """取描述的第一句；中文句读、问号、换行都算断句。"""
    s = " ".join(str(text or "").split())
    for sep in ("。", "；", "！", "？", "?", "\n"):
        i = s.find(sep)
        if i > 0:
            s = s[:i]
    return s.strip()


def _cmd_desc(api: str) -> str:
    """命令说明：用接口注册表里的 desc 第一句（页面上那条接口说明就是它）。"""
    info = INTERFACES.get(str(api or "")) or {}
    s = _first_sentence(info.get("desc"))
    return s[:_DESC_MAX] + "…" if len(s) > _DESC_MAX else s


def _menu_items(only_ready: bool, self_id: str) -> tuple[list[dict], int, int]:
    """菜单里的命令行 → (rows, 本机器人可用数, 启用的命令总数)。

    `self_id` 为空（管理页预览、或拿不到 bot）时**不过滤也不标注**，一律当可用 ——
    否则预览里会一片「未开启」，看不出菜单本来长什么样。
    """
    cfg = load_cfg()
    rows: list[dict] = []
    ready = 0
    total = 0
    for c in cfg.get("commands") or []:
        if not isinstance(c, dict) or not c.get("enabled", True):
            continue
        total += 1
        ok = bot_allows(cfg, self_id, c) if self_id else True
        if ok:
            ready += 1
        if only_ready and not ok:
            continue
        cmd_name = str(c.get("cmd") or "")
        if not cmd_name:
            continue
        aliases = [str(a) for a in (c.get("aliases") or []) if str(a).strip()]
        rows.append({
            "index": len(rows) + 1,
            "id": str(c.get("id") or ""),
            "cmd": cmd_name,
            "aliases": "、".join(aliases),
            "desc": _cmd_desc(str(c.get("api") or "")),
            "api": str(c.get("api") or ""),
            "ready": ok,
        })
    return rows, ready, total


def _plain_text(rows: list[dict]) -> str:
    """默认正文：**只有命令词**，一行一个。

    用户明确要求「就只显示命令就好了」—— 标题、别名、一句说明、末尾提示全都不放
    （句读越少越好认，也避免 QQ 官方 markdown 通道把 `#`/`-` 之类当语法渲染）。
    这些内容仍然作为模板变量提供（{title} / {aliases} / {desc} / …），
    想显示的话在「详细设置」里自己配模板。
    """
    return "\n".join(str(r["cmd"]) for r in rows if r.get("cmd"))


def _shorten(cmd: str) -> str:
    """按钮的默认显示文案：剥掉 绝区零/米游社 前缀（用户：菜单本身就是绝区零的）。"""
    for p in _LABEL_STRIP:
        if cmd.startswith(p) and len(cmd) > len(p):
            return cmd[len(p):]
    return cmd


def _parse_buttons(text: Any, rows: list[dict], per_row: int
                   ) -> tuple[list[list[dict]], list[str]]:
    """把「按钮布局」配置解析成按钮行 → (行列表, 没认出来的词)。

    格式（每行 = 一排按钮，按钮间用空格 / 中英文逗号 / 顿号分隔）：
        米游社登录=扫码登录 米游社账号=账号查看      ← 按钮显示「扫码登录」，点了发「米游社登录」
        签到 切换角色                                ← 只写命令词，显示文字自动剥前缀
    匹配对象：命令词**或别名**（精确匹配，优先 cmd）。没配 / 全都认不出来时，
    退回自动布局：按 per_row 个一排、显示文字用 `_shorten` 剥前缀。
    """
    items: dict[str, dict] = {}
    for r in rows:
        items[r["cmd"]] = r
        for a in (r.get("aliases") or "").split("、"):
            if a:
                items.setdefault(a, r)

    def _find(name: str) -> dict | None:
        """先精确匹配（命令词/别名），再「剥前缀」比对 —— 落盘里的命令词常带
        「绝区零/米游社」前缀（如 绝区零签到），布局里写短词「签到」也要能对上。"""
        if name in items:
            return items[name]
        key = _shorten(name)
        return next((x for x in rows if _shorten(x["cmd"]) == key), None)

    out: list[list[dict]] = []
    unknown: list[str] = []
    src = str(text or "").strip()
    if src:
        for line in src.splitlines():
            line = line.strip()
            if not line:
                continue
            btn_row: list[dict] = []
            for tok in re.split(r"[,，、\s]+", line):
                tok = tok.strip()
                if not tok:
                    continue
                parts = re.split(r"[=＝]", tok, maxsplit=1)
                name = parts[0].strip()
                label = (parts[1].strip() if len(parts) > 1 else "") or _shorten(name)
                r = _find(name)
                if r is None:
                    unknown.append(tok)
                    continue
                btn_row.append({"label": label[:LABEL_MAX], "data": r["cmd"]})
            if btn_row:
                out.append(btn_row)
    if unknown:
        logger.info(f"命令菜单：按钮布局里有 {len(unknown)} 个没认出来的词，已跳过：{unknown}")
    if not out:  # 没配或全错 → 自动布局兜底，保证按钮功能不至于直接消失
        for i in range(0, len(rows), per_row):
            out.append([{"label": _shorten(r["cmd"])[:LABEL_MAX], "data": r["cmd"]}
                        for r in rows[i:i + per_row]])
    return out[:ROWS_MAX], unknown


async def _diagnose(ctx: Ctx, btn_rows: list[list[dict]],
                    unknown: list[str], kb_text: str) -> str:
    """按钮自检：逐条回报「协议 → 场景 → 按钮构造 → 发送结果」。

    正常菜单是给所有人看的、不能塞排查信息；可按钮一旦不出现，用户手上就只有
    「一屏文字」，连代码到底有没有走到发送都看不出来。所以留这个口子：
    它会**真的**按正常流程尝试一次带按钮的发送（成功的话你会同时看到按钮），
    并把每一步的结果写在文字里 —— 一眼就能分清是「没走到发送」「发送被拒」
    还是「官方收下了但没渲染」。
    """
    ev_cls = type(ctx.event).__name__ if ctx.event is not None else "None"
    bot_cls = type(ctx.bot).__name__ if ctx.bot is not None else "None"
    can = ctx.event is not None and can_use_buttons(ctx.event)
    kb = build_keyboard(btn_rows)
    # 键位结构是**普通 dict**（botpy 的 TypedDict，不是对象）→ 用 dict 取值，别 getattr
    kb_rows = len(((kb or {}).get("content") or {}).get("rows") or [])
    n_btns = sum(len(r) for r in btn_rows)
    lines = [
        "【命令菜单 · 按钮诊断】",
        f"协议判定：{ctx.protocol}",
        f"机器人类：{bot_cls}",
        f"事件类：{ev_cls}",
        f"场景：{scene_name(ctx.event) if ctx.event is not None else '—'}",
        f"这个场景允许挂按钮：{'是' if can else '否'}",
        f"按钮构造：{'成功（%d 行 %d 个）' % (kb_rows, n_btns) if kb is not None else '失败'}",
        f"按钮上方正文：{kb_text or '（空）'}",
    ]
    if unknown:
        lines.append(f"布局里没认出来的词（已跳过）：{'、'.join(unknown)}")
    if not can or kb is None:
        lines.append("→ 结论：还没走到发送那一步，所以既不会有按钮、也不会有报错")
    else:
        ok, why = await send_buttons(ctx, kb_text, btn_rows)
        lines.append("发送结果：" + ("成功（看看上面有没有按钮）" if ok else why))
    return "\n".join(lines)


@interface(
    "zzz_help", "绝区零 · 命令菜单",
    "把当前可用的命令列成菜单。OneBot 发纯文字（一行一个命令词）；QQ 官方机器人"
    "发一串排好版的**按钮**（点一下就等于发出那条命令），按钮上方只带一行短文字，"
    "按钮文案与分行布局、那行文字都在下方设置里改。发送失败时退回文字并附原因。"
    "附加参数「诊断」可让它回报按钮每一步的判定结果（排查为什么没有按钮）",
    options=[
        {
            "key": "buttons", "label": "按钮布局（QQ 官方）", "type": "textarea",
            "default": _DEFAULT_BUTTONS,
            "hint": "一行 = 一排按钮，按钮间用空格/逗号/顿号分隔；「命令词=显示文字」"
                    "自定义按钮文案（=右边想写什么都行），只写命令词则显示剥掉"
                    "「绝区零/米游社」前缀的短词。留空 = 自动按命令生成。"
                    "没写进来的命令不出按钮",
        },
        {
            "key": "kb_text", "label": "按钮上方那行字（QQ 官方）", "type": "text",
            "default": _DEFAULT_KB_TEXT,
            "hint": "QQ 官方要求带按钮的消息必须有正文，正文为空会直接报错"
                    "（40034030 消息content字段不能为空）→ 所以固定给一句最短的话。"
                    "留空则用不可见的零宽空格（能否显示按钮取决于机器人，一般会变空白消息）",
        },
        {
            "key": "only_ready", "label": "只列本机器人已开启的命令", "type": "switch", "default": True,
            "hint": "影响文字版菜单（OneBot / 按钮发送失败时的退回）与按钮候选范围；"
                    "关掉就列出全部启用的命令",
        },
        {
            "key": "per_row", "label": "按钮每行几个（自动布局用）", "type": "number",
            "default": 3, "min": 1, "max": 5,
            "hint": "只在「按钮布局」留空时生效（官方限制：每行最多 5 个、最多 5 行）",
        },
    ],
    tpl_vars=[
        {"name": "title", "desc": "菜单标题（默认「绝区零 · 命令菜单」）"},
        {"name": "count", "desc": "本机器人可用的命令数"},
        {"name": "total", "desc": "启用的命令总数"},
        {"name": "items", "desc": "命令行列表，配合 {#items}…{/items} 循环输出"},
        {"name": "index / cmd / aliases / desc", "desc": "循环内每条：序号 / 命令词 / 别名（顿号连） / 一句说明"},
        {"name": "ready", "desc": "循环内每条：这条命令在当前机器人是否开启（true / false）"},
        {"name": "button_error", "desc": "按钮没发出去时的原因（正常为空；配了模板时可写它来显示原因）"},
    ],
    sample=(
        "【{title}】{count}/{total} 条可用\n"
        "{#items}{index}. {cmd}（{aliases}）\n"
        "{/items}"
    ),
)
async def _api_zzz_help(ctx: Ctx):
    """命令菜单：OneBot 发纯文字；QQ 官方发「一行短文字 + 按钮」（失败逐档降级并附原因）。"""
    only_ready = bool(ctx.opt("only_ready", True))
    try:
        per_row = int(ctx.opt("per_row", 3) or 3)
    except (TypeError, ValueError):
        per_row = 3
    per_row = max(1, min(COLS_MAX, per_row))
    self_id = str(ctx.self_id or "")

    rows, ready, total = _menu_items(only_ready, self_id)
    if not rows:
        return ("这个机器人当前没有任何可用命令。"
                "到管理页「绝区零 → 机器人命令」给它打开几条（总开关没开时一条都不会有）")

    # 每次都记一行：出问题时「协议判成了什么 / 事件是哪个类 / 机器人是哪个类」
    # 是唯一能一眼定性的信息（用户那头只看得到菜单文字）。别删。
    logger.info(
        f"命令菜单：protocol={ctx.protocol} "
        f"bot={type(ctx.bot).__name__ if ctx.bot is not None else None} "
        f"event={type(ctx.event).__name__ if ctx.event is not None else None}"
    )

    # 按钮布局：默认值写死在代码里（_DEFAULT_BUTTONS），详细设置里可随时改；
    # 老配置没这个键 → ctx.opt 落到 default，不用迁移。
    btn_rows, unknown = _parse_buttons(
        ctx.opt("buttons", _DEFAULT_BUTTONS), rows, per_row)
    # 按钮上方那行字：官方**不允许正文为空**（实测报 40034030 消息content字段不能为空），
    # 所以固定给一句短的；留空才退到零宽空格（不可见，用来试「纯按钮」）。
    kb_text = str(ctx.opt("kb_text", _DEFAULT_KB_TEXT) or "").strip() or ZWSP
    text = _plain_text(rows)
    vars_ = {"title": _TITLE, "count": ready, "total": total, "items": rows}
    data = {
        "self_id": self_id, "protocol": ctx.protocol, "only_ready": only_ready,
        "per_row": per_row, "count": ready, "total": total, "items": rows,
        "buttons": btn_rows, "kb_text": kb_text,
    }

    # 「绝区零菜单 诊断」：不返回菜单，改为把按钮每一步的判定结果回报出来。
    if str(ctx.arg or "").strip().lower() in _DIAG_WORDS:
        data["diagnose"] = True
        return {"text": await _diagnose(ctx, btn_rows, unknown, kb_text),
                "vars": vars_, "data": data}

    if ctx.protocol == "qq":
        sent, why = await send_buttons(ctx, kb_text, btn_rows)
        if sent:
            # 已经自己发完了 → 返回 silent，让分发器别再发一遍纯文字。
            return {"silent": True, "vars": vars_, "data": data}
        if why:
            # 按钮没发出去：把原因跟上，别让用户以为「本来就没有按钮」。
            # 同时写进 vars / data：配了模板时正文会被模板覆盖，原因就只能靠
            # 模板里写 {button_error} 来显示（data 那份是给管理页预览看的）。
            data["button_error"] = why
            vars_["button_error"] = why
            text = f"{text}\n{why}"
    return {"text": text, "vars": vars_, "data": data}
