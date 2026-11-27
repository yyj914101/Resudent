from . import db
from datetime import datetime


class SchemaChangeRequest(db.Model):
    """防漂移审批流。"""
    __tablename__ = 'schema_change_request'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    request_no = db.Column(db.String(30), nullable=False, unique=True)
    change_type = db.Column(db.String(20), nullable=False)      # add_field/change_mapping/version_bump
    payload = db.Column(db.Text)
    status = db.Column(db.String(10), nullable=False, default='pending')
    requested_by = db.Column(db.Integer)
    decided_by = db.Column(db.Integer)
    decided_at = db.Column(db.DateTime)
    reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.now)


class DataModelVersion(db.Model):
    """数据模型版本登记。"""
    __tablename__ = 'data_model_version'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    model_name = db.Column(db.String(50), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    change_log = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('model_name', 'version', name='uq_data_model_version'),
    )
