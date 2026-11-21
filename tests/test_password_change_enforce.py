# -*- coding: utf-8 -*-
"""tests/test_password_change_enforce.py — 强制改密全局拦截回归（P1-2 补强）。

覆盖：
  1) must_change_password=True 用户访问业务页 → 302 到改密页
  2) must_change_password=True 用户访问改密页/登出 → 放行（避免死循环）
  3) must_change_password=True 用户 JSON 接口 → 403 + must_change_password 标记
  4) must_change_password=False 用户访问业务页 → 放行
  5) 未登录访问业务页 → 302 到登录页（不受改密拦截，交由权限拦截）
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
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _set_admin_must_change(app, value):
    """在 app context 内设置 admin 的 must_change_password 标记并返回其 id。"""
    from models import db, User
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        admin.must_change_password = value
        db.session.commit()
        return admin.id


def _login_as(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def test_must_change_redirect_to_change_password(tmp_path, monkeypatch):
    """未改密用户访问业务页 → 302 到改密页（而非直接放行）。"""
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    admin_id = _set_admin_must_change(app, True)
    _login_as(client, admin_id)
    r = client.get("/dashboard/")
    assert r.status_code == 302
    assert "/auth/change-password" in r.headers.get("Location", "")


def test_must_change_can_access_change_password(tmp_path, monkeypatch):
    """未改密用户访问改密页/登出 → 放行（避免死循环，能完成改密）。"""
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    admin_id = _set_admin_must_change(app, True)
    _login_as(client, admin_id)
    assert client.get("/auth/change-password").status_code == 200
    assert client.get("/auth/logout").status_code in (200, 302)


def test_must_change_json_returns_403(tmp_path, monkeypatch):
    """未改密用户 AJAX 请求 → 403 JSON，带 must_change_password 标记（供前端跳转）。"""
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    admin_id = _set_admin_must_change(app, True)
    _login_as(client, admin_id)
    r = client.get("/dashboard/", headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 403
    data = r.get_json()
    assert data is not None
    assert data.get("must_change_password") is True


def test_normal_user_not_blocked(tmp_path, monkeypatch):
    """已改密用户访问业务页 → 放行（不受拦截）。"""
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    admin_id = _set_admin_must_change(app, False)
    _login_as(client, admin_id)
    assert client.get("/dashboard/").status_code == 200


def test_anonymous_not_affected(tmp_path, monkeypatch):
    """未登录访问业务页 → 302 到登录页（改密拦截不抢跑，交由权限拦截）。"""
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    r = client.get("/dashboard/")
    assert r.status_code == 302
    assert "/auth/login" in r.headers.get("Location", "")
