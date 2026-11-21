# -*- coding: utf-8 -*-
"""GLB-02：SPA 页面级 extra_css 注入守卫。

SPA 只替换 #contentArea，而 {% block extra_css %} 在 <head>；若不注入，
经侧栏进入 dashboard / certificate 等页面时对应样式表不加载（035 R-035-02 根因）。

本文件只做静态守卫：确保注入 seam 存在、全局样式被过滤、同 URL 去重、
且调用发生在内容替换之前。运行时由浏览器验收（见 053-RESULT）。
"""
import os

import pytest


def _spa_js():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'static', 'js', 'spa.js')
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def test_glb02_inject_seam_and_order():
    js = _spa_js()
    assert 'function injectPageStyles($parsed)' in js
    assert "link.setAttribute('data-spa-page-css', '1')" in js
    # 调用必须在内容替换之前，样式才能随新内容一起生效
    call = js.index('injectPageStyles($parsed)')
    replace = js.index("$('#contentArea').html(contentHtml)")
    assert call < replace


def test_glb02_global_styles_filtered_and_deduped():
    js = _spa_js()
    assert 'var SPA_GLOBAL_STYLES' in js
    for name in ('bootstrap.min.css', 'adminlte.min.css', 'bootstrap-icons',
                 'css/tokens.css', 'css/app.css', 'css/skeleton.css'):
        assert name in js
    assert 'function isGlobalStyle(href)' in js
    assert 'if (!raw || isGlobalStyle(raw)) return;' in js
    assert 'function hasPageStyle(url)' in js
    assert 'if (hasPageStyle(a.href)) return;' in js


@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


def test_glb02_ajax_response_carries_page_css(app):
    """前置条件：页面 SSR 响应确含 extra_css 链接（客户端 seam 有东西可注入）。

    058 起首页样式已迁全局层（dashboard 不再挂 extra_css）；062 P2 起待办页样式
    并入全局 components.css（todo_list.css 已删），改用组织架构页作样本。
    """
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    html = client.get('/organization/').get_data(as_text=True)
    assert 'css/pages/organization_list.css' in html
