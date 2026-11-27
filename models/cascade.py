from . import db
from datetime import datetime


class CascadeChecklist(db.Model):
    """R5 级联处置清单（死亡→补贴/标签/台账）。"""
    __tablename__ = 'cascade_checklist'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    death_report_id = db.Column(db.Integer, db.ForeignKey('death_reports.id'), nullable=False)
    affected_items = db.Column(db.Text, nullable=False)         # JSON：补贴项目/最近发放/标签/台账
    generated_at = db.Column(db.DateTime, default=datetime.now)
    printed = db.Column(db.Integer, default=0)
    status = db.Column(db.String(10), nullable=False, default='有效')  # 有效/作废（B2 撤回置作废）


class CascadeNotice(db.Model):
    """R5 停发告知单（B1）。"""
    __tablename__ = 'cascade_notice'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    death_report_id = db.Column(db.Integer, db.ForeignKey('death_reports.id'), nullable=False)
    notice_no = db.Column(db.String(30), nullable=False, unique=True)  # TZ-YYYYMMDD-####
    resident_name = db.Column(db.String(50), nullable=False)
    id_card_enc = db.Column(db.Text)                            # 密文原生
    id_card_hash = db.Column(db.String(64))
    project_ids = db.Column(db.Text, nullable=False)            # JSON 数组
    stop_from = db.Column(db.String(7), nullable=False)         # 停发起始期 YYYY-MM
    reason = db.Column(db.String(50), nullable=False)           # 死亡/注销
    generated_at = db.Column(db.DateTime, default=datetime.now)
    operator = db.Column(db.Integer)
    printed_count = db.Column(db.Integer, default=0)            # 重新打印留痕
