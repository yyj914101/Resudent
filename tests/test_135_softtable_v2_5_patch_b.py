# -*- coding: utf-8 -*-
"""tests.test_135_softtable_v2_5_patch_b — 软表 V2.5 补丁 B（前端 UI 重构 + 统一工作台零侵入收敛）契约测试。

注：外发补丁原文件名 test_130_softtable_v2_5_patch_b.py（按补丁作者序号），与主仓任务 130
（通用 .docx 自动解析渲染引擎）撞号，套打时按主仓任务号重命名为 test_135（AGENTS.md 测试运行规约）。
"""
import os
import re

import pytest
from jinja2 import Environment, FileSystemLoader


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    return open(os.path.join(_root(), rel), encoding='utf-8').read()


def test_jinja_templates_parse_cleanly():
    env = Environment(loader=FileSystemLoader(os.path.join(_root(), 'templates')))
    for tpl in [
        'dynatable/index.html',
        'dynatable/upload.html',
        'dynatable/list.html',
        'components/roster/_softtable_workbench.html',
        'components/roster/roster_page.html',
    ]:
        env.parse(_read(os.path.join('templates', tpl)))


def test_unified_workbench_shared_by_dynatable_and_roster():
    list_html = _read('templates/dynatable/list.html')
    roster_html = _read('templates/components/roster/roster_page.html')
    wb_html = _read('templates/components/roster/_softtable_workbench.html')
    a4_js = _read('static/js/pages/dynatable_a4_view.js')

    assert "include 'components/roster/_softtable_workbench.html'" in list_html
    assert "include 'components/roster/_softtable_workbench.html'" in roster_html
    assert 'dynatable_a4_view.js' in roster_html
    assert 'dynatable_v2.css' in list_html and 'dynatable_v2.css' in roster_html
    # R-096 列表页快照正则契约保护：fields 仍保持 __ST_LIST__ 末键
    assert re.search(r'fields:\s*\{\{\s*fields\s*\|\s*tojson\s*\}\}\s*\};', list_html)
    # 共享片段含兄弟表切换区（137：顶层 SwitchBar；138 起去左侧导航树，多子表统一走顶部「独立软表分区」胶囊）
    assert 'id="dtV2SwitchBar"' in wb_html
    assert 'id="dtV2ListPartitions"' in wb_html
    assert 'id="dtV2Sider' not in wb_html, '138 起工作台不得再有左侧导航树（满宽单卡）'
    assert 'id="dtV2BtnDeleteTable"' in wb_html
    # 控制器兼容 __ST_LIST__ 与 __ST_ROSTER__
    assert 'window.__ST_LIST__ || window.__ST_ROSTER__' in a4_js
    assert 'renderSiblingsUI' in a4_js


def test_index_grouping_delete_and_rollover_contracts():
    idx_html = _read('templates/dynatable/index.html')
    idx_js = _read('static/js/pages/dynatable_index.js')
    rt_py = _read('routes/dynatable.py')

    assert 'id="dtGroupList"' in idx_html
    assert 'id="dtDeleteOverlay"' in idx_html
    assert 'id="dtCloneOverlay"' in idx_html
    assert '/api/group/' in idx_js and '/delete' in idx_js
    assert '/clone-period' in idx_js
    assert "'is_builtin'" in rt_py[rt_py.index('def api_tables'):rt_py.index('def api_records')]
    assert "'period_tag'" in rt_py[rt_py.index('def api_tables'):rt_py.index('def api_records')]


def test_upload_wizard_single_track_and_safety_folder_contracts():
    up_html = _read('templates/dynatable/upload.html')
    up_js = _read('static/js/pages/dynatable_v2_upload.js')

    # 已彻底消除旧 V1/V2 双表单与隐式读取 #f_module
    assert 'id="uploadForm"' not in up_html
    # 140（C-1）：3 步向导退役为 1 屏（v2Stepper → v2QuickConfirm + v2AdvancedBox）
    assert 'id="v2Stepper"' not in up_html
    assert 'id="v2QuickConfirm"' in up_html and 'id="v2AdvancedBox"' in up_html
    assert 'id="v2ModuleSelect"' in up_html
    assert 'id="v2SafetyFolderSelect"' in up_html
    assert "getElementById('f_module')" not in up_js
    assert 'safety_folder_id' in up_js
    assert 'data-col-type' in up_js and 'data-col-sens' in up_js

