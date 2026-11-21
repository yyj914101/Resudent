# -*- coding: utf-8 -*-
"""tests/test_062_resident_query_structure.py — 居民查询页 v4.0 结构守卫（062 P4）。

真源：062 任务书 P4 + docs/design/062-P4-居民查询-架构设计-2026-10-29.md。
守护：
  1) 065 胶囊：module_tabs('resident','query') 且「居民查询」唯一激活（#tab=query）
  2) 页头：sc-page-head + 唯一 primary（新增居民）+ 批量导入/导出
  3) 搜索区：sc-toolbar 基础 2 项 + 高级 10 控件（默认收起）+ 冻结 id
  4) 模式切换：data-mode-group 容器 + 2 个 .mode-btn.sc-chip（明细 active）
  5) 双段表：sc-table + detailHead 11 列 / householdHead 10 列 + 分页/跳页契约 id
  6) 弹窗：3 个 sc-modal 壳 + addMemberForm 15 字段 / 9 data-dict / 9 必填星
  7) ?name= seam：服务端注入 window.__RESIDENT_INIT__ + JS 首查一次（无 name 不查）
  8) 页面级 CSS 已删（resident_list.css 不再被引用；JS 渲染层无裸色值）
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 冻结契约（设计文档 §7）：模板必须出现的 DOM id
HTML_IDS = [
    'searchIdCard', 'searchName', 'searchAgeMin', 'searchAgeMax', 'searchGender',
    'searchStatus', 'searchFlowStatus', 'searchVillageGroup', 'searchPoliticsStatus',
    'searchHouseholdProp', 'searchPhone', 'searchAddress',
    'btnSearch', 'btnReset', 'btnToggleAdv', 'advancedSearch',
    'statsText', 'modeToggle',
    'residentTable', 'tableHead', 'detailHead', 'householdHead', 'residentTbody',
    'pagination', 'jumpPage', 'btnJumpPage', 'totalCount', 'pageInfo', 'footerInfo', 'btnExport',
    'detailModal', 'detailModalBody', 'detailModalTitle',
    'memberDetailModal', 'memberDetailBody', 'memberDetailTitle',
    'addMemberModal', 'addMemberForm', 'btnAddMemberSubmit',
]

# 冻结契约：JS 渲染层动态生成的 id（只在函数体里出现）
JS_IDS = [
    'editHouseholdForm', 'memberEditForm', 'btnSaveHousehold', 'btnAddMember',
    'chOldRelation', 'chSelectedName', 'btnChSubmit', 'subsidyOverviewArea',
]

ADD_MEMBER_FIELDS = [
    'household_code', 'name', 'id_card', 'gender', 'birth_date', 'relationship',
    'village_group', 'nation', 'phone', 'politics_status', 'education',
    'health_status', 'marital_status', 'military_status', 'bank_name',
    'bank_card', 'remark',
]


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


def _page_html(tmp_path, monkeypatch, query=''):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # 首装种子 admin
    resp = client.get('/resident/' + query)
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


def _js(name):
    with open(os.path.join(BASE, 'static', 'js', 'pages', name), encoding='utf-8') as f:
        return f.read()


def _block(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    assert i < j, f'锚点顺序异常：{start} 应在 {end} 之前'
    return html[i:j]


def test_chips_resident_query_active(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    assert html.count('sc-chip is-active') == 1
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=query"', html)
    for frag in ('居民查询', '新增居民', '店铺信息'):
        assert frag in html


def test_page_head_single_primary(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    head = _block(html, '<header class="sc-page-head">', '</header>')
    assert '居民查询' in head
    assert head.count('sc-btn--primary') == 1, '页头区主按钮必须唯一（§2.2）'
    for frag in ('新增居民', '批量导入', '导出'):
        assert frag in head


def test_search_toolbar_basic_and_advanced(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    adv = _block(html, 'id="advancedSearch"', 'id="statsText"')
    assert 'style="display:none"' in adv.split('>')[0], '高级筛选默认收起'
    assert adv.count('class="sc-field"') == 10, '高级筛选 10 控件（设计 §3）'
    for dom_id in ('searchAgeMin', 'searchAgeMax', 'searchGender', 'searchStatus',
                   'searchFlowStatus', 'searchVillageGroup', 'searchPoliticsStatus',
                   'searchHouseholdProp', 'searchPhone', 'searchAddress'):
        assert f'id="{dom_id}"' in adv, f'高级筛选缺 #{dom_id}'
    basic = html[:html.index('id="advancedSearch"')]
    assert 'sc-toolbar' in basic
    for dom_id in ('searchIdCard', 'searchName', 'btnSearch', 'btnReset', 'btnToggleAdv'):
        assert f'id="{dom_id}"' in basic, f'基础搜索缺 #{dom_id}'


def test_mode_toggle_two_chips(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    group = _block(html, 'data-mode-group', '</div>')
    assert 'id="modeToggle"' in group
    assert group.count('mode-btn') == 2
    assert re.search(r'class="sc-chip mode-btn is-active"[^>]*data-mode="detail"', group)
    assert re.search(r'class="sc-chip mode-btn"[^>]*data-mode="household"', group)


def test_dual_head_columns(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    assert '<table class="sc-table" id="residentTable">' in html
    detail = re.search(r'<tr id="detailHead">(.*?)</tr>', html, re.S).group(1)
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', detail)
    assert cols == ['#', '姓名', '身份证号', '民族', '性别', '年龄', '所在组',
                    '政治面貌', '与户主关系', '状态', '流动'], f'明细 11 列，实得 {cols}'
    household = re.search(r'<tr id="householdHead"[^>]*>(.*?)</tr>', html, re.S).group(1)
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', household)
    assert cols == ['#', '户主姓名', '性别', '年龄', '所在组', '家庭住址',
                    '户主身份证号', '家庭人数', '户属性', '电话'], f'按户 10 列，实得 {cols}'


def test_pagination_contract_ids(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('pagination', 'jumpPage', 'btnJumpPage', 'totalCount', 'pageInfo',
                   'footerInfo', 'statsText'):
        assert f'id="{dom_id}"' in html, f'缺分页契约 #{dom_id}'
    assert 'sc-pagination__pager' in html
    for dom_id in HTML_IDS:
        assert f'id="{dom_id}"' in html, f'冻结契约缺 #{dom_id}'


def test_modals_structure_and_add_member_form(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    assert html.count('class="sc-modal') >= 3, '3 个弹窗壳均应换 sc-modal'
    form = _block(html, 'id="addMemberForm"', '</form>')
    for name in ADD_MEMBER_FIELDS:
        assert f'name="{name}"' in form, f'新增成员表单缺字段 {name}'
    assert form.count('data-dict="') == 9, '字典下拉 9 项（data-dict 契约）'
    assert form.count('sc-field__required') == 9, '必填 9 项（红星口径）'
    for anchor in ('dict-sm', 'data-bs-dismiss="modal"'):
        assert anchor in form or anchor in html


def test_name_seam_injected_and_first_query(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch, query='?name=张三')
    m = re.search(r'window\.__RESIDENT_INIT__ = \{ name: ("[^"]*") \};', html)
    assert m, '模板缺 __RESIDENT_INIT__ 注入'
    assert json.loads(m.group(1)) == '张三', '?name= 未注入模板'

    js = _js('resident_list.js')
    assert 'window.__RESIDENT_INIT__' in js
    assert "if (init.name) { $('#searchName').val(init.name); loadData(); }" in js, \
        '首查必须回填 #searchName 且只触发一次 loadData'

    route = open(os.path.join(BASE, 'routes', 'resident.py'), encoding='utf-8').read()
    assert "request.args.get('name')" in route, 'seam 必须在服务端（SPA 伪导航丢 query）'
    assert 'initial_name=initial_name' in route


def test_no_name_keeps_empty_state(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    m = re.search(r'window\.__RESIDENT_INIT__ = \{ name: ("[^"]*") \};', html)
    assert m and json.loads(m.group(1)) == '', '无 name 时应注入空串（保持空态不自动查）'


def test_js_render_uses_sc_and_reads_init():
    js = _js('resident_list.js')
    assert 'sc-badge' in js and 'badge-status' not in js, '状态徽章未换 sc-badge'
    assert '#1877C9' not in js, 'JS 渲染层仍有裸色值'
    assert 'is-active' in js and "'background':'transparent'" not in js, '模式切换仍在写内联色'
    for fn in ('window.openDetailModal = openDetailModal;', 'window.loadResidentList = loadResidentList;'):
        assert fn in js, '全局函数契约被破坏'
    assert "params.page = currentPage; params.limit = pageSize;" in js
    assert '/resident/api/household-summary' in js
    assert '/resident/api/export?mode=household&' in js


def test_detail_js_contract_ids():
    js = _js('resident_detail_modal.js')
    for dom_id in JS_IDS:
        assert dom_id in js, f'详情 JS 渲染层缺冻结 id #{dom_id}'
    assert "var dicts = ['民族','与户主关系','所在组','政治面貌','学历','健康状态','婚姻状况','兵役状况','开户行']" in js
    for export in ('window.renderDetailModal', 'window.showChangeHeadForm', 'window.deleteMember',
                   'window.editHousehold', 'window.refreshSubsidyOverview'):
        assert export in js, f'详情 JS 全局导出缺失 {export}'


def test_page_css_removed():
    assert not os.path.exists(os.path.join(BASE, 'static', 'css', 'pages', 'resident_list.css')), \
        'resident_list.css 应已删除（页面级 CSS 归零）'
    with open(os.path.join(BASE, 'templates', 'resident', 'list.html'), encoding='utf-8') as f:
        tpl = f.read()
    assert 'resident_list.css' not in tpl
    assert '{% block extra_css %}' not in tpl
