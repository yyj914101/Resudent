# -*- coding: utf-8 -*-
"""tests/test_notice.py — 045 补贴公示（模板 + 月度记录 + 三栏公示栏契约 + 年月切换 + 上传 + 导出 + 权限）

覆盖任务书验收与 2026-11-25 真实公示栏改造：
- 模板闭环（新建/编辑/恢复标准格式/停用/删除；内置模板可删；名称修正）
- 真实 9 列三栏并排公示表（低保 3×3 户主/人口/金额，其余 3×3 组别/户主/金额；表头+表尾备注）
- 按月生成（动态社区+年月副标题、快照隔离、沿用上期名单、同月重复返回既有记录）
- 年月更改（record/update 支持修改 period，自动联动更新副标题年月并拦截同月冲突）
- 本地上传（xlsx/CSV 表头匹配、自动跳过前导合并标题行、单栏 3 列自动折行填充 9 列三栏、未匹配列忽略）
- 记录状态（draft→published）、1:1 真实公示栏 Excel 导出（文件名 y年m月+台账名.xlsx、合并单元格、公式防注入）
- 权限（ADR-016：notice.view/add/edit/delete/export 动作位）
"""
import io
import json
import os
import sys
from urllib.parse import unquote

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
        assert '重度残疾人护理补贴人员明细表' in html
        assert '优待抚恤人员明细表' in html
        # 首次请求触发幂等种子：10 类各 1 条
        data = client.get('/notice/api/templates?include_inactive=1').get_json()['data']
        types = {t['notice_type'] for t in data}
        assert len(types) == 10, f'内置模板应为 10 类，实际 {types}'
        # 幂等：再次请求不重复
        again = client.get('/notice/api/templates?include_inactive=1').get_json()['data']
        assert len(again) == 10

    def test_default_table_shape(self, client):
        from models.notice import default_table
        dibao = default_table('dibao')
        assert dibao['layout'] == 'tri_column'
        assert len(dibao['columns']) == 9
        assert dibao['columns'][0]['label'] == '户主姓名'
        assert dibao['columns'][1]['label'] == '保障\n人口数'
        assert dibao['columns'][2]['label'] == '保障金额\n（元）'
        assert dibao['header_title'] == '补助资金公开表'
        assert len(dibao['footer_notes']) == 2
        assert len(dibao['rows']) == 1 and len(dibao['rows'][0]) == 9

        elderly = default_table('elderly')
        assert len(elderly['columns']) == 9
        assert [c['label'] for c in elderly['columns'][:3]] == ['组别', '户主姓名', '补助金额\n（元）']
        assert '高龄津贴' in elderly['footer_notes'][0]


class TestTemplateCrud:
    def test_create_update_reset_status_delete(self, client, app):
        # 新建（只传类型 → 套内置默认三栏表头）
        res = client.post('/notice/api/template/create',
                          json={'notice_type': 'dibao'}).get_json()
        assert res['success'] is True
        tid = res['data']['item']['id']

        # 表格编辑：改表头 + 增列 + 增行 + 单元格
        schema = res['data']['item']['schema']
        schema['columns'][0]['label'] = '姓名'
        schema['columns'].append({'key': 'c10', 'label': '银行账号', 'width': 160})
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

        # 一键恢复标准三栏公示表格式
        rst = client.post(f'/notice/api/template/reset/{tid}').get_json()
        assert rst['success'] is True
        assert len(rst['data']['item']['schema']['columns']) == 9
        assert rst['data']['item']['schema']['columns'][0]['label'] == '户主姓名'

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
        assert len(res['data']['item']['schema']['columns']) == 9


