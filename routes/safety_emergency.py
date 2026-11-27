# -*- coding: utf-8 -*-
"""routes/safety_emergency.py — 应急管理 + 综合安全检查结构化闭环（087）

固定蓝图门面扩展：路由挂在与档案共用的 safety_bp 上（「并入 safety」菜单树 v4 口径），
鉴权零新增登记——ADR-016 BLUEPRINT_MODULES['safety']='safety' 已覆盖：
GET→safety.view / POST→safety.edit 自动映射，漏归/无权一律 403（默认拒绝）。

三边界（动态台账设计 §十.6，不混）：
  应急工作方案 → safety 档案树（m028 守卫种子「应急工作方案」根目录，页内直达）；
  工作队伍 / 储备物资 → 软表名册（095 接入：078 dynamic_table 只读查询，
  module_key='safety' + group_name='应急管理'，点入 /dynatable/t/<code>，无表空态不伪造）；
  综合安全检查 → 本模块固定结构化闭环（检查单 1:N 隐患行，B-10）。

整改闭环状态机（唯一真源 models/safety_check.HAZARD_STATUSES）：
  待整改 → 待复核（上报整改情况）→ 已闭环（复核通过）；
  待复核 --驳回--> 待整改（留 review_note）。检查单状态为派生值，不落库。
"""
from datetime import datetime

from flask import render_template, request
from sqlalchemy import case, func, text

from models import db
from models.dynamic_table import DynamicRecord, DynamicTable
from models.safety import SafetyNode
from models.safety_check import SafetyCheck, SafetyCheckHazard
from routes.safety import safety_bp, ROOT_NAME
from utils.decorators import login_required
from utils.helpers import get_dict_items, json_response, log_operation, paginate_query

_M028_PATH_SEGMENT = ('scripts', 'migrations', 'm028_emergency_safety_check.sql')


def _apply_m028():
    """幂等执行 m028（建表 IF NOT EXISTS + 枚举/档案种子守卫，可重跑）。"""
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        *_M028_PATH_SEGMENT)
    with open(path, encoding='utf-8') as f:
        sql = f.read()
    raw = db.engine.raw_connection()
    try:
        raw.executescript(sql)
        raw.commit()
    finally:
        raw.close()


def _ensure_emergency_seed():
    """应用层幂等种子（087）：枚举分类或档案目录缺失时补跑 m028（幂等可重跑）。

    放在蓝图级：无权限请求被 app 级 enforce_role_permissions 提前拦截，不会触发。
    R-087-08 收口：三条件并成一条标量子查询（原每请求 3 次），全就位零开销跳过。
    """
    row = db.session.execute(text(
        "SELECT (SELECT COUNT(*) FROM dict_categories WHERE name IN "
        "('综合安全检查类型', '安全隐患等级')) AS cats, "
        "(SELECT COUNT(*) FROM safety_nodes WHERE parent_id IS NULL "
        "AND name = '应急工作方案') AS folders"
    )).one()
    if row.cats == 2 and row.folders == 1:
        return
    _apply_m028()


# ── 页面（GET → safety.view） ─────────────────────────────────────────────

# 095 名册归属：078 软表中 module_key='safety' 且 group_name='应急管理' 的台账；
# 两卡按表名关键词分流（含「队伍」→ 工作队伍卡 / 含「物资」→ 储备物资卡），
# 其余组内表仍在 /dynatable/ 总览可见。只读复用 078（不改引擎/端点，零 DDL）。
ROSTER_MODULE_KEY = 'safety'
ROSTER_GROUP_NAME = '应急管理'


def _collect_subtree_table_nodes(root_folder_id):
    """收集应急工作方案目录子树下的全部 table 节点。"""
    if not root_folder_id:
        return []
    found = []
    queue = [root_folder_id]
    seen = set()
    while queue:
        pid = queue.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        for ch in (SafetyNode.query.filter_by(parent_id=pid)
                   .order_by(SafetyNode.sort_order.asc(), SafetyNode.id.asc()).all()):
            if ch.node_type == 'folder':
                queue.append(ch.id)
            elif ch.node_type == 'table' and ch.table_code:
                found.append(ch)
    return found


