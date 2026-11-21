# -*- coding: utf-8 -*-
"""models/governance_petition.py — 086 信访维稳：信访受理主表 + 办理记录子表

「受理—办理—办结」两表闭环：信访件 1:N 办理记录，状态与办结日期落在主表。
- 不以档案归档代替结构化闭环（一对多不进软表，动态台账设计 §十.1 口径）。
- 结构唯一真源：scripts/migrations/m033_governance_petition.py（CREATE TABLE IF NOT EXISTS
  + 索引 + 字典种子守卫）；新库由 create_all 建齐，本迁移仅服务存量库（m028 先例）。
- 办理状态机（应用层流转，状态即行为）：
    待受理 → 办理中 → 已办结
              ↘ 不予受理（不受理须在 remark 写明依据）
  取值集合与 dict_items「信访办理状态」同源（utils/dict_seed.DICT_SEEDS），
  流转校验在路由层，字典只做展示口径，防双真源。
- 敏感字段接 R12 字段级加密：信访人 contact / id_card 明文列加密后置 NULL，
  密文落 *_enc，等值检索走 *_hash（对齐 governance / shop / mediation 先例）。
  注意：contact_hash / id_card_hash 不加 unique —— 同一人可多次来信来访。
"""
from datetime import datetime

from . import db


class GovernancePetition(db.Model):
    """信访件受理主表（一个信访件 = 一条记录，办理过程为子行）。"""
    __tablename__ = 'governance_petition'

    id = db.Column(db.Integer, primary_key=True)
    petition_no = db.Column(db.String(30), nullable=False, unique=True, index=True)  # 信访编号（业务单号，如 XF2026-0001）
    source = db.Column(db.String(30))                             # 信访来源（dict: 信访来源）
    petitioner_name = db.Column(db.String(50), nullable=False)    # 信访人姓名
    contact = db.Column(db.String(30))                            # 联系方式（加密后置 NULL）
    contact_enc = db.Column(db.Text)                              # 联系方式密文
    contact_hash = db.Column(db.String(64))                       # 联系方式等值检索（非唯一）
    id_card = db.Column(db.String(18))                            # 身份证号（加密后置 NULL）
    id_card_enc = db.Column(db.Text)                              # 身份证号密文
    id_card_hash = db.Column(db.String(64))                       # 身份证号等值检索（非唯一：可重复来访）
    address = db.Column(db.String(200))                           # 住址/涉及地点
    category = db.Column(db.String(30), index=True)               # 诉求类别（dict: 信访诉求类别）
    summary = db.Column(db.Text)                                  # 诉求摘要
    accept_date = db.Column(db.Date, index=True)                  # 受理日期（超期预警与月报口径列）
    due_date = db.Column(db.Date)                                 # 办理期限（受理日 + 法定/约定天数）
    case_officer = db.Column(db.String(50))                       # 承办人
    status = db.Column(db.String(30), default='待受理', index=True)  # 办理状态（dict: 信访办理状态）
    close_date = db.Column(db.Date)                               # 办结日期
    reply = db.Column(db.Text)                                    # 答复意见
    satisfaction = db.Column(db.String(30))                       # 满意度（dict: 信访满意度）
    remark = db.Column(db.Text)                                   # 备注（不予受理依据等）
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    handles = db.relationship('GovernancePetitionHandle', backref='petition',
                              lazy='dynamic', cascade='all, delete-orphan',
                              order_by='GovernancePetitionHandle.id')


class GovernancePetitionHandle(db.Model):
    """信访件办理记录（承办过程留痕的最小单元，只增不改）。"""
    __tablename__ = 'governance_petition_handle'

    id = db.Column(db.Integer, primary_key=True)
    petition_id = db.Column(db.Integer, db.ForeignKey('governance_petition.id'),
                            nullable=False, index=True)            # 所属信访件
    handle_date = db.Column(db.Date)                              # 办理日期
    handler = db.Column(db.String(50))                            # 经办人
    action = db.Column(db.Text)                                   # 办理措施（约谈/转办/协调会等）
    result = db.Column(db.Text)                                   # 阶段结果
    next_plan = db.Column(db.Text)                                # 下一步计划
    created_at = db.Column(db.DateTime, default=datetime.now)     # 记录写入时间（子表只增不改，无 updated_at）
