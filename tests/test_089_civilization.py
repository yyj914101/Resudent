# -*- coding: utf-8 -*-
"""tests/test_089_civilization.py — 089 文明实践站（civilization 门面 / 公约固定表 / 软表活动记录 / 两指标）

覆盖（docs/tasks/089-civilization-station.md 验收标准中可自动化部分）：
A. 门面页与引导态：GET /civilization/ 200（汇总卡 + 公约卡 + 活动记录入口 +
   规范列指引）；未登录 302；侧栏「党建工作」组含文明实践站（N-4）
B. m040 幂等：删库重跑 1 分类/5 条目、二跑全跳过；CATEGORIES 与 DICT_SEEDS
   同值；部分唯一索引在位（含 WHERE is_current = 1）；与 m031 的 dynamic_table
   行零重复（civilization_activity 恒 1 行、零字段预置）；缺字典表 → RuntimeError
C. 公约唯一现行（验收 1/2）：换版流（新增现行 → 旧现行同事务转历史）、
   任意时刻现行 ≤ 1、历史只读（编辑 403）、现行不可删（400）、标题校验、
   DB 部分唯一索引兜底竞态（绕过应用层写第二现行 → IntegrityError）
D. 软表活动闭环：规范列 CSV 上传 → 建表（m031 预置行就地复用）→ 枚举按
   value 筛选 → 非法枚举拒绝（roster + dynatable 400）→ 导出 enum 出 label
E. 两指标三处同值（验收 3）：年度场次/人次 = 活动明细求和（对账断言），
   页面代理 == 统一入口 == 引擎直调；人次逐场累加不去重（Q-B）；
   未建册 0 值 fail-soft；注册表九项声明字段齐全
F. 权限三处同步（验收 4）：BLUEPRINT_MODULES['civilization'] / role_mgmt 面板
   键集 / 授权正例（civilization.view 角色 200，无权限 403，dynatable-only 不可借道）
G. 深链与 404：未知/跨模块 code 404；引导页下发 civilization 端点不夹带 shop/pension
H. 附件四端点（092 收口）：上传/列表/下载/删除 + 跨模块下载 403
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
_M040_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm040_civilization.py')

CODE = 'civilization_activity'
HEADERS = ['活动日期', '活动主题', '活动类型', '参与人数', '活动地点', '活动内容', '照片', '记录人']
ROWS = [
    ['2026-01-10', '春节宣讲', '理论宣讲', '120', '村委会礼堂', '理论宣讲进乡村', 'photo1.png', '李记录'],
    ['2026-03-15', '春耕培训', '科技科普', '45', '田间地头', '农业科技培训', '', '王记录'],
    ['2025-12-01', '健身展演', '健身体育', '80', '文化广场', '全民健身展演', 'photo2.png', '李记录'],
]
# 对账口径（一页纸 §4）：2026 场次 2 / 人次 165；2025 场次 1 / 人次 80
YEAR_2026_SESSIONS, YEAR_2026_VISITS = 2, 165
YEAR_2025_SESSIONS, YEAR_2025_VISITS = 1, 80

# 一页纸 v1.2 §1 活动记录 8 列（A-001~A-004 必填，A-005~A-008 否；枚举挂 089 字典分类）
CIV_FIELDS = [
    {'code': 'f00', 'display_name': '活动日期', 'data_type': 'date',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f01', 'display_name': '活动主题', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f02', 'display_name': '活动类型', 'data_type': 'enum',
     'dict_category_code': '文明活动类型', 'sensitivity': 'none', 'is_required': True},
    {'code': 'f03', 'display_name': '参与人数', 'data_type': 'number',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f04', 'display_name': '活动地点', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f05', 'display_name': '活动内容', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f06', 'display_name': '照片', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f07', 'display_name': '记录人', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
]


# ------------------------------------------------------------------ 夹具/工具

def _load_migration(filename, modname):
    """importlib 加载迁移脚本（scripts/ 非包；test_088 同款方式）。"""
    spec = importlib.util.spec_from_file_location(modname, os.path.join(_ROOT, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_m031():
    return _load_migration(os.path.join('scripts', 'migrations', 'm031_roster_metadata.py'),
                           'm031_under_test_089')


def _load_m040():
    return _load_migration(os.path.join('scripts', 'migrations', 'm040_civilization.py'),
                           'm040_under_test_089')


@pytest.fixture()
def app(isolated_app_factory):
    """隔离应用 + 密钥解锁 + m031 元数据预置 + m040 建表补种（与部署顺序一致）。
    089 文明活动类型字典由 create_app 的 ensure_enum_dicts 守卫自动补种（DICT_SEEDS）。"""
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('civilization-test', salt=generate_salt())
    stats = _load_m031().run(os.environ['SHUCUN_DB_PATH'])
    assert len(stats['inserted']) == 7, 'm031 预置 7 张台账必须成功：%s' % stats
    _load_m040().run(os.environ['SHUCUN_DB_PATH'])
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
        role = Role(name='role_%s' % username, description='089 用例',
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
    return client.post('/civilization/roster/%s/api/upload' % code,
                       data={'file': (_csv_bytes(headers, rows), 'civilization_activity.csv')},
                       content_type='multipart/form-data')


def _create(client, token, code=CODE, name='文明实践活动记录'):
    return client.post('/civilization/roster/%s/api/create' % code,
                       json={'token': token, 'name': name, 'fields': CIV_FIELDS})


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
    return client.get('/civilization/roster/%s/api/list%s' % (CODE, query)).get_json()


def _convention_create(client, title, content='', attachment_path=''):
    return client.post('/civilization/api/conventions',
                       json={'title': title, 'content': content,
                             'attachment_path': attachment_path})


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
    """验收 A：门面页 200 + 汇总卡 + 公约卡 + 活动入口 + 规范列指引；未登录 302。"""

    def test_index_renders_cards_and_entry(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/civilization/')
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)
        assert '/civilization/roster/%s/' % CODE in html, '门面页必须给出活动记录入口'
        assert 'civSessions' in html and 'civPersonVisits' in html, \
            '汇总卡容器（年度场次/人次）必须在位'
        assert '居民公约' in html and '新增版本' in html, '公约卡（含新增版本入口）必须在位'
        assert '尚未建立' in html, '未建册须出引导态'
        for col in HEADERS:
            assert col in html, '规范列指引缺「%s」（一页纸 v1.2 §1）' % col
        assert '【待确认】' not in html, '活动 8 列已全部人工确认，不得出现待确认列'

    def test_index_requires_login(self, app):
        """深链 /civilization/ 未登录 → 302 跳登录。"""
        client = app.test_client()
        resp = client.get('/civilization/')
        assert resp.status_code == 302, resp.get_data(as_text=True)
        assert '/auth/login' in resp.headers.get('Location', '')

    def test_civilization_page_in_party_sidebar_group(self, app):
        """验收 5（nav）：/civilization/ 渲染的侧栏「党建工作」组含文明实践站（N-4 机构管理下）。"""
        from utils.nav_spec import NAV_GROUPS
        client = app.test_client()
        _login(client, _admin_id(app))
        html = client.get('/civilization/').get_data(as_text=True)
        m = re.search(r'<aside class="app-sidebar sc-sidebar" id="sidebar">.*?</aside>',
                      html, re.S)
        assert m, '侧栏未渲染'
        sidebar = m.group(0)
        party = [g for g in NAV_GROUPS if g['key'] == 'party'][0]
        assert party['items'][-1]['endpoint'] == 'civilization.index', \
            '文明实践站必须挂在「党建工作」组末位（机构管理下，N-4）'
        seg = re.split(r'<div class="sc-sidebar__group">', sidebar)
        party_html = [s for s in seg if '党建工作' in s]
        assert party_html and 'href="/civilization/"' in party_html[0], \
            '侧栏「党建工作」组必须含 /civilization/'


# ------------------------------------------------------------------ B. m040 幂等

class TestM040Migration:
    """验收 5：m040 重放幂等；与 m031 的 dynamic_table 行零重复。"""

    def test_idempotent_after_delete_reseed(self, app):
        """删除 089 字典分类后重跑：1 分类/5 条目；二跑全跳过、计数不变。"""
        db_path = os.environ['SHUCUN_DB_PATH']
        with app.app_context():
            from models import DictCategory, DictItem, db
            cat = DictCategory.query.filter_by(name='文明活动类型').first()
            if cat:
                DictItem.query.filter_by(category_id=cat.id).delete()
                db.session.delete(cat)
            db.session.commit()

        m040 = _load_m040()
        first = m040.run(db_path)
        assert first['new_categories'] == ['文明活动类型'], first
        assert first['new_items'] == 5, '1 分类 × 5 取值 = 5 条目：%s' % first
        assert first['table_created'] is False, 'civ_convention 已由夹具 m040 建好，重放只跳过'
        second = m040.run(db_path)
        assert second['new_categories'] == [] and second['new_items'] == 0, '二跑不得重复插入'
        assert second['skipped_categories'] == ['文明活动类型']

        with app.app_context():
            from models import DictCategory, DictItem
            cat = DictCategory.query.filter_by(name='文明活动类型').first()
            assert cat is not None
            items = DictItem.query.filter_by(category_id=cat.id).order_by(
                DictItem.sort_order.asc()).all()
            assert [i.label for i in items] == ['理论宣讲', '教育服务', '文化服务',
                                                '科技科普', '健身体育'], 'Q-A 五类字面量'
            for item in items:
                assert item.value == item.label, '089 字典取值 label=value'

    def test_categories_match_dict_seeds(self):
        """m040 CATEGORIES 与 DICT_SEEDS 同值（唯一真源，不得另造枚举；083A 同款断言）。"""
        from utils.dict_seed import DICT_SEEDS
        m040 = _load_m040()
        assert m040.CATEGORIES == {k: DICT_SEEDS[k] for k in m040.CATEGORIES}, \
            'm040 字典字面量必须与 DICT_SEEDS 逐字同值'

    def test_no_convention_status_dict(self, app):
        """Q-01 方案 A：不建「公约状态」字典（is_current 唯一真源）。"""
        with app.app_context():
            from models import DictCategory
            assert DictCategory.query.filter_by(name='公约状态').first() is None, \
                '「公约状态」字典不得存在'

    def test_partial_unique_index_present(self, app):
        """唯一现行 DB 侧保险：部分唯一索引在位且带 WHERE is_current = 1。"""
        db_path = os.environ['SHUCUN_DB_PATH']
        conn = sqlite3.connect(db_path)
        try:
            indexes = {row[1]: row for row in conn.execute(
                'PRAGMA index_list(civ_convention)')}
            assert 'uq_civ_convention_current' in indexes, '部分唯一索引必须存在：%s' % indexes
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' "
                "AND name='uq_civ_convention_current'").fetchone()[0]
            assert 'UNIQUE' in sql.upper() and 'WHERE' in sql.upper() \
                   and 'is_current = 1' in sql, '必须是 is_current = 1 的部分唯一索引：%s' % sql
        finally:
            conn.close()

    def test_no_dynamic_table_duplication_with_m031(self, app):
        """m040 与 m031 的 dynamic_table 行零重复：civilization_activity 恒 1 行。"""
        db_path = os.environ['SHUCUN_DB_PATH']
        _load_m040().run(db_path)  # m040 不插 dynamic_table 行
        n = _sqlite_scalar(db_path,
                           "SELECT COUNT(*) FROM dynamic_table WHERE code = '%s'" % CODE)
        assert n == 1, 'civilization_activity 元数据行必须恰好 1 行（m031 唯一预置点）'
        fields = _sqlite_scalar(db_path,
                                "SELECT COUNT(*) FROM dynamic_field WHERE table_id IN "
                                "(SELECT id FROM dynamic_table WHERE code = '%s')" % CODE)
        assert fields == 0, 'm040 不得预置字段行（ADR-025 决策 3）'

    def test_missing_dict_tables_raise(self, tmp_path):
        """库无字典表 → run 抛 RuntimeError（拒绝在无结构库上执行）。"""
        empty_db = str(tmp_path / 'no_dict.db')
        with pytest.raises(RuntimeError):
            _load_m040().run(empty_db)

    def test_replay_on_create_all_db_preserves_data(self, app):
        """create_all 库重放 m040：表体已存在不重建，索引/字典补齐，公约数据零丢失。"""
        with app.app_context():
            from models import CivConvention, db
            db.session.add(CivConvention(title='重放前公约', is_current=True))
            db.session.commit()
        before = _sqlite_scalar(os.environ['SHUCUN_DB_PATH'],
                                'SELECT COUNT(*) FROM civ_convention')
        stats = _load_m040().run(os.environ['SHUCUN_DB_PATH'])
        assert stats['table_created'] is False and stats['new_items'] == 0, stats
        after = _sqlite_scalar(os.environ['SHUCUN_DB_PATH'],
                               'SELECT COUNT(*) FROM civ_convention')
        assert before == after == 1, '重放 m040 不得丢失公约数据'


# ------------------------------------------------------------------ C. 公约唯一现行

class TestConventionFlow:
    """验收 1/2：唯一现行双保险 + 换版流 + 历史只读 + 现行不可删。"""

    def test_create_first_version_is_current(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = _convention_create(client, '村规民约（2026 修订）', '第一条 …', 'cuneguimin.pdf')
        assert resp.status_code == 200 and resp.get_json()['success'], resp.get_data(as_text=True)

        body = client.get('/civilization/api/conventions').get_json()
        assert body['data']['current']['title'] == '村规民约（2026 修订）'
        assert body['data']['current']['is_current'] is True
        assert body['data']['history'] == []
        assert body['data']['current']['attachment_path'] == 'cuneguimin.pdf'

    def test_version_change_demotes_old_invariant_one_current(self, app):
        """换版流：新增版本置现行，旧版本同事务自动转历史；任意时刻现行 ≤ 1。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        first = _convention_create(client, '第一版', 'v1 内容').get_json()['data']
        second = _convention_create(client, '第二版', 'v2 内容').get_json()['data']

        body = client.get('/civilization/api/conventions').get_json()
        assert body['data']['current']['id'] == second['id'], '最新版本必须是现行'
        assert body['data']['current']['is_current'] is True
        history_ids = [row['id'] for row in body['data']['history']]
        assert history_ids == [first['id']], '旧版本必须转历史（按创建时间倒序）'
        assert all(row['is_current'] is False for row in body['data']['history'])

        with app.app_context():
            from models import CivConvention
            assert CivConvention.query.filter_by(is_current=True).count() == 1, \
                '任意时刻「是否现行 = 1」至多一条'
            old = CivConvention.query.get(first['id'])
            assert old.is_current is False, '换版后旧版本必须自动转历史'

    def test_history_readonly_edit_403(self, app):
        """验收 2：历史版本只读（编辑接口返回 403）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        first = _convention_create(client, '第一版').get_json()['data']
        _convention_create(client, '第二版')  # 换版 → first 转历史
        resp = client.post('/civilization/api/conventions/%d' % first['id'],
                           json={'title': '篡改历史'})
        assert resp.status_code == 403, '历史版本编辑必须 403：%s' % resp.status_code
        assert resp.get_json()['success'] is False

        # 现行版本可正常编辑
        current_id = client.get('/civilization/api/conventions').get_json()['data']['current']['id']
        ok = client.post('/civilization/api/conventions/%d' % current_id,
                         json={'title': '第二版（修订）', 'content': '修订正文'})
        assert ok.status_code == 200 and ok.get_json()['success']
        assert ok.get_json()['data']['content'] == '修订正文'

    def test_delete_history_ok_current_refused(self, app):
        """删除仅限历史版本（清错行）；现行版本须先换版转历史后才能删。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        first = _convention_create(client, '第一版').get_json()['data']
        second = _convention_create(client, '第二版').get_json()['data']

        refuse = client.post('/civilization/api/conventions/%d/delete' % second['id'])
        assert refuse.status_code == 400 and refuse.get_json()['success'] is False, \
            '现行版本不可删除'
        ok = client.post('/civilization/api/conventions/%d/delete' % first['id'])
        assert ok.status_code == 200 and ok.get_json()['success']
        assert client.post('/civilization/api/conventions/99999/delete').status_code == 404

    def test_title_validation(self, app):
        """V-06（修订版）：标题不得为空、≤100 字。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        empty = _convention_create(client, '   ')
        assert empty.status_code == 400 or empty.get_json()['success'] is False
        too_long = _convention_create(client, '标' * 101)
        assert too_long.get_json()['success'] is False, '标题超 100 字必须拒绝'
        assert '100' in too_long.get_json()['msg']

    def test_db_partial_unique_index_catches_race(self, app):
        """验收 1（竞态反例）：绕过应用层直写第二现行 → DB 部分唯一索引捕获 IntegrityError。"""
        db_path = os.environ['SHUCUN_DB_PATH']
        client = app.test_client()
        _login(client, _admin_id(app))
        _convention_create(client, '第一版')

        conn = sqlite3.connect(db_path)
        try:
            conn.execute('PRAGMA foreign_keys = ON')
            # 竞态形态一：并发新增第二个现行（模拟两请求同时 commit 新现行）
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO civ_convention (title, is_current) VALUES ('并发现行', 1)")
            conn.rollback()
            # 竞态形态二：绕过应用层把历史行 UPDATE 回现行（模拟并发换版互漏）
            conn.execute("INSERT INTO civ_convention (title, is_current) VALUES ('历史行', 0)")
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE civ_convention SET is_current = 1 WHERE title = '历史行'")
        finally:
            conn.close()


# ------------------------------------------------------------------ D. 软表活动闭环

class TestRosterFlow:
    """验收 3（软表部分）：规范列建册 + 枚举 value 口径 + 非法枚举拒绝 + 导出 label。"""

    def test_create_roster_reuses_m031_preset_row(self, app):
        """建表复用 m031 预置行（不另起新行），字段按规范列落库。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        created = _build_roster(client)
        assert created['data']['imported'] == 3 and created['data']['code'] == CODE

        with app.app_context():
            from models import DynamicField, DynamicRecord, DynamicTable
            tables = DynamicTable.query.filter_by(code=CODE).all()
            assert len(tables) == 1, 'm031 预置行必须就地复用'
            assert tables[0].created_by == 'm031'
            assert tables[0].module_key == 'civilization'
            assert tables[0].group_name == '人口与台账', 'm031 预置组名不得被覆盖为其他组'
            assert DynamicField.query.filter_by(table_id=tables[0].id).count() == 8
            assert DynamicRecord.query.filter_by(table_id=tables[0].id).count() == 3

    def test_list_filter_enum_value(self, app):
        """列表：枚举按 value 筛选（096 口径）；无 identity 列故无打码路径。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        body = _list(client)
        assert body['success'] and body['total'] == 3
        assert '__digest__' not in body['data'][0], '指纹不得回传前端'

        enumed = _list(client, query='?f_f02=理论宣讲')
        assert enumed['total'] == 1, '枚举按 value 筛选命中 1 行（label=value 种子）'

        date_min = _list(client, query='?f_f00_min=2026-01-01&f_f00_max=2026-12-31')
        assert date_min['total'] == 2, '日期区间筛选命中 2026 年 2 行'

        page = client.get('/civilization/roster/%s/' % CODE)
        html = page.get_data(as_text=True)
        assert '尚未建立' not in html, '已建表不得再出引导页'
        assert '<option value="理论宣讲">理论宣讲</option>' in html, \
            '筛选下拉 value 传值 / label 展示（096 口径）'

    def test_invalid_enum_rejected_roster_and_dynatable_400(self, app):
        """非法枚举：roster 新增拒绝（success=False）；dynatable 更新 HTTP 400。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        bad = client.post('/civilization/roster/%s/api/record/0' % CODE,
                          json={'values': {'f00': '2026-05-01', 'f01': 'x',
                                           'f02': '广场舞大赛', 'f03': '10'}})
        body = bad.get_json()
        assert body['success'] is False, '非法枚举值必须被拒绝'
        assert '不在字典' in body['msg'], body

        rid = _list(client)['data'][0]['id']
        resp = client.post('/dynatable/api/t/%s/record/%d' % (CODE, rid),
                           json={'code': 'f02', 'value': '广场舞大赛'})
        assert resp.status_code == 400, 'dynatable 口径非法枚举必须 400：%s' % resp.status_code

    def test_export_enum_label(self, app):
        """导出：enum 出 label（096 口径）；列表列序 = 一页纸 §5 默认列序。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        resp = client.get('/civilization/roster/%s/api/export' % CODE)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        sheet = _export_cells(resp)
        assert sheet[0] == HEADERS, '导出列序必须与规范列一致（一页纸 §5）'
        assert sheet[1][2] == '理论宣讲', '导出 enum 列必须映射 label'
        assert sheet[1][3] == 120, '参与人数列保持数值'


# ------------------------------------------------------------------ E. 两指标三处同值

class TestMetricsReconciliation:
    """验收 3：年度场次/人次 = 明细求和（对账断言）；页面代理 == 统一入口 == 引擎直调。"""

    def test_metrics_reconcile_with_details(self, app, _clean_agg_cache):
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        from utils.metrics import MetricsEngine
        with app.app_context():
            s26 = MetricsEngine.query('civilization_sessions', filters={'year': '2026'})
            p26 = MetricsEngine.query('civilization_person_visits', filters={'year': '2026'})
            s25 = MetricsEngine.query('civilization_sessions', filters={'year': '2025'})
            p25 = MetricsEngine.query('civilization_person_visits', filters={'year': '2025'})
            s_all = MetricsEngine.query('civilization_sessions')
            p_all = MetricsEngine.query('civilization_person_visits')
        assert s26['data'] == {'sessions': YEAR_2026_SESSIONS}, '2026 场次 = 年度内活动行数'
        assert p26['data'] == {'person_visits': YEAR_2026_VISITS}, '2026 人次 = Σ 参与人数（Q-B 不去重）'
        assert s25['data'] == {'sessions': YEAR_2025_SESSIONS}
        assert p25['data'] == {'person_visits': YEAR_2025_VISITS}
        assert s_all['data'] == {'sessions': 3}, '不带 year = 全部年度'
        assert p_all['data'] == {'person_visits': YEAR_2026_VISITS + YEAR_2025_VISITS}

        # 对账断言：指标 = 明细逐行求和（验收 3「对账断言」；原始记录按字段 code 键存）
        with app.app_context():
            from models import DynamicRecord, DynamicTable
            table = DynamicTable.query.filter_by(code=CODE).first()
            raws = [json.loads(r.data or '{}')
                    for r in DynamicRecord.query.filter_by(table_id=table.id).all()]
            detail_visits_2026 = sum(int(r['f03']) for r in raws
                                     if (r.get('f00') or '').startswith('2026-'))
        assert detail_visits_2026 == YEAR_2026_VISITS == p26['data']['person_visits'], \
            '指标人次必须等于明细逐行求和'

    def test_three_callers_same_value(self, app, _clean_agg_cache):
        client = app.test_client()
        _login(client, _admin_id(app))
        _build_roster(client)

        from utils.metrics import MetricsEngine
        with app.app_context():
            sessions = MetricsEngine.query('civilization_sessions', filters={'year': '2026'})
            visits = MetricsEngine.query('civilization_person_visits', filters={'year': '2026'})

        # 页面代理（MetricsEngine 唯一取数）
        proxy = client.get('/civilization/api/stats?year=2026').get_json()
        assert proxy['success'], proxy
        assert proxy['data']['sessions']['data'] == sessions['data'], '代理与引擎同值'
        assert proxy['data']['person_visits']['data'] == visits['data'], '代理与引擎同值'

        # 统一统计入口（看板/报表消费方路径）
        unified = client.get(
            '/api/stats/query?metric_key=civilization_person_visits&year=2026').get_json()
        assert unified['success'], unified
        assert unified['data']['data'] == visits['data'], '统一入口与引擎同值'
        unified_s = client.get(
            '/api/stats/query?metric_key=civilization_sessions&year=2026').get_json()
        assert unified_s['data']['data'] == sessions['data'], '统一入口与引擎同值'

    def test_bad_year_400(self, app, _clean_agg_cache):
        """year 筛选非法值 → 代理 400（口径同 party handler）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/civilization/api/stats?year=not-a-year')
        assert resp.status_code == 400 and resp.get_json()['success'] is False

    def test_registry_schema_and_softtable_absent_zero(self, app, _clean_agg_cache):
        """注册表九项声明字段齐全；未建册（无字段行）时指标为 0 值不报错。"""
        from utils.metrics import METRIC_REGISTRY, SCHEMA_KEYS
        for key in ('civilization_sessions', 'civilization_person_visits'):
            metric = METRIC_REGISTRY[key]
            for field in SCHEMA_KEYS:
                assert field in metric, '%s 缺声明字段 %s' % (key, field)
            assert metric['permission'] == 'civilization'
        from utils.metrics import MetricsEngine
        with app.app_context():
            data = MetricsEngine.query('civilization_sessions')['data']
            visits = MetricsEngine.query('civilization_person_visits')['data']
        assert data == {'sessions': 0}, '未建册/空台账指标应返回 0（fail-soft，不抛错）'
        assert visits == {'person_visits': 0}


