# -*- coding: utf-8 -*-
"""Sprint5 T18-1 走访计划 CRUD + 待访清单生成 API 单测。

覆盖（对应 Sprint5 §3 WBS T18-1 + §4.1 验收 + PRD R7-E2 L554 / R1-E4 L281）：
- TestVisitPlanCrud：计划 CRUD + 字段校验 + 有记录禁删 + 权限（routes/visit.py）
- TestCandidateGeneration：待访清单口径——标签命中/死亡排除/流出排除/
  空 scope 默认全标签/按区域/按对象类型/儿童行级过滤/target_count 回写
"""
import os
import sys
import json
from datetime import date

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
        sess['user_id'] = 1  # admin（role_id=1 全通，儿童行可见）
    return client


def _years_ago(n, day=15):
    today = date.today()
    return date(today.year - n, today.month, min(day, 28))


def _make_resident(app, name, birth_date, status='normal', flow_status='常住', village_group='思榜', grid=''):
    """建一个居民（无密文敏感字段，清单用例不需要）。"""
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status=status,
                     flow_status=flow_status, village_group=village_group, grid=grid)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_tag(app, rid, tag_code, status='有效'):
    """给居民打政策性标签（ResidentTag，T11 口径）。"""
    from models import db, ResidentTag
    with app.app_context():
        t = ResidentTag(resident_id=rid, tag_code=tag_code, status=status, source='测试')
        db.session.add(t)
        db.session.commit()
        return t.id


def _make_record(app, plan_id, resident_id):
    """造一条走访记录（用于禁删用例）。"""
    from models import db, VisitRecord
    with app.app_context():
        r = VisitRecord(plan_id=plan_id, resident_id=resident_id, visit_date=date.today(),
                        visitor_id=1, visit_type='入户', content='测试')
        db.session.add(r)
        db.session.commit()
        return r.id


def _create_plan(client, **overrides):
    """默认：按标签（高龄老人）。scope_params 传 JSON 字符串。"""
    payload = {
        'plan_name': '季度走访计划',
        'plan_type': '按标签',
        'status': '草稿',
        'scope_params': json.dumps({'tags': ['高龄老人']}, ensure_ascii=False),
    }
    payload.update(overrides)
    return client.post('/visit/api/save', data=payload)


def _plan_id(app):
    from models import VisitPlan
    with app.app_context():
        return VisitPlan.query.first().id


