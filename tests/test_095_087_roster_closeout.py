# -*- coding: utf-8 -*-
"""tests/test_095_087_roster_closeout.py — 087 收口（095）：名册接软表数据层 + P3 技术债清零。

覆盖任务书交付物：
1. 名册两卡接真数据（module_key='safety' + group_name='应急管理' 只读查 dynamic_table，
   断言行数 = 软表记录数；无表空态引导，零伪造 sc-table）
2. 权限：卡片渲染沿用 safety.view；点入 /dynatable/* 由 dynatable.view 把关
   （二选一取「可见 + 403」，ADR-016 默认拒绝）
3. R-087-07 空隐患单显示「无隐患」不冒充闭环（数据口径不变）
4. R-087-08 种子守卫三查询并一条
5. R-087-09 死配置删除（checks.html __PAGE_CFG__ / emergency() 死参数）
6. R-087-10 列表隐患计数单查询聚合
7. R-087-11 073 账目补「应急页→综合检查」卡片入口可达性断言（能红）
8. R-087-13 /safety/checks 深链侧栏高亮别名（spa.js 静态断言，073 先例）

鉴权口径同 test_087：新面挂 safety_bp（ADR-016 已归类），漏归反例 403。
"""
import json
import re
import uuid
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

# R-095-01：路径基准跨平台（Windows 下 __file__ 为反斜杠，rsplit('/tests/') 切不开）
BASE = str(Path(__file__).resolve().parents[1])
CHECKS_HTML = BASE + '/templates/safety/checks.html'
EMERGENCY_HTML = BASE + '/templates/safety/emergency.html'
SPA_JS = BASE + '/static/js/spa.js'
CHECKS_JS = BASE + '/static/js/pages/safety_checks.js'


def _login(app):
    client = app.test_client()
    r = client.get('/auth/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', r.get_data(as_text=True))
    token = m.group(1) if m else ''
    client.post('/auth/login', data={'csrf_token': token, 'username': 'admin', 'password': 'admin123'})
    return client


def _perm_client(app, **perm_flags):
    """指定权限位的操作员（v3 权限格式，未列出的键默认拒绝）。"""
    from models import db, Role, User
    perms = {'schema_version': 3}
    perms.update(perm_flags)
    role_name = f'权995-{uuid.uuid4().hex[:8]}'
    with app.app_context():
        role = Role(name=role_name, permissions=json.dumps(perms, ensure_ascii=False), is_system=False)
        db.session.add(role)
        db.session.commit()
        u = User(username=f'op_095_{uuid.uuid4().hex[:6]}', password_hash=generate_password_hash('x'),
                 role_id=role.id, is_active=True)
        db.session.add(u)
        db.session.commit()
        uid = u.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid
    return client


def _seed_roster(app, code, name, records, module_key='safety', group_name='应急管理'):
    """ORM 直种软表元数据 + 记录行（只读复用 078 模型，不走上传流）。"""
    from models import db, DynamicRecord, DynamicTable
    with app.app_context():
        t = DynamicTable(code=code, name=name, module_key=module_key,
                         group_name=group_name, permission_key='dynatable')
        db.session.add(t)
        db.session.flush()
        for i in range(records):
            db.session.add(DynamicRecord(table_id=t.id, data=json.dumps({'f00': 'v%d' % i})))
        db.session.commit()
        return t.id


def _card_chunk(html, title):
    """按卡标题截取 <section class="sc-card"> 块（名册断言用）。"""
    for chunk in html.split('<section class="sc-card">')[1:]:
        if title in chunk.split('</section>')[0]:
            return chunk.split('</section>')[0]
    raise AssertionError(f'未找到卡片：{title}')


def _create_check(client, title='五一节前消防安全检查', date='2026-04-29'):
    resp = client.post('/safety/api/checks', json={
        'title': title, 'check_date': date, 'check_type': '节假日检查',
    }).get_json()
    assert resp['success'], resp.get('msg')
    return resp['data']


