from . import db
from datetime import datetime

class Veteran(db.Model):
    __tablename__ = 'veteran'
    id = db.Column(db.Integer, primary_key=True)
    # 1. 序号 - auto PK
    # 2. 姓名
    name = db.Column(db.String(50), nullable=False, index=True)
    # 3. 身份证号码（R12 整改：唯一约束迁移至 id_card_hash，原列迁移后置 NULL）
    id_card = db.Column(db.String(18), index=True)
    id_card_enc = db.Column(db.Text)
    id_card_hash = db.Column(db.String(64), unique=True)
    # 4. 性别
    gender = db.Column(db.String(4))
    # 5. 民族
    nation = db.Column(db.String(20), default='汉族')
    # 6. 政治面貌
    politics_status = db.Column(db.String(20), default='群众')
    # 7. 文化程度
    education = db.Column(db.String(20))
    # 8. 户口所在地
    registered_residence = db.Column(db.String(200))
    # 9. 家庭详细地址
    home_address = db.Column(db.String(200))
    # 9a. 所在网格
    grid = db.Column(db.String(50), default='')
    # 10. 本人联系方式
    phone = db.Column(db.String(20))
    phone_enc = db.Column(db.Text)
    phone_hash = db.Column(db.String(64))
    # 11. 主要亲属姓名
    relative_name = db.Column(db.String(50))
    # 12. 主要亲属联系方式
    relative_phone = db.Column(db.String(20))
    relative_phone_enc = db.Column(db.Text)
    relative_phone_hash = db.Column(db.String(64))
    # 13. 入伍年月
    enlist_date = db.Column(db.Date)
    # 14. 退役年月
    discharge_date = db.Column(db.Date)
    # 15. 服役年限
    service_years = db.Column(db.Integer)
    # 16. 原服役部队
    service_unit = db.Column(db.String(200))
    # 17. 原服役兵种
    service_branch = db.Column(db.String(30))
    # 18. 原服役职务/军衔
    service_rank = db.Column(db.String(50))
    # 19. 退伍证件编号
    discharge_cert_no = db.Column(db.String(50))
    # 20. 户籍性质
    household_reg_type = db.Column(db.String(20))
    # 21. 安置情况
    placement_status = db.Column(db.String(20))
    key_group_category = db.Column(db.String(40))  # 082 B-3：退役重点群体类别（空=非重点群体）
    retire_category = db.Column(db.String(30))  # 093 B-8：退役类别（独立字段，不复用兵种/安置情况）
    # 22. 备注
    remark = db.Column(db.Text)
    # System fields
    serial_no = db.Column(db.Integer)  # 序号
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
