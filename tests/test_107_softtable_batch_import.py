# -*- coding: utf-8 -*-
"""tests/test_107_softtable_batch_import.py — 107 软表批量导入异步分批（模式 A｜ADR-031）

覆盖（docs/tasks/107-softtable-batch-import.md 验收标准 + 106 §9 检查表）：
A. 异步分批：execute 建 A1 任务（task_type=softtable_batch）；runner 经门面分批入库；
   进度单调递增至 100；批次 done；对账 total_rows = success_rows + fail_rows
B. 失败行分离：失败只进 softtable_batch_error（仅 行号/列名/原因，零原值），不入
   dynamic_record；导出走 write_export_xlsx token 通道，导出无身份明文
C. 幂等三件套：同文件同作用域（module_key+table_code+source_hash）重复上传返回既有批次；
   0 行入库的失败批次可整批重进（清旧失败行、起新任务）；已入库的失败批次 409 拒绝；
   mark_aborted_stale 把残留 running 标 aborted
D. 门面边界：跨 module_key 的 code 一律按不存在处理（runner 置 failed，零落库）
E. 加密形态：identity 字段落库 Fernet 密文 + digest 指纹（与同步路径同口径）
F. 同步路径不回归：api_upload → api_meta（下发 async_min_rows）→ api_create 小表照常；
   同步与异步共用同一套字段规格校验（normalize_fields 在两路径同样生效）
G. 权限归类（ADR-016）：新端点挂 dynatable 蓝图；未登录 302；
   execute→add / status/report→view / export-failed→export（action_for_request 实测）
H. 迁移 m045：两表 + idx_* 索引，重复执行幂等
I. 2 万行口径：MAX_LIST_ROWS 截断时 api_records 返回 truncated + total_records（不静默失真）
"""
import io
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ID_OK = '110101199001011234'

FIELDS = [
    {'code': 'f00', 'display_name': '姓名', 'data_type': 'text', 'dict_category_code': None,
     'sensitivity': 'none', 'is_required': True, 'sort_order': 0},
    {'code': 'f01', 'display_name': '身份证号', 'data_type': 'text', 'dict_category_code': None,
     'sensitivity': 'identity', 'is_required': True, 'sort_order': 1},
    {'code': 'f02', 'display_name': '金额', 'data_type': 'number', 'dict_category_code': None,
     'sensitivity': 'amount', 'is_required': False, 'sort_order': 2},
    {'code': 'f03', 'display_name': '日期', 'data_type': 'date', 'dict_category_code': None,
     'sensitivity': 'none', 'is_required': False, 'sort_order': 3},
]


# ------------------------------------------------------------------ 夹具/工具

@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('107-softtable-batch', salt=generate_salt())
    return application


def _login(client, user_id=1, username='admin'):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _xlsx(rows, headers=('姓名', '身份证号', '金额', '日期')):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(list(headers))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _good_row(i=0):
    return ['张三%d' % i, _ID_OK, '%d.50' % (i + 1), '2026-01-%02d' % (i % 28 + 1)]


def _upload(client, buf, filename='roster.xlsx'):
    buf.seek(0)
    resp = client.post('/dynatable/api/upload',
                       data={'file': (buf, filename)},
                       content_type='multipart/form-data')
    body = resp.get_json()
    assert body['success'] is True, (body, resp.status_code)
    return body['data']


def _execute(client, token, code):
    """提交异步导入（run_async 由调用方 monkeypatch 拦截）。"""
    payload = {'token': token, 'code': code, 'name': '台账 %s' % code,
               'module_key': 'dynatable', 'group_name': '', 'description': '',
               'fields': FIELDS}
    return client.post('/dynatable/api/import-execute', data=json.dumps(payload),
                       content_type='application/json')


def _run_batch(app, batch_id, task_type='softtable_batch'):
    """同步直调后台处理函数（避开线程时序，test_105 同款）。"""
    with app.app_context():
        from models import db
        from utils.async_tasks import create_task
        from utils.softtable_batch import run_softtable_batch
        task = create_task(task_type, '1', '107 测试')
        db.session.commit()
        run_softtable_batch(task.task_id, batch_id)
        return task.task_id


