# -*- coding: utf-8 -*-
"""Sprint5 T19 到期预警单测。

覆盖（PRD R8 L576-602 + Sprint5 §3 T19 + §4.2，2026-08-27 拍板口径）：
- 三触发点：周期到期（月40/季100/年400，一次性不触发）/ 年龄边界（≥项目 age_max）/
  停发确认（项目停用存在在册对象）
- 幂等（重复巡检不重复生成）；处置关闭 + todo 联动；7 日未处置置顶标红
- 死亡自动关闭（R8-E4）；惰性巡检轨迹；并发互斥；无权限 403；todo 删除联动
"""
import os
import sys
import json
import time
import threading
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
    monkeypatch.setenv("SHUCUN_DISABLE_PERIODIC", "1")  # T19：测试隔离，禁周期巡检线程
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


def _make_resident(app, name, birth_date, status='normal'):
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _make_project(app, name='高龄津贴', cycle='月', age_max=None, status='启用'):
    from models import db, SubsidyProject
    with app.app_context():
        p = SubsidyProject(name=name, project_type='高龄津贴', cycle=cycle,
                           age_min=None, age_max=age_max, status=status)
        db.session.add(p)
        db.session.commit()
        return p.id


def _make_record(app, project_id, resident_id, issue_date, rec_status='正常'):
    from models import db, SubsidyRecord
    with app.app_context():
        r = SubsidyRecord(resident_id=resident_id, project_id=project_id,
                          issue_date=issue_date, period=issue_date.strftime('%Y-%m'),
                          amount=100, source='normal', status=rec_status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _generate(app):
    from utils.warn_service import generate_warnings
    with app.app_context():
        return generate_warnings()


def _pending_warns(app):
    from models import WarnRecord
    with app.app_context():
        return WarnRecord.query.filter_by(status='未处置').all()


class TestTriggerPoints:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_cycle_overdue_generated(self, app):
        """①周期到期：月发距上次发放超 40 天 → 生成预警。"""
        rid = _make_resident(app, '张大爷', _years_ago(70))
        pid = _make_project(app, cycle='月')
        _make_record(app, pid, rid, date.today() - timedelta(days=50))
        result = _generate(app)
        assert result['new'] == 1
        warns = _pending_warns(app)
        assert warns[0].warn_type == '周期到期'
        assert '40' in warns[0].trigger_reason

    def test_cycle_not_overdue(self, app):
        """周期未超阈值（10 天 < 40 天）→ 不生成。"""
        rid = _make_resident(app, '李奶奶', _years_ago(70))
        pid = _make_project(app, cycle='月')
        _make_record(app, pid, rid, date.today() - timedelta(days=10))
        assert _generate(app)['new'] == 0

    def test_cycle_once_skipped(self, app):
        """一次性项目不触发周期到期。"""
        rid = _make_resident(app, '王叔', _years_ago(60))
        pid = _make_project(app, cycle='一次性')
        _make_record(app, pid, rid, date.today() - timedelta(days=500))
        assert _generate(app)['new'] == 0

    def test_age_boundary(self, app):
        """②年龄边界：85 岁 ≥ age_max=80 → 生成；75 岁 → 不生成。"""
        rid_old = _make_resident(app, '高龄对象', _years_ago(85))
        rid_young = _make_resident(app, '中龄对象', _years_ago(75))
        pid = _make_project(app, age_max=80)
        _make_record(app, pid, rid_old, date.today() - timedelta(days=5))
        _make_record(app, pid, rid_young, date.today() - timedelta(days=5))
        result = _generate(app)
        assert result['new'] == 1
        warns = _pending_warns(app)
        assert warns[0].warn_type == '年龄边界'
        assert warns[0].resident_id == rid_old

    def test_stop_project(self, app):
        """③停发确认：项目停用且存在在册对象 → 生成。"""
        rid = _make_resident(app, '在册对象', _years_ago(65))
        pid = _make_project(app, status='停用')
        _make_record(app, pid, rid, date.today() - timedelta(days=3))
        result = _generate(app)
        assert result['new'] == 1
        assert _pending_warns(app)[0].warn_type == '停发确认'

    def test_idempotent(self, app):
        """幂等：连续两次巡检 → 未处置条数不变。"""
        rid = _make_resident(app, '张大爷', _years_ago(70))
        pid = _make_project(app, cycle='月')
        _make_record(app, pid, rid, date.today() - timedelta(days=50))
        assert _generate(app)['new'] == 1
        assert _generate(app)['new'] == 0  # 重复巡检不重复生成
        assert len(_pending_warns(app)) == 1


class TestWarnFlow:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    @pytest.fixture()
    def app_ctx(self, app):
        with app.app_context():
            yield

    def _seed_warn(self, app, client):
        rid = _make_resident(app, '张大爷', _years_ago(70))
        pid = _make_project(app, cycle='月')
        _make_record(app, pid, rid, date.today() - timedelta(days=50))
        _generate(app)
        from models import WarnRecord
        with app.app_context():
            return rid, pid, WarnRecord.query.first().id

    def test_handle_warning(self, app):
        """处置：POST /warn/api/handle → 已处置 + 关联 todo dismissed + 留痕。"""
        client = _admin_client(app)
        _, _, wid = self._seed_warn(app, client)
        r = client.post('/warn/api/handle', data={'id': str(wid), 'result': '已核实'})
        assert r.get_json()['success'] is True
        from models import WarnRecord, TodoItem, OperationLog
        with app.app_context():
            w = WarnRecord.query.get(wid)
            assert w.status == '已处置' and w.handle_result == '已核实'
            todo = TodoItem.query.filter_by(todo_type='warn', related_id=wid).first()
            assert todo is not None and todo.status == 'dismissed'
            log = OperationLog.query.filter_by(module='warn').first()
            assert log is not None and '处置' in log.summary
        # 幂等：重复处置
        r2 = client.post('/warn/api/handle', data={'id': str(wid)})
        assert r2.get_json()['success'] is False

    def test_overdue_pinned(self, app):
        """7 日未处置置顶标红：created_at=now-8d 的 warn todo → api_list 首条 overdue=true。"""
        client = _admin_client(app)
        _, _, wid = self._seed_warn(app, client)
        from models import TodoItem, db
        from datetime import datetime as dt
        with app.app_context():
            t = TodoItem.query.filter_by(todo_type='warn', related_id=wid).first()
            t.created_at = dt.now() - timedelta(days=8)
            db.session.commit()
        j = client.get('/todo/api/list').get_json()
        assert j['success']
        assert j['data']['data'][0]['overdue'] is True
        assert j['data']['data'][0]['todo_type'] == 'warn'

    def test_death_auto_close(self, app):
        """死亡自动关闭（R8-E4）：置 deceased → 预警已关闭 + 已注销停发 + todo dismissed。"""
        client = _admin_client(app)
        rid, _, wid = self._seed_warn(app, client)
        r = client.post('/resident/api/status', data={'id': str(rid), 'status': 'deceased'})
        assert r.get_json()['success'] is True
        from models import WarnRecord, TodoItem
        with app.app_context():
            w = WarnRecord.query.get(wid)
            assert w.status == '已关闭' and w.handle_result == '已注销停发'
            todo = TodoItem.query.filter_by(todo_type='warn', related_id=wid).first()
            assert todo.status == 'dismissed'

    def test_todo_delete_sync(self, app):
        """删除 warn todo → warn_record 同步已处置（待办区处置）。"""
        client = _admin_client(app)
        _, _, wid = self._seed_warn(app, client)
        from models import TodoItem
        with app.app_context():
            tid = TodoItem.query.filter_by(todo_type='warn', related_id=wid).first().id
        r = client.post(f'/todo/api/delete/{tid}')
        assert r.get_json()['success'] is True
        from models import WarnRecord
        with app.app_context():
            assert WarnRecord.query.get(wid).status == '已处置'

    def test_check_task_flow(self, app, app_ctx):
        """惰性巡检轨迹：POST /warn/api/check → task 轮询 done、summary 含新增。"""
        client = _admin_client(app)
        rid = _make_resident(app, '张大爷', _years_ago(70))
        pid = _make_project(app, cycle='月')
        _make_record(app, pid, rid, date.today() - timedelta(days=50))
        j = client.post('/warn/api/check').get_json()
        assert j['success'] and j['data']['task_id']
        task_id = j['data']['task_id']
        from utils.async_tasks import get_task
        for _ in range(200):
            t = get_task(task_id)
            if t and t.status in ('done', 'failed'):
                break
            time.sleep(0.02)
        assert t is not None and t.status == 'done', (t.result_summary if t else None)
        assert '新增' in (t.result_summary or '')

    def test_concurrency_mutex(self, app, app_ctx):
        """并发互斥：lazy_check 占用中再触发 → 立即 failed。"""
        from utils.async_tasks import create_task, get_task, run_async, _update
        from models import db
        hold = threading.Event()

        def hold_worker(task_id, **kw):
            hold.wait(timeout=2)
            _update(task_id, status='done', progress=100, result_summary='释放',
                    finished_at=datetime.now())

        t1 = create_task('lazy_check', 'tester', '占用')
        db.session.commit()
        run_async('lazy_check', t1.task_id, hold_worker)
        time.sleep(0.05)
        t2 = create_task('lazy_check', 'tester', '冲突')
        db.session.commit()
        run_async('lazy_check', t2.task_id, hold_worker)
        for _ in range(50):
            if get_task(t2.task_id).status in ('done', 'failed'):
                break
            time.sleep(0.01)
        assert get_task(t2.task_id).status == 'failed'
        assert '并发互斥' in (get_task(t2.task_id).result_summary or '')
        hold.set()
        for _ in range(100):
            if get_task(t1.task_id).status in ('done', 'failed'):
                break
            time.sleep(0.01)
        assert get_task(t1.task_id).status == 'done'

    def test_403_without_warn_edit(self, app):
        """无 warn.edit → handle 403。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='无预警权限T19', permissions=json.dumps({'resident.view': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            u = User(username='无预警权限T19', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.post('/warn/api/handle', data={'id': '1'})
        assert r.status_code == 403
        r2 = client.post('/warn/api/check')
        assert r2.status_code == 403
