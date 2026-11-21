# -*- coding: utf-8 -*-
"""tests/test_notice.py — 045 补贴公示（模板 + 月度记录 + 表格数据契约 + 上传 + 权限）

覆盖任务书验收 1–7：
- 模板闭环（新建/编辑/停用/删除；内置模板可删）
- 表格编辑（增删行列 / 表头 / 单元格；畸形输入归一化）
- 按月生成（快照：模板后改不影响历史；同月重复给出既有记录）
- 本地上传（xlsx/CSV 表头匹配、未匹配列忽略、无可匹配列拒绝）
- 记录状态（draft→published）、Excel 导出（可打开、内容一致）
- 权限（ADR-016：notice.view/add/edit/delete/export 动作位）
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    for key, val in {
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_LAN_AUTHORIZED": "1",
        "SHUCUN_DISABLE_PERIODIC": "1",
        "SHUCUN_DISABLE_PASSWORD_CHANGE": "1",
    }.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    for d in ("uploads", "backups", "logs", "temp", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


@pytest.fixture()
def client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = 1  # admin
    return c


def _mk_user(app, username, perms):
    from models import db, Role, User
    with app.app_context():
        role = Role(name='公示测试角色', permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.commit()
        u = User(username=username, role_id=role.id, is_active=True)
        u.set_password('TestPass123')
        db.session.add(u)
        db.session.commit()
        return u.id


def _login_as(client, user_id):
    with client.session_transaction() as s:
        s['user_id'] = user_id


def _open_perms(**overrides):
    perms = {'__schema_version__': 3,
             'notice.view': True, 'notice.add': True, 'notice.edit': True,
             'notice.delete': True, 'notice.export': True,
             'special': {'view_sensitive': False, 'death_confirm': False,
                         'import_batch': False, 'backup_restore': False,
                         'mask_config': False}}
    perms.update(overrides)
    return perms


def _write_xlsx(path, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)


def _first_template(client, notice_type='dibao'):
    res = client.get(f'/notice/api/templates?notice_type={notice_type}').get_json()
    return res['data'][0]


class TestSeedAndPage:
    def test_page_and_builtin_seed(self, client):
        r = client.get('/notice/')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert '补贴公示' in html and '救助类' in html and '优抚就业类' in html
        # 首次请求触发幂等种子：10 类各 1 条
        data = client.get('/notice/api/templates?include_inactive=1').get_json()['data']
        types = {t['notice_type'] for t in data}
        assert len(types) == 10, f'内置模板应为 10 类，实际 {types}'
        # 幂等：再次请求不重复
        again = client.get('/notice/api/templates?include_inactive=1').get_json()['data']
        assert len(again) == 10

    def test_default_table_shape(self, client):
        from models.notice import default_table
        table = default_table('dibao')
        assert table['columns'][0]['label'] == '序号'
        assert len(table['rows']) == 1 and len(table['rows'][0]) == len(table['columns'])


class TestTemplateCrud:
    def test_create_update_status_delete(self, client, app):
        from models import NoticeTemplate, db
        # 新建（只传类型 → 套内置默认表头）
        res = client.post('/notice/api/template/create',
                          json={'notice_type': 'dibao'}).get_json()
        assert res['success'] is True
        tid = res['data']['item']['id']

        # 表格编辑：改表头 + 增列 + 增行 + 单元格
        schema = res['data']['item']['schema']
        schema['columns'][0]['label'] = '姓名'
        schema['columns'].append({'key': 'c9', 'label': '银行账号', 'width': 160})
        schema['rows'][0][0] = '张三'
        schema['rows'].append(['李四'] + [''] * (len(schema['columns']) - 1))
        upd = client.post(f'/notice/api/template/update/{tid}',
                          json={'name': '低保公示（自定义）', 'title': '2026年10月低保公示',
                                'schema': schema}).get_json()
        assert upd['success'] is True
        assert upd['data']['item']['title'] == '2026年10月低保公示'
        assert upd['data']['item']['version'] == 2
        assert upd['data']['item']['schema']['columns'][0]['label'] == '姓名'
        assert upd['data']['item']['schema']['rows'][1][0] == '李四'

        # 停用 → 默认列表不含，include_inactive 含
        client.post(f'/notice/api/template/status/{tid}', json={'is_active': False})
        active = client.get('/notice/api/templates?notice_type=dibao').get_json()['data']
        assert tid not in [t['id'] for t in active]
        all_tpl = client.get('/notice/api/templates?notice_type=dibao&include_inactive=1'
                             ).get_json()['data']
        assert tid in [t['id'] for t in all_tpl]

        # 内置模板可删（拍板⑥）——删除后列表不再出现
        builtin_id = _first_template(client)['id']
        del_res = client.post(f'/notice/api/template/delete/{builtin_id}').get_json()
        assert del_res['success'] is True
        remaining = client.get('/notice/api/templates?notice_type=dibao&include_inactive=1'
                               ).get_json()['data']
        assert builtin_id not in [t['id'] for t in remaining]

    def test_delete_blocked_when_records_exist(self, client):
        tpl = _first_template(client)
        client.post('/notice/api/record/generate',
                    json={'template_id': tpl['id'], 'period': '2026-10'})
        res = client.post(f"/notice/api/template/delete/{tpl['id']}").get_json()
        assert res['success'] is False and '记录' in res['msg']

    def test_malformed_schema_falls_back(self, client):
        tpl = _first_template(client)
        res = client.post(f"/notice/api/template/update/{tpl['id']}",
                          json={'schema': {'columns': [], 'rows': 'x'}}).get_json()
        assert res['success'] is True
        assert len(res['data']['item']['schema']['columns']) >= 1


class TestRecords:
    def test_generate_snapshot_and_duplicate(self, client):
        tpl = _first_template(client)
        res = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-10'}).get_json()
        assert res['success'] is True
        rec = res['data']['item']
        assert rec['status'] == 'draft' and rec['period'] == '2026-10'
        assert rec['title'] == tpl['title']
        assert rec['table_data']['columns'] == tpl['schema']['columns']

        # 模板后续修改不影响已生成记录（快照）
        new_schema = dict(tpl['schema'])
        new_schema['columns'] = [{'key': 'c1', 'label': '改后的列名', 'width': 100}]
        new_schema['rows'] = [['x']]
        client.post(f"/notice/api/template/update/{tpl['id']}",
                    json={'title': '改后的表名', 'schema': new_schema})
        detail = client.get(f"/notice/api/record/{rec['id']}").get_json()['data']
        assert detail['title'] == tpl['title']
        assert detail['table_data']['columns'][0]['label'] != '改后的列名'

        # 同月重复 → 返回既有记录（success False + data.id）
        dup = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-10'}).get_json()
        assert dup['success'] is False and dup['data']['id'] == rec['id']

    def test_update_publish_delete(self, client):
        tpl = _first_template(client)
        rec = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-11'}
                          ).get_json()['data']['item']
        table = rec['table_data']
        table['rows'] = [['1', '张三'] + [''] * (len(table['columns']) - 2)]
        upd = client.post(f"/notice/api/record/update/{rec['id']}",
                          json={'title': '11月低保公示', 'table_data': table}).get_json()
        assert upd['success'] is True and upd['data']['item']['title'] == '11月低保公示'

        pub = client.post(f"/notice/api/record/status/{rec['id']}",
                          json={'status': 'published'}).get_json()
        assert pub['data']['item']['status'] == 'published'
        listed = client.get('/notice/api/records?notice_type=dibao&period=2026-11'
                            ).get_json()['data']
        assert listed[0]['status'] == 'published'

        assert client.post(f"/notice/api/record/delete/{rec['id']}").get_json()['success']

    def test_export_escapes_formula_cells(self, client):
        """R-045-02/04：= + - @ 开头导出为文本（data_type='s'）且**不加可见单引号**。"""
        import openpyxl
        tpl = _first_template(client)
        rec = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2027-01'}
                          ).get_json()['data']['item']
        table = rec['table_data']
        table['columns'][0]['label'] = '=危险列名'
        table['rows'] = [['=1+1', '+SUM(A1)', '-2', '@cmd'] +
                         [''] * max(0, len(table['columns']) - 4)]
        client.post(f"/notice/api/record/update/{rec['id']}",
                    json={'table_data': table})
        r = client.get(f"/notice/api/record/export/{rec['id']}")
        ws = openpyxl.load_workbook(io.BytesIO(r.data)).active
        assert ws.cell(row=2, column=1).value == '=危险列名'
        assert ws.cell(row=2, column=1).data_type == 's'
        assert ws.cell(row=3, column=1).value == '=1+1'
        assert ws.cell(row=3, column=1).data_type == 's'
        assert not str(ws.cell(row=3, column=1).value).startswith("'"), '不应有可见单引号前缀'
        assert ws.cell(row=3, column=2).value == '+SUM(A1)'
        assert ws.cell(row=3, column=2).data_type == 's'

    def test_export_excel(self, client):
        import openpyxl
        tpl = _first_template(client)
        rec = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-12'}
                          ).get_json()['data']['item']
        r = client.get(f"/notice/api/record/export/{rec['id']}")
        assert r.status_code == 200
        assert r.data[:4] == b'PK\x03\x04'
        wb = openpyxl.load_workbook(io.BytesIO(r.data))
        ws = wb.active
        assert ws.cell(row=1, column=1).value == rec['title']
        assert ws.cell(row=2, column=1).value == rec['table_data']['columns'][0]['label']


class TestUpload:
    def test_xlsx_header_match_and_unmatched(self, client, app, tmp_path):
        tpl = _first_template(client)
        labels = [c['label'] for c in tpl['schema']['columns']]
        path = tmp_path / 'fill.xlsx'
        _write_xlsx(str(path), [labels + ['陌生列'], ['1', '张三'] + [''] * (len(labels) - 2) + ['x']])
        with open(path, 'rb') as f:
            r = client.post('/notice/api/record/import',
                            data={'file': (io.BytesIO(f.read()), 'fill.xlsx'),
                                  'columns': json.dumps(tpl['schema']['columns'])},
                            content_type='multipart/form-data')
        d = r.get_json()
        assert d['success'] is True
        assert d['data']['rows'][0][1] == '张三'
        assert '陌生列' in d['data']['unmatched']
        assert len(d['data']['matched']) == len(labels)

    def test_csv_gb18030(self, client, app):
        tpl = _first_template(client)
        labels = [c['label'] for c in tpl['schema']['columns']]
        csv_text = ','.join(labels) + '\n' + ','.join(['1', '李四'] + [''] * (len(labels) - 2))
        raw = csv_text.encode('gb18030')
        r = client.post('/notice/api/record/import',
                        data={'file': (io.BytesIO(raw), 'fill.csv'),
                              'columns': json.dumps(tpl['schema']['columns'])},
                        content_type='multipart/form-data')
        d = r.get_json()
        assert d['success'] is True and d['data']['rows'][0][1] == '李四'

    def test_no_matching_header_rejected(self, client):
        tpl = _first_template(client)
        raw = '甲,乙\n1,2\n'.encode('utf-8')
        r = client.post('/notice/api/record/import',
                        data={'file': (io.BytesIO(raw), 'x.csv'),
                              'columns': json.dumps(tpl['schema']['columns'])},
                        content_type='multipart/form-data')
        d = r.get_json()
        assert d['success'] is False and '没有' in d['msg']

    def test_bad_extension_rejected(self, client):
        tpl = _first_template(client)
        r = client.post('/notice/api/record/import',
                        data={'file': (io.BytesIO(b'abc'), 'x.txt'),
                              'columns': json.dumps(tpl['schema']['columns'])},
                        content_type='multipart/form-data')
        assert r.get_json()['success'] is False


class TestSeededRole:
    """R-045-01（P2）：权限种子必须落在唯一有调用方的 app.py，且业务角色可真正进入模块。"""

    def test_notice_permissions_seeded_for_civil_role(self, app):
        from models import Role, db, User
        with app.app_context():
            role = Role.query.filter_by(name='民政主任').first()
            assert role is not None, '新建库应预置民政主任角色'
            perms = json.loads(role.permissions or '{}')
            missing = [f'notice.{a}' for a in ('view', 'add', 'edit', 'delete', 'export')
                       if not perms.get(f'notice.{a}')]
            assert not missing, f'民政主任缺少 notice 权限位：{missing}'
            u = User(username='mz_notice', role_id=role.id, is_active=True)
            u.set_password('TestPass123')
            db.session.add(u)
            db.session.commit()
            uid = u.id
        c = app.test_client()
        _login_as(c, uid)
        # 修复前：无 notice.* → 全局鉴权 403；修复后：200
        assert c.get('/notice/').status_code == 200
        assert c.get('/notice/api/templates').status_code == 200


class TestPermissions:
    def test_view_only_user_cannot_write(self, app, client):
        uid = _mk_user(app, 'notice_viewer', _open_perms(
            **{'notice.add': False, 'notice.edit': False, 'notice.delete': False}))
        c = app.test_client()
        _login_as(c, uid)
        tpl = _first_template(c)
        assert c.get('/notice/api/templates').status_code == 200
        assert c.post('/notice/api/template/update/%d' % tpl['id'],
                      json={'name': 'x'}).status_code == 403
        assert c.post('/notice/api/record/generate',
                      json={'template_id': tpl['id'], 'period': '2026-10'}).status_code == 403
        assert c.post(f"/notice/api/template/delete/{tpl['id']}").status_code == 403

    def test_no_add_cannot_import(self, app):
        uid = _mk_user(app, 'notice_no_add', _open_perms(**{'notice.add': False}))
        c = app.test_client()
        _login_as(c, uid)
        r = c.post('/notice/api/record/import',
                   data={'file': (io.BytesIO(b'PK\x03\x04'), 'x.xlsx'), 'columns': '[]'},
                   content_type='multipart/form-data')
        assert r.status_code == 403

    def test_no_view_cannot_render(self, app):
        perms = _open_perms(**{'notice.view': False})
        uid = _mk_user(app, 'notice_no_view', perms)
        c = app.test_client()
        _login_as(c, uid)
        assert c.get('/notice/').status_code == 403
