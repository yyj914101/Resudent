# -*- coding: utf-8 -*-
"""tests.test_140_softtable_v2_5_patch_c1 — 补丁 C-1（懒人智能传表 + 组织架构反哺 + 5 项遗漏收口）测试。"""
import importlib.util
import os
import sys

from jinja2 import Environment, FileSystemLoader


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    return open(os.path.join(_root(), rel), encoding='utf-8').read()


def _load(rel, name):
    root = _root()
    if root not in sys.path:
        sys.path.insert(0, root)
    spec = importlib.util.spec_from_file_location(name, os.path.join(root, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_smart_route_detects_builtin_physical_and_v2():
    sr = _load('utils/softtable_smart_route.py', 'softtable_smart_route_c1')

    r1 = sr.detect_smart_route('环卫队伍花名册.xlsx', {'workbook_group': '环卫队伍花名册', 'sheets': []})
    assert r1['kind'] == 'builtin_softtable' and r1['target_code'] == 'sanitation_team'

    r2 = sr.detect_smart_route('2026低保对象名册.xlsx', {'workbook_group': '低保对象名册', 'sheets': []})
    assert r2['kind'] == 'physical_table' and r2['target_key'] == 'dibao'

    # 高龄津贴增减变动表必须走 softtable_v2 且自动推荐 civil
    r3 = sr.detect_smart_route('高龄津贴增减变动表.xlsx', {'workbook_group': '高龄津贴增减变动表', 'sheets': []})
    assert r3['kind'] == 'softtable_v2' and r3['recommended_module'] == 'civil'

    # 三委花名册自动推荐 organization
    r4 = sr.detect_smart_route('1、社区三委、小组长、居民代表成员花名册.xlsx', {'workbook_group': '社区三委花名册', 'sheets': []})
    assert r4['kind'] == 'softtable_v2' and r4['recommended_module'] == 'organization'


def test_org_sync_field_extraction_and_type_inference():
    osync = _load('utils/org_sync.py', 'org_sync_c1')
    assert osync.infer_org_type('党委成员花名册') == '社区党委'
    assert osync.infer_org_type('居委成员花名册') == '居民委员会'
    assert osync.infer_org_type('居民小组长花名册') == '居民小组长'

    fields = [
        {'code': 'f1', 'display_name': '姓名'},
        {'code': 'f2', 'display_name': '职位'},
        {'code': 'f3', 'display_name': '身份证号码'},
        {'code': 'f4', 'display_name': '联系电话'},
        {'code': 'f5', 'display_name': '政治面貌'},
    ]
    payload = osync.extract_member_payload(
        {'f1': '林蛟', 'f2': '书记', 'f3': '511324199304120112', 'f4': '13890810011', 'f5': '党员'},
        fields, '社区党委',
    )
    assert payload['name'] == '林蛟'
    assert payload['position'] == '书记'
    assert payload['id_card'] == '511324199304120112'
    assert payload['phone'] == '13890810011'
    assert payload['politics_status'] == '党员'


def test_five_gaps_and_dead_v1_cleanup():
    assert not os.path.exists(os.path.join(_root(), 'templates/dynatable/confirm.html'))
    assert not os.path.exists(os.path.join(_root(), 'static/js/pages/dynatable_upload.js'))
    assert not os.path.exists(os.path.join(_root(), 'static/js/pages/dynatable_confirm.js'))
    assert not os.path.exists(os.path.join(_root(), 'static/css/pages/dynatable_upload.css'))

    assert "globals['is_builtin_roster_code']" in _read('app.py')
    wb = _read('templates/components/roster/_softtable_workbench.html')
    assert 'is_builtin_roster_code(table.module_key, table.code)' in wb
    assert 'id="dtV2BackfillTarget"' in wb
    assert 'id="dtV2BtnToggleReveal"' in wb
    assert '仅超管可解锁明文' not in _read('static/js/pages/dynatable_list.js')
    assert 'keepPartitionNav' in _read('static/js/pages/dynatable_a4_view.js')

    up = _read('templates/dynatable/upload.html')
    assert 'id="v2SmartRouteBanner"' in up
    assert 'id="v2AdvancedBox"' in up

    env = Environment(loader=FileSystemLoader(os.path.join(_root(), 'templates')))
    for tpl in ('dynatable/upload.html', 'components/roster/_softtable_workbench.html', 'organization/list.html'):
        env.parse(_read(os.path.join('templates', tpl)))


def test_smart_upload_word_form_integration():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    upload_html = open(os.path.join(root, 'templates', 'dynatable', 'upload.html'), encoding='utf-8').read()
    upload_js = open(os.path.join(root, 'static', 'js', 'pages', 'dynatable_v2_upload.js'), encoding='utf-8').read()
    wf_js = open(os.path.join(root, 'static', 'js', 'pages', 'word_form.js'), encoding='utf-8').read()
    assert 'accept=".xlsx,.xls,.xlsb,.csv,.xlsm,.docx"' in upload_html
    assert 'v2SmartRouteAltBtn' in upload_html
    assert '/word-form/api/template/upload' in upload_js
    assert '/word-form/?tpl_id=' in upload_js
    assert "lower.endsWith('.doc')" in upload_js
    assert "get('tpl_id')" in wf_js
