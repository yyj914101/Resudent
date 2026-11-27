# -*- coding: utf-8 -*-
"""tests/test_062_organization_structure.py — 组织架构页 v4.0 结构守卫（062 首页）。

真源：照单施工-6页字段清单 §一 + 组件-handoff v1.5 + 页面-handoff v1.0 §三。
守护：
  1) 065 胶囊：module_tabs('org') 渲染且「组织架构」唯一激活（#tab=structure）
  2) 页头：sc-page-head + 页头区唯一主按钮（§2.2 每屏最多 1 个 primary）
  3) 表格：sc-table 11 列 + JS 契约 id（dataTable/tableBody/pagination/pageStat）
  4) 搜索：#searchForm 三字段（name/org_type/is_current）
  5) 弹窗：sc-modal + 冻结 id（formModal/dataForm/modalTitle/editId）+ 10 字段 + 必填星
  6) JS 渲染层：徽章/行内操作已换 sc-（BS badge/btn 残留即红）
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
        sess['user_id'] = 1  # 首装种子 admin（organization.view 经 '*' 全权）
    resp = client.get('/organization/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _head_section(html):
    m = re.search(r'<header class="sc-page-head">.*?</header>', html, re.S)
    assert m, '缺 sc-page-head 页头'
    return m.group(0)


def test_chips_org_group_active(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    assert '组织架构' in html and '党员管理' in html and '党费缴纳' in html
    assert html.count('sc-chip is-active') == 1
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=structure"', html)


def test_page_head_single_primary(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    head = _head_section(html)
    assert '组织架构' in head
    assert head.count('sc-btn--primary') == 1, '页头区主按钮必须唯一（§2.2）'
    assert '新增成员' in head and '导出' in head


def test_table_11_columns_with_contract_ids(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('dataTable', 'tableBody', 'pagination', 'pageStat', 'searchForm'):
        assert f'id="{dom_id}"' in html, f'缺 JS 契约节点 #{dom_id}'
    thead = re.search(r'<thead class="sc-table__head">.*?</thead>', html, re.S)
    assert thead, '缺 sc-table__head'
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', thead.group(0))
    assert cols == ['#', '机构分类', '姓名', '职务', '身份证号', '电话',
                    '政治面貌', '任职开始', '任职结束', '状态', '操作']
    assert 'sc-table-wrap' in html


def test_search_form_three_fields(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    form = re.search(r'<form class="org-toolbar"[^>]*>.*?</form>', html, re.S)
    assert form, '缺搜索工具条'
    body = form.group(0)
    for frag in ('name="name"', 'name="org_type"', 'id="search_org_type"',
                 'name="is_current"', '全部状态', '现任', '离任', '查询'):
        assert frag in body, f'搜索工具条缺 {frag}'


def test_modal_sc_structure_and_fields(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('formModal', 'dataForm', 'modalTitle', 'editId'):
        assert f'id="{dom_id}"' in html
    assert 'class="sc-modal sc-modal--lg"' in html
    # 渲染输出无 Jinja 标记：从弹窗起点截到文档尾（其后仅 extra_js 脚本）
    body = html[html.index('id="formModal"'):]
    fields = re.findall(r'class="sc-field(?: sc-field--[-\w]+)?"', body)
    # 清单 §一 10 字段 = 9 可见 sc-field + 隐藏主键 id（editId，下方单断）
    assert len(fields) == 9, f'弹窗应 9 个可见字段（清单 §一），实得 {len(fields)}'
    assert re.search(r'<input type="hidden" name="id" id="editId">', body)
    # 必填：机构分类 / 姓名（前端 required + 红星）
    assert body.count('sc-field__required') == 2
    assert 'name="org_type" id="f_org_type" required' in body
    assert 'name="name" id="f_name" required' in body
    # 现任 checkbox 默认勾选（js:33 新增态置 True）
    assert re.search(r'name="is_current" id="f_is_current"[^>]*checked', body)
    # 弹窗主按钮 = 保存（sc-btn--primary 全页共 2 处：页头新增 + 弹窗保存）
    assert html.count('sc-btn--primary') == 2


def test_js_row_renders_use_sc_components():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base, 'static', 'js', 'pages', 'organization_list.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    assert 'sc-badge sc-badge--success' in js and 'sc-badge--muted' in js
    assert 'sc-row-actions' in js and 'is-danger' in js
    assert 'class="badge' not in js, 'BS 徽章残留'
    assert 'class="btn ' not in js, 'BS 行内按钮残留'
    assert 'pageStat' in js, '缺分页统计回填（onLoaded → #pageStat）'
