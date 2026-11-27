# -*- coding: utf-8 -*-
"""tests/test_103_webui_contracts.py — 103 网页走查缺陷收口（契约与回归守护）

覆盖（docs/tasks/103-webui-contract-fixes.md 验收标准中可自动化部分）：
A. R-103-04 契约：json_response total 恒含（0 是合法统计值；显式 None 才省略键）——
   反例先红后绿（原 `if total:` 在 total=0 时省键，前端空表渲染「共 undefined 条」）
B. R-103-05 契约：党务活动 save/delete 写后失效聚合缓存——metric 反例先红后绿
   （原 45s TTL 内统计滞后，走查实证「录 3 条仍显 1 场」）；JS 侧 loadMetrics 显式
   重算以静态断言守护
C. R-103-01 回归确认：roster.js 列表渲染契约（render(resp) 扁平体，leader e871aad
   已入库）——静态断言守护防回退
D. R-103-02 回归确认：/dynatable/upload 分组下拉含「应急管理」（同上提交）
E. R-103-03 回归守护：4 个推进弹窗（信访/诉求/就业补贴/党员发展）模板含
   transitionError 内联错误行 + JS 含 showTransitionError 渲染钩子（选举届次推进
   为 confirm+apiPost 无弹窗形态，apiPost error 分支已渲染 msg，见 RESULT §三）
"""
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _repo_path(rel):
    return os.path.join(_ROOT, rel)


@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())
    return application


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    from utils import agg_cache
    agg_cache.invalidate()
    yield
    agg_cache.invalidate()


def _login(client, user_id, username='admin'):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _admin_id(app):
    with app.app_context():
        from models import User
        return User.query.filter_by(username='admin').first().id


# ------------------------------------------------------------------ A. R-103-04

def test_json_response_includes_zero_total(app):
    """反例先红后绿：total=0 必须出现在响应体（原 `if total:` 省键 → undefined）。"""
    from utils.helpers import json_response
    with app.test_request_context():
        payload = json_response(True, '').get_json()
        assert payload['total'] == 0, '空统计也是合法值，键必须恒含'
        assert payload['success'] is True

        payload_none = json_response(True, '', total=None).get_json()
        assert 'total' not in payload_none, '显式 None 保留「无计数」语义出口'

        payload_n = json_response(True, '', total=7).get_json()
        assert payload_n['total'] == 7

        payload_err = json_response(False, '记录不存在').get_json()
        assert payload_err['total'] == 0 and payload_err['success'] is False


