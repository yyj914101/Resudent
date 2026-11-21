from . import db
from datetime import datetime

class CareTarget(db.Model):
    __tablename__ = 'care_targets'
    id = db.Column(db.Integer, primary_key=True)
    resident_id = db.Column(db.Integer, db.ForeignKey('residents.id'))
    name = db.Column(db.String(50))
    care_type = db.Column(db.String(20))
    age = db.Column(db.Integer)
    guardian = db.Column(db.String(50))
    guardian_phone = db.Column(db.String(20))
    guardian_phone_enc = db.Column(db.Text)       # R12 字段级加密（密文）
    guardian_phone_hash = db.Column(db.String(64))  # SHA-256 等值检索
    parent_workplace = db.Column(db.String(100))
    school = db.Column(db.String(100))
    main_difficulty = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    # R2（m002）：八字段组 + 软失效
    target_type = db.Column(db.String(10))              # 老人/儿童
    elder_type = db.Column(db.String(10))               # 困难（高龄动态不落库）
    child_type = db.Column(db.String(10))               # 留守/困境（不设"一般"）
    source = db.Column(db.String(20))                   # 镇级名单/手工建档
    guardian_consent = db.Column(db.String(10))         # 已同意/待补录
    guardian_consent_time = db.Column(db.DateTime)
    guardian_consent_way = db.Column(db.String(20))
    difficulty_reason = db.Column(db.String(200))
    need = db.Column(db.String(200))
    age_class_override = db.Column(db.Text)             # JSON：override_class/reason/by/time
    status = db.Column(db.String(10), nullable=False, default='有效')
    invalid_reason = db.Column(db.String(100))

    resident = db.relationship('Resident', backref='care_records')

    # 儿童政策性入册类型（留守/困境；不设"一般"）
    POLICY_CHILD_TYPES = ('留守', '困境')

    @staticmethod
    def validate_child_entry(target_type, child_type):
        """儿童仅政策性入册校验（T13-3）。

        返回 None 表示合法；否则返回错误提示文案。
        儿童（target_type=='儿童'）且 child_type 非留守/困境 → 拒绝（不入册）。
        """
        if target_type == '儿童' and child_type not in CareTarget.POLICY_CHILD_TYPES:
            return '儿童仅留守/困境可政策性入册（<18 无认定不入册）'
        return None


class SpecialSupport(db.Model):
    __tablename__ = 'special_support'
    id = db.Column(db.Integer, primary_key=True)
    village_group = db.Column(db.String(50))           # 所在组
    household_name = db.Column(db.String(50))           # 户主姓名
    household_id_card = db.Column(db.String(18))        # 户主身份证号
    household_id_card_enc = db.Column(db.Text)          # R12 字段级加密（密文）
    household_id_card_hash = db.Column(db.String(64))   # SHA-256 等值检索
    beneficiary_count = db.Column(db.Integer, default=1) # 发放人数
    guarantee_amount = db.Column(db.Float)              # 保障金额
    support_mode = db.Column(db.String(50))             # 供养方式
    support_institution = db.Column(db.String(100))     # 供养机构
    start_date = db.Column(db.Date)                     # 开始享受时间
    change_time = db.Column(db.Date)                    # 变更时间
    bank_card = db.Column(db.String(30))                # 银行账号
    bank_card_enc = db.Column(db.Text)                  # R12 字段级加密（密文）
    bank_card_hash = db.Column(db.String(64))           # SHA-256 等值检索
    payment_month = db.Column(db.String(20))            # 发放月份
    account_holder = db.Column(db.String(50))           # 开户人
    account_holder_identity = db.Column(db.String(50))  # 开户人身份
    account_holder_id_card = db.Column(db.String(18))   # 开户人身份证号
    account_holder_id_card_enc = db.Column(db.Text)
    account_holder_id_card_hash = db.Column(db.String(64))
    bank_type = db.Column(db.String(50))                # 开户银行种类
    address = db.Column(db.String(200))                 # 家庭地址
    registered_address = db.Column(db.String(200))      # 户籍地址
    phone = db.Column(db.String(20))                    # 联系电话
    phone_enc = db.Column(db.Text)                      # R12 字段级加密（密文）
    phone_hash = db.Column(db.String(64))               # SHA-256 等值检索
    self_care_ability = db.Column(db.String(50))        # 自理能力
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    # R5-E1③（m002）：软失效
    status = db.Column(db.String(10), nullable=False, default='有效')
    invalid_reason = db.Column(db.String(100))

class UnsupportedChild(db.Model):
    __tablename__ = 'unsupported_children'
    id = db.Column(db.Integer, primary_key=True)
    village_group = db.Column(db.String(50))           # 所在组
    amount = db.Column(db.Float)                       # 发放金额（元）
    name = db.Column(db.String(50))                    # 儿童姓名
    id_card = db.Column(db.String(18))                 # 身份证号码
    id_card_enc = db.Column(db.Text)                   # R12 字段级加密（密文）
    id_card_hash = db.Column(db.String(64))            # SHA-256 等值检索
    gender = db.Column(db.String(10))                  # 性别
    birth_date = db.Column(db.Date)                    # 出生日期（用于算年龄）
    father_status = db.Column(db.String(100))          # 父亲情况
    mother_status = db.Column(db.String(100))          # 母亲情况
    health_status = db.Column(db.String(100))          # 儿童身体情况
    cardholder_relation = db.Column(db.String(50))     # 持卡人与本人关系
    phone = db.Column(db.String(20))                   # 联系电话
    phone_enc = db.Column(db.Text)                     # R12 字段级加密（密文）
    phone_hash = db.Column(db.String(64))              # SHA-256 等值检索
    address = db.Column(db.String(200))                # 家庭地址
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