class TestRecords:
    def test_generate_snapshot_copy_latest_and_duplicate(self, client):
        tpl = _first_template(client)
        res = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-10'}).get_json()
        assert res['success'] is True
        rec = res['data']['item']
        assert rec['status'] == 'draft' and rec['period'] == '2026-10'
        assert rec['title'] == '石垭社区2026年10月享受低保补助人员明细公示'
        assert rec['export_filename'] == '2026年10月低保补助人员明细公示.xlsx'
        assert rec['table_data']['columns'] == tpl['schema']['columns']

        # 填入一条数据后，次月使用 copy_latest=True 沿用上期名单
        td = rec['table_data']
        td['rows'] = [['张三', '2', '800', '', '', '', '', '', '']]
        client.post(f"/notice/api/record/update/{rec['id']}", json={'table_data': td})

        res_next = client.post('/notice/api/record/generate',
                               json={'template_id': tpl['id'], 'period': '2026-11',
                                     'copy_latest': True}).get_json()
        assert res_next['success'] is True
        next_rec = res_next['data']['item']
        assert next_rec['title'] == '石垭社区2026年11月享受低保补助人员明细公示'
        assert next_rec['table_data']['rows'][0][0] == '张三'

        # 模板后续修改不影响已生成记录（快照）
        new_schema = dict(tpl['schema'])
        new_schema['columns'] = [{'key': 'c1', 'label': '改后的列名', 'width': 100}]
        new_schema['rows'] = [['x']]
        client.post(f"/notice/api/template/update/{tpl['id']}",
                    json={'title': '改后的表名', 'schema': new_schema})
        detail = client.get(f"/notice/api/record/{rec['id']}").get_json()['data']
        assert detail['title'] == '石垭社区2026年10月享受低保补助人员明细公示'
        assert detail['table_data']['columns'][0]['label'] != '改后的列名'

        # 同月重复 → 返回既有记录（success False + data.id）
        dup = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-10'}).get_json()
        assert dup['success'] is False and dup['data']['id'] == rec['id']

    def test_update_period_syncs_subtitle_and_blocks_conflict(self, client):
        tpl = _first_template(client)
        rec = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-10'}
                          ).get_json()['data']['item']
        # 更改年份与月份 → 副标题与导出文件名自动联动更新
        upd = client.post(f"/notice/api/record/update/{rec['id']}",
                          json={'period': '2027-03'}).get_json()
        assert upd['success'] is True
        item = upd['data']['item']
        assert item['period'] == '2027-03'
        assert item['title'] == '石垭社区2027年3月享受低保补助人员明细公示'
        assert item['export_filename'] == '2027年3月低保补助人员明细公示.xlsx'

        # 再建 2027-04 记录，尝试把 rec 改成 2027-04 应提示冲突
        client.post('/notice/api/record/generate',
                    json={'template_id': tpl['id'], 'period': '2027-04'})
        conflict = client.post(f"/notice/api/record/update/{rec['id']}",
                               json={'period': '2027-04'}).get_json()
        assert conflict['success'] is False and '已存在' in conflict['msg']

    def test_update_publish_delete(self, client):
        tpl = _first_template(client)
        rec = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-11'}
                          ).get_json()['data']['item']
        table = rec['table_data']
        table['rows'] = [['张三', '3', '900'] + [''] * (len(table['columns']) - 3)]
        upd = client.post(f"/notice/api/record/update/{rec['id']}",
                          json={'title': '石垭社区2026年11月享受低保补助人员明细公示',
                                'table_data': table}).get_json()
        assert upd['success'] is True and upd['data']['item']['title'] == '石垭社区2026年11月享受低保补助人员明细公示'

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
        # Row 1=大标题, Row 2=副标题, Row 3=表头, Row 4=首行数据
        assert ws.cell(row=3, column=1).value == '=危险列名'
        assert ws.cell(row=3, column=1).data_type == 's'
        assert ws.cell(row=4, column=1).value == '=1+1'
        assert ws.cell(row=4, column=1).data_type == 's'
        assert not str(ws.cell(row=4, column=1).value).startswith("'"), '不应有可见单引号前缀'
        assert ws.cell(row=4, column=2).value == '+SUM(A1)'
        assert ws.cell(row=4, column=2).data_type == 's'

    def test_export_excel_matches_publicity_format_and_filename(self, client):
        import openpyxl
        tpl = _first_template(client, 'elderly')
        rec = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-10'}
                          ).get_json()['data']['item']
        td = rec['table_data']
        td['rows'] = [['1组', '余子贞', '120.00', '2组', '邓辉', '40', '3组', '聂积秀', '40']]
        client.post(f"/notice/api/record/update/{rec['id']}", json={'table_data': td})

        r = client.get(f"/notice/api/record/export/{rec['id']}")
        assert r.status_code == 200
        assert r.data[:4] == b'PK\x03\x04'
        cd = unquote(r.headers.get('Content-Disposition', ''))
        assert '2026年10月高龄补贴人员明细表.xlsx' in cd

        wb = openpyxl.load_workbook(io.BytesIO(r.data))
        ws = wb.active
        merged = {str(rng) for rng in ws.merged_cells.ranges}
        assert 'A1:I1' in merged and 'A2:I2' in merged and 'A5:I5' in merged
        assert ws['A1'].value == '补助资金公开表'
        assert ws['A2'].value == '石垭社区2026年10月高龄补贴人员明细表'
        assert ws['A3'].value == '组别' and ws['B3'].value == '户主姓名'
        assert ws['B4'].value == '余子贞' and ws['E4'].value == '邓辉' and ws['H4'].value == '聂积秀'
        assert '高龄津贴' in ws['A5'].value
        assert ws.page_setup.paperSize == int(ws.PAPERSIZE_A4)  # openpyxl 落盘后 paperSize 归一化为 int，类常量为 str '9'
        assert ws.page_setup.orientation == ws.ORIENTATION_PORTRAIT



    def test_export_xls_and_reupload_roundtrip(self, client):
        tpl = _first_template(client, 'dibao')
        rec = client.post('/notice/api/record/generate',
                          json={'template_id': tpl['id'], 'period': '2026-10'}
                          ).get_json()['data']['item']
        td = rec['table_data']
        td['page_size'] = 'a4_landscape'
        td['columns'][0]['width'] = 130
        td['rows'] = [['张三', '2', '860', '李四', '1', '460', '', '', '']]
        client.post(f"/notice/api/record/update/{rec['id']}", json={'table_data': td})

        r = client.get(f"/notice/api/record/export/{rec['id']}?format=xls")
        assert r.status_code == 200
        cd = unquote(r.headers.get('Content-Disposition', ''))
        assert '2026年10月低保补助人员明细公示.xls' in cd
        assert b'Landscape' in r.data and b'FitWidth>1<' in r.data

        # 导出的 .xls 可直接再上传解析回填
        up = client.post('/notice/api/record/import',
                         data={'file': (io.BytesIO(r.data), '2026年10月低保补助人员明细公示.xls'),
                               'columns': json.dumps(tpl['schema']['columns'])},
                         content_type='multipart/form-data').get_json()
        assert up['success'] is True
        assert up['data']['rows'][0][0] == '张三' and up['data']['rows'][0][3] == '李四'


