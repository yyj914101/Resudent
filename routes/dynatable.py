# -*- coding: utf-8 -*-
"""routes/dynatable.py — 软表引擎蓝图（078 交付物 4）

职责边界：本文件只做「取参数 → 调 utils.softtable_engine → 包 JSON / 渲染」，
**不做任何建表/改表语句**（ADR-025：业务台账零物理 DDL，改表 = 改元数据行）。

权限（ADR-016）：整蓝图归 BLUEPRINT_MODULES['dynatable']，动作位按方法自动映射
（GET→view / 上传建表→add / 编辑→edit / 删字段→delete）。身份标识解锁额外 @admin_required。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import uuid

from flask import Blueprint, jsonify, render_template, request, session

import config
from models import (DynamicField, DynamicRecord, DynamicTable, SofttableBatchError,
                    SofttableBatchImport, db)
from utils import roster_facade as facade
from utils import softtable_engine as eng
from utils.async_tasks import create_task, get_task
from utils.crypto import key_holder
from utils.decorators import admin_required, login_required
from utils.dynatable_modules import MODULE_KEYS, label_for, module_options
from utils.helpers import log_operation, write_export_xlsx
from utils.softtable_batch import ASYNC_ROW_THRESHOLD, TASK_TYPE, run_softtable_batch

dynatable_bp = Blueprint('dynatable', __name__)

# MODULE_KEYS / 中文标签统一由 utils.dynatable_modules 供给（键=持久化值，中文=界面展示）；
# 本文件不再维护第二份键清单，避免「校验白名单」与「下拉选项」漂移。
GROUP_NAMES = ('人口与台账', '社会保障', '业务办理', '民生服务', '应急管理')
ALLOWED_EXT = ('.xlsx', '.xlsm', '.csv')
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_LIST_ROWS = 20000


# ------------------------------------------------------------------ 内部工具

def _ok(data=None, msg='', **extra):
    body = {'success': True, 'msg': msg}
    if data is not None:
        body['data'] = data
    body.update(extra)
    return jsonify(body)


def _fail(msg, code=400, **extra):
    body = {'success': False, 'msg': msg}
    body.update(extra)
    return jsonify(body), code


def _who() -> str:
    return session.get('username') or session.get('real_name') or 'unknown'


def _temp_dir() -> str:
    path = os.path.join(config.TEMP_DIR, 'softtable')
    os.makedirs(path, exist_ok=True)
    return path


def _backup_dir() -> str:
    path = os.path.join(config.BACKUP_DIR, 'softtable')
    os.makedirs(path, exist_ok=True)
    return path


def _get_table(code: str):
    return DynamicTable.query.filter_by(code=(code or '').strip()).first()


def _table_fields(table) -> list:
    return (DynamicField.query.filter_by(table_id=table.id)
            .order_by(DynamicField.sort_order.asc(), DynamicField.id.asc()).all())


def _field_specs(table) -> list:
    """ORM 字段 → 引擎可消费的纯 dict 规格。"""
    return [{
        'id': f.id, 'code': f.code, 'display_name': f.display_name, 'data_type': f.data_type,
        'dict_category_code': f.dict_category_code, 'sensitivity': f.sensitivity,
        'is_required': bool(f.is_required), 'sort_order': f.sort_order,
    } for f in _table_fields(table)]


def _enum_options(category) -> list:
    """枚举列可选值：[{value, label}]，唯一来源 dict_items（不用 get_enum，避免种子回退幽灵项）。"""
    from utils.helpers import get_dict_items
    return [{'value': i.value, 'label': i.label} for i in get_dict_items(category)]


def _fields_with_options(table) -> list:
    """字段规格 + 枚举列的可选值（运行时读 dict_items，不落副本）。"""
    specs = []
    for spec in _field_specs(table):
        view = dict(spec)
        view['options'] = _enum_options(spec['dict_category_code']) \
            if spec['data_type'] == 'enum' and spec['dict_category_code'] else []
        specs.append(view)
    return specs


def _hmac_key():
    if not key_holder.is_initialized:
        return None
    return eng.derive_hmac_key(key_holder.get_key_bytes())


def _digest_fn():
    key = _hmac_key()
    if not key:
        return None
    return lambda code, value: eng.identity_digest(key, code, value)


def _rows_of(table):
    return (DynamicRecord.query.filter_by(table_id=table.id)
            .order_by(DynamicRecord.id.asc()).all())


def _token_file(token: str):
    if not token or not all(c in '0123456789abcdef' for c in token):
        return None
    for ext in ALLOWED_EXT:
        path = os.path.join(_temp_dir(), token + ext)
        if os.path.exists(path):
            return path, ext
    return None


def _meta_path(token: str) -> str:
    return os.path.join(_temp_dir(), token + '.meta.json')


def _snapshot_backup(table, fields, records) -> str:
    """结构变更前快照：JSON 明文 + .wbak 加密件（密钥可用时）。返回 JSON 路径。"""
    payload = {
        'table': {'code': table.code, 'name': table.name, 'module_key': table.module_key,
                  'group_name': table.group_name, 'description': table.description},
        'fields': [{'code': f.code, 'display_name': f.display_name, 'data_type': f.data_type,
                    'dict_category_code': f.dict_category_code, 'sensitivity': f.sensitivity,
                    'is_required': bool(f.is_required), 'sort_order': f.sort_order}
                   for f in fields],
        'records': [{'id': r.id, 'data': r.data, 'digest': r.digest,
                     'created_by': r.created_by} for r in records],
        'taken_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=1).encode('utf-8')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    base = os.path.join(_backup_dir(), '%s-%s' % (table.code, stamp))
    json_path = base + '.json'
    wbak_path = base + '.wbak'
    # A-7 偏差 D-1：JSON 明文件 + 加密件双份（主理人已批准保留）。
    # 两份都先写 .tmp 再 os.replace，任一份失败则把已写的一并清掉，绝不留半截备份。
    written = []
    try:
        for path, data in ((json_path, blob),
                           (wbak_path, key_holder.encrypt_bytes(blob) if key_holder.is_initialized else None)):
            if data is None:
                continue
            tmp = path + '.tmp'
            with open(tmp, 'wb') as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            written.append(path)
    except Exception as exc:
        for path in written + [p + '.tmp' for p in (json_path, wbak_path)]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        raise eng.SoftTableError('备份写入失败，已中止结构变更：%s' % exc)
    return json_path


# ------------------------------------------------------------------ 页面

@dynatable_bp.route('/')
@login_required
def index():
    return render_template('dynatable/index.html')


@dynatable_bp.route('/upload')
@login_required
def upload_page():
    # module_options: [{'value': 英文键, 'label': 中文}]；提交仍是键，界面显示中文
    return render_template('dynatable/upload.html',
                           module_options=module_options(), group_names=GROUP_NAMES)


@dynatable_bp.route('/confirm')
@login_required
def confirm_page():
    return render_template('dynatable/confirm.html',
                           token=request.args.get('token', ''))


@dynatable_bp.route('/t/<code>')
@login_required
def list_page(code):
    table = _get_table(code)
    if not table:
        return render_template('dynatable/missing.html', code=code), 404
    return render_template('dynatable/list.html', table=table,
                           module_label=label_for(table.module_key),
                           fields=_fields_with_options(table))


@dynatable_bp.route('/t/<code>/structure')
@login_required
def structure_page(code):
    table = _get_table(code)
    if not table:
        return render_template('dynatable/missing.html', code=code), 404
    return render_template('dynatable/structure.html', table=table,
                           fields=_fields_with_options(table),
                           dict_categories=eng.dict_categories_for_enum())


# ------------------------------------------------------------------ 上传/建表

@dynatable_bp.route('/api/upload', methods=['POST'])
@login_required
def api_upload():
    raw = request.files.get('file')
    if not raw or not raw.filename:
        return _fail('请选择要上传的 .xlsx / .csv 文件')
    filename = raw.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return _fail('只支持 .xlsx / .csv 文件')
    raw.stream.seek(0, os.SEEK_END)
    size = raw.stream.tell()
    raw.stream.seek(0)
    if size > MAX_UPLOAD_BYTES:
        return _fail('文件超过 20MB 上限，请拆分后再上传')

    token = secrets.token_hex(16)
    dest = os.path.join(_temp_dir(), token + ext)
    raw.save(dest)
    try:
        with open(dest, 'rb') as fh:
            headers, rows = eng.parse_table_file(filename, fh)
        suggestions = eng.suggest_fields(headers, rows)
    except eng.SoftTableError as exc:
        if os.path.exists(dest):
            os.remove(dest)
        return _fail(str(exc))

    meta = {'filename': filename, 'headers': headers,
            'suggestions': suggestions, 'row_count': len(rows), 'uploaded_by': _who()}
    with open(_meta_path(token), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False)
    sample = [[eng._as_text(c) for c in row[:len(headers)]] for row in rows[:5]]
    return _ok({'token': token, 'filename': filename, 'headers': headers,
                'suggestions': suggestions, 'row_count': len(rows),
                'sample': sample,
                'dict_categories': eng.dict_categories_for_enum()})


@dynatable_bp.route('/api/meta/<token>')
@login_required
def api_meta(token):
    """确认页回读推断结果（token 只存临时目录，建表后即删）。"""
    found = _token_file(token)
    meta_file = _meta_path(token)
    if not found or not os.path.exists(meta_file):
        return _fail('上传凭证已失效，请重新上传文件', code=404)
    with open(meta_file, encoding='utf-8') as fh:
        meta = json.load(fh)
    meta['dict_categories'] = eng.dict_categories_for_enum()
    # 107：异步路由阈值单一真源下发（前端确认页按 row_count > async_min_rows 走异步分批）
    meta['async_min_rows'] = ASYNC_ROW_THRESHOLD
    return _ok(meta)


@dynatable_bp.route('/api/create', methods=['POST'])
@login_required
def api_create():
    payload = request.get_json(silent=True) or {}
    token = (payload.get('token') or '').strip()
    found = _token_file(token)
    if not found:
        return _fail('上传凭证已失效，请重新上传文件')
    path, _ext = found
    try:
        code = eng.validate_table_code(payload.get('code'))
        fields = eng.normalize_fields(payload.get('fields'))
    except eng.SoftTableError as exc:
        return _fail(str(exc))
    if _get_table(code):
        return _fail('表标识 %s 已存在，请换一个' % code)
    name = (payload.get('name') or '').strip()
    if not name:
        return _fail('请填写台账名称')
    module_key = (payload.get('module_key') or 'dynatable').strip()
    if module_key not in MODULE_KEYS:
        return _fail('未知菜单归属：%s' % module_key)

    try:
        with open(path, 'rb') as fh:
            _headers, rows = eng.parse_table_file(_meta_name(token), fh)
        records, errors = eng.build_records(fields, rows)
    except eng.SoftTableError as exc:
        return _fail(str(exc))

    if errors and not payload.get('skip_error_rows'):
        return _fail('有 %d 行数据不合规，未建表（可勾选「跳过错误行」后重试）' % len(errors),
                     code=400, errors=errors[:50], error_total=len(errors))

    try:
        table = DynamicTable(code=code, name=name, module_key=module_key,
                             group_name=(payload.get('group_name') or '').strip() or None,
                             description=(payload.get('description') or '').strip() or None,
                             permission_key='dynatable', created_by=_who())
        db.session.add(table)
        db.session.flush()
        for spec in fields:
            db.session.add(DynamicField(
                table_id=table.id, code=spec['code'], display_name=spec['display_name'],
                data_type=spec['data_type'], dict_category_code=spec['dict_category_code'],
                sensitivity=spec['sensitivity'], is_required=spec['is_required'],
                sort_order=spec['sort_order']))
        for rec in records:
            db.session.add(DynamicRecord(table_id=table.id, data=rec['data'],
                                         digest=rec['digest'], created_by=_who()))
        db.session.commit()
    except Exception as exc:  # 建表失败整笔回滚，不留半截元数据
        db.session.rollback()
        return _fail('建表失败：%s' % exc, code=500)

    log_operation(_who(), 'dynatable', 'add',
                  '软表建表 %s（%s）' % (name, code),
                  '字段 %d 个，导入 %d 行' % (len(fields), len(records)))
    for suffix in ('.meta.json',):
        meta_file = os.path.join(_temp_dir(), token + suffix)
        if os.path.exists(meta_file):
            os.remove(meta_file)
    if os.path.exists(path):
        os.remove(path)
    return _ok({'code': code, 'imported': len(records), 'skipped': len(errors)},
               msg='建表完成，已导入 %d 行' % len(records), errors=errors[:20])


def _meta_name(token):
    """从 meta 取原始文件名（决定解析器分支：xlsx / csv）。"""
    meta_file = _meta_path(token)
    if os.path.exists(meta_file):
        with open(meta_file, encoding='utf-8') as fh:
            return json.load(fh).get('filename', 'upload.xlsx')
    return 'upload.xlsx'


# ------------------------------------------------------------------ 异步分批导入（107，模式 A）

@dynatable_bp.route('/api/import-execute', methods=['POST'])
@login_required
def api_import_execute():
    """107 异步建表导入（大表分批，A1 后台；小表仍走 api_create 同步路径，不替换）。

    台账元数据经 facade.create_table 同步建（元数据几行，秒级），数据由
    utils.softtable_batch 后台按 BATCH_SIZE 分批经 facade.import_records 灌入。
    幂等（106 §6.1，判重键 = module_key + table_code + source_hash）：
    - 既有批次 pending/running/done → 直接返回既有批次，不重复执行；
    - 既有批次 failed/aborted 且 0 行入库 → 复用批次行重进（清旧失败行，起新 A1 任务）；
    - 既有批次已入库（success_rows>0）→ 409 拒绝（软表无业务唯一键，重跑必重复）。
    """
    payload = request.get_json(silent=True) or {}
    token = (payload.get('token') or '').strip()
    found = _token_file(token)
    if not found:
        return _fail('上传凭证已失效，请重新上传文件')
    path, _ext = found
    try:
        code = eng.validate_table_code(payload.get('code'))
        fields = eng.normalize_fields(payload.get('fields'))
    except eng.SoftTableError as exc:
        return _fail(str(exc))
    name = (payload.get('name') or '').strip()
    if not name:
        return _fail('请填写台账名称')
    module_key = (payload.get('module_key') or 'dynatable').strip()
    if module_key not in MODULE_KEYS:
        return _fail('未知菜单归属：%s' % module_key)

    with open(path, 'rb') as fh:
        source_hash = hashlib.sha256(fh.read()).hexdigest()

    task = None
    # ponytail: 判重查询与建批次行之间存在并发窗口（单进程 waitress + 单操作员场景可忽略）；
    # 如需严格防并发，加 (module_key, table_code, source_hash) 唯一索引即可
    dup = (SofttableBatchImport.query
           .filter_by(module_key=module_key, table_code=code, source_hash=source_hash)
           .first())
    if dup is not None:
        if dup.status in ('failed', 'aborted') and (dup.success_rows or 0) > 0:
            # 已入库的失败/中断批次：重跑必重复（软表无业务唯一键），拒绝并指引改码/换件重导
            return _fail('该批次已有 %d 行入库，整批重跑会产生重复记录；'
                         '请更换台账编码后重新导入，或修改文件内容后重新上传' % dup.success_rows, code=409)
        if dup.status in ('failed', 'aborted'):
            # 0 行入库的失败/中断批次：整批重进（清旧失败行，复用批次行，起新 A1 任务）；
            # source_path 以本次上传件重新绑定（旧 token 件可能已清理）；source_file 保留
            # 批次内原名（判重已确认同内容同文件，且首次 execute 已删 meta json）
            SofttableBatchError.query.filter_by(batch_id=dup.batch_id).delete()
            dup.status = 'pending'
            dup.fail_rows = 0
            dup.total_rows = 0
            dup.source_path = path
            batch = dup
            log_operation(_who(), 'dynatable', 'add',
                          '软表异步导入重进 %s（%s）' % (name, code),
                          '前次失败（0 行入库），重试导入')
        else:
            return _ok({'batch_id': dup.batch_id, 'task_id': dup.task_id,
                        'duplicate': True, 'status': dup.status},
                       msg='该文件在此台账已建过导入批次（状态 %s），返回既有批次'
                           % dup.status)
    else:
        try:
            # 门面 seam（ADR-027）：建表校验（code 查重/module_key 白名单）全在 facade
            facade.create_table(module_key, code, name,
                                group_name=(payload.get('group_name') or '').strip() or None,
                                fields=fields, created_by=_who())
        except eng.SoftTableError as exc:
            return _fail(str(exc), code=409)
        task = create_task(TASK_TYPE, _who(), '软表批量导入 %s' % name)
        batch = SofttableBatchImport(
            batch_id=uuid.uuid4().hex[:12], module_key=module_key, table_code=code,
            source_file=_meta_name(token), source_path=path, source_hash=source_hash,
            operator=_who(), status='pending', task_id=task.task_id)
        db.session.add(batch)
        db.session.commit()
        log_operation(_who(), 'dynatable', 'add',
                      '软表异步导入 %s（%s）' % (name, code),
                      '字段 %d 个，文件 %s，后台分批执行' % (len(fields), _meta_name(token)))
        # meta 不再需要（失败重进不需 meta）；源文件由执行体完成后清理
        meta_file = _meta_path(token)
        if os.path.exists(meta_file):
            os.remove(meta_file)

    if task is None:  # 重进路径：新起 A1 任务并回写批次
        task = create_task(TASK_TYPE, _who(), '软表批量导入 %s（重进）' % name)
        batch.task_id = task.task_id
        db.session.commit()
    # 懒导入（同 routes/election.py 惯例）：测试 monkeypatch async_tasks.run_async 可拦截
    from utils.async_tasks import run_async
    run_async(TASK_TYPE, task.task_id, run_softtable_batch, batch_id=batch.batch_id)
    return _ok({'batch_id': batch.batch_id, 'task_id': task.task_id},
               msg='已提交后台分批导入')


@dynatable_bp.route('/api/import-status/<task_id>')
@login_required
def api_import_status(task_id):
    """107 进度轮询（A1 轨迹：pending→running→done/failed；进度单调递增）。"""
    t = get_task(task_id)
    if not t:
        return _fail('任务不存在', code=404)
    return _ok({'task_id': t.task_id, 'status': t.status, 'progress': t.progress,
                'result_summary': t.result_summary})


@dynatable_bp.route('/api/import-report/<batch_id>')
@login_required
def api_import_report(batch_id):
    """107 导入结果报告：total/success/fail 对账 + 失败行（仅 行号/列名/原因，无原值）。"""
    batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
    if not batch:
        return _fail('批次不存在', code=404)
    errors = [{'row_no': e.row_no, 'field_name': e.field_name, 'reason': e.reason}
              for e in SofttableBatchError.query.filter_by(batch_id=batch_id)
              .order_by(SofttableBatchError.row_no).limit(200).all()]
    return _ok({'batch_id': batch.batch_id, 'table_code': batch.table_code,
                'module_key': batch.module_key, 'status': batch.status,
                'total_rows': batch.total_rows or 0,
                'success_rows': batch.success_rows or 0,
                'fail_rows': batch.fail_rows or 0,
                'errors': errors})


@dynatable_bp.route('/api/import-export-failed/<batch_id>')
@login_required
def api_import_export_failed(batch_id):
    """107 仅导出失败行：write_export_xlsx token 通道。

    失败行暂存表不存任何行原值，导出只有 数据行号/出错列/失败原因——无明文可泄。
    """
    batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
    if not batch:
        return _fail('批次不存在', code=404)
    errs = SofttableBatchError.query.filter_by(batch_id=batch_id)\
        .order_by(SofttableBatchError.row_no).all()
    rows = [[e.row_no, e.field_name or '', e.reason or ''] for e in errs]
    abs_path, token_url = write_export_xlsx(
        ['数据行号', '出错列', '失败原因'], rows,
        module='dynatable', filename='软表导入失败行-%s.xlsx' % batch.table_code)
    if not token_url:
        return _fail('导出通道未就绪', code=500)
    log_operation(_who(), 'dynatable', 'export',
                  '导出软表导入失败行（%s，%d 行）' % (batch.table_code, len(rows)))
    return _ok({'download_url': '/report/download/%s' % token_url,
                'file': os.path.basename(abs_path)}, msg='导出成功')


# ------------------------------------------------------------------ 台账清单

@dynatable_bp.route('/api/list')
@login_required
def api_tables():
    rows = DynamicTable.query.order_by(DynamicTable.id.desc()).all()
    data = []
    for t in rows:
        data.append({
            'id': t.id, 'code': t.code, 'name': t.name, 'module_key': t.module_key,
            # 中文归属随行下发：列表「菜单归属」列直接展示 module_label，
            # 前端不再维护第二份映射（键仍原样返回，供编辑/排障使用）
            'module_label': label_for(t.module_key),
            'group_name': t.group_name or '', 'description': t.description or '',
            'field_count': DynamicField.query.filter_by(table_id=t.id).count(),
            'record_count': DynamicRecord.query.filter_by(table_id=t.id).count(),
            'updated_at': t.updated_at.strftime('%Y-%m-%d %H:%M') if t.updated_at else '',
        })
    return _ok(data, total=len(data))


# ------------------------------------------------------------------ 数据列表

@dynatable_bp.route('/api/t/<code>/records')
@login_required
def api_records(code):
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    specs = _field_specs(table)
    fields = _table_fields(table)
    # 107（078 遗留 P2）：MAX_LIST_ROWS 是列表展示口径——截断后 total 不再静默失真：
    # truncated 标记 + total_records（库内真实行数）随响应下发，前端显式提示「已截断」。
    all_records = _rows_of(table)
    truncated = len(all_records) > MAX_LIST_ROWS
    records = all_records[:MAX_LIST_ROWS] if truncated else all_records
    decoded = eng.decode_records(specs, records)

    filters, ranges, sort_field, sort_dir = {}, {}, None, 'asc'
    for key, value in request.args.items():
        if key.startswith('f_') and key.endswith('_min'):
            ranges.setdefault(key[2:-4], {})['min'] = value
        elif key.startswith('f_') and key.endswith('_max'):
            ranges.setdefault(key[2:-4], {})['max'] = value
        elif key.startswith('f_'):
            filters[key[2:]] = value
    filters.update({k: v for k, v in ranges.items() if v})
    sort_field = request.args.get('sort') or None
    sort_dir = request.args.get('dir') or 'asc'

    rows = eng.select_rows(decoded, specs, filters, sort_field, sort_dir,
                           digest_fn=_digest_fn())
    page = max(request.args.get('page', 1, type=int), 1)
    limit = min(max(request.args.get('limit', 20, type=int), 1), 200)
    start = (page - 1) * limit
    # 指纹只在服务端参与筛选，不回传前端
    page_rows = [{k: v for k, v in r.items() if k != '__digest__'}
                 for r in rows[start:start + limit]]
    return _ok(page_rows, total=len(rows), truncated=truncated,
               total_records=len(all_records))


@dynatable_bp.route('/api/t/<code>/record/<int:rid>', methods=['POST'])
@login_required
def api_update_record(code, rid):
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    record = DynamicRecord.query.filter_by(id=rid, table_id=table.id).first()
    if not record:
        return _fail('记录不存在', code=404)
    payload = request.get_json(silent=True) or {}
    cell_code = (payload.get('code') or '').strip()
    field = DynamicField.query.filter_by(table_id=table.id, code=cell_code).first()
    if not field:
        return _fail('字段不存在')
    spec = _field_specs(table)
    field_spec = next((s for s in spec if s['code'] == cell_code), None)
    try:
        value = eng.coerce_value(field_spec, payload.get('value'))
    except eng.SoftTableError as exc:
        return _fail(str(exc))
    if value is None and field.is_required:
        return _fail('「%s」是必填项' % field.display_name)

    data = json.loads(record.data or '{}')
    digest = json.loads(record.digest or '{}')
    if value is None:
        data.pop(cell_code, None)
        digest.pop(cell_code, None)
    elif field.sensitivity == 'identity':
        key = _hmac_key()
        if not key or not key_holder.is_initialized:
            return _fail('密钥未解锁，无法写入身份标识字段；请重新登录', code=409)
        data[cell_code] = key_holder.encrypt(str(value))
        digest[cell_code] = eng.identity_digest(key, cell_code, str(value))
    else:
        data[cell_code] = value
        digest.pop(cell_code, None)
    record.data = json.dumps(data, ensure_ascii=False, sort_keys=True)
    record.digest = json.dumps(digest, ensure_ascii=False, sort_keys=True) if digest else None
    db.session.commit()
    shown = (eng.mask_identity(str(value))
             if field.sensitivity == 'identity' else value)
    return _ok({'id': rid, 'code': cell_code, 'value': shown}, msg='已保存')


@dynatable_bp.route('/api/t/<code>/record/<int:rid>/reveal', methods=['POST'])
@login_required
@admin_required
def api_reveal_record(code, rid):
    """超管解锁 identity 明文（受控，不写审计日志——设计 §五 明确不设）。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    record = DynamicRecord.query.filter_by(id=rid, table_id=table.id).first()
    if not record:
        return _fail('记录不存在', code=404)
    specs = [f for f in _field_specs(table) if f['sensitivity'] == 'identity']
    decoded = eng.decode_records(specs, [record], reveal=True)
    return _ok({f['code']: decoded[0].get(f['code'], '') for f in specs})


