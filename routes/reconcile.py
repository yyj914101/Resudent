# -*- coding: utf-8 -*-
"""死亡名单对账蓝图（T15-4，R5-E3/B4/O3，唯一接线）。

公安/殡葬死亡名单导入 → 四分类比对（已登记/疑似未登记/无档案/名单外）落
reconcile_task/reconcile_result（密文+hash）；季度计划 B4 双触发；源名单
≤30 天限期删除（U5，M6.0 C3）；结果导出走 temp/exports token 通道。
owner map：表结构唯一 owner=models/reconcile.py；业务判断进 utils/reconcile_service.py。
"""
import json
import os
from datetime import date, datetime

from flask import Blueprint, render_template, request, session
from models import db, User
from models.reconcile import ReconcileTask, ReconcileResult
from utils.decorators import login_required, permission_required
from utils.helpers import json_response, log_operation, write_export_xlsx
from utils.reconcile_service import (
    parse_xlsx, compare_batch, purge_source_file,
    ensure_reconcile_columns,
)

reconcile_bp = Blueprint('reconcile', __name__, template_folder='../templates')

SOURCE_LABELS = {'公安': '公安名单', '殡葬': '殡葬名单', '季度计划': '季度计划'}
MATCH_TYPES = ('已登记', '疑似未登记', '无档案', '名单外')

_MAX_SIZE = 10 * 1024 * 1024  # 10MB


def _reconcile_imports_dir():
    from config import Config
    d = getattr(Config, 'IMPORTS_DIR', None)
    if not d:
        d = os.path.join(os.environ.get('SHUCUN_TEMP_DIR', ''), 'imports')
    d = os.path.join(d, 'reconcile')
    os.makedirs(d, exist_ok=True)
    return d


def _can_view_sensitive():
    from utils.permissions import user_has_special
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    if not user:
        return False
    return bool(user.role_id == 1 or user_has_special(user, 'view_sensitive'))


def _decrypt_id_card(result):
    """结果行身份证：view_sensitive 解密明文，否则脱敏（前6后4）。"""
    from utils.crypto import key_holder
    from utils.masking import mask_id_card
    if not result.id_card_enc:
        return ''
    raw = ''
    try:
        raw = key_holder.decrypt(result.id_card_enc)
    except Exception:
        raw = ''
    if _can_view_sensitive():
        return raw
    return mask_id_card(raw) if raw else ''


def _result_to_dict(r):
    return {
        'id': r.id,
        'task_id': r.task_id,
        'name': r.name or '',
        'id_card': _decrypt_id_card(r),
        'death_date': r.death_date.strftime('%Y-%m-%d') if r.death_date else '',
        'match_type': r.match_type,
        'handle_status': r.handle_status,
    }


# ─── 页面 ─────────────────────────────────────────────

@reconcile_bp.route('/')
@login_required
@permission_required('reconcile.view')
def index():
    """对账任务页（单页：列表 + 导入 + 详情 + 处置 + 导出 + 删源）。"""
    return render_template('reconcile/task_list.html')


# ─── API: 任务列表 ─────────────────────────────────────

@reconcile_bp.route('/api/tasks', methods=['GET'])
@login_required
@permission_required('reconcile.view')
def api_tasks():
    """任务列表（含四类计数/源文件删除态，可按季度筛选）。"""
    q = request.args.get('quarter', '').strip()
    query = ReconcileTask.query.order_by(ReconcileTask.id.desc())
    if q:
        query = query.filter_by(quarter=q)
    data = []
    for t in query.limit(200).all():
        summary = {}
        if t.result_summary:
            try:
                summary = json.loads(t.result_summary)
            except (ValueError, TypeError):
                summary = {}
        data.append({
            'id': t.id,
            'source': t.source,
            'source_label': SOURCE_LABELS.get(t.source, t.source),
            'task_date': t.task_date.strftime('%Y-%m-%d') if t.task_date else '',
            'quarter': t.quarter or '',
            'status': t.status,
            'summary': summary,
            'has_source': bool(t.import_file),
            'source_deleted': bool(t.source_deleted_at),
            'created_at': t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else '',
        })
    return json_response(True, data=data)


# ─── API: 导入名单（B4 双触发之二：导入触发） ──────────────

