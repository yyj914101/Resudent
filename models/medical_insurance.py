from . import db
from datetime import datetime

class MedicalInsurance(db.Model):
    __tablename__ = 'medical_insurance_payments'
    id = db.Column(db.Integer, primary_key=True)
    seq_no = db.Column(db.Integer)
    name = db.Column(db.String(50), index=True)
    id_card = db.Column(db.String(18), index=True)
    id_card_enc = db.Column(db.Text)              # R12 字段级加密（密文）
    id_card_hash = db.Column(db.String(64))       # SHA-256 等值检索
    gender = db.Column(db.String(10))
    address = db.Column(db.String(200))
    village_group = db.Column(db.String(50), index=True)
    contact_phone = db.Column(db.String(120))
    contact_phone_enc = db.Column(db.Text)
    contact_phone_hash = db.Column(db.String(64))
    mobile_phone = db.Column(db.String(120))
    mobile_phone_enc = db.Column(db.Text)
    mobile_phone_hash = db.Column(db.String(64))
    payment_year = db.Column(db.Integer, index=True)
    household_name = db.Column(db.String(50))
    household_property = db.Column(db.String(50))
    insurance_status = db.Column(db.String(50))
    is_paid = db.Column(db.String(10), default='\u5426')
    is_state_paid = db.Column(db.String(10), default='\u5426')
    state_paid_identity = db.Column(db.String(100))
    is_deductible = db.Column(db.String(10), default='\u5426')
    deduction_reason = db.Column(db.String(200))
    is_relocated = db.Column(db.String(10), default='\u5426')
    data_source = db.Column(db.String(30), default='\u533b\u4fdd\u4e0b\u53d1')
    resident_id = db.Column(db.Integer, index=True)
    link_status = db.Column(db.String(30), default='\u672a\u5173\u8054\u5c45\u6c11')
    import_batch = db.Column(db.String(100))
    verify_status = db.Column(db.String(30), default='\u672a\u6838\u5bf9')
    benefit_cert_status = db.Column(db.String(30), default='未认证')  # 082 B-1：医保待遇认证状态（与核对状态/参保情况分离）
    exception_note = db.Column(db.String(300))
    remark = db.Column(db.Text)
    insurance_type = db.Column(db.String(20), default='resident', index=True)  # 054-B：居民/职工/其他
    ext_data = db.Column(db.Text)  # 054-B：职工医保/其他专属字段 JSON（字段目录驱动，不硬编码列）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.Index('idx_medical_year_group', 'payment_year', 'village_group'),
        db.Index('idx_medical_year_paid', 'payment_year', 'is_paid'),
    )
