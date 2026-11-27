from flask import Blueprint, render_template, request, jsonify, send_file, session, redirect
from models import db, SubsidyProject, SubsidyRecord, SubsidyBatchImport, SubsidyBatchError, DictCategory, DictItem
from utils.decorators import login_required
from utils.helpers import json_response, paginate_query, log_operation, export_excel
from utils.subsidy_service import PROJECT_TYPES, CYCLES, PROJECT_STATUSES, _parse_target_tags, check_eligibility, run_subsidy_batch
from utils.crypto import sha256_hash
from datetime import datetime, date
import io, os, json, hashlib, uuid
# 092 模板附件区：服务收口（三蓝图唯一真源，禁复制实现）
from utils import attachment_service
from utils.attachment_service import attachment_api

subsidy_bp = Blueprint('subsidy', __name__)

# 既有端点 def list() 在模块级遮蔽内置 list；辅助函数用此处捕获的内置引用
_builtin_list = list
_builtin_str = str

# 枚举白名单（单一真值，来源 utils/subsidy_service.py）
_PROJECT_TYPE_SET = set(PROJECT_TYPES)
_CYCLE_SET = set(CYCLES)
_PROJECT_STATUS_SET = set(PROJECT_STATUSES)

# 082 B-2 救助动态状态：字典分类「救助动态状态」为取值真源，常量仅兜底
SUBSIDY_STATUSES = ['正常', '停发', '新增']


def _status_values():
    """救助动态状态取值（字典 active 项按 sort_order）；分类缺失/无项时回退常量。"""
    cat = DictCategory.query.filter_by(name='救助动态状态').first()
    if not cat:
        return SUBSIDY_STATUSES
    vals = [i.value for i in DictItem.query.filter_by(
        category_id=cat.id, is_active=True).order_by(DictItem.sort_order).all()]
    return vals or SUBSIDY_STATUSES

# ==================== T14-1 补贴项目配置（Sprint3 第一批） ====================

def _parse_amount(raw):
    """金额解析：空 → None（允许）；非空须 ≥0、≤99999999.99、两位小数（Numeric10,2）。

    P3-6 整改：改用 Decimal 校验两位小数，避免 round(v,2)!=v 的浮点精度陷阱
    （对齐 utils.subsidy_service._batch_parse_amount 口径）。
    """
    from decimal import Decimal, InvalidOperation
    s = (raw or '').strip()
    if not s:
        return None, True
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None, False
    if d < 0 or d > Decimal('99999999.99'):
        return None, False
    if d.as_tuple().exponent < -2:  # 超过两位小数 → 非法
        return None, False
    return float(d), True


def _parse_int_opt(raw):
    """可选整数解析：空 → (None, True)；非整数 → (None, False)。"""
    s = (raw or '').strip()
    if not s:
        return None, True
    try:
        return int(s), True
    except ValueError:
        return None, False


def _parse_tags_form(raw):
    """target_tags 表单（JSON 数组字符串）：空 → []；坏 JSON/非字符串数组 → None（拒绝）。"""
    s = (raw or '').strip()
    if not s:
        return [], True
    try:
        tags = json.loads(s)
    except (ValueError, TypeError):
        return None, False
    if not isinstance(tags, _builtin_list) or not all(isinstance(t, _builtin_str) for t in tags):
        return None, False
    return [t.strip() for t in tags if t.strip()], True


def _project_to_dict(it):
    return {
        'id': it.id,
        'name': it.name,
        'project_type': it.project_type,
        'cycle': it.cycle,
        'default_amount': float(it.default_amount) if it.default_amount is not None else None,
        'age_min': it.age_min,
        'age_max': it.age_max,
        'target_tags': _parse_target_tags(it.target_tags),
        'status': it.status,
        'remark': it.remark or '',
        'create_time': it.create_time.isoformat() if it.create_time else '',
    }


@subsidy_bp.route('/projects')
@login_required
def project_list_page():
    """补贴项目配置页面（T14-1，复用 subsidy.view 权限键）。"""
    return render_template('subsidy/project_list.html',
                           project_types=PROJECT_TYPES,
                           cycles=CYCLES,
                           statuses=PROJECT_STATUSES)


