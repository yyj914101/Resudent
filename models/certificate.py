from . import db
from datetime import datetime

class CertificateTemplate(db.Model):
    __tablename__ = 'certificate_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    cert_type = db.Column(db.String(50))
    template_content = db.Column(db.Text)
    # 042：迁入 exe V2.45.2 模板中心能力（电子公章已按拍板①彻底删除）
    field_schema = db.Column(db.Text, default='[]')
    page_settings = db.Column(db.Text, default='{}')
    is_custom = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    version = db.Column(db.Integer, default=1)
    created_by = db.Column(db.String(50))
    updated_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class CertificateIssue(db.Model):
    """042：证明开具记录（开具编号/快照/作废留痕）。"""
    __tablename__ = 'certificate_issues'
    id = db.Column(db.Integer, primary_key=True)
    serial_no = db.Column(db.String(32), unique=True, nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey('certificate_templates.id'))
    template_name = db.Column(db.String(100), nullable=False)
    template_version = db.Column(db.Integer, default=1)
    cert_type = db.Column(db.String(50))
    resident_id = db.Column(db.Integer, db.ForeignKey('residents.id'))
    subject_name = db.Column(db.String(50), nullable=False)
    subject_id_card = db.Column(db.String(18))
    form_data = db.Column(db.Text)
    rendered_content = db.Column(db.Text)
    page_settings = db.Column(db.Text)
    issuer_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    issuer_name = db.Column(db.String(50))
    issue_unit = db.Column(db.String(150))
    purpose = db.Column(db.String(200))
    copies = db.Column(db.Integer, default=1)
    issued_at = db.Column(db.DateTime, default=datetime.now, index=True)
    status = db.Column(db.String(20), default='issued')
    voided_by = db.Column(db.String(50))
    voided_at = db.Column(db.DateTime)
    void_reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.now)
