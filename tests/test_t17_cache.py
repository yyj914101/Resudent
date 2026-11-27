# -*- coding: utf-8 -*-
"""T17-2 agg_cache 单测（Sprint4 M4.3 里程碑）。

覆盖（详细设计 §5.2 L961）：
- TTL 行为（过期即 miss，monotonic 时钟）
- key = 口径版本+参数（CACHE_VERSION='v1.1' 内嵌，不同参数不同 key）
- 「新登记困境儿童轮询 ≤60s 自动 +1」验收边界钉住（TTL 到期自然刷新，写操作不失效）
- 缓存内容仅计数、不含敏感明文
"""
import os
import sys
import time
from datetime import date

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
    monkeypatch.setenv("SHUCUN_LAN_AUTHORIZED", "1")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    monkeypatch.setattr(config.Config, "IMPORTS_DIR", str(tmp_path / "temp" / "imports"))
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def _years_ago(n):
    return date.today().replace(year=date.today().year - n)


def _make_resident(app, name, birth_date):
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status='normal',
                     flow_status='常住', gender='男', village_group='第1组')
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_tag(app, resident_id, code):
    from models import db, ResidentTag
    with app.app_context():
        t = ResidentTag(resident_id=resident_id, tag_code=code, status='有效', source='自动')
        db.session.add(t)
        db.session.commit()


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    from utils.agg_cache import invalidate
    invalidate()
    yield
    invalidate()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    _unlock_keyholder()
    return _make_app(tmp_path, monkeypatch)


def test_ttl_expiry():
    """TTL 内命中、过期后 miss（短 TTL，不真睡 45s）。"""
    from utils.agg_cache import put, get, make_key
    k = make_key('t', '1')
    put(k, {'n': 1}, ttl=1)
    assert get(k) == {'n': 1}
    time.sleep(1.1)
    assert get(k) is None


def test_key_version_and_parts():
    """key = 口径版本+参数：含 v1.1；参数不同 key 不同。"""
    from utils.agg_cache import make_key, CACHE_VERSION
    assert make_key('dashboard', 'counts') == f'{CACHE_VERSION}:dashboard:counts'
    assert make_key('a', 'b') != make_key('a', 'c')


def test_dashboard_cache_refresh_bound(app, monkeypatch):
    """验收 4.2#3 钉住：新登记困境儿童 TTL 内不变、到期自然刷新 +1。"""
    from utils import agg_cache
    from utils.agg_cache import invalidate

    for i in range(8):
        rid = _make_resident(app, f'困境{i}', _years_ago(8))
        _make_tag(app, rid, '困境儿童')

    client = _admin_client(app)
    d1 = client.get('/dashboard/api/dashboard/stats').get_json()['data']
    assert d1['distressed_child_count'] == 8

    # 新登记第 9 名：TTL 内缓存未失效，仍显示 8（写操作不主动失效，详细设计 L961）
    rid9 = _make_resident(app, '困境9', _years_ago(7))
    _make_tag(app, rid9, '困境儿童')
    d2 = client.get('/dashboard/api/dashboard/stats').get_json()['data']
    assert d2['distressed_child_count'] == 8, 'TTL 内应仍为 8（写操作不主动失效）'

    # 模拟 TTL 自然到期（等价验证：把已缓存条目 deadline 缩短到 0.1s 后，到期自动刷新 +1）
    key = agg_cache.make_key('dashboard', 'counts')
    with agg_cache._lock:
        if key in agg_cache._store:
            _, val = agg_cache._store[key]
            agg_cache._store[key] = (time.monotonic() + 0.1, val)
    time.sleep(0.2)
    d3 = client.get('/dashboard/api/dashboard/stats').get_json()['data']
    assert d3['distressed_child_count'] == 9, 'TTL 过期自然刷新应自动 +1 到 9'
    invalidate()


def test_cache_no_plaintext(app):
    """缓存内容仅计数，不含敏感明文 key。"""
    from utils.agg_cache import compute_dashboard_counts
    with app.app_context():
        counts = compute_dashboard_counts()
    text = str(counts)
    for word in ('id_card', 'phone', 'bank_card', '身份证', '手机号'):
        assert word not in text, f'缓存不应含敏感字段: {word}'
