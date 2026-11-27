# -*- coding: utf-8 -*-
"""tests/test_090b_snapshots.py — 090B 选举名册页 SSR 渲染快照（test_088 同款）。

/election/ 由 test_template_smoke 的无参 GET 扫描自动收录（election.html）；
/election/rounds/<rid>/voters 带参被跳过，本文件把名册页纳入快照门禁，
复用 test_template_smoke 的归一化实现：
  首跑 → 生成 tests/snapshots/election_voters.html（须提交入库）；
  次跑 → 与基线逐字节对比，任一差异即 fail。

名册/结果行由页面 JS 异步加载，快照固化的是 SSR 壳层（页头届次信息/状态徽章/
推进按钮位/工具条/空表/弹窗骨架）——足够守护模板回归；数据交互由 test_090b_election 覆盖。
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

# 归一化与快照目录复用既有实现，保证与全站其它页同一口径
from test_template_smoke import SNAPSHOT_DIR, _normalize


def _prepare(app):
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('election-snapshot-test', salt=generate_salt())


def _login(app):
    client = app.test_client()
    with app.app_context():
        from models import User
        admin = User.query.filter_by(username='admin').first()
    with client.session_transaction() as sess:
        sess['user_id'] = admin.id
        sess['username'] = admin.username
    return client


def _check_snapshot(client, route, name, tmp):
    """GET route → 归一化 → 与 tests/snapshots/<name> 对比（缺则生成）。"""
    resp = client.get(route)
    assert resp.status_code == 200, '%s 渲染失败：%s' % (route, resp.status_code)
    assert 'text/html' in (resp.content_type or ''), '%s 非 HTML' % route
    norm = _normalize(resp.get_data(as_text=True), tmp)
    snap = os.path.join(SNAPSHOT_DIR, name)
    if not os.path.exists(snap):
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        with open(snap, 'w', encoding='utf-8', newline='\n') as f:
            f.write(norm)
        pytest.skip('首跑生成基线 %s，提交入库后重跑进入对比模式' % name)
    with open(snap, encoding='utf-8') as f:
        baseline = f.read()
    # 换行符不参与 diff：仓库 checkout 可能按平台转为 CRLF，比较前统一归一为 LF
    assert baseline.replace('\r\n', '\n') == norm.replace('\r\n', '\n'), (
        '%s 渲染与基线 %s 不一致。若为有意改动：删除快照后重跑重建基线；'
        '若为无意回归：先还原模板。' % (route, name)
    )


@pytest.fixture()
def app_round(isolated_app_factory):
    """预置一个「筹备」态届次（字段全部字面量，保证快照确定性）。"""
    application = isolated_app_factory()
    _prepare(application)
    with application.app_context():
        from models import db, ElectionRound
        db.session.add(ElectionRound(
            round_name='第十二届村民委员会换届选举',
            start_date=date(2026, 12, 1), end_date=date(2029, 11, 30),
            register_start=date(2026, 11, 1), register_end=date(2026, 11, 20),
            vote_date=date(2026, 12, 15), status='筹备', remark=''))
        db.session.commit()
        rid = db.session.query(ElectionRound.id).order_by(ElectionRound.id.desc()).first()[0]
    return application, rid


def test_election_voters_snapshot(tmp_path, app_round):
    """名册页壳层：届次头（任期/登记期/投票日/状态徽章）+ 对账条 + 名册/结果卡 + 弹窗骨架。"""
    application, rid = app_round
    _check_snapshot(_login(application), '/election/rounds/%d/voters' % rid,
                    'election_voters.html', str(tmp_path))
