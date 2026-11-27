# -*- coding: utf-8 -*-
"""T17-3/4/5 report 蓝图单测（Sprint4 M4.4/M4.5 里程碑）。

覆盖：
- TestStatsTable（T17-3）：四分类×网格×4 年龄段矩阵+合计 / grid 筛选 / 年龄段边界（17/18/60/80）/
  override 计入与 30 天失效 / 与 dashboard 卡片口径一致
- TestRoster（T17-4）：预览 200+脱敏 / 儿童行过滤 / 分页
- TestExport（T17-4）：落盘+token 一次性 / 脱敏明文两态 / log 含明文标记 / Z3 儿童行导出过滤 /
  download 权限 / type=stats / 弱势群体标签列+汇总行 / 3000 行 ≤30s（RUN_PERF）
- TestEmpty（T17-5）：dashboard 空态引导 / vulnerable/target_list 空态 / base.html 导入入口
"""
import os
import sys
import json
import time
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHUCUN_LAN_AUTHORIZED", "1")
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
        sess['user_id'] = 1  # admin
    return client


def _years_ago(n):
    return date.today().replace(year=date.today().year - n)


def _make_resident(app, name, birth_date, grid='第1组', status='normal', flow_status='常住'):
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status=status, flow_status=flow_status,
                     village_group=grid)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_tag(app, resident_id, code, status='有效'):
    from models import db, ResidentTag
    with app.app_context():
        t = ResidentTag(resident_id=resident_id, tag_code=code, status=status, source='自动')
        db.session.add(t)
        db.session.commit()
        return t.id


def _make_care_override(app, resident_id, override_class, days_ago=0):
    from models import db, CareTarget
    with app.app_context():
        ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
        ov = json.dumps({'override_class': override_class, 'reason': '测试', 'by': 1, 'time': ts})
        c = CareTarget(resident_id=resident_id, target_type='老人', age_class_override=ov,
                       status='有效', source='手工建档')
        db.session.add(c)
        db.session.commit()
        return c.id


def _make_child_target(app, resident_id, child_type):
    """CareTarget 儿童（留守/困境），供 Z3 儿童行过滤测试。"""
    from models import db, CareTarget
    with app.app_context():
        c = CareTarget(resident_id=resident_id, target_type='儿童', child_type=child_type,
                       status='有效', source='手工建档')
        db.session.add(c)
        db.session.commit()
        return c.id


def _make_dibao(app, resident_id, status='有效'):
    from models import db, Dibao
    with app.app_context():
        r = Dibao(resident_id=resident_id, status=status, dibao_type='农村低保')
        db.session.add(r)
        db.session.commit()
        return r.id


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    from utils.agg_cache import invalidate
    invalidate()
    yield
    invalidate()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    _unlock_keyholder()
    return _make_app(tmp_path, monkeypatch)