def _add_hazard(client, check_id, desc, level='一般'):
    resp = client.post(f'/safety/api/checks/{check_id}/hazards',
                       json={'description': desc, 'level': level}).get_json()
    assert resp['success'], resp.get('msg')
    return resp['data']


# ============ 1. 名册两卡接真数据（验收 1/2/3 的 SSR 面） ============

def test_roster_cards_empty_state(isolated_app_factory):
    """无软表：空态引导（去建表）+ 新建名册入口；零伪造（不出现 sc-table）。"""
    app = isolated_app_factory()
    client = _login(app)
    html = client.get('/safety/emergency').get_data(as_text=True)

    assert '工作队伍（名册）' in html and '储备物资（名册）' in html
    assert '尚未建表' in html and '应急管理' in html          # 空态文案含归属指引
    assert '/dynatable/upload' in html                        # 建表引导入口
    assert '0 张名册' in html
    assert 'sc-table' not in html                             # 不伪造表格
    assert '待软表引擎' not in html                            # 占位徽章退役


def test_roster_cards_real_tables(isolated_app_factory):
    """有软表：表名/记录数/最近更新/进入齐备，断言行数 = 软表记录数；分流与隔离正确。"""
    app = isolated_app_factory()
    _seed_roster(app, 'safety_team', '应急工作队伍名册', 7)
    _seed_roster(app, 'safety_supply', '应急储备物资名册', 11)
    _seed_roster(app, 'other_group', '安全其他台账', 99, group_name='人口与台账')   # 同模块异组
    _seed_roster(app, 'wrong_module', '养老队伍台账', 88, module_key='pension')     # 异模块同组
    client = _login(app)
    html = client.get('/safety/emergency').get_data(as_text=True)

    team = _card_chunk(html, '工作队伍（名册）')
    supply = _card_chunk(html, '储备物资（名册）')
    # 队伍卡：队伍表在场 + 记录数 = dynamic_record 行数 + 进入链接
    assert '应急工作队伍名册' in team and '<td>7</td>' in team
    assert '/dynatable/t/safety_team' in team
    assert '应急储备物资名册' not in team                     # 两卡分流不串
    # 物资卡同构
    assert '应急储备物资名册' in supply and '<td>11</td>' in supply
    assert '/dynatable/t/safety_supply' in supply
    assert '应急工作队伍名册' not in supply
    # 干扰项不可见：同模块异组 / 异模块同组都不出现在名册区
    assert '安全其他台账' not in html
    assert '养老队伍台账' not in html
    # 新建名册入口两卡齐备
    assert html.count('/dynatable/upload') >= 2


def test_roster_entry_guarded_by_dynatable_view(isolated_app_factory):
    """权限二选一（任务书交付物）：无 dynatable.view 者卡片可见（safety.view 渲染），
    点入明细 / 上传建表一律 403（ADR-016 默认拒绝，测试锁定）。"""
    app = isolated_app_factory()
    _seed_roster(app, 'safety_team', '应急工作队伍名册', 2)
    client = _perm_client(app, **{'safety.view': True, 'safety.edit': True,
                                  'dynatable.view': False, 'dynatable.add': False})
    resp = client.get('/safety/emergency')
    assert resp.status_code == 200                            # 卡片页可渲染（safety.view）
    assert '应急工作队伍名册' in resp.get_data(as_text=True)    # 卡片可见
    assert client.get('/dynatable/t/safety_team').status_code == 403   # 明细不可达
    assert client.get('/dynatable/upload').status_code == 403          # 建表入口同样把关


# ============ 2. R-087-07：空隐患单显示语义 ============

