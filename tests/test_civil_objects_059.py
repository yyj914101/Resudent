# -*- coding: utf-8 -*-
"""tests/test_civil_objects_059.py — 民政对象 10 项结构守卫（059）。

守护验收标准 1/2/5/6 与字段清单关键形态：
  1) 10 页全部接 065 胶囊宏：两行 5+5 + 行标签 + 唯一激活 + href 带 #tab=
  2) 低保 17 数据列（067 补保障状态）/ 残疾 17 数据列 + 列选择器 + 横向滚动
  3) 关爱对象 D-21 C：/care/ 不再有入口页，3 个页内胶囊（默认特困）
  4) 统计表 4 段 / 死亡对账 5 tab + hash 承载
  5) 6 处 bug 的“正确形态”不回流（残疾字典/儿童 birth_date/公益岗导出/花名册列）
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

# (endpoint, active_key, page title)
PAGES = [
    ('dibao.dibao_list', 'dibao', '低保人员查询'),
    ('disabled.disabled_list', 'disabled', '残疾人查询'),
    ('care.care_list', 'care', '特困供养人员'),
    ('care.special_list', 'care', '特困供养人员'),
    ('care.child_list', 'care', '事实无人抚养儿童'),
    ('care.target_list', 'care', '一老一小关怀台账'),
    ('welfare.page_list', 'welfare', '公益岗查询'),
    ('resident.vulnerable_page', 'vulnerable', '弱势群体台账'),
    ('youshu.list_page', 'youshu', '优抚对象'),
    ('report.stats_table', 'stats', '一老一小统计表'),
    ('report.roster_view', 'roster', '民政对象花名册'),
    # 131：报表审批（通用 Word 表单）降级为民政对象·报表行第三枚胶囊，页壳同 civil
    ('word_form.index', 'word-form', '报表审批'),
]

CHIP_RE = re.compile(r'class="sc-chip(?: is-active)?"')
SUBCHIP_RE = re.compile(r'class="sc-subchip(?: is-active)?"')


def _main_heads(html, table_id):
    """主数据表表头列名（按 table id 取作用域）。

    092 起同页允许「办事流程模板」附件区等第二张表：页级 th 正则会把新增
    section 的表头一并计入（disabled 实测 25==20 误报）。列契约锚定主表，
    断言数值与必含列不放宽。
    """
    m = re.search(r'<table[^>]*id="%s"[^>]*>(.*?)</table>' % table_id, html, re.S)
    assert m, '主数据表 %s 不存在' % table_id
    ths = re.findall(r'<th(?: [^>]*)?>(.*?)</th>', m.group(1), re.S)
    return [t.strip() for t in ths if t.strip() and 'sc-chip' not in t]


def _login(app):
    client = app.test_client()
    r = client.get('/auth/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', r.get_data(as_text=True))
    token = m.group(1) if m else ''
    client.post('/auth/login', data={
        'csrf_token': token, 'username': ADMIN_USER, 'password': ADMIN_PASS,
    })
    return client


def _path(app, endpoint):
    with app.test_request_context('/'):
        from flask import url_for
        return url_for(endpoint)


def _assert_civil_chips(html, active_key):
    # 076：045c0b0 胶囊栅格对齐后，civil 两行走 sc-chips__grid 平铺（label 与胶囊进单网格）；
    # 091/084：civil 由 5+5 变 6+5（就业服务 +1），栅格放宽为「多行非等数 + 最大列数」，
    # grid-template-columns: 72px repeat(6, max-content)；行数/标签/胶囊数/激活态契约不变。
    assert html.count('sc-chips__grid') == 1
    assert html.count('sc-chips__row') == 0, 'civil 已改栅格平铺，不应残留逐行 __row'
    assert html.count('sc-chips__label') == 2
    # 2026-11-16：8 枚 = 对象查询 6（含新增优抚对象）+ 报表 2（就业服务/对账/走访迁出）
    # 131：+1「报表审批」→ 9 枚 = 对象查询 6 + 报表 3
    assert len(CHIP_RE.findall(html)) == 9
    assert html.count('class="sc-chip is-active"') == 1, active_key
    assert '#tab=%s' % active_key in html


def test_civil_pages_share_two_row_chips_and_affixes(isolated_app_factory):
    app = isolated_app_factory()
    client = _login(app)
    for endpoint, active_key, title in PAGES:
        path = _path(app, endpoint)
        resp = client.get(path)
        assert resp.status_code == 200, (endpoint, resp.status_code)
        html = resp.get_data(as_text=True)
        _assert_civil_chips(html, active_key)
        assert 'sc-page-head__name' in html and title in html, endpoint
        # 137（补丁 B-2）：固定行标签带 style="grid-column:1" 列对齐，断言放宽为「标签 + 文本」
        assert re.search(r'class="sc-chips__label"[^>]*>对象查询<', html)
        assert re.search(r'class="sc-chips__label"[^>]*>报表<', html)
        assert 'css/pages/civil_pages.css' in html
        assert 'js/civil_pages.js' in html


def test_wide_tables_have_17_17_data_cols_and_col_picker(isolated_app_factory):
    """067：低保 17 数据列（新增保障状态，ADR-024②）/ 残疾 19 数据列（093 B-6 增服务事项/办理状态）+ 列选择器。"""
    app = isolated_app_factory()
    client = _login(app)

    dibao = client.get(_path(app, 'dibao.dibao_list')).get_data(as_text=True)
    assert 'data-col-picker-key="dibao"' in dibao
    dibao_heads = _main_heads(dibao, 'dibaoTable')
    assert dibao_heads[-1] == '操作'
    assert len(dibao_heads) == 18          # 17 数据列（含 067 保障状态） + 操作
    for col in ('所属地区', '户主姓名', '成员姓名', '是否户主', '救助类别', '户救助金额', '保障状态'):
        assert col in dibao_heads

    disabled = client.get(_path(app, 'disabled.disabled_list')).get_data(as_text=True)
    assert 'data-col-picker-key="disabled"' in disabled
    disabled_heads = _main_heads(disabled, 'disabledTable')
    assert disabled_heads[-1] == '操作'
    assert len(disabled_heads) == 20       # 19 数据列（093 B-6 增服务事项/办理状态） + 操作
    for col in ('残疾证号', '残疾类别', '残疾等级', '生活补贴', '护理补贴', '服务事项', '办理状态'):
        assert col in disabled_heads


def test_care_single_page_with_three_subchips(isolated_app_factory):
    app = isolated_app_factory()
    client = _login(app)
    for endpoint, active in (('care.care_list', 'special'),
                             ('care.special_list', 'special'),
                             ('care.child_list', 'child'),
                             ('care.target_list', 'target')):
        html = client.get(_path(app, endpoint)).get_data(as_text=True)
        assert len(SUBCHIP_RE.findall(html)) == 3, endpoint
        assert html.count('class="sc-subchip is-active"') == 1, endpoint
        assert '#tab=%s' % active in html
        # D-21 C：入口页卡片已取消
        assert '进入管理' not in html, endpoint


def test_stats_four_age_bands_and_reconcile_five_tabs(isolated_app_factory):
    app = isolated_app_factory()
    client = _login(app)

    stats = client.get(_path(app, 'report.stats_table')).get_data(as_text=True)
    for band in ('0-17', '18-59', '60-79', '80+'):
        assert band in stats

    rec = client.get(_path(app, 'reconcile.index')).get_data(as_text=True)
    assert rec.count('id="typeTabs"') == 1
    assert rec.count('data-key=') == 5
    for key in ('all', 'registered', 'suspect', 'no-file', 'outside'):
        assert '#tab=%s' % key in rec


def test_bug_shapes_not_copied():
    """静态守卫：6 处 bug 的正确形态（B1/B3/B4/B5/B6）。"""
    js = os.path.join(ROOT, 'static', 'js', 'pages')
    disabled_js = open(os.path.join(js, 'disabled_list.js'), encoding='utf-8').read()
    assert "dictSelect('残疾类型'" in disabled_js
    assert "dictSelect('残疾类别'" not in disabled_js

    special_js = open(os.path.join(js, 'care_special_list.js'), encoding='utf-8').read()
    # 076：045c0b0 弹窗分组卡片式改版后，GLB-45 意图（change_time 渲染为 date 而非 text）
    # 由 f('变更时间','change_time','date') 显式声明承载，原 k==='change_time' 分支退役
    assert "'change_time','date'" in special_js

    child_js = open(os.path.join(js, 'care_child_list.js'), encoding='utf-8').read()
    # 076：045c0b0 分组卡片式后，B4/GLB-46 意图（详情固定 12 字段含出生日期）由 field() 承载；
    # 2026-11-11 详情 v2（ui1.md 只读规范）改 {} 对象字面量 + d2ValMono 渲染，字段意图不变
    assert "label: '出生日期'" in child_js and "d2ValMono(d2.birth_date)" in child_js

    welfare_js = open(os.path.join(js, 'welfare_job_list.js'), encoding='utf-8').read()
    assert "#btnExport" in welfare_js and "searchForm" in welfare_js

    roster_tpl = open(os.path.join(ROOT, 'templates', 'report', 'roster.html'), encoding='utf-8').read()
    assert "headers[3] == '出生日期'" in roster_tpl
    assert 'r.detail' in roster_tpl
