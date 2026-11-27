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
import re      # V2 第二阶段 api_v2_clone_period 期次标签转 code 后缀所需（外发补丁漏带，套打补）
import secrets
import time
import uuid

import io
from flask import Blueprint, g, jsonify, render_template, request, send_file, session

import config
from models import (DynamicField, DynamicRecord, DynamicTable, SofttableBatchError,
                    SofttableBatchImport, db)
from utils import roster_facade as facade
from utils import softtable_engine as eng
from utils.softtable_layout import (
    LayoutConflictError,
    SoftTableLayoutStore,
    export_styled_table_xlsx,
    export_workbook_group_xlsx,
    is_masked_placeholder,
    map_resident_to_softtable_row,
)
from utils.softtable_v2_parser import parse_workbook_v2
from datetime import datetime
from utils.async_tasks import create_task, get_task, is_task_busy, sync_failed_pending_batches
from utils.crypto import key_holder
from utils.decorators import admin_required, login_required
from utils.dynatable_modules import MODULE_KEYS, label_for, module_options
from utils.helpers import log_operation, write_export_xlsx
from utils.softtable_batch import ASYNC_ROW_THRESHOLD, TASK_TYPE, run_softtable_batch

dynatable_bp = Blueprint('dynatable', __name__)

# MODULE_KEYS / 中文标签统一由 utils.dynatable_modules 供给（键=持久化值，中文=界面展示）；
# 本文件不再维护第二份键清单，避免「校验白名单」与「下拉选项」漂移。
GROUP_NAMES = ('人口与台账', '社会保障', '业务办理', '民生服务', '应急管理')
ALLOWED_EXT = ('.xlsx', '.xls', '.xlsb', '.csv', '.xlsm')
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
    # C-1：V1 三步向导已退役，confirm 死页转回统一智能传表入口
    from flask import redirect, url_for
    return redirect(url_for('dynatable.upload_page'))


