# -*- coding: utf-8 -*-
"""tests/test_062_governance_ledger_structure.py — 综治 4 台账 v4.0 结构守卫（062 P3）。

真源：照单施工-6页字段清单 §五（CATEGORY_CONFIG 配置驱动）+ 组件-handoff v1.5。
守护（4 个 category 参数化，期望值来自页面内联 __GOV_CFG__ 真源）：
  1) 065 胶囊：2026-11-16 综治重构后 governance 三类分区 9 枚；
     special-visit 台账宿主 visitcare 组（tab_module 切换），当前 category 唯一激活
  2) 页头：sc-page-head + 配置标题 + 唯一主按钮（新增记录）+ 导出台账
  3) 表格：sc-table 列 = 序号 + config.columns + 操作；JS 契约 id 齐
  4) 工具条：sc-toolbar keyword + 查询/重置
  5) 弹窗：sc-modal--lg + 隐藏主键 + 字段数/必填星/wide 整行与配置一致
  6) JS 渲染层：sc-row-actions/sc-btn--text/sc-ellipsis + #pageStat（BS btn 残留即红）
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CATEGORIES = ['special-visit', 'stability-risk', 'mental-health', 'security-committee']

# 2026-11-16 综治重构（a1116d9 三类分区 + 99b2caf 胶囊归属迁移，契约同源
# tests/test_module_tabs.py::test_governance_group_three_partitions）：
# governance 9 枚；特困人员走访迁出至 visitcare 组（走访关爱）。
GOV_CHIP_LABELS = ('严重精神障碍', '治保委员会', '刑事人员',
                   '道交隐患', '重点车辆', '劝导站',
                   '矛盾纠纷化解', '社会稳定风险', '信访维稳')
VISITCARE_CHIP_LABELS = ('特困人员走访', '走访计划', '走访记录')


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


def _page_html(category, tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get(f'/governance/{category}/')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    m = re.search(r'window\.__GOV_CFG__ = \{ config: (.*?), category: "', html)
    assert m, '缺 __GOV_CFG__ 配置注入'
    return html, json.loads(m.group(1))


def _head_section(html):
    m = re.search(r'<header class="sc-page-head">.*?</header>', html, re.S)
    assert m, '缺 sc-page-head 页头'
    return m.group(0)


@pytest.mark.parametrize('category', CATEGORIES)
def test_ledger_page_structure(category, tmp_path, monkeypatch):
    html, cfg = _page_html(category, tmp_path, monkeypatch)

    # 1) 胶囊：随 11-16 综治重构 —— special-visit 台账宿主 visitcare 组（tab_module），
    #    其余三类宿主 governance 组（三类分区 9 枚）；当前 category 唯一激活
    if category == 'special-visit':
        for label in VISITCARE_CHIP_LABELS:
            assert label in html, f'缺胶囊 {label}'
        assert '矛盾纠纷化解' not in html, '综治胶囊不应出现在走访关爱页'
    else:
        for label in GOV_CHIP_LABELS:
            assert label in html, f'缺胶囊 {label}'
        assert '特困人员走访' not in html, '特困人员走访已迁出综治组（走访关爱）'
    assert html.count('sc-chip is-active') == 1
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=%s"'
                     % re.escape(category), html)

    # 2) 页头：配置标题 + 唯一主按钮 + 导出台账
    head = _head_section(html)
    assert cfg['title'] in head
    assert head.count('sc-btn--primary') == 1, '页头区主按钮必须唯一（§2.2）'
    assert '新增记录' in head and '导出台账' in head

    # 3) 表格：列 = 序号 + config.columns（按配置顺序）+ 操作
    for dom_id in ('dataTable', 'tableBody', 'pagination', 'pageStat', 'searchForm'):
        assert f'id="{dom_id}"' in html, f'缺 JS 契约节点 #{dom_id}'
    thead = re.search(r'<thead class="sc-table__head">.*?</thead>', html, re.S)
    assert thead, '缺 sc-table__head'
    ths = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', thead.group(0))
    label_map = {f['key']: f['label'] for f in cfg['fields']}
    expected = ['序号'] + [label_map[k] for k in cfg['columns']] + ['操作']
    assert ths == expected, f'{category} 列不符：期望 {expected}，实得 {ths}'
    assert 'sc-table-wrap' in html

    # 4) 工具条
    form = re.search(r'<form class="sc-toolbar"[^>]*>.*?</form>', html, re.S)
    assert form, '缺 sc-toolbar'
    body = form.group(0)
    for frag in ('name="keyword"', 'id="resetButton"', '查询'):
        assert frag in body, f'工具条缺 {frag}'


@pytest.mark.parametrize('category', CATEGORIES)
def test_ledger_modal_matches_config(category, tmp_path, monkeypatch):
    html, cfg = _page_html(category, tmp_path, monkeypatch)
    body = html[html.index('id="formModal"'):]
    # ADR-022 胶囊统一改造：允许 gs-form-modal 等附加修饰类（sc-modal sc-modal--lg 基础组合仍必在）
    assert re.search(r'class="sc-modal sc-modal--lg(?: [\w-]+)*"', body)
    assert re.search(r'<input type="hidden" name="id" id="recordId">', body)
    fields = re.findall(r'class="sc-field(?: sc-field--[-\w]+)*"', body)
    assert len(fields) == len(cfg['fields']), \
        f'{category} 弹窗字段数应为 {len(cfg["fields"])}，实得 {len(fields)}'
    required = sum(1 for f in cfg['fields'] if f['required'])
    assert body.count('sc-field__required') == required, \
        f'{category} 必填星应为 {required}'
    wide = sum(1 for f in cfg['fields'] if f['wide'])
    assert body.count('sc-field--full') == wide, f'{category} wide 整行应为 {wide}'
    for f in cfg['fields']:
        if f['kind'] == 'date':
            assert f'type="date" class="sc-input" name="{f["key"]}"' in body


def test_js_row_renders_use_sc_components():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base, 'static', 'js', 'pages', 'governance_ledger.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    assert 'sc-row-actions' in js and 'is-danger' in js
    assert 'sc-ellipsis' in js
    assert 'class="btn ' not in js, 'BS 行内按钮残留'
    assert 'btn-group' not in js, 'BS 按钮组残留'
    assert 'pageStat' in js, '缺分页统计回填（#pageStat）'
