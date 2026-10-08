"""危局强袭战 / 式舆防卫战 的战绩快照持久化：data/zzz/records/{kind}/{uid}.json

为什么必须自己存
----------------
官方接口 hadal_mem_detail_v2（危局）/ hadal_info_v2（防卫战）**只返两份数据**：
schedule_type=1（本期）与 2（上期），再往前官方就查不到了。所以每拿到一次结果
就按「赛期」存一份快照 —— 日积月累才拼得出完整历史。

赛期（period）怎么认
--------------------
用接口自带的赛期边界拼 key（官方给的就是赛期起止，不靠猜）：
  deadly: data.start_time / data.end_time
  shiyu : data.hadal_info_v2.hadal_begin_time / hadal_end_time
PartialTime → "2026-09-22T04:00"，key = "起点~终点"。
**本期 →（几周后）变上期时这个 key 不变**，所以「同一赛期查多次」永远落在同一条上，
不会因为接口从 schedule_type=1 换成 2 就多出一条。

合并规则（用户要求：本期数据会多次变动，只留最新）
--------------------------------------------------
- key 已存在 → **覆盖 data**（只留最新一版），保留 first_seen，刷新 updated_at、revisions+1；
- key 不存在 → 追加；
- 列表按 key 倒序（= 赛期从新到旧）。

落盘位置
--------
data/zzz/records/… —— 和抽卡记录（data/zzz/{uid}.csv）同属**用户数据**，
放项目根 data/ 下而不是插件里：插件可以被市场卸载/重装，用户数据不能跟着走。
写盘用 tmp + os.replace 原子替换，写坏一半不会毁掉旧存档（同 gacha_store 的做法）。
旁边还有个 records/_capture.json，只记「上次自动抓取时刻」，给后台循环做节流用，
不是战绩数据（见 last_capture_at / mark_capture）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# 支持的战绩类型（也是 URL 参数 kind 的取值）
KINDS = ("deadly", "shiyu")
KIND_LABEL = {"deadly": "危局强袭战", "shiyu": "式舆防卫战"}


from ...paths import zzz_path

DATA_DIR = zzz_path("records")

# 自动存档的「上次抓取时刻」标记（给后台循环节流用，不是战绩数据）。
# 放在 records/ 根下、和 deadly/ shiyu/ 两个子目录平级，不会被当成某个 uid 的战绩读走。
_STATE_PATH = DATA_DIR / "_capture.json"


def path(kind: str, uid: str) -> Path:
    return DATA_DIR / str(kind) / f"{uid}.json"


def last_capture_at() -> int:
    """上次自动存档**尝试**的时刻（0 = 从未抓过）。文件缺失/读坏都按 0 算。"""
    try:
        doc = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return int(doc.get("last_capture_at") or 0)
    except (OSError, ValueError, TypeError):
        return 0


def mark_capture() -> None:
    """记一笔「刚抓过」，供后台循环算下一次该在什么时候。

    写失败不抛：它只是节流用的，最坏结果是下次启动时多抓一轮 ——
    而存档本身是「同赛期覆盖成最新」的幂等操作，多抓没有副作用。
    """
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"last_capture_at": int(time.time())}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, _STATE_PATH)
    except OSError:
        pass


# ---------------- 赛期识别 ----------------


def _pad(n) -> str:
    return str(int(n or 0)).zfill(2)


def _fmt_time(t) -> str:
    """PartialTime → "2026-09-22T04:00"（官方没给秒时就不带秒）。缺字段返回空串。"""
    if not isinstance(t, dict) or not t.get("year"):
        return ""
    s = f"{int(t['year'])}-{_pad(t.get('month'))}-{_pad(t.get('day'))}"
    if t.get("hour") is not None:
        s += f"T{_pad(t.get('hour'))}:{_pad(t.get('minute'))}"
    return s


def bounds(kind: str, data: dict) -> tuple[dict, dict]:
    """取赛期起止（PartialTime 原始字典）。"""
    d = data if isinstance(data, dict) else {}
    if kind == "shiyu":
        info = d.get("hadal_info_v2")
        info = info if isinstance(info, dict) else {}
        return (
            info.get("hadal_begin_time") if isinstance(info.get("hadal_begin_time"), dict) else {},
            info.get("hadal_end_time") if isinstance(info.get("hadal_end_time"), dict) else {},
        )
    return (
        d.get("start_time") if isinstance(d.get("start_time"), dict) else {},
        d.get("end_time") if isinstance(d.get("end_time"), dict) else {},
    )


def period_key(kind: str, data: dict) -> str:
    """赛期唯一 key。两个时间都取不到时返回空串（这种情况下不存档）。"""
    a, b = bounds(kind, data)
    sa, sb = _fmt_time(a), _fmt_time(b)
    if not sa and not sb:
        return ""
    return f"{sa}~{sb}"


def period_label(kind: str, data: dict) -> str:
    """给人看的赛期："2026-09-22 ~ 2026-10-06"（只用日期部分）。"""
    a, b = bounds(kind, data)
    da, db = _fmt_time(a)[:10], _fmt_time(b)[:10]
    if da and db:
        return f"{da} ~ {db}"
    return da or db or ""


# ---------------- 摘要（列表页用，不必把整份 data 发给前端） ----------------


def _summary(kind: str, data: dict) -> dict:
    d = data if isinstance(data, dict) else {}
    if kind == "shiyu":
        info = d.get("hadal_info_v2") if isinstance(d.get("hadal_info_v2"), dict) else {}
        brief = info.get("brief") if isinstance(info.get("brief"), dict) else {}
        fifth = info.get("fitfh_layer_detail") if isinstance(info.get("fitfh_layer_detail"), dict) else {}
        teams = fifth.get("layer_challenge_info_list")
        teams = teams if isinstance(teams, list) else []
        score = brief.get("score")
        if not score:                       # brief 偶尔为空，退回「第五防线各队得分之和」
            score = sum(int(t.get("score") or 0) for t in teams if isinstance(t, dict))
        return {
            "nick": str(d.get("nick_name") or ""),
            "score": int(score or 0),
            "rank_percent": brief.get("rank_percent"),
            "rating": str(brief.get("rating") or ""),
            "teams": len(teams),
        }
    return {
        "nick": str(d.get("nick_name") or ""),
        "score": int(d.get("total_score") or 0),
        "star": int(d.get("total_star") or 0),
        "rank_percent": d.get("rank_percent"),
        "hard": bool(d.get("hard_list")),
        "hard_rank_percent": d.get("hard_rank_percent"),
    }


def _strip(entry: dict) -> dict:
    """去掉体积最大的 data，只留 meta（列表接口用）。"""
    return {k: v for k, v in entry.items() if k != "data"}


# ---------------- 读写 ----------------


def load(kind: str, uid: str) -> dict:
    """读某个角色某个类型的存档；没有或读不出来返回空壳（不抛）。"""
    empty = {"kind": kind, "uid": str(uid), "server": "", "updated_at": 0, "count": 0, "periods": []}
    p = path(kind, uid)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(doc, dict) or not isinstance(doc.get("periods"), list):
        return empty
    doc["periods"] = [x for x in doc["periods"] if isinstance(x, dict) and x.get("key")]
    doc["periods"].sort(key=lambda x: str(x.get("key") or ""), reverse=True)
    doc["count"] = len(doc["periods"])
    return doc


def _write(kind: str, uid: str, doc: dict) -> None:
    p = path(kind, uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)                      # 原子换文：写一半失败不会毁掉旧存档


def save(kind: str, uid: str, server: str, data: dict, source: str = "web") -> dict:
    """把一个赛期的快照落盘（同赛期覆盖成最新的那版）。

    返回该赛期的 meta（不含 data）；**取不到赛期信息时返回 {} 且不写盘**。
    """
    if kind not in KINDS:
        raise ValueError(f"未知战绩类型：{kind}")
    key = period_key(kind, data)
    if not key:
        return {}

    uid = str(uid)
    doc = load(kind, uid)
    now = int(time.time())
    old = next((x for x in doc["periods"] if x.get("key") == key), None)

    entry = {
        "key": key,
        "period": period_label(kind, data),
        "start": bounds(kind, data)[0],
        "end": bounds(kind, data)[1],
        "summary": _summary(kind, data),
        "first_seen": int((old or {}).get("first_seen") or now),
        "updated_at": now,
        # 同一赛期被刷新过几次（本期会被玩家重打，这个数能看出变动频率）
        "revisions": int((old or {}).get("revisions") or 0) + 1,
        "source": str(source or "web"),
        "data": data,
    }
    periods = [x for x in doc["periods"] if x.get("key") != key]
    periods.append(entry)
    periods.sort(key=lambda x: str(x.get("key") or ""), reverse=True)

    doc = {
        "kind": kind,
        "uid": uid,
        "server": str(server or doc.get("server") or ""),
        "updated_at": now,
        "count": len(periods),
        "periods": periods,
    }
    _write(kind, uid, doc)
    return _strip(entry)


def list_periods(kind: str, uid: str) -> dict:
    """赛期列表（meta，不含 data）——历史面板用。"""
    doc = load(kind, uid)
    return {
        "uid": str(uid),
        "server": doc.get("server") or "",
        "count": len(doc["periods"]),
        "updated_at": int(doc.get("updated_at") or 0),
        "periods": [_strip(x) for x in doc["periods"]],
    }


def get_period(kind: str, uid: str, key: str) -> dict | None:
    """取某一期的完整快照（含 data）；没有返回 None。"""
    for x in load(kind, uid)["periods"]:
        if x.get("key") == key:
            return x
    return None


def drop_period(kind: str, uid: str, key: str) -> bool:
    """删掉某一期（主要给「存错了/测试数据」兜底）。返回是否真的删了。"""
    doc = load(kind, uid)
    keep = [x for x in doc["periods"] if x.get("key") != key]
    if len(keep) == len(doc["periods"]):
        return False
    doc["periods"] = keep
    doc["count"] = len(keep)
    doc["updated_at"] = int(time.time())
    _write(kind, uid, doc)
    return True
