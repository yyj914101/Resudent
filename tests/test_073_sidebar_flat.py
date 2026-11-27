# -*- coding: utf-8 -*-
"""tests/test_073_sidebar_flat.py — 073 壳层侧栏扁平化结构断言（v5 扁平 IA）。

覆盖：
  1) 扁平组（综合工作 + 四大板块 + 数据管理）+ 底部设置：组标签顺序、href 账目、
     两层结构零残留（无 collapse 触发器 / .collapse / .sc-subnav / chevron）、外链 = 0
  2) 侧栏入口 + 065 module_tabs 胶囊逐 URL GET 200
     （2026-11-16：死亡上报回社区治理组；导入批次与台账建表并入「数据管理」组；
      证明开具页不再承载跨页胶囊）
  3) spa.js 扁平适配静态断言：扁平组收空逻辑在位；3 处存量 .collapse 依赖原样保留
  4) D-22 回退态 twolayer 冒烟：折叠盒 / 触发器成对、组标签与真源一致

所有侧栏账目均从单一真源 utils/nav_spec.py（NAV_GROUPS / NAV_SETTINGS）派生，
测试内不再维护 FLAT_ENDPOINTS / GROUP_LABELS 副本。
"""
import os
import re
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.nav_spec import NAV_GROUPS, NAV_SETTINGS

from helpers.structure_assert import analyze, assert_balanced

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIDEBAR_PARTIAL = os.path.join(
    BASE, "templates", "components", "_partials", "sidebar_menu.html")
SPA_JS = os.path.join(BASE, "static", "js", "spa.js")

# 纯胶囊可达的原二级入口：065 module_tabs 的独立账目，不是侧栏入口副本
# （6 个原二级项已升为侧栏入口本体，不在此列）
# resident 组 2（102 减年度缴费）+ 社会保障组 2（102 增养老胶囊，宿主医保页）
# + org 组 4（083A 增党务活动、083B 增党员发展）+ civil 组 8（084 增就业服务后迁出）
# + governance 组 8（2026-11-16 三类分区：社区矫正/戒毒下线、改刑事人员、走访三项迁出）
# + visitcare 组 3（采访谈计划页宿主）+ 死亡上报 1
CHIP_ENDPOINTS = {
    'resident.list': ['resident.add', 'shop.list_page'],
    'medical_insurance.list': ['pension.index'],
    'organization.list': ['party.party_list', 'party.development_page', 'party.activity_page', 'party.dues_list'],
    'dibao.dibao_list': [
        'disabled.disabled_list', 'care.care_list', 'welfare.page_list',
        'resident.vulnerable_page', 'youshu.list_page',
        'report.stats_table', 'report.roster_view', 'word_form.index',
    ],
    'mediation.list': [
        ('governance.roster_page', {'code': 'gov_stability_risk'}),
        ('governance.roster_page', {'code': 'gov_mental_health'}),
        ('governance.roster_page', {'code': 'gov_security_committee'}),
        # 2026-11-16：社区矫正 / 社区戒毒康复下线，刑满释放帮教改名刑事人员
        ('governance.roster_page', {'code': 'gov_criminal'}),
        ('governance.roster_page', {'code': 'gov_road_hazard'}),
        ('governance.roster_page', {'code': 'gov_key_vehicle'}),
        ('governance.roster_page', {'code': 'gov_guide_station'}),
        'governance.petition_page',
    ],
    # 2026-11-16：走访关爱独立入口（侧栏一级项 = 走访计划页，页内三枚胶囊）
    'visit.list_page': [
        ('governance.roster_page', {'code': 'gov_special_visit'}),
        'visit.history',
    ],
    # 2026-11-16：就业服务迁入社会保障组（宿主 = 医保参保管理页）
    'medical_insurance.list': ['pension.index', 'welfare.employment_page'],
    # 2026-11-16：死亡名单对账由民政对象迁出，与死亡上报成组（宿主 = 死亡上报页）
    'death_report.list': ['reconcile.index'],
    # 历史账目：证明开具页已无跨页胶囊（死亡上报 / 导入批次升为侧栏入口）。
}

def _make_app(isolated_app_factory):
    app = isolated_app_factory({'SHUCUN_LAN_AUTHORIZED': '1'})
    return app


