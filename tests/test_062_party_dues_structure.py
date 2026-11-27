# -*- coding: utf-8 -*-
"""tests/test_062_party_dues_structure.py — 党费缴纳页 v4.0 结构守卫（062 P5）。

真源：062 任务书 P5 + docs/design/062-P5-党费退伍死亡上报-架构设计-2026-10-29.md §6/§7。
守护：
  1) 065 胶囊：org 组 3 枚，党费页「党费缴纳」唯一激活（#tab=dues）；党员页 #tab=party
  2) 页头：sc-page-head + 导出；全页唯一 primary（工具栏「查询」）
  3) 搜索：sc-toolbar 4 项 + 列设置挂载点 duesColHost
  4) 统计：3 张卡（应缴/已缴/欠缴）
  5) 18 列年度矩阵：标题行 colspan=18 + 列头 18 列 + 横滚 + dense
  6) 弹窗：2 个 sc-modal（单月编辑 6 字段 / 批量收入 3 字段）+ 冻结 id
  7) JS：月份格 sc-month-cell 三态、列显隐 scColToggle、无裸色值
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DUES_COLS = ['#', '姓名', '党支部', '平均月收入', '1月', '2月', '3月', '4月', '5月', '6月',
             '7月', '8月', '9月', '10月', '11月', '12月', '年欠缴', '操作']

DUES_IDS = [
    'searchName', 'searchIdCard', 'searchYear', 'searchStatus', 'btnSearch', 'btnReset', 'btnExport',
    'statsBar', 'statAnnualCalc', 'statAnnualPaid', 'statAnnualArrears',
    'duesTable', 'titleYear', 'duesTbody', 'footerInfo', 'pagination',
    'editModal', 'editModalTitle', 'editForm', 'editMemberId', 'editMonth', 'editName',
    'editMonthLabel', 'editIncome', 'editCalculated', 'editPaid', 'editArrears', 'btnSaveDues',
    'batchIncomeModal', 'batchMemberId', 'batchMemberName', 'batchStartMonth', 'batchIncome',
    'btnBatchIncomeSave',
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


def _html(tmp_path, monkeypatch, path='/party/dues'):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get(path)
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


def _js():
    with open(os.path.join(BASE, 'static', 'js', 'pages', 'party_dues.js'), encoding='utf-8') as f:
        return f.read()


def _block(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    assert i < j
    return html[i:j]


def test_chips_org_dues_active(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    assert html.count('sc-chip is-active') == 1
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=dues"', html)
    for frag in ('组织架构', '党员管理', '党费缴纳'):
        assert frag in html


def test_party_page_chips_active(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch, '/party/')
    assert html.count('sc-chip is-active') == 1
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=party"', html)


def test_page_head_and_single_primary(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    head = _block(html, '<header class="sc-page-head">', '</header>')
    assert '党费缴纳' in head
    assert 'id="btnExport"' in head
    assert head.count('sc-btn--primary') == 0, '页头不放主按钮（主按钮是工具栏查询）'
    toolbar = _block(html, 'class="sc-toolbar"', 'id="statsBar"')
    assert toolbar.count('sc-btn--primary') == 1, '工具栏唯一 primary（查询）'


def test_toolbar_four_filters_and_col_host(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    body = _block(html, 'class="sc-toolbar"', 'id="statsBar"')
    for dom_id in ('searchName', 'searchIdCard', 'searchYear', 'searchStatus'):
        assert f'id="{dom_id}"' in body
    assert 'id="btnSearch"' in body and 'id="btnReset"' in body
    assert 'id="duesColHost"' in body, '列设置挂载点缺失'
    assert body.count('class="sc-field"') == 4


def test_stats_three_cards(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    stats = _block(html, 'id="statsBar"', 'id="duesTable"')
    for dom_id in ('statAnnualCalc', 'statAnnualPaid', 'statAnnualArrears'):
        assert f'id="{dom_id}"' in stats
    for label in ('年度应缴总额', '年度已缴总额', '年度欠缴总额'):
        assert label in stats


def test_table_18_columns_and_scroll(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    assert 'class="sc-table sc-table--dense" id="duesTable"' in html
    assert 'min-width:1120px' in html
    assert 'sc-table-wrap' in html
    assert 'colspan="18"' in html
    trs = re.findall(r'<thead>(.*?)</thead>', html, re.S)[0]
    last = re.findall(r'<tr[^>]*>(.*?)</tr>', trs, re.S)[-1]
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', last)
    assert cols == DUES_COLS, f'18 列口径，实得 {cols}'
    for dom_id in ('duesTable', 'duesTbody', 'footerInfo', 'pagination'):
        assert f'id="{dom_id}"' in html


def test_modals_sc_structure(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    for dom_id in DUES_IDS:
        assert f'id="{dom_id}"' in html, f'冻结契约缺 #{dom_id}'
    assert html.count('class="sc-modal"') >= 2
    edit = _block(html, 'id="editForm"', '</form>')
    assert edit.count('class="sc-field"') == 6
    batch = html[html.index('id="batchIncomeModal"'):]
    assert len(re.findall(r'class="sc-field(?: sc-field--[-\w]+)?"', batch)) == 3
    assert html.count('data-bs-dismiss="modal"') >= 4


def test_js_render_sc_and_col_toggle():
    js = _js()
    assert 'sc-month-cell' in js and 'sc-month-cell--paid' in js and 'sc-month-cell--unpaid' in js
    assert 'month-cell' in js, '点击绑定契约 .month-cell 不得丢'
    assert '#1877C9' not in js and '#d97706' not in js, '渲染层仍有裸色值'
    assert 'scColToggle.init(\'#duesTable\'' in js
    assert "storageKey: 'dues_col_hidden'" in js
    assert 'colspan="18"' in js
    assert '/party/api/dues/list' in js and '/party/api/dues/save' in js
    assert '/party/api/dues/income-standard' in js


def test_col_toggle_helper_shape():
    with open(os.path.join(BASE, 'static', 'js', 'app.js'), encoding='utf-8') as f:
        appjs = f.read()
    assert 'var scColToggle' in appjs
    assert "data-sc-col-toggle" in appjs
    assert "nth-child(" in appjs, '隐藏必须走 nth-child 规则（行重绘零侵入）'
    assert 'localStorage' in appjs