class TestUpload:
    def test_xlsx_publicity_tri_col_skips_titles_and_footer(self, client, app, tmp_path):
        tpl = _first_template(client, 'elderly')
        path = tmp_path / 'elderly_pub.xlsx'
        _write_xlsx(str(path), [
            ['补助资金公开表'],
            ['石垭社区2026年10月高龄补贴人员明细表'],
            ['组别', '户主姓名', '补助金额（元）', '组别', '户主姓名', '补助金额（元）', '组别', '户主姓名', '补助金额（元）'],
            ['1组', '余子贞', '120', '2组', '邓辉', '40', '3组', '聂积秀', '40'],
            ['按上级文件要求，从10月1日起，高龄津贴按照新标准执行：80-89周岁40元'],
        ])
        with open(path, 'rb') as f:
            r = client.post('/notice/api/record/import',
                            data={'file': (io.BytesIO(f.read()), 'elderly_pub.xlsx'),
                                  'columns': json.dumps(tpl['schema']['columns'])},
                            content_type='multipart/form-data')
        d = r.get_json()
        assert d['success'] is True
        assert len(d['data']['rows']) == 1
        assert d['data']['rows'][0][1] == '余子贞' and d['data']['rows'][0][4] == '邓辉'
        assert d['data']['footer_notes'] and '高龄津贴' in d['data']['footer_notes'][0]

    def test_xlsx_single_col_auto_packs_into_tri_col(self, client, app, tmp_path):
        tpl = _first_template(client, 'dibao')
        path = tmp_path / 'single_dibao.xlsx'
        _write_xlsx(str(path), [
            ['户主姓名', '保障人口数', '保障金额（元）', '陌生列'],
            ['张三', '2', '800', 'x'],
            ['李四', '1', '450', 'y'],
            ['王五', '3', '1200', 'z'],
            ['赵六', '1', '400', 'w'],
        ])
        with open(path, 'rb') as f:
            r = client.post('/notice/api/record/import',
                            data={'file': (io.BytesIO(f.read()), 'single_dibao.xlsx'),
                                  'columns': json.dumps(tpl['schema']['columns'])},
                            content_type='multipart/form-data')
        d = r.get_json()
        assert d['success'] is True
        assert '陌生列' in d['data']['unmatched']
        assert len(d['data']['rows']) == 2
        assert d['data']['rows'][0] == ['张三', '2', '800', '李四', '1', '450', '王五', '3', '1200']
        assert d['data']['rows'][1] == ['赵六', '1', '400', '', '', '', '', '', '']

    def test_csv_gb18030(self, client, app):
        tpl = _first_template(client)
        csv_text = '户主姓名,保障人口数,保障金额（元）\n李四,2,600\n'
        raw = csv_text.encode('gb18030')
        r = client.post('/notice/api/record/import',
                        data={'file': (io.BytesIO(raw), 'fill.csv'),
                              'columns': json.dumps(tpl['schema']['columns'])},
                        content_type='multipart/form-data')
        d = r.get_json()
        assert d['success'] is True and d['data']['rows'][0][0] == '李四'

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

