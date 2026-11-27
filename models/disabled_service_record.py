from . import db
from datetime import datetime

class DisabledServiceRecord(db.Model):
    __tablename__ = 'disabled_service_records'
    id = db.Column(db.Integer, primary_key=True)
    # R-093-01：不在此建 index=True（会生成 ix_* 自动索引，与 m027 的 idx_* 双索引漂移）
    # 索引统一由 m027 迁移建 idx_disabled_service_records_disabled，保证新库/存量库集合一致
    disabled_id = db.Column(db.Integer, db.ForeignKey('disabled_persons.id'), nullable=False)
    service_item = db.Column(db.String(30), nullable=False)                 # 残疾人服务事项
    service_status = db.Column(db.String(20), nullable=False, default='待受理')  # 服务办理状态
    service_progress = db.Column(db.String(200))                             # 办理进度
    service_owner = db.Column(db.String(50))                                 # 责任人
    service_date = db.Column(db.Date)                                        # 服务日期
    remark = db.Column(db.String(200))                                       # 备注
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
