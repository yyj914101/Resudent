# -*- coding: utf-8 -*-
"""T15-1 死亡级联单测（Sprint6 第一批，CascadeService 单事务 6 项级联）。

覆盖（对应 PRD R5-E1~E7 + ISS-04 + 计划稿 §4.1）：
- 确认后 6 项级联逐表断言（residents.status→deceased / subsidy_record 停发 /
  dibao·disabled·care_target·special_support 软失效 / resident_tag 失效 / 预警关闭）
- 单事务失败整体回滚（R5-E4，故障注入 monkeypatch，全仓首个回滚验证用例）
- 幂等：done 拒绝重复确认；failed 可重跑
- 权限：无 special.death_confirm 拒绝 403
- 边界：id_card 无匹配居民拒绝；pending 草稿无级联副作用（R5-E5 语义）
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
        sess['user_id'] = 1  # admin（super admin 全权限）
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


def _make_care_target(app, resident_id, status='有效'):
    from models import db, CareTarget
    with app.app_context():
        r = CareTarget(resident_id=resident_id, target_type='高龄老人', child_type='',
                       source='手工建档', status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_special_support(app, household_id_card, status='有效'):
    from models import db, SpecialSupport
    with app.app_context():
        r = SpecialSupport(household_name='户主', household_id_card=household_id_card,
                           status=status, support_mode='集中供养')
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_tag(app, resident_id, tag_code='dibao', status='有效'):
    from models import db, ResidentTag
    with app.app_context():
        r = ResidentTag(resident_id=resident_id, tag_code=tag_code, status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_project(app):
    from models import db, SubsidyProject
    with app.app_context():
        r = SubsidyProject(name='高龄津贴', project_type='高龄津贴', cycle='月',
                           default_amount=100, status='启用')
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_subsidy_record(app, resident_id, project_id, status='正常'):
    from models import db, SubsidyRecord
    from datetime import date
    with app.app_context():
        r = SubsidyRecord(resident_id=resident_id, project_id=project_id,
                          issue_date=date.today(), period='2026-08', amount=100,
                          status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_warn(app, resident_id, project_id=None, status='未处置'):
    from models import db, WarnRecord
    with app.app_context():
        r = WarnRecord(resident_id=resident_id, project_id=project_id,
                       warn_type='周期到期', status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_death_report(app, deceased_name, id_card, death_date='2026-08-15'):
    from models import db, DeathReport
    from datetime import datetime
    with app.app_context():
        r = DeathReport(deceased_name=deceased_name, id_card=id_card,
                        death_date=datetime.strptime(death_date, '%Y-%m-%d').date())
        db.session.add(r)
        db.session.commit()
        return r.id


def _confirm(client, rid):
    return client.post('/death-report/api/confirm', data={'id': rid})


def _row(app, model_cls, pk):
    with app.app_context():
        return model_cls.query.get(pk)


def _years_ago(n):
    from datetime import date
    return date.today().replace(year=date.today().year - n)


ID_CARD = '110101196001011234'


def _seed_full(app):
    """造全套级联对象：居民 + 四表 + 标签 + 补贴 + 预警 + 死亡登记。"""
    rid = _make_resident(app, '死亡甲', _years_ago(66), ID_CARD)
    d1 = _make_dibao(app, rid)
    d2 = _make_disabled(app, rid)
    c1 = _make_care_target(app, rid)
    s1 = _make_special_support(app, ID_CARD)
    t1 = _make_tag(app, rid)
    pid = _make_project(app)
    r1 = _make_subsidy_record(app, rid, pid)
    w1 = _make_warn(app, rid, pid)
    drid = _make_death_report(app, '死亡甲', ID_CARD)
    return dict(rid=rid, d1=d1, d2=d2, c1=c1, s1=s1, t1=t1, r1=r1, w1=w1, drid=drid)


# ============ T15-1 死亡级联（CascadeService） ============
class TestCascadeService:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_all_six_cascade(self, app):
        """R5-E1/ISS-04：确认后 6 项级联逐表断言 + 不物理删除 + cascade_status=done。"""
        from models import (Resident, Dibao, Disabled, CareTarget, SpecialSupport,
                            ResidentTag, SubsidyRecord, WarnRecord, DeathReport)
        seed = _seed_full(app)
        client = _admin_client(app)

        resp = _confirm(client, seed['drid'])
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True, body
        assert body['data']['subsidies'] == 1

        # ① residents.status→deceased
        r = _row(app, Resident, seed['rid'])
        assert r.status == 'deceased'
        assert r.status_reason == '死亡登记确认'
        # ② subsidy_record 停发（note 留痕）
        rec = _row(app, SubsidyRecord, seed['r1'])
        assert rec.status == '停发'
        assert '死亡停发' in rec.note
        # ③ 四表软失效
        assert _row(app, Dibao, seed['d1']).status == '失效'
        assert _row(app, Disabled, seed['d2']).status == '失效'
        assert _row(app, CareTarget, seed['c1']).status == '失效'   # ⑤ 一老一小台账标记失效
        assert _row(app, SpecialSupport, seed['s1']).status == '失效'  # hash 反向匹配
        # ④ resident_tag 失效
        assert _row(app, ResidentTag, seed['t1']).status == '失效'
        # ⑥ 预警关闭
        assert _row(app, WarnRecord, seed['w1']).status == '已关闭'
        # cascade_status=done + 不物理删除（记录仍存在）
        dr = _row(app, DeathReport, seed['drid'])
        assert dr.cascade_status == 'done'
        assert dr.confirmed_at is not None
        assert _row(app, Dibao, seed['d1']) is not None
        assert _row(app, SubsidyRecord, seed['r1']) is not None

    def test_rollback_on_failure(self, app, monkeypatch):
        """R5-E4：级联中途失败整体回滚 + cascade_status=failed（全仓首个故障注入用例）。"""
        from models import Resident, Dibao, SubsidyRecord, DeathReport
        seed = _seed_full(app)
        client = _admin_client(app)

        # 故障注入：级联第④步（软失效）抛异常——函数内 import 故 patch 模块属性生效
        def boom(resident_id, reason=''):
            raise RuntimeError('模拟软失效失败')

        import utils.death_soft_invalidate
        monkeypatch.setattr(utils.death_soft_invalidate, 'soft_invalidate_vulnerable', boom)

        resp = _confirm(client, seed['drid'])
        body = resp.get_json()
        assert body['success'] is False

        # 全回滚：① 未置 deceased；② 未停发；③ 未失效；死亡登记标记 failed
        assert _row(app, Resident, seed['rid']).status == 'normal'
        assert _row(app, SubsidyRecord, seed['r1']).status == '正常'
        assert _row(app, Dibao, seed['d1']).status == '有效'
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'failed'

    def test_idempotent_done(self, app):
        """幂等：done 后重复确认拒绝，不重复级联。"""
        from models import DeathReport
        seed = _seed_full(app)
        client = _admin_client(app)

        r1 = _confirm(client, seed['drid'])
        assert r1.get_json()['success'] is True
        r2 = _confirm(client, seed['drid'])
        body = r2.get_json()
        assert body['success'] is False
        assert '已确认' in body['msg']
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'done'

    def test_failed_retry(self, app, monkeypatch):
        """failed 可重跑：首次故障→failed，恢复后重跑→done。"""
        from models import Resident, DeathReport
        seed = _seed_full(app)
        client = _admin_client(app)

        import utils.death_soft_invalidate

        def boom(resident_id, reason=''):
            raise RuntimeError('模拟失败')

        monkeypatch.setattr(utils.death_soft_invalidate, 'soft_invalidate_vulnerable', boom)
        r1 = _confirm(client, seed['drid'])
        assert r1.get_json()['success'] is False
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'failed'

        # 恢复后重跑（monkeypatch 恢复原函数）
        monkeypatch.undo()
        r2 = _confirm(client, seed['drid'])
        assert r2.get_json()['success'] is True
        assert _row(app, Resident, seed['rid']).status == 'deceased'
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'done'

    def test_permission_403(self, app):
        """权限：无 special.death_confirm 的用户确认 → 403。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        seed = _seed_full(app)

        # 普通操作员角色：无 death_confirm 位
        with app.app_context():
            perms = {'resident': ['view'], 'death_report': ['view']}
            role = Role(name='普通操作员15', permissions=json.dumps(perms, ensure_ascii=False), is_system=False)
            db.session.add(role)
            db.session.commit()
            u = User(username='op15', password_hash=generate_password_hash('x'), role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid

        resp = _confirm(client, seed['drid'])
        assert resp.status_code == 403

    def test_no_resident(self, app):
        """边界：id_card 无匹配居民 → 拒绝确认（提示先登记居民）。"""
        # 不造居民，只造死亡登记
        drid = _make_death_report(app, '无名氏', '999999999999999999')
        client = _admin_client(app)
        resp = _confirm(client, drid)
        body = resp.get_json()
        assert body['success'] is False
        assert '居民' in body['msg']

    def test_no_id_card(self, app):
        """边界：死亡登记缺身份证 → 拒绝确认。"""
        from models import db, DeathReport
        from datetime import datetime
        with app.app_context():
            r = DeathReport(deceased_name='无证氏', id_card='',
                            death_date=datetime.strptime('2026-08-15', '%Y-%m-%d').date())
            db.session.add(r)
            db.session.commit()
            drid = r.id
        client = _admin_client(app)
        resp = _confirm(client, drid)
        assert resp.get_json()['success'] is False

    def test_pending_no_effect(self, app):
        """R5-E5：pending 草稿未确认不触发任何级联副作用。"""
        from models import Resident, Dibao, SubsidyRecord, WarnRecord
        seed = _seed_full(app)
        # 不调用 confirm——直接断言对象状态未变
        assert _row(app, Resident, seed['rid']).status == 'normal'
        assert _row(app, Dibao, seed['d1']).status == '有效'
        assert _row(app, SubsidyRecord, seed['r1']).status == '正常'
        assert _row(app, WarnRecord, seed['w1']).status == '未处置'


# ============ T15-2 48h 撤回 + E22 冲突禁止（Revoke） ============
def _make_todo(app, warn_id, status='todo'):
    from models import db, TodoItem
    with app.app_context():
        r = TodoItem(title='到期预警', todo_type='warn', related_id=warn_id, status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_checklist(app, death_report_id, status='有效'):
    from models import db, CascadeChecklist
    with app.app_context():
        r = CascadeChecklist(death_report_id=death_report_id, affected_items='[]', status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_reconcile_task(app):
    from models import db, ReconcileTask
    from datetime import date
    with app.app_context():
        r = ReconcileTask(source='公安', task_date=date.today(), quarter='2026-Q3',
                          status='done')
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_reconcile_result(app, task_id, id_card_hash, handle_status='已处置'):
    from models import db, ReconcileResult
    with app.app_context():
        r = ReconcileResult(task_id=task_id, id_card_hash=id_card_hash,
                            match_type='疑似未登记', handle_status=handle_status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _revoke(client, rid):
    return client.post('/death-report/api/revoke', data={'id': rid})


class TestRevoke:
    """T15-2 48h 撤回 + E22 冲突禁止（继承 TestCascadeService 复用 _seed_full 造数）。"""
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_revoke_within_48h_restores_all(self, app):
        """B2/R5-E6：48h 内撤回，6 表逐表恢复 + dr 重置 pending + confirm 字段清空。"""
        from models import (Resident, Dibao, SubsidyRecord, WarnRecord, TodoItem,
                            ResidentTag, DeathReport)
        seed = _seed_full(app)
        client = _admin_client(app)
        w_todo = _make_todo(app, seed['w1'])   # 预警关联 todo（close 时被 dismiss）

        # 确认（级联 6 项）
        r = _confirm(client, seed['drid'])
        assert r.get_json()['success'] is True
        # 预警关闭时 todo 被 dismiss
        assert _row(app, TodoItem, w_todo).status == 'dismissed'

        # 48h 内撤回
        r = _revoke(client, seed['drid'])
        assert r.get_json()['success'] is True, r.get_data(as_text=True)

        # ① resident 恢复 normal
        res = _row(app, Resident, seed['rid'])
        assert res.status == 'normal'
        assert res.status_date is None and res.status_reason is None
        # ② subsidy 恢复正常 + note 追加撤回记录
        rec = _row(app, SubsidyRecord, seed['r1'])
        assert rec.status == '正常'
        assert '撤回恢复' in rec.note
        # ③④ 四表/tag 恢复有效 + invalid_reason 清空
        assert _row(app, Dibao, seed['d1']).status == '有效'
        assert _row(app, ResidentTag, seed['t1']).status == '有效'
        assert _row(app, ResidentTag, seed['t1']).invalid_reason is None
        # ⑤⑥ 预警恢复未处置 + todo 恢复 todo
        assert _row(app, WarnRecord, seed['w1']).status == '未处置'
        assert _row(app, TodoItem, w_todo).status == 'todo'
        # 收尾：dr 重置 pending + confirm 字段清空
        dr = _row(app, DeathReport, seed['drid'])
        assert dr.cascade_status == 'pending'
        assert dr.confirmed_at is None

    def test_revoke_after_48h_rejected(self, app):
        """B2：超 48h 撤回入口关闭（走人工纠错），dr 保持 done。"""
        from models import db, DeathReport
        from datetime import datetime, timedelta
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        # 造数：confirmed_at 置 49h 前
        with app.app_context():
            dr = DeathReport.query.get(seed['drid'])
            dr.confirmed_at = datetime.now() - timedelta(hours=49)
            db.session.commit()

        resp = _revoke(client, seed['drid'])
        body = resp.get_json()
        assert body['success'] is False
        assert '48' in body['msg'] and '人工纠错' in body['msg']
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'done'

    def test_revoke_conflict_new_subsidy(self, app):
        """E22：确认后期间新补贴发放（amount>0）→ 禁止撤回。"""
        from models import db, SubsidyRecord, DeathReport
        from datetime import datetime
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        # 造数：确认后新发放一笔（create_time 默认 now > confirmed_at）
        with app.app_context():
            s = SubsidyRecord(resident_id=seed['rid'], project_id=1,
                              issue_date=datetime.now().date(), period='2026-09',
                              amount=200, status='正常')
            db.session.add(s)
            db.session.commit()

        resp = _revoke(client, seed['drid'])
        body = resp.get_json()
        assert body['success'] is False
        assert 'E22' in body['msg'] or '冲突' in body['msg']
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'done'

    def test_revoke_conflict_reconcile(self, app):
        """E22：确认后对账引用（reconcile_result 已处置）→ 禁止撤回。"""
        from utils.crypto import sha256_hash
        from models import DeathReport
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        # 造数：对账结果引用该居民（id_card_hash 匹配 + 已处置）
        tid = _make_reconcile_task(app)
        _make_reconcile_result(app, tid, sha256_hash(ID_CARD), handle_status='已处置')

        resp = _revoke(client, seed['drid'])
        body = resp.get_json()
        assert body['success'] is False
        assert '对账' in body['msg']
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'done'

    def test_revoke_then_reconfirm(self, app):
        """撤回后重置 pending，可再次确认（6 表再次级联）。"""
        from models import Resident, SubsidyRecord, DeathReport
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        _revoke(client, seed['drid'])
        # 重新确认
        r = _confirm(client, seed['drid'])
        assert r.get_json()['success'] is True
        assert _row(app, Resident, seed['rid']).status == 'deceased'
        assert _row(app, SubsidyRecord, seed['r1']).status == '停发'
        assert _row(app, DeathReport, seed['drid']).cascade_status == 'done'

    def test_revoke_checklist_voided(self, app):
        """cascade_checklist 置作废（T15-3 预留逻辑）。"""
        from models import CascadeChecklist
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        cid = _make_checklist(app, seed['drid'], status='有效')

        _revoke(client, seed['drid'])
        assert _row(app, CascadeChecklist, cid).status == '作废'

    def test_revoke_no_death_report(self, app):
        """记录不存在 → 拒绝。"""
        client = _admin_client(app)
        resp = _revoke(client, 99999)
        assert resp.get_json()['success'] is False

    def test_revoke_permission_403(self, app):
        """权限：无 special.death_confirm → 403。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        seed = _seed_full(app)
        with app.app_context():
            perms = {'resident': ['view'], 'death_report': ['view']}
            role = Role(name='普通操作员撤回', permissions=json.dumps(perms, ensure_ascii=False),
                        is_system=False)
            db.session.add(role)
            db.session.commit()
            u = User(username='oprevoke', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        resp = _revoke(client, seed['drid'])
        assert resp.status_code == 403
