# -*- coding: utf-8 -*-
"""054 代码复核回归：党费超期三态须跨年判定（P2-8）。

原实现只看月份（`md['month'] < now.month`），导致查上一年度时 10~12 月被误判为
`arrears`（未到期）而非 `overdue`（已逾期），未来年度则相反。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / 'shucun.db')
    for k, v in {
        'SHUCUN_DB_PATH': db_path,
        'SHUCUN_TEMP_DIR': str(tmp_path / 'temp'),
        'SHUCUN_UPLOAD_DIR': str(tmp_path / 'uploads'),
        'SHUCUN_BACKUP_DIR': str(tmp_path / 'backups'),
        'SHUCUN_DATA_DIR': str(tmp_path / 'data'),
        'SHUCUN_LAN_AUTHORIZED': '1',
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config, 'TEMP_DIR', str(tmp_path / 'temp'))
    monkeypatch.setattr(config.Config, 'SQLALCHEMY_DATABASE_URI',
                        f'sqlite:///{db_path}?check_same_thread=False')
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    return application


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def _seed_member_with_fee(app, year, month, income=5000, paid=0):
    """建一名党员 + 指定年月一条党费记录（paid < calc 即欠缴）。"""
    from models import PartyFee, PartyMember, db
    with app.app_context():
        member = PartyMember(name=f'党员{year}{month:02d}')
        db.session.add(member)
        db.session.commit()
        db.session.add(PartyFee(member_id=member.id, fee_year=year, fee_month=month,
                                monthly_income=income, paid_amount=paid))
        db.session.commit()
        return member.id


def _status_of(app, year, member_id):
    """/party/api/dues/list 里该党员的 overall_status。"""
    client = _admin_client(app)
    resp = client.get(f'/party/api/dues/list?year={year}&limit=100')
    body = resp.get_json()
    assert body['success'], body
    rows = (body['data'] or {}).get('data') or []
    for row in rows:
        if row['id'] == member_id:
            return row['overall_status']
    raise AssertionError(f'未在返回中找到党员 {member_id}，返回 {len(rows)} 行')


class TestCrossYearOverdue:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_past_year_december_is_overdue(self, app):
        """上一年度的 12 月：全年已过，欠缴必须是 overdue（修复前判成 arrears）。"""
        from datetime import datetime
        last_year = datetime.now().year - 1
        mid = _seed_member_with_fee(app, last_year, 12)
        assert _status_of(app, last_year, mid) == 'overdue'

    def test_current_year_future_month_is_arrears(self, app):
        """当年最后一个月的欠缴：还没到期，是 arrears 不是 overdue。"""
        from datetime import datetime
        now = datetime.now()
        mid = _seed_member_with_fee(app, now.year, 12)
        expected = 'overdue' if 12 < now.month else 'arrears'
        assert _status_of(app, now.year, mid) == expected

    def test_future_year_is_never_overdue(self, app):
        """未来年度：不存在「过去月」，欠缴只能是 arrears。"""
        from datetime import datetime
        next_year = datetime.now().year + 1
        mid = _seed_member_with_fee(app, next_year, 1)
        assert _status_of(app, next_year, mid) == 'arrears'

    def test_paid_up_is_normal(self, app):
        """反向对照：足额缴纳 → normal（三态判定不得误伤）。"""
        from datetime import datetime
        last_year = datetime.now().year - 1
        mid = _seed_member_with_fee(app, last_year, 6, income=5000, paid=50)
        assert _status_of(app, last_year, mid) == 'normal'
