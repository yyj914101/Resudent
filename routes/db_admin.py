# -*- coding: utf-8 -*-
"""routes.db_admin — 数据库管理面板（025 基础版：🟢只读 + 🟡低风险）。

归属 ADMIN_BLUEPRINTS（ADR-016：admin 专属，漏归=403）；本文件只做 HTTP 接线，
安全判定/DDL 生成/备份/审计全部下沉 utils/ddl_guard.py（owner=utils/ 纪律）。
"""
from flask import Blueprint, render_template, request, session

from utils import ddl_guard
from utils.decorators import admin_required, login_required
from utils.helpers import json_response

db_admin_bp = Blueprint('db_admin', __name__)

# 面板允许的黄级操作白名单（与 ddl_guard.plan 支持集一致）
_ACTIONS = {'create_table', 'add_column', 'create_index', 'drop_index',
            'rename_table', 'copy_table', 'analyze', 'vacuum'}

# 🔴 红级操作白名单（026）
_RED_ACTIONS = set(ddl_guard.RED_ACTIONS)


def _operator():
    return session.get('username') or 'admin'


@db_admin_bp.route('/')
@login_required
@admin_required
def index():
    return render_template('db_admin/index.html')


# ---------- 🟢 只读 ----------

@db_admin_bp.route('/api/overview')
@login_required
@admin_required
def api_overview():
    return json_response(True, data=ddl_guard.overview())


@db_admin_bp.route('/api/tables')
@login_required
@admin_required
def api_tables():
    return json_response(True, data=ddl_guard.tables_list())


@db_admin_bp.route('/api/table/<name>')
@login_required
@admin_required
def api_table_detail(name):
    try:
        return json_response(True, data=ddl_guard.table_detail(name))
    except ddl_guard.GuardError as e:
        return json_response(False, str(e)), 400


@db_admin_bp.route('/api/check')
@login_required
@admin_required
def api_check():
    return json_response(True, data=ddl_guard.integrity_check())


@db_admin_bp.route('/api/reconcile')
@login_required
@admin_required
def api_reconcile():
    return json_response(True, data=ddl_guard.reconcile_inventory())


@db_admin_bp.route('/api/audit')
@login_required
@admin_required
def api_audit():
    return json_response(True, data=ddl_guard.audit_list())


# ---------- 🟡 低风险（计划预览 + 执行） ----------

@db_admin_bp.route('/api/preview', methods=['POST'])
@login_required
@admin_required
def api_preview():
    """黄级操作确认弹窗数据源：只生成 SQL 计划与风险提示，不执行。"""
    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '')
    if action not in _ACTIONS:
        return json_response(False, f'未知操作类型：{action}'), 400
    try:
        info = ddl_guard.plan(action, data.get('payload') or {})
    except ddl_guard.GuardError as e:
        return json_response(False, str(e)), 400
    sql = info['sql']
    return json_response(True, data={
        'action': action,
        'target': info['target'],
        'sql': sql if isinstance(sql, str) else '\n'.join(sql),
        'app_table': info['app_table'],
        'warning': info['warning'],
        'backup_required': True,  # 口径：所有 🟡 + VACUUM 强制前置备份
    })


@db_admin_bp.route('/api/execute', methods=['POST'])
@login_required
@admin_required
def api_execute():
    """黄级操作执行：前置备份 → 执行 → 审计（三重安全闸在 ddl_guard 内强制）。"""
    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '')
    if action not in _ACTIONS:
        return json_response(False, f'未知操作类型：{action}'), 400
    try:
        result = ddl_guard.execute(action, data.get('payload') or {}, _operator())
    except ddl_guard.GuardError as e:
        return json_response(False, str(e)), 400
    return json_response(True, '操作完成', data={
        'backup_file': result['backup_file'],
        'sql': result['sql'],
        'target': result['target'],
        'size_before': result.get('size_before'),
        'size_after': result.get('size_after'),
    })


# ---------- 🔴 高风险操作（026：三重闸服务端强制） ----------

@db_admin_bp.route('/api/red/preview', methods=['POST'])
@login_required
@admin_required
def api_red_preview():
    """闸 2 影响面展示（只计算、不备份、不执行）。"""
    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '')
    if action not in _RED_ACTIONS:
        return json_response(False, f'未知红级操作：{action}'), 400
    try:
        info = ddl_guard.plan_red(action, data.get('payload') or {})
    except ddl_guard.GuardError as e:
        return json_response(False, str(e)), 400
    sql = info['sql']
    return json_response(True, data={
        'action': action,
        'target': info['target'],
        'sql': sql if isinstance(sql, str) else '\n'.join(sql),
        'rows': info['rows'],
        'deps': info['deps'],
        'app_table': info['app_table'],
    })


@db_admin_bp.route('/api/red/execute', methods=['POST'])
@login_required
@admin_required
def api_red_execute():
    """红级执行：闸 1（前置备份）→ 闸 2（表名确认）→ 执行 → 闸 3（恢复指引），全程服务端强制。"""
    data = request.get_json(silent=True) or {}
    action = str(data.get('action') or '')
    if action not in _RED_ACTIONS:
        return json_response(False, f'未知红级操作：{action}'), 400
    try:
        result = ddl_guard.execute_red(
            action, data.get('payload') or {}, _operator(),
            data.get('confirm_name'))
    except ddl_guard.GuardError as e:
        return json_response(False, str(e)), 400
    return json_response(True, '红级操作完成', data=result)


@db_admin_bp.errorhandler(ddl_guard.GuardError)
def _guard_error(e):
    return json_response(False, str(e)), 400