@dynatable_bp.route('/t/<code>')
@login_required
def list_page(code):
    from utils.dynatable_modules import is_builtin_roster_code, tab_group_for
    table = _get_table(code)
    if not table:
        return render_template('dynatable/missing.html', code=code), 404
    nav_module = tab_group_for(table.module_key) if table.module_key != 'dynatable' else None
    return render_template(
        'dynatable/list.html',
        table=table,
        module_label=label_for(table.module_key),
        fields=_fields_with_options(table),
        nav_module=nav_module,
        nav_active=('dt:' + table.code) if nav_module else '',
        is_builtin=bool(is_builtin_roster_code(table.module_key, table.code)),
    )


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
        return _fail('仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式文件')
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
    existing = _get_table(code)
    if existing is not None and (existing.module_key != (payload.get('module_key') or 'dynatable').strip()
                                 or facade.is_built(existing)):
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
        table = facade.create_table(
            module_key, code, name,
            group_name=(payload.get('group_name') or '').strip() or None,
            fields=fields, created_by=_who(),
            description=(payload.get('description') or '').strip() or None,
        )
        for rec in records:
            db.session.add(DynamicRecord(table_id=table.id, data=rec['data'],
                                         digest=rec['digest'], created_by=_who()))
        safety_folder_id = payload.get('safety_folder_id')
        if module_key == 'safety' and safety_folder_id:
            from routes.safety import link_safety_table_node
            link_safety_table_node(int(safety_folder_id), code, name, created_by=_who())
        db.session.commit()
    except eng.SoftTableError as exc:
        db.session.rollback()
        return _fail(str(exc))
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
        sync_failed_pending_batches([dup])
    if dup is not None:
        if dup.status in ('failed', 'aborted') and (dup.success_rows or 0) > 0:
            # 已入库的失败/中断批次：重跑必重复（软表无业务唯一键），拒绝并指引改码/换件重导
            return _fail('该批次已有 %d 行入库，整批重跑会产生重复记录；'
                         '请更换台账编码后重新导入，或修改文件内容后重新上传' % dup.success_rows, code=409)
        if dup.status in ('failed', 'aborted'):
            if is_task_busy(TASK_TYPE):
                return _fail('并发互斥：已有软表导入任务正在运行，请稍后重试', code=409)
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
        if is_task_busy(TASK_TYPE):
            return _fail('并发互斥：已有软表导入任务正在运行，请稍后重试', code=409)
        try:
            # 门面 seam（ADR-027）：建表校验（code 查重/module_key 白名单）全在 facade
            facade.create_table(module_key, code, name,
                                group_name=(payload.get('group_name') or '').strip() or None,
                                fields=fields, created_by=_who())
            safety_folder_id = payload.get('safety_folder_id')
            if module_key == 'safety' and safety_folder_id:
                from routes.safety import link_safety_table_node
                link_safety_table_node(int(safety_folder_id), code, name, created_by=_who())
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
    accepted = run_async(TASK_TYPE, task.task_id, run_softtable_batch, batch_id=batch.batch_id)
    if accepted is False:
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        return _fail('并发互斥：已有软表导入任务正在运行，请稍后重试', code=409,
                     batch_id=batch.batch_id, task_id=task.task_id)
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
    sync_failed_pending_batches([batch])
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
    from utils.dynatable_modules import is_builtin_roster_code
    rows = DynamicTable.query.order_by(DynamicTable.id.desc()).all()
    store = _layout_store()
    data = []
    for t in rows:
        lay = store.get_layout(t.code, fallback_title=t.name, col_count=1)
        data.append({
            'id': t.id, 'code': t.code, 'name': t.name, 'module_key': t.module_key,
            # 中文归属随行下发：列表「菜单归属」列直接展示 module_label，
            # 前端不再维护第二份映射（键仍原样返回，供编辑/排障使用）
            'module_label': label_for(t.module_key),
            'group_name': t.group_name or '', 'description': t.description or '',
            'period_tag': (lay or {}).get('period_tag', ''),
            'is_builtin': bool(is_builtin_roster_code(t.module_key, t.code)),
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
    q_kw = (request.args.get('q') or '').strip()
    if q_kw:
        filters['__q__'] = q_kw
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
        if is_masked_placeholder(value) and not payload.get('allow_masked'):
            return _fail('敏感字段处于脱敏状态，请先点击查看明文后再修改，防止覆盖真实密文', code=400)
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
    # C-1：organization 软表编辑保存后静默单向反哺（失败不阻断保存主流程）
    _maybe_sync_org(table)
    shown = (eng.mask_identity(str(value))
             if field.sensitivity == 'identity' else value)
    return _ok({'id': rid, 'code': cell_code, 'value': shown}, msg='已保存')


@dynatable_bp.route('/api/t/<code>/record/<int:rid>/reveal', methods=['POST'])
@login_required
def api_reveal_record(code, rid):
    """受控解锁 identity 明文：超管或具备 view_sensitive 特权位角色均可调用。"""
    from models import User
    from utils.permissions import user_has_special
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    if not user or not (user.role_id == 1 or user_has_special(user, 'view_sensitive')):
        return _fail('无权查看敏感身份标识明文', code=403)
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    record = DynamicRecord.query.filter_by(id=rid, table_id=table.id).first()
    if not record:
        return _fail('记录不存在', code=404)
    specs = [f for f in _field_specs(table) if f['sensitivity'] == 'identity']
    decoded = eng.decode_records(specs, [record], reveal=True)
    g.skip_masking = True
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
    q_kw = (request.args.get('q') or '').strip()
    if q_kw:
        filters['__q__'] = q_kw
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
    use_styled = (request.args.get('raw') != '1') and (
        request.args.get('styled') == '1' or _layout_store().exists(code)
    )
    if use_styled:
        layout = _layout_store().get_layout(code, fallback_title=table.name, col_count=len(headers))
        xlsx_bytes = export_styled_table_xlsx(headers, out, layout, table_name=table.name)
        suffix = '（明文）' if can_view_sensitive else '（脱敏）'
        return send_file(
            io.BytesIO(xlsx_bytes),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'{table.name}{suffix}.xlsx',
        )
    return export_excel(headers, out, '%s.xlsx' % table.name,
                        can_view_sensitive=can_view_sensitive, already_masked=True)

# ------------------------------------------------------------------ 已有软表二次批量导入（第一批闭环）

@dynatable_bp.route('/api/t/<code>/append-upload', methods=['POST'])
@login_required
def api_append_upload(code):
    """已有软表二次追加导入：上传解析并按表头自动匹配现有字段。"""
    table = _get_table(code)
    if not table or not facade.is_built(table):
        return _fail('台账不存在或尚未建表', code=404)
    raw = request.files.get('file')
    if not raw or not raw.filename:
        return _fail('请选择要上传的 .xlsx / .csv 文件')
    filename = raw.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return _fail('仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式文件')
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
    except eng.SoftTableError as exc:
        if os.path.exists(dest):
            os.remove(dest)
        return _fail(str(exc))

    specs = _field_specs(table)
    field_matches, unmatched_headers, missing_required = facade.match_append_headers(specs, headers)
    meta = {'filename': filename, 'headers': headers, 'row_count': len(rows),
            'uploaded_by': _who(), 'code': code, 'mode': 'append'}
    with open(_meta_path(token), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False)
    sample = [[eng._as_text(c) for c in row[:len(headers)]] for row in rows[:5]]
    return _ok({
        'token': token, 'filename': filename, 'headers': headers,
        'fields': field_matches, 'unmatched_headers': unmatched_headers,
        'missing_required': missing_required, 'row_count': len(rows),
        'async_min_rows': ASYNC_ROW_THRESHOLD,
        'will_async': len(rows) > ASYNC_ROW_THRESHOLD,
        'sample': sample,
    })


@dynatable_bp.route('/api/t/<code>/append-execute', methods=['POST'])
@login_required
def api_append_execute(code):
    """已有软表二次追加执行：≤2000 行同步落库，>2000 行走 A1 异步分批。"""
    from utils.roster_routes import start_softtable_async_batch

    table = _get_table(code)
    if not table or not facade.is_built(table):
        return _fail('台账不存在或尚未建表', code=404)
    payload = request.get_json(silent=True) or {}
    token = (payload.get('token') or '').strip()
    found = _token_file(token)
    if not found:
        return _fail('上传凭证已失效，请重新上传文件')
    path, _ext = found
    specs = _field_specs(table)
    filename = _meta_name(token)
    try:
        with open(path, 'rb') as fh:
            headers, rows = eng.parse_table_file(filename, fh)
        clean_map = facade.normalize_column_map(specs, headers, payload.get('column_map'))
    except eng.SoftTableError as exc:
        return _fail(str(exc))

    if len(rows) > ASYNC_ROW_THRESHOLD:
        ok, status_code, data, msg = start_softtable_async_batch(
            module_key=table.module_key, table_code=code, table_name=table.name,
            source_path=path, source_file=filename, operator=_who(),
            log_module='dynatable', log_action_title='软表追加导入',
            column_map=clean_map)
        meta_file = _meta_path(token)
        if ok and os.path.exists(meta_file):
            os.remove(meta_file)
        return (_ok(data, msg=msg) if ok else _fail(msg, code=status_code))

    projected = facade.project_rows_by_map(specs, headers, rows, clean_map)
    try:
        _recs, errors = eng.build_records(specs, projected)
    except eng.SoftTableError as exc:
        return _fail(str(exc))
    if errors and not payload.get('skip_error_rows'):
        return _fail('有 %d 行数据不合规，未导入（可勾选「跳过错误行」后重试）' % len(errors),
                     code=400, errors=errors[:50], error_total=len(errors))
    try:
        imported, _errs = facade.import_records(
            table, specs, projected, skip_error_rows=True, created_by=_who())
    except eng.SoftTableError as exc:
        db.session.rollback()
        return _fail(str(exc))
    except Exception as exc:
        db.session.rollback()
        return _fail('导入失败：%s' % exc, code=500)

    log_operation(_who(), 'dynatable', 'add',
                  '软表追加导入 %s（%s）' % (table.name, code),
                  '导入 %d 行，跳过 %d 行' % (imported, len(errors)))
    meta_file = _meta_path(token)
    if os.path.exists(meta_file):
        os.remove(meta_file)
    if os.path.exists(path):
        os.remove(path)
    return _ok({'mode': 'sync', 'code': code, 'imported': imported, 'skipped': len(errors)},
               msg='导入完成，已追加 %d 行' % imported, errors=errors[:20])


# ------------------------------------------------------------------ 软表引擎 V2（结构感知导入 + 版式快照 + 整本导出）

def _layout_store():
    # DATA_DIR 是 config 模块级常量（不在 Config 类上）：外发补丁原写作 Config.DATA_DIR，
    # 套打实测 AttributeError → V2 建表/版式/整本导出/样式导出四个入口全 500，已修。
    return SoftTableLayoutStore(os.path.join(config.DATA_DIR, 'softtable_layouts'))


def _v2_meta_path(token: str) -> str:
    return os.path.join(_temp_dir(), token + '.v2.json')


def _cleanup_orphan_layouts(store, created):
    """批量建表失败时清掉本次已落盘的版式（库已回滚，版式文件不能留成孤儿）。"""
    for item in created:
        try:
            store.delete_layout(item['code'])
        except OSError:
            pass


@dynatable_bp.route('/api/upload-v2', methods=['POST'])
@login_required
def api_upload_v2():
    """V2 结构感知上传：扫描全部 Sheet，自动识别 4 类形态与 6 项生产短板。"""
    raw = request.files.get('file')
    if not raw or not raw.filename:
        return _fail('请选择要上传的 Excel / CSV 文件')
    filename = raw.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        return _fail('仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式文件')
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
            parsed = parse_workbook_v2(filename, fh)
    except Exception as exc:
        if os.path.exists(dest):
            os.remove(dest)
        return _fail('V2 解析失败：%s' % exc)

    parsed['token'] = token
    parsed['uploaded_by'] = _who()
    # C-1：文件名 + 全列名指纹智能分流（内置表 / 物理底账 / V2 复杂表推荐挂载模块）
    from utils.softtable_smart_route import detect_smart_route
    parsed['smart_route'] = detect_smart_route(filename, parsed)
    with open(_v2_meta_path(token), 'w', encoding='utf-8') as fh:
        json.dump(parsed, fh, ensure_ascii=False)
    return _ok(parsed)


@dynatable_bp.route('/api/reparse-v2', methods=['POST'])
@login_required
def api_reparse_v2():
    """人工指定表头起止行后重解析（兜底 3 层复合表头/斜线表头）。"""
    payload = request.get_json(silent=True) or {}
    token = (payload.get('token') or '').strip()
    found = _token_file(token)
    meta_file = _v2_meta_path(token)
    if not found or not os.path.exists(meta_file):
        return _fail('上传凭证已失效，请重新上传文件', code=404)
    path, _ext = found
    with open(meta_file, encoding='utf-8') as fh:
        old_meta = json.load(fh)
    try:
        with open(path, 'rb') as fh:
            parsed = parse_workbook_v2(
                old_meta.get('filename') or os.path.basename(path),
                fh,
                header_overrides=payload.get('header_overrides') or {},
            )
    except Exception as exc:
        return _fail('重解析失败：%s' % exc)
    parsed['token'] = token
    parsed['uploaded_by'] = _who()
    with open(meta_file, 'w', encoding='utf-8') as fh:
        json.dump(parsed, fh, ensure_ascii=False)
    return _ok(parsed)


@dynatable_bp.route('/api/create-batch', methods=['POST'])
@login_required
def api_create_batch():
    """一次建出多个同工作簿子表，加密入库并落盘各自 LayoutSpec。"""
    payload = request.get_json(silent=True) or {}
    token = (payload.get('token') or '').strip()
    found = _token_file(token)
    meta_file = _v2_meta_path(token)
    if not found or not os.path.exists(meta_file):
        return _fail('上传凭证已失效，请重新上传文件')
    path, _ext = found
    module_key = (payload.get('module_key') or 'dynatable').strip()
    if module_key not in MODULE_KEYS:
        return _fail('未知菜单归属：%s' % module_key)
    group_name = (payload.get('group_name') or '').strip() or None
    period_tag = (payload.get('period_tag') or '').strip()
    items = payload.get('items') or []
    if not items:
        return _fail('请至少选择 1 张子表')

    with open(meta_file, encoding='utf-8') as fh:
        parsed = json.load(fh)
    sheet_map = {sh['sheet_name']: sh for sh in parsed.get('sheets') or []}
    skip_error_rows = str(payload.get('skip_error_rows')).lower() in ('1', 'true', 'yes')

    # 阶段一：全量预检，任何一项不过即整笔拒绝 —— 此时库与版式目录均未写入。
    # （外发补丁原把校验放在落库循环内：中途 return 绕过 except，不回滚也不清版式；
    #   且无论是否勾选都静默丢弃错误行，与 /api/create 的口径不一致，已修。）
    plans, all_errors = [], []
    for item in items:
        try:
            code = eng.validate_table_code(item.get('code'))
        except eng.SoftTableError as exc:
            return _fail(str(exc))
        name = (item.get('name') or '').strip()
        if not name:
            return _fail('存在未填写名称的子表')
        if _get_table(code) is not None:
            return _fail('表标识 %s 已存在，请修改后重试' % code)
        sh = sheet_map.get(item.get('sheet_name'))
        t_idx = int(item.get('table_idx', 0))
        if not sh or t_idx >= len(sh.get('tables') or []):
            return _fail('找不到对应的子表解析数据：%s' % name)
        t_data = sh['tables'][t_idx]
        raw_fields = item.get('fields') or t_data.get('fields') or []
        try:
            norm_fields = eng.normalize_fields([
                {
                    'code': f.get('code') or f'f{i:02d}',
                    'display_name': f.get('display_name') or f'列{i + 1}',
                    'data_type': f.get('data_type') or 'text',
                    'sensitivity': f.get('sensitivity') or 'none',
                    'is_required': bool(f.get('is_required')),
                }
                for i, f in enumerate(raw_fields)
            ])
        except eng.SoftTableError as exc:
            return _fail('%s：%s' % (name, exc))
        try:
            records, errors = eng.build_records(norm_fields, t_data.get('rows') or [])
        except eng.SoftTableError as exc:
            # 业务错误（如「密钥未解锁，无法写入身份标识字段」）须以可读消息返回，不能冒成 500
            return _fail(str(exc))
        all_errors.extend(errors)
        plans.append((code, name, norm_fields, records, errors, t_data.get('layout')))

    if all_errors and not skip_error_rows:
        return _fail('有 %d 行数据不合规，未建表（可勾选「跳过错误行」后重试）' % len(all_errors),
                     code=400, errors=all_errors[:50], error_total=len(all_errors))

    # 阶段二：统一落库；失败则回滚库并清掉本次已落盘的版式，不留孤儿版式文件。
    created = []
    store = _layout_store()
    try:
        for code, name, norm_fields, records, errors, base_layout in plans:
            table = facade.create_table(
                module_key, code, name,
                group_name=group_name,
                fields=norm_fields, created_by=_who(),
                description=f'V2 批量导入（{parsed.get("filename", "")}）',
            )
            for rec in records:
                db.session.add(DynamicRecord(
                    table_id=table.id, data=rec['data'],
                    digest=rec['digest'], created_by=_who(),
                ))
            layout = dict(base_layout or {})
            if group_name:
                layout['workbook_group'] = group_name
            if period_tag:
                layout['period_tag'] = period_tag
            if 'combine_on_single_page' in payload and payload.get('combine_on_single_page') is not None:
                layout['combine_on_single_page'] = bool(payload.get('combine_on_single_page'))
            safety_folder_id = payload.get('safety_folder_id')
            if module_key == 'safety' and safety_folder_id:
                from routes.safety import link_safety_table_node
                link_safety_table_node(int(safety_folder_id), code, name, created_by=_who())
            store.save_layout(code, layout)
            created.append({'code': code, 'name': name, 'imported': len(records), 'skipped': len(errors)})
        db.session.commit()
        # C-1：挂 organization 的软表建表后静默单向反哺 OrganizationMember（失败不影响建表主流程）
        if module_key == 'organization':
            for item_c in created:
                _maybe_sync_org(_get_table(item_c['code']))
    except eng.SoftTableError as exc:
        db.session.rollback()
        _cleanup_orphan_layouts(store, created)
        return _fail(str(exc))
    except Exception as exc:
        db.session.rollback()
        _cleanup_orphan_layouts(store, created)
        return _fail('批量建表失败：%s' % exc, code=500)

    log_operation(_who(), 'dynatable', 'add',
                  '软表V2批量建表 %d 张（%s）' % (len(created), group_name or ''),
                  ', '.join(c['code'] for c in created))
    for p in (meta_file, path):
        if os.path.exists(p):
            os.remove(p)
    return _ok({'created': created}, msg='批量建表完成，共创建 %d 张台账' % len(created))


@dynatable_bp.route('/api/t/<code>/layout', methods=['GET', 'POST'])
@login_required
def api_table_layout(code):
    """读写单张软表的 LayoutSpec（POST 带 version_ts 乐观锁防并发覆写）。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    store = _layout_store()
    if request.method == 'GET':
        specs = _field_specs(table)
        return _ok(store.get_layout(code, fallback_title=table.name, col_count=len(specs)))

    payload = request.get_json(silent=True) or {}
    expected_ts = payload.pop('expected_version_ts', None)
    try:
        saved = store.save_layout(code, payload, expected_version_ts=expected_ts)
    except LayoutConflictError as exc:
        return _fail(str(exc), code=409)
    except ValueError as exc:
        return _fail(str(exc), code=400)
    log_operation(_who(), 'dynatable', 'edit', '更新软表版式 %s' % code, '')
    return _ok(saved, msg='版式已保存')


@dynatable_bp.route('/api/group/<path:group_name>/export')
@login_required
def api_export_workbook_group(group_name):
    """按 group_name 导出整本 1:1 生产工作簿（支持多 Sheet / 单页多区块 / 左右并排双表）。"""
    tables = DynamicTable.query.filter_by(group_name=group_name).order_by(DynamicTable.id.asc()).all()
    if not tables:
        return _fail('未找到该分组下的软表台账', code=404)

    from models import User
    from utils.permissions import user_has_special
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    can_view_sensitive = bool(user and (user.role_id == 1 or user_has_special(user, 'view_sensitive')))
    store = _layout_store()

    bundles = []
    for tbl in tables:
        specs = _field_specs(tbl)
        decoded = eng.decode_records(specs, _rows_of(tbl), reveal=can_view_sensitive)
        headers = [s['display_name'] for s in specs]
        rows = [[row.get(s['code'], '') for s in specs] for row in decoded]
        layout = store.get_layout(tbl.code, fallback_title=tbl.name, col_count=len(headers))
        bundles.append((tbl.name, headers, rows, layout))

    xlsx_bytes = export_workbook_group_xlsx(bundles, group_name=group_name)
    suffix = '（明文）' if can_view_sensitive else '（脱敏）'
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'{group_name}{suffix}.xlsx',
    )


# ------------------------------------------------------------------ 软表引擎 V2 第二阶段（A4 视图数据 / 行级增删回填 / 期次结转）

@dynatable_bp.route('/api/residents/search')
@login_required
def api_v2_search_residents():
    from models import Resident
    q = (request.args.get('q') or '').strip()
    query = Resident.query.filter(Resident.status == 'normal')
    if q:
        query = query.filter(Resident.name.contains(q))
    rows = query.order_by(Resident.id.desc()).limit(25).all()
    return _ok([
        {'id': r.id, 'name': r.name, 'label': f'{r.name}（{r.gender or "-"} · {r.village_group or "未分组"}）'}
        for r in rows
    ])


@dynatable_bp.route('/api/t/<code>/a4-data')
@login_required
def api_v2_a4_data(code):
    """加载单张软表的 A4 视图数据包（默认上限 300 行保流畅，敏感列默认脱敏）。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    specs = _field_specs(table)
    all_recs = _rows_of(table)
    limit_cap = 300
    truncated = len(all_recs) > limit_cap
    decoded = eng.decode_records(specs, all_recs[:limit_cap], reveal=False)
    clean_rows = [{k: v for k, v in r.items() if k != '__digest__'} for r in decoded]
    store = _layout_store()
    layout = store.get_layout(code, fallback_title=table.name, col_count=len(specs))
    siblings = []
    if table.group_name:
        sib_tables = (DynamicTable.query.filter_by(group_name=table.group_name)
                      .order_by(DynamicTable.id.asc()).all())
        combine = bool(layout.get('combine_on_single_page'))
        for st in sib_tables:
            st_specs = _field_specs(st)
            st_lay = store.get_layout(st.code, fallback_title=st.name, col_count=len(st_specs))
            entry = {
                'code': st.code,
                'name': st.name,
                'sheet_order': st_lay.get('sheet_order', 0),
                'panel_role': st_lay.get('panel_role'),
                'panel_title': st_lay.get('panel_title'),
            }
            if combine:
                st_recs = _rows_of(st)[:limit_cap]
                st_dec = eng.decode_records(st_specs, st_recs, reveal=False)
                entry['fields'] = _fields_with_options(st)
                entry['layout'] = st_lay
                entry['records'] = [{k: v for k, v in r.items() if k != '__digest__'} for r in st_dec]
            siblings.append(entry)
        siblings.sort(key=lambda x: x.get('sheet_order', 0))
    return _ok({
        'table': {'code': table.code, 'name': table.name, 'group_name': table.group_name or ''},
        'fields': _fields_with_options(table),
        'layout': layout,
        'records': clean_rows,
        'truncated': truncated,
        'total': len(all_recs),
        'siblings': siblings,
    })


@dynatable_bp.route('/api/t/<code>/record/create', methods=['POST'])
@login_required
def api_v2_create_record(code):
    """新增单条软表记录（支持空白手填行或指定 resident_id 一键回填并加密入库）。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    specs = _field_specs(table)
    payload = request.get_json(silent=True) or {}
    val_map = dict(payload.get('values') or {})

    resident_id = payload.get('resident_id')
    if resident_id:
        from models import Dibao, Disabled, PartyMember, Resident  # 真实模型名：Dibao/Disabled（外发补丁误写 DibaoRecord/DisabledPerson，套打修）
        from utils.docx_form_engine import build_resident_autofill_dict
        resident = Resident.query.get(int(resident_id))
        if not resident:
            return _fail('居民不存在', code=404)
        disabled = Disabled.query.filter_by(resident_id=resident.id).first()
        dibao = Dibao.query.filter_by(resident_id=resident.id).first()
        # 党员表无 resident_id 外键（119 主档联动同款）：按居民 id_card_hash 关联
        party = None
        if getattr(resident, 'id_card_hash', None):
            party = PartyMember.query.filter_by(id_card_hash=resident.id_card_hash).first()
        res_info = build_resident_autofill_dict(resident, disabled=disabled, dibao=dibao)
        res_info['education'] = getattr(resident, 'education', '') or ''
        res_info['politics_status'] = getattr(resident, 'politics_status', '') or ''
        if party and getattr(party, 'join_date', None):
            res_info['join_date'] = party.join_date.strftime('%Y-%m-%d')
        next_seq = DynamicRecord.query.filter_by(table_id=table.id).count() + 1
        mapped = map_resident_to_softtable_row(specs, res_info, next_seq=next_seq)
        mapped.update(val_map)
        val_map = mapped

    row_list = [val_map.get(s['code'], '') for s in specs]
    try:
        recs, errors = eng.build_records(specs, [row_list])
    except eng.SoftTableError as exc:
        return _fail(str(exc))
    if errors and not recs:
        return _fail('新增行校验失败：%s' % errors[0].get('reason', ''), code=400)
    rec_payload = recs[0] if recs else {'data': '{}', 'digest': None}
    rec = DynamicRecord(table_id=table.id, data=rec_payload['data'],
                        digest=rec_payload['digest'], created_by=_who())
    db.session.add(rec)
    db.session.commit()
    decoded = eng.decode_records(specs, [rec], reveal=False)[0]
    decoded.pop('__digest__', None)
    return _ok(decoded, msg='已新增记录')


@dynatable_bp.route('/api/t/<code>/record/<int:rid>/delete', methods=['POST'])
@login_required
def api_v2_delete_record(code, rid):
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    rec = DynamicRecord.query.filter_by(id=rid, table_id=table.id).first()
    if not rec:
        return _fail('记录不存在', code=404)
    db.session.delete(rec)
    db.session.commit()
    log_operation(_who(), 'dynatable', 'delete', f'删除软表记录 {code}#{rid}', '')
    return _ok({'id': rid}, msg='记录已删除')


@dynatable_bp.route('/api/t/<code>/clone-period', methods=['POST'])
@login_required
def api_v2_clone_period(code):
    """复制为新期次 / 新届次（克隆表结构与 LayoutSpec，可选沿用上期名单或清空重填）。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    payload = request.get_json(silent=True) or {}
    period_tag = (payload.get('period_tag') or '').strip()
    if not period_tag:
        return _fail('请填写新期次或届次标签（如 2026-12）')
    copy_records = bool(payload.get('copy_records', False))

    suffix = re.sub(r'[^a-z0-9]+', '_', period_tag.lower()).strip('_') or secrets.token_hex(3)
    new_code = f'{code[:40]}_{suffix}'[:58]
    if _get_table(new_code) is not None:
        new_code = f'{code[:36]}_{suffix[:10]}_{secrets.token_hex(2)}'[:58]
    new_name = f'{table.name}（{period_tag}）'

    specs = _field_specs(table)
    store = _layout_store()
    cloned_list = [{'code': new_code, 'name': new_name, 'copied': 0}]
    # #10 整组结转原子性：全部克隆（主表+兄弟表）在同一事务内一次 commit——
    # 补丁原版主表先行 commit、兄弟表循环无 try/except，中途失败裸 500 且
    # 已落盘版式 JSON 不可随 DB 回滚（132 #6「循环内 return 绕过 except」同类）。
    try:
        new_tbl = facade.create_table(
            table.module_key, new_code, new_name,
            group_name=table.group_name,
            fields=specs, created_by=_who(),
            description=f'由「{table.name}」结转生成（期次：{period_tag}）',
        )
        copied = 0
        if copy_records:
            for r in _rows_of(table):
                db.session.add(DynamicRecord(
                    table_id=new_tbl.id, data=r.data, digest=r.digest, created_by=_who(),
                ))
                copied += 1
        _layout_store().clone_layout_for_period(code, new_code, period_tag, new_title=new_name)
        cloned_list[0]['copied'] = copied

        if payload.get('clone_group') and table.group_name:
            new_group = f'{table.group_name}（{period_tag}）'
            new_tbl.group_name = new_group
            sibs = DynamicTable.query.filter(
                DynamicTable.group_name == table.group_name,
                DynamicTable.id != table.id,
            ).order_by(DynamicTable.id.asc()).all()
            for sib in sibs:
                s_code = f'{sib.code[:36]}_{suffix[:10]}_{secrets.token_hex(2)}'[:58]
                s_name = f'{sib.name}（{period_tag}）'
                s_specs = _field_specs(sib)
                s_new = facade.create_table(
                    sib.module_key, s_code, s_name,
                    group_name=new_group, fields=s_specs, created_by=_who(),
                    description=f'由「{sib.name}」整组结转生成（期次：{period_tag}）',
                )
                s_copied = 0
                if copy_records:
                    for sr in _rows_of(sib):
                        db.session.add(DynamicRecord(
                            table_id=s_new.id, data=sr.data, digest=sr.digest, created_by=_who(),
                        ))
                        s_copied += 1
                s_lay = _layout_store().clone_layout_for_period(sib.code, s_code, period_tag, new_title=s_name)
                s_lay['workbook_group'] = new_group
                _layout_store().save_layout(s_code, s_lay)
                cloned_list.append({'code': s_code, 'name': s_name, 'copied': s_copied})
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        # 库回滚后回收本次已落盘的版式 JSON（_cleanup_orphan_layouts 同款，防孤儿）
        _cleanup_orphan_layouts(store, cloned_list)
        return _fail('期次结转失败：%s' % exc, code=500)

    log_operation(_who(), 'dynatable', 'add', f'软表期次结转 {code} -> {new_code}',
                  f'期次 {period_tag}，共结转 {len(cloned_list)} 张表')
    return _ok({'code': new_code, 'name': new_name, 'copied': copied, 'cloned': cloned_list},
               msg=f'已生成新期次台账：{new_name}')


def _delete_single_table_cascade(table):
    """级联清理单张软表的 5 处关联资产（调用前需已校验非内置表，调用后由外层 commit）。"""
    from models.dynamic_table import SofttableBatchError, SofttableBatchImport
    from models.safety import SafetyNode
    fields = _table_fields(table)
    records = _rows_of(table)
    _snapshot_backup(table, fields, records)

    DynamicRecord.query.filter_by(table_id=table.id).delete(synchronize_session=False)
    DynamicField.query.filter_by(table_id=table.id).delete(synchronize_session=False)
    batches = SofttableBatchImport.query.filter_by(
        module_key=table.module_key, table_code=table.code
    ).all()
    for b in batches:
        SofttableBatchError.query.filter_by(batch_id=b.batch_id).delete(synchronize_session=False)
        db.session.delete(b)
    SafetyNode.query.filter_by(node_type='table', table_code=table.code).delete(synchronize_session=False)
    db.session.delete(table)
    return table.code


@dynatable_bp.route('/api/t/<code>/delete', methods=['POST'])
@login_required
def api_delete_table(code):
    """安全删除单张软表（内置预置表禁止删除；删前自动写双份加密快照并级联清理 5 处资产）。"""
    from utils.dynatable_modules import is_builtin_roster_code
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    if is_builtin_roster_code(table.module_key, table.code):
        return _fail('系统内置预置台账不允许删除', code=400)
    tname = table.name
    try:
        _delete_single_table_cascade(table)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return _fail('删除台账失败：%s' % exc, code=500)
    # #11 版式清理移出事务 try：commit 已成功再报「删除失败」会造成半截矛盾态
    # （表已删、重试得 404）；版式文件缺失 delete_layout 本就幂等返回 False。
    try:
        _layout_store().delete_layout(code)
    except OSError:
        pass
    log_operation(_who(), 'dynatable', 'delete', f'删除软表 {tname}（{code}）', '')
    return _ok({'code': code}, msg=f'已安全删除台账「{tname}」')


@dynatable_bp.route('/api/group/<path:group_name>/delete', methods=['POST'])
@login_required
def api_delete_group(group_name):
    """整组安全删除同工作簿下的所有自定义软表。"""
    from utils.dynatable_modules import is_builtin_roster_code
    tables = DynamicTable.query.filter_by(group_name=group_name).all()
    if not tables:
        return _fail('未找到该分组下的软表台账', code=404)
    for t in tables:
        if is_builtin_roster_code(t.module_key, t.code):
            return _fail(f'分组内包含系统内置表「{t.name}」，不允许整组删除', code=400)
    codes = []
    store = _layout_store()
    try:
        for t in tables:
            codes.append(_delete_single_table_cascade(t))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return _fail('整组删除失败：%s' % exc, code=500)
    # #11 同款：版式清理在 commit 后，失败不回滚 DB（delete_layout 幂等）
    for c in codes:
        try:
            store.delete_layout(c)
        except OSError:
            pass
    log_operation(_who(), 'dynatable', 'delete', f'整组删除软表「{group_name}」', ', '.join(codes))
    return _ok({'deleted': codes}, msg=f'已安全删除分组「{group_name}」下共 {len(codes)} 张台账')


def _maybe_sync_org(table):
    """organization 模块软表建表/批量导入后静默单向反哺 OrganizationMember（失败不阻断主流程）。"""
    if not table or table.module_key != 'organization':
        return None
    try:
        from utils.org_sync import sync_org_table_to_members
        return sync_org_table_to_members(table)
    except Exception:
        db.session.rollback()
        return None


@dynatable_bp.route('/api/t/<code>/sync-org', methods=['POST'])
@login_required
def api_sync_org(code):
    """手动触发：把单张 organization 软表全量反哺到组织架构成员名册。"""
    table = _get_table(code)
    if not table:
        return _fail('台账不存在', code=404)
    if table.module_key != 'organization':
        return _fail('该台账不挂组织架构模块，不支持同步到组织架构', code=400)
    res = _maybe_sync_org(table)
    if res is None:
        return _fail('同步到组织架构失败，请稍后再试', code=500)
    return _ok(res, msg=f'已同步到组织架构：新增 {res["inserted"]} 人、更新 {res["updated"]} 人')