# ------------------------------------------------------------------ F. 权限三处同步

class TestPermissions:
    """验收 4：BLUEPRINT_MODULES / 权限面板键集 / 授权正例（红→绿）。"""

    def test_blueprint_module_registered(self):
        from utils.permissions import BLUEPRINT_MODULES
        assert BLUEPRINT_MODULES.get('civilization') == 'civilization'

    def test_permission_panel_covers_civilization(self):
        """096 门禁同款断言：面板 permissionModules 键集 ⊇ BLUEPRINT_MODULES 值集。"""
        from utils.permissions import BLUEPRINT_MODULES
        path = os.path.join(_ROOT, 'static', 'js', 'pages', 'role_mgmt_list.js')
        src = open(path, encoding='utf-8').read()
        match = re.search(r'var permissionModules = \{([\s\S]*?)\};', src)
        assert match, 'role_mgmt_list.js 必须声明 permissionModules'
        keys = set(re.findall(r"'([^']+)'\s*:", match.group(1)))
        missing = set(BLUEPRINT_MODULES.values()) - keys
        assert not missing, '权限面板缺少模块键：%s' % sorted(missing)
        assert "'civilization': '文明实践'" in src, '面板中文名缺「文明实践」'

    def test_module_key_synced_three_places(self):
        """模块键三处同步：m031 预置 / roster_facade 白名单 / dynatable MODULE_KEYS。"""
        from routes.dynatable import MODULE_KEYS as DYNATABLE_KEYS
        from utils.roster_facade import MODULE_KEYS as FACADE_KEYS
        assert 'civilization' in FACADE_KEYS
        assert 'civilization' in DYNATABLE_KEYS

    def test_role_with_civilization_view_granted(self, app):
        """红→绿：授予 civilization.view 的非超管可实际访问门面页/名册/指标代理。"""
        uid = _make_role_user(app, 'p089_clerk', {'civilization.view': True})
        client = app.test_client()
        _login(client, uid, 'p089_clerk')
        assert client.get('/civilization/').status_code == 200
        assert client.get('/civilization/roster/%s/' % CODE).status_code == 200
        stats = client.get('/civilization/api/stats')
        assert stats.status_code == 200 and stats.get_json()['success']

    def test_role_without_civilization_forbidden(self, app):
        """无 civilization 位 → 403（页面/名册/API 三层默认拒绝；未登录 403 反例由登录墙 302 兜底）。"""
        uid = _make_role_user(app, 'p089_none', {})
        client = app.test_client()
        _login(client, uid, 'p089_none')
        assert client.get('/civilization/').status_code == 403
        assert client.get('/civilization/roster/%s/' % CODE).status_code == 403
        assert client.get('/civilization/roster/%s/api/list' % CODE).status_code == 403

    def test_dynatable_only_cannot_borrow(self, app):
        """dynatable 权限不可借道业务名册（ADR-027，088/099 R-04 同款）。"""
        uid = _make_role_user(app, 'p089_dyn_only', {'dynatable.view': True})
        client = app.test_client()
        _login(client, uid, 'p089_dyn_only')
        assert client.get('/civilization/roster/%s/' % CODE).status_code == 403

    def test_stats_query_permission_gate(self, app, _clean_agg_cache):
        """统一入口按指标 permission 二次校验：无 civilization.view → 403。"""
        uid = _make_role_user(app, 'p089_unified', {})
        client = app.test_client()
        _login(client, uid, 'p089_unified')
        resp = client.get('/api/stats/query?metric_key=civilization_sessions')
        assert resp.status_code == 403, resp.get_data(as_text=True)

    def test_convention_action_bits_enforced(self, app):
        """公约写操作走动作位：仅 view 位的新增/编辑/删除全部 403。"""
        uid = _make_role_user(app, 'p089_viewonly', {'civilization.view': True})
        client = app.test_client()
        _login(client, uid, 'p089_viewonly')
        assert client.post('/civilization/api/conventions',
                           json={'title': '越权版本'}).status_code == 403


