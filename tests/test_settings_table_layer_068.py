# -*- coding: utf-8 -*-
"""068 设置中心 12 内容区换壳 B：表格层 sc- 化门禁。

覆盖任务书 4 条验收：
1. 12 内容区逐页 DOM 含 .sc-table/.sc-pagination/.sc-modal（按页型登记例外）；
2. 页脚「共 N 条 · 每页 20 条」+ 方形页码签；旧跳页分页下线；
3. 分权口径不回退（ADR-023：字典/字段审批读开放、写 admin）；
4. 组件真源 = components.css（不得另抄页面级样式）。

做法：模板/JS 静态断言 + 12 页真实渲染冒烟（渲染层只读，不改业务）。
"""
import os
import re

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE, 'templates')
JS = os.path.join(BASE, 'static', 'js', 'pages')
CSS = os.path.join(BASE, 'static', 'css')

# 页型登记：页 → (模板, 期望组件, 例外说明)
PAGES = {
    'user_mgmt': ('templates/user_mgmt/list.html', {'sc-table', 'sc-pagination', 'sc-modal', 'sc-toolbar'}, ''),
    'role_mgmt': ('templates/role_mgmt/list.html', {'sc-table', 'sc-pagination', 'sc-modal'}, '权限矩阵在弹窗内由 JS 渲染'),
    'license': ('templates/settings/license.html', {'sc-card', 'sc-kv', 'sc-modal'}, '表单型：无表格/分页'),
    'dict_mgmt': ('templates/dict_mgmt/list.html', {'sc-table', 'sc-modal'}, '字典项全量加载，无分页/搜索工具条'),
    'backup': ('templates/backup/list.html', {'sc-table', 'sc-modal'}, '备份文件全量列出，无分页'),
    'branding': ('templates/settings/branding.html', {'sc-card', 'sc-field'}, '表单型：无表格/分页/弹窗'),
    'notification': ('templates/settings/notification.html', {'sc-card', 'sc-field'}, '表单型：无表格/分页/弹窗'),
    'screen': ('templates/screen/manage.html', {'sc-table', 'sc-card'}, '大屏全量列出，无分页/弹窗（confirm 为原生）'),
    'device': ('templates/device/list.html', {'sc-table', 'sc-modal'}, '设备全量列出，无分页'),
    'log': ('templates/log/list.html', {'sc-table', 'sc-pagination', 'sc-toolbar'}, '无弹窗'),
    'db_admin': ('templates/db_admin/index.html', {'sc-table', 'sc-modal'}, 'DDL 面板无分页（全表清单）'),
    'registry': ('templates/resident_enhance/import_registry_audit.html', {'sc-table', 'sc-toolbar'}, '服务端渲染，无弹窗/分页'),
}

ENDPOINTS = {
    'user_mgmt': 'user_mgmt.list', 'role_mgmt': 'role_mgmt.list', 'license': 'license.index',
    'dict_mgmt': 'dict_mgmt.list', 'backup': 'backup.index', 'branding': 'settings.branding',
    'notification': 'settings.notification', 'screen': 'screen_admin.manage',
    'device': 'device_mgmt.index', 'log': 'log.list', 'db_admin': 'db_admin.index',
    'registry': 'resident_enhance.import_registry',
}

# 页脚统计（crudTable 页：onLoaded 写 pageStat）
STAT_PAGES = {
    '用户管理': 'static/js/pages/user_mgmt_list.js',
    '角色管理': 'static/js/pages/role_mgmt_list.js',
    '操作日志': 'static/js/pages/log_list.js',
}


def _read(*parts):
    with open(os.path.join(BASE, *parts), encoding='utf-8') as fh:
        return fh.read()


@pytest.mark.parametrize('key,spec', sorted(PAGES.items()))
def test_page_dom_components(key, spec):
    """验收 1：逐页 DOM 含登记组件；页面级样式不另抄组件（无 settings_shared）。"""
    rel, expected, _note = spec
    html = _read(rel)
    for cls in expected:
        assert cls in html, (key, cls, rel)
    assert 'data-sc-settings=' in html, rel
    # 返回链接由 settings_shell 宏统一渲染（模板内不重复字面量）
    assert ('settings_head(' in html or 'settings_head_actions(' in html), rel
    assert 'settings_shared.css' not in html, rel


