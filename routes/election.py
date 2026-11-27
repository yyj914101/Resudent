# -*- coding: utf-8 -*-
"""routes/election.py — 090B 公共事务·选举（届次状态机 + 选民名册 + 当选结果）。

业务语义收口 utils/election_service.py（状态机迁移表/守卫/唯一性/对账），路由只做
HTTP 接线与参数提取（AGENTS.md §11：routes 不拥有业务语义；视图名含动作关键字，
动作位由 permissions.action_for_request 自动映射，零平行实现）。
权限：全局 enforce_role_permissions 按 BLUEPRINT_MODULES['election'] 模块位，默认拒绝兜底。
附件：选举材料复用 092 通用附件表（module_key='election'，收口 utils/attachment_service）。
导出：token 通道（AGENTS.md 红线 8），身份证打码（一页纸 §8）。
"""
from datetime import datetime

import hashlib
import io
import os
import uuid

from flask import Blueprint, abort, render_template, request, session, send_file

from models import db, ElectionRound, ElectionVoter, ElectionResult
from models import ElectionBatchImport, ElectionBatchError
from utils import attachment_service, election_service
from utils import election_field_catalog as field_catalog
from utils.attachment_service import attachment_api
from utils.crypto import sha256_hash
from utils.decorators import login_required
from utils.dict_enum import get_enum
from utils.election_service import (election_api, apply_voter_fields,
                                    run_election_batch, validate_voter_fields,
                                    voter_decrypt)
from utils.helpers import json_response, log_operation, write_export_xlsx
from utils.masking import mask_id_card

election_bp = Blueprint('election', __name__)

# 105：加密读写管线已下沉 utils/election_service（单条登记与批量导入共用，禁止两套）
_voter_decrypt = voter_decrypt

_BATCH_MAX_SIZE = 10 * 1024 * 1024       # 批量导入文件上限（与补贴批量登记同口径）


def _text(v):
    return (v or '').strip()


def _parse_date_or_raise(raw, label):
    s = _text(raw)
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError('%s格式不正确（应为 YYYY-MM-DD）' % label)


def _parse_bool(form, key):
    """表单布尔位（结果录入等单条表单用；选民字段校验已下沉 service）。"""
    return _text(form.get(key)) in ('1', 'on', 'true', '是')


def _round_dict(r):
    """届次序列化 + 名册/结果计数（列表页与名册页头共用）。"""
    voters = ElectionVoter.query.filter_by(round_id=r.id).all()
    results = ElectionResult.query.filter_by(round_id=r.id).all()
    registered = sum(1 for v in voters if v.register_status == '已登记')
    return {
        'id': r.id,
        'round_name': r.round_name or '',
        'start_date': r.start_date.strftime('%Y-%m-%d') if r.start_date else '',
        'end_date': r.end_date.strftime('%Y-%m-%d') if r.end_date else '',
        'register_start': r.register_start.strftime('%Y-%m-%d') if r.register_start else '',
        'register_end': r.register_end.strftime('%Y-%m-%d') if r.register_end else '',
        'vote_date': r.vote_date.strftime('%Y-%m-%d') if r.vote_date else '',
        'status': r.status or '筹备',
        'remark': r.remark or '',
        'voters_count': len(voters),
        'registered_count': registered,
        'results_count': len(results),
        'votes_total': sum(int(e.votes or 0) for e in results),
    }


def _voter_dict(v):
    return {
        'id': v.id,
        'round_id': v.round_id,
        'name': v.name or '',
        'id_card': _voter_decrypt(v, 'id_card'),
        'group_name': v.group_name or '',
        'register_status': v.register_status or '',
        'is_out': bool(v.is_out),
        'proxy_name': v.proxy_name or '',
        'mobile_box': bool(v.mobile_box),
        'phone': _voter_decrypt(v, 'phone'),
        'remark': v.remark or '',
    }


def _result_dict(e):
    return {
        'id': e.id,
        'round_id': e.round_id,
        'position': e.position or '',
        'winner_name': e.winner_name or '',
        'winner_id_card_hash': e.winner_id_card_hash or '',
        'votes': int(e.votes or 0),
        'is_elected': bool(e.is_elected),
        'notice_date': e.notice_date.strftime('%Y-%m-%d') if e.notice_date else '',
        'remark': e.remark or '',
    }


