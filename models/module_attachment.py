from . import db
from datetime import datetime


class ModuleAttachment(db.Model):
    """092 模板附件区：通用模块级附件表（立项规划 §十一裁决 8：不复用 SafetyNode）。

    - module_key 归属模块（disabled/care/subsidy，零新模块键）；target_type/target_id
      为记录级预留（本批不启用 UI，恒 'module'/NULL）。
    - original_name 只入库不落盘；落盘名 stored_name 由 attachment_service 生成
      （uuid4 hex + 白名单扩展名，防路径穿越双保险）。
    - R-093-01 纪律：模型侧不建 index=True（避免与迁移 idx_* 双索引漂移），
      索引统一由 m036 建 idx_module_attachments_module / idx_module_attachments_target，
      新库（create_all）/存量库（m036）索引集合一致。
    """
    __tablename__ = 'module_attachments'

    id = db.Column(db.Integer, primary_key=True)
    module_key = db.Column(db.String(30), nullable=False)
    target_type = db.Column(db.String(30), nullable=False, default='module')
    target_id = db.Column(db.Integer)
    original_name = db.Column(db.String(255), nullable=False)
    stored_name = db.Column(db.String(64), nullable=False)
    ext = db.Column(db.String(10), nullable=False)
    size = db.Column(db.Integer, nullable=False, default=0)
    mime = db.Column(db.String(120))
    uploaded_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
