# -*- coding: utf-8 -*-
"""models.resident_sync_log — 主档变更副本同步日志（022）。

每次主档白名单字段变更触发副本回写时，逐字段留痕：
字段 / 旧值(脱敏) / 新值(脱敏) / 影响表(JSON 表→行数) / 状态 / 操作人 / 时间。
脱敏红线：旧/新值不落明文（phone/bank_card 经 utils.masking 打码）。
"""
from . import db
from datetime import datetime
from sqlalchemy import text


class ResidentSyncLog(db.Model):
    __tablename__ = 'resident_sync_log'
    id = db.Column(db.Integer, primary_key=True)
    # 索引统一走 __table_args__ 命名索引（审阅修复 P3-4：去掉 index=True 防同列双索引）
    resident_id = db.Column(db.Integer)               # 主档居民 id
    field = db.Column(db.String(30))                  # 主档字段名（白名单内）
    old_value = db.Column(db.String(100))             # 旧值（敏感字段脱敏）
    new_value = db.Column(db.String(100))             # 新值（敏感字段脱敏）
    affected_tables = db.Column(db.Text)              # JSON：{表: 该字段实际影响行数}
    # 口径对齐 m010 DDL（审阅修复 P3-5）：NOT NULL + 服务端默认
    status = db.Column(db.String(10), nullable=False, default='success',
                       server_default='success')      # success/failed
    error_message = db.Column(db.String(200))         # 失败原因（截断 200，审阅修复 2026-10-12）
    operator = db.Column(db.String(50))               # 操作人
    created_at = db.Column(db.DateTime, default=datetime.now,
                           server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        db.Index('idx_resident_sync_log_rid', 'resident_id'),
        db.Index('idx_resident_sync_log_field', 'field'),
    )