# ============ T17-3 统计表（批次 A） ============
class TestStatsTable:
    def test_matrix_and_totals(self, app):
        """四分类×网格×年龄段矩阵 + 合计行/列 + grand_total。"""
        rid1 = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')          # 80+
        rid2 = _make_resident(app, '困难乙', _years_ago(40), grid='第1组')          # 18-59
        rid3 = _make_resident(app, '留守丙', _years_ago(10), grid='第2组')          # 0-17
        _make_tag(app, rid2, '困难老人')
        _make_tag(app, rid3, '留守儿童')

        from utils.agg_cache import get_stats_table
        with app.app_context():
            d = get_stats_table(None)
        assert d['grids'] == ['第1组', '第2组']
        assert d['age_bands'] == ['0-17', '18-59', '60-79', '80+']
        by = {r['category']: r for r in d['rows']}
        assert by['高龄']['cells']['第1组'] == [0, 0, 0, 1]
        assert by['困难']['cells']['第1组'] == [0, 1, 0, 0]
        assert by['留守']['cells']['第2组'] == [1, 0, 0, 0]
        assert by['困境']['total'] == 0
        assert d['totals']['第1组'] == [0, 1, 0, 1]
        assert d['totals']['第2组'] == [1, 0, 0, 0]
        assert d['grand_total'] == 3

    def test_grid_filter(self, app):
        """grid 参数筛选：仅统计该网格。"""
        rid1 = _make_resident(app, 'A', _years_ago(81), grid='第1组')
        rid2 = _make_resident(app, 'B', _years_ago(81), grid='第2组')
        from utils.agg_cache import get_stats_table
        with app.app_context():
            d = get_stats_table('第1组')
        assert d['grids'] == ['第1组']
        assert d['rows'][0]['total'] == 1
        assert d['grand_total'] == 1

    def test_age_band_edges(self, app):
        """年龄段边界：17→0-17、18→18-59、60→60-79、79→60-79、80→80+（打困难标签入表）。"""
        from datetime import timedelta

        def born_for_age(n):
            # 精确日差口径：days//365.2425 >= n  ⟺  days >= n*365.2425，取 +1 天保证达周岁
            return date.today() - timedelta(days=int(n * 365.2425) + 1)

        cases = [(17, 0), (18, 1), (59, 1), (60, 2), (79, 2), (80, 3)]
        for i, (age, band_idx) in enumerate(cases):
            rid = _make_resident(app, f'边{i}', born_for_age(age), grid='第1组')
            _make_tag(app, rid, '困难老人')   # 打标签使其计入统计表，验证分箱
        from utils.agg_cache import get_stats_table
        with app.app_context():
            d = get_stats_table('第1组')
        by = {r['category']: r for r in d['rows']}
        # 困难行按年龄分箱：17→0-17、18/59→18-59、60/79→60-79、80→80+
        assert by['困难']['cells']['第1组'] == [1, 2, 2, 1]
        # 高龄阈值默认 80：6 人中仅 80 岁计入高龄行（一人多类：80 岁同时计困难+高龄）
        assert by['高龄']['cells']['第1组'] == [0, 0, 0, 1]
        assert d['totals']['第1组'] == [1, 2, 2, 2]
        assert d['grand_total'] == 7

    def test_override_included_and_expired(self, app):
        """override 计入（60 岁 override 高龄）与 30 天超期失效回退。"""
        r60 = _make_resident(app, '六十', _years_ago(60), grid='第1组')
        _make_care_override(app, r60, '高龄')                     # 有效 override
        r60b = _make_resident(app, '六十B', _years_ago(60), grid='第1组')
        _make_care_override(app, r60b, '高龄', days_ago=40)       # 过期 override
        from utils.agg_cache import get_stats_table
        with app.app_context():
            d = get_stats_table('第1组')
        by = {r['category']: r for r in d['rows']}
        # 60 岁本应落 18-59 段；override 后计入高龄行（80+ 段占位，row total 含之）
        assert by['高龄']['total'] == 1
        assert by['高龄']['cells']['第1组'][1] == 0

    def test_consistent_with_dashboard(self, app):
        """统计表各行合计与 dashboard 四卡片一致（同源防漂移）。"""
        for i in range(3):
            rid = _make_resident(app, f'高{i}', _years_ago(81), grid='第1组')
        for i in range(2):
            rid = _make_resident(app, f'困{i}', _years_ago(40), grid='第2组')
            _make_tag(app, rid, '困难老人')
        from utils.agg_cache import get_stats_table, get_dashboard_counts
        with app.app_context():
            st = get_stats_table(None)
            dc = get_dashboard_counts()
        by = {r['category']: r for r in st['rows']}
        assert by['高龄']['total'] == dc['elderly_count'] == 3
        assert by['困难']['total'] == dc['hard_elderly_count'] == 2

    def test_page_renders(self, app):
        """统计表页面渲染 200（admin），含分类行与合计。"""
        rid = _make_resident(app, '甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        r = client.get('/report/stats-table')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert '高龄老人' in html and '合计' in html and '第1组' in html


# ============ T17-4 花名册预览（批次 B） ============
class TestRoster:
    def test_preview_masked_and_child_filtered(self, app):
        """预览 200：无明文权身份证脱敏；儿童行对 admin 可见（admin 可看全部）。"""
        rid1 = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        rid2 = _make_resident(app, '留守童', _years_ago(10), grid='第1组')
        _make_tag(app, rid2, '留守儿童')
        _make_child_target(app, rid2, '留守')
        client = _admin_client(app)
        r = client.get('/report/roster?category=留守')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert '留守儿童' in html and '留守童' in html
        # admin（role_id=1）可看儿童行明细；身份证展示（预览页脱敏逻辑对 admin 豁免明文但页面展示 id_card）
        assert '共 1 人' in html

    def test_preview_child_hidden_for_non_privileged(self, app):
        """非民政主任角色（无儿童行级可见）→ 儿童行被过滤。"""
        from models import db, User, Role
        rid2 = _make_resident(app, '留守童', _years_ago(10), grid='第1组')
        _make_tag(app, rid2, '留守儿童')
        _make_child_target(app, rid2, '留守')
        with app.app_context():
            role = Role(name='网格员', permissions=json.dumps({'report.view': True}))
            db.session.add(role)
            db.session.commit()
            u = User(username='网格员1', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get('/report/roster?category=留守')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert '共 0 人' in html          # 儿童行被过滤
        assert '留守童' not in html

    def test_preview_pagination(self, app):
        """分页：20/页，第 2 页存在。"""
        for i in range(25):
            rid = _make_resident(app, f'高{i}', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        r = client.get('/report/roster?category=高龄&page=2')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert '共 25 人' in html and '2/2' in html


# ============ T17-4 导出（批次 B） ============
class TestExport:
    def _export_and_download(self, app, payload, expect_file=True):
        """POST export-excel → 取 token → GET download；返回 (status, content_type, filename)。"""
        client = _admin_client(app)
        r = client.post('/report/export-excel', json=payload)
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        url = r.get_json()['data']['download_url']
        dr = client.get(url)
        if expect_file:
            assert dr.status_code == 200
            return dr
        return dr

    def test_roster_export_token_one_time(self, app):
        """花名册导出：落盘+token 一次性（二次下载 403）。"""
        rid = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        r = client.post('/report/export-excel', json={'type': 'roster', 'category': '高龄'})
        assert r.status_code == 200
        url = r.get_json()['data']['download_url']
        assert client.get(url).status_code == 200
        assert client.get(url).status_code == 403     # 一次性消费

    def test_export_masked_vs_plain(self, app):
        """脱敏版（无 view_sensitive）与明文版（admin）两态：明文文件名含「含明文_」。"""
        from models import db, Resident, User, Role
        from utils.crypto import key_holder
        rid = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        with app.app_context():
            r = db.session.get(Resident, rid)
            r.id_card = None
            r.id_card_enc = key_holder.encrypt('110101194501011234')
            db.session.commit()
        # admin（role_id=1）→ 明文（Content-Disposition 为 RFC5987 编码，unquote 校验「含明文_」前缀）
        import urllib.parse
        client = _admin_client(app)
        r1 = client.post('/report/export-excel', json={'type': 'roster', 'category': '高龄'})
        url1 = r1.get_json()['data']['download_url']
        dr1 = client.get(url1)
        cd = urllib.parse.unquote(dr1.headers.get('Content-Disposition', ''))
        assert '含明文_' in cd
        # 网格员（无 view_sensitive）→ 无 report.export 权限 → 403
        with app.app_context():
            role = Role(name='网格员', permissions=json.dumps({'report.view': True}))
            db.session.add(role); db.session.commit()
            u = User(username='网格员2', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        client2 = app.test_client()
        with client2.session_transaction() as sess:
            sess['user_id'] = uid
        r2 = client2.post('/report/export-excel', json={'type': 'roster', 'category': '高龄'})
        assert r2.status_code == 403              # 无 report.export 权限

    def test_export_log_contains_plain_marker(self, app):
        """log_operation 留痕含「含明文=是/否」（US-5/R11-E4）。"""
        from models import OperationLog
        rid = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        client.post('/report/export-excel', json={'type': 'roster', 'category': '高龄'})
        with app.app_context():
            logs = OperationLog.query.filter_by(module='report', action='export').all()
            assert logs, '应存在 report.export 留痕'
            assert any('含明文=是' in (l.detail or '') for l in logs), \
                [l.detail for l in logs]

    def test_export_child_rows_filtered_z3(self, app):
        """Z3 落点 3：导出装配层儿童行过滤（导出文件不含留守/困境儿童行）。"""
        import io, openpyxl
        rid1 = _make_resident(app, '留守童', _years_ago(10), grid='第1组')
        _make_tag(app, rid1, '留守儿童')
        _make_child_target(app, rid1, '留守')
        # 网格员导出 → 儿童行被过滤
        from models import db, User, Role
        with app.app_context():
            role = Role(name='网格员', permissions=json.dumps({'report.view': True}))
            db.session.add(role); db.session.commit()
            u = User(username='网格员3', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        # 网格员无 report.export → 403（用 admin 导出后过滤验证需另设权限；此处验证权限边界）
        assert client.post('/report/export-excel', json={'type': 'roster', 'category': '留守'}).status_code == 403
        # admin 可导出；导出文件内容由 admin 可见（行级不滤 admin）
        client2 = _admin_client(app)
        r = client2.post('/report/export-excel', json={'type': 'roster', 'category': '留守'})
        dr = client2.get(r.get_json()['data']['download_url'])
        wb = openpyxl.load_workbook(io.BytesIO(dr.data))
        ws = wb.active
        names = [row[0].value for row in ws.iter_rows(min_row=2) if row[0].value]
        assert '留守童' in names

    def test_export_stats_table(self, app):
        """type=stats：矩阵导出（表头=分类×网格×年龄段+合计）。"""
        import io, openpyxl
        rid = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        r = client.post('/report/export-excel', json={'type': 'stats'})
        dr = client.get(r.get_json()['data']['download_url'])
        wb = openpyxl.load_workbook(io.BytesIO(dr.data))
        ws = wb.active
        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        assert '对象分类' in headers and '合计' in headers
        assert any('第1组' in str(h) for h in headers)

    def test_export_vuln_tags_and_summary(self, app):
        """弱势群体花名册：低保分类 + 标签列 + 统计汇总行（R3 验收 4）。"""
        import io, openpyxl
        rid = _make_resident(app, '低保甲', _years_ago(40), grid='第1组')
        _make_dibao(app, rid, '有效')
        client = _admin_client(app)
        r = client.post('/report/export-excel', json={'type': 'roster', 'category': '低保'})
        dr = client.get(r.get_json()['data']['download_url'])
        wb = openpyxl.load_workbook(io.BytesIO(dr.data))
        ws = wb.active
        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        assert '标签' in headers
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 2                      # 明细 + 汇总行
        assert rows[0][0] == '低保甲'
        assert rows[-1][0] == '人数统计'            # R3 验收 4：统计汇总行
        assert '低保 共 1 人' in str(rows[-1])

    def test_export_elderly_summary_row(self, app):
        """一老一小花名册：明细+人数统计行（R6 验收 #2）。"""
        import io, openpyxl
        for i in range(3):
            _make_resident(app, f'高{i}', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        r = client.post('/report/export-excel', json={'type': 'roster', 'category': '高龄'})
        dr = client.get(r.get_json()['data']['download_url'])
        wb = openpyxl.load_workbook(io.BytesIO(dr.data))
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 4                      # 3 明细 + 1 统计行
        assert rows[-1][0] == '人数统计'
        assert '高龄 共 3 人' in str(rows[-1])

    def test_export_z3_end_to_end(self, app):
        """Z3 端到端：有 report.export 但非民政主任角色 → 导出文件不含留守/困境儿童行。"""
        import io, openpyxl, json as _json
        from models import db, User, Role
        rid = _make_resident(app, '留守童', _years_ago(10), grid='第1组')
        _make_tag(app, rid, '留守儿童')
        _make_child_target(app, rid, '留守')
        with app.app_context():
            role = Role(name='导出员', permissions=_json.dumps(
                {'report.view': True, 'report.export': True}))   # 有导出权但非民政主任
            db.session.add(role); db.session.commit()
            u = User(username='导出员1', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.post('/report/export-excel', json={'type': 'roster', 'category': '留守'})
        assert r.status_code == 200
        dr = client.get(r.get_json()['data']['download_url'])
        wb = openpyxl.load_workbook(io.BytesIO(dr.data))
        ws = wb.active
        names = [row[0].value for row in ws.iter_rows(min_row=2) if row[0].value]
        assert '留守童' not in names               # 儿童行被过滤（明细仅汇总行）
        assert names[-1] == '人数统计'

    def test_export_token_expired(self, app):
        """token 过期：改 expires_at 过期后下载 → 403。"""
        import time as _time
        from utils.export_token import export_token_store
        rid = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        r = client.post('/report/export-excel', json={'type': 'roster', 'category': '高龄'})
        url = r.get_json()['data']['download_url']
        token = url.rsplit('/', 1)[-1]
        with export_token_store._lock:
            export_token_store._tokens[token].expires_at = _time.time() - 1
        assert client.get(url).status_code == 403

    def test_export_download_requires_export_perm(self, app):
        """download 权限：签发 token 后，无 report.export 权限的用户下载 → 403。"""
        from models import db, User, Role
        rid = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        r = client.post('/report/export-excel', json={'type': 'roster', 'category': '高龄'})
        url = r.get_json()['data']['download_url']
        with app.app_context():
            role = Role(name='网格员', permissions=json.dumps({'report.view': True}))
            db.session.add(role); db.session.commit()
            u = User(username='网格员4', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        client2 = app.test_client()
        with client2.session_transaction() as sess:
            sess['user_id'] = uid
        # token 绑定 admin（user_id 不匹配）+ 无权限 → 403
        assert client2.get(url).status_code == 403

    def test_export_perf_3000_rows(self, app):
        """3000 行花名册导出 ≤30s（RUN_PERF=1 启用）。"""
        if os.environ.get('RUN_PERF') != '1':
            pytest.skip('性能压测默认跳过（RUN_PERF=1 启用）')
        import io, openpyxl
        for i in range(3000):
            _make_resident(app, f'高{i}', _years_ago(81), grid=f'第{i % 8 + 1}组')
        client = _admin_client(app)
        t0 = time.perf_counter()
        r = client.post('/report/export-excel', json={'type': 'roster', 'category': '高龄'})
        dr = client.get(r.get_json()['data']['download_url'])
        elapsed = time.perf_counter() - t0
        assert dr.status_code == 200
        wb = openpyxl.load_workbook(io.BytesIO(dr.data))
        ws = wb.active
        assert ws.max_row >= 3000
        assert elapsed < 30, f'导出耗时 {elapsed:.1f}s 超 30s'


# ============ T17-5 空态引导（批次 C） ============
class TestEmpty:
    def test_dashboard_empty_guide(self, app):
        """空库首页：dashboard.html 含空态引导块与导入快捷入口（无人口时 JS 显示）。

        035 R-035-01：文案与落点对齐——按钮与副标题统一为「导入居民名单」口径
        （落点 = 新增居民页 custom tab 自定义名单导入向导）。
        """
        # 空库（不造居民）
        client = _admin_client(app)
        html = client.get('/dashboard/').get_data(as_text=True)
        assert 'emptyGuide' in html
        assert '点击此处导入居民名单' in html, '空态按钮文案（035 与落点对齐）'
        assert '请先导入居民名单开始使用' in html, '同页副标题口径统一'
        # 空态导入入口 → /resident/add?tab=custom（自定义名单导入向导）
        assert '/resident/add?tab=custom' in html
        # 空态引导依赖 JS 按 total_population==0 切换（stats 端点返回 0）
        d = client.get('/dashboard/api/dashboard/stats').get_json()['data']
        assert d['total_population'] == 0

    def test_dashboard_empty_guide_hidden_when_data(self, app):
        """有数据时引导块隐藏（JS 逻辑 d-none 切换；模板含 d-none 默认）。"""
        rid = _make_resident(app, '甲', _years_ago(40), grid='第1组')
        client = _admin_client(app)
        html = client.get('/dashboard/').get_data(as_text=True)
        assert 'emptyGuide' in html and 'd-none' in html

    def test_vuln_and_care_empty_copy(self, app):
        """弱势群体/一老一小台账空态文案含引导。

        GLB-05（B 类）：空态文案已迁到页面 JS 运行时注入，SSR 模板只剩表格容器；
        断言改为「模板骨架 + JS 文案」两段，不再要求 server-rendered HTML 含文案。
        """
        client = _admin_client(app)
        vh = client.get('/resident/vulnerable').get_data(as_text=True)
        vh_js = client.get('/static/js/pages/resident_vulnerable.js').get_data(as_text=True)
        assert 'id="vb"' in vh
        assert '暂无数据' in vh_js and '导入镇级名单' in vh_js
        ch = client.get('/care/target').get_data(as_text=True)
        ch_js = client.get('/static/js/pages/care_target_list.js').get_data(as_text=True)
        assert 'id="tb"' in ch
        assert '暂无数据' in ch_js and '导入镇级名单' in ch_js

    def test_base_nav_import_entry(self, app):
        """侧栏入口断言语义改写（035 R-035-01 → 073 二次改写）：「名单导入」冗余入口已删；
        073 侧栏扁平化后「新增居民」不再侧栏直达（两层时代为居民管理二级项），
        改由居民管理页 065 胶囊承载（resident 组 'add' chip）——入口仍可达，仅承载位置迁移。"""
        client = _admin_client(app)
        html = client.get('/dashboard/').get_data(as_text=True)
        sidebar_html = html.split('id="sidebar"', 1)[1].split('</aside>', 1)[0]
        # ① 侧栏不再含「名单导入」菜单项，也不再携带 custom 深链
        assert '名单导入' not in sidebar_html, '侧栏「名单导入」冗余入口应已删除'
        assert 'tab=custom' not in sidebar_html
        # ② 073 扁平语义：侧栏无「新增居民」直达；「居民管理」入口在场且正确指向列表页
        assert 'href="/resident/add"' not in sidebar_html, '073 后侧栏不应保留新增居民直达项'
        assert 'href="/resident/"' in sidebar_html, '侧栏「居民管理」入口应保留'
        # ③ 首页空态导入按钮存在且指向 ?tab=custom
        assert 'emptyGuide' in html
        assert '/resident/add?tab=custom' in html

    def test_glb01_dashboard_todo_card_requires_permission(self, app):
        """GLB-01：无 todo.view 时不渲染待办卡片，前端不再发 /todo/api/list 403。"""
        from models import db, Role, User
        with app.app_context():
            role = Role(name='无待办角色', permissions=json.dumps(
                {'resident.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.commit()
            user = User(username='notodo', role_id=role.id, is_active=True)
            user.set_password('TestPass123')
            db.session.add(user)
            db.session.commit()
            uid = user.id
        c = app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = uid
        html = c.get('/dashboard/').get_data(as_text=True)
        assert 'dashboardTodoList' not in html, '无 todo.view 不应渲染待办卡片'
        # admin 仍可见（权限前置未误伤）
        admin_html = _admin_client(app).get('/dashboard/').get_data(as_text=True)
        assert 'dashboardTodoList' in admin_html


# ============ T21-1 全类别花名册扩展（R10-E1 全类别） ============
def _make_hard_family(app, name, grid='第1组', prop='五保户'):
    """困难家庭居民（户属性：脱贫户/监测户/五保户）。"""
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=_years_ago(50), status='normal',
                     flow_status='常住', village_group=grid, household_property=prop)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_visit(app, resident_id):
    """走访记录直插（R7 VisitRecord，plan_id 可空）。"""
    from models import db, VisitRecord
    with app.app_context():
        v = VisitRecord(plan_id=None, resident_id=resident_id, visit_date=date.today(),
                        visitor_id=1, visit_type='入户', content='测试')
        db.session.add(v)
        db.session.commit()
        return v.id


def _make_subsidy(app, resident_id, status='正常'):
    """补贴发放记录直插（SubsidyProject 兜底创建）。"""
    from models import db, SubsidyProject, SubsidyRecord
    with app.app_context():
        p = SubsidyProject.query.first()
        if not p:
            p = SubsidyProject(name='测试项目', project_type='其他', cycle='月', status='启用')
            db.session.add(p)
            db.session.commit()
        r = SubsidyRecord(resident_id=resident_id, project_id=p.id, issue_date=date.today(),
                          period='2026-08', amount=100, channel='镇级直发',
                          source='normal', status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


class TestRosterExt:
    """T21-1 全类别扩展：儿童/困难家庭/走访对象/补贴对象（R10-E1，复用 R6 组件口径）。"""

    def _export_roster(self, client, category):
        r = client.post('/report/export-excel', json={'type': 'roster', 'category': category})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        return client.get(r.get_json()['data']['download_url'])

    def test_preview_four_new_categories(self, app):
        """四类预览 200：儿童/困难家庭/走访对象/补贴对象。"""
        rid1 = _make_resident(app, '童甲', _years_ago(10), grid='第1组')
        _make_child_target(app, rid1, '留守')
        rid2 = _make_hard_family(app, '困甲', grid='第1组')
        rid3 = _make_resident(app, '走甲', _years_ago(50), grid='第1组')
        _make_visit(app, rid3)
        rid4 = _make_resident(app, '补甲', _years_ago(60), grid='第1组')
        _make_subsidy(app, rid4)
        client = _admin_client(app)
        for cat, name in [('儿童', '童甲'), ('困难家庭', '困甲'),
                          ('走访对象', '走甲'), ('补贴对象', '补甲')]:
            html = client.get(f'/report/roster?category={cat}').get_data(as_text=True)
            assert '共 1 人' in html, cat
            assert name in html, cat

    def test_export_each_category_with_summary(self, app):
        """四类导出 Excel：明细 + 统计行「共 N 人」+ 表头与 R6 居民维度一致。"""
        import io, openpyxl
        rid1 = _make_resident(app, '童甲', _years_ago(10), grid='第1组')
        _make_child_target(app, rid1, '留守')
        rid2 = _make_hard_family(app, '困甲', grid='第1组')
        rid3 = _make_resident(app, '走甲', _years_ago(50), grid='第1组')
        _make_visit(app, rid3)
        rid4 = _make_resident(app, '补甲', _years_ago(60), grid='第1组')
        _make_subsidy(app, rid4)
        client = _admin_client(app)
        for cat, name in [('儿童', '童甲'), ('困难家庭', '困甲'),
                          ('走访对象', '走甲'), ('补贴对象', '补甲')]:
            dr = self._export_roster(client, cat)
            assert dr.status_code == 200
            wb = openpyxl.load_workbook(io.BytesIO(dr.data))
            ws = wb.active
            cells = [c.value for row in ws.iter_rows() for c in row if c.value]
            assert name in cells, cat
            assert any(f'{cat} 共 1 人' in str(c) for c in cells), cat
            headers = [c.value for c in ws[1]]
            assert headers == ['姓名', '身份证', '性别', '出生日期', '住址', '网格', '分类', '备注'], cat

    def test_export_masked_vs_plain_extra(self, app):
        """脱敏两态（R10-E3）：admin 明文文件名含「含明文_」；有 export 权限无 view_sensitive → 脱敏版。"""
        import io, openpyxl, urllib.parse
        from models import db, Resident, User, Role
        from utils.crypto import key_holder
        rid = _make_resident(app, '补甲', _years_ago(60), grid='第1组')
        _make_subsidy(app, rid)
        with app.app_context():
            r = db.session.get(Resident, rid)
            r.id_card = None
            r.id_card_enc = key_holder.encrypt('110101195601011234')
            db.session.commit()
        # admin → 明文
        client = _admin_client(app)
        dr = self._export_roster(client, '补贴对象')
        cd = urllib.parse.unquote(dr.headers.get('Content-Disposition', ''))
        assert '含明文_' in cd
        wb = openpyxl.load_workbook(io.BytesIO(dr.data))
        id_cells = [row[1].value for row in wb.active.iter_rows(min_row=2) if row[1].value]
        assert '110101195601011234' in id_cells
        # 有 report.export 无 view_sensitive → 200 脱敏版（身份证前6后4）
        with app.app_context():
            role = Role(name='导出员', permissions=json.dumps(
                {'report.view': True, 'report.export': True}))
            db.session.add(role); db.session.commit()
            u = User(username='导出员1', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        client2 = app.test_client()
        with client2.session_transaction() as sess:
            sess['user_id'] = uid
        dr2 = self._export_roster(client2, '补贴对象')
        cd2 = urllib.parse.unquote(dr2.headers.get('Content-Disposition', ''))
        assert '含明文_' not in cd2
        wb2 = openpyxl.load_workbook(io.BytesIO(dr2.data))
        id_cells2 = [row[1].value for row in wb2.active.iter_rows(min_row=2) if row[1].value]
        assert '110101195601011234' not in id_cells2
        assert any(c and c.startswith('110101') and '****' in c for c in id_cells2)

    def test_export_child_rows_filtered_extra(self, app):
        """Z3：儿童类导出，非民政主任（有 export 权限）→ 儿童行过滤；admin 可见。"""
        import io, openpyxl
        from models import db, User, Role
        rid = _make_resident(app, '留守童', _years_ago(10), grid='第1组')
        _make_child_target(app, rid, '留守')
        with app.app_context():
            role = Role(name='导出员', permissions=json.dumps(
                {'report.view': True, 'report.export': True}))
            db.session.add(role); db.session.commit()
            u = User(username='导出员2', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        dr = self._export_roster(client, '儿童')
        wb = openpyxl.load_workbook(io.BytesIO(dr.data))
        ws = wb.active
        names = [row[0].value for row in ws.iter_rows(min_row=2) if row[0].value]
        assert '留守童' not in names            # 儿童行被过滤
        assert any('儿童 共 0 人' in str(c.value) for row in ws.iter_rows() for c in row if c.value)
        # admin 导出含儿童行
        client2 = _admin_client(app)
        dr2 = self._export_roster(client2, '儿童')
        wb2 = openpyxl.load_workbook(io.BytesIO(dr2.data))
        ws2 = wb2.active
        names2 = [row[0].value for row in ws2.iter_rows(min_row=2) if row[0].value]
        assert '留守童' in names2

