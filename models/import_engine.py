from . import db
from datetime import datetime


class ImportBatch(db.Model):
    """R1-E2 导入批次总控。"""
    __tablename__ = 'import_batch'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), nullable=False, unique=True)
    source_file = db.Column(db.String(200))
    source_path = db.Column(db.String(300))                     # temp/imports/ 内路径（不入 uploads）
    source_hash = db.Column(db.String(64))                      # 防重复导入
    operator = db.Column(db.Integer)
    status = db.Column(db.String(10), nullable=False, default='pending')  # pending/mapped/running/done/blocked/aborted
    total_rows = db.Column(db.Integer, default=0)
    success_rows = db.Column(db.Integer, default=0)
    fail_rows = db.Column(db.Integer, default=0)
    skip_rows = db.Column(db.Integer, default=0)
    # 034 P1：mode=template|custom；policy=skip|update_non_empty（m013）
    mode = db.Column(db.String(10), default='custom')
    profile_key = db.Column(db.String(40))                     # P3 预留（standard_v1 / custom_<hash>）
    catalog_version = db.Column(db.String(20))                 # 036 F4：模板批次版本快照（独立列，不污染 profile_key）
    policy = db.Column(db.String(20), default='skip')
    updated_rows = db.Column(db.Integer, default=0)            # 覆盖更新成功数（双模式四计数口径）
    new_field_count = db.Column(db.Integer, default=0)
    thresholds = db.Column(db.Text)                             # JSON 守卫阈值快照
    block_reason = db.Column(db.String(200))
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)


class ImportFieldMapping(db.Model):
    """R1-E2 字段血缘。"""
    __tablename__ = 'import_field_mapping'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), db.ForeignKey('import_batch.batch_id'), nullable=False)
    source_field = db.Column(db.String(100), nullable=False)    # 规范化后源表头
    target_field = db.Column(db.String(100))                    # NULL=未映射
    target_table = db.Column(db.String(50))
    storage = db.Column(db.String(10))                          # column/json_ext
    mapping_kind = db.Column(db.String(10))                     # rename/convert/enum/ignore/unknown
    conversion = db.Column(db.Text)                             # JSON
    value_domain_map = db.Column(db.Text)                       # JSON
    required = db.Column(db.Integer, default=0)
    is_template = db.Column(db.Integer, default=0)              # 全局可复用模板规则
    matched_by = db.Column(db.String(10))                       # exact/fuzzy/template/manual/unknown
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_ifm_batch', 'batch_id'),
    )


class ImportRowError(db.Model):
    """R1-E2 异常暂存区。"""
    __tablename__ = 'import_row_error'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), db.ForeignKey('import_batch.batch_id'), nullable=False)
    row_no = db.Column(db.Integer, nullable=False)
    field = db.Column(db.String(100))                           # 行级错误为 NULL
    error_level = db.Column(db.String(5), nullable=False)       # warn/error
    error_type = db.Column(db.String(10), nullable=False)       # required/type/enum/unmapped/row
    reason = db.Column(db.String(300))
    raw_row = db.Column(db.Text)                                # JSON 原文（展示时脱敏）
    resolved = db.Column(db.Integer, default=0)
    resolution = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.Index('idx_ire_batch', 'batch_id', 'error_level'),
    )


class ImportProfile(db.Model):
    """036 P3：导入方案（映射复用）。standard_v1 由 FIELD_CATALOG 派生（m014 种子）。"""
    __tablename__ = 'import_profiles'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    profile_key = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    is_builtin = db.Column(db.Boolean, nullable=False, default=False)
    catalog_version = db.Column(db.String(20))
    mapping_json = db.Column(db.Text, nullable=False)            # [{header, field, transform}]
    default_policy = db.Column(db.String(20), default='skip')
    required_permission = db.Column(db.String(60))
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class ImportFieldRegistry(db.Model):
    """R1-E2 字段注册中心（方案B）。"""
    __tablename__ = 'import_field_registry'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    code = db.Column(db.String(50), nullable=False, unique=True)
    display_name = db.Column(db.String(100))
    data_type = db.Column(db.String(20))
    source_batch = db.Column(db.String(36))
    definition = db.Column(db.Text)                             # JSON：口径说明/统计用途声明
    status = db.Column(db.String(10), nullable=False, default='draft')  # draft/approved/rejected/active
    approved_by = db.Column(db.Integer)
    approved_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.now)
