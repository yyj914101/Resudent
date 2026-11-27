from . import db
from datetime import datetime

class TodoItem(db.Model):
    __tablename__ = 'todo_items'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text)
    todo_type = db.Column(db.String(50))
    status = db.Column(db.String(20), default='todo')
    remind_time = db.Column(db.DateTime)
    related_id = db.Column(db.Integer)
    completed_by = db.Column(db.String(50))
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)
