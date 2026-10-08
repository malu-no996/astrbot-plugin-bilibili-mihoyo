"""社交命令 · 机制层：接口注册表 + 回复模板 + 公共常量。

social.py 拆出来的第一块（原文件 1400+ 行，按功能拆成十几块）。这里只放**机制**，
具体接口在 `account.py`（米游社账号）/ `record.py`（绝区零战绩）/ `gacha.py` /
`codex.py` / `sign.py` / `autosign.py` / `help.py`：

  · Ctx           接口能看到的上下文（谁发的、哪个机器人、参数、这条命令的配置）
  · INTERFACES    接口注册表，`@interface` 装饰器往里塞
  · 回复模板      `{变量}` / `{#items}…{/items}` 的极简渲染，两个协议各一套
  · run_interface 执行接口并把返回值收敛成「要发出去的文本」

`PREFIX / MODULE / driver / app` 从 `core/web.py` 取（全插件共用一份，别在这里重定义），
本模块仍然是最底层（不依赖任何接口实现模块），所以转出这些名字不会形成循环导入。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


# ================= 接口注册表 =================
#
# 「纯后台接口」都写在这里，页面上的下拉框就是按注册顺序列的。


@dataclass
class Ctx:
    """接口的调用上下文：只给接口看得见「这次是谁、在哪个机器人上、带了什么参数」。"""

    event: Any = None                 # NoneBot Event（可能是群消息 / 私聊）
    arg: str = ""                     # 命令之后的纯文本参数（如「绝区零危局 123456」的 123456）
    self_id: str = ""                 # 触发这次调用的机器人 self_id
    cmd: dict = field(default_factory=dict)   # 命中那条配置（便于接口读自定义字段）
    bot: Any = None                   # 机器人实例：接口要主动发消息（如发二维码图）时用
    user_id: str = ""                 # 发命令的人（OneBot=QQ号 / QQ官方=openid），查绑定用
    # 协议：「onebot」= OneBot v11（第三方）/「qq」= QQ 官方机器人。选回复模板按它分叉。
    protocol: str = "onebot"
    # 接口填给模板用的变量（见 render_template）。接口返回 dict 里的 vars 会自动并进来。
    vars: dict = field(default_factory=dict)
    # 接口返回 dict 里的 data（原始数据）：只在管理页「预览」时展示成 JSON，
    # 让人知道接口到底给了什么、模板能拿哪些字段。真实命令执行时不用它。
    data: Any = None

    def opt(self, key: str, default: Any = None) -> Any:
        """读这条命令「详细设置」里配的接口选项（没配过就给默认值）。"""
        opts = (self.cmd or {}).get("options")
        if not isinstance(opts, dict) or key not in opts:
            return default
        return opts[key]


INTERFACES: dict[str, dict] = {}


def interface(key: str, label: str, desc: str = "", options: list | None = None,
              tpl_vars: list | None = None, sample: str = ""):
    """把一个 async 函数登记成「可配置的接口」。

    用法：

        @interface("zzz_deadly", "绝区零危局", "危局强袭战本期成绩")
        async def _api_zzz_deadly(ctx: Ctx) -> str:
            ...

    后三个可选参数是给「详细设置」弹窗用的（页面按 schema 渲染表单，**加设置项不用改前端**）：

      options   接口专属设置项，形如
                [{"key": "mode", "label": "查询方式", "type": "select",
                  "options": [{"value": "name", "label": "按名字"}], "default": "name"}]
                type 支持 select / text / number（页面按类型出控件，后端按它校验 + 补默认）。
      tpl_vars  模板变量说明 [{"name": "title", "desc": "标题"}]，只给页面上当提示。
      sample    示例模板，页面「填入示例」按钮直接写进编辑框。
    """
    def deco(fn):
        INTERFACES[key] = {
            "key": key, "label": label, "desc": desc, "handler": fn,
            "options": options or [], "tpl_vars": tpl_vars or [], "sample": sample,
        }
        return fn
    return deco



def _bot_protocol(bot: Any, event: Any = None) -> str:
    """机器人协议：「onebot」= OneBot v11（第三方）/「qq」= QQ 官方机器人。

    用来挑回复模板、并决定要不要尝试 QQ 官方专属能力（如命令菜单的按钮）。

    ⚠️ **AstrBot 版看的是「平台适配器类型名」**：`event.get_platform_name()`，
    官方机器人是 `qq_official`（含 webhook 变体），NapCat 等是 `aiocqhttp`。

    原版（nonebot）按 `type(obj).__module__.startswith("nonebot.adapters.qq")` 判断，
    移植到 AstrBot 后**恒不成立**（事件类在 `astrbot.core.platform.sources.qqofficial`）→
    永远返回 "onebot" → 官方机器人连按钮都不会尝试，静默退回纯文字菜单。
    （正式分发用的是 `dispatch._protocol()`，同样按平台名判断，两处口径要一致。）

    `bot` 参数留着只为兼容原签名（AstrBot 里平台名从事件上取最可靠）。
    """
    del bot
    try:
        name = str(event.get_platform_name() or "") if event is not None else ""
    except Exception:  # noqa: BLE001
        name = ""
    return "qq" if name.strip().lower().startswith("qq_official") else "onebot"



# ---------------- 回复模板 ----------------
#
# 模板语法（刻意做得极简，够用就行）：
#   {name}                 取一个变量（支持 {detail.name} 这种点路径）
#   {#items} … {/items}    循环块：vars["items"] 是列表时逐条渲染中间那段，
#                          块内既能写 {name} 也能写 {index}（从 1 开始）
#                          变量是 dict 时当「一条」渲染（详情块就是这么用的）
#   取不到 / 值为空 → 替换成空串（绝不把 None、{} 之类漏进消息里）

_TPL_BLOCK = re.compile(r"\{#(\w+)\}([\s\S]*?)\{/\1\}")
_TPL_VAR = re.compile(r"\{([\w.]+)\}")


def _tpl_lookup(scope: Any, key: str) -> str:
    """按点路径取值（{a.b.c}）；任何一环取不到都返回空串。"""
    cur = scope
    for part in key.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return ""
    if cur is None or isinstance(cur, (dict, list)):
        return ""
    return str(cur)


def _tpl_fill(tpl: str, scope: Any) -> str:
    """把一段模板里的 {x} 全部替换掉（无论变量是否存在 —— 不存在的必须清成空串，
    否则残留的 {x} 会在外层再被替换一次，拿错作用域的值）。"""
    return _TPL_VAR.sub(lambda m: _tpl_lookup(scope, m.group(1)), tpl)


def render_template(tpl: str, vars: Any) -> str:
    """按 vars 渲染模板，返回最终文本（渲染结果为空串也算空，调用方会退回默认文本）。"""
    if not tpl:
        return ""
    vars = vars if isinstance(vars, dict) else {}

    def one_block(m: re.Match) -> str:
        rows = vars.get(m.group(1))
        if isinstance(rows, dict):
            rows = [rows]                                # 详情块：单个对象当一条渲染
        if not isinstance(rows, list):
            return ""
        out = []
        for i, row in enumerate(rows, 1):
            scope = dict(row) if isinstance(row, dict) else {"value": row}
            scope.setdefault("index", i)
            out.append(_tpl_fill(m.group(2), scope))
        return "".join(out)

    return _tpl_fill(_TPL_BLOCK.sub(one_block, str(tpl)), vars).strip()


def apply_tpl(cmd: dict, protocol: str, vars: Any, fallback: str) -> str:
    """命令配了回复模板就用模板，否则用接口自己给的文本。

    OneBot（第三方）与 QQ 官方各一套：QQ 官方有些富文本/长度限制不一样，
    分开配才不用互相迁就。

    ⚠️ **QQ 那套留空时借用 OneBot 那套**（用户要求）：两边的正文往往一模一样，
    只在 OneBot 里配一次就够了，不必抄两份。反过来 OneBot 留空时**不**借用 QQ 的
    —— OneBot 模板可能含 QQ 专属写法（按钮/换行），拿过来会出错。
    都没配（或渲染出来是空的）→ 用接口默认文本，不会发出空消息。
    """
    tpls = cmd.get("tpl") if isinstance(cmd, dict) else None
    if not isinstance(tpls, dict):
        return fallback
    order = ("qq", "onebot") if protocol == "qq" else ("onebot",)
    for key in order:
        tpl = str(tpls.get(key) or "")
        if tpl.strip():
            return render_template(tpl, vars) or fallback
    return fallback


def _render(out: Any) -> str:
    """**统一的结果处理**：接口返回什么形态，都由这里收敛成一段要发出去的文本。

    接口约定返回值可以是：
      - str           → 原样发送
      - dict          → 取 text 作为文本，vars 作为模板变量（由 run_interface 并进 ctx.vars）
      - None / 空串   → 兜底文案（避免「机器人抽风式地什么都不回」）
    """
    if out is None:
        return "（没有可返回的内容）"
    if isinstance(out, str):
        return out.strip() or "（没有可返回的内容）"
    if isinstance(out, dict):
        text = str(out.get("text") or "").strip()
        return text or "（没有可返回的内容）"
    return str(out)


async def run_interface(api_key: str, ctx: Ctx) -> str:
    """执行某个接口并返回「处理后的结果文本」。接口不存在 / 抛错都在这里收敛。

    接口返回 dict 时，里面的 vars 会并进 ctx.vars —— 那是给回复模板用的变量，
    返回文本只负责「没配模板时的默认输出」。
    另外 dict 里的 data 会存到 ctx.data：管理页「预览」会把它和 vars 一起
    以 JSON 展示出来（配模板时能看清接口到底提供了哪些字段）。
    特殊约定：dict 带 `"silent": True` 表示**接口已自行发完消息**（如抽卡总结图），
    这里返回空串，发送端据此跳过后续的文字发送。
    """
    item = INTERFACES.get(str(api_key or ""))
    if not item:
        return f"接口不存在：{api_key}（请在「接口配置」里重新选择）"
    try:
        out = await item["handler"](ctx)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"miyoushe 社交命令执行失败：{api_key}")
        return f"执行失败：{type(exc).__name__}: {exc}"
    if isinstance(out, dict) and isinstance(out.get("vars"), dict):
        ctx.vars.update(out["vars"])
    if isinstance(out, dict) and out.get("data") is not None:
        ctx.data = out["data"]
    if isinstance(out, dict) and out.get("silent"):
        return ""
    return _render(out)
