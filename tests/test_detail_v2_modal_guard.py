# -*- coding: utf-8 -*-
"""详情/编辑弹窗 v2 可交互守卫（2026-11-11）。

背景：Bootstrap 5 的 `.modal-dialog` 默认 `pointer-events: none`，靠 `.modal-content`
把内容层恢复成 `auto`。detail_v2 弹窗用自定义 `.d2-shell` / `.ed2-shell` 直接替代
`.modal-content`，却没恢复该属性 —— 结果是整条子树继承 `none`：详情弹窗底部
【删除】【关闭】【编辑资料】全不可点，编辑弹窗 28 个表单控件与【保存修改】也全不可点。
页面看着正常、接口也正常，只有真实点击才暴露。

修复口径与既有 `.sc-modal` 一致：内容层显式 `pointer-events: auto`。
本文件用源码级断言钉住，回退即红；运行时命中由浏览器实测佐证。
"""
import io
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(ROOT, 'static', 'css', 'components.css')
TPL_DIR = os.path.join(ROOT, 'templates')


def _read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def _rule_body(css, selector):
    """取 `selector { ... }` 的完整规则体（花括号平衡，避免被后续规则截断）。"""
    i = css.index(selector + ' {')
    j = css.index('{', i)
    depth = 0
    for k in range(j, len(css)):
        if css[k] == '{':
            depth += 1
        elif css[k] == '}':
            depth -= 1
            if depth == 0:
                return css[j:k + 1]
    raise AssertionError('%s 规则未闭合' % selector)


@pytest.mark.parametrize('selector', ['.d2-shell', '.ed2-shell'])
def test_custom_modal_shell_restores_pointer_events(selector):
    body = _rule_body(_read(CSS_PATH), selector)
    assert re.search(r'pointer-events\s*:\s*auto\s*;', body), (
        '%s 必须恢复 pointer-events: auto，否则弹窗内所有控件不可点击' % selector)


def test_no_uncovered_custom_modal_shell():
    """新增自定义弹窗内容层若绕过 .modal-content，必须同步补 pointer-events 或登记白名单。"""
    covered = {'d2-shell', 'ed2-shell'}
    found = set()
    for dirpath, _, names in os.walk(TPL_DIR):
        for name in names:
            if not name.endswith('.html'):
                continue
            lines = _read(os.path.join(dirpath, name)).splitlines()
            for i, line in enumerate(lines):
                if 'modal-dialog' not in line or 'class=' not in line:
                    continue
                seg = '\n'.join(lines[i:i + 4])
                after = seg.split('modal-dialog', 1)[1]
                m = re.search(r'<div class="([^"]+)"', after)
                if not m:
                    continue
                first = m.group(1).split()[0]
                if first.startswith('sc-modal') or first == 'modal-content':
                    continue
                found.add(first)
    extra = found - covered
    assert not extra, (
        '发现未守卫的自定义弹窗内容层 %s：请补 pointer-events: auto 并加入本测试白名单' % sorted(extra))
