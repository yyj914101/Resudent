# -*- coding: utf-8 -*-
"""T14-3 发放登记+冲正 / T14-4 个人总览 单测（Sprint3 第二批）。

覆盖（对应 PRD R4-E2/E3/E4/E6 + E17 冲正 + Q1 唯一数据源 + 详细设计 §8 record/reverse/overview 端点）：
- TestRecordRegistration：单笔登记（防误发校验链 + 拦截日志 + 流出放行）
- TestReverse：冲正（反向负额 + source_hash 幂等防重）
- TestOverview：个人总览（明细 + 累计总额 + 最近期间 + 筛选）
"""
import io
import os
import sys
import json

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


def _make_resident(app, name, birth_date, id_card=None, status='normal', flow_status='常住'):
    from models import db, Resident
    from utils.crypto import key_holder, sha256_hash, sha256_prefix_hash
    _unlock_keyholder()
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status=status, flow_status=flow_status)
        if id_card:
            r.id_card = None
            r.id_card_enc = key_holder.encrypt(id_card)
            r.id_card_hash = sha256_hash(id_card)
            r.id_card_prefix_hash = sha256_prefix_hash(id_card, 6)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_project(app, **kwargs):
    from models import db, SubsidyProject
    data = {'name': '高龄津贴', 'project_type': '高龄津贴', 'cycle': '月',
            'default_amount': 100, 'status': '启用'}
    data.update(kwargs)
    with app.app_context():
        p = SubsidyProject(**data)
        db.session.add(p)
        db.session.commit()
        return p.id


def _get(app, model_cls, pk):
    with app.app_context():
        return model_cls.query.get(pk)


def _years_ago(n):
    from datetime import date
    return date.today().replace(year=date.today().year - n)


