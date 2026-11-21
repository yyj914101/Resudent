from flask import Blueprint, render_template, request, session, jsonify, send_file, send_from_directory, g
from models import (db, Resident, User, ResidentTag, Dibao, Disabled,
                    CareTarget, SpecialSupport, ImportBatch, ImportRowError)
from utils.decorators import login_required, admin_required, permission_required
from utils.helpers import paginate_query, json_response, export_excel, import_excel_template_year, log_operation, get_dict_items
from utils.crypto import key_holder, sha256_hash, sha256_prefix_hash
from utils.masking import child_rows_guard, mask_export_rows
from utils.permissions import user_has_special
from utils.sensitive_fields import make_sensitive_helpers
from utils.resident_sync import ResidentSyncService
from utils.death_soft_invalidate import soft_invalidate_vulnerable
from utils.resident_flow import (is_outflow, flow_label, is_inflow,
                                 FLOW_IN, FLOW_OUT, FLOW_RESIDENT,
                                 filter_by_flow_status, exclude_outflow)
from datetime import datetime, date
from config import Config
from werkzeug.utils import secure_filename
from sqlalchemy import or_
import io
import json
import logging
import os
import re
import uuid
import zipfile

resident_bp = Blueprint('resident', __name__, template_folder='../templates')

# 旧状态字面常量（Excel/请求入参兼容；A4 转写为 flow_status，非流动性判断）
_LEGACY_MOVED_OUT = 'moved_out'

# T11-1（R12 读密文闭环）：敏感字段读写统一入口（复用 utils.sensitive_fields 通用管线）
_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', 'id_card_prefix_hash'),
    'phone': ('phone_enc', 'phone_hash', 'phone_prefix_hash'),
    'bank_card': ('bank_card_enc', 'bank_card_hash', None),
}

# 复用重构：按模型绑定生成 (decrypt, set_encrypted, clause)，Resident/Dibao 共用底层实现
_decrypt_field, _set_encrypted_field, _sensitive_clause = make_sensitive_helpers(
    Resident, _SENS_FIELDS, key_holder)


