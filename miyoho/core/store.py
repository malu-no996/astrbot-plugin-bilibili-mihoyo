"""米游社账号凭证持久化：data/miyoushe_accounts.json。

与 B 站插件不同，米游社需要**同时管理多个账号**，所以这里是「多账号」结构：

{
  "current": "12345678",          # 当前选中的 account_id（空串=未选）
  "accounts": {
    "12345678": {
      "account_id": "12345678",
      "mid": "12345678",           # 米游社通行证 mid（部分接口用）
      "cookie": {"ltuid_v2": "...", "ltoken_v2": "...", "account_id": "...",
                 "cookie_token": "...", "account_mid_v2": "..."},
      "nickname": "昵称", "face": "头像 URL", "level": 0,
      "login_at": 1759..., "expires_at": 0,
      "updated_at": 1759...
    }
  }
}

凭证属敏感信息，只落在本机 data/ 目录；对外（配置页 / 命令）一律只给掩码后的
meta，绝不回传 ltoken / cookie_token 等可用凭证。

**落盘加密**：整个文件经 `plugins.libs.securestore` 加密（Fernet / AES-128-CBC +
HMAC-SHA256），密文里记着用的是哪把密钥；旧明文文件在第一次读取时会自动加密写回。
密钥来自 .env 的 `MALU_AUTH_KEY`，没配就用自动生成的 `data/.auth_key`。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .. import securestore

from ..paths import data_path

_FILE = data_path("miyoushe_accounts.json")

_lock = threading.RLock()
_state: dict = {"current": "", "accounts": {}}


def _load() -> None:
    global _state
    try:
        loaded = securestore.load(_FILE)
        if isinstance(loaded, dict):
            accs = loaded.get("accounts")
            _state = {
                "current": str(loaded.get("current") or ""),
                "accounts": accs if isinstance(accs, dict) else {},
            }
    except Exception:  # noqa: BLE001 —— 读不出来就当没有账号，别拦住插件加载
        pass


_load()


def _save() -> None:
    try:
        securestore.dump(_FILE, _state)    # 加密 + 原子写（先 .tmp 再替换）
    except Exception:  # noqa: BLE001 —— 写盘失败不该把调用方带崩
        pass


# ---------------- 写 ----------------

# 需要保留的 Cookie 字段（登录成功后从 Set-Cookie 里挑这些）
COOKIE_KEYS = (
    "ltuid", "ltuid_v2", "ltoken", "ltoken_v2", "ltmid", "ltmid_v2",
    "account_id", "account_id_v2", "account_mid", "account_mid_v2",
    "cookie_token", "cookie_token_v2", "login_uid", "login_ticket",
    # stoken / stuid / mid：抽卡（genAuthKey）专用。2026-09 之前登录的账号没有这三项，
    # 表现为抽卡一直 retcode -100「登录状态失效」，重新扫码登录即可补上。
    "stoken", "stoken_v2", "stuid", "stuid_v2", "mid",
)


def save_account(account_id, cookie: dict, profile: dict | None = None, expires_at: int = 0) -> dict:
    """保存（或覆盖）一个账号。

    - account_id：米游社账号 id（字符串化后作为 key）
    - cookie：可直接用于请求的 Cookie 字典
    - profile：{nickname, face, level, mid}
    """
    aid = str(account_id or "").strip()
    if not aid:
        raise ValueError("缺少 account_id")
    info = profile or {}
    with _lock:
        prev = _state["accounts"].get(aid) or {}
        # 凭证「合并」而非覆盖：HYP 扫码登录只发 stoken/stuid/mid（Set-Cookie 里可能
        # 没有 ltoken / cookie_token），直接覆盖会把旧的可用凭证丢掉，战绩接口就挂了。
        merged = dict(prev.get("cookie") or {})
        merged.update(
            {k: str(v) for k, v in dict(cookie or {}).items() if v not in (None, "")}
        )
        item = {
            "account_id": aid,
            "mid": str(info.get("mid") or prev.get("mid") or aid),
            "cookie": merged,
            "nickname": str(info.get("nickname") or prev.get("nickname") or ""),
            "face": str(info.get("face") or prev.get("face") or ""),
            "level": int(info.get("level") or prev.get("level") or 0),
            "login_at": int(prev.get("login_at") or time.time()),
            "updated_at": int(time.time()),
            "expires_at": int(expires_at or prev.get("expires_at") or 0),
        }
        _state["accounts"][aid] = item
        if not _state.get("current"):
            _state["current"] = aid
        _save()
        return dict(item)


def update_profile(account_id, profile: dict) -> None:
    """补充/刷新账号昵称头像等信息。"""
    aid = str(account_id or "")
    with _lock:
        item = _state["accounts"].get(aid)
        if not item:
            return
        for key in ("nickname", "face", "mid"):
            if profile.get(key):
                item[key] = str(profile[key])
        if profile.get("level") is not None:
            try:
                item["level"] = int(profile["level"])
            except (TypeError, ValueError):
                pass
        item["updated_at"] = int(time.time())
        _save()


def remove_account(account_id) -> bool:
    """删除一个账号；若删的是当前账号，把 current 落到剩下的第一个。"""
    aid = str(account_id or "")
    with _lock:
        if aid not in _state["accounts"]:
            return False
        _state["accounts"].pop(aid, None)
        if _state.get("current") == aid:
            _state["current"] = next(iter(_state["accounts"]), "")
        _save()
        return True


def select(account_id) -> bool:
    """切换当前账号。"""
    aid = str(account_id or "")
    with _lock:
        if aid not in _state["accounts"]:
            return False
        _state["current"] = aid
        _save()
        return True


def clear_all() -> None:
    """清空全部账号（调试用）。"""
    global _state
    with _lock:
        _state = {"current": "", "accounts": {}}
        try:
            _FILE.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------- 读 ----------------


def account_ids() -> list[str]:
    with _lock:
        return list(_state["accounts"].keys())


def current_id() -> str:
    with _lock:
        cur = str(_state.get("current") or "")
        if cur and cur in _state["accounts"]:
            return cur
        return next(iter(_state["accounts"]), "")


def get(account_id=None) -> dict | None:
    """取账号完整记录（含凭证，仅后端内部使用）。account_id 为空则取当前账号。"""
    aid = str(account_id or current_id() or "")
    if not aid:
        return None
    with _lock:
        item = _state["accounts"].get(aid)
        return json.loads(json.dumps(item)) if item else None


def cookie(account_id=None) -> dict:
    item = get(account_id)
    return dict(item.get("cookie") or {}) if item else {}


def cookie_str(account_id=None) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookie(account_id).items())


def logged_in(account_id=None) -> bool:
    """是否已登录：以 ltoken_v2 / cookie_token 为准。"""
    ck = cookie(account_id)
    return bool(ck.get("ltoken_v2") or ck.get("cookie_token") or ck.get("ltoken"))


def has_any() -> bool:
    return bool(account_ids())


# ---------------- 对外（脱敏） ----------------


def _mask(text: str) -> str:
    text = str(text or "")
    if len(text) <= 8:
        return "*" * len(text)
    return text[:4] + "*" * (len(text) - 8) + text[-4:]


def public_accounts() -> list[dict]:
    """给配置页展示的账号列表（不含任何可用凭证）。"""
    cur = current_id()
    with _lock:
        out = []
        for aid, item in _state["accounts"].items():
            ck = item.get("cookie") or {}
            out.append(
                {
                    "account_id": aid,
                    "mid": str(item.get("mid") or aid),
                    "nickname": str(item.get("nickname") or ""),
                    "face": str(item.get("face") or ""),
                    "level": int(item.get("level") or 0),
                    "login_at": int(item.get("login_at") or 0),
                    "expires_at": int(item.get("expires_at") or 0),
                    "has_ltoken": bool(ck.get("ltoken_v2") or ck.get("ltoken")),
                    "has_cookie_token": bool(ck.get("cookie_token") or ck.get("cookie_token_v2")),
                    # 真正的「能否用」：ltoken 或 cookie_token 任一在（两者都在才算完整）
                    "logged": bool(
                        ck.get("ltoken_v2") or ck.get("ltoken")
                        or ck.get("cookie_token") or ck.get("cookie_token_v2")
                    ),
                    # 只有 stoken 没有 ltoken/cookie_token 的账号：角色列表 / 战绩会全挂
                    "incomplete": (
                        bool(ck.get("stoken"))
                        and not (
                            ck.get("ltoken_v2") or ck.get("ltoken")
                            or ck.get("cookie_token") or ck.get("cookie_token_v2")
                        )
                    ),
                    # 抽卡（genAuthKey）必需；旧版扫码登录的账号没有，需重新登录
                    "has_stoken": bool(ck.get("stoken") or ck.get("stoken_v2")),
                    "cookie_keys": sorted(ck.keys()),
                    "ltoken_masked": _mask(ck.get("ltoken_v2") or ck.get("ltoken") or ""),
                    "current": aid == cur,
                }
            )
        return out


def masked() -> dict:
    """整体脱敏状态。"""
    return {
        "logged": bool(current_id()),
        "current": current_id(),
        "count": len(account_ids()),
        "accounts": public_accounts(),
        "file": str(_FILE),
        # 落盘加密自检（密钥来源、文件是密文还是明文），不含任何明文凭证
        "storage": securestore.info(_FILE),
    }