def test_pagination_offline_and_stat_footer():
    """验收 2：旧 Bootstrap 分页下线；crudTable 页统一「共 N 条 · 每页 20 条」页脚。"""
    for key in ('user_mgmt', 'role_mgmt', 'log'):
        html = _read(PAGES[key][0])
        assert 'class="pagination' not in html, key
        assert 'sc-pagination__pager' in html, key
        assert 'sc-pagination__stat' in html, key
    for name, rel in STAT_PAGES.items():
        js = _read(rel)
        assert 'pageStat' in js, name
        assert '每页 20 条' in js, name
    # 平方页码签由组件层收敛（renderPagination 输出 li.page-item>a.page-link）
    comp = _read('static/css/components.css')
    assert '.sc-pagination .page-link' in comp
    assert 'width: 30px' in comp and 'height: 30px' in comp


def test_status_badge_from_real_fields():
    """验收：状态列一律 sc-badge（真实字段渲染），不得残留 BS badge。"""
    checks = {
        'static/js/pages/user_mgmt_list.js': 'is_active',
        'static/js/pages/role_mgmt_list.js': 'is_system',
        'static/js/pages/log_list.js': 'result',
        'static/js/pages/dict_mgmt_list.js': 'is_active',
    }
    for rel, field in checks.items():
        js = _read(rel)
        assert 'sc-badge' in js, rel
        assert field in js, rel
        assert 'badge bg-' not in js, rel
    for rel in ('templates/device/list.html', 'templates/screen/manage.html',
                'templates/resident_enhance/import_registry_audit.html'):
        html = _read(rel)
        assert 'sc-badge' in html, rel
        assert 'badge bg-' not in html, rel


def test_ddl_panel_keeps_seven_modals_and_three_gates():
    """验收 1/3：DDL 面板 7 弹窗保留 + 三重闸仍完整（068 只换壳，不改鉴权/备份）。"""
    html = _read('templates/db_admin/index.html')
    assert len(set(re.findall(r'<div class="modal fade" id="([^"]+)"', html))) == 7
    for token in ('闸 1', '闸 2', '闸 3', 'redConfirmName', 'redResultBody'):
        assert token in html
    assert 'sc-modal--danger' in html and 'sc-modal--warning' in html


def test_permission_contract_unchanged():
    """验收 3：分权口径不回退（ADR-023）——字典写端 admin、字段审批 admin、页面读由权限位过滤。"""
    perms = _read('utils/permissions.py')
    assert "'dict_mgmt': 'dict'" in perms
    assert "'resident_enhance': 'resident'" in perms
    dict_js = _read('static/js/pages/dict_mgmt_list.js')
    assert 'DICT_CAN_MANAGE' in dict_js and 'isAdmin' in dict_js
    reg = _read('templates/resident_enhance/import_registry_audit.html')
    assert "role_id == 1" in reg


def test_component_source_is_global_layer():
    """验收 4：组件唯一真源 components.css，且由 base.html 全局加载（禁页面级 extra_css 抄样式）。"""
    base = _read('templates/base.html')
    assert 'css/components.css' in base
    comp = _read('static/css/components.css')
    for token in ('.sc-table', '.sc-pagination', '.sc-badge', '.sc-modal', '.sc-toolbar',
                  '.sc-field', '.sc-backlink', '.sc-settings-page', '.sc-kv', '.sc-list'):
        assert token in comp, token
    # 已下线页面级旧样式文件
    for dead in ('user_mgmt_list.css', 'role_mgmt_list.css', 'dict_mgmt_list.css', 'log_list.css',
                 'settings_shared.css'):
        assert not os.path.exists(os.path.join(CSS, 'pages', dead)), dead


# ─── 真实渲染冒烟 ────────────────────────────────────────────────────

@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


def test_twelve_pages_render_sc_layers(app):
    from flask import url_for
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    with app.test_request_context('/'):
        paths = {k: url_for(ep) for k, ep in ENDPOINTS.items()}
    for key, path in paths.items():
        resp = client.get(path)
        assert resp.status_code == 200, (key, path, resp.status_code)
        html = resp.get_data(as_text=True)
        for cls in PAGES[key][1]:
            assert cls in html, (key, cls)
        assert '← 返回设置中心' in html, key