# ------------------------------------------------------------------ 端点契约（补丁自带 5 例全在静态源码层，132/133/134 先例延续）
def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / 'shucun.db')
    for key, value in {
        'SHUCUN_DB_PATH': db_path,
        'SHUCUN_TEMP_DIR': str(tmp_path / 'temp'),
        'SHUCUN_UPLOAD_DIR': str(tmp_path / 'uploads'),
        'SHUCUN_BACKUP_DIR': str(tmp_path / 'backups'),
        'SHUCUN_DATA_DIR': str(tmp_path / 'data'),
        'SHUCUN_LOG_DIR': str(tmp_path / 'logs'),
        'SHUCUN_LICENSE_FILE': str(tmp_path / 'license.key'),
        'SHUCUN_LAN_AUTHORIZED': '1',
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(config, 'TEMP_DIR', str(tmp_path / 'temp'))
    monkeypatch.setattr(config, 'UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(config.Config, 'SQLALCHEMY_DATABASE_URI',
                        'sqlite:///%s?check_same_thread=False' % db_path)
    for sub in ('temp', 'uploads', 'backups', 'data', 'logs'):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    return application


@pytest.fixture()
def client(tmp_path, monkeypatch):
    application = _make_app(tmp_path, monkeypatch)
    from utils.crypto import generate_salt, key_holder
    key_holder.initialize('softtable-test-pass', salt=generate_salt())
    c = application.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
    c.application = application
    return c


def _mk_table(client, code='wbx_a', group_name='工作簿乙'):
    from models import DynamicRecord, db
    from utils import softtable_engine as eng
    from utils import roster_facade as facade
    with client.application.app_context():
        fields = eng.normalize_fields([
            {'code': 'f00', 'display_name': '姓名', 'data_type': 'text',
             'sensitivity': 'none', 'is_required': True},
        ])
        table = facade.create_table('dynatable', code, '工作台测试表', group_name=group_name,
                                    fields=fields, created_by='admin')
        for row in (['甲'], ['乙']):
            recs, _errors = eng.build_records(fields, [row])
            for r in recs:
                db.session.add(DynamicRecord(table_id=table.id, data=r['data'],
                                             digest=r['digest'], created_by='admin'))
        db.session.commit()


def test_api_list_carries_period_tag_and_builtin(client):
    """api_list 随行下发 period_tag / is_builtin——分组首页聚合与内置表置灰的数据源。"""
    _mk_table(client)
    res = client.get('/dynatable/api/list')
    assert res.status_code == 200 and res.get_json()['success'], res.get_json()
    row = res.get_json()['data'][0]
    assert 'period_tag' in row and 'is_builtin' in row
    assert row['group_name'] == '工作簿乙'


def test_workbench_delete_button_endpoint_roundtrip(client):
    """工作台删表按钮 → /api/t/<code>/delete 全链：删除成功跳台账中心。"""
    from models import DynamicTable
    _mk_table(client, code='wbx_del', group_name='')
    res = client.post('/dynatable/api/t/wbx_del/delete', json={})
    assert res.status_code == 200 and res.get_json()['success'], res.get_json()
    with client.application.app_context():
        assert DynamicTable.query.filter_by(code='wbx_del').first() is None


def test_index_delete_group_with_builtin_blocked(client):
    """分组首页整组删除：内置表保护 400（index.js is_builtin 置灰的兜底防线）。"""
    from models import db
    from utils.dynatable_modules import TAB_GROUP_CONFIG
    from utils import softtable_engine as eng
    from utils import roster_facade as facade
    with client.application.app_context():
        fields = eng.normalize_fields([
            {'code': 'f00', 'display_name': '姓名', 'data_type': 'text', 'sensitivity': 'none'},
        ])
        builtin_code = next(iter(TAB_GROUP_CONFIG['social']['exclude_codes']))
        facade.create_table('pension', builtin_code, '内置预置', group_name='含内置组',
                            fields=fields, created_by='admin')
        db.session.commit()
    res = client.post('/dynatable/api/group/%s/delete' % '含内置组', json={})
    assert res.status_code == 400
    assert '内置' in res.get_json()['msg']


def test_upload_page_renders_wizard_and_module_options(client):
    """140（C-1）1 屏智能传表页渲染：v2QuickConfirm/v2ModuleSelect 在 HTML、module_options 上下文仍供给。"""
    res = client.get('/dynatable/upload')
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'id="v2QuickConfirm"' in html and 'id="v2ModuleSelect"' in html
    assert 'id="v2Stepper"' not in html, '3 步向导已随 C-1 退役'
    assert 'id="uploadForm"' not in html, 'V1 表单应已移除（单轨收敛）'


def test_safety_children_endpoint_serves_folder_list(client):
    """#12 修复钉住：向导 safety 文件夹下拉真源 /safety/api/children 可用且含 node_type。"""
    res = client.get('/safety/api/children')
    assert res.status_code == 200
    body = res.get_json()
    assert body.get('success') and 'items' in body.get('data', {})