@reconcile_bp.route('/import', methods=['POST'])
@login_required
@permission_required('reconcile.add')
def api_import():
    """上传对账名单 xlsx → 幂等（source_hash）→ 建任务 → run_async 比对。

    view_name 含 import → add 权限（对齐详细设计：导入比对 reconcile.add+import_batch）。
    """
    import hashlib
    import uuid
    from werkzeug.utils import secure_filename
    from utils.async_tasks import create_task, run_async
    from utils.reconcile_service import current_quarter

    ensure_reconcile_columns()  # 存量库幂等补列

    f = request.files.get('file')
    from utils.spreadsheet_reader import (
        SPREADSHEET_ERR_MSG,
        is_allowed_spreadsheet,
        preserve_spreadsheet_ext,
    )
    if not f or not f.filename:
        return json_response(False, '请选择名单文件')
    if not is_allowed_spreadsheet(f.filename):
        return json_response(False, SPREADSHEET_ERR_MSG)

    data = f.read()
    if len(data) > _MAX_SIZE:
        return json_response(False, '文件超过 10MB 上限')
    try:
        parse_xlsx(data, filename=f.filename)
    except ValueError as e:
        return json_response(False, str(e))

    file_hash = hashlib.sha256(data).hexdigest()
    dup = ReconcileTask.query.filter_by(source_hash=file_hash).first()
    if dup:
        return json_response(True, data={'task_id': dup.id, 'duplicate': True,
                                         'msg': '该文件已导入过，返回既有任务'})

    fname = f"{uuid.uuid4().hex[:12]}_{preserve_spreadsheet_ext(f.filename, secure_filename(f.filename))}"
    dest = os.path.join(_reconcile_imports_dir(), fname)
    with open(dest, 'wb') as out:
        out.write(data)

    source = request.form.get('source', '').strip() or '公安'
    if source not in SOURCE_LABELS:
        return json_response(False, 'source 仅支持 公安/殡葬')

    rt = ReconcileTask(
        source=source,
        import_file=dest,
        task_date=date.today(),
        quarter=current_quarter(),
        status='pending',
        operator=session.get('user_id'),
        source_hash=file_hash,
    )
    db.session.add(rt)
    db.session.commit()

    task = create_task('reconcile_import', str(session.get('user_id', '')), '死亡名单对账')
    db.session.commit()
    run_async('reconcile_import', task.task_id, compare_batch,
              reconcile_task_id=rt.id)
    return json_response(True, data={'task_id': rt.id, 'a1_task_id': task.task_id})


# ─── API: 任务详情 / 分类清单 ──────────────────────────

@reconcile_bp.route('/api/task/<int:tid>', methods=['GET'])
@login_required
@permission_required('reconcile.view')
def api_task_detail(tid):
    """任务详情：四类计数 + 源文件态。"""
    t = ReconcileTask.query.get(tid)
    if not t:
        return json_response(False, '任务不存在'), 404
    summary = {}
    if t.result_summary:
        try:
            summary = json.loads(t.result_summary)
        except (ValueError, TypeError):
            summary = {}
    return json_response(True, data={
        'id': t.id,
        'source': t.source,
        'source_label': SOURCE_LABELS.get(t.source, t.source),
        'quarter': t.quarter or '',
        'status': t.status,
        'summary': summary,
        'has_source': bool(t.import_file),
        'source_deleted': bool(t.source_deleted_at),
        'source_deleted_at': t.source_deleted_at.strftime('%Y-%m-%d %H:%M') if t.source_deleted_at else '',
    })


@reconcile_bp.route('/api/task/<int:tid>/results', methods=['GET'])
@login_required
@permission_required('reconcile.view')
def api_task_results(tid):
    """分类清单（match_type 筛选；身份证脱敏，明文仅 view_sensitive）。"""
    match_type = request.args.get('match_type', '').strip()
    query = ReconcileResult.query.filter_by(task_id=tid)
    if match_type:
        query = query.filter_by(match_type=match_type)
    rows = [r for r in query.order_by(ReconcileResult.id).limit(1000).all()]
    data = [_result_to_dict(r) for r in rows]
    return json_response(True, data=data, total=len(data))


# ─── API: 处置（登记/排除，E10/K1b） ───────────────────