def _get_round_or_404(rid):
    r = ElectionRound.query.get(rid)
    if not r:
        abort(404)
    return r


# ═══════════════════ 页面 ═══════════════════

@election_bp.route('/')
@login_required
def index():
    """届次列表页（§8 届次列表：名称/任期起止/登记期/投票日/状态，可筛状态）。"""
    return render_template('election/index.html',
                           round_statuses=get_enum('选举届次状态'))


@election_bp.route('/rounds/<int:rid>/voters')
@login_required
def voters_page(rid):
    """选民名册页（§8 选民名册 + 结果录入；届次上下文，含状态推进与对账）。"""
    r = _get_round_or_404(rid)
    return render_template('election/voters.html',
                           round=_round_dict(r),
                           register_statuses=get_enum('选民登记状态'),
                           positions=get_enum('当选职务'),
                           round_statuses=get_enum('选举届次状态'))


# ═══════════════════ 届次 ═══════════════════

@election_bp.route('/api/rounds')
@login_required
def api_round_list():
    status = _text(request.args.get('status'))
    query = ElectionRound.query.order_by(ElectionRound.id.desc())
    if status:
        query = query.filter(ElectionRound.status == status)
    return json_response(success=True, data={'data': [_round_dict(r) for r in query.all()]})


def _validate_round_form():
    """一页纸 §2：R-001/R-002/R-004/R-005/R-006 必填；R-003/R-005 日期先后约束。"""
    form = request.form
    name = _text(form.get('round_name'))
    if not name:
        raise ValueError('届次名称必填')
    start = _parse_date_or_raise(form.get('start_date'), '任期起始')
    if not start:
        raise ValueError('任期起始日期必填')
    end = _parse_date_or_raise(form.get('end_date'), '任期截止')
    register_start = _parse_date_or_raise(form.get('register_start'), '登记期开始')
    if not register_start:
        raise ValueError('登记期开始日期必填')
    register_end = _parse_date_or_raise(form.get('register_end'), '登记期结束')
    if not register_end:
        raise ValueError('登记期结束日期必填')
    vote_date = _parse_date_or_raise(form.get('vote_date'), '投票日')
    if not vote_date:
        raise ValueError('投票日必填')
    if end and end < start:
        raise ValueError('任期截止不得早于任期起始')
    if register_end < register_start:
        raise ValueError('登记期结束不得早于登记期开始')
    return {'round_name': name, 'start_date': start, 'end_date': end,
            'register_start': register_start, 'register_end': register_end,
            'vote_date': vote_date, 'remark': _text(form.get('remark'))}


@election_bp.route('/api/rounds/add', methods=['POST'])
@login_required
@election_api
def api_round_add():
    fields = _validate_round_form()
    r = ElectionRound(**fields, status='筹备')   # 新届次固定从 S-01 起点起步（§6）
    db.session.add(r)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'add', '新建选举届次 %s' % r.round_name)
    return json_response(True, '', {'id': r.id})


@election_bp.route('/api/rounds/edit', methods=['POST'])
@login_required
@election_api
def api_round_edit():
    rid = request.form.get('id', 0, type=int)
    r = ElectionRound.query.get(rid)
    if not r:
        return json_response(False, '届次不存在')
    fields = _validate_round_form()
    # 状态不随编辑走：状态只经 /transition 状态机变更（§6 防绕过口径，governance 同款）
    for k, v in fields.items():
        setattr(r, k, v)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'edit', '编辑选举届次 %s' % r.round_name)
    return json_response(True, '')


@election_bp.route('/api/rounds/<int:rid>/transition', methods=['POST'])
@login_required
@election_api
def api_round_transition(rid):
    """届次状态机唯一入口（V-01：非法迁移 409 且不落库）。"""
    r = _get_round_or_404(rid)
    to_status = _text(request.form.get('to_status'))
    if not to_status or to_status not in get_enum('选举届次状态'):
        return json_response(False, '目标状态取值不合法')
    current = r.status or '筹备'
    election_service.check_transition(r, to_status)   # 拒绝抛 TransitionDenied → 409
    r.status = to_status
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'transition',
                  '届次 %s 状态迁移: %s→%s' % (r.round_name, current, to_status))
    return json_response(True, '状态已更新为%s' % to_status, data={'status': to_status})


