"""导出文件一次性下载令牌（T07 §4.4 独立通道）

语义：
- 令牌绑定 user_id + 文件路径 + 模块，TTL 默认 10 分钟
- 一次性消费：下载后即失效
- 内存存储（单机足够），进程重启即失效（符合"一次性"语义）
- 每次 issue 时清理过期项

工程师红线：导出文件禁经 serve_upload，防 G6 破口。
"""
from __future__ import annotations

import time
import uuid
import threading
from typing import Optional, Tuple

_DEFAULT_TTL = 600  # 10 分钟


class _TokenEntry:
    __slots__ = ('token', 'user_id', 'file_path', 'module', 'expires_at', 'used')

    def __init__(self, token, user_id, file_path, module, expires_at):
        self.token = token
        self.user_id = user_id
        self.file_path = file_path
        self.module = module
        self.expires_at = expires_at
        self.used = False


class ExportTokenStore:
    """线程安全的内存令牌存储。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens: dict[str, _TokenEntry] = {}

    def issue(self, user_id: int, file_path: str, module: str,
              ttl: int = _DEFAULT_TTL) -> str:
        """生成一次性下载令牌。"""
        self._cleanup_expired()
        token = uuid.uuid4().hex
        entry = _TokenEntry(token, user_id, file_path, module,
                            time.time() + ttl)
        with self._lock:
            self._tokens[token] = entry
        return token

    def verify(self, token: str, user_id: int) -> Tuple[bool, Optional[str], Optional[str]]:
        """校验令牌（不消费）。返回 (ok, file_path, module)。"""
        with self._lock:
            entry = self._tokens.get(token)
        if entry is None:
            return False, None, None
        if entry.used:
            return False, None, None
        if time.time() > entry.expires_at:
            return False, None, None
        if entry.user_id != user_id:
            return False, None, None
        return True, entry.file_path, entry.module

    def consume(self, token: str) -> None:
        """消费令牌（下载后调用，使其失效）。"""
        with self._lock:
            entry = self._tokens.get(token)
            if entry is not None:
                entry.used = True

    def _cleanup_expired(self) -> None:
        """清理过期且已消费的令牌。"""
        now = time.time()
        with self._lock:
            expired = [t for t, e in self._tokens.items()
                       if now > e.expires_at or e.used]
            for t in expired:
                del self._tokens[t]


# 全局单例（单机模式）
export_token_store = ExportTokenStore()
