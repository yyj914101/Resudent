from . import db
from datetime import datetime


class WarnRecord(db.Model):
    """R8 到期预警记录（二期业务，一期落结构）。"""
    __tablename__ = 'warn_record'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    resident_id = db.Column(db.Integer, db.ForeignKey('residents.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('subsidy_project.id'))
    warn_type = db.Column(db.String(20), nullable=False)       # 周期到期/年龄边界/停发确认
    trigger_reason = db.Column(db.String(200))
    status = db.Column(db.String(10), nullable=False, default='未处置')  # 未处置/已处置/已关闭
    handled_by = db.Column(db.Integer)
    handled_at = db.Column(db.DateTime)
    handle_result = db.Column(db.String(200))
    create_time = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_warn_record_status', 'status'),
    )
