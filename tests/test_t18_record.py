# -*- coding: utf-8 -*-
"""Sprint5 T18-2 走访记录连续补录单测。

覆盖（对应 Sprint5 §3 WBS T18-2 + PRD US-4 L224 / R7-E3 / R1-E4 L281）：
- 批量补录一次保存多条、同键 upsert 幂等、部分成功+错误清单
- 死亡/非应访/库外拦截；电话放行流出（R1-E4）与入户拒绝流出
- 儿童行级权限、敏感明文拦截、visit.add 权限 403
- 照片上传（白名单/大小）、完成度（completed_count/rate）、操作留痕
"""
import io
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


def _today():
    return date.today().isoformat()


def _make_resident(app, name, birth_date, status='normal', flow_status='常住', village_group='思榜'):
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status=status,
                     flow_status=flow_status, village_group=village_group)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_tag(app, rid, tag_code, status='有效'):
    from models import db, ResidentTag
    with app.app_context():
        t = ResidentTag(resident_id=rid, tag_code=tag_code, status=status, source='测试')
        db.session.add(t)
        db.session.commit()
        return t.id


def _create_plan(client, plan_name='走访计划', plan_type='按标签', tags=('高龄老人',)):
    payload = {'plan_name': plan_name, 'plan_type': plan_type, 'status': '草稿',
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


def _row(rid, visit_type='入户', visit_date=None, content='走访正常', has_need=0, need_desc='', photos=None):
    return {'resident_id': rid, 'visit_date': visit_date or _today(), 'visit_type': visit_type,
            'content': content, 'has_need': has_need, 'need_desc': need_desc, 'photos': photos or []}


def _record_count(app, plan_id):
    from models import VisitRecord
    with app.app_context():
        return VisitRecord.query.filter(VisitRecord.plan_id == plan_id).count()


class TestBatchRecord:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _elder_residents(self, app, n=5):
        ids = [_make_resident(app, f'老人{i}', _years_ago(80 + i)) for i in range(n)]
        for rid in ids:
            _make_tag(app, rid, '高龄老人')
        return ids

    def test_batch_success_5_rows(self, app):
        """5 户一次保存完成（US-4），inserted==5，completed_count 正确。"""
        client = _admin_client(app)
        ids = self._elder_residents(app, 5)
        pid = _create_plan(client)
        j = _batch(client, pid, [_row(r) for r in ids]).get_json()
        assert j['success'], j
        d = j['data']
        assert d['total'] == 5 and d['success'] == 5 and d['inserted'] == 5
        assert d['errors'] == []
        assert d['completed_count'] == 5
        assert _record_count(app, pid) == 5

    def test_batch_upsert_idempotent(self, app):
        """重复提交同键 → updated 不新增（幂等，用户拍板）。"""
        client = _admin_client(app)
        ids = self._elder_residents(app, 2)
        pid = _create_plan(client)
        rows = [_row(ids[0], content='第一次'), _row(ids[1], content='第一次')]
        assert _batch(client, pid, rows).get_json()['data']['inserted'] == 2
        rows2 = [_row(ids[0], content='第二次'), _row(ids[1], content='第二次')]
        j2 = _batch(client, pid, rows2).get_json()['data']
        assert j2['inserted'] == 0 and j2['updated'] == 2 and j2['success'] == 2
        assert _record_count(app, pid) == 2  # 无重复插入
        # 内容已更新为第二次
        from models import VisitRecord
        with app.app_context():
            rec = VisitRecord.query.filter_by(plan_id=pid, resident_id=ids[0]).first()
            assert rec.content == '第二次'

    def test_batch_partial_errors(self, app):
        """部分成功：非法行返回行号与原因，合法行照常落库。"""
        client = _admin_client(app)
        ids = self._elder_residents(app, 3)
        outsider = _make_resident(app, '无标签村民', _years_ago(40))
        pid = _create_plan(client)
        rows = [_row(ids[0]), _row(ids[1]), _row(ids[2]), _row(outsider)]  # 第 4 行非应访
        j = _batch(client, pid, rows).get_json()['data']
        assert j['success'] == 3 and len(j['errors']) == 1
        assert j['errors'][0]['row'] == 4
        assert '不在应访对象' in j['errors'][0]['msg']

    def test_batch_deceased_blocked(self, app):
        rid = _make_resident(app, '已故老人', _years_ago(85), status='deceased')
        _make_tag(app, rid, '高龄老人')
        client = _admin_client(app)
        pid = _create_plan(client)
        j = _batch(client, pid, [_row(rid)]).get_json()['data']
        assert j['success'] == 0 and len(j['errors']) == 1
        assert '已死亡' in j['errors'][0]['msg']

    def test_batch_missing_resident(self, app):
        client = _admin_client(app)
        pid = _create_plan(client)
        j = _batch(client, pid, [_row(99999)]).get_json()['data']
        assert j['success'] == 0 and len(j['errors']) == 1
        assert '居民不存在' in j['errors'][0]['msg']

    def test_batch_phone_allows_outflow(self, app):
        """R1-E4：电话走访保留流出对象 → 成功。"""
        rid = _make_resident(app, '流出老人', _years_ago(82), flow_status='流出')
        _make_tag(app, rid, '高龄老人')
        client = _admin_client(app)
        pid = _create_plan(client)
        j = _batch(client, pid, [_row(rid, visit_type='电话')]).get_json()['data']
        assert j['success'] == 1 and j['errors'] == []

    def test_batch_household_rejects_outflow(self, app):
        """R1-E4：入户类型仍排除流出对象 → 拒绝。"""
        rid = _make_resident(app, '流出老人', _years_ago(82), flow_status='流出')
        _make_tag(app, rid, '高龄老人')
        client = _admin_client(app)
        pid = _create_plan(client)
        j = _batch(client, pid, [_row(rid, visit_type='入户')]).get_json()['data']
        assert j['success'] == 0 and len(j['errors']) == 1
        assert '不在应访对象' in j['errors'][0]['msg']

    def test_batch_child_permission(self, app):
        """儿童行级权限：普通用户补录儿童行被拒，admin 可。"""
        from models import db, Role, User
        rid = _make_resident(app, '小留守', _years_ago(9))
        _make_tag(app, rid, '留守儿童')
        client = _admin_client(app)
        pid = _create_plan(client, tags=('留守儿童',))
        # admin 成功
        j = _batch(client, pid, [_row(rid)]).get_json()['data']
        assert j['success'] == 1, j

        # 普通用户（有 visit.add 但非民政主任）→ 行级拒绝
        with app.app_context():
            role = Role(name='普通用户T182', permissions=json.dumps(
                {'visit.view': True, 'visit.add': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='普通用户T182', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        plain = app.test_client()
        with plain.session_transaction() as sess:
            sess['user_id'] = uid
        j2 = _batch(plain, pid, [_row(rid)]).get_json()['data']
        assert j2['success'] == 0 and len(j2['errors']) == 1
        assert '无权补录留守/困境儿童' in j2['errors'][0]['msg']

    def test_batch_sensitive_content_blocked(self, app):
        """content 含 18 位身份证字面明文 → 行拒绝（scan_mask 纪律）。"""
        ids = self._elder_residents(app, 1)
        client = _admin_client(app)
        pid = _create_plan(client)
        j = _batch(client, pid, [_row(ids[0], content='证件号 110101198601011251')]).get_json()['data']
        assert j['success'] == 0 and len(j['errors']) == 1
        assert '敏感明文' in j['errors'][0]['msg']

    def test_batch_403_without_visit_add(self, app):
        from models import db, Role, User
        with app.app_context():
            role = Role(name='只读用户T182', permissions=json.dumps({'visit.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='只读用户T182', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        pid = _create_plan(_admin_client(app))
        r = _batch(client, pid, [_row(1)])
        assert r.status_code == 403

    def test_photo_upload_ok(self, app):
        client = _admin_client(app)
        png = io.BytesIO(b'\x89PNG\r\n\x1a\n' + b'0' * 64)
        r = client.post('/visit/api/photo/upload',
                        data={'photos': (png, 'a.png')},
                        content_type='multipart/form-data')
        j = r.get_json()
        assert j['success'], j
        f = j['data']['files'][0]
        assert f['filename'].startswith('visit/')
        assert f['filename'].endswith('.png')
        assert f['url'] == '/uploads/visit/' + f['filename'].split('/', 1)[1]

    def test_photo_upload_bad_ext(self, app):
        client = _admin_client(app)
        r = client.post('/visit/api/photo/upload',
                        data={'photos': (io.BytesIO(b'x'), 'a.exe')},
                        content_type='multipart/form-data')
        assert r.get_json()['success'] is False

    def test_records_endpoint_completed_rate(self, app):
        client = _admin_client(app)
        ids = self._elder_residents(app, 4)
        pid = _create_plan(client)
        assert _batch(client, pid, [_row(r) for r in ids[:2]]).get_json()['data']['success'] == 2
        j = client.get(f'/visit/api/records?plan_id={pid}').get_json()
        assert j['success']
        d = j['data']
        assert d['completed_count'] == 2 and d['target_count'] == 4
        assert d['rate'] == 50.0
        assert set(d['recorded']) == set(ids[:2])

    def test_log_operation_written(self, app):
        client = _admin_client(app)
        ids = self._elder_residents(app, 1)
        pid = _create_plan(client)
        _batch(client, pid, [_row(ids[0])])
        from models import OperationLog
        with app.app_context():
            log = OperationLog.query.filter_by(module='visit').order_by(OperationLog.id.desc()).first()
            assert log is not None
            assert '批量补录' in log.summary
            assert '110101' not in (log.detail or '')  # detail 无明文

    def test_batch_photo_path_invalid(self, app):
        """photos 写侧路径白名单（T18-4）：../、绝对路径、非 visit/ 前缀 → 行拒绝。"""
        ids = self._elder_residents(app, 1)
        client = _admin_client(app)
        pid = _create_plan(client)
        for bad in (['../evil.png'], ['/etc/passwd'], ['other/x.png'], ['notjson']):
            j = _batch(client, pid, [_row(ids[0], photos=bad)]).get_json()['data']
            assert j['success'] == 0, bad
            assert '照片路径不合法' in j['errors'][0]['msg'], bad
        # 合法 visit/ 前缀 → 通过
        j2 = _batch(client, pid, [_row(ids[0], photos=['visit/abc.png'])]).get_json()['data']
        assert j2['success'] == 1 and j2['errors'] == []
