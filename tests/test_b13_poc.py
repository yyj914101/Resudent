# -*- coding: utf-8 -*-
"""B13 口令通道 PoC 端到端验证（T05 验收，Sprint1 必补项）。

无 M0 原型（详细设计 v1.1 §1.4.3 明确标注），本 PoC 在沙盒用 Flask 测试客户端验证：
1. 主通道 /auth/bootstrap-key：仅回环可访问；令牌 TTL/一次性；口令错误 401 不锁定；成功派生密钥
2. 降级路径 /auth/unlock：仅回环渲染表单；首启设置口令；二次解锁；错误口令不锁定
3. 非回环拦截：remote_addr 非回环 → 503
"""
import os
import sys

os.environ.setdefault("SHUCUN_DATA_DIR", "/tmp/shucun_b13/data")
os.environ.setdefault("SHUCUN_DB_PATH", "/tmp/shucun_b13/shucun.db")
os.environ.setdefault("SHUCUN_UPLOAD_DIR", "/tmp/shucun_b13/uploads")
os.environ.setdefault("SHUCUN_BACKUP_DIR", "/tmp/shucun_b13/backups")
os.environ.setdefault("SHUCUN_LOG_DIR", "/tmp/shucun_b13/logs")
os.environ.setdefault("SHUCUN_TEMP_DIR", "/tmp/shucun_b13/temp")
os.environ.setdefault("SHUCUN_LICENSE_FILE", "/tmp/shucun_b13/license.key")
os.environ.setdefault("SHUCUN_PORT", "5000")
os.environ.setdefault("SHUCUN_SECRET_KEY", "smoke-test-key")

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import config
    from utils.crypto import key_holder, bootstrap_token
    # 隔离 DB 与元数据路径（Windows 兼容：/tmp 路径在 Windows 不可写）
    db_path = str(tmp_path / "shucun.db")
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir, exist_ok=True)
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    monkeypatch.setenv("SHUCUN_DATA_DIR", data_dir)
    monkeypatch.setattr("utils.crypto._default_meta_path", lambda: os.path.join(data_dir, "key_meta.json"))
    monkeypatch.setattr("utils.crypto.bootstrap_token", bootstrap_token)  # 保持单例
    # 必须在 monkeypatch 之后 import app，避免 app.py 模块级 app=create_app() 用旧 DB 路径
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.environ_base.setdefault("REMOTE_ADDR", "127.0.0.1")
        yield c
    # 清理内存态
    key_holder.lock()
    bootstrap_token._token = None


class TestBootstrapKeyMainChannel:
    def test_non_loopback_rejected(self, client, monkeypatch):
        """非回环（已授权 LAN）→ 503 不渲染表单。"""
        monkeypatch.setenv("SHUCUN_LAN_AUTHORIZED", "1")  # 隔离 LAN 403，专测 B13 503
        with client.application.test_client() as c:
            c.environ_base["REMOTE_ADDR"] = "192.168.1.10"
            r = c.get("/auth/bootstrap-key", query_string={"token": "x", "password": "y"})
            assert r.status_code == 503
            assert r.get_json()["success"] is False

    def test_missing_params(self, client):
        r = client.get("/auth/bootstrap-key", query_string={})
        assert r.status_code == 400

    def test_wrong_token_401(self, client):
        from utils.crypto import bootstrap_token
        bootstrap_token.issue()
        r = client.get("/auth/bootstrap-key", query_string={"token": "wrong", "password": "pwd123"})
        assert r.status_code == 401
        assert r.get_json()["success"] is False

    def test_full_flow_first_run(self, client, tmp_path):
        """首启：令牌有效 → 派生密钥 → key_meta 落盘（不含密钥）。"""
        from utils.crypto import bootstrap_token, key_holder
        import json
        tok = bootstrap_token.issue()
        r = client.get("/auth/bootstrap-key", query_string={"token": tok, "password": "启动口令12345"})
        assert r.status_code == 200
        assert r.get_json()["success"] is True
        assert key_holder.is_initialized
        meta_path = os.path.join(tmp_path, "data", "key_meta.json")
        assert os.path.exists(meta_path)
        meta = json.load(open(meta_path, encoding="utf-8"))
        assert "salt_hex" in meta
        assert "password" not in meta and "key" not in meta  # 密钥不落盘

    def test_token_one_time(self, client):
        """令牌一次性：消费后不可重用。"""
        from utils.crypto import bootstrap_token
        tok = bootstrap_token.issue()
        r1 = client.get("/auth/bootstrap-key", query_string={"token": tok, "password": "pwd123456"})
        assert r1.status_code == 200
        r2 = client.get("/auth/bootstrap-key", query_string={"token": tok, "password": "pwd123456"})
        assert r2.status_code == 401  # 已消费


class TestUnlockDegradedPath:
    def test_first_run_page(self, client):
        r = client.get("/auth/unlock")
        assert r.status_code == 200
        assert "设置启动口令" in r.get_data(as_text=True)

    def test_non_loopback_503(self, client, monkeypatch):
        monkeypatch.setenv("SHUCUN_LAN_AUTHORIZED", "1")
        with client.application.test_client() as c:
            c.environ_base["REMOTE_ADDR"] = "10.0.0.5"
            r = c.get("/auth/unlock")
            assert r.status_code == 503

    def test_set_password_then_reenter(self, client, tmp_path):
        """首启设置 → 内存清空（模拟重启）→ 解锁；错误口令不锁定。"""
        from utils.crypto import key_holder
        r = client.post("/auth/unlock", data={"password": "启动口令12345", "confirm_password": "启动口令12345"})
        assert r.status_code == 302  # 成功跳转 dashboard
        assert key_holder.is_initialized
        # 模拟重启：内存清空（key_meta 仍在）
        key_holder.lock()
        # 错误口令 → 不锁定，页面提示
        r2 = client.post("/auth/unlock", data={"password": "错误口令"})
        assert r2.status_code == 200
        assert "口令错误" in r2.get_data(as_text=True)
        assert not key_holder.is_initialized
        # 正确口令 → 解锁成功
        r3 = client.post("/auth/unlock", data={"password": "启动口令12345"})
        assert r3.status_code == 302
        assert key_holder.is_initialized


class TestKeyStatus:
    def test_key_status(self, client):
        from utils.crypto import key_holder
        key_holder.lock()
        r = client.get("/auth/key-status")
        assert r.status_code == 200
        assert r.get_json()["initialized"] is False
