from flask import Blueprint, render_template, request, session
from models import db, User, Role
from utils.decorators import login_required, admin_required
from utils.helpers import json_response, paginate_query, log_operation
from datetime import datetime

user_mgmt_bp = Blueprint('user_mgmt', __name__)


@user_mgmt_bp.route('/')
@login_required
@admin_required
def list():
    return render_template('user_mgmt/list.html')


def _user_to_dict(item):
    """单条序列化（R-12：list 与 detail 共用）。"""
    return {
        'id': item.id,
        'username': item.username,
        'real_name': item.real_name or '',
        'role_id': item.role_id,
        'role_name': item.role.name if item.role else '',
        'is_active': item.is_active,
        'last_login': item.last_login.strftime('%Y-%m-%d %H:%M') if item.last_login else '',
        'created_at': item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else '',
    }


@user_mgmt_bp.route('/api/list')
@login_required
@admin_required
def api_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    keyword = request.args.get('keyword', '').strip()

    query = User.query

    if keyword:
        query = query.filter(
            db.or_(
                User.username.contains(keyword),
                User.real_name.contains(keyword)
            )
        )

    query = query.order_by(User.id.asc())
    items, total, pages = paginate_query(query, page, limit)

    data = [_user_to_dict(item) for item in items]

    return json_response(True, data=data, total=total)


@user_mgmt_bp.route('/api/detail/<int:item_id>')
@login_required
@admin_required
def api_detail(item_id):
    """单条详情（R-12：编辑改走 detail）。"""
    item = User.query.get(item_id)
    if not item:
        return json_response(False, '记录不存在')
    return json_response(True, data=_user_to_dict(item))


@user_mgmt_bp.route('/api/save', methods=['POST'])
@login_required
@admin_required
def api_save():
    item_id = request.form.get('id', type=int)
    username = request.form.get('username', '').strip()
    real_name = request.form.get('real_name', '').strip()
    role_id = request.form.get('role_id', type=int)
    password = request.form.get('password', '')

    if not username:
        return json_response(False, '用户名不能为空')

    if item_id:
        item = User.query.get(item_id)
        if not item:
            return json_response(False, '用户不存在')
        # Check username uniqueness (excluding current user)
        existing = User.query.filter(User.username == username, User.id != item_id).first()
        if existing:
            return json_response(False, '用户名已存在')
        action = 'edit'
    else:
        existing = User.query.filter_by(username=username).first()
        if existing:
            return json_response(False, '用户名已存在')
        item = User()
        action = 'add'

    item.username = username
    item.real_name = real_name
    item.role_id = role_id or 2  # default role_id=2 (普通用户)

    if password:
        item.set_password(password)

    db.session.add(item)
    db.session.commit()
    log_operation(session.get('username'), '用户管理', action, f'{action}用户: {username}', result='success')
    return json_response(True, '保存成功')


@user_mgmt_bp.route('/api/toggle/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def api_toggle(item_id):
    item = User.query.get(item_id)
    if not item:
        return json_response(False, '用户不存在')
    if item.id == session.get('user_id'):
        return json_response(False, '不能停用自己的账号')
    item.is_active = not item.is_active
    db.session.commit()
    status = '启用' if item.is_active else '停用'
    log_operation(session.get('username'), '用户管理', 'toggle', f'{status}用户: {item.username}', result='success')
    return json_response(True, f'已{status}')


@user_mgmt_bp.route('/api/reset-password/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def api_reset_password(item_id):
    import secrets
    import string
    item = User.query.get(item_id)
    if not item:
        return json_response(False, '用户不存在')
    # P1-2 整改：随机强口令（大小写字母+数字，12位），仅一次性展示；强制下次登录改密
    alphabet = string.ascii_letters + string.digits
    while True:
        default_password = ''.join(secrets.choice(alphabet) for _ in range(12))
        if any(c.isdigit() for c in default_password) and any(c.isalpha() for c in default_password):
            break
    item.set_password(default_password)
    item.must_change_password = True
    db.session.commit()
    log_operation(session.get('username'), '用户管理', 'reset_password', f'重置用户密码: {item.username}', result='success')
    return json_response(True, f'密码已重置（仅本次展示，用户登录后须修改）: {default_password}')


@user_mgmt_bp.route('/api/delete/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def api_delete(item_id):
    if item_id == session.get('user_id'):
        return json_response(False, '不能删除自己的账号')
    item = User.query.get(item_id)
    if not item:
        return json_response(False, '用户不存在')
    username = item.username
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username'), '用户管理', 'delete', f'删除用户: {username}', result='success')
    return json_response(True, '删除成功')