@reconcile_bp.route('/api/result/<int:rid>/handle-save', methods=['POST'])
@login_required
@permission_required('reconcile.edit')
def api_result_handle(rid):
    """处置对账结果：action=登记（仅疑似未登记/无档案，转办死亡登记）/ 排除。

    - 已登记类禁止登记（系统已一致）
    - 幂等：已处置结果重复处置拒绝
    - 处置后 handle_status='已处置'（E22 对账引用即构成撤回冲突，T15-2 已实现）
    """
    form = request.form if request.form else (request.json or {})
    action = (form.get('action') or '').strip()
    if action not in ('登记', '排除'):
        return json_response(False, 'action 仅支持 登记/排除')

    r = ReconcileResult.query.get(rid)
    if not r:
        return json_response(False, '对账结果不存在'), 404
    if r.handle_status == '已处置':
        return json_response(False, '该结果已处置，请勿重复操作')

    now = datetime.now()
    if action == '登记':
        if r.match_type == '已登记':
            return json_response(False, '已登记死亡对象无需再登记')
        # 疑似未登记/无档案 → 转办死亡登记（无档案提示先补居民档案）
        hint = '已转办死亡登记'
        if r.match_type == '无档案':
            hint = '系统无该居民档案，请先补录居民档案后再登记死亡'
        r.handle_status = '已处置'
        r.handled_by = session.get('user_id')
        r.handled_at = now
        db.session.commit()
        log_operation(session.get('username', ''), '对账', 'handle',
                      f'对账处置-登记: {r.name or ""}（{r.match_type}）')
        return json_response(True, hint, data={
            'result_id': r.id, 'redirect': '/death-report/',
            'name': r.name or '', 'match_type': r.match_type, 'need_resident': r.match_type == '无档案',
        })

    # 排除
    r.handle_status = '已处置'
    r.handled_by = session.get('user_id')
    r.handled_at = now
    db.session.commit()
    log_operation(session.get('username', ''), '对账', 'handle',
                  f'对账处置-排除: {r.name or ""}（{r.match_type}）')
    return json_response(True, '已排除（标记已处置）', data={'result_id': r.id})


# ─── API: 删除源名单（≤30 天限期，U5） ──────────────────

@reconcile_bp.route('/task/<int:tid>/purge-source', methods=['POST'])
@login_required
@permission_required('reconcile.edit')
def api_purge_source(tid):
    """手动删除源名单（仅留比对结果）。季度计划任务无源文件直接置位。"""
    t = ReconcileTask.query.get(tid)
    if not t:
        return json_response(False, '任务不存在'), 404
    if t.source_deleted_at:
        return json_response(True, '源名单已删除')
    purge_source_file(t)
    log_operation(session.get('username', ''), '对账', 'purge_source',
                  f'删除对账源名单（任务#{tid}，{t.source}）')
    return json_response(True, '源名单已删除（仅保留比对结果）')


# ─── API: 导出结果（temp/exports token 通道） ────────────

@reconcile_bp.route('/api/task/<int:tid>/export', methods=['POST'])
@login_required
@permission_required('reconcile.export')
def api_export(tid):
    """导出对账结果 Excel（四分类清单；无 view_sensitive 出脱敏版；token 通道）。"""
    from utils.masking import mask_export_rows

    t = ReconcileTask.query.get(tid)
    if not t:
        return json_response(False, '任务不存在'), 404
    rows_all = ReconcileResult.query.filter_by(task_id=tid).all()

    headers = ['分类', '姓名', '身份证', '死亡日期', '处置状态']
    rows = []
    for r in rows_all:
        rows.append([r.match_type, r.name or '', _decrypt_id_card(r),
                     r.death_date.strftime('%Y-%m-%d') if r.death_date else '',
                     r.handle_status])
    rows = mask_export_rows(headers, rows, _can_view_sensitive())

    abs_path, token_url = write_export_xlsx(
        headers, rows, module='reconcile',
        filename=f'死亡名单对账结果_{t.source}_{t.id}.xlsx')
    if not token_url:
        return json_response(False, '导出通道未就绪')
    return json_response(True, '导出成功', data={'download_url': f'/report/download/{token_url}',
                                                'file': os.path.basename(abs_path)})


# ─── API: A1 任务进度轮询 ───────────────────────────────

@reconcile_bp.route('/api/import-status/<a1_task_id>', methods=['GET'])
@login_required
@permission_required('reconcile.view')
def api_import_status(a1_task_id):
    """A1 异步任务轨迹轮询（对齐 T14-5 import-status 模式）。"""
    from utils.async_tasks import get_task
    t = get_task(a1_task_id)
    if not t:
        return json_response(False, '任务不存在'), 404
    return json_response(True, data={
        'status': t.status,
        'result_summary': t.result_summary or '',
        'error': t.result_summary if t.status == 'failed' else '',
    })
