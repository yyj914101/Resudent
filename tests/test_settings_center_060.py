# -*- coding: utf-8 -*-
"""060 设置中心：入口页 4×3 + 12 内容区的局部门禁。

覆盖任务书 5 条验收：
1. 入口页 4 组 × 3 卡 + 卡片规格（高 88 / 圆角 10 / 零阴影 / 描边 #E5E5E5）；
2. 12 内容区 3 类范式（列表型 6 / 表单型 3 / 特殊型 3）；
3. 数据库管理面板 7 弹窗；
4. 权限过滤：10/12 admin 专属，空分组整组隐藏；
5. 每页「← 返回设置中心」（12/400 #737373）。

只读断言 + 一次真实渲染冒烟，不改业务逻辑。
"""
import os
import re

import pytest


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE, 'templates')
PAGE_CSS = os.path.join(BASE, 'static', 'css', 'pages')

# 12 内容区：模板相对路径 → 范式（6 列表 / 3 表单 / 3 特殊）
SETTINGS_PAGES = {
    'list': [
        'templates/user_mgmt/list.html',
        'templates/dict_mgmt/list.html',
        'templates/backup/list.html',
        'templates/device/list.html',
        'templates/log/list.html',
        'templates/resident_enhance/import_registry_audit.html',
    ],
    'form': [
        'templates/settings/license.html',
        'templates/settings/branding.html',
        'templates/settings/notification.html',
    ],
    'special': [
        'templates/role_mgmt/list.html',
        'templates/screen/manage.html',
        'templates/db_admin/index.html',
    ],
}

# 12 内容区对应端点（渲染冒烟用）
SETTINGS_ENDPOINTS = [
    'user_mgmt.list',
    'role_mgmt.list',
    'license.index',
    'dict_mgmt.list',
    'backup.index',
    'settings.branding',
    'settings.notification',
    'screen_admin.manage',
    'device_mgmt.index',
    'log.list',
    'db_admin.index',
    'resident_enhance.import_registry',
]


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def test_paradigm_counts_are_6_3_3():
    """验收 2：3 类范式 = 列表 6 / 表单 3 / 特殊 3，共 12 页且不重不漏。"""
    assert len(SETTINGS_PAGES['list']) == 6
    assert len(SETTINGS_PAGES['form']) == 3
    assert len(SETTINGS_PAGES['special']) == 3
    flat = [p for group in SETTINGS_PAGES.values() for p in group]
    assert len(flat) == len(set(flat)) == 12


@pytest.mark.parametrize('kind,pages', sorted(SETTINGS_PAGES.items()))
def test_pages_carry_settings_shell(kind, pages):
    """验收 2/5：每页都套统一壳（范式标记 + 返回链接宏）。

    068 起公共样式收口到全局 components.css（禁止页面级 extra_css），
    故不再断言 settings_shared.css 链接。
    """
    for rel in pages:
        html = _read(os.path.join(BASE, rel))
        assert "from 'components/_macros/settings_shell.html' import settings_head" in html, rel
        assert '{{ settings_head(' in html or '{% call settings_head_actions(' in html, rel
        assert 'data-sc-settings="%s"' % kind in html, rel
        assert 'settings_shared.css' not in html, rel


def test_backlink_spec_12_400_muted():
    """验收 5：返回链接 12/400 #737373，hover #171717（组件 handoff §2.11）；068 起在 components.css。"""
    css = _read(os.path.join(BASE, 'static', 'css', 'components.css'))
    assert '.sc-backlink {' in css
    block = css.split('.sc-backlink {', 1)[1].split('}', 1)[0]
    assert 'font-size: 12px' in block
    assert 'font-weight: 400' in block
    assert 'color: var(--c-muted)' in block
    assert '.sc-backlink:hover { color: var(--c-ink); }' in css
    # 令牌值 = 设计稿 #737373 / #171717
    tokens = _read(os.path.join(BASE, 'static', 'css', 'tokens.css'))
    assert '--c-muted: #737373' in tokens
    assert '--c-ink: #0A0A0A' in tokens and '--c-ink-2: #171717' in tokens


def test_hub_card_spec():
    """验收 1：入口卡高 88 / 圆角 10 / 零阴影 / 描边 #E5E5E5 / 组内 3 列。"""
    css = _read(os.path.join(PAGE_CSS, 'settings_center.css'))
    block = css.split('.sc-set-card {', 1)[1].split('}', 1)[0]
    assert 'height: 88px' in block
    assert 'border: 1px solid var(--c-line-3)' in block
    assert 'border-radius: var(--r-card)' in block
    assert 'box-shadow: none' in block
    assert 'repeat(3, minmax(0, 1fr))' in css
    tokens = _read(os.path.join(BASE, 'static', 'css', 'tokens.css'))
    assert '--c-line-3: #E5E5E5' in tokens
    assert '--r-card: 10px' in tokens


