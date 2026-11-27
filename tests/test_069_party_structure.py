# -*- coding: utf-8 -*-
"""tests/test_069_party_structure.py — 党员列表页换壳结构守卫（069）。

真源：v7-12 党员管理样图 + components.css 组件层（062 唯一真源）+ organization 页同级实现。
守护：
  1) 065 胶囊：module_tabs('org') 渲染且「党员管理」唯一激活（#tab=party）
  2) 页头：sc-page-head（标题/副标题/操作钮）+ 页头区唯一主按钮（§2.2）
  3) 表格：sc-table 17 列 + JS 契约 id（tb/fi/pg + 搜索 4 字段 2 钮）
  4) 分页：sc-pagination（__stat #__stat #fi / __pager #pg）
  5) 弹窗：新增/编辑 fmModal + 批量导入 impModal 均 sc-modal；JS 契约 id 全保留
  6) 内容区无旧 bootstrap 表格残留（dtModal/feeModal 详情壳不在本任务范围）
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


def _page_html(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # 首装种子 admin（party.view 经 '*' 全权）
    resp = client.get('/party/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _slice(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    return html[i:j]


def test_chips_party_active(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    assert '组织架构' in html and '党员管理' in html and '党费缴纳' in html
    assert html.count('sc-chip is-active') == 1
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=party"', html)


def test_page_head_title_meta_single_primary(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    head = _slice(html, '<header class="sc-page-head">', '</header>')
    assert '党员管理' in head
    assert 'sc-page-head__meta' in head
    assert head.count('sc-btn--primary') == 1, '页头区主按钮必须唯一（§2.2）'
    assert 'id="btnAdd"' in head and '新增党员' in head


def test_table_17_columns_with_contract_ids(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('tb', 'fi', 'pg', 'sName', 'sIdCard', 'sType', 'sBranch',
                   'btnSearch', 'btnReset'):
        assert f'id="{dom_id}"' in html, f'缺 JS 契约节点 #{dom_id}'
    thead = re.search(r'<thead class="sc-table__head">.*?</thead>', html, re.S)
    assert thead, '缺 sc-table__head'
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', thead.group(0))
    assert cols == ['#', '姓名', '身份证号', '性别', '出生日期', '年龄', '党龄',
                    '民族', '学历', '入党日期', '转正日期', '人员类别', '手机号',
                    '党支部', '现居住址', '党费', '操作']
    assert 'sc-table-wrap' in html
    assert 'class="sc-table sc-table--dense"' in html


def test_search_toolbar_sc_components(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    toolbar = _slice(html, '<section class="sc-card party-list-card',
                     '<div class="sc-table-wrap"')
    for frag in ('class="sc-input"', 'class="sc-select"',
                 'id="btnSearch"', 'id="btnReset"', '查询', '重置'):
        assert frag in toolbar, f'搜索工具条缺 {frag}'
    assert 'form-control' not in toolbar, '工具条残留 BS 控件'


def test_pagination_sc_structure(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    pager = _slice(html, '<div class="sc-pagination">', '</section>')
    assert 'class="sc-pagination__stat" id="fi"' in pager
    assert 'class="sc-pagination__pager" id="pg"' in pager


def test_modals_sc_structure_and_contract_ids(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    # 新增/编辑弹窗：sc-modal--lg + 冻结 id + 15 字段（080 拆四维） + branch-select 回填类
    form_slice = html[html.index('id="fmModal"'):]
    assert 'class="sc-modal sc-modal--lg"' in form_slice
    for dom_id in ('fm', 'fid', 'fmTitle', 'fmName', 'fmIdCard', 'fmGender',
                   'fmBirth', 'fmNation', 'fmLookup',
                   'fmMemberIdentity', 'fmMemberMobility',
                   'fmEmploymentStatus', 'fmPartyStatus'):
        assert f'id="{dom_id}"' in form_slice, f'弹窗缺 JS 契约节点 #{dom_id}'
    fields = re.findall(r'class="sc-field"', form_slice)
    assert len(fields) == 15, f'弹窗应 15 个字段（fm，080 拆维度后），实得 {len(fields)}'
    assert 'branch-select' in form_slice
    assert 'name="name" id="fmName" required' in form_slice
    # 批量导入弹窗：sc-modal + 契约 id
    imp_slice = html[html.index('id="impModal"'):html.index('id="dtModal"')]
    assert 'class="sc-modal pw-modal"' in imp_slice
    for dom_id in ('impMode', 'impFile', 'impRes'):
        assert f'id="{dom_id}"' in imp_slice
    # 弹窗主按钮 = 保存/导入（sc-btn--primary 全页共 3 处：页头新增 + 弹窗保存 + 导入）
    assert html.count('sc-btn--primary') == 4


def test_no_legacy_bootstrap_table_in_content(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    content = _slice(html, '<section class="sc-card party-list-card',
                     '<div class="sc-pagination">')
    assert 'table table-sm' not in content, '内容区残留旧 bootstrap 表格'
    assert 'table-bordered' not in content, '内容区残留旧 bootstrap 表格'
    assert 'card-header' not in content and 'card-footer' not in content


def test_brand_actions_use_sc_buttons(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    actions = _slice(html, 'class="party-page-actions', '</section>')
    assert actions.count('sc-btn sc-btn--secondary') == 3
    assert 'btn btn-' not in actions, '品牌卡残留旧 BS 按钮'


def test_components_sc_modal_pointer_events_auto():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css_path = os.path.join(base, 'static', 'css', 'components.css')
    with open(css_path, encoding='utf-8') as f:
        css = f.read()
    block = re.search(r'\.sc-modal \{([^}]*)\}', css)
    assert block and 'pointer-events: auto' in block.group(1), (
        'sc-modal 必须恢复 pointer-events，否则点击穿透 backdrop 自动关闭')


def test_js_contract_intact():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base, 'static', 'js', 'pages', 'party_list.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    # 069 约束：JS 契约零改动 —— 渲染目标 tb/fi/pg 与行内 col-* 类名仍在用
    for frag in ("$('#tb')", "$('#fi')", "$('#pg')", 'col-index', 'col-name'):
        assert frag in js, f'party_list.js 契约节点变更：{frag}'
    # 069-FIX：行内按钮保留 JS 钩子类，仅替换外观类
    for frag in ('sc-badge--success', 'sc-btn--text btn-fee',
                 'sc-btn--text btn-auto', 'sc-btn--text is-danger btn-del'):
        assert frag in js, f'party_list.js 行内外观类缺失：{frag}'
