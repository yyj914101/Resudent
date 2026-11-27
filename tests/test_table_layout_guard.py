# -*- coding: utf-8 -*-
"""全站表格 / 复选框布局守卫（2026-11-13）。

两处实测根因，任一回退即红：

1. `.sc-checkbox` 未限定标签时，被误挂到 `.sc-field` 容器上会把 211px 宽的表单
   字段压成 18x18，中文标签逐字竖排（登记选民 / 结果录入弹窗截图实证）。
2. `.sc-table__th` 有 `white-space: nowrap` 而 td 没有；且大量 tbody 由 JS 渲染为
   裸 `<td>`（无 `sc-table__td` 类，如 todo_list.js），只给带类选择器补 nowrap
   会漏掉它们，列多表格的中文单元格被压成窄高多行（/todo/ 标题列 102px/85px）。
   超宽必须交给 `.sc-table-wrap(overflow-x:auto)` 横向滚动。

本文件只做源码级断言，不依赖数据库、不渲染页面。
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(ROOT, 'static', 'css', 'components.css')
TEMPLATE_DIR = os.path.join(ROOT, 'templates')


def _read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def _rule(css, selector):
    m = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    assert m, '未找到选择器 %s 的规则块' % selector
    return m.group(1)


def test_checkbox_size_rule_is_tag_scoped():
    css = _read(CSS_PATH)
    body = _rule(css, 'input.sc-checkbox')
    assert 'width: 18px' in body and 'height: 18px' in body, (
        '复选框尺寸必须限定在 input 上，否则容器会被压缩')


def test_bare_sc_checkbox_has_no_size_rules():
    """裸 .sc-checkbox 可用于 margin 等无害属性，但不得再出现 width/height。"""
    css = _read(CSS_PATH)
    for m in re.finditer(r'(?<![\w-])\.sc-checkbox\s*\{([^}]*)\}', css):
        body = m.group(1)
        assert 'width' not in body and 'height' not in body, (
            '裸 .sc-checkbox 尺寸规则会压塌 .sc-field 容器')


def test_bare_table_td_has_nowrap():
    """覆盖 crudTable / 手写 JS 输出的裸 <td>（2026-11-13 补漏点）。"""
    css = _read(CSS_PATH)
    body = _rule(css, '.sc-table td')
    assert 'white-space: nowrap' in body, (
        '裸 td 缺 nowrap 时中文单元格会被压成逐字竖排（/todo/ 实测）')


def test_classed_table_td_keeps_nowrap():
    css = _read(CSS_PATH)
    assert 'white-space: nowrap' in _rule(css, '.sc-table__td')


def test_table_wrap_provides_horizontal_scroll():
    css = _read(CSS_PATH)
    assert 'overflow-x: auto' in _rule(css, '.sc-table-wrap'), (
        'td 撑开后必须由 .sc-table-wrap 横向滚动兜底')


def test_checkbox_class_only_on_input_in_templates():
    bad = []
    for root, _dirs, files in os.walk(TEMPLATE_DIR):
        for name in files:
            if not name.endswith('.html'):
                continue
            path = os.path.join(root, name)
            src = _read(path)
            for m in re.finditer(
                    r'<(\w+)([^>]*class="[^"]*\bsc-checkbox\b[^"]*"[^>]*)>', src):
                if m.group(1).lower() != 'input':
                    bad.append((os.path.relpath(path, ROOT), m.group(1)))
    assert not bad, 'sc-checkbox 只允许挂在 <input> 上，越界：%s' % bad
