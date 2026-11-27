from . import db
from datetime import datetime

class OrganizationMember(db.Model):
    __tablename__ = 'organization_members'
    id = db.Column(db.Integer, primary_key=True)
    org_type = db.Column(db.String(50), nullable=False)  # 机构分类
    name = db.Column(db.String(50), nullable=False)
    position = db.Column(db.String(50))          # 职务
    id_card = db.Column(db.String(18))
    id_card_enc = db.Column(db.Text)              # R12 字段级加密（密文）
    id_card_hash = db.Column(db.String(64))       # SHA-256 等值检索
    phone = db.Column(db.String(120))
    phone_enc = db.Column(db.Text)
    phone_hash = db.Column(db.String(64))
    politics_status = db.Column(db.String(20))
    start_date = db.Column(db.Date)              # 任职开始日期
    end_date = db.Column(db.Date)                # 任职结束日期（空=现任）
    is_current = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
