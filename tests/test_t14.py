# -*- coding: utf-8 -*-
"""T14 R4 补贴管理（防误发）单测（Sprint3 第一批）。

覆盖（对应 PRD R4-E1~E6 + C4 集合定义 + 详细设计 §8 T14 行）：
- TestEligibility：check_eligibility 资格判定（D1，utils/subsidy_service.py）
  - 高龄津贴动态年龄边界（79/80/81）、死亡硬拦截（E3）、流出放行+提示（E6）、
    项目停用拦截（E4）、年龄边界、低保（is_deceased 排除/软失效）、困难老人补助、
    残疾补贴、标签命中/未命中、空标签放行、special_support hash 匹配、
    unsupported_children 兜底、None 入参
- TestProjectCrud：项目 CRUD 接口 + 字段校验 + 权限（D2，routes/subsidy.py project 端点）
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
    """建一个居民（含密文身份证）；支持死亡/流出状态（E3/E6 用例）。"""
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
    """建一个补贴项目（SubsidyProject），返回 id。"""
    from models import db, SubsidyProject
    data = {'name': '测试项目', 'project_type': '高龄津贴', 'cycle': '月',
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


def date_today():
    from datetime import date
    return date.today()


# ============ 资格判定核心（D1） ============
class TestEligibility:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _check(self, app, resident, project):
        from utils.subsidy_service import check_eligibility
        with app.app_context():
            return check_eligibility(resident, project)

    def test_high_age_elderly_eligible(self, app):
        """81 岁 → 高龄津贴 eligible=True。"""
        from models import Resident, SubsidyProject
        rid = _make_resident(app, '高龄甲', _years_ago(81), '110101194501011234')
        pid = _make_project(app, project_type='高龄津贴', name='高龄津贴')
        r = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert r['eligible'] is True, r
        assert r['detail']['age'] == 81
        assert r['reasons'] == []

    def test_high_age_threshold_boundary(self, app):
        """高龄阈值边界：80 岁通过，79 岁拦截。"""
        from models import Resident, SubsidyProject
        pid = _make_project(app, project_type='高龄津贴', name='高龄津贴')
        rid80 = _make_resident(app, '高龄乙', _years_ago(80), '110101194601011234')
        rid79 = _make_resident(app, '高龄丙', _years_ago(79), '110101194701011234')
        assert self._check(app, _get(app, Resident, rid80), _get(app, SubsidyProject, pid))['eligible'] is True
        r79 = self._check(app, _get(app, Resident, rid79), _get(app, SubsidyProject, pid))
        assert r79['eligible'] is False
        assert any('未达高龄津贴阈值' in x for x in r79['reasons'])

    def test_deceased_blocked(self, app):
        """死亡居民 → 拦截（E3），reason 含「已死亡」。"""
        from models import Resident, SubsidyProject
        rid = _make_resident(app, '死亡丁', _years_ago(81), '110101194501011235', status='deceased')
        pid = _make_project(app, project_type='高龄津贴', name='高龄津贴')
        r = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert r['eligible'] is False
        assert r['detail']['deceased'] is True
        assert any('已死亡' in x for x in r['reasons'])

    def test_flow_out_allowed_with_hint(self, app):
        """流出居民 → 放行（R4-E6）+ flow_out=True + 提示。"""
        from models import Resident, SubsidyProject
        rid = _make_resident(app, '流出戊', _years_ago(81), '110101194501011236', flow_status='流出')
        pid = _make_project(app, project_type='高龄津贴', name='高龄津贴')
        r = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert r['eligible'] is True, r
        assert r['detail']['flow_out'] is True
        assert any('已流出' in x for x in r['reasons'])

    def test_project_disabled_blocked(self, app):
        """项目停用 → 拦截（E4）。"""
        from models import Resident, SubsidyProject
        rid = _make_resident(app, '停用己', _years_ago(81), '110101194501011237')
        pid = _make_project(app, project_type='高龄津贴', name='高龄津贴', status='停用')
        r = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert r['eligible'] is False
        assert any('已停用' in x for x in r['reasons'])

    def test_none_inputs_blocked(self, app):
        """resident/project 为 None → 拦截。"""
        from utils.subsidy_service import check_eligibility
        assert check_eligibility(None, None)['eligible'] is False
        assert check_eligibility(None, None)['reasons'] == ['居民或项目不存在']

    def test_age_boundary_blocked(self, app):
        """项目 age_min/age_max 适用边界：超出 → 拦截。"""
        from models import Resident, SubsidyProject
        rid = _make_resident(app, '边界庚', _years_ago(70), '110101195601011238')
        pid = _make_project(app, project_type='其他', name='70岁以上补助',
                            age_min=80, age_max=120, target_tags='[]')
        r = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert r['eligible'] is False
        assert any('低于项目适用下限' in x for x in r['reasons'])

    def test_dibao_active_eligible(self, app):
        """低保金：Dibao 有效 → 通过；成员死亡 → 拦截；软失效 → 拦截。"""
        from models import db, Resident, SubsidyProject, Dibao
        pid = _make_project(app, project_type='低保金', name='低保金')
        rid1 = _make_resident(app, '低保辛', _years_ago(40), '110101198601011239')
        with app.app_context():
            db.session.add(Dibao(resident_id=rid1, name='低保辛', status='有效'))
            db.session.commit()
        assert self._check(app, _get(app, Resident, rid1), _get(app, SubsidyProject, pid))['eligible'] is True

        rid2 = _make_resident(app, '低保壬', _years_ago(40), '110101198601011240')
        with app.app_context():
            db.session.add(Dibao(resident_id=rid2, name='低保壬', status='有效', is_deceased='是'))
            db.session.commit()
        r2 = self._check(app, _get(app, Resident, rid2), _get(app, SubsidyProject, pid))
        assert r2['eligible'] is False
        assert any('低保' in x for x in r2['reasons'])

        rid3 = _make_resident(app, '低保癸', _years_ago(40), '110101198601011241')
        with app.app_context():
            db.session.add(Dibao(resident_id=rid3, name='低保癸', status='失效', invalid_reason='死亡'))
            db.session.commit()
        r3 = self._check(app, _get(app, Resident, rid3), _get(app, SubsidyProject, pid))
        assert r3['eligible'] is False

    def test_care_target_elderly_difficulty(self, app):
        """困难老人补助：CareTarget(老人/困难/有效) 通过；无记录拦截。"""
        from models import db, Resident, SubsidyProject, CareTarget
        pid = _make_project(app, project_type='困难老人补助', name='困难老人补助')
        rid1 = _make_resident(app, '困难甲', _years_ago(82), '110101194401011242')
        with app.app_context():
            db.session.add(CareTarget(resident_id=rid1, name='困难甲', target_type='老人',
                                      elder_type='困难', status='有效'))
            db.session.commit()
        assert self._check(app, _get(app, Resident, rid1), _get(app, SubsidyProject, pid))['eligible'] is True

        rid2 = _make_resident(app, '困难乙', _years_ago(82), '110101194401011243')
        r2 = self._check(app, _get(app, Resident, rid2), _get(app, SubsidyProject, pid))
        assert r2['eligible'] is False
        assert any('困难老人补助' in x for x in r2['reasons'])

    def test_disabled_eligible(self, app):
        """残疾补贴：Disabled 有效通过；无记录拦截。"""
        from models import db, Resident, SubsidyProject, Disabled
        pid = _make_project(app, project_type='残疾补贴', name='残疾补贴')
        rid1 = _make_resident(app, '残疾甲', _years_ago(50), '110101197601011244')
        with app.app_context():
            db.session.add(Disabled(resident_id=rid1, name='残疾甲', status='有效'))
            db.session.commit()
        assert self._check(app, _get(app, Resident, rid1), _get(app, SubsidyProject, pid))['eligible'] is True

        rid2 = _make_resident(app, '残疾乙', _years_ago(50), '110101197601011245')
        r2 = self._check(app, _get(app, Resident, rid2), _get(app, SubsidyProject, pid))
        assert r2['eligible'] is False

    def test_tag_match(self, app):
        """其他类型：target_tags 命中有效标签 → 通过；未命中 → 拦截。"""
        from models import db, Resident, SubsidyProject, ResidentTag
        pid_hit = _make_project(app, project_type='其他', name='老年补贴',
                                target_tags=json.dumps(['高龄老人'], ensure_ascii=False))
        pid_miss = _make_project(app, project_type='其他', name='其他补贴',
                                 target_tags=json.dumps(['低保对象'], ensure_ascii=False))
        rid = _make_resident(app, '标签甲', _years_ago(81), '110101194501011246')
        with app.app_context():
            db.session.add(ResidentTag(resident_id=rid, tag_code='高龄老人', status='有效'))
            db.session.commit()
        r_hit = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid_hit))
        assert r_hit['eligible'] is True
        assert r_hit['detail']['matched_tags'] == ['高龄老人']
        r_miss = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid_miss))
        assert r_miss['eligible'] is False
        assert any('未命中适用标签' in x for x in r_miss['reasons'])

    def test_tag_empty_allowed(self, app):
        """其他类型：无 target_tags → 无标签约束，生命周期/年龄通过即放行。"""
        from models import Resident, SubsidyProject
        rid = _make_resident(app, '空标甲', _years_ago(50), '110101197601011247')
        pid = _make_project(app, project_type='其他', name='无标签项目')
        r = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert r['eligible'] is True, r

    def test_special_support_hash_match(self, app):
        """特困供养（无 resident_id）：household_id_card 哈希匹配 → 通过。"""
        from models import db, Resident, SubsidyProject, SpecialSupport
        id_card = '110101194501011248'
        rid = _make_resident(app, '特困甲', _years_ago(78), id_card)
        pid = _make_project(app, project_type='其他', name='特困供养金',
                            target_tags=json.dumps(['特困供养'], ensure_ascii=False))
        with app.app_context():
            db.session.add(SpecialSupport(household_name='特困甲', household_id_card=id_card, status='有效'))
            db.session.commit()
        r = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert r['eligible'] is True, r
        assert r['detail']['special_support'] is True

    def test_unsupported_child_fallback(self, app):
        """事实无人抚养儿童（无 resident_id）：name+birth_date 兜底匹配 → 通过。"""
        from models import db, Resident, SubsidyProject, UnsupportedChild
        rid = _make_resident(app, '孤儿甲', _years_ago(10), '110101201601011249')
        pid = _make_project(app, project_type='其他', name='事实无人抚养儿童补助',
                            target_tags=json.dumps(['事实无人抚养儿童'], ensure_ascii=False))
        with app.app_context():
            r = Resident.query.get(rid)
            db.session.add(UnsupportedChild(name='孤儿甲', birth_date=r.birth_date))
            db.session.commit()
        res = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert res['eligible'] is True, res
        assert res['detail']['unsupported_child'] is True

    def test_birth_date_missing_dynamic_age(self, app):
        """高龄津贴但居民缺出生日期 → 拦截。"""
        from models import Resident, SubsidyProject
        rid = _make_resident(app, '无生日', None, '110101199001011250')
        pid = _make_project(app, project_type='高龄津贴', name='高龄津贴')
        r = self._check(app, _get(app, Resident, rid), _get(app, SubsidyProject, pid))
        assert r['eligible'] is False
        assert any('无法判定高龄津贴资格' in x or '缺出生日期' in x for x in r['reasons'])

    def test_parse_target_tags_robust(self, app):
        """target_tags 解析容错：坏 JSON → []；空 → []；列表 → 清洗。"""
        from utils.subsidy_service import _parse_target_tags
        assert _parse_target_tags(None) == []
        assert _parse_target_tags('') == []
        assert _parse_target_tags('not-json{{{') == []
        assert _parse_target_tags('{"a":1}') == []
        assert _parse_target_tags('["高龄老人", " 低保对象 "]') == ['高龄老人', '低保对象']


# ============ 项目 CRUD（D2） ============
class TestProjectCrud:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _client(self, app):
        return _admin_client(app)

    def _create(self, client, **overrides):
        payload = {
            'name': '高龄津贴',
            'project_type': '高龄津贴',
            'cycle': '月',
            'default_amount': '100.00',
            'status': '启用',
            'target_tags': '[]',
        }
        payload.update(overrides)
        return client.post('/subsidy/api/project/save', data=payload)

    def test_create_project(self, app):
        """创建项目 → 成功，列表可见。"""
        client = self._client(app)
        r = self._create(client)
        j = r.get_json()
        assert j['success'], j
        r2 = client.get('/subsidy/api/project/list')
        j2 = r2.get_json()
        assert j2['success']
        assert j2['total'] == 1
        assert j2['data'][0]['name'] == '高龄津贴'

    def test_create_missing_name(self, app):
        r = self._create(self._client(app), name='')
        assert r.get_json()['success'] is False
        assert '不能为空' in r.get_json()['msg']

    def test_create_name_too_long(self, app):
        r = self._create(self._client(app), name='长' * 101)
        assert r.get_json()['success'] is False

    def test_create_bad_project_type(self, app):
        r = self._create(self._client(app), project_type='非法类型')
        assert r.get_json()['success'] is False
        assert '项目类型' in r.get_json()['msg']

    def test_create_bad_cycle(self, app):
        r = self._create(self._client(app), cycle='周')
        assert r.get_json()['success'] is False

    def test_create_bad_amount_negative(self, app):
        r = self._create(self._client(app), default_amount='-1')
        assert r.get_json()['success'] is False

    def test_create_bad_amount_precision(self, app):
        r = self._create(self._client(app), default_amount='1.999')
        assert r.get_json()['success'] is False

    def test_create_bad_age_range(self, app):
        r = self._create(self._client(app), age_min='90', age_max='80')
        assert r.get_json()['success'] is False
        assert '年龄' in r.get_json()['msg']

    def test_create_bad_age_non_int(self, app):
        r = self._create(self._client(app), age_min='abc')
        assert r.get_json()['success'] is False

    def test_create_bad_target_tags(self, app):
        r = self._create(self._client(app), target_tags='not-json{{{')
        assert r.get_json()['success'] is False
        assert '标签' in r.get_json()['msg']

    def test_update_project(self, app):
        client = self._client(app)
        assert self._create(client).get_json()['success'] is True
        with app.app_context():
            from models import SubsidyProject
            pid = SubsidyProject.query.first().id
        r = self._create(client, id=str(pid), name='高龄津贴-调整', status='停用')
        assert r.get_json()['success'] is True, r.get_json()
        r2 = client.get('/subsidy/api/project/list')
        row = r2.get_json()['data'][0]
        assert row['name'] == '高龄津贴-调整'
        assert row['status'] == '停用'

    def test_update_missing_id(self, app):
        r = self._create(self._client(app), id='9999', name='不存在项目')
        assert r.get_json()['success'] is False
        assert '不存在' in r.get_json()['msg']

    def test_delete_project(self, app):
        client = self._client(app)
        assert self._create(client).get_json()['success'] is True
        with app.app_context():
            from models import SubsidyProject
            pid = SubsidyProject.query.first().id
        r = client.post(f'/subsidy/api/project/delete/{pid}')
        assert r.get_json()['success'] is True
        assert client.get('/subsidy/api/project/list').get_json()['total'] == 0

    def test_delete_with_records_blocked(self, app):
        client = self._client(app)
        assert self._create(client).get_json()['success'] is True
        rid = _make_resident(app, '发放居民', _years_ago(40), '110101198601011251')
        with app.app_context():
            from models import db, SubsidyProject, SubsidyRecord
            pid = SubsidyProject.query.first().id
            db.session.add(SubsidyRecord(resident_id=rid, project_id=pid,
                                         issue_date=date_today(), period='2026-08',
                                         amount=100, source='normal'))
            db.session.commit()
        r = client.post(f'/subsidy/api/project/delete/{pid}')
        j = r.get_json()
        assert j['success'] is False
        assert '已有发放记录' in j['msg']

    def test_delete_missing(self, app):
        r = self._client(app).post('/subsidy/api/project/delete/9999')
        assert r.get_json()['success'] is False

    def test_list_filter_and_paginate(self, app):
        client = self._client(app)
        for i, (nm, tp) in enumerate([('项目A', '低保金'), ('项目B', '残疾补贴'), ('项目C', '其他')], 1):
            self._create(client, name=nm, project_type=tp, cycle='季')
        j = client.get('/subsidy/api/project/list?project_type=低保金').get_json()
        assert j['total'] == 1 and j['data'][0]['name'] == '项目A'
        j2 = client.get('/subsidy/api/project/list?limit=2').get_json()
        assert j2['total'] == 3 and len(j2['data']) == 2

    def test_target_tags_roundtrip(self, app):
        """target_tags JSON 数组落库/读库往返一致。"""
        client = self._client(app)
        tags = ['高龄老人', '低保对象']
        assert self._create(client, target_tags=json.dumps(tags, ensure_ascii=False)).get_json()['success'] is True
        row = client.get('/subsidy/api/project/list').get_json()['data'][0]
        assert row['target_tags'] == tags

    def test_permission_403_without_subsidy_edit(self, app):
        """非 admin 且无 subsidy.add 权限 → save 403（权限键接线，PRD M24）。"""
        from models import db, Role, User
        with app.app_context():
            role = Role(name='无补贴权限T14', permissions=json.dumps({'resident.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='普通用户T14', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = self._create(client)
        assert r.status_code == 403

    def test_page_render(self, app):
        """项目配置页面渲染 200（登录后）。"""
        r = self._client(app).get('/subsidy/projects')
        assert r.status_code == 200