@subsidy_bp.route('/api/project/list')
@login_required
def api_project_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)

    query = SubsidyProject.query
    name = request.args.get('name', '', type=str).strip()
    ptype = request.args.get('project_type', '', type=str).strip()
    status = request.args.get('status', '', type=str).strip()

    if name:
        query = query.filter(SubsidyProject.name.like(f'%{name}%'))
    if ptype:
        query = query.filter(SubsidyProject.project_type == ptype)
    if status:
        query = query.filter(SubsidyProject.status == status)

    query = query.order_by(SubsidyProject.id.desc())
    items, total, pages = paginate_query(query, page=page, per_page=limit)

    return jsonify({
        'success': True,
        'data': [_project_to_dict(it) for it in items],
        'total': total,
        'page': page,
        'pages': pages,
    })


@subsidy_bp.route('/api/project/detail/<int:item_id>')
@login_required
def api_project_detail(item_id):
    """单条详情（R-12：编辑改走 detail）。"""
    item = SubsidyProject.query.get(item_id)
    if not item:
        return json_response(False, '记录不存在')
    return json_response(True, data=_project_to_dict(item))


@subsidy_bp.route('/api/project/save', methods=['POST'])
@login_required
def api_project_save():
    from flask import session
    try:
        item_id = request.form.get('id', 0, type=int)
        name = request.form.get('name', '').strip()
        project_type = request.form.get('project_type', '').strip()
        cycle = request.form.get('cycle', '').strip()
        status = request.form.get('status', '').strip() or '启用'
        remark = request.form.get('remark', '').strip()

        # ---- 字段校验（T14-1） ----
        if not name:
            return json_response(False, '项目名称不能为空')
        if len(name) > 100:
            return json_response(False, '项目名称过长（≤100 字符）')
        if project_type not in _PROJECT_TYPE_SET:
            return json_response(False, '项目类型不合法')
        if cycle not in _CYCLE_SET:
            return json_response(False, '发放周期不合法')
        if status not in _PROJECT_STATUS_SET:
            return json_response(False, '状态不合法')

        default_amount, amount_ok = _parse_amount(request.form.get('default_amount', ''))
        if not amount_ok:
            return json_response(False, '默认金额不合法（≥0，两位小数）')

        age_min, age_min_ok = _parse_int_opt(request.form.get('age_min', ''))
        age_max, age_max_ok = _parse_int_opt(request.form.get('age_max', ''))
        if not age_min_ok or not age_max_ok:
            return json_response(False, '年龄边界不合法')
        if age_min is not None and age_min < 0:
            return json_response(False, '年龄下限不能为负')
        if age_max is not None and age_max < 0:
            return json_response(False, '年龄上限不能为负')
        if age_min is not None and age_max is not None and age_min > age_max:
            return json_response(False, '年龄下限不能大于上限')

        target_tags, tags_ok = _parse_tags_form(request.form.get('target_tags', ''))
        if not tags_ok:
            return json_response(False, '适用标签格式不正确')
        if len(remark) > 200:
            return json_response(False, '备注过长（≤200 字符）')

        # ---- 写库（新增/更新） ----
        if item_id:
            item = SubsidyProject.query.get(item_id)
            if not item:
                return json_response(False, '记录不存在')
            item.name = name
            item.project_type = project_type
            item.cycle = cycle
            item.default_amount = default_amount
            item.age_min = age_min
            item.age_max = age_max
            item.target_tags = json.dumps(target_tags, ensure_ascii=False)
            item.status = status
            item.remark = remark
            log_operation(session.get('username', ''), 'subsidy', 'edit',
                          f'编辑补贴项目: {name} ({project_type})')
        else:
            item = SubsidyProject(
                name=name, project_type=project_type, cycle=cycle,
                default_amount=default_amount, age_min=age_min, age_max=age_max,
                target_tags=json.dumps(target_tags, ensure_ascii=False),
                status=status, remark=remark,
                create_by=session.get('user_id'),
            )
            db.session.add(item)
            log_operation(session.get('username', ''), 'subsidy', 'add',
                          f'新增补贴项目: {name} ({project_type})')

        db.session.commit()
        return json_response(True, '保存成功')

    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')


