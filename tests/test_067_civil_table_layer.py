# -*- coding: utf-8 -*-
"""tests/test_067_civil_table_layer.py — 表格层统一换壳 A 结构守卫（067）。

真源：任务书 067 + ADR-024①②（列口径=实库真实字段；徽章=真实字段渲染）
      + audit3 基准（stage3-sample-vs-page-audit3-20261030.md）。
守护：
  1) 059 十二页 + 061 四页 DOM 均含 sc-table + sc-pagination；旧 bootstrap 表格、
     旧「跳至 N 页 GO」分页、sc-filter-card/sc-table-card 旧壳全站下线
  2) 低保保障状态列（support_status）模型/迁移/筛选/录入入口闭环
  3) 残疾页泄漏开发文案已删
  4) 徽章只由真实字段渲染：JS/模板层 sc-badge 断言 + BS badge 残留即红
  5) CSS 收口：civil_pages.css 不再有被通用件替代的规则
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (route, 页面名, 是否要求 sc-pagination, 是否要求 sc-table)
PAGES = [
    ('/dibao/', '低保人员查询', True, True),
    ('/disabled/', '残疾人查询', True, True),
    ('/care/special', '特困供养人员', True, True),
    ('/care/child', '事实无人抚养儿童', True, True),
    ('/care/target', '一老一小关怀台账', True, True),
    ('/welfare/', '公益岗查询', True, True),
    ('/resident/vulnerable', '弱势群体台账', True, True),
    ('/report/stats-table', '一老一小统计表', False, True),   # 交叉表无分页（v8-07 同）；空库渲染空态
    ('/report/roster', '民政对象花名册', True, True),
    ('/reconcile/', '死亡名单对账', True, True),              # 统计条形态（v8-09 无页码签）
    ('/visit/', '走访计划', True, True),
    ('/visit/history', '走访记录', True, True),
    ('/certificate/', '证明开具', True, True),                # 开具记录表（recordPane）
    ('/medical-insurance/', '医保缴费', True, True),          # 表头由 JS 渲染（SSR 只有 sc-table__head）
    ('/notice/', '补贴公示', False, False),                   # 工作台卡片布局（v7-07），无列表表格
    ('/safety/', '安全工作', True, True),
]

# 旧结构黑名单：这些标记在 059/061 受检页出现即红
LEGACY_MARKS = (
    'table table-sm table-bordered',
    'table table-hover table-bordered',
    'pagination pagination-sm',
    'sc-filter-card',
    'sc-table-card',
    'pageJump',
)


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    for k, v in {
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_LAN_AUTHORIZED": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _login(app):
    client = app.test_client()
    r = client.get('/auth/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', r.get_data(as_text=True))
    token = m.group(1) if m else ''
    client.post('/auth/login', data={'csrf_token': token, 'username': 'admin', 'password': 'admin123'})
    return client


def _pages_html(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = _login(app)
    out = {}
    for route, name, _need_pg, _need_table in PAGES:
        resp = client.get(route)
        assert resp.status_code == 200, (route, resp.status_code)
        out[route] = resp.get_data(as_text=True)
    return out


def test_all_pages_have_sc_table_and_no_legacy(tmp_path, monkeypatch):
    """验收标准 1：sc-table 在场；旧表格/旧分页/旧壳/GO 分页全站下线。"""
    pages = _pages_html(tmp_path, monkeypatch)
    for route, name, _need_pg, need_table in PAGES:
        html = pages[route]
        if need_table:
            # 统计表空库渲染空态；医保表头由 JS 渲染（SSR 只有 sc-table__head 壳）
            has_table = ('sc-table__th' in html or 'sc-table__head' in html
                         or '暂无统计数据' in html)
            assert has_table, f'{route}（{name}）缺 sc-table 表头'
        for mark in LEGACY_MARKS:
            assert mark not in html, f'{route}（{name}）残留旧标记 {mark!r}'


def test_paginated_pages_have_sc_pagination(tmp_path, monkeypatch):
    """验收标准 1：受检分页页含 sc-pagination（统计条 + 页码签容器）。"""
    pages = _pages_html(tmp_path, monkeypatch)
    for route, name, need_pg, need_table in PAGES:
        html = pages[route]
        if need_pg:
            assert 'class="sc-pagination"' in html, f'{route}（{name}）缺 sc-pagination'
            assert 'sc-pagination__stat' in html, f'{route}（{name}）缺分页统计条'


def test_leaked_dev_copy_removed(tmp_path, monkeypatch):
    """验收标准 4：残疾人查询工具栏泄漏文案删除（模板层）。"""
    tpl = open(os.path.join(ROOT, 'templates', 'disabled', 'list.html'), encoding='utf-8').read()
    assert '400ms 防抖' not in tpl, '泄漏开发文案仍在 disabled/list.html'
    for _, html in _pages_html(tmp_path, monkeypatch).items():
        assert '400ms 防抖' not in html


def test_support_status_field_closed_loop(tmp_path, monkeypatch):
    """验收标准 2/3：低保保障状态字段补建闭环（ADR-024②）。"""
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        from models import db
        from models.dibao import Dibao
        cols = {r[1] for r in db.session.execute(db.text('PRAGMA table_info(dibao)')).fetchall()}
        assert 'support_status' in cols, 'dibao 表缺 support_status 列'
        assert Dibao.__table__.c.support_status.default.arg == '已认定'

    html = _pages_html(tmp_path, monkeypatch)['/dibao/']
    assert '<th class="sc-table__th">保障状态</th>' in html, '低保表缺保障状态列'
    assert 'id="searchSupport"' in html, '低保工具条缺保障状态筛选'
    assert 'name="support_status"' in html, '低保表单缺保障状态录入入口'
    # 筛选走真实字段（路由层过滤参数）
    route_src = open(os.path.join(ROOT, 'routes', 'dibao.py'), encoding='utf-8').read()
    assert "Dibao.support_status == support_status" in route_src

    # 迁移脚本落库（宪法 §1.6：结构变更一律落 DDL 脚本）
    assert os.path.exists(os.path.join(ROOT, 'scripts', 'migrations', 'm023_dibao_support_status.py'))
    app_src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
    assert "ensure_columns('dibao', {'support_status':" in app_src, '存量库幂等补列缺失'


def test_badges_only_from_real_fields():
    """验收标准 2：徽章由真实字段渲染；JS/模板层 BS 徽章与按钮残留即红。"""
    js_dir = os.path.join(ROOT, 'static', 'js', 'pages')

    dibao_js = open(os.path.join(js_dir, 'dibao_list.js'), encoding='utf-8').read()
    assert "class=\"sc-badge '" in dibao_js and 'support_status' in dibao_js
    for tone in ('sc-badge--success', 'sc-badge--warning', 'sc-badge--muted'):
        assert tone in dibao_js, f'低保徽章缺档位 {tone}'
    assert 'class="btn ' not in dibao_js

    target_js = open(os.path.join(js_dir, 'care_target_list.js'), encoding='utf-8').read()
    assert 'guardian_consent' in target_js and 'sc-badge--warning' in target_js

    plan_js = open(os.path.join(js_dir, 'visit_plan_list.js'), encoding='utf-8').read()
    assert "s === '进行中'" in plan_js and 'sc-badge--primary' in plan_js

    reconcile_js = open(os.path.join(js_dir, 'reconcile_task_list.js'), encoding='utf-8').read()
    assert 'STATUS_LABEL' in reconcile_js and 'MATCH_BADGE' in reconcile_js
    assert 'class="badge' not in reconcile_js and 'class="btn ' not in reconcile_js

    history_js = open(os.path.join(js_dir, 'visit_history.js'), encoding='utf-8').read()
    assert 'sc-badge--warning' in history_js and 'class="btn ' not in history_js

    notice_js = open(os.path.join(js_dir, 'notice_list.js'), encoding='utf-8').read()
    assert 'notice-state-pill' in notice_js, '补贴公示状态胶囊 .notice-state-pill 缺失'
    assert 'rec.status' in notice_js, '状态胶囊必须由真实字段 rec.status 驱动'
    assert 'class="badge' not in notice_js, '补贴公示不得回退到 BS badge'
    notice_tpl = open(os.path.join(ROOT, 'templates', 'notice', 'list.html'), encoding='utf-8').read()
    state_pill = re.search(r'<[^>]*id="statePill"[^>]*>', notice_tpl)
    assert state_pill and 'notice-state-pill' in state_pill.group(0), \
        '补贴公示页头状态胶囊 #statePill 必须带 .notice-state-pill'

    # crud.js 引擎：tag 徽章 + 行内操作均换 sc-（唯一 tagMap 消费方 medical）
    crud_js = open(os.path.join(ROOT, 'static', 'js', 'crud.js'), encoding='utf-8').read()
    assert 'class="badge bg-' not in crud_js
    assert "class=\"sc-badge sc-badge--' + tagColor" in crud_js
    assert 'sc-row-actions' in crud_js

    # certificate 记录表徽章（模板内联 JS）：status 真实字段
    cert_tpl = open(os.path.join(ROOT, 'templates', 'certificate', 'list.html'), encoding='utf-8').read()
    assert 'sc-badge--danger">已作废' in cert_tpl and 'cert-status-badge' not in cert_tpl


def test_civil_css_cleanup():
    """被通用件替代的规则已删（交付物：civil_pages.css 清理）。"""
    css = open(os.path.join(ROOT, 'static', 'css', 'pages', 'civil_pages.css'), encoding='utf-8').read()
    for dead in ('.sc-filter-card', '.sc-table-card', '.pagination', '.form-control', '.btn-outline-primary'):
        assert dead not in css, f'civil_pages.css 残留被替代规则 {dead}'
    for alive in ('.sc-subchip', '.sc-stat', '.scope-panel'):
        assert alive in css, f'civil_pages.css 误删仍用规则 {alive}'
    # 075 RC-18：.sc-table-toolbar/.sc-col-count 上收 components.css 全局层（GLB-02 组件真源唯一），
    # veteran 复用；civil_pages.css 不再保留页面级平行定义
    assert '.sc-table-toolbar' not in css and '.sc-col-count' not in css, \
        '列计数行样式应只在全局层 components.css'
    gcss = open(os.path.join(ROOT, 'static', 'css', 'components.css'), encoding='utf-8').read()
    assert '.sc-table-toolbar' in gcss and '.sc-col-count' in gcss, \
        'components.css 缺全局列计数行样式'


def test_medical_page_head_responsive_guard():
    """067-FIX P1-01：医保页 7 操作钮在 1024 不得挤压标题/溢出页头。"""
    css = open(os.path.join(ROOT, 'static', 'css', 'pages', 'medical_insurance.css'),
               encoding='utf-8').read()
    assert '@media (max-width: 1199.98px)' in css
    assert '.medical-page .sc-page-head__actions' in css
    assert 'flex-wrap: wrap' in css and 'width: 100%' in css


def test_dibao_list_api_support_status_filter(tmp_path, monkeypatch):
    """列表接口按保障状态过滤（真实字段查询）。"""
    app = _make_app(tmp_path, monkeypatch)
    client = _login(app)
    with app.app_context():
        from models import db
        from models.dibao import Dibao
        db.session.add(Dibao(name='张三', support_status='复审中'))
        db.session.add(Dibao(name='李四', support_status='已认定'))
        db.session.commit()
    resp = client.get('/dibao/api/list?support_status=%E5%A4%8D%E5%AE%A1%E4%B8%AD')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success']
    names = [it['name'] for it in data['data']['data']]
    assert names == ['张三'], names
    assert data['data']['data'][0]['support_status'] == '复审中'
