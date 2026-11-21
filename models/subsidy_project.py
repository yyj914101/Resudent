from . import db
from datetime import datetime


class SubsidyProject(db.Model):
    """R4 补贴项目主档。"""
    __tablename__ = 'subsidy_project'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    project_type = db.Column(db.String(20), nullable=False)  # 高龄津贴/困难老人补助/低保金/残疾补贴/其他
    cycle = db.Column(db.String(10), nullable=False)          # 月/季/年/一次性
    default_amount = db.Column(db.Numeric(10, 2))
    age_min = db.Column(db.Integer)
    age_max = db.Column(db.Integer)
    target_tags = db.Column(db.Text)                           # JSON 数组（适用标签码）
    status = db.Column(db.String(10), nullable=False, default='启用')  # 启用/停用
    remark = db.Column(db.String(200))
    create_by = db.Column(db.Integer)
    create_time = db.Column(db.DateTime, default=datetime.now)


class SubsidyRecord(db.Model):
    """R4 补贴发放记录（业务字段只增不改，冲正另记）。"""
    __tablename__ = 'subsidy_record'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    resident_id = db.Column(db.Integer, db.ForeignKey('residents.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('subsidy_project.id'), nullable=False)
    issue_date = db.Column(db.Date, nullable=False)
    period = db.Column(db.String(7), nullable=False)           # YYYY-MM
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    channel = db.Column(db.String(10), nullable=False, default='镇级直发')
    source = db.Column(db.String(10), nullable=False, default='normal')  # normal/legacy
    status = db.Column(db.String(10), nullable=False, default='正常')    # 正常/停发/新增（082 B-2）
    note = db.Column(db.String(200))
    legacy_id = db.Column(db.Integer, unique=True)             # 存量迁移幂等键 = subsidies.id
    source_hash = db.Column(db.String(64), unique=True)        # 幂等键（冲正/批量登记防重，R5）；NULL 不参与唯一
    create_by = db.Column(db.Integer)
    create_time = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_subsidy_record_resident', 'resident_id'),
        db.Index('idx_subsidy_record_project_period', 'project_id', 'period'),
        db.Index('idx_subsidy_record_status', 'status'),
    )


class SubsidyBatchImport(db.Model):
    """T14-5 补贴批量登记批次总控（轻量，独立于居民导入 ImportBatch）。"""
    __tablename__ = 'subsidy_batch_import'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), nullable=False, unique=True)
    project_id = db.Column(db.Integer, db.ForeignKey('subsidy_project.id'), nullable=False)
    source_file = db.Column(db.String(200))
    source_path = db.Column(db.String(300))                     # temp/imports/ 内路径
    source_hash = db.Column(db.String(64))                      # 防重复导入（sha256 文件内容）
    operator = db.Column(db.Integer)
    status = db.Column(db.String(10), nullable=False, default='pending')  # pending/running/done/failed/aborted（aborted=重启残留，108 幂等三件套）
    total_rows = db.Column(db.Integer, default=0)
    success_rows = db.Column(db.Integer, default=0)
    fail_rows = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)

    __table_args__ = (
        db.Index('idx_subsidy_batch_project', 'project_id'),
    )


class SubsidyBatchError(db.Model):
    """T14-5 补贴批量登记失败行暂存（脱敏）。"""
    __tablename__ = 'subsidy_batch_error'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), db.ForeignKey('subsidy_batch_import.batch_id'), nullable=False)
    row_no = db.Column(db.Integer, nullable=False)
    id_card_masked = db.Column(db.String(30))                   # 脱敏身份证（前6后4）
    reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_subsidy_batch_error', 'batch_id'),
    )
