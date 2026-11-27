# -*- coding: utf-8 -*-
"""models/domain_batch.py — 通用域异步批量导入批次总控与失败行暂存（m047）。

供医保参保管理（medical）与民政对象管理（dibao / disabled / care_special /
care_child / care_target / welfare_job）共用，避免为每个固定大表模块复制平行批次表。
失败行表只存 1 基行号 + 出错列名 + 脱敏原因，绝不落身份证/银行卡明文。
"""
from datetime import datetime

from . import db


class DomainBatchImport(db.Model):
    __tablename__ = 'domain_batch_import'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), nullable=False, unique=True)
    domain = db.Column(db.String(40), nullable=False)
    scope_key = db.Column(db.String(100), nullable=False, default='')
    source_file = db.Column(db.String(200))
    source_path = db.Column(db.String(300))
    source_hash = db.Column(db.String(64))
    mode = db.Column(db.String(20), nullable=False, default='append')
    operator = db.Column(db.String(50))
    status = db.Column(db.String(12), nullable=False, default='pending')
    total_rows = db.Column(db.Integer, default=0)
    inserted_rows = db.Column(db.Integer, default=0)
    updated_rows = db.Column(db.Integer, default=0)
    skip_rows = db.Column(db.Integer, default=0)
    fail_rows = db.Column(db.Integer, default=0)
    task_id = db.Column(db.String(16))
    created_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)


class DomainBatchError(db.Model):
    """失败行暂存：零敏感原值落库，仅存行号、列名与脱敏原因。"""
    __tablename__ = 'domain_batch_error'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), db.ForeignKey('domain_batch_import.batch_id'),
                         nullable=False)
    row_no = db.Column(db.Integer, nullable=False)
    field_name = db.Column(db.String(100))
    reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.now)