def _emergency_roster(folder_id=None):
    """应急名册：优先从「应急工作方案」目录树取挂载的台账（走 /safety/roster/<code>/），
    并向下兼容历史仅按 group_name='应急管理' 建表的遗留软表（按 code 去重）。"""
    tree_nodes = _collect_subtree_table_nodes(folder_id)
    tree_by_code = {n.table_code: n for n in tree_nodes}
    legacy_tables = (DynamicTable.query
                     .filter_by(module_key=ROSTER_MODULE_KEY, group_name=ROSTER_GROUP_NAME)
                     .order_by(DynamicTable.id.asc()).all())
    ordered_tables = []
    seen_codes = set()
    if tree_by_code:
        mounted = {t.code: t for t in DynamicTable.query.filter(
            DynamicTable.code.in_(list(tree_by_code.keys())), DynamicTable.module_key == ROSTER_MODULE_KEY
        ).all()}
        for n in tree_nodes:
            t = mounted.get(n.table_code)
            if t and t.code not in seen_codes:
                seen_codes.add(t.code)
                ordered_tables.append((t, n))
    for t in legacy_tables:
        if t.code not in seen_codes:
            seen_codes.add(t.code)
            ordered_tables.append((t, None))
    counts = {}
    if ordered_tables:
        rows = (db.session.query(DynamicRecord.table_id, func.count(DynamicRecord.id))
                .filter(DynamicRecord.table_id.in_([t.id for t, _ in ordered_tables]))
                .group_by(DynamicRecord.table_id).all())
        counts = dict(rows)
    roster = []
    for t, node in ordered_tables:
        from_tree = node is not None
        url = (f'/safety/roster/{t.code}/?from_node={node.parent_id or folder_id}'
               if from_tree else f'/dynatable/t/{t.code}')
        roster.append({
            'code': t.code,
            'name': node.name if from_tree else t.name,
            'records': counts.get(t.id, 0),
            'updated': t.updated_at.strftime('%Y-%m-%d %H:%M') if t.updated_at else '',
            'from_tree': from_tree,
            'url': url,
        })
    team = [t for t in roster if '队伍' in t['name']]
    supply = [t for t in roster if '物资' in t['name']]
    other = [t for t in roster if t['from_tree'] and '队伍' not in t['name'] and '物资' not in t['name']]
    return {'team': team, 'supply': supply, 'other': other}


@safety_bp.route('/emergency')
@login_required
def emergency():
    """应急管理页（表 10 门面）：方案→档案 / 目录挂载台账+历史名册 + 检查闭环概览。"""
    folder = SafetyNode.query.filter_by(parent_id=None, name='应急工作方案').first()
    folder_id = folder.id if folder else None
    roster = _emergency_roster(folder_id=folder_id)
    return render_template(
        'safety/emergency.html',
        archive_folder_id=folder_id,
        roster_team=roster['team'],
        roster_supply=roster['supply'],
        roster_other=roster['other'],
    )


@safety_bp.route('/checks')
@login_required
def checks_page():
    """综合安全检查列表页（B-10）：列表 + 录入 + 闭环操作台。"""
    return render_template(
        'safety/checks.html',
        check_types=get_dict_items('综合安全检查类型'),
        hazard_levels=get_dict_items('安全隐患等级'),
    )


# ── 检查单 API ────────────────────────────────────────────────────────────

def _check_dto(c, hazard_total=None, hazard_open=None):
    """检查单 DTO。hazard_total/hazard_open 可传预聚合值（R-087-10 列表单查询），
    不传时逐项派生（详情/保存单记录场景）。

    status 为数据口径（087 验收语义：无隐患或全闭环 → 已闭环）；
    status_display 为展示口径（R-087-07：hazard_total==0 单列「无隐患」，不冒充闭环）。
    """
    total = c.hazard_total if hazard_total is None else hazard_total
    open_n = c.hazard_open if hazard_open is None else hazard_open
    status = '已闭环' if open_n == 0 else '整改中'
    return {
        'id': c.id,
        'title': c.title,
        'check_date': c.check_date.strftime('%Y-%m-%d') if c.check_date else '',
        'check_type': c.check_type or '',
        'location': c.location or '',
        'checker': c.checker or '',
        'remark': c.remark or '',
        'hazard_total': total,
        'hazard_open': open_n,
        'status': status,                                          # 派生：已闭环 | 整改中
        'status_display': '无隐患' if total == 0 else status,       # 展示：空隐患单不冒充闭环
    }


def _hazard_counts(check_ids):
    """隐患计数单查询聚合：{check_id: (total, open)}（R-087-10，替代每行 2 次 count）。"""
    if not check_ids:
        return {}
    rows = (db.session.query(
                SafetyCheckHazard.check_id,
                func.count(SafetyCheckHazard.id),
                func.coalesce(func.sum(case(
                    (SafetyCheckHazard.status != '已闭环', 1), else_=0)), 0))
            .filter(SafetyCheckHazard.check_id.in_(check_ids))
            .group_by(SafetyCheckHazard.check_id).all())
    return {cid: (n, int(open_n)) for cid, n, open_n in rows}


