from . import db
from datetime import datetime


class ReconcileTask(db.Model):
    """R5 对账任务（公安/殡葬/季度计划双触发）。"""
    __tablename__ = 'reconcile_task'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    source = db.Column(db.String(20), nullable=False)          # 公安/殡葬/季度计划
    import_file = db.Column(db.String(200))
    task_date = db.Column(db.Date, nullable=False)
    quarter = db.Column(db.String(7))                           # 2026-Q3
    status = db.Column(db.String(10), nullable=False, default='pending')
    result_summary = db.Column(db.Text)                         # JSON：四类结果计数
    operator = db.Column(db.Integer)
    source_hash = db.Column(db.String(64), unique=True)         # 导入幂等键（T15-4，NULL 不参与唯一；季度任务无文件天然兼容）
    source_deleted_at = db.Column(db.DateTime)                  # 源名单删除时间（≤30 天）
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_reconcile_task_quarter', 'quarter', 'status'),
    )


class ReconcileResult(db.Model):
    """R5 对账结果（新表密文原生，无明文列）。

    match_type 四分类（2026-08-28 主理人拍板，PRD 三分类细化）：
      已登记（名单行，系统档案 status=deceased）
      疑似未登记（名单行，系统在册未死亡）
      无档案（名单行，系统无居民档案）
      名单外（系统 deceased 但名单无，全量补集比对）
    """
    __tablename__ = 'reconcile_result'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    task_id = db.Column(db.Integer, db.ForeignKey('reconcile_task.id'), nullable=False)
    name = db.Column(db.String(50))                             # 名单行姓名/系统档案姓名（展示+登记预填）
    death_date = db.Column(db.Date)                             # 名单行死亡日期（登记预填）
    id_card_enc = db.Column(db.Text)                            # 敏感字段密文
    id_card_hash = db.Column(db.String(64))                     # SHA-256 等值检索
    match_type = db.Column(db.String(20), nullable=False)       # 已登记/疑似未登记/无档案/名单外
    handle_status = db.Column(db.String(10), nullable=False, default='未处置')
    handled_by = db.Column(db.Integer)
    handled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_reconcile_result_task', 'task_id', 'match_type'),
        db.Index('idx_reconcile_result_id_card_hash', 'id_card_hash'),
    )
