# -*- coding: utf-8 -*-
"""models/welfare_flex_subsidy.py — 084 就业服务：灵活就业补贴申报台账

一次申报一条记录；同一人同一批次同一补贴类型只允许一条（uq_flex_subsidy 防重复发放）。
- 结构唯一真源：scripts/migrations/m037_employment_service.py（CREATE TABLE IF NOT EXISTS
  + 唯一索引 + 字典种子守卫）；新库由 create_all 建齐。
- 申报状态为应用层流转（待申报 → 已申报 → 已审核 → 已发放 / 未通过），
  取值集合与 dict_items「补贴申报状态」同源（utils/dict_seed.DICT_SEEDS），
  状态即行为：流转校验在路由层，字典只做展示口径。
- 敏感字段接 R12 字段级加密：id_card 明文列加密后置 NULL，密文落 id_card_enc，
  等值检索走 id_card_hash。
- 金额用 Numeric(10,2) 而非 Float：SQLite 无 decimal 类型，SQLAlchemy 以文本存
  并用 Decimal 取回，避免补贴金额浮点累加误差（对账/汇总口径）。
"""
from datetime import datetime

from . import db


class WelfareFlexSubsidy(db.Model):
    """灵活就业补贴申报（084 就业服务）。"""
    __tablename__ = 'welfare_flex_subsidy'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)                # 申报人姓名
    id_card = db.Column(db.String(18))                            # R12 加密后原列置 NULL
    id_card_enc = db.Column(db.Text)                              # 身份证号密文
    id_card_hash = db.Column(db.String(64), index=True)           # SHA-256 等值检索
    resident_id = db.Column(db.Integer, index=True)               # 关联 resident.id（软关联）
    apply_year = db.Column(db.Integer, index=True, nullable=False)  # 申报年度（如 2026）
    batch_no = db.Column(db.String(30))                           # 批次号（镇/县下发批次）
    subsidy_type = db.Column(db.String(30), nullable=False)       # 补贴类型（dict: 灵活就业补贴类型）
    amount = db.Column(db.Numeric(10, 2))                         # 补贴金额（元，Decimal 存取）
    apply_status = db.Column(db.String(30), default='待申报', index=True)  # 申报状态（dict: 补贴申报状态）
    apply_date = db.Column(db.Date)                               # 申报日期
    review_date = db.Column(db.Date)                              # 审核日期
    issue_date = db.Column(db.Date)                               # 发放日期
    reject_reason = db.Column(db.String(200))                     # 未通过原因
    remark = db.Column(db.Text)                                   # 备注
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        # 同人 + 同年度 + 同补贴类型唯一：防重复申报/重复发放（批量导入去重依据）
        db.UniqueConstraint('id_card_hash', 'apply_year', 'subsidy_type', name='uq_flex_subsidy'),
        # 年度进度看板主查询（年度 + 状态）走覆盖前置列
        db.Index('idx_flex_year_status', 'apply_year', 'apply_status'),
    )