def _hazard_dto(h):
    return {
        'id': h.id,
        'description': h.description,
        'level': h.level or '一般',
        'location': h.location or '',
        'rectify_owner': h.rectify_owner or '',
        'rectify_deadline': h.rectify_deadline.strftime('%Y-%m-%d') if h.rectify_deadline else '',
        'status': h.status,
        'rectify_note': h.rectify_note or '',
        'rectified_at': h.rectified_at.strftime('%Y-%m-%d %H:%M') if h.rectified_at else '',
        'review_note': h.review_note or '',
        'reviewed_at': h.reviewed_at.strftime('%Y-%m-%d %H:%M') if h.reviewed_at else '',
    }


@safety_bp.route('/api/checks')
@login_required
def api_checks():
    """检查单分页列表（title 模糊筛 + 派生状态；隐患计数单查询聚合，R-087-10）。"""
    q = SafetyCheck.query
    title = (request.args.get('title') or '').strip()
    if title:
        q = q.filter(SafetyCheck.title.contains(title))
    q = q.order_by(SafetyCheck.check_date.desc(), SafetyCheck.id.desc())
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    items, total, _pages = paginate_query(q, page, per_page)
    counts = _hazard_counts([c.id for c in items])
    data = []
    for c in items:
        hazard_total, hazard_open = counts.get(c.id, (0, 0))
        data.append(_check_dto(c, hazard_total=hazard_total, hazard_open=hazard_open))
    return json_response(True, data=data, total=total)


@safety_bp.route('/api/checks', methods=['POST'])
@login_required
def api_check_save():
    """新增/编辑检查单基本信息（隐患行走独立端点，录入后按需追加）。"""
    payload = request.get_json(silent=True) or request.form
    check_id = int(payload.get('id') or 0)
    title = (payload.get('title') or '').strip()
    check_date = (payload.get('check_date') or '').strip()
    if not title:
        return json_response(False, '检查名称不能为空')
    if not check_date:
        return json_response(False, '检查日期不能为空')
    try:
        check_dt = datetime.strptime(check_date, '%Y-%m-%d').date()
    except ValueError:
        return json_response(False, '检查日期格式应为 YYYY-MM-DD')
    check_type = (payload.get('check_type') or '').strip() or ''
    # R-087-06 顺手收口（返工单 §六）：与隐患等级同口径，枚举外取值拒绝
    if check_type and check_type not in {it.value for it in get_dict_items('综合安全检查类型')}:
        return json_response(False, '检查类型不在枚举内')

    check = SafetyCheck.query.get(check_id) if check_id else None
    if check_id and not check:
        return json_response(False, '检查单不存在')
    is_new = check is None
    if is_new:
        check = SafetyCheck()
        check.created_by = session_username()
        db.session.add(check)
    check.title = title
    check.check_date = check_dt
    check.check_type = check_type
    check.location = (payload.get('location') or '').strip() or ''
    check.checker = (payload.get('checker') or '').strip() or ''
    check.remark = (payload.get('remark') or '').strip() or ''
    db.session.commit()
    log_operation(session_username(), '安全工作', 'add' if is_new else 'edit',
                  f'综合安全检查单: {check.title}')
    return json_response(True, '保存成功', data=_check_dto(check))


def session_username():
    from flask import session
    return session.get('username', '')


@safety_bp.route('/api/checks/<int:check_id>', methods=['DELETE'])
@login_required
def api_check_delete(check_id):
    """删除检查单（级联删隐患行；写 OperationLog）。"""
    check = SafetyCheck.query.get(check_id)
    if not check:
        return json_response(False, '检查单不存在')
    title = check.title
    db.session.delete(check)
    db.session.commit()
    log_operation(session_username(), '安全工作', 'delete', f'删除综合安全检查单: {title}')
    return json_response(True, '删除成功')


@safety_bp.route('/api/checks/<int:check_id>')
@login_required
def api_check_detail(check_id):
    """检查单详情：基本信息 + 隐患行（闭环操作台数据源）。"""
    check = SafetyCheck.query.get(check_id)
    if not check:
        return json_response(False, '检查单不存在')
    data = _check_dto(check)
    data['hazards'] = [_hazard_dto(h) for h in check.hazards]
    return json_response(True, data=data)


# ── 隐患行 + 闭环状态机 API ───────────────────────────────────────────────

