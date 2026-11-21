# -*- coding: utf-8 -*-
"""Sprint5 T18-3 走访记录历史追溯单测。

覆盖（PRD R7-E4 L556 + Sprint5 §3 T18-3 + §12.3 覆盖率锚点）：
- 四维筛选（计划/居民/时间/走访人）+ 方式 + keyword 模糊
- 分页、行字段解析（plan/visitor 名、photos url 拼接）
- 时间线（按居民全量、日期 desc）
- 覆盖率 G2/K4（去重居民口径，全局与按计划；流出电话记录不虚增分子；分母 0 兜底）
- 儿童行级过滤、visit.view 403、/uploads/visit/ SVG 占位、页面渲染 200、GET 不留痕
"""
import os
import sys
import json
from datetime import date, timedelta

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


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def _years_ago(n, day=15):
    today = date.today()
    return date(today.year - n, today.month, min(day, 28))


def _today_iso():
    return date.today().isoformat()


def _make_resident(app, name, birth_date, status='normal', flow_status='常住', village_group='思榜'):
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status=status,
                     flow_status=flow_status, village_group=village_group)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_tag(app, rid, tag_code):
    from models import db, ResidentTag
    with app.app_context():
        t = ResidentTag(resident_id=rid, tag_code=tag_code, status='有效', source='测试')
        db.session.add(t)
        db.session.commit()
        return t.id


def _create_plan(client, plan_name='追溯计划', tags=('高龄老人',)):
    payload = {'plan_name': plan_name, 'plan_type': '按标签', 'status': '草稿',
               'scope_params': json.dumps({'tags': list(tags)}, ensure_ascii=False)}
    j = client.post('/visit/api/save', data=payload).get_json()
    assert j['success'], j
    from models import VisitPlan
    with client.application.app_context():
        return VisitPlan.query.first().id


def _batch(client, plan_id, rows):
    return client.post('/visit/api/record/batch',
                       data=json.dumps({'plan_id': plan_id, 'rows': rows}),
                       content_type='application/json')


def _row(rid, visit_type='入户', visit_date=None, content='走访正常', photos=None):
    return {'resident_id': rid, 'visit_date': visit_date or _today_iso(), 'visit_type': visit_type,
            'content': content, 'has_need': 0, 'need_desc': '', 'photos': photos or []}


