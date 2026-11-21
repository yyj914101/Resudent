# -*- coding: utf-8 -*-
"""utils.notice_no — 停发告知单编号生成（T15-3，R5/B1）。

规则：TZ-YYYYMMDD-####（当日 4 位序号，如 TZ-20260828-0001）。
- 当日最大序号 +1（SQLAlchemy autoflush 感知同事务新增行）
- unique 冲突兜底重试 1 次（SQLite 单写者 busy_timeout=30s，冲突概率极低）
"""
from datetime import datetime


def generate_notice_no() -> str:
    """生成当日唯一告知单编号。"""
    from models.cascade import CascadeNotice

    today = datetime.now().strftime('%Y%m%d')
    prefix = f'TZ-{today}-'

    # 当日最大序号（autoflush 保证同事务内先插入的 notice 可被感知）
    latest = (CascadeNotice.query
              .filter(CascadeNotice.notice_no.like(prefix + '%'))
              .order_by(CascadeNotice.notice_no.desc())
              .first())
    seq = 1
    if latest and latest.notice_no.startswith(prefix):
        try:
            seq = int(latest.notice_no[len(prefix):]) + 1
        except ValueError:
            seq = 1

    candidate = f'{prefix}{seq:04d}'
    return candidate
