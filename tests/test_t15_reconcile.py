# -*- coding: utf-8 -*-
"""T15-4 公安/殡葬死亡名单对账单测（Sprint6，reconcile 蓝图）。

覆盖（对应 PRD R5-E3/B4/O3 + 计划稿 §4.1 第 6 条 + M6.0 C3）：
- 导入：缺身份证列拒绝 / 非 xlsx 拒绝 / 同文件幂等返既有任务 / 四分类计数全对 / 结果无明文列
- 处置：登记（转办死亡登记）/ 排除 + 重复拒绝 / 已登记不允许登记
- 导出：temp/exports token 通道 / 脱敏 / 权限 403
- 权限：view/add/edit/export 403 全套
- 季度：首启建任务+待办 / 幂等 / 导入联动季度任务 done + 待办 dismiss
- 清理：purge 置 source_deleted_at / >30 天自动清理
- E22：对账结果已处置构成撤回冲突（交叉验证）
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from test_t15_cascade import (
    _make_app, _admin_client, _make_resident, _make_death_report,
    _make_dibao, _make_project, _make_subsidy_record, _make_tag,
    _confirm, _row, _seed_full, _years_ago, ID_CARD,
)

ID_CARD2 = '110101196201011234'   # 在册未死亡
ID_CARD3 = '110101198001011236'   # 无档案


def _make_xlsx(rows):
    """构造对账名单 xlsx（rows=[(姓名, 身份证, 死亡日期)]）。"""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['姓名', '身份证', '死亡日期'])
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _do_import(client, rows, source='公安', filename='test.xlsx'):
    """导入并轮询 A1 完成，返回 (task_id, resp)。"""
    buf = _make_xlsx(rows)
    r = client.post('/reconcile/import',
                    data={'file': (buf, filename), 'source': source},
                    content_type='multipart/form-data')
    body = r.get_json()
    assert body['success'] is True, body
    task_id = body['data']['task_id']
    a1 = body['data']['a1_task_id']
    for _ in range(40):
        st = client.get(f'/reconcile/api/import-status/{a1}').get_json()['data']['status']
        if st in ('done', 'failed'):
            break
        time.sleep(0.25)
    assert st == 'done', f'A1 任务未完成: {st}'
    return task_id, body


def _result_list(client, task_id, match_type=''):
    url = f'/reconcile/api/task/{task_id}/results'
    if match_type:
        url += '?match_type=' + match_type
    return client.get(url).get_json()['data']


def _op_client(app, perms_extra=None):
    """普通操作员（v3 权限格式，默认仅 reconcile.view；角色名随机防重名）。"""
    import uuid
    from models import db, Role, User
    from werkzeug.security import generate_password_hash
    perms = {'reconcile.view': True, 'schema_version': 3}
    if perms_extra:
        perms.update(perms_extra)
    role_name = f'对账操作员T15-4-{uuid.uuid4().hex[:8]}'
    with app.app_context():
        role = Role(name=role_name, permissions=json.dumps(perms, ensure_ascii=False), is_system=False)
        db.session.add(role)
        db.session.commit()
        u = User(username=f'op_rc4_{uuid.uuid4().hex[:6]}', password_hash=generate_password_hash('x'),
                 role_id=role.id, is_active=True)
        db.session.add(u)
        db.session.commit()
        uid = u.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid
    return client


# ============ 导入与四分类 ============
class TestImport:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _seed(self, app):
        """死亡甲（确认死亡）+ 死亡乙（在册未死亡）。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        assert _confirm(client, seed['drid']).get_json()['success'] is True
        _make_resident(app, '死亡乙', _years_ago(70), ID_CARD2)
        return seed, client

    def test_missing_id_card_column_rejected(self, app):
        """缺身份证列 → 400 拒绝。"""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['姓名', '年龄'])
        ws.append(['张三', 70])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        client = _admin_client(app)
        r = client.post('/reconcile/import',
                        data={'file': (buf, 'bad.xlsx'), 'source': '公安'},
                        content_type='multipart/form-data')
        body = r.get_json()
        assert body['success'] is False
        assert '身份证' in body['msg']

    def test_non_xlsx_rejected(self, app):
        """非 xlsx → 拒绝。"""
        client = _admin_client(app)
        r = client.post('/reconcile/import',
                        data={'file': (io.BytesIO(b'abc'), 'bad.csv'), 'source': '公安'},
                        content_type='multipart/form-data')
        assert r.get_json()['success'] is False

    def test_four_match_types_counts(self, app):
        """四分类计数全对：已登记/疑似未登记/无档案各 1。"""
        seed, client = self._seed(app)
        task_id, _ = _do_import(client, [
            ('死亡甲', ID_CARD, '2026-08-15'),
            ('死亡乙', ID_CARD2, '2026-08-20'),
            ('无名丙', ID_CARD3, '2026-08-22'),
        ])
        det = client.get(f'/reconcile/api/task/{task_id}').get_json()['data']
        assert det['summary'] == {'已登记': 1, '疑似未登记': 1, '无档案': 1, '名单外': 0}
        assert det['status'] == 'done'
        assert det['source'] == '公安'

    def test_source_file_has_no_plaintext_column(self, app):
        """结果无明文身份证列（仅 enc+hash）。"""
        from models.reconcile import ReconcileResult
        seed, client = self._seed(app)
        task_id, _ = _do_import(client, [('无名丙', ID_CARD3, '2026-08-22')])
        with app.app_context():
            r = ReconcileResult.query.filter_by(task_id=task_id).first()
        assert r.id_card_enc and r.id_card_hash
        assert not hasattr(r, 'id_card')  # 无明文列

    def test_duplicate_import_idempotent(self, app):
        """同文件重复导入 → 返回既有任务（source_hash 幂等）。

        注：openpyxl 生成 xlsx 含 zip 时间戳，两次独立生成字节不同；
        用同一 raw bytes 包装新流上传两次以模拟「同一文件」（真实用户重复上传同一文件字节一致）。
        """
        import time as _t
        seed, client = self._seed(app)
        raw = _make_xlsx([('无名丙', ID_CARD3, '2026-08-22')]).getvalue()
        r1 = client.post('/reconcile/import',
                         data={'file': (io.BytesIO(raw), 'test.xlsx'), 'source': '公安'},
                         content_type='multipart/form-data')
        b1 = r1.get_json()
        assert b1['success'] is True, b1
        task_id = b1['data']['task_id']
        a1 = b1['data']['a1_task_id']
        for _ in range(40):
            st = client.get(f'/reconcile/api/import-status/{a1}').get_json()['data']['status']
            if st in ('done', 'failed'):
                break
            _t.sleep(0.25)
        assert st == 'done'
        r2 = client.post('/reconcile/import',
                         data={'file': (io.BytesIO(raw), 'test.xlsx'), 'source': '公安'},
                         content_type='multipart/form-data')
        body2 = r2.get_json()
        assert body2['success'] is True
        assert body2['data']['duplicate'] is True
        assert body2['data']['task_id'] == task_id

    def test_list_outside_roster(self, app):
        """名单外：系统已死亡但名单无 → 全量补集。"""
        seed, client = self._seed(app)
        # 导入名单只含死亡乙（在册）→ 死亡甲（deceased）应进名单外
        task_id, _ = _do_import(client, [('死亡乙', ID_CARD2, '2026-08-20')])
        det = client.get(f'/reconcile/api/task/{task_id}').get_json()['data']
        assert det['summary']['名单外'] == 1
        outside = _result_list(client, task_id, '名单外')
        assert outside[0]['name'] == '死亡甲'


