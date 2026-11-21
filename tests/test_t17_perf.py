# -*- coding: utf-8 -*-
"""T17-1 首页聚合性能压测（Sprint4 M4.3 里程碑，标记 RUN_PERF 不纳入默认回归）。

验收（详细设计 §8 L1156 5000 备数口径 + Sprint4 计划 §4.3）：首页聚合 P95 <2s。
方法：造 5000 居民（含高龄/标签/补贴记录），对 /dashboard/api/dashboard/stats 测 P95 响应时间。
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


@pytest.mark.skipif(
    os.environ.get('RUN_PERF') != '1',
    reason='性能压测默认跳过（5000 人规模，RUN_PERF=1 启用）'
)
def test_dashboard_p95_under_2s(tmp_path, monkeypatch):
    """5000 备数首页聚合 P95 <2s（详细设计 L1156 口径，替代原 3000）。"""
    from models import db, Resident, SubsidyProject, SubsidyRecord, ResidentTag
    from utils.agg_cache import invalidate

    app = _make_app(tmp_path, monkeypatch)
    _unlock_keyholder()
    invalidate()

    N = 5000  # 5000 人上限备数（L5 闭环）

    with app.app_context():
        p = SubsidyProject(name='高龄津贴', project_type='高龄津贴', cycle='月',
                           default_amount=100, status='启用')
        db.session.add(p)
        db.session.commit()
        pid = p.id

        residents = []
        for i in range(N):
            # 前 500 名高龄（80 岁+），其余 40 岁；标签 200 困难/150 留守/60 困境
            bd = _years_ago(81) if i < 500 else _years_ago(40)
            r = Resident(name=f'居民{i}', birth_date=bd, status='normal',
                         flow_status='常住', gender='男' if i % 2 == 0 else '女',
                         village_group=f'第{i % 8 + 1}组')
            residents.append(r)
        db.session.add_all(residents)
        db.session.commit()

        tags = []
        for i in range(200):
            tags.append(ResidentTag(resident_id=residents[i].id, tag_code='困难老人',
                                    status='有效', source='自动'))
        for i in range(150):
            tags.append(ResidentTag(resident_id=residents[500 + i].id, tag_code='留守儿童',
                                    status='有效', source='自动'))
        for i in range(60):
            tags.append(ResidentTag(resident_id=residents[650 + i].id, tag_code='困境儿童',
                                    status='有效', source='自动'))
        db.session.add_all(tags)
        db.session.commit()

        recs = []
        for r in residents[:2000]:  # 2000 人各 2 笔补贴记录
            for j in range(2):
                recs.append(SubsidyRecord(
                    resident_id=r.id, project_id=pid, issue_date=date.today(),
                    period=f'{date.today().year:04d}-0{8 - j}', amount=100 + j * 10,
                    channel='镇级直发', source='normal', status='正常'))
        db.session.add_all(recs)
        db.session.commit()

    client = _admin_client(app)

    latencies = []
    SAMPLES = 30
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        resp = client.get('/dashboard/api/dashboard/stats')
        elapsed = (time.perf_counter() - t0) * 1000  # ms
        assert resp.status_code == 200
        data = resp.get_json()['data']
        # 命中缓存后数字仍正确（防缓存空壳）
        assert data['total_population'] == N
        assert data['elderly_count'] == 500
        latencies.append(elapsed)

    latencies.sort()
    p95 = latencies[int(SAMPLES * 0.95) - 1] if SAMPLES >= 20 else latencies[-1]
    avg = sum(latencies) / len(latencies)

    assert p95 < 2000, f"首页聚合 P95={p95:.1f}ms 超过 2000ms 阈值（avg={avg:.1f}ms，N={N}）"
