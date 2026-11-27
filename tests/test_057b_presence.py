# -*- coding: utf-8 -*-
"""tests/test_057b_presence.py — 心跳/在线人数（057b）回归测试。

覆盖：
  1) 未登录（JSON/XHR）→ 401：PUBLIC_ENDPOINTS 豁免 + @login_required 端点内自校验（ADR-016）
  2) heartbeat 刷新当前用户 last_active
  3) online-count 仅统计 now - last_active < 60s 的用户
  4) 归类自检：端点已列入 PUBLIC_ENDPOINTS（漏归 = 全端点 403）
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    for k, v in {
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_LAN_AUTHORIZED": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _login(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # 首装种子 admin


def _set_last_active(app, user_id, dt):
    from models import db, User
    with app.app_context():
        User.query.filter_by(id=user_id).update({'last_active': dt})
        db.session.commit()


XHR = {"X-Requested-With": "XMLHttpRequest"}


def test_presence_requires_login(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    assert client.post('/api/heartbeat', json={}).status_code == 401
    assert client.get('/api/online-count', headers=XHR).status_code == 401


def test_heartbeat_updates_last_active(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    _login(client)
    _set_last_active(app, 1, datetime.now() - timedelta(minutes=10))
    r = client.post('/api/heartbeat', json={})
    assert r.status_code == 200
    assert r.get_json()['success'] is True
    from models import User
    with app.app_context():
        fresh = User.query.get(1).last_active
    assert (datetime.now() - fresh).total_seconds() < 5


def test_online_count_window(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    _login(client)
    now = datetime.now()
    _set_last_active(app, 1, now - timedelta(seconds=10))  # 窗口内 → 在线
    body = client.get('/api/online-count', headers=XHR).get_json()
    assert body['success'] is True and body['online'] == 1
    _set_last_active(app, 1, now - timedelta(seconds=90))  # 窗口外 → 不计
    assert client.get('/api/online-count', headers=XHR).get_json()['online'] == 0


def test_presence_endpoints_classified():
    from utils.permissions import PUBLIC_ENDPOINTS
    assert 'presence.heartbeat' in PUBLIC_ENDPOINTS
    assert 'presence.online_count' in PUBLIC_ENDPOINTS