# ═══════════════════ 选民名册 ═══════════════════

@election_bp.route('/api/rounds/<int:rid>/voters')
@login_required
def api_voter_list(rid):
    _get_round_or_404(rid)
    query = ElectionVoter.query.filter_by(round_id=rid).order_by(ElectionVoter.id.desc())
    group = _text(request.args.get('group_name'))
    status = _text(request.args.get('register_status'))
    is_out = _text(request.args.get('is_out'))
    if group:
        query = query.filter(ElectionVoter.group_name == group)
    if status:
        query = query.filter(ElectionVoter.register_status == status)
    if is_out in ('1', '0'):
        query = query.filter(ElectionVoter.is_out == (is_out == '1'))
    voters = query.all()
    return json_response(success=True, data={
        'data': [_voter_dict(v) for v in voters],
        'reconcile': election_service.reconcile_round(rid),   # §7 对账随列表下发
    })


def _validate_voter_form():
    """一页纸 §3/§10 校验：口径唯一真源已下沉 utils/election_service（批量导入共用）。"""
    return validate_voter_fields(request.form)


def _apply_voter_fields(v, fields):
    """写入选民字段：口径唯一真源 utils/election_service.apply_voter_fields。"""
    apply_voter_fields(v, fields)


@election_bp.route('/api/rounds/<int:rid>/voters/add', methods=['POST'])
@login_required
@election_api
def api_voter_add(rid):
    r = _get_round_or_404(rid)
    election_service.check_voter_editable(r)           # V-02：仅「选民登记」状态 → 409
    fields = _validate_voter_form()
    election_service.check_voter_unique(rid, fields['id_card'])   # V-03 → 409
    v = ElectionVoter(round_id=rid)
    _apply_voter_fields(v, fields)
    db.session.add(v)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'add', '登记选民 %s（届次 %s）' % (v.name, r.round_name))
    return json_response(True, '', {'id': v.id})


@election_bp.route('/api/voters/<int:vid>/edit', methods=['POST'])
@login_required
@election_api
def api_voter_edit(vid):
    v = ElectionVoter.query.get(vid)
    if not v:
        return json_response(False, '选民不存在')
    r = _get_round_or_404(v.round_id)
    election_service.check_voter_editable(r)           # V-02 → 409
    fields = _validate_voter_form()
    election_service.check_voter_unique(v.round_id, fields['id_card'], exclude_id=v.id)
    _apply_voter_fields(v, fields)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'edit', '修改选民 %s' % v.name)
    return json_response(True, '')


@election_bp.route('/api/voters/<int:vid>/delete', methods=['POST'])
@login_required
@election_api
def api_voter_delete(vid):
    v = ElectionVoter.query.get(vid)
    if not v:
        return json_response(False, '选民不存在')
    r = _get_round_or_404(v.round_id)
    election_service.check_voter_editable(r)           # 登记期外名册冻结（V-02 同口径，RESULT 留痕）
    name = v.name
    db.session.delete(v)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'delete', '删除选民 %s' % name)
    return json_response(True, '删除成功')


@election_bp.route('/api/rounds/<int:rid>/voters/export')
@login_required
def api_voter_export(rid):
    """选民名册导出（一页纸 §8 列序；身份证打码；token 通道）。"""
    _get_round_or_404(rid)
    query = ElectionVoter.query.filter_by(round_id=rid).order_by(ElectionVoter.id.desc())
    # 与列表同源筛选（导出当前筛选视图）
    group = _text(request.args.get('group_name'))
    status = _text(request.args.get('register_status'))
    is_out = _text(request.args.get('is_out'))
    if group:
        query = query.filter(ElectionVoter.group_name == group)
    if status:
        query = query.filter(ElectionVoter.register_status == status)
    if is_out in ('1', '0'):
        query = query.filter(ElectionVoter.is_out == (is_out == '1'))

    headers = ['姓名', '身份证（打码）', '组别', '登记状态', '是否外出', '受托人', '流动票箱']
    rows = []
    for v in query.all():
        rows.append([v.name or '', mask_id_card(_voter_decrypt(v, 'id_card')),
                     v.group_name or '', v.register_status or '',
                     '是' if v.is_out else '否', v.proxy_name or '',
                     '是' if v.mobile_box else '否'])
    abs_path, token_url = write_export_xlsx(
        headers, rows, module='election', filename='选民名册.xlsx')
    if not token_url:
        return json_response(False, '导出通道未就绪')
    log_operation(session.get('username', ''), 'election', 'export', '导出选民名册（%d 行）' % len(rows))
    return json_response(True, '导出成功', data={
        'download_url': '/report/download/%s' % token_url,
        'file': abs_path.split('/')[-1].split('\\')[-1],
    })


