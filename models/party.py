from . import db
from datetime import datetime

class PartyMember(db.Model):
    __tablename__ = 'party_members'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    id_card = db.Column(db.String(18))
    id_card_enc = db.Column(db.Text)              # R12 字段级加密（密文）
    id_card_hash = db.Column(db.String(64))       # SHA-256 等值检索
    gender = db.Column(db.String(10))
    birth_date = db.Column(db.Date)
    nation = db.Column(db.String(20))
    education = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    phone_enc = db.Column(db.Text)
    phone_hash = db.Column(db.String(64))
    current_address = db.Column(db.String(200))
    join_date = db.Column(db.Date)
    confirm_date = db.Column(db.Date)
    member_type = db.Column(db.String(20))        # 历史列：080 起不再写入（拆分见 member_identity/member_mobility/employment_status/party_status）
    member_identity = db.Column(db.String(20))    # 党员身份：正式党员/预备党员（字典「党员身份」）
    member_mobility = db.Column(db.String(20))    # 党员流动状态：在家/流动（字典「党员流动状态」）
    employment_status = db.Column(db.String(20))  # 在职状态：在职/退休（字典「党员在职状态」）
    party_status = db.Column(db.String(20), default='在册')  # 在册状态：在册/已转出（字典「党员在册状态」）
    branch = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    fees = db.relationship('PartyFee', backref='member', lazy='dynamic',
                          cascade='all, delete-orphan')
    transfers = db.relationship('PartyTransfer', backref='member', lazy='dynamic',
                               cascade='all, delete-orphan', order_by='PartyTransfer.seq_no')
    positions = db.relationship('PartyPosition', backref='member', lazy='dynamic',
                               cascade='all, delete-orphan', order_by='PartyPosition.start_date.desc()')

class PartyFee(db.Model):
    __tablename__ = 'party_fees'
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('party_members.id'), nullable=False)
    fee_year = db.Column(db.Integer, nullable=False)
    fee_month = db.Column(db.Integer, nullable=False)
    monthly_income = db.Column(db.Float, default=0)    # 税后月收入
    amount = db.Column(db.Float, default=0)             # 月应缴党费（自动计算）
    paid_amount = db.Column(db.Float, default=0)        # 本月实缴
    status = db.Column(db.String(20), default='normal')  # normal/overdue/arrears
    created_at = db.Column(db.DateTime, default=datetime.now)

class PartyTransfer(db.Model):
    __tablename__ = 'party_transfers'
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('party_members.id'), nullable=False)
    seq_no = db.Column(db.Integer, default=1)
    transfer_type = db.Column(db.String(20))   # 转入/转出
    transfer_date = db.Column(db.Date)
    from_branch = db.Column(db.String(100))
    to_branch = db.Column(db.String(100))
    reason = db.Column(db.String(200))
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

class PartyPosition(db.Model):
    __tablename__ = 'party_positions'
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('party_members.id'), nullable=False)
    position_name = db.Column(db.String(100))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
