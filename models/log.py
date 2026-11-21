from . import db
from datetime import datetime

class OperationLog(db.Model):
    __tablename__ = 'operation_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(db.String(50))
    module = db.Column(db.String(50))
    action = db.Column(db.String(50))
    summary = db.Column(db.String(500))
    detail = db.Column(db.Text)
    result = db.Column(db.String(20), default='success')
    ip_address = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
