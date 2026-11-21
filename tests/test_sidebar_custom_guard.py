# -*- coding: utf-8 -*-
"""侧栏自定义能力守卫（2026-11-11）。

覆盖两项：
① 菜单名可编辑：范围仅 #menu 内菜单项文字、localStorage 持久化、校验与恢复默认；
② 窄屏抽屉可重复收起：遮罩 + 侧栏关闭按钮 + 单一状态入口 + class 观察器。

本文件全部为源码级静态断言：不依赖浏览器，钉住「不能回退」的契约。
运行时行为由 Playwright 多视口走查佐证（见 ui-adjustments 第十八节）。
"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, 'static', 'js', 'app.js')
SHELL_JS = os.path.join(ROOT, 'static', 'js', 'shell.js')
SHELL_CSS = os.path.join(ROOT, 'static', 'css', 'shell.css')
BASE_HTML = os.path.join(ROOT, 'templates', 'base.html')
NAV_SPEC = os.path.join(ROOT, 'utils', 'nav_spec.py')


def _read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def test_global_toggle_contract_kept():
    """base.html 的 inline onclick 依赖全局 toggleSidebar，改名会让窄屏展开直接失效。"""
    assert 'onclick="toggleSidebar()"' in _read(BASE_HTML)
    assert 'window.toggleSidebar = function' in _read(APP_JS)


def test_sidebar_state_has_single_entry_and_observer():
    src = _read(APP_JS)
    assert 'window.setSidebarOpen' in src
    assert 'MutationObserver' in src, (
        'shell.js 的 Esc 与 spa.js 换页会直接删 sidebar-open，需观察器同步遮罩/按钮状态')
    assert "OVERLAY_CLS = 'sc-sidebar-overlay'" in src
    assert 'scSidebarClose' in src
    assert "max-width: 991.98px" not in src or 'MOBILE_MQ' in src


def test_narrow_viewport_overlay_and_close_css():
    css = _read(SHELL_CSS)
    assert 'body.sidebar-open .sc-sidebar-overlay' in css
    assert 'body.sidebar-open .sc-sidebar-close' in css
    assert 'z-index: 1037' in css, '遮罩须低于 .app-sidebar(1038)，否则侧栏一并被盖'
    assert css.count('@media (max-width: 991.98px)') >= 2


def test_nav_label_edit_scope_and_validation():
    src = _read(SHELL_JS)
    assert "KEY = 'sc.nav.labels'" in src
    assert "getElementById('menu')" in src, '未登录页无 #menu 时不得注入编辑 UI'
    assert 'querySelectorAll(\'li > a.nav-link[href]\')' in src
    assert 'span.textContent = next' in src, '必须走 textContent，禁止 innerHTML 注入用户输入'
    assert 'var MAX = 16' in src
    assert '/[<>\\x00-\\x1f]/' in src, '须拒绝尖括号与控制字符'
    assert '全部恢复默认' in src


def test_nav_spec_is_untouched_single_source():
    """改名只作用于前端展示，Python 真源与 SSR 结构不得被改写。"""
    assert 'NAV_GROUPS' in _read(NAV_SPEC)
    assert 'url_for(item.endpoint)' in _read(
        os.path.join(ROOT, 'templates', 'components', '_partials', 'sidebar_menu.html'))


def test_nav_label_boot_before_first_paint():
    """2026-11-16 闪烁修复：菜单名覆盖必须在首次绘制前同步完成。

    复现（5091 整页导航，addInitScript 采样）：
      t=130ms readyState=interactive → span「民政对象管理」(SSR 原名)
      t=155ms readyState=complete   → span「社会救助」(DOMContentLoaded 后 apply)
    根因：shell.js 在 body 末尾同步加载、菜单 DOM 已解析完，但旧实现因
    readyState==='loading' 挂到 DOMContentLoaded 才 apply()，中间浏览器已把
    SSR 原名画出来。功能胶囊是普通 <a>（spa.js 只拦截侧栏链接），每次切换
    都是整页导航 → 每次必现「原名↔新名」闪烁。
    修复：菜单链接已存在时立即 initNavLabels()；无菜单/链接未就绪仍回退
    DOMContentLoaded（登录页与模板顺序调整不漏覆盖）。
    """
    src = _read(SHELL_JS)
    assert "var bootMenu = document.getElementById('menu');" in src, \
        '缺首帧前引导：菜单 DOM 就绪时必须立即初始化，不能只等 DOMContentLoaded'
    assert "bootMenu.querySelectorAll('li > a.nav-link[href]')" in src
    assert 'if (bootLinks && bootLinks.length) {\n        initNavLabels();' in src
    assert "document.addEventListener('DOMContentLoaded', initNavLabels);" in src, \
        '须保留 DOMContentLoaded 兜底（无 #menu 的页面 / 模板顺序调整）'

    # 默认名基线必须来自 SSR 属性，避免「立即覆盖后 defaults 缓存了自定义名」导致恢复默认失效
    assert "a.getAttribute('data-sc-nav-default')" in src
    assert "a.setAttribute('data-sc-nav-default', defaults[key])" in src


def test_nav_label_boot_matches_shell_contract():
    """首帧引导脚本与 shell.js 必须使用同一套契约，禁止单边漂移。

    引导脚本在 <head> 同步加载（base.html），菜单节点插入 DOM 的同一微任务内
    完成覆盖；shell.js 负责面板、storage 同步与运行期重渲染兜底。
    """
    boot = _read(os.path.join(ROOT, 'static', 'js', 'nav_labels_boot.js'))
    shell = _read(SHELL_JS)
    base = _read(BASE_HTML)

    assert "filename='js/nav_labels_boot.js'" in base, 'base.html 须在 head 同步加载引导脚本'
    # 引导脚本必须早于 __SPA_CFG__ 之外的所有 body 脚本（head 内即满足）
    assert base.index("js/nav_labels_boot.js") < base.index('</head>'), \
        '引导脚本必须在 </head> 之前，否则赶不上首帧'

    for token in ("'sc.nav.labels'", 'data-sc-nav-default', 'textContent',
                  'MutationObserver', 'DOMContentLoaded'):
        assert token in boot, '引导脚本缺契约项：%s' % token
    assert 'var MAX = 16' in shell and 'v.length > 16' in boot, \
        '两处长度上限须一致（1~16 字符）'
    assert '/[<>\\x00-\\x1f]/' in shell and '/[<>\\x00-\\x1f]/' in boot, \
        '两处非法字符校验须一致'
    assert "split('#')[0].split('?')[0]" in shell and "split('#')[0].split('?')[0]" in boot, \
        '两处 key 归一化（去 hash / query）须一致'
    assert 'innerHTML' not in boot, '引导脚本禁止 innerHTML 写入用户输入'
