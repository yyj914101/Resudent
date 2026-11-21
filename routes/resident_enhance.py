"""居民台账导入向导蓝图（T01/T02）— 四步单页 + A1 异步导入管线。

端点（权限：模块 resident + 显式 special.import_batch 位）：
- POST    /import/upload             步1：上传名单（035 R-035-01：GET 302 深链分支已删）
- GET     /import/<batch_id>/mapping 步2：三级匹配展示
- POST    /import/<batch_id>/mapping 步2：保存映射确认 + 阈值守卫
- GET     /import/<batch_id>/validate 步3：dry-run 校验预览（脱敏）
- POST    /import/<batch_id>/execute  步4：提交 A1 任务
- GET     /import/<batch_id>/report   结果报告（脱敏）
- GET     /import/<batch_id>/export-failed 仅导出失败行
- GET     /import/status/<task_id>    进度轮询
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, time

from flask import (Blueprint, request, session, jsonify, render_template,
                   redirect, url_for)
from werkzeug.utils import secure_filename

from models import db, User, ImportBatch, ImportRowError
from utils.decorators import permission_required, admin_required, login_required
from utils.async_tasks import create_task, run_async, get_task
from utils.permissions import user_has_special
from utils.import_engine import (build_mapping, read_xlsx, validate_rows,
                                 save_mapping, load_mapping, run_import,
                                 compute_threshold, start_batch,
                                 apply_preset_mapping, count_rows_xlsx,
                                 resume_import,
                                 MAX_IMPORT_SIZE, SLOW_ROW_HINT,
                                 TEMPLATE_REQUIRED_CODES,
                                 CATALOG_VERSION, apply_profile_mapping,
                                 save_profile, get_profile, list_profiles,
                                 mapping_json_from_batch, batch_catalog_version,
                                 batch_version_mismatch,
                                 approve_registry_field, reject_registry_field,
                                 list_registry)

resident_enhance_bp = Blueprint('resident_enhance', __name__,
                                template_folder='../templates')


def _current_user():
    return User.query.get(session.get('user_id')) if session.get('user_id') else None


def _is_admin():
    user = _current_user()
    return bool(user and user.role_id == 1)


def _get_batch(batch_id) -> ImportBatch | None:
    return ImportBatch.query.filter_by(batch_id=batch_id).first()


def _read_batch_xlsx(batch):
    """读批次源文件；丢失/不可读时返回 None（R-036-10：调用方统一 404 提示）。"""
    try:
        return read_xlsx(batch.source_path)
    except (FileNotFoundError, OSError):
        return None


def _imports_dir() -> str:
    from config import Config
    d = getattr(Config, 'IMPORTS_DIR', None)
    if not d:
        d = os.path.join(os.environ.get('SHUCUN_TEMP_DIR', ''), 'imports')
    os.makedirs(d, exist_ok=True)
    return d


def _has_import_special() -> bool:
    """ADR-016：batches 端点按 special.import_batch 显式判定（admin 视为有）。"""
    user = _current_user()
    return bool(user and user_has_special(user, 'import_batch'))


def _profile_dict(p) -> dict:
    return {
        'profile_key': p.profile_key, 'name': p.name, 'is_builtin': bool(p.is_builtin),
        'catalog_version': p.catalog_version,
        'default_policy': p.default_policy or 'skip',
        'required_permission': p.required_permission,
        'mapping': json.loads(p.mapping_json or '[]'),
        'created_by': p.created_by,
    }


# ---------------------------------------------------------------
# 036 F2：映射方案保存 / 加载复用
# ---------------------------------------------------------------

@resident_enhance_bp.route('/profiles', methods=['GET'])
@permission_required('resident.edit')
def profiles_list():
    user = _current_user()
    rows = list_profiles(username=user.username if user else None,
                         is_admin=bool(user and user.role_id == 1))
    return jsonify({'success': True, 'data': [_profile_dict(p) for p in rows]})


@resident_enhance_bp.route('/profiles', methods=['POST'])
@permission_required('resident.edit')
def profiles_edit():
    """自定义导入成功后「保存为方案」：从批次血缘生成 mapping_json（仍展示确认页）。

    视图名含 edit 关键字（021 API-02 动作推断）：全局鉴权按 resident.edit 放行，
    与端点级 @permission_required('resident.edit') 口径一致。
    """
    user = _current_user()
    payload = request.get_json(silent=True) or {}
    name = (payload.get('name') or '').strip()
    batch_id = payload.get('batch_id')
    if not name:
        return jsonify({'success': False, 'msg': '请填写方案名称'}), 400
    if not batch_id or not _get_batch(batch_id):
        return jsonify({'success': False, 'msg': '批次不存在'}), 404
    mapping = mapping_json_from_batch(batch_id)
    if not mapping:
        return jsonify({'success': False, 'msg': '该批次尚无映射，无法保存方案'}), 400
    rec = save_profile(name, mapping, created_by=user.username if user else None,
                       default_policy='skip',
                       required_permission='special.import_batch')
    if rec is None:
        return jsonify({'success': False, 'msg': '内置方案不可覆盖'}), 400
    return jsonify({'success': True, 'data': _profile_dict(rec)})


# ---------------------------------------------------------------
# 036 F3：批次列表（页面 + 列表 API；权益 special.import_batch）
# ---------------------------------------------------------------

@resident_enhance_bp.route('/batches')
def import_batches_page():
    if not _has_import_special():
        # R-036-08①：浏览器直接打开（侧栏/书签）渲染 403 页；XHR 仍返 JSON
        from utils.permissions import response_wants_json
        if response_wants_json():
            return jsonify({'success': False, 'msg': '权限不足'}), 403
        return render_template('errors/403.html'), 403
    return render_template('resident_enhance/import_batches.html')


@resident_enhance_bp.route('/batches/data')
def import_batches_data():
    if not _has_import_special():
        return jsonify({'success': False, 'msg': '权限不足'}), 403
    page = max(request.args.get('page', 1, type=int), 1)
    limit = min(max(request.args.get('limit', 20, type=int), 1), 100)

    q = ImportBatch.query
    status = (request.args.get('status') or '').strip()
    mode = (request.args.get('mode') or '').strip()
    if status:
        q = q.filter_by(status=status)
    if mode:
        q = q.filter_by(mode=mode)
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    try:
        if date_from:
            q = q.filter(ImportBatch.created_at >= datetime.combine(
                datetime.strptime(date_from, '%Y-%m-%d').date(), time.min))
        if date_to:
            q = q.filter(ImportBatch.created_at <= datetime.combine(
                datetime.strptime(date_to, '%Y-%m-%d').date(), time.max))
    except ValueError:
        return jsonify({'success': False, 'msg': '日期格式应为 YYYY-MM-DD'}), 400

    total = q.count()
    rows = (q.order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
            .offset((page - 1) * limit).limit(limit).all())
    op_ids = {r.operator for r in rows if r.operator}
    names = ({u.id: u.username for u in User.query.filter(User.id.in_(op_ids)).all()}
             if op_ids else {})
    data = [{
        'id': r.id, 'batch_id': r.batch_id, 'source_file': r.source_file,
        'mode': r.mode, 'status': r.status, 'profile_key': r.profile_key,
        'catalog_version': batch_catalog_version(r),       # R-036-06：模板版本快照
        'total_rows': r.total_rows or 0, 'inserted_rows': r.success_rows or 0,
        'updated_rows': r.updated_rows or 0, 'skip_rows': r.skip_rows or 0,
        'fail_rows': r.fail_rows or 0,
        'operator': names.get(r.operator) or (str(r.operator) if r.operator else ''),
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else '',
    } for r in rows]
    pages = (total + limit - 1) // limit
    return jsonify({'success': True, 'data': data, 'total': total, 'pages': pages})


# ---------------------------------------------------------------
# 036 F5：字段注册审批页（063-FIX：页面 resident.view 可见；审批动作 admin 专属）
# ---------------------------------------------------------------

@resident_enhance_bp.route('/registry')
@login_required
def import_registry():
    """字段注册审批页：登录 + resident.view 可见（063-FIX，A 方案 §4.3）。

    登录由显式 @login_required 兜底（与 dict_mgmt.list 对齐，AUD-19）；模块位由
    BLUEPRINT_MODULES['resident_enhance'] = 'resident' 判定；审批动作 approve/reject
    仍保留 @admin_required，036 F5 安全意图不放松。
    """
    status = (request.args.get('status') or '').strip() or None
    rows = []
    recs = list_registry(status)
    ids = {rec.approved_by for rec in recs if rec.approved_by}
    names = ({u.id: u.username for u in User.query.filter(User.id.in_(ids)).all()}
             if ids else {})
    for rec in recs:
        raw = rec.definition or ''
        d = None
        try:
            d = json.loads(raw)
        except (TypeError, ValueError):
            d = None
        if isinstance(d, dict):
            # R-036-11：True/False 渲染中文，不再让 Python 布尔值直出界面
            text = '；'.join(
                f'{k}：{"是" if v is True else v}'
                for k, v in d.items() if v) or raw
        else:
            text = raw
        rows.append({'rec': rec, 'definition': text,
                     'note': (d or {}).get('口径说明', '') if isinstance(d, dict) else '',
                     'usage': (d or {}).get('统计用途', '') if isinstance(d, dict) else '',
                     'approved_by_name': names.get(rec.approved_by) or ''})
    return render_template('resident_enhance/import_registry_audit.html',
                           rows=rows, status=status or '')


@resident_enhance_bp.route('/registry/<code>/approve', methods=['POST'])
@admin_required
def import_registry_approve(code):
    # R-036-08②：检查返回值；R-036-11：审批时可填「口径说明/统计用途」（非必填）
    definition = {}
    note = (request.form.get('definition_note') or '').strip()
    usage = (request.form.get('definition_usage') or '').strip()
    if note:
        definition['口径说明'] = note
    if usage:
        definition['统计用途'] = usage
    if not approve_registry_field(code, approved_by=session.get('user_id'),
                                  definition=definition or None):
        from flask import flash
        flash(f'字段 {code} 不存在', 'danger')
    return redirect(url_for('resident_enhance.import_registry', status='draft'))


@resident_enhance_bp.route('/registry/<code>/reject', methods=['POST'])
@admin_required
def import_registry_reject(code):
    if not reject_registry_field(code, approved_by=session.get('user_id')):
        from flask import flash
        flash(f'字段 {code} 不存在', 'danger')
    return redirect(url_for('resident_enhance.import_registry', status='draft'))


@resident_enhance_bp.route('/upload', methods=['POST'])
@permission_required('special.import_batch')
def import_upload():
    """步1：POST 上传 .xlsx（≤50MB，034 P1 统一口径；zip magic + 行数护栏已入内核 start_batch）。

    035 R-035-01③：GET 302 深链分支已删——侧栏「名单导入」入口移除后仅剩旧书签。
    直接 GET 访问：admin → 405 METHOD NOT ALLOWED（URL 仍匹配 POST-only 路由，
    Flask 路由层拒绝方法；035 实测勘误：任务书预估 404，实际 405）；非 admin →
    方法不匹配 endpoint=None 命中 ADR-016 默认拒绝 403。
    向导唯一入口 = 新增居民页 custom tab（resident/add?tab=custom）。

    POST 参数：mode=template|custom（034）。template=预设映射；缺必需列 → 批次 blocked
    并返回缺失列清单；模板外列忽略并计数提示。custom=三级匹配（现状）。
    021 API-03：special.import_batch 改显式装饰器（AGENTS §11 红线 #4）。
    """
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'success': False, 'msg': '请选择 .xlsx 名单文件'}), 400
    mode = request.form.get('mode', 'custom')
    if mode not in ('template', 'custom'):
        mode = 'custom'

    batch, err, was_dup = start_batch(f, session.get('user_id'), mode)
    if err:
        return jsonify({'success': False, 'msg': err}), 400
    if was_dup:
        # source_hash 幂等命中既有批次（034 幂等件套一）
        # 035 R-034-03②：running/aborted/blocked 批次附带 resumable=True（判定直接
        # 复用内核 resume_import，单一真源），前端据此提供「继续导入」动作。
        return jsonify({'success': True, 'data': {
            'batch_id': batch.batch_id, 'duplicate': True,
            'msg': '该文件已导入过，返回既有批次', 'mode': batch.mode,
            'status': batch.status,
            # 036 F4：批次模板版本快照与当前目录版本不一致 → 导入前提示（不阻断）
            'catalog_version': batch_catalog_version(batch),
            'current_catalog_version': CATALOG_VERSION,
            'version_mismatch': batch_version_mismatch(batch),
            'resumable': resume_import(batch)}})

    row_count, _over = count_rows_xlsx(batch.source_path)
    warning = '文件超过 5 万行，导入将异步分批执行，耗时较长' if row_count > SLOW_ROW_HINT else None

    # 模板模式：预设映射（缺必需列阻断）；自定义模式：等待步2人工映射
    if mode == 'template':
        loaded = _read_batch_xlsx(batch)
        if loaded is None:
            return jsonify({'success': False, 'msg': '源文件已丢失，请重新上传该名单文件'}), 404
        headers, _rows = loaded
        preset = apply_preset_mapping(batch.batch_id, headers)
        if not preset['ok']:
            batch.status = 'blocked'
            batch.block_reason = '模板模式缺少必需列：' + '、'.join(preset['missing'])
            db.session.commit()
            return jsonify({'success': True, 'data': {
                'batch_id': batch.batch_id, 'mode': mode,
                'blocked': True, 'missing': preset['missing'],
                'extra': preset['extra'], 'msg': '缺少必需列，已阻断（可转自定义导入）'}})
        batch.status = 'mapped'
        db.session.commit()
        return jsonify({'success': True, 'data': {
            'batch_id': batch.batch_id, 'mode': mode,
            'mapped': True, 'matched': [c for _h, c in preset['matched']],
            'extra': preset['extra'], 'row_count': row_count, 'warning': warning}})

    return jsonify({'success': True, 'data': {
        'batch_id': batch.batch_id, 'mode': mode, 'row_count': row_count,
        'warning': warning}})


@resident_enhance_bp.route('/<batch_id>/mapping', methods=['GET'])
@permission_required('special.import_batch')
def import_mapping(batch_id):
    """步2：读表头跑三级匹配，返回逐列映射 + 阈值守卫 + 目录（供下拉）。

    036 F2：?profile_key=xxx 加载方案覆盖当前表头；catalog_version 不一致时
    返回 version_mismatch=True 由前端提示复核（仍展示确认页，不静默套用）。
    """
    batch = _get_batch(batch_id)
    if not batch:
        return jsonify({'success': False, 'msg': '批次不存在'}), 404
    loaded = _read_batch_xlsx(batch)                     # R-036-10：源文件丢失 → 404
    if loaded is None:
        return jsonify({'success': False, 'msg': '源文件已丢失，请重新上传该名单文件'}), 404
    headers, _rows = loaded
    result = build_mapping(headers)
    result['batch'] = {'batch_id': batch.batch_id, 'status': batch.status,
                       'source_file': batch.source_file}
    result['current_catalog_version'] = CATALOG_VERSION

    profile_key = (request.args.get('profile_key') or '').strip()
    if profile_key:
        rec = get_profile(profile_key)
        if not rec:
            return jsonify({'success': False, 'msg': '方案不存在'}), 404
        try:
            mapping = json.loads(rec.mapping_json or '[]')
        except (TypeError, ValueError):
            mapping = []
        result['columns'], unmatched = apply_profile_mapping(result['columns'], mapping)
        for col in result['columns']:
            if 'action' not in col:
                col['action'] = 'map' if col.get('target') else ''
        blocked, thresholds, reason = _final_threshold(result['columns'])
        result['threshold'] = {'blocked': blocked, 'thresholds': thresholds,
                               'reason': reason}
        result['profile'] = {
            'profile_key': rec.profile_key, 'name': rec.name,
            'catalog_version': rec.catalog_version,
            'version_mismatch': rec.catalog_version != CATALOG_VERSION,
            'unmatched': unmatched,
        }
    return jsonify({'success': True, 'data': result})


def _final_threshold(columns) -> tuple[bool, dict, str | None]:
    """步2 保存后的最终阈值：未映射=无 action 列；新增字段=ext 列。"""
    from utils.import_engine import UNMAPPED_RATIO_MAX, NEW_FIELD_MAX
    total = len(columns)
    unmapped = [c for c in columns if c.get('action') not in ('map', 'ext', 'ignore')]
    new_fields = [c for c in columns if c.get('action') == 'ext']
    ratio = (len(unmapped) / total) if total else 0.0
    thresholds = {
        'total_cols': total,
        'unmapped': len(unmapped),
        'ratio': round(ratio, 4),
        'new_fields': len(new_fields),
    }
    blocked = (ratio > UNMAPPED_RATIO_MAX) or (len(new_fields) > NEW_FIELD_MAX)
    reason = None
    if blocked:
        parts = []
        if ratio > UNMAPPED_RATIO_MAX:
            parts.append(f'未映射比例 {ratio:.0%} > {UNMAPPED_RATIO_MAX:.0%}')
        if len(new_fields) > NEW_FIELD_MAX:
            parts.append(f'新增字段 {len(new_fields)} > {NEW_FIELD_MAX}')
        reason = '；'.join(parts)
    return blocked, thresholds, reason


@resident_enhance_bp.route('/<batch_id>/mapping', methods=['POST'])
@permission_required('special.import_batch')
def import_mapping_save(batch_id):
    """步2：保存映射确认（血缘落库）+ 阈值守卫（blocked 需 admin 确认豁免）。"""
    batch = _get_batch(batch_id)
    if not batch:
        return jsonify({'success': False, 'msg': '批次不存在'}), 404

    payload = request.get_json(silent=True) or {}
    columns = payload.get('columns', [])
    admin_confirm = bool(payload.get('admin_confirm', False))
    if not columns:
        return jsonify({'success': False, 'msg': '缺少映射列'}), 400

    blocked, thresholds, reason = _final_threshold(columns)
    if blocked and not (admin_confirm and _is_admin()):
        batch.status = 'blocked'
        batch.thresholds = json.dumps(thresholds, ensure_ascii=False)
        batch.block_reason = reason
        batch.new_field_count = thresholds['new_fields']
        db.session.commit()
        return jsonify({'success': True, 'data': {
            'blocked': True, 'need_admin': True, 'reason': reason,
            'thresholds': thresholds}})

    # 034 P2 目标9：模板通道被阻断（缺必需列）的批次转入自定义映射时，
    # 切换为 custom 语义——后续校验按自定义口径（姓名/身份证），不再套模板四必填。
    # 模板批次只有被阻断才会到达本端点（正常模板流不经过手工映射保存）。
    if batch.status == 'blocked' and batch.mode == 'template':
        batch.mode = 'custom'

    # 落血缘（先清旧映射，幂等重存）
    from models import ImportFieldMapping
    ImportFieldMapping.query.filter_by(batch_id=batch_id).delete()
    save_mapping(batch_id, columns)

    batch.status = 'mapped'
    batch.thresholds = json.dumps(thresholds, ensure_ascii=False)
    batch.block_reason = reason if blocked else None
    batch.new_field_count = thresholds['new_fields']
    db.session.commit()
    return jsonify({'success': True, 'data': {
        'blocked': False, 'status': 'mapped', 'thresholds': thresholds}})


@resident_enhance_bp.route('/<batch_id>/validate', methods=['GET'])
@permission_required('special.import_batch')
def import_validate(batch_id):
    """步3：dry-run 校验预览（必填/格式/去重），身份证脱敏。

    034 P1：模板模式必填集合 = 姓名/身份证/与户主关系/户主名；自定义 = 姓名/身份证。
    """
    batch = _get_batch(batch_id)
    if not batch:
        return jsonify({'success': False, 'msg': '批次不存在'}), 404
    column_map = load_mapping(batch_id)
    if not column_map:
        return jsonify({'success': False, 'msg': '请先完成字段映射'}), 400
    loaded = _read_batch_xlsx(batch)                     # R-036-10：源文件丢失 → 404
    if loaded is None:
        return jsonify({'success': False, 'msg': '源文件已丢失，请重新上传该名单文件'}), 404
    headers, rows = loaded
    required_codes = (TEMPLATE_REQUIRED_CODES
                      if batch.mode == 'template' else ('name', 'id_card'))
    summary = validate_rows(batch_id, headers, rows, column_map,
                            required_codes=required_codes)
    summary['mode'] = batch.mode
    return jsonify({'success': True, 'data': summary})


@resident_enhance_bp.route('/<batch_id>/execute', methods=['POST'])
@permission_required('special.import_batch')
def import_execute(batch_id):
    """步4：提交 A1 后台任务（type=import），返回 202 task_id。

    034 P1：policy=skip|update_non_empty 落批次（默认 skip）；
    覆盖语义由内核 apply_resident_row 统一（空值不覆盖/空状态·空户编号保留）。
    """
    batch = _get_batch(batch_id)
    if not batch:
        return jsonify({'success': False, 'msg': '批次不存在'}), 404
    if not load_mapping(batch_id):
        return jsonify({'success': False, 'msg': '请先完成字段映射'}), 400
    if batch.status == 'blocked':
        # 021 并单修复 018 D-01：blocked 批次携带残留旧映射可直 execute 的绕过
        return jsonify({'success': False,
                        'msg': '该批次已被阈值守卫拦截，需重新提交映射（或管理员豁免确认）后方可执行'}), 409
    if batch.status == 'running':
        return jsonify({'success': False, 'msg': '该批次正在导入中'}), 409
    if batch.status == 'done':
        return jsonify({'success': False, 'msg': '该批次已完成导入'}), 409

    payload = request.get_json(silent=True) or {}
    policy = payload.get('policy', 'skip')
    if policy not in ('skip', 'update_non_empty'):
        return jsonify({'success': False, 'msg': '策略参数无效（skip|update_non_empty）'}), 400
    batch.policy = policy
    db.session.commit()

    task = create_task('import', str(session.get('user_id', '')), '居民名单导入')
    db.session.commit()
    run_async('import', task.task_id, run_import, batch_id=batch_id)
    return jsonify({'success': True, 'data': {'task_id': task.task_id}}), 202


@resident_enhance_bp.route('/status/<task_id>', methods=['GET'])
@permission_required('special.import_batch')
def import_status(task_id):
    """进度轮询（A1 轨迹：pending→running→done/failed）。"""
    t = get_task(task_id)
    if not t:
        return jsonify({'success': False, 'msg': '任务不存在'}), 404
    return jsonify({'success': True, 'data': {
        'status': t.status, 'progress': t.progress,
        'result_summary': t.result_summary}})


@resident_enhance_bp.route('/<batch_id>/report', methods=['GET'])
@permission_required('special.import_batch')
def import_report(batch_id):
    """结果报告（脱敏）：批次统计 + 失败行明细。"""
    batch = _get_batch(batch_id)
    if not batch:
        return jsonify({'success': False, 'msg': '批次不存在'}), 404
    errors = [{
        'row_no': e.row_no, 'field': e.field, 'error_type': e.error_type,
        'reason': e.reason, 'raw_row': e.raw_row,
    } for e in ImportRowError.query.filter_by(batch_id=batch_id)
        .order_by(ImportRowError.row_no).limit(200).all()]
    return jsonify({'success': True, 'data': {
        'batch_id': batch.batch_id, 'status': batch.status,
        'mode': batch.mode, 'policy': batch.policy or 'skip',
        'total_rows': batch.total_rows, 'success_rows': batch.success_rows,
        'updated_rows': batch.updated_rows, 'fail_rows': batch.fail_rows,
        'skip_rows': batch.skip_rows,
        'new_field_count': batch.new_field_count,
        'thresholds': json.loads(batch.thresholds) if batch.thresholds else None,
        'errors': errors}})


@resident_enhance_bp.route('/<batch_id>/export-failed', methods=['GET'])
@permission_required('special.import_batch')
def export_failed_rows(batch_id):
    """仅导出失败行（108 对齐：write_export_xlsx token 通道，302 直下）。

    raw_row 落库前经 mask_text 脱敏（import_engine._add_row_error）；302 到
    一次性令牌下载端点 /report/download/<token>，导航式调用（<a href>/
    location.href）天然跟随。
    """
    batch = _get_batch(batch_id)
    if not batch:
        return jsonify({'success': False, 'msg': '批次不存在'}), 404
    from utils.helpers import write_export_xlsx, log_operation
    errs = ImportRowError.query.filter_by(batch_id=batch_id)\
        .order_by(ImportRowError.row_no).all()
    rows = [[e.row_no, e.field or '', e.error_type or '', e.reason or '', e.raw_row or '']
            for e in errs]
    abs_path, token_url = write_export_xlsx(
        ['行号', '字段', '错误类型', '原因', '原文(脱敏)'], rows,
        module='resident', filename=f'import_failed_{batch_id}.xlsx')
    if not token_url:
        return jsonify({'success': False, 'msg': '导出通道未就绪'}), 503
    log_operation(session.get('username', ''), 'resident', 'export',
                  '导出居民导入失败行（%d 行）' % len(rows))
    return redirect('/report/download/%s' % token_url)