def test_r087_07_no_hazard_display_not_closed(isolated_app_factory):
    """hazard_total==0 单列「无隐患」，不冒充闭环；数据口径 status 保持 087 验收语义。"""
    app = isolated_app_factory()
    client = _login(app)
    check = _create_check(client)

    rows = client.get('/safety/api/checks').get_json()['data']
    assert rows[0]['hazard_total'] == 0
    assert rows[0]['status_display'] == '无隐患'              # 展示口径：不冒充闭环
    assert rows[0]['status'] == '已闭环'                       # 数据口径不变（B-10 已验收）

    _add_hazard(client, check['id'], '灭火器过期')
    rows = client.get('/safety/api/checks').get_json()['data']
    assert rows[0]['status_display'] == '整改中'

    # 第二条隐患也闭环 → 真闭环（有隐患且全闭环 = 已闭环；只有 0 隐患才显示「无隐患」）
    detail = client.get(f"/safety/api/checks/{check['id']}").get_json()['data']
    for hh in detail['hazards']:
        if hh['status'] != '已闭环':
            client.post(f"/safety/api/hazards/{hh['id']}/rectify", json={'rectify_note': '已处理'})
            client.post(f"/safety/api/hazards/{hh['id']}/review", json={'action': 'pass', 'review_note': '通过'})
    rows = client.get('/safety/api/checks').get_json()['data']
    assert rows[0]['status_display'] == '已闭环' and rows[0]['hazard_total'] == 1
    # 详情端点同契约
    detail = client.get(f"/safety/api/checks/{check['id']}").get_json()['data']
    assert detail['status_display'] == '已闭环'


def test_r087_07_js_renders_no_hazard_badge():
    """列表 JS 按 status_display 渲染三态徽章（静态断言，073 spa.js 先例）。"""
    with open(CHECKS_JS, encoding='utf-8') as f:
        js = f.read()
    assert 'status_display' in js, 'JS 未消费 status_display（R-087-07 前端半修复）'
    assert 'sc-badge--muted' in js, '无隐患徽章样式缺失'


# ============ 3. R-087-08：种子守卫单查询 ============

def test_r087_08_seed_guard_single_query(isolated_app_factory):
    """种子就位态守卫只发 1 次查询（原 3 次）；条件缺失时仍幂等补种。"""
    from sqlalchemy import event

    from models import db
    from routes.safety_emergency import _ensure_emergency_seed

    app = isolated_app_factory()
    client = _login(app)
    client.get('/safety/emergency')          # 首个请求完成 m028 播种

    with app.app_context():
        counter = {'n': 0}

        def _count(*_args, **_kw):
            counter['n'] += 1

        event.listen(db.engine, 'before_cursor_execute', _count)
        try:
            _ensure_emergency_seed()
        finally:
            event.remove(db.engine, 'before_cursor_execute', _count)
        assert counter['n'] == 1, f'R-087-08：种子就位态应 1 次查询，实际 {counter["n"]}'

        # 功能不变：删档案目录 → 守卫幂等补种
        from models.safety import SafetyNode
        folder = SafetyNode.query.filter_by(parent_id=None, name='应急工作方案').first()
        db.session.delete(folder)
        db.session.commit()
        _ensure_emergency_seed()
        assert SafetyNode.query.filter_by(parent_id=None, name='应急工作方案').count() == 1


# ============ 4. R-087-09：死配置删除 ============

def test_r087_09_dead_config_removed():
    """checks.html 不再注入 JS 未读的 __PAGE_CFG__.hazardStatuses（删除口径）。"""
    with open(CHECKS_HTML, encoding='utf-8') as f:
        html = f.read()
    assert '__PAGE_CFG__' not in html, '死配置 __PAGE_CFG__ 未删除（R-087-09）'
    assert 'hazardStatuses' not in html, '死配置 hazardStatuses 未删除（R-087-09）'


def test_r087_09_checks_page_still_renders(isolated_app_factory):
    """删死配置后检查台账页照常渲染：spec-02 六件套 + 枚举下拉在位。"""
    app = isolated_app_factory()
    client = _login(app)
    html = client.get('/safety/checks').get_data(as_text=True)
    assert 'class="sc-table"' in html and 'class="sc-pagination"' in html
    assert '常规检查' in html and '重大' in html            # 枚举服务端渲染未被误伤


def test_r087_09_emergency_dead_params_removed():
    """emergency() 不再传模板未用的 check_types/hazard_levels/softtable_ready。"""
    with open(BASE + '/routes/safety_emergency.py', encoding='utf-8') as f:
        src = f.read()
    fn = src.split("def emergency(")[1].split("def checks_page(")[0]
    for dead in ('check_types', 'hazard_levels', 'softtable_ready'):
        assert dead not in fn, f'emergency() 死参数 {dead} 未删除（R-087-09）'


