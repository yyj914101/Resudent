from functools import wraps
from flask import session, redirect, url_for, jsonify, request
from models import User, Role
import json
from utils.permissions import user_has_permission

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'msg': '未登录'}), 401
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        user = User.query.get(session['user_id'])
        if not user or user.role_id != 1:  # role_id=1 is super admin
            return jsonify({'success': False, 'msg': '权限不足'}), 403
        return f(*args, **kwargs)
    return decorated_function

def permission_required(perm_key):
    """Check if user has specific permission.
    perm_key format: 'module.action' e.g. 'resident.add'
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('auth.login'))
            user = User.query.get(session['user_id'])
            if not user:
                return redirect(url_for('auth.login'))
            # Super admin (role_id=1) has all permissions
            if user.role_id == 1:
                return f(*args, **kwargs)
            if not user_has_permission(user, perm_key):
                return jsonify({'success': False, 'msg': '权限不足'}), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator
