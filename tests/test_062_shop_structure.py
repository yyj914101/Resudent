# -*- coding: utf-8 -*-
"""tests/test_062_shop_structure.py — 店铺信息页 v4.0 结构守卫（062 P2）。

真源：照单施工-6页字段清单 §二 + 组件-handoff v1.5 + 页面-handoff v1.0 §三。
守护：
  1) 065 胶囊：module_tabs('resident') 渲染且「店铺信息」唯一激活（#tab=shop）
  2) 页头：sc-page-head + 页头区唯一主按钮（§2.2 每屏最多 1 个 primary）
  3) 表格：sc-table 8 列 + JS 契约 id（dataTable/tableBody/pagination/pageStat/searchForm）
  4) 工具条：sc-toolbar 两字段（keyword/id_card）
  5) 弹窗：sc-modal + 冻结 id（formModal/saveForm/recordId/formTitle）+ 6 字段 + 必填星
  6) 批量导入弹窗：文件 .xlsx（不照抄 .xls 矛盾）+ mode(append/overwrite)
  7) JS 渲染层：行内操作已换 sc-（BS btn 残留即红）+ #pageStat 回填
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
        sess['user_id'] = 1  # 首装种子 admin（shop 权限经 '*' 全权）
    resp = client.get('/shop/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _head_section(html):
    m = re.search(r'<header class="sc-page-head(?:\s[^"]*)?">.*?</header>', html, re.S)
    assert m, '缺 sc-page-head 页头'
    return m.group(0)


def test_chips_resident_group_active(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    assert '居民查询' in html and '新增居民' in html and '店铺信息' in html
    assert html.count('sc-chip is-active') == 1
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=shop"', html)


def test_page_head_single_primary(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    head = _head_section(html)
    assert '店铺信息' in head
    assert head.count('sc-btn--primary') == 1, '页头区主按钮必须唯一（§2.2）'
    for frag in ('新增店铺', '批量导入', '下载模板', '导出 Excel'):
        assert frag in head, f'页头缺动作 {frag}'


def test_table_8_columns_with_contract_ids(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('dataTable', 'tableBody', 'pagination', 'pageStat', 'searchForm'):
        assert f'id="{dom_id}"' in html, f'缺 JS 契约节点 #{dom_id}'
    thead = re.search(r'<thead class="sc-table__head">.*?</thead>', html, re.S)
    assert thead, '缺 sc-table__head'
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', thead.group(0))
    assert cols == ['序号', '店铺名称', '街道名', '门牌号', '商户姓名',
                    '身份证号', '电话', '操作'], f'清单 §二 8 列，实得 {cols}'
    assert 'sc-table-wrap' in html


def test_toolbar_two_fields(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    form = re.search(r'<form class="sc-toolbar"[^>]*>.*?</form>', html, re.S)
    assert form, '缺 sc-toolbar 搜索工具条'
    body = form.group(0)
    for frag in ('name="keyword"', 'name="id_card"', 'id="resetButton"', '查询'):
        assert frag in body, f'搜索工具条缺 {frag}'


def test_modal_sc_structure_and_fields(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('formModal', 'saveForm', 'recordId', 'formTitle'):
        assert f'id="{dom_id}"' in html
    assert 'class="sc-modal sc-modal--lg"' in html
    # 只截 formModal（其后紧跟 importModal，勿把导入弹窗字段一并算入）
    body = html[html.index('id="formModal"'):html.index('id="importModal"')]
    fields = re.findall(r'class="sc-field(?: sc-field--[-\w]+)?"', body)
    assert len(fields) == 6, f'编辑弹窗应 6 个可见字段（清单 §二），实得 {len(fields)}'
    assert re.search(r'<input type="hidden" name="id" id="recordId">', body)
    assert body.count('sc-field__required') == 1
    assert 'name="name" id="f_name" required' in body
    # 弹窗主按钮：页头新增 + 两弹窗保存/导入 = 全页 3 处 primary
    assert html.count('sc-btn--primary') == 3


def test_import_modal_xlsx_and_mode(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for frag in ('id="importModal"', 'id="importForm"', 'data-bs-dismiss="modal"'):
        assert frag in html
    body = html[html.index('id="importModal"'):]
    # 139：127 起全站多格式上传（.xlsx/.xls/.xlsb/.csv/.xlsm），前端 accept 与单一真源对齐；
    # 原 047 口径「后端只放行 .xlsx」已被 127 取代 —— 只更新断言，不回退能力。
    # 绑定 SPREADSHEET_ACCEPT：常量变更而模板未跟时本断言会报。
    from utils.spreadsheet_reader import SPREADSHEET_ACCEPT
    assert f'accept="{SPREADSHEET_ACCEPT}"' in body
    # 2026-11-19 换壳：f_file 隐藏化进投放区，required 移除（提交由暂存态门控）
    assert f'id="f_file" accept="{SPREADSHEET_ACCEPT}"' in body
    assert 'name="file"' in body
    assert 'pw-modal' in body and 'pw-drop' in body
    assert 'name="mode"' in body and 'append' in body and 'overwrite' in body


def test_js_row_renders_use_sc_components():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base, 'static', 'js', 'pages', 'shop_list.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    assert 'sc-row-actions' in js and 'is-danger' in js
    assert 'class="btn ' not in js, 'BS 行内按钮残留'
    assert 'btn-group' not in js, 'BS 按钮组残留'
    assert 'pageStat' in js, '缺分页统计回填（#pageStat）'


def test_attribute_contexts_use_escattr():
    """101 R1：属性上下文（data-fee/data-name）必须用 escAttr，禁止文本转义 esc。"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base, 'static', 'js', 'pages', 'shop_list.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    attr_lines = [ln for ln in js.splitlines()
                  if 'data-fee="' in ln or 'data-name="' in ln]
    assert attr_lines, '缺 data-fee/data-name 属性渲染行'
    for ln in attr_lines:
        assert 'escAttr(' in ln, '属性上下文必须用 escAttr：%s' % ln.strip()
        assert 'esc(' not in ln.replace('escAttr(', ''), '属性上下文残留文本转义 esc：%s' % ln.strip()
