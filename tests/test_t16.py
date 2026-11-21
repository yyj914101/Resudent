# -*- coding: utf-8 -*-
"""T16-1 弱势群体台账（R3 统一入口）单测（Sprint4 第一批）。

覆盖（对应 PRD R3-E1/E3 验收 1/2/3 + 详细设计 §8 T16 + Z3 儿童行级 M23 落点）：
- TestVulnerableLedger：低保免二次录入 / 一人多类不重复计人 / 软失效过滤 /
  special_support 身份证 hash 匹配 / 儿童行级过滤（非民政主任） / 统计与列表一致
- 权限与页面：未登录 401 / 未知分类 400 / 页面渲染 200
"""
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


def _make_dibao(app, resident_id, status='有效', dibao_type='农村低保'):
    from models import db, Dibao
    with app.app_context():
        r = Dibao(resident_id=resident_id, status=status, dibao_type=dibao_type)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_disabled(app, resident_id, status='有效', disability_type='肢体'):
    from models import db, Disabled
    with app.app_context():
        r = Disabled(resident_id=resident_id, status=status, disability_type=disability_type)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_care_target(app, resident_id, target_type, child_type='', status='有效'):
    from models import db, CareTarget
    with app.app_context():
        r = CareTarget(resident_id=resident_id, target_type=target_type, child_type=child_type,
                       source='手工建档', status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_special_support(app, household_name, household_id_card, status='有效', support_mode='集中供养'):
    from models import db, SpecialSupport
    with app.app_context():
        r = SpecialSupport(household_name=household_name, household_id_card=household_id_card,
                           status=status, support_mode=support_mode)
        db.session.add(r)
        db.session.commit()
        return r.id


def _years_ago(n):
    from datetime import date
    return date.today().replace(year=date.today().year - n)


def _list_data(client, category, name=''):
    r = client.get(f'/resident/api/vulnerable/list?category={category}&name={name}&page=1&limit=50')
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()['data']


# ============ 弱势群体台账（T16-1） ============
class TestVulnerableLedger:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_dibao_auto_listed(self, app):
        """R3 验收 1：低保对象自动列出，免二次录入。"""
        rid = _make_resident(app, '低保甲', _years_ago(60), '110101196601011234')
        _make_dibao(app, rid)
        client = _admin_client(app)
        d = _list_data(client, 'dibao')
        names = [r['name'] for r in d['data']]
        assert '低保甲' in names
        assert d['stats']['dibao']['count'] == 1

    def test_multi_category_no_duplicate_total(self, app):
        """R3 验收 2：一人多类（低保+残疾）各分类计 1，去重总人数计 1。"""
        rid = _make_resident(app, '双类乙', _years_ago(50), '110101197601011234')
        _make_dibao(app, rid)
        _make_disabled(app, rid)
        client = _admin_client(app)
        d = _list_data(client, 'dibao')
        assert d['stats']['dibao']['count'] == 1
        assert d['stats']['disabled']['count'] == 1
        assert d['stats']['total']['count'] == 1  # 按人（resident）去重

    def test_multi_category_distinct_residents_total(self, app):
        """一人一类两人：总人数 2。"""
        r1 = _make_resident(app, '低保丙', _years_ago(70), '110101195601011234')
        r2 = _make_resident(app, '残疾丁', _years_ago(45), '110101198101011234')
        _make_dibao(app, r1)
        _make_disabled(app, r2)
        client = _admin_client(app)
        d = _list_data(client, 'dibao')
        assert d['stats']['dibao']['count'] == 1
        assert d['stats']['disabled']['count'] == 1
        assert d['stats']['total']['count'] == 2

    def test_soft_invalid_excluded(self, app):
        """R3-E4 软失效：status=失效 记录不出现在台账。"""
        rid1 = _make_resident(app, '有效低保戊', _years_ago(66), '110101196001011234')
        rid2 = _make_resident(app, '失效低保己', _years_ago(67), '110101195901011234')
        _make_dibao(app, rid1)
        _make_dibao(app, rid2, status='失效', dibao_type='农村低保')
        client = _admin_client(app)
        d = _list_data(client, 'dibao')
        names = [r['name'] for r in d['data']]
        assert '有效低保戊' in names
        assert '失效低保己' not in names
        assert d['stats']['dibao']['count'] == 1

    def test_special_support_hash_match(self, app):
        """special_support 无 resident_id：按 household_id_card sha256 hash 匹配居民（T09 决策 2）。"""
        id_card = '110101195501011234'
        rid = _make_resident(app, '特困庚', _years_ago(69), id_card)
        _make_special_support(app, '特困庚', id_card)
        client = _admin_client(app)
        d = _list_data(client, 'special')
        rows = d['data']
        assert len(rows) == 1
        assert rows[0]['name'] == '特困庚'
        assert rows[0]['resident_id'] == rid  # hash 匹配到居民
        assert d['stats']['special']['count'] == 1

    def test_special_support_unmatched_row(self, app):
        """special_support 未匹配到居民：仍列出（未关联），统计计入。"""
        _make_special_support(app, '无关联辛', '110101199001019999')
        client = _admin_client(app)
        d = _list_data(client, 'special')
        assert len(d['data']) == 1
        assert d['data'][0]['resident_id'] is None
        assert d['stats']['special']['count'] == 1

    def test_child_rows_guard(self, app):
        """Z3 M23 落点：非民政主任过滤留守/困境儿童行；admin 可见。"""
        from models import db, CareTarget, Role, User
        from werkzeug.security import generate_password_hash
        _unlock_keyholder()
        elder_rid = _make_resident(app, '特困老人壬', _years_ago(83), '110101194301011234')
        child_rid = _make_resident(app, '困境儿童癸', _years_ago(9), '110101201701011234')
        _make_care_target(app, elder_rid, '老人')
        _make_care_target(app, child_rid, '儿童', child_type='困境')
        # 普通操作员（非民政主任，具 resident.view 权限，无 view_sensitive）
        with app.app_context():
            perms = {'resident.view': True, 'schema_version': 3, 'special': {}}
            role = Role(name='普通操作员16', permissions=json.dumps(perms, ensure_ascii=False), is_system=False)
            db.session.add(role)
            db.session.flush()
            u = User(username='op16', password_hash=generate_password_hash('x'), role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        d = _list_data(client, 'tekun')
        names = [r['name'] for r in d['data']]
        assert '特困老人壬' in names
        assert '困境儿童癸' not in names  # 儿童行被过滤
        # admin 可见全部
        d2 = _list_data(_admin_client(app), 'tekun')
        names2 = [r['name'] for r in d2['data']]
        assert '困境儿童癸' in names2

    def test_stats_and_list_consistent(self, app):
        """统计计数与列表 total 一致（低保分类）。"""
        r1 = _make_resident(app, '统计一', _years_ago(60), '110101196601011231')
        r2 = _make_resident(app, '统计二', _years_ago(61), '110101196501011231')
        _make_dibao(app, r1)
        _make_dibao(app, r2)
        client = _admin_client(app)
        d = _list_data(client, 'dibao')
        assert d['total'] == d['stats']['dibao']['count'] == 2

    def test_name_filter(self, app):
        """姓名筛选。"""
        r1 = _make_resident(app, '筛选张三', _years_ago(60), '110101196601011232')
        r2 = _make_resident(app, '筛选李四', _years_ago(61), '110101196501011232')
        _make_dibao(app, r1)
        _make_dibao(app, r2)
        client = _admin_client(app)
        d = _list_data(client, 'dibao', name='张三')
        assert len(d['data']) == 1
        assert d['data'][0]['name'] == '筛选张三'

    def test_id_card_masked(self, app):
        """身份证默认脱敏：普通角色（无 view_sensitive）响应无明文；admin 豁免可见明文。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        id_card = '110101196601011230'
        rid = _make_resident(app, '脱敏子', _years_ago(60), id_card)
        _make_dibao(app, rid)
        # 普通操作员（resident.view 权限，无 view_sensitive）
        with app.app_context():
            perms = {'resident.view': True, 'schema_version': 3, 'special': {}}
            role = Role(name='普通操作员脱敏', permissions=json.dumps(perms, ensure_ascii=False), is_system=False)
            db.session.add(role)
            db.session.flush()
            u = User(username='opmask', password_hash=generate_password_hash('x'), role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get('/resident/api/vulnerable/list?category=dibao')
        raw = r.get_data(as_text=True)
        assert id_card not in raw  # 明文不出现
        d = r.get_json()['data']
        assert d['data'][0]['id_card']  # 脱敏值非空
        assert d['data'][0]['id_card'] != id_card
        # admin（view_sensitive 豁免）可见明文（设计行为，明文查看专用端点）
        r2 = _admin_client(app).get('/resident/api/vulnerable/list?category=dibao')
        assert id_card in r2.get_data(as_text=True)

    def test_page_renders(self, app):
        """页面渲染 200（登录后）。"""
        client = _admin_client(app)
        r = client.get('/resident/vulnerable')
        assert r.status_code == 200
        assert '弱势群体台账' in r.get_data(as_text=True)

    def test_unauthorized_401(self, app):
        """未登录访问 API → 401。"""
        client = app.test_client()
        r = client.get('/resident/api/vulnerable/list?category=dibao')
        assert r.status_code == 401

    def test_unknown_category(self, app):
        """未知分类 → success=False。"""
        client = _admin_client(app)
        r = client.get('/resident/api/vulnerable/list?category=unknown')
        j = r.get_json()
        assert j['success'] is False


# ============ T16-2 标签回填（R3-E2） ============
class TestTagBackfill:
    """标签回填：全量回填（修正口径）/幂等/软失效过滤/special_support hash/事件驱动/字典补录。"""

    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    @staticmethod
    def _tags(app, resident_id):
        from models import db, ResidentTag
        with app.app_context():
            return {(t.tag_code, t.status) for t in ResidentTag.query.filter_by(
                resident_id=resident_id).all()}

    def test_backfill_all_categories(self, app):
        """全量回填：四表 → 困难老人/留守儿童/困境儿童/特困供养/低保/残疾。"""
        from utils.tag_backfill import backfill_tags
        elder = _make_resident(app, '回填老人', _years_ago(82), '110101194401011231')
        liushou = _make_resident(app, '留守儿', _years_ago(9), '110101201701011231')
        kunjing = _make_resident(app, '困境儿', _years_ago(8), '110101201801011231')
        sp_id = '110101195501011231'
        special = _make_resident(app, '特困户', _years_ago(70), sp_id)
        d1 = _make_dibao(app, elder)
        d2 = _make_disabled(app, elder)
        _make_care_target(app, elder, '老人')
        _make_care_target(app, liushou, '儿童', child_type='留守')
        _make_care_target(app, kunjing, '儿童', child_type='困境')
        _make_special_support(app, '特困户', sp_id)
        from models import db
        with app.app_context():
            stat = backfill_tags()
            assert stat['added'] >= 6
            assert self._tags(app, elder) >= {('低保', '有效'), ('残疾', '有效'), ('困难老人', '有效')}
            assert self._tags(app, liushou) >= {('留守儿童', '有效')}
            assert self._tags(app, kunjing) >= {('困境儿童', '有效')}
            assert self._tags(app, special) >= {('特困供养', '有效')}

    def test_backfill_idempotent(self, app):
        """幂等：重跑 added=0、skipped 增加、结果不变。"""
        from utils.tag_backfill import backfill_tags
        rid = _make_resident(app, '幂等甲', _years_ago(66), '110101196001011231')
        _make_dibao(app, rid)
        from models import db
        with app.app_context():
            s1 = backfill_tags()
            assert s1['added'] >= 1
            before = self._tags(app, rid)
            s2 = backfill_tags()
            assert s2['added'] == 0
            assert s2['skipped'] >= s1['skipped']
            assert self._tags(app, rid) == before

    def test_backfill_skips_invalid(self, app):
        """软失效记录不打标（status='失效' 跳过）。"""
        from utils.tag_backfill import backfill_tags
        rid1 = _make_resident(app, '有效低保乙', _years_ago(60), '110101196601011231')
        rid2 = _make_resident(app, '失效低保丙', _years_ago(61), '110101196501011231')
        _make_dibao(app, rid1)
        _make_dibao(app, rid2, status='失效')
        from models import db
        with app.app_context():
            backfill_tags()
            assert self._tags(app, rid1) >= {('低保', '有效')}
            assert self._tags(app, rid2) == set()

    def test_special_support_hash_tag(self, app):
        """special_support 无 resident_id：hash 匹配居民 → 特困供养标签。"""
        from utils.tag_backfill import backfill_tags
        sp_id = '110101195501011233'
        rid = _make_resident(app, '特困丁', _years_ago(71), sp_id)
        _make_special_support(app, '特困丁', sp_id)
        from models import db
        with app.app_context():
            backfill_tags()
            assert self._tags(app, rid) >= {('特困供养', '有效')}

    def test_event_dibao_save(self, app):
        """事件驱动：dibao api_save → 低保标签（R3-E2）。"""
        rid = _make_resident(app, '事件低保戊', _years_ago(60), '110101196601011234')
        client = _admin_client(app)
        r = client.post('/dibao/api/save', data={
            'name': '事件低保戊', 'id_card': '110101196601011234', 'dibao_type': 'A类'})
        assert r.get_json()['success'] is True
        assert self._tags(app, rid) >= {('低保', '有效')}

    def test_event_disabled_save(self, app):
        """事件驱动：disabled api_save → 残疾标签。"""
        rid = _make_resident(app, '事件残疾己', _years_ago(40), '110101198601011234')
        client = _admin_client(app)
        r = client.post('/disabled/api/save', data={
            'name': '事件残疾己', 'id_card': '110101198601011234',
            'disability_type': '肢体', 'disability_level': '三级'})
        assert r.get_json()['success'] is True
        assert self._tags(app, rid) >= {('残疾', '有效')}

    def test_event_care_target_save(self, app):
        """事件驱动：care api_target_save → 困难老人/留守儿童/困境儿童。"""
        elder = _make_resident(app, '事件老人庚', _years_ago(83), '110101194301011234')
        child = _make_resident(app, '事件留守辛', _years_ago(9), '110101201701011234')
        client = _admin_client(app)
        r1 = client.post('/care/api/target/save', data={
            'resident_id': str(elder), 'target_type': '老人', 'elder_type': '困难', 'source': '手工建档'})
        assert r1.get_json()['success'] is True
        r2 = client.post('/care/api/target/save', data={
            'resident_id': str(child), 'target_type': '儿童', 'child_type': '留守', 'source': '手工建档'})
        assert r2.get_json()['success'] is True
        assert self._tags(app, elder) >= {('困难老人', '有效')}
        assert self._tags(app, child) >= {('留守儿童', '有效')}

    def test_event_invalidate(self, app):
        """事件驱动：care api_target_delete（软失效）→ 标签软失效（R3-E4）。"""
        rid = _make_resident(app, '事件失效壬', _years_ago(84), '110101194201011234')
        tid = _make_care_target(app, rid, '老人')
        client = _admin_client(app)
        # 先确认保存即有标签（此处直接造数据，手工补标模拟）
        from utils.tag_backfill import ensure_tag
        from models import db
        with app.app_context():
            ensure_tag(rid, '困难老人')
            db.session.commit()
        r = client.post(f'/care/api/target/delete/{tid}', data={'reason': '手工失效'})
        assert r.get_json()['success'] is True
        tags = self._tags(app, rid)
        assert ('困难老人', '有效') not in tags
        assert ('困难老人', '失效') in tags

    def test_tag_dict_seed(self, app):
        """标签字典幂等补录『特困供养』。"""
        from utils.tag_backfill import ensure_tag_dict
        from models import db, DictItem, DictCategory
        with app.app_context():
            s1 = ensure_tag_dict()
            assert s1['added'] == 1
            cat = DictCategory.query.filter_by(name='居民标签').first()
            assert cat is not None
            assert DictItem.query.filter_by(category_id=cat.id, value='特困供养').first() is not None
            s2 = ensure_tag_dict()
            assert s2['added'] == 0 and s2['skipped'] == 1

    def test_backfill_report_diff(self, app):
        """回填执行后 by_category 与标签实际一致（diff 口径）。"""
        from utils.tag_backfill import backfill_tags
        rid = _make_resident(app, '报告癸', _years_ago(70), '110101195601011231')
        _make_dibao(app, rid)
        from models import db
        with app.app_context():
            stat = backfill_tags()
            assert stat['by_category'].get('低保', 0) >= 1
            assert stat['added'] >= 1


# ============ T16-3 死亡软失效联动（R3-E4） ============
class TestDeathSoftInvalidate:
    """死亡软失效：四表 + resident_tag 联动软失效；幂等；台账排除；不物理删除。"""

    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    @staticmethod
    def _mark_deceased(app, resident_id, reason='测试死亡'):
        client = _admin_client(app)
        return client.post('/resident/api/status', data={
            'id': str(resident_id), 'status': 'deceased', 'status_reason': reason})

    @staticmethod
    def _row_status(app, model, row_id):
        from models import db
        with app.app_context():
            return model.query.get(row_id)

    @staticmethod
    def _tags(app, resident_id):
        from models import db, ResidentTag
        with app.app_context():
            return {(t.tag_code, t.status) for t in ResidentTag.query.filter_by(
                resident_id=resident_id).all()}

    def test_death_invalidates_four_tables(self, app):
        """死亡 → dibao/disabled/care_target 关联记录软失效（status=失效 + invalid_reason）。"""
        from models import db, Dibao, Disabled, CareTarget
        rid = _make_resident(app, '死亡甲', _years_ago(80), '110101194601011234')
        d1 = _make_dibao(app, rid)
        d2 = _make_disabled(app, rid)
        t1 = _make_care_target(app, rid, '老人')
        r = self._mark_deceased(app, rid, reason='因病死亡')
        assert r.get_json()['success'] is True
        assert self._row_status(app, Dibao, d1).status == '失效'
        assert self._row_status(app, Dibao, d1).invalid_reason == '因病死亡'
        assert self._row_status(app, Disabled, d2).status == '失效'
        assert self._row_status(app, CareTarget, t1).status == '失效'

    def test_death_invalidates_special_hash(self, app):
        """special_support 反向 hash 匹配 → 软失效（无 resident_id 场景）。"""
        from models import db, SpecialSupport
        sp_id = '110101195501011235'
        rid = _make_resident(app, '死亡特困乙', _years_ago(71), sp_id)
        sp = _make_special_support(app, '死亡特困乙', sp_id)
        r = self._mark_deceased(app, rid)
        assert r.get_json()['success'] is True
        assert self._row_status(app, SpecialSupport, sp).status == '失效'

    def test_death_invalidates_tags(self, app):
        """死亡 → 该居民全部有效 resident_tag 软失效。"""
        from utils.tag_backfill import ensure_tag
        from models import db
        rid = _make_resident(app, '死亡丙', _years_ago(60), '110101196601011236')
        _make_dibao(app, rid)
        _make_disabled(app, rid)
        with app.app_context():
            ensure_tag(rid, '低保'); ensure_tag(rid, '残疾'); db.session.commit()
        r = self._mark_deceased(app, rid)
        assert r.get_json()['success'] is True
        tags = self._tags(app, rid)
        assert ('低保', '有效') not in tags and ('低保', '失效') in tags
        assert ('残疾', '有效') not in tags and ('残疾', '失效') in tags

    def test_death_excluded_from_ledger(self, app):
        """死亡软失效后 T16-1 弱势台账不再列出该居民（R3-E4：不出现在统计）。"""
        rid = _make_resident(app, '死亡丁', _years_ago(60), '110101196601011237')
        _make_dibao(app, rid)
        # 死亡前：台账列出
        d0 = _list_data(_admin_client(app), 'dibao')
        assert any(r['name'] == '死亡丁' for r in d0['data'])
        # 死亡后：台账排除
        self._mark_deceased(app, rid)
        d1 = _list_data(_admin_client(app), 'dibao')
        assert not any(r['name'] == '死亡丁' for r in d1['data'])
        assert d1['stats']['dibao']['count'] == d0['stats']['dibao']['count'] - 1

    def test_death_idempotent(self, app):
        """重复置 deceased 幂等：失效计数不重复增长、invalid_reason 保留。"""
        from models import db, Dibao
        rid = _make_resident(app, '死亡戊', _years_ago(60), '110101196601011238')
        d1 = _make_dibao(app, rid)
        r1 = self._mark_deceased(app, rid, reason='首次死亡')
        assert r1.get_json()['success'] is True
        row = self._row_status(app, Dibao, d1)
        assert row.status == '失效' and row.invalid_reason == '首次死亡'
        # 二次调用：状态仍失效、invalid_reason 不被覆盖
        r2 = self._mark_deceased(app, rid, reason='二次死亡')
        assert r2.get_json()['success'] is True
        row2 = self._row_status(app, Dibao, d1)
        assert row2.status == '失效' and row2.invalid_reason == '首次死亡'

    def test_death_only_affected_resident(self, app):
        """其他居民的四表记录与标签不受影响。"""
        from models import db, Dibao
        rid1 = _make_resident(app, '死亡己', _years_ago(60), '110101196601011239')
        rid2 = _make_resident(app, '存活庚', _years_ago(60), '110101196601011240')
        d2 = _make_dibao(app, rid2)
        self._mark_deceased(app, rid1)
        row2 = self._row_status(app, Dibao, d2)
        assert row2.status == '有效'
        assert self._tags(app, rid2) == set()

    def test_record_retained_not_deleted(self, app):
        """软失效后记录仍在库中（不物理删除，invalid_reason 可查）。"""
        from models import db, Dibao
        rid = _make_resident(app, '死亡辛', _years_ago(60), '110101196601011241')
        d1 = _make_dibao(app, rid)
        self._mark_deceased(app, rid, reason='核查死亡')
        with app.app_context():
            row = Dibao.query.get(d1)
            assert row is not None  # 记录保留
            assert row.status == '失效'
            assert row.invalid_reason == '核查死亡'
