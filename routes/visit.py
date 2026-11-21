# -*- coding: utf-8 -*-
"""routes.visit — 走访计划蓝图（Sprint5 T18-1）。

owner map：本模块只做接线/校验/分页/留痕；走访口径与候选装配唯一落
utils/visit_service.py（禁在路由内重写判定逻辑）。
模块权限由 app.py 全局 before_request 经 BLUEPRINT_MODULES 自动映射
（save→visit.add、delete→visit.delete、其余 GET→visit.view）。
"""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, g, jsonify, render_template, request, session

from models import VisitPlan, VisitRecord, db
from utils import visit_service
from utils.decorators import login_required
from utils.helpers import json_response, log_operation, paginate_query
from utils.masking import child_rows_guard, filter_child_rows

visit_bp = Blueprint('visit', __name__)


def _plan_dict(p):
    completed = visit_service.completed_count(p.id)
    target = p.target_count or 0
    rate = round(completed / target * 100, 1) if target else 0
    return {
        'id': p.id,
        'plan_name': p.plan_name,
        'plan_type': p.plan_type,
        'scope_params': p.scope_params or '',
        'target_count': p.target_count or 0,
        'completed_count': completed,
        'rate': rate,
        'status': p.status,
        'created_by': p.created_by,
        'created_at': p.created_at.strftime('%Y-%m-%d %H:%M') if p.created_at else '',
        'finished_at': p.finished_at.strftime('%Y-%m-%d %H:%M') if p.finished_at else '',
    }


@visit_bp.route('/')
@login_required
def list_page():
    """走访计划列表页。"""
    return render_template(
        'visit/plan_list.html',
        plan_types=visit_service.PLAN_TYPES,
        plan_statuses=visit_service.PLAN_STATUSES,
        object_type_groups=visit_service.OBJECT_TYPE_GROUPS,
        policy_tags=visit_service.POLICY_TAGS,
    )


@visit_bp.route('/api/list')
@login_required
def api_list():
    """走访计划分页列表（plan_name/plan_type/status 过滤）。"""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    query = VisitPlan.query
    name = request.args.get('plan_name', '').strip()
    ptype = request.args.get('plan_type', '').strip()
    status = request.args.get('status', '').strip()
    if name:
        query = query.filter(VisitPlan.plan_name.like(f'%{name}%'))
    if ptype:
        query = query.filter(VisitPlan.plan_type == ptype)
    if status:
        query = query.filter(VisitPlan.status == status)
    query = query.order_by(VisitPlan.id.desc())
    items, total, pages = paginate_query(query, page=page, per_page=limit)
    data = [_plan_dict(p) for p in items]
    return jsonify({'success': True, 'data': data, 'total': total, 'page': page, 'pages': pages})


@visit_bp.route('/api/detail/<int:item_id>')
@login_required
def api_detail(item_id):
    """单条详情（R-12：编辑改走 detail，避免拉全量 find 超限落空）。"""
    item = VisitPlan.query.get(item_id)
    if not item:
        return json_response(False, '记录不存在')
    return json_response(True, data=_plan_dict(item))