def _login(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # 首装种子 admin


def _urls(app, entries):
    """endpoint 列表 → 路径集合。元组两形态：(endpoint, kwargs字典) 路由带参；
    (endpoint, 标签字符串) 仅文档用。"""
    out = set()
    with app.test_request_context('/'):
        for e in entries:
            if isinstance(e, tuple):
                endpoint, extra = e
                if isinstance(extra, dict):
                    out.add(app.jinja_env.globals['url_for'](endpoint, **extra))
                else:
                    out.add(app.jinja_env.globals['url_for'](endpoint))
            else:
                out.add(app.jinja_env.globals['url_for'](e))
    return out


def _nav_expected_urls(app):
    """真源全部入口 URL：各组 item['endpoint'] + 底部 NAV_SETTINGS['endpoint']。"""
    entries = [item['endpoint'] for g in NAV_GROUPS for item in g['items']]
    entries.append(NAV_SETTINGS['endpoint'])
    return _urls(app, entries)


def _nav_labels():
    """真源组标签（按顺序）。"""
    return [g['label'] for g in NAV_GROUPS]


def _expected_link_count():
    """真源链接数 = 16 组内入口 + 1 底部设置（不写死 17）。"""
    return sum(len(g['items']) for g in NAV_GROUPS) + 1


def _nav_links(fragment):
    """片段内 `nav-link sc-nav-item` 的 href 列表（去 #fragment）。"""
    return [re.sub(r'#.*$', '', h) for h in re.findall(
        r'<a class="nav-link sc-nav-item[^"]*"\s+href="([^"]+)"', fragment)]


def _sidebar(html):
    m = re.search(r'<aside class="app-sidebar sc-sidebar" id="sidebar">.*?</aside>',
                  html, re.S)
    assert m, '侧栏 <aside id="sidebar"> 未渲染'
    return m.group(0)


def _menu(html):
    m = re.search(r'<div id="menu">(.*?)\n        </div>', html, re.S)
    assert m, '#menu 未渲染'
    return m.group(1)


# ══════════ 1) 扁平五组结构 ══════════

def test_flat_structure_five_groups_from_nav_spec(isolated_app_factory):
    app = _make_app(isolated_app_factory)
    client = app.test_client()
    _login(client)
    html = client.get('/dashboard/').get_data(as_text=True)
    sidebar = _sidebar(html)
    menu = _menu(sidebar)
    report = analyze(sidebar)
    assert_balanced(report)

    # 五组，标签顺序与真源逐字一致
    labels = re.findall(r'<h4 class="sc-sidebar__label">([^<]+)</h4>', menu)
    assert labels == _nav_labels(), f'组标签应为 {_nav_labels()}，实际 {labels}'

    # 16 组内入口 + bottom 设置 1 个；href 账目全对
    expected = _nav_expected_urls(app)
    links = _nav_links(sidebar)
    got = set(links)
    assert len(links) == _expected_link_count(), \
        f'侧栏链接应 {_expected_link_count()} 个，实际 {len(links)}'
    assert got == expected, f'入口账目不齐：缺 {expected - got}，多 {got - expected}'

    # 两层结构零残留 + 旧入口文案零残留
    for forbidden in ('data-bs-toggle="collapse"', 'class="collapse"',
                      'sc-subnav', 'sc-nav-item__chevron', '实用工具'):
        assert forbidden not in sidebar, f'扁平态残留两层结构标记：{forbidden}'
    # 2026-11-16：死亡上报 / 导入批次 已回到侧栏（社区治理 / 数据管理），仅年度缴费仍为撤项
    for retired in ('年度缴费',):
        assert retired not in sidebar, f'101 已融合的入口仍渲染：{retired}'

    # 外链 = 0
    assert 'http://' not in sidebar and 'https://' not in sidebar, '侧栏出现外链'


def test_flat_group_membership_from_nav_spec(isolated_app_factory):
    """逐组核对：组标签序列与成员 href 全部来自真源（admin 全可见）。"""
    app = _make_app(isolated_app_factory)
    client = app.test_client()
    _login(client)
    html = client.get('/dashboard/').get_data(as_text=True)
    menu = _menu(_sidebar(html))

    groups = menu.split('<div class="sc-sidebar__group">')[1:]
    assert len(groups) == len(NAV_GROUPS), \
        f'组数应为 {len(NAV_GROUPS)}，实际 {len(groups)}'

    labels = []
    group_hrefs = []
    for seg in groups:
        m = re.search(r'<h4 class="sc-sidebar__label">([^<]+)</h4>', seg)
        assert m, '组内缺少 h4 标签'
        labels.append(m.group(1))
        group_hrefs.append(set(_nav_links(seg)))

    assert labels == _nav_labels(), f'组标签应为 {_nav_labels()}，实际 {labels}'
    for group, hrefs in zip(NAV_GROUPS, group_hrefs):
        expected = _urls(app, [item['endpoint'] for item in group['items']])
        assert hrefs == expected, \
            f'组「{group["label"]}」成员不符：缺 {expected - hrefs}，多 {hrefs - expected}'


def test_flat_icons_present(isolated_app_factory):
    """全部侧栏入口（含底部设置）均带 lucide 图标；数量由真源派生。"""
    app = _make_app(isolated_app_factory)
    client = app.test_client()
    _login(client)
    html = client.get('/dashboard/').get_data(as_text=True)
    sidebar = _sidebar(html)
    entries = re.split(r'<a class="nav-link sc-nav-item', sidebar)[1:]
    assert len(entries) == _expected_link_count(), \
        f'侧栏入口应 {_expected_link_count()} 个，实际 {len(entries)}'
    no_icon = [e[:40] for e in entries if '<svg' not in e.split('</a>')[0]]
    assert not no_icon, f'以下入口缺图标：{no_icon}'


# ══════════ 2) 入口可达 ══════════

def test_entries_reachable(isolated_app_factory):
    """侧栏入口 GET 200；065 module_tabs 胶囊仍在宿主页渲染且 GET 200。"""
    app = _make_app(isolated_app_factory)
    client = app.test_client()
    _login(client)

    nav_urls = _nav_expected_urls(app)
    for url in sorted(nav_urls):
        resp = client.get(url)
        assert resp.status_code == 200, f'侧栏入口 {url} → {resp.status_code}'

    chip_urls = _urls(
        app, [e for elist in CHIP_ENDPOINTS.values() for e in elist])
    assert len(chip_urls) == sum(len(v) for v in CHIP_ENDPOINTS.values()), \
        '胶囊账目存在重复 URL（集合长度应等于 CHIP_ENDPOINTS 条目数）'
    for host_endpoint, sub in CHIP_ENDPOINTS.items():
        host_url = _urls(app, [host_endpoint]).pop()
        page = client.get(host_url).get_data(as_text=True)
        chips = re.findall(r'<a class="sc-chip[^"]*" href="([^"]+)"', page)
        chip_paths = {re.sub(r'#.*$', '', c) for c in chips}
        for e in sub:
            url = _urls(app, [e]).pop()
            assert url in chip_paths, \
                f'{e} 未在 {host_endpoint} 的 module_tabs 胶囊中渲染（二级入口丢失）'
            resp = client.get(url)
            assert resp.status_code == 200, f'胶囊入口 {url} → {resp.status_code}'

    # 唯一 URL = 侧栏入口 + 纯胶囊，两者必须零重叠。
    # 2026-11-16：死亡上报 / 导入批次 由纯胶囊升为侧栏入口，台账建表随「数据管理」组新增，
    # 故原「47 = 17 + 30」硬编码账目退役，改为「账目互斥」断言（避免每次调 IA 都要改魔数）。
    assert not (nav_urls & chip_urls), \
        f'侧栏与胶囊入口重叠（同一功能两条入口应合并）：{nav_urls & chip_urls}'


# ══════════ 3) spa.js 扁平适配 ══════════

def test_spa_js_flat_adaptation():
    with open(SPA_JS, encoding='utf-8') as f:
        js = f.read()
    # 073 增量：扁平组收空（组标题非 li，级联删不到；回退态组跳过）
    assert "$('#menu .sc-sidebar__group')" in js, 'spa.js 缺扁平组收空逻辑'
    m = re.search(r"\$\('#menu \.sc-sidebar__group'\)\.each\(function\(\) \{(.*?)\}\);", js, re.S)
    assert m and ".collapse" in m.group(1) and "d-none" in m.group(1), \
        '收空逻辑必须带 .collapse 回退守卫 + d-none 收空'
    # 3 处存量 .collapse 依赖原样保留（评估文档 §一：回退态必需）
    assert js.count("$('#menu .collapse')") == 3, \
        '存量 .collapse 依赖（收空折叠/按URL展开/高亮）必须原样保留 3 处'
    # SPA 拦截/标签记录对折叠触发器的跳过守卫保留
    assert js.count('data-bs-toggle') >= 2


# ══════════ 4) 单一真源 / 权限 / 回退态 ══════════

def test_nav_spec_is_single_source(isolated_app_factory):
    """运行期向真源追加伪组：侧栏必须即时反映（证明无副本）。"""
    app = _make_app(isolated_app_factory)
    client = app.test_client()
    _login(client)

    NAV_GROUPS.append({'key': 'zz-fake', 'label': '伪组', 'items': [
        {'endpoint': 'resident.add', 'label': '伪入口', 'icon': 'users'},
    ]})
    try:
        html = client.get('/dashboard/').get_data(as_text=True)
        sidebar = _sidebar(html)
        menu = _menu(sidebar)

        labels = re.findall(r'<h4 class="sc-sidebar__label">([^<]+)</h4>', menu)
        assert labels == _nav_labels(), \
            f'追加后组标签应为 {_nav_labels()}，实际 {labels}'

        expected = _nav_expected_urls(app)
        got = set(_nav_links(sidebar))
        assert got == expected, f'追加后账目不对：缺 {expected - got}，多 {got - expected}'
        assert '伪入口' in sidebar, '真源追加项未渲染'
    finally:
        NAV_GROUPS.pop()


def test_civil_director_visibility(isolated_app_factory):
    """民政主任：侧栏无导入批次（102 已撤侧栏项）；设置仅 admin 可见。"""
    from models import db, User, Role
    from werkzeug.security import generate_password_hash

    app = _make_app(isolated_app_factory)
    with app.app_context():
        role = Role.query.filter_by(name='民政主任').first()
        assert role is not None, '「民政主任」角色种子缺失'
        u = User(username='mz_director_073',
                 password_hash=generate_password_hash('x'),
                 role_id=role.id, is_active=True)
        db.session.add(u)
        db.session.commit()
        uid = u.id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid

    html = client.get('/dashboard/').get_data(as_text=True)
    sidebar = _sidebar(html)
    # 2026-11-16：「数据管理」组两项走项级权限门（app._NAV_ITEM_GUARDS）——
    # 民政主任无 special.import_batch / dynatable.view → 两项都不应出现；
    # 死亡上报为纯业务入口（无项级守卫）→ 应随社区治理组可见。
    assert '导入批次' not in sidebar, '民政主任无 special.import_batch，不应看到导入批次入口'
    assert '台账建表' not in sidebar, '民政主任无 dynatable.view，不应看到台账建表入口'
    assert '死亡上报' in sidebar, '死亡上报无项级守卫，业务入口应对民政主任可见'
    assert '<h4 class="sc-sidebar__label">系统</h4>' not in sidebar, \
        '102 后侧栏无「系统」组'

    expected = _nav_expected_urls(app)
    expected -= _urls(app, [NAV_SETTINGS['endpoint']])
    expected -= _urls(app, ['resident_enhance.import_batches_page', 'dynatable.index'])
    got = set(_nav_links(sidebar))
    assert got == expected, f'民政主任账目不对：缺 {expected - got}，多 {got - expected}'

    # 直接打开导入批次页：403（非 404/500）
    resp = client.get('/import/batches')
    assert resp.status_code == 403, f'导入批次页应 403，实际 {resp.status_code}'


def test_data_group_guards_positive(isolated_app_factory):
    """「数据管理」组项级守卫正例：各自权限到位即出现，缺位不出现（2026-11-16 新增组）。"""
    import json

    from models import db, User, Role
    from werkzeug.security import generate_password_hash

    app = _make_app(isolated_app_factory)
    with app.app_context():
        role = Role(name='数据维护_073', description='073 用例',
                    permissions=json.dumps({'dynatable.view': True}))
        db.session.add(role)
        db.session.commit()
        u = User(username='data_073', password_hash=generate_password_hash('x'),
                 role_id=role.id, is_active=True)
        db.session.add(u)
        db.session.commit()
        uid = u.id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid
    sidebar = _sidebar(client.get('/dashboard/').get_data(as_text=True))

    assert '台账建表' in sidebar and '/dynatable/' in sidebar, \
        '有 dynatable.view 应看到「台账建表」'
    assert '导入批次' not in sidebar, '无 special.import_batch 不应看到「导入批次」'
    # 仅剩一项时组仍渲染（组内 0 项才整组丢弃）
    assert '数据管理' in sidebar


def test_twolayer_fallback_smoke(isolated_app_factory):
    """SIDEBAR_LAYOUT 翻 'twolayer'：五组冒烟，折叠盒 / 触发器成对。"""
    with open(SIDEBAR_PARTIAL, encoding='utf-8') as f:
        src = f.read()
    assert src.count("{% set SIDEBAR_LAYOUT = 'flat' %}") == 1
    fallback_src = src.replace("{% set SIDEBAR_LAYOUT = 'flat' %}",
                               "{% set SIDEBAR_LAYOUT = 'twolayer' %}")

    app = _make_app(isolated_app_factory)
    user = SimpleNamespace(real_name='管理员', username='admin', role_id=1,
                           role=SimpleNamespace(name='超级系统管理员'))
    with app.test_request_context('/dashboard/'):
        tpl = app.jinja_env.from_string(fallback_src)
        html = tpl.render(current_user=user, system_title='村务管理',
                          has_perm=lambda p: True, nav_settings=NAV_SETTINGS)

    report = analyze(html)
    assert_balanced(report)

    labels = re.findall(r'<h4 class="sc-sidebar__label">([^<]+)</h4>', html)
    assert labels == _nav_labels(), f'回退态组标签应为 {_nav_labels()}，实际 {labels}'

    boxes = re.findall(r'<div class="collapse" id="(menu[A-Za-z]+)">', html)
    assert boxes, '回退态折叠盒缺失'
    for bid in boxes:
        assert f'href="#{bid}"' in html, f'{bid} 触发器缺失（不变量④）'
    assert 'menuTools' not in html, '回退态不应再有 menuTools 折叠盒'