def test_list_endpoint_carries_zero_total_on_empty(app):
    """端到端反例：空列表 API 的 total 必须为 0 而非缺键（safety_checks 口径）。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    body = client.get('/safety/api/checks').get_json()
    assert body['success'], body
    assert body.get('total') == 0, '空表 total 恒含（原缺键 → 页脚「共 undefined 条」）'


# ------------------------------------------------------------------ B. R-103-05

def test_party_activity_write_invalidates_metric_cache(app, _clean_agg_cache):
    """反例先红后绿：save 后 MetricsEngine 直调必须立即反映新数据（原 45s TTL 内滞后）。"""
    client = app.test_client()
    _login(client, _admin_id(app))

    from utils.metrics import MetricsEngine
    with app.app_context():
        before = MetricsEngine.query('party_activity')['data']
    assert before['session_count'] == 0, '空表基线'

    save = client.post('/party/activity/api/save', data={
        'activity_type': '党员教育', 'title': '主题党日活动',
        'activity_date': '2026-11-10', 'expected_count': '12', 'actual_count': '11'})
    assert save.get_json()['success'], save.get_data(as_text=True)

    with app.app_context():
        after = MetricsEngine.query('party_activity')['data']
    assert after['session_count'] == 1, \
        '写后失效：统计必须立即反映新增（原缓存 45s 内滞后）'
    assert after['expected_sum'] == 12 and after['actual_sum'] == 11

    did = save.get_json()['data']['id']
    gone = client.post('/party/activity/api/delete/%d' % did)
    assert gone.get_json()['success']
    with app.app_context():
        removed = MetricsEngine.query('party_activity')['data']
    assert removed['session_count'] == 0, '删除同口径失效'


def test_party_activity_js_recalculates_stats():
    """R-103-05 JS 侧守护：save/delete 成功回调显式调用 loadMetrics。"""
    src = open(_repo_path('static/js/pages/party_activity.js'), encoding='utf-8').read()
    save_block = src[src.index('function saveRow'):src.index('function deleteRow')]
    assert 'loadMetrics()' in save_block, 'save 成功回调必须重算统计'
    delete_block = src[src.index('function deleteRow'):src.index('function exportLedger')]
    assert 'loadMetrics()' in delete_block, 'delete 成功回调必须重算统计'


# ------------------------------------------------------------------ C/D. R-103-01 / 02 回归确认

def test_roster_js_render_contract():
    """R-103-01 静态守护：roster.js load() 必须 render(resp) 扁平体（leader e871aad 口径）。"""
    src = open(_repo_path('static/js/pages/roster.js'), encoding='utf-8').read()
    code_lines = [ln for ln in src.splitlines()
                  if not ln.lstrip().startswith('//')]
    code = '\n'.join(code_lines)
    assert 'render(resp)' in code, '列表渲染必须吃整个扁平响应体'
    assert 'render(resp.data' not in code, \
        '禁止回退到 render(resp.data || {})（恒「暂无数据」缺陷；注释留痕不计）'
    assert 'R-103-01' in src, '修复留痕注释在位'


def test_dynatable_upload_group_dropdown_has_emergency(app):
    """R-103-02 回归确认（135 迁移注记）：V2.5 统一向导把 V1「分组下拉（预设五组含应急管理）」
    改为自由输入框（v2GroupInput，workbook_group 不再受预设约束）——预设五组的语义守护
    由 test_dynatable_group_names_constant（GROUP_NAMES 常量）继续承担；本例改为守护
    向导页渲染成功且分组输入框在位（应急台账建卡入口 = 分组名自由填「应急管理」）。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    resp = client.get('/dynatable/upload')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert 'id="v2GroupInput"' in html, '1 屏确认区分组输入框在位（应急台账建卡入口：自由填写组名）'
    # 140（C-1）：3 步向导退役为 1 屏智能传表（v2Stepper 移除、v2QuickConfirm 承接）
    assert 'id="v2QuickConfirm"' in html, 'C-1 1 屏极速确认区渲染'
    assert 'id="v2Stepper"' not in html, '3 步向导已退役'


def test_dynatable_group_names_constant():
    """R-103-02 常量守护：GROUP_NAMES 五组。"""
    from routes.dynatable import GROUP_NAMES
    assert '应急管理' in GROUP_NAMES


# ------------------------------------------------------------------ E. R-103-03 回归守护

@pytest.mark.parametrize('rel', [
    'templates/governance/petition.html',
    'templates/demand/index.html',
    'templates/employment/employment.html',
    'templates/party/development.html',
])
def test_transition_modals_have_inline_error_line(rel):
    """R-103-03：四个推进弹窗必须含 transitionError 内联错误行（toast 会被
    modal backdrop 遮住——409 msg 渲染的可靠通道）。"""
    src = open(_repo_path(rel), encoding='utf-8').read()
    assert 'id="transitionError"' in src, '%s 缺内联错误行' % rel


@pytest.mark.parametrize('rel', [
    'static/js/pages/governance_petition.js',
    'static/js/pages/demand.js',
    'static/js/pages/employment.js',
    'static/js/pages/party_development.js',
])
def test_transition_js_renders_error_line(rel):
    """R-103-03 JS 守护：失败路径必须把 msg 写入弹窗内错误行并在打开时清除。"""
    src = open(_repo_path(rel), encoding='utf-8').read()
    assert 'showTransitionError' in src, '%s 缺错误行渲染' % rel
    assert 'clearTransitionError' in src, '%s 缺错误行清除（打开弹窗须复位）'
    assert src.count('showTransitionError(') >= 2, '%s 409 与语义失败双路径都要渲染' % rel


def test_election_transition_reports_msg_via_apipost():
    """选举届次推进为 confirm+apiPost（无弹窗形态）：apiPost error 分支已渲染
    xhr.responseJSON.msg → 409 提示可见（RESULT §三 口径），本用例锁定该链路存在。"""
    src = open(_repo_path('static/js/app.js'), encoding='utf-8').read()
    m = re.search(r'function apiPost[\s\S]*?\n\}', src)
    assert m, 'apiPost 必须存在'
    assert 'responseJSON.msg' in m.group(0), 'apiPost error 分支须透传后端 msg'
