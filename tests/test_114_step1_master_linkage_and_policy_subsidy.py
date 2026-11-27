# -*- coding: utf-8 -*-
"""tests/test_114_step1_master_linkage_and_policy_subsidy.py — 第一步数据联动收口测试。

覆盖：
1. 主档单条编辑：低保/残疾历史空 resident_id 按 id_card_hash 回退命中并自动回填 resident_id；
   优抚、就业服务、选民名册同步生效；
2. 低保详情接口（/dibao/api/detail/<id>）主档优先读取最新联系电话（无需在 dibao 表冗余 phone 列）；
3. 居民批量导入更新（utils/import_engine.py:run_import）同样触发 ResidentSyncService.propagate；
4. 声明式 POLICY_SUBSIDY_REGISTRY 聚合 6 张民政专项表在享补贴与合计（/subsidy/api/resident/<id>/overview）。
"""
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


def test_master_sync_backfill_dibao_live_phone_and_new_modules(isolated_app_factory):
    app = isolated_app_factory()
    # （test_t14_batch._unlock_keyholder / test_110 同款姿势），否则 key_holder.encrypt 报「密钥未初始化」。
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())
    client = _login_admin(app)

    id_card = '11010119850101' + '4321'
    old_phone = '1380000' + '1111'
    new_phone = '1390000' + '2222'

    with app.app_context():
        from models import (Dibao, Disabled, ElectionRound, ElectionVoter,
                            Resident, WelfareEmployment, Youshu, db)
        from utils.crypto import key_holder, sha256_hash

        ich = sha256_hash(id_card)
        res = Resident(
            name='张联动', id_card_enc=key_holder.encrypt(id_card), id_card_hash=ich,
            phone_enc=key_holder.encrypt(old_phone), phone_hash=sha256_hash(old_phone),
            address='老地址1号', village_group='一组', gender='男',
        )
        db.session.add(res)
        db.session.flush()
        rid = res.id

        # 刻意 resident_id=None（模拟历史导入记录）
        db_row = Dibao(name='张联动', id_card_hash=ich, address='老地址1号', grid='一组', household_amount=600)
        dis_row = Disabled(name='张联动', id_card_hash=ich, village_group='一组',
                           phone_enc=key_holder.encrypt(old_phone), phone_hash=sha256_hash(old_phone),
                           subsidy_living=100, subsidy_nursing=150)
        ys_row = Youshu(name='张联动', id_card_hash=ich,
                        phone_enc=key_holder.encrypt(old_phone), phone_hash=sha256_hash(old_phone),
                        care_category='伤残军人', amount=500)
        emp_row = WelfareEmployment(name='张联动', id_card_hash=ich, village_group='一组',
                                    phone_enc=key_holder.encrypt(old_phone), phone_hash=sha256_hash(old_phone))
        from datetime import date
        er = ElectionRound(round_name='2026届换届', start_date=date(2026, 1, 1),
                           register_start=date(2026, 1, 6), register_end=date(2026, 1, 20),
                           vote_date=date(2026, 1, 25))
        db.session.add(er)
        db.session.flush()
        ev_row = ElectionVoter(round_id=er.id, name='张联动', id_card_hash=ich, group_name='一组',
                               phone_enc=key_holder.encrypt(old_phone), phone_hash=sha256_hash(old_phone))
        db.session.add_all([db_row, dis_row, ys_row, emp_row, ev_row])
        db.session.commit()
        db_id = db_row.id

    resp = client.post('/resident/api/save', data={
        'id': rid, 'name': '张联动改', 'id_card': id_card,
        'phone': new_phone, 'address': '新地址88号', 'village_group': '二组',
        'gender': '男',
    })
    assert resp.status_code == 200 and resp.get_json()['success'], resp.get_data(as_text=True)

    # 低保详情主档优先读取最新联系电话
    detail = client.get(f'/dibao/api/detail/{db_id}').get_json()
    assert detail['success']
    assert detail['data']['name'] == '张联动改'
    assert detail['data']['address'] == '新地址88号'
    assert detail['data']['phone'] == new_phone

    with app.app_context():
        from models import (Dibao, Disabled, ElectionVoter, WelfareEmployment, Youshu)
        from utils.crypto import key_holder, sha256_hash

        ich = sha256_hash(id_card)
        db_r = Dibao.query.filter_by(id_card_hash=ich).first()
        assert db_r.resident_id == rid and db_r.grid == '二组'

        dis_r = Disabled.query.filter_by(id_card_hash=ich).first()
        assert dis_r.resident_id == rid and key_holder.decrypt(dis_r.phone_enc) == new_phone

        ys_r = Youshu.query.filter_by(id_card_hash=ich).first()
        assert ys_r.name == '张联动改' and key_holder.decrypt(ys_r.phone_enc) == new_phone

        emp_r = WelfareEmployment.query.filter_by(id_card_hash=ich).first()
        assert emp_r.resident_id == rid and emp_r.village_group == '二组'
        assert key_holder.decrypt(emp_r.phone_enc) == new_phone

        ev_r = ElectionVoter.query.filter_by(id_card_hash=ich).first()
        assert ev_r.name == '张联动改' and ev_r.group_name == '二组'
        assert key_holder.decrypt(ev_r.phone_enc) == new_phone


def test_declarative_policy_subsidy_registry_aggregation(isolated_app_factory):
    app = isolated_app_factory()
    # （test_t14_batch._unlock_keyholder / test_110 同款姿势），否则 key_holder.encrypt 报「密钥未初始化」。
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())
    client = _login_admin(app)
    id_card = '11010119700202' + '8765'

    with app.app_context():
        from models import (Dibao, Disabled, Resident, SpecialSupport,
                            UnsupportedChild, WelfareJob, Youshu, db)
        from utils.crypto import key_holder, sha256_hash
        from utils.subsidy_service import POLICY_SUBSIDY_REGISTRY

        assert len(POLICY_SUBSIDY_REGISTRY) >= 6
        ich = sha256_hash(id_card)
        res = Resident(name='王补贴', id_card_enc=key_holder.encrypt(id_card), id_card_hash=ich)
        db.session.add(res)
        db.session.flush()
        rid = res.id

        db.session.add_all([
            Dibao(name='王补贴', id_card_hash=ich, dibao_type='农村低保', household_amount=650.0, status='有效'),
            Disabled(name='王补贴', id_card_hash=ich, disability_type='肢体',
                     subsidy_living=100.0, subsidy_nursing=120.0, status='有效'),
            Youshu(name='王补贴', id_card_hash=ich, care_category='参战退役', amount=800.0, status='有效'),
            SpecialSupport(household_name='王补贴', household_id_card_hash=ich,
                           support_mode='分散供养', guarantee_amount=900.0, status='有效'),
            UnsupportedChild(name='王补贴', id_card_hash=ich, amount=500.0),
            WelfareJob(name='王补贴', id_card_hash=ich, job_type='保洁员', monthly_amount=1000.0),
        ])
        db.session.commit()

    resp = client.get(f'/subsidy/api/resident/{rid}/overview')
    assert resp.status_code == 200
    d = resp.get_json()['data']
    assert d['policy_count'] == 7
    assert d['policy_total'] == 4070.0
    names = {it['name'] for it in d['policy_subsidies']}
    assert {'低保救助', '残疾人困难生活补贴', '残疾人重度护理补贴',
            '优抚对象补贴', '特困供养保障', '事实无人抚养儿童补贴', '公益性岗位补贴'} == names