@safety_bp.route('/api/checks/<int:check_id>/hazards', methods=['POST'])
@login_required
def api_hazard_add(check_id):
    """新增隐患行（初始态 = 待整改）。"""
    check = SafetyCheck.query.get(check_id)
    if not check:
        return json_response(False, '检查单不存在')
    payload = request.get_json(silent=True) or request.form
    description = (payload.get('description') or '').strip()
    if not description:
        return json_response(False, '隐患描述不能为空')
    level = (payload.get('level') or '').strip()
    if level and level not in {it.value for it in get_dict_items('安全隐患等级')}:
        return json_response(False, '隐患等级不在枚举内')
    deadline = (payload.get('rectify_deadline') or '').strip()
    if deadline:
        try:
            deadline = datetime.strptime(deadline, '%Y-%m-%d').date()
        except ValueError:
            return json_response(False, '整改期限格式应为 YYYY-MM-DD')
    else:
        deadline = None
    hazard = SafetyCheckHazard(
        check_id=check.id,
        description=description,
        level=level or '一般',
        location=(payload.get('location') or '').strip() or '',
        rectify_owner=(payload.get('rectify_owner') or '').strip() or '',
        rectify_deadline=deadline,
        status='待整改',
    )
    db.session.add(hazard)
    db.session.commit()
    log_operation(session_username(), '安全工作', 'add',
                  f'隐患登记: {check.title} / {description[:30]}')
    return json_response(True, '隐患已登记', data=_hazard_dto(hazard))


def _get_hazard_or_none(hazard_id):
    return SafetyCheckHazard.query.get(hazard_id)


@safety_bp.route('/api/hazards/<int:hazard_id>/rectify', methods=['POST'])
@login_required
def api_hazard_rectify(hazard_id):
    """上报整改：待整改 → 待复核（整改情况必填）。"""
    hazard = _get_hazard_or_none(hazard_id)
    if not hazard:
        return json_response(False, '隐患记录不存在')
    if hazard.status != '待整改':
        return json_response(False, f'当前状态「{hazard.status}」不可上报整改')
    payload = request.get_json(silent=True) or request.form
    note = (payload.get('rectify_note') or '').strip()
    if not note:
        return json_response(False, '整改情况不能为空')
    hazard.status = '待复核'
    hazard.rectify_note = note
    hazard.rectified_at = datetime.now()
    db.session.commit()
    log_operation(session_username(), '安全工作', 'edit', f'整改上报: 隐患#{hazard.id}')
    return json_response(True, '整改情况已上报，待复核', data=_hazard_dto(hazard))


@safety_bp.route('/api/hazards/<int:hazard_id>/review', methods=['POST'])
@login_required
def api_hazard_review(hazard_id):
    """复核：通过 → 已闭环；驳回 → 回到待整改（复核意见必填）。"""
    hazard = _get_hazard_or_none(hazard_id)
    if not hazard:
        return json_response(False, '隐患记录不存在')
    if hazard.status != '待复核':
        return json_response(False, f'当前状态「{hazard.status}」不可复核')
    payload = request.get_json(silent=True) or request.form
    action = (payload.get('action') or '').strip()
    note = (payload.get('review_note') or '').strip()
    if action not in ('pass', 'reject'):
        return json_response(False, '复核动作应为 pass 或 reject')
    if not note:
        return json_response(False, '复核意见不能为空')
    if action == 'pass':
        hazard.status = '已闭环'
    else:
        hazard.status = '待整改'      # 驳回：整改人重报，闭环被拒记录留痕
    hazard.review_note = note
    hazard.reviewed_at = datetime.now()
    db.session.commit()
    log_operation(session_username(), '安全工作', 'edit',
                  f'隐患复核{"通过" if action == "pass" else "驳回"}: 隐患#{hazard.id}')
    return json_response(True, '复核完成' if action == 'pass' else '已驳回，退回整改',
                         data=_hazard_dto(hazard))


@safety_bp.route('/api/emergency/summary')
@login_required
def api_emergency_summary():
    """应急管理页概览：检查/隐患计数 + 档案目录节点（方案入口锚点）。"""
    total_checks = SafetyCheck.query.count()
    open_hazards = SafetyCheckHazard.query.filter(SafetyCheckHazard.status != '已闭环').count()
    closed_hazards = SafetyCheckHazard.query.filter_by(status='已闭环').count()
    folder = SafetyNode.query.filter_by(parent_id=None, name='应急工作方案').first()
    return json_response(True, data={
        'total_checks': total_checks,
        'open_hazards': open_hazards,
        'closed_hazards': closed_hazards,
        'archive_folder_id': folder.id if folder else None,
        'root_name': ROOT_NAME,
    })