# ============ 处置 ============
class TestHandle:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_register_action_returns_hint(self, app):
        """疑似未登记 → 登记：标记已处置 + 返回转办死亡登记提示。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        _make_resident(app, '死亡乙', _years_ago(70), ID_CARD2)
        task_id, _ = _do_import(client, [('死亡乙', ID_CARD2, '2026-08-20')])
        r = _result_list(client, task_id, '疑似未登记')[0]

        resp = client.post(f'/reconcile/api/result/{r["id"]}/handle-save', data={'action': '登记'})
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['redirect'] == '/death-report/'
        assert body['data']['need_resident'] is False
        from models.reconcile import ReconcileResult
        assert _row(app, ReconcileResult, r['id']).handle_status == '已处置'

    def test_no_archive_register_needs_resident(self, app):
        """无档案 → 登记：提示先补录居民档案。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        task_id, _ = _do_import(client, [('无名丙', ID_CARD3, '2026-08-22')])
        r = _result_list(client, task_id, '无档案')[0]
        resp = client.post(f'/reconcile/api/result/{r["id"]}/handle-save', data={'action': '登记'})
        body = resp.get_json()
        assert body['success'] is True
        assert body['data']['need_resident'] is True

    def test_exclude_and_repeat_rejected(self, app):
        """排除 → 已处置；重复处置拒绝。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        _make_resident(app, '死亡乙', _years_ago(70), ID_CARD2)
        task_id, _ = _do_import(client, [('死亡乙', ID_CARD2, '2026-08-20')])
        r = _result_list(client, task_id, '疑似未登记')[0]

        assert client.post(f'/reconcile/api/result/{r["id"]}/handle-save',
                           data={'action': '排除'}).get_json()['success'] is True
        r2 = client.post(f'/reconcile/api/result/{r["id"]}/handle-save', data={'action': '排除'})
        assert r2.get_json()['success'] is False
        assert '已处置' in r2.get_json()['msg']

    def test_registered_not_registerable(self, app):
        """已登记类 → 不允许登记。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        task_id, _ = _do_import(client, [('死亡甲', ID_CARD, '2026-08-15')])
        r = _result_list(client, task_id, '已登记')[0]
        resp = client.post(f'/reconcile/api/result/{r["id"]}/handle-save', data={'action': '登记'})
        assert resp.get_json()['success'] is False


