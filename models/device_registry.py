# -*- coding: utf-8 -*-
"""设备准入白名单（049）。

设计要点（方案 §4.4）：
- 一行 = 一台设备（客户机 / 大屏）；`machine_code` 唯一。
- `credential` 存服务机签发的 Ed25519 凭证 JSON（含 sign），客户端侧另存一份。
- 状态机：pending（首次登记待审批）→ approved（放行）→ revoked（立即失效）。
- 大屏（device_type='screen'）无机器码，`machine_code` 存 token 哈希占位。

DDL 走迁移脚本 scripts/migrations/m019_device_registry.sql（轨 A，幂等）。
"""
from datetime import datetime

from . import db


class DeviceRegistry(db.Model):
    __tablename__ = 'device_registry'

    id = db.Column(db.Integer, primary_key=True)
    machine_code = db.Column(db.String(16), nullable=False, unique=True, index=True)
    device_type = db.Column(db.String(10), default='client')      # client / screen
    credential = db.Column(db.Text)                               # 凭证 JSON（含 sign）
    label = db.Column(db.String(100))
    status = db.Column(db.String(20), default='pending', index=True)
    expire = db.Column(db.String(20), default='forever')
    first_seen = db.Column(db.DateTime)
    last_seen = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'machine_code': self.machine_code,
            'device_type': self.device_type or 'client',
            'label': self.label or '',
            'status': self.status or 'pending',
            'expire': self.expire or 'forever',
            'first_seen': self.first_seen.strftime('%Y-%m-%d %H:%M:%S') if self.first_seen else '',
            'last_seen': self.last_seen.strftime('%Y-%m-%d %H:%M:%S') if self.last_seen else '',
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else '',
        }