@subsidy_bp.route('/api/project/delete/<int:id>', methods=['POST'])
@login_required
def api_project_delete(id):
    from flask import session
    item = SubsidyProject.query.get(id)
    if not item:
        return json_response(False, '记录不存在')
    # 审计安全：已有发放记录的项目禁删（可改为停用）
    if SubsidyRecord.query.filter_by(project_id=id).first():
        return json_response(False, '该项目已有发放记录，禁止删除（可改为停用）')
    name = item.name
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username', ''), 'subsidy', 'delete',
                  f'删除补贴项目: {name}')
    return json_response(True, '已删除')


# ==================== T14-3 发放登记 + 冲正 / T14-4 个人总览（Sprint3 第二批） ====================

def _parse_date_opt(raw):
    """日期解析（YYYY-MM-DD）：空 → (None, True)；非法 → (None, False)。"""
    s = (raw or '').strip()
    if not s:
        return None, True
    try:
        return datetime.strptime(s, '%Y-%m-%d').date(), True
    except ValueError:
        return None, False


def _parse_period(raw):
    """发放期间（YYYY-MM）：非法格式返回 (None, False)。"""
    s = (raw or '').strip()
    if not s:
        return None, False
    if len(s) != 7 or s[4] != '-':
        return None, False
    try:
        y, m = int(s[:4]), int(s[5:])
    except ValueError:
        return None, False
    if not (1 <= m <= 12):
        return None, False
    return s, True


def _record_to_dict(it, project_name=''):
    """发放记录序列化（总览/登记回显共用）。"""
    return {
        'id': it.id,
        'resident_id': it.resident_id,
        'project_id': it.project_id,
        'project_name': project_name or '',
        'issue_date': it.issue_date.isoformat() if it.issue_date else '',
        'period': it.period or '',
        'amount': float(it.amount) if it.amount is not None else 0.0,
        'channel': it.channel or '',
        'source': it.source or '',
        'status': it.status or '',
        'note': it.note or '',
    }


@subsidy_bp.route('/records')
@login_required
def record_page():
    """发放登记页面（T14-3，复用 subsidy.view 权限键）。"""
    return render_template('subsidy/record.html')


@subsidy_bp.route('/api/record/list')
@login_required
def api_record_list():
    """发放记录列表（登记页查询回显 + 冲正入口）。"""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)

    query = SubsidyRecord.query
    resident_id = request.args.get('resident_id', 0, type=int)
    project_id = request.args.get('project_id', 0, type=int)
    period = request.args.get('period', '', type=str).strip()
    status = request.args.get('status', '', type=str).strip()

    if resident_id:
        query = query.filter(SubsidyRecord.resident_id == resident_id)
    if project_id:
        query = query.filter(SubsidyRecord.project_id == project_id)
    if period:
        query = query.filter(SubsidyRecord.period == period)
    if status:
        query = query.filter(SubsidyRecord.status == status)

    query = query.order_by(SubsidyRecord.id.desc())
    items, total, pages = paginate_query(query, page=page, per_page=limit)

    # 项目名映射（避免 N+1）
    pids = {it.project_id for it in items}
    name_map = {p.id: p.name for p in SubsidyProject.query.filter(
        SubsidyProject.id.in_(pids)).all()} if pids else {}

    return jsonify({
        'success': True,
        'data': [_record_to_dict(it, name_map.get(it.project_id, '')) for it in items],
        'total': total,
        'page': page,
        'pages': pages,
    })


