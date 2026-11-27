# -*- coding: utf-8 -*-
"""tests/test_105_election_voter_import.py — 105 选民名册批量导入（ADR-030）

覆盖（docs/tasks/105-election-voter-batch-import.md 验收标准 1~8）：
A. 字段目录：恰好 9 项、顺序与显示名逐字固定；模板端点只出这 9 列
B. 状态门控：上传与执行各校验一次 check_voter_editable（非「选民登记」→ 409）
C. 异步批次：import-execute 建 A1 任务并提交 run_async（task_id 返回）
D. 成功/失败分离：成功落 election_voter，失败只进暂存表；total = success + fail
E. overwrite 语义：同届次同人命中即覆盖，空单元格保留库中原值
F. 失败行导出：token 通道 + 身份证打码（无明文）
G. 幂等：同文件重复上传返回既有批次；running 批次启动后标 aborted
H. 权限：BLUEPRINT_MODULES 归类 + 无模块位 403 + 未登录 302
I. 只读态：非「选民登记」届次页面渲染只读说明
J. 迁移 m042：两表 + idx_* 索引，重复执行幂等
"""
import io
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import election_field_catalog as catalog

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ID_A = '110101199001011234'
ID_B = '110101199001011235'
ID_BAD = '11010119900101123'          # 17 位

TEAM_HEADERS = catalog.template_headers()


# ------------------------------------------------------------------ 夹具/工具

@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('105-election-import', salt=generate_salt())
    return application


