# -*- coding: utf-8 -*-
"""tests/test_crypto.py — utils.crypto 单元测试（T05 验收）。

覆盖：
- 纯函数：加解密往返、密文长度锚点、哈希索引、PBKDF2 派生稳定性
- KeyHolder：首启初始化、解锁、口令错误不锁定、未初始化抛错、自校验
- BootstrapToken：一次性、TTL、防重放
- retry_delay 指数退避
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.crypto import (
    PBKDF2_ITERATIONS_DEFAULT,
    BootstrapToken,
    CryptoUninitialized,
    KeyHolder,
    bootstrap_token,
    decrypt_value,
    derive_fernet_key,
    encrypt_value,
    fernet_ciphertext_length,
    generate_fernet_key,
    generate_salt,
    key_holder,
    retry_delay,
    sha256_hash,
    sha256_prefix_hash,
)


class TestPureFunctions:
    def test_encrypt_decrypt_roundtrip(self):
        key = generate_fernet_key()
        plain = "居民张三 510105199001010011"
        enc = encrypt_value(key, plain)
        assert enc != plain
        assert decrypt_value(key, enc) == plain

    def test_wrong_key_raises(self):
        k1 = generate_fernet_key()
        k2 = generate_fernet_key()
        enc = encrypt_value(k1, "机密")
        with pytest.raises(Exception):
            decrypt_value(k2, enc)

    def test_ciphertext_length_anchors(self):
        # M0 数字锚点：phone(11)→100；id_card(18)/bank_card(19)→120
        assert fernet_ciphertext_length(11) == 100
        assert fernet_ciphertext_length(18) == 120
        assert fernet_ciphertext_length(19) == 120

    def test_sha256_hash_deterministic(self):
        v = "510105199001010011"
        assert sha256_hash(v) == sha256_hash(v)
        assert len(sha256_hash(v)) == 64
        assert sha256_hash(v) != sha256_hash(v[:-1] + "2")

    def test_sha256_prefix_hash(self):
        v = "510105"
        assert sha256_prefix_hash(v, 4) == sha256_prefix_hash(v, 4)
        assert sha256_prefix_hash(v, 4) != sha256_prefix_hash(v, 5)

    def test_pbkdf2_stable_and_distinct(self):
        salt = generate_salt()
        k1 = derive_fernet_key("口令123", salt)
        k2 = derive_fernet_key("口令123", salt)
        k3 = derive_fernet_key("口令456", salt)
        k4 = derive_fernet_key("口令123", generate_salt())
        assert k1 == k2
        assert k1 != k3
        assert k1 != k4

    def test_pbkdf2_default_iterations(self):
        assert PBKDF2_ITERATIONS_DEFAULT == 310_000


class TestKeyHolder:
    def test_uninitialized_raises(self, tmp_path):
        kh = KeyHolder(meta_path=str(tmp_path / "meta.json"))
        with pytest.raises(CryptoUninitialized):
            kh.encrypt("x")

    def test_initialize_then_encrypt_decrypt(self, tmp_path):
        kh = KeyHolder(meta_path=str(tmp_path / "meta.json"))
        meta = kh.initialize("启动口令", salt=generate_salt())
        assert meta["salt_hex"]
        assert "key" not in meta and "password" not in meta  # 密钥不落盘
        enc = kh.encrypt("敏感字段")
        assert kh.decrypt(enc) == "敏感字段"

    def test_unlock_wrong_password_no_lock(self, tmp_path):
        # B13 真实语义：服务重启后密钥不在内存（D1），须重新输入口令解锁
        kh = KeyHolder(meta_path=str(tmp_path / "meta.json"))
        salt = generate_salt()
        meta = kh.initialize("正确口令", salt=salt)
        kh.lock()  # 模拟进程重启后内存密钥清空
        check_token = meta["check_token"]
        # 错误口令 → 解锁失败，但仍可再试（不锁定）
        assert not kh.unlock("错误口令", salt, check_token=check_token)
        assert not kh.is_initialized
        # 正确口令 → 成功
        assert kh.unlock("正确口令", salt, check_token=check_token)
        assert kh.is_initialized

    def test_lock_clears(self, tmp_path):
        kh = KeyHolder(meta_path=str(tmp_path / "meta.json"))
        salt = generate_salt()
        meta = kh.initialize("口令", salt=salt)
        enc = kh.encrypt("数据")
        kh.lock()
        with pytest.raises(CryptoUninitialized):
            kh.decrypt(enc)
        assert kh.unlock("口令", salt, check_token=meta["check_token"])


class TestBootstrapToken:
    def test_one_time(self):
        bt = BootstrapToken(ttl=600)
        tok = bt.issue()
        assert bt.check(tok)
        assert not bt.check(tok)  # 一次性

    def test_ttl_expiry(self):
        bt = BootstrapToken(ttl=1)
        tok = bt.issue()
        time.sleep(1.1)
        assert not bt.check(tok)

    def test_wrong_token(self):
        bt = BootstrapToken(ttl=600)
        bt.issue()
        assert not bt.check("wrong")

    def test_retry_delay_exponential(self):
        assert retry_delay(0) == 0.5
        assert retry_delay(1) == 1.0
        assert retry_delay(2) == 2.0
        assert retry_delay(10) == 30.0  # 封顶
