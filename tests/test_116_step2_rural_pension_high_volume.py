# -*- coding: utf-8 -*-
"""tests/test_116_step2_rural_pension_high_volume.py — 第二步补丁回归测试：
1. 农保复用 MedicalInsurance 大体量固定表（insurance_type='rural_pension'）与医保严格隔离；
2. 农保 A1 分批导入（import-upload -> import-execute）自动关联居民并写入 rural_pension；
3. 居民管理修改联系电话后，同事务自动同步至医保与农保双台账；
4. MetricsEngine 养老保险参保率与待遇认证率在农保大表有数据时自动按物理表计算。
"""
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


def test_rural_pension_isolation_and_resident_sync(isolated_app_factory):
    app = isolated_app_factory()
    # （test_t14_batch._unlock_keyholder / test_110/114/115 同款姿势），否则 key_holder.encrypt 报「密钥未初始化」。
    from utils.crypto import key_holder as _kh, generate_salt as _gs
    if not _kh.is_initialized:
        _kh.initialize('测试口令123', salt=_gs())
    client = _login_admin(app)
    id_card = '11010119700101' + '5566'
    old_phone = '1380000' + '1111'
    new_phone = '1390000' + '9999'

    with app.app_context():
        from models import Resident, db
        from utils.crypto import key_holder, sha256_hash
        res = Resident(
            name='周农保',
            id_card_enc=key_holder.encrypt(id_card),
            id_card_hash=sha256_hash(id_card),
            phone_enc=key_holder.encrypt(old_phone),
            phone_hash=sha256_hash(old_phone),
            village_group='一组',
        )
        db.session.add(res)
        db.session.commit()
        rid = res.id

    # 1. 同一年度同时建医保（resident）与农保（rural_pension）记录
    r_med = client.post('/medical-insurance/api/save', data={
        'name': '周农保', 'id_card': id_card, 'payment_year': 2026,
        'insurance_type': 'resident', 'is_paid': '是',
    }).get_json()
    assert r_med['success']

    r_pen = client.post('/pension/api/save', data={
        'name': '周农保', 'id_card': id_card, 'payment_year': 2026,
        'insurance_status': '正常参保', 'is_paid': '是', 'benefit_cert_status': '已认证',
    }).get_json()
    assert r_pen['success']

    # 2. 医保列表与农保列表严格隔离（互不串行）
    med_list = client.get('/medical-insurance/api/list?payment_year=2026').get_json()
    pen_list = client.get('/pension/api/list?payment_year=2026').get_json()
    assert med_list['total'] == 1 and med_list['data'][0]['insurance_type'] == 'resident'
    assert pen_list['total'] == 1 and pen_list['data'][0]['insurance_type'] == 'rural_pension'

    # 3. 居民主档修改联系电话 → 医保与农保双记录同步更新
    sv = client.post('/resident/api/save', data={
        'id': rid, 'name': '周农保', 'id_card': id_card,
        'phone': new_phone, 'village_group': '二组',
    }).get_json()
    assert sv['success']

    med_after = client.get('/medical-insurance/api/list?payment_year=2026').get_json()['data'][0]
    pen_after = client.get('/pension/api/list?payment_year=2026').get_json()['data'][0]
    assert med_after['contact_phone'] == new_phone and med_after['village_group'] == '二组'
    assert pen_after['contact_phone'] == new_phone and pen_after['village_group'] == '二组'

    # 4. MetricsEngine 养老参保率与认证率命中物理大表（1/1 = 1.00）
    with app.app_context():
        from utils.metrics import MetricsEngine
        from utils.agg_cache import invalidate as _cache_invalidate
        _cache_invalidate()
        cov = MetricsEngine.query('pension_coverage')
        cert = MetricsEngine.query('pension_cert_rate')
        assert cov['total'] == 1 and cov['data']['rate'] == '1.00'
        assert cert['total'] == 1 and cert['data']['rate'] == '1.00'


def test_rural_pension_a1_batch_import_csv(isolated_app_factory):
    app = isolated_app_factory()
    # （test_t14_batch._unlock_keyholder / test_110/114/115 同款姿势），否则 key_holder.encrypt 报「密钥未初始化」。
    from utils.crypto import key_holder as _kh, generate_salt as _gs
    if not _kh.is_initialized:
        _kh.initialize('测试口令123', salt=_gs())
    client = _login_admin(app)
    root_resp = client.get('/pension/')
    assert root_resp.status_code == 200
    root_html = root_resp.get_data(as_text=True)
    assert 'id="rpTableBody"' in root_html and 'id="rpDetailModal"' in root_html and '软表建册' not in root_html
    opts = client.get('/pension/api/options').get_json()['data']
    assert 'field_catalog' in opts and len(opts['field_catalog']['fields']['rural_pension']) >= 10

    id1 = '11010119650505' + '7788'
    csv_body = f'姓名,身份证号,缴费年度,参保情况,是否已缴,待遇认证状态\n吴大爷,{id1},2026,正常参保,是,已认证\n'.encode('utf-8-sig')
    up = client.post(
        '/pension/api/import-upload',
        data={'file': (io.BytesIO(csv_body), 'pension_2026.csv')},
        content_type='multipart/form-data',
    ).get_json()
    assert up['success'] and up['data']['token']

    # run_async 是真线程异步（A1 框架），测试按 112 惯例 monkeypatch 为同步执行
    import utils.async_tasks as _at

    def _sync_run_async(task_type, task_id, fn, *args, **kwargs):
        with app.app_context():
            fn(task_id, *args, **kwargs)
        return True

    _orig_run_async = _at.run_async
    _at.run_async = _sync_run_async
    try:
        ex = client.post('/pension/api/import-execute', json={
            'token': up['data']['token'],
            'import_batch': '2026农保测试批次',
        }).get_json()
    finally:
        _at.run_async = _orig_run_async
    assert ex['success'], ex
    bid = ex['data']['batch_id']
    rep = client.get('/pension/api/import-report/' + bid).get_json()
    assert rep['success'], rep
    assert rep['data']['inserted_rows'] == 1, rep['data']

    # 医保列表应仍为 0，农保列表为 1
    assert client.get('/medical-insurance/api/list?payment_year=2026').get_json()['total'] == 0
    assert client.get('/pension/api/list?payment_year=2026').get_json()['total'] == 1

    # 校验【导入批次】统一视图可见 pension 域，且 report_url 可直达（无 /pension/ledger 404）
    with app.app_context():
        from utils.batch_center import query_unified_batches
        bc = query_unified_batches(source_module='pension')
        assert bc['total'] == 1
        row = bc['data'][0]
        assert row['source_module'] == 'pension'
        assert row['source_label'] == '农保/养老保险'
        assert row['report_url'].startswith('/pension/api/import-report/')
        rep = client.get(row['report_url'])
        assert rep.status_code == 200 and rep.get_json()['success']

    # 校验批量标记已缴仅作用于农保记录
    pen_id = client.get('/pension/api/list?payment_year=2026').get_json()['data'][0]['id']
    bp = client.post('/pension/api/batch-paid', data={'ids[]': [str(pen_id)]}).get_json()
    assert bp['success'] and bp['data']['updated'] == 1