@subsidy_bp.route('/api/record/add', methods=['POST'])
@login_required
def record_add():
    """单条发放登记（含防误发校验，E3/E4/E6）。"""
    from flask import session
    try:
        resident_id = request.form.get('resident_id', 0, type=int)
        project_id = request.form.get('project_id', 0, type=int)
        issue_date_raw = request.form.get('issue_date', '').strip()
        period = request.form.get('period', '').strip()
        amount_raw = request.form.get('amount', '').strip()
        note = request.form.get('note', '').strip()
        status = request.form.get('status', '').strip() or '正常'

        if not resident_id or not project_id:
            return json_response(False, '居民与项目不能为空')
        if status not in _status_values():
            return json_response(False, '状态不合法（正常/停发/新增）')

        from models import Resident
        resident = Resident.query.get(resident_id)
        project = SubsidyProject.query.get(project_id)
        if not resident:
            return json_response(False, '居民不存在')
        if not project:
            return json_response(False, '补贴项目不存在')

        issue_date, date_ok = _parse_date_opt(issue_date_raw)
        if not date_ok:
            return json_response(False, '发放日期格式不正确（YYYY-MM-DD）')
        if issue_date is None:
            issue_date = date.today()

        period_ok = _parse_period(period)
        if not period_ok[1]:
            return json_response(False, '发放期间格式不正确（YYYY-MM）')

        # 金额：缺省取项目默认金额；显式传入走 _parse_amount 校验
        if amount_raw:
            amount, amount_ok = _parse_amount(amount_raw)
            if not amount_ok:
                return json_response(False, '金额不合法（≥0，两位小数）')
            if amount is None:
                amount = 0
        else:
            amount = float(project.default_amount) if project.default_amount is not None else 0.0

        if len(note) > 200:
            return json_response(False, '备注过长（≤200 字符）')

        # 防误发校验（唯一判定入口）
        eligibility = check_eligibility(resident, project)
        if not eligibility['eligible']:
            log_operation(session.get('username', ''), 'subsidy', 'blocked_subsidy',
                          f'拦截补贴登记: {resident.name} → {project.name}',
                          detail='; '.join(eligibility['reasons']), result='blocked')
            return json_response(False, '登记被拦截: ' + '；'.join(eligibility['reasons']),
                                 data={'blocked': True, 'reasons': eligibility['reasons']})

        # 放行写库
        rec = SubsidyRecord(
            resident_id=resident_id, project_id=project_id,
            issue_date=issue_date, period=period, amount=amount,
            channel='镇级直发', source='normal', status=status, note=note,
            create_by=session.get('user_id'),
        )
        db.session.add(rec)
        db.session.commit()
        log_operation(session.get('username', ''), 'subsidy', 'add',
                      f'登记补贴: {resident.name} → {project.name} ({period} {amount})')

        flow_out = eligibility['detail'].get('flow_out', False)
        return json_response(True, '登记成功', data={
            'id': rec.id,
            'flow_out': flow_out,
            'flow_hint': '该居民已流出，请确认发放资格' if flow_out else '',
        })

    except Exception as e:
        db.session.rollback()
        return json_response(False, f'登记失败: {str(e)}')


@subsidy_bp.route('/api/record/reverse-save', methods=['POST'])
@login_required
def record_reverse_save():
    """冲正（E17）：新增反向负额记录，业务字段只增不改 + source_hash 幂等防重。"""
    from flask import session
    try:
        record_id = request.form.get('id', 0, type=int)
        note = request.form.get('note', '').strip()
        if not record_id:
            return json_response(False, '缺少原记录ID')

        original = SubsidyRecord.query.get(record_id)
        if not original:
            return json_response(False, '原发放记录不存在')

        # source_hash 幂等防重（R5）：同一原记录只允许一次冲正
        reverse_hash = sha256_hash(f"{record_id}:reverse")
        if SubsidyRecord.query.filter_by(source_hash=reverse_hash).first():
            return json_response(False, '该记录已冲正，禁止重复冲正')

        if len(note) > 200:
            return json_response(False, '备注过长（≤200 字符）')

        # 反向负额记录（不改原记录）
        reverse = SubsidyRecord(
            resident_id=original.resident_id,
            project_id=original.project_id,
            issue_date=original.issue_date,
            period=original.period,
            amount=-float(original.amount) if original.amount is not None else 0.0,
            channel=original.channel or '镇级直发',
            source='reverse',
            status='正常',
            note=note or f'冲正原记录#{record_id}',
            source_hash=reverse_hash,
            create_by=session.get('user_id'),
        )
        db.session.add(reverse)
        db.session.commit()
        log_operation(session.get('username', ''), 'subsidy', 'reverse',
                      f'冲正补贴记录#{record_id}: {original.period} {float(original.amount)}')
        return json_response(True, '冲正成功', data={'id': reverse.id})

    except Exception as e:
        db.session.rollback()
        return json_response(False, f'冲正失败: {str(e)}')


