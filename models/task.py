from . import db
from datetime import datetime

class AsyncTask(db.Model):
    """A1 单机后台任务状态表（T05 产出，详细设计 v1.1 §1.3）。

    status: pending → running → done / failed（可重进）
    progress: 0-100；result_summary: 结果/失败原因摘要
    task_type 互斥锁在进程内（utils/async_tasks.py），表内记录可追溯。
    """
    __tablename__ = 'async_task'
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    task_type = db.Column(db.String(50), nullable=False, index=True)
    title = db.Column(db.String(100), default='')
    status = db.Column(db.String(20), default='pending', index=True)
    progress = db.Column(db.Integer, default=0)
    operator = db.Column(db.String(50), default='')
    result_summary = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.now)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
