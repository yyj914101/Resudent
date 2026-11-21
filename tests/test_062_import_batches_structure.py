# -*- coding: utf-8 -*-
"""tests/test_062_import_batches_structure.py — 导入批次页 v4.0 结构守卫（062 P2）。

真源：照单施工-6页字段清单 §四 + 组件-handoff v1.5 + 页面-handoff v1.0 §三。
守护：
  1) 065 胶囊：module_tabs('tools') 渲染且「导入批次」唯一激活（#tab=import-batch）
  2) 页头：sc-page-head + 只读页无主按钮（清单 §四：无弹窗、无新增）
  3) 表格：sc-table 12 列 + JS 契约 id（dataTable/tableBody/pagination/pageStat）
  4) 工具条：status(6 态)/mode/date_from/date_to + 筛选
  5) JS 渲染层：6 态 sc-badge（BS badge 残留即红）+ 报告/失败行 sc-btn--text + #pageStat
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
        sess['user_id'] = 1  # 种子 admin 视同持 special.import_batch
    resp = client.get('/import/batches')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _head_section(html):
    m = re.search(r'<header class="sc-page-head">.*?</header>', html, re.S)
    assert m, '缺 sc-page-head 页头'
    return m.group(0)


def test_tools_chips_removed(tmp_path, monkeypatch):
    """2026-11-16：导入批次升为侧栏「数据管理」入口，证明开具页不再承载跨页胶囊。"""
    html = _page_html(tmp_path, monkeypatch)
    assert '#tab=import-batch' not in html, '证明开具胶囊组应已撤除'
    assert '#tab=death' not in html, 'tools 组应整组撤除（不留单枚胶囊）'
    assert 'sc-chip is-active' not in html
    # 入口改由侧栏承载（真源 utils/nav_spec.py）
    from utils.nav_spec import NAV_GROUPS
    data_group = [g for g in NAV_GROUPS if g['key'] == 'data'][0]
    assert [i['label'] for i in data_group['items']] == ['台账建表', '导入批次']


def test_page_head_readonly(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    head = _head_section(html)
    assert '导入批次' in head
    assert head.count('sc-btn--primary') == 0, '只读页不应有主按钮'
    assert '按创建时间倒序' in head


def test_table_12_columns_with_contract_ids(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('dataTable', 'tableBody', 'pagination', 'pageStat', 'searchForm'):
        assert f'id="{dom_id}"' in html, f'缺 JS 契约节点 #{dom_id}'
    thead = re.search(r'<thead class="sc-table__head">.*?</thead>', html, re.S)
    assert thead, '缺 sc-table__head'
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', thead.group(0))
    assert cols == ['批次号', '源文件', '模式', '模板版本', '状态', '新增',
                    '更新', '跳过', '失败', '操作人', '创建时间', '操作'], \
        f'清单 §四 12 列，实得 {cols}'
    assert 'sc-table-wrap' in html


def test_toolbar_filters(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    form = re.search(r'<form class="sc-toolbar"[^>]*>.*?</form>', html, re.S)
    assert form, '缺 sc-toolbar 筛选工具条'
    body = form.group(0)
    for frag in ('name="status"', 'name="mode"', 'name="date_from"',
                 'name="date_to"', '筛选'):
        assert frag in body, f'筛选工具条缺 {frag}'
    for opt in ('pending', 'mapped', 'running', 'done', 'blocked', 'aborted'):
        assert f'value="{opt}"' in body, f'缺状态档 {opt}'


def test_js_status_badges_and_links():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base, 'static', 'js', 'pages', 'import_batches.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    assert 'sc-badge sc-badge--' in js
    for tone in ('muted', 'info', 'primary', 'success', 'danger', 'warning'):
        assert f"'{tone}'" in js, f'缺状态档映射 {tone}'
    assert 'sc-row-actions' in js and 'sc-btn--text' in js
    assert 'class="badge bg-' not in js, 'BS 徽章残留'
    assert 'class="btn ' not in js, 'BS 操作按钮残留'
    assert 'sc-text-danger' in js, '失败列红色须走令牌类'
    assert 'onLoaded' in js and 'pageStat' in js, '缺分页统计回填'