@subsidy_bp.route('/api/resident/<int:resident_id>/overview')
@login_required
def subsidy_overview(resident_id):
    """个人补贴总览（E2）：历史明细 + 累计总额 + 最近发放期间，统一取 subsidy_record（Q1）。"""
    from models import Resident
    resident = Resident.query.get(resident_id)
    if not resident:
        return json_response(False, '居民不存在')

    project_id = request.args.get('project_id', 0, type=int)
    period = request.args.get('period', '', type=str).strip()

    query = SubsidyRecord.query.filter(SubsidyRecord.resident_id == resident_id)
    if project_id:
        query = query.filter(SubsidyRecord.project_id == project_id)
    if period:
        query = query.filter(SubsidyRecord.period == period)

    records = query.order_by(SubsidyRecord.issue_date.desc(), SubsidyRecord.id.desc()).all()

    # 项目名映射
    pids = {r.project_id for r in records}
    name_map = {p.id: p.name for p in SubsidyProject.query.filter(
        SubsidyProject.id.in_(pids)).all()} if pids else {}

    detail = [_record_to_dict(r, name_map.get(r.project_id, '')) for r in records]

    # 累计总额：正负求和（source=reverse 负额自然抵消；U2 默认口径：冲正不计入累计）
    total_amount = sum(float(r.amount) if r.amount is not None else 0.0 for r in records)
    total_amount = round(total_amount, 2)

    # 最近发放期间：取正向记录（source != reverse）中 period 最大者
    positive_periods = [r.period for r in records if r.source != 'reverse' and r.period]
    last_period = max(positive_periods) if positive_periods else ''

    from utils.subsidy_service import collect_policy_subsidies
    policy = collect_policy_subsidies(resident)
    return json_response(True, data={
        'resident': {'id': resident.id, 'name': resident.name or ''},
        'detail': detail,
        'total_amount': total_amount,
        'last_period': last_period,
        'count': len(detail),
        'policy_subsidies': policy['items'],
        'policy_total': policy['total'],
        'policy_count': policy['count'],
    })


# ==================== T14-5 批量登记导入（E5，Sprint3 第三批） ====================

_BATCH_MAX_SIZE = 10 * 1024 * 1024  # 10MB


def _imports_dir() -> str:
    from config import Config
    d = getattr(Config, 'IMPORTS_DIR', None)
    if not d:
        d = os.path.join(os.environ.get('SHUCUN_TEMP_DIR', ''), 'imports')
    os.makedirs(d, exist_ok=True)
    return d


def _require_subsidy_batch() -> bool:
    """批量登记需 import_batch special 位（对齐详细设计 §8 record_import 权限）。"""
    from flask import session
    from models import User
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    if not user:
        return False
    if user.role_id == 1:
        return True
    from utils.permissions import user_has_special
    return user_has_special(user, 'import_batch')


def _batch_denied():
    return json_response(False, '无批量登记权限（import_batch）'), 403


@subsidy_bp.route('/api/record/import-upload', methods=['POST'])
@login_required
def record_import_upload():
    """批量登记步1：上传 xlsx（≤10MB）+ 建 SubsidyBatchImport 批次。"""
    if not _require_subsidy_batch():
        return _batch_denied()
    from flask import session
    from werkzeug.utils import secure_filename

    project_id = request.form.get('project_id', 0, type=int)
    if not project_id:
        return json_response(False, '请选择补贴项目')
    project = SubsidyProject.query.get(project_id)
    if not project:
        return json_response(False, '补贴项目不存在')

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
    if len(data) > _BATCH_MAX_SIZE:
        return json_response(False, '文件超过 10MB 上限')

    # source_hash 防重复导入（作用域：project_id + source_hash）
    file_hash = hashlib.sha256(data).hexdigest()
    dup = SubsidyBatchImport.query.filter_by(
        project_id=project_id, source_hash=file_hash).first()
    if dup:
        return json_response(True, data={'batch_id': dup.batch_id, 'duplicate': True,
                                         'msg': '本项目已导入该文件，返回既有批次'})

    fname = f"{uuid.uuid4().hex[:12]}_{preserve_spreadsheet_ext(f.filename, secure_filename(f.filename))}"
    dest = os.path.join(_imports_dir(), fname)
    with open(dest, 'wb') as out:
        out.write(data)

    batch = SubsidyBatchImport(
        batch_id=uuid.uuid4().hex[:12],
        project_id=project_id,
        source_file=f.filename,
        source_path=dest,
        source_hash=file_hash,
        operator=session.get('user_id'),
        status='pending',
    )
    db.session.add(batch)
    db.session.commit()
    return json_response(True, data={'batch_id': batch.batch_id})


