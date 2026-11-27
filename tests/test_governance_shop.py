# -*- coding: utf-8 -*-
"""综治管理（governance 4 台账）+ 店铺信息（shop）功能测试。

覆盖（对应菜单模块重构方案 阶段 5/6 验收）：
- governance：4 台账页渲染、未知 category 404、CRUD + id_card R12 加密、导出
- shop：页面渲染、CRUD + id_card R12 加密、导出、模板下载、批量导入（append/overwrite/去重）
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

ID_CARD = '110101199001011234'
GOV_CATEGORIES = ['special-visit', 'stability-risk', 'mental-health', 'security-committee']


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    for k, v in {
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_LAN_AUTHORIZED": "1",
    }.items():
        monkeypatch.setenv(k, v)
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
        sess['user_id'] = 1  # admin 全权限
    return client


def _mk_role_user(app, username, perms):
    """建角色 + 用户，返回 user_id（权限矩阵用例用）。"""
    import json
    from models import db, Role, User
    with app.app_context():
        role = Role(name=username + '_role',
                    permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.commit()
        user = User(username=username, role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        return user.id


def _login_as(client, user_id):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id


def _base_perms(**overrides):
    perms = {'__schema_version__': 3,
             'special': {'view_sensitive': False, 'death_confirm': False,
                         'import_batch': False, 'backup_restore': False,
                         'mask_config': False}}
    perms.update(overrides)
    return perms


# ═══════════════ 综治管理（governance 4 台账） ═══════════════

class TestGovernanceLedger:
    """Patch C-2：第一代 GovernanceLedger 已移除，历史 /governance/<category>/ 302 跳转至软表 V2。"""
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    @pytest.mark.parametrize("category", GOV_CATEGORIES)
    def test_page_redirects_to_v2_roster(self, app, category):
        from utils.governance_catalog import GOV_CATEGORY_CODES
        client = _admin_client(app)
        r = client.get(f'/governance/{category}/')
        assert r.status_code == 302
        assert f'/governance/roster/{GOV_CATEGORY_CODES[category]}/' in (r.headers.get('Location') or '')
        r2 = client.get(f'/governance/{category}/', follow_redirects=True)
        assert r2.status_code == 200

    def test_unknown_category_404(self, app):
        client = _admin_client(app)
        r = client.get('/governance/not-exist/')
        assert r.status_code == 404

    def test_requires_view_permission(self, app):
        """R-047-08：缺 mediation.view → 页面 403（ADR-016 复用位）。"""
        uid = _mk_role_user(app, 'gov_denied', _base_perms())
        c = app.test_client()
        _login_as(c, uid)
        assert c.get('/governance/special-visit/').status_code == 403


# ═══════════════ 店铺信息（shop） ═══════════════

class TestShop:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_page_renders(self, app):
        client = _admin_client(app)
        r = client.get('/shop/')
        assert r.status_code == 200

    def test_crud_and_encryption(self, app):
        """CRUD + id_card 字段级加密。"""
        _unlock_keyholder()
        from utils.crypto import sha256_hash
        client = _admin_client(app)

        r = client.post('/shop/api/save', data={
            'name': '便民超市', 'street': '中山路', 'house_number': '12号',
            'owner_name': '王五', 'id_card': ID_CARD, 'phone': '13912345678',
        })
        j = r.get_json()
        assert j['success'], j

        from models import Shop
        with app.app_context():
            row = Shop.query.first()
            assert row.id_card is None
            assert row.id_card_enc.startswith('gAAAA')
            assert row.id_card_hash == sha256_hash(ID_CARD)
            assert row.name == '便民超市'

        r = client.get('/shop/api/list')
        j = r.get_json()
        assert j['success'] and j['total'] == 1
        assert j['data'][0]['id_card'] == ID_CARD
        rid = j['data'][0]['id']

        r = client.get(f'/shop/api/detail/{rid}')
        assert r.get_json()['data']['street'] == '中山路'

        r = client.post(f'/shop/api/delete/{rid}')
        assert r.get_json()['success']

    def test_export_and_template(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        client.post('/shop/api/save', data={'name': '便利店', 'id_card': ID_CARD})
        r = client.get('/shop/api/export')
        assert r.status_code == 200 and 'spreadsheetml' in (r.content_type or '')
        r2 = client.get('/shop/api/template')
        assert r2.status_code == 200 and 'spreadsheetml' in (r2.content_type or '')

    def _make_xlsx(self, rows):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['店铺名称', '街道名', '门牌号', '商户姓名', '身份证号', '电话'])
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    def test_import_append_skip_dup(self, app):
        """append：首条导入，重复行（同身份证号）跳过。"""
        _unlock_keyholder()
        client = _admin_client(app)
        buf = self._make_xlsx([
            ['甲超市', '街A', '1号', '张三', ID_CARD, '13800000001'],
            ['乙超市', '街B', '2号', '李四', ID_CARD, '13800000002'],  # 同身份证 → skip
        ])
        r = client.post('/shop/api/import', data={'mode': 'append', 'file': (buf, 'shops.xlsx')},
                        content_type='multipart/form-data')
        j = r.get_json()
        assert j['success'], j
        assert j['data']['imported'] == 1
        assert j['data']['skipped'] == 1

        from models import Shop
        with app.app_context():
            assert Shop.query.count() == 1

    def test_import_overwrite(self, app):
        """overwrite：同名/同证覆盖更新，不新增。"""
        _unlock_keyholder()
        client = _admin_client(app)
        client.post('/shop/api/save', data={'name': '甲超市', 'id_card': ID_CARD, 'street': '旧街'})

        buf = self._make_xlsx([
            ['甲超市', '新街', '9号', '张三', ID_CARD, '13800000001'],
        ])
        r = client.post('/shop/api/import', data={'mode': 'overwrite', 'file': (buf, 'shops.xlsx')},
                        content_type='multipart/form-data')
        j = r.get_json()
        assert j['success'], j
        assert j['data']['updated'] == 1 and j['data']['imported'] == 0

        from models import Shop
        with app.app_context():
            row = Shop.query.first()
            assert row.street == '新街'
            assert Shop.query.count() == 1

    def test_permissions_seeded_for_civil_role(self, app):
        """R-047-01：权限种子必须走真实 app.py 角色（民政主任含 shop 五位 + 页面 200）。"""
        import json
        from models import Role, User, db
        with app.app_context():
            role = Role.query.filter_by(name='民政主任').first()
            assert role is not None
            perms = json.loads(role.permissions or '{}')
            missing = [f'shop.{a}' for a in ('view', 'add', 'edit', 'delete', 'export')
                       if not perms.get(f'shop.{a}')]
            assert not missing, f'民政主任缺少 shop 权限位：{missing}'
            u = User(username='mz_shop', role_id=role.id, is_active=True)
            u.set_password('TestPass123')
            db.session.add(u)
            db.session.commit()
            uid = u.id
        c = app.test_client()
        _login_as(c, uid)
        assert c.get('/shop/').status_code == 200
        assert c.get('/shop/api/list').status_code == 200

    def test_requires_view_permission(self, app):
        """R-047-08：缺 shop.view → 页面/接口 403。"""
        uid = _mk_role_user(app, 'shop_denied', _base_perms())
        c = app.test_client()
        _login_as(c, uid)
        assert c.get('/shop/').status_code == 403
        assert c.get('/shop/api/list').status_code == 403

    def test_export_escapes_formula(self, app):
        """R-047-02/09：= 开头的店铺名导出为文本单元格（非公式），且无可见单引号前缀。"""
        import openpyxl
        _unlock_keyholder()
        client = _admin_client(app)
        client.post('/shop/api/save', data={'name': '=1+1', 'id_card': ID_CARD})
        r = client.get('/shop/api/export')
        ws = openpyxl.load_workbook(io.BytesIO(r.data)).active
        cell = ws.cell(row=2, column=2)
        assert cell.data_type == 's' and cell.value == '=1+1'

    def test_import_rejects_bad_mode_and_file(self, app):
        """R-047-04 + 139：mode 非 append/overwrite、越界扩展名、坏 zip 头均拒绝；CSV 属允许格式。"""
        _unlock_keyholder()
        client = _admin_client(app)
        buf = self._make_xlsx([['甲超市', '街A', '1号', '张三', ID_CARD, '138']])
        r = client.post('/shop/api/import', data={'mode': 'foo', 'file': (buf, 'shops.xlsx')},
                        content_type='multipart/form-data')
        assert r.get_json()['success'] is False and '模式' in r.get_json()['msg']
        # 139：127 起 CSV 等 5 种格式均为允许契约（单一真源 is_allowed_spreadsheet），
        # 原「shops.csv 应被拒」口径作废 —— 改为正向断言 + 用越界扩展名承担拒绝面。
        from utils.spreadsheet_reader import SPREADSHEET_ERR_MSG
        csv_body = ('店铺名称,街道名,门牌号,商户姓名,身份证号,电话\n'
                    f'甲超市,街A,1号,张三,{ID_CARD},138\n').encode('utf-8')
        r = client.post('/shop/api/import',
                        data={'mode': 'append', 'file': (io.BytesIO(csv_body), 'shops.csv')},
                        content_type='multipart/form-data')
        js = r.get_json()
        assert js['success'] is True and js['data']['imported'] == 1, js
        r = client.post('/shop/api/import',
                        data={'mode': 'append', 'file': (io.BytesIO(b'a,b'), 'shops.txt')},
                        content_type='multipart/form-data')
        assert r.get_json()['success'] is False and SPREADSHEET_ERR_MSG in r.get_json()['msg']
        r = client.post('/shop/api/import',
                        data={'mode': 'append', 'file': (io.BytesIO(b'not-a-zip'), 'shops.xlsx')},
                        content_type='multipart/form-data')
        assert r.get_json()['success'] is False and 'zip' in r.get_json()['msg']
