"""抽卡数据统计：把原始 item 列表算成前端要展示的各类汇总。

设计对照「绝区零工坊」式总结（总览卡 / 抽卡总结 / 卡片模式 / 近期卡池 / 版本总结）：

- **pity 链**：每个金（S级）距离上一个金的抽数（数据起点处的第一个金即真实已垫，
  不再补占位卡）；尾部输出 `pity_now`（当前已垫，即最新一次出金之后到现在的抽数）。
- **UP 判定**：优先用 API 返回的 `up_ids`（item_id 命中即 UP）。没有 up_ids 时
  up 相关指标输出 null，前端显示 "—"，不影响其他统计。
- **频段归一**：item 的 `gacha_type` 可能是基础类型（1/2/3/5/102/103）也可能是
  池子编号（1001/2001/3001/5001/12002/13002），统一归到基础类型（ZZZeroUID 映射）。
- **版本分组**：按 `gacha_id` 分组；版本开始时间取该池最早记录的日期。
- **花费**：按 160 复色菲林 / 折估算。

输入 items 要求按 id 倒序（时间倒序，gacha_store.save 出来的顺序）。
"""

from __future__ import annotations

# 基础频段 → 名称（与 client.GACHA_TYPES 一致；这里复制一份避免循环 import）
POOL_NAMES = {
    "1": "常驻频段",
    "2": "独家频段",
    "3": "音擎频段",
    "5": "邦布频段",
    "102": "独家重映",
    "103": "音擎回响",
}
# 池子编号 → 基础频段（ZZZeroUID gacha_type_meta_data）
_CODE_TO_BASE = {
    "1001": "1", "2001": "2", "3001": "3",
    "5001": "5", "12002": "102", "13002": "103",
}
# 汇总页签里「限定/常驻」的口径
LIMITED_BASES = ("2", "102")
STANDARD_BASES = ("1",)
# 补集法判 歪 适用的频段（限定角色 + 限定音擎；邦布无常驻池概念，常驻频段无 UP 概念）
COMPLEMENT_BASES = ("2", "102", "3", "103")
# 总结页签里的频段显示顺序
POOL_ORDER = ("2", "3", "1", "5", "102", "103")

FILM_PER_PULL = 160  # 每抽消耗复色菲林

# 常驻 S 名单（「补集法」判 歪 用）。
# 官方 getGachaLog **不返回** is_up / 歪 标记（原始字段只有
# uid/gacha_id/gacha_type/item_id/count/time/name/lang/item_type/rank_type/id），
# 只能靠「限定频段上的 S = UP，常驻 S = 歪」反推。下面两份名单**需要你按当前版本核对**
# 后再填（填标准/常驻 S 级的中文名，逗号分隔）：
#   - **空集合时补集法不生效**：判不出 UP/歪，命令与总结图里**什么都不显示**
#     （2026-10-01 用户要求：判不出就别写「未知」）；
#   - 填上后，限定频段(独家/音擎/重映/回响)上的 S 角色/音擎就能判 UP / 歪。
#   - 常驻频段(1)与邦布频段(5)没有「UP」概念，不参与判定（保持不显示）。
STANDARD_S_AGENTS = set()       # 例：{"猫宫又奈", "珂蕾妲", "丽娜", "格莉丝", "「11号」", "莱卡恩"}
STANDARD_S_WENGINES = set()     # 例：{"啜泣摇篮", "燃狱齿轮", "硫磺石"}


def base_type(item: dict) -> str:
    """把 item 的 gacha_type（池子编号或基础类型）归一到基础频段；未知返回 ""。"""
    t = str(item.get("gacha_type") or "")
    if t in POOL_NAMES:
        return t
    return _CODE_TO_BASE.get(t, "")


def rank_of(item: dict) -> str:
    """品质统一成 S / A / 其他原文。ZZZ 的 rank_type 数字口径不统一，两者都认。"""
    r = str(item.get("rank_type") or "").upper()
    if r in ("S", "4", "5"):
        return "S"
    if r in ("A", "3"):
        return "A"
    return r or "B"