def _apply_post_filters(query, page, limit, post_filters):
    """有敏感二次过滤时 Python 层分页；否则走 SQL 分页。"""
    if not post_filters:
        return paginate_query(query, page, limit)
    all_rows = query.all()
    for pf in post_filters:
        all_rows = pf(all_rows)
    total = len(all_rows)
    start = (page - 1) * limit
    page_items = all_rows[start:start + limit]
    pages = max(1, (total + limit - 1) // limit) if total else 0
    return page_items, total, pages


@resident_bp.route('/<int:resident_id>/sensitive')
@permission_required('special.view_sensitive')
def resident_sensitive(resident_id):
    """T06 逐条明文查看：仅 view_sensitive 位；豁免 JSON 层脱敏；记日志只记字段名。"""
    field = request.args.get('field', 'id_card')
    if field not in ('id_card', 'phone', 'bank_card'):
        return json_response(False, '不支持的字段')
    r = Resident.query.get(resident_id)
    if not r:
        return json_response(False, '居民不存在')
    value = _decrypt_field(r, field)
    g.skip_masking = True
    user = User.query.get(session.get('user_id'))
    log_operation(user.username if user else 'system', 'resident', 'view_sensitive',
                  f'明文查看居民#{resident_id} 字段:{field}', result='success')
    return json_response(True, data={field: value})


def _resident_filtered_query(args, include_household_code=False, sensitive_post_filters=None):
    """Build resident filters shared by list, household summary, and export.

    sensitive_post_filters: 可选 list 容器，用于收集敏感字段（id_card/phone）的
    应用层二次过滤回调（hash 候选桶 + 解密收敛），由调用方在取结果后执行。
    """
    name = args.get('name', '').strip()
    id_card = args.get('id_card', '').strip()
    age_min = args.get('age_min', type=int)
    age_max = args.get('age_max', type=int)
    village_group = args.get('village_group', '').strip()
    grid = args.get('grid', '').strip()
    politics_status = args.get('politics_status', '').strip()
    status = args.get('status', '').strip()
    phone = args.get('phone', '').strip()
    gender = args.get('gender', '').strip()
    household_property = args.get('household_property', '').strip()
    household_code = args.get('household_code', '').strip() if include_household_code else ''
    address = args.get('address', '').strip()
    flow_status = args.get('flow_status', '').strip()
    tag_code = args.get('tag_code', '').strip()

    query = Resident.query
    if name:
        query = query.filter(Resident.name.like(f'%{name}%'))
    if id_card:
        query, pf = _sensitive_clause(query, 'id_card', id_card)
        if pf and sensitive_post_filters is not None:
            sensitive_post_filters.append(pf)
    if phone:
        query, pf = _sensitive_clause(query, 'phone', phone)
        if pf and sensitive_post_filters is not None:
            sensitive_post_filters.append(pf)
    if gender:
        query = query.filter(Resident.gender == gender)
    if household_property:
        query = query.filter(Resident.household_property.like(f'%{household_property}%'))
    if address:
        query = query.filter(Resident.address.like(f'%{address}%'))
    if household_code:
        query = query.filter(Resident.household_code == household_code)
    if village_group:
        query = query.filter(Resident.village_group == village_group)
    if grid:
        query = query.filter(Resident.grid == grid)
    if politics_status:
        query = query.filter(Resident.politics_status == politics_status)
    if status:
        query = query.filter(Resident.status == status)
    if flow_status:
        query = filter_by_flow_status(query, flow_status)
    if tag_code:
        query = query.join(ResidentTag, ResidentTag.resident_id == Resident.id).filter(
            ResidentTag.tag_code == tag_code,
            ResidentTag.status == '有效',
        )

    now = datetime.now()
    if age_min is not None:
        max_birth = date(now.year - age_min, now.month, now.day)
        query = query.filter(Resident.birth_date <= max_birth)
    if age_max is not None:
        min_birth = date(now.year - age_max - 1, now.month, now.day)
        query = query.filter(Resident.birth_date >= min_birth)

    return query.order_by(Resident.household_code, Resident.relationship)


# ─── Page Routes ─────────────────────────────────────────────────────

@resident_bp.route('/')
@login_required
def list():
    """Render resident list page.

    062 P4 验收 6：顶栏搜索 ?name= 由服务端注入首查（spa.js 伪导航丢 query，
    前端不得依赖 location.search，同 resident/add 注释口径）。
    """
    initial_name = (request.args.get('name') or '').strip()
    return render_template('resident/list.html', initial_name=initial_name)


@resident_bp.route('/add')
@login_required
def add():
    """Render add resident page（034 P2：三模式工作台初始 tab 由服务端渲染——
    spa.js 伪导航丢 query，前端不得依赖 location.search）。"""
    tab = request.args.get('tab') or 'manual'
    if tab not in ('manual', 'template', 'custom'):
        tab = 'manual'
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    can_custom = bool(user and (user.role_id == 1 or user_has_special(user, 'import_batch')))
    if tab == 'custom' and not can_custom:
        tab = 'manual'  # 无权限深链兜底：不渲染空 pane（034 验收 9）
    from utils.import_engine import CATALOG_VERSION
    return render_template('resident/add.html', initial_tab=tab,
                           can_custom=can_custom,
                           catalog_version=CATALOG_VERSION)


# ─── AJAX: Paginated List ────────────────────────────────────────────

@resident_bp.route('/api/list')
@login_required
def api_list():
    """AJAX paginated list with search filters."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    post_filters = []
    query = _resident_filtered_query(request.args, include_household_code=True,
                                     sensitive_post_filters=post_filters)

    items, total, pages = _apply_post_filters(query, page, limit, post_filters)

    # P2-5 整改：批量预取标签（消除每行 N+1 查询）
    tag_map = {}
    resident_ids = [r.id for r in items]
    if resident_ids:
        for t in ResidentTag.query.filter(
                ResidentTag.resident_id.in_(resident_ids),
                ResidentTag.status == '有效').all():
            tag_map.setdefault(t.resident_id, []).append(t.tag_code)

    data = []
    now = datetime.now()
    for r in items:
        age = ''
        if r.birth_date:
            age = now.year - r.birth_date.year - \
                  ((now.month, now.day) < (r.birth_date.month, r.birth_date.day))
        data.append({
            'id': r.id,
            'name': r.name or '',
            'id_card': _decrypt_field(r, 'id_card'),
            'nation': r.nation or '',
            'gender': r.gender or '',
            'age': age,
            'village_group': r.village_group or '',
            'grid': r.grid or '',
            'politics_status': r.politics_status or '',
            'status': r.status or 'normal',
            'flow_status': r.flow_status or FLOW_RESIDENT,
            'source': r.source or '手工',
            'household_code': r.household_code or '',
            'phone': _decrypt_field(r, 'phone'),
            'relationship': r.relationship or '',
            'tags': tag_map.get(r.id, []),
        })

    return json_response(
        success=True,
        data={
            'data': data,
            'total': total,
            'pages': pages,
            'page': page,
            'limit': limit,
        }
    )


# ─── AJAX: Household Summary (按户汇总) ──────────────────────────────

@resident_bp.route('/api/household-summary')
@login_required
def api_household_summary():
    """AJAX paginated household summary list (grouped by household_code)."""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    post_filters = []
    base = _resident_filtered_query(request.args, sensitive_post_filters=post_filters)
    now = datetime.now()
    all_members = base.all()
    for pf in post_filters:
        all_members = pf(all_members)
    total_persons = len(all_members)

    # Group by household_code
    households = {}
    for r in all_members:
        hc = r.household_code or f'_nogroup_{r.id}'
        if hc not in households:
            households[hc] = {'members': [], 'household_code': r.household_code or ''}
        households[hc]['members'].append(r)

    hh_list = []
    for hc, group in households.items():
        members = group['members']
        head = next((m for m in members if m.relationship in ('户主本人', '户主')), members[0])
        has_deceased = any(m.status == 'deceased' for m in members)
        has_moved = any(is_outflow(m) for m in members)
        head_age = ''
        if head.birth_date:
            head_age = now.year - head.birth_date.year - ((now.month, now.day) < (head.birth_date.month, head.birth_date.day))
        hh_list.append({
            'household_code': head.household_code or '',
            'household_name': head.household_name or head.name or '',
            'head_name': head.name or '',
            'head_gender': head.gender or '',
            'head_age': head_age,
            'head_id_card': _decrypt_field(head, 'id_card'),
            'head_phone': _decrypt_field(head, 'phone'),
            'village_group': head.village_group or '',
            'grid': head.grid or '',
            'address': head.address or '',
            'household_property': head.household_property or '',
            'member_count': len(members),
            'head_id': head.id,
            'has_deceased': has_deceased,
            'has_moved': has_moved,
        })

    total_households = len(hh_list)
    total_pages = max(1, (total_households + limit - 1) // limit)
    start = (page - 1) * limit
    page_items = hh_list[start:start + limit]

    return json_response(success=True, data={
        'total_households': total_households,
        'total_persons': total_persons,
        'page': page,
        'pages': total_pages,
        'limit': limit,
        'data': page_items,
    })


# ─── AJAX: Resident Detail (with household members) ──────────────────

@resident_bp.route('/api/detail/<int:resident_id>')
@login_required
def api_detail(resident_id):
    """GET resident detail JSON including household members."""
    r = Resident.query.get(resident_id)
    if not r:
        return json_response(False, '居民不存在')

    now = datetime.now()
    age = ''
    if r.birth_date:
        age = now.year - r.birth_date.year - \
              ((now.month, now.day) < (r.birth_date.month, r.birth_date.day))

    detail = {
        'id': r.id,
        'name': r.name or '',
        'id_card': _decrypt_field(r, 'id_card'),
        'gender': r.gender or '',
        'birth_date': r.birth_date.strftime('%Y-%m-%d') if r.birth_date else '',
        'age': age,
        'nation': r.nation or '',
        'phone': _decrypt_field(r, 'phone'),
        'village_group': r.village_group or '',
            'grid': r.grid or '',
        'address': r.address or '',
        'relationship': r.relationship or '',
        'household_type': r.household_type or '',
        'politics_status': r.politics_status or '',
        'education': r.education or '',
        'occupation': r.occupation or '',
        'health_status': r.health_status or '',
        'household_property': r.household_property or '',
        'household_name': r.household_name or '',
        'household_code': r.household_code or '',
        'bank_name': r.bank_name or '',
        'bank_card': _decrypt_field(r, 'bank_card'),
        'marital_status': r.marital_status or '',
        'military_status': r.military_status or '',
        'remark': r.remark or '',
        'status': r.status or 'normal',
        'flow_status': r.flow_status or FLOW_RESIDENT,
        'source': r.source or '手工',
        'status_date': r.status_date.strftime('%Y-%m-%d') if r.status_date else '',
        'status_reason': r.status_reason or '',
        'tags': [{
            'tag_code': t.tag_code,
            'source': t.source or '',
            'create_time': t.create_time.strftime('%Y-%m-%d') if t.create_time else '',
        } for t in ResidentTag.query.filter_by(resident_id=r.id, status='有效').all()],
    }

    # Household members (same household_code)
    members = []
    if r.household_code:
        hh_members = Resident.query.filter(
            Resident.household_code == r.household_code
        ).order_by(Resident.relationship).all()
        for m in hh_members:
            m_age = ''
            if m.birth_date:
                m_age = now.year - m.birth_date.year - \
                        ((now.month, now.day) < (m.birth_date.month, m.birth_date.day))
            members.append({
                'id': m.id,
                'name': m.name or '',
                'id_card': _decrypt_field(m, 'id_card'),
                'gender': m.gender or '',
                'age': m_age,
                'relationship': m.relationship or '',
                'village_group': m.village_group or '',
                'phone': _decrypt_field(m, 'phone'),
                'nation': m.nation or '',
                'household_name': m.household_name or '',
                'household_code': m.household_code or '',
                'address': m.address or '',
                'household_property': m.household_property or '',
                'politics_status': m.politics_status or '',
                'education': m.education or '',
                'occupation': m.occupation or '',
                'marital_status': m.marital_status or '',
                'military_status': m.military_status or '',
                'health_status': m.health_status or '',
                'bank_name': m.bank_name or '',
                'bank_card': m.bank_card or '',
                'grid': m.grid or '',
                'remark': m.remark or '',
                'status': m.status or 'normal',
            })

    return json_response(success=True, data={
        'detail': detail,
        'members': members,
    })


# ─── AJAX: Save / Update Resident ────────────────────────────────────

@resident_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    """Save or update resident info."""
    form = request.form if request.form else (request.json or {})
    rid = form.get('id', type=int)

    # Parse dates
    def parse_date(val):
        if not val:
            return None
        try:
            return datetime.strptime(val.strip(), '%Y-%m-%d').date()
        except ValueError:
            return None

    birth_date = parse_date(form.get('birth_date'))
    status_date = parse_date(form.get('status_date'))

    if rid:
        r = Resident.query.get(rid)
        if not r:
            return json_response(False, '居民不存在')
    else:
        r = Resident()
        db.session.add(r)

    # 022：同步变更捕获。编辑时先取白名单旧值快照，并固守 id_card 不可变前提。
    changes = {}
    if rid:
        # 拍板 #4/#6：主档 id_card 唯一且设计上不可修改，编辑时变更即拒绝。
        # 审阅修复 P3-6：按 id_card_hash 等值对比（sha256 不依赖加密密钥，
        # 密文不可读/解密失败时同样生效，fail-closed 防止覆盖后松散耦合表失联）；
        # hash 为空（未建档证件号）时允许补录。
        new_id = (form.get('id_card') or '').strip().upper()
        if new_id and r.id_card_hash:
            if sha256_hash(new_id) != r.id_card_hash:
                return json_response(False, '居民身份证号不可修改')
        old_snap = ResidentSyncService.snapshot_plain(r, _decrypt_field)

    # Only update fields that are present in the form data
    # This prevents wiping fields that weren't edited
    def _set_field(obj, field_name, form_key=None):
        fk = form_key or field_name
        if fk in form:
            val = form.get(fk, '').strip()
            setattr(obj, field_name, val)

    _set_field(r, 'name')
    _set_encrypted_field(r, 'id_card', form.get('id_card', ''))
    _set_field(r, 'gender')
    # Auto-fill birth date from ID card if not provided（用表单原值，r.id_card 已置 NULL）
    id_card_str = form.get('id_card', '').strip()
    if 'birth_date' in form:
        r.birth_date = birth_date
    elif not r.birth_date and id_card_str and len(id_card_str) >= 14:
        try:
            birth_str = id_card_str[6:14]
            r.birth_date = datetime.strptime(birth_str, '%Y%m%d').date()
        except Exception:
            pass
    _set_field(r, 'nation')
    _set_encrypted_field(r, 'phone', form.get('phone', ''))
    _set_field(r, 'village_group')
    _set_field(r, 'grid')
    _set_field(r, 'address')
    _set_field(r, 'relationship')
    _set_field(r, 'household_type')
    _set_field(r, 'politics_status')
    _set_field(r, 'education')
    _set_field(r, 'occupation')
    _set_field(r, 'health_status')
    _set_field(r, 'household_property')
    # Preserve household info when editing (don't clear if form doesn't provide them)
    raw_hh_name = form.get('household_name', '').strip()
    raw_hh_code = form.get('household_code', '').strip()
    # Clean placeholder text
    if raw_hh_code and ('（' in raw_hh_code or '自动' in raw_hh_code):
        raw_hh_code = ''
    if rid:
        # Editing: only update household fields if they are non-empty in the form
        if raw_hh_name:
            r.household_name = raw_hh_name
        if raw_hh_code:
            r.household_code = raw_hh_code
    else:
        r.household_name = raw_hh_name
        r.household_code = raw_hh_code
    # Auto-generate household_code for new households
    if not rid and not r.household_code:
        existing_hh = Resident.query.filter(
            Resident.household_name == r.household_name,
            Resident.household_code.isnot(None),
            Resident.household_code != ''
        ).first()
        if existing_hh and existing_hh.household_code:
            r.household_code = existing_hh.household_code
        else:
            max_code = db.session.query(db.func.max(Resident.household_code)).scalar()
            counter = 0
            if max_code and max_code.startswith('H'):
                try:
                    counter = int(max_code[1:])
                except ValueError:
                    counter = 0
            r.household_code = f'H{counter + 1:04d}'
    _set_field(r, 'bank_name')
    _set_encrypted_field(r, 'bank_card', form.get('bank_card', ''))
    _set_field(r, 'marital_status')
    _set_field(r, 'military_status')
    _set_field(r, 'remark')
    # T11-5：流动标记 + 建档来源（flow_status ∈ 流动状态字典；流入建档默认 source=手工）
    raw_flow = form.get('flow_status', '').strip()
    if raw_flow:
        # 079 A0：取值集合与报文均从「流动状态」字典读（缺项回退种子，零行为变化）
        from utils.dict_enum import get_enum
        flow_values = get_enum('流动状态')
        if raw_flow not in flow_values:
            return json_response(False, '流动状态仅支持：' + '/'.join(flow_values))
        r.flow_status = raw_flow
    if not rid:
        r.source = '手工'
    # 编辑时：只有表单显式传入 status 字段才更新状态
    if rid and 'status' not in form and 'status_date' not in form and 'status_reason' not in form:
        pass  # 保持原有状态不变
    else:
        # New resident: default status to normal
        if not rid:
            if 'status' not in form:
                r.status = 'normal'
            else:
                _set_field(r, 'status')
        else:
            _set_field(r, 'status')
        if 'status_date' in form:
            r.status_date = status_date
        if 'status_reason' in form:
            _set_field(r, 'status_reason')

    try:
        # 022：编辑且有白名单字段变更才触发副本同步（与主档同事务）
        if rid:
            new_snap = ResidentSyncService.snapshot_plain(r, _decrypt_field)
            changes = ResidentSyncService.diff(old_snap, new_snap)
            if changes:
                ResidentSyncService.propagate(
                    r, changes, operator=session.get('username', ''))
        db.session.commit()
        action = 'edit' if rid else 'add'
        # GLB-13：关键字段（证件/银行账户）变更前后值留痕——detail 落库前经
        # log_operation 统一脱敏（身份证/银行卡自动打掩码），既有日志页可直接查看。
        # 注意用解包而非 list(v)：本模块 `list` 被同名视图函数（L148）占用。
        key_changes = {f: [v[0], v[1]] for f, v in changes.items()
                       if f in ('id_card', 'bank_card', 'bank_name')}
        log_operation(
            user_name=session.get('username', ''),
            module='居民管理',
            action=action,
            summary=f"{'编辑' if rid else '新增'}居民: {r.name} (ID:{r.id})",
            detail=(json.dumps(key_changes, ensure_ascii=False, default=str)
                    if key_changes else '')
        )
        return json_response(True, '保存成功', data={'id': r.id, 'household_code': r.household_code})
    except Exception as e:
        db.session.rollback()
        # 022：失败整体回滚后另起独立事务补写 failed 日志（不被回滚连带清掉）
        if changes:
            try:
                ResidentSyncService.write_failed_log(
                    r, changes, str(e), operator=session.get('username', ''))
            except Exception as e2:
                # 审阅修复 P3-7：补写失败不再裸吞，留日志痕迹（不二次抛出）
                logging.getLogger('shucun').warning(
                    '居民保存失败且 failed 日志补写失败（rid=%s）: %s / %s',
                    rid, e2, e)
        return json_response(False, f'保存失败: {str(e)}')


# ─── AJAX: Delete Resident ──────────────────────────────────────────

@resident_bp.route('/api/delete/<int:resident_id>', methods=['POST'])
@login_required
def api_delete(resident_id):
    """Delete a resident record."""
    r = Resident.query.get(resident_id)
    if not r:
        return json_response(False, '居民不存在')

    name = r.name
    try:
        db.session.delete(r)
        db.session.commit()
        log_operation(
            user_name=session.get('username', ''),
            module='居民管理',
            action='delete',
            summary=f'删除居民: {name} (ID:{resident_id})'
        )
        return json_response(True, '删除成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'删除失败: {str(e)}')


# ─── AJAX: Delete Entire Household ───────────────────────────────────

@resident_bp.route('/api/household/delete/<household_code>', methods=['POST'])
@login_required
def api_household_delete(household_code):
    """Delete all residents in a household by household_code."""
    if not household_code:
        return json_response(False, '户编号不能为空')

    members = Resident.query.filter(
        Resident.household_code == household_code
    ).all()

    if not members:
        return json_response(False, '该户不存在或已被删除')

    count = len(members)
    names = [m.name for m in members]
    household_name = members[0].household_name or names[0]

    try:
        for m in members:
            db.session.delete(m)
        db.session.commit()
        log_operation(
            user_name=session.get('username', ''),
            module='居民管理',
            action='delete',
            summary=f'删除整户: {household_name}(户编号:{household_code}), 共{count}人: {",".join(names)}'
        )
        return json_response(True, f'整户删除成功，共删除{count}条记录', data={
            'count': count,
            'household_code': household_code,
            'household_name': household_name,
        })
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'删除失败: {str(e)}')


# ─── AJAX: Change Status ─────────────────────────────────────────────

@resident_bp.route('/api/status', methods=['POST'])
@login_required
def api_status():
    """Change resident lifecycle status (normal/deceased)；迁出走 flow_status（A4 口径）。"""
    form = request.form if request.form else (request.json or {})
    resident_id = form.get('id', type=int)
    status = form.get('status', '').strip()
    status_date = None
    if form.get('status_date'):
        try:
            status_date = datetime.strptime(form.get('status_date').strip(), '%Y-%m-%d').date()
        except ValueError:
            pass
    status_reason = form.get('status_reason', '').strip()

    # A4：status 管生命周期（normal/deceased）；moved_out 入参 → 转写 flow_status='流出' + status='normal'
    if not resident_id or status not in ('deceased', 'normal', _LEGACY_MOVED_OUT):
        return json_response(False, '参数错误')

    r = Resident.query.get(resident_id)
    if not r:
        return json_response(False, '居民不存在')

    if status == _LEGACY_MOVED_OUT:
        r.status = 'normal'
        r.flow_status = FLOW_OUT
    else:
        r.status = status
    r.status_date = status_date
    r.status_reason = status_reason

    try:
        # T16-3 死亡软失效联动（R3-E4）：置 deceased 时弱势群体四表 + resident_tag 软失效
        # （同一事务；不物理删除；不涉补贴停发/告知单——T15 CascadeService 范围）
        if r.status == 'deceased':
            soft_invalidate_vulnerable(r.id, reason=status_reason or '死亡注销')
            # T19（R8-E4）：死亡自动关闭该居民全部未处置到期预警（同事务，commit 前）
            from utils.warn_service import close_warnings_for_death
            close_warnings_for_death(r.id)
        db.session.commit()
        status_labels = {'normal': '正常', 'deceased': '死亡', 'moved_out': '迁出'}
        log_operation(
            user_name=session.get('username', ''),
            module='居民管理',
            action='status',
            summary=f'变更居民状态: {r.name} -> {status_labels.get(status, status)}'
        )
        return json_response(True, '状态更新成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'状态更新失败: {str(e)}')


# ─── AJAX: Household Members ─────────────────────────────────────────

@resident_bp.route('/api/household/<household_code>')
@login_required
def api_household(household_code):
    """Get all members of a household."""
    if not household_code:
        return json_response(False, '户编号不能为空')

    members = Resident.query.filter(
        Resident.household_code == household_code
    ).order_by(Resident.relationship).all()

    now = datetime.now()
    data = []
    for m in members:
        age = ''
        if m.birth_date:
            age = now.year - m.birth_date.year - \
                  ((now.month, now.day) < (m.birth_date.month, m.birth_date.day))
        data.append({
            'id': m.id,
            'name': m.name or '',
            'id_card': _decrypt_field(m, 'id_card'),
            'gender': m.gender or '',
            'age': age,
            'relationship': m.relationship or '',
            'village_group': m.village_group or '',
            'phone': _decrypt_field(m, 'phone'),
            'nation': m.nation or '',
            'politics_status': m.politics_status or '',
            'education': m.education or '',
            'occupation': m.occupation or '',
            'status': m.status or 'normal',
            'flow_status': m.flow_status or FLOW_RESIDENT,
        })

    return json_response(success=True, data=data)


# ─── AJAX: Add Member to Existing Household ──────────────────────────

@resident_bp.route('/api/household/add', methods=['POST'])
@login_required
def api_household_add():
    """Add new member to an existing household (auto-fills household info)."""
    form = request.form if request.form else (request.json or {})
    household_code = form.get('household_code', '').strip()

    if not household_code:
        return json_response(False, '户编号不能为空')

    head_relations = ['户主本人', '户主', '本人']

    # Get the household head or first resident to auto-fill common fields
    household_members = Resident.query.filter(
        Resident.household_code == household_code
    ).order_by(Resident.relationship).all()
    template = next((m for m in household_members if m.relationship in head_relations), None)
    if not template and household_members:
        template = household_members[0]

    if not template:
        return json_response(False, '未找到该户编号对应的住户信息')

    def parse_date(val):
        if not val:
            return None
        try:
            return datetime.strptime(val.strip(), '%Y-%m-%d').date()
        except ValueError:
            return None

    r = Resident()
    r.name = form.get('name', '').strip()
    _set_encrypted_field(r, 'id_card', form.get('id_card', ''))
    r.gender = form.get('gender', '').strip()
    r.birth_date = parse_date(form.get('birth_date'))
    if not r.birth_date and form.get('id_card') and len(form.get('id_card', '').strip()) >= 14:
        try:
            birth_str = form.get('id_card', '').strip()[6:14]
            r.birth_date = datetime.strptime(birth_str, '%Y%m%d').date()
        except ValueError:
            pass
    if not r.gender and form.get('id_card') and len(form.get('id_card', '').strip()) == 18:
        try:
            r.gender = '男' if int(form.get('id_card', '').strip()[16]) % 2 == 1 else '女'
        except ValueError:
            pass
    r.nation = form.get('nation', '').strip() or template.nation
    _set_encrypted_field(r, 'phone', form.get('phone', ''))
    r.village_group = form.get('village_group', '').strip() or template.village_group
    r.grid = form.get('grid', '').strip() or template.grid or template.village_group
    r.address = form.get('address', '').strip() or template.address
    r.relationship = form.get('relationship', '').strip()
    r.household_type = form.get('household_type', '').strip() or template.household_type
    r.politics_status = form.get('politics_status', '').strip()
    r.education = form.get('education', '').strip()
    r.occupation = form.get('occupation', '').strip()
    r.health_status = form.get('health_status', '').strip()
    r.household_property = form.get('household_property', '').strip() or template.household_property
    r.household_name = template.household_name
    r.household_code = household_code
    r.bank_name = form.get('bank_name', '').strip()
    _set_encrypted_field(r, 'bank_card', form.get('bank_card', ''))
    r.marital_status = form.get('marital_status', '').strip()
    r.military_status = form.get('military_status', '').strip()
    r.remark = form.get('remark', '').strip()
    r.status = 'normal'
    r.flow_status = form.get('flow_status', '').strip() or FLOW_RESIDENT
    r.source = '手工'

    if not r.name:
        return json_response(False, '姓名不能为空')

    try:
        db.session.add(r)
        db.session.commit()
        log_operation(
            user_name=session.get('username', ''),
            module='居民管理',
            action='add',
            summary=f'新增家庭成员: {r.name} (户编号:{household_code})'
        )
        return json_response(True, '新增成功', data={'id': r.id})
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'新增失败: {str(e)}')


# ─── AJAX: Dictionary Options ─────────────────────────────────────────


# ─── AJAX: Export to Excel ───────────────────────────────────────────

@resident_bp.route('/api/export')
@login_required
def api_export():
    """Export query results to Excel.
    - mode=household: one row per household
    - default: one row per resident
    """
    mode = request.args.get('mode', '').strip()
    post_filters = []
    residents = _resident_filtered_query(request.args, sensitive_post_filters=post_filters).all()
    for pf in post_filters:
        residents = pf(residents)

    now = datetime.now()

    if mode == 'household':
        # ── 按户汇总导出 ──
        from collections import OrderedDict
        hh_map = OrderedDict()
        for r in residents:
            hc = r.household_code or f'_nogroup_{r.id}'
            if hc not in hh_map:
                hh_map[hc] = {'members': [], 'code': r.household_code or ''}
            hh_map[hc]['members'].append(r)

        headers = ['序号', '户主姓名', '户编号', '身份证号', '联系电话', '所在组', '家庭地址', '成员数', '人员状态']
        status_labels = {'normal': '正常', 'deceased': '死亡', 'moved_out': '迁出'}
        rows = []
        for idx, (hc, group) in enumerate(hh_map.items(), 1):
            members = group['members']
            head = next((m for m in members if m.relationship in ('户主本人', '户主')), members[0])
            member_statuses = set()
            for m in members:
                if m.status == 'deceased': member_statuses.add('含死亡')
                elif is_outflow(m): member_statuses.add('含迁出')
                else: member_statuses.add('正常')
            status_str = '、'.join(sorted(member_statuses))
            rows.append([
                idx,
                head.name or '',
                head.household_code or '',
                _decrypt_field(head, 'id_card'),
                _decrypt_field(head, 'phone'),
                head.village_group or '',
                head.address or '',
                len(members),
                status_str,
            ])
        log_operation(session.get('username', ''), '居民管理', 'export',
                      f'导出按户汇总 {len(rows)}户')
        return export_excel(headers, rows, '按户汇总.xlsx')
    else:
        # ── 居民明细导出 ──
        headers = [
            '户主', '户编号', '姓名', '身份证号', '性别', '出生日期',
            '年龄', '民族', '联系电话', '所在组', '家庭地址', '与户主关系',
            '户口类型', '政治面貌', '学历', '职业', '健康状态', '户属性',
            '开户行', '银行卡号', '人员状态'
        ]
        status_labels = {'normal': '正常', 'deceased': '死亡', 'moved_out': '迁出'}
        rows = []
        for r in residents:
            age = ''
            if r.birth_date:
                age = now.year - r.birth_date.year - \
                      ((now.month, now.day) < (r.birth_date.month, r.birth_date.day))
            rows.append([
                r.household_name or '',
                r.household_code or '',
                r.name or '',
                _decrypt_field(r, 'id_card'),
                r.gender or '',
                r.birth_date.strftime('%Y-%m-%d') if r.birth_date else '',
                age,
                r.nation or '',
                _decrypt_field(r, 'phone'),
                r.village_group or '',
                r.address or '',
                r.relationship or '',
                r.household_type or '',
                r.politics_status or '',
                r.education or '',
                r.occupation or '',
                r.health_status or '',
                r.household_property or '',
                r.bank_name or '',
                _decrypt_field(r, 'bank_card'),
                status_labels.get(r.status, r.status or '正常'),
            ])
        log_operation(session.get('username', ''), '居民管理', 'export',
                      f'导出居民数据 {len(residents)}条')
        return export_excel(headers, rows, '居民信息.xlsx')


# ─── AJAX: Download Import Template ──────────────────────────────────

@resident_bp.route('/api/template')
@login_required
def api_template():
    """Download Excel import template with dropdown validations.

    034 P1：模板列由 FIELD_CATALOG 唯一真源派生（删除手写 23 列清单；
    导入引擎 template_field_map()）；列顺序=目录顺序（人员状态在备注后）。
    """
    from utils.import_engine import template_field_map, CATALOG_VERSION
    field_map = template_field_map()

    log_operation(
        user_name=session.get('username', ''),
        module='居民管理',
        action='template',
        summary='下载导入模板'
    )
    return import_excel_template_year(field_map, '居民导入模板.xlsx',
                                      version=CATALOG_VERSION)


# ─── AJAX: Import from Excel ─────────────────────────────────────────

@resident_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    """034 P1 薄门面：快导并入内核（建批次 → 预设映射 → 校验 → 异步执行 → 同步外观轮询）。

    模板模式（模块位 resident.POST→edit，权限 resident.edit）；旧参数 mode=append/overwrite
    映射为 policy=skip/update_non_empty；响应保留旧字段 imported/updated/skipped/errors。
    写库逻辑只存在内核 apply_resident_row（本端点零直接写 Resident）。
    """
    import time

    from utils.import_engine import (start_batch, apply_preset_mapping, read_xlsx,
                                     validate_rows, load_mapping, TEMPLATE_REQUIRED_CODES,
                                     run_import)
    from utils.async_tasks import create_task, run_async, get_task

    file = request.files.get('file')
    if not file:
        return json_response(False, '请选择文件')

    mode = request.form.get('mode', 'append')  # 旧口径：append=仅新增 / overwrite=覆盖更新
    policy = 'update_non_empty' if mode == 'overwrite' else 'skip'

    batch, err, was_dup = start_batch(file, session.get('user_id'), 'template')
    if err:
        return json_response(False, err)
    if was_dup:
        from utils.import_engine import (CATALOG_VERSION, batch_catalog_version,
                                         batch_version_mismatch)
        return json_response(True, '该文件已导入过（返回既有批次）', {
            'batch_id': batch.batch_id, 'duplicate': True,
            'status': batch.status, 'async': batch.status == 'running',
            # 036 F4：模板版本不一致 → 导入前提示（不失败）
            'catalog_version': batch_catalog_version(batch),
            'current_catalog_version': CATALOG_VERSION,
            'version_mismatch': batch_version_mismatch(batch)})

    # 预设映射（缺必需列 → 阻断并可转自定义）
    headers, rows = read_xlsx(batch.source_path)
    preset = apply_preset_mapping(batch.batch_id, headers)
    if not preset['ok'] or preset.get('error'):
        batch.status = 'blocked'
        batch.block_reason = (preset.get('error') or
                              '缺少必需列：' + '、'.join(preset['missing']))
        db.session.commit()
        msg = batch.block_reason
        if preset.get('extra'):
            msg += '；模板外列已忽略：' + '、'.join(preset['extra'][:10])
        return json_response(False, msg, {'blocked': True,
                                          'batch_id': batch.batch_id,
                                          'missing': preset['missing'],
                                          'extra': preset['extra']})

    batch.status = 'mapped'
    batch.policy = policy
    db.session.commit()

    # 校验（模板模式必填集合 = 姓名/身份证/与户主关系/户主名）
    column_map = load_mapping(batch.batch_id)
    summary = validate_rows(batch.batch_id, headers, rows, column_map,
                            required_codes=TEMPLATE_REQUIRED_CODES)

    # 异步执行 + 同步外观（小文件轮询至完成，超时 35s 返回 task_id 由前端续轮询）
    task = create_task('import', str(session.get('user_id', '')), '居民名单导入')
    db.session.commit()
    run_async('import', task.task_id, run_import, batch_id=batch.batch_id)

    deadline = time.time() + 35
    while time.time() < deadline:
        # 034：expire_all 丢弃本线程 session 的旧批次缓存——worker 线程回写后本线程才能读到最新状态
        db.session.expire_all()
        batch = ImportBatch.query.filter_by(batch_id=batch.batch_id).first()
        if batch.status in ('done', 'aborted', 'blocked'):
            break
        time.sleep(0.4)

    # 结果回执（keep 旧字段）
    if batch.status != 'done':
        return json_response(True, '导入已进入后台执行（大文件异步处理）', {
            'batch_id': batch.batch_id, 'task_id': task.task_id,
            'async': True, 'imported': 0, 'updated': 0, 'skipped': 0, 'errors': []})

    imported = (batch.success_rows or 0) - (batch.updated_rows or 0)
    updated = batch.updated_rows or 0
    skipped = batch.skip_rows or 0
    errors = [f'第{e.row_no}行: {e.reason}' for e in ImportRowError.query
              .filter_by(batch_id=batch.batch_id).order_by(ImportRowError.row_no)
              .limit(50).all()]

    log_operation(
        user_name=session.get('username', ''),
        module='居民管理',
        action='import',
        summary=f'导入居民 {imported}条新增, {updated}条更新, {skipped}条跳过'
    )

    msg_parts = []
    if imported:
        msg_parts.append(f'新增 {imported} 条')
    if updated:
        msg_parts.append(f'覆盖更新 {updated} 条')
    if skipped:
        msg_parts.append(f'跳过(重复) {skipped} 条')
    msg = '、'.join(msg_parts) if msg_parts else '无变更'
    if errors:
        msg += f'<br>失败 {len(errors)} 条:<br>' + '<br>'.join(errors[:20])
        if len(errors) > 20:
            msg += f'<br>...等共{len(errors)}条错误'

    return json_response(success=(len(errors) == 0), msg=msg, data={
        'imported': imported,
        'updated': updated,
        'skipped': skipped,
        'errors': errors[:50],
        'batch_id': batch.batch_id,
    })


@resident_bp.route('/api/lookup')
@login_required
def api_lookup():
    """Look up resident by name or ID card. Returns matched residents for auto-fill."""
    name = request.args.get('name', '').strip()
    id_card = request.args.get('id_card', '').strip()
    q = Resident.query.filter(Resident.status != '死亡')
    if name:
        q = q.filter(Resident.name == name)
    if id_card:
        q = q.filter(Resident.id_card_hash == sha256_hash(id_card.strip().upper()))
    if not name and not id_card:
        return json_response(False, '请输入姓名或身份证号')
    items = q.limit(10).all()
    data = []
    for r in items:
        data.append({
            'id': r.id, 'name': r.name or '',
            'id_card': _decrypt_field(r, 'id_card'),
            'gender': r.gender or '', 'nation': r.nation or '',
            'phone': _decrypt_field(r, 'phone'),
            'village_group': r.village_group or '',
            'grid': r.grid or '',
            'education': r.education or '',
            'address': r.address or '', 'household_name': r.household_name or '',
            'household_code': r.household_code or '',
            'household_type': r.household_type or '',
            'household_property': r.household_property or '',
            'relationship': r.relationship or '',
        })
    return json_response(success=True, data=data, total=len(data))


# ─── 更换户主 ───────────────────────────────────────────────

@resident_bp.route('/api/change-head/query', methods=['POST'])
@login_required
def api_change_head_query():
    """查询住户信息（按户主姓名 or 户编号）"""
    form = request.form if request.form else (request.json or {})
    name = form.get('name', '').strip()
    code = form.get('household_code', '').strip()

    HEAD_RELATIONS = ['户主本人', '户主', '本人']
    EXCLUDED_STATUSES = ['deceased', '死亡']  # A4：生命周期排除；流动性排除走 is_outflow

    if not name and not code:
        return json_response(False, '请输入户主姓名或户编号')

    # 按户编号查
    if code:
        members = Resident.query.filter(
            Resident.household_code == code
        ).order_by(Resident.relationship).all()
    else:
        # 按户主姓名查（模糊匹配，兼容 户主/户主本人）
        head = Resident.query.filter(
            Resident.name.like(f'%{name}%'),
            Resident.relationship.in_(HEAD_RELATIONS)
        ).first()
        if not head or not head.household_code:
            return json_response(False, f'未找到户主"{name}"对应的家庭')
        members = Resident.query.filter(
            Resident.household_code == head.household_code
        ).order_by(Resident.relationship).all()

    if not members:
        return json_response(False, '未找到家庭成员')

    now = datetime.now()
    member_list = []
    for m in members:
        age = ''
        if m.birth_date:
            age = now.year - m.birth_date.year - \
                  ((now.month, now.day) < (m.birth_date.month, m.birth_date.day))
        member_list.append({
            'id': m.id,
            'name': m.name or '',
            'id_card': _decrypt_field(m, 'id_card'),
            'gender': m.gender or '',
            'age': age,
            'relationship': m.relationship or '',
            'phone': _decrypt_field(m, 'phone'),
            'status': m.status or 'normal',
            'flow_status': m.flow_status or FLOW_RESIDENT,
            'is_head': m.relationship in HEAD_RELATIONS,
        })

    head = next((m for m in members if m.relationship in HEAD_RELATIONS), members[0])
    head_name = head.name or ''

    # 过滤掉可选候选人（排除户主本人、已故 + 流出人员）
    candidate_ids = [
        m.id for m in members
        if m.relationship not in HEAD_RELATIONS
        and m.status not in EXCLUDED_STATUSES
        and not is_outflow(m)
    ]

    return json_response(success=True, data={
        'household_code': members[0].household_code or '',
        'household_name': members[0].household_name or '',
        'village_group': members[0].village_group or '',
        'current_head': head_name,
        'members': member_list,
        'candidate_ids': candidate_ids,
    })


@resident_bp.route('/api/change-head/execute', methods=['POST'])
@login_required
def api_change_head_execute():
    """执行更换户主"""
    form = request.form if request.form else (request.json or {})
    household_code = form.get('household_code', '').strip()
    old_head_id = form.get('old_head_id', type=int)
    new_head_id = form.get('new_head_id', type=int)
    old_new_relation = form.get('old_new_relation', '').strip()

    HEAD_RELATIONS = ['户主本人', '户主', '本人']

    if not household_code or not old_head_id or not new_head_id:
        return json_response(False, '参数不完整')

    if old_head_id == new_head_id:
        return json_response(False, '新旧户主不能是同一人')

    old_head = Resident.query.get(old_head_id)
    new_head = Resident.query.get(new_head_id)

    if not old_head or not new_head:
        return json_response(False, '未找到对应居民')
    if old_head.household_code != household_code or new_head.household_code != household_code:
        return json_response(False, '新旧户主不属于同一家庭')

    # 校验新户主不是已故/流出人员（A4：生命周期 + 流动性）
    if new_head.status in ('deceased', '死亡') or is_outflow(new_head):
        return json_response(False, '已故或迁出人员不能担任户主')

    # 执行更换
    old_head.relationship = old_new_relation if old_new_relation else '其他亲属'
    new_head.relationship = '户主本人'
    # 更新户主姓名
    for m in Resident.query.filter(Resident.household_code == household_code).all():
        m.household_name = new_head.name

    try:
        db.session.commit()
        log_operation(
            user_name=session.get('username', ''),
            module='居民管理',
            action='change_head',
            summary=f'更换户主: {old_head.name} -> {new_head.name} (户编号: {household_code})'
        )
        return json_response(True, f'户主已由"{old_head.name}"变更为"{new_head.name}"')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'更换失败: {str(e)}')


# ═══════════════════════════════════════════════════════════════
# T11-3 多标签（R1 快捷标签，resident_tag 接线）
# ═══════════════════════════════════════════════════════════════

_TAG_CATEGORIES = ('居民标签', '户属性', '关爱对象分类')


def _valid_tag_codes():
    """合法标签码集合（来自字典；仅政策性标签）。"""
    codes = set()
    for cat in _TAG_CATEGORIES:
        for item in get_dict_items(cat):
            codes.add((item.value or item.label or '').strip())
    return codes


@resident_bp.route('/api/tag/add', methods=['POST'])
@login_required
def api_tag_add():
    """打标签（POST→resident.add 权限）。tag_code 须 ∈ 字典，非法 400；同人同码幂等。"""
    form = request.form if request.form else (request.json or {})
    resident_id = form.get('resident_id', type=int)
    tag_code = (form.get('tag_code') or '').strip()
    if not resident_id or not tag_code:
        return json_response(False, '参数错误')
    r = Resident.query.get(resident_id)
    if not r:
        return json_response(False, '居民不存在')
    if tag_code not in _valid_tag_codes():
        return json_response(False, f'非法标签码：{tag_code}（请从字典中选择）')

    exists = ResidentTag.query.filter_by(
        resident_id=resident_id, tag_code=tag_code, status='有效'
    ).first()
    if exists:
        return json_response(True, '该标签已存在（幂等）')
    db.session.add(ResidentTag(
        resident_id=resident_id,
        tag_code=tag_code,
        source='手动',
        status='有效',
        operator=session.get('user_id'),
    ))
    try:
        db.session.commit()
        log_operation(session.get('username', ''), '居民管理', 'edit',
                      f'给居民#{resident_id}打标签: {tag_code}')
        return json_response(True, '标签添加成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'标签添加失败: {str(e)}')


@resident_bp.route('/api/tag/remove', methods=['POST'])
@login_required
def api_tag_remove():
    """移除标签（软失效：status='失效'）。"""
    form = request.form if request.form else (request.json or {})
    resident_id = form.get('resident_id', type=int)
    tag_code = (form.get('tag_code') or '').strip()
    if not resident_id or not tag_code:
        return json_response(False, '参数错误')
    tag = ResidentTag.query.filter_by(
        resident_id=resident_id, tag_code=tag_code, status='有效'
    ).first()
    if not tag:
        return json_response(False, '该标签不存在')
    tag.status = '失效'
    tag.invalid_reason = '手动移除'
    tag.invalid_time = datetime.now()
    try:
        db.session.commit()
        log_operation(session.get('username', ''), '居民管理', 'edit',
                      f'移除居民#{resident_id}标签: {tag_code}')
        return json_response(True, '标签已移除')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'标签移除失败: {str(e)}')


@resident_bp.route('/api/tag/list')
@login_required
def api_tag_list():
    """居民有效标签列表（GET /resident/api/tag/list?resident_id=）。"""
    resident_id = request.args.get('resident_id', type=int)
    if not resident_id:
        return json_response(False, '缺少 resident_id')
    tags = [{
        'tag_code': t.tag_code,
        'source': t.source or '',
        'create_time': t.create_time.strftime('%Y-%m-%d %H:%M') if t.create_time else '',
    } for t in ResidentTag.query.filter_by(resident_id=resident_id, status='有效')
        .order_by(ResidentTag.create_time).all()]
    return json_response(success=True, data=tags)


@resident_bp.route('/api/tag/search')
@login_required
def api_tag_search():
    """按标签筛选居民（GET /resident/api/tag/search?tag_code=&page=&limit=）。"""
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    tag_code = request.args.get('tag_code', '').strip()
    if not tag_code:
        return json_response(False, '缺少 tag_code')
    post_filters = []
    query = Resident.query.join(ResidentTag, ResidentTag.resident_id == Resident.id).filter(
        ResidentTag.tag_code == tag_code,
        ResidentTag.status == '有效',
    ).order_by(Resident.name)
    items, total, pages = _apply_post_filters(query, page, limit, post_filters)
    now = datetime.now()
    data = []
    for r in items:
        age = ''
        if r.birth_date:
            age = now.year - r.birth_date.year - \
                  ((now.month, now.day) < (r.birth_date.month, r.birth_date.day))
        data.append({
            'id': r.id,
            'name': r.name or '',
            'id_card': _decrypt_field(r, 'id_card'),
            'gender': r.gender or '',
            'age': age,
            'village_group': r.village_group or '',
            'household_code': r.household_code or '',
            'phone': _decrypt_field(r, 'phone'),
            'flow_status': r.flow_status or FLOW_RESIDENT,
        })
    return json_response(success=True, data={
        'data': data, 'total': total, 'pages': pages,
        'page': page, 'limit': limit,
    })


# ═══════════════════════════════════════════════════════════════
# T11-4 家庭关系：移出本户（解除挂户，保留档案）
# ═══════════════════════════════════════════════════════════════

@resident_bp.route('/api/household/remove', methods=['POST'])
@login_required
def api_household_remove():
    """移出本户：清空 household_code/relationship（保留居民档案，非删档）。"""
    form = request.form if request.form else (request.json or {})
    resident_id = form.get('resident_id', type=int)
    if not resident_id:
        return json_response(False, '参数错误')
    r = Resident.query.get(resident_id)
    if not r:
        return json_response(False, '居民不存在')
    old_code = r.household_code or ''
    r.household_code = ''
    r.relationship = ''
    try:
        db.session.commit()
        log_operation(session.get('username', ''), '居民管理', 'edit',
                      f'居民#{resident_id}({r.name})移出本户: {old_code}')
        return json_response(True, '已移出本户（档案保留）')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'移出本户失败: {str(e)}')


# ═══════════════════ T16-1 弱势群体台账（R3，统一入口）═══════════════════
# 四分类映射（对齐 run_migration._backfill_tags 权威口径，PRD R3-E1/C4）：
#   低保=dibao / 残疾=disabled / 特困=care_target（特困供养老人+困境儿童）/ 其他困难=special_support
_VULN_CATEGORIES = (
    ('dibao', '低保', Dibao),
    ('disabled', '残疾', Disabled),
    ('tekun', '特困', CareTarget),
    ('special', '其他困难', SpecialSupport),
)
_VULN_MODEL_MAP = {k: (name, model) for k, name, model in _VULN_CATEGORIES}


def _vuln_match_resident(row):
    """弱势群体行关联居民：优先 resident_id；special_support 无 resident_id，
    按 household_id_card sha256 hash 匹配居民库（T09 决策 2，对齐 subsidy_service._match_without_resident_id）。
    匹配过程不输出明文身份证（脱敏红线）。"""
    rid = getattr(row, 'resident_id', None)
    if rid:
        return Resident.query.get(rid)
    hcard = getattr(row, 'household_id_card', None) or ''
    if hcard:
        h = sha256_hash(hcard)
        return Resident.query.filter_by(id_card_hash=h).first()
    return None


def _vuln_is_child_row(row):
    """弱势群体行是否为留守/困境儿童（行级可见判定，对齐 masking._is_sensitive_child_row）。"""
    target_type = getattr(row, 'target_type', None)
    child_type = getattr(row, 'child_type', None)
    return target_type == '儿童' and child_type in ('留守', '困境')


def _vuln_row_dict(row, category):
    """弱势群体行转 dict（脱敏由全局 after_request 中间件按字段名处理）。"""
    res = _vuln_match_resident(row)
    d = {
        'id': row.id,
        'category': category,
        'resident_id': res.id if res else None,
        'name': (res.name if res else '') or getattr(row, 'name', '') or getattr(row, 'household_name', '') or '',
        'id_card': _decrypt_field(res, 'id_card') if res else '',
        'gender': (res.gender if res else '') or getattr(row, 'gender', '') or '',
        'age': '',
        'grid': (res.grid if res else '') or getattr(row, 'grid', '') or getattr(row, 'village_group', '') or '',
        'detail': '',
    }
    if res and res.birth_date:
        today = date.today()
        d['age'] = today.year - res.birth_date.year - (
            (today.month, today.day) < (res.birth_date.month, res.birth_date.day))
    if category == 'dibao':
        d['detail'] = (row.dibao_type or row.sub_category or '')
    elif category == 'disabled':
        d['detail'] = f"{row.disability_type or ''} {row.disability_level or ''}".strip()
    elif category == 'tekun':
        d['detail'] = f"{row.target_type or ''} {row.elder_type or ''}{row.child_type or ''}".strip()
    elif category == 'special':
        d['detail'] = row.support_mode or ''
    return d


def _vuln_stats():
    """四分类计数 + 按人去重总人数（R3-E3：一人多类不重复计人）。

    去重键：匹配到居民 → ('r', resident_id)；未匹配 special_support 行 → ('sp', row_id)。
    儿童行不因行级过滤而改变统计口径（对齐 care.py counts 保留先例）。
    """
    stats = {}
    seen = set()
    for key, name, model in _VULN_CATEGORIES:
        cnt = 0
        for r in model.query.filter(model.status == '有效').all():
            res = _vuln_match_resident(r)
            if res:
                seen.add(('r', res.id))
            else:
                seen.add(('sp', r.id))
            cnt += 1
        stats[key] = {'name': name, 'count': cnt}
    stats['total'] = {'name': '去重总人数', 'count': len(seen)}
    return stats


@resident_bp.route('/vulnerable')
@login_required
def vulnerable_page():
    """弱势群体台账统一入口（R3，T16-1）。"""
    # 075 RC-06：副标题「村名 · 10 类民政对象 · 在册 N 人」（本页为聚合视图，计数同源）
    from utils.agg_cache import get_civil_objects_count
    return render_template('resident/vulnerable.html', civil_headcount=get_civil_objects_count())


@resident_bp.route('/api/vulnerable/list')
@login_required
@child_rows_guard
def api_vulnerable_list():
    """弱势群体台账数据接口（R3 验收 1/2 + 儿童行级 Z3 M23 落点）。

    GET /resident/api/vulnerable/list?category=dibao|disabled|tekun|special&name=&page=&limit=
    返回 stats（四分类+去重总人数）与当前分类分页明细。
    """
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    category = (request.args.get('category') or 'dibao').strip()
    name = request.args.get('name', '').strip()
    if category not in _VULN_MODEL_MAP:
        return json_response(False, '未知分类')
    _, model = _VULN_MODEL_MAP[category]

    items = []
    for row in model.query.filter(model.status == '有效').order_by(model.id.desc()).all():
        # 儿童行级可见性二次过滤（Z3 M23 入口落点；非民政主任/admin 过滤留守/困境儿童行）
        if not g.get('can_see_children', False) and _vuln_is_child_row(row):
            continue
        d = _vuln_row_dict(row, category)
        if name and name not in (d.get('name') or ''):
            continue
        items.append(d)

    total = len(items)
    pages = max(1, (total + limit - 1) // limit)
    page_items = items[(page - 1) * limit: page * limit]
    return json_response(success=True, data={
        'data': page_items,
        'total': total,
        'pages': pages,
        'page': page,
        'limit': limit,
        'category': category,
        'stats': _vuln_stats(),
    })


@resident_bp.route('/sync-log')
@login_required
@admin_required
def sync_log():
    """GLB-07：主档→副本同步日志追溯（admin；含 GLB-06 conflict 留痕行）。

    ADR-016：resident 蓝图（BLUEPRINT_MODULES 'resident'）蓝图级归类；视图名
    GET → action view → resident.view，端点级再叠 admin_required（日志含变更值）。
    """
    from models import ResidentSyncLog
    status = (request.args.get('status') or '').strip()
    field = (request.args.get('field') or '').strip()
    rid = request.args.get('resident_id', type=int)
    page = max(request.args.get('page', 1, type=int), 1)
    PAGE_SIZE = 50                              # 硬约束：查询带上限

    q = ResidentSyncLog.query
    if status:
        q = q.filter_by(status=status)
    if field:
        q = q.filter_by(field=field)
    if rid:
        q = q.filter_by(resident_id=rid)
    total = q.count()
    rows = (q.order_by(ResidentSyncLog.id.desc())
             .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all())
    names = {}
    ids = {r.resident_id for r in rows if r.resident_id}
    if ids:
        names = {x.id: x.name for x in Resident.query.filter(Resident.id.in_(ids)).all()}
    return render_template('resident/sync_log.html',
                           rows=rows, names=names, total=total,
                           page=page, pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                           status=status, field=field, resident_id=rid or '')