def _records_of(app, code):
    with app.app_context():
        from models import DynamicRecord, DynamicTable
        table = DynamicTable.query.filter_by(code=code).first()
        if table is None:
            return 0, []
        recs = DynamicRecord.query.filter_by(table_id=table.id).all()
        return len(recs), [r.data for r in recs]


# ------------------------------------------------------------------ A. 异步分批

class TestAsyncBatch:
    def test_execute_creates_task_and_schedules_runner(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        meta = _upload(client, _xlsx([_good_row()]))
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        resp = _execute(client, meta['token'], 'exec_t1')
        body = resp.get_json()
        assert body['success'] is True, (body, resp.status_code)
        assert body['data']['task_id'] and body['data']['batch_id']

        status = client.get('/dynatable/api/import-status/%s'
                            % body['data']['task_id']).get_json()
        assert status['success'] and status['data']['status'] == 'pending'
        # 台账元数据已在 execute 同步建好（schema-on-import，facade.create_table）
        with app.app_context():
            from models import DynamicTable
            assert DynamicTable.query.filter_by(code='exec_t1').first() is not None

    def test_runner_batches_import_and_reconciles(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        rows = [_good_row(i) for i in range(7)] + [
            ['王五', _ID_OK, 'abc', '2026-01-03'],            # 「金额」不是数字 → 失败行
            ['', _ID_OK, '2.00', '2026-01-04'],               # 姓名必填空 → 失败行
        ]
        meta = _upload(client, _xlsx(rows))
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        body = _execute(client, meta['token'], 'batch_t1').get_json()
        batch_id = body['data']['batch_id']

        updates = []
        import utils.async_tasks as async_tasks
        orig_update = async_tasks._update

        def rec_update(task_id, **kw):
            updates.append(kw)
            orig_update(task_id, **kw)

        monkeypatch.setattr(async_tasks, '_update', rec_update)
        task_id = _run_batch(app, batch_id)

        with app.app_context():
            from models import SofttableBatchImport
            from utils.async_tasks import get_task
            batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
            assert batch.status == 'done'
            assert batch.total_rows == len(rows)
            assert batch.success_rows == len(rows) - 2      # 2 失败行
            assert batch.fail_rows == 2
            assert batch.finished_at is not None
            task = get_task(task_id)
            assert task.status == 'done' and task.progress == 100
            assert '成功 %d' % batch.success_rows in task.result_summary
            # 进度单调递增（分批回执 + 终态 100）
            progresses = [u['progress'] for u in updates if 'progress' in u]
            assert progresses == sorted(progresses), progresses
            assert progresses[-1] == 100

        # 对账：total = success + fail（报告端点同口径）
        report = client.get('/dynatable/api/import-report/%s' % batch_id).get_json()
        assert report['success'] is True
        d = report['data']
        assert d['total_rows'] == d['success_rows'] + d['fail_rows'] == len(rows)
        assert len(d['errors']) == 2

    def test_status_endpoint_404_unknown_task(self, app):
        client = app.test_client()
        _login(client)
        assert client.get('/dynatable/api/import-status/no-such').status_code == 404
        assert client.get('/dynatable/api/import-report/no-such').status_code == 404
        assert client.get('/dynatable/api/import-export-failed/no-such').status_code == 404


# ------------------------------------------------------------------ B. 失败行

class TestFailedRows:
    def test_failed_rows_only_in_staging_and_export_masked(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        rows = [_good_row(i) for i in range(3)] + [
            ['赵六', _ID_OK, 'x-y-z', '2026-01-05'],          # 金额非法 → 失败行
        ]
        meta = _upload(client, _xlsx(rows))
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        batch_id = _execute(client, meta['token'], 'fail_t1')\
            .get_json()['data']['batch_id']
        _run_batch(app, batch_id)

        # 失败行只进暂存表（零原值），不入 dynamic_record
        with app.app_context():
            from models import DynamicRecord, DynamicTable, SofttableBatchError
            table = DynamicTable.query.filter_by(code='fail_t1').first()
            assert DynamicRecord.query.filter_by(table_id=table.id).count() == 3
            errs = SofttableBatchError.query.filter_by(batch_id=batch_id).all()
            assert len(errs) == 1
            assert errs[0].row_no == 4                        # 第 4 数据行（与同步路径同口径）
            blob = json.dumps([e.field_name for e in errs] + [e.reason for e in errs],
                              ensure_ascii=False)
            assert _ID_OK not in blob, '失败行暂存绝不落任何行原值'

        # 报告对账
        report = client.get('/dynatable/api/import-report/%s' % batch_id).get_json()['data']
        assert report['success_rows'] + report['fail_rows'] == report['total_rows'] == 4

        # 导出：token 通道；内容只有 行号/列名/原因，无身份明文
        resp = client.get('/dynatable/api/import-export-failed/%s' % batch_id)
        body = resp.get_json()
        assert body['success'] is True, body
        assert body['data']['download_url'].startswith('/report/download/')
        dl = client.get(body['data']['download_url'])
        assert dl.status_code == 200
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(dl.data)).active
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        assert header == ['数据行号', '出错列', '失败原因']
        all_text = json.dumps([[c.value for c in row] for row in ws.iter_rows()],
                              ensure_ascii=False, default=str)
        assert _ID_OK not in all_text, '导出文件不得含身份明文'


# ------------------------------------------------------------------ C. 幂等三件套

class TestIdempotency:
    def test_duplicate_upload_same_scope_returns_existing_batch(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        # 同一字节两次上传（保证 sha256 一致；请求结束 Werkzeug 会关闭流，各自用新 BytesIO）
        raw = _xlsx([_good_row(), _good_row(1)]).getvalue()
        meta1 = _upload(client, io.BytesIO(raw))
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        first = _execute(client, meta1['token'], 'dup_t1').get_json()
        batch_id = first['data']['batch_id']
        _run_batch(app, batch_id)                              # 置 done

        meta2 = _upload(client, io.BytesIO(raw))               # 同字节再上传
        again = _execute(client, meta2['token'], 'dup_t1').get_json()
        assert again['success'] is True
        assert again['data']['duplicate'] is True
        assert again['data']['batch_id'] == batch_id, '同文件同作用域返回既有批次'

        with app.app_context():
            from models import SofttableBatchImport
            assert SofttableBatchImport.query.filter_by(
                module_key='dynatable', table_code='dup_t1').count() == 1, '不新建批次'

    def test_failed_zero_committed_batch_reenters(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        rows = [_good_row(), _good_row(1)]
        buf = _xlsx(rows)
        meta = _upload(client, buf)
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        first = _execute(client, meta['token'], 'retry_t1').get_json()
        batch_id = first['data']['batch_id']

        # 破坏源文件路径 → runner 失败（0 行入库），并制造一条旧失败行痕迹
        with app.app_context():
            from models import SofttableBatchError, SofttableBatchImport, db
            batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
            batch.source_path = '/nonexistent/roster.xlsx'
            db.session.commit()
        _run_batch(app, batch_id)
        with app.app_context():
            from models import SofttableBatchImport, SofttableBatchError
            batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
            assert batch.status == 'failed'
            assert (batch.success_rows or 0) == 0
            db.session.add(SofttableBatchError(batch_id=batch_id, row_no=1,
                                               field_name='旧', reason='上次残留'))
            db.session.commit()

        # 重进：复用批次行、清旧失败行、新 A1 任务
        second = _execute(client, meta['token'], 'retry_t1').get_json()
        assert second['success'] is True
        assert second['data']['batch_id'] == batch_id
        assert second['data']['task_id'] != first['data']['task_id']
        with app.app_context():
            from models import SofttableBatchError
            assert SofttableBatchError.query.filter_by(batch_id=batch_id).count() == 0
        _run_batch(app, batch_id)
        cnt, _datas = _records_of(app, 'retry_t1')
        assert cnt == 2, '重进整批重跑，0 行入库前提下来到全量入库'
        report = client.get('/dynatable/api/import-report/%s' % batch_id).get_json()['data']
        assert report['status'] == 'done'
        assert report['success_rows'] == 2 and report['fail_rows'] == 0

    def test_failed_batch_with_committed_rows_refused_409(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        raw = _xlsx([_good_row(), _good_row(1)]).getvalue()    # 字节快照（流会被请求关闭）
        meta = _upload(client, io.BytesIO(raw))
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        body = _execute(client, meta['token'], 'refuse_t1').get_json()
        batch_id = body['data']['batch_id']
        _run_batch(app, batch_id)                              # done，2 行入库

        with app.app_context():                                # 人为置 failed（模拟中途失败）
            from models import SofttableBatchImport, db
            SofttableBatchImport.query.filter_by(batch_id=batch_id).first().status = 'failed'
            db.session.commit()
        # done 批次源文件已清，重传同文件（同 sha256）触发判重 → 已入库失败批次 409 拒绝
        meta2 = _upload(client, io.BytesIO(raw))
        again = _execute(client, meta2['token'], 'refuse_t1')
        assert again.status_code == 409, again.get_data(as_text=True)
        cnt, _datas = _records_of(app, 'refuse_t1')
        assert cnt == 2, '拒绝后不重复入库'

    def test_mark_aborted_stale_marks_running(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        meta = _upload(client, _xlsx([_good_row()]))
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        body = _execute(client, meta['token'], 'abort_t1').get_json()
        batch_id = body['data']['batch_id']
        with app.app_context():
            from models import SofttableBatchImport, db
            from utils.softtable_batch import mark_aborted_stale
            SofttableBatchImport.query.filter_by(batch_id=batch_id).first().status = 'running'
            db.session.commit()
            assert mark_aborted_stale() == 1
            assert SofttableBatchImport.query.filter_by(batch_id=batch_id)\
                .first().status == 'aborted'
            assert mark_aborted_stale() == 0, '幂等：再次执行不再命中'


# ------------------------------------------------------------------ D. 门面边界

class TestFacadeBoundary:
    def test_cross_module_code_rejected_by_facade(self, app, monkeypatch):
        """跨 module_key 的 code 一律按不存在处理（ADR-027）：runner 置 failed，零落库。"""
        client = app.test_client()
        _login(client)
        meta = _upload(client, _xlsx([_good_row()]))
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        body = _execute(client, meta['token'], 'facade_t1').get_json()
        batch_id = body['data']['batch_id']
        with app.app_context():                                # 篡改批次归属 → 跨模块
            from models import SofttableBatchImport, db
            SofttableBatchImport.query.filter_by(batch_id=batch_id).first().module_key = 'shop'
            db.session.commit()
        task_id = _run_batch(app, batch_id)
        with app.app_context():
            from models import SofttableBatchImport
            from utils.async_tasks import get_task
            batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
            assert batch.status == 'failed'
            assert '归属不符' in get_task(task_id).result_summary
        cnt, _datas = _records_of(app, 'facade_t1')
        assert cnt == 0, '跨模块 code 一律按不存在处理，零落库'


# ------------------------------------------------------------------ E. 加密形态

class TestEncryptedAtRest:
    def test_identity_field_stored_as_ciphertext_with_digest(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        meta = _upload(client, _xlsx([_good_row()]))
        monkeypatch.setattr('utils.async_tasks.run_async', lambda *a, **kw: None)
        batch_id = _execute(client, meta['token'], 'enc_t1')\
            .get_json()['data']['batch_id']
        _run_batch(app, batch_id)

        cnt, datas = _records_of(app, 'enc_t1')
        assert cnt == 1
        row = json.loads(datas[0])
        assert row['f01'] != _ID_OK, 'identity 字段必须 Fernet 密文落库'
        assert row['f00'] == '张三0'                            # 非 identity 明文
        with app.app_context():
            from models import DynamicRecord, DynamicTable
            table = DynamicTable.query.filter_by(code='enc_t1').first()
            rec = DynamicRecord.query.filter_by(table_id=table.id).first()
            digest = json.loads(rec.digest or '{}')
            assert 'f01' in digest and len(digest['f01']) == 64, 'identity 指纹 HMAC-SHA256'
            assert 'f00' not in digest


# ------------------------------------------------------------------ F. 同步路径不回归

class TestSyncPathUnchanged:
    def test_small_file_sync_path_still_works(self, app):
        """api_upload → api_meta（含 async_min_rows 下发）→ api_create 小表路径照常。"""
        client = app.test_client()
        _login(client)
        meta = _upload(client, _xlsx([_good_row(), _good_row(1)]))
        assert meta['row_count'] == 2
        meta_resp = client.get('/dynatable/api/meta/%s' % meta['token']).get_json()
        assert meta_resp['success'] is True
        assert meta_resp['data']['async_min_rows'] == 2000, '阈值单一真源由 api_meta 下发'
        payload = {'token': meta['token'], 'code': 'sync_t1', 'name': '同步小表',
                   'module_key': 'dynatable', 'group_name': '', 'description': '',
                   'fields': FIELDS, 'skip_error_rows': False}
        resp = client.post('/dynatable/api/create', data=json.dumps(payload),
                           content_type='application/json')
        body = resp.get_json()
        assert body['success'] is True, body
        assert body['data']['imported'] == 2
        cnt, _datas = _records_of(app, 'sync_t1')
        assert cnt == 2

    def test_sync_create_still_rejects_bad_rows_without_skip(self, app):
        """同步路径「错误行不跳过则整单拒绝」的既有语义保持不变。"""
        client = app.test_client()
        _login(client)
        rows = [_good_row(), ['钱七', _ID_OK, 'abc', '2026-01-06']]   # 第 2 行金额非法
        meta = _upload(client, _xlsx(rows))
        payload = {'token': meta['token'], 'code': 'sync_t2', 'name': '同步拒绝',
                   'module_key': 'dynatable', 'group_name': '', 'description': '',
                   'fields': FIELDS, 'skip_error_rows': False}
        resp = client.post('/dynatable/api/create', data=json.dumps(payload),
                           content_type='application/json')
        body = resp.get_json()
        assert resp.status_code == 400 and body['success'] is False
        assert body['error_total'] == 1
        cnt, _datas = _records_of(app, 'sync_t2')
        assert cnt == 0, '未跳过错误行时一行都不落库（既有语义）'


# ------------------------------------------------------------------ G. 权限归类（ADR-016）

class TestPermissions:
    NEW_ENDPOINTS = {
        'dynatable.api_import_execute': 'add',
        'dynatable.api_import_status': 'view',
        'dynatable.api_import_report': 'view',
        'dynatable.api_import_export_failed': 'export',
    }

    def test_endpoints_registered_under_dynatable_blueprint(self, app):
        endpoints = {rule.endpoint for rule in app.url_map.iter_rules()
                     if rule.endpoint.startswith('dynatable.api_import')}
        assert endpoints == set(self.NEW_ENDPOINTS), endpoints

    def test_action_mapping_by_view_name(self, app):
        from utils.permissions import action_for_request
        with app.test_request_context('/dynatable/api/import-execute', method='POST'):
            assert action_for_request('dynatable.api_import_execute') == 'add'
        with app.test_request_context('/dynatable/api/import-status/t1', method='GET'):
            assert action_for_request('dynatable.api_import_status') == 'view'
        with app.test_request_context('/dynatable/api/import-report/b1', method='GET'):
            assert action_for_request('dynatable.api_import_report') == 'view'
        with app.test_request_context('/dynatable/api/import-export-failed/b1', method='GET'):
            assert action_for_request('dynatable.api_import_export_failed') == 'export'

    def test_unauthenticated_rejected(self, app):
        """未登录：API 路径经 response_wants_json() 判定返回 401（app.before_request）。"""
        client = app.test_client()
        for path in ('/dynatable/api/import-status/x', '/dynatable/api/import-report/b1',
                     '/dynatable/api/import-export-failed/b1'):
            assert client.get(path).status_code == 401
        assert client.post('/dynatable/api/import-execute', data='{}',
                           content_type='application/json').status_code == 401


# ------------------------------------------------------------------ H. 迁移 m045

class TestMigrationM045:
    M045 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'scripts', 'migrations', 'm045_softtable_batch_import.sql')

    def test_idempotent_double_run_creates_tables_and_indexes(self, tmp_path):
        db_path = tmp_path / 'm045.db'
        script = open(self.M045, encoding='utf-8').read()
        for _ in range(2):                                     # 双跑幂等
            conn = sqlite3.connect(str(db_path))
            conn.executescript(script)
            conn.commit()
            conn.close()
        conn = sqlite3.connect(str(db_path))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        indexes = {r[1] for r in conn.execute('PRAGMA index_list(softtable_batch_import)')
                   .fetchall()}
        err_indexes = {r[1] for r in conn.execute(
            'PRAGMA index_list(softtable_batch_error)').fetchall()}
        conn.close()
        assert {'softtable_batch_import', 'softtable_batch_error'} <= tables
        assert 'idx_softtable_batch_import_scope' in indexes
        assert 'idx_softtable_batch_import_status' in indexes
        assert 'idx_softtable_batch_error_batch' in err_indexes

    def test_model_matches_migration_columns(self, app):
        """create_all 建库与 m045 存量库两条路径 schema 一致（R-093-01 双索引漂移纪律）。"""
        with app.app_context():
            from models import db as _db
            from sqlalchemy import text
            rows = _db.session.execute(text(
                "SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND name IN ('softtable_batch_import','softtable_batch_error')")).fetchall()
        created = dict(rows)
        assert set(created) == {'softtable_batch_import', 'softtable_batch_error'}
        m45 = open(self.M045, encoding='utf-8').read()
        for table in ('softtable_batch_import', 'softtable_batch_error'):
            start = m45.find('CREATE TABLE IF NOT EXISTS %s (' % table)
            mig_cols = self._col_names(m45[start:m45.find(');', start)])
            missing = mig_cols - self._col_names(created[table])
            assert not missing, '%s 缺列 %s' % (table, missing)

    @staticmethod
    def _col_names(ddl):
        """列名集合（两路 DDL 均无括号内逗号类型，朴素切分即可；约束段剔除）。"""
        body = ddl[ddl.find('(') + 1:ddl.rfind(')')]
        names = set()
        for seg in body.split(','):
            tok = seg.strip().split()
            if not tok:
                continue
            name = tok[0].strip('"').strip('`')
            if name.upper() in ('FOREIGN', 'UNIQUE', 'PRIMARY', 'CHECK', 'CONSTRAINT'):
                continue
            names.add(name)
        return names


# ------------------------------------------------------------------ I. 2 万行口径

class TestListRowsTruncation:
    def test_truncation_flag_explicit_not_silent(self, app, monkeypatch):
        """超过列表上限：truncated=true + total_records=真实行数（078 遗留 P2 闭环）。"""
        client = app.test_client()
        _login(client)
        with app.app_context():
            from models import db
            from utils import roster_facade as facade
            fields = [{'code': 'f00', 'display_name': '名称', 'data_type': 'text',
                       'dict_category_code': None, 'sensitivity': 'none',
                       'is_required': False, 'sort_order': 0}]
            table = facade.create_table('dynatable', 'trunc_t1', '截断口径',
                                        fields=fields, created_by='tester')
            facade.import_records(table, fields,
                                  [['行%d' % i] for i in range(7)],
                                  skip_error_rows=True, created_by='tester')
            db.session.expire_all()
        monkeypatch.setattr('routes.dynatable.MAX_LIST_ROWS', 5)
        resp = client.get('/dynatable/api/t/trunc_t1/records?page=1&limit=20')
        body = resp.get_json()
        assert body['success'] is True
        assert body['truncated'] is True, '截断必须显式标记'
        assert body['total_records'] == 7, '真实行数随响应下发'
        assert body['total'] == 5, '筛选在截断集上进行，total 为截断集口径'

    def test_no_truncation_below_limit(self, app):
        client = app.test_client()
        _login(client)
        with app.app_context():
            from models import db
            from utils import roster_facade as facade
            fields = [{'code': 'f00', 'display_name': '名称', 'data_type': 'text',
                       'dict_category_code': None, 'sensitivity': 'none',
                       'is_required': False, 'sort_order': 0}]
            table = facade.create_table('dynatable', 'trunc_t2', '不截断',
                                        fields=fields, created_by='tester')
            facade.import_records(table, fields, [['甲'], ['乙']],
                                  skip_error_rows=True, created_by='tester')
            db.session.expire_all()
        resp = client.get('/dynatable/api/t/trunc_t2/records?page=1&limit=20')
        body = resp.get_json()
        assert body['truncated'] is False
        assert body['total_records'] == 2 and body['total'] == 2
