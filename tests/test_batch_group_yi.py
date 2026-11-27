# -*- coding: utf-8 -*-
"""tests/test_batch_group_yi.py — 052 独立组乙（GLB-13/14/18/24）。

GLB-19（crudTable 行内开关）经评估零消费者、不做死代码实现——结论与依据见
052-RESULT 独立组乙节（登记原文即「体验增强，无阻塞」）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from test_022_resident_sync import _admin_client, _make_app, _make_resident

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))      # cunwuguanli-main
REPO_ROOT = os.path.dirname(os.path.dirname(ROOT))                        # git 仓库根


@pytest.fixture()
def app(tmp_path, monkeypatch):
    application = _make_app(tmp_path, monkeypatch)
    from test_022_resident_sync import _unlock_keyholder
    _unlock_keyholder()
    return application


# ---------------- GLB-24：veteran 下拉幂等填装 ----------------

class TestVeteranDictIdempotent:
    def test_load_dict_empties_both_branches(self):
        """loadDict 两个分支都必须先 empty()——SPA 复进页面不累积重复选项。"""
        src = open(os.path.join(ROOT, 'static/js/pages/veteran_list.js'),
                   encoding='utf-8').read()
        m = re.search(r'function loadDict\(.*?\n\}', src, re.S)
        assert m, '未找到 loadDict 函数'
        assert m.group(0).count('empty()') >= 2, 'keepFirst=false 分支未清空旧选项'


# ---------------- GLB-18：m0xx 草稿归档（处置②，不删表） ----------------

class TestM0xxArchived:
    def test_draft_moved_out_of_migrations(self):
        assert not list(__import__('glob').glob(
            os.path.join(ROOT, 'scripts/migrations/m0xx*'))), 'm0xx 草稿仍在 migrations 目录'

    def test_archived_with_index_note(self):
        archived = os.path.join(REPO_ROOT, 'docs', '02-一次性运行产物',
                                'm0xx_drop_removed_modules.sql')
        assert os.path.exists(archived)
        idx = open(os.path.join(REPO_ROOT, 'docs', '00-归档清单与移动日志.md'),
                   encoding='utf-8').read()
        assert 'm0xx_drop_removed_modules' in idx


# ---------------- GLB-14：表级 ANALYZE + 备份结构导出 ----------------

class TestTableLevelAnalyze:
    def test_build_analyze_sql_with_table(self, app):
        from utils.ddl_guard import build_analyze_sql, GuardError
        with app.app_context():
            assert build_analyze_sql('residents') == 'ANALYZE "residents"'
            assert build_analyze_sql() == 'ANALYZE'
            with pytest.raises(GuardError):
                build_analyze_sql('no_such_table_xyz')

    def test_plan_accepts_table(self, app):
        from utils.ddl_guard import plan
        with app.app_context():
            r = plan('analyze', {'table': 'residents'})
            assert r['sql'] == 'ANALYZE "residents"'


class TestBackupSchemaExport:
    def test_export_schema_returns_ddl(self, app):
        r = _admin_client(app).get('/backup/api/export-schema')
        assert r.status_code == 200
        assert 'CREATE TABLE' in r.get_data(as_text=True)
        assert 'attachment' in (r.headers.get('Content-Disposition') or '')


# ---------------- GLB-13：关键字段变更留痕（OperationLog.detail） ----------------

class TestKeyFieldAuditTrail:
    def test_bank_card_change_logged_with_diff(self, app):
        client = _admin_client(app)
        rid = _make_resident(app, bank_card='6222021234567890')
        from models import db, OperationLog
        with app.app_context():
            db.session.query(OperationLog).delete()
            db.session.commit()
        r = client.post('/resident/api/save', data={
            'id': rid, 'bank_card': '6222029876543210'})
        assert r.get_json()['success']
        with app.app_context():
            log = (OperationLog.query.filter_by(action='edit')
                   .order_by(OperationLog.id.desc()).first())
            assert log is not None
            assert 'bank_card' in (log.detail or ''), '关键字段 diff 未留痕'
            # 旧→新都在，且为脱敏形态（mask_text 掩码中段）
            assert '622202' in log.detail and '7890' in log.detail and '3210' in log.detail
            assert '*' in log.detail and '6222021234567890' not in log.detail

    def test_no_spurious_detail_when_nothing_key_changed(self, app):
        """非关键字段（如备注）变更不产生 detail（避免日志噪音）。"""
        client = _admin_client(app)
        rid = _make_resident(app, remark='原备注')
        from models import db, OperationLog
        with app.app_context():
            db.session.query(OperationLog).delete()
            db.session.commit()
        client.post('/resident/api/save', data={'id': rid, 'remark': '新备注'})
        with app.app_context():
            log = (OperationLog.query.filter_by(action='edit')
                   .order_by(OperationLog.id.desc()).first())
            assert log is not None
            assert not (log.detail or '').strip()
