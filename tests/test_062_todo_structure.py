# -*- coding: utf-8 -*-
"""tests/test_062_todo_structure.py — 待办提醒页 v4.0 结构守卫（062 P2）。

真源：照单施工-6页字段清单 §三 + 组件-handoff v1.5 + 页面-handoff v1.0 §三。
守护：
  1) 无模块胶囊（待办为侧栏一级项，不属任何 module_tabs 分组）
  2) 页头：sc-page-head + 页头区唯一主按钮（§2.2）
  3) 表格：sc-table 8 列（全选/标题/内容/类型/状态/完成人/创建时间/操作）
  4) 批量条：已选 N 项 + 批量完成 + 批量删除 + 分页统计
  5) 弹窗：sc-modal + 冻结 id + 4 字段（标题★/提醒时间/类型 datalist/内容）
  6) JS 渲染层：徽章换 sc-（BS badge 残留即红）+ 行内操作换 sc-btn--text
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
        sess['user_id'] = 1
    resp = client.get('/todo/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _head_section(html):
    m = re.search(r'<header class="sc-page-head">.*?</header>', html, re.S)
    assert m, '缺 sc-page-head 页头'
    return m.group(0)


def test_no_module_chips(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    assert 'sc-chips' not in html, '待办为独立页，不应渲染模块胶囊'


def test_page_head_single_primary(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    head = _head_section(html)
    assert '待办提醒' in head
    assert head.count('sc-btn--primary') == 1, '页头区主按钮必须唯一（§2.2）'
    assert '新增待办' in head and '导出' in head


def test_table_8_columns_with_contract_ids(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('tb', 'pg', 'pageStat', 'checkAll', 'selStat'):
        assert f'id="{dom_id}"' in html, f'缺 JS 契约节点 #{dom_id}'
    thead = re.search(r'<thead class="sc-table__head">.*?</thead>', html, re.S)
    assert thead, '缺 sc-table__head'
    body = thead.group(0)
    assert body.count('class="sc-table__th') == 8, '清单 §三 8 列'
    for title in ('标题', '内容', '类型', '状态', '完成人', '创建时间', '操作'):
        assert f'>{title}</th>' in body, f'缺表头 {title}'
    assert 'sc-table-wrap' in html


def test_batch_bar_and_pagination(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    assert 'id="selStat"' in html and '已选 0 项' in html
    assert '批量完成' in html and '批量删除' in html
    assert re.search(r'class="sc-pagination__stat"[^>]*id="pageStat"', html), \
        '分页统计必须用 sc-pagination__stat'
    assert '<ul class="sc-pagination__pager" id="pg">' in html


def test_modal_sc_structure_and_fields(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('todoFormModal', 'todoForm', 'btnSaveTodo', 'todoTypeOptions'):
        assert f'id="{dom_id}"' in html
    assert 'class="sc-modal"' in html, '缺 sc-modal 弹窗'
    body = html[html.index('id="todoFormModal"'):]
    fields = re.findall(r'class="sc-field(?: sc-field--[-\w]+)?"', body)
    assert len(fields) == 4, f'清单 §三 4 字段，实得 {len(fields)}'
    assert body.count('sc-field__required') == 1
    assert 'name="title" id="f_title" maxlength="100" required' in body
    assert 'type="datetime-local"' in body and 'list="todoTypeOptions"' in body
    assert 'rows="4"' in body and 'maxlength="1000"' in body
    assert html.count('sc-btn--primary') == 2  # 页头新增 + 弹窗保存


def test_js_row_renders_use_sc_components():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base, 'static', 'js', 'pages', 'todo_list.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    assert 'sc-badge sc-badge--success' in js and 'sc-badge--warning' in js
    assert 'sc-badge--danger' in js, '超7天红徽章'
    assert 'sc-row-actions' in js and 'is-danger' in js
    assert 'class="badge' not in js, 'BS 徽章残留'
    assert 'class="btn ' not in js, 'BS 行内按钮残留'
    assert 'pageStat' in js and 'selStat' in js, '缺分页统计/已选回填'