# ------------------------------------------------------------------ 结构修改

@dynatable_bp.route('/api/t/<code>/fields')
@login_required
def api_fields(code):
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    return _ok(_field_specs(table))


@dynatable_bp.route('/api/t/<code>/field/add', methods=['POST'])
@login_required
def api_field_add(code):
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    payload = request.get_json(silent=True) or {}
    existing = [f.code for f in _table_fields(table)]
    spec = {
        'code': eng.next_field_code(existing),
        'display_name': (payload.get('display_name') or '').strip(),
        'data_type': (payload.get('data_type') or 'text').strip(),
        'dict_category_code': (payload.get('dict_category_code') or '').strip() or None,
        'sensitivity': (payload.get('sensitivity') or 'none').strip(),
        'is_required': bool(payload.get('is_required')),
    }
    try:
        normalized = eng.normalize_fields([spec])[0]
    except eng.SoftTableError as exc:
        return _fail(str(exc))
    field = DynamicField(table_id=table.id, code=normalized['code'],
                         display_name=normalized['display_name'],
                         data_type=normalized['data_type'],
                         dict_category_code=normalized['dict_category_code'],
                         sensitivity=normalized['sensitivity'],
                         is_required=normalized['is_required'],
                         sort_order=len(existing))
    db.session.add(field)
    db.session.commit()
    log_operation(_who(), 'dynatable', 'edit', '软表加字段 %s.%s' % (code, field.code),
                  field.display_name)
    return _ok({'code': field.code, 'display_name': field.display_name}, msg='字段已添加')