# ============ 导出与权限 ============
class TestExportAndPerm:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_export_token_download(self, app):
        """导出：token 通道下载成功。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        task_id, _ = _do_import(client, [('死亡甲', ID_CARD, '2026-08-15')])
        resp = client.post(f'/reconcile/api/task/{task_id}/export')
        body = resp.get_json()
        assert body['success'] is True, body
        url = body['data']['download_url']
        assert url.startswith('/report/download/')
        dl = client.get(url)
        assert dl.status_code == 200
        assert 'application/vnd.openxmlformats' in dl.headers.get('Content-Type', '')

    def test_export_masked_without_sensitive(self, app):
        """导出：无 view_sensitive 用户（有 add/export 权限）→ 内容脱敏。

        token 绑定 issue 用户（export_token_store 按 user_id 校验），
        故导入/导出/下载须由同一无敏感权限用户完成。
        """
        seed = _seed_full(app)
        admin = _admin_client(app)
        _confirm(admin, seed['drid'])
        # /report/download/<token> 由全局 before_request 推导 action=export（T17-4 机制）
        client = _op_client(app, {'reconcile.add': True, 'reconcile.export': True,
                                  'report.export': True})
        task_id, _ = _do_import(client, [('死亡甲', ID_CARD, '2026-08-15')])
        resp = client.post(f'/reconcile/api/task/{task_id}/export')
        body = resp.get_json()
        assert body['success'] is True, body
        dl = client.get(body['data']['download_url'])
        assert dl.status_code == 200
        # 打开 xlsx 校验内容脱敏
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(dl.data), data_only=True)
        ws = wb.active
        plain_found = False
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if cell and ID_CARD in str(cell):
                    plain_found = True
        assert plain_found is False

    def test_permission_403_matrix(self, app):
        """权限矩阵：view/add/edit/export 分别校验。"""
        from models.reconcile import ReconcileTask
        seed = _seed_full(app)
        admin = _admin_client(app)
        _confirm(admin, seed['drid'])
        task_id, _ = _do_import(admin, [('死亡甲', ID_CARD, '2026-08-15')])
        r = _result_list(admin, task_id)[0]

        # 无权限用户：全部 403（显式覆盖默认 view 权限为 False）
        no_perm = _op_client(app, {'reconcile.view': False})
        assert no_perm.get('/reconcile/').status_code == 403
        assert no_perm.get('/reconcile/api/tasks').status_code == 403
        assert no_perm.post('/reconcile/import').status_code == 403
        assert no_perm.post(f'/reconcile/api/result/{r["id"]}/handle-save',
                            data={'action': '排除'}).status_code == 403
        assert no_perm.post(f'/reconcile/api/task/{task_id}/export').status_code == 403
        assert no_perm.post(f'/reconcile/task/{task_id}/purge-source').status_code == 403

        # 仅 view：import 403 / export 403 / handle 403
        view_only = _op_client(app, {})
        assert view_only.post('/reconcile/import').status_code == 403
        assert view_only.post(f'/reconcile/api/task/{task_id}/export').status_code == 403
        assert view_only.post(f'/reconcile/api/result/{r["id"]}/handle-save',
                              data={'action': '排除'}).status_code == 403


# ============ 季度计划（B4） ============
class TestQuarter:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_first_launch_creates_quarter_task_and_todo(self, app):
        """季度首启：创建季度计划任务 + 首页待办。"""
        from utils.reconcile_service import ensure_quarter_task, current_quarter
        from models.reconcile import ReconcileTask
        from models.todo import TodoItem
        with app.app_context():
            tid = ensure_quarter_task()
        assert tid is not None
        with app.app_context():
            t = ReconcileTask.query.get(tid)
            assert t.source == '季度计划'
            assert t.quarter == current_quarter()
            assert t.status == 'pending'
            assert t.source_hash is None
            todo = TodoItem.query.filter_by(todo_type='reconcile', related_id=tid).first()
            assert todo is not None
            assert '对账' in todo.title

    def test_quarter_idempotent(self, app):
        """幂等：重复检查不重复建任务。"""
        from utils.reconcile_service import ensure_quarter_task
        from models.reconcile import ReconcileTask
        with app.app_context():
            ensure_quarter_task()
            ensure_quarter_task()
            cnt = ReconcileTask.query.filter_by(source='季度计划').count()
        assert cnt == 1

    def test_import_completes_quarter_task(self, app):
        """导入比对 done → 联动季度任务 done + 待办 dismiss。"""
        from utils.reconcile_service import ensure_quarter_task
        from models.reconcile import ReconcileTask
        from models.todo import TodoItem
        seed = _seed_full(app)
        with app.app_context():
            ensure_quarter_task()
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        _do_import(client, [('死亡甲', ID_CARD, '2026-08-15')])
        with app.app_context():
            q = ReconcileTask.query.filter_by(source='季度计划').first()
            assert q.status == 'done'
            todo = TodoItem.query.filter_by(todo_type='reconcile', related_id=q.id).first()
            assert todo.status == 'dismissed'


# ============ 源名单限期删除（U5，M6.0 C3） ============
class TestSourceCleanup:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_purge_source_sets_deleted_at(self, app):
        """手动删除源名单：文件删除 + source_deleted_at 置位。"""
        from models.reconcile import ReconcileTask
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        task_id, _ = _do_import(client, [('死亡甲', ID_CARD, '2026-08-15')])
        with app.app_context():
            t = ReconcileTask.query.get(task_id)
            fpath = t.import_file
        assert os.path.isfile(fpath)
        resp = client.post(f'/reconcile/task/{task_id}/purge-source')
        assert resp.get_json()['success'] is True
        assert not os.path.isfile(fpath)
        with app.app_context():
            assert ReconcileTask.query.get(task_id).source_deleted_at is not None

    def test_auto_cleanup_over_30_days(self, app):
        """超 30 天源名单自动清理（周期任务）。"""
        from utils.reconcile_service import cleanup_expired_sources
        from models.reconcile import ReconcileTask
        from models import db
        from datetime import datetime, timedelta
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        task_id, _ = _do_import(client, [('死亡甲', ID_CARD, '2026-08-15')])
        with app.app_context():
            t = ReconcileTask.query.get(task_id)
            fpath = t.import_file
            # 回拨 created_at 到 31 天前（update 需 commit 持久化）
            t.created_at = datetime.now() - timedelta(days=31)
            ReconcileTask.query.filter_by(id=task_id).update({'created_at': t.created_at})
            db.session.commit()
            t2 = ReconcileTask.query.get(task_id)
            assert t2.created_at < datetime.now() - timedelta(days=30)
        assert os.path.isfile(fpath)
        with app.app_context():
            n = cleanup_expired_sources()
        assert n == 1
        assert not os.path.isfile(fpath)
        with app.app_context():
            assert ReconcileTask.query.get(task_id).source_deleted_at is not None


# ============ E22 交叉验证（对账引用禁止撤回） ============
class TestE22Cross:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_handled_result_blocks_revoke(self, app):
        """E22：确认后对账结果已处置 → 撤回被禁止（T15-2 联动）。"""
        seed = _seed_full(app)
        client = _admin_client(app)
        _confirm(client, seed['drid'])
        # 导入含死亡甲的名单（已登记类），处置为已处置
        task_id, _ = _do_import(client, [('死亡甲', ID_CARD, '2026-08-15')])
        r = _result_list(client, task_id, '已登记')[0]
        # 已登记不允许登记 → 用排除处置（置已处置）
        resp = client.post(f'/reconcile/api/result/{r["id"]}/handle-save', data={'action': '排除'})
        assert resp.get_json()['success'] is True

        rev = client.post('/death-report/api/revoke', data={'id': seed['drid']})
        body = rev.get_json()
        assert body['success'] is False
        assert '对账' in body['msg']
