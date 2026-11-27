# -*- coding: utf-8 -*-
"""tests/test_096_softtable_defects.py — 096 软表缺陷三修

覆盖（对应 .trae/specs/fix-096-softtable-defects/spec.md）：
1. 枚举 label≠value：页面下发 value/label 对、按 value 筛选命中、编辑提交 label 落 value
2. 建表/导入口径：分类存在但条目全停用 → 空行与含行建表均 400 且同一文案、零落库
3. 权限面板：permissionModules 键集覆盖 BLUEPRINT_MODULES 值集；非超管授予 dynatable 可建表
"""
import io
import json
import os
import re

import pytest

HEADERS = ['姓名', '参保状态']


# ------------------------------------------------------------------ 夹具/工具

@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


def _seed_category(app, name, pairs, active=True):
    """建字典分类与条目（pairs = [(label, value), ...]）。"""
    with app.app_context():
        from models import DictCategory, DictItem, db
        cat = DictCategory(name=name, description='096 用例')
        db.session.add(cat)
        db.session.flush()
        for i, (label, value) in enumerate(pairs):
            db.session.add(DictItem(category_id=cat.id, label=label, value=value,
                                    sort_order=i, is_active=active))
        db.session.commit()


def _login(client, user_id, username='admin'):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _admin_id(app):
    with app.app_context():
        from models import User
        return User.query.filter_by(username='admin').first().id


def _xlsx_bytes(headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _upload(client, rows, headers=HEADERS):
    return client.post('/dynatable/api/upload',
                       data={'file': (_xlsx_bytes(headers, rows), 'sample.xlsx')},
                       content_type='multipart/form-data')


def _fields(category='参保状态'):
    return [
        {'code': 'f00', 'display_name': '姓名', 'data_type': 'text'},
        {'code': 'f01', 'display_name': '参保状态', 'data_type': 'enum',
         'dict_category_code': category},
    ]


def _create(client, token, code, fields, module_key='pension'):
    return client.post('/dynatable/api/create',
                       json={'token': token, 'code': code, 'name': '096 台账',
                             'module_key': module_key, 'fields': fields})


def _extract_list_fields(html):
    """从列表页内联脚本取 fields 数组（外层是 JS 对象，键无引号，故只取数组）。"""
    match = re.search(r'fields:\s*(\[[\s\S]*\])\s*\}\s*;', html)
    assert match, '台账列表页必须下发 window.__ST_LIST__.fields'
    return json.loads(match.group(1))


# ------------------------------------------------------------------ 1. 枚举 label≠value

def test_enum_label_value_same_source(app):
    """R-078-05：存储 value / 展示 label / 筛选按 value / 编辑落 value。"""
    _seed_category(app, '参保状态', [('正常参保', '01'), ('暂停参保', '02')])
    client = app.test_client()
    _login(client, _admin_id(app))

    resp = _upload(client, [['张三', '正常参保'], ['李四', '暂停参保'], ['王五', '正常参保']])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    created = _create(client, resp.get_json()['data']['token'], 'st_enum', _fields())
    assert created.status_code == 200, created.get_data(as_text=True)

    page = client.get('/dynatable/t/st_enum')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    page_fields = _extract_list_fields(html)
    enum_field = [f for f in page_fields if f['code'] == 'f01'][0]
    options = enum_field['options']
    assert {'value': '01', 'label': '正常参保'} in options
    assert {'value': '02', 'label': '暂停参保'} in options
    # R-096-02：筛选下拉必须是 value=value / 文本=label（动态页不在 SSR 快照集）
    assert '<option value="01">正常参保</option>' in html

    hit = client.get('/dynatable/api/t/st_enum/records?f_f01=01').get_json()
    assert hit['success'] and hit['total'] == 2, '按 value 筛选必须命中 2 行'
    rid = hit['data'][0]['id']

    saved = client.post('/dynatable/api/t/st_enum/record/%d' % rid,
                        json={'code': 'f01', 'value': '暂停参保'})
    assert saved.get_json()['success'], saved.get_data(as_text=True)
    with app.app_context():
        from models import DynamicRecord
        rec = DynamicRecord.query.get(rid)
        data = json.loads(rec.data)
        assert data['f01'] == '02', '编辑提交 label 必须落 value'
        assert '暂停参保' not in rec.data


# ------------------------------------------------------------------ 2. 建表/导入口径

def test_inactive_category_same_error_and_no_write(app):
    """R-078-12：分类存在但全停用时，空行/含行建表同码同文案且零落库。"""
    _seed_category(app, '停用状态', [('正常参保', '01'), ('暂停参保', '02')], active=False)
    client = app.test_client()
    _login(client, _admin_id(app))

    empty_up = _upload(client, [])
    rows_up = _upload(client, [['张三', '正常参保']])
    assert empty_up.status_code == 200 and rows_up.status_code == 200
    from models import DynamicField, DynamicRecord
    with app.app_context():
        fields_before, records_before = DynamicField.query.count(), DynamicRecord.query.count()

    empty = _create(client, empty_up.get_json()['data']['token'],
                    'st_inactive_empty', _fields('停用状态'))
    rows = _create(client, rows_up.get_json()['data']['token'],
                   'st_inactive_rows', _fields('停用状态'))
    assert empty.status_code == 400, empty.get_data(as_text=True)
    assert rows.status_code == 400, rows.get_data(as_text=True)
    empty_msg = empty.get_json()['msg']
    rows_msg = rows.get_json()['msg']
    assert '没有可用选项' in empty_msg
    assert empty_msg == rows_msg, '建表与导入必须同一文案：%r / %r' % (empty_msg, rows_msg)

    with app.app_context():
        from models import DynamicTable
        assert DynamicTable.query.filter_by(code='st_inactive_empty').first() is None
        assert DynamicTable.query.filter_by(code='st_inactive_rows').first() is None
        # 141（C-2）：启动即初始化 8 张综治固定表（77 字段），「全新库零字段」前提不再成立 → 断言前后不变
        assert DynamicField.query.count() == fields_before
        assert DynamicRecord.query.count() == records_before


# ------------------------------------------------------------------ 3. 权限面板

def _role_mgmt_js_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, 'static', 'js', 'pages', 'role_mgmt_list.js')