class TestHistory:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _seed(self, app, client):
        """1 计划 3 高龄老人 + 1 无标签村民；2 条记录（今天入户 + 昨天电话）。"""
        ids = [_make_resident(app, f'老人{i}', _years_ago(82 + i)) for i in range(3)]
        for rid in ids:
            _make_tag(app, rid, '高龄老人')
        outsider = _make_resident(app, '普通村民', _years_ago(40))
        pid = _create_plan(client)
        r1 = _batch(client, pid, [_row(ids[0], visit_type='入户'),
                                  _row(ids[1], visit_type='电话',
                                       visit_date=(date.today() - timedelta(days=1)).isoformat())])
        assert r1.get_json()['data']['success'] == 2
        return {'plan_id': pid, 'elder': ids, 'outsider': outsider}

    def test_filter_by_plan(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get(f'/visit/api/history?plan_id={s["plan_id"]}').get_json()
        assert j['total'] == 2

    def test_filter_by_resident(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get(f'/visit/api/history?resident_id={s["elder"][0]}').get_json()
        assert j['total'] == 1
        assert j['data'][0]['resident_id'] == s['elder'][0]

    def test_filter_by_date_range(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get('/visit/api/history?start_date=' + _today_iso() + '&end_date=' + _today_iso()).get_json()
        assert j['total'] == 1  # 仅今天的入户记录

    def test_filter_by_visitor_and_type(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get(f'/visit/api/history?visitor_id=1&visit_type=电话').get_json()
        assert j['total'] == 1
        assert j['data'][0]['visit_type'] == '电话'

    def test_filter_keyword(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get('/visit/api/history?keyword=老人1').get_json()
        assert j['total'] == 1 and j['data'][0]['resident_name'] == '老人1'

    def test_pagination(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get('/visit/api/history?limit=1&page=1').get_json()
        assert j['total'] == 2 and len(j['data']) == 1 and j['pages'] == 2

    def test_row_fields_and_photos_url(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        # 补一条带照片的记录
        _batch(client, s['plan_id'], [_row(s['elder'][2], photos=['visit/a.png'])])
        j = client.get(f'/visit/api/history?resident_id={s["elder"][2]}').get_json()
        row = j['data'][0]
        assert row['plan_name'] == '追溯计划'
        assert row['visitor_name']  # admin 用户 real_name/username
        assert row['photos'][0]['name'] == 'visit/a.png'
        assert row['photos'][0]['url'] == '/uploads/visit/a.png'

    def test_timeline_all_records_desc(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get(f'/visit/api/history?resident_id={s["elder"][0]}&limit=1000').get_json()
        assert j['total'] >= 1
        dates = [r['visit_date'] for r in j['data']]
        assert dates == sorted(dates, reverse=True)

    def test_coverage_global(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get('/visit/api/history').get_json()
        stats = j['stats']
        assert stats['scope'] == 'global'
        assert stats['total'] == 3          # 3 高龄应访（流出无、死亡无）
        assert stats['visited'] == 2        # 2 个不同居民被访（电话那条也算）
        assert stats['coverage_rate'] == round(2 / 3 * 100, 1)
        assert stats['completion_rate'] == stats['coverage_rate']

    def test_coverage_outflow_phone_not_inflate(self, app):
        """流出对象电话记录不虚增全局分子（分子∩应访集合）。"""
        client = _admin_client(app)
        s = self._seed(app, client)
        outflow = _make_resident(app, '流出老人', _years_ago(83), flow_status='流出')
        _make_tag(app, outflow, '高龄老人')
        _batch(client, s['plan_id'], [_row(outflow, visit_type='电话')])
        j = client.get('/visit/api/history').get_json()
        stats = j['stats']
        assert stats['total'] == 3          # 流出被排除出应访
        assert stats['visited'] == 2        # 流出电话记录不虚增分子

    def test_coverage_by_plan(self, app):
        client = _admin_client(app)
        s = self._seed(app, client)
        j = client.get(f'/visit/api/history?plan_id={s["plan_id"]}').get_json()
        stats = j['stats']
        assert stats['scope'] == 'plan'
        assert stats['total'] == 3
        assert stats['visited'] == 2
        assert stats['coverage_rate'] == round(2 / 3 * 100, 1)

    def test_coverage_empty_db(self, app):
        client = _admin_client(app)
        j = client.get('/visit/api/history').get_json()
        stats = j['stats']
        assert stats['total'] == 0 and stats['visited'] == 0
        assert stats['coverage_rate'] == 0 and stats['completion_rate'] == 0

    def test_child_row_filtered_for_plain_user(self, app):
        from models import db, Role, User
        rid = _make_resident(app, '小留守', _years_ago(9))
        _make_tag(app, rid, '留守儿童')
        client = _admin_client(app)
        pid = _create_plan(client, tags=('留守儿童',))
        _batch(client, pid, [_row(rid)])

        j_admin = client.get(f'/visit/api/history?plan_id={pid}').get_json()
        assert any(r['resident_id'] == rid for r in j_admin['data'])

        with app.app_context():
            role = Role(name='普通用户T183', permissions=json.dumps(
                {'visit.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='普通用户T183', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        plain = app.test_client()
        with plain.session_transaction() as sess:
            sess['user_id'] = uid
        j_plain = plain.get(f'/visit/api/history?plan_id={pid}').get_json()
        assert not any(r['resident_id'] == rid for r in j_plain['data'])

    def test_403_without_visit_view(self, app):
        from models import db, Role, User
        with app.app_context():
            role = Role(name='无关用户T183', permissions=json.dumps({'resident.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='无关用户T183', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        assert client.get('/visit/history').status_code == 403
        assert client.get('/visit/api/history').status_code == 403

    def test_page_render(self, app):
        r = _admin_client(app).get('/visit/history')
        assert r.status_code == 200
        assert '走访记录'.encode() in r.data or '走访记录' in r.get_data(as_text=True)

    def test_get_no_operation_log(self, app):
        """GET 查看类不新增 visit 操作日志（对齐 api_list 先例）。"""
        client = _admin_client(app)
        self._seed(app, client)
        before = 0
        from models import OperationLog
        with app.app_context():
            before = OperationLog.query.filter_by(module='visit').count()
        client.get('/visit/api/history')
        with app.app_context():
            after = OperationLog.query.filter_by(module='visit').count()
        assert after == before
