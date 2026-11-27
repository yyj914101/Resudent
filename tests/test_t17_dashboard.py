# -*- coding: utf-8 -*-
"""T17-1 dashboard 双轨取数单测（Sprint4 M4.2 里程碑）。

覆盖（对应 PRD R6 验收 4.2#1/#3/#4 + 详细设计 §2.3 口径映射表 L225-229 + L961 agg_cache）：
- TestFourCards：四卡片 120/30/25/8 精确断言 / override 优先 / override 30 天失效回退 / 标签失效不计
- TestSubsidy：补贴总额 subsidy_record 聚合（normal+legacy、停发不计、旧表不影响）
- TestFilters：软失效过滤（dibao/disabled/special_support）/ 流出不计人口 / 待办字段剔除
- TestPrecision：SQL julianday 精确日差与 care_age.calculate_age 逐人一致（钉精度）
"""
import os
import sys
import json
import random
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


def _make_resident(app, name, birth_date, status='normal', flow_status='常住',
                   gender='男', village_group='第1组'):
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status=status, flow_status=flow_status,
                     gender=gender, village_group=village_group)
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
        ov = json.dumps({'override_class': override_class, 'reason': '测试',
                         'by': 1, 'time': ts})
        c = CareTarget(resident_id=resident_id, target_type='老人',
                       age_class_override=ov, status='有效', source='手工建档')
        db.session.add(c)
        db.session.commit()
        return c.id