def test_permission_panel_covers_all_blueprint_modules():
    """R-078-06：面板模块键集必须覆盖 BLUEPRINT_MODULES 值集。"""
    from utils.permissions import BLUEPRINT_MODULES

    src = open(_role_mgmt_js_path(), encoding='utf-8').read()
    match = re.search(r'var permissionModules = \{([\s\S]*?)\};', src)
    assert match, 'role_mgmt_list.js 必须声明 permissionModules'
    keys = set(re.findall(r"'([^']+)'\s*:", match.group(1)))
    missing = set(BLUEPRINT_MODULES.values()) - keys
    assert not missing, '权限面板缺少模块键：%s' % sorted(missing)


def test_non_admin_with_dynatable_can_create(app):
    """非超管授予 dynatable.view/add 后应能上传并建表（权限面板扩面防回归）。"""
    _seed_category(app, '参保状态', [('正常参保', '01'), ('暂停参保', '02')])
    with app.app_context():
        from models import Role, User, db
        role = Role(name='st096_clerk', description='096 用例',
                    permissions=json.dumps({'dynatable.view': True, 'dynatable.add': True}))
        db.session.add(role)
        db.session.commit()
        user = User(username='st096_clerk', role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        uid = user.id

    client = app.test_client()
    _login(client, uid, 'st096_clerk')
    resp = _upload(client, [['张三', '正常参保']])
    assert resp.status_code == 200, resp.get_data(as_text=True)
    created = _create(client, resp.get_json()['data']['token'], 'st_clerk_table', _fields())
    assert created.status_code == 200, created.get_data(as_text=True)

    with app.app_context():
        from models import DynamicTable
        assert DynamicTable.query.filter_by(code='st_clerk_table').first() is not None


# ------------------------------------------------------------------ 4. 记录导出（返工 A / R-096-01）

def _export_cells(resp):
    """解析导出 xlsx → 二维单元格列表（首行为表头）。"""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.data), read_only=True, data_only=True)
    ws = wb.active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _create_enum_table(app, client, rows, code='st_enum'):
    _seed_category(app, '参保状态', [('正常参保', '01'), ('暂停参保', '02')])
    up = _upload(client, rows)
    assert up.status_code == 200, up.get_data(as_text=True)
    created = _create(client, up.get_json()['data']['token'], code, _fields())
    assert created.status_code == 200, created.get_data(as_text=True)


