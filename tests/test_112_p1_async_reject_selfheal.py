# -*- coding: utf-8 -*-
"""tests/test_112_p1_async_reject_selfheal.py — P1 异步并发拒入不留孤儿 pending 批次 + 自愈测试。"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _login_admin(app):
    client = app.test_client()
    with app.app_context():
        from models import User
        admin = User.query.filter_by(username='admin').first()
    with client.session_transaction() as sess:
        sess['user_id'] = admin.id
        sess['username'] = admin.username
    return client


def _csv_bytes(lines):
    return ('\n'.join(lines) + '\n').encode('utf-8-sig')


def test_domain_batch_reject_on_busy_lock_returns_409_without_pending(isolated_app_factory):
    """并发锁占用时前置拒入：返回 409，且不落孤儿 pending 批次。"""
    app = isolated_app_factory()
    client = _login_admin(app)

    from utils.async_tasks import _lock_for
    from utils.domain_batch import TASK_TYPE
    lock = _lock_for(TASK_TYPE)

    up = client.post('/dibao/api/import-upload', data={
        'file': (io.BytesIO(_csv_bytes(['成员姓名,户救助金额', '张测试,800'])), 'db.csv')
    }, content_type='multipart/form-data')
    token = up.get_json()['data']['token']

    assert lock.acquire(blocking=False)
    try:
        ex = client.post('/dibao/api/import-execute', json={'token': token, 'mode': 'append'})
        assert ex.status_code == 409
        assert ex.get_json()['success'] is False
    finally:
        lock.release()

    with app.app_context():
        from models import DomainBatchImport
        assert DomainBatchImport.query.filter_by(domain='dibao').count() == 0


def test_domain_batch_toctou_reject_marks_failed_and_reenterable(isolated_app_factory, monkeypatch):
    """TOCTOU 窗口：run_async 返回 False 时立刻回写批次 failed 并返 409，随后重传可正常重进。"""
    app = isolated_app_factory()
    client = _login_admin(app)
    import utils.async_tasks as at

    monkeypatch.setattr(at, 'run_async', lambda *a, **kw: False)
    csv_raw = _csv_bytes(['成员姓名,户救助金额', '李并发,900'])

    up = client.post('/dibao/api/import-upload', data={
        'file': (io.BytesIO(csv_raw), 'db2.csv')
    }, content_type='multipart/form-data')
    token = up.get_json()['data']['token']

    ex = client.post('/dibao/api/import-execute', json={'token': token, 'mode': 'append'})
    assert ex.status_code == 409
    bid = ex.get_json()['data']['batch_id']

    with app.app_context():
        from models import DomainBatchImport
        b = DomainBatchImport.query.filter_by(batch_id=bid).first()
        assert b is not None and b.status == 'failed'

    # 恢复正常执行后重传同文件：应命中 0 行失败重进并成功完成
    def _sync_run_async(task_type, task_id, fn, *args, **kwargs):
        with app.app_context():
            fn(task_id, *args, **kwargs)
        return True

    monkeypatch.setattr(at, 'run_async', _sync_run_async)
    up2 = client.post('/dibao/api/import-upload', data={
        'file': (io.BytesIO(csv_raw), 'db2.csv')
    }, content_type='multipart/form-data')
    ex2 = client.post('/dibao/api/import-execute', json={
        'token': up2.get_json()['data']['token'], 'mode': 'append'
    })
    assert ex2.status_code == 200 and ex2.get_json()['success']
    rep = client.get('/dibao/api/import-report/' + bid).get_json()
    assert rep['data']['status'] == 'done' and rep['data']['inserted_rows'] == 1


def test_stuck_pending_batch_self_heals_in_batch_center_and_reentry(isolated_app_factory):
    """存量卡死 pending（绑定 AsyncTask 已 failed）：在导入批次视图查询时自动自愈为 failed。"""
    app = isolated_app_factory()
    client = _login_admin(app)

    with app.app_context():
        from models import AsyncTask, DomainBatchImport, db
        t = AsyncTask(task_id='deadtask0001', task_type='domain_batch', title='低保导入',
                      status='failed', result_summary='并发互斥：同类任务已在运行')
        b = DomainBatchImport(batch_id='stuckbatch01', domain='dibao', scope_key='',
                              source_file='stuck.csv', source_hash='abc123',
                              operator='admin', status='pending', task_id='deadtask0001')
        db.session.add_all([t, b])
        db.session.commit()

    resp = client.get('/import/batches/data?source_module=civil&status=failed').get_json()
    assert resp['success']
    matched = [r for r in resp['data'] if r['batch_id'] == 'stuckbatch01']
    assert len(matched) == 1 and matched[0]['status'] == 'failed'


def test_lazy_check_periodic_skips_when_lock_busy(isolated_app_factory):
    """lazy_check 锁被占用时周期触发静默跳过，不产生多余 failed 噪音行。"""
    app = isolated_app_factory()
    from utils.async_tasks import _lock_for, lazy_check_periodic
    lock = _lock_for('lazy_check')
    with app.app_context():
        from models import AsyncTask
        before = AsyncTask.query.filter_by(task_type='lazy_check').count()
        assert lock.acquire(blocking=False)
        try:
            lazy_check_periodic()
        finally:
            lock.release()
        after = AsyncTask.query.filter_by(task_type='lazy_check').count()
        assert after == before
