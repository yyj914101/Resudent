from . import db
from datetime import datetime


class Shop(db.Model):
    """店铺信息台账（居民管理组）：店铺名称、位置（街道名+门牌号）、商户姓名、身份证号、电话。

    身份证号接 R12 字段级加密（id_card_enc/id_card_hash），消除明文孤岛；
    电话为非敏感业务字段，保持明文（对齐方案 §6）。
    """
    __tablename__ = 'shops'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)   # 店铺名称
    street = db.Column(db.String(100), default='')                 # 街道名
    house_number = db.Column(db.String(50), default='')            # 门牌号
    owner_name = db.Column(db.String(50), default='')              # 商户登记姓名
    id_card = db.Column(db.String(18))                            # R12 加密后原列置 NULL（无 default，避免 INSERT 写 ''）
    id_card_enc = db.Column(db.Text)                               # 字段级密文
    id_card_hash = db.Column(db.String(64))                        # SHA-256 等值检索
    phone = db.Column(db.String(120), default='')
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