def _is_up(item: dict):
    """是否 UP。

    判定优先级：
      1) 官方直给 up_ids（极少数记录带）→ item_id 命中即 UP，否则 歪；
      2) 常驻 S 补集法：仅对「限定频段(2/3/102/103) + S级 + 角色/音擎」生效 ——
         名字命中常驻 S 名单 → 歪(False)，否则该限定 S 必是当期限定 UP(True)；
      3) 其余（无 up_ids 且未配名单 / 常驻频段 / 邦布频段 / A级）→ None(未知)，
         绝不瞎猜。
    """
    ups = {str(x) for x in (item.get("up_ids") or [])}
    if ups:
        return str(item.get("item_id") or "") in ups

    # 补集法：只在限定频段 + S 级 + 名单已配置时才生效
    base = base_type(item)
    if base in COMPLEMENT_BASES and rank_of(item) == "S":
        itype = "角色" if "角色" in str(item.get("item_type") or "") else (
            "音擎" if "音擎" in str(item.get("item_type") or "") else "")
        name = str(item.get("name") or "")
        if itype == "角色" and STANDARD_S_AGENTS:
            return name not in STANDARD_S_AGENTS      # 不在常驻名单 = 限定UP
        if itype == "音擎" and STANDARD_S_WENGINES:
            return name not in STANDARD_S_WENGINES
    return None


def _has_up_info(items: list) -> bool:
    """能否判 歪：要么记录带了 up_ids，要么常驻 S 补集名单已配置。"""
    if any(item.get("up_ids") for item in items if isinstance(item, dict)):
        return True
    return bool(STANDARD_S_AGENTS or STANDARD_S_WENGINES)


def summarize(items: list) -> dict:
    """把倒序的原始 items 算成完整汇总（见模块 docstring 的口径说明）。"""
    items = [it for it in items if isinstance(it, dict)]

    total = len(items)
    s_total = sum(1 for it in items if rank_of(it) == "S")
    a_total = sum(1 for it in items if rank_of(it) == "A")

    pools = _pools(items)
    versions = _versions(items)
    recent = _recent_golden(items, 40)

    limited_s = sum(pools[b]["s_count"] for b in LIMITED_BASES if b in pools)
    standard_s = sum(pools[b]["s_count"] for b in STANDARD_BASES if b in pools)

    return {
        "total": total,
        "s_total": s_total,
        "a_total": a_total,
        "avg_per_s": round(total / s_total, 1) if s_total else None,
        "limited_s": limited_s,
        "standard_s": standard_s,
        "pools": [pools[b] for b in POOL_ORDER if b in pools],
        "versions": versions,
        "recent": recent,
    }


def _head_gap(golden: list, pity: int) -> list:
    """（已废弃）曾用于数据起点前补 "?" 占位卡。

    实测发现：抽卡数据从最旧记录起就是连续保存的（官方保留半年），第一个金就是真实
    距数据起点的已垫抽数，补占位卡反而会把真实的首个金（如莱卡恩 38 抽）错显成
    "已垫 37 抽"。故不再补卡，直接返回原链；保留函数签名以免其他调用点报错。
    """
    return golden


def _build_golden(its: list) -> tuple[list, int]:
    """由正序 items 生成 pity 链 + 尾部已垫抽数。返回 (golden, pity_now)。"""
    golden: list[dict] = []
    pity = 0
    for it in its:
        pity += 1
        if rank_of(it) != "S":
            continue
        golden.append(
            {
                "name": str(it.get("name") or "未知"),
                "item_id": str(it.get("item_id") or ""),
                "time": str(it.get("time") or ""),
                "pity": pity,
                "is_up": _is_up(it),
                "first": False,
            }
        )
        pity = 0
    return golden, pity


