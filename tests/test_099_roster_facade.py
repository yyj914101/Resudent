# -*- coding: utf-8 -*-
"""tests/test_099_roster_facade.py — 099 软表挂载门面（roster facade / shop 薄路由 / m031）

覆盖（docs/tasks/099-roster-facade.md 验收标准中可自动化部分）：
A. 门面页与引导页：m031 预置未建表 → 200 引导页（含「尚未建立」+ 上传入口）；
   未知 code → 404；跨模块 code（module_key 非 shop）→ 404
B. 建表闭环：CSV 上传（身份证列推断 identity）→ 确认字段建表 → 落库行数正确；
   重复建同 code → 409；建表后台账页含表名
C. list 与 record CRUD：金额区间/枚举 value/identity 指纹筛选；新增打码；
   掩码回填不覆盖密文；改 identity 明文后打码变化；记录不存在 success=False
D. 权限归属：shop.view 角色可开门面页；无 shop 权限 403；未登录 302
E. 导出：admin 明文 + enum 出 label + operation_logs 留痕；无 view_sensitive 打码
F. m031 幂等：首跑 inserted=7、二跑 skipped=7 且行数不变、dynamic_field 恒 0；
   库无 dynamic_table → RuntimeError
"""
import importlib.util
import io
import json
import os
import sqlite3

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_M031_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm031_roster_metadata.py')

HEADERS = ['店铺名称', '负责人', '身份证号', '缴费类型', '金额']
ROWS = [
    ['旺铺超市', '张三', '110101199001011234', '物业费', '800'],
    ['便民理发', '李四', '110101199001011235', '水电费', '1200.5'],
    ['旺铺分店', '张三', '110101199001011234', '物业费', '3000'],
]
IDENT = '110101199001011234'
CODE = 'shop_facility'


# ------------------------------------------------------------------ 夹具/工具

def _load_m031():
    """importlib 加载迁移脚本（scripts/ 非包；app.py m025 同款方式）。"""
    spec = importlib.util.spec_from_file_location('m031_roster_metadata_under_test', _M031_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def app(isolated_app_factory):
    """隔离应用 + 密钥解锁 + m031 元数据预置（与部署顺序一致：create_all → m031）
    + 枚举字典分类「缴费类型」（建表确认页必须挂真实 dict_items 分类）。"""
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('roster-facade-test', salt=generate_salt())
    stats = _load_m031().run(os.environ['SHUCUN_DB_PATH'])
    assert len(stats['inserted']) == 7, 'm031 预置 7 张台账必须成功：%s' % stats
    with application.app_context():
        from models import DictCategory, DictItem, db
        cat = DictCategory(name='缴费类型', description='099 用例')
        db.session.add(cat)
        db.session.flush()
        for i, (label, value) in enumerate((('物业费', '01'), ('水电费', '02'))):
            db.session.add(DictItem(category_id=cat.id, label=label, value=value,
                                    sort_order=i, is_active=True))
        db.session.commit()
    return application


def _login(client, user_id, username='admin'):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _admin_id(app):
    with app.app_context():
        from models import User
        return User.query.filter_by(username='admin').first().id


def _make_role_user(app, username, permissions, role_name=None):
    """建角色 + 用户（默认无任何模块权限，除非 permissions 给出）。返回 user.id。"""
    with app.app_context():
        from models import Role, User, db
        role = Role(name=role_name or ('role_%s' % username), description='099 用例',
                    permissions=json.dumps(permissions))
        db.session.add(role)
        db.session.commit()
        user = User(username=username, role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        return user.id


def _csv_bytes(headers, rows):
    lines = [','.join(headers)]
    lines += [','.join(row) for row in rows]
    buf = io.BytesIO(('\r\n'.join(lines) + '\r\n').encode('utf-8-sig'))
    buf.seek(0)
    return buf


def _upload(client, code=CODE, headers=HEADERS, rows=ROWS, filename='roster.csv'):
    return client.post('/shop/roster/%s/api/upload' % code,
                       data={'file': (_csv_bytes(headers, rows), filename)},
                       content_type='multipart/form-data')


def _confirm_fields(suggestions):
    """上传建议 → 确认页提交规格：负责人改 text（低基数误报枚举）、缴费类型挂真实分类。"""
    fields = [dict(s) for s in suggestions]
    for f in fields:
        if f['display_name'] == '负责人':
            f['data_type'] = 'text'
        if f['display_name'] == '缴费类型':
            f['data_type'] = 'enum'
            f['dict_category_code'] = '缴费类型'
    return fields


def _create(client, token, fields, code=CODE, name='基础设施分布'):
    return client.post('/shop/roster/%s/api/create' % code,
                       json={'token': token, 'name': name, 'fields': fields})


def _build_table(client, code=CODE, name='基础设施分布'):
    """CSV 上传 + 确认建表闭环（断言两步均成功），返回 (上传data, 建表json)。"""
    up = _upload(client, code)
    assert up.status_code == 200, up.get_data(as_text=True)
    body = up.get_json()
    assert body['success'], body
    created = _create(client, body['data']['token'],
                      _confirm_fields(body['data']['suggestions']),
                      code=code, name=name)
    assert created.status_code == 200, created.get_data(as_text=True)
    assert created.get_json()['success'], created.get_json()
    return body['data'], created.get_json()


def _list(client, code=CODE, query=''):
    return client.get('/shop/roster/%s/api/list%s' % (code, query)).get_json()


def _record_cipher(app, rid, code='f02'):
    """直读库中 identity 密文（绕过展示层打码）。"""
    with app.app_context():
        from models import DynamicRecord
        rec = DynamicRecord.query.get(rid)
        return json.loads(rec.data)[code]


def _export_cells(resp):
    """解析导出 xlsx → 二维单元格列表（首行为表头）。"""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.data), read_only=True, data_only=True)
    ws = wb.active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return rows


