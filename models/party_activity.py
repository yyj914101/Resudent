# -*- coding: utf-8 -*-
"""models/party_activity.py — 083A 党务活动台账：活动主表 + 民主评议逐人子表

三类活动（党员教育 / 组织生活 / 党风廉政，立项规划 §三.083A「三合一」）固定单表
多类型承载（§9.4 裁决：不走软表——三类同构且需按支部统计）。民主评议不占类型
枚举，逐人等次结果落子表（颗粒度拍板：逐人）。

结构唯一真源：scripts/migrations/m032_party_activity.py（CREATE TABLE IF NOT EXISTS
+ 索引 + 字典种子守卫）；新库由 create_all 建齐，本迁移仅服务存量库（m033 先例）。

- 活动类型取值与 dict_items「党务活动类型」同源（utils/dict_seed.DICT_SEEDS），
  路由层只认字典校验，防双真源。
- 民主评议等次取值与 dict_items「民主评议等次」同源；等次候选值【待确认-上级口径】
  （党务活动不在字段清单 v1 的 12 表内，按 §5.2 缺口流程先以党内通行四等次落地，
  上级报表到位后走 §六 流程收敛）。
- 评议子表 (year, party_member_id) 唯一（任务书交付物；未获放宽裁决前按唯一落地，
  重复提交走 upsert 更新等次，不产生重复行）。party_members 零改动（复用 080 四维）。
- 敏感字段：不采集主持人身份证；不采集联系电话（任务书约束，无 R12 加密列）。
"""
from datetime import datetime

from . import db


class PartyActivity(db.Model):
    """党务活动主表（一次活动 = 一条记录；类型区分三类台账）。"""
    __tablename__ = 'party_activity'

    id = db.Column(db.Integer, primary_key=True)
    activity_type = db.Column(db.String(30), nullable=False, index=True)  # 活动类型（dict: 党务活动类型）
    title = db.Column(db.String(100), nullable=False)                     # 活动主题
    activity_date = db.Column(db.Date, nullable=False, index=True)        # 活动日期（年度聚合口径列）
    host = db.Column(db.String(50))                                       # 主持人（不采集身份证）
    expected_count = db.Column(db.Integer, default=0)                     # 应到人数（非负整数）
    actual_count = db.Column(db.Integer, default=0)                       # 实到人数（非负整数；缺席合法，实到≤应到不强制）
    branch = db.Column(db.String(100), index=True)                        # 支部（按支部统计口径列）
    content = db.Column(db.Text)                                          # 活动内容
    attachment_path = db.Column(db.String(200))                           # 附件路径（占位：092 模板/附件统一承载）
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class PartyActivityReview(db.Model):
    """民主评议逐人结果（年度 + 党员 一条等次记录，upsert 更新不重复）。"""
    __tablename__ = 'party_activity_review'
    __table_args__ = (
        db.UniqueConstraint('year', 'party_member_id', name='uq_party_review_year_member'),
    )

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False, index=True)              # 评议年度
    party_member_id = db.Column(db.Integer, db.ForeignKey('party_members.id'),
                                nullable=False, index=True)               # 党员（party_members 在册名单选择）
    grade = db.Column(db.String(30), nullable=False)                      # 评议等次（dict: 民主评议等次，【待确认-上级口径】）
    review_date = db.Column(db.Date)                                      # 评议日期
    remark = db.Column(db.Text)
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    member = db.relationship('PartyMember', backref='activity_reviews')
