# -*- coding: utf-8 -*-
"""models/dynamic_table.py — 软表引擎元数据三表（078 交付物 1）

ADR-025：业务台账零物理 DDL —— 一张台账 = dynamic_table 一行，
一个字段 = dynamic_field 一行，一行数据 = dynamic_record 一行（整行 JSON）。

结构唯一真源：scripts/migrations/m024_dynamic_tables.sql（轨 A，幂等）。
本模型供 SQLAlchemy ORM 与 create_all 使用，字段与 m024 一一对应。

命名注意：表名用单数（dynamic_table / dynamic_field / dynamic_record），
与设计文档 §二 一致；字典表是复数（dict_categories / dict_items），勿混。
"""
from datetime import datetime

from . import db

DATA_TYPES = ('text', 'number', 'date', 'enum')
SENSITIVITY_LEVELS = ('none', 'identity', 'amount')


class DynamicTable(db.Model):
    """一张业务台账的元数据（不存在对应物理表）。"""

    __tablename__ = 'dynamic_table'

    id = db.Column(db.Integer, primary_key=True)
    # 表英文标识：仅 [a-z0-9_]，写入前过白名单（API-06 同源校验）
    code = db.Column(db.String(60), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    # 挂靠的固定蓝图/模块键（pension/shop/safety/governance/sanitation）
    module_key = db.Column(db.String(60), nullable=False)
    group_name = db.Column(db.String(60))
    description = db.Column(db.Text)
    # 首期统一 dynatable（丙方案）；二期表级权限再细分
    permission_key = db.Column(db.String(60), nullable=False,
                               default='dynatable', server_default='dynatable')
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class DynamicField(db.Model):
    """软表列定义（改表 = 改这里的行，不执行 ALTER TABLE）。"""

    __tablename__ = 'dynamic_field'
    __table_args__ = (
        db.UniqueConstraint('table_id', 'code', name='uq_dynamic_field_table_code'),
        db.Index('ix_dynamic_field_table', 'table_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    table_id = db.Column(db.Integer, nullable=False)
    # 字段标识：f00 起稳定自动序号，不做拼音、不需用户确认（ADR-025 决策 3）
    code = db.Column(db.String(60), nullable=False)
    display_name = db.Column(db.String(100), nullable=False)
    data_type = db.Column(db.String(10), nullable=False,
                          default='text', server_default='text')
    # enum 只存分类编码，运行时读 dict_items（单一真源，不落取值副本）
    dict_category_code = db.Column(db.String(100))
    sensitivity = db.Column(db.String(10), nullable=False,
                            default='none', server_default='none')
    is_required = db.Column(db.Boolean, nullable=False, default=False,
                            server_default=db.text('0'))
    sort_order = db.Column(db.Integer, nullable=False, default=0,
                           server_default=db.text('0'))
    created_at = db.Column(db.DateTime, default=datetime.now)


class DynamicRecord(db.Model):
    """一行业务数据：整行 JSON 存 data，identity 指纹存 digest。"""

    __tablename__ = 'dynamic_record'
    __table_args__ = (
        db.Index('ix_dynamic_record_table', 'table_id'),
        db.Index('ix_dynamic_record_table_created', 'table_id', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    table_id = db.Column(db.Integer, nullable=False)
    # {字段code: 值}；identity 字段值为 Fernet 密文，amount 明文
    data = db.Column(db.Text, nullable=False)
    # {字段code: HMAC-SHA256 指纹}，供 identity 等值/查重（不可范围/排序）
    digest = db.Column(db.Text)
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class SofttableBatchImport(db.Model):
    """107 软表批量导入批次总控（模式 A｜ADR-031；与 ElectionBatchImport 同构）。

    状态：pending → running → done / failed / aborted（进程重启由启动钩子标 aborted）。
    source_hash = sha256(文件内容)；判重键 = module_key + table_code + source_hash
    （106 §6.1：判重必须含模块与业务作用域——软表的作用域即目标台账 code）。
    R-093-01 纪律：模型侧不建 index=True，索引统一由 m045 建 idx_*。
    """
    __tablename__ = 'softtable_batch_import'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), nullable=False, unique=True)
    module_key = db.Column(db.String(60), nullable=False)
    table_code = db.Column(db.String(60), nullable=False)
    source_file = db.Column(db.String(200))
    source_path = db.Column(db.String(300))                     # temp/softtable/ 内路径
    source_hash = db.Column(db.String(64))                      # 防重复导入（sha256 文件内容）
    operator = db.Column(db.String(50))                         # 用户名（对齐 DynamicTable.created_by 口径）
    status = db.Column(db.String(10), nullable=False, default='pending')
    total_rows = db.Column(db.Integer, default=0)
    success_rows = db.Column(db.Integer, default=0)
    fail_rows = db.Column(db.Integer, default=0)
    task_id = db.Column(db.String(12))                          # A1 任务关联（异步轨迹对账）
    created_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)


class SofttableBatchError(db.Model):
    """107 软表批量导入失败行暂存：只存 数据行号 + 出错列名 + 原因，零原值落库。

    不存任何行内容（引擎 build_records 的行级错误本就不含原值），identity/金额
    等业务值绝不会明文落库或进导出——「敏感字段已打码，无明文」按构造成立。
    """
    __tablename__ = 'softtable_batch_error'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), db.ForeignKey('softtable_batch_import.batch_id'),
                         nullable=False)
    row_no = db.Column(db.Integer, nullable=False)              # 数据行号（与同步路径错误提示同口径，1 基）
    field_name = db.Column(db.String(100))
    reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.now)
