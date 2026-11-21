from . import db
from datetime import datetime


class VisitPlan(db.Model):
    """R7 走访计划（二期业务，一期随 m001 落结构）。"""
    __tablename__ = 'visit_plan'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    plan_name = db.Column(db.String(100), nullable=False)
    plan_type = db.Column(db.String(20), nullable=False)       # 按区域/按对象类型/按标签
    scope_params = db.Column(db.Text)                          # JSON
    target_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(10), nullable=False, default='草稿')  # 草稿/进行中/已完成
    created_by = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)


class VisitRecord(db.Model):
    """R7 走访记录。"""
    __tablename__ = 'visit_record'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('visit_plan.id'))
    resident_id = db.Column(db.Integer, db.ForeignKey('residents.id'), nullable=False)
    visit_date = db.Column(db.Date, nullable=False)
    visitor_id = db.Column(db.Integer, nullable=False)
    visit_type = db.Column(db.String(10), nullable=False)       # 入户/电话/其他
    content = db.Column(db.Text)
    has_need = db.Column(db.Integer, nullable=False, default=0)  # 0=无（默认减负）
    need_desc = db.Column(db.Text)
    photos = db.Column(db.Text)                                 # JSON 数组：uploads/visit/ 相对路径
    create_by = db.Column(db.Integer)
    create_time = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_visit_record_resident', 'resident_id'),
        db.Index('idx_visit_record_plan', 'plan_id'),
    )
