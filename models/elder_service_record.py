from . import db
from datetime import datetime

class ElderServiceRecord(db.Model):
    __tablename__ = 'elder_service_records'
    id = db.Column(db.Integer, primary_key=True)
    # R-093-01：同 disabled_service_record，索引统一由 m027 建 idx_*，避免 ix_* 双索引漂移
    care_target_id = db.Column(db.Integer, db.ForeignKey('care_targets.id'), nullable=False)
    service_item = db.Column(db.String(30), nullable=False)                 # 老年人服务事项
    service_status = db.Column(db.String(20), nullable=False, default='待受理')  # 服务办理状态
    service_progress = db.Column(db.String(200))                             # 办理进度
    service_owner = db.Column(db.String(50))                                 # 责任人
    service_date = db.Column(db.Date)                                        # 服务日期
    remark = db.Column(db.String(200))                                       # 备注
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