@visit_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    """走访计划新建/更新（按 id upsert，幂等；save→visit.add 权限自动映射）。"""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    item_id = data.get('id')
    plan_name = (data.get('plan_name') or '').strip()
    plan_type = (data.get('plan_type') or '').strip()
    status = (data.get('status') or '草稿').strip()

    if not plan_name:
        return json_response(False, '计划名称不能为空')
    if len(plan_name) > 100:
        return json_response(False, '计划名称过长（≤100 字符）')
    if plan_type not in visit_service.PLAN_TYPES:
        return json_response(False, '计划类型不合法')
    if status not in visit_service.PLAN_STATUSES:
        return json_response(False, '状态不合法')

    params, err = visit_service.parse_scope_params(data.get('scope_params'))
    if err:
        return json_response(False, err)
    params = params or {}
    if plan_type == '按区域':
        af = params.get('area_field') or 'village_group'
        if af not in visit_service.AREA_FIELDS:
            return json_response(False, '区域字段不合法')
        if not params.get('values'):
            return json_response(False, '按区域计划必须指定区域（values）')
    if plan_type == '按对象类型':
        types = params.get('types') or []
        if types and any(t not in visit_service.OBJECT_TYPE_GROUPS for t in types):
            return json_response(False, '对象类型不合法')
    if plan_type == '按标签':
        tags = params.get('tags') or []
        if tags and any(t not in visit_service.POLICY_TAGS for t in tags):
            return json_response(False, '标签不合法（须在政策性标签集合内）')

    try:
        if item_id:
            plan = VisitPlan.query.get(item_id)
            if not plan:
                return json_response(False, '计划不存在')
            action = 'edit'
        else:
            plan = VisitPlan()
            plan.created_by = session.get('user_id')
            action = 'add'
        plan.plan_name = plan_name
        plan.plan_type = plan_type
        plan.status = status
        plan.scope_params = json.dumps(params, ensure_ascii=False) if params else None
        plan.target_count = len(visit_service.candidate_ids(plan_type, params))
        db.session.add(plan)
        db.session.commit()
        log_operation(session.get('username', ''), 'visit', action,
                      f'走访计划[{action}]：{plan_name}', detail=str(plan.id))
        return json_response(True, '保存成功', data={'id': plan.id, 'target_count': plan.target_count})
    except Exception:
        db.session.rollback()
        return json_response(False, '保存失败，请重试')


@visit_bp.route('/api/delete/<int:pid>', methods=['POST'])
@login_required
def api_delete(pid):
    """删除走访计划（已有走访记录禁删，提示改停用）。"""
    plan = VisitPlan.query.get(pid)
    if not plan:
        return json_response(False, '计划不存在')
    has_record = VisitRecord.query.filter(VisitRecord.plan_id == pid).first()
    if has_record:
        return json_response(False, '该计划已有走访记录，禁止删除，可改为「已完成」')
    try:
        db.session.delete(plan)
        db.session.commit()
        log_operation(session.get('username', ''), 'visit', 'delete',
                      f'走访计划[删除]：{plan.plan_name}', detail=str(pid))
        return json_response(True, '删除成功')
    except Exception:
        db.session.rollback()
        return json_response(False, '删除失败，请重试')


@visit_bp.route('/api/candidates')
@login_required
@child_rows_guard
def api_candidates():
    """待访清单生成 API（R7-E2：应访对象/区域/计划时间；统一排除死亡+流出）。

    行级可见：留守/困境儿童行按 can_see_children 过滤（@child_rows_guard）。
    同时回写 plan.target_count（幂等，重复调用结果一致）。
    """
    plan_id = request.args.get('plan_id', type=int)
    if not plan_id:
        return json_response(False, '缺少 plan_id')
    plan = VisitPlan.query.get(plan_id)
    if not plan:
        return json_response(False, '计划不存在')

    params, err = visit_service.parse_scope_params(plan.scope_params)
    if err:
        return json_response(False, err)
    params = params or {}

    ids = visit_service.candidate_ids(plan.plan_type, params)
    rows = visit_service.candidate_rows(ids)

    keyword = request.args.get('keyword', '').strip()
    if keyword:
        rows = [r for r in rows
                if keyword in (r['name'] or '') or keyword in (r['village_group'] or '')
                or keyword in (r['grid'] or '')]

    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    total = len(rows)
    start = (page - 1) * limit
    page_rows = rows[start:start + limit]
    # 儿童行级可见过滤（非 admin/民政主任 隐藏留守/困境儿童明细行）
    page_rows = filter_child_rows(page_rows, g.get('can_see_children', False))
    pages = (total + limit - 1) // limit if limit else 0

    if plan.target_count != total:
        plan.target_count = total
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

    return jsonify({'success': True, 'data': page_rows, 'total': total,
                    'page': page, 'pages': pages, 'plan_id': plan.id})


