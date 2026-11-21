from . import db
from datetime import datetime

class Youshu(db.Model):
    """优抚对象台账（2026-11-16）：因伤病残涉核退役军人的优待补贴。

    字段口径（主理人派单）：姓名 / 身份证 / 电话 / 救助类别（字典可改）/ 优抚金额 /
    是否死亡 / 保障状态。身份证与电话按 R12 字段级加密管线存列（_enc/_hash），
    明文列只做 ORM 形态、写入后置 NULL（见 routes/youshu.py 的 sensitive 管线）。
    """
    __tablename__ = 'youshu'
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(50))                   # 姓名
    id_card = db.Column(db.String(18))                # 身份证号（写入后置 NULL，仅 _enc 生效）
    id_card_enc = db.Column(db.Text)
    id_card_hash = db.Column(db.String(64))
    phone = db.Column(db.String(30))                  # 电话（同上）
    phone_enc = db.Column(db.Text)
    phone_hash = db.Column(db.String(64))

    care_category = db.Column(db.String(20))          # 救助类别（字典「优抚对象救助类别」）
    amount = db.Column(db.Float)                      # 优抚金额（元）
    is_deceased = db.Column(db.String(10), default='否')   # 是否死亡
    # 保障状态与低保同口径（已认定/复审中/待核实），徽章由真实字段渲染
    support_status = db.Column(db.String(10), default='已认定')
    remark = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    status = db.Column(db.String(10), nullable=False, default='有效')   # 软失效
    invalid_reason = db.Column(db.String(100))