def _sqlite_count(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------------ A. 门面页与引导页

class TestFacadePages:
    """验收 A：m031 预置未建表 → 引导页；未知/跨模块 code → 404。"""

    def test_preset_tables_show_bootstrap(self, app):
        """m031 预置空行（无字段行）按未建表处理：200 引导页 + 上传入口。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        for code in ('shop_facility', 'shop_annual_fee'):
            resp = client.get('/shop/roster/%s/' % code)
            assert resp.status_code == 200, resp.get_data(as_text=True)
            html = resp.get_data(as_text=True)
            assert '尚未建立' in html, '%s 引导页必须提示未建表' % code
            assert '上传' in html, '引导页必须给出上传入口'
            assert '/shop/roster/%s/api/upload' % code in html, '引导页必须下发门面上传端点'

    def test_unknown_code_404(self, app):
        """未知 code：facade.get_table 归 None → 404。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/shop/roster/no_such_code/')
        assert resp.status_code == 404, resp.get_data(as_text=True)

    def test_cross_module_code_404(self, app):
        """跨模块隔离：module_key='pension' 的台账对 shop 门面一律按不存在处理。"""
        with app.app_context():
            from models import DynamicTable, db
            db.session.add(DynamicTable(code='x_pension_only', name='养老名册',
                                        module_key='pension', group_name='社会保障'))
            db.session.commit()
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/shop/roster/x_pension_only/')
        assert resp.status_code == 404, resp.get_data(as_text=True)


# ------------------------------------------------------------------ B. 建表闭环

class TestCreateFlow:
    """验收 B：CSV 上传 → 字段推断 → 建表导入落库；重复建表 409。"""

    def test_upload_csv_suggests_fields(self, app):
        """上传含身份证列的 CSV：token + 表头 + 推断建议（身份证→identity）+ 分类清单。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = _upload(client)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        data = resp.get_json()['data']
        assert data['token'], '必须下发建表 token'
        assert data['headers'] == HEADERS
        assert data['row_count'] == 3
        by_name = {s['display_name']: s for s in data['suggestions']}
        assert by_name['身份证号']['data_type'] == 'text', '证件号列按文本存'
        assert by_name['身份证号']['sensitivity'] == 'identity', '身份证列必须推断为 identity'
        assert by_name['金额']['data_type'] == 'number'
        assert by_name['缴费类型']['data_type'] == 'enum', '低基数列建议枚举'
        assert '缴费类型' in [c['code'] for c in data['dict_categories']], \
            'dict_categories 必须来自真实字典分类'

    def test_create_table_imports_and_page(self, app):
        """建表闭环：复用 m031 预置行补字段，Table/Field/Record 落库正确，页面切台账页。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _up, created = _build_table(client)
        assert created['data']['imported'] == 3 and created['data']['code'] == CODE

        with app.app_context():
            from models import DynamicField, DynamicRecord, DynamicTable
            tables = DynamicTable.query.filter_by(code=CODE).all()
            assert len(tables) == 1, 'm031 预置行必须就地复用，不得另起新行'
            table = tables[0]
            assert table.module_key == 'shop'
            assert table.created_by == 'm031', '就地复用 m031 预置行（created_by 不改写）'
            assert DynamicField.query.filter_by(table_id=table.id).count() == 5
            assert DynamicRecord.query.filter_by(table_id=table.id).count() == 3

        page = client.get('/shop/roster/%s/' % CODE)
        assert page.status_code == 200, page.get_data(as_text=True)
        html = page.get_data(as_text=True)
        assert '基础设施分布' in html, '台账页必须含表名'
        assert '尚未建立' not in html, '已建表不得再出引导页'

    def test_duplicate_create_conflict_409(self, app):
        """同 code 重复建表 → 409（防误操作，不覆盖）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client)
        up = _upload(client)          # 再传一次拿新 token（首次 token 建表后即删）
        body = up.get_json()
        created = _create(client, body['data']['token'],
                          _confirm_fields(body['data']['suggestions']))
        assert created.status_code == 409, created.get_data(as_text=True)
        assert created.get_json()['success'] is False


# ------------------------------------------------------------------ C. list 与 record CRUD

class TestListAndRecordCrud:
    """验收 C：筛选口径同 dynatable；新增打码；掩码回填保密文；不存在行报错。"""

    def test_list_filters_amount_enum_and_identity(self, app):
        """列表：金额区间 / 枚举按 value 筛选 / identity 指纹查重；页面下拉 value 传值 label 展示。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client)

        body = _list(client)
        assert body['success'] and body['total'] == 3 and len(body['data']) == 3
        assert body['truncated'] is False
        assert '__digest__' not in body['data'][0], '指纹不得回传前端'
        assert '*' in body['data'][0]['f02'], 'identity 列表必须打码'
        assert IDENT not in json.dumps(body, ensure_ascii=False), '列表不得出现完整明文'

        ranged = _list(client, query='?f_f04_min=1000')
        assert ranged['total'] == 2, '金额区间筛选必须命中 1200.5/3000 两行'

        enumed = _list(client, query='?f_f03=01')
        assert enumed['total'] == 2, '枚举按 value 筛选必须命中 2 行'

        dup = _list(client, query='?f_f02=' + IDENT)
        assert dup['total'] == 2, 'identity 明文查重必须经 HMAC 指纹命中 2 行'

        page = client.get('/shop/roster/%s/' % CODE)
        assert '<option value="01">物业费</option>' in page.get_data(as_text=True), \
            '筛选下拉必须 value 传值 / label 展示（096 口径）'

    def test_list_truncated_flag(self, app, monkeypatch):
        """R-099-05：MAX_LIST_ROWS 截断语义锁定——超限即置 truncated=True 并只取前 N 行。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client)
        from utils import roster_facade as facade
        monkeypatch.setattr(facade, 'MAX_LIST_ROWS', 2, raising=True)
        with app.app_context():
            table = facade.get_table('shop', CODE)
            out = facade.list_rows(table, page=1, limit=100)
        assert out['truncated'] is True, '3 行 > MAX_LIST_ROWS=2 必须标记截断'
        assert len(out['data']) == 2, '截断后只返回前 MAX_LIST_ROWS 行'
        assert out['total'] == 2, '截断后 total 为截断集计数'

    def test_add_record_masks_identity_and_stores_enum_value(self, app):
        """新增一行：identity 落密文、列表打码；枚举提交 label 落 value。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client)
        saved = client.post('/shop/roster/%s/api/record/0' % CODE, json={'values': {
            'f00': '新店', 'f01': '王五', 'f02': '110101199001019999',
            'f03': '物业费', 'f04': '900'}})
        assert saved.status_code == 200, saved.get_data(as_text=True)
        row = saved.get_json()['data']
        assert '*' in row['f02'] and '110101199001019999' not in row['f02']

        with app.app_context():
            from models import DynamicRecord
            rec = DynamicRecord.query.filter_by(
                table_id=DynamicRecord.query.get(row['id']).table_id).count()
        assert rec == 4, '导入 3 行 + 新增 1 行'

        listed = _list(client)
        assert listed['total'] == 4
        hit = [r for r in listed['data'] if r['id'] == row['id']][0]
        assert hit['f02'].endswith('9999') and '*' in hit['f02']
        with app.app_context():
            from models import DynamicRecord
            data = json.loads(DynamicRecord.query.get(row['id']).data)
        assert data['f03'] == '01', '枚举提交 label 必须落 value'

    def test_update_with_mask_backfill_keeps_ciphertext(self, app):
        """编辑时原样回传 identity 掩码值 → 该字段跳过写入，库中密文不变。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client)
        first = _list(client)['data'][0]
        rid, masked = first['id'], first['f02']
        cipher_before = _record_cipher(app, rid)

        resp = client.post('/shop/roster/%s/api/record/%d' % (CODE, rid),
                           json={'values': {'f00': '旺铺超市改', 'f02': masked}})
        assert resp.get_json()['success'], resp.get_data(as_text=True)

        assert _record_cipher(app, rid) == cipher_before, '掩码回填不得覆盖密文'
        refreshed = _list(client)['data'][0]
        assert refreshed['f00'] == '旺铺超市改', '非 identity 字段必须正常更新'
        assert refreshed['f02'] == masked, '未改动的 identity 展示不变'

    def test_update_identity_to_new_plaintext_changes_mask(self, app):
        """改 identity 为新明文 → 密文重写，打码展示随之变化。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client)
        second = _list(client)['data'][1]
        rid, old_masked = second['id'], second['f02']

        resp = client.post('/shop/roster/%s/api/record/%d' % (CODE, rid),
                           json={'values': {'f02': '110101199001017777'}})
        assert resp.get_json()['success'], resp.get_data(as_text=True)

        listed = _list(client)
        hit = [r for r in listed['data'] if r['id'] == rid][0]
        assert hit['f02'] != old_masked, 'identity 变更后打码展示必须变化'
        assert hit['f02'].endswith('7777') and '*' in hit['f02']
        assert '110101199001017777' not in json.dumps(listed, ensure_ascii=False), \
            '列表不得出现新明文'

    def test_update_missing_record_fails(self, app):
        """record 不存在的 rid → success=False（SoftTableError 转错误响应）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client)
        resp = client.post('/shop/roster/%s/api/record/99999' % CODE,
                           json={'values': {'f00': 'x'}})
        body = resp.get_json()
        assert body['success'] is False
        assert '不存在' in body['msg']


# ------------------------------------------------------------------ D. 权限归属

class TestPermissions:
    """验收 D：shop 模块位鉴权（BLUEPRINT_MODULES['shop']，enforce_role_permissions 推断）。"""

    def test_role_with_shop_view_opens_page(self, app):
        """有 shop.view 权限的角色 → 门面页 200。"""
        uid = _make_role_user(app, 'shop_viewer_099', {'shop.view': True})
        client = app.test_client()
        _login(client, uid, 'shop_viewer_099')
        resp = client.get('/shop/roster/%s/' % CODE)
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_role_without_shop_forbidden(self, app):
        """无任何 shop 权限的角色 → 403（enforce 默认拒绝）。"""
        uid = _make_role_user(app, 'no_shop_099', {})
        client = app.test_client()
        _login(client, uid, 'no_shop_099')
        resp = client.get('/shop/roster/%s/' % CODE)
        assert resp.status_code == 403, resp.get_data(as_text=True)

    def test_anonymous_redirects_login(self, app):
        """未登录 → 302 跳登录页。"""
        client = app.test_client()
        resp = client.get('/shop/roster/%s/' % CODE)
        assert resp.status_code == 302, resp.get_data(as_text=True)

    def test_role_with_only_dynatable_view_forbidden(self, app):
        """R-099-04：仅有 dynatable.view（无 shop 权限）的角色 → 403。

        门面端点一律落 shop 蓝图、权限走业务模块位（ADR-027），
        持有 dynatable 权限不可借道访问业务名册。
        """
        uid = _make_role_user(app, 'dynatable_only_099', {'dynatable.view': True})
        client = app.test_client()
        _login(client, uid, 'dynatable_only_099')
        resp = client.get('/shop/roster/%s/' % CODE)
        assert resp.status_code == 403, resp.get_data(as_text=True)
        # API 端点同口径拒绝（不是渲染层才拦）
        resp = client.get('/shop/roster/%s/api/list' % CODE)
        assert resp.status_code == 403, resp.get_data(as_text=True)


# ------------------------------------------------------------------ E. 导出

class TestExport:
    """验收 E：导出口径同 096（admin 明文 / 无 view_sensitive 打码 / 留痕）。"""

    def test_admin_export_plaintext_enum_label_and_log(self, app):
        """admin 导出：xlsx、identity 明文、enum 出 label，且写 operation_logs。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client)

        resp = client.get('/shop/roster/%s/api/export' % CODE)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert 'spreadsheetml' in resp.content_type

        sheet = _export_cells(resp)
        assert sheet[0] == HEADERS
        assert sheet[1][2] == IDENT, 'admin（role_id=1）导出 identity 必须明文'
        assert sheet[1][3] == '物业费', '导出 enum 列必须映射 label（096 口径）'

        with app.app_context():
            from models import OperationLog
            log = (OperationLog.query.filter_by(module='店铺信息', action='export')
                   .order_by(OperationLog.id.desc()).first())
            assert log is not None, '导出动作必须写操作日志'
            assert '导出台账' in (log.summary or ''), '日志摘要须含导出台账语义'

    def test_export_masked_without_view_sensitive(self, app):
        """无 view_sensitive 特权用户导出：identity 列打码，不含完整明文。"""
        uid = _make_role_user(app, 'shop_export_099',
                              {'shop.view': True, 'shop.export': True})
        admin = app.test_client()
        _login(admin, _admin_id(app))
        _build_table(admin)

        client = app.test_client()
        _login(client, uid, 'shop_export_099')
        resp = client.get('/shop/roster/%s/api/export' % CODE)
        assert resp.status_code == 200, resp.get_data(as_text=True)

        sheet = _export_cells(resp)
        assert sheet[1][2] == '110***********1234', '无 view_sensitive 必须打码导出'
        assert IDENT not in str(sheet), '导出文件不得含完整明文'


