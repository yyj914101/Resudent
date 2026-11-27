# -*- coding: utf-8 -*-
"""花名册（Roster）标准蓝图注册工厂（P1-2 + 第一批软表闭环升级）。

独立于 utils/roster_facade.py 门面层，保持门面 API（get_table / create_table /
import_records / list_rows / save_row / export_rows / MODULE_KEYS 等）完全不变。

第一批软表闭环增强：
1. 动态归属软表自动透传 nav_module / nav_active='dt:<code>' / show_back=False，
   以页内胶囊形态与原生业务页单层无缝切换；
2. 首次建表（roster_create）与二次追加导入（append-upload / append-execute）均按
   ASYNC_ROW_THRESHOLD 自动分流同步/异步（复用 utils/softtable_batch.py），
   并暴露 import-status / import-report / import-export-failed 三端点；
3. register_roster_append_routes 可单独供自定义花名册蓝图（如 shop_bp）复用。
"""
import hashlib
import json
import os
import secrets
import uuid

import config
from flask import abort, jsonify, render_template, request, session
from models import SofttableBatchError, SofttableBatchImport, db
from utils import roster_facade as facade
from utils import softtable_engine as eng
from datetime import datetime
from utils.async_tasks import create_task, get_task, is_task_busy, sync_failed_pending_batches
from utils.decorators import login_required
from utils.dynatable_modules import tab_group_for
from utils.helpers import export_excel, json_response, log_operation, write_export_xlsx
from utils.softtable_batch import ASYNC_ROW_THRESHOLD, TASK_TYPE, run_softtable_batch


def _make_temp_helpers(allowed_ext):
    def _temp_dir():
        """上传临时目录（与 dynatable 同款 <TEMP_DIR>/softtable；token 使用后即删）。"""
        path = os.path.join(config.TEMP_DIR, 'softtable')
        os.makedirs(path, exist_ok=True)
        return path

    def _token_file(token):
        """token → (文件路径, 扩展名)；仅接受 hex 字符，防路径穿越。"""
        if not token or not isinstance(token, str) or not all(c in '0123456789abcdef' for c in token):
            return None
        for ext in allowed_ext:
            path = os.path.join(_temp_dir(), token + ext)
            if os.path.exists(path):
                return path, ext
        return None

    def _meta_path(token):
        return os.path.join(_temp_dir(), token + '.meta.json')

    def _meta_name(token):
        """从 meta 取原始文件名（决定解析器分支：xlsx / csv）。"""
        meta_file = _meta_path(token)
        if os.path.exists(meta_file):
            with open(meta_file, encoding='utf-8') as fh:
                return json.load(fh).get('filename', 'upload.xlsx')
        return 'upload.xlsx'

    return _temp_dir, _token_file, _meta_path, _meta_name


