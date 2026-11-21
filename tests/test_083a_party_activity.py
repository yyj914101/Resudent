# -*- coding: utf-8 -*-
"""tests/test_083a_party_activity.py — 083A 党务活动台账回归。

覆盖：
- 三类活动（党员教育/组织生活/党风廉政）CRUD / 筛选（年度×支部×类型）/ 导出
- 枚举只认 dict_items（非法值语义失败且不落库）；应到/实到非负整数；实到≤应到不强制
- 民主评议逐人：批量录入 upsert（年度+党员唯一）、等次非法失败不落库、分布=逐人求和
- 聚合对账：MetricsEngine（party_activity / party_review_grade）= 明细求和
- 权限：未登录 401/302、无 party 模块位 403、metrics 端点按指标权限 403
- 迁移 m032 双跑幂等；字典三源一致
"""
import importlib.util
import io
import json
import os
import sqlite3
import sys
import uuid
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DICT_EXPECTED = {
    '党务活动类型': ['党员教育', '组织生活', '党风廉政'],
    '民主评议等次': ['优秀', '合格', '基本合格', '不合格'],
}


# ══════════════════════════ 夹具 ══════════════════════════

@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    import config as cfg
    application.config['TEST_DB_PATH'] = str(cfg.DB_PATH)
    return application


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    from utils.agg_cache import invalidate
    invalidate()
    yield
    invalidate()


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令083', salt=generate_salt())


def _client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
    return client


def _admin_client(app):
    return _client_for(app, 1)


