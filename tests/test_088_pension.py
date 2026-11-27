# -*- coding: utf-8 -*-
"""tests/test_088_pension.py — 088 养老保险（pension 门面 / 软表参保名册 / 两指标）

覆盖（docs/tasks/088-pension-insurance.md 验收标准中可自动化部分）：
A. 门面页与引导态：GET /pension/ 200（指标卡容器 + 名册入口 + 规范列指引 +
   待确认列不猜字段）；未登录 302
B. m038 幂等：删库重跑 inserted=3 分类/9 条目、二跑全跳过；与 m031 的
   dynamic_table 行零重复（pension_roster 恒 1 行）；缺字典表 → RuntimeError
C. 软表名册闭环：规范列 CSV 上传 → 建表（m031 预置行就地复用）→ 列表筛选
   （枚举按 value / identity 指纹）→ 导出 enum 出 label；非法枚举 roster 拒绝、
   dynatable 400（R-078-05 同源口径）
D. 两指标三处同值：MetricsEngine.query 直调 == /pension/api/stats 代理 ==
   统一入口 /api/stats/query；参保率=正常参保/总数、认证完成率空值按「未认证」
   计入分母展示且**不回填库**（反例：原始记录零改动）
E. 权限三处同步：BLUEPRINT_MODULES['pension'] / role_mgmt 面板键集 / 授权正例
   （pension.view 角色 200，无权限 403，dynatable-only 不可借道）
F. 深链与侧栏：102 起 /pension/ 并入社会保障页内胶囊（侧栏不再单列）
"""
import importlib.util
import io
import json
import os
import re
import sqlite3

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_M031_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm031_roster_metadata.py')
_M038_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm038_pension.py')

CODE = 'pension_roster'
HEADERS = ['姓名', '身份证号', '参保类型', '参保状态', '缴费年度', '待遇认证状态']
ROWS = [
    ['张三', '110101199001011234', '职工养老', '正常参保', '2026', '已认证'],
    ['李四', '110101199001011235', '城乡居民养老', '正常参保', '2026', ''],
    ['王五', '110101199001011236', '城乡居民养老', '暂停参保', '2026', '未认证'],
    ['赵六', '110101199001011237', '其他', '未参保', '2026', '已认证'],
]
IDENT = '110101199001011234'

# 字段清单-v1 §3.4.2 养老段规范列（枚举挂 088 三个字典分类；身份证 identity）
PENSION_FIELDS = [
    {'code': 'f00', 'display_name': '姓名', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f01', 'display_name': '身份证号', 'data_type': 'text',
     'sensitivity': 'identity', 'is_required': False},
    {'code': 'f02', 'display_name': '参保类型', 'data_type': 'enum',
     'dict_category_code': '养老保险类型', 'sensitivity': 'none', 'is_required': False},
    {'code': 'f03', 'display_name': '参保状态', 'data_type': 'enum',
     'dict_category_code': '养老保险参保状态', 'sensitivity': 'none', 'is_required': False},
    {'code': 'f04', 'display_name': '缴费年度', 'data_type': 'number',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f05', 'display_name': '待遇认证状态', 'data_type': 'enum',
     'dict_category_code': '养老待遇认证状态', 'sensitivity': 'none', 'is_required': False},
]


# ------------------------------------------------------------------ 夹具/工具

def _load_migration(filename, modname):
    """importlib 加载迁移脚本（scripts/ 非包；test_099 同款方式）。"""
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_ROOT, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_m031():
    return _load_migration(os.path.join('scripts', 'migrations', 'm031_roster_metadata.py'),
                           'm031_under_test_088')


def _load_m038():
    return _load_migration(os.path.join('scripts', 'migrations', 'm038_pension.py'),
                           'm038_under_test_088')


@pytest.fixture()
def app(isolated_app_factory):
    """隔离应用 + 密钥解锁 + m031 元数据预置（与部署顺序一致）。
    088 三个字典分类由 create_app 的 ensure_enum_dicts 守卫自动补种（DICT_SEEDS）。"""
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('pension-test', salt=generate_salt())
    stats = _load_m031().run(os.environ['SHUCUN_DB_PATH'])
    assert len(stats['inserted']) == 7, 'm031 预置 7 张台账必须成功：%s' % stats
    return application


