# -*- coding: utf-8 -*-
"""models/safety.py — 安全工作模块档案目录树（030 交付物 2）

SafetyNode 单表同构树：folder（目录）/ file（文件）共用一张表，
parent_id 表达无限层级（NULL=根）；SQLite 单写者 + WAL 沿用全局连接约定。

结构唯一真源：scripts/migrations/m012_safety_nodes.sql（本表 DDL + 20 类种子）；
本模型供 SQLAlchemy ORM 与 create_all 使用，字段与 m012 一一对应。
"""
from datetime import datetime

from . import db


class SafetyNode(db.Model):
    __tablename__ = 'safety_nodes'

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, index=True)  # NULL=根；应用层保证不存在悬空引用
    name = db.Column(db.String(120), nullable=False)
    node_type = db.Column(db.String(10), nullable=False, default='folder')  # folder | file
    sort_order = db.Column(db.Integer, default=0)
    # ---- 以下字段仅 file 使用 ----
    stored_name = db.Column(db.String(80))     # UUID 落盘名（uploads/safety/{uuid}.{ext}）
    original_name = db.Column(db.String(200))  # 原名（展示/下载用；secure_filename 清洗后）
    ext = db.Column(db.String(20))
    size = db.Column(db.Integer, default=0)
    mime = db.Column(db.String(100))
    # ------------------------------------
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)