class TestVisitPlanCrud:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_create_plan(self, app):
        """创建计划 → 成功，列表可见。"""
        client = _admin_client(app)
        j = _create_plan(client).get_json()
        assert j['success'], j
        j2 = client.get('/visit/api/list').get_json()
        assert j2['success']
        assert j2['total'] == 1
        assert j2['data'][0]['plan_name'] == '季度走访计划'
        assert j2['data'][0]['plan_type'] == '按标签'

    def test_create_missing_name(self, app):
        r = _create_plan(_admin_client(app), plan_name='')
        assert r.get_json()['success'] is False
        assert '不能为空' in r.get_json()['msg']

    def test_create_name_too_long(self, app):
        r = _create_plan(_admin_client(app), plan_name='长' * 101)
        assert r.get_json()['success'] is False

    def test_create_bad_plan_type(self, app):
        r = _create_plan(_admin_client(app), plan_type='非法类型')
        assert r.get_json()['success'] is False
        assert '计划类型' in r.get_json()['msg']

    def test_create_bad_status(self, app):
        r = _create_plan(_admin_client(app), status='非法状态')
        assert r.get_json()['success'] is False
        assert '状态' in r.get_json()['msg']

    def test_create_bad_scope_json(self, app):
        r = _create_plan(_admin_client(app), scope_params='not-json{{{')
        assert r.get_json()['success'] is False

    def test_create_region_missing_values(self, app):
        r = _create_plan(_admin_client(app), plan_type='按区域',
                         scope_params=json.dumps({'area_field': 'village_group', 'values': []}))
        assert r.get_json()['success'] is False
        assert '区域' in r.get_json()['msg']

    def test_create_bad_objtype(self, app):
        r = _create_plan(_admin_client(app), plan_type='按对象类型',
                         scope_params=json.dumps({'types': ['外星人']}))
        assert r.get_json()['success'] is False
        assert '对象类型' in r.get_json()['msg']

    def test_create_tag_not_in_policy(self, app):
        r = _create_plan(_admin_client(app), plan_type='按标签',
                         scope_params=json.dumps({'tags': ['非政策性标签']}))
        assert r.get_json()['success'] is False
        assert '标签' in r.get_json()['msg']

    def test_update_plan(self, app):
        client = _admin_client(app)
        assert _create_plan(client).get_json()['success'] is True
        pid = _plan_id(app)
        r = _create_plan(client, id=str(pid), plan_name='调整后计划', status='进行中')
        assert r.get_json()['success'] is True, r.get_json()
        row = client.get('/visit/api/list').get_json()['data'][0]
        assert row['plan_name'] == '调整后计划'
        assert row['status'] == '进行中'

    def test_update_missing_id(self, app):
        r = _create_plan(_admin_client(app), id='9999')
        assert r.get_json()['success'] is False
        assert '不存在' in r.get_json()['msg']

    def test_delete_plan(self, app):
        client = _admin_client(app)
        assert _create_plan(client).get_json()['success'] is True
        pid = _plan_id(app)
        assert client.post(f'/visit/api/delete/{pid}').get_json()['success'] is True
        assert client.get('/visit/api/list').get_json()['total'] == 0

    def test_delete_with_records_blocked(self, app):
        client = _admin_client(app)
        assert _create_plan(client).get_json()['success'] is True
        pid = _plan_id(app)
        rid = _make_resident(app, '走访对象', _years_ago(40))
        _make_record(app, pid, rid)
        j = client.post(f'/visit/api/delete/{pid}').get_json()
        assert j['success'] is False
        assert '走访记录' in j['msg']

    def test_delete_missing(self, app):
        j = _admin_client(app).post('/visit/api/delete/9999').get_json()
        assert j['success'] is False

    def test_list_filter_and_paginate(self, app):
        client = _admin_client(app)
        _create_plan(client, plan_name='计划A', plan_type='按区域',
                     scope_params=json.dumps({'area_field': 'village_group', 'values': ['思榜']}))
        _create_plan(client, plan_name='计划B', plan_type='按对象类型',
                     scope_params=json.dumps({'types': ['老人']}))
        _create_plan(client, plan_name='计划C', plan_type='按标签',
                     scope_params=json.dumps({'tags': ['低保']}))
        j = client.get('/visit/api/list?plan_type=按标签').get_json()
        assert j['total'] == 1 and j['data'][0]['plan_name'] == '计划C'
        j2 = client.get('/visit/api/list?limit=2').get_json()
        assert j2['total'] == 3 and len(j2['data']) == 2

    def test_scope_params_roundtrip(self, app):
        """scope_params JSON 落库/读库往返一致。"""
        client = _admin_client(app)
        sp = {'tags': ['高龄老人', '低保']}
        assert _create_plan(client, scope_params=json.dumps(sp, ensure_ascii=False)).get_json()['success'] is True
        row = client.get('/visit/api/list').get_json()['data'][0]
        assert json.loads(row['scope_params']) == sp

    def test_permission_403_without_visit_add(self, app):
        """非 admin 且无 visit.add 权限 → save 403（BLUEPRINT_MODULES 接线）。"""
        from models import db, Role, User
        with app.app_context():
            role = Role(name='无走访权限T18', permissions=json.dumps({'resident.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='普通用户T18', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = _create_plan(client)
        assert r.status_code == 403

    def test_page_render(self, app):
        r = _admin_client(app).get('/visit/')
        assert r.status_code == 200


class TestCandidateGeneration:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _plan_for(self, app, plan_type='按标签', sp=None, **overrides):
        client = _admin_client(app)
        payload = {'plan_name': '清单计划', 'plan_type': plan_type, 'status': '草稿'}
        if sp is not None:
            payload['scope_params'] = json.dumps(sp, ensure_ascii=False)
        payload.update(overrides)
        j = client.post('/visit/api/save', data=payload).get_json()
        assert j['success'], j
        return client, _plan_id(app)

    def test_tag_hit(self, app):
        """高龄老人标签命中 → 入清单。"""
        rid = _make_resident(app, '张大爷', _years_ago(85))
        _make_tag(app, rid, '高龄老人')
        client, pid = self._plan_for(app, sp={'tags': ['高龄老人']})
        j = client.get(f'/visit/api/candidates?plan_id={pid}').get_json()
        assert j['success']
        ids = [r['resident_id'] for r in j['data']]
        assert rid in ids

    def test_deceased_excluded(self, app):
        """死亡居民排除（status='deceased'）。"""
        rid = _make_resident(app, '已故老人', _years_ago(88), status='deceased')
        _make_tag(app, rid, '高龄老人')
        client, pid = self._plan_for(app, sp={'tags': ['高龄老人']})
        ids = [r['resident_id'] for r in client.get(f'/visit/api/candidates?plan_id={pid}').get_json()['data']]
        assert rid not in ids

    def test_outflow_excluded(self, app):
        """流出居民排除（flow_status=流出；统一排除口径，2026-08-27 拍板）。"""
        rid = _make_resident(app, '流出老人', _years_ago(82), flow_status='流出')
        _make_tag(app, rid, '高龄老人')
        client, pid = self._plan_for(app, sp={'tags': ['高龄老人']})
        ids = [r['resident_id'] for r in client.get(f'/visit/api/candidates?plan_id={pid}').get_json()['data']]
        assert rid not in ids

    def test_tag_miss_excluded(self, app):
        """无标签居民不入清单（非应访对象）。"""
        rid = _make_resident(app, '普通村民', _years_ago(40))
        client, pid = self._plan_for(app, sp={'tags': ['高龄老人']})
        ids = [r['resident_id'] for r in client.get(f'/visit/api/candidates?plan_id={pid}').get_json()['data']]
        assert rid not in ids

    def test_invalid_tag_excluded(self, app):
        """失效标签（status=失效）不命中。"""
        rid = _make_resident(app, '失效标签居民', _years_ago(81))
        _make_tag(app, rid, '高龄老人', status='失效')
        client, pid = self._plan_for(app, sp={'tags': ['高龄老人']})
        ids = [r['resident_id'] for r in client.get(f'/visit/api/candidates?plan_id={pid}').get_json()['data']]
        assert rid not in ids

    def test_empty_scope_default_all_tags(self, app):
        """scope_params 为空 → 默认全部政策性标签（低保命中）。"""
        rid = _make_resident(app, '低保对象', _years_ago(50))
        _make_tag(app, rid, '低保')
        client, pid = self._plan_for(app, sp=None)
        ids = [r['resident_id'] for r in client.get(f'/visit/api/candidates?plan_id={pid}').get_json()['data']]
        assert rid in ids

    def test_region_filter(self, app):
        """按区域：village_group 命中/未命中。"""
        rid_hit = _make_resident(app, '思榜老人', _years_ago(83), village_group='思榜')
        rid_miss = _make_resident(app, '旧寨老人', _years_ago(84), village_group='旧寨')
        _make_tag(app, rid_hit, '高龄老人')
        _make_tag(app, rid_miss, '高龄老人')
        client, pid = self._plan_for(app, plan_type='按区域',
                                     sp={'area_field': 'village_group', 'values': ['思榜']})
        ids = [r['resident_id'] for r in client.get(f'/visit/api/candidates?plan_id={pid}').get_json()['data']]
        assert rid_hit in ids and rid_miss not in ids

    def test_objtype_child_only(self, app):
        """按对象类型 types=['儿童'] → 仅儿童标签命中。"""
        rid_child = _make_resident(app, '小留守', _years_ago(10))
        rid_elder = _make_resident(app, '困难老人', _years_ago(70))
        _make_tag(app, rid_child, '留守儿童')
        _make_tag(app, rid_elder, '困难老人')
        client, pid = self._plan_for(app, plan_type='按对象类型', sp={'types': ['儿童']})
        ids = [r['resident_id'] for r in client.get(f'/visit/api/candidates?plan_id={pid}').get_json()['data']]
        assert rid_child in ids and rid_elder not in ids

    def test_child_rows_filtered_for_plain_user(self, app):
        """儿童行级过滤：普通用户不见留守/困境儿童行，admin 可见。"""
        rid_child = _make_resident(app, '小留守', _years_ago(9))
        rid_elder = _make_resident(app, '张大爷', _years_ago(85))
        _make_tag(app, rid_child, '留守儿童')
        _make_tag(app, rid_elder, '高龄老人')
        client, pid = self._plan_for(app, sp={'tags': ['留守儿童', '高龄老人']})

        # admin（role_id=1）可见儿童行
        j_admin = client.get(f'/visit/api/candidates?plan_id={pid}').get_json()
        admin_rows = j_admin['data']
        assert any(r['resident_id'] == rid_child for r in admin_rows)

        # 普通用户（无民政主任角色）→ 儿童行被过滤
        from models import db, Role, User
        with app.app_context():
            role = Role(name='普通用户T18B', permissions=json.dumps({'visit.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='普通用户T18B', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        plain = app.test_client()
        with plain.session_transaction() as sess:
            sess['user_id'] = uid
        j_plain = plain.get(f'/visit/api/candidates?plan_id={pid}').get_json()
        plain_rows = j_plain['data']
        assert not any(r['resident_id'] == rid_child for r in plain_rows)
        assert any(r['resident_id'] == rid_elder for r in plain_rows)

    def test_candidates_requires_plan(self, app):
        j = _admin_client(app).get('/visit/api/candidates').get_json()
        assert j['success'] is False
        assert 'plan_id' in j['msg']

    def test_candidates_missing_plan(self, app):
        j = _admin_client(app).get('/visit/api/candidates?plan_id=9999').get_json()
        assert j['success'] is False
        assert '不存在' in j['msg']

    def test_target_count_backfill(self, app):
        """调用 candidates 后 plan.target_count 回写为实际候选数。"""
        rid1 = _make_resident(app, '老人甲', _years_ago(82))
        rid2 = _make_resident(app, '老人乙', _years_ago(81))
        _make_tag(app, rid1, '高龄老人')
        _make_tag(app, rid2, '高龄老人')
        client, pid = self._plan_for(app, sp={'tags': ['高龄老人']})
        j = client.get(f'/visit/api/candidates?plan_id={pid}').get_json()
        assert j['success']
        assert j['total'] == 2
        row = client.get('/visit/api/list').get_json()['data'][0]
        assert row['target_count'] == 2
