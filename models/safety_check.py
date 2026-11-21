# -*- coding: utf-8 -*-
"""models/safety_check.py — 综合安全检查结构化记录（087，B-10）

「检查—隐患—整改」两表闭环：检查单 1:N 隐患行，整改状态追踪在隐患行。
- 不以档案归档代替结构化闭环（handoff 补丁 B-10 口径；动态台账设计 §十：一对多不进软表）。
- 结构唯一真源：scripts/migrations/m028_emergency_safety_check.sql（CREATE TABLE IF NOT EXISTS
  + 枚举/档案种子守卫）；新库由 create_all 建齐，本 SQL 仅服务存量库（m017 先例）。
- 整改状态机（应用层流转，非字典枚举——状态即行为）：
    待整改 → 待复核（整改人上报情况）→ 已闭环（复核通过）
                    ↑__________｜（复核驳回 → 回到待整改，留 review_note）
  检查单状态为派生值（闭环口径：无隐患或全部隐患已闭环），不落库，防双真源。
- 枚举收敛（检查类型/隐患等级）：dict_items 守卫种子（任务书约束），读取走 get_dict_items()。
"""
from datetime import datetime

from . import db

# 隐患整改状态机（唯一真源：本元组；顺序即流转方向）
HAZARD_STATUSES = ('待整改', '待复核', '已闭环')


class SafetyCheck(db.Model):
    """综合安全检查单（一次检查 = 一条记录，隐患为子行）。"""
    __tablename__ = 'safety_checks'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)          # 检查名称，如「五一节前消防安全检查」
    check_date = db.Column(db.Date, nullable=False)            # 检查日期
    check_type = db.Column(db.String(30), default='')          # 检查类型（dict_items: 综合安全检查类型）
    location = db.Column(db.String(200), default='')           # 检查范围/地点
    checker = db.Column(db.String(60), default='')             # 检查人/检查单位
    remark = db.Column(db.String(500), default='')             # 检查情况综述
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    hazards = db.relationship('SafetyCheckHazard', backref='check',
                              lazy='dynamic', cascade='all, delete-orphan',
                              order_by='SafetyCheckHazard.id')

    # ---- 派生状态（不落库：隐患行是唯一真源） ----
    @property
    def hazard_total(self):
        return self.hazards.count()

    @property
    def hazard_open(self):
        """未闭环隐患数（待整改 + 待复核）。"""
        return self.hazards.filter(SafetyCheckHazard.status != '已闭环').count()

    @property
    def status(self):
        """检查单派生状态：全部隐患闭环（或无隐患）→ 已闭环，否则 整改中。"""
        return '已闭环' if self.hazard_open == 0 else '整改中'


class SafetyCheckHazard(db.Model):
    """隐患行（闭环追踪的最小单元）。"""
    __tablename__ = 'safety_check_hazards'

    id = db.Column(db.Integer, primary_key=True)
    check_id = db.Column(db.Integer, db.ForeignKey('safety_checks.id'), nullable=False, index=True)
    description = db.Column(db.String(500), nullable=False)    # 隐患描述
    level = db.Column(db.String(20), default='一般')            # 隐患等级（dict_items: 安全隐患等级）
    location = db.Column(db.String(200), default='')           # 隐患位置
    rectify_owner = db.Column(db.String(60), default='')       # 整改责任人
    rectify_deadline = db.Column(db.Date)                      # 整改期限
    status = db.Column(db.String(10), nullable=False, default='待整改')  # 待整改|待复核|已闭环
    rectify_note = db.Column(db.String(500), default='')       # 整改情况（上报时填写）
    rectified_at = db.Column(db.DateTime)                      # 上报整改时间
    review_note = db.Column(db.String(500), default='')        # 复核意见（通过/驳回理由）
    reviewed_at = db.Column(db.DateTime)                       # 复核时间
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