def test_export_enum_outputs_label(app):
    """导出 enum 列输出 label，不得出现 value 编码。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    _create_enum_table(app, client,
                       [['张三', '正常参保'], ['李四', '暂停参保'], ['王五', '正常参保']])

    resp = client.get('/dynatable/api/t/st_enum/export')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert 'spreadsheetml' in resp.content_type
    assert 'attachment' in resp.headers.get('Content-Disposition', '')

    sheet = _export_cells(resp)
    assert sheet[0] == ['姓名', '参保状态']
    values = [row[1] for row in sheet[1:]]
    assert values == ['正常参保', '暂停参保', '正常参保']
    assert '01' not in values and '02' not in values


def test_export_respects_filters_and_exports_all_rows(app):
    """筛选导出行数 = records 命中数；无筛选导出全量（不受分页限制）。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    _create_enum_table(app, client,
                       [['张三', '正常参保'], ['李四', '暂停参保'], ['王五', '正常参保']])

    full = _export_cells(client.get('/dynatable/api/t/st_enum/export'))
    hits = client.get('/dynatable/api/t/st_enum/records?limit=1').get_json()
    assert len(full) - 1 == hits['total'] == 3

    for query in ('f_f01=01', 'f_f00=张三'):
        sheet = _export_cells(client.get('/dynatable/api/t/st_enum/export?' + query))
        expect = client.get('/dynatable/api/t/st_enum/records?' + query).get_json()['total']
        assert len(sheet) - 1 == expect, '%s 导出行数与列表不一致' % query
    assert len(_export_cells(client.get('/dynatable/api/t/st_enum/export?f_f01=01'))) == 3


def test_export_empty_table_header_only(app):
    """空表导出：200、表头正确、0 数据行。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    _create_enum_table(app, client, [])

    resp = client.get('/dynatable/api/t/st_enum/export')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _export_cells(resp) == [['姓名', '参保状态']]


def test_export_requires_export_permission(app):
    """非超管仅有 dynatable.view（无 export）→ 导出 403。"""
    admin = app.test_client()
    _login(admin, _admin_id(app))
    _create_enum_table(app, admin, [['张三', '正常参保']])

    with app.app_context():
        from models import Role, User, db
        role = Role(name='st096_viewer', description='096 导出用例',
                    permissions=json.dumps({'dynatable.view': True}))
        db.session.add(role)
        db.session.commit()
        user = User(username='st096_viewer', role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        uid = user.id

    client = app.test_client()
    _login(client, uid, 'st096_viewer')
    resp = client.get('/dynatable/api/t/st_enum/export')
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert resp.get_json()['success'] is False


def test_export_identity_masked_for_non_admin(app):
    """R-096b-02：identity 字段导出——非敏感用户掩码且文件名不带「含明文_」；admin 明文且带「含明文_」。"""
    from urllib.parse import unquote
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令096', salt=generate_salt())
    admin = app.test_client()
    _login(admin, _admin_id(app))
    _seed_category(app, '参保状态', [('正常参保', '01')])
    up = _upload(admin, [['张三', '正常参保', '110101199001011234']],
                 headers=['姓名', '参保状态', '身份证号'])
    assert up.status_code == 200, up.get_data(as_text=True)
    fields = _fields() + [{'code': 'f02', 'display_name': '身份证号',
                           'data_type': 'text', 'sensitivity': 'identity'}]
    created = _create(admin, up.get_json()['data']['token'], 'st_ident', fields)
    assert created.status_code == 200, created.get_data(as_text=True)

    # admin 导出：identity 明文 + 文件名带「含明文_」
    resp = admin.get('/dynatable/api/t/st_ident/export')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert '含明文_' in unquote(resp.headers.get('Content-Disposition', ''))
    sheet = _export_cells(resp)
    assert sheet[0] == ['姓名', '参保状态', '身份证号']
    assert sheet[1][2] == '110101199001011234'

    # 非敏感用户（view+export，无 view_sensitive）：掩码 + 文件名不带「含明文_」
    with app.app_context():
        from models import Role, User, db
        role = Role(name='st096_export_nosens', description='096 identity 导出',
                    permissions=json.dumps({'dynatable.view': True, 'dynatable.export': True}))
        db.session.add(role)
        db.session.commit()
        user = User(username='st096_export_nosens', role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        uid = user.id

    client = app.test_client()
    _login(client, uid, 'st096_export_nosens')
    resp2 = client.get('/dynatable/api/t/st_ident/export')
    assert resp2.status_code == 200, resp2.get_data(as_text=True)
    assert '含明文_' not in unquote(resp2.headers.get('Content-Disposition', ''))
    sheet2 = _export_cells(resp2)
    assert sheet2[1][2] == '110***********1234'
