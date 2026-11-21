# -*- coding: utf-8 -*-
"""055-FIX 响应式守卫（leader 验收补丁，2026-10-27）。

背景：055 逐字复刻 exe V2.45.2 党员页 CSS，连带继承了它的一处原生缺陷 ——
`.party-page-actions` 是 `flex: 0 0 auto`（实测固定 565px），而 `.party-page-brand`
是 `min-width: 0`（可被无限压缩）。在 992~1199.98 区间（页头仍是 row 布局、
`991.98` 的 column 断点不覆盖）品牌区被压到 100px 以内：

    1024×768 修复前实测：headerH=1087px、titleW=51px、titleLines=2、descH=751px

修复：该区间改为两行堆叠（`flex-wrap: wrap` + brand 占满一行 + 按钮左对齐），
与 768 断点的降级形态一致；1440/1200 零影响（实测 headerH 298/362，仍单行）。

本文件用源码级断言钉住这两处，回退即红。运行时布局由 055 实拍图佐证
（`gui-test-screenshots/055/party-1024x768.png` 修复前 / `-fixed.png` 修复后）。
"""
import io
import os
import re

import pytest

CSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'static', 'css', 'pages', 'party_list.css')


def _css():
    with io.open(CSS_PATH, encoding='utf-8') as f:
        return f.read()


def _media_block(css, header):
    """取 `@media ... {` 起、括号平衡到 `}` 的整块文本。"""
    i = css.index(header)
    j = css.index('{', i)
    depth = 0
    k = j
    while k < len(css):
        if css[k] == '{':
            depth += 1
        elif css[k] == '}':
            depth -= 1
            if depth == 0:
                return css[i:k + 1]
        k += 1
    raise AssertionError('媒体查询块未闭合: %s' % header)


class TestResponsiveGuards:
    def test_992_1199_band_stacks_header(self):
        """992~1199.98 必须两行堆叠 —— 这是 1024 宽不塌陷的唯一原因。"""
        block = _media_block(_css(), '@media (min-width: 992px) and (max-width: 1199.98px)')
        assert '.party-page-header-row' in block
        assert re.search(r'\.party-page-header-row\s*\{[^}]*flex-wrap:\s*wrap', block), \
            '992~1199.98 区间缺少 flex-wrap: wrap → 1024 宽页头会塌陷'
        assert re.search(r'\.party-page-brand\s*\{[^}]*flex:\s*1 1 100%', block), \
            '品牌区必须占满一行，否则会与固定宽按钮组抢宽度'
        assert re.search(r'\.party-page-actions\s*\{[^}]*justify-content:\s*flex-start', block), \
            '换行后按钮应左对齐（与 768 断点一致）'

    def test_actions_keep_intrinsic_width(self):
        """按钮组必须是固有宽度：若被改成可收缩，按钮文字会挤压换行。"""
        block = _media_block(_css(), '.party-page-actions {')
        assert 'flex: 0 0 auto' in block

    def test_768_band_is_two_columns(self):
        """768 整宽必须命中 2 列 —— 用 767.98 会落到 991.98 的 4 列，不满足验收 3.3。"""
        block = _media_block(_css(), '@media (max-width: 768px)')
        assert 'repeat(2, minmax(110px, 1fr))' in block
        assert '.party-branch-summary' in block
        # 防回退成 Bootstrap 惯例的 767.98（那会让 768 视口差 0.02px 落回 4 列）
        assert '@media (max-width: 767.98px)' not in _css()

    def test_1199_band_is_four_columns(self):
        """1199.98 仍为 4 列（Bootstrap xl 之前的既有行为，不在本次修改范围）。"""
        block = _media_block(_css(), '@media (max-width: 1199.98px)')
        assert 'repeat(4, minmax(100px, 1fr))' in block