@dynatable_bp.route('/api/t/<code>/field/<int:fid>/update', methods=['POST'])
@login_required
def api_field_update(code, fid):
    """改名 / 改类型 / 改敏感级别 / 改必填 / 改顺序。改类型与改敏感走「确认 + 备份」。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    field = DynamicField.query.filter_by(id=fid, table_id=table.id).first()
    if not field:
        return _fail('字段不存在', code=404)
    payload = request.get_json(silent=True) or {}
    new_type = (payload.get('data_type') or field.data_type).strip()
    new_sens = (payload.get('sensitivity') or field.sensitivity).strip()
    new_name = (payload.get('display_name') or field.display_name).strip()
    new_cat = (payload.get('dict_category_code') or field.dict_category_code or '').strip() or None
    new_required = bool(payload.get('is_required', field.is_required))
    sort_order = payload.get('sort_order')
    structural = (new_type != field.data_type or new_sens != field.sensitivity
                  or new_cat != field.dict_category_code)

    candidate = {'code': field.code, 'display_name': new_name, 'data_type': new_type,
                 'dict_category_code': new_cat, 'sensitivity': new_sens,
                 'is_required': new_required, 'sort_order': field.sort_order}
    try:
        eng.normalize_fields([candidate])
    except eng.SoftTableError as exc:
        return _fail(str(exc))

    records = _rows_of(table)
    if structural:
        if not payload.get('confirm'):
            return _fail('结构变更需要确认（勾选确认后重试）', code=409,
                         need_confirm=True, affected=len(records))
        try:
            json_path = _snapshot_backup(table, _table_fields(table), records)
        except eng.SoftTableError as exc:
            return _fail(str(exc), code=500)
        # 改类型与改敏感级别都必须重算存量：sensitivity 变化同样会改变
        # 「加密/明文 + 指纹」的落库形态，只挂 new_type 会留下假打码与空指纹（Q-2）。
        if new_type != field.data_type or new_sens != field.sensitivity:
            bad = _retype_values(table, field, candidate, records)
            if bad:
                db.session.rollback()   # _retype_values 已就地改模型，失败必须整笔回退
                return _fail('有 %d 行无法转换为目标类型，已中止（数据未改动，备份：%s）'
                             % (len(bad), os.path.basename(json_path)),
                             code=400, errors=bad[:50])
    field.display_name = new_name[:100]
    field.data_type = new_type
    field.sensitivity = new_sens
    field.dict_category_code = new_cat
    field.is_required = new_required
    if sort_order is not None:
        try:
            field.sort_order = int(sort_order)
        except (TypeError, ValueError):
            pass
    db.session.commit()
    log_operation(_who(), 'dynatable', 'edit',
                  '软表改字段 %s.%s' % (code, field.code),
                  '类型=%s 敏感=%s 必填=%s' % (new_type, new_sens, new_required))
    return _ok({'code': field.code}, msg='字段已更新')


def _retype_values(table, field, candidate, records):
    """把既有值按新类型重算；返回失败行（空列表 = 全部可转换）。"""
    bad = []
    specs = [s for s in _field_specs(table) if s['code'] != field.code] + [candidate]
    field_map = {s['code']: s for s in specs}
    hmac_key = _hmac_key()
    for rec in records:
        try:
            data = json.loads(rec.data or '{}')
        except ValueError:
            data = {}
        if field.code not in data:
            continue
        raw = data[field.code]
        if field.sensitivity == 'identity':
            try:
                raw = key_holder.decrypt(str(raw))
            except Exception:
                bad.append({'id': rec.id, 'msg': '既有密文无法用当前密钥解密'})
                continue
        try:
            value = eng.coerce_value(field_map[field.code], raw)
        except eng.SoftTableError as exc:
            bad.append({'id': rec.id, 'msg': str(exc)})
            continue
        if value is None:
            data.pop(field.code, None)
            continue
        if candidate['sensitivity'] == 'identity':
            if not hmac_key:
                bad.append({'id': rec.id, 'msg': '密钥未解锁'})
                continue
            data[field.code] = key_holder.encrypt(str(value))
            digest = json.loads(rec.digest or '{}')
            digest[field.code] = eng.identity_digest(hmac_key, field.code, str(value))
            rec.digest = json.dumps(digest, ensure_ascii=False, sort_keys=True)
        else:
            data[field.code] = value
            digest = json.loads(rec.digest or '{}')
            digest.pop(field.code, None)
            rec.digest = json.dumps(digest, ensure_ascii=False, sort_keys=True) if digest else None
        rec.data = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return bad


@dynatable_bp.route('/api/t/<code>/field/<int:fid>/delete', methods=['POST'])
@login_required
def api_field_delete(code, fid):
    """删字段：影响行数确认 + 自动备份，确认后从所有行剔除该键。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    field = DynamicField.query.filter_by(id=fid, table_id=table.id).first()
    if not field:
        return _fail('字段不存在', code=404)
    payload = request.get_json(silent=True) or {}
    records = _rows_of(table)
    affected = 0
    for r in records:
        try:
            if field.code in json.loads(r.data or '{}'):
                affected += 1
        except ValueError:
            continue
    if not payload.get('confirm'):
        return _fail('删字段不可撤销，需要确认（影响 %d 行）' % affected,
                     code=409, need_confirm=True, affected=affected)
    try:
        json_path = _snapshot_backup(table, _table_fields(table), records)
    except eng.SoftTableError as exc:
        return _fail(str(exc), code=500)
    for rec in records:
        try:
            data = json.loads(rec.data or '{}')
            digest = json.loads(rec.digest or '{}')
        except ValueError:
            continue
        if field.code in data:
            data.pop(field.code, None)
            rec.data = json.dumps(data, ensure_ascii=False, sort_keys=True)
        if field.code in digest:
            digest.pop(field.code, None)
            rec.digest = json.dumps(digest, ensure_ascii=False, sort_keys=True) if digest else None
    name = field.display_name
    db.session.delete(field)
    db.session.commit()
    log_operation(_who(), 'dynatable', 'delete', '软表删字段 %s.%s' % (code, name),
                  '影响 %d 行，备份 %s' % (affected, os.path.basename(json_path)))
    return _ok({'affected': affected, 'backup': os.path.basename(json_path)}, msg='字段已删除')