# ============ 发放登记（T14-3） ============
class TestRecordRegistration:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _client(self, app):
        return _admin_client(app)

    def _add(self, client, resident_id, project_id, **overrides):
        payload = {
            'resident_id': str(resident_id),
            'project_id': str(project_id),
            'period': '2026-08',
            'issue_date': '2026-08-19',
        }
        payload.update(overrides)
        return client.post('/subsidy/api/record/add', data=payload)

    def test_register_success(self, app):
        """高龄津贴 eligible 居民 → 登记成功，record 落库 source=normal。"""
        from models import SubsidyRecord
        rid = _make_resident(app, '高龄甲', _years_ago(81), '110101194501011234')
        pid = _make_project(app)
        r = self._add(self._client(app), rid, pid)
        j = r.get_json()
        assert j['success'], j
        assert j['data']['flow_out'] is False
        with app.app_context():
            rec = SubsidyRecord.query.filter_by(resident_id=rid, project_id=pid).first()
            assert rec is not None
            assert rec.source == 'normal'
            assert rec.status == '正常'
            assert float(rec.amount) == 100.0  # 默认金额

    def test_register_amount_override(self, app):
        """显式金额 → 使用传入值。"""
        from models import SubsidyRecord
        rid = _make_resident(app, '高龄乙', _years_ago(81), '110101194501011235')
        pid = _make_project(app)
        r = self._add(self._client(app), rid, pid, amount='88.50')
        assert r.get_json()['success'], r.get_json()
        with app.app_context():
            rec = SubsidyRecord.query.filter_by(resident_id=rid).first()
            assert float(rec.amount) == 88.50

    def test_register_deceased_blocked(self, app):
        """死亡居民登记 → 拦截 + blocked_subsidy 日志（E3）。"""
        from models import db, SubsidyRecord, OperationLog
        rid = _make_resident(app, '死亡丙', _years_ago(81), '110101194501011236', status='deceased')
        pid = _make_project(app)
        r = self._add(self._client(app), rid, pid)
        j = r.get_json()
        assert j['success'] is False
        assert j['data']['blocked'] is True
        assert any('已死亡' in x for x in j['data']['reasons'])
        with app.app_context():
            assert SubsidyRecord.query.filter_by(resident_id=rid).count() == 0
            log = OperationLog.query.filter_by(action='blocked_subsidy').first()
            assert log is not None
            assert '已死亡' in log.detail

    def test_register_disabled_project_blocked(self, app):
        """停用项目登记 → 拦截（E4）。"""
        from models import SubsidyRecord
        rid = _make_resident(app, '停用丁', _years_ago(81), '110101194501011237')
        pid = _make_project(app, status='停用')
        r = self._add(self._client(app), rid, pid)
        j = r.get_json()
        assert j['success'] is False
        assert any('停用' in x for x in j['data']['reasons'])
        with app.app_context():
            assert SubsidyRecord.query.filter_by(resident_id=rid).count() == 0

    def test_register_flow_out_allowed(self, app):
        """流出居民登记 → 放行 + flow_out=True（E6）。"""
        from models import SubsidyRecord
        rid = _make_resident(app, '流出戊', _years_ago(81), '110101194501011238', flow_status='流出')
        pid = _make_project(app)
        r = self._add(self._client(app), rid, pid)
        j = r.get_json()
        assert j['success'] is True, j
        assert j['data']['flow_out'] is True
        assert '已流出' in j['data']['flow_hint']
        with app.app_context():
            assert SubsidyRecord.query.filter_by(resident_id=rid).count() == 1

    def test_register_age_not_match_blocked(self, app):
        """年龄不匹配 → 拦截。"""
        rid = _make_resident(app, '低龄己', _years_ago(70), '110101195601011239')
        pid = _make_project(app)  # 高龄津贴阈值 ≥80
        r = self._add(self._client(app), rid, pid)
        assert r.get_json()['success'] is False

    def test_register_bad_period(self, app):
        """期间格式非法 → 拒绝。"""
        rid = _make_resident(app, '期间庚', _years_ago(81), '110101194501011240')
        pid = _make_project(app)
        r = self._add(self._client(app), rid, pid, period='202608')
        assert r.get_json()['success'] is False
        assert '期间' in r.get_json()['msg']

    def test_register_bad_amount(self, app):
        """金额非法（负数/超精度）→ 拒绝。"""
        rid = _make_resident(app, '金额辛', _years_ago(81), '110101194501011241')
        pid = _make_project(app)
        assert self._add(self._client(app), rid, pid, amount='-1').get_json()['success'] is False
        assert self._add(self._client(app), rid, pid, amount='1.999').get_json()['success'] is False

    def test_register_missing_resident(self, app):
        """居民不存在 → 拦截。"""
        pid = _make_project(app)
        r = self._add(self._client(app), 9999, pid)
        assert r.get_json()['success'] is False
        assert '居民不存在' in r.get_json()['msg']

    def test_register_missing_project(self, app):
        """项目不存在 → 拦截。"""
        rid = _make_resident(app, '无项目壬', _years_ago(81), '110101194501011242')
        r = self._add(self._client(app), rid, 9999)
        assert r.get_json()['success'] is False
        assert '项目不存在' in r.get_json()['msg']

    def test_register_permission_403(self, app):
        """非 admin 无 subsidy.add → 403。"""
        from models import db, Role, User
        with app.app_context():
            role = Role(name='无补贴权限T143', permissions=json.dumps({'resident.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='普通用户T143', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        rid = _make_resident(app, '权限癸', _years_ago(81), '110101194501011243')
        pid = _make_project(app)
        r = self._add(client, rid, pid)
        assert r.status_code == 403


# ============ 冲正（T14-3） ============
class TestReverse:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _client(self, app):
        return _admin_client(app)

    def _register(self, app, client):
        rid = _make_resident(app, '冲正甲', _years_ago(81), '110101194501011244')
        pid = _make_project(app)
        r = client.post('/subsidy/api/record/add', data={
            'resident_id': str(rid), 'project_id': str(pid),
            'period': '2026-08', 'issue_date': '2026-08-19',
        })
        assert r.get_json()['success']
        with app.app_context():
            from models import SubsidyRecord
            rec = SubsidyRecord.query.filter_by(resident_id=rid).first()
            return rec.id, rid, pid

    def test_reverse_success(self, app):
        """冲正成功 → 新增反向负额记录 source=reverse。"""
        from models import SubsidyRecord
        client = self._client(app)
        rec_id, rid, pid = self._register(app, client)
        r = client.post('/subsidy/api/record/reverse-save', data={'id': str(rec_id)})
        j = r.get_json()
        assert j['success'], j
        with app.app_context():
            orig = SubsidyRecord.query.get(rec_id)
            reverse = SubsidyRecord.query.filter_by(source='reverse').first()
            assert orig is not None  # 原记录不改
            assert reverse is not None
            assert float(reverse.amount) == -100.0
            assert reverse.resident_id == rid
            assert reverse.source_hash is not None

    def test_reverse_idempotent(self, app):
        """重复冲正 → source_hash 幂等拒绝（R5）。"""
        client = self._client(app)
        rec_id, rid, pid = self._register(app, client)
        assert client.post('/subsidy/api/record/reverse-save', data={'id': str(rec_id)}).get_json()['success'] is True
        r2 = client.post('/subsidy/api/record/reverse-save', data={'id': str(rec_id)})
        j2 = r2.get_json()
        assert j2['success'] is False
        assert '已冲正' in j2['msg']

    def test_reverse_missing(self, app):
        """原记录不存在 → 拒绝。"""
        r = self._client(app).post('/subsidy/api/record/reverse-save', data={'id': '9999'})
        assert r.get_json()['success'] is False
        assert '不存在' in r.get_json()['msg']

    def test_reverse_net_zero(self, app):
        """冲正后累计总额归零（正负抵消）。"""
        from models import SubsidyRecord
        client = self._client(app)
        rec_id, rid, pid = self._register(app, client)
        client.post('/subsidy/api/record/reverse-save', data={'id': str(rec_id)})
        r = client.get(f'/subsidy/api/resident/{rid}/overview')
        j = r.get_json()
        assert j['success']
        assert j['data']['total_amount'] == 0.0
        assert j['data']['count'] == 2  # 1 正向 + 1 反向


# ============ 个人总览（T14-4） ============
class TestOverview:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _client(self, app):
        return _admin_client(app)

    def _register_many(self, app, client, rid, pid, count=3, base_amount=100):
        for i in range(count):
            r = client.post('/subsidy/api/record/add', data={
                'resident_id': str(rid), 'project_id': str(pid),
                'period': f'2026-0{8 - i}', 'issue_date': f'2026-08-{10 + i}',
                'amount': str(base_amount + i * 10),
            })
            assert r.get_json()['success']

    def test_overview_three_records(self, app):
        """3 笔登记 → 3 明细 + 累计总额 + 最近发放期间（E2 验收）。"""
        client = self._client(app)
        rid = _make_resident(app, '总览甲', _years_ago(81), '110101194501011245')
        pid = _make_project(app)
        self._register_many(app, client, rid, pid, count=3)
        r = client.get(f'/subsidy/api/resident/{rid}/overview')
        j = r.get_json()
        assert j['success']
        d = j['data']
        assert d['count'] == 3
        assert len(d['detail']) == 3
        assert d['total_amount'] == 330.0  # 100 + 110 + 120
        assert d['last_period'] == '2026-08'  # 最近期间
        assert d['resident']['name'] == '总览甲'
        # 明细含项目名
        assert all(x['project_name'] == '高龄津贴' for x in d['detail'])

    def test_overview_legacy_included(self, app):
        """含 legacy 存量 → 明细含 legacy（Q1）。"""
        from models import db, SubsidyRecord
        client = self._client(app)
        rid = _make_resident(app, '存量乙', _years_ago(81), '110101194501011246')
        pid = _make_project(app)
        with app.app_context():
            db.session.add(SubsidyRecord(resident_id=rid, project_id=pid,
                                         issue_date=_years_ago(0), period='2025-12',
                                         amount=200, source='legacy', status='正常'))
            db.session.commit()
        r = client.get(f'/subsidy/api/resident/{rid}/overview')
        j = r.get_json()
        assert j['success']
        assert j['data']['count'] == 1
        assert j['data']['detail'][0]['source'] == 'legacy'
        assert j['data']['total_amount'] == 200.0

    def test_overview_filter_project(self, app):
        """按 project_id 筛选 → 只返回该项目。"""
        client = self._client(app)
        rid = _make_resident(app, '筛选丙', _years_ago(81), '110101194501011247')
        pid1 = _make_project(app, name='高龄津贴')
        pid2 = _make_project(app, name='困难老人补助', project_type='困难老人补助', default_amount=50)
        self._register_many(app, client, rid, pid1, count=1)
        # 第二项目登记（困难老人补助需 CareTarget 记录，此处直接造 legacy 记录模拟）
        from models import db, SubsidyRecord
        with app.app_context():
            db.session.add(SubsidyRecord(resident_id=rid, project_id=pid2,
                                         issue_date=_years_ago(0), period='2026-07',
                                         amount=50, source='normal', status='正常'))
            db.session.commit()
        r = client.get(f'/subsidy/api/resident/{rid}/overview?project_id={pid1}')
        j = r.get_json()
        assert j['data']['count'] == 1
        assert j['data']['detail'][0]['project_name'] == '高龄津贴'

    def test_overview_filter_period(self, app):
        """按 period 筛选 → 只返回该期间。"""
        client = self._client(app)
        rid = _make_resident(app, '期间丁', _years_ago(81), '110101194501011248')
        pid = _make_project(app)
        self._register_many(app, client, rid, pid, count=3)
        r = client.get(f'/subsidy/api/resident/{rid}/overview?period=2026-08')
        j = r.get_json()
        assert j['data']['count'] == 1
        assert j['data']['detail'][0]['period'] == '2026-08'

    def test_overview_empty(self, app):
        """居民无发放记录 → 空明细 + 累计 0。"""
        client = self._client(app)
        rid = _make_resident(app, '无记录戊', _years_ago(81), '110101194501011249')
        r = client.get(f'/subsidy/api/resident/{rid}/overview')
        j = r.get_json()
        assert j['success']
        assert j['data']['count'] == 0
        assert j['data']['total_amount'] == 0.0
        assert j['data']['last_period'] == ''

    def test_overview_missing_resident(self, app):
        """居民不存在 → 拒绝。"""
        r = self._client(app).get('/subsidy/api/resident/9999/overview')
        assert r.get_json()['success'] is False

    def test_overview_permission_403(self, app):
        """无 subsidy.view 权限 → 403。"""
        from models import db, Role, User
        with app.app_context():
            role = Role(name='无补贴查看T144', permissions=json.dumps({'resident.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='普通用户T144', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        rid = _make_resident(app, '权限己', _years_ago(81), '110101194501011250')
        r = client.get(f'/subsidy/api/resident/{rid}/overview')
        assert r.status_code == 403
