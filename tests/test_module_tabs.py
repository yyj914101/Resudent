# -*- coding: utf-8 -*-
"""tests/test_module_tabs.py — 分组胶囊宏（065）结构守卫。

守护四件事：
  1) civil 是两行 6+5 + 两个行标签（A 方案 §3.3 真源）
  2) 单行模块无行标签
  3) active_key 命中唯一；不存在 key → 0 个激活且不报错
  4) 五个模块 key 都能渲染出胶囊；无效 key 渲染为空

2026-11-16：tools 组撤除（证明开具 / 死亡上报 / 导入批次三项中，后两者按主理人指示
升为侧栏入口：死亡上报→社区治理组、导入批次→数据管理组），故原「tools 权限过滤」
用例随之退役；胶囊组的权限过滤能力仍由宏内 `it.get('perm')` 分支保留。
"""
import os
import re
import sys

from flask import render_template_string

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TPL = ("{% from 'components/_macros/module_tabs.html' import module_tabs %}"
        "{{ module_tabs('__KEY__', '__ACTIVE__') }}")


def _render(app, module_key, active_key=''):
    # 用占位符替换而不是 %-格式化：模板里的 `%}` 会被 Python 的 % 运算当格式符
    src = _TPL.replace('__KEY__', module_key).replace('__ACTIVE__', active_key)
    with app.test_request_context('/'):
        return render_template_string(src)


_CHIP_RE = re.compile(r'class="sc-chip(?: is-active)?"')


def _chips(html):
    """数胶囊本身：class="sc-chip" / class="sc-chip is-active"（不含 sc-chips 容器类）。"""
    return len(_CHIP_RE.findall(html))


def test_civil_two_rows(isolated_app_factory):
    html = _render(isolated_app_factory(), 'civil', 'dibao')
    # 2026-11-16 主理人派单：胶囊文案补全 + 重排（8 枚 = 对象查询 6 + 报表 2），
    # 就业服务/对账/走访计划/走访记录迁出；新增优抚对象。栅格平铺契约不变。
    assert html.count('sc-chips__grid') == 1
    assert html.count('sc-chips__row') == 0, 'civil 已改栅格平铺，不应残留逐行 __row'
    assert _chips(html) == 9
    assert html.count('sc-chips__label') == 2
    assert '对象查询' in html and '报表' in html and '报表 · 走访' not in html
    # 文案补全（旧短名 低保/残疾/关爱/公益岗/弱势/统计表/花名册 退役）
    for label in ('低保查询', '残疾查询', '关爱对象', '公益岗查询', '弱势群体查询',
                  '优抚对象', '一老一小统计表', '民政对象总花名册', '报表审批'):
        assert '>' + label + '<' in html, '缺胶囊文案：%s' % label
    for retired in ('>低保<', '>残疾<', '>关爱<', '>公益岗<', '>弱势<', '>统计表<', '>花名册<'):
        assert retired not in html, '旧短名残留：%s' % retired
    # D-18 hash 承载（验收 3）：8 个 href 全部带 #tab=<key>，不得纯路由跳转
    assert len(re.findall(r'href="[^"]*#tab=[^"]+"', html)) == 9
    assert '#tab=dibao' in html and '#tab=roster' in html and '#tab=youshu' in html and '#tab=word-form' in html
    # 迁出项不再出现在 civil 组
    for gone in ('#tab=visit-plan', '#tab=visit-log', '#tab=reconcile', '#tab=employment'):
        assert gone not in html, '已迁出项残留：%s' % gone



def test_single_row_without_label(isolated_app_factory):
    """单行组（居民）无行标签：3 枚胶囊 + 0 标签。

    （原 tools 组用例随 2026-11-16 撤组退役，权限过滤能力由宏内 perm 分支保留。）
    """
    html = _render(isolated_app_factory(), 'resident', 'add')
    assert html.count('sc-chips__row') == 1
    assert html.count('sc-chips__label') == 0
    assert _chips(html) == 4      # 居民查询 / 新增居民 / 店铺信息 / 设施分布
    assert html.count('is-active') == 1 and '#tab=add' in html