# ============ 5. R-087-10：列表隐患计数单查询聚合 ============

def test_r087_10_list_hazard_counts_single_query(isolated_app_factory):
    """20 行列表场景：隐患表相关查询从 ~40 次（每行 2 count）降到 1 次 group by 聚合。"""
    from sqlalchemy import event

    from models import db

    app = isolated_app_factory()
    client = _login(app)
    for i in range(20):
        check = _create_check(client, title=f'批量检查{i:02d}', date='2026-05-01')
        _add_hazard(client, check['id'], f'隐患{i:02d}')
        if i % 2 == 0:
            h = client.get(f"/safety/api/checks/{check['id']}").get_json()['data']['hazards'][0]
            client.post(f"/safety/api/hazards/{h['id']}/rectify", json={'rectify_note': '已处理'})
            client.post(f"/safety/api/hazards/{h['id']}/review", json={'action': 'pass', 'review_note': '通过'})

    counter = {'n': 0}

    def _count(*_args, **_kw):
        counter['n'] += 1

    with app.app_context():
        event.listen(db.engine, 'before_cursor_execute', _count)
        try:
            resp = client.get('/safety/api/checks?page=1&per_page=20').get_json()
        finally:
            event.remove(db.engine, 'before_cursor_execute', _count)
    assert resp['success'] and resp['total'] == 20

    # 聚合正确性：0 隐患行 status_display=无隐患；1 隐患已闭环行=已闭环；1 隐患未闭环行=整改中
    by_title = {c['title']: c for c in resp['data']}
    assert by_title['批量检查00']['status_display'] == '已闭环'
    assert by_title['批量检查01']['status_display'] == '整改中'
    assert by_title['批量检查01']['hazard_open'] == 1

    # 查询数：隐患表仅 1 次聚合（旧实现每行 2 次 count ≈ 40 次）
    assert counter['n'] <= 15, f'R-087-10：整页请求查询数应常数级，实际 {counter["n"]}'


# ============ 6. R-087-11：073 账目补卡片入口可达性断言（能红） ============

def test_r087_11_emergency_to_checks_entry(isolated_app_factory):
    """应急页→综合检查卡片入口在场且可达（073 账目不覆盖页面内容级入口，本用例补登）。"""
    app = isolated_app_factory()
    client = _login(app)
    html = client.get('/safety/emergency').get_data(as_text=True)
    card = _card_chunk(html, '综合安全检查（闭环台账）')
    assert 'href="/safety/checks"' in card, '应急页检查卡片入口丢失（073 账目应红）'
    assert client.get('/safety/checks').status_code == 200   # 入口可达


# ============ 7. R-087-13：checks 深链侧栏高亮别名 ============

def test_r087_13_spa_alias_highlights_emergency():
    """/safety/checks 深链 → 应急管理叶高亮（spa.js 路径别名，静态断言）。"""
    with open(SPA_JS, encoding='utf-8') as f:
        js = f.read()
    assert 'MENU_PATH_ALIAS' in js, 'spa.js 缺深链高亮别名表（R-087-13）'
    assert "'/safety/checks': '/safety/emergency'" in js, 'checks 深链别名未登记（R-087-13）'
    # 别名消费点必须在 exact 匹配之前生效
    assert js.index('MENU_PATH_ALIAS[current]') < js.index("$('#menu a[href]')"), \
        '别名应在菜单匹配前归一 current'


def test_r087_13_checks_deep_link_renders_shell(isolated_app_factory):
    """深链访问 /safety/checks：整壳渲染正常（侧栏 13 入口在页面中，别名匹配目标在场）。"""
    app = isolated_app_factory()
    client = _login(app)
    html = client.get('/safety/checks').get_data(as_text=True)
    assert '/safety/emergency' in html                       # 别名目标入口随壳渲染
    assert 'id="menu"' in html
