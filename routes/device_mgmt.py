# -*- coding: utf-8 -*-
"""设备管理后台（049）—— 白名单录入/审批/撤销/重新签发（ADR-016：ADMIN_BLUEPRINTS）。

审批语义（方案 §9 拍板 2）：
    pending（首次握手自动登记）→ approve → approved（可换 token）
    approved → revoke → revoked（立即清会话，旧 token 当场失效）
    reissue：重新签发凭证并清会话——旧凭证的 sign 与库内不再一致，握手即被拒。

大屏隔离（050 R-050-01/02）：`device_type='screen'` 行的凭证属 /screen-admin，
本页 reissue 对其直接拒绝（Ed25519 凭证会覆盖大屏 token）；徽章计数只统计客户机。
"""
import json
from datetime import datetime

from flask import Blueprint, render_template, session

from models import db, DeviceRegistry
from utils.decorators import admin_required, login_required
from utils.device_auth import (DeviceKeyCorruptedError, drop_sessions_for,
                              issue_credential, slim_credential)
from utils.helpers import json_response, log_operation

device_mgmt_bp = Blueprint('device_mgmt', __name__)


@device_mgmt_bp.route('/')
@login_required
@admin_required
def index():
    return render_template('device/list.html')


@device_mgmt_bp.route('/api/list')
@login_required
@admin_required
def api_list():
    rows = DeviceRegistry.query.order_by(DeviceRegistry.created_at.desc(),
                                         DeviceRegistry.id.desc()).all()
    counts = {'pending': 0, 'approved': 0, 'revoked': 0}
    items = []
    for row in rows:
        # R-050-02：三态徽章只统计客户机（大屏行照常列出，但其 approved 由大屏管理页解释）
        if (row.device_type or 'client') != 'screen':
            counts[row.status or 'pending'] = counts.get(row.status or 'pending', 0) + 1
        items.append(row.to_dict())
    return json_response(True, data={'items': items, 'counts': counts})


def _row(device_id):
    return DeviceRegistry.query.get(device_id)


def _audit(action, row, detail):
    log_operation(session.get('username', ''), '设备准入', action,
                  f'{detail}（机器码 {row.machine_code}）', result='success')


@device_mgmt_bp.route('/api/<int:device_id>/approve', methods=['POST'])
@login_required
@admin_required
def api_approve(device_id):
    row = _row(device_id)
    if not row:
        return json_response(False, '设备不存在')
    row.status = 'approved'
    row.last_seen = datetime.now()
    db.session.commit()
    _audit('edit', row, '批准设备准入')
    return json_response(True, '已批准', data={'item': row.to_dict()})


@device_mgmt_bp.route('/api/<int:device_id>/revoke', methods=['POST'])
@login_required
@admin_required
def api_revoke(device_id):
    row = _row(device_id)
    if not row:
        return json_response(False, '设备不存在')
    row.status = 'revoked'
    db.session.commit()
    dropped = drop_sessions_for(row.machine_code)
    _audit('edit', row, f'撤销设备准入（清会话 {dropped} 个）')
    return json_response(True, '已撤销，该设备会话已立即失效', data={'item': row.to_dict()})


@device_mgmt_bp.route('/api/<int:device_id>/reissue', methods=['POST'])
@login_required
@admin_required
def api_reissue(device_id):
    row = _row(device_id)
    if not row:
        return json_response(False, '设备不存在')
    if row.device_type == 'screen':                # R-050-01：凭证覆盖会清掉大屏 token
        return json_response(False, '大屏凭证请在「大屏管理」页轮换（本页签发会破坏大屏数据）')
    try:
        cred = issue_credential(row.machine_code, label=row.label or '', expire=row.expire or 'forever')
    except DeviceKeyCorruptedError as exc:         # R-050-03：可读 JSON，不落 500
        return json_response(False, str(exc))
    row.credential = json.dumps(slim_credential(cred), ensure_ascii=False)   # GLB-41
    row.status = 'approved'
    db.session.commit()
    dropped = drop_sessions_for(row.machine_code)
    _audit('edit', row, f'重新签发凭证（清会话 {dropped} 个）')
    return json_response(True, '已重新签发，请把凭证发给该设备', data={
        'item': row.to_dict(), 'credential': cred,
    })


@device_mgmt_bp.route('/api/<int:device_id>', methods=['DELETE'])
@login_required
@admin_required
def api_delete(device_id):
    row = _row(device_id)
    if not row:
        return json_response(False, '设备不存在')
    machine = row.machine_code
    db.session.delete(row)
    db.session.commit()
    dropped = drop_sessions_for(machine)
    log_operation(session.get('username', ''), '设备准入', 'delete',
                  f'删除设备记录（机器码 {machine}，清会话 {dropped} 个）', result='success')
    return json_response(True, '已删除（该设备下次握手将重新登记待审批）')