def start_softtable_async_batch(*, module_key, table_code, table_name, source_path,
                                source_file, operator, log_module, log_action_title,
                                column_map=None):
    """统一的软表异步分批导入编排（幂等判重 + 0 行失败重进 + 启动 A1 任务）。

    返回 (ok, status_code, payload_dict, msg)。
    """
    with open(source_path, 'rb') as fh:
        source_hash = hashlib.sha256(fh.read()).hexdigest()

    task = None
    dup = (SofttableBatchImport.query
           .filter_by(module_key=module_key, table_code=table_code, source_hash=source_hash)
           .first())
    if dup is not None:
        sync_failed_pending_batches([dup])
    if dup is not None:
        if dup.status in ('failed', 'aborted') and (dup.success_rows or 0) > 0:
            return (False, 409, None,
                    '该批次已有 %d 行入库，整批重跑会产生重复记录；'
                    '请修改文件内容后重新上传' % dup.success_rows)
        if dup.status in ('failed', 'aborted'):
            if is_task_busy(TASK_TYPE):
                return (False, 409, None, '并发互斥：已有软表导入任务正在运行，请稍后重试')
            SofttableBatchError.query.filter_by(batch_id=dup.batch_id).delete()
            dup.status = 'pending'
            dup.fail_rows = 0
            dup.total_rows = 0
            dup.source_path = source_path
            batch = dup
            log_operation(operator, log_module, 'add',
                          '%s重进 %s（%s）' % (log_action_title, table_name, table_code),
                          '前次失败（0 行入库），重试导入')
        else:
            return (True, 200,
                    {'mode': 'async', 'code': table_code, 'batch_id': dup.batch_id,
                     'task_id': dup.task_id, 'duplicate': True, 'status': dup.status},
                    '该文件在此台账已建过导入批次（状态 %s），返回既有批次' % dup.status)
    else:
        if is_task_busy(TASK_TYPE):
            return (False, 409, None, '并发互斥：已有软表导入任务正在运行，请稍后重试')
        task = create_task(TASK_TYPE, operator, '%s %s' % (log_action_title, table_name))
        batch = SofttableBatchImport(
            batch_id=uuid.uuid4().hex[:12], module_key=module_key, table_code=table_code,
            source_file=source_file, source_path=source_path, source_hash=source_hash,
            operator=operator, status='pending', task_id=task.task_id)
        db.session.add(batch)
        db.session.commit()
        log_operation(operator, log_module, 'add',
                      '%s %s（%s）' % (log_action_title, table_name, table_code),
                      '文件 %s，后台分批执行' % source_file)

    if task is None:
        task = create_task(TASK_TYPE, operator, '%s %s（重进）' % (log_action_title, table_name))
        batch.task_id = task.task_id
        db.session.commit()

    from utils.async_tasks import run_async
    accepted = run_async(TASK_TYPE, task.task_id, run_softtable_batch,
                         batch_id=batch.batch_id, column_map=column_map)
    if accepted is False:
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        return (False, 409,
                {'mode': 'async', 'code': table_code, 'batch_id': batch.batch_id, 'task_id': task.task_id},
                '并发互斥：已有软表导入任务正在运行，请稍后重试')
    return (True, 200,
            {'mode': 'async', 'code': table_code, 'batch_id': batch.batch_id, 'task_id': task.task_id},
            '已提交后台分批导入')


