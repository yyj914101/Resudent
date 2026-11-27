# -*- coding: utf-8 -*-
"""T14-5 补贴批量登记（E5）单测（Sprint3 第三批）。

覆盖（对应 PRD R4-E5 L407 + 详细设计 §8 record_import 端点）：
- TestSubsidyBatchCore：run_subsidy_batch 核心逻辑（同步直调，避开异步线程时序）
- TestSubsidyBatchEndpoint：批量登记端点（上传/执行/报告/导出/权限）
"""
import io
import os
import sys
import json
import uuid
import time

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


def _years_ago(n):
    from datetime import date
    return date.today().replace(year=date.today().year - n)


def _write_xlsx(path, headers, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _make_batch(app, project_id, xlsx_path, status='pending'):
    from models import db, SubsidyBatchImport
    with app.app_context():
        b = SubsidyBatchImport(
            batch_id=uuid.uuid4().hex[:12],
            project_id=project_id,
            source_file='batch.xlsx',
            source_path=xlsx_path,
            source_hash='h_' + uuid.uuid4().hex[:8],
            operator=1,
            status=status,
        )
        db.session.add(b)
        db.session.commit()
        return b.batch_id


# ============ 批量登记核心（同步直调） ============
class TestSubsidyBatchCore:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _run(self, app, batch_id):
        from utils.subsidy_service import run_subsidy_batch
        with app.app_context():
            # 模拟 A1：直接调用（已处 app context）
            run_subsidy_batch('dummy_task_id', batch_id)

    def test_batch_98_success_2_fail(self, app, tmp_path):
        """100 户批量：98 成功 + 2 失败（含死亡对象）→ 结果报告清晰（E5）。"""
        from models import db, SubsidyBatchImport, SubsidyRecord
        pid = _make_project(app)
        # 98 个正常高龄居民 + 2 个死亡居民
        normal_ids = []
        for i in range(98):
            idc = f'11010119450{1000 + i:03d}0'  # 生成唯一身份证
            normal_ids.append(_make_resident(app, f'高龄{i}', _years_ago(81), idc))
        # 2 个死亡居民（身份证需唯一）
        deceased_ids = []
        for i in range(2):
            idc = f'11010119500{2000 + i:03d}0'
            deceased_ids.append(_make_resident(app, f'死亡{i}', _years_ago(81), idc, status='deceased'))

        # 造 xlsx：100 行（98 正常 + 2 死亡）
        headers = ['身份证号', '发放期间', '金额']
        rows = []
        for i, rid in enumerate(normal_ids):
            rows.append([f'11010119450{1000 + i:03d}0', '2026-08', 100])
        for i, rid in enumerate(deceased_ids):
            rows.append([f'11010119500{2000 + i:03d}0', '2026-08', 100])

        xlsx = str(tmp_path / "batch100.xlsx")
        _write_xlsx(xlsx, headers, rows)
        batch_id = _make_batch(app, pid, xlsx)

        self._run(app, batch_id)

        with app.app_context():
            b = SubsidyBatchImport.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'
            assert b.success_rows == 98
            assert b.fail_rows == 2
            assert b.total_rows == 100
            # 98 条记录落库
            assert SubsidyRecord.query.filter_by(project_id=pid, source='normal').count() == 98

    def test_batch_deceased_blocked_with_log(self, app, tmp_path):
        """死亡居民逐条拦截 + blocked_subsidy 日志。"""
        from models import db, SubsidyBatchImport, SubsidyBatchError, OperationLog
        pid = _make_project(app)
        deceased_idc = '110101194501011234'
        _make_resident(app, '死亡甲', _years_ago(81), deceased_idc, status='deceased')

        xlsx = str(tmp_path / "batch_dead.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [[deceased_idc, '2026-08', 100]])
        batch_id = _make_batch(app, pid, xlsx)

        self._run(app, batch_id)

        with app.app_context():
            b = SubsidyBatchImport.query.filter_by(batch_id=batch_id).first()
            assert b.fail_rows == 1
            err = SubsidyBatchError.query.filter_by(batch_id=batch_id).first()
            assert err is not None
            assert '已死亡' in err.reason
            # 身份证脱敏（前6后4）
            assert err.id_card_masked == '110101********1234'
            # blocked_subsidy 日志
            assert OperationLog.query.filter_by(action='blocked_subsidy').first() is not None

    def test_batch_disabled_project_blocked(self, app, tmp_path):
        """停用项目批量登记 → 全部拦截。"""
        from models import SubsidyBatchImport
        pid = _make_project(app, status='停用')
        idc = '110101194501011235'
        _make_resident(app, '停用甲', _years_ago(81), idc)

        xlsx = str(tmp_path / "batch_disabled.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [[idc, '2026-08', 100]])
        batch_id = _make_batch(app, pid, xlsx)

        self._run(app, batch_id)

        with app.app_context():
            b = SubsidyBatchImport.query.filter_by(batch_id=batch_id).first()
            assert b.fail_rows == 1

    def test_batch_resident_not_found(self, app, tmp_path):
        """居民不存在 → 失败行（身份证脱敏）。"""
        from models import SubsidyBatchError
        pid = _make_project(app)
        idc = '110101194501011236'  # 未建档居民

        xlsx = str(tmp_path / "batch_notfound.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [[idc, '2026-08', 100]])
        batch_id = _make_batch(app, pid, xlsx)

        self._run(app, batch_id)

        with app.app_context():
            err = SubsidyBatchError.query.filter_by(batch_id=batch_id).first()
            assert err is not None
            assert '居民不存在' in err.reason
            assert err.id_card_masked == '110101********1236'

    def test_batch_idempotent_skip_duplicate(self, app, tmp_path):
        """行级 source_hash 幂等：重复登记同居民同期间 → 跳过。"""
        from models import db, SubsidyRecord
        pid = _make_project(app)
        idc = '110101194501011237'
        rid = _make_resident(app, '幂等甲', _years_ago(81), idc)

        xlsx = str(tmp_path / "batch_idem.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [[idc, '2026-08', 100]])
        batch_id = _make_batch(app, pid, xlsx)

        self._run(app, batch_id)

        # 再跑一次（新批次，同居民同期间）
        xlsx2 = str(tmp_path / "batch_idem2.xlsx")
        _write_xlsx(xlsx2, ['身份证号', '发放期间', '金额'], [[idc, '2026-08', 100]])
        batch_id2 = _make_batch(app, pid, xlsx2)

        self._run(app, batch_id2)

        with app.app_context():
            # 只有第一次成功（第二次幂等跳过）
            assert SubsidyRecord.query.filter_by(resident_id=rid, project_id=pid, source='normal').count() == 1


# ============ 批量登记端点 ============
class TestSubsidyBatchEndpoint:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _client(self, app):
        return _admin_client(app)

    def _upload(self, app, client, project_id, xlsx_path):
        with open(xlsx_path, 'rb') as f:
            data = {'project_id': str(project_id), 'file': (io.BytesIO(f.read()), 'batch.xlsx')}
        return client.post('/subsidy/api/record/import-upload', data=data,
                           content_type='multipart/form-data')

    def test_upload_creates_batch(self, app, tmp_path):
        """上传 xlsx 建批次成功。"""
        pid = _make_project(app)
        xlsx = str(tmp_path / "u.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [['110101194501011238', '2026-08', 100]])
        r = self._upload(app, self._client(app), pid, xlsx)
        j = r.get_json()
        assert j['success'], j
        assert j['data']['batch_id']

    def test_upload_duplicate_returns_existing(self, app, tmp_path):
        """source_hash 幂等：重复提交同一文件 → 返回既有批次。"""
        pid = _make_project(app)
        xlsx = str(tmp_path / "dup.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [['110101194501011239', '2026-08', 100]])
        client = self._client(app)
        r1 = self._upload(app, client, pid, xlsx)
        assert r1.get_json()['success']
        r2 = self._upload(app, client, pid, xlsx)
        j2 = r2.get_json()
        assert j2['success']
        assert j2['data']['duplicate'] is True

    def test_upload_same_file_different_project_not_reused(self, app, tmp_path):
        """R-108-01①：同文件不同 project_id → 各自建批、互不复用。"""
        from models import SubsidyBatchImport
        pid_a = _make_project(app, name='高龄津贴A')
        pid_b = _make_project(app, name='高龄津贴B')
        xlsx = str(tmp_path / "scope.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [['110101194501011244', '2026-08', 100]])
        client = self._client(app)
        j1 = self._upload(app, client, pid_a, xlsx).get_json()
        j2 = self._upload(app, client, pid_b, xlsx).get_json()
        assert j1['success'] and j2['success'], (j1, j2)
        assert not j1['data'].get('duplicate')
        assert not j2['data'].get('duplicate')
        assert j1['data']['batch_id'] != j2['data']['batch_id']
        with app.app_context():
            assert SubsidyBatchImport.query.filter_by(project_id=pid_a).count() == 1
            assert SubsidyBatchImport.query.filter_by(project_id=pid_b).count() == 1

    def test_upload_same_file_same_project_reused(self, app, tmp_path):
        """R-108-01②：同文件同 project_id → duplicate=True 且返回既有批次。"""
        pid = _make_project(app, name='高龄津贴C')
        xlsx = str(tmp_path / "scope_same.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [['110101194501011245', '2026-08', 100]])
        client = self._client(app)
        j1 = self._upload(app, client, pid, xlsx).get_json()
        j2 = self._upload(app, client, pid, xlsx).get_json()
        assert j1['success'] and j2['success'], (j1, j2)
        assert j2['data']['duplicate'] is True
        assert j2['data']['batch_id'] == j1['data']['batch_id']
        assert '本项目已导入该文件' in j2['data']['msg']

    def test_upload_bad_project(self, app, tmp_path):
        """项目不存在 → 拒绝。"""
        xlsx = str(tmp_path / "bad.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [['110101194501011240', '2026-08', 100]])
        r = self._upload(app, self._client(app), 9999, xlsx)
        assert r.get_json()['success'] is False

    def test_report_endpoint(self, app, tmp_path):
        """结果报告端点返回统计 + 失败行脱敏。"""
        pid = _make_project(app)
        idc = '110101194501011241'
        _make_resident(app, '报告甲', _years_ago(81), idc, status='deceased')
        xlsx = str(tmp_path / "rep.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [[idc, '2026-08', 100]])
        batch_id = _make_batch(app, pid, xlsx)

        # 同步跑核心逻辑，再查报告端点
        from utils.subsidy_service import run_subsidy_batch
        with app.app_context():
            run_subsidy_batch('dummy', batch_id)

        r = self._client(app).get(f'/subsidy/api/record/import-report/{batch_id}')
        j = r.get_json()
        assert j['success']
        d = j['data']
        assert d['total_rows'] == 1
        assert d['fail_rows'] == 1
        assert d['errors'][0]['id_card_masked'] == '110101********1241'

    def test_export_failed_endpoint(self, app, tmp_path):
        """失败行导出：token 通道 302 → 一次性令牌直出 xlsx（108 对齐 106 §7.1）。"""
        pid = _make_project(app)
        idc = '110101194501011242'
        _make_resident(app, '导出甲', _years_ago(81), idc, status='deceased')
        xlsx = str(tmp_path / "exp.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [[idc, '2026-08', 100]])
        batch_id = _make_batch(app, pid, xlsx)

        from utils.subsidy_service import run_subsidy_batch
        with app.app_context():
            run_subsidy_batch('dummy', batch_id)

        client = self._client(app)
        r = client.get(f'/subsidy/api/record/import-export-failed/{batch_id}')
        assert r.status_code == 302
        assert '/report/download/' in r.headers.get('Location', '')
        r2 = client.get(r.headers['Location'], follow_redirects=False)
        assert r2.status_code == 200
        assert r2.data.startswith(b'PK')  # xlsx 文件头
        assert idc.encode() not in r2.data, '导出不得含明文身份证'

    def test_mark_aborted_stale(self, app):
        """108 幂等三件套：残留 running 批次标 aborted（aborted 可重新 execute）。"""
        pid = _make_project(app)
        batch_id = _make_batch(app, pid, None, status='running')
        with app.app_context():
            from utils.subsidy_service import mark_aborted_stale
            from models import SubsidyBatchImport
            assert mark_aborted_stale() == 1
            assert SubsidyBatchImport.query.filter_by(batch_id=batch_id).first().status == 'aborted'
            # 幂等：再跑一遍无残留
            assert mark_aborted_stale() == 0

    def test_upload_permission_403(self, app, tmp_path):
        """无 import_batch 权限 → 403。"""
        from models import db, Role, User
        with app.app_context():
            role = Role(name='无导入权限T145', permissions=json.dumps({'subsidy.add': True}, ensure_ascii=False))
            db.session.add(role)
            db.session.flush()
            user = User(username='普通用户T145', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            uid = user.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        pid = _make_project(app)
        xlsx = str(tmp_path / "perm.xlsx")
        _write_xlsx(xlsx, ['身份证号', '发放期间', '金额'], [['110101194501011243', '2026-08', 100]])
        r = self._upload(app, client, pid, xlsx)
        assert r.status_code == 403


# ============ 108 R-108-06：app.py 启动钩子结构断言（防误删/取单边） ============
def test_app_startup_four_batch_cleanup_hooks_structure():
    """四段批量清理钩子各恰 1 处，且全部位于启动守卫内。

    守护对象：居民（import_engine，034）／选举（election_service，105）／
    软表（softtable_batch，107）／补贴（subsidy_service，108）。
    合并 app.py 时若整体取 ours/theirs 会静默丢失一侧，本断言即防该回归。
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')
    checks = [
        'from utils.import_engine import mark_aborted_stale',
        'from utils.election_service import mark_aborted_stale',
        'from utils.softtable_batch import mark_aborted_stale',
        'from utils.subsidy_service import mark_aborted_stale',
    ]
    for ref in checks:
        assert src.count(ref) == 1, f'{ref} 应恰出现 1 次（防合并取单边）'
    guard = re.search(r'^\s*if not skip_startup_hooks:', src, re.M)
    assert guard is not None, '未找到启动守卫 if not skip_startup_hooks:'
    for ref in checks:
        assert src.index(ref) > guard.start(), f'{ref} 必须在启动守卫内'
