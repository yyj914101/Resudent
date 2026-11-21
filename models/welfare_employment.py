# -*- coding: utf-8 -*-
"""models/welfare_employment.py — 084 就业服务：就业状态登记台账

一人一条就业状态记录（同一人多次登记按变更日期留多行，最新 update_date 为当前状态）。
- 结构唯一真源：scripts/migrations/m037_employment_service.py（CREATE TABLE IF NOT EXISTS
  + 索引 + 字典种子守卫）；新库由 create_all 建齐，本迁移仅服务存量库（m028 先例）。
- 敏感字段接 R12 字段级加密：id_card / phone 明文列加密后置 NULL，
  密文落 *_enc，等值检索走 *_hash（对齐 governance / shop / mediation 先例）。
- 枚举收敛（就业状态 / 就业类型 / 就业行业）：读取走 utils.dict_enum.get_dict_items，
  字面量唯一真源 utils/dict_seed.DICT_SEEDS，本文件不重复枚举。
"""
from datetime import datetime

from . import db


class WelfareEmployment(db.Model):
    """就业状态登记（084 就业服务）。"""
    __tablename__ = 'welfare_employment'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)                # 姓名
    id_card = db.Column(db.String(18))                            # R12 加密后原列置 NULL（无 default，避免 INSERT 写 ''）
    id_card_enc = db.Column(db.Text)                              # 身份证号密文
    id_card_hash = db.Column(db.String(64), unique=True, index=True)  # SHA-256 等值检索
    gender = db.Column(db.String(10))                             # 性别
    village_group = db.Column(db.String(50), index=True)          # 所属村组（按组统计口径）
    phone = db.Column(db.String(30))                              # 联系电话（加密后置 NULL）
    phone_enc = db.Column(db.Text)                                # 联系电话密文
    phone_hash = db.Column(db.String(64))                         # 电话等值检索（非唯一：家庭共号）
    employment_status = db.Column(db.String(30), index=True)      # 就业状态（dict: 就业状态）
    employment_type = db.Column(db.String(30))                    # 就业类型（dict: 就业类型）
    employer = db.Column(db.String(100))                          # 用工单位/经营主体
    industry = db.Column(db.String(30))                           # 所属行业（dict: 就业行业）
    work_location = db.Column(db.String(100))                     # 务工地点（本地/县外/省外）
    start_date = db.Column(db.Date)                               # 就业（或失业）起始日期
    update_date = db.Column(db.Date)                              # 状态更新日期（前端「统计截止」口径）
    resident_id = db.Column(db.Integer, index=True)               # 关联 resident.id（软关联，不加 FK：存量台账可无户籍）
    remark = db.Column(db.Text)                                   # 备注
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
