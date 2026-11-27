# -*- coding: utf-8 -*-
"""tests/test_115_linkage_visual_config.py — 设置中心可视化数据联动与补贴口径配置回归测试。"""
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


def test_visual_policy_override_and_reset_live_effect(isolated_app_factory):
    app = isolated_app_factory()
    # （test_t14_batch._unlock_keyholder / test_110/114 同款姿势），否则 key_holder.encrypt 报「密钥未初始化」。
    from utils.crypto import key_holder as _kh, generate_salt as _gs
    if not _kh.is_initialized:
        _kh.initialize('测试口令123', salt=_gs())
    client = _login_admin(app)
    id_card = '11010119750303' + '6543'

    with app.app_context():
        from models import Dibao, Resident, Youshu, db
        from utils.crypto import key_holder, sha256_hash
        ich = sha256_hash(id_card)
        res = Resident(name='赵配置', id_card_enc=key_holder.encrypt(id_card), id_card_hash=ich)
        db.session.add(res)
        db.session.flush()
        rid = res.id
        db.session.add_all([
            Dibao(name='赵配置', id_card_hash=ich, household_amount=500.0, status='有效'),
            Youshu(name='赵配置', id_card_hash=ich, amount=300.0, status='有效'),
        ])
        db.session.commit()

    # 1. 读取配置元数据页与数据接口
    assert client.get('/settings/linkage').status_code == 200
    cfg = client.get('/settings/api/linkage/data').get_json()
    assert cfg['success'] and len(cfg['data']['policy_items']) >= 6

    # 2. 停用低保项 + 修改优抚展示名称，立即生效于居民详情聚合
    items = cfg['data']['policy_items']
    for it in items:
        if it['module'] == 'dibao':
            it['enabled'] = False
        if it['module'] == 'youshu':
            it['name'] = '退役优抚金（生产口径）'
    sv = client.post('/settings/api/linkage/policy', json={
        'policy_items': items, 'softtable_subsidies': []
    }).get_json()
    assert sv['success']

    ov = client.get(f'/subsidy/api/resident/{rid}/overview').get_json()['data']
    assert ov['policy_count'] == 1
    assert ov['policy_total'] == 300.0
    assert ov['policy_subsidies'][0]['name'] == '退役优抚金（生产口径）'

    # 3. 恢复默认后两项均重新生效（500 + 300 = 800）
    rs = client.post('/settings/api/linkage/reset', json={'scope': 'policy'}).get_json()
    assert rs['success']
    ov2 = client.get(f'/subsidy/api/resident/{rid}/overview').get_json()['data']
    assert ov2['policy_count'] == 2 and ov2['policy_total'] == 800.0


def test_visual_sync_override_disables_module_live(isolated_app_factory):
    app = isolated_app_factory()
    # （test_t14_batch._unlock_keyholder / test_110/114 同款姿势），否则 key_holder.encrypt 报「密钥未初始化」。
    from utils.crypto import key_holder as _kh, generate_salt as _gs
    if not _kh.is_initialized:
        _kh.initialize('测试口令123', salt=_gs())
    client = _login_admin(app)
    id_card = '11010119880404' + '2468'
    old_phone = '1370000' + '3333'
    new_phone = '1360000' + '4444'

    with app.app_context():
        from models import Resident, Youshu, db
        from utils.crypto import key_holder, sha256_hash
        ich = sha256_hash(id_card)
        res = Resident(name='钱开关', id_card_enc=key_holder.encrypt(id_card), id_card_hash=ich,
                       phone_enc=key_holder.encrypt(old_phone), phone_hash=sha256_hash(old_phone))
        db.session.add(res)
        db.session.flush()
        rid = res.id
        db.session.add(Youshu(name='钱开关', id_card_hash=ich,
                              phone_enc=key_holder.encrypt(old_phone), phone_hash=sha256_hash(old_phone)))
        db.session.commit()

    # 关闭 youshu 的主档联动
    cfg = client.get('/settings/api/linkage/data').get_json()['data']
    for row in cfg['sync_tables']:
        if row['table'] == 'youshu':
            row['enabled'] = False
    sv = client.post('/settings/api/linkage/sync', json={'sync_tables': cfg['sync_tables']}).get_json()
    assert sv['success']

    # 修改居民电话后，youshu 应保持旧电话
    client.post('/resident/api/save', data={'id': rid, 'name': '钱开关', 'id_card': id_card, 'phone': new_phone})
    with app.app_context():
        from models import Youshu
        from utils.crypto import key_holder, sha256_hash
        ys = Youshu.query.filter_by(id_card_hash=sha256_hash(id_card)).first()
        assert key_holder.decrypt(ys.phone_enc) == old_phone