def test_active_is_unique_or_none(isolated_app_factory):
    app = isolated_app_factory()
    html = _render(app, 'civil', 'dibao')
    assert html.count('is-active') == 1
    assert '>低保查询<' in html
    # 反例：不存在的 key → 0 激活，且不抛错
    assert _render(app, 'civil', 'no-such-key').count('is-active') == 0


def test_social_group_medical_pension_employment(isolated_app_factory):
    """102 / 2026-11-16：社会保障页内胶囊——医保参保管理 / 养老保险 / 就业服务（迁入）。"""
    html = _render(isolated_app_factory(), 'social', 'pension')
    assert _chips(html) == 3
    assert '医保参保管理' in html and '养老保险' in html and '就业服务' in html
    assert html.count('is-active') == 1 and '#tab=pension' in html
    assert '#tab=medical-insurance' in html and '#tab=employment' in html


def test_governance_group_three_partitions(isolated_app_factory):
    """2026-11-16：综治管理按「人员管理 / 场所与行业 / 矛盾调处」三类重排。

    社区矫正 / 社区戒毒康复 已下线；刑满释放帮教改名刑事人员；
    特困人员走访 / 走访计划 / 走访记录 迁出至 visitcare 组。
    """
    html = _render(isolated_app_factory(), 'governance', 'criminal')
    assert html.count('sc-chips__grid') == 1, '三类分区必须走栅格平铺'
    assert html.count('sc-chips__row') == 0
    assert _chips(html) == 9, '综治管理应有 9 枚胶囊（3+3+3）'
    # 137（补丁 B-2）：固定行业务行标签显式 grid-column:1 保证列对齐，标签可能带 style 属性
    labels = re.findall(r'class="sc-chips__label"[^>]*>([^<]+)<', html)
    assert labels == ['人员管理', '场所与行业', '矛盾调处'], labels
    for key in ('#tab=mental-health', '#tab=security-committee', '#tab=criminal',
                '#tab=road-hazard', '#tab=key-vehicle', '#tab=guide-station',
                '#tab=mediation', '#tab=stability-risk', '#tab=petition'):
        assert key in html, '缺胶囊：%s' % key
    assert '#tab=criminal' in html and '刑事人员' in html
    assert html.count('is-active') == 1
    # 下线与迁出项零残留
    for gone in ('#tab=community-correction', '#tab=drug-rehab',
                 '#tab=resettlement', '#tab=special-visit',
                 '#tab=visit-plan', '#tab=visit-log'):
        assert gone not in html, '已下线/已迁出项残留：%s' % gone
    for text in ('社区矫正', '社区戒毒康复', '刑满释放帮教'):
        assert text not in html, '旧文案残留：%s' % text


def test_visitcare_group_three_chips(isolated_app_factory):
    """2026-11-16：走访关爱独立组——特困人员走访 / 走访计划 / 走访记录（宿主走访计划页）。"""
    html = _render(isolated_app_factory(), 'visitcare', 'visit-plan')
    assert html.count('sc-chips__row') == 1
    assert html.count('sc-chips__label') == 0, '单行组无行标签'
    assert _chips(html) == 3
    for key in ('#tab=special-visit', '#tab=visit-plan', '#tab=visit-log'):
        assert key in html, '缺胶囊：%s' % key
    assert html.count('is-active') == 1 and '#tab=visit-plan' in html


def test_death_group_two_chips(isolated_app_factory):
    """2026-11-16：死亡名单对账由民政对象迁出，与死亡上报成组（death 组，宿主死亡上报页）。"""
    html = _render(isolated_app_factory(), 'death', 'death')
    assert _chips(html) == 2
    assert '死亡上报' in html and '死亡名单对账' in html
    assert html.count('is-active') == 1 and '#tab=death' in html and '#tab=reconcile' in html



def test_all_modules_render_and_unknown_is_empty(isolated_app_factory):
    app = isolated_app_factory()
    for key in ('civil', 'resident', 'social', 'org', 'governance', 'visitcare', 'death'):
        assert 'sc-chip' in _render(app, key), key
    assert _render(app, 'not-a-module').strip() == ''
    # 2026-11-16 撤组：tools 不再渲染任何胶囊
    assert _render(app, 'tools').strip() == ''
