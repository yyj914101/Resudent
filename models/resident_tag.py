from . import db
from datetime import datetime


class ResidentTag(db.Model):
    """R1 居民标签（仅政策性标签，软失效）。"""
    __tablename__ = 'resident_tag'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    resident_id = db.Column(db.Integer, db.ForeignKey('residents.id'), nullable=False)
    tag_code = db.Column(db.String(50), nullable=False)       # dict_item（仅政策性标签）
    source = db.Column(db.String(10), nullable=False, default='手动')  # 自动/手动/导入
    status = db.Column(db.String(10), nullable=False, default='有效')  # 有效/失效（软失效）
    invalid_reason = db.Column(db.String(100))
    invalid_time = db.Column(db.DateTime)
    operator = db.Column(db.Integer)
    create_time = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('resident_id', 'tag_code', 'status', name='uq_resident_tag'),
        db.Index('idx_resident_tag_code_status', 'tag_code', 'status'),
    )