def register_roster_append_routes(bp, *, module_key: str, log_module: str,
                                  allowed_ext=('.xlsx', '.xls', '.xlsb', '.csv', '.xlsm'),
                                  max_upload_bytes: int = 20 * 1024 * 1024):
    """注册已有软表的二次批量导入与异步进度/报告/失败行导出 5 个标准端点。"""
    _MODULE_KEY = module_key
    _LOG_MODULE = log_module
    _ALLOWED_EXT = allowed_ext
    _MAX_UPLOAD_BYTES = max_upload_bytes
    _temp_dir, _token_file, _meta_path, _meta_name = _make_temp_helpers(_ALLOWED_EXT)

    def _who():
        return session.get('username', '')

    @bp.route('/roster/<code>/api/append-upload', methods=['POST'])
    @login_required
    def roster_append_upload(code):
        """上传 Excel/CSV 解析并按表头自动匹配已有软表字段（返回列映射预览与异步标记）。"""
        table = facade.get_table(_MODULE_KEY, code)
        if table is None or not facade.is_built(table):
            return json_response(False, '台账不存在或尚未建表'), 404
        raw = request.files.get('file')
        if not raw or not raw.filename:
            return json_response(False, '请选择要上传的 .xlsx / .csv 文件')
        filename = raw.filename
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _ALLOWED_EXT:
            return json_response(False, '仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式文件')
        raw.stream.seek(0, os.SEEK_END)
        size = raw.stream.tell()
        raw.stream.seek(0)
        if size > _MAX_UPLOAD_BYTES:
            return json_response(False, '文件超过 20MB 上限，请拆分后再上传')

        token = secrets.token_hex(16)
        dest = os.path.join(_temp_dir(), token + ext)
        raw.save(dest)
        try:
            with open(dest, 'rb') as fh:
                headers, rows = eng.parse_table_file(filename, fh)
        except eng.SoftTableError as exc:
            if os.path.exists(dest):
                os.remove(dest)
            return json_response(False, str(exc))

        specs = facade.field_specs(table)
        field_matches, unmatched_headers, missing_required = facade.match_append_headers(specs, headers)
        meta = {'filename': filename, 'headers': headers, 'row_count': len(rows),
                'uploaded_by': _who(), 'code': code, 'mode': 'append'}
        with open(_meta_path(token), 'w', encoding='utf-8') as fh:
            json.dump(meta, fh, ensure_ascii=False)
        sample = [[eng._as_text(c) for c in row[:len(headers)]] for row in rows[:5]]
        return json_response(True, '', data={
            'token': token,
            'filename': filename,
            'headers': headers,
            'fields': field_matches,
            'unmatched_headers': unmatched_headers,
            'missing_required': missing_required,
            'row_count': len(rows),
            'async_min_rows': ASYNC_ROW_THRESHOLD,
            'will_async': len(rows) > ASYNC_ROW_THRESHOLD,
            'sample': sample,
        })

    @bp.route('/roster/<code>/api/append-execute', methods=['POST'])
    @login_required
    def roster_append_import_execute(code):
        """执行二次追加导入：≤2000 行同步落库，>2000 行走 A1 异步分批。"""
        table = facade.get_table(_MODULE_KEY, code)
        if table is None or not facade.is_built(table):
            return json_response(False, '台账不存在或尚未建表'), 404
        payload = request.get_json(silent=True) or {}
        token = (payload.get('token') or '').strip()
        found = _token_file(token)
        if not found:
            return json_response(False, '上传凭证已失效，请重新上传文件')
        path, _ext = found
        specs = facade.field_specs(table)
        filename = _meta_name(token)
        try:
            with open(path, 'rb') as fh:
                headers, rows = eng.parse_table_file(filename, fh)
            clean_map = facade.normalize_column_map(specs, headers, payload.get('column_map'))
        except eng.SoftTableError as exc:
            return json_response(False, str(exc))

        if len(rows) > ASYNC_ROW_THRESHOLD:
            ok, status_code, data, msg = start_softtable_async_batch(
                module_key=_MODULE_KEY, table_code=code, table_name=table.name,
                source_path=path, source_file=filename, operator=_who(),
                log_module=_LOG_MODULE, log_action_title='软表追加导入',
                column_map=clean_map)
            meta_file = _meta_path(token)
            if ok and os.path.exists(meta_file):
                os.remove(meta_file)
            return json_response(ok, msg, data=data), status_code

        projected = facade.project_rows_by_map(specs, headers, rows, clean_map)
        try:
            _recs, errors = eng.build_records(specs, projected)
        except eng.SoftTableError as exc:
            return json_response(False, str(exc))
        if errors and not payload.get('skip_error_rows'):
            return json_response(
                False,
                '有 %d 行数据不合规，未导入（可勾选「跳过错误行」后重试）' % len(errors),
                data={'errors': errors[:50], 'error_total': len(errors)})
        try:
            imported, _errs = facade.import_records(
                table, specs, projected, skip_error_rows=True, created_by=_who())
        except eng.SoftTableError as exc:
            db.session.rollback()
            return json_response(False, str(exc))
        except Exception as exc:
            db.session.rollback()
            return json_response(False, '导入失败：%s' % exc)

        log_operation(_who(), _LOG_MODULE, 'add', '软表追加导入 %s（%s）' % (table.name, code),
                      '导入 %d 行，跳过 %d 行' % (imported, len(errors)))
        meta_file = _meta_path(token)
        if os.path.exists(meta_file):
            os.remove(meta_file)
        if os.path.exists(path):
            os.remove(path)
        return json_response(
            True, '导入完成，已追加 %d 行' % imported,
            data={'mode': 'sync', 'code': code, 'imported': imported,
                  'skipped': len(errors), 'errors': errors[:20]})

    @bp.route('/roster/<code>/api/import-status/<task_id>')
    @login_required
    def roster_import_status(code, task_id):
        """轮询软表异步导入任务进度（GET → 所属模块 view）。"""
        table = facade.get_table(_MODULE_KEY, code)
        if table is None:
            return json_response(False, '台账不存在'), 404
        t = get_task(task_id)
        if not t:
            return json_response(False, '任务不存在'), 404
        return json_response(True, '', data={
            'task_id': t.task_id, 'status': t.status, 'progress': t.progress,
            'result_summary': t.result_summary or '',
        })

    @bp.route('/roster/<code>/api/import-report/<batch_id>')
    @login_required
    def roster_import_report(code, batch_id):
        """软表导入批次报告 + 前 50 条错误摘要（GET → 所属模块 view）。"""
        batch = SofttableBatchImport.query.filter_by(
            batch_id=batch_id, module_key=_MODULE_KEY, table_code=code).first()
        if not batch:
            return json_response(False, '导入批次不存在'), 404
        sync_failed_pending_batches([batch])
        errs = (SofttableBatchError.query.filter_by(batch_id=batch_id)
                .order_by(SofttableBatchError.row_no.asc(), SofttableBatchError.id.asc())
                .limit(50).all())
        return json_response(True, '', data={
            'batch_id': batch.batch_id, 'table_code': batch.table_code,
            'module_key': batch.module_key, 'status': batch.status,
            'source_file': batch.source_file or '',
            'total_rows': batch.total_rows or 0,
            'success_rows': batch.success_rows or 0,
            'fail_rows': batch.fail_rows or 0,
            'errors': [{'row': e.row_no, 'field': e.field_name or '', 'msg': e.reason or ''}
                       for e in errs],
        })

    @bp.route('/roster/<code>/api/import-export-failed/<batch_id>')
    @login_required
    def roster_import_export_failed(code, batch_id):
        """导出软表导入失败行清单 .xlsx（仅行号+列名+原因，零敏感原值；GET → export）。"""
        batch = SofttableBatchImport.query.filter_by(
            batch_id=batch_id, module_key=_MODULE_KEY, table_code=code).first()
        if not batch:
            return json_response(False, '导入批次不存在'), 404
        errs = (SofttableBatchError.query.filter_by(batch_id=batch_id)
                .order_by(SofttableBatchError.row_no.asc(), SofttableBatchError.id.asc()).all())
        rows = [[e.row_no, e.field_name or '', e.reason or ''] for e in errs]
        # write_export_xlsx 返回 (abs_path, token_url)，走 /report/download/<token> 通道，
        # 与 dynatable.api_import_export_failed 同款姿势（107 口径）。
        abs_path, token_url = write_export_xlsx(
            ['数据行号', '出错字段', '失败原因'], rows,
            module=_LOG_MODULE,
            filename='软表导入失败行_%s_%s.xlsx' % (batch.table_code, batch_id))
        if not token_url:
            return json_response(False, '导出通道未就绪'), 500
        log_operation(_who(), _LOG_MODULE, 'export',
                      '导出软表失败行 %s（%s）%d 条' % (batch.table_code, batch_id, len(rows)))
        return json_response(True, '导出成功', data={
            'download_url': '/report/download/%s' % token_url,
            'file': os.path.basename(abs_path),
        })