def _role_client(app, perms):
    from models import db, Role, User
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        role = Role(name=f'083A角色-{suffix}',
                    permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.commit()
        user = User(username=f'083A用户-{suffix}', password_hash='x', role_id=role.id)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    return _client_for(app, uid)


def _count(app, sql, params=()):
    conn = sqlite3.connect(app.config['TEST_DB_PATH'])
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _make_member(app, name, branch='第一党支部', party_status='在册'):
    from models import db, PartyMember
    with app.app_context():
        member = PartyMember(name=name, branch=branch, party_status=party_status)
        db.session.add(member)
        db.session.commit()
        mid = member.id
    return mid


def _activity_payload(**overrides):
    data = {
        'activity_type': '党员教育',
        'title': '党的二十大精神学习会',
        'activity_date': '2026-03-15',
        'host': '张书记',
        'expected_count': '20',
        'actual_count': '18',
        'branch': '第一党支部',
        'content': '集中学习',
        'remark': '',
    }
    data.update(overrides)
    return data


def _save_activity(client, **overrides):
    return client.post('/party/activity/api/save', data=_activity_payload(**overrides))


def _load_m032():
    path = os.path.join(BASE, 'scripts', 'migrations', 'm032_party_activity.py')
    spec = importlib.util.spec_from_file_location('m032_party_activity', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════ 字典 + 迁移 ══════════════════════════

class TestDictAndMigration:
    def test_seed_constant_matches(self):
        from utils.dict_seed import DICT_SEEDS
        for category, values in DICT_EXPECTED.items():
            assert DICT_SEEDS[category] == values, f'{category} 种子不一致'

    def test_seed_literal_not_duplicated_in_boot_files(self):
        from utils.dict_seed import DICT_SEEDS
        for category, values in DICT_EXPECTED.items():
            assert DICT_SEEDS[category] == values
        for name in ('init_db.py', 'app.py'):
            text = open(os.path.join(BASE, name), encoding='utf-8').read()
            for category, values in DICT_EXPECTED.items():
                literal = "'%s': [%s]," % (
                    category, ','.join("'%s'" % v for v in values))
                assert literal not in text, f'{name} 仍含 {category} 首装字面量（应唯一真源 DICT_SEEDS）'

    def test_m032_categories_match_dict_seeds(self):
        """R-083A-P3-3：m032.CATEGORIES 与 DICT_SEEDS 同值（迁移字面量非独立真源）。"""
        from utils.dict_seed import DICT_SEEDS
        mod = _load_m032()
        assert mod.CATEGORIES == {k: DICT_SEEDS[k] for k in mod.CATEGORIES}, \
            'm032.CATEGORIES 与 DICT_SEEDS 漂移'

    def test_app_db_has_two_categories(self, app):
        from models import DictCategory, DictItem
        with app.app_context():
            for category, values in DICT_EXPECTED.items():
                cat = DictCategory.query.filter_by(name=category).first()
                assert cat is not None, f'缺字典分类 {category}'
                got = [i.value for i in DictItem.query.filter_by(
                    category_id=cat.id).order_by(DictItem.sort_order).all()]
                assert got == values, f'{category} 取值不一致：{got}'
            from utils.dict_seed import ensure_enum_dicts
            assert ensure_enum_dicts() == (0, 0), '字典幂等补录二跑应零新增'

    def test_m032_idempotent_double_run(self, tmp_path):
        """m032 双跑幂等：首跑建表+补字典，二跑零变更。"""
        db_path = str(tmp_path / 'legacy.db')
        conn = sqlite3.connect(db_path)
        conn.execute(
            'CREATE TABLE dict_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name VARCHAR(50), description VARCHAR(200), is_active INTEGER, '
            'sort_order INTEGER, created_at DATETIME)')
        conn.execute(
            'CREATE TABLE dict_items (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'category_id INTEGER, label VARCHAR(100), value VARCHAR(100), '
            'sort_order INTEGER, is_active INTEGER, created_at DATETIME)')
        conn.commit()
        conn.close()

        mod = _load_m032()
        first = mod.run(db_path)
        assert sorted(first['new_categories']) == ['党务活动类型', '民主评议等次']
        assert first['new_items'] == 7

        second = mod.run(db_path)
        assert second['new_categories'] == []
        assert second['new_items'] == 0
        assert sorted(second['skipped_categories']) == ['党务活动类型', '民主评议等次']

        conn = sqlite3.connect(db_path)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {'party_activity', 'party_activity_review'} <= tables
            # (year, party_member_id) 唯一索引存在
            indexes = {r[1] for r in conn.execute('PRAGMA index_list(party_activity_review)')}
            assert 'uq_party_review_year_member' in indexes
            cats = conn.execute(
                'SELECT COUNT(*) FROM dict_categories WHERE name IN (?, ?)',
                ('党务活动类型', '民主评议等次')).fetchone()[0]
            items = conn.execute(
                'SELECT COUNT(*) FROM dict_items WHERE value IN '
                "('党员教育','组织生活','党风廉政','优秀','合格','基本合格','不合格')").fetchone()[0]
            assert cats == 2 and items == 7, '字典重复执行不得重复插入'
        finally:
            conn.close()


# ══════════════════════════ 活动 CRUD / 筛选 / 导出 ══════════════════════════

class TestActivityCrud:
    def test_create_list_detail_edit_delete(self, app):
        _unlock_keyholder()
        client = _admin_client(app)

        resp = _save_activity(client).get_json()
        assert resp['success'] is True, resp.get('msg')
        aid = resp['data']['id']

        listing = client.get('/party/activity/api/list').get_json()
        assert listing['success'] and listing['total'] == 1
        row = listing['data'][0]
        assert row['activity_type'] == '党员教育'
        assert row['expected_count'] == 20 and row['actual_count'] == 18

        detail = client.get(f'/party/activity/api/detail/{aid}').get_json()
        assert detail['success']
        assert detail['data']['title'] == '党的二十大精神学习会'

        resp = _save_activity(client, id=str(aid), title='学习会（更新）',
                              actual_count='21').get_json()
        assert resp['success'] is True
        detail = client.get(f'/party/activity/api/detail/{aid}').get_json()
        assert detail['data']['title'] == '学习会（更新）'
        assert detail['data']['actual_count'] == 21

        resp = client.post(f'/party/activity/api/delete/{aid}').get_json()
        assert resp['success'] is True
        assert _count(app, 'SELECT COUNT(*) FROM party_activity') == 0

    def test_required_fields_rejected_without_write(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        for field in ('activity_type', 'title', 'activity_date'):
            resp = _save_activity(client, **{field: ''}).get_json()
            assert resp['success'] is False, f'{field} 必填校验失效'
        assert _count(app, 'SELECT COUNT(*) FROM party_activity') == 0

    def test_activity_type_must_be_dict_value(self, app):
        """验收 4：activity_type 只接受字典内取值；非法值语义失败且不落库。"""
        _unlock_keyholder()
        client = _admin_client(app)
        resp = _save_activity(client, activity_type='文体活动').get_json()
        assert resp['success'] is False
        assert '不合法' in resp['msg'], resp['msg']
        assert _count(app, 'SELECT COUNT(*) FROM party_activity') == 0, '非法枚举值不得写库'

    def test_counts_non_negative_int_and_actual_may_exceed_expected(self, app):
        """应到/实到非负整数；实到 ≤ 应到不强制（缺席合法，列席超应到亦允许）。"""
        _unlock_keyholder()
        client = _admin_client(app)
        for bad in ({'expected_count': '-3'}, {'actual_count': 'abc'},
                    {'expected_count': '2.5'}, {'actual_count': '-1'}):
            resp = _save_activity(client, **bad).get_json()
            assert resp['success'] is False, f'{bad} 应被拒绝'
        resp = _save_activity(client, expected_count='10',
                              actual_count='25').get_json()
        assert resp['success'] is True, '实到超应到（列席）不应强制拒绝'
        assert _count(app, 'SELECT COUNT(*) FROM party_activity') == 1

    def test_filter_year_branch_type(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        _save_activity(client, title='A', activity_date='2025-06-01',
                       branch='第一党支部', activity_type='党员教育')
        _save_activity(client, title='B', activity_date='2026-03-01',
                       branch='第一党支部', activity_type='组织生活')
        _save_activity(client, title='C', activity_date='2026-03-02',
                       branch='第二党支部', activity_type='党风廉政')

        cases = [
            ({'year': '2026'}, {'B', 'C'}),
            ({'year': '2025'}, {'A'}),
            ({'branch': '第一党支部'}, {'A', 'B'}),
            ({'activity_type': '组织生活'}, {'B'}),
            ({'year': '2026', 'branch': '第一党支部'}, {'B'}),
            ({}, {'A', 'B', 'C'}),
        ]
        for params, expected_titles in cases:
            resp = client.get('/party/activity/api/list', query_string=params).get_json()
            assert resp['success']
            titles = {r['title'] for r in resp['data']}
            assert titles == expected_titles, f'{params} → {titles}'

    def test_export_xlsx_with_filters(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        _save_activity(client, title='A', activity_date='2025-06-01', branch='第一党支部')
        _save_activity(client, title='B', activity_date='2026-03-01', branch='第二党支部')

        import openpyxl
        export = client.get('/party/activity/api/export', query_string={'year': '2026'})
        assert 'spreadsheetml' in (export.content_type or '')
        wb = openpyxl.load_workbook(io.BytesIO(export.data))
        rows = [list(r) for r in wb.active.iter_rows(min_row=2, values_only=True)]
        assert len(rows) == 1 and rows[0][1] == 'B'
        header = [c.value for c in wb.active[1]]
        assert header == ['活动类型', '活动主题', '活动日期', '主持人',
                          '应到人数', '实到人数', '所在党支部', '活动内容', '备注']

    def test_missing_record_404_semantics(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        resp = client.post('/party/activity/api/save',
                           data=_activity_payload(id='99999')).get_json()
        assert resp['success'] is False
        resp = client.get('/party/activity/api/detail/99999').get_json()
        assert resp['success'] is False
        resp = client.post('/party/activity/api/delete/99999').get_json()
        assert resp['success'] is False


# ══════════════════════════ 民主评议子表 ══════════════════════════

class TestReviews:
    def test_batch_save_upsert_and_distribution_sum(self, app):
        """验收 3：等次分布 = 逐人记录求和；(year, member) 唯一 upsert。"""
        _unlock_keyholder()
        client = _admin_client(app)
        mid_a = _make_member(app, '甲')
        mid_b = _make_member(app, '乙')

        resp = client.post('/party/activity/api/review-save', data={
            'year': '2026', 'review_date': '2026-12-20',
            'member_ids': [str(mid_a), str(mid_b)],
            'grades': ['优秀', '合格'],
        }).get_json()
        assert resp['success'] is True, resp.get('msg')
        assert _count(app, 'SELECT COUNT(*) FROM party_activity_review') == 2

        rows = client.get('/party/activity/api/reviews',
                          query_string={'year': '2026'}).get_json()['data']
        assert len(rows) == 2
        names = {r['party_member_id']: r['member_name'] for r in rows}
        assert names[mid_a] == '甲' and names[mid_b] == '乙'
        tally = {}
        for r in rows:
            tally[r['grade']] = tally.get(r['grade'], 0) + 1
        assert tally == {'优秀': 1, '合格': 1}, '分布必须等于逐人记录求和'

        # upsert：同年度同党员重复提交 → 更新等次，不产生新行
        resp = client.post('/party/activity/api/review-save', data={
            'year': '2026', 'member_ids': [str(mid_a)], 'grades': ['基本合格'],
        }).get_json()
        assert resp['success'] is True
        assert _count(app, 'SELECT COUNT(*) FROM party_activity_review') == 2
        rows = client.get('/party/activity/api/reviews',
                          query_string={'year': '2026'}).get_json()['data']
        grade_a = [r['grade'] for r in rows if r['party_member_id'] == mid_a][0]
        assert grade_a == '基本合格'

        # 其他年度互不影响
        resp = client.post('/party/activity/api/review-save', data={
            'year': '2025', 'member_ids': [str(mid_a)], 'grades': ['优秀'],
        }).get_json()
        assert resp['success'] is True
        assert _count(app, 'SELECT COUNT(*) FROM party_activity_review') == 3

    def test_invalid_grade_rejected_without_write(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        mid = _make_member(app, '丙')
        resp = client.post('/party/activity/api/review-save', data={
            'year': '2026', 'member_ids': [str(mid)], 'grades': ['称职'],
        }).get_json()
        assert resp['success'] is False
        assert '不合法' in resp['msg']
        assert _count(app, 'SELECT COUNT(*) FROM party_activity_review') == 0, '非法等次不得写库'

    def test_batch_atomic_all_or_nothing(self, app):
        """整批前置校验：任一非法整批失败不落库（不留半批）。"""
        _unlock_keyholder()
        client = _admin_client(app)
        mid_a = _make_member(app, '甲')
        resp = client.post('/party/activity/api/review-save', data={
            'year': '2026',
            'member_ids': [str(mid_a), '99999'],
            'grades': ['优秀', '合格'],
        }).get_json()
        assert resp['success'] is False
        assert _count(app, 'SELECT COUNT(*) FROM party_activity_review') == 0

    def test_reviews_require_year(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        resp = client.get('/party/activity/api/reviews').get_json()
        assert resp['success'] is False

        resp = client.post('/party/activity/api/review-save',
                           data={'member_ids': ['1'], 'grades': ['优秀']}).get_json()
        assert resp['success'] is False

    def test_member_count_mismatch_rejected(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        resp = client.post('/party/activity/api/review-save', data={
            'year': '2026', 'member_ids': ['1', '2'], 'grades': ['优秀'],
        }).get_json()
        assert resp['success'] is False
        assert '数量不一致' in resp['msg']


# ══════════════════════════ 聚合对账（MetricsEngine） ══════════════════════════

class TestAggregation:
    def test_party_activity_metric_equals_detail_sum(self, app):
        """验收 2/6：按年度×支部聚合次数与应到实到合计 = 明细求和；指标与页面同源。"""
        _unlock_keyholder()
        client = _admin_client(app)
        _save_activity(client, title='A1', activity_date='2026-01-10',
                       branch='第一党支部', activity_type='党员教育',
                       expected_count='20', actual_count='18')
        _save_activity(client, title='A2', activity_date='2026-02-10',
                       branch='第一党支部', activity_type='组织生活',
                       expected_count='20', actual_count='15')
        _save_activity(client, title='A3', activity_date='2025-12-01',
                       branch='第二党支部', activity_type='党风廉政',
                       expected_count='10', actual_count='9')

        from utils.metrics import MetricsEngine
        with app.app_context():
            result = MetricsEngine.query('party_activity', dims=['year', 'branch'])
        detail = client.get('/party/activity/api/list?limit=100').get_json()['data']
        assert result['total'] == len(detail) == 3

        buckets = {b['key']: b for b in result['buckets']}
        detail_2026_1 = [r for r in detail
                         if r['activity_date'].startswith('2026') and r['branch'] == '第一党支部']
        bucket = buckets['2026 | 第一党支部']
        assert bucket['count'] == len(detail_2026_1) == 2
        assert bucket['expected_sum'] == sum(r['expected_count'] for r in detail_2026_1)
        assert bucket['actual_sum'] == sum(r['actual_count'] for r in detail_2026_1)
        assert result['data']['session_count'] == 3
        assert result['data']['expected_sum'] == sum(r['expected_count'] for r in detail)
        assert result['data']['actual_sum'] == sum(r['actual_count'] for r in detail)

        # 指标经 /api/stats/query 与 MetricsEngine 同值（页面统计走同一路径）
        stats = client.get('/api/stats/query',
                           query_string={'metric_key': 'party_activity',
                                         'dims': 'year,branch'}).get_json()
        assert stats['success'] is True
        assert stats['data']['total'] == result['total']
        assert stats['data']['data']['expected_sum'] == result['data']['expected_sum']

    def test_party_activity_metric_filter_year(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        _save_activity(client, title='A1', activity_date='2026-01-10',
                       expected_count='20', actual_count='18')
        _save_activity(client, title='A2', activity_date='2025-12-01',
                       expected_count='10', actual_count='9')

        from utils.metrics import MetricsEngine
        with app.app_context():
            result = MetricsEngine.query('party_activity', dims=['year'],
                                         filters={'year': '2026'})
        assert result['total'] == 1
        assert result['data']['expected_sum'] == 20

    def test_party_review_grade_metric_equals_rows(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        mids = [_make_member(app, name) for name in ('甲', '乙', '丙')]
        client.post('/party/activity/api/review-save', data={
            'year': '2026',
            'member_ids': [str(m) for m in mids],
            'grades': ['优秀', '合格', '合格'],
        })

        from utils.metrics import MetricsEngine
        with app.app_context():
            result = MetricsEngine.query('party_review_grade', dims=['grade'])
        assert result['total'] == 3, '等次分布 = 逐人记录求和'
        counts = {b['key']: b['count'] for b in result['buckets']}
        assert counts == {'合格': 2, '优秀': 1}

        with app.app_context():
            by_year = MetricsEngine.query('party_review_grade', dims=['year'],
                                          filters={'year': '2026'})
        assert by_year['total'] == 3

    def test_metric_reflects_writes_after_cache_invalidate(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        from utils.metrics import MetricsEngine
        from utils.agg_cache import invalidate
        with app.app_context():
            assert MetricsEngine.query('party_activity')['total'] == 0
        _save_activity(client, title='X', activity_date='2026-05-01')
        invalidate()
        with app.app_context():
            assert MetricsEngine.query('party_activity')['total'] == 1

    def test_unknown_dim_rejected(self, app):
        from utils.metrics import MetricsEngine
        with app.app_context():
            with pytest.raises(ValueError):
                MetricsEngine.query('party_activity', dims=['grid'])


# ══════════════════════════ 权限（三处同步自查） ══════════════════════════

class TestPermissions:
    def test_unauthenticated(self, app):
        _unlock_keyholder()
        client = app.test_client()
        page = client.get('/party/activity')
        assert page.status_code == 302, '未登录页面应重定向登录'
        for path in ('/party/activity/api/list', '/party/activity/api/options',
                     '/party/activity/api/export'):
            resp = client.get(path, headers={'X-Requested-With': 'XMLHttpRequest'})
            assert resp.status_code == 401, f'{path} 未登录应 401'
        save = client.post('/party/activity/api/save', data=_activity_payload())
        assert save.status_code == 401
        assert _count(app, 'SELECT COUNT(*) FROM party_activity') == 0, '越权请求写入了数据'

    def test_role_without_party_module_denied(self, app):
        """无 party 模块位：页面 403 + API 403 + 保存不落库。"""
        _unlock_keyholder()
        client = _role_client(app, {'resident': {'view': True}})
        page = client.get('/party/activity')
        assert page.status_code == 403
        for path in ('/party/activity/api/list', '/party/activity/api/options',
                     '/party/activity/api/reviews?year=2026',
                     '/party/activity/api/export'):
            resp = client.get(path, headers={'X-Requested-With': 'XMLHttpRequest'})
            assert resp.status_code == 403, f'{path} 无模块位应 403'
        save = client.post('/party/activity/api/save', data=_activity_payload())
        assert save.status_code == 403
        review_save = client.post('/party/activity/api/review-save', data={
            'year': '2026', 'member_ids': ['1'], 'grades': ['优秀']})
        assert review_save.status_code == 403
        assert _count(app, 'SELECT COUNT(*) FROM party_activity') == 0

    def test_metrics_endpoint_respects_party_permission(self, app):
        _unlock_keyholder()
        client = _role_client(app, {'resident': {'view': True}})
        resp = client.get('/api/stats/query',
                          query_string={'metric_key': 'party_activity'})
        assert resp.status_code == 403, '无 party.view 访问 party 指标应 403'

        admin = _admin_client(app)
        ok = admin.get('/api/stats/query',
                       query_string={'metric_key': 'party_activity'}).get_json()
        assert ok['success'] is True

    def test_page_renders_for_admin(self, app):
        _unlock_keyholder()
        client = _admin_client(app)
        html = client.get('/party/activity').get_data(as_text=True)
        assert '党务活动台账' in html
        assert "module_tabs('org', 'party-activity')" not in html  # 宏应渲染为胶囊而非源码
        assert 'party-activity' in html, 'org 组新胶囊应渲染'
