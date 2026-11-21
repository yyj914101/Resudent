from . import db
from datetime import datetime


class GovernanceLedger(db.Model):
    """综治管理分类工作台账（特困走访 / 社会稳定风险 / 严重精神障碍 / 治保委员会）。

    配置驱动：category 区分台账类型，payload(JSON) 存各类型专属字段；
    id_card 接 R12 字段级加密（id_card_enc/id_card_hash），对齐 mediation/subsidy 先例。
    """
    __tablename__ = 'governance_ledgers'

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(40), nullable=False, index=True)
    subtype = db.Column(db.String(40), default='', index=True)
    name = db.Column(db.String(50), default='', index=True)
    id_card = db.Column(db.String(18))                            # R12 加密后原列置 NULL（无 default，避免 INSERT 写 ''）
    id_card_enc = db.Column(db.Text)                             # 字段级密文
    id_card_hash = db.Column(db.String(64))                      # SHA-256 等值检索
    phone = db.Column(db.String(30), default='')
    record_date = db.Column(db.Date, index=True)
    payload = db.Column(db.Text, default='{}')
    created_by = db.Column(db.String(50), default='')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