def _pools(items: list) -> dict:
    """按频段汇总 + 每频段的 pity 链（时间倒序，最新出的金在前）。"""
    # 预排序：倒序 → 正序（旧 → 新）
    ordered = list(reversed(items))
    by_pool: dict[str, list] = {}
    for it in ordered:
        by_pool.setdefault(base_type(it) or "other", []).append(it)

    out: dict[str, dict] = {}
    for base, its in by_pool.items():
        golden, pity = _build_golden(its)
        s_count = len(golden)
        golden = list(reversed(golden))   # 最新出的金在前（用户要求从最新记录开始，而不是最旧）
        has_up = base in COMPLEMENT_BASES
        for g in golden:
            g["has_up"] = has_up
        a_count = sum(1 for it in its if rank_of(it) == "A")
        # 「不歪概率」只算限定频段（常驻/邦布无 UP 概念，不参与）
        up_count = None
        if base in COMPLEMENT_BASES:
            up_count = sum(1 for g in golden if g["is_up"]) if _has_up_info(its) else None

        entry = {
            "base": base,
            "name": POOL_NAMES.get(base, "其他"),
            "total": len(its),
            "s_count": s_count,
            "a_count": a_count,
            "avg_per_s": round(len(its) / s_count, 1) if s_count else None,
            "up_count": up_count,
            "up_rate": round(up_count / s_count, 3) if up_count is not None and s_count else None,
            "avg_per_up": (
                round(len(its) / up_count, 1) if up_count is not None and up_count else None
            ),
            "pity_now": pity,          # 当前已垫（距上次金）
            "golden": golden,
        }
        if base == "other":            # 未知频段排最后
            out["~other"] = entry
        else:
            out[base] = entry
    return out


def _version_key(item: dict) -> tuple:
    """卡池分组键。

    **坑（2026-09-30 实测）**：ZZZ 登录凭证路线拉回来的记录，`gacha_id` 恒为 `0`
    （只有游戏内分享链接那套参数才带真实卡池号）。直接按它分组会把六个频段
    揉成一张「卡池 0」的假卡。这里退化：gacha_id 无效时按「频段 + 月份」分组。
    """
    gid = str(item.get("gacha_id") or "")
    if gid and gid != "0":
        return ("id", gid, "")
    return ("na", base_type(item) or "other", str(item.get("time") or "")[:7])


def _versions(items: list) -> list[dict]:
    """按卡池分组：抽数 / S / A / 花费 / S 的 pity 链 / A 的聚合计数。

    有真实 gacha_id → 一张卡一个池；没有（gacha_id=0）→ 退化为「频段 · 月份」，
    并用 `approx: True` 告诉前端这张卡是按月份估出来的，不是官方卡池。
    """
    ordered = list(reversed(items))
    groups: dict[tuple, list] = {}
    for it in ordered:
        groups.setdefault(_version_key(it), []).append(it)

    out: list[dict] = []
    for key, its in groups.items():
        kind, first, second = key
        base = base_type(its[0]) if its else ""
        s_items, _ = _build_golden(its)
        s_items = list(reversed(s_items))   # 最新出的金在前
        has_up = base in COMPLEMENT_BASES
        for g in s_items:
            g["has_up"] = has_up
        approx = kind == "na"

        a_map: dict[str, int] = {}
        for it in its:
            if rank_of(it) == "A":
                n = str(it.get("name") or "未知")
                a_map[n] = a_map.get(n, 0) + 1

        times = [str(it.get("time") or "") for it in its if it.get("time")]
        if approx:
            pool_name = f"{POOL_NAMES.get(base, '其他')} · {second or '未知月份'}"
            title = pool_name
        else:
            pool_name = POOL_NAMES.get(base, "未知频段")
            title = f"{pool_name} · {first}"

        out.append(
            {
                "gacha_id": "" if approx else first,
                "base": base,
                "approx": approx,          # True = 按月份估的，不是官方卡池编号
                "pool_name": pool_name,
                "title": title,
                "start": min(times)[:10] if times else "",
                "end": max(times)[:10] if times else "",
                "total": len(its),
                "s_count": len(s_items),
                "a_count": sum(a_map.values()),
                "cost": len(its) * FILM_PER_PULL,
                "s_items": s_items,
                "a_counts": sorted(a_map.items(), key=lambda kv: -kv[1]),
            }
        )
    out.sort(key=lambda v: v["start"] or "", reverse=True)
    return out


def _recent_golden(items: list, limit: int = 40) -> list[dict]:
    """最近出到的金（时间倒序），带所在频段。"""
    out: list[dict] = []
    for it in items:
        if rank_of(it) != "S":
            continue
        base = base_type(it)
        out.append(
            {
                "time": str(it.get("time") or ""),
                "name": str(it.get("name") or "未知"),
                "item_id": str(it.get("item_id") or ""),
                "pool": POOL_NAMES.get(base, "未知"),
                "is_up": _is_up(it),
                "has_up": base in COMPLEMENT_BASES,
            }
        )
        if len(out) >= limit:
            break
    return out