# ------------------------------------------------------------------ F. m031 幂等

class TestM031Migration:
    """验收 F：m031 元数据预置幂等、不插字段行、缺表拒绝。"""

    def test_idempotent_preset(self, isolated_app_factory):
        """连续跑 2 次：首跑 inserted=7；二跑 inserted=[] 且 skipped=7；行数不变、m031 不新增字段行。

        141（C-2）：启动即初始化 8 张综治固定表（77 字段），「零表零字段」前提不再成立 → 以运行前为基线断言增量。
        """
        _app = isolated_app_factory()
        m031 = _load_m031()
        db_path = os.environ['SHUCUN_DB_PATH']
        codes = sorted(code for code, *_rest in m031.ROSTER_TABLES)
        tables_before = _sqlite_count(db_path, 'dynamic_table')
        fields_before = _sqlite_count(db_path, 'dynamic_field')

        first = m031.run(db_path)
        assert sorted(first['inserted']) == codes and first['skipped'] == []
        n_after_first = _sqlite_count(db_path, 'dynamic_table')
        assert n_after_first == tables_before + len(codes)

        second = m031.run(db_path)
        assert second['inserted'] == [], '二跑不得重复插入'
        assert sorted(second['skipped']) == codes
        assert _sqlite_count(db_path, 'dynamic_table') == n_after_first, '幂等：行数不变'
        assert _sqlite_count(db_path, 'dynamic_field') == fields_before, 'm031 不得预置字段行'

    def test_missing_dynamic_table_raises(self, tmp_path):
        """库无 dynamic_table 表 → run 抛 RuntimeError（拒绝在无结构库上执行）。"""
        empty_db = str(tmp_path / 'no_meta.db')
        with pytest.raises(RuntimeError):
            _load_m031().run(empty_db)
