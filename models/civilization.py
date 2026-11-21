# -*- coding: utf-8 -*-
"""models/civilization.py — 089 文明实践站：居民公约版本固定表

文明实践站数据层按 ADR-028 三分法：文明实践活动记录走软表
（civilization_activity，m031 已预置元数据行，字段由首次上传引导确认生成，
ADR-025 决策 3，**无固定模型**）；居民公约有「同一时刻仅一个现行」唯一性
约束（任务书工作项 1），落固定小表 civ_convention。

结构唯一真源：scripts/migrations/m040_civilization.py（CREATE TABLE IF NOT EXISTS
+ 部分唯一索引 + 字典种子守卫）；新库由 create_all 建齐（表体）+ m040 重放
（索引集合与存量库一致，m036 先例），本迁移仅服务存量库。

- 版本号/生效日期已按一页纸 v1.2 人工裁定删除（R-01/R-02）：版本历史按
  created_at 排序，「是否现行」由 is_current 标记，不建「公约状态」字典
  （Q-01 方案 A：is_current 唯一真源，展示文案由页面映射「现行/历史」）。
- 唯一现行约束 = m040 部分唯一索引（SQLite：CREATE UNIQUE INDEX ... WHERE
  is_current = 1）+ 应用层换版互斥（同事务把旧版本置历史）双保险；
  模型侧不声明该索引（R-093-01 双索引漂移纪律，索引唯一来源 m040 DDL）。
- 正文 content Text 兜底 + 附件引用为增强（一页纸 §8 默认口径）；
  附件正文走 092 module_attachment（module_key='civilization'），
  attachment_path 仅存引用，不落文件本体。
- 敏感字段：无（公约/活动记录均不采集身份证，无 R12 加密列）。
"""
from datetime import datetime

from . import db


class CivConvention(db.Model):
    """居民公约版本表（一次换版 = 新增一行；is_current 唯一现行）。"""
    __tablename__ = 'civ_convention'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)          # 标题（C-003，必填）
    content = db.Column(db.Text)                               # 正文（C-004，Text 兜底）
    attachment_path = db.Column(db.String(255))                # 附件引用（C-005，增强）
    is_current = db.Column(db.Boolean, nullable=False, default=False)  # 是否现行（C-006，唯一现行）
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