# ============ T18-2 连续补录 ============

@visit_bp.route('/record_entry')
@login_required
def record_entry():
    """连续补录页（US-4：文本+拍照+需求采集，一次保存多条）。"""
    return render_template(
        'visit/record_entry.html',
        plan_types=visit_service.PLAN_TYPES,
        plan_statuses=visit_service.PLAN_STATUSES,
        visit_types=visit_service.VISIT_TYPES,
    )


@visit_bp.route('/api/record/batch', methods=['POST'])
@login_required
@child_rows_guard
def api_batch_save():
    """批量补录（视图名含 save → visit.add 权限自动映射）。

    请求：{"plan_id":1,"rows":[{resident_id,visit_date,visit_type,content,has_need,need_desc,photos}]}
    幂等：同键 (plan_id, resident_id, visit_date, visit_type) upsert（用户拍板）。
    行级失败：errors=[{row,msg}]，成功行照常落库（部分成功）。
    """
    data = request.get_json(silent=True) or {}
    plan_id = data.get('plan_id')
    rows = data.get('rows')
    if not plan_id or not isinstance(rows, list):
        return json_response(False, '参数不完整（plan_id + rows）')
    plan = VisitPlan.query.get(plan_id)
    if not plan:
        return json_response(False, '计划不存在')

    result = visit_service.save_batch_records(
        plan, rows, session.get('user_id'),
        can_see_children=g.get('can_see_children', False),
    )
    log_operation(session.get('username', ''), 'visit', 'batch_save',
                  f'走访批量补录：成功{result["success"]}条失败{len(result["errors"])}条',
                  detail=str(plan_id))
    return jsonify({'success': True, 'data': result})