class TestCustomCatalog:
    def test_create_custom_group_and_table_under_existing_group(self, client):
        # 1) 新增自定义大类分组「资产类」（纯建分类）
        g_res = client.post('/notice/api/group/create',
                            json={'group_name': '资产类'}).get_json()
        assert g_res['success'] is True
        labels = [g['label'] for g in g_res['data']['groups']]
        assert '资产类' in labels

        # 2) 在「资产类」下新建公示表
        t1 = client.post('/notice/api/type/create',
                         json={'group_name': '资产类',
                               'table_name': '集体资产分红人员明细表',
                               'preset': 'common'}).get_json()
        assert t1['success'] is True
        key1 = t1['data']['type']['key']
        tpl1 = t1['data']['template']
        assert tpl1['notice_type'] == key1
        assert len(tpl1['schema']['columns']) == 9

        # 3) 在已有「福利类」下新建自定义公示表
        t2 = client.post('/notice/api/type/create',
                         json={'group_name': '福利类',
                               'table_name': '高龄老人慰问补贴明细表',
                               'preset': 'common'}).get_json()
        assert t2['success'] is True
        welfare_group = next(g for g in t2['data']['groups'] if g['label'] == '福利类')
        assert any(item['name'] == '高龄老人慰问补贴明细表' for item in welfare_group['types'])

        # 4) 自定义公示表可按月生成并按「y年m月+台账名」导出
        rec = client.post('/notice/api/record/generate',
                          json={'template_id': tpl1['id'], 'period': '2026-10'}
                          ).get_json()['data']['item']
        assert rec['title'] == '石垭社区2026年10月集体资产分红人员明细表'
        assert rec['export_filename'] == '2026年10月集体资产分红人员明细表.xlsx'
        exp = client.get(f"/notice/api/record/export/{rec['id']}?format=xlsx")
        assert exp.status_code == 200
        assert '2026年10月集体资产分红人员明细表.xlsx' in unquote(exp.headers.get('Content-Disposition', ''))