@pytest.fixture()
def _clean_agg_cache():
    """指标缓存隔离：每用例前后失效（test_084 同款，防跨用例串值）。"""
    from utils import agg_cache
    agg_cache.invalidate()
    yield
    agg_cache.invalidate()


def _login(client, user_id, username='admin'):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _admin_id(app):
    with app.app_context():
        from models import User
        return User.query.filter_by(username='admin').first().id


def _make_role_user(app, username, permissions):
    """建角色 + 用户（permissions 为模块位 dict）。返回 user.id。"""
    with app.app_context():
        from models import Role, User, db
        role = Role(name='role_%s' % username, description='088 用例',
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


def _upload(client, headers=HEADERS, rows=ROWS, code=CODE):
    return client.post('/pension/roster/%s/api/upload' % code,
                       data={'file': (_csv_bytes(headers, rows), 'pension_roster.csv')},
                       content_type='multipart/form-data')


def _create(client, token, code=CODE, name='养老保险参保名册'):
    return client.post('/pension/roster/%s/api/create' % code,
                       json={'token': token, 'name': name, 'fields': PENSION_FIELDS})


def _build_roster(client):
    """上传 + 确认建表闭环（断言两步成功），返回建表响应 json。"""
    up = _upload(client)
    assert up.status_code == 200, up.get_data(as_text=True)
    body = up.get_json()
    assert body['success'], body
    created = _create(client, body['data']['token'])
    assert created.status_code == 200, created.get_data(as_text=True)
    assert created.get_json()['success'], created.get_json()
    return created.get_json()


def _list(client, query=''):
    return client.get('/pension/roster/%s/api/list%s' % (CODE, query)).get_json()


def _export_cells(resp):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.data), read_only=True, data_only=True)
    rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    wb.close()
    return rows


def _sqlite_scalar(db_path, sql):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------------ A. 门面页与引导态