@subsidy_bp.route('/api/record/import-execute', methods=['POST'])
@login_required
def record_import_execute():
    """批量登记步2：提交 A1 后台任务（type=subsidy_batch），返回 task_id。"""
    if not _require_subsidy_batch():
        return _batch_denied()
    from flask import session
    from utils.async_tasks import create_task, run_async

    batch_id = request.form.get('batch_id', '').strip()
    if not batch_id:
        return json_response(False, '缺少批次ID')

    batch = SubsidyBatchImport.query.filter_by(batch_id=batch_id).first()
    if not batch:
        return json_response(False, '批次不存在')
    if batch.status == 'running':
        return json_response(False, '该批次正在登记中'), 409
    if batch.status == 'done':
        return json_response(False, '该批次已完成登记'), 409

    task = create_task('subsidy_batch', str(session.get('user_id', '')), '补贴批量登记')
    db.session.commit()
    run_async('subsidy_batch', task.task_id, run_subsidy_batch, batch_id=batch_id)
    return json_response(True, data={'task_id': task.task_id})


@subsidy_bp.route('/api/record/import-status/<task_id>', methods=['GET'])
@login_required
def record_import_status(task_id):
    """批量登记进度轮询（A1 轨迹：pending→running→done/failed）。"""
    from utils.async_tasks import get_task
    t = get_task(task_id)
    if not t:
        return json_response(False, '任务不存在'), 404
    return json_response(True, data={
        'status': t.status, 'progress': t.progress,
        'result_summary': t.result_summary})


@subsidy_bp.route('/api/record/import-report/<batch_id>', methods=['GET'])
@login_required
def record_import_report(batch_id):
    """批量登记结果报告（脱敏）：批次统计 + 失败行明细（身份证前6后4）。"""
    batch = SubsidyBatchImport.query.filter_by(batch_id=batch_id).first()
    if not batch:
        return json_response(False, '批次不存在'), 404
    errors = [{
        'row_no': e.row_no, 'id_card_masked': e.id_card_masked, 'reason': e.reason,
    } for e in SubsidyBatchError.query.filter_by(batch_id=batch_id)
        .order_by(SubsidyBatchError.row_no).limit(200).all()]
    return json_response(True, data={
        'batch_id': batch.batch_id, 'status': batch.status,
        'project_id': batch.project_id,
        'total_rows': batch.total_rows, 'success_rows': batch.success_rows,
        'fail_rows': batch.fail_rows, 'errors': errors})


@subsidy_bp.route('/api/record/import-export-failed/<batch_id>', methods=['GET'])
@login_required
def record_import_export_failed(batch_id):
    """仅导出批量登记失败行（108 对齐：write_export_xlsx token 通道，302 直下）。

    行数据落库时已脱敏（SubsidyBatchError.id_card_masked）；302 到一次性令牌
    下载端点 /report/download/<token>，导航式调用（<a href>/location.href）天然跟随。
    """
    from utils.helpers import write_export_xlsx
    batch = SubsidyBatchImport.query.filter_by(batch_id=batch_id).first()
    if not batch:
        return json_response(False, '批次不存在'), 404
    errs = SubsidyBatchError.query.filter_by(batch_id=batch_id)\
        .order_by(SubsidyBatchError.row_no).all()
    rows = [[e.row_no, e.id_card_masked or '', e.reason or ''] for e in errs]
    abs_path, token_url = write_export_xlsx(
        ['行号', '身份证(脱敏)', '失败原因'], rows,
        module='subsidy', filename=f'subsidy_batch_failed_{batch_id}.xlsx')
    if not token_url:
        return json_response(False, '导出通道未就绪')
    log_operation(session.get('username', ''), 'subsidy', 'export',
                  '导出补贴批量登记失败行（%d 行）' % len(rows))
    return redirect('/report/download/%s' % token_url)


# ═══════════════════ 092 模板附件区（收口 utils/attachment_service，禁复制实现） ═══════════════════

from utils.attachment_service import register_attachment_routes as _register_attachment_routes  # noqa: E402
_register_attachment_routes(subsidy_bp, 'subsidy')