# ═══════════════════ 105 选民批量导入（ADR-030：异步批次 + 失败行分离导出 + overwrite） ═══════════════════
# 端点命名成套照抄 routes/subsidy.py:548-669；目录/校验/写入/批次全部收口 utils，
# 本段只做 HTTP 接线。字段目录唯一真源 utils/election_field_catalog.py。

def _batch_imports_dir() -> str:
    """批量导入临时目录（与补贴批量登记同口径：TEMP_DIR/imports）。"""
    from config import Config
    d = getattr(Config, 'IMPORTS_DIR', None)
    if not d:
        d = os.path.join(os.environ.get('SHUCUN_TEMP_DIR', ''), 'imports')
    os.makedirs(d, exist_ok=True)
    return d


@election_bp.route('/api/rounds/<int:rid>/voters/template')
@login_required
@election_api
def api_voter_template(rid):
    """导入模板：只含目录 9 列（完整身份证列；不得复用打码导出表头）。"""
    _get_round_or_404(rid)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = '选民导入模板'
    headers = field_catalog.template_headers()
    ws.append(headers)
    for idx, header in enumerate(headers, 1):
        ws.column_dimensions[chr(64 + idx)].width = max(12, len(header) * 2 + 4)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='选民导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@election_bp.route('/api/rounds/<int:rid>/voters/import-upload', methods=['POST'])
@login_required
@election_api
def api_voter_import_upload(rid):
    """步1：上传 xlsx/xls/csv（≤10MB）+ 建批次；同届次同文件按 source_hash 返回既有批次（幂等）。

    2026-11-19 契约扩展：.xls（xlrd）/ .csv（标准库）与 .xlsx（openpyxl）同管线，
    解析分发见 utils.import_engine.read_import_file。
    """
    r = _get_round_or_404(rid)
    election_service.check_voter_editable(r)          # 上传时校验（验收 1 第一次）

    f = request.files.get('file')
    if not f or not f.filename:
        return json_response(False, '请选择 .xlsx / .xls / .xlsb / .csv / .xlsm 名单文件')
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.xlsx', '.xls', '.xlsb', '.csv', '.xlsm'):
        return json_response(False, '仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式')
    data = f.read()
    if len(data) > _BATCH_MAX_SIZE:
        return json_response(False, '文件超过 10MB 上限')

    file_hash = hashlib.sha256(data).hexdigest()
    dup = ElectionBatchImport.query.filter_by(round_id=rid, source_hash=file_hash).first()
    if dup:
        return json_response(True, data={'batch_id': dup.batch_id, 'duplicate': True,
                                         'msg': '该文件已导入过，返回既有批次'})

    from werkzeug.utils import secure_filename
    dest = os.path.join(_batch_imports_dir(),
                        '%s_%s' % (uuid.uuid4().hex[:12], secure_filename(f.filename)))
    with open(dest, 'wb') as out:
        out.write(data)

    batch = ElectionBatchImport(batch_id=uuid.uuid4().hex[:12], round_id=rid,
                                source_file=f.filename, source_path=dest,
                                source_hash=file_hash, operator=session.get('user_id'),
                                status='pending')
    db.session.add(batch)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'add',
                  '上传选民名单（届次 %s，文件 %s）' % (r.round_name, f.filename))
    return json_response(True, data={'batch_id': batch.batch_id})


