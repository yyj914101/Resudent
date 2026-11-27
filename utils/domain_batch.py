# -*- coding: utf-8 -*-
"""utils/domain_batch.py — 通用域 A1 异步分批导入框架与标准 5 端点工厂（第三批）。

各固定大表业务模块（医保参保、低保、残疾、关爱对象、公益岗）只需提供：
- row_handler(row_dict, ctx) -> 'inserted' | 'updated' | 'skipped'（抛 ValueError/RuntimeError 记失败行）
- 可选 pre_run(batch, headers, ctx)（如 replace 模式清库或初始化默认参数）
即可通过 register_domain_batch_routes 一次性挂载标准 5 端点：
  import-upload / import-execute / import-status / import-report / import-export-failed
并自动接入【数据管理 → 导入批次】统一运维视图。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime

from flask import request, session

import config
from models import DomainBatchError, DomainBatchImport, db
from utils.async_tasks import create_task, get_task, is_task_busy, sync_failed_pending_batches
from utils.decorators import login_required
from utils.helpers import json_response, log_operation, write_export_xlsx
from utils.import_engine import read_import_file
from utils.masking import mask_text

TASK_TYPE = 'domain_batch'
BATCH_SIZE = 500
ASYNC_ROW_THRESHOLD = 500
ALLOWED_EXT = ('.xlsx', '.xls', '.xlsb', '.csv', '.xlsm')
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

DOMAIN_LABELS = {
    'medical': ('医保参保', '/medical-insurance'),
    'pension': ('农保/养老保险', '/pension'),
    'dibao': ('低保人员', '/dibao'),
    'disabled': ('残疾人', '/disabled'),
    'care_special': ('特困人员', '/care/special'),
    'care_child': ('困境儿童', '/care/child'),
    'care_target': ('关爱对象', '/care/target'),
    'welfare_job': ('公益岗', '/welfare'),
}

_DOMAIN_SPECS = {}


def _temp_dir() -> str:
    path = os.path.join(config.TEMP_DIR, 'domain_batch')
    os.makedirs(path, exist_ok=True)
    return path


def _token_file(token: str):
    if not token or not isinstance(token, str) or not all(c in '0123456789abcdef' for c in token):
        return None
    for ext in ALLOWED_EXT:
        path = os.path.join(_temp_dir(), token + ext)
        if os.path.exists(path):
            return path, ext
    return None


def _meta_path(token: str) -> str:
    return os.path.join(_temp_dir(), token + '.meta.json')


def _meta_name(token: str) -> str:
    mf = _meta_path(token)
    if os.path.exists(mf):
        with open(mf, encoding='utf-8') as fh:
            return json.load(fh).get('filename', 'upload.xlsx')
    return 'upload.xlsx'


def add_batch_error(batch_id: str, row_no: int, field_name: str, reason: str):
    """失败行落库前经 mask_text 脱敏，零身份证/手机/银行卡明文。"""
    db.session.add(DomainBatchError(
        batch_id=batch_id,
        row_no=int(row_no),
        field_name=(str(field_name) if field_name else '')[:100],
        reason=mask_text(str(reason or ''))[:300],
    ))


def mark_aborted_stale() -> int:
    """进程重启后，将残留 running 的通用域导入批次标为 aborted。"""
    stale = DomainBatchImport.query.filter_by(status='running').all()
    for batch in stale:
        batch.status = 'aborted'
    if stale:
        db.session.commit()
    return len(stale)


def run_domain_batch(task_id: str, batch_id: str, options=None):
    """A1 后台执行体：解析文件 → 每 BATCH_SIZE 行一事务调用业务 row_handler → 回写计数。"""
    from utils.async_tasks import _update

    batch = DomainBatchImport.query.filter_by(batch_id=batch_id).first()
    if batch is None:
        raise RuntimeError('导入批次不存在：%s' % batch_id)
    if batch.status == 'done':
        return
    spec = _DOMAIN_SPECS.get(batch.domain)
    if spec is None:
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='未注册的导入域：%s' % batch.domain)
        return
    if not batch.source_path or not os.path.exists(batch.source_path):
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='源文件已失效，请重新上传')
        return

    try:
        headers, rows = read_import_file(batch.source_path)
    except Exception as exc:
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='文件解析失败：%s' % exc)
        return

    ctx = dict(options or {})
    ctx['headers'] = headers
    ctx['mode'] = batch.mode
    ctx['scope_key'] = batch.scope_key
    ctx['operator'] = batch.operator

    total = len(rows)
    batch.status = 'running'
    batch.total_rows = total
    db.session.commit()
    _update(task_id, progress=0, result_summary='开始分批导入 %d 行' % total)

    inserted = updated = skipped = failed = 0
    try:
        pre_run = spec.get('pre_run')
        if callable(pre_run):
            pre_run(batch, headers, ctx)
            db.session.commit()

        row_handler = spec['row_handler']
        for start in range(0, total, BATCH_SIZE):
            chunk = rows[start:start + BATCH_SIZE]
            for offset, raw_row in enumerate(chunk):
                row_no = start + offset + 2  # Excel 行号（第 1 行为表头）
                try:
                    from utils.header_aliases import normalize_domain_row
                    outcome = row_handler(normalize_domain_row(raw_row, batch.domain), ctx)
                    if outcome == 'inserted':
                        inserted += 1
                    elif outcome == 'updated':
                        updated += 1
                    else:
                        skipped += 1
                except Exception as row_exc:
                    failed += 1
                    add_batch_error(batch_id, row_no, '', str(row_exc))
            db.session.commit()
            done_rows = min(start + BATCH_SIZE, total)
            pct = 99 if total == 0 else min(99, int(done_rows * 100 / total))
            _update(task_id, progress=pct,
                    result_summary='已处理 %d/%d 行（新增 %d，更新 %d，跳过 %d，失败 %d）'
                                   % (done_rows, total, inserted, updated, skipped, failed))
    except Exception as exc:
        db.session.rollback()
        batch = DomainBatchImport.query.filter_by(batch_id=batch_id).first()
        batch.status = 'failed'
        batch.inserted_rows = inserted
        batch.updated_rows = updated
        batch.skip_rows = skipped
        batch.fail_rows = failed
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='分批导入中止：%s: %s' % (type(exc).__name__, exc))
        return

    batch = DomainBatchImport.query.filter_by(batch_id=batch_id).first()
    batch.status = 'done'
    batch.inserted_rows = inserted
    batch.updated_rows = updated
    batch.skip_rows = skipped
    batch.fail_rows = failed
    batch.finished_at = datetime.now()
    db.session.commit()
    try:
        if os.path.exists(batch.source_path):
            os.remove(batch.source_path)
    except OSError:
        pass
    _update(task_id, status='done', progress=100,
            result_summary='导入完成：新增 %d，更新 %d，跳过 %d，失败 %d'
                           % (inserted, updated, skipped, failed),
            finished_at=datetime.now())


def register_domain_batch_routes(bp, *, domain: str, log_module: str,
                                 row_handler, pre_run=None,
                                 inspect_headers=None,
                                 url_prefix: str = '',
                                 endpoint_prefix: str = ''):
    """向业务蓝图挂载标准 5 个 A1 异步批量导入端点（零重复样板代码）。"""
    _DOMAIN_SPECS[domain] = {'row_handler': row_handler, 'pre_run': pre_run}
    ep = (endpoint_prefix or domain).replace('-', '_')
    pref = url_prefix.rstrip('/')

    def _who():
        return session.get('username') or str(session.get('user_id') or '')

    def _upload():
        raw = request.files.get('file')
        if not raw or not raw.filename:
            return json_response(False, '请选择要上传的 .xlsx / .xls / .csv 文件')
        filename = raw.filename
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXT:
            return json_response(False, '仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式文件')
        raw.stream.seek(0, os.SEEK_END)
        size = raw.stream.tell()
        raw.stream.seek(0)
        if size > MAX_UPLOAD_BYTES:
            return json_response(False, '文件超过 50MB 上限，请拆分后再上传')

        token = secrets.token_hex(16)
        dest = os.path.join(_temp_dir(), token + ext)
        raw.save(dest)
        try:
            headers, rows = read_import_file(dest)
        except Exception as exc:
            if os.path.exists(dest):
                os.remove(dest)
            return json_response(False, '文件解析失败：%s' % exc)

        extra_info = {}
        if callable(inspect_headers):
            extra_info = inspect_headers(headers, request.form) or {}
        with open(_meta_path(token), 'w', encoding='utf-8') as fh:
            json.dump({'filename': filename, 'headers': headers,
                       'row_count': len(rows), 'domain': domain}, fh, ensure_ascii=False)
        data = {
            'token': token,
            'filename': filename,
            'headers': headers,
            'row_count': len(rows),
            'async_min_rows': ASYNC_ROW_THRESHOLD,
            'will_async': len(rows) > ASYNC_ROW_THRESHOLD,
        }
        data.update(extra_info)
        return json_response(True, '', data=data)

    def _execute():
        payload = request.get_json(silent=True) or request.form.to_dict()
        token = (payload.get('token') or '').strip()
        found = _token_file(token)
        if not found:
            return json_response(False, '上传凭证已失效，请重新上传文件')
        path, _ext = found
        filename = _meta_name(token)
        mode = (payload.get('mode') or 'append').strip() or 'append'
        scope_key = (payload.get('scope_key') or payload.get('insurance_type') or '').strip()[:100]

        with open(path, 'rb') as fh:
            source_hash = hashlib.sha256(fh.read()).hexdigest()

        dup = (DomainBatchImport.query
               .filter_by(domain=domain, scope_key=scope_key, source_hash=source_hash)
               .first())
        if dup is not None:
            sync_failed_pending_batches([dup])
        task = None
        if dup is not None:
            if dup.status in ('failed', 'aborted') and (dup.inserted_rows or 0) + (dup.updated_rows or 0) == 0:
                if is_task_busy(TASK_TYPE):
                    return json_response(False, '并发互斥：已有批量导入任务正在运行，请稍后重试'), 409
                DomainBatchError.query.filter_by(batch_id=dup.batch_id).delete()
                dup.status = 'pending'
                dup.fail_rows = 0
                dup.source_path = path
                batch = dup
            elif dup.status in ('pending', 'running', 'done'):
                return json_response(
                    True, '该文件已存在导入批次（状态 %s），返回既有批次' % dup.status,
                    data={'batch_id': dup.batch_id, 'task_id': dup.task_id,
                          'duplicate': True, 'status': dup.status})
            else:
                return json_response(
                    False, '该批次已有部分数据入库，请修改文件后重新上传'), 409
        else:
            if is_task_busy(TASK_TYPE):
                return json_response(False, '并发互斥：已有批量导入任务正在运行，请稍后重试'), 409
            task = create_task(TASK_TYPE, _who(), '%s批量导入' % log_module)
            batch = DomainBatchImport(
                batch_id=uuid.uuid4().hex[:12],
                domain=domain,
                scope_key=scope_key,
                source_file=filename,
                source_path=path,
                source_hash=source_hash,
                mode=mode,
                operator=_who(),
                status='pending',
                task_id=task.task_id,
            )
            db.session.add(batch)
            db.session.commit()
            log_operation(_who(), log_module, 'import',
                          '发起异步批量导入 %s（批次 %s）' % (filename, batch.batch_id))

        if task is None:
            task = create_task(TASK_TYPE, _who(), '%s批量导入（重进）' % log_module)
            batch.task_id = task.task_id
            db.session.commit()

        mf = _meta_path(token)
        if os.path.exists(mf):
            os.remove(mf)

        options = {k: v for k, v in payload.items() if k not in ('token',)}
        from utils.async_tasks import run_async
        accepted = run_async(TASK_TYPE, task.task_id, run_domain_batch,
                             batch_id=batch.batch_id, options=options)
        if accepted is False:
            batch.status = 'failed'
            batch.finished_at = datetime.now()
            db.session.commit()
            return json_response(
                False, '并发互斥：已有批量导入任务正在运行，请稍后重试',
                data={'batch_id': batch.batch_id, 'task_id': task.task_id}), 409
        return json_response(True, '已提交后台分批导入',
                             data={'batch_id': batch.batch_id, 'task_id': task.task_id})

    def _status(task_id):
        t = get_task(task_id)
        if not t:
            return json_response(False, '任务不存在'), 404
        return json_response(True, '', data={
            'task_id': t.task_id, 'status': t.status, 'progress': t.progress,
            'result_summary': t.result_summary or '',
        })

    def _report(batch_id):
        batch = DomainBatchImport.query.filter_by(batch_id=batch_id, domain=domain).first()
        if not batch:
            return json_response(False, '导入批次不存在'), 404
        sync_failed_pending_batches([batch])
        errs = (DomainBatchError.query.filter_by(batch_id=batch_id)
                .order_by(DomainBatchError.row_no.asc(), DomainBatchError.id.asc())
                .limit(50).all())
        return json_response(True, '', data={
            'batch_id': batch.batch_id, 'domain': batch.domain,
            'scope_key': batch.scope_key, 'source_file': batch.source_file or '',
            'mode': batch.mode, 'status': batch.status,
            'total_rows': batch.total_rows or 0,
            'inserted_rows': batch.inserted_rows or 0,
            'updated_rows': batch.updated_rows or 0,
            'skip_rows': batch.skip_rows or 0,
            'fail_rows': batch.fail_rows or 0,
            'errors': [{'row': e.row_no, 'field': e.field_name or '', 'msg': e.reason or ''}
                       for e in errs],
        })

    def _export_failed(batch_id):
        batch = DomainBatchImport.query.filter_by(batch_id=batch_id, domain=domain).first()
        if not batch:
            return json_response(False, '导入批次不存在'), 404
        errs = (DomainBatchError.query.filter_by(batch_id=batch_id)
                .order_by(DomainBatchError.row_no.asc(), DomainBatchError.id.asc()).all())
        rows = [[e.row_no, e.field_name or '', e.reason or ''] for e in errs]
        # write_export_xlsx 返回 (abs_path, token_url)，走 /report/download/<token> 通道，
        # 与 dynatable.api_import_export_failed 同款姿势（107 口径；直返元组会产出 status 0 坏响应）。
        abs_path, token_url = write_export_xlsx(
            ['Excel行号', '出错字段', '失败原因'], rows,
            module=log_module,
            filename='%s导入失败行_%s.xlsx' % (log_module, batch_id))
        if not token_url:
            return json_response(False, '导出通道未就绪'), 500
        log_operation(_who(), log_module, 'export',
                      '导出导入失败行（%s）%d 条' % (batch_id, len(rows)))
        return json_response(True, '导出成功', data={
            'download_url': '/report/download/%s' % token_url,
            'file': os.path.basename(abs_path),
        })

    bp.add_url_rule(f'{pref}/api/import-upload', endpoint=f'{ep}_import_upload',
                    view_func=login_required(_upload), methods=['POST'])
    bp.add_url_rule(f'{pref}/api/import-execute', endpoint=f'{ep}_import_execute',
                    view_func=login_required(_execute), methods=['POST'])
    bp.add_url_rule(f'{pref}/api/import-status/<task_id>', endpoint=f'{ep}_import_status',
                    view_func=login_required(_status), methods=['GET'])
    bp.add_url_rule(f'{pref}/api/import-report/<batch_id>', endpoint=f'{ep}_import_report',
                    view_func=login_required(_report), methods=['GET'])
    bp.add_url_rule(f'{pref}/api/import-export-failed/<batch_id>', endpoint=f'{ep}_import_export_failed',
                    view_func=login_required(_export_failed), methods=['GET'])
