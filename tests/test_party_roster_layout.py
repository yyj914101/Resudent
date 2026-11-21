# -*- coding: utf-8 -*-
"""055 党员名册页布局复刻：结构断言 + api_summary 口径断言。

069 换壳增量：品牌卡操作区 5→4（新增党员上移 sc-page-head）、表卡换 sc-card；
080 A-1 增量：人员类别拆四维，汇总格 7→8（补「退休党员」），其余口径不变。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def _roster_html(app):
    resp = _admin_client(app).get('/party/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _slice(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    return html[i:j]


# 2026-11-19 设计稿换壳（ui-adjustments §三十四）：锚点类名加 pw-* 后缀，
# 旧结构类名（party-summary-grid/item/value、party-branch-summary、party-page-header/
# actions/description）保留为测试契约；标签文字与 8 格口径不变。
ANCHOR_GRID = '<div class="party-summary-grid pw-kpis">'
ANCHOR_BRANCH = '<div class="party-branch-summary pw-branches">'
ANCHOR_LABEL = '<span class="pw-kpi__label">'


def _label_text(seg):
    """pw-kpi__label 内的指标名：色点 span 之后的文字，至本 label 的 </span> 止。"""
    after_dot = seg.split('</span>', 1)[1]
    return after_dot.split('<')[0].strip()


class TestPartyRosterStructure:
    def test_header_before_card(self, app):
        html = _roster_html(app)
        # 069 换壳后页序：module_tabs → sc-page-head → 055 品牌卡/汇总 → sc-card 表格
        assert html.index('sc-page-head') < html.index(
            '<section class="party-page-header') < html.index(
            '<section class="sc-card party-list-card')

    def test_page_head_single_primary(self, app):
        html = _roster_html(app)
        head = _slice(html, '<header class="sc-page-head">', '</header>')
        assert '党员管理' in head
        assert 'sc-page-head__meta' in head
        # §2.2 页头区主按钮唯一 = 新增党员（自品牌卡上移，069）
        assert head.count('sc-btn--primary') == 1
        assert 'id="btnAdd"' in head

    def test_seven_summary_items(self, app):
        # 080 A-1：7 → 8 格（补「退休党员」）
        html = _roster_html(app)
        grid = _slice(html, ANCHOR_GRID, ANCHOR_BRANCH)
        assert len(re.findall(r'class="party-summary-item[^"]*"', grid)) == 8

    def test_summary_labels_exact_order(self, app):
        html = _roster_html(app)
        grid = _slice(html, ANCHOR_GRID, ANCHOR_BRANCH)
        labels = [_label_text(seg) for seg in grid.split(ANCHOR_LABEL)[1:]]
        assert labels == ['在册党员', '正式党员', '预备党员', '流动党员', '退休党员',
                          '居民自动关联', '已建档党员', '已转出']

    def test_primary_and_alert_items(self, app):
        # 换壳后 value 与 label 同层（span 并列），主/警示格由容器类表达
        html = _roster_html(app)
        total = re.search(
            r'<div class="(party-summary-item[^"]*)"[^>]*>(?:(?!</div>).)*?id="summaryTotal"', html, re.S)
        transferred = re.search(
            r'<div class="(party-summary-item[^"]*)"[^>]*>(?:(?!</div>).)*?id="summaryTransferred"', html, re.S)
        assert total and ('is-primary' in total.group(1) or 'pw-kpi--primary' in total.group(1))
        assert transferred and ('is-alert' in transferred.group(1) or 'pw-kpi--alert' in transferred.group(1))

    def test_branch_summary_loading_placeholder(self, app):
        html = _roster_html(app)
        assert 'id="partyBranchSummary"' in html
        branch = _slice(html, 'id="partyBranchSummary"', '</div>')
        assert '正在加载' in branch

    def test_brand_actions_three_after_template_move(self, app):
        # 2026-11-19：下载模板移入批量导入弹窗 → 顶栏操作组 4→3（同步/导入/导出）
        html = _roster_html(app)
        actions = _slice(html, 'class="party-page-actions', '</section>')
        clickable = re.findall(r'<(?:button|a)\b', actions)
        assert len(clickable) == 3
        assert 'id="btnSyncResidents"' in actions and 'id="btnAdd"' not in actions
        order = [actions.index(x) for x in ('同步居民政治面貌', '批量导入', '导出名册')]
        assert order == sorted(order)
        assert '/party/api/template' not in actions, '下载模板必须收敛进导入弹窗'

    def test_template_bar_in_import_modal(self, app):
        # 设计稿 §5.1 第 2 区：模板下载条位于 impModal 内（pw-tplbar）
        html = _roster_html(app)
        imp = _slice(html, 'id="impModal"', 'id="dtModal"')
        assert 'pw-tplbar' in imp and '/party/api/template' in imp
        assert '下载标准导入模板' in imp

    def test_import_modal_drop_zone_and_mode_cards(self, app):
        # 设计稿 §5：双列单选卡（radio 组 #impMode 保留）+ File Drop Zone
        html = _roster_html(app)
        imp = _slice(html, 'id="impModal"', 'id="dtModal"')
        for dom_id in ('impMode', 'impFile', 'impRes', 'impDrop', 'impStaged', 'impSubmit'):
            assert f'id="{dom_id}"' in imp, f'导入弹窗缺契约节点 #{dom_id}'
        assert 'pw-drop' in imp and 'pw-radio-card' in imp
        assert 'value="append"' in imp and 'value="overwrite"' in imp
        assert '<select class="sc-select" id="impMode"' not in imp, '旧下拉导入模式应换为单选卡'

    def test_privacy_switch_present(self, app):
        # 设计稿 §4.3：检索栏右侧隐私脱敏滑动开关（默认开）
        html = _roster_html(app)
        assert 'id="pwPrivacySwitch"' in html
        assert 'aria-checked="true"' in html

    def test_card_header_has_no_action_buttons(self, app):
        html = _roster_html(app)
        toolbar = _slice(html, '<section class="sc-card party-list-card',
                         '<div class="sc-table-wrap"')
        for marker in ('id="btnAdd"', 'id="btnSyncResidents"',
                       '导出名册', '/party/api/template'):
            assert marker not in toolbar
        # 批量导入仅以弹窗唤起入口存在于概览卡，不在表卡工具条
        assert "$('#impModal')" not in toolbar

    def test_description_in_help_bubble(self, app):
        # 品牌卡长说明收进 ? 气泡（pw-help__bubble），关键词契约保持
        html = _roster_html(app)
        bubble = _slice(html, 'class="pw-help__bubble"', '</div>')
        for keyword in ('中共党员', '中共预备党员', '新增党员'):
            assert keyword in bubble


def _seed_roster(app):
    """080 A-1 四维构造：正式2 + 预备1 + 流动1 + 已转出1 + 居民自动关联1。

    身份/流动/在职/在册：正式甲（正式党员/在家/退休/在册，A 支部）、
    正式乙（正式党员/在家/在职/在册，B 支部）、预备丙（预备党员/在家/在册，空支部）、
    流动丁（身份空/流动/在册，A 支部）、转出戊（身份空/流动空/已转出，A 支部，必须不计入）；
    auto 居民归未分配。member_type 为历史列，本用例不再传值。
    """
    from models import PartyMember, Resident, db
    with app.app_context():
        db.session.query(PartyMember).delete()
        db.session.query(Resident).delete()
        db.session.commit()
        db.session.add_all([
            PartyMember(name='正式甲', member_identity='正式党员', member_mobility='在家',
                        employment_status='退休', party_status='在册', branch='A支部'),
            PartyMember(name='正式乙', member_identity='正式党员', member_mobility='在家',
                        employment_status='在职', party_status='在册', branch='B支部'),
            PartyMember(name='预备丙', member_identity='预备党员', member_mobility='在家',
                        party_status='在册', branch=''),
            PartyMember(name='流动丁', member_identity='', member_mobility='流动',
                        party_status='在册', branch='A支部'),
            PartyMember(name='转出戊', member_identity='', member_mobility='',
                        party_status='已转出', branch='A支部'),
            Resident(name='自动己', politics_status='中共党员', id_card_hash='a' * 64),
        ])
        db.session.commit()


def _summary(app):
    resp = _admin_client(app).get('/party/api/summary')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'], body
    return body['data']


class TestPartySummaryBranchStats:
    def test_counts(self, app):
        _seed_roster(app)
        d = _summary(app)
        assert d['total_archived'] == 5
        assert d['on_roll'] == 4
        assert d['formal'] == 2
        assert d['probation'] == 1
        assert d['floating'] == 1
        assert d['retired'] == 1
        assert d['transferred'] == 1
        assert d['auto'] == 1

    def test_transferred_excluded_and_unassigned(self, app):
        _seed_roster(app)
        d = _summary(app)
        stats = {x['branch']: x['count'] for x in d['branch_stats']}
        assert stats['A支部'] == 2, '已转出人员不得计入支部人数'
        assert stats['B支部'] == 1
        assert stats['未分配党支部'] == 2, '空支部预备党员 + auto 居民应合并未分配'
        assert d['unassigned_branch'] == stats['未分配党支部']

    def test_sort_order(self, app):
        _seed_roster(app)
        d = _summary(app)
        expected = sorted(
            {'A支部': 2, 'B支部': 1, '未分配党支部': 2}.items(),
            key=lambda kv: (-kv[1], kv[0]))
        assert [(x['branch'], x['count']) for x in d['branch_stats']] == expected

    def test_formal_to_transferred_reduces_counts(self, app):
        from models import PartyMember, db
        _seed_roster(app)
        with app.app_context():
            m = PartyMember.query.filter_by(name='正式甲').first()
            m.member_identity = ''
            m.member_mobility = ''
            m.party_status = '已转出'
            db.session.commit()
        d = _summary(app)
        assert d['formal'] == 1
        assert d['on_roll'] == 3
        assert d['total_archived'] == 4
        stats = {x['branch']: x['count'] for x in d['branch_stats']}
        assert stats['A支部'] == 1
