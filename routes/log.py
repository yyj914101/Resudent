from flask import Blueprint, render_template, request, session
from models import db, OperationLog
from utils.decorators import login_required, admin_required
from utils.helpers import json_response, paginate_query, log_operation, export_excel
from datetime import datetime, timedelta

log_bp = Blueprint('log', __name__)


@log_bp.route('/')
@login_required
@admin_required
def list():
    return render_template('log/list.html')


@log_bp.route('/api/list')
@login_required
@admin_required
def api_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    user_name = request.args.get('user_name', '').strip()
    module = request.args.get('module', '').strip()
    action = request.args.get('action', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    query = OperationLog.query

    if user_name:
        query = query.filter(OperationLog.user_name.contains(user_name))
    if module:
        query = query.filter(OperationLog.module == module)
    if action:
        query = query.filter(OperationLog.action == action)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(OperationLog.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(OperationLog.created_at < dt_to)
        except ValueError:
            pass

    query = query.order_by(OperationLog.id.desc())
    items, total, pages = paginate_query(query, page, limit)

    data = []
    for item in items:
        data.append({
            'id': item.id,
            'user_name': item.user_name or '',
            'module': item.module or '',
            'action': item.action or '',
            'summary': item.summary or '',
            'detail': item.detail or '',
            'result': item.result or '',
            'ip_address': item.ip_address or '',
            'created_at': item.created_at.strftime('%Y-%m-%d %H:%M:%S') if item.created_at else '',
        })

    return json_response(True, data=data, total=total)


@log_bp.route('/api/export')
@login_required
@admin_required
def api_export():
    user_name = request.args.get('user_name', '').strip()
    module = request.args.get('module', '').strip()
    action = request.args.get('action', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    query = OperationLog.query

    if user_name:
        query = query.filter(OperationLog.user_name.contains(user_name))
    if module:
        query = query.filter(OperationLog.module == module)
    if action:
        query = query.filter(OperationLog.action == action)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(OperationLog.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(OperationLog.created_at < dt_to)
        except ValueError:
            pass

    items = query.order_by(OperationLog.id.desc()).all()

    headers = ['操作人', '模块', '操作类型', '摘要', '详细信息', '结果', 'IP地址', '操作时间']
    rows = []
    for item in items:
        rows.append([
            item.user_name or '',
            item.module or '',
            item.action or '',
            item.summary or '',
            item.detail or '',
            item.result or '',
            item.ip_address or '',
            item.created_at.strftime('%Y-%m-%d %H:%M:%S') if item.created_at else '',
        ])

    return export_excel(headers, rows, '操作日志.xlsx')
