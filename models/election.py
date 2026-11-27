from . import db
from datetime import datetime


class ElectionRound(db.Model):
    """090B 公共事务·选举：届次（一页纸字段清单 v1.2 §2，R-001~R-009）。

    状态机（§6）：筹备→选民登记→投票→结果公示→已完成；非法迁移 409 不落库，
    迁移表唯一真源 utils/election_service.ELECTION_TRANSITIONS。
    R-093-01 纪律：模型侧不建 index=True，索引统一由 m035 建 idx_*。
    """
    __tablename__ = 'election_round'

    id = db.Column(db.Integer, primary_key=True)
    round_name = db.Column(db.String(100), nullable=False)          # R-001 届次名称
    start_date = db.Column(db.Date, nullable=False)                 # R-002 任期起
    end_date = db.Column(db.Date)                                   # R-003 任期止（≥起始）
    register_start = db.Column(db.Date, nullable=False)             # R-004 登记期起
    register_end = db.Column(db.Date, nullable=False)               # R-005 登记期止（≥登记起）
    vote_date = db.Column(db.Date, nullable=False)                  # R-006 投票日
    status = db.Column(db.String(30), nullable=False, default='筹备')  # R-007 字典 D-01
    remark = db.Column(db.Text)                                     # R-008
    created_at = db.Column(db.DateTime, default=datetime.now)       # R-009
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class ElectionVoter(db.Model):
    """090B 选民名册（一页纸 §3，V-001~V-011）。

    - 与 resident 软关联（V-011 resident_hash 由身份证派生，不建外键）；
    - V-002/V-009 接 R12 字段级加密管线（明文列无 default，加密后置 NULL，密文落
      *_enc，等值检索走 *_hash）；
    - V-007 委托凭证号已按 R-04 删除；
    - 唯一约束 V-03：同届次同人一条（round_id + id_card_hash）——模型侧
      UniqueConstraint 落 sqlite_autoindex，m035 先探测等价唯一索引再建，防双建。
    """
    __tablename__ = 'election_voter'
    __table_args__ = (
        db.UniqueConstraint('round_id', 'id_card_hash', name='uq_election_voter_round_hash'),
    )

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('election_round.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)                 # V-001
    id_card_enc = db.Column(db.Text)                                # V-002 身份证（密文）
    id_card_hash = db.Column(db.String(64), nullable=False)         # V-002 身份证哈希（等值检索）
    group_name = db.Column(db.String(50))                           # V-003 所在组
    register_status = db.Column(db.String(30), nullable=False, default='未登记')  # V-004 字典 D-02
    is_out = db.Column(db.Boolean, default=False)                   # V-005 是否外出
    proxy_name = db.Column(db.String(50))                           # V-006 委托受托人（委托投票必填）
    mobile_box = db.Column(db.Boolean, default=False)               # V-008 流动票箱
    phone_enc = db.Column(db.Text)                                  # V-009 联系方式（密文）
    phone_hash = db.Column(db.String(64))                           # V-009 联系方式哈希
    resident_hash = db.Column(db.String(64))                        # V-011 系统列：由 V-002 派生
    remark = db.Column(db.Text)                                     # V-010
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class ElectionResult(db.Model):
    """090B 当选结果（一页纸 §4，E-001~E-007；归属届次）。

    E-003 winner_id_card_hash 为纯哈希列（与选民表软关联，不存明文不加密）。
    """
    __tablename__ = 'election_result'

    id = db.Column(db.Integer, primary_key=True)
    round_id = db.Column(db.Integer, db.ForeignKey('election_round.id'), nullable=False)
    position = db.Column(db.String(30), nullable=False)             # E-001 字典 D-03
    winner_name = db.Column(db.String(50), nullable=False)          # E-002
    winner_id_card_hash = db.Column(db.String(64))                  # E-003（软关联）
    votes = db.Column(db.Integer, nullable=False, default=0)        # E-004 ≥0
    is_elected = db.Column(db.Boolean, nullable=False, default=False)  # E-005
    notice_date = db.Column(db.Date)                                # E-006 公示日期
    remark = db.Column(db.Text)                                     # E-007
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class ElectionBatchImport(db.Model):
    """105 选民批量导入批次总控（辅助表，ADR-030 裁定 3；与 SubsidyBatchImport 同构）。

    状态：pending → running → done / failed / aborted（进程重启由启动钩子标 aborted）。
    source_hash = sha256(文件内容)：同届次同文件重复上传直接返回既有批次（幂等，裁定 5）。
    R-093-01 纪律：模型侧不建 index=True，索引统一由 m042 建 idx_*。
    """
    __tablename__ = 'election_batch_import'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), nullable=False, unique=True)
    round_id = db.Column(db.Integer, db.ForeignKey('election_round.id'), nullable=False)
    source_file = db.Column(db.String(200))
    source_path = db.Column(db.String(300))                     # temp/imports/ 内路径
    source_hash = db.Column(db.String(64))                      # 防重复导入（sha256 文件内容）
    operator = db.Column(db.Integer)
    status = db.Column(db.String(10), nullable=False, default='pending')
    total_rows = db.Column(db.Integer, default=0)
    success_rows = db.Column(db.Integer, default=0)
    fail_rows = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    finished_at = db.Column(db.DateTime)


class ElectionBatchError(db.Model):
    """105 选民批量导入失败行暂存（脱敏：身份证仅存前 6 后 4）。"""
    __tablename__ = 'election_batch_error'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(36), db.ForeignKey('election_batch_import.batch_id'),
                         nullable=False)
    row_no = db.Column(db.Integer, nullable=False)
    id_card_masked = db.Column(db.String(30))
    reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=datetime.now)
