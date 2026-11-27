# -*- coding: utf-8 -*-
"""058 首页仪表复刻：4 KPI 卡 + 6 格次级指标条 + 待办权限门控。

验收映射（docs/tasks/058-home-dashboard.md）：
1) 4 张 KPI 卡，图标块四色分类（--c-cat-1..4）
2) 次级指标条按样板格数对齐（6 格）
3) 无趋势行（主理人改稿已删，勿加回）
4) SSR 离线自包含（零外链）+ 样式落全局层 app.css（不靠 extra_css）
5) 待办卡按 todo.view 门控：无权限不渲染（GLB-01 口径）
"""
import json

import pytest


@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def _dashboard_html(client):
    resp = client.get('/dashboard/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_four_kpi_cards_with_category_icons(app):
    html = _dashboard_html(_admin_client(app))
    assert html.count('<div class="sc-kpi">') == 4
    for i in range(1, 5):
        assert 'sc-kpi__icon--%d' % i in html
    for label in ('总人口', '总户数', '党员人数', '困境儿童'):
        assert label in html
    assert 'id="statPopulation"' in html


def test_stat_strip_has_six_cells(app):
    html = _dashboard_html(_admin_client(app))
    assert html.count('<div class="sc-stat">') == 6
    # AUD-04：第 5 格已按样板改为「退役军人」（veteran_count 由 agg_cache 提供）
    for label in ('低保对象', '公益岗位', '困难老人', '残疾人员', '退役军人', '高龄老人'):
        assert label in html
    assert 'id="statVeteran"' in html


def test_no_trend_row(app):
    html = _dashboard_html(_admin_client(app))
    assert '较上月' not in html
    assert 'sc-kpi__delta' not in html


def test_offline_self_contained_and_global_css(app):
    html = _dashboard_html(_admin_client(app))
    assert 'http://' not in html and 'https://' not in html
    assert '/static/css/app.css' in html
    assert 'css/pages/dashboard.css' not in html


def test_todo_card_gated_by_todo_view(app):
    """无 todo.view：待办卡不渲染；JS 以元素存在为发请求前置（GLB-01）。"""
    from models import db, Role, User
    with app.app_context():
        role = Role(name='058无待办权限', permissions=json.dumps({'resident.view': True}))
        db.session.add(role)
        db.session.commit()
        user = User(username='058_no_todo', password_hash='x', role_id=role.id)
        db.session.add(user)
        db.session.commit()
        uid = user.id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid
    html = _dashboard_html(client)
    assert 'dashboardTodoList' not in html
    assert 'dashboardTodoList' in _dashboard_html(_admin_client(app))
