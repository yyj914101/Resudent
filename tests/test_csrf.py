# -*- coding: utf-8 -*-
"""tests/test_csrf.py — 全局 CSRF 防护回归测试（P2-1 整改收尾）。

覆盖：
  1) 非回环（局域网）POST 无 token → 400/403 拒绝
  2) 携带有效 token 的 POST → 通过（进入业务逻辑）
  3) JSON 接口 POST（application/json）→ 豁免（CORS 预检保护）
  4) 回环（127.0.0.1）POST 无 token → 豁免（本机通道）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHUCUN_SECRET_KEY", "test")
    # 模拟已授权局域网模式（否则 enforce_lan_license 会先 403，掩盖 CSRF 行为）
    monkeypatch.setenv("SHUCUN_LAN_AUTHORIZED", "1")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


# 非回环（局域网）来源，使 _is_local_request()=False，触发 CSRF 校验
LAN = {"REMOTE_ADDR": "192.168.1.100"}


def test_csrf_rejects_missing_token(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    # GET 渲染登录页 → 生成会话 token
    client.get("/auth/login")
    r = client.post("/auth/login", data={"username": "admin", "password": "x"},
                    environ_base=LAN)
    assert r.status_code in (400, 403)


def test_csrf_accepts_valid_token(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    client.get("/auth/login")
    with client.session_transaction() as sess:
        token = sess["_csrf_token"]
    assert token
    r = client.post("/auth/login",
                    data={"username": "admin", "password": "wrong", "csrf_token": token},
                    environ_base=LAN)
    # CSRF 通过后进入登录逻辑（密码错误 → 仍返回 200 登录页）
    assert r.status_code == 200


def test_csrf_exempts_json_api(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    client.get("/auth/login")
    # JSON 接口（application/json）豁免，进入登录逻辑返回 200
    r = client.post("/auth/login", json={"username": "admin", "password": "x"},
                    environ_base=LAN)
    assert r.status_code == 200


def test_csrf_exempts_loopback(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    client.get("/auth/login")
    # 默认 test client remote_addr 为 127.0.0.1（回环），无 token 应放行
    r = client.post("/auth/login", data={"username": "admin", "password": "x"})
    assert r.status_code == 200
