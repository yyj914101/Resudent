from . import db
from datetime import datetime


def _default_flow_status() -> str:
    """079 A0：flow_status 列默认值（SQLAlchemy 插入时调用，带 app 上下文）。

    「流动状态」字典等值绑定（label 命中 → 字典 value）；缺项回退种子，零行为变化。
    函数内延迟导入避免 models ↔ utils 循环导入。
    """
    from utils.dict_enum import get_enum_value
    return get_enum_value('流动状态', '常住')


class Resident(db.Model):
    __tablename__ = 'residents'
    id = db.Column(db.Integer, primary_key=True)
    # 户信息
    household_name = db.Column(db.String(50))        # 户主名
    household_code = db.Column(db.String(50))         # 户编号
    # 居民信息
    name = db.Column(db.String(50), nullable=False, index=True)
    id_card = db.Column(db.String(18), index=True)    # 身份证号
    birth_date = db.Column(db.Date)                    # 出生日期
    gender = db.Column(db.String(10))                  # 性别
    nation = db.Column(db.String(20))                  # 民族
    phone = db.Column(db.String(120))                   # 联系电话
    village_group = db.Column(db.String(50))           # 所在组
    grid = db.Column(db.String(50), default='')         # 所在网格
    address = db.Column(db.String(200))                # 家庭地址
    relationship = db.Column(db.String(50))            # 与户主关系
    household_type = db.Column(db.String(20))          # 户口类型
    politics_status = db.Column(db.String(20))         # 政治面貌
    education = db.Column(db.String(20))               # 学历
    occupation = db.Column(db.String(50))              # 职业
    health_status = db.Column(db.String(20))           # 健康状态
    household_property = db.Column(db.String(20))      # 户属性
    bank_name = db.Column(db.String(50))               # 开户行
    bank_card = db.Column(db.String(30))               # 银行卡号
    marital_status = db.Column(db.String(20))            # 婚姻状况
    military_status = db.Column(db.String(20))            # 兵役状况
    remark = db.Column(db.Text)                           # 备注
    status = db.Column(db.String(20), default='normal') # normal/deceased/moved_out
    status_date = db.Column(db.Date)                   # 状态变更日期
    status_reason = db.Column(db.String(200))          # 状态变更原因
    # R1（m002）：流动性单一真值 + 扩展列
    # 079 A0：列默认改可调用（插入时求值，app 上下文内走「流动状态」字典；无上下文/缺项回退种子）
    flow_status = db.Column(db.String(10), default=_default_flow_status)  # 常住/流入/流出
    ext_data = db.Column(db.Text)                          # JSON 扩展列
    # R1（m006，T11）：建档来源（手工/导入），来源可查（PRD R1）
    source = db.Column(db.String(10), default='手工')       # 手工/导入
    # R12（m003）：字段级加密列（密文 + 哈希索引；原列迁移后置 NULL）
    id_card_enc = db.Column(db.Text)
    id_card_hash = db.Column(db.String(64))
    id_card_prefix_hash = db.Column(db.String(64))         # sha256(前6位)，模糊候选桶
    phone_enc = db.Column(db.Text)
    phone_hash = db.Column(db.String(64))
    phone_prefix_hash = db.Column(db.String(64))           # m006：sha256(前6位)，phone 模糊候选桶
    bank_card_enc = db.Column(db.Text)
    bank_card_hash = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.Index('idx_resident_id_card', 'id_card'),
        db.Index('idx_resident_name_group', 'name', 'village_group'),
        db.Index('idx_residents_flow_status', 'flow_status'),
    )
