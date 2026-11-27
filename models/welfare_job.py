from . import db
from datetime import datetime

class WelfareJob(db.Model):
    __tablename__ = 'welfare_jobs'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    village_group = db.Column(db.String(50))
    id_card = db.Column(db.String(18))
    id_card_enc = db.Column(db.Text)              # R12 字段级加密（密文）
    id_card_hash = db.Column(db.String(64))       # SHA-256 等值检索
    phone = db.Column(db.String(120))
    phone_enc = db.Column(db.Text)
    phone_hash = db.Column(db.String(64))
    job_type = db.Column(db.String(50))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    monthly_amount = db.Column(db.Float)
    bank_info = db.Column(db.String(100))
    bank_info_enc = db.Column(db.Text)
    bank_info_hash = db.Column(db.String(64))
    bank_type = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
