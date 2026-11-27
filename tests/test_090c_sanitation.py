# -*- coding: utf-8 -*-
"""tests/test_090c_sanitation.py — 090C 环卫工作（sanitation 门面 / 三软表 / 两指标 / 092 照片附件）

覆盖（docs/tasks/090C-sanitation.md 验收标准中可自动化部分）：
A. 门面页与引导态：GET /sanitation/ 200（两指标卡容器 + 三软表入口 + 规范列指引 +
   附件区）；未登录 302
B. 字典种子：ensure_enum_dicts 补「整治记录类型」四类（一页纸 v1.2 D-03/Q-01）；反例——
   队伍类型/点位类型（D-01/D-02）不建组（R-01/R-02 删除裁定，删字段连带删组）
C. m031 锁定：三张表行各恒 1、module_key='sanitation'、group='民生服务'；
   dynamic_field 恒 0（ADR-025 决策 3，字段由首次上传生成）
D. 软表全链路：队伍册 CSV 上传 → 建表（m031 预置行就地复用）→ 列表 → 台账页 →
   导出（identity 电话打码/明文）；必填列空行先验后建（不勾跳过 → 不建表）；
   重复建表 409；整治册 enum 三处同源（确认下拉分类 / 筛选 value / 导出字面量，
   R-078-05 口径）
E. 两指标三处同值：M-01 Σ在岗人数（空值不参与求和，一页纸 V-03）/ M-02 点位行数
   （方案A，R-04「数量/容量」列不参与统计）；MetricsEngine.query ==
   /sanitation/api/stats == /api/stats/query；注册表九项声明字段；未建表 0 值 fail-soft
F. 权限三处同步：BLUEPRINT_MODULES['sanitation'] / role_mgmt 面板键集 / 授权正例
   （sanitation.view 200，无权限 403，dynatable-only 不可借道，统一入口 403）
G. 侧栏：/sanitation/ 入「社区治理」组（真源 nav_spec 派生断言）
H. 深链：未知 code 404；跨模块 code 双向 404
I. 092 照片附件：上传 → 列表 → 下载 → 删除全链路；module_key='sanitation'
"""
import importlib.util
import io
import json
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEAM_CODE = 'sanitation_team'
SITE_CODE = 'sanitation_site'
CLEANUP_CODE = 'sanitation_cleanup'

