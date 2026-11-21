# -*- coding: utf-8 -*-
"""tests/test_085_venue_facility.py — 085 场所/设施（软表两册 + 年度缴费透视）。

覆盖（docs/tasks/085-venue-facility.md 验收标准中可自动化部分）：
A. m039 字典补录：首跑 2 分类 4 条目（clean_fee value≠label）+ 重放幂等 + 空库守卫
B. pivot_rows 纯函数：空表 / 单年 / 跨年自动列（验收 3）/ 列小计=明细求和 / 空值不计 / 行序保持
C. 两页可达（验收 1）：/shop/facility/ 200（未建引导页）；/shop/annual-fee/ 未建引导、
   建后透视矩阵（¥ 金额 + 小计）；未知 code 404
D. 重复拦截（验收 2）：保存路同场所+项目+年度拦、跨年放行、编辑自身放行；
   导入路批内重复拦（skip_error_rows 也不放行）
E. 指标（一页纸 §3）：shop_venue_total = shops 行数；shop_facility_active 使用状态=在用；
   未建表为 0
F. 导航（一页纸 §5）：102 起年度缴费撤出侧栏、融合店铺页；resident 胶囊减年度缴费；
   无 shop 权限角色开门面页 403（权限随业务模块位）
"""
import importlib.util
import io
import json
import os
import re
from decimal import Decimal

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_M031_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm031_roster_metadata.py')
_M039_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm039_venue_facility.py')

FEE_CODE = 'shop_annual_fee'
FACILITY_CODE = 'shop_facility'

# 一页纸 §2 定稿列（真实确认流的字段 code 由页生成——测试用显式 code，与门面同构）
FEE_FIELDS = [
    {'code': 'venue_name', 'display_name': '场所名称', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'pay_item', 'display_name': '缴费项目', 'data_type': 'enum',
     'dict_category_code': '缴费项目', 'sensitivity': 'none', 'is_required': True},
    {'code': 'pay_year', 'display_name': '缴费年度', 'data_type': 'number',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'amount_paid', 'display_name': '实缴金额', 'data_type': 'number',
     'sensitivity': 'amount', 'is_required': True},
]
FEE_HEADERS = ['场所名称', '缴费项目', '缴费年度', '实缴金额']


# ------------------------------------------------------------------ 夹具/工具