def _login(client, user_id=1, username='admin'):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _make_role_user(app, username, permissions):
    with app.app_context():
        from models import Role, User, db
        role = Role(name='role_%s' % username, description='105 用例',
                    permissions=json.dumps(permissions))
        db.session.add(role)
        db.session.commit()
        user = User(username=username, role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        return user.id


def _make_round(app, status='选民登记'):
    """直接建届次（避免状态机前置依赖，聚焦导入面）。"""
    with app.app_context():
        from models import ElectionRound, db
        today = date.today()
        rnd = ElectionRound(
            round_name='105 批量导入测试届', start_date=today,
            end_date=today + timedelta(days=365 * 3),
            register_start=today - timedelta(days=7),
            register_end=today + timedelta(days=7),
            vote_date=today + timedelta(days=14), status=status, remark='')
        db.session.add(rnd)
        db.session.commit()
        return rnd.id


def _xlsx(headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _row(name='张三', id_card=ID_A, group='一组', status='已登记',
         is_out='否', proxy='', phone='13800000001', mobile_box='否', remark=''):
    """按目录顺序（姓名/身份证号/所在组/登记状态/是否外出/委托受托人/联系电话/是否票箱/备注）。"""
    return [name, id_card, group, status, is_out, proxy, phone, mobile_box, remark]


def _upload(client, rid, headers, rows, filename='voters.xlsx'):
    return client.post('/election/api/rounds/%d/voters/import-upload' % rid,
                       data={'file': (_xlsx(headers, rows), filename)},
                       content_type='multipart/form-data')


def _batch_id(client, rid, rows):
    resp = _upload(client, rid, TEAM_HEADERS, rows)
    body = resp.get_json()
    assert body['success'] is True, (body, resp.status_code)
    return body['data']['batch_id']


def _run_batch(app, batch_id):
    """同步直调后台处理函数（避开线程时序，test_t14 同款）。"""
    with app.app_context():
        from models import db
        from utils.async_tasks import create_task
        from utils.election_service import run_election_batch
        task = create_task('election_voter_batch', '1', '105 测试')
        db.session.commit()
        run_election_batch(task.task_id, batch_id)
        return task.task_id


def _report(client, rid, batch_id):
    return client.get('/election/api/rounds/%d/voters/import-report/%s' % (rid, batch_id)).get_json()


# ------------------------------------------------------------------ A. 字段目录

class TestFieldCatalog:
    def test_exactly_nine_in_fixed_order(self):
        assert len(catalog.FIELDS) == 9
        assert catalog.FIELD_LABELS == ('姓名', '身份证号', '所在组', '登记状态', '是否外出',
                                        '委托受托人', '联系电话', '是否票箱', '备注')
        assert catalog.FIELD_KEYS == ('name', 'id_card', 'group_name', 'register_status',
                                      'is_out', 'proxy_name', 'phone', 'mobile_box', 'remark')
        assert len(set(catalog.FIELD_KEYS)) == 9, '目录 key 不得重复'

    def test_template_endpoint_only_catalog_headers(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        resp = client.get('/election/api/rounds/%d/voters/template' % rid)
        assert resp.status_code == 200
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(resp.data)).active
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        assert header == list(TEAM_HEADERS), '模板只放目录 9 列，且不含序号/说明列'

    def test_map_headers_reports_missing(self):
        mapping, missing = catalog.map_headers(['姓名', '身份证号', '未知列'])
        assert mapping == {'name': '姓名', 'id_card': '身份证号'}
        assert '所在组' in missing and '备注' in missing


# ------------------------------------------------------------------ B. 状态门控

class TestStatusGate:
    def test_upload_and_execute_rejected_outside_register_status(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app, status='投票')
        up = _upload(client, rid, TEAM_HEADERS, [_row()])
        assert up.status_code == 409, up.get_data(as_text=True)   # 上传时校验
        ex = client.post('/election/api/rounds/%d/voters/import-execute' % rid,
                         data={'batch_id': 'whatever'})
        assert ex.status_code == 409, ex.get_data(as_text=True)   # 执行时校验

    def test_execute_rechecks_status_after_queue(self, app):
        """排队期间届次离开「选民登记」→ 执行时二次校验拒绝且不落库。"""
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        batch_id = _batch_id(client, rid, [_row()])
        with app.app_context():
            from models import ElectionRound, db
            ElectionRound.query.get(rid).status = '投票'
            db.session.commit()
        _run_batch(app, batch_id)
        with app.app_context():
            from models import ElectionVoter
            assert ElectionVoter.query.filter_by(round_id=rid).count() == 0
        assert _report(client, rid, batch_id)['data']['status'] == 'failed'


# ------------------------------------------------------------------ B2. 上传契约扩展（2026-11-19：.xlsx / .xls / .csv）

class TestUploadContract:
    """契约扩展：.csv 端到端真导入（标准库解析，编码自适应）；
    .xls 接受上传、解析失败优雅降级（批次 failed 不崩）；未知扩展上传即拒。"""

    def test_upload_rejects_unknown_extension(self, app):
        rid = _make_round(app)
        client = app.test_client()
        _login(client)
        resp = client.post('/election/api/rounds/%d/voters/import-upload' % rid,
                           data={'file': (io.BytesIO(b'x'), 'voters.txt')},
                           content_type='multipart/form-data')
        body = resp.get_json()
        assert body['success'] is False, (body, resp.status_code)
        assert '.xlsx' in body['msg'] and '.csv' in body['msg']

    def test_csv_import_end_to_end(self, app):
        rid = _make_round(app)
        client = app.test_client()
        _login(client)
        # 带 BOM 的 UTF-8 CSV（模拟 Excel「另存为 CSV」口径，验证 utf-8-sig 解码）
        csv_line = '姓名,身份证号,所在组,登记状态,是否外出,委托受托人,联系电话,是否票箱,备注'
        csv_data = '张三,%s,一组,已登记,否,,13800000001,否,CSV导入' % ID_A
        csv_bytes = (csv_line + '\n' + csv_data + '\n').encode('utf-8-sig')
        up = client.post('/election/api/rounds/%d/voters/import-upload' % rid,
                         data={'file': (io.BytesIO(csv_bytes), 'voters.csv')},
                         content_type='multipart/form-data')
        body = up.get_json()
        assert body['success'] is True, (body, up.status_code)
        batch_id = body['data']['batch_id']
        _run_batch(app, batch_id)
        report = _report(client, rid, batch_id)['data']
        assert report['status'] == 'done', report
        assert report['total_rows'] == 1 and report['success_rows'] == 1, report
        with app.app_context():
            from models import ElectionVoter
            v = ElectionVoter.query.filter_by(round_id=rid).one()
            assert v.name == '张三'
            assert v.id_card_hash, '加密管线照常'

    def test_xls_upload_accepted_and_parse_failure_graceful(self, app):
        rid = _make_round(app)
        client = app.test_client()
        _login(client)
        # 测试环境无法构造真 .xls 二进制（xlrd 只读）：以伪二进制验证「上传放行 +
        # 执行期解析失败优雅降级（批次 failed，不崩任务）」的降级链路
        up = client.post('/election/api/rounds/%d/voters/import-upload' % rid,
                         data={'file': (io.BytesIO(b'not-a-real-xls'), 'voters.xls')},
                         content_type='multipart/form-data')
        body = up.get_json()
        assert body['success'] is True, (body, up.status_code)
        batch_id = body['data']['batch_id']
        _run_batch(app, batch_id)
        report = _report(client, rid, batch_id)['data']
        assert report['status'] == 'failed', report


# ------------------------------------------------------------------ C. 异步批次

class TestAsyncBatch:
    def test_execute_creates_task_and_schedules_runner(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        batch_id = _batch_id(client, rid, [_row()])

        calls = {}
        import utils.async_tasks as async_tasks
        monkeypatch.setattr(async_tasks, 'run_async',
                            lambda *a, **kw: calls.setdefault('args', (a, kw)))
        resp = client.post('/election/api/rounds/%d/voters/import-execute' % rid,
                           data={'batch_id': batch_id})
        body = resp.get_json()
        assert body['success'] is True and body['data']['task_id']
        assert calls, '执行端点必须提交 A1 后台任务'
        args, kwargs = calls['args']
        assert args[0] == 'election_voter_batch' and kwargs['batch_id'] == batch_id

        status = client.get('/election/api/rounds/%d/voters/import-status/%s'
                            % (rid, body['data']['task_id'])).get_json()
        assert status['success'] and status['data']['status'] == 'pending'

    def test_duplicate_execute_conflicts(self, app, monkeypatch):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        batch_id = _batch_id(client, rid, [_row()])
        import utils.async_tasks as async_tasks
        monkeypatch.setattr(async_tasks, 'run_async', lambda *a, **kw: None)
        assert client.post('/election/api/rounds/%d/voters/import-execute' % rid,
                           data={'batch_id': batch_id}).status_code == 200
        _run_batch(app, batch_id)                                     # 置 done
        again = client.post('/election/api/rounds/%d/voters/import-execute' % rid,
                            data={'batch_id': batch_id})
        assert again.status_code == 409, '已完成批次不得重跑'


# ------------------------------------------------------------------ D. 成功/失败分离

class TestSeparation:
    def test_success_and_failure_split_reconciles(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        rows = [
            _row(name='张三', id_card=ID_A),
            _row(name='李四', id_card=ID_B, status='未登记'),
            _row(name='', id_card='110101199001011236'),          # 缺姓名
            _row(name='王五', id_card=ID_BAD),                    # 17 位
            _row(name='赵六', id_card='110101199001011237', status='不存在状态'),
            _row(name='孙七', id_card='110101199001011238',
                 status='委托投票', proxy=''),                     # 委托无受托人
        ]
        batch_id = _batch_id(client, rid, rows)
        _run_batch(app, batch_id)

        data = _report(client, rid, batch_id)['data']
        assert data['status'] == 'done'
        assert data['total_rows'] == 6
        assert data['success_rows'] == 2 and data['fail_rows'] == 4
        assert data['success_rows'] + data['fail_rows'] == data['total_rows'], '报告须对账'
        assert len(data['errors']) == 4
        for err in data['errors']:
            assert err['row_no'] >= 2 and err['reason']

        with app.app_context():
            from models import ElectionVoter
            names = {v.name for v in ElectionVoter.query.filter_by(round_id=rid).all()}
        assert names == {'张三', '李四'}, '失败行不得写入业务表'

    def test_failed_rows_are_masked(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        batch_id = _batch_id(client, rid, [_row(name='', id_card=ID_A)])
        _run_batch(app, batch_id)
        with app.app_context():
            from models import ElectionBatchError
            err = ElectionBatchError.query.filter_by(batch_id=batch_id).first()
        assert err is not None
        assert ID_A not in (err.id_card_masked or ''), '失败行身份证必须打码'
        assert err.id_card_masked.startswith('110101') and err.id_card_masked.endswith('1234')


# ------------------------------------------------------------------ E. overwrite 语义

class TestOverwrite:
    def test_second_import_updates_instead_of_duplicates(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)

        first = _batch_id(client, rid, [
            _row(name='张三', id_card=ID_A, group='一组', phone='13800000001')])
        _run_batch(app, first)

        second = _batch_id(client, rid, [
            _row(name='张三改', id_card=ID_A, group='', status='', is_out='',
                 proxy='', phone='', mobile_box='', remark='')])
        _run_batch(app, second)

        with app.app_context():
            from models import ElectionVoter
            voters = ElectionVoter.query.filter_by(round_id=rid).all()
            assert len(voters) == 1, '同届次同人必须覆盖，不得新增'
            v = voters[0]
            assert v.name == '张三改', '非空字段覆盖'
            assert v.group_name == '一组', '空单元格保留库中原值（所在组）'
            assert v.register_status == '已登记', '空单元格保留库中原值（登记状态）'

    def test_same_file_duplicate_upload_returns_existing_batch(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        rows = [_row()]
        first = _upload(client, rid, TEAM_HEADERS, rows).get_json()
        dup = _upload(client, rid, TEAM_HEADERS, rows).get_json()
        assert first['data']['batch_id'] == dup['data']['batch_id']
        assert dup['data'].get('duplicate') is True

    def test_id_card_encrypted_and_hash_unique(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        batch_id = _batch_id(client, rid, [_row(id_card=ID_A)])
        _run_batch(app, batch_id)
        with app.app_context():
            from models import ElectionVoter
            from utils.crypto import sha256_hash
            v = ElectionVoter.query.filter_by(round_id=rid).first()
        assert v.id_card_enc and v.id_card_enc.startswith('gAAAA'), '身份证必须走 R12 加密'
        assert v.id_card_hash == sha256_hash(ID_A), 'hash 供唯一/检索'
        assert v.resident_hash == sha256_hash(ID_A), 'V-011 派生 hash'


# ------------------------------------------------------------------ F. 失败行导出

class TestFailedExport:
    def test_export_failed_rows_via_token_channel(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        batch_id = _batch_id(client, rid, [
            _row(name='张三', id_card=ID_A),
            _row(name='', id_card=ID_B, status='未登记'),
        ])
        _run_batch(app, batch_id)
        resp = client.get('/election/api/rounds/%d/voters/import-export-failed/%s'
                          % (rid, batch_id))
        body = resp.get_json()
        assert body['success'] is True, (body, resp.status_code)
        assert body['data']['download_url'].startswith('/report/download/')

        xlsx = client.get(body['data']['download_url'])
        assert xlsx.status_code == 200
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(xlsx.data)).active
        rows = [[c.value for c in r] for r in ws.iter_rows()]
        assert rows[0] == ['行号', '身份证(脱敏)', '失败原因']
        flat = str(rows)
        assert ID_B not in flat, '导出不得含明文身份证'
        assert '110101********1235' in flat


# ------------------------------------------------------------------ G. 幂等 / 中断

class TestIdempotency:
    def test_reupload_then_reimport_is_idempotent(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        batch_id = _batch_id(client, rid, [_row()])
        _run_batch(app, batch_id)
        _run_batch(app, batch_id)          # done 批次重跑直接返回
        with app.app_context():
            from models import ElectionVoter
            assert ElectionVoter.query.filter_by(round_id=rid).count() == 1

    def test_mark_aborted_stale(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app)
        batch_id = _batch_id(client, rid, [_row()])
        with app.app_context():
            from models import ElectionBatchImport, db
            batch = ElectionBatchImport.query.filter_by(batch_id=batch_id).first()
            batch.status = 'running'
            db.session.commit()
            from utils.election_service import mark_aborted_stale
            assert mark_aborted_stale() >= 1
            db.session.expire_all()
            assert ElectionBatchImport.query.filter_by(batch_id=batch_id).first().status == 'aborted'


# ------------------------------------------------------------------ H. 权限

class TestPermissions:
    def test_blueprint_module_registered(self):
        from utils.permissions import BLUEPRINT_MODULES
        assert BLUEPRINT_MODULES.get('election') == 'election'

    def test_without_election_module_forbidden(self, app):
        uid = _make_role_user(app, 'z105_none', {'disabled.view': True})
        client = app.test_client()
        _login(client, uid, 'z105_none')
        rid = _make_round(app)
        assert client.get('/election/rounds/%d/voters' % rid).status_code == 403
        assert client.get('/election/api/rounds/%d/voters/template' % rid).status_code == 403
        assert _upload(client, rid, TEAM_HEADERS, [_row()]).status_code == 403

    def test_anonymous_redirected(self, app):
        client = app.test_client()
        rid = _make_round(app)
        resp = client.get('/election/rounds/%d/voters' % rid)
        assert resp.status_code == 302 and '/auth/login' in resp.headers.get('Location', '')


# ------------------------------------------------------------------ I. 只读态

class TestReadOnlyHint:
    def test_hint_rendered_when_round_not_editable(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app, status='已完成')
        html = client.get('/election/rounds/%d/voters' % rid).get_data(as_text=True)
        assert 'id="readonlyHint"' in html and '名册只读' in html

    def test_no_hint_in_register_status(self, app):
        client = app.test_client()
        _login(client)
        rid = _make_round(app, status='选民登记')
        html = client.get('/election/rounds/%d/voters' % rid).get_data(as_text=True)
        assert 'id="readonlyHint"' not in html


# ------------------------------------------------------------------ J. 迁移 m042

class TestMigration:
    def test_m042_idempotent(self, tmp_path):
        sql_path = os.path.join(_ROOT, 'scripts', 'migrations', 'm042_election_batch_import.sql')
        with open(sql_path, encoding='utf-8') as fh:
            sql = fh.read()
        db_path = str(tmp_path / 'm042.db')
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE election_round (id INTEGER PRIMARY KEY)')
        conn.executescript(sql)
        conn.executescript(sql)          # 二次执行必须幂等
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        conn.close()
        assert {'election_batch_import', 'election_batch_error'} <= tables
        for idx in ('idx_election_batch_import_round', 'idx_election_batch_import_hash',
                    'idx_election_batch_error_batch'):
            assert idx in indexes
