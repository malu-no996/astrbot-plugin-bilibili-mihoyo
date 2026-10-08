"""抓取并**持久化**绝区零图鉴数据（写入 plugins/_vendor/miyoushe/data/zzz/ 下的分段文件）。

为什么要单独一个模块
--------------------
图鉴是静态资料（代理人 / 音擎 / 驱动盘 / 邦布），不该每次打开页面都去外部站点拉一遍。
所以这里的产物是「一次性抓取 → 落盘」，之后前台只读本地快照：

  ① 资料文本：参考项目 ZZZeroUID 的 map 表（中文名 / 稀有度 / 元素 / 职业 / 套装效果 / 音擎效果）
       - PartnerId2Data_<ver>.json       代理人（level 突破档位与材料、extra_level 核心技加成）
       - PartnerId2SkillParam_<ver>.json 代理人技能倍率（{技能: {参数: [Main, Growth]}}，
                                         倍率 = (Main + Growth × 技能等级) / 10000，前台换算）
       - WeaponId2Data_<ver>.json        音擎（含 1~5 阶音擎效果）
       - EquipId2Data_<ver>.json         驱动盘套装（2 件套 / 4 件套效果）
     目录可用环境变量 ZZZ_REF_DIR 覆盖（默认见 REF_DIR），版本号自动取目录里最新的一版。
  ② 邦布：官方 /buddy/info（需要已登录的米游社账号，读 data/miyoushe_accounts.json 里的 cookie）。
  ③ **图标**：全部下载到本机 plugins/_vendor/miyoushe/data/zzz/assets/（按 URL 哈希去重，只下一次），
     json 里存的是「本地路由」/miyoho-asset/<name>
     —— 之后打开图鉴不再访问任何外部地址，也不再受第三方镜像可用性影响。

触发方式：
  - 后台接口 POST /admin/api/miyoushe/zzz/codex/refresh（前端「更新数据」按钮）
  - 命令行：.venv/Scripts/python.exe tools/gen_zzz_codex.py

注意：本模块**不导入本插件的包**（`from . import …` 那种；NoneBot 未初始化时导入整包会抛
ValueError: NoneBot has not been initialized），也不做任何包内相对导入，
所以它在后台和命令行两种环境都能跑，且插件被放在 plugins/<名>/ 还是
plugins/_vendor/<名>/ 都无所谓。
图标命名规则与 asset_cache._name 保持一致（md5(url) + 扩展名），共用同一目录。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import random
import string
import time
import uuid

import httpx
from loguru import logger

REF_DIR = pathlib.Path(
    os.environ.get("ZZZ_REF_DIR", "G:/project/zzzuid/ZZZeroUID/ZZZeroUID/utils/map")
)
# 本文件在 src/zzz/codex/ 下 → parents[3] 才是插件根（被 tools/gen_zzz_codex.py 用
# importlib 单独加载时这个相对定位依然成立，因为它不依赖包上下文）。
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]
DATA_DIR = PLUGIN_ROOT / "data" / "zzz"
# 四类各一个文件（不再混在一个 codex_data.json 里）。
SECTION_FILES = {name: DATA_DIR / f"{name}.json" for name in ("agents", "wengines", "discs", "bangboo")}
META_FILE = DATA_DIR / "meta.json"

# 图标静态镜像（nanoka 的数据站），webp 体积小。音擎 / 驱动盘 / 邦布用它。
ICON_BASE = "https://static.nanoka.cc/assets/zzz"

# 代理人圆形头像改用 Enka 的官方资源镜像（png）。为什么不用 nanoka：
# nanoka 的 IconRoleCircle 是**滞后的**，新角色会拿到上一个人的图（实测 68 号
# 洛克茜拿到的内容和 67 号蕾米埃尔一字不差），Enka 则是每期及时更新。
# 编号规则两边一致（sprite_id → 两位补零），Enka 拉不到时回退 nanoka（见 _icon_alt）。
ICON_ENKA = "https://enka.network/ui/zzz"

# 与 asset_cache 共用的本地图片目录 / 路由前缀。
# 目录在**插件内部**（plugins/_vendor/miyoushe/data/zzz/assets/）而非项目根 data/：
# 这份缓存随功能入库（克隆即用），放这儿就不必在根 .gitignore 里为 data/ 下的子目录开洞。
# 用 __file__ 定位：后台（NoneBot 已启动）与命令行（tools/gen_zzz_codex.py）两种环境的 CWD
# 可能不同，写死相对路径会找不到目录。
ASSET_DIR = pathlib.Path(__file__).resolve().parents[3] / "data" / "zzz" / "assets"
ASSET_ROUTE = "/miyoho-asset/"
ASSET_DIR.mkdir(parents=True, exist_ok=True)

ELEM = {200: "物理", 201: "火", 202: "冰", 203: "电", 204: "风", 205: "以太"}
PROF = {1: "强攻", 2: "击破", 3: "异常", 4: "支援", 5: "防护", 6: "命破", 7: "锋御"}


def _id_num(it: dict) -> int:
    """条目 id 里的数字部分（图鉴 id 一律纯数字；万一有前缀也只取数字）。"""
    s = "".join(ch for ch in str(it.get("id") or "") if ch.isdigit())
    return int(s) if s else 0


def _sort_newest(items: list[dict], rarity_first: bool = True) -> list[dict]:
    """图鉴统一排序：**稀有度 S 在前，同稀有度内新的在前**。

    图鉴 id 是随时间递增的（角色 1021 猫又 → 1641 菲欧妮），所以「新在前」= id 数值降序。
    注意这里**不是** id 升序 —— 升序会把 1.0 的老角色排在最前面。
    """
    if rarity_first:
        items.sort(key=lambda x: (x.get("rarity") != "S", -_id_num(x)))
    else:
        items.sort(key=lambda x: -_id_num(x))
    return items

UA = (
    "Mozilla/5.0 (Linux; Android 12; MI 6 Build/REL; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/111.0.5563.116 Mobile Safari/537.36 miHoYoBBS/2.73.1"
)

_CONCURRENCY = 8          # 图标并发下载数（对镜像友好一点）
_TIMEOUT_S = 25           # 单张图标下载超时（秒）
_CONNECT_S = 10           # 连接超时（秒）
_TIMEOUT = httpx.Timeout(_TIMEOUT_S, connect=_CONNECT_S)


# ---------------- 参考项目文本资料 ----------------

def ref_version() -> str:
    """取参考项目里最新的一版数据（PartnerId2Data_<ver>.json）。"""
    env = os.environ.get("ZZZ_MAP_VER")
    if env:
        return env
    if not REF_DIR.exists():
        raise RuntimeError(f"参考项目数据目录不存在：{REF_DIR}（可用环境变量 ZZZ_REF_DIR 指定）")


    def _key(name: str) -> tuple:
        raw = name.replace("PartnerId2Data_", "").replace(".json", "")
        return tuple(int(p) if p.isdigit() else 0 for p in raw.split("."))

    cands = sorted((p.name for p in REF_DIR.glob("PartnerId2Data_*.json")), key=_key)
    if not cands:
        raise RuntimeError(f"参考项目数据目录里没有 PartnerId2Data_*.json：{REF_DIR}")
    return cands[-1].replace("PartnerId2Data_", "").replace(".json", "")


def _load(name: str, ver: str):
    p = REF_DIR / f"{name}_{ver}.json"
    if not p.exists():
        raise RuntimeError(f"缺少参考数据文件 {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _sprite_icon(sprite) -> str:
    """代理人圆形头像（sprite_id → IconRoleCircleNN.png，走 Enka）。"""
    try:
        n = int(str(sprite).lstrip("0") or "0")
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return ""
    return f"{ICON_ENKA}/IconRoleCircle{n:02d}.png"


def _icon_alt(url: str) -> str:
    """头像的备用地址：Enka 没有的（如测试服角色）回退 nanoka 的 webp 镜像。"""
    if url.startswith(ICON_ENKA):
        return url.replace(ICON_ENKA, ICON_BASE).replace(".png", ".webp")
    return ""


def _num(x) -> float | int:
    """参考项目里数值有 str/int/float 三种写法，统一成数字；取不到给 0。"""
    try:
        n = float(x)
    except (TypeError, ValueError):
        return 0
    return int(n) if n.is_integer() else n


def _agent_skills(raw) -> list[dict]:
    """技能倍率：[{name, params:[{k, main, growth}]}]。

    数据源是参考项目的 PartnerId2SkillParam_<ver>.json：
        {"普通攻击：伏特速攻": {"一段伤害倍率": [3120, 290], ...}}
    那两个数是 [Main, Growth]，**实际倍率**要按
        (Main + Growth × 技能等级) / 10000
    现算（和 ZZZeroUID 的 utils/data.py 一致），所以这里原样存下来给前台换算。
    """
    out = []
    for sname, params in (raw or {}).items():
        if not isinstance(params, dict):
            continue
        ps = []
        for pk, pv in params.items():
            if isinstance(pv, (list, tuple)) and len(pv) >= 2:
                ps.append({"k": str(pk), "main": _num(pv[0]), "growth": _num(pv[1])})
        if ps:
            out.append({"name": str(sname), "params": ps})
    return out


def _agent_levels(raw) -> list[dict]:
    """突破档位：[{min, max, hp, atk, def, mats:[{id, n}]}]。

    参考项目只给了「这一档突破后」的属性值与所需材料（材料只有 ID，没有名字表，所以
    前台只把 ID 当参考值列出来，不猜名字）。
    """
    out = []
    for key in sorted((raw or {}), key=lambda k: int(k) if str(k).isdigit() else 0):
        lv = raw[key] or {}
        mats = [{"id": str(mid), "n": _num(cnt)}
                for mid, cnt in (lv.get("materials") or {}).items()]
        out.append({
            "min": _num(lv.get("level_min")), "max": _num(lv.get("level_max")),
            "hp": _num(lv.get("hp_max")), "atk": _num(lv.get("attack")),
            "def": _num(lv.get("defence")), "mats": mats,
        })
    return out


def _agent_extras(raw) -> list[dict]:
    """核心技（基础特性）加成：[{max, items:[{k, v}]}]（每档给出该档生效的属性加成）。"""
    out = []
    for key in sorted((raw or {}), key=lambda k: int(k) if str(k).isdigit() else 0):
        ex = raw[key] or {}
        items = []
        for _, e in (ex.get("extra") or {}).items():
            if isinstance(e, dict) and e.get("name"):
                items.append({"k": str(e["name"]), "v": _num(e.get("value"))})
        out.append({"max": _num(ex.get("max_level")), "items": items})
    return out


def build_agents(ver: str) -> list[dict]:
    """代理人：基础信息 + 技能倍率 + 突破档位（属性/材料）+ 核心技加成 + 头像。"""
    skills_all = _load("PartnerId2SkillParam", ver)
    out = []
    for cid, v in _load("PartnerId2Data", ver).items():
        out.append({
            "id": str(cid),
            "name": v.get("name") or f"角色{cid}",
            "full_name": v.get("full_name") or v.get("name") or "",
            "en_name": v.get("en_name") or "",
            "rarity": str(v.get("rarity") or "A").upper(),
            "element": ELEM.get(int(v.get("element_type") or 0), ""),
            "profession": PROF.get(int(v.get("weapon_type") or 0), ""),
            "camp": v.get("camp") or "",
            "hit_type": v.get("hit_type") or "",
            "icon": _sprite_icon(v.get("sprite_id")),
            # ↓ 详情页用的扩展数据（原来只取了上面 9 个字段，点开当然「没东西看」）
            "skills": _agent_skills(skills_all.get(str(cid))),
            "levels": _agent_levels(v.get("level")),
            "extras": _agent_extras(v.get("extra_level")),
        })
    _sort_newest(out)
    return out


def build_wengines(ver: str) -> list[dict]:
    """音擎：主/副词条 + 1~5 阶音擎效果（talents）。"""
    out = []
    for wid, v in _load("WeaponId2Data", ver).items():
        code = v.get("code_name") or ""
        if not code:
            continue
        talents = []
        for _, t in sorted((v.get("talents") or {}).items(), key=lambda kv: str(kv[0])):
            if not isinstance(t, dict):
                continue
            talents.append({"name": t.get("name") or "", "desc": t.get("desc") or ""})
        out.append({
            "id": str(wid),
            "name": v.get("name") or f"音擎{wid}",
            "rarity": str(v.get("rarity") or "A").upper(),
            "main": f"{v.get('props_name') or ''} {v.get('props_value') or ''}".strip(),
            "sub": f"{v.get('rand_props_name') or ''} {v.get('rand_props_value') or ''}".strip(),
            "talents": talents,
            "icon": f"{ICON_BASE}/{code}.webp",
        })
    _sort_newest(out)
    return out


def build_discs(ver: str) -> list[dict]:
    """驱动盘套装：2 件套（desc1）/ 4 件套（desc2）效果。"""
    out = []
    for sid, v in _load("EquipId2Data", ver).items():
        sprite = (v.get("sprite_file") or "").replace("3D", "")
        if not sprite:
            continue
        out.append({
            "id": str(sid),
            "name": v.get("equip_name") or f"驱动盘{sid}",
            "desc1": v.get("desc1") or "",
            "desc2": v.get("desc2") or "",
            "icon": f"{ICON_BASE}/{sprite}.webp",
        })
    _sort_newest(out, rarity_first=False)   # 驱动盘没有稀有度，只按「新的在前」
    return out


# ---------------- 邦布（官方 /buddy/info） ----------------

def _read_accounts_file(p: "pathlib.Path") -> dict:
    """读账号存档（**已加密**，见 miyoho/securestore）。

    ⚠️ 本文件被 importlib 按路径单独加载，**不能有相对导入**，所以这里用绝对导入
    读同一个文件；万一插件 securestore 不可用（脱离宿主单独跑），退回明文 JSON 直读。
    """
    try:
        from ... import securestore

        data = securestore.load(p)
    except Exception:  # noqa: BLE001 —— 脱离宿主环境时退回明文
        data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(
            "账号存档读不出来（data/miyoushe_accounts.json）——"
            "多半是加密密钥变了（data/.auth_key 被删/被换，或 MALU_AUTH_KEY 改过），"
            "把密钥找回来，或重新扫码登录一次"
        )
    return data


def _account_cookie() -> str:
    """读本机保存的当前账号 cookie（只读，命令行/后台通用）。"""
    from ...paths import data_path
    p = data_path("miyoushe_accounts.json")
    if not p.exists():
        raise RuntimeError("没有米游社账号存档（data/miyoushe_accounts.json），无法抓邦布列表")
    accs = _read_accounts_file(p)
    aid = accs.get("current") or (list((accs.get("accounts") or {}).keys()) or [""])[0]
    ck = ((accs.get("accounts") or {}).get(aid) or {}).get("cookie") or {}
    if not ck:
        raise RuntimeError("当前账号没有可用 cookie，请先扫码登录")
    return "; ".join(f"{k}={v}" for k, v in ck.items())


def _api_headers(cookie: str) -> dict:
    return {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "x-rpc-app_version": "2.73.1",
        "x-rpc-device_id": uuid.uuid4().hex[:16],
        "x-rpc-device_fp": "".join(random.choices(string.ascii_lowercase + string.digits, k=13)),
        "x-rpc-device_name": "MI 6",
        "x-rpc-device_model": "MI 6",
        "x-rpc-sys_version": "12",
        "x-rpc-channel": "mihoyo",
        "x-rpc-platform": "android",
        "x-rpc-client_type": "5",
        "Referer": "https://act.mihoyo.com/",
        "Origin": "https://act.mihoyo.com",
        "Cookie": cookie,
    }


async def build_bangboo() -> list[dict]:
    """官方 /buddy/info：全量邦布（id / 名字 / 稀有度 / 图标）。"""
    cookie = _account_cookie()
    headers = _api_headers(cookie)
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as c:
        roles = (await c.get(
            "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie",
            params={"game_biz": "nap_cn"}, headers=headers,
        )).json()
        role = ((roles.get("data") or {}).get("list") or [{}])[0]
        uid, srv = role.get("game_uid"), role.get("region") or "prod_gf_cn"
        if not uid:
            raise RuntimeError(f"该账号没有绑定绝区零角色（{roles.get('message') or roles.get('retcode')}）")
        j = (await c.get(
            "https://api-takumi-record.mihoyo.com/event/game_record_zzz/api/zzz/buddy/info",
            params={"role_id": str(uid), "server": srv, "lang": "zh-cn"}, headers=headers,
        )).json()
    lst = (j.get("data") or {}).get("list") or []
    if not lst:
        raise RuntimeError(f"邦布接口没返回数据（{j.get('message') or j.get('retcode')}）")
    out = [
        {
            "id": str(b.get("id")),
            "name": b.get("name") or "",
            "rarity": str(b.get("rarity") or "A").upper(),
            "icon": b.get("bangboo_square_url") or "",
        }
        for b in lst
    ]
    _sort_newest(out)
    return out


# ---------------- 图标本地化 ----------------

def _ext(url: str) -> str:
    p = url.split("?")[0].lower()
    for suf in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if p.endswith(suf):
            return "jpg" if suf in (".jpg", ".jpeg") else suf.lstrip(".")
    return "png"


def _name(url: str) -> str:
    """与 asset_cache._name 完全一致：md5(url) + 扩展名（共用 ASSET_DIR）。"""
    return hashlib.md5(url.encode("utf-8")).hexdigest() + "." + _ext(url)


async def _localize(items: list[dict], client: httpx.AsyncClient) -> None:
    """把条目里的 icon 远端 URL 换成本地路由（已缓存过就直接换，不重复下载）。

    主地址拉不到时依次试 `_icon_alt` 给的备用地址，全失败才保留远端 URL
    （前端 cxImgErr 会兜住，不会出现破图占位）。
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def one(it: dict) -> None:
        src = it.get("icon") or ""
        if not src.startswith("http"):
            return
        for url in (src, _icon_alt(src)):
            if not url:
                continue
            path = ASSET_DIR / _name(url)
            if not path.exists() or path.stat().st_size == 0:
                async with sem:
                    try:
                        r = await client.get(url)
                        r.raise_for_status()
                        path.write_bytes(r.content)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("图鉴图标缓存失败 {}: {}", url, exc)
                        continue
            it["icon"] = ASSET_ROUTE + _name(url)
            return
        logger.warning("图鉴图标所有源都失败，保留远端地址 {}", src)

    await asyncio.gather(*[one(it) for it in items])


