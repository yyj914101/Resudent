from flask import Blueprint, render_template, request, session, jsonify
from models import db, Role
from utils.decorators import login_required, admin_required
from utils.helpers import json_response, paginate_query, log_operation
from utils.permissions import (
    PERMISSION_SCHEMA_KEY,
    PERMISSION_SCHEMA_VERSION,
    normalize_role_permissions,
)
import json
from datetime import datetime

role_mgmt_bp = Blueprint('role_mgmt', __name__)


@role_mgmt_bp.route('/')
@login_required
@admin_required
def list():
    return render_template('role_mgmt/list.html')


@role_mgmt_bp.route('/api/list')
@login_required
@admin_required
def api_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    keyword = request.args.get('keyword', '').strip()

    query = Role.query

    if keyword:
        query = query.filter(Role.name.contains(keyword))

    query = query.order_by(Role.id.asc())
    items, total, pages = paginate_query(query, page, limit)

    data = []
    for item in items:
        # P3-1 整改：损坏 JSON 兜底返回空权限，避免 500
        try:
            perms = normalize_role_permissions(json.loads(item.permissions or '{}'))
        except (ValueError, TypeError):
            perms = normalize_role_permissions({})
        data.append({
            'id': item.id,
            'name': item.name,
            'description': item.description or '',
            'permissions': perms,
            'is_system': item.is_system,
            'user_count': len(item.users) if item.users else 0,
            'created_at': item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else '',
        })

    return json_response(True, data=data, total=total)


@role_mgmt_bp.route('/api/save', methods=['POST'])
@login_required
@admin_required
def api_save():
    item_id = request.form.get('id', type=int)
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    permissions_json = request.form.get('permissions', '{}')

    if not name:
        return json_response(False, '角色名不能为空')

    # Parse permissions JSON
    try:
        perms = json.loads(permissions_json) if isinstance(permissions_json, str) else permissions_json
    except json.JSONDecodeError:
        return json_response(False, '权限数据格式错误')
    perms[PERMISSION_SCHEMA_KEY] = PERMISSION_SCHEMA_VERSION

    if item_id:
        item = Role.query.get(item_id)
        if not item:
            return json_response(False, '角色不存在')
        existing = Role.query.filter(Role.name == name, Role.id != item_id).first()
        if existing:
            return json_response(False, '角色名已存在')
        action = 'edit'
    else:
        existing = Role.query.filter_by(name=name).first()
        if existing:
            return json_response(False, '角色名已存在')
        item = Role()
        item.is_system = False
        action = 'add'

    item.name = name
    item.description = description
    item.permissions = json.dumps(perms, ensure_ascii=False)

    db.session.add(item)
    db.session.commit()
    log_operation(session.get('username'), '角色管理', action, f'{action}角色: {name}', result='success')
    return json_response(True, '保存成功')


@role_mgmt_bp.route('/api/delete/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def api_delete(item_id):
    item = Role.query.get(item_id)
    if not item:
        return json_response(False, '角色不存在')
    if item.is_system:
        return json_response(False, '系统内置角色不能删除')
    if item.users:
        return json_response(False, f'该角色下还有{item.users.count()}个用户，无法删除')
    name = item.name
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username'), '角色管理', 'delete', f'删除角色: {name}', result='success')
    return json_response(True, '删除成功')


@role_mgmt_bp.route('/api/copy/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def api_copy(item_id):
    """Copy permissions from an existing role."""
    source = Role.query.get(item_id)
    if not source:
        return json_response(False, '源角色不存在')

    new_name = request.form.get('name', '').strip()
    if not new_name:
        return json_response(False, '请输入新角色名')

    existing = Role.query.filter_by(name=new_name).first()
    if existing:
        return json_response(False, '角色名已存在')

    try:
        source_permissions = normalize_role_permissions(json.loads(source.permissions or '{}'))
    except (TypeError, ValueError):
        source_permissions = {}
    source_permissions[PERMISSION_SCHEMA_KEY] = PERMISSION_SCHEMA_VERSION

    new_role = Role(
        name=new_name,
        description=source.description + '（复制）',
        permissions=json.dumps(source_permissions, ensure_ascii=False),
        is_system=False
    )
    db.session.add(new_role)
    db.session.commit()

    log_operation(session.get('username'), '角色管理', 'copy',
                  f'从角色"{source.name}"复制权限创建角色"{new_name}"', result='success')
    return json_response(True, '复制成功')