def _make_subsidy_record(app, resident_id, amount, period, status='正常', source='normal'):
    from models import db, SubsidyProject, SubsidyRecord
    with app.app_context():
        p = SubsidyProject.query.first()
        if not p:
            p = SubsidyProject(name='测试项目', project_type='其他', cycle='月', status='启用')
            db.session.add(p)
            db.session.commit()
        r = SubsidyRecord(resident_id=resident_id, project_id=p.id, issue_date=date.today(),
                          period=period, amount=amount, channel='镇级直发',
                          source=source, status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_dibao(app, resident_id, status='有效'):
    from models import db, Dibao
    with app.app_context():
        r = Dibao(resident_id=resident_id, status=status, dibao_type='农村低保')
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_disabled(app, resident_id, status='有效'):
    from models import db, Disabled
    with app.app_context():
        r = Disabled(resident_id=resident_id, status=status, disability_type='肢体')
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_special_support(app, household_name, household_id_card, status='有效'):
    from models import db, SpecialSupport
    with app.app_context():
        r = SpecialSupport(household_name=household_name, household_id_card=household_id_card,
                           status=status, support_mode='集中供养')
        db.session.add(r)
        db.session.commit()
        return r.id


def _stats(client):
    r = client.get('/dashboard/api/dashboard/stats')
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()['data']


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    """每个用例前后清理进程级 agg_cache（T17-2 缓存跨测试共享，避免数据污染）。"""
    from utils.agg_cache import invalidate
    invalidate()
    yield
    invalidate()


class TestFourCards:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_four_cards_120_30_25_8(self, app):
        """PRD 验收 4.2#1：120 高龄 / 30 困难 / 25 留守 / 8 困境 → 四卡片精确断言。"""
        for i in range(120):
            _make_resident(app, f'高龄{i}', _years_ago(81))           # 81 岁 → 高龄
        for i in range(30):
            rid = _make_resident(app, f'困难{i}', _years_ago(40))
            _make_tag(app, rid, '困难老人')
        for i in range(25):
            rid = _make_resident(app, f'留守{i}', _years_ago(10))
            _make_tag(app, rid, '留守儿童')
        for i in range(8):
            rid = _make_resident(app, f'困境{i}', _years_ago(8))
            _make_tag(app, rid, '困境儿童')

        client = _admin_client(app)
        d = _stats(client)
        assert d['elderly_count'] == 120
        assert d['hard_elderly_count'] == 30
        assert d['left_behind_count'] == 25
        assert d['distressed_child_count'] == 8

    def test_override_priority(self, app):
        """override 覆盖值优先：60 岁 override=高龄 计入；81 岁 override=儿童 不计入。"""
        r60 = _make_resident(app, '六十', _years_ago(60))
        _make_care_override(app, r60, '高龄')
        r81 = _make_resident(app, '八一', _years_ago(81))
        _make_care_override(app, r81, '儿童')

        client = _admin_client(app)
        d = _stats(client)
        assert d['elderly_count'] == 1, f"应仅含 override 高龄的 60 岁居民，实得 {d['elderly_count']}"

    def test_override_expired_30day(self, app):
        """override 超 30 天保护期失效，回退动态计算。"""
        r60 = _make_resident(app, '六十', _years_ago(60))
        _make_care_override(app, r60, '高龄', days_ago=40)

        client = _admin_client(app)
        d = _stats(client)
        assert d['elderly_count'] == 0, "过期 override 不应计入高龄"

    def test_tag_invalid_excluded(self, app):
        """困难老人标签失效不计入卡片。"""
        rid = _make_resident(app, '困难', _years_ago(40))
        _make_tag(app, rid, '困难老人', status='失效')

        client = _admin_client(app)
        d = _stats(client)
        assert d['hard_elderly_count'] == 0


class TestSubsidy:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_subsidy_record_agg(self, app):
        """补贴总额 = subsidy_record（normal+legacy 合计、停发不计）。"""
        now = date.today()
        r1 = _make_resident(app, '甲', _years_ago(40))
        r2 = _make_resident(app, '乙', _years_ago(30))
        # 当年 normal + legacy 各一笔
        _make_subsidy_record(app, r1, 100, f'{now.year:04d}-08', source='normal')
        _make_subsidy_record(app, r2, 50, f'{now.year:04d}-07', source='legacy')
        # 停发不计
        _make_subsidy_record(app, r1, 999, f'{now.year:04d}-08', status='停发')

        client = _admin_client(app)
        d = _stats(client)
        assert d['total_subsidy'] == 150.0, f"应 100+50=150，实得 {d['total_subsidy']}"


class TestFilters:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_soft_invalid_filter(self, app):
        """低保/残疾/特困软失效不计数（对齐 T16 台账 status='有效' 口径）。"""
        r1 = _make_resident(app, '有效低保', _years_ago(40))
        r2 = _make_resident(app, '失效低保', _years_ago(40))
        r3 = _make_resident(app, '有效残疾', _years_ago(40))
        r4 = _make_resident(app, '失效残疾', _years_ago(40))
        _make_dibao(app, r1, '有效')
        _make_dibao(app, r2, '失效')
        _make_disabled(app, r3, '有效')
        _make_disabled(app, r4, '失效')
        _make_special_support(app, '特困甲', '110101198001010011', '有效')
        _make_special_support(app, '特困乙', '110101198001010022', '失效')

        client = _admin_client(app)
        d = _stats(client)
        assert d['dibao_count'] == 1
        assert d['disabled_count'] == 1
        assert d['special_support_count'] == 1

    def test_population_exclude_outflow(self, app):
        """流出/死亡居民不计总人口（resident_flow 唯一口径）。"""
        _make_resident(app, '常住', _years_ago(40), flow_status='常住')
        _make_resident(app, '流出', _years_ago(40), flow_status='流出')
        _make_resident(app, '死亡', _years_ago(40), status='deceased')

        client = _admin_client(app)
        d = _stats(client)
        assert d['total_population'] == 1

    def test_todo_fields_removed(self, app):
        """待办区剔除（主理人拍板）：返回 data 无 todo_count/building_pending。"""
        from models import db, TodoItem
        r = _make_resident(app, '某人', _years_ago(40))
        with app.app_context():
            db.session.add(TodoItem(title='测试待办', status='todo'))
            db.session.commit()
        client = _admin_client(app)
        d = _stats(client)
        assert 'todo_count' not in d
        assert 'building_pending' not in d


class TestPrecision:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_age_sql_matches_care_age(self, app):
        """SQL julianday 精确日差与 care_age.calculate_age 逐人一致（钉精度）。"""
        from utils.care_age import calculate_age, elderly_threshold
        th = elderly_threshold()
        rnd = random.Random(42)
        samples = []
        today = date.today()
        # 覆盖边界：恰好 th*365.2425 天前（临界点）
        for delta_days in (int(th * 365.2425) - 3, int(th * 365.2425) - 1,
                           int(th * 365.2425), int(th * 365.2425) + 1,
                           int(th * 365.2425) + 3):
            samples.append(today - timedelta(days=delta_days))
        # 随机样本 1935-2010 年
        for _ in range(40):
            samples.append(today - timedelta(days=rnd.randint(4000, 33000)))

        expected = 0
        for i, bd in enumerate(samples):
            rid = _make_resident(app, f'样本{i}', bd)
            if (calculate_age(bd) or 0) >= th:
                expected += 1

        client = _admin_client(app)
        d = _stats(client)
        assert d['elderly_count'] == expected, \
            f"SQL 精确日差与 care_age 不一致：SQL={d['elderly_count']} Python={expected}"
