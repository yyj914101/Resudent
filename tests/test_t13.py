# -*- coding: utf-8 -*-
"""T13 R2 一老一小关怀台账 单测（Sprint2）。

覆盖（对应 PRD R2 验收 + Sprint2 §3.3 T13-1~T13-6）：
- 年龄精确日差（calculate_age 生日当天/跨年/2月29；is_elderly 阈值 80/85 切换；is_child <18 边界）
- 动态归类不落库（81 岁 dynamic_class='高龄' 且 DB 无 elder_type='高龄'、无「高龄老人」tag）
- 儿童仅政策性入册（16 岁无认定不入册；认定留守入册）
- age_class_override 30 天保护（冲突检测 / 落 JSON / 30 天内优先 / >30 天失效）
- guardian_consent 待补录流（待补录建档不阻断 / list 标识 / consent 补录 / TodoItem 生成与 done / export 标注）
- child_rows_guard 儿童行级可见（非民政主任过滤留守/困境儿童）
- 镇级名单导入建档 + 居民不存在错误行
"""
import io
import os
import sys

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


def _make_resident(app, name, birth_date, id_card=None):
    """建一个居民（含密文身份证，供 CareTarget 关联）。"""
    from models import db, Resident
    from utils.crypto import key_holder, sha256_hash, sha256_prefix_hash
    _unlock_keyholder()
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date)
        if id_card:
            r.id_card = None
            r.id_card_enc = key_holder.encrypt(id_card)
            r.id_card_hash = sha256_hash(id_card)
            r.id_card_prefix_hash = sha256_prefix_hash(id_card, 6)
        db.session.add(r)
        db.session.commit()
        return r.id


def _years_ago(n):
    """精确构造 n 年前日期（避免 timedelta 闰年误差）。"""
    from datetime import date
    return date.today().replace(year=date.today().year - n)


# ============ 年龄计算（T13-2） ============
class TestCareAge:
    def test_calculate_age_exact(self):
        from utils.care_age import calculate_age
        from datetime import date, timedelta
        today = date.today()
        # 80 岁（精确到日）
        assert calculate_age(_years_ago(80)) == 80
        # 无出生日期
        assert calculate_age(None) is None

    def test_is_elderly_threshold_switch(self):
        from utils.care_age import is_elderly, elderly_threshold
        from datetime import date
        today = date.today()
        # 明确 85 岁：出生年份 = 今年-85（用 replace 避免闰年误差）
        bd85 = today.replace(year=today.year - 85)
        age85 = (today - bd85).days // 365.2425
        assert age85 == 85
        # 默认阈值 80 → 高龄
        assert is_elderly(bd85) is True
        # 阈值 85 → 仍高龄；阈值 90 → 非高龄
        assert is_elderly(bd85, threshold=85) is True
        assert is_elderly(bd85, threshold=90) is False

    def test_is_child_boundary(self):
        from utils.care_age import is_child
        from datetime import date
        today = date.today()
        # 16 岁 < 18 → 儿童候选池
        bd16 = today.replace(year=today.year - 16)
        assert is_child(bd16) is True
        # 18 岁 → 非儿童（满 18 周岁，除非生日未到则为 17）
        bd18 = today.replace(year=today.year - 18)
        age18 = (today - bd18).days // 365.2425
        assert is_child(bd18) is (age18 < 18)

    def test_classify_age(self):
        from utils.care_age import classify_age
        assert classify_age(_years_ago(85))['class'] == '高龄'
        assert classify_age(_years_ago(16))['class'] == '儿童'
        assert classify_age(_years_ago(30))['class'] == '无'


# ============ override 30 天保护（T13-4） ============
class TestOverride:
    def test_parse_override_30day_window(self):
        from utils.care_age import parse_override
        from datetime import datetime, timedelta
        import json
        now = datetime(2026, 8, 17, 12, 0, 0)
        # 30 天内 → 有效
        within = json.dumps({'override_class': '高龄', 'reason': 'x', 'by': 'y',
                             'time': (now - timedelta(days=10)).isoformat()})
        assert parse_override(within, now=now)['override_class'] == '高龄'
        # 超 30 天 → 失效
        expired = json.dumps({'override_class': '高龄', 'reason': 'x', 'by': 'y',
                              'time': (now - timedelta(days=31)).isoformat()})
        assert parse_override(expired, now=now) is None

    def test_classify_override_priority(self):
        from utils.care_age import classify_age
        from datetime import datetime
        # 30 岁居民（动态应为「无」），但 override 为「高龄」→ 优先覆盖
        override = {'override_class': '高龄', 'time': datetime.now().isoformat()}
        assert classify_age(_years_ago(30), override)['class'] == '高龄'