def test_hub_template_renders_4_groups_of_3():
    """验收 1：hub 模板按后端分组渲染（4 组 × 3 卡，组标签 + 图标 + 说明）。"""
    html = _read(os.path.join(TPL, 'settings', 'index.html'))
    assert '{% for group in groups %}' in html
    assert 'sc-set-grid' in html and 'sc-set-card' in html
    assert "icon(item.icon, 18)" in html


def test_db_admin_has_seven_modals():
    """验收 3：数据库管理面板 7 个弹窗（功能清单 §二-11）。"""
    html = _read(os.path.join(TPL, 'db_admin', 'index.html'))
    ids = set(re.findall(r'<div class="modal fade" id="([^"]+)"', html))
    assert ids == {
        'noticeModal', 'tableDetailModal', 'opModal', 'confirmModal',
        'redModal', 'confirmRedModal', 'redResultModal',
    }
    assert len(ids) == 7


def test_db_admin_three_tier_gates():
    """验收 3：黄级两步（表单 → 预览 SQL 确认）+ 红级三重闸（备份 / 输入表名 / 结果恢复指引）。"""
    html = _read(os.path.join(TPL, 'db_admin', 'index.html'))
    assert 'id="opModal"' in html and 'id="confirmModal"' in html
    assert '闸 1' in html and '闸 2' in html and '闸 3' in html
    assert 'id="redConfirmName"' in html      # 闸 2：输入目标表名
    assert 'id="redResultBody"' in html       # 闸 3：结果 + 恢复指引
    assert '自动生成 .wbak 加密备份' in html
    # 危险弹窗统一按 .sc-modal--danger（068 收口，不再用 BS bg-danger 头部）
    assert 'sc-modal--danger' in html


# ─── 权限过滤（纯函数，app context 只用于 url_for） ───────────────────

@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


def _groups(app, is_admin, allowed):
    from routes.settings import _visible_hub_groups
    with app.test_request_context('/settings/center'):
        return _visible_hub_groups(is_admin, lambda key: key in allowed)


def test_admin_sees_four_groups_of_three(app):
    groups = _groups(app, True, set())
    assert [g['label'] for g in groups] == ['账号与权限', '数据与规则', '界面与展示', '系统与审计']
    assert [len(g['items']) for g in groups] == [3, 3, 3, 3]
    titles = [i['title'] for g in groups for i in g['items']]
    assert titles == [
        '用户管理', '角色管理', '授权管理',
        '字典管理', '字段注册审批', '数据库安全',
        '界面设置', '通知配置', '大屏管理',
        '设备管理', '操作日志', '数据库管理面板',
    ]


def test_non_admin_sees_only_permitted_items(app):
    """验收 4：非 admin 仅 2 项可见；其余 3 组无可见项 → 整组隐藏，不出现空组。"""
    groups = _groups(app, False, {'dict.view', 'resident.view'})
    assert len(groups) == 1
    assert groups[0]['label'] == '数据与规则'
    assert [i['title'] for i in groups[0]['items']] == ['字典管理', '字段注册审批']


def test_non_admin_keeps_single_group_when_one_perm_missing(app):
    groups = _groups(app, False, {'dict.view'})
    assert [g['label'] for g in groups] == ['数据与规则']
    assert [i['title'] for i in groups[0]['items']] == ['字典管理']


def test_non_admin_without_any_perm_sees_nothing(app):
    assert _groups(app, False, set()) == []


# ─── 渲染冒烟（真实模板 + 真实路由） ──────────────────────────────────

def test_hub_and_twelve_pages_render_with_backlink(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    with app.test_request_context('/'):
        from flask import url_for
        hub_path = url_for('settings.center')
        paths = {ep: url_for(ep) for ep in SETTINGS_ENDPOINTS}
    hub = client.get(hub_path)
    assert hub.status_code == 200
    body = hub.get_data(as_text=True)
    assert body.count('class="sc-set-card"') == 12
    assert body.count('sc-set-group__label') == 4
    for endpoint, path in paths.items():
        resp = client.get(path)
        assert resp.status_code == 200, (endpoint, path, resp.status_code)
        html = resp.get_data(as_text=True)
        assert '← 返回设置中心' in html, endpoint
        assert 'class="sc-backlink"' in html, endpoint
