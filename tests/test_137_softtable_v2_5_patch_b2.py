# -*- coding: utf-8 -*-
"""tests.test_137_softtable_v2_5_patch_b2 — 补丁 B-2 全页面对齐、模块胶囊换行与合页 A4 渲染回归测试。"""
import importlib.util
import io
import os
import re
import sys

import openpyxl
from jinja2 import Environment, FileSystemLoader


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    return open(os.path.join(_root(), rel), encoding='utf-8').read()


def _load_mod(rel, name):
    root = _root()
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(name, os.path.join(root, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_patch_b2_jinja_templates_parse():
    env = Environment(loader=FileSystemLoader(os.path.join(_root(), 'templates')))
    for tpl in [
        'components/_macros/module_tabs.html',
        'components/roster/_softtable_workbench.html',
        'components/roster/roster_page.html',
        'dynatable/index.html',
        'dynatable/upload.html',
        'dynatable/list.html',
    ]:
        env.parse(_read(os.path.join('templates', tpl)))


def test_module_tabs_ext_row_separated_from_grid():
    mt = _read('templates/components/_macros/module_tabs.html')
    assert 'ns_ext.row' in mt
    assert 'grid-column:1' in mt
    assert 'border-top:1px dashed var(--c-line-2)' in mt


def test_parser_cleans_workbook_group_and_emits_sample_rows():
    parser = _load_mod('utils/softtable_v2_parser.py', 'softtable_v2_parser_b2')
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '测试表'
    ws.append(['姓名', '补贴月份'])
    ws.append(['陈明贵', '2026-01'])
    ws.append(['李桂兰', '2026-01'])
    buf = io.BytesIO()
    wb.save(buf)
    res = parser.parse_workbook_v2('高龄津贴增减变动表（电子档）(1).xlsx最新.xlsx', io.BytesIO(buf.getvalue()))
    assert res['workbook_group'] == '高龄津贴增减变动表'
    t = res['sheets'][0]['tables'][0]
    assert 'sample_rows' in t and len(t['sample_rows']) == 2


def test_upload_wizard_full_width_and_step3_grid():
    up_html = _read('templates/dynatable/upload.html')
    up_js = _read('static/js/pages/dynatable_v2_upload.js')
    assert 'max-width:1080px' not in up_html
    assert 'dtv2-form-grid' in up_html
    assert 'id="v2CombinePage"' in up_html
    assert 'combine_on_single_page' in up_js
    assert 't.sample_rows' in up_js


def test_workbench_combined_a4_and_list_fault_tolerance():
    wb_html = _read('templates/components/roster/_softtable_workbench.html')
    list_html = _read('templates/dynatable/list.html')
    a4_js = _read('static/js/pages/dynatable_a4_view.js')

    assert 'module_tabs(nav_module' in list_html
    assert re.search(r'fields:\s*\{\{\s*fields\s*\|\s*tojson\s*\}\}\s*\};', list_html)
    for btn_id in ('dtV2ListAddRow', 'dtV2ListDelRow', 'dtV2ListAddCol', 'dtV2ListDelCol'):
        assert f'id="{btn_id}"' in wb_html
    assert 'renderSideBySideA4' in a4_js
    assert 'renderVerticalSectionsA4' in a4_js
    assert 'window.prompt(' not in a4_js
    assert 'window.confirm(' not in a4_js
