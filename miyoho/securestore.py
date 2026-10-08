"""本地敏感数据的落盘加密（Fernet = AES-128-CBC + HMAC-SHA256）。

**为什么要有它**：内置插件与市场插件会把登录凭证落在 `data/*.json` 里
（B 站 `bilibili_auth.json` 的 SESSDATA / bili_jct，米游社 `miyoushe_accounts.json`
的 ltoken / stoken / cookie_token）。原先这些都是**明文 JSON** —— 谁拿到文件，
谁就等于拿到那几个账号的登录态。

**方案**
- 算法：`cryptography` 的 Fernet（AES-128-CBC + HMAC-SHA256，密文自带随机 IV 与
  时间戳，能防篡改）。依赖本机已有，不需要额外装。
- 加密粒度：**整个文件**（调用方传进来的整个 dict 序列化后一次加密）。新增敏感字段
  不用改这里，也不会漏。
- 文件格式（就是把普通 JSON 换成一个信封）：

      {"_enc": 1, "k": "file" | "env", "data": "<fernet token>"}

  信封里记着这把密文是用哪个来源的密钥加的，读取时照着选，两个来源可混用。

**密钥从哪来**（二选一，优先环境变量）
1. 环境变量 ``MIYOHO_AUTH_KEY``（写在 `.env` 里）：任意字符串，经 scrypt 派生 32 字节。
   好处是跟着 `.env` 一起备份/迁移，密钥由你掌握。
2. 自动密钥文件 ``data/.auth_key``：首次使用时随机生成 32 字节并落盘（尝试 0600）。
   零配置；整个 `data/` 连它一起搬走，到新机器照样能读。

**边界（别当成万能锁）**
- ✅ 挡得住「密文单独泄漏」：`data/` 被云盘同步走、整个目录打包外发、误
  `git add -f data/`、别人远程翻你的文件 —— 只拿到密文，没密钥没用。
- ❌ 挡不住「能读你磁盘的人」：密钥就在同一台机器上。真要为这种情况设防，
  得上 DPAPI（绑定 Windows 账户）或启动口令，代价是换机要重新登录。
- ⚠️ Windows 上 `os.chmod(0o600)` 基本是安慰剂（NTFS 下 Python 的 chmod 只改只读位），
  别指望它隔离同机其他用户。

**用法**（调用方只需把 `json.load/dump` 换掉）

    from miyoho import securestore
    data = securestore.load(path)      # 不存在 → None；旧明文 → 读出来并顺手加密写回
    securestore.dump(path, data)       # 原子写：先写 .tmp 再 os.replace

**兼容与容错**
- `load()` 遇到**旧明文**文件会原样返回并立刻加密写回（透明迁移，不用手工搬）。
- 解密失败（密钥被换/被删）**不抛给调用方**，返回 None 并记 error 日志：
  bot 还能起来，重新扫码登录即可；文件本身没被动，密钥找回来就能读回。
- 缺少 `cryptography`，或显式设置 `MIYOHO_AUTH_DISABLE=1`，则整体退回明文（并告警）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path

try:  # NoneBot 环境用它的 logger，独立跑脚本时退回标准库
    from loguru import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("securestore")

#: 密钥来源一：环境变量（写在 .env）
ENV_KEY = "MIYOHO_AUTH_KEY"
#: 应急开关：显式设为 1 则退回明文（密钥丢了又不想重新登录时用）
ENV_DISABLE = "MIYOHO_AUTH_DISABLE"
#: 密钥来源二：自动生成的密钥文件（相对仓库根，与 data/ 下其它数据同级）
from .paths import data_path
KEY_FILE = data_path(".auth_key")

_MARK = 1
_KDF_SALT = b"astrbot-plugin-miyoho/securestore/v1"

_lock = threading.RLock()
_cache: dict[str, bytes] = {}
_fernet_mod = None
_fernet_tried = False
_last_error = ""


class SecureStoreError(RuntimeError):
    """密钥不对、密文损坏或环境不支持加解密。"""


# ---------------- 可用性 ----------------


def _import_fernet():
    """惰性导入 cryptography（没装也不该让插件 import 失败）。"""
    global _fernet_mod, _fernet_tried
    if not _fernet_tried:
        _fernet_tried = True
        try:
            from cryptography.fernet import Fernet, InvalidToken

            _fernet_mod = (Fernet, InvalidToken)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"敏感数据加密不可用（未安装 cryptography：{exc}），将退回明文存储"
            )
            _fernet_mod = None
    return _fernet_mod


def disabled_by_env() -> bool:
    return str(os.environ.get(ENV_DISABLE) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def enabled() -> bool:
    """是否真的在加密。"""
    if disabled_by_env():
        return False
    return _import_fernet() is not None


def using_env_key() -> bool:
    return bool(str(os.environ.get(ENV_KEY) or "").strip())


def key_source() -> str:
    """当前会用的密钥来源：``env``（.env 里的 MIYOHO_AUTH_KEY）或 ``file``（自动密钥文件）。"""
    return "env" if using_env_key() else "file"


# ---------------- 密钥 ----------------


def _env_key() -> bytes:
    secret = str(os.environ.get(ENV_KEY) or "").strip()
    raw = hashlib.scrypt(
        secret.encode("utf-8"), salt=_KDF_SALT, n=2**14, r=8, p=1, dklen=32
    )
    return base64.urlsafe_b64encode(raw)


def _file_key() -> bytes:
    with _lock:
        try:
            text = KEY_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            return text.encode("ascii")
        key = base64.urlsafe_b64encode(secrets.token_bytes(32))
        try:
            KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            KEY_FILE.write_text(key.decode("ascii"), encoding="utf-8")
            try:
                os.chmod(KEY_FILE, 0o600)
            except OSError:
                pass
        except OSError as exc:
            raise SecureStoreError(f"密钥文件写入失败（{KEY_FILE}）：{exc}") from exc
        logger.info(
            f"已生成敏感数据加密密钥 {KEY_FILE} —— 备份/迁移 data/ 时请把它一起带走，"
            f"丢了就只能重新登录。"
        )
        return key


def _key(source: str) -> bytes:
    with _lock:
        if source not in _cache:
            _cache[source] = _env_key() if source == "env" else _file_key()
        return _cache[source]


# ---------------- 加解密 ----------------


def is_envelope(obj) -> bool:
    """判断读到的 JSON 是不是「加密信封」。"""
    return isinstance(obj, dict) and obj.get("_enc") == _MARK and "data" in obj


def encrypt(data):
    """把任意可 JSON 化的对象封成信封；不支持加密时原样返回。"""
    mod = _import_fernet()
    if mod is None or disabled_by_env():
        return data
    Fernet, _ = mod
    source = key_source()
    token = Fernet(_key(source)).encrypt(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return {"_enc": _MARK, "k": source, "data": token.decode("ascii")}


def decrypt(payload: dict):
    """拆信封。失败抛 SecureStoreError（调用方一般用 load()，不用直接调这个）。"""
    mod = _import_fernet()
    if mod is None:
        raise SecureStoreError("本机没有 cryptography，无法解密")
    Fernet, InvalidToken = mod
    source = "env" if str(payload.get("k") or "") == "env" else "file"
    try:
        raw = Fernet(_key(source)).decrypt(str(payload.get("data") or "").encode("ascii"))
    except InvalidToken as exc:
        raise SecureStoreError("密钥不匹配") from exc
    except SecureStoreError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 任何解析异常都归成「解不开」
        raise SecureStoreError(str(exc)) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecureStoreError(f"明文不是合法 JSON：{exc}") from exc


# ---------------- 文件读写（调用方用这两个） ----------------


def dump(path, data) -> None:
    """加密后原子写盘（先写 .tmp 再替换，避免中途崩掉留下半截文件）。"""
    path = Path(path)
    payload = encrypt(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def load(path):
    """读回数据（密文/旧明文都能读）。

    - 文件不存在 → ``None``；
    - 读到旧明文 → 原样返回，**并立刻加密写回**（透明迁移）；
    - 解密失败 → 记 error 日志并返回 ``None``（不抛，保持 bot 能启动）。
    """
    global _last_error
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        _last_error = f"不是合法 JSON：{exc}"
        logger.warning(f"敏感数据读取失败（{path}）：{_last_error}")
        return None

    if not is_envelope(obj):
        if isinstance(obj, dict) and enabled():
            try:
                dump(path, obj)
                logger.info(f"敏感数据已加密落盘：{path}（原为明文）")
            except OSError as exc:  # noqa: BLE001
                logger.warning(f"敏感数据加密写回失败（{path}）：{exc}")
        return obj

    try:
        return decrypt(obj)
    except SecureStoreError as exc:
        _last_error = str(exc)
        logger.error(
            f"敏感数据解密失败（{path}）：{_last_error} —— 多半是加密密钥变了"
            f"（{KEY_FILE} 被删/被换，或 {ENV_KEY} 改过）。"
            f"文件没被动过，把密钥找回来即可读回；否则重新扫码登录一次。"
        )
        return None


# ---------------- 给配置页看的自检信息 ----------------


def last_error() -> str:
    return _last_error


def info(path) -> dict:
    """加密状态自检（**不含任何明文**），供配置页展示。"""
    path = Path(path)
    state = "missing"
    if path.exists():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            state = "encrypted" if is_envelope(obj) else "plaintext"
        except Exception:  # noqa: BLE001
            state = "broken"
    return {
        "enabled": enabled(),
        "state": state,
        "key_source": key_source() if enabled() else "",
        "key_file": str(KEY_FILE),
        "key_file_exists": KEY_FILE.exists(),
        "env_var": ENV_KEY,
        "file": str(path),
        "last_error": _last_error,
    }