def _load_migration(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_m039():
    return _load_migration(_M039_PATH, 'm039_under_test')


@pytest.fixture()
def app(isolated_app_factory):
    """隔离应用 + 密钥解锁 + m031 元数据 + m039 字典（部署顺序：create_all → m031 → m039）。

    「设施类型」可能已被启动期 ensure_enum_dicts（DICT_SEEDS 真源）先行预置——
    这本身是 DICT_SEEDS 接线正确性的证据；m039 负责的是 value≠label 的缴费项目。
    """
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('venue-facility-test', salt=generate_salt())
    m031 = _load_migration(_M031_PATH, 'm031_under_test')
    stats31 = m031.run(os.environ['SHUCUN_DB_PATH'])
    assert len(stats31['inserted']) == 7, 'm031 预置 7 张台账必须成功：%s' % stats31
    stats = _load_m039().run(os.environ['SHUCUN_DB_PATH'])
    assert '缴费项目:clean_fee' in stats['items'], 'm039 应补 value≠label 的缴费项目：%s' % stats
    return application


def _login(client, user_id=1, username='admin'):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _build_fee_table(app):
    """直接经门面建 annual_fee 表（与确认页提交同构：normalize_fields → create_table）。"""
    from utils import roster_facade as facade
    from utils import softtable_engine as eng
    with app.app_context():
        facade.create_table('shop', FEE_CODE, '重点场所年度缴费', '人口与台账',
                            eng.normalize_fields([dict(f) for f in FEE_FIELDS]),
                            created_by='admin')


def _fee_values(name, year, amount, item='clean_fee'):
    return {'venue_name': name, 'pay_item': item,
            'pay_year': year, 'amount_paid': amount}


def _save(client, values, rid=None):
    url = '/shop/roster/%s/api/record' % FEE_CODE
    if rid:
        url += '/%d' % rid
    return client.post(url, json={'values': values})


def _make_role_user(app, username, permissions):
    with app.app_context():
        from models import Role, User, db
        role = Role(name='role_%s' % username, description='085 用例',
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


# ------------------------------------------------------------------ A. m039 字典

class TestM039Migration:
    def test_replay_idempotent_and_values(self, app):
        mod = _load_m039()
        db_path = os.environ['SHUCUN_DB_PATH']
        stats = mod.run(db_path)  # 夹具已首跑 → 全跳过
        assert stats['categories'] == [] and stats['items'] == []
        # 取值口径：clean_fee 编码 + 展示名（一页纸 §1 value≠label）
        with app.app_context():
            from models import DictCategory, DictItem
            cat = DictCategory.query.filter_by(name='缴费项目').one()
            items = {i.value: i.label for i in DictItem.query.filter_by(category_id=cat.id)}
            assert items == {'clean_fee': '清洁费（街道门店）'}
            cat2 = DictCategory.query.filter_by(name='设施类型').one()
            labels = [i.value for i in DictItem.query.filter_by(category_id=cat2.id)
                      .order_by(DictItem.sort_order)]
            assert labels == ['清洁点', '消防栓', '路灯']
            # DICT_SEEDS 唯一真源含设施类型（新库经 init_db 播种同值）
            from utils.dict_seed import DICT_SEEDS
            assert DICT_SEEDS['设施类型'] == ['清洁点', '消防栓', '路灯']
            # R-085-03：结构化种子（value≠label）两源逐值一致，且不进 label-only 闭集
            import init_db
            structured = {name: entries for name, _desc, entries in mod.VENUE_DICT_SEEDS
                          if any(value != label for value, label in entries)}
            assert structured == init_db.STRUCTURED_DICT_SEEDS
            for name in init_db.STRUCTURED_DICT_SEEDS:
                assert name not in DICT_SEEDS, '结构化种子不应混入 DICT_SEEDS：%s' % name

    def test_guard_missing_dict_tables(self, tmp_path):
        with pytest.raises(RuntimeError):
            _load_m039().run(str(tmp_path / 'empty.db'))

    def test_dump_sql_available(self):
        sql = _load_m039().dump_sql()
        assert 'clean_fee' in sql and '清洁费（街道门店）' in sql and '消防栓' in sql


# ------------------------------------------------------------------ B. pivot_rows

class TestPivotRows:
    def test_empty(self):
        from utils import softtable_engine as eng
        out = eng.pivot_rows([], 'venue_name', 'pay_year', 'amount_paid')
        assert out['row_keys'] == [] and out['col_keys'] == []
        assert out['cells'] == {} and out['col_totals'] == {}
        assert out['grand_total'] == 0

    def test_single_year_and_subtotal(self):
        from utils import softtable_engine as eng
        rows = [{'v': '甲', 'y': 2025, 'm': '800'},
                {'v': '乙', 'y': 2025, 'm': '1200.5'},
                {'v': '甲', 'y': 2025, 'm': '100'}]
        out = eng.pivot_rows(rows, 'v', 'y', 'm')
        assert out['col_keys'] == [2025]
        assert out['cells']['甲'][2025] == Decimal('900')
        assert out['cells']['乙'][2025] == Decimal('1200.5')
        assert out['col_totals'][2025] == Decimal('2100.5')
        assert out['grand_total'] == Decimal('2100.5')

    def test_cross_year_auto_column_and_sort(self):
        """跨年新增年度列自动出现（验收 3），列按年度升序。"""
        from utils import softtable_engine as eng
        rows = [{'v': '甲', 'y': 2025, 'm': '800'},
                {'v': '甲', 'y': 2024, 'm': '600'},
                {'v': '甲', 'y': 2026, 'm': '900'}]
        out = eng.pivot_rows(rows, 'v', 'y', 'm')
        assert out['col_keys'] == [2024, 2025, 2026]
        assert out['cells']['甲'][2024] == Decimal('600')
        assert out['col_totals'][2026] == Decimal('900')

    def test_blank_and_bad_values_not_counted(self):
        """空值/非数值不参与求和（一页纸 V-05）。"""
        from utils import softtable_engine as eng
        rows = [{'v': '甲', 'y': 2025, 'm': ''},
                {'v': '甲', 'y': 2025, 'm': '欠缴'},
                {'v': '甲', 'y': 2025, 'm': '300'}]
        out = eng.pivot_rows(rows, 'v', 'y', 'm')
        assert out['cells']['甲'][2025] == Decimal('300')

    def test_row_order_preserved_and_blank_keys_skipped(self):
        from utils import softtable_engine as eng
        rows = [{'v': '乙', 'y': 2025, 'm': '1'},
                {'v': '甲', 'y': 2025, 'm': '2'},
                {'v': '', 'y': 2025, 'm': '99'},
                {'v': '丙', 'y': '', 'm': '99'}]
        out = eng.pivot_rows(rows, 'v', 'y', 'm')
        assert out['row_keys'] == ['乙', '甲']  # 首现序，非排序
        assert out['col_totals'][2025] == Decimal('3')

    def test_non_numeric_rows_not_created(self):
        """R-085-05：只有非数值/空值的行与列不进矩阵（跳过而非计 0）。"""
        from utils import softtable_engine as eng
        rows = [{'v': '甲', 'y': 2025, 'm': '欠缴'},
                {'v': '乙', 'y': 2026, 'm': ''},
                {'v': '丁', 'y': 2026, 'm': 'NaN'},
                {'v': '戊', 'y': 2026, 'm': 'Infinity'},
                {'v': '丙', 'y': 2025, 'm': '100'}]
        out = eng.pivot_rows(rows, 'v', 'y', 'm')
        assert out['row_keys'] == ['丙']
        assert out['col_keys'] == [2025]
        assert out['cells'] == {'丙': {2025: Decimal('100')}}
        assert out['col_totals'] == {2025: Decimal('100')}
        assert out['grand_total'] == Decimal('100')


# ------------------------------------------------------------------ C. 两页可达

class TestFacadePages:
    def test_facility_page_bootstrap_when_unbuilt(self, app):
        _login(app.test_client())
        client = app.test_client()
        _login(client)
        resp = client.get('/shop/facility/')
        assert resp.status_code == 200
        assert 'shop_facility' in resp.get_data(as_text=True)

    def test_annual_fee_page_bootstrap_then_pivot(self, app):
        client = app.test_client()
        _login(client)
        # 未建表 → 引导页 200
        assert client.get('/shop/annual-fee/').status_code == 200
        # 建表 + 两场所三年明细
        _build_fee_table(app)
        with app.app_context():
            from utils import roster_facade as facade
            from models import DynamicTable
            table = DynamicTable.query.filter_by(code=FEE_CODE).one()
            facade.save_row(table, None, _fee_values('旺铺超市', '2025', '800'))
            facade.save_row(table, None, _fee_values('旺铺超市', '2026', '900'))
            facade.save_row(table, None, _fee_values('便民理发', '2025', '1200.5'))
        resp = client.get('/shop/annual-fee/')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert '旺铺超市' in html and '便民理发' in html
        assert '¥800.00' in html and '¥1200.50' in html
        assert '¥2000.50' in html  # 2025 列小计 = 800 + 1200.5（验收 3 对账）
        assert '小计' in html and '2026年' in html  # 跨年列自动出现

    def test_zero_amount_renders_currency(self, app):
        """R-085-06：合法 0 金额显示 ¥0.00，不落「—」（与未录入区分）。"""
        _build_fee_table(app)
        with app.app_context():
            from utils import roster_facade as facade
            from models import DynamicTable
            table = DynamicTable.query.filter_by(code=FEE_CODE).one()
            facade.save_row(table, None, _fee_values('旺铺超市', '2025', '0'))
        client = app.test_client()
        _login(client)
        html = client.get('/shop/annual-fee/').get_data(as_text=True)
        assert '¥0.00' in html

    def test_unknown_code_404(self, app):
        client = app.test_client()
        _login(client)
        assert client.get('/shop/roster/no_such_code/').status_code == 404


class TestFreshAppPages:
    """R-085-01：未执行 m031 的全新库也必须 200（无侧栏/胶囊死链）。"""

    def test_pages_200_without_m031(self, isolated_app_factory):
        application = isolated_app_factory()
        client = application.test_client()
        _login(client)
        for url in ('/shop/facility/', '/shop/annual-fee/'):
            resp = client.get(url)
            assert resp.status_code == 200, '%s → %s' % (url, resp.status_code)
            body = resp.get_data(as_text=True)
            assert '台账元数据未初始化' in body, '%s 未渲染 m031 引导文案' % url


# ------------------------------------------------------------------ D. 重复拦截

class TestDuplicateGuard:
    def _prepare(self, app):
        _build_fee_table(app)
        client = app.test_client()
        _login(client)
        resp = _save(client, _fee_values('旺铺超市', '2025', '800'))
        assert resp.get_json()['success'], resp.get_json()
        return client

    def test_save_duplicate_blocked(self, app):
        client = self._prepare(app)
        resp = _save(client, _fee_values('旺铺超市', '2025', '999'))
        body = resp.get_json()
        assert body['success'] is False and '重复' in body['msg']

    def test_save_different_year_allowed(self, app):
        client = self._prepare(app)
        resp = _save(client, _fee_values('旺铺超市', '2026', '900'))
        assert resp.get_json()['success'] is True

    def test_save_edit_self_allowed(self, app):
        """编辑行自身（金额改动、键不变）不触发拦截。"""
        client = self._prepare(app)
        with app.app_context():
            from models import DynamicRecord, DynamicTable
            table = DynamicTable.query.filter_by(code=FEE_CODE).one()
            rid = DynamicRecord.query.filter_by(table_id=table.id).one().id
        resp = _save(client, _fee_values('旺铺超市', '2025', '1000'), rid=rid)
        assert resp.get_json()['success'] is True
        # 改键撞其他行仍拦
        _save(client, _fee_values('便民理发', '2025', '500'))
        resp = _save(client, _fee_values('便民理发', '2025', '700'), rid=rid)
        assert resp.get_json()['success'] is False

    def test_import_duplicate_blocked_even_with_skip(self, app):
        """导入路批内重复拦截；重复属数据冲突，skip_error_rows 不放行（V-02）。"""
        client = app.test_client()
        _login(client)
        rows = [['旺铺超市', '清洁费（街道门店）', '2025', '800'],
                ['旺铺超市', '清洁费（街道门店）', '2025', '800']]
        up = client.post('/shop/roster/%s/api/upload' % FEE_CODE,
                         data={'file': (_csv_bytes(FEE_HEADERS, rows), 'fee.csv')},
                         content_type='multipart/form-data')
        token = up.get_json()['data']['token']
        created = client.post('/shop/roster/%s/api/create' % FEE_CODE,
                              json={'token': token, 'name': '重点场所年度缴费',
                                    'fields': FEE_FIELDS, 'skip_error_rows': True})
        body = created.get_json()
        assert body['success'] is False and '重复' in body['msg']
        # 干净文件可建表，且 label→value 归一落库
        up2 = client.post('/shop/roster/%s/api/upload' % FEE_CODE,
                          data={'file': (_csv_bytes(FEE_HEADERS, rows[:1]), 'fee2.csv')},
                          content_type='multipart/form-data')
        token2 = up2.get_json()['data']['token']
        created2 = client.post('/shop/roster/%s/api/create' % FEE_CODE,
                               json={'token': token2, 'name': '重点场所年度缴费',
                                     'fields': FEE_FIELDS})
        assert created2.get_json()['success'], created2.get_json()
        assert created2.get_json()['data']['imported'] == 1


# ------------------------------------------------------------------ E. 指标

class TestMetrics:
    def test_venue_total_counts_shops(self, app):
        with app.app_context():
            from models import Shop, db
            db.session.add(Shop(name='旺铺超市'))
            db.session.add(Shop(name='便民理发'))
            db.session.commit()
        with app.app_context():
            from utils.metrics import MetricsEngine
            out = MetricsEngine.query('shop_venue_total')
            assert out['data']['total'] == 2

    def test_facility_active_and_unbuilt_zero(self, app):
        from utils import agg_cache
        from utils.metrics import MetricsEngine
        with app.app_context():
            # 未建表 → 0（与 088 未建册口径一致）
            agg_cache.invalidate()
            assert MetricsEngine.query('shop_facility_active')['data']['active'] == 0
            # 建表 + 使用状态列：2 在用 / 1 停用
            from utils import roster_facade as facade
            from utils import softtable_engine as eng
            fields = eng.normalize_fields([
                {'code': 'f_name', 'display_name': '设施名称', 'data_type': 'text',
                 'sensitivity': 'none', 'is_required': True},
                {'code': 'f_status', 'display_name': '使用状态', 'data_type': 'text',
                 'sensitivity': 'none', 'is_required': False},
            ])
            facade.create_table('shop', FACILITY_CODE, '基础设施分布', '人口与台账',
                                fields, created_by='admin')
            from models import DynamicTable
            table = DynamicTable.query.filter_by(code=FACILITY_CODE).one()
            facade.save_row(table, None, {'f_name': '清洁点A', 'f_status': '在用'})
            facade.save_row(table, None, {'f_name': '消防栓B', 'f_status': '在用'})
            facade.save_row(table, None, {'f_name': '路灯C', 'f_status': '停用'})
            agg_cache.invalidate()
            out = MetricsEngine.query('shop_facility_active')
            assert out['data']['active'] == 2 and out['data']['total'] == 3


# ------------------------------------------------------------------ F. 导航与权限

class TestNavAndPermissions:
    def test_nav_spec_annual_fee_folded_into_shop(self):
        """102：年度缴费撤出侧栏（101 已融合店铺页，改由页头按钮进入）。"""
        from utils.nav_spec import NAV_GROUPS
        endpoints = [i['endpoint'] for g in NAV_GROUPS for i in g['items']]
        assert 'shop.annual_fee_page' not in endpoints

    def test_resident_chips_and_social_chips(self, isolated_app_factory):
        """102：resident 胶囊减年度缴费；新增 social 组承载医保/养老切换。"""
        app = isolated_app_factory()
        from flask import render_template_string
        resident_src = ("{% from 'components/_macros/module_tabs.html' import module_tabs %}"
                        "{{ module_tabs('resident', 'query') }}")
        social_src = ("{% from 'components/_macros/module_tabs.html' import module_tabs %}"
                      "{{ module_tabs('social', 'medical-insurance') }}")
        with app.test_request_context('/'):
            resident_html = render_template_string(resident_src)
            social_html = render_template_string(social_src)
        assert '设施分布' in resident_html
        assert '年度缴费' not in resident_html, '102：resident 胶囊已撤年度缴费'
        assert '医保参保管理' in social_html and '养老保险' in social_html
        assert social_html.count('is-active') == 1 and '#tab=medical-insurance' in social_html

    def test_sidebar_no_longer_renders_annual_fee(self, app):
        client = app.test_client()
        _login(client)
        html = client.get('/shop/').get_data(as_text=True)
        sidebar = re.search(r'<aside class="app-sidebar sc-sidebar" id="sidebar">.*?</aside>',
                            html, re.S)
        assert sidebar, '侧栏未渲染'
        assert 'href="/shop/annual-fee/"' not in sidebar.group(0), '102：侧栏年度缴费入口应已撤'
        # 店铺页内两报表按钮常驻（101 能力保留；批量登记按钮随 fee_ctx 就绪态出现）
        assert '缴费透视' in html and 'Excel 上传' in html

    def test_no_shop_permission_403(self, app):
        """无 shop 模块位角色开门面页 → 403（权限随业务模块位，V-01 反例）。"""
        uid = _make_role_user(app, 'clerk085', {'__schema_version__': 3,
                                                'special': {'view_sensitive': False}})
        client = app.test_client()
        _login(client, user_id=uid, username='clerk085')
        assert client.get('/shop/facility/').status_code == 403
        assert client.get('/shop/annual-fee/').status_code == 403

    def test_annual_fee_detail_requires_shop_view(self, app):
        """101 R1：详情 GET 归 shop.view——无 shop 模块位用户 403（P2-1 反例）。"""
        uid = _make_role_user(app, 'noperm101', {'__schema_version__': 3})
        client = app.test_client()
        _login(client, user_id=uid, username='noperm101')
        assert client.get('/shop/annual-fee/detail?name=x').status_code == 403

    def test_annual_fee_batch_add_requires_shop_add(self, app):
        """101 R1：批量登记 POST 归 shop.add——仅 view 用户 403（P2-1 反例）。"""
        uid = _make_role_user(app, 'viewer101', {'__schema_version__': 3, 'shop.view': True})
        client = app.test_client()
        _login(client, user_id=uid, username='viewer101')
        assert client.get('/shop/annual-fee/detail?name=x').status_code == 200
        assert client.post('/shop/annual-fee/batch-add',
                           json={'year': 2026, 'rows': [{'name': 'x', 'amount': '1'}]}
                           ).status_code == 403


# ------------------------------------------------------------------ 101. 年度缴费融合（店铺页内完整入口）

class TestShopAnnualFeeMerge:
    """101：店铺列表当年状态列/筛选 + 详情历年记录/登记 + 批量登记；透视/导出零回归。

    覆盖 101 验收 1/2/3 可自动化部分：
    - 列表当年状态列（已缴含金额/未缴）与 fee_status 筛选；
    - 登记一条（roster record 通道）后列表状态与详情历年记录同步更新；
    - /shop/annual-fee/ 与 /annual-fee/export 仍 200；roster 上传页可达；
    - 软表未初始化/未建表 → 引导页/友好 JSON，不 500；
    - 未建表时店铺页不渲染缴费件（列/按钮缺席），页面行为同旧版。
    """

    def _prepare(self, app):
        _build_fee_table(app)
        _mk_shops(app, ['旺铺超市', '便民理发', '甜心烘焙'])
        client = app.test_client()
        _login(client)
        return client

    def _save_year_rows(self, app, rows):
        from utils import roster_facade as facade
        from models import DynamicTable
        with app.app_context():
            table = DynamicTable.query.filter_by(code=FEE_CODE).one()
            for name, year, amount in rows:
                facade.save_row(table, None, _fee_values(name, year, amount))

    def test_list_page_renders_fee_column_and_buttons(self, app):
        client = self._prepare(app)
        html = client.get('/shop/').get_data(as_text=True)
        year = _current_year()
        assert '年度清洁费（%s）' % year in html, '当年状态列表头缺席（界面固定「年度清洁费」口径）'
        assert '按年度批量登记' in html and 'feeStatusFilter' in html
        assert '缴费透视' in html and 'Excel 上传' in html
        assert 'feeCtx' in html and 'id="feeModal"' in html and 'id="feeBatchModal"' in html

    def test_api_list_fee_status_embedded(self, app):
        client = self._prepare(app)
        year = str(_current_year())
        self._save_year_rows(app, [('旺铺超市', year, '800')])
        resp = client.get('/shop/api/list').get_json()
        by_name = {d['name']: d for d in resp['data']}
        assert by_name['旺铺超市']['fee']['status'] == '已缴'
        assert float(by_name['旺铺超市']['fee']['amount']) == 800.0
        others = [d for d in resp['data'] if d['name'] != '旺铺超市']
        assert all(d['fee']['status'] == '未缴' for d in others), '未缴费店铺应为未缴态'

    def test_fee_status_filter(self, app):
        client = self._prepare(app)
        year = str(_current_year())
        self._save_year_rows(app, [('旺铺超市', year, '800')])
        paid = client.get('/shop/api/list?fee_status=paid').get_json()
        assert [d['name'] for d in paid['data']] == ['旺铺超市']
        unpaid = client.get('/shop/api/list?fee_status=unpaid').get_json()
        assert '旺铺超市' not in [d['name'] for d in unpaid['data']]
        assert all(d['fee']['status'] == '未缴' for d in unpaid['data'])

    def test_detail_history_sorted_desc(self, app):
        client = self._prepare(app)
        self._save_year_rows(app, [('旺铺超市', '2025', '800'), ('旺铺超市', '2026', '900')])
        resp = client.get('/shop/annual-fee/detail?name=旺铺超市').get_json()
        assert resp['success']
        data = resp['data']
        assert data['item_display'] == '年度清洁费'
        assert [r['year'] for r in data['records']] == ['2026', '2025'], '历年记录按年度倒序'
        assert float(data['records'][0]['amount']) == 900.0
        assert data['codes']['venue'] == 'venue_name' and data['codes']['year'] == 'pay_year'
        assert data['codes']['amount'] == 'amount_paid'
        assert data['item_value'] == 'clean_fee', '界面隐藏项目后写入值应为 m039 定稿枚举值'

    def test_single_register_via_roster_api_syncs(self, app):
        """登记一条（roster record 通道，详情 codes 拼值）→ 列表状态与详情历年同步更新。"""
        client = self._prepare(app)
        year = str(_current_year())
        detail = client.get('/shop/annual-fee/detail?name=便民理发').get_json()['data']
        c = detail['codes']
        resp = client.post('/shop/roster/%s/api/record' % FEE_CODE,
                           json={'values': {c['venue']: '便民理发', c['item']: detail['item_value'],
                                            c['year']: year, c['amount']: '600'}}).get_json()
        assert resp['success'], resp
        lst = client.get('/shop/api/list').get_json()
        by_name = {d['name']: d for d in lst['data']}
        assert by_name['便民理发']['fee']['status'] == '已缴'
        assert float(by_name['便民理发']['fee']['amount']) == 600.0
        detail2 = client.get('/shop/annual-fee/detail?name=便民理发').get_json()['data']
        assert any(r['year'] == year for r in detail2['records']), '历年记录应含当年新登记'
        # 同店铺同年度重复登记 → 唯一规则拦截（V-02）
        dup = client.post('/shop/roster/%s/api/record' % FEE_CODE,
                          json={'values': {c['venue']: '便民理发', c['item']: detail['item_value'],
                                           c['year']: year, c['amount']: '1'}}).get_json()
        assert not dup['success'] and '重复' in dup['msg']

    def test_batch_add_and_dup_skip(self, app):
        client = self._prepare(app)
        year = str(_current_year())
        self._save_year_rows(app, [('旺铺超市', year, '800')])   # 旺铺当年已缴 → 批量应跳过
        resp = client.post('/shop/annual-fee/batch-add', json={
            'year': year,
            'rows': [{'name': '旺铺超市', 'amount': '999'},
                     {'name': '便民理发', 'amount': '600'},
                     {'name': '甜心烘焙', 'amount': ''},   # 前端留空不会提交；服务端也容忍
                     ]}).get_json()
        assert resp['success'], resp
        data = resp['data']
        assert data['ok'] == 1, data
        assert [s['name'] for s in data['skipped']] == ['旺铺超市']
        detail = client.get('/shop/annual-fee/detail?name=便民理发').get_json()['data']
        assert any(r['year'] == year and float(r['amount']) == 600.0 for r in detail['records'])
        # 批量后列表状态同步
        lst = client.get('/shop/api/list?fee_status=paid').get_json()
        assert sorted(d['name'] for d in lst['data']) == ['便民理发', '旺铺超市']

    def test_batch_add_validation(self, app):
        client = self._prepare(app)
        year = str(_current_year())
        # 非法金额 → failed 明细（SoftTableError 转行级错误，不 500）
        resp = client.post('/shop/annual-fee/batch-add', json={
            'year': year, 'rows': [{'name': '旺铺超市', 'amount': 'abc'}]}).get_json()
        assert resp['success'] and resp['data']['ok'] == 0
        assert resp['data']['failed'] and '不是数字' in resp['data']['failed'][0]['error']
        # 缺行 → 友好拒绝
        resp2 = client.post('/shop/annual-fee/batch-add', json={'year': year, 'rows': []}).get_json()
        assert not resp2['success'] and '缺少批量登记行' in resp2['msg']

    def test_unbuilt_degrades_gracefully(self, app):
        """m031 元数据在但未建表：店铺页不渲染缴费件；详情/批量/透视/导出友好不 500。"""
        client = app.test_client()
        _login(client)
        html = client.get('/shop/').get_data(as_text=True)
        assert '年度清洁费（' not in html, '未建表不应渲染当年状态列'
        assert '按年度批量登记' not in html
        assert '缴费透视' in html and 'Excel 上传' in html, '两报表按钮常在（引导页可达）'
        for url in ('/shop/annual-fee/detail?name=x',):
            resp = client.get(url).get_json()
            assert not resp['success'] and '尚未建立' in resp['msg']
        resp = client.post('/shop/annual-fee/batch-add',
                           json={'year': 2026, 'rows': [{'name': 'x', 'amount': '1'}]}).get_json()
        assert not resp['success'] and '尚未建立' in resp['msg']
        assert client.get('/shop/annual-fee/').status_code == 200  # 引导页
        assert client.get('/shop/annual-fee/export').get_json()['success'] is False

    def test_pages_200_without_m031_101(self, isolated_app_factory):
        """连 m031 都未跑（全新库）：店铺页 200 且不渲染缴费件；详情/批量友好拒绝。"""
        application = isolated_app_factory()
        client = application.test_client()
        _login(client)
        assert client.get('/shop/').status_code == 200
        html = client.get('/shop/').get_data(as_text=True)
        assert '年度清洁费（' not in html and '按年度批量登记' not in html
        assert not client.get('/shop/annual-fee/detail?name=x').get_json()['success']
        assert not client.post('/shop/annual-fee/batch-add',
                               json={'year': 2026, 'rows': [{'name': 'x', 'amount': '1'}]}).get_json()['success']
        assert client.get('/shop/annual-fee/').status_code == 200  # m031 缺 → 未初始化引导页


def _current_year():
    from datetime import datetime
    return datetime.now().year


def _mk_shops(app, names):
    """101：建 Shop 行（api_list 列的是 shops 表；缴费明细按场所名称与店铺名精确匹配）。"""
    from models import db, Shop
    with app.app_context():
        for name in names:
            if not Shop.query.filter_by(name=name).first():
                db.session.add(Shop(name=name))
        db.session.commit()
