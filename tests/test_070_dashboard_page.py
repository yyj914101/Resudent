# -*- coding: utf-8 -*-
"""tests/test_070_dashboard_page.py — 首页页面级补齐（070）结构/数据守卫。

守护：
  1) spec-01 五区块骨架：sc-page-head / sc-grid 8/4 / sc-card / sc-table / sc-badge（JS）
  2) 聚合值来自实库：人口结构 6 段、人口构成互斥分桶、复核进度、医保率
  3) 058 红线：4 KPI + 6 格指标条结构不动；快捷入口旧卡已移除
  4) 067 合并后低保复核块按 `dibao.support_status` 真实口径计算（不再占位）
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"


def _login(app):
    client = app.test_client()
    r = client.get('/auth/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', r.get_data(as_text=True))
    token = m.group(1) if m else ''
    client.post('/auth/login', data={
        'csrf_token': token, 'username': ADMIN_USER, 'password': ADMIN_PASS,
    })
    return client


def _seed_residents(app):
    """最小真实数据：1 户主高龄+低保、1 残疾、1 留守儿（2 户）。"""
    with app.app_context():
        from models import db, Resident, Dibao, Disabled, ResidentTag
        from utils.agg_cache import invalidate

        r1 = Resident(name='甲', household_name='甲', household_code='H1',
                      birth_date=date(1940, 1, 1), gender='男', village_group='一组',
                      household_property='一般农户', household_type='农业户口', nation='汉族',
                      status='normal', flow_status='常住')
        r2 = Resident(name='乙', household_name='乙', household_code='H2',
                      birth_date=date(2000, 1, 1), gender='女', village_group='二组',
                      household_property='脱贫户', household_type='农业户口', nation='苗族',
                      status='normal', flow_status='常住')
        r3 = Resident(name='丙', household_name='甲', household_code='H1',
                      birth_date=date(2015, 1, 1), gender='男', village_group='一组',
                      household_property='一般农户', household_type='农业户口', nation='汉族',
                      status='normal', flow_status='常住')
        db.session.add_all([r1, r2, r3])
        db.session.flush()
        db.session.add(Dibao(resident_id=r1.id, name='甲', status='有效'))
        db.session.add(Disabled(resident_id=r2.id, name='乙', status='有效',
                                disability_level='三级'))
        db.session.add(ResidentTag(resident_id=r3.id, tag_code='留守儿童', status='有效'))
        db.session.commit()
        invalidate()


def test_dashboard_page_has_spec01_structure(isolated_app_factory):
    app = isolated_app_factory()
    _seed_residents(app)
    client = _login(app)
    html = client.get('/dashboard/').get_data(as_text=True)

    assert 'class="sc-page-head"' in html
    assert 'class="sc-page-head__name">首页' in html
    assert 'class="sc-grid sc-dashboard-grid"' in html
    assert 'class="sc-grid__main"' in html and 'class="sc-grid__side"' in html
    assert html.count('class="sc-card"') >= 6, '五区块卡片壳缺失'
    assert 'class="sc-table"' in html
    for node in ('groupStatsFoot', 'structureTabs', 'structureChart', 'housePropChart',
                 'compositionChart', 'compositionLegend', 'reviewProgress',
                 'dashboardTodoList', 'medicalRateChart', 'medicalRateText'):
        assert 'id="%s"' % node in html, node
    assert 'sc-badge' in open(os.path.join(ROOT, 'static/js/pages/dashboard.js'), encoding='utf-8').read()


def test_dashboard_stats_aggregates_real_data(isolated_app_factory):
    app = isolated_app_factory()
    _seed_residents(app)
    client = _login(app)
    d = client.get('/dashboard/api/dashboard/stats').get_json()['data']

    assert d['total_population'] == 3
    assert d['total_households'] == 2
    assert d['age_structure']['labels'] == ['0-17', '18-35', '36-59', '60-79', '80-89', '90+']
    assert d['age_structure']['counts'] == [1, 1, 0, 0, 1, 0]
    assert d['age_structure']['labor'] == 1
    assert d['age_structure']['nonlabor'] == 2
    assert d['age_structure']['unknown'] == 0

    comp = d['population_composition']
    assert comp['total_people'] == 3
    assert comp['total_households'] == 2
    assert [it['count'] for it in comp['items']] == [2, 0, 1, 0]
    assert round(sum(it['pct'] for it in comp['items']), 1) == 100.0

    review = {r['label']: r for r in d['review_progress']}
    assert review['低保复核']['available'] is True, '067 合并后应使用 support_status 真实口径'
    assert review['低保复核']['done'] == 1 and review['低保复核']['total'] == 1
    assert review['高龄认证']['done'] == 1 and review['高龄认证']['total'] == 1
    assert review['残疾评定']['done'] == 1 and review['残疾评定']['total'] == 1
    assert d['data_updated_at']


def test_old_dashboard_blocks_removed(isolated_app_factory):
    app = isolated_app_factory()
    client = _login(app)
    html = client.get('/dashboard/').get_data(as_text=True)
    # 070：快捷入口卡移除、旧待办标题移除；058 KPI/指标条保留
    assert '快捷入口' not in html
    assert '待办提醒（T20-3' not in html
    assert 'id="statPopulation"' in html and 'id="statHouseholds"' in html
    assert 'id="statParty"' in html and 'id="statDistressed"' in html
    assert 'class="sc-stat-strip"' in html
    for stat in ('statDibao', 'statWelfare', 'statHardElderly', 'statDisabled', 'statVeteran', 'statElderly'):
        assert 'id="%s"' % stat in html, stat


def test_dashboard_js_uses_real_aggregate_fields():
    src = open(os.path.join(ROOT, 'static/js/pages/dashboard.js'), encoding='utf-8').read()
    for field in ('age_structure', 'population_composition', 'review_progress',
                  'medical_paid_rate', 'medical_total', 'household_prop_stats'):
        assert field in src, field
    assert "status: 'todo'" in src
    assert 'quickMediation' not in src and 'quickSubsidy' not in src
    assert '.destroy()' in src, '轮询重绘必须先销毁旧 Chart 实例'
