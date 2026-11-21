# -*- coding: utf-8 -*-
"""utils.crypto — 字段级 Fernet 加密管线（R12 落地，T05）。

规格依据（详细设计 v1.1 §1.4 + PRD v1.2.3 R12 + 宪法 §1 红线）：
- Fernet（AES128-CTR + HMAC-SHA256），字段级加密存储
- 启动口令 → PBKDF2-HMAC-SHA256（310k 次 / 16 字节盐）→ 32 字节 → url-safe base64 Fernet key
- 密钥不落盘：key_meta.json 仅存盐与元信息，不含密钥/口令
- 等值检索走 SHA-256 哈希索引（密文不可 LIKE）
- 前缀桶哈希供部分匹配（应用层二次解密过滤）

移植自 m0-verification/m0lib/crypto_utils.py（M0 P-09/D1 已 PASS），
接口向后兼容 + 增加 KeyHolder（进程内存持有）与 check_token（B13 口令通道）。

实测数字锚点（引用一律以此为准）：PBKDF2 310k ≈162.6ms；
phone(11)→密文 100 字符；id_card(18)/bank_card(19)→密文 120 字符；
加密写入 ≈1.4 万条/s；哈希等值检索 <0.1ms。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import threading
import time
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# D1 拍板：PBKDF2-HMAC-SHA256 迭代 310,000 次（实测 ≈162.6ms，启动一次可接受）
PBKDF2_ITERATIONS_DEFAULT = 310_000
SALT_LENGTH = 16
KEY_LENGTH = 32
TOKEN_TTL_SECONDS = 600          # B13 令牌 TTL ≤10min
TOKEN_RETRY_MAX_ATTEMPTS = 5     # 端点级指数退避上限
TOKEN_RETRY_BASE_DELAY = 0.5     # 指数退避基数（秒）

# ---------------------------------------------------------------
# 纯函数层（与 m0lib 兼容）
# ---------------------------------------------------------------


def generate_fernet_key() -> str:
    """生成随机 Fernet key（url-safe base64，44 字符，str）。"""
    return Fernet.generate_key().decode("ascii")


def generate_salt() -> bytes:
    """生成 16 字节随机盐（盐非机密、随库归档）。"""
    return os.urandom(SALT_LENGTH)


def derive_fernet_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS_DEFAULT) -> bytes:
    """用户启动口令 → PBKDF2-HMAC-SHA256 → 32 字节 → url-safe base64 Fernet key（bytes）。

    同口令+同盐 → 密钥稳定；不同口令/不同盐 → 密钥不同（D1 已验证）。
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=iterations,
    )
    raw = kdf.derive(password.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _fernet(key) -> Fernet:
    """key 兼容 str（44 字符）或 bytes（url-safe base64）。"""
    if isinstance(key, str):
        key = key.encode("ascii")
    return Fernet(key)


def encrypt_value(key, value: str) -> str:
    """Fernet 加密字符串 → 密文字符串。"""
    return _fernet(key).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_value(key, token: str) -> str:
    """Fernet 解密密文字符串 → 明文。错误密钥抛 InvalidToken（明文不可恢复）。"""
    return _fernet(key).decrypt(token.encode("ascii")).decode("utf-8")


def sha256_hash(value: str) -> str:
    """SHA-256 哈希索引（整值等值检索/唯一约束，密文不可 LIKE 故走哈希）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_prefix_hash(value: str, n: int) -> str:
    """前缀桶哈希：取明文前 n 位做 SHA-256（部分匹配候选集，须应用层二次解密过滤）。"""
    return hashlib.sha256(value[:n].encode("utf-8")).hexdigest()


def fernet_ciphertext_length(plain_len: int) -> int:
    """Fernet token 密文长度估算（含 8 字节时间戳，PKCS7 补齐）。

    结构：version(1) + timestamp(8) + IV(16) + ciphertext(PKCS7) + HMAC(32)，
    再经 base64url（4 字节一组，向上取整）。
    """
    padded = (plain_len // 16 + 1) * 16  # PKCS7：至少补 1 字节
    raw = 1 + 8 + 16 + padded + 32
    return ((raw + 2) // 3) * 4  # ceil(raw/3)*4


# ---- 文件级分块 Fernet（T20-1：.wbak 加密，防大包 OOM） ----

_FILE_CHUNK = 1024 * 1024  # 1MB/块


def encrypt_bytes_stream(key, data: bytes) -> bytes:
    """分块 Fernet 加密任意字节流 → 密文 bytes（4 字节块数前缀 + 各块 token）。"""
    f = _fernet(key)
    n = max(1, (len(data) + _FILE_CHUNK - 1) // _FILE_CHUNK)
    out = bytearray(struct.pack('>I', n))
    off = 0
    for _ in range(n):
        chunk = data[off:off + _FILE_CHUNK]
        out += f.encrypt(chunk)
        off += len(chunk)
    return bytes(out)


def decrypt_bytes_stream(key, token: bytes) -> bytes:
    """分块 Fernet 解密（encrypt_bytes_stream 逆操作）。错误密钥抛 InvalidToken。"""
    f = _fernet(key)
    if len(token) < 4:
        raise InvalidToken('密文长度不足')
    n = struct.unpack('>I', token[:4])[0]
    body = token[4:]
    ct_full = fernet_ciphertext_length(_FILE_CHUNK)
    out = bytearray()
    off = 0
    for i in range(n):
        ct = body[off:off + ct_full] if i < n - 1 else body[off:]
        out += f.decrypt(ct)
        off += len(ct)
    return bytes(out)


# ---------------------------------------------------------------
# KeyHolder — 进程内存密钥持有（密钥不落盘红线）
# ---------------------------------------------------------------


class KeyHolder:
    """进程内唯一密钥持有者。

    - 启动口令经 PBKDF2 派生密钥，密钥仅存内存（self._key）
    - key_meta.json 仅存 salt/iterations/created_at/version，绝不含密钥与口令
    - 线程安全：初始化与解锁加锁；未解锁前 encrypt/decrypt 抛 CryptoUninitialized
    """

    def __init__(self, meta_path: Optional[str] = None):
        self._key: Optional[bytes] = None
        self._lock = threading.Lock()
        self.meta_path = meta_path or _default_meta_path()

    # ---- 状态 ----

    @property
    def is_initialized(self) -> bool:
        return self._key is not None

    # ---- 初始化 / 解锁 ----

    # 自校验标记：首启时用真实密钥加密存档，解锁时用派生密钥解密比对
    _CHECK_MARKER = "shucun-key-check-v1"

    def initialize(self, password: str, salt: Optional[bytes] = None,
                   iterations: int = PBKDF2_ITERATIONS_DEFAULT) -> dict:
        """首启：生成盐并派生密钥（当无 key_meta 时调用）。返回 key_meta 字典。"""
        with self._lock:
            if salt is None:
                salt = generate_salt()
            key = derive_fernet_key(password, salt, iterations)
            self._key = key
            check_token = encrypt_value(key, self._CHECK_MARKER)
            meta = {
                "version": 1,
                "salt_hex": salt.hex(),
                "iterations": iterations,
                "check_token": check_token,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            return meta

    def unlock(self, password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS_DEFAULT,
               check_token: Optional[str] = None) -> bool:
        """解锁：用口令 + 归档盐派生密钥，解密归档自校验标记比对。

        口令错误 → 解密失败/不匹配 → 返回 False（不抛错不锁定，态③）。
        """
        with self._lock:
            key = derive_fernet_key(password, salt, iterations)
            # 校验：用派生密钥解密归档标记，成功且匹配即口令正确
            try:
                if decrypt_value(key, check_token or "") != self._CHECK_MARKER:
                    return False
            except Exception:
                return False
            self._key = key
            return True

    def lock(self) -> None:
        """锁定：清空内存密钥（口令遗忘恢复/管理员锁定用）。"""
        with self._lock:
            self._key = None

    # ---- 加解密 ----

    def encrypt(self, plaintext: str) -> str:
        key = self._require_key()
        return encrypt_value(key, plaintext)

    def decrypt(self, ciphertext: str) -> str:
        key = self._require_key()
        return decrypt_value(key, ciphertext)

    # ---- 文件级字节接口（T20-1：.wbak 加密备份，密钥仍不落盘） ----

    def get_key_bytes(self) -> bytes:
        """返回内存密钥（进程内持有；供文件级分块加密）。"""
        return self._require_key()

    def encrypt_bytes(self, data: bytes) -> bytes:
        return encrypt_bytes_stream(self._require_key(), data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        return decrypt_bytes_stream(self._require_key(), token)

    # ---- 内部 ----

    def _require_key(self) -> bytes:
        with self._lock:
            if self._key is None:
                raise CryptoUninitialized("密钥未初始化：请先设置/输入启动口令")
            return self._key


class CryptoUninitialized(Exception):
    """密钥未初始化/已锁定。"""


# ---------------------------------------------------------------
# 元信息持久化（key_meta.json 管理，不含密钥）
# ---------------------------------------------------------------


def _default_meta_path() -> str:
    data_dir = os.environ.get("SHUCUN_DATA_DIR", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(data_dir, "key_meta.json")


def save_key_meta(meta: dict, meta_path: Optional[str] = None) -> None:
    path = meta_path or _default_meta_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def load_key_meta(meta_path: Optional[str] = None) -> Optional[dict]:
    path = meta_path or _default_meta_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------
# B13 口令通道 — 一次性引导令牌（主通道 + 降级路径共用）
# ---------------------------------------------------------------


class BootstrapToken:
    """一次性引导令牌：launcher 持有，/auth/bootstrap-key 校验。

    - 令牌仅内存态（服务重启需重新输入口令，D1 口径）
    - TTL ≤10min；校验成功后立即作废（一次性）
    - 端点级指数退避由路由层配合（见 _retry_delay）
    """

    def __init__(self, ttl: int = TOKEN_TTL_SECONDS):
        self._token: Optional[str] = None
        self._created: float = 0.0
        self._ttl = ttl
        self._lock = threading.Lock()

    def issue(self) -> str:
        with self._lock:
            self._token = os.urandom(16).hex()
            self._created = time.time()
            return self._token

    def check(self, token: str) -> bool:
        """校验并一次性消费。过期/不匹配/已消费 → False。"""
        with self._lock:
            if self._token is None:
                return False
            if time.time() - self._created > self._ttl:
                self._token = None
                return False
            ok = hmac.compare_digest(self._token, token or "")
            if ok:
                self._token = None  # 一次性
            return ok

    @property
    def is_alive(self) -> bool:
        return self._token is not None and (time.time() - self._created <= self._ttl)


def retry_delay(attempt: int, base: float = TOKEN_RETRY_BASE_DELAY) -> float:
    """端点级指数退避：attempt 从 0 起，返回 base * 2^attempt，封顶 30s。"""
    return min(base * (2 ** max(attempt, 0)), 30.0)


# 模块级单例（应用内共享）
key_holder = KeyHolder()
bootstrap_token = BootstrapToken()