# ---------------- 组装 / 落盘 ----------------

async def build_all() -> dict:
    """抓取四类图鉴数据并本地化图标（不落盘，返回 dict）。"""
    ver = ref_version()
    logger.info("图鉴数据版本 {}（来源 {}）", ver, REF_DIR)
    agents = build_agents(ver)
    wengines = build_wengines(ver)
    discs = build_discs(ver)
    try:
        bangboo = await build_bangboo()
    except Exception as exc:  # noqa: BLE001
        logger.warning("抓取邦布列表失败（{}），沿用上一次的本地数据", exc)
        bangboo = _prev_section("bangboo")

    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:
        for group in (agents, wengines, discs, bangboo):
            await _localize(group, c)

    data = {
        "version": ver,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "agents": agents,
        "wengines": wengines,
        "discs": discs,
        "bangboo": bangboo,
    }
    logger.info(
        "图鉴抓取完成：代理人 {} / 音擎 {} / 驱动盘 {} / 邦布 {}",
        len(agents), len(wengines), len(discs), len(bangboo),
    )
    return data


def _prev_section(name: str) -> list:
    """读上一次落盘的某一节（用于邦布抓取失败时保留旧数据）。"""
    try:
        items = json.loads(SECTION_FILES[name].read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except (OSError, ValueError):
        return []


_LANG_MOD = None


def _lang_module():
    """按路径加载同目录的 lang.py（**不能**用相对导入：本模块刻意不导入本插件的包）。"""
    global _LANG_MOD
    if _LANG_MOD is None:
        import importlib.util

        p = pathlib.Path(__file__).with_name("lang.py")
        spec = importlib.util.spec_from_file_location("miyoushe_codex_lang", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LANG_MOD = mod
    return _LANG_MOD


async def build_and_save() -> dict:
    """抓取 + 落盘，并让 codex_data 的内存缓存失效。返回落盘后的数据。

    落盘格式：data/zzz/ 下四个分段文件（agents/wengines/discs/bangboo）+ 一个 meta.json
    （版本号 + 更新时间）。四类不再混在一个 codex_data.json 里。

    落盘后**顺带生成多语言映射**（data/zzz/lang/<类别>-<语言>.json）：这一步要读 7 张
    官方文本表、属于重 CPU + 重 IO，所以丢到线程里跑，别把事件循环卡住。失败只警告，不影响图鉴。
    """
    data = await build_all()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in SECTION_FILES:
        SECTION_FILES[name].write_text(
            json.dumps(data.get(name, []), ensure_ascii=False, indent=1), encoding="utf-8"
        )
    META_FILE.write_text(
        json.dumps(
            {"version": data.get("version") or "", "updated": data.get("updated") or ""},
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    try:
        await asyncio.to_thread(_lang_module().build_and_save, data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("图鉴多语言生成失败（图鉴本身不受影响）：{}", exc)
    return data


# ---------------- 「更新数据」流程自述 ----------------
#
# 图鉴「本地快照 · 数据版本 …（… 更新）」后面那个「更新方式」小链接，展示的就是这里的内容。
# 说明**全部由本文件与 lang.py 里的真实常量拼出来**（源地址 / 目录 / 文件名 /
# 并发与超时 / 表格体积），所以以后改抓取逻辑，面板上的说明会跟着变，
# 不会出现「文档写的是 A、代码做的是 B」。


def _size(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def _p(path) -> str:
    """路径统一成正斜杠（Windows 的反斜杠在页面上不好看，也容易被当成转义）。"""
    return str(path).replace("\\", "/")


def pipeline() -> dict:
    """「更新数据」到底做了什么（纯描述：不联网、不写盘，只做几次 stat）。"""
    lang = _lang_module()
    try:
        ver = ref_version()
    except Exception:  # noqa: BLE001  参考目录缺失时也得能说明流程
        ver = ""

    # 官方文本表清单（带本地体积）：基准表 + 各目标语言表
    total = 0
    links_tm = []
    for code, fname in [("zh-cn", lang.BASE_FILE)] + list(lang.LANG_FILES.items()):
        p = lang.TEXTMAP_DIR / fname
        try:
            n = p.stat().st_size
        except OSError:
            n = 0
        total += n
        links_tm.append({
            "k": lang.LANG_NAMES.get(code, code),
            "v": fname,
            "note": (_size(n) if n else "未缓存") + ("　·　基准表（反查用）" if code == "zh-cn" else ""),
        })

    ref_files = [
        ("PartnerId2Data" + (f"_{ver}" if ver else "") + ".json",
         "代理人：名字 / 稀有度 / 元素 / 职业 / 阵营 + 突破档位与所需材料（level）+ 核心技加成（extra_level）"),
        ("PartnerId2SkillParam" + (f"_{ver}" if ver else "") + ".json",
         "代理人技能倍率：存原始 [Main, Growth]，前台按 (Main + Growth × 技能等级) / 10000 换算"),
        ("WeaponId2Data" + (f"_{ver}" if ver else "") + ".json",
         "音擎：主 / 副词条 + 1~5 阶音擎效果"),
        ("EquipId2Data" + (f"_{ver}" if ver else "") + ".json",
         "驱动盘套装：2 件套 / 4 件套效果"),
    ]

    steps = [
        {
            "n": 1,
            "title": "资料文本 · 直接读本地参考库（不联网）",
            "desc": "代理人 / 音擎 / 驱动盘的资料来自参考项目 ZZZeroUID 的 map 表 —— 那是别人已经从游戏"
                    "配置表里导出的中文 JSON，我们只读本机文件，不下载、不解析游戏包。"
                    "版本号自动取目录里最新的一版（也可用环境变量 ZZZ_MAP_VER 指定）。",
            "links": [{"k": "目录", "v": _p(REF_DIR), "note": "可用环境变量 ZZZ_REF_DIR 覆盖"}] +
                     [{"k": k, "v": "", "note": v} for k, v in ref_files],
            "out": "代理人 agents / 音擎 wengines / 驱动盘 discs",
        },
        {
            "n": 2,
            "title": "邦布 · 官方接口（需要已登录的米游社账号）",
            "desc": "邦布名单只有官方接口有，抓取时用【本机当前账号】的 cookie 现拉一次。"
                    "没登录、或接口失败，就沿用上一次落盘的邦布数据，不会把这一节清空。",
            "links": [
                {"k": "① 取角色", "note": "拿到该账号下的绝区零 uid 与区服",
                 "v": "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie?game_biz=nap_cn"},
                {"k": "② 邦布列表", "note": "返回 id / 名字 / 稀有度 / 图标地址",
                 "v": "https://api-takumi-record.mihoyo.com/event/game_record_zzz/api/zzz/buddy/info"
                      "?role_id=<uid>&server=<区服>&lang=zh-cn"},
                {"k": "凭证来源", "v": "data/miyoushe_accounts.json",
                 "note": "只读本机存档，不回传；抓取用的设备指纹是每次随机生成的"},
            ],
            "out": "邦布 bangboo",
        },
        {
            "n": 3,
            "title": "图标 · 全部下载到本机（一次性）",
            "desc": "四类条目的图标在抓取时下完，json 里存的是本地路由，所以之后打开图鉴"
                    "不访问任何外部地址。同一个地址只下一次（文件名 = md5(url) + 扩展名），"
                    "已经存在的文件直接跳过；主地址失败会自动试备用地址。",
            "links": [
                {"k": "代理人头像", "v": f"{ICON_ENKA}/IconRoleCircle<编号>.png",
                 "note": "Enka 官方资源镜像，编号 = sprite_id 两位补零，新角色更新及时"},
                {"k": "备用源", "v": f"{ICON_BASE}/IconRoleCircle<编号>.webp",
                 "note": "Enka 没有的（测试服角色）回退 nanoka；nanoka 的新头像有滞后（68 号曾错放成 67 号），故只作备用"},
                {"k": "音擎 / 驱动盘 / 邦布", "v": f"{ICON_BASE}/<code_name>.webp",
                 "note": "仍然走 nanoka 镜像"},
                {"k": "落盘目录", "v": _p(ASSET_DIR),
                 "note": f"并发 {_CONCURRENCY} · 单张超时 {_TIMEOUT_S}s（连接 {_CONNECT_S}s）"},
            ],
            "out": f"本地路由 {ASSET_ROUTE}<md5>.webp",
        },
        {
            "n": 4,
            "title": "落盘 · 一份本地快照",
            "desc": "四类数据 + 版本号 + 更新时间写成一个 JSON。抓取只在你点「更新数据」时发生，"
                    "之后前台接口只读这份快照。",
            "links": [
                {"k": "产物", "v": _p(DATA_DIR), "note": f"当前版本 {ver or '未知'}，四类各一个 json + meta.json"},
                {"k": "触发方式", "v": "POST /admin/api/miyoushe/zzz/codex/refresh",
                 "note": "界面「更新数据」按钮；或命令行 tools/gen_zzz_codex.py（不用重启 bot）"},
            ],
            "out": "data/zzz/{agents,wengines,discs,bangboo}.json + meta.json",
        },
        {
            "n": 5,
            "title": "多语言映射 · 官方 TextMap 文本表（繁中 / 英 / 日 / 韩 / 泰 / 俄）",
            "desc": f"官方文本表的 key 是路径式标识符（如 Chat_PartnerName_1011 = 安比短名），"
                    f"同一个 key 在各语言表里一一对应。但我们的图鉴数据只存了中文、丢了 key，"
                    f"所以要「反查」：先用中文原文在简中基准表里找出候选 key，再用它去各语言表取译文。"
                    f"文本表缓存在本机（合计 {_size(total) if total else '未缓存'}），只在生成时下载。",
            "links": [
                {"k": "镜像仓库", "v": lang.SOURCE_URL,
                 "note": "GitHub 上的原仓库 2024-07 被米游社 DMCA 封禁，只有这个 Gitea 镜像还活着"},
                {"k": "下载地址前缀", "v": lang.MIRROR + "/<文件名>"},
                {"k": "缓存目录", "v": _p(lang.TEXTMAP_DIR), "note": "已在 .gitignore，不进仓库"},
            ] + links_tm,
            "bullets": [
                "① 模板：能按 id 直接拼出 key —— 驱动盘 EquipmentSuit_{id}_name / _2_des / _4_des、"
                "邦布 Bangboo_Name_{id}、代理人全名 Partner_Name_{id}，零歧义",
                "② 命名空间：把候选压到固定前缀 —— 元素 ElementType_*、职业 ProfessionName_*、"
                "伤害类型 HitType_*、音擎 Item_Weapon_*_Name / Weapon_TalentTitle_* / Weapon_TalentDes_*",
                "③ 签名投票：仍有多候选时，把每个候选的「各语言译文组合」当签名投票，取最一致的那组"
                "（同一句话在不同场景里翻译一般相同）",
                "取不到的文本不写入映射，前端原样显示中文 —— 宁缺毋错，不会出现空白或乱码",
                "官方镜像的版本可能比参考项目旧（如镜像 3.2.0 / 图鉴 3.3.3），"
                "差集里的新角色、新音擎会回退中文，等镜像更新后重跑即可补齐",
            ],
            "out": "data/zzz/lang/<类别>-<语言>.json（{条目id: 同构翻译对象}，按 4 类 × 6 语言拆分）",
        },
        {
            "n": 6,
            "title": "前台 · 之后全走本地",
            "desc": "更新完成后后台丢掉内存缓存，页面重新拉一次 /zzz/codex。此后浏览、搜索、"
                    "筛选、看详情都在本地完成，不产生任何外部请求；多语言映射是按需加载的 —— "
                    "默认简中时一次都不拉，切到非简中才拉 /zzz/codex/lang。",
            "links": [
                {"k": "数据接口", "v": "GET /admin/api/miyoushe/zzz/codex", "note": "四类数据 + 版本号，不带多语言映射"},
                {"k": "多语言接口", "v": "GET /admin/api/miyoushe/zzz/codex/lang", "note": "切非简中时才拉"},
                {"k": "图标接口", "v": "GET /miyoho-asset/<文件名>", "note": "本机文件，不走外网"},
            ],
            "out": "无需联网即可使用",
        },
    ]

    return {
        "version": ver,
        "summary": "更新只在你点「更新数据」时发生：抓取（联网）→ 图标本地化 → 落盘 → 生成多语言，"
                   "之后前台全部读本地文件。",
        "steps": steps,
        "table_dir": _p(lang.TEXTMAP_DIR),
        "table_total": _size(total) if total else "未缓存",
    }
