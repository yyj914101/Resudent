from . import db
from datetime import datetime

class Disabled(db.Model):
    __tablename__ = 'disabled_persons'
    id = db.Column(db.Integer, primary_key=True)
    resident_id = db.Column(db.Integer, db.ForeignKey('residents.id'))
    # 基本信息
    village_group = db.Column(db.String(50))       # 所在组
    name = db.Column(db.String(50))                 # 姓名
    id_card = db.Column(db.String(18))              # 身份证号
    id_card_enc = db.Column(db.Text)                # R12 字段级加密（密文）
    id_card_hash = db.Column(db.String(64))         # SHA-256 等值检索
    gender = db.Column(db.String(10))               # 性别
    nation = db.Column(db.String(20))               # 民族
    education = db.Column(db.String(20))            # 文化程度
    phone = db.Column(db.String(120))                # 手机
    phone_enc = db.Column(db.Text)
    phone_hash = db.Column(db.String(64))
    registered_address = db.Column(db.String(200))  # 户籍地地址
    # 监护人
    guardian_name = db.Column(db.String(50))        # 监护人姓名
    guardian_relation = db.Column(db.String(50))    # 监护人关系
    guardian_phone = db.Column(db.String(120))       # 监护人手机
    guardian_phone_enc = db.Column(db.Text)
    guardian_phone_hash = db.Column(db.String(64))
    # 残疾信息
    cert_no = db.Column(db.String(30))              # 残疾证号
    disability_type = db.Column(db.String(20))      # 残疾类别
    disability_level = db.Column(db.String(20))     # 残疾等级
    disability_detail = db.Column(db.String(200))   # 残疾详情
    # 补贴信息
    subsidy_living = db.Column(db.Float)            # 生活补贴
    subsidy_nursing = db.Column(db.Float)           # 护理补贴
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    # R5-E1③（m002）：软失效
    status = db.Column(db.String(10), nullable=False, default='有效')
    invalid_reason = db.Column(db.String(100))

    resident = db.relationship('Resident', backref='disabled_records')