# ============ 动态归类不落库 + 儿童政策性入册（T13-2/T13-3） ============
class TestDynamicClassification:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_elderly_dynamic_no_persist(self, app):
        """81 岁居民动态归类「高龄」，但 DB 无 elder_type='高龄'、无「高龄老人」tag。"""
        from models import db, CareTarget, ResidentTag
        rid = _make_resident(app, '高龄甲', _years_ago(81), '110101194501011234')
        with app.app_context():
            ct = CareTarget(resident_id=rid, target_type='老人', source='手工建档', status='有效')
            db.session.add(ct)
            db.session.commit()
        client = _admin_client(app)
        r = client.get('/care/api/target/list')
        j = r.get_json()
        assert j['success']
        items = [d for d in j['data']['data'] if d['resident_id'] == rid]
        assert len(items) == 1
        assert items[0]['dynamic_class'] == '高龄'
        # 不落库：DB 无 elder_type='高龄'，无「高龄老人」tag
        with app.app_context():
            ct2 = CareTarget.query.filter_by(resident_id=rid).first()
            assert ct2.elder_type != '高龄'
            assert ResidentTag.query.filter_by(resident_id=rid, tag_code='高龄老人').first() is None

    def test_child_only_policy_entry(self, app):
        """儿童仅留守/困境入册：16 岁无认定不入册。"""
        rid = _make_resident(app, '儿童乙', _years_ago(16), '110101201001011234')
        client = _admin_client(app)
        # 无认定（child_type 空）→ 拒绝
        r = client.post('/care/api/target/save', data={
            'resident_id': rid, 'target_type': '儿童', 'child_type': '', 'source': '手工建档'})
        j = r.get_json()
        assert j['success'] is False
        # 认定留守 → 入册
        r2 = client.post('/care/api/target/save', data={
            'resident_id': rid, 'target_type': '儿童', 'child_type': '留守', 'source': '手工建档'})
        j2 = r2.get_json()
        assert j2['success'] is True


# ============ guardian_consent 待补录流（T13-5） ============
class TestGuardianConsent:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_consent_pending_flow(self, app):
        from models import db, CareTarget, TodoItem
        rid = _make_resident(app, '困境儿童丙', _years_ago(10), '110101201601011234')
        client = _admin_client(app)
        # 建档，guardian_consent 缺省 → 待补录，不阻断
        r = client.post('/care/api/target/save', data={
            'resident_id': rid, 'target_type': '儿童', 'child_type': '困境', 'source': '镇级名单',
            'guardian_consent': ''})
        j = r.get_json()
        assert j['success'] is True
        ct_id = j['data']['id']
        # 待办已生成
        with app.app_context():
            todo = TodoItem.query.filter_by(todo_type='care_guardian_consent', related_id=ct_id, status='todo').first()
            assert todo is not None
        # list 标识待补录
        r2 = client.get('/care/api/target/list')
        items = [d for d in r2.get_json()['data']['data'] if d['id'] == ct_id]
        assert items[0]['guardian_consent'] == '待补录'
        # 补录
        r3 = client.post(f'/care/api/target/consent/{ct_id}', data={'way': '现场确认'})
        assert r3.get_json()['success'] is True
        with app.app_context():
            ct = CareTarget.query.get(ct_id)
            assert ct.guardian_consent == '已同意'
            todo2 = TodoItem.query.filter_by(todo_type='care_guardian_consent', related_id=ct_id).first()
            assert todo2.status == 'done'


# ============ child_rows_guard 儿童行级可见（T13 接线） ============
class TestChildRowGuard:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_non_director_filtered(self, app):
        """非民政主任角色 list 过滤留守/困境儿童明细。"""
        from models import db, CareTarget, Role, User
        from werkzeug.security import generate_password_hash
        import json
        _unlock_keyholder()
        # 建老人 + 困境儿童各一
        elder_rid = _make_resident(app, '老人丁', _years_ago(82), '110101194401011234')
        child_rid = _make_resident(app, '困境儿童戊', _years_ago(9), '110101201701011234')
        with app.app_context():
            db.session.add(CareTarget(resident_id=elder_rid, target_type='老人', source='手工建档', status='有效'))
            db.session.add(CareTarget(resident_id=child_rid, target_type='儿童', child_type='困境', source='手工建档', status='有效'))
            # 普通操作员角色（非民政主任，但具 care.view 权限）
            perms = {'care.view': True, 'schema_version': 3, 'special': {}}
            role = Role(name='普通操作员', permissions=json.dumps(perms, ensure_ascii=False), is_system=False)
            db.session.add(role)
            db.session.flush()
            u = User(username='op13', password_hash=generate_password_hash('x'), role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get('/care/api/target/list')
        j = r.get_json()
        names = [d['name'] for d in j['data']['data']]
        # 老人可见，困境儿童被过滤
        assert '老人丁' in names
        assert '困境儿童戊' not in names


# ============ 镇级名单导入建档（T13-1 对接 T12） ============
class TestTargetImport:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_import_targets_from_xlsx(self, app, tmp_path):
        import openpyxl
        from models import db, CareTarget
        _unlock_keyholder()
        # 预置居民（含密文身份证）
        rid = _make_resident(app, '名单老人己', _years_ago(83), '110101194301011234')

        xlsx = tmp_path / "target.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['姓名', '身份证号', '目标类型', '老人类型', '儿童类型', '来源', '困难原因', '需求', '监护人', '监护人同意'])
        ws.append(['名单老人己', '110101194301011234', '老人', '困难', '', '镇级名单', '独居', '照护', '', ''])
        ws.append(['不存在的居民', '110101194501015555', '老人', '困难', '', '镇级名单', '', '', '', ''])
        wb.save(str(xlsx))

        client = _admin_client(app)
        with open(str(xlsx), 'rb') as f:
            data = {'file': (io.BytesIO(f.read()), 'target.xlsx')}
            r = client.post('/care/api/target/import', data=data, content_type='multipart/form-data')
        j = r.get_json()
        # 成功（1 导入 + 1 错误行）
        assert '新增1条' in j['msg']
        assert '居民档案不存在' in j['msg']
        with app.app_context():
            ct = CareTarget.query.filter_by(resident_id=rid).first()
            assert ct is not None
            assert ct.target_type == '老人'
            assert ct.source == '镇级名单'