@dynatable_bp.route('/api/t/<code>/field/reorder', methods=['POST'])
@login_required
def api_field_reorder(code):
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    payload = request.get_json(silent=True) or {}
    order = payload.get('order') or []
    if not isinstance(order, list) or not order:
        return _fail('请提供字段顺序（code 数组）')
    fields = {f.code: f for f in _table_fields(table)}
    for idx, field_code in enumerate(order):
        if field_code in fields:
            fields[field_code].sort_order = idx
    db.session.commit()
    return _ok({'order': order}, msg='字段顺序已保存')


# ------------------------------------------------------------------ 记录导出（096 返工 A）

@dynatable_bp.route('/api/t/<code>/export')
@login_required
def api_export_records(code):
    """按当前筛选导出全量记录（xlsx）。视图名含 export → 动作位 dynatable.export。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)

    from models import User
    from utils.helpers import export_excel
    from utils.permissions import user_has_special
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    can_view_sensitive = bool(user and (user.role_id == 1
                                        or user_has_special(user, 'view_sensitive')))

    specs = _field_specs(table)
    decoded = eng.decode_records(specs, _rows_of(table), reveal=can_view_sensitive)

    filters, ranges = {}, {}
    for key, value in request.args.items():
        if key.startswith('f_') and key.endswith('_min'):
            ranges.setdefault(key[2:-4], {})['min'] = value
        elif key.startswith('f_') and key.endswith('_max'):
            ranges.setdefault(key[2:-4], {})['max'] = value
        elif key.startswith('f_'):
            filters[key[2:]] = value
    filters.update({k: v for k, v in ranges.items() if v})
    rows = eng.select_rows(decoded, specs, filters,
                           request.args.get('sort') or None,
                           request.args.get('dir') or 'asc',
                           digest_fn=_digest_fn())

    enum_labels = {}
    for spec in specs:
        if spec['data_type'] == 'enum' and spec['dict_category_code']:
            enum_labels[spec['code']] = {
                opt['value']: opt['label']
                for opt in _enum_options(spec['dict_category_code'])}
    headers = [spec['display_name'] for spec in specs]
    out = []
    for row in rows:
        out.append([enum_labels.get(spec['code'], {}).get(row.get(spec['code'], ''),
                                                          row.get(spec['code'], ''))
                    for spec in specs])

    # identity 列已在 decode_records 按 can_view_sensitive 打码/解密，这里 already_masked=True
    # 跳过 export_excel 的按表头二次脱敏；文件名仍按真实 can_view_sensitive 标记（R-096b-01）。
    return export_excel(headers, out, '%s.xlsx' % table.name,
                        can_view_sensitive=can_view_sensitive, already_masked=True)
