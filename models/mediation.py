from . import db
from datetime import datetime

class Mediation(db.Model):
    __tablename__ = 'mediations'
    id = db.Column(db.Integer, primary_key=True)
    case_no = db.Column(db.String(50), unique=True)
    dispute_type = db.Column(db.String(50))
    location = db.Column(db.String(200))
    applicant = db.Column(db.String(50))
    applicant_id_card = db.Column(db.String(18))
    applicant_id_card_enc = db.Column(db.Text)    # R12 字段级加密（密文）
    applicant_id_card_hash = db.Column(db.String(64))  # SHA-256 等值检索
    applicant_village_group = db.Column(db.String(50))
    respondent = db.Column(db.String(50))
    respondent_id_card = db.Column(db.String(18))
    respondent_id_card_enc = db.Column(db.Text)
    respondent_id_card_hash = db.Column(db.String(64))
    people_count = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='mediating')
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    mediator = db.Column(db.String(50))
    mediation_date = db.Column(db.Date)
    content = db.Column(db.Text)
    result = db.Column(db.Text)
    return_reason = db.Column(db.Text)
    attachments = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
