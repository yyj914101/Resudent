# -*- coding: utf-8 -*-
"""routes.warn — 到期预警蓝图（Sprint5 T19，R8）。

owner map：本模块只接线/留痕；预警判定/处置唯一落 utils/warn_service.py。
模块权限由全局 before_request 经 BLUEPRINT_MODULES['warn']='warn' 自动映射
（GET → warn.view；POST handle/check → warn.edit）。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from models import db
from utils import warn_service
from utils.async_tasks import create_task, get_task, run_async, run_lazy_check
from utils.decorators import login_required
from utils.helpers import json_response, log_operation

warn_bp = Blueprint('warn', __name__)


@warn_bp.route('/api/handle', methods=['POST'])
@login_required
def api_handle():
    """处置预警（未处置 → 已处置），同步 dismiss 关联 warn todo；留痕。"""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    try:
        warn_id = int(data.get('id'))
    except (TypeError, ValueError):
        return json_response(False, '缺少预警 id')
    result = (data.get('result') or '').strip() or '已处置'
    ok, msg = warn_service.handle_warning(warn_id, result=result,
                                          operator_id=session.get('user_id'))
    if ok:
        log_operation(session.get('username', ''), 'warn', 'handle',
                      f'到期预警[处置]：{warn_id}', detail=result)
    return json_response(ok, msg)


@warn_bp.route('/api/check', methods=['POST'])
@login_required
def api_check():
    """手动触发惰性巡检（与周期巡检共享 lazy_check 互斥锁，撞车立即 failed）。"""
    task = create_task('lazy_check', session.get('username', '') or 'system', '到期预警巡检')
    db.session.commit()
    run_async('lazy_check', task.task_id, run_lazy_check)
    return jsonify({'success': True, 'data': {'task_id': task.task_id}})