# ------------------------------------------------------------------ G. 深链与 404

class TestDeepLinks:
    """验收 G：未知/跨模块 code 404；引导页下发 civilization 端点不夹带其他模块。"""

    def test_unknown_code_404(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        assert client.get('/civilization/roster/no_such_code/').status_code == 404

    def test_cross_module_code_404(self, app):
        """pension 模块的台账对 civilization 门面按不存在处理（门面 module_key 归属校验）。"""
        with app.app_context():
            from models import DynamicTable, db
            db.session.add(DynamicTable(code='x_pension_only', name='养老台账',
                                        module_key='pension', group_name='社会保障'))
            db.session.commit()
        client = app.test_client()
        _login(client, _admin_id(app))
        assert client.get('/civilization/roster/x_pension_only/').status_code == 404

    def test_bootstrap_page_shows_upload_entry(self, app):
        """未建册：门面 roster 页 200 引导页（含上传入口与 civilization 门面端点）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        resp = client.get('/civilization/roster/%s/' % CODE)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        html = resp.get_data(as_text=True)
        assert '尚未建立' in html
        assert '/civilization/roster/%s/api/upload' % CODE in html, '引导页下发 civilization 上传端点'
        assert '/shop/roster/%s/' % CODE not in html, 'civilization 门面不得夹带 shop 端点'
        assert '/pension/roster/%s/' % CODE not in html, 'civilization 门面不得夹带 pension 端点'


# ------------------------------------------------------------------ H. 附件四端点（092 收口）

class TestAttachments:
    """092 module_attachment 接入：上传/列表/下载/删除 + 跨模块下载 403。"""

    def test_upload_list_download_delete(self, app):
        client = app.test_client()
        _login(client, _admin_id(app))
        png = io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
        up = client.post('/civilization/api/attachments/upload',
                         data={'file': (png, 'activity.png')},
                         content_type='multipart/form-data')
        assert up.status_code == 200 and up.get_json()['success'], up.get_data(as_text=True)
        att_id = up.get_json()['data']['id']

        listed = client.get('/civilization/api/attachments').get_json()
        assert listed['success'] and len(listed['data']['data']) == 1
        assert listed['data']['data'][0]['original_name'] == 'activity.png'

        downloaded = client.get('/civilization/api/attachments/%d/download' % att_id)
        assert downloaded.status_code == 200
        assert downloaded.data.startswith(b'\x89PNG')

        deleted = client.post('/civilization/api/attachments/%d/delete' % att_id)
        assert deleted.status_code == 200 and deleted.get_json()['success']
        assert client.get('/civilization/api/attachments').get_json()['data']['data'] == []

    def test_cross_module_download_forbidden(self, app):
        """092 防线：civilization 门面不得下载其他模块的附件（PermissionError → 403）。"""
        client = app.test_client()
        _login(client, _admin_id(app))
        with app.app_context():
            from models import ModuleAttachment, db
            db.session.add(ModuleAttachment(module_key='disabled', target_type='module',
                                            original_name='other.pdf', stored_name='x.pdf',
                                            ext='pdf', size=1, mime='application/pdf'))
            db.session.commit()
            other_id = ModuleAttachment.query.filter_by(module_key='disabled').first().id
        assert other_id
        resp = client.get('/civilization/api/attachments/%d/download' % other_id)
        assert resp.status_code == 403, '跨模块附件下载必须 403：%s' % resp.status_code
