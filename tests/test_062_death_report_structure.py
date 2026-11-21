# -*- coding: utf-8 -*-
"""tests/test_062_death_report_structure.py — 死亡上报页 v4.0 结构守卫（062 P5）。

真源：062 任务书 P5 + 设计文档 §6–§7。
守护：
  1) 065 胶囊：tools 组 3 枚，「死亡上报」唯一激活（#tab=death）
  2) 页头：sc-page-head + 3 个 secondary 动作；全页唯一 primary（工具栏「查询」）
  3) 参数区：sc-toolbar 7 项（冻结 id）+ 查询
  4) 10 列可编辑名单：标题行 colspan=10、填报行 colspan 合计=10、列头 10 列、横滚
  5) 冻结 id（含快照归一化锚点 reportYear/reportMonth/titleYear/titleMonth）
  6) JS：8 格 contenteditable、updateRow 列位置契约、sc- 操作按钮、无裸色值
  7) death_list.css 已清除误粘残片，只留 @media print
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEATH_COLS = ['编号', '姓名', '户籍地址', '所在组', '性别', '身份证号码', '死亡时间', '对象类型', '备注', '操作']

DEATH_IDS = [
    'reportYear', 'reportMonth', 'reportUnit', 'villageName', 'reporter', 'responsiblePerson',
    'reportDate', 'reportUnitHistory', 'villageNameHistory', 'rowCount',
    'templateTable', 'templateBody', 'noDataRow', 'titleCell', 'titleVillage',
    'titleYear', 'titleMonth', 'headerUnit', 'headerReporter', 'headerResponsible', 'headerDate',
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


def _html(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/death-report/')
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


def _js():
    with open(os.path.join(BASE, 'static', 'js', 'pages', 'death_list.js'), encoding='utf-8') as f:
        return f.read()


def _block(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    assert i < j
    return html[i:j]


def test_tools_chips_removed(tmp_path, monkeypatch):
    """2026-11-16 导航 IA：tools 组退役，死亡上报升为侧栏「社区治理」入口；
    页内换 death 模块组（死亡上报 ↔ 死亡名单对账，D-18 hash 承载），死亡上报唯一激活。
    契约与 tests/test_module_tabs.py::test_death_group_two_chips 同源。"""
    html = _html(tmp_path, monkeypatch)
    # tools 组退役：导入批次胶囊零残留
    assert '#tab=import-batch' not in html, 'tools 组（导入批次）胶囊应已撤除'
    # death 模块组：两枚胶囊，死亡上报唯一激活
    assert '#tab=death' in html and '#tab=reconcile' in html, '缺 death 模块组两枚胶囊'
    assert html.count('sc-chip is-active') == 1, '死亡上报应唯一激活'
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=death"', html)
    assert '死亡名单对账' in html
    from utils.nav_spec import NAV_GROUPS
    governance = [g for g in NAV_GROUPS if g['key'] == 'governance'][0]
    assert 'death_report.list' in [i['endpoint'] for i in governance['items']], \
        '死亡上报须成为社区治理组侧栏入口'


def test_page_head_actions_and_single_primary(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    head = _block(html, '<header class="sc-page-head">', '</header>')
    assert '死亡上报' in head
    assert head.count('sc-btn--primary') == 0
    for frag in ('添加行', '导出 Excel', '打印'):
        assert frag in head
    assert html.count('sc-btn--primary') == 1, '全页唯一 primary（工具栏查询）'


def test_param_toolbar_seven_fields(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    bar = _block(html, '<div class="sc-toolbar">', 'id="rowCount"')
    assert bar.count('class="sc-field"') == 7
    for dom_id in ('reportYear', 'reportMonth', 'reportUnit', 'villageName',
                   'reporter', 'responsiblePerson', 'reportDate'):
        assert f'id="{dom_id}"' in bar
    assert 'queryDeceased()' in bar


def test_table_10_columns_and_colspans(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    assert 'class="sc-table sc-table--dense" id="templateTable"' in html
    assert 'min-width:1000px' in html and 'sc-table-wrap' in html
    head = re.search(r'<thead>(.*?)</thead>', html, re.S).group(1)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', head, re.S)
    assert 'colspan="10"' in rows[0], '标题行必须跨 10 列'
    ths = re.findall(r'<th[^>]*>', rows[1])
    spans = [int(re.search(r'colspan="(\d+)"', t).group(1)) if 'colspan=' in t else 1 for t in ths]
    assert sum(spans) == 10, f'填报行 colspan 合计应为 10，实得 {spans}'
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', rows[2])
    assert cols == DEATH_COLS, f'10 列口径，实得 {cols}'
    assert 'colspan="10"' in html[html.index('id="noDataRow"'):]


def test_frozen_ids(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    for dom_id in DEATH_IDS:
        assert f'id="{dom_id}"' in html, f'冻结契约缺 #{dom_id}'


def test_js_editable_contract_and_sc():
    js = _js()
    assert js.count('contenteditable="true"') == 8, '8 个可编辑格（编号/操作除外）'
    for idx, field in enumerate(['name', 'address', 'village_group', 'gender', 'id_card',
                                 'death_date', 'person_type', 'remark'], start=1):
        assert f'currentRows[idx].{field} = cells.eq({idx})' in js, f'列位置契约破坏：{field}'
    assert 'sc-row-actions' in js and 'sc-btn--text' in js
    assert 'btn btn-' not in js, 'BS 按钮残留'
    assert 'class="text-danger"' not in js, '裸 BS 危险色残留'
    for export in ('window.addRow', 'window.deleteRow', 'window.exportExcel', 'window.printTable',
                   'window.queryDeceased', 'window.revokeDeath', 'window.updateRow'):
        assert export in js, f'全局导出缺失 {export}'
    assert '/death-report/api/deceased' in js and '/death-report/api/export' in js


def test_death_css_trimmed_to_print():
    with open(os.path.join(BASE, 'static', 'css', 'pages', 'death_list.css'), encoding='utf-8') as f:
        css = f.read()
    assert "' +" not in css and "\\\n" not in css, '仍残留误粘的 JS 字符串'
    assert '@media print' in css
    with open(os.path.join(BASE, 'static', 'css', 'components.css'), encoding='utf-8') as f:
        comp = f.read()
    assert '.sc-table td[contenteditable]' in comp, '可编辑单元格样式应上移组件层'