@election_bp.route('/api/rounds/<int:rid>/voters/import-execute', methods=['POST'])
@login_required
@election_api
def api_voter_import_execute(rid):
    """步2：A1 后台任务（type=election_voter_batch），返回 task_id；执行前二次校验状态。"""
    r = _get_round_or_404(rid)
    election_service.check_voter_editable(r)          # 执行时第二次校验（验收 1）

    batch_id = _text(request.form.get('batch_id'))
    if not batch_id:
        return json_response(False, '缺少批次ID')
    batch = ElectionBatchImport.query.filter_by(batch_id=batch_id, round_id=rid).first()
    if not batch:
        return json_response(False, '批次不存在')
    if batch.status == 'running':
        return json_response(False, '该批次正在导入中'), 409
    if batch.status == 'done':
        return json_response(False, '该批次已完成导入'), 409

    from utils.async_tasks import create_task, run_async
    task = create_task('election_voter_batch', str(session.get('user_id', '')), '选民批量导入')
    db.session.commit()
    run_async('election_voter_batch', task.task_id, run_election_batch, batch_id=batch_id)
    return json_response(True, data={'task_id': task.task_id})


@election_bp.route('/api/rounds/<int:rid>/voters/import-status/<task_id>')
@login_required
@election_api
def api_voter_import_status(rid, task_id):
    """进度轮询（A1 轨迹：pending→running→done/failed）。"""
    _get_round_or_404(rid)
    from utils.async_tasks import get_task
    t = get_task(task_id)
    if not t:
        return json_response(False, '任务不存在'), 404
    return json_response(True, data={'status': t.status, 'progress': t.progress,
                                     'result_summary': t.result_summary})


@election_bp.route('/api/rounds/<int:rid>/voters/import-report/<batch_id>')
@login_required
@election_api
def api_voter_import_report(rid, batch_id):
    """导入结果报告：success_rows / fail_rows / total_rows 对账 + 失败行（已脱敏）。"""
    _get_round_or_404(rid)
    batch = ElectionBatchImport.query.filter_by(batch_id=batch_id, round_id=rid).first()
    if not batch:
        return json_response(False, '批次不存在'), 404
    errors = [{'row_no': e.row_no, 'id_card_masked': e.id_card_masked, 'reason': e.reason}
              for e in ElectionBatchError.query.filter_by(batch_id=batch_id)
              .order_by(ElectionBatchError.row_no).limit(200).all()]
    return json_response(True, data={
        'batch_id': batch.batch_id, 'status': batch.status,
        'total_rows': batch.total_rows, 'success_rows': batch.success_rows,
        'fail_rows': batch.fail_rows, 'errors': errors})


@election_bp.route('/api/rounds/<int:rid>/voters/import-export-failed/<batch_id>')
@login_required
@election_api
def api_voter_import_export_failed(rid, batch_id):
    """仅导出失败行：temp/exports/ token 通道；只输出打码身份证，不含明文。"""
    _get_round_or_404(rid)
    batch = ElectionBatchImport.query.filter_by(batch_id=batch_id, round_id=rid).first()
    if not batch:
        return json_response(False, '批次不存在'), 404
    errs = ElectionBatchError.query.filter_by(batch_id=batch_id)\
        .order_by(ElectionBatchError.row_no).all()
    rows = [[e.row_no, e.id_card_masked or '', e.reason or ''] for e in errs]
    abs_path, token_url = write_export_xlsx(
        ['行号', '身份证(脱敏)', '失败原因'], rows,
        module='election', filename='选民导入失败行.xlsx')
    if not token_url:
        return json_response(False, '导出通道未就绪')
    log_operation(session.get('username', ''), 'election', 'export',
                  '导出选民导入失败行（%d 行）' % len(rows))
    return json_response(True, '导出成功', data={
        'download_url': '/report/download/%s' % token_url,
        'file': os.path.basename(abs_path),
    })


# ═══════════════════ 当选结果 ═══════════════════

@election_bp.route('/api/rounds/<int:rid>/results')
@login_required
def api_result_list(rid):
    _get_round_or_404(rid)
    query = ElectionResult.query.filter_by(round_id=rid).order_by(ElectionResult.id.desc())
    position = _text(request.args.get('position'))
    is_elected = _text(request.args.get('is_elected'))
    if position:
        query = query.filter(ElectionResult.position == position)
    if is_elected in ('1', '0'):
        query = query.filter(ElectionResult.is_elected == (is_elected == '1'))
    return json_response(success=True, data={
        'data': [_result_dict(e) for e in query.all()],
        'reconcile': election_service.reconcile_round(rid),
    })