# 一页纸 v1.2 §1 队伍列（类型列已删；联系电话 identity；在岗人数必填）
TEAM_HEADERS = ['队伍名称', '负责人', '联系电话', '在岗人数', '负责片区', '备注']
TEAM_ROWS = [
    ['第一保洁队', '张三', '13800000001', '12', '村东片', ''],
    ['第二保洁队', '李四', '13800000002', '3', '村西片', '临时队伍'],
    ['第三保洁队', '王五', '13800000003', '', '村北片', '在岗人数缺失'],
]
TEAM_FIELDS = [
    {'code': 'f00', 'display_name': '队伍名称', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f01', 'display_name': '负责人', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f02', 'display_name': '联系电话', 'data_type': 'text',
     'sensitivity': 'identity', 'is_required': False},
    {'code': 'f03', 'display_name': '在岗人数', 'data_type': 'number',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f04', 'display_name': '负责片区', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f05', 'display_name': '备注', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
]

# 一页纸 v1.2 §2 点位列（类型列已删；数量/容量保留展示、不参与 M-02）
SITE_HEADERS = ['点位名称', '所在位置', '数量/容量', '使用状态', '责任人', '备注']
SITE_ROWS = [
    ['垃圾收集点-村口', '村口主干道旁', '2', '在用', '赵六', ''],
    ['公厕-河东', '河东集贸市场', '1', '停用', '钱七', '待修缮'],
]
SITE_FIELDS = [
    {'code': 'f00', 'display_name': '点位名称', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f01', 'display_name': '所在位置', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f02', 'display_name': '数量/容量', 'data_type': 'number',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f03', 'display_name': '使用状态', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f04', 'display_name': '责任人', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f05', 'display_name': '备注', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
]

# 一页纸 v1.2 §3 整治列（enum 挂 D-03 整治记录类型；照片列仅路径/附件引用）
CLEANUP_HEADERS = ['整治日期', '整治地点/片区', '整治类型', '参与人数', '整治内容', '照片', '记录人']
CLEANUP_ROWS = [
    ['2026-11-01', '村口主干道', '日常保洁', '8', '清扫落叶200米', '', '孙八'],
    ['2026-11-02', '河东集贸市场', '垃圾清运', '5', '清运垃圾3车', 'att://12', '孙八'],
    ['2026-11-03', '村部周边', '其他', '3', '清理小广告', '', '周九'],
]
CLEANUP_FIELDS = [
    {'code': 'f00', 'display_name': '整治日期', 'data_type': 'date',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f01', 'display_name': '整治地点/片区', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f02', 'display_name': '整治类型', 'data_type': 'enum',
     'dict_category_code': '整治记录类型', 'sensitivity': 'none', 'is_required': True},
    {'code': 'f03', 'display_name': '参与人数', 'data_type': 'number',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f04', 'display_name': '整治内容', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f05', 'display_name': '照片', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f06', 'display_name': '记录人', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
]


# ------------------------------------------------------------------ 夹具/工具

def _load_m031():
    """importlib 加载 m031（scripts/ 非包；test_088/099 同款方式）。"""
    path = os.path.join(_ROOT, 'scripts', 'migrations', 'm031_roster_metadata.py')
    spec = importlib.util.spec_from_file_location('m031_under_test_090c', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def app(isolated_app_factory):
    """隔离应用 + 密钥解锁 + m031 元数据预置（与部署顺序一致）。
    「整治记录类型」由 create_app 的 ensure_enum_dicts 守卫自动补种（DICT_SEEDS）。"""
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('sanitation-test', salt=generate_salt())
    stats = _load_m031().run(os.environ['SHUCUN_DB_PATH'])
    assert len(stats['inserted']) == 7, 'm031 预置 7 张台账必须成功：%s' % stats
    return application


@pytest.fixture()
def _clean_agg_cache():
    """指标缓存隔离：每用例前后失效（test_084/088 同款，防跨用例串值）。"""
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
        role = Role(name='role_%s' % username, description='090C 用例',
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


def _upload(client, code, headers, rows):
    return client.post('/sanitation/roster/%s/api/upload' % code,
                       data={'file': (_csv_bytes(headers, rows), '%s.csv' % code)},
                       content_type='multipart/form-data')


def _create(client, code, token, fields, name):
    return client.post('/sanitation/roster/%s/api/create' % code,
                       json={'token': token, 'name': name, 'fields': fields})


def _build_table(client, code, headers, rows, fields, name):
    """上传 + 确认建表闭环（断言两步成功），返回建表响应 json。"""
    up = _upload(client, code, headers, rows)
    assert up.status_code == 200, up.get_data(as_text=True)
    body = up.get_json()
    assert body['success'], body
    created = _create(client, code, body['data']['token'], fields, name)
    assert created.status_code == 200, created.get_data(as_text=True)
    assert created.get_json()['success'], created.get_json()
    return created.get_json()


def _export_cells(resp):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.data), read_only=True, data_only=True)
    rows = [list(r) for r in wb.active.iter_rows(values_only=True)]
    wb.close()
    return rows


# ------------------------------------------------------------------ A. 门面页与引导态

class TestFacadePage:
    """验收 1（SSR 部分）：门面页 200 + 两指标卡 + 三软表入口 + 规范列指引；未登录 302。"""

    def test_index_renders_entries_metrics_and_guidance(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/sanitation/')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)
        for code in (TEAM_CODE, SITE_CODE, CLEANUP_CODE):
            assert '/sanitation/roster/%s/' % code in html, '三软表入口缺 %s' % code
        assert 'sanitationHeadcount' in html and 'sanitationSiteCount' in html, \
            '两指标卡容器（M-01/M-02）必须在位'
        assert 'attSection' not in html and 'module_attachments.js' not in html, '整治照片附件区必须下线'
        assert '台账尚未建立' in html, '未建表须出引导态'
        for col in ('队伍名称', '在岗人数', '点位名称', '所在位置', '整治日期', '整治类型'):
            assert col in html, '规范列指引缺「%s」（一页纸 v1.2）' % col
        # 已删除的类型列不得再出现在引导里（R-01/R-02 裁定）
        assert '队伍类型' not in html and '点位类型' not in html
        # 设计规范要求整治照片附件模块彻底下线
        assert 'module_attachments.js' not in html

    def test_index_requires_login(self, app):
        client = app.test_client()
        resp = client.get('/sanitation/')
        assert resp.status_code == 302, resp.get_data(as_text=True)
        assert '/auth/login' in resp.headers.get('Location', '')

    def test_roster_bootstrap_before_build(self, app):
        """未建表 → 200 引导页（验收 4 首次使用引导）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/sanitation/roster/%s/' % TEAM_CODE)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)
        assert '环卫保洁队伍' in html
        assert '/sanitation/roster/%s/api/upload' % TEAM_CODE in html


# ------------------------------------------------------------------ B. 字典种子

class TestDictSeeds:
    """验收 3（字典面）：D-03 四类补种；D-01/D-02 不建组。"""

    def test_cleanup_type_dict_seeded(self, app):
        with app.app_context():
            from models import DictCategory, DictItem
            cat = DictCategory.query.filter_by(name='整治记录类型').first()
            assert cat is not None, '「整治记录类型」应由 ensure_enum_dicts 补种'
            labels = [i.label for i in DictItem.query.filter_by(category_id=cat.id)
                      .order_by(DictItem.sort_order.asc(), DictItem.id.asc()).all()]
            assert labels == ['日常保洁', '垃圾清运', '专项整治', '其他'], \
                '一页纸 v1.2 Q-01 四类字面量（label=value 同值）'

    def test_deleted_type_dicts_absent(self, app):
        """反例：队伍/点位类型字段已删（R-01/R-02），字典整组不得存在。"""
        with app.app_context():
            from models import DictCategory
            assert DictCategory.query.filter_by(name='环卫队伍类型').first() is None
            assert DictCategory.query.filter_by(name='设施点位类型').first() is None


# ------------------------------------------------------------------ C. m031 锁定

class TestM031Preset:
    """验收（零 DDL 面）：三张表行由 m031 预置，本任务零迁移文件。"""

    def test_three_tables_preset_exactly_once(self, app):
        with app.app_context():
            from models import DynamicField, DynamicTable
            for code in (TEAM_CODE, SITE_CODE, CLEANUP_CODE):
                rows = DynamicTable.query.filter_by(code=code).all()
                assert len(rows) == 1, '%s 必须恰 1 行（m031 预置，禁重复插行）' % code
                assert rows[0].module_key == 'sanitation'
                assert rows[0].group_name == '民生服务', '一页纸 §7：侧栏组名（m031 已固定）'
                assert DynamicField.query.filter_by(table_id=rows[0].id).count() == 0, \
                    'ADR-025 决策 3：不预置字段，首次上传生成'


# ------------------------------------------------------------------ D. 软表全链路

class TestRosterChain:
    """验收 4：建表→上传→列表→导出全链路；验收 3：enum 三处同源；验收 1：无 403 反例。"""

    def test_team_full_chain_and_duplicate_409(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))

        # 必填列空行：先验后建（不勾跳过 → 不建表不留半截）
        up = _upload(client, TEAM_CODE, TEAM_HEADERS, TEAM_ROWS)
        assert up.status_code == 200 and up.get_json()['success'], up.get_json()
        token = up.get_json()['data']['token']
        refused = _create(client, TEAM_CODE, token, TEAM_FIELDS, '环卫保洁队伍')
        assert refused.status_code == 200 and not refused.get_json()['success']
        assert '不合规' in refused.get_json()['msg']
        with app.app_context():
            from models import DynamicField, DynamicTable
            table = DynamicTable.query.filter_by(code=TEAM_CODE).first()
            assert DynamicField.query.filter_by(table_id=table.id).count() == 0, \
                '建表被拒后必须零字段残留'

        # 勾选跳过错误行 → 建表导入 2 行（m031 预置行就地复用；token 未失效可复用）
        created = client.post('/sanitation/roster/%s/api/create' % TEAM_CODE,
                              json={'token': token, 'name': '环卫保洁队伍',
                                    'fields': TEAM_FIELDS, 'skip_error_rows': True})
        assert created.status_code == 200, created.get_data(as_text=True)
        body = created.get_json()
        assert body['success'] and body['data']['imported'] == 2, body

        with app.app_context():
            from models import DynamicField, DynamicRecord, DynamicTable
            tables = DynamicTable.query.filter_by(code=TEAM_CODE).all()
            assert len(tables) == 1, 'm031 预置行必须就地复用，不得另起新行'
            assert DynamicField.query.filter_by(table_id=tables[0].id).count() == 6
            assert DynamicRecord.query.filter_by(table_id=tables[0].id).count() == 2

        # 重复建表 → 409（需有效 token：重新上传取新 token，表已建 → 拒绝）
        up2 = _upload(client, TEAM_CODE, TEAM_HEADERS, TEAM_ROWS[:2])
        assert up2.status_code == 200 and up2.get_json()['success'], up2.get_json()
        dup = client.post('/sanitation/roster/%s/api/create' % TEAM_CODE,
                          json={'token': up2.get_json()['data']['token'],
                                'name': '环卫保洁队伍', 'fields': TEAM_FIELDS})
        assert dup.status_code == 409, dup.get_data(as_text=True)

        # 台账页 200 且不再出引导态
        page = client.get('/sanitation/roster/%s/' % TEAM_CODE)
        html = page.get_data(as_text=True)
        assert page.status_code == 200
        assert '尚未建立' not in html and '队伍名称' in html

        # admin 导出：identity 电话明文
        export = client.get('/sanitation/roster/%s/api/export' % TEAM_CODE)
        assert export.status_code == 200, export.get_data(as_text=True)
        sheet = _export_cells(export)
        assert sheet[0] == TEAM_HEADERS
        assert sheet[1][2] == '13800000001', 'admin（role_id=1）导出 identity 明文'

        # 无 view_sensitive 导出：电话打码
        uid = _make_role_user(app, 'z090c_export',
                              {'sanitation.view': True, 'sanitation.export': True})
        other = app.test_client()
        _login(other, uid, 'z090c_export')
        masked = other.get('/sanitation/roster/%s/api/export' % TEAM_CODE)
        assert masked.status_code == 200
        sheet = _export_cells(masked)
        assert '13800000001' not in str(sheet), '无 view_sensitive 必须打码导出'

    def test_cleanup_enum_three_places_same_source(self, app):
        """验收 3（R-078-05 回归守护）：enum 列 字段确认下拉 / 筛选 / 导出 三处一致。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        created = _build_table(client, CLEANUP_CODE, CLEANUP_HEADERS, CLEANUP_ROWS,
                               CLEANUP_FIELDS, '环卫整治记录')
        assert created['data']['imported'] == 3

        # ① 字段确认下拉源：上传响应 dict_categories 含「整治记录类型」
        up = _upload(client, SITE_CODE, SITE_HEADERS, SITE_ROWS)
        assert up.get_json()['success']
        cat_names = [c['name'] for c in up.get_json()['data']['dict_categories']]
        assert '整治记录类型' in cat_names

        # ② 列表筛选按 value 精确命中
        listed = client.get('/sanitation/roster/%s/api/list?f_f02=%s'
                            % (CLEANUP_CODE, '日常保洁')).get_json()
        assert listed['success'] and listed['total'] == 1, listed
        assert listed['data'][0]['f01'] == '村口主干道'

        # 台账页 enum 下拉选项与字典同源（空表页上的选项字面量即来源字典）
        page = client.get('/sanitation/roster/%s/' % CLEANUP_CODE)
        assert page.status_code == 200
        for label in ('日常保洁', '垃圾清运', '专项整治', '其他'):
            assert label in page.get_data(as_text=True), 'enum 下拉缺选项「%s」' % label

        # ③ 导出出同源字面量
        export = client.get('/sanitation/roster/%s/api/export' % CLEANUP_CODE)
        sheet = _export_cells(export)
        assert sheet[0] == CLEANUP_HEADERS
        labels = [row[2] for row in sheet[1:]]
        assert labels == ['日常保洁', '垃圾清运', '其他'], '导出 enum 列必须与字典字面量一致'

    def test_site_full_chain_minimal(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        created = _build_table(client, SITE_CODE, SITE_HEADERS, SITE_ROWS,
                               SITE_FIELDS, '环卫设施点位')
        assert created['data']['imported'] == 2
        listed = client.get('/sanitation/roster/%s/api/list' % SITE_CODE).get_json()
        assert listed['success'] and listed['total'] == 2


# ------------------------------------------------------------------ E. 两指标三处同值

class TestMetricsReconciliation:
    """验收 2：M-01/M-02 页面代理 == 统一入口 == 引擎直调；空值不参与求和。"""

    def test_three_callers_same_value(self, app, _clean_agg_cache):
        client = app.test_client()
        _login(client, _admin_id(app))
        # 前两行有效（在岗人数 12+3）；第三行空值行在下方手工补插，专测 V-03 空值口径
        _build_table(client, TEAM_CODE, TEAM_HEADERS, TEAM_ROWS[:2], TEAM_FIELDS, '环卫保洁队伍')
        _build_table(client, SITE_CODE, SITE_HEADERS, SITE_ROWS, SITE_FIELDS, '环卫设施点位')

        # 队伍册手工补一行「在岗人数」为空的记录（V-03：空值不参与求和、计入 empty）
        with app.app_context():
            from models import DynamicRecord, DynamicTable
            table = DynamicTable.query.filter_by(code=TEAM_CODE).first()
            db_record = DynamicRecord(table_id=table.id,
                                      data=json.dumps({'f00': '第四保洁队'}, ensure_ascii=False),
                                      created_by='t090c')
            from models import db
            db.session.add(db_record)
            db.session.commit()

        from utils.metrics import MetricsEngine
        with app.app_context():
            head = MetricsEngine.query('sanitation_team_headcount')
            site = MetricsEngine.query('sanitation_site_count')
        assert head['data'] == {'total': 3, 'sum': 15, 'empty': 1}, \
            'M-01 = Σ在岗人数（12+3），空值行不参与求和'
        assert site['data'] == {'total': 2}, \
            'M-02 方案A = sanitation_site 行数（「数量/容量」2+1=3 不参与统计）'

        # 页面代理（MetricsEngine 唯一取数）
        proxy = client.get('/sanitation/api/stats').get_json()
        assert proxy['success'], proxy
        assert proxy['data']['headcount']['data'] == head['data'], '代理与引擎同值'
        assert proxy['data']['site_count']['data'] == site['data'], '代理与引擎同值'

        # 统一统计入口（看板/报表消费方路径）
        unified_h = client.get('/api/stats/query?metric_key=sanitation_team_headcount').get_json()
        unified_s = client.get('/api/stats/query?metric_key=sanitation_site_count').get_json()
        assert unified_h['success'] and unified_h['data']['data'] == head['data']
        assert unified_s['success'] and unified_s['data']['data'] == site['data']

    def test_dirty_numeric_values_fail_soft_no_500(self, app, _clean_agg_cache):
        """复验 P1：负数 / 非数值 / NaN / Infinity 均 fail-soft，不得 500 打挂指标端点。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_table(client, TEAM_CODE, TEAM_HEADERS, TEAM_ROWS[:2], TEAM_FIELDS, '环卫保洁队伍')
        with app.app_context():
            from models import DynamicRecord, DynamicTable, db
            table = DynamicTable.query.filter_by(code=TEAM_CODE).first()
            for raw in ('-5', 'abc', 'NaN', 'Infinity'):
                db.session.add(DynamicRecord(
                    table_id=table.id,
                    data=json.dumps({'f00': '脏数据', 'f03': raw}, ensure_ascii=False),
                    created_by='t090c'))
            db.session.commit()

        from utils.metrics import MetricsEngine
        with app.app_context():
            head = MetricsEngine.query('sanitation_team_headcount')['data']
        assert head == {'total': 6, 'sum': 15, 'empty': 4}, \
            '四类脏值必须全走 fail-soft（负数/非数值/NaN/Infinity）'

        proxy = client.get('/sanitation/api/stats')
        assert proxy.status_code == 200, proxy.get_data(as_text=True)
        assert proxy.get_json()['data']['headcount']['data'] == head, '代理不得 500'
        unified = client.get('/api/stats/query?metric_key=sanitation_team_headcount')
        assert unified.status_code == 200, unified.get_data(as_text=True)
        assert unified.get_json()['data']['data'] == head, '统一入口不得 500'

    def test_registry_schema_and_softtable_absent_zero(self, app, _clean_agg_cache):
        """注册表九项声明字段齐全；未建表时指标 0 值不报错（fail-soft）。"""
        from utils.metrics import METRIC_REGISTRY, SCHEMA_KEYS
        for key in ('sanitation_team_headcount', 'sanitation_site_count'):
            metric = METRIC_REGISTRY[key]
            for field in SCHEMA_KEYS:
                assert field in metric, '%s 缺声明字段 %s' % (key, field)
            assert metric['permission'] == 'sanitation'
        from utils.metrics import MetricsEngine
        with app.app_context():
            head = MetricsEngine.query('sanitation_team_headcount')['data']
            site = MetricsEngine.query('sanitation_site_count')['data']
        assert head == {'total': 0, 'sum': 0, 'empty': 0}, '未建表 M-01 应 0 值'
        assert site == {'total': 0}, '未建表 M-02 应 0 值'


# ------------------------------------------------------------------ F. 权限三处同步

class TestPermissions:
    """验收 5：三处同步 + 096 门禁守护 + 授权红→绿。"""

    def test_blueprint_module_registered(self):
        from utils.permissions import BLUEPRINT_MODULES
        assert BLUEPRINT_MODULES.get('sanitation') == 'sanitation'

    def test_permission_panel_covers_sanitation(self):
        """096 门禁同款断言：面板 permissionModules 键集 ⊇ BLUEPRINT_MODULES 值集。"""
        from utils.permissions import BLUEPRINT_MODULES
        path = os.path.join(_ROOT, 'static', 'js', 'pages', 'role_mgmt_list.js')
        src = open(path, encoding='utf-8').read()
        match = re.search(r'var permissionModules = \{([\s\S]*?)\};', src)
        assert match, 'role_mgmt_list.js 必须声明 permissionModules'
        keys = set(re.findall(r"'([^']+)'\s*:", match.group(1)))
        missing = set(BLUEPRINT_MODULES.values()) - keys
        assert not missing, '权限面板缺少模块键：%s' % sorted(missing)
        assert "'sanitation': '环卫工作'" in src, '面板中文名缺「环卫工作」'

    def test_role_with_sanitation_view_granted(self, app):
        """红→绿：授予 sanitation.view 的非超管可实际访问门面页/台账/指标代理。"""
        uid = _make_role_user(app, 'z090c_clerk', {'sanitation.view': True})
        client = app.test_client()
        _login(client, uid, 'z090c_clerk')
        assert client.get('/sanitation/').status_code == 200
        assert client.get('/sanitation/roster/%s/' % TEAM_CODE).status_code == 200
        stats = client.get('/sanitation/api/stats')
        assert stats.status_code == 200 and stats.get_json()['success']

    def test_role_without_sanitation_forbidden(self, app):
        """无 sanitation 位 → 403（页面/台账/API/附件 四层默认拒绝）。"""
        uid = _make_role_user(app, 'z090c_none', {})
        client = app.test_client()
        _login(client, uid, 'z090c_none')
        assert client.get('/sanitation/').status_code == 403
        assert client.get('/sanitation/roster/%s/' % TEAM_CODE).status_code == 403
        assert client.get('/sanitation/roster/%s/api/list' % TEAM_CODE).status_code == 403
        assert client.get('/sanitation/api/attachments').status_code == 403

    def test_view_only_post_blocked_by_action_bits(self, app):
        """复验 P2-2：仅有 sanitation.view 时，写 POST 必须 403（动作位自动映射）。"""
        uid = _make_role_user(app, 'z090c_view_only', {'sanitation.view': True})
        client = app.test_client()
        _login(client, uid, 'z090c_view_only')
        assert client.get('/sanitation/').status_code == 200, 'view 位应可读门面页'

        rec = client.post('/sanitation/roster/%s/api/record' % TEAM_CODE,
                          json={'values': {'f00': '无权限写入'}})
        assert rec.status_code == 403, rec.get_data(as_text=True)   # add 动作位
        up = client.post('/sanitation/api/attachments/upload',
                         data={'file': (io.BytesIO(b'x'), 'x.png')},
                         content_type='multipart/form-data')
        assert up.status_code == 403, up.get_data(as_text=True)      # upload → add 动作位
        dele = client.post('/sanitation/api/attachments/1/delete')
        assert dele.status_code == 403, dele.get_data(as_text=True)  # delete 动作位

    def test_dynatable_only_cannot_borrow(self, app):
        """dynatable 权限不可借道业务台账（ADR-027，099 R-04 同款）。"""
        uid = _make_role_user(app, 'z090c_dyn_only', {'dynatable.view': True})
        client = app.test_client()
        _login(client, uid, 'z090c_dyn_only')
        assert client.get('/sanitation/roster/%s/' % TEAM_CODE).status_code == 403

    def test_stats_query_permission_gate(self, app, _clean_agg_cache):
        """统一入口按指标 permission 二次校验：无 sanitation.view → 403。"""
        uid = _make_role_user(app, 'z090c_unified', {})
        client = app.test_client()
        _login(client, uid, 'z090c_unified')
        resp = client.get('/api/stats/query?metric_key=sanitation_team_headcount')
        assert resp.status_code == 403, resp.get_data(as_text=True)


# ------------------------------------------------------------------ G. 侧栏 + H. 深链

class TestSidebarAndDeepLinks:
    """验收 5（nav_spec 挂载）+ 验收 1（404 反例）。"""

    def test_sanitation_in_governance_sidebar_group(self, app):
        """/sanitation/ 渲染的侧栏「社区治理」组含环卫工作入口（真源派生）。"""
        from utils.nav_spec import NAV_GROUPS
        client = app.test_client()
        _login(client, _admin_id(app))
        html = client.get('/sanitation/').get_data(as_text=True)
        m = re.search(r'<aside class="app-sidebar sc-sidebar" id="sidebar">.*?</aside>',
                      html, re.S)
        assert m, '侧栏未渲染'
        sidebar = m.group(0)
        seg = re.split(r'<div class="sc-sidebar__group">', sidebar)
        group_html = [s for s in seg if '社区治理' in s]
        assert group_html and 'href="/sanitation/"' in group_html[0], \
            '侧栏「社区治理」组必须含 /sanitation/（一页纸 §7）'
        governance = [g for g in NAV_GROUPS if g['key'] == 'governance'][0]
        assert {'endpoint': 'sanitation.index', 'label': '环卫工作', 'icon': 'wrench'} \
            in governance['items']

    def test_unknown_and_cross_module_code_404(self, app):
        """未知 code / 跨模块 code 双向 404（facade.get_table 归 None）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        assert client.get('/sanitation/roster/unknown_code/').status_code == 404
        assert client.get('/sanitation/roster/pension_roster/').status_code == 404
        assert client.get('/pension/roster/%s/' % TEAM_CODE).status_code == 404


# ------------------------------------------------------------------ I. 设计规范下线项

class TestDeprecatedAttachmentModule:
    """设计稿规范明确移除整治照片附件模块及其环卫专属 API。"""

    def test_attachment_routes_removed(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        assert client.get('/sanitation/api/attachments').status_code == 404
        assert client.post('/sanitation/api/attachments/upload').status_code == 404
