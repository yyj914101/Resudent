# -*- coding: utf-8 -*-
"""T14-6 个人补贴总览性能压测（Sprint3 第三批，标记 slow 不纳入默认回归）。

验收（详细设计 §8 L1156 + Sprint3 计划 §3.6）：总览 P95 <2s（3000 人规模，5000 备数口径）。
方法：造 3000 居民 + 各 3 笔 subsidy_record，对 subsidy_overview 端点测 P95 响应时间。
"""
import os
import sys
import time

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
    from datetime import date
    return date.today().replace(year=date.today().year - n)


@pytest.mark.skipif(
    os.environ.get('RUN_PERF') != '1',
    reason='性能压测默认跳过（3000 人规模，RUN_PERF=1 启用）'
)
def test_overview_p95_under_2s(tmp_path, monkeypatch):
    """3000 居民规模总览 P95 <2s。"""
    from models import db, Resident, SubsidyProject, SubsidyRecord
    from utils.crypto import key_holder, sha256_hash, sha256_prefix_hash

    app = _make_app(tmp_path, monkeypatch)
    _unlock_keyholder()

    N = 3000  # 3000 人规模
    RECORDS_PER = 3

    with app.app_context():
        # 建项目
        p = SubsidyProject(name='高龄津贴', project_type='高龄津贴', cycle='月',
                           default_amount=100, status='启用')
        db.session.add(p)
        db.session.commit()
        pid = p.id

        # 批量建居民（含密文身份证，避免逐条 commit 开销）
        residents = []
        for i in range(N):
            idc = f'11010119450{i:06d}0'
            r = Resident(name=f'居民{i}', birth_date=_years_ago(81), status='normal',
                         flow_status='常住')
            r.id_card = None
            r.id_card_enc = key_holder.encrypt(idc)
            r.id_card_hash = sha256_hash(idc)
            r.id_card_prefix_hash = sha256_prefix_hash(idc, 6)
            residents.append(r)
        db.session.add_all(residents)
        db.session.commit()

        # 批量建发放记录（每人 3 笔）
        recs = []
        from datetime import date
        for r in residents:
            for j in range(RECORDS_PER):
                recs.append(SubsidyRecord(
                    resident_id=r.id, project_id=pid,
                    issue_date=date.today(), period=f'2026-0{8 - j}',
                    amount=100 + j * 10, channel='镇级直发',
                    source='normal', status='正常'))
        db.session.add_all(recs)
        db.session.commit()

        # 目标居民 id（随机取一个）
        target_rid = residents[N // 2].id

    client = _admin_client(app)

    # 测 P95：多次请求总览端点
    latencies = []
    SAMPLES = 30
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        resp = client.get(f'/subsidy/api/resident/{target_rid}/overview')
        elapsed = (time.perf_counter() - t0) * 1000  # ms
        assert resp.status_code == 200
        latencies.append(elapsed)

    latencies.sort()
    p95 = latencies[int(SAMPLES * 0.95) - 1] if SAMPLES >= 20 else latencies[-1]
    avg = sum(latencies) / len(latencies)

    # 断言：P95 < 2000ms（2s）
    assert p95 < 2000, f"总览 P95={p95:.1f}ms 超过 2000ms 阈值（avg={avg:.1f}ms）"
