# -*- coding: utf-8 -*-
"""models/party_development.py — 083B 党员发展：五阶段名册主表 + 阶段迁移日志子表

五阶段（申请人→积极分子→发展对象→预备党员→正式党员）固定表承载
（立项规划 §9.4 裁决 + ADR-025「有流程不入软表」）。结构唯一真源：
scripts/migrations/m041_party_development.py（CREATE TABLE IF NOT EXISTS + 唯一索引
+ 字典种子守卫）；新库由 create_all 建齐，本迁移仅服务存量库（m032/m033 先例）。

- 阶段取值与 dict_items「党员发展阶段」同源（utils/dict_seed.DICT_SEEDS）；
  状态机在路由层（仅前滚一格，非法迁移 HTTP 409），字典只做展示口径，防双真源。
- id_card_hash 唯一（一人一条发展记录）；身份证/联系电话接 R12 字段级加密：
  明文列加密后置 NULL，密文落 *_enc，等值检索走 *_hash（对齐 party_members/governance_petition）。
- 转正回写（迁移到「正式党员」且显式确认）：仅允许改 party_members 的
  party_status 与 member_identity 两列（080 四维保护）；改前快照留日志子表
  writeback_before（验收 5 的 diff 断言 + 留痕口径，建议项 2 勘正：两列而非单列）。
- introducer（培养联系人/入党介绍人）单列自由文本；是否分列/拆子表为字段清单
  §5.3 遗留待确认项，多人需求出现时另建子表而非横向多列。
"""
from datetime import datetime

from . import db


class PartyDevelopment(db.Model):
    """党员发展名册主表（一名发展对象 = 一行，五阶段日期纵向列）。"""
    __tablename__ = 'party_development'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)               # 姓名
    id_card = db.Column(db.String(18))                            # 身份证号（加密后置 NULL）
    id_card_enc = db.Column(db.Text)                              # 身份证号密文
    id_card_hash = db.Column(db.String(64), index=True)           # 身份证号等值检索（唯一：一人一条，见 __table_args__）
    gender = db.Column(db.String(10))                             # 性别
    phone = db.Column(db.String(20))                              # 联系电话（加密后置 NULL）
    phone_enc = db.Column(db.Text)                                # 联系电话密文
    phone_hash = db.Column(db.String(64))                         # 联系电话等值检索（非唯一）
    branch = db.Column(db.String(100), index=True)                # 所属党支部（与表 1 同口径）
    stage = db.Column(db.String(30), nullable=False, default='申请人',
                      index=True)                                 # 当前阶段（dict: 党员发展阶段）
    apply_date = db.Column(db.Date)                               # 申请入党日期
    activist_date = db.Column(db.Date)                            # 积极分子确定日期
    develop_date = db.Column(db.Date)                             # 发展对象确定日期
    probate_date = db.Column(db.Date)                             # 预备党员日期
    full_member_date = db.Column(db.Date)                         # 转正日期
    introducer = db.Column(db.String(100))                        # 培养联系人 / 入党介绍人（单列，分列待确认）
    remark = db.Column(db.Text)                                   # 备注
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    # R3：一人一条发展记录（SQLite 唯一约束对 NULL 不生效，等价 m041 的部分唯一索引）
    __table_args__ = (
        db.UniqueConstraint('id_card_hash',
                            name='uq_party_development_id_card_hash'),
    )

    logs = db.relationship('PartyDevelopmentLog', backref='development',
                           lazy='dynamic', cascade='all, delete-orphan',
                           order_by='PartyDevelopmentLog.id')


class PartyDevelopmentLog(db.Model):
    """阶段迁移日志（只增不改）：每次前滚一行；转正回写留改前快照。"""
    __tablename__ = 'party_development_log'

    id = db.Column(db.Integer, primary_key=True)
    dev_id = db.Column(db.Integer, db.ForeignKey('party_development.id'),
                       nullable=False, index=True)                # 所属发展记录
    from_stage = db.Column(db.String(30), nullable=False)         # 迁移前阶段
    to_stage = db.Column(db.String(30), nullable=False)           # 迁移后阶段
    stage_date = db.Column(db.Date)                               # 该阶段节点日期
    operator = db.Column(db.String(50))                           # 操作人
    writeback_before = db.Column(db.Text)                         # 转正回写前快照（party_status/member_identity）
    created_at = db.Column(db.DateTime, default=datetime.now)     # 只增不改，无 updated_at
