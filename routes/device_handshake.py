# -*- coding: utf-8 -*-
"""设备握手端点（049）—— 客户端准入的唯一入口（ADR-016：PUBLIC_ENDPOINTS）。

协议（方案 §5）：
    首次：客户端 ──machine_code(+label)──▶ 服务机
          服务机：登记 pending + 签发凭证回传（客户端本地保存）→「等待审批」
    审批后：客户端 ──machine_code+credential──▶ 服务机
          服务机：验签 + 机器码比对 + 白名单 approved → 会话 token
    单机版：准入未启用 → status=disabled，客户端直接打开浏览器（旧行为）

安全边界（重要）：
- **approved 必须带凭证**：只报机器码不能换 token，否则知道机器码即可冒充已批准设备。
- pending 不回 token；revoked 直接 403。
- 凭证与表内 `sign` 逐字节比对：`reissue` 后旧凭证立即失效（不只是验签通过）。
"""
import json
from datetime import datetime

from flask import Blueprint, request

from models import db, DeviceRegistry
from utils.device_auth import (DeviceKeyCorruptedError, admission_enabled,
                              issue_credential, issue_session,
                              slim_credential, verify_credential)
from utils.helpers import json_response

device_handshake_bp = Blueprint('device_handshake', __name__)

MACHINE_LEN = 16
PENDING_LIMIT = 100


def _payload():
    """兼容 JSON（客户端）与 form（手工调试）两种提交。"""
    return request.get_json(silent=True) or request.form.to_dict() or {}


def _clean_machine(value):
    machine = (value or '').strip().upper()
    if len(machine) != MACHINE_LEN or any(c not in '0123456789ABCDEF' for c in machine):
        return None
    return machine


@device_handshake_bp.route('/status')
def status():
    """客户端探测：是否需要握手（单机版返回 disabled）。"""
    return json_response(True, data={'admission': admission_enabled()})


@device_handshake_bp.route('/handshake', methods=['POST'])
def handshake():
    if not admission_enabled():
        return json_response(True, data={'status': 'disabled',
                                         'msg': '当前为单机版，无需设备准入'})

    try:
        return _handshake()
    except DeviceKeyCorruptedError as exc:
        # R-050-03：密钥文件损坏时返回可读 JSON，客户端能提示恢复路径（不落通用 500）
        return json_response(False, str(exc))


def _handshake():
    payload = _payload()
    machine = _clean_machine(payload.get('machine_code'))
    if not machine:
        return json_response(False, '机器码格式不合法（应为 16 位十六进制）')

    supplied = payload.get('credential')
    if isinstance(supplied, str):
        try:
            supplied = json.loads(supplied)
        except ValueError:
            return json_response(False, '凭证格式错误（应为 JSON）')

    row = DeviceRegistry.query.filter_by(machine_code=machine).first()
    now = datetime.now()

    if row is None:
        # 首次登记：pending + 回传凭证（管理员审批后客户端凭此换 token）
        if DeviceRegistry.query.filter_by(status='pending').count() >= PENDING_LIMIT:
            return json_response(False, '待审批设备过多，请联系管理员清理')
        label = (payload.get('label') or '')[:100]
        cred = issue_credential(machine, label=label)
        row = DeviceRegistry(machine_code=machine, device_type='client',
                             credential=json.dumps(slim_credential(cred),    # GLB-41
                                                   ensure_ascii=False),
                             label=label, status='pending', expire='forever',
                             first_seen=now, last_seen=now)
        db.session.add(row)
        db.session.commit()
        return json_response(True, '已提交，等待管理员审批', data={
            'status': 'pending', 'machine_code': machine, 'credential': cred,
        })

    row.last_seen = now
    db.session.commit()

    if row.status == 'revoked':
        return json_response(False, '设备已被管理员撤销', data={'status': 'revoked'})
    if row.status != 'approved':
        return json_response(True, '等待管理员审批', data={
            'status': 'pending', 'machine_code': machine,
        })

    # approved：必须持有效凭证（只报机器码不放行）
    if not supplied:
        return json_response(False, '缺少设备凭证，请联系管理员重新签发')
    ok, msg = verify_credential(supplied, machine)
    if not ok:
        return json_response(False, msg)
    try:
        stored = json.loads(row.credential or '{}')
    except ValueError:
        stored = {}
    if stored.get('sign') != supplied.get('sign'):
        return json_response(False, '凭证已失效，请联系管理员重新签发')

    token = issue_session(machine)
    return json_response(True, '准入通过', data={
        'status': 'approved', 'token': token, 'machine_code': machine,
        'expire': supplied.get('expire') or row.expire or 'forever',
    })