def _validate_result_form():
    """一页纸 §4/§10：E-001 取字典 D-03；E-002 必填；E-004 ≥0（V-05）。"""
    form = request.form
    position = _text(form.get('position'))
    if not position:
        raise ValueError('当选职务必填')
    if position not in get_enum('当选职务'):
        raise ValueError('当选职务取值不合法（字典 D-03）')
    winner_name = _text(form.get('winner_name'))
    if not winner_name:
        raise ValueError('当选人姓名必填')
    try:
        votes = int(_text(form.get('votes')) or '0')
    except ValueError:
        raise ValueError('得票须为非负整数')
    if votes < 0:
        raise ValueError('得票须为非负整数（V-05）')
    notice_date = _parse_date_or_raise(form.get('notice_date'), '公示日期')
    winner_id_card = _text(form.get('winner_id_card')).upper()
    if winner_id_card and len(winner_id_card) != 18:
        raise ValueError('当选人身份证号须为 18 位')
    return {'position': position, 'winner_name': winner_name, 'votes': votes,
            'is_elected': _parse_bool(form, 'is_elected'), 'notice_date': notice_date,
            'winner_id_card': winner_id_card, 'remark': _text(form.get('remark'))}


@election_bp.route('/api/rounds/<int:rid>/results/add', methods=['POST'])
@login_required
@election_api
def api_result_add(rid):
    r = _get_round_or_404(rid)
    election_service.check_result_editable(r)          # 录入窗口：投票/结果公示 → 409
    fields = _validate_result_form()
    e = ElectionResult(round_id=rid,
                       position=fields['position'], winner_name=fields['winner_name'],
                       votes=fields['votes'], is_elected=fields['is_elected'],
                       notice_date=fields['notice_date'], remark=fields['remark'],
                       winner_id_card_hash=(sha256_hash(fields['winner_id_card'])
                                            if fields['winner_id_card'] else None))
    db.session.add(e)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'add',
                  '录入结果 %s：%s（%d 票）' % (r.round_name, e.winner_name, e.votes))
    return json_response(True, '', {'id': e.id})


@election_bp.route('/api/results/<int:eid>/edit', methods=['POST'])
@login_required
@election_api
def api_result_edit(eid):
    e = ElectionResult.query.get(eid)
    if not e:
        return json_response(False, '结果记录不存在')
    r = _get_round_or_404(e.round_id)
    election_service.check_result_editable(r)
    fields = _validate_result_form()
    e.position = fields['position']
    e.winner_name = fields['winner_name']
    e.votes = fields['votes']
    e.is_elected = fields['is_elected']
    e.notice_date = fields['notice_date']
    e.remark = fields['remark']
    e.winner_id_card_hash = (sha256_hash(fields['winner_id_card'])
                             if fields['winner_id_card'] else e.winner_id_card_hash)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'edit', '修改结果 %s' % e.winner_name)
    return json_response(True, '')


@election_bp.route('/api/results/<int:eid>/delete', methods=['POST'])
@login_required
@election_api
def api_result_delete(eid):
    e = ElectionResult.query.get(eid)
    if not e:
        return json_response(False, '结果记录不存在')
    r = _get_round_or_404(e.round_id)
    election_service.check_result_editable(r)          # 已完成届次结果冻结
    name = e.winner_name
    db.session.delete(e)
    db.session.commit()
    log_operation(session.get('username', ''), 'election', 'delete', '删除结果 %s' % name)
    return json_response(True, '删除成功')


# ═══════════════════ 对账（§7 M-01/M-02/M-03） ═══════════════════

@election_bp.route('/api/rounds/<int:rid>/reconcile')
@login_required
def api_round_reconcile(rid):
    _get_round_or_404(rid)
    return json_response(success=True, data=election_service.reconcile_round(rid))


# ═══════════════════ 092 模板附件区（module_key='election'，禁复制实现） ═══════════════════

from utils.attachment_service import register_attachment_routes as _register_attachment_routes  # noqa: E402
_register_attachment_routes(election_bp, 'election')