@visit_bp.route('/api/photo/upload', methods=['POST'])
@login_required
def api_photo_upload():
    """走访照片上传（多图，存 uploads/visit/；落库值为相对路径 visit/<name>）。"""
    import os as _os
    import uuid as _uuid
    from config import Config
    from werkzeug.utils import secure_filename as _secure

    files = request.files.getlist('photos')
    if not files:
        return json_response(False, '请选择照片文件')
    save_dir = _os.path.join(Config.UPLOAD_FOLDER, 'visit')
    _os.makedirs(save_dir, exist_ok=True)

    saved = []
    for f in files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext not in visit_service.PHOTO_ALLOWED:
            return json_response(False, '仅支持 jpg/png/gif/webp 图片格式')
        if f.content_length and f.content_length > visit_service.PHOTO_MAX_BYTES:
            return json_response(False, '单张照片不能超过 5MB')
        name = f'{_uuid.uuid4().hex}.{ext}'
        f.save(_os.path.join(save_dir, name))
        saved.append({
            'filename': f'visit/{name}',
            'original_name': _secure(f.filename) or name,
            'url': f'/uploads/visit/{name}',
            'uploaded_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })
    if not saved:
        return json_response(False, '未接收到有效照片文件')
    return jsonify({'success': True, 'data': {'files': saved}})


@visit_bp.route('/api/records')
@login_required
def api_records():
    """按计划查已补录集合与完成度（补录页回显：completed_count/target_count/rate）。"""
    plan_id = request.args.get('plan_id', type=int)
    if not plan_id:
        return json_response(False, '缺少 plan_id')
    plan = VisitPlan.query.get(plan_id)
    if not plan:
        return json_response(False, '计划不存在')
    completed = visit_service.completed_count(plan_id)
    target = plan.target_count or 0
    rate = round(completed / target * 100, 1) if target else 0
    recorded_ids = [rid for (rid,) in db.session.query(VisitRecord.resident_id)
                    .filter(VisitRecord.plan_id == plan_id).distinct().all()]
    return jsonify({'success': True, 'data': {
        'plan_id': plan_id, 'completed_count': completed,
        'target_count': target, 'rate': rate, 'recorded': recorded_ids,
    }})


# ============ T18-3 历史追溯 ============

@visit_bp.route('/history')
@login_required
def history():
    """走访记录历史追溯页（PRD R7-E4：按居民/计划/时间/走访人筛选，含照片缩略预览）。"""
    from models import User
    users = User.query.filter(User.is_active.is_(True)).order_by(User.id).all()
    visitors = [{'id': u.id, 'name': u.real_name or u.username} for u in users]
    return render_template(
        'visit/history.html',
        visit_types=visit_service.VISIT_TYPES,
        visitors=visitors,
    )


@visit_bp.route('/api/history')
@login_required
@child_rows_guard
def api_history():
    """走访记录追溯 API：四维筛选（计划/居民/时间/走访人）+ 方式 + keyword，分页 + 覆盖率 stats。

    儿童行级可见：留守/困境儿童记录按 can_see_children 过滤当前页（total 保持未过滤）。
    """
    from models import Resident, User

    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    query = VisitRecord.query

    plan_id = request.args.get('plan_id', type=int)
    resident_id = request.args.get('resident_id', type=int)
    visitor_id = request.args.get('visitor_id', type=int)
    visit_type = request.args.get('visit_type', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    keyword = request.args.get('keyword', '').strip()

    if plan_id:
        query = query.filter(VisitRecord.plan_id == plan_id)
    if resident_id:
        query = query.filter(VisitRecord.resident_id == resident_id)
    if visitor_id:
        query = query.filter(VisitRecord.visitor_id == visitor_id)
    if visit_type:
        query = query.filter(VisitRecord.visit_type == visit_type)
    if start_date:
        query = query.filter(VisitRecord.visit_date >= start_date)
    if end_date:
        query = query.filter(VisitRecord.visit_date <= end_date)
    if keyword:
        query = query.join(Resident, Resident.id == VisitRecord.resident_id).filter(
            db.or_(Resident.name.like(f'%{keyword}%'),
                   Resident.village_group.like(f'%{keyword}%'),
                   Resident.grid.like(f'%{keyword}%'))
        )

    query = query.order_by(VisitRecord.visit_date.desc(), VisitRecord.id.desc())
    items, total, pages = paginate_query(query, page=page, per_page=limit)

    # 防 N+1：页内批量取 plan/resident/visitor 名
    plan_ids = {r.plan_id for r in items if r.plan_id}
    res_ids = {r.resident_id for r in items}
    vis_ids = {r.visitor_id for r in items if r.visitor_id}
    plan_map = {p.id: p.plan_name for p in VisitPlan.query.filter(VisitPlan.id.in_(plan_ids)).all()} if plan_ids else {}
    res_map = {r.id: r for r in Resident.query.filter(Resident.id.in_(res_ids)).all()} if res_ids else {}
    user_map = {u.id: (u.real_name or u.username)
                for u in User.query.filter(User.id.in_(vis_ids)).all()} if vis_ids else {}
    child_map = visit_service.child_type_map(res_ids)

    rows = []
    for r in items:
        res = res_map.get(r.resident_id)
        ct = child_map.get(r.resident_id, '')
        rows.append({
            'id': r.id,
            'plan_id': r.plan_id,
            'plan_name': plan_map.get(r.plan_id, ''),
            'resident_id': r.resident_id,
            'resident_name': res.name if res else '',
            'village_group': res.village_group if res else '',
            'visit_date': r.visit_date.isoformat() if r.visit_date else '',
            'visit_type': r.visit_type,
            'visitor_id': r.visitor_id,
            'visitor_name': user_map.get(r.visitor_id, ''),
            'content': r.content or '',
            'has_need': r.has_need or 0,
            'need_desc': r.need_desc or '',
            'photos': visit_service.photos_to_urls(r.photos),
            'target_type': '儿童' if ct else '',
            'child_type': ct,
        })
    rows = filter_child_rows(rows, g.get('can_see_children', False))

    stats = visit_service.coverage_summary(plan_id)
    return jsonify({'success': True, 'data': rows, 'total': total,
                    'page': page, 'pages': pages, 'stats': stats})
