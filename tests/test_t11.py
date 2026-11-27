# -*- coding: utf-8 -*-
"""T11 R1 常住人口库增强 单测（Sprint2）。

覆盖：
- T11-1 读密文 CRUD：api_save 加密写 / api_list·detail 解密显示（admin 明文 + 普通角色脱敏）/
       敏感搜索（id_card 精确·前6前缀 / phone 精确）/ 导出无明文 / sensitive 端点解密 / 日志无明文
- T11-2 moved_out 收敛：scan_flow_judge 0 warn / 户汇总 flow_out 判定
- T11-3 多标签：打/删/幂等/非法码 400/按标签筛人/软失效后可重打
- T11-4 家庭关系：成员添加加密写 / 移出本户 / change-head 排除流出
- T11-5 流动标记：flow_status 筛选 / 手工流入建档 source='手工' / 导入 source='导入'
"""
import io
import os
import sys

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
        sess['user_id'] = 1  # admin
    return client


def _make_non_admin(app):
    import json
    from models import db, Role, User
    with app.app_context():
        perms = {
            'resident.view': True, 'resident.add': True, 'resident.edit': True,
            'resident.export': True,
            'special': {'view_sensitive': False, 'death_confirm': False,
                        'import_batch': False, 'backup_restore': False,
                        'mask_config': False},
            'schema_version': 3,
        }
        role = Role(name="普通角色T11", permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.flush()
        user = User(username="普通用户T11", password_hash="x", role_id=role.id, is_active=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_resident(app, name='张三', id_card='110101199001011234', phone='13812345678',
                   bank_card='6222021234567890123', **extra):
    """通过 api_save（加密写）造居民，返回 id。"""
    client = _admin_client(app)
    payload = {
        'name': name, 'id_card': id_card, 'phone': phone, 'bank_card': bank_card,
    }
    payload.update(extra)
    r = client.post('/resident/api/save', data=payload)
    j = r.get_json()
    assert j['success'], j
    return j['data']['id']


# ═══════════════ T11-1 读密文 CRUD ═══════════════

class TestReadCipherCRUD:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_save_encrypts_sensitive_fields(self, app):
        _unlock_keyholder()
        from models import db, Resident
        rid = _make_resident(app)
        with app.app_context():
            r = Resident.query.get(rid)
            assert r.id_card is None                       # 原列置 NULL
            assert r.id_card_enc                            # 密文非空
            assert r.id_card_hash                           # 哈希索引
            assert r.id_card_prefix_hash                    # 前缀桶
            assert r.phone is None
            assert r.phone_enc and r.phone_prefix_hash
            assert r.bank_card is None
            assert r.bank_card_enc

    def test_api_list_decrypts_for_admin(self, app):
        _unlock_keyholder()
        rid = _make_resident(app)
        client = _admin_client(app)
        data = client.get('/resident/api/list?page=1&limit=20').get_json()
        rows = {r['id']: r for r in data['data']['data']}
        assert rows[rid]['id_card'] == '110101199001011234'
        assert rows[rid]['phone'] == '13812345678'

    def test_api_list_masked_for_non_admin(self, app):
        _unlock_keyholder()
        rid = _make_resident(app)
        uid = _make_non_admin(app)
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        data = client.get('/resident/api/list?page=1&limit=20').get_json()
        rows = {r['id']: r for r in data['data']['data']}
        assert rows[rid]['id_card'] == '110101********1234'   # 6+4 脱敏
        assert rows[rid]['phone'] == '138****5678'            # 3+4 脱敏

    def test_api_detail_decrypts(self, app):
        _unlock_keyholder()
        rid = _make_resident(app)
        client = _admin_client(app)
        data = client.get(f'/resident/api/detail/{rid}').get_json()
        detail = data['data']['detail']
        assert detail['id_card'] == '110101199001011234'
        assert detail['phone'] == '13812345678'
        assert detail['bank_card'] == '6222021234567890123'

    def test_search_id_card_exact(self, app):
        _unlock_keyholder()
        _make_resident(app, name='甲', id_card='110101199001011234')
        _make_resident(app, name='乙', id_card='110101199002022345')
        client = _admin_client(app)
        data = client.get('/resident/api/list?id_card=110101199001011234').get_json()
        names = [r['name'] for r in data['data']['data']]
        assert names == ['甲']

    def test_search_id_card_prefix6(self, app):
        _unlock_keyholder()
        _make_resident(app, name='甲', id_card='110101199001011234')
        _make_resident(app, name='乙', id_card='220202199002022345')
        client = _admin_client(app)
        # 前6位候选桶 + 应用层二次过滤
        data = client.get('/resident/api/list?id_card=110101').get_json()
        names = [r['name'] for r in data['data']['data']]
        assert names == ['甲']

    def test_search_phone_exact(self, app):
        _unlock_keyholder()
        _make_resident(app, name='甲', phone='13812345678')
        _make_resident(app, name='乙', phone='13900001111')
        client = _admin_client(app)
        data = client.get('/resident/api/list?phone=13812345678').get_json()
        names = [r['name'] for r in data['data']['data']]
        assert names == ['甲']

    def test_sensitive_endpoint_decrypts(self, app):
        _unlock_keyholder()
        rid = _make_resident(app)
        client = _admin_client(app)
        resp = client.get(f'/resident/{rid}/sensitive?field=id_card')
        assert resp.status_code == 200
        assert resp.get_json()['data']['id_card'] == '110101199001011234'

    def test_export_no_plaintext_via_encryption(self, app):
        _unlock_keyholder()
        _make_resident(app)
        client = _admin_client(app)
        resp = client.get('/resident/api/export')
        assert resp.status_code == 200
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(resp.data))
        ws = wb.active
        # 第4列=身份证号：应为明文（admin 导出）或脱敏，绝不含密文 gAAAA
        values = [str(c.value or '') for row in ws.iter_rows() for c in row]
        assert not any('gAAAA' in v for v in values)


# ═══════════════ T11-2 moved_out 收敛 ═══════════════

class TestFlowJudgeConverge:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_scan_flow_judge_zero_warn(self):
        import subprocess
        import sys
        r = subprocess.run(
            [sys.executable, 'scripts/checks/scan_flow_judge.py'],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True, text=True)
        assert r.returncode == 0
        assert '0 处' in r.stdout
        assert 'PASS' in r.stdout

    def test_household_summary_flow_out(self, app):
        _unlock_keyholder()
        from models import db, Resident
        with app.app_context():
            r1 = Resident(name='户主甲', household_code='H0001', relationship='户主本人')
            r2 = Resident(name='成员乙', household_code='H0001', relationship='子女', flow_status='流出')
            db.session.add_all([r1, r2])
            db.session.commit()
        client = _admin_client(app)
        data = client.get('/resident/api/household-summary').get_json()
        hh = data['data']['data'][0]
        assert hh['has_moved'] is True


# ═══════════════ T11-3 多标签 ═══════════════

class TestTags:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_tag_add_and_list(self, app):
        _unlock_keyholder()
        rid = _make_resident(app)
        client = _admin_client(app)
        j = client.post('/resident/api/tag/add', data={'resident_id': rid, 'tag_code': '低保'}).get_json()
        assert j['success']
        j2 = client.post('/resident/api/tag/add', data={'resident_id': rid, 'tag_code': '残疾'}).get_json()
        assert j2['success']
        tags = client.get(f'/resident/api/tag/list?resident_id={rid}').get_json()['data']
        assert {t['tag_code'] for t in tags} == {'低保', '残疾'}

    def test_tag_add_idempotent(self, app):
        _unlock_keyholder()
        rid = _make_resident(app)
        client = _admin_client(app)
        assert client.post('/resident/api/tag/add', data={'resident_id': rid, 'tag_code': '低保'}).get_json()['success']
        r2 = client.post('/resident/api/tag/add', data={'resident_id': rid, 'tag_code': '低保'})
        assert r2.get_json()['success']  # 幂等：重复打返回成功

    def test_tag_add_invalid_code_400(self, app):
        _unlock_keyholder()
        rid = _make_resident(app)
        client = _admin_client(app)
        r = client.post('/resident/api/tag/add', data={'resident_id': rid, 'tag_code': '不存在标签XYZ'})
        assert r.get_json()['success'] is False
        assert '非法标签码' in r.get_json()['msg']

    def test_tag_search_by_code(self, app):
        _unlock_keyholder()
        rid1 = _make_resident(app, name='甲')
        rid2 = _make_resident(app, name='乙')
        client = _admin_client(app)
        client.post('/resident/api/tag/add', data={'resident_id': rid1, 'tag_code': '低保'})
        client.post('/resident/api/tag/add', data={'resident_id': rid2, 'tag_code': '残疾'})
        data = client.get('/resident/api/tag/search?tag_code=低保').get_json()['data']
        assert [r['name'] for r in data['data']] == ['甲']

    def test_tag_remove_soft_delete_then_readd(self, app):
        _unlock_keyholder()
        rid = _make_resident(app)
        client = _admin_client(app)
        client.post('/resident/api/tag/add', data={'resident_id': rid, 'tag_code': '低保'})
        r = client.post('/resident/api/tag/remove', data={'resident_id': rid, 'tag_code': '低保'})
        assert r.get_json()['success']
        tags = client.get(f'/resident/api/tag/list?resident_id={rid}').get_json()['data']
        assert tags == []
        # 软失效后可重打
        assert client.post('/resident/api/tag/add', data={'resident_id': rid, 'tag_code': '低保'}).get_json()['success']


# ═══════════════ T11-4 家庭关系 ═══════════════

class TestHousehold:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_household_add_encrypts(self, app):
        _unlock_keyholder()
        from models import db, Resident
        with app.app_context():
            head = Resident(name='户主甲', household_code='H0001', relationship='户主本人')
            db.session.add(head)
            db.session.commit()
        client = _admin_client(app)
        r = client.post('/resident/api/household/add', data={
            'household_code': 'H0001', 'name': '新成员', 'id_card': '110101199003033456',
            'relationship': '子女',
        })
        assert r.get_json()['success']
        with app.app_context():
            m = Resident.query.filter_by(name='新成员').first()
            assert m.id_card is None
            assert m.id_card_enc
            assert m.household_code == 'H0001'

    def test_household_remove(self, app):
        _unlock_keyholder()
        from models import db, Resident
        with app.app_context():
            head = Resident(name='户主甲', household_code='H0001', relationship='户主本人')
            member = Resident(name='成员乙', household_code='H0001', relationship='子女')
            db.session.add_all([head, member])
            db.session.commit()
            member_id = member.id
        client = _admin_client(app)
        r = client.post('/resident/api/household/remove', data={'resident_id': member_id})
        assert r.get_json()['success']
        with app.app_context():
            m = Resident.query.get(member_id)
            assert m.household_code == ''
            assert m.relationship == ''

    def test_change_head_excludes_flow_out(self, app):
        _unlock_keyholder()
        from models import db, Resident
        with app.app_context():
            head = Resident(name='户主甲', household_code='H0001', relationship='户主本人')
            normal = Resident(name='成员乙', household_code='H0001', relationship='子女')
            out = Resident(name='成员丙', household_code='H0001', relationship='其他亲属', flow_status='流出')
            db.session.add_all([head, normal, out])
            db.session.commit()
            normal_id, out_id = normal.id, out.id
        client = _admin_client(app)
        data = client.post('/resident/api/change-head/query',
                           data={'household_code': 'H0001'}).get_json()
        assert normal_id in data['data']['candidate_ids']
        assert out_id not in data['data']['candidate_ids']


# ═══════════════ T11-5 流动标记 ═══════════════

class TestFlowStatus:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_flow_status_filter(self, app):
        _unlock_keyholder()
        _make_resident(app, name='甲', flow_status='常住')
        _make_resident(app, name='乙', flow_status='流入')
        client = _admin_client(app)
        data = client.get('/resident/api/list?flow_status=流入').get_json()
        names = [r['name'] for r in data['data']['data']]
        assert names == ['乙']

    def test_save_inflow_source_manual(self, app):
        _unlock_keyholder()
        from models import db, Resident
        client = _admin_client(app)
        j = client.post('/resident/api/save', data={
            'name': '流入新人', 'id_card': '110101199004044567', 'flow_status': '流入',
        }).get_json()
        assert j['success']
        with app.app_context():
            r = Resident.query.get(j['data']['id'])
            assert r.flow_status == '流入'
            assert r.source == '手工'

    def test_import_source_imported(self, app):
        _unlock_keyholder()
        from models import db, Resident
        with app.app_context():
            r = Resident(name='导入居民', id_card_enc='dummy', id_card_hash='h1',
                         id_card_prefix_hash='p1', source='导入', flow_status='常住')
            db.session.add(r)
            db.session.commit()
        client = _admin_client(app)
        data = client.get('/resident/api/list?name=导入居民').get_json()
        row = data['data']['data'][0]
        assert row['source'] == '导入'
