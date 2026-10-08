"""社交命令 · 配置读写（data/zzz/social.json）。

从 social.py 拆出来的第四块：命令表 / 机器人开关的清洗、落盘与读取。

配置属于**用户数据**，放项目根 data/ 下而不是插件里（插件可被市场卸载重装，
用户配置不能跟着走）。写法与 record_store 一致：tmp + os.replace 原子替换。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from .core import INTERFACES


from ..paths import zzz_path

_CFG_FILE = zzz_path("social.json")



# ================= 配置读写 =================

# ================= 指令组（组名 + 子命令 两段式） =================
#
# 2026-10-08 起命令改成「指令组 + 子命令」两段式（用户要求）：
#     mhy login          米游社登录 / 扫码登录 / 米游社扫码登录
#     zzz deadly         绝区零危局 / 危局强袭战 / zzz危局
# 命令词用**英文**（组名 mhy = 米游社、zzz = 绝区零），**别名照旧全中文**；
# 也兼容「不写组名、直接发命令词或别名」的老习惯（见 dispatch.match_command）。
GROUP_MHY = "mhy"
GROUP_ZZZ = "zzz"
# 面板「指令组」下拉里的候选。后端**不限制**组名（写别的照样能用），这里只是候选。
GROUPS: list[str] = [GROUP_MHY, GROUP_ZZZ]

# ⚠️ id 是「内置命令」的身份，别随便改：_normalize 靠它区分
#   「用户自己删掉的」和「后来新增的内置命令」（见 _ensure_new_defaults）。
DEFAULT_COMMANDS: list[dict] = [
    {
        "id": "mys_login", "group": GROUP_MHY, "cmd": "login",
        "aliases": ["米游社登录", "扫码登录", "米游社扫码登录"], "api": "mys_login",
        "enabled": True, "admin_only": False,
    },
    {
        "id": "mys_binds", "group": GROUP_MHY, "cmd": "account",
        "aliases": ["米游社账号", "我的米游社"], "api": "mys_binds",
        "enabled": True, "admin_only": False,
    },
    {
        # 米游社切换：不带参数列账号（文字版「账号列表」+「1、昵称：账号ID」一行一个）；
        # **QQ 官方**改为把这些账号做成按钮（文案=昵称，点下去发「mhy switch 账号ID」），
        # 每行 3 个、最多 9 个（官方硬上限是每行 5 个 / 最多 25 个）。
        "id": "mys_switch", "group": GROUP_MHY, "cmd": "switch",
        "aliases": ["米游社切换", "切换米游社"], "api": "mys_switch",
        "enabled": True, "admin_only": False,
        "options": {"kb_text": "账号列表", "per_row": 3, "max_buttons": 9},
    },
    {
        "id": "mys_unbind", "group": GROUP_MHY, "cmd": "unbind",
        "aliases": ["米游社解绑", "解绑米游社"], "api": "mys_unbind",
        "enabled": True, "admin_only": False,
    },
    {
        # 一个米游社账号可能绑了多个绝区零角色：不带参数列出来（默认那个打勾），
        # **QQ 官方**改成把这些角色做成按钮（文案=角色名，点下去发「mhy role UID」），
        # 每行 3 个、最多 9 个；命令后加 UID 就切换默认查哪个 —— 结果只回一句
        # 「【角色名】切换成功 / 失败」。危局 / 防卫战 / 抽卡 都跟着这个默认走。
        "id": "zzz_role", "group": GROUP_MHY, "cmd": "role",
        "aliases": ["切换角色", "绝区零角色", "绝区零切换角色", "zzz角色"], "api": "zzz_role",
        "enabled": True, "admin_only": False,
        "options": {"kb_text": "角色列表", "per_row": 3, "max_buttons": 9},
    },
    {
        "id": "zzz_deadly", "group": GROUP_ZZZ, "cmd": "deadly",
        "aliases": ["绝区零危局", "危局强袭战", "zzz危局"], "api": "zzz_deadly",
        "enabled": True, "admin_only": False,
    },
    {
        "id": "zzz_shiyu", "group": GROUP_ZZZ, "cmd": "shiyu",
        "aliases": ["绝区零防卫战", "式舆防卫战", "zzz防卫战"], "api": "zzz_shiyu",
        "enabled": True, "admin_only": False,
    },
    {
        # 群排行三件套：范围限定在**当前 Q 群**里绑定了账号的人，成绩**只读本地存档**
        # （用户要求：有查过记录的才上榜，不实时拉取）。危局 = 普通总分，
        # 绝境 = 绝境难度得分，防卫战 = 防卫战总分。
        "id": "zzz_deadly_rank", "group": GROUP_ZZZ, "cmd": "deadly-rank",
        "aliases": ["危局群排行", "危局排行", "zzz危局排行"], "api": "zzz_deadly_rank",
        "enabled": True, "admin_only": False, "options": {"limit": 10},
    },
    {
        # 绝境群排行多一个「输出方式」：**默认图片版**（2026-10-09 起，有图就不发文字；
        # 榜单图 = 排名 / 分数 / 队伍头像（右上角影画数）/ 角色名）。
        # 选项默认值由接口 schema 决定（这里写出来只是让默认值一目了然）。
        "id": "zzz_hard_rank", "group": GROUP_ZZZ, "cmd": "hard-rank",
        "aliases": ["绝境群排行", "绝境排行", "危局绝境群排行", "zzz绝境排行"], "api": "zzz_hard_rank",
        "enabled": True, "admin_only": False, "options": {"limit": 10, "output": "image"},
    },
    {
        "id": "zzz_shiyu_rank", "group": GROUP_ZZZ, "cmd": "shiyu-rank",
        "aliases": ["防卫战群排行", "防卫战排行", "zzz防卫战排行"], "api": "zzz_shiyu_rank",
        "enabled": True, "admin_only": False, "options": {"limit": 10},
    },
    {
        # 探索类三件套：常驻玩法的进度（没有「本期/上期」的概念）。
        "id": "zzz_abyss", "group": GROUP_ZZZ, "cmd": "abyss",
        "aliases": ["绝区零空洞", "零号空洞", "zzz空洞"], "api": "zzz_abyss",
        "enabled": True, "admin_only": False,
    },
    {
        "id": "zzz_zenkov", "group": GROUP_ZZZ, "cmd": "zenkov",
        "aliases": ["绝区零诡域", "迷宫诡域", "zzz诡域"], "api": "zzz_zenkov",
        "enabled": True, "admin_only": False,
    },
    {
        "id": "zzz_void", "group": GROUP_ZZZ, "cmd": "void",
        "aliases": ["绝区零推演", "临界推演", "zzz临界"], "api": "zzz_void",
        "enabled": True, "admin_only": False,
    },
    {
        # 实时便笺：电量 / 活跃度 / 刮刮乐 / 录像店 / 委托 / 周常。
        "id": "zzz_note", "group": GROUP_ZZZ, "cmd": "note",
        "aliases": ["实时便笺", "体力", "zzz体力", "绝区零便笺"], "api": "zzz_note",
        "enabled": True, "admin_only": False,
    },
    {
        # 玩家概览：活跃天数 / 角色数 / 邦布数 / 层数（网页「我的绝区零」那一屏）。
        "id": "zzz_profile", "group": GROUP_ZZZ, "cmd": "profile",
        "aliases": ["绝区零档案", "玩家概览", "绝区零查询", "zzz查询"], "api": "zzz_profile",
        "enabled": True, "admin_only": False,
    },
    {
        # 代理人面板：命令后必须带角色名（「zzz card 雅」），默认出图。
        # （原名叫「角色面板」，那套版面是搬 ZZZeroUID 的、显示效果不好，
        #   2026-10-05 换成网页版「代理人详细」浮层的版面；命令词随菜单一起改叫
        #   「代理人面板」——「角色面板 / 代理人详细」都留作别名，老习惯照样能用。）
        "id": "zzz_card", "group": GROUP_ZZZ, "cmd": "card",
        "aliases": ["代理人面板", "角色面板", "代理人详细", "绝区零面板", "zzz面板", "面板"],
        "api": "zzz_card",
        "enabled": True, "admin_only": False,
        "options": {"output": "image"},
    },
    {
        # 绳网月报：本月资源收入构成；命令后加月份查历史（「zzz month 202610」「zzz month 上月」）。
        "id": "zzz_month", "group": GROUP_ZZZ, "cmd": "month",
        "aliases": ["绳网月报", "月历", "札记", "zzz月报"], "api": "zzz_month",
        "enabled": True, "admin_only": False,
    },
    {
        "id": "zzz_gacha", "group": GROUP_ZZZ, "cmd": "gacha",
        "aliases": ["绝区零抽卡", "zzz抽卡", "绝区零调频"], "api": "zzz_gacha",
        "enabled": True, "admin_only": False,
    },
    {
        "id": "zzz_codex", "group": GROUP_ZZZ, "cmd": "codex",
        "aliases": ["绝区零图鉴", "zzz图鉴", "绝区零资料"], "api": "zzz_codex",
        "enabled": True, "admin_only": False,
        # 默认「按最新」：**不带参数**发一次就能拿到最新的几位代理人（列表默认 6 条，超出不显示）。
        # 带参数时命令行自动识别、不用动这里的设置：
        #   数字 = 取第几条（按上面 mode）、「火」= 属性列表、「异常 2」= 职业列表第 2 条、
        #   其它 = 按名字搜。详见 social_codex._api_zzz_codex。
        "options": {"category": "agents", "mode": "latest", "limit": 6},
    },
    {
        # 签到：直接发「zzz sign」签**默认账号**；「zzz sign all」签**全部**绑定账号。
        "id": "zzz_sign", "group": GROUP_ZZZ, "cmd": "sign",
        "aliases": ["签到", "每日签到", "绝区零签到", "zzz签到"], "api": "zzz_sign",
        "enabled": True, "admin_only": False,
    },
    {
        # 一键签到：签的是**页面上配好的那份目标清单**（「签到」页 → 自动签到，
        # 可横跨多个米游社账号），不是发命令的人自己绑的号 —— 两者别搞混。
        # 谁能触发由「详细设置 → 允许触发的人」决定（留空 = 不限制）。
        "id": "zzz_sign_all", "group": GROUP_ZZZ, "cmd": "sign-all",
        "aliases": ["一键签到", "绝区零一键签到", "自动签到"], "api": "zzz_sign_all",
        "enabled": True, "admin_only": False,
        "options": {"allow_users": ""},
    },
    {
        # 命令菜单（帮助）：OneBot 发纯文字命令列表；QQ 官方**只发按钮不带文字**，
        # 按钮的显示文案与分行布局默认写在代码里（social_help._DEFAULT_BUTTONS），
        # 详细设置里可改。点一下 = 发那条命令。放在最后，菜单里也排在最末。
        "id": "zzz_help", "group": GROUP_ZZZ, "cmd": "help",
        "aliases": ["绝区零帮助", "zzz帮助", "绝区零菜单", "zzz菜单"], "api": "zzz_help",
        "enabled": True, "admin_only": False,
        # 只列本机器人已开启的命令；按钮每行 3 个仅是「布局留空时」的自动兜底。
        "options": {"only_ready": True, "per_row": 3},
    },
]

# 老配置迁移表：id → (组名, 英文命令词)。
# 磁盘上那些「还没有 group 字段」的配置（= 2026-10-08 之前存的，命令词还是中文）
# 在这里一次性迁到新形态；**原来的中文命令词会降级成别名**，老习惯照旧能用。
_GROUP_MIGRATE: dict[str, tuple[str, str]] = {
    str(d["id"]): (str(d.get("group") or ""), str(d.get("cmd") or ""))
    for d in DEFAULT_COMMANDS
}

_lock = threading.RLock()
_cfg: dict | None = None


def _clean_options(api: str, raw: Any) -> dict:
    """按接口的 schema 清洗「详细设置」里的选项：只认声明过的键，并按 type 纠正类型。

    为什么要按 schema 过一遍而不是原样存：
      · 用户在页面上改了「绑定的接口」→ 旧的选项键残留，接口读到不认识的值会出怪结果；
      · 后来给接口**新增**了设置项 → 老配置里没有这个键，接口读到 None。
    两边都靠这里「按当前 schema 重新生成一份」解决：缺的补 default，多的丢掉。
    """
    schema = (INTERFACES.get(str(api or "")) or {}).get("options") or []
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for f in schema:
        if not isinstance(f, dict):
            continue
        key = str(f.get("key") or "")
        if not key:
            continue
        val = raw.get(key)
        kind = str(f.get("type") or "text")
        if kind == "number":
            try:
                val = int(val)
            except (TypeError, ValueError):
                val = int(f.get("default") or 0)
            lo, hi = f.get("min"), f.get("max")
            if isinstance(lo, int):
                val = max(lo, val)
            if isinstance(hi, int):
                val = min(hi, val)
        elif kind == "switch":
            val = bool(val) if isinstance(val, bool) else bool(f.get("default"))
        elif kind == "select":
            allowed = {str(o.get("value")) for o in (f.get("options") or [])}
            val = str(val if val is not None else f.get("default") or "")
            if allowed and val not in allowed:
                val = str(f.get("default") or (sorted(allowed)[0] if allowed else ""))
        else:
            val = str(val if val is not None else f.get("default") or "")
        out[key] = val
    return out


def _clean_tpl(raw: Any) -> dict:
    """回复模板：{"onebot": "...", "qq": "..."}，只留字符串，空串也留着（= 用默认文本）。"""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "onebot": str(raw.get("onebot") or ""),
        "qq": str(raw.get("qq") or ""),
    }


def _clean_commands(raw: Any) -> list[dict]:
    out: list[dict] = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        gid = str(c.get("id") or uuid.uuid4().hex[:8])
        group = str(c.get("group") or "").strip().lower()
        cmd = str(c.get("cmd") or "").strip()
        if not cmd:
            continue                                  # 没有命令名的行直接丢掉
        raw_aliases = [str(a or "").strip() for a in (c.get("aliases") or [])]
        want = _GROUP_MIGRATE.get(gid)
        if want and not group:
            # 老配置迁移（2026-10-08 之前的存档）：补上指令组 + 英文命令词，
            # 原来的中文命令词降级成别名 —— 什么都不丢，老习惯照旧能发。
            group = want[0]
            if cmd != want[1]:
                raw_aliases.insert(0, cmd)
            cmd = want[1]
        aliases = []
        for a in raw_aliases:
            a = str(a or "").strip()
            if a and a != cmd and a not in aliases:
                aliases.append(a)
        api = str(c.get("api") or "")
        out.append(
            {
                "id": gid,
                "group": group,                           # 指令组（mhy / zzz；空 = 不带组名）
                "cmd": cmd,                               # 子命令词（英文）
                "aliases": aliases,                        # 别名（照旧支持中文）
                "api": api,
                "enabled": bool(c.get("enabled", True)),
                "admin_only": bool(c.get("admin_only", False)),
                "options": _clean_options(api, c.get("options")),   # 接口专属「详细设置」
                "tpl": _clean_tpl(c.get("tpl")),                    # 两套回复模板
            }
        )
    return out


def _clean_switches(raw: Any) -> dict:
    """机器人总开关：{self_id: bool}，只留明确写过的。"""
    if not isinstance(raw, dict):
        return {}
    return {str(k): bool(v) for k, v in raw.items()}


def _clean_bot_cmds(raw: Any) -> dict:
    """机器人 × 命令 的逐命令开关：{self_id: {cmd_id: bool}}。"""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for sid, m in raw.items():
        if not isinstance(m, dict):
            continue
        inner = {str(k): bool(v) for k, v in m.items()}
        if inner:
            out[str(sid)] = inner
    return out


def _ensure_new_defaults(commands: list[dict], known: set[str]) -> None:
    """新增的内置命令，要能让**已经存在的**配置也看到。

    难点：磁盘上「这条 id 不在 commands 里」有两种成因，必须区分开 ——
      · 用户自己在页面上删掉的 → **不能**给人家补回来
      · 我后来新增的内置命令 → 应该补进来（否则老配置永远看不到新命令）

    所以额外维护一份 `known`（出现过的内置命令 id）：
      id 在 known 里但不在 commands 里  → 用户删的，不补
      id 不在 known 里                  → 新增内置，补进来并记入 known
    首次运行（没有配置文件）时 known 为空 → 全部预置命令都补上，等价于默认值。
    """
    for d in DEFAULT_COMMANDS:
        if str(d["id"]) not in known:
            commands.append(dict(d))
            known.add(str(d["id"]))


def _normalize(raw: Any) -> dict:
    """把磁盘上的（可能是旧版 / 手改坏的）结构整成完整可用的配置。"""
    raw = raw if isinstance(raw, dict) else {}
    # 注意用 `in` 判断：用户把命令全删光时 commands 是**空数组**，
    # 那是合法状态，不能因为「空」就回退成默认几条（否则删不掉）。
    commands = _clean_commands(raw.get("commands")) if "commands" in raw else []
    known = {str(x) for x in (raw.get("known") or []) if str(x)}
    known |= {str(c.get("id")) for c in commands}     # 当前已有的都算「见过」
    _ensure_new_defaults(commands, known)
    return {
        "version": 1,
        "commands": commands,
        "bots": _clean_switches(raw.get("bots")),
        "bot_cmds": _clean_bot_cmds(raw.get("bot_cmds")),
        "known": sorted(known),
    }


def _load_from_disk() -> dict:
    try:
        return _normalize(json.loads(_CFG_FILE.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return _normalize({})                        # 首次运行 → 带上 4 条预置命令
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"miyoushe 社交命令配置读取失败，改用默认值：{exc}")
        return _normalize({})


def load_cfg() -> dict:
    """读配置（内存缓存；首次读盘）。分发器热路径，不要每来一条消息就读盘。"""
    global _cfg
    with _lock:
        if _cfg is None:
            _cfg = _load_from_disk()
        return _cfg


def _write(cfg: dict) -> None:
    """原子落盘（tmp + os.replace）：写坏一半不会毁掉旧配置。"""
    path = _CFG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def save_cfg(commands: Any, bots: Any, bot_cmds: Any) -> dict:
    """保存配置（整份覆盖），并立即刷新内存缓存 —— 改完即时生效、不用重启。"""
    global _cfg
    with _lock:
        if _cfg is None:                                 # 没读过盘就先读，别丢掉 known
            _cfg = _load_from_disk()
        # known 必须带过去：这次被删掉的命令 id 要留在 known 里，
        # 否则下次读盘会被当成「新增内置命令」再补回来（= 用户删不掉）。
        prev = _cfg
        known = {str(x) for x in (prev.get("known") or [])}
        known |= {str(c.get("id")) for c in (prev.get("commands") or [])}
        cmds = _clean_commands(commands)
        known |= {str(c.get("id")) for c in cmds}
        cfg = {
            "version": 1,
            "commands": cmds,
            "bots": _clean_switches(bots),
            "bot_cmds": _clean_bot_cmds(bot_cmds),
            "known": sorted(known),
        }
        try:
            _write(cfg)
        except OSError as exc:
            logger.warning(f"miyoushe 社交命令配置保存失败：{exc}")
            raise
        _cfg = cfg
    return cfg
