from . import db
from datetime import datetime


class MigrationRecord(db.Model):
    """存量迁移总控（切换点原子化载体）。"""
    __tablename__ = 'migration_record'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    migration_no = db.Column(db.String(30), nullable=False, unique=True)
    version = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(10), nullable=False, default='running')  # running/done/failed
    row_stats = db.Column(db.Text)                              # JSON：各阶段行数
    reconcile_result = db.Column(db.Text)                       # JSON：对账报告（G-4 引用）
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)


class MigrationLog(db.Model):
    """存量迁移行级留痕。"""
    __tablename__ = 'migration_log'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    migration_no = db.Column(db.String(30), nullable=False)
    source_table = db.Column(db.String(50), nullable=False)
    source_id = db.Column(db.Integer, nullable=False)
    target_id = db.Column(db.Integer)
    migrated_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('migration_no', 'source_table', 'source_id', name='uq_migration_log'),
    )


class MigrationOrphanLog(db.Model):
    """存量迁移孤儿记录（不静默丢弃）。"""
    __tablename__ = 'migration_orphan_log'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    migration_no = db.Column(db.String(30), nullable=False)
    source_table = db.Column(db.String(50), nullable=False)
    source_id = db.Column(db.Integer)
    orphan_type = db.Column(db.String(30), nullable=False)      # id_card_empty / no_resident_match
    raw_row = db.Column(db.Text, nullable=False)                # JSON 整行快照（可对账可补录）
    amount = db.Column(db.Numeric(10, 2))                       # G-4 孤儿总额口径字段
    year = db.Column(db.Integer)
    handled = db.Column(db.Integer, nullable=False, default=0)  # 0=待人工核对
    handled_by = db.Column(db.Integer)
    handled_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)
