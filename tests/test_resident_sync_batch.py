# -*- coding: utf-8 -*-
"""tests/test_resident_sync_batch.py — 052 簇 B 居民同步批（GLB-06/07/08）。

- GLB-06：回写前冲突检测（副本值 ≠ 主档旧值 → 记 conflict 日志留痕后再覆盖）
- GLB-07：resident_sync_log 追溯查询界面（admin，可按状态过滤）
- GLB-08：id_card_hash 存量重复审计（只读）+ 唯一索引迁移 m020（轨 A）
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from test_022_resident_sync import (_admin_client, _edit, _make_app,
                                    _make_resident, _seed_strong)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def app(tmp_path, monkeypatch):
    application = _make_app(tmp_path, monkeypatch)
    from test_022_resident_sync import _unlock_keyholder
    _unlock_keyholder()
    return application


def _logs(app, **filters):
    from models import ResidentSyncLog
    with app.app_context():
        q = ResidentSyncLog.query
        for k, v in filters.items():
            q = q.filter(getattr(ResidentSyncLog, k) == v)
        return q.order_by(ResidentSyncLog.id).all()


# ---------------- GLB-06：冲突检测（差异留痕后再覆盖） ----------------

class TestConflictDetection:
    def test_divergent_copy_logged_as_conflict(self, app):
        """副本被他人改过（≠主档旧值）→ conflict 行留痕副本值，再按主档覆盖。"""
        client = _admin_client(app)
        rid = _make_resident(app, name='张三')
        _seed_strong(app, rid)                     # dibao 副本 name='旧名'（分叉）
        j = _edit(client, rid, name='新名').get_json()
        assert j['success'], j
        conflict = _logs(app, status='conflict', field='name')
        assert conflict, '副本分叉未被检出（无条件覆盖，无留痕）'
        assert '旧名' in (conflict[0].old_value or '')       # 被覆盖的副本值
        assert conflict[0].new_value == '新名'                # 主档新值
        from models import Dibao
        with app.app_context():
            assert Dibao.query.filter_by(resident_id=rid).first().name == '新名'  # 覆盖生效

    def test_aligned_copy_no_conflict_row(self, app):
        """副本与主档一致（无分叉）→ 只有 success 行，零噪音。"""
        from models import db, Dibao
        client = _admin_client(app)
        rid = _make_resident(app, name='张三')
        with app.app_context():
            db.session.add(Dibao(resident_id=rid, name='张三'))   # 与主档旧值一致
            db.session.commit()
        assert _edit(client, rid, name='新名').get_json()['success']
        assert _logs(app, status='conflict') == []
        assert _logs(app, status='success', field='name')


# ---------------- GLB-07：同步日志追溯界面 ----------------

class TestSyncLogQueryPage:
    def test_admin_can_view_and_filter(self, app):
        client = _admin_client(app)
        rid = _make_resident(app, name='张三')
        _seed_strong(app, rid)
        _edit(client, rid, name='新名')            # 产生 success + conflict 日志
        r = client.get('/resident/sync-log')
        assert r.status_code == 200
        page = r.get_data(as_text=True)
        assert '同步' in page and 'conflict' in page
        r2 = client.get('/resident/sync-log?status=conflict')
        assert r2.status_code == 200

    def test_non_admin_denied(self, app):
        from models import db, Role, User
        with app.app_context():
            role = Role(name='普通角色Z')
            db.session.add(role)
            db.session.commit()
            u = User(username='plain_z', role_id=role.id, is_active=True)
            u.set_password('TestPass123')
            db.session.add(u)
            db.session.commit()
            uid = u.id
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = uid
        assert c.get('/resident/sync-log').status_code == 403


# ---------------- GLB-08：id_card_hash 审计 + 唯一索引 ----------------

def _exec_m020(app):
    from sqlalchemy import text
    from models import db
    path = os.path.join(ROOT, 'scripts/migrations/m020_resident_id_card_hash_unique.sql')
    with open(path, encoding='utf-8') as fh:
        body = '\n'.join(ln for ln in fh.read().splitlines()
                         if not ln.strip().startswith('--'))
    with app.app_context():
        for stmt in [s.strip() for s in body.split(';') if s.strip()]:
            db.session.execute(text(stmt))
        db.session.commit()


class TestIdCardHashUnique:
    def test_m020_blocks_new_duplicates(self, app):
        from sqlalchemy.exc import IntegrityError
        from models import db, Resident
        _exec_m020(app)
        with app.app_context():
            db.session.add(Resident(name='甲', id_card_hash='HASH_DUP1'))
            db.session.commit()
            db.session.add(Resident(name='乙', id_card_hash='HASH_DUP1'))
            with pytest.raises(IntegrityError):
                db.session.commit()

    def test_m020_fails_on_dirty_data(self, app):
        """迁移自守卫：存量已有重复时建唯一索引必须失败（而非静默半生效）。"""
        from sqlalchemy.exc import IntegrityError, OperationalError
        from sqlalchemy import text
        from models import db, Resident
        with app.app_context():
            db.session.add(Resident(name='甲', id_card_hash='HASH_DUP2'))
            db.session.add(Resident(name='乙', id_card_hash='HASH_DUP2'))
            db.session.commit()
        with pytest.raises((IntegrityError, OperationalError)):
            _exec_m020(app)

    def test_audit_script_reports_duplicates(self, app, tmp_path):
        """只读审计：重复 hash 打印清单；干净库静默 exit 0。"""
        from models import db, Resident
        with app.app_context():
            db.session.add(Resident(name='甲', id_card_hash='HASH_DUP3'))
            db.session.add(Resident(name='乙', id_card_hash='HASH_DUP3'))
            db.session.commit()
            db_path = db.session.get_bind().url.database
        script = os.path.join(ROOT, 'scripts/audit_id_card_duplicates.py')
        out = subprocess.run([sys.executable, script, '--db', db_path],
                             capture_output=True, text=True)
        assert out.returncode == 1, '有重复时审计应 exit 1'
        assert 'HASH_DUP3' in out.stdout
