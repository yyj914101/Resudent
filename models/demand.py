# -*- coding: utf-8 -*-
"""090A 民生诉求：受理主表 + 处理记录子表（固定表，ADR-028 三分法：有状态机 → 固定表）。

- 结构真源：scripts/migrations/m034_livelihood_demand.py（存量库）与 create_all（新库）；
- 字段口径：docs/design/090A-民生诉求-一页纸字段清单-模板-20261106.md v1.1
  （无业务单号、无办理期限、无答复、无满意度；主键自增 id）；
- 取值集合与 dict_items「诉求来源 / 诉求类别 / 诉求办理状态」同源
  （utils/dict_seed.DICT_SEEDS，诉求来源 value 编码见一页纸 D-01）；
- 敏感字段按 R12 双列口径：明文列置 NULL + ``*_enc`` 密文 + ``*_hash`` 等值检索
  （联系方式、身份证号；hash 非唯一——同一诉求人可多次诉求）；
- 状态机：待受理→已办结 / 待受理→不予受理（两跳；非法迁移 409，校验在路由层）。
"""
from datetime import datetime

from models import db


class Demand(db.Model):
    """民生诉求受理主表。"""

    __tablename__ = 'demand'

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(30), nullable=False, index=True)   # 诉求来源（dict: 诉求来源，value 编码）
    category = db.Column(db.String(30), nullable=False, index=True)  # 诉求类别（dict: 诉求类别）
    petitioner_name = db.Column(db.String(50), nullable=False)       # 诉求人姓名（PII-identity，展示打码）
    contact = db.Column(db.String(30))                               # 联系方式明文列（R12 加密后置 NULL）
    contact_enc = db.Column(db.Text)                                 # 联系方式密文
    contact_hash = db.Column(db.String(64), index=True)              # 联系方式等值检索（非唯一）
    id_card = db.Column(db.String(20))                               # 身份证明文列（R12 加密后置 NULL）
    id_card_enc = db.Column(db.Text)                                 # 身份证密文（列表/导出不展示）
    id_card_hash = db.Column(db.String(64), index=True)              # 身份证等值检索（非唯一）
    address = db.Column(db.String(200))                              # 住址/涉及地点（PII-identity，展示打码）
    summary = db.Column(db.Text, nullable=False)                     # 诉求内容摘要
    accept_date = db.Column(db.Date, nullable=False, index=True)     # 受理日期（默认当天）
    case_officer = db.Column(db.String(50))                          # 责任人/承办人
    status = db.Column(db.String(30), default='待受理', nullable=False, index=True)  # 办理状态（dict: 诉求办理状态）
    close_date = db.Column(db.Date)                                  # 办结日期（终态必填，≥ 受理日期）
    remark = db.Column(db.Text)                                      # 备注/不予受理依据（列表/导出不展示）
    created_by = db.Column(db.String(50))                            # 创建人（系统列）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    handles = db.relationship('DemandHandle', backref='demand', lazy='dynamic',
                              cascade='all, delete-orphan',
                              order_by='DemandHandle.id')


class DemandHandle(db.Model):
    """处理记录子表（只增不改；主表删除级联）。"""

    __tablename__ = 'demand_handle'

    id = db.Column(db.Integer, primary_key=True)
    demand_id = db.Column(db.Integer, db.ForeignKey('demand.id'), nullable=False, index=True)
    handle_date = db.Column(db.Date, nullable=False)                 # 处理日期（默认当天）
    handler = db.Column(db.String(50), nullable=False)               # 经办人
    action = db.Column(db.Text, nullable=False)                      # 处理措施/内容（约谈/转办/协调会等）
    created_at = db.Column(db.DateTime, default=datetime.now)
