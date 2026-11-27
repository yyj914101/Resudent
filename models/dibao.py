from . import db
from datetime import datetime

class Dibao(db.Model):
    __tablename__ = 'dibao'
    id = db.Column(db.Integer, primary_key=True)
    resident_id = db.Column(db.Integer, db.ForeignKey('residents.id'))

    # 民政模板字段（兼容导入）
    region = db.Column(db.String(50))                # 所属地区
    grid = db.Column(db.String(50), default='')                 # 所在网格
    household_name = db.Column(db.String(50))        # 户主姓名
    household_id_card = db.Column(db.String(18))     # 户主身份证号
    # R12 字段级加密列（对齐 Resident 口径；迁移脚本 m003 已建列，此处补齐 ORM 映射）
    household_id_card_enc = db.Column(db.Text)
    household_id_card_hash = db.Column(db.String(64))
    name = db.Column(db.String(50))                  # 成员姓名
    id_card = db.Column(db.String(18))               # 成员身份证号
    id_card_enc = db.Column(db.Text)
    id_card_hash = db.Column(db.String(64))
    is_head = db.Column(db.String(10))               # 是否户主
    relationship = db.Column(db.String(50))           # 与户主关系
    start_date = db.Column(db.Date)                  # 开始享受时间
    change_time = db.Column(db.Date)                 # 变更时间
    dibao_type = db.Column(db.String(20))            # 救助类别
    sub_category = db.Column(db.String(50))           # 救助细类
    beneficiary_count = db.Column(db.Integer, default=1)  # 享受人口数
    household_amount = db.Column(db.Float)           # 户救助金额
    payment_month = db.Column(db.String(20))         # 发放月份
    account_holder = db.Column(db.String(50))        # 开户人
    account_holder_identity = db.Column(db.String(50))  # 开户人身份
    account_holder_id_card = db.Column(db.String(18))  # 开户人身份证号
    account_holder_id_card_enc = db.Column(db.Text)
    account_holder_id_card_hash = db.Column(db.String(64))
    bank_card = db.Column(db.String(30))             # 银行账号
    bank_card_enc = db.Column(db.Text)
    bank_card_hash = db.Column(db.String(64))
    bank_type = db.Column(db.String(50))             # 开户银行种类
    address = db.Column(db.String(200))              # 家庭地址
    registered_address = db.Column(db.String(200))   # 户籍地址
    health_status = db.Column(db.String(20))         # 健康状况
    disability_type = db.Column(db.String(20))       # 残疾类型
    poverty_registered = db.Column(db.String(20))    # 建档立卡贫困对象
    is_deceased = db.Column(db.String(10))           # 是否死亡成员
    # 067 ADR-024②：保障状态业务最小集（已认定/复审中/待核实），徽章由真实字段渲染
    support_status = db.Column(db.String(10), default='已认定')
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    # R5-E1③（m002）：软失效
    status = db.Column(db.String(10), nullable=False, default='有效')
    invalid_reason = db.Column(db.String(100))

    resident = db.relationship('Resident', backref='dibao_records')

    @property
    def age(self):
        if self.resident and self.resident.birth_date:
            now = datetime.now()
            return now.year - self.resident.birth_date.year - \
                   ((now.month, now.day) < (self.resident.birth_date.month, self.resident.birth_date.day))
        return ''