def register_roster_routes(bp, *, module_key: str, group_name: str, log_module: str,
                           nav_module: str = None,
                           allowed_ext=('.xlsx', '.xls', '.xlsb', '.csv', '.xlsm'),
                           max_upload_bytes: int = 20 * 1024 * 1024):
    """将标准花名册端点（含二次追加与异步分批）统一挂载到业务蓝图，消除跨模块重复拷贝。"""
    _ROSTER_MODULE_KEY = module_key
    _ROSTER_PREFIX = '/' + module_key
    _ROSTER_GROUP_NAME = group_name
    _LOG_MODULE = log_module
    _ROSTER_NAV_MODULE = nav_module or tab_group_for(module_key)
    _ROSTER_ALLOWED_EXT = allowed_ext
    _ROSTER_MAX_UPLOAD_BYTES = max_upload_bytes
    _roster_temp_dir, _roster_token_file, _roster_meta_path, _roster_meta_name = _make_temp_helpers(
        _ROSTER_ALLOWED_EXT)

    def _roster_who():
        return session.get('username', '')

    def _roster_filters():
        """f_ 前缀查询参数 → 引擎筛选条件（含 _min/_max 区间），口径同 dynatable api_records。"""
        filters, ranges = {}, {}
        for key, value in request.args.items():
            if key.startswith('f_') and key.endswith('_min'):
                ranges.setdefault(key[2:-4], {})['min'] = value
            elif key.startswith('f_') and key.endswith('_max'):
                ranges.setdefault(key[2:-4], {})['max'] = value
            elif key.startswith('f_'):
                filters[key[2:]] = value
        filters.update({k: v for k, v in ranges.items() if v})
        q_kw = (request.args.get('q') or '').strip()
        if q_kw:
            filters['__q__'] = q_kw
        return filters

    @bp.route('/roster/<code>/')
    @login_required
    def roster_page(code):
        """门面页：已建（有字段行）→ 台账页；未建（m031 预置空行）→ 200 引导页；
        跨模块 / 未知 code（facade.get_table 归 None）→ 404。"""
        if _ROSTER_MODULE_KEY == 'governance':
            # C-2（141）：8 张综治固定表懒自愈（启动钩子未跑/失败时不再 404）+ 历史数据按需补迁。
            # 补迁只在「旧表仍在且未迁完」时才真正执行（密钥锁定时推迟，解锁后进本页即补）。
            try:
                from utils.governance_v2_migration import (
                    ensure_governance_v2_tables, migrate_legacy_if_pending)
                if facade.get_table(_ROSTER_MODULE_KEY, code) is None:
                    ensure_governance_v2_tables(migrate_legacy=True)
                else:
                    migrate_legacy_if_pending()
            except Exception:  # noqa: BLE001 — 自愈失败不阻断页面
                db.session.rollback()
        table = facade.get_table(_ROSTER_MODULE_KEY, code)
        if table is None:
            abort(404)
        fields = facade.fields_with_options(table)
        if not fields:
            # meta 传纯 dict（模板 __ST_ROSTER__ 会 tojson 序列化，ORM 对象不可序列化）
            return render_template('components/roster/roster_bootstrap.html', code=code,
                                   roster_prefix=_ROSTER_PREFIX,
                                   meta={'name': table.name, 'module_key': table.module_key,
                                         'description': table.description or ''})
        extra_ctx = {}
        if _ROSTER_NAV_MODULE and facade.is_dynamic_tab_table(_ROSTER_MODULE_KEY, code):
            extra_ctx = {
                'nav_module': _ROSTER_NAV_MODULE,
                'nav_active': 'dt:' + code,
                'show_back': False,
            }
        elif _ROSTER_MODULE_KEY == 'governance':
            from utils.governance_catalog import CATEGORY_CONFIG, GOV_CODE_TO_CATEGORY
            _gov_cat = GOV_CODE_TO_CATEGORY.get(code)
            if _gov_cat:
                _gov_cfg = CATEGORY_CONFIG.get(_gov_cat) or {}
                extra_ctx = {
                    'nav_module': _gov_cfg.get('tab_module') or 'governance',
                    'nav_active': _gov_cat,
                    'show_back': False,
                }
        from_node = request.args.get('from_node', type=int)
        back_url = ('%s/?node=%d' % (_ROSTER_PREFIX, from_node)) if from_node else (_ROSTER_PREFIX + '/')
        return render_template('components/roster/roster_page.html',
                               table=table, code=code, fields=fields,
                               roster_prefix=_ROSTER_PREFIX, back_url=back_url,
                               **extra_ctx)

    @bp.route('/roster/<code>/upload')
    @login_required
    def roster_upload_page(code):
        """上传与字段确认页（GET → 所属模块 view）：未建表引导页与台账页共用同一上传流程的独立入口。"""
        table = facade.get_table(_ROSTER_MODULE_KEY, code)
        if table is None:
            abort(404)
        return render_template('components/roster/roster_upload.html', code=code,
                               roster_prefix=_ROSTER_PREFIX,
                               meta={'name': table.name, 'module_key': table.module_key})

    @bp.route('/roster/<code>/api/list')
    @login_required
    def roster_list(code):
        """分页/筛选/排序；truncated 标记列表已按 MAX_LIST_ROWS 截断。"""
        table = facade.get_table(_ROSTER_MODULE_KEY, code)
        if table is None:
            return json_response(False, '台账不存在')
        result = facade.list_rows(table,
                                  page=request.args.get('page', 1, type=int),
                                  limit=request.args.get('limit', 20, type=int),
                                  sort=request.args.get('sort') or None,
                                  order=request.args.get('dir') or 'asc',
                                  filters=_roster_filters())
        # json_response 无扩展位，truncated 随统一响应体一并返回
        return jsonify({'success': True, 'msg': '', 'data': result['data'],
                        'total': result['total'], 'truncated': result['truncated']})

    def _roster_record_save(code, rid):
        """record 新增/更新共用体：rid=None/0 新增（roster.js 拼 /api/record/0 表示新增），
        否则更新；SoftTableError 转错误响应。"""
        rid = rid or None
        table = facade.get_table(_ROSTER_MODULE_KEY, code)
        if table is None:
            return json_response(False, '台账不存在')
        payload = request.get_json(silent=True) or {}
        values = payload.get('values')
        if not isinstance(values, dict):
            values = {k: v for k, v in payload.items() if k not in ('id', 'rid')}
        try:
            row = facade.save_row(table, rid, values, created_by=_roster_who())
        except eng.SoftTableError as exc:
            return json_response(False, str(exc))
        log_operation(_roster_who(), _LOG_MODULE, 'edit' if rid else 'add',
                      '台账 %s（%s）%s记录 #%s' % (table.name, code,
                                                  '编辑' if rid else '新增', row.get('id')))
        return json_response(True, '已保存', data=row)

    @bp.route('/roster/<code>/api/record', methods=['POST'])
    @login_required
    def roster_record_add(code):
        """新增一行（视图名含 add → 所属模块 add 动作位）。"""
        return _roster_record_save(code, None)

    @bp.route('/roster/<code>/api/record/<int:rid>', methods=['POST'])
    @login_required
    def roster_record_edit(code, rid):
        """更新一行（视图名含 edit → 所属模块 edit 动作位）。"""
        return _roster_record_save(code, rid)

    @bp.route('/roster/<code>/api/upload', methods=['POST'])
    @login_required
    def roster_upload(code):
        """上传 Excel/CSV → 引擎解析 + 字段推断，token + meta 落临时目录（建表后即删）。"""
        raw = request.files.get('file')
        if not raw or not raw.filename:
            return json_response(False, '请选择要上传的 .xlsx / .csv 文件')
        filename = raw.filename
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _ROSTER_ALLOWED_EXT:
            return json_response(False, '仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式文件')
        raw.stream.seek(0, os.SEEK_END)
        size = raw.stream.tell()
        raw.stream.seek(0)
        if size > _ROSTER_MAX_UPLOAD_BYTES:
            return json_response(False, '文件超过 20MB 上限，请拆分后再上传')

        token = secrets.token_hex(16)
        dest = os.path.join(_roster_temp_dir(), token + ext)
        raw.save(dest)
        try:
            with open(dest, 'rb') as fh:
                headers, rows = eng.parse_table_file(filename, fh)
            suggestions = eng.suggest_fields(headers, rows)
        except eng.SoftTableError as exc:
            if os.path.exists(dest):
                os.remove(dest)
            return json_response(False, str(exc))

        meta = {'filename': filename, 'headers': headers, 'suggestions': suggestions,
                'row_count': len(rows), 'uploaded_by': _roster_who(), 'code': code}
        with open(_roster_meta_path(token), 'w', encoding='utf-8') as fh:
            json.dump(meta, fh, ensure_ascii=False)
        sample = [[eng._as_text(c) for c in row[:len(headers)]] for row in rows[:5]]
        return json_response(True, '', data={
            'token': token, 'filename': filename, 'headers': headers,
            'suggestions': suggestions, 'row_count': len(rows), 'sample': sample,
            'async_min_rows': ASYNC_ROW_THRESHOLD,
            'will_async': len(rows) > ASYNC_ROW_THRESHOLD,
            'dict_categories': eng.dict_categories_for_enum(),
        })

    @bp.route('/roster/<code>/api/create', methods=['POST'])
    @login_required
    def roster_create(code):
        """确认字段后建表：code 由 URL 决定（不取 payload），module/group 为所属模块门面固定值；
        ≤2000 行同步写入，>2000 行同步建表后切 A1 异步分批；重复建已建台账 → 409。"""
        payload = request.get_json(silent=True) or {}
        token = (payload.get('token') or '').strip()
        found = _roster_token_file(token)
        if not found:
            return json_response(False, '上传凭证已失效，请重新上传文件')
        path, _ext = found
        try:
            fields = eng.normalize_fields(payload.get('fields'))
        except eng.SoftTableError as exc:
            return json_response(False, str(exc))
        name = (payload.get('name') or '').strip()
        if not name:
            return json_response(False, '请填写台账名称')

        table = facade.get_table(_ROSTER_MODULE_KEY, code)
        if table is not None and facade.is_built(table):
            return (json_response(False, '该台账已存在，请勿重复建表'), 409)

        filename = _roster_meta_name(token)
        try:
            with open(path, 'rb') as fh:
                _headers, rows = eng.parse_table_file(filename, fh)
        except eng.SoftTableError as exc:
            return json_response(False, str(exc))

        if len(rows) > ASYNC_ROW_THRESHOLD:
            try:
                table = facade.create_table(_ROSTER_MODULE_KEY, code, name, _ROSTER_GROUP_NAME,
                                            fields, created_by=_roster_who())
            except eng.SoftTableError as exc:
                db.session.rollback()
                return json_response(False, str(exc)), 409
            ok, status_code, data, msg = start_softtable_async_batch(
                module_key=_ROSTER_MODULE_KEY, table_code=code, table_name=table.name,
                source_path=path, source_file=filename, operator=_roster_who(),
                log_module=_LOG_MODULE, log_action_title='软表异步建表')
            meta_file = _roster_meta_path(token)
            if ok and os.path.exists(meta_file):
                os.remove(meta_file)
            return json_response(ok, msg, data=data), status_code

        try:
            # 先验后建（dynatable api_create 同款门禁）：错误行未勾选跳过 → 不建表不留半截
            _records, errors = eng.build_records(fields, rows)
        except eng.SoftTableError as exc:
            return json_response(False, str(exc))
        if errors and not payload.get('skip_error_rows'):
            return json_response(False, '有 %d 行数据不合规，未建表（可勾选「跳过错误行」后重试）' % len(errors),
                                 data={'errors': errors[:50], 'error_total': len(errors)})

        try:
            table = facade.create_table(_ROSTER_MODULE_KEY, code, name, _ROSTER_GROUP_NAME,
                                        fields, created_by=_roster_who())
            imported, _errs = facade.import_records(table, fields, rows, skip_error_rows=True,
                                                    created_by=_roster_who())
        except eng.SoftTableError as exc:
            db.session.rollback()
            return json_response(False, str(exc))
        except Exception as exc:
            db.session.rollback()
            return json_response(False, '建表失败：%s' % exc)

        log_operation(_roster_who(), _LOG_MODULE, 'add', '软表建表 %s（%s）' % (name, code),
                      '字段 %d 个，导入 %d 行' % (len(fields), imported))
        meta_file = _roster_meta_path(token)
        if os.path.exists(meta_file):
            os.remove(meta_file)
        if os.path.exists(path):
            os.remove(path)
        return json_response(True, '建表完成，已导入 %d 行' % imported,
                             data={'mode': 'sync', 'code': code, 'imported': imported,
                                   'skipped': len(errors)})

    @bp.route('/roster/<code>/api/export')
    @login_required
    def roster_export(code):
        """按当前筛选导出 xlsx（096 口径）；统一走门面打码口径。"""
        table = facade.get_table(_ROSTER_MODULE_KEY, code)
        if table is None:
            return json_response(False, '台账不存在')
        from models import User
        from utils.permissions import user_has_special
        user = User.query.get(session.get('user_id')) if session.get('user_id') else None
        can_view_sensitive = bool(user and (user.role_id == 1
                                            or user_has_special(user, 'view_sensitive')))
        headers, rows = facade.export_rows(table, can_view_sensitive,
                                           filters=_roster_filters(),
                                           sort=request.args.get('sort') or None,
                                           order=request.args.get('dir') or 'asc')
        log_operation(_roster_who(), _LOG_MODULE, 'export',
                      '导出台账 %s（%s）%d 行' % (table.name, code, len(rows)))
        # identity 列已在 decode_records 打码/解密 → already_masked=True 防二次脱敏（R-096b-01）
        return export_excel(headers, rows, '%s.xlsx' % table.name,
                            can_view_sensitive=can_view_sensitive, already_masked=True)

    register_roster_append_routes(bp, module_key=_ROSTER_MODULE_KEY, log_module=_LOG_MODULE,
                                  allowed_ext=_ROSTER_ALLOWED_EXT,
                                  max_upload_bytes=_ROSTER_MAX_UPLOAD_BYTES)