class TestFacadePage:
    """验收 A/5：门面页 200 + 指标卡 + 名册入口 + 规范列指引；未登录 302。"""

    def test_index_renders_guidance_and_entry(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/pension/')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)
        assert 'id="rpSearchForm"' in html and 'id="rpTableBody"' in html, \
            '养老保险首页必须直接展示农保大表台账（与医保同构）'
        assert 'id="rpStatsBar"' in html and 'id="rpImportModal"' in html, \
            '指标卡容器与批量导入弹窗必须在位'
        assert '软表建册' not in html and '进入农保大表台账' not in html, \
            '不应再出现软表建册或二级跳转按钮'

    def test_index_requires_login(self, app):
        """深链 /pension/ 未登录 → 302 跳登录（验收 5）。"""
        client = app.test_client()
        resp = client.get('/pension/')
        assert resp.status_code == 302, resp.get_data(as_text=True)
        assert '/auth/login' in resp.headers.get('Location', '')

    def test_pension_chip_under_social_group(self, app):
        """102：养老保险并入社会保障页内胶囊，侧栏不再单列。"""
        from utils.nav_spec import NAV_GROUPS
        client = app.test_client()
        _login(client, _admin_id(app))
        html = client.get('/pension/').get_data(as_text=True)
        m = re.search(r'<aside class="app-sidebar sc-sidebar" id="sidebar">.*?</aside>',
                      html, re.S)
        assert m, '侧栏未渲染'
        sidebar = m.group(0)
        welfare = [g for g in NAV_GROUPS if g['key'] == 'welfare'][0]
        seg = re.split(r'<div class="sc-sidebar__group">', sidebar)
        welfare_html = [s for s in seg if '民生保障' in s]
        assert welfare_html and 'href="/medical-insurance/"' in welfare_html[0], \
            '侧栏「民生保障」组必须含社会保障页（医保宿主）'
        assert not any(i['endpoint'] == 'pension.index' for i in welfare['items']), \
            '102：养老保险不应再作为侧栏入口'
        # 页内胶囊承载医保 / 养老切换
        assert 'href="/medical-insurance/#tab=medical-insurance"' in html
        assert 'href="/pension/#tab=pension"' in html


# ------------------------------------------------------------------ B. m038 幂等

class TestM038Migration:
    """验收 6：m038 重放幂等；与 m031 的 dynamic_table 行零重复。"""

    def test_idempotent_after_delete_reseed(self, app):
        """删除 088 三分类后重跑：inserted=3 分类/9 条目；二跑全跳过、计数不变。"""
        db_path = os.environ['SHUCUN_DB_PATH']
        with app.app_context():
            from models import DictCategory, DictItem, db
            names = ['养老保险类型', '养老保险参保状态', '养老待遇认证状态']
            for name in names:
                cat = DictCategory.query.filter_by(name=name).first()
                if cat:
                    DictItem.query.filter_by(category_id=cat.id).delete()
                    db.session.delete(cat)
            db.session.commit()

        m038 = _load_m038()
        first = m038.run(db_path)
        assert sorted(first['categories']) == sorted(
            ['养老保险类型', '养老保险参保状态', '养老待遇认证状态']), first
        assert len(first['items']) == 9, '3 分类 × 3 取值 = 9 条目：%s' % first
        second = m038.run(db_path)
        assert second['categories'] == [] and second['items'] == [], '二跑不得重复插入'
        assert len(second['skipped_categories']) == 3 and len(second['skipped_items']) == 9

        with app.app_context():
            from models import DictCategory, DictItem
            for name, expected in (('养老保险类型', 3), ('养老保险参保状态', 3),
                                   ('养老待遇认证状态', 3)):
                cat = DictCategory.query.filter_by(name=name).first()
                assert cat is not None, '分类 %s 必须存在' % name
                assert DictItem.query.filter_by(category_id=cat.id).count() == expected
            # label=value 同值口径（DICT_SEEDS 唯一真源）
            cat = DictCategory.query.filter_by(name='养老保险类型').first()
            for item in DictItem.query.filter_by(category_id=cat.id).all():
                assert item.value == item.label, '088 字典取值 label=value'

    def test_no_dynamic_table_duplication_with_m031(self, app):
        """m038 与 m031 的 dynamic_table 行零重复：pension_roster 恒 1 行。"""
        db_path = os.environ['SHUCUN_DB_PATH']
        _load_m038().run(db_path)  # m038 不插 dynamic_table 行
        n = _sqlite_scalar(db_path,
                           "SELECT COUNT(*) FROM dynamic_table WHERE code = '%s'" % CODE)
        assert n == 1, 'pension_roster 元数据行必须恰好 1 行（m031 唯一预置点）'
        fields = _sqlite_scalar(db_path,
                                "SELECT COUNT(*) FROM dynamic_field WHERE table_id IN "
                                "(SELECT id FROM dynamic_table WHERE code = '%s')" % CODE)
        assert fields == 0, 'm038 不得预置字段行（ADR-025 决策 3）'

    def test_missing_dict_tables_raise(self, tmp_path):
        """库无字典表 → run 抛 RuntimeError（拒绝在无结构库上执行）。"""
        empty_db = str(tmp_path / 'no_dict.db')
        with pytest.raises(RuntimeError):
            _load_m038().run(empty_db)


# ------------------------------------------------------------------ C. 软表名册闭环

class TestRosterFlow:
    """验收 3：规范列建册 + 枚举 value 口径 + 非法枚举拒绝 + 导出 label。"""

    def test_create_roster_reuses_m031_preset_row(self, app):
        """建表复用 m031 预置行（不另起新行），字段按规范列落库。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        created = _build_roster(client)
        assert created['data']['imported'] == 4 and created['data']['code'] == CODE

        with app.app_context():
            from models import DynamicField, DynamicRecord, DynamicTable
            tables = DynamicTable.query.filter_by(code=CODE).all()
            assert len(tables) == 1, 'm031 预置行必须就地复用'
            assert tables[0].created_by == 'm031'
            assert tables[0].module_key == 'pension'
            assert DynamicField.query.filter_by(table_id=tables[0].id).count() == 6
            assert DynamicRecord.query.filter_by(table_id=tables[0].id).count() == 4

    def test_list_filter_enum_value_and_identity_digest(self, app):
        """列表：枚举按 value 筛选 / identity 指纹查重 / 打码展示（R-078-05 同源）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        body = _list(client)
        assert body['success'] and body['total'] == 4
        assert '__digest__' not in body['data'][0], '指纹不得回传前端'
        assert '*' in body['data'][0]['f01'], 'identity 列表必须打码'

        enumed = _list(client, query='?f_f03=正常参保')
        assert enumed['total'] == 2, '枚举按 value 筛选命中 2 行（label=value 种子）'

        dup = _list(client, query='?f_f01=' + IDENT)
        assert dup['total'] == 1, 'identity 明文查重经 HMAC 指纹命中本行（名册 4 人身份证互异）'

        page = client.get('/pension/roster/%s/' % CODE)
        html = page.get_data(as_text=True)
        assert '尚未建立' not in html, '已建表不得再出引导页'
        assert '<option value="正常参保">正常参保</option>' in html, \
            '筛选下拉 value 传值 / label 展示（096 口径）'

    def test_invalid_enum_rejected_roster_and_dynatable_400(self, app):
        """非法枚举：roster 新增拒绝（success=False）；dynatable 更新 HTTP 400。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        bad = client.post('/pension/roster/%s/api/record/0' % CODE,
                          json={'values': {'f00': '测试', 'f03': '破产参保'}})
        body = bad.get_json()
        assert body['success'] is False, '非法枚举值必须被拒绝'
        assert '不在字典' in body['msg'], body

        rid = _list(client)['data'][0]['id']
        resp = client.post('/dynatable/api/t/%s/record/%d' % (CODE, rid),
                           json={'code': 'f03', 'value': '破产参保'})
        assert resp.status_code == 400, 'dynatable 口径非法枚举必须 400：%s' % resp.status_code

    def test_export_enum_label_and_masked_identity(self, app):
        """导出：enum 出 label（096 口径）；无 view_sensitive 的 identity 列打码。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        admin_export = client.get('/pension/roster/%s/api/export' % CODE)
        assert admin_export.status_code == 200, admin_export.get_data(as_text=True)
        sheet = _export_cells(admin_export)
        assert sheet[0] == HEADERS
        assert sheet[1][1] == IDENT, 'admin（role_id=1）导出 identity 明文'
        assert sheet[1][2] == '职工养老', '导出 enum 列必须映射 label'

        uid = _make_role_user(app, 'p088_export',
                              {'pension.view': True, 'pension.export': True})
        other = app.test_client()
        _login(other, uid, 'p088_export')
        masked = other.get('/pension/roster/%s/api/export' % CODE)
        assert masked.status_code == 200
        sheet = _export_cells(masked)
        assert sheet[1][1] == '110***********1234', '无 view_sensitive 必须打码导出'
        assert IDENT not in str(sheet), '导出文件不得含完整明文'


# ------------------------------------------------------------------ D. 两指标三处同值

class TestLedgerFirstPage:
    """2026-11-16：取消二级布局——已建册时 /pension/ 直接渲染台账（与医保参保管理同构）。

    旧形态：/pension/ 门面页只有「进入名册台账」按钮，查看数据需二次点击 /pension/roster/<code>/。
    新形态：页内胶囊 + 页头动作 + 指标卡 + 筛选工具条 + 表格 + 分页，一次点击即见数据。
    """

    def test_root_renders_ledger_when_built(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        html = client.get('/pension/').get_data(as_text=True)
        assert 'id="rpTableBody"' in html, '/pension/ 必须直接渲染农保大表台账'
        assert 'id="rpSearchForm"' in html and 'id="rpPagination"' in html, '筛选工具条与分页须同页'
        assert 'id="rpFormModal"' in html, '新增/编辑记录弹窗须在位'
        assert 'id="rpStatsBar"' in html, '台账页保留指标卡（与医保参保管理页同构）'
        assert 'pension-page' in html and 'css/pages/pension.css' in html, '未接入养老保险页面样式'
        assert '返回业务页' not in html and '软表建册' not in html, '不应再有软表建册或返回上级入口'
        # 页内胶囊仍承载医保/养老切换
        assert 'href="/medical-insurance/#tab=medical-insurance"' in html
        assert 'href="/pension/#tab=pension"' in html

    def test_ledger_bookmark_path_still_available(self, app):
        """旧书签 /pension/roster/<code>/ 保留可用（带返回业务页），仅 /pension/ 升为台账首屏。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)
        resp = client.get('/pension/roster/%s/' % CODE)
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'id="recordTable"' in html
        assert '返回业务页' in html, '二级路径保留返回入口'


# ------------------------------------------------------------------ D. 两指标三处同值

class TestMetricsReconciliation:
    """验收 1/2：参保率/认证完成率 页面代理 == 统一入口 == 引擎直调；空值不回填库。"""

    def test_three_callers_same_value(self, app, _clean_agg_cache):
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        from utils.metrics import MetricsEngine
        with app.app_context():
            coverage = MetricsEngine.query('pension_coverage')
            cert = MetricsEngine.query('pension_cert_rate')
        assert coverage['data'] == {'total': 4, 'insured': 2, 'rate': '0.50'}, \
            '参保率 = 正常参保(2)/名册(4)'
        assert cert['data'] == {'total': 4, 'certified': 2, 'unfilled': 1,
                                'rate': '0.50'}, \
            '认证完成率 = 已认证(2)/名册(4)，空值按「未认证」计入分母'

        # 页面代理（MetricsEngine 唯一取数）
        proxy = client.get('/pension/api/stats').get_json()
        assert proxy['success'], proxy
        assert proxy['data']['coverage']['data'] == coverage['data'], '代理与引擎同值'
        assert proxy['data']['cert_rate']['data'] == cert['data'], '代理与引擎同值'

        # 统一统计入口（看板/报表消费方路径）
        unified = client.get('/api/stats/query?metric_key=pension_cert_rate').get_json()
        assert unified['success'], unified
        assert unified['data']['data'] == cert['data'], '统一入口与引擎同值'
        unified_c = client.get('/api/stats/query?metric_key=pension_coverage').get_json()
        assert unified_c['data']['data'] == coverage['data'], '统一入口与引擎同值'

    def test_cert_empty_value_not_backfilled(self, app, _clean_agg_cache):
        """反例：认证状态为空的行参与分母展示，但库中原始记录零改动（不回填）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        with app.app_context():
            from models import DynamicField, DynamicRecord, DynamicTable
            table = DynamicTable.query.filter_by(code=CODE).first()
            f05 = DynamicField.query.filter_by(table_id=table.id, code='f05').first()
            assert f05 is not None and f05.display_name == '待遇认证状态'
            raws = [json.loads(r.data or '{}') for r in
                    DynamicRecord.query.filter_by(table_id=table.id).all()]
        empty_before = [raw.get('f05') for raw in raws if not raw.get('f05')]
        assert len(empty_before) == 1, '导入数据含 1 行认证状态为空'

        client.get('/pension/api/stats')  # 触发指标查询
        with app.app_context():
            from models import DynamicRecord, DynamicTable
            table = DynamicTable.query.filter_by(code=CODE).first()
            raws = [json.loads(r.data or '{}') for r in
                    DynamicRecord.query.filter_by(table_id=table.id).all()]
        assert [raw.get('f05') for raw in raws if not raw.get('f05')] == empty_before, \
            '空值兜底只做展示，不得静默回填库'

    def test_registry_schema_and_softtable_absent_zero(self, app, _clean_agg_cache):
        """注册表九项声明字段齐全；未建册（无字段行）时指标为 0 值不报错。"""
        from utils.metrics import METRIC_REGISTRY, SCHEMA_KEYS
        for key in ('pension_coverage', 'pension_cert_rate'):
            metric = METRIC_REGISTRY[key]
            for field in SCHEMA_KEYS:
                assert field in metric, '%s 缺声明字段 %s' % (key, field)
            assert metric['permission'] == 'pension'
        from utils.metrics import MetricsEngine
        with app.app_context():
            data = MetricsEngine.query('pension_coverage')['data']
        assert data == {'total': 0, 'insured': 0, 'rate': '0.00'}, \
            '未建册/空名册指标应返回 0 值（fail-soft，不抛错）'


# ------------------------------------------------------------------ E. 权限三处同步

class TestPermissions:
    """验收 4：BLUEPRINT_MODULES / 权限面板键集 / 授权正例（红→绿）。"""

    def test_blueprint_module_registered(self):
        from utils.permissions import BLUEPRINT_MODULES
        assert BLUEPRINT_MODULES.get('pension') == 'pension'

    def test_permission_panel_covers_pension(self):
        """096 门禁同款断言：面板 permissionModules 键集 ⊇ BLUEPRINT_MODULES 值集。"""
        from utils.permissions import BLUEPRINT_MODULES
        path = os.path.join(_ROOT, 'static', 'js', 'pages', 'role_mgmt_list.js')
        src = open(path, encoding='utf-8').read()
        match = re.search(r'var permissionModules = \{([\s\S]*?)\};', src)
        assert match, 'role_mgmt_list.js 必须声明 permissionModules'
        keys = set(re.findall(r"'([^']+)'\s*:", match.group(1)))
        missing = set(BLUEPRINT_MODULES.values()) - keys
        assert not missing, '权限面板缺少模块键：%s' % sorted(missing)
        assert "'pension': '养老保险'" in src, '面板中文名缺「养老保险」'

    def test_role_with_pension_view_granted(self, app):
        """红→绿：授予 pension.view 的非超管可实际访问门面页/名册/指标代理。"""
        uid = _make_role_user(app, 'p088_clerk', {'pension.view': True})
        client = app.test_client()
        _login(client, uid, 'p088_clerk')
        assert client.get('/pension/').status_code == 200
        assert client.get('/pension/roster/%s/' % CODE).status_code == 200
        stats = client.get('/pension/api/stats')
        assert stats.status_code == 200 and stats.get_json()['success']

    def test_role_without_pension_forbidden(self, app):
        """无 pension 位 → 403（页面/名册/API 三层默认拒绝）。"""
        uid = _make_role_user(app, 'p088_none', {})
        client = app.test_client()
        _login(client, uid, 'p088_none')
        assert client.get('/pension/').status_code == 403
        assert client.get('/pension/roster/%s/' % CODE).status_code == 403
        assert client.get('/pension/roster/%s/api/list' % CODE).status_code == 403

    def test_dynatable_only_cannot_borrow(self, app):
        """dynatable 权限不可借道业务名册（ADR-027，099 R-04 同款）。"""
        uid = _make_role_user(app, 'p088_dyn_only', {'dynatable.view': True})
        client = app.test_client()
        _login(client, uid, 'p088_dyn_only')
        assert client.get('/pension/roster/%s/' % CODE).status_code == 403

    def test_stats_query_permission_gate(self, app, _clean_agg_cache):
        """统一入口按指标 permission 二次校验：无 pension.view → 403。"""
        uid = _make_role_user(app, 'p088_unified', {})
        client = app.test_client()
        _login(client, uid, 'p088_unified')
        resp = client.get('/api/stats/query?metric_key=pension_coverage')
        assert resp.status_code == 403, resp.get_data(as_text=True)


# ------------------------------------------------------------------ F. 深链与 404

class TestDeepLinks:
    """验收 5（SSR 部分）：未知/跨模块 code 404；上传页未登录 302。"""

    def test_unknown_code_404(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        assert client.get('/pension/roster/no_such_code/').status_code == 404

    def test_cross_module_code_404(self, app):
        """shop 模块的台账对 pension 门面按不存在处理（门面 module_key 归属校验）。"""
        with app.app_context():
            from models import DynamicTable, db
            db.session.add(DynamicTable(code='x_shop_only', name='店铺台账',
                                        module_key='shop', group_name='人口与台账'))
            db.session.commit()
        client = app.test_client()
        _login(client, _admin_id(app))
        assert client.get('/pension/roster/x_shop_only/').status_code == 404

    def test_bootstrap_page_shows_upload_entry(self, app):
        """未建册：门面 roster 页 200 引导页（含上传入口与 pension 门面端点）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/pension/roster/%s/' % CODE)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)
        assert '尚未建立' in html
        assert '/pension/roster/%s/api/upload' % CODE in html, '引导页下发 pension 上传端点'
        assert '/shop/roster/%s/' % CODE not in html, 'pension 门面不得夹带 shop 端点'
