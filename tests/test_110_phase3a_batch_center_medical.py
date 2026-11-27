# -*- coding: utf-8 -*-
"""tests/test_110_phase3a_batch_center_medical.py — 第三批-上（统一导入批次视图 + 医保异步批导入）。"""
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


def test_medical_async_import_and_unified_batch_center(isolated_app_factory, monkeypatch):
    app = isolated_app_factory()
    # 医保导入含身份证（identity 敏感列）：写库走 KeyHolder 加密，须先初始化密钥
    # （test_t14_batch._unlock_keyholder 同款姿势），否则行 handler 报「密钥未初始化」记失败。
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())
    client = _login_admin(app)

    import utils.async_tasks as at

    def _sync_run_async(task_type, task_id, fn, *args, **kwargs):
        with app.app_context():
            fn(task_id, *args, **kwargs)

    monkeypatch.setattr(at, 'run_async', _sync_run_async)

    # 1. 医保异步导入五端点全链路
    up = client.post('/medical-insurance/api/import-upload', data={
        'file': (io.BytesIO(_csv_bytes([
            '姓名,身份证号,性别,缴费年度',
            '张医保,110101199001011234,男,2026',
            ',,女,2026',
        ])), 'med_2026.csv'),
        'insurance_type': 'resident',
    }, content_type='multipart/form-data')
    assert up.status_code == 200 and up.get_json()['success']
    token = up.get_json()['data']['token']

    ex = client.post('/medical-insurance/api/import-execute', json={
        'token': token,
        'insurance_type': 'resident',
        'import_batch': '2026年医保测试批次',
    })
    eb = ex.get_json()
    assert ex.status_code == 200 and eb['success'], eb
    batch_id = eb['data']['batch_id']
    task_id = eb['data']['task_id']

    st = client.get('/medical-insurance/api/import-status/' + task_id).get_json()
    assert st['success'] and st['data']['status'] == 'done'

    rep = client.get('/medical-insurance/api/import-report/' + batch_id).get_json()
    assert rep['success']
    assert rep['data']['inserted_rows'] == 1
    assert rep['data']['skip_rows'] == 1

    # 2. 统一【导入批次】视图应聚合可见医保批次 + 软表批次，并支持 source_module 过滤
    with app.app_context():
        from models import SofttableBatchImport, db
        db.session.add(SofttableBatchImport(
            batch_id='st_demo_001', module_key='pension', table_code='pension_roster',
            source_file='pension.xlsx', operator='admin', status='done',
            total_rows=10, success_rows=10, fail_rows=0))
        db.session.commit()

    all_resp = client.get('/import/batches/data').get_json()
    assert all_resp['success'] and all_resp['total'] >= 2
    mods = {item['source_module'] for item in all_resp['data']}
    assert 'medical' in mods and 'softtable' in mods

    med_only = client.get('/import/batches/data?source_module=medical').get_json()
    assert med_only['success'] and len(med_only['data']) == 1
    assert med_only['data'][0]['batch_id'] == batch_id
    assert med_only['data'][0]['report_url'] == '/medical-insurance/api/import-report/' + batch_id
