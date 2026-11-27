# -*- coding: utf-8 -*-
"""tests/test_111_phase3b_civil_batch.py — 第三批-下（民政对象 4 子模块 A1 异步批导入）。"""
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


def _run_async_flow(client, prefix, csv_text, extra=None):
    up = client.post(prefix + '/api/import-upload', data={
        'file': (io.BytesIO(_csv_bytes(csv_text)), 'civil.csv')
    }, content_type='multipart/form-data')
    assert up.status_code == 200 and up.get_json()['success'], up.get_data(as_text=True)
    token = up.get_json()['data']['token']

    payload = dict(extra or {})
    payload['token'] = token
    ex = client.post(prefix + '/api/import-execute', json=payload)
    eb = ex.get_json()
    assert ex.status_code == 200 and eb['success'], eb
    batch_id = eb['data']['batch_id']
    task_id = eb['data']['task_id']

    st = client.get(prefix + '/api/import-status/' + task_id).get_json()
    assert st['success'] and st['data']['status'] == 'done'
    rep = client.get(prefix + '/api/import-report/' + batch_id).get_json()
    assert rep['success']
    return rep['data'], batch_id


def test_civil_domains_async_batch_and_unified_view(isolated_app_factory, monkeypatch):
    app = isolated_app_factory()
    # 民政五域导入含身份证（identity 敏感列）：写库走 KeyHolder 加密，须先初始化密钥
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

    # 1. 低保人员
    rep_db, bid_db = _run_async_flow(
        client, '/dibao',
        ['成员姓名,成员身份证号,户救助金额', '张低保,110101198001011111,800', ',110101198001011112,500'],
        {'mode': 'append'},
    )
    assert rep_db['inserted_rows'] == 1 and rep_db['fail_rows'] == 1

    # 2. 残疾人
    rep_dis, _ = _run_async_flow(
        client, '/disabled',
        ['姓名,身份证号,残疾类别,困难生活补贴', '李残疾,110101198202022222,肢体,200'],
        {'mode': 'append'},
    )
    assert rep_dis['inserted_rows'] == 1

    # 3. 特困人员 + 困境儿童
    rep_sp, _ = _run_async_flow(
        client, '/care/special',
        ['户主姓名,户主身份证号,保障金额', '王特困,110101195003033333,900'],
        {'mode': 'append'},
    )
    assert rep_sp['inserted_rows'] == 1

    rep_ch, _ = _run_async_flow(
        client, '/care/child',
        ['儿童姓名,身份证号码,发放金额（元）', '赵儿童,110101201504044444,600'],
        {'mode': 'append'},
    )
    assert rep_ch['inserted_rows'] == 1

    # 4. 公益岗
    rep_wj, _ = _run_async_flow(
        client, '/welfare',
        ['姓名,身份证号,岗位类型,月补贴金额', '钱公益,110101197505055555,保洁,1200'],
        {'mode': 'append'},
    )
    assert rep_wj['inserted_rows'] == 1

    # 5. 统一【导入批次】筛选 civil 应聚合全部 5 个民政批次，且失败行导出可用
    civil_list = client.get('/import/batches/data?source_module=civil').get_json()
    assert civil_list['success'] and civil_list['total'] == 5
    exp = client.get('/dibao/api/import-export-failed/' + bid_db)
    assert exp.status_code == 200
