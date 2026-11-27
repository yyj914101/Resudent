# -*- coding: utf-8 -*-
"""tests/test_civilization_ui_guard.py — 089 文明实践站门面页 UI 源码级守卫（2026-11-13）。

背景（ui-adjustments-20261101.md 第十六节）：民政详情 v2 自定义内容层继承
BS5 `.modal-dialog` 的 `pointer-events: none`，导致底部按钮与表单「看着正常、
点击全失效」；SSR 快照只做文本 diff，对 CSS 交互属性零感知。本页改版把该风险
拆成可自动回归的源码断言，运行时命中由浏览器实测（getComputedStyle +
elementFromPoint + 真实点击）佐证。

守卫维度：
  1. 离线自包含：零外链（不引 Tailwind CDN），页面样式走 extra_css 本地文件；
  2. 令牌纪律：页面 CSS 零裸色值，颜色/圆角/间距全 var() 引用 tokens.css；
  3. 指针事件：交互层显式 auto、禁用态保留 none、隐藏文件域可命中；
  4. 上传链路：label 包裹隐藏 input + change 触发，禁止按钮 onclick 读到空 files；
  5. 弹窗壳：沿用已守卫的 .sc-modal，不引入未登记白名单的自定义内容层；
  6. 结构契约：指标/操作/表格 id 与口径 title 不得在改版中丢失（089 门禁依赖）。
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, 'templates', 'civilization', 'index.html')
CSS = os.path.join(ROOT, 'static', 'css', 'pages', 'civilization.css')
JS = os.path.join(ROOT, 'static', 'js', 'pages', 'civilization.js')
ATT_JS = os.path.join(ROOT, 'static', 'js', 'pages', 'module_attachments.js')


def _read(path):
    with io.open(path, encoding='utf-8') as fh:
        return fh.read()


def test_offline_selfcontained_and_local_css():
    """离线自包含：静态资源全部本地，页面级 CSS 由 extra_css 引入。"""
    tpl = _read(TPL)
    assert 'cdn.tailwindcss.com' not in tpl
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', tpl), '页面不得引用外链资源'
    assert "{% block extra_css %}" in tpl
    assert "filename='css/pages/civilization.css'" in tpl


def test_css_all_colors_are_tokens():
    """页面 CSS 只承载版式，色值/圆角/间距一律 var() 引用三层令牌。"""
    css = _read(CSS)
    assert not re.findall(r'#[0-9a-fA-F]{3,8}\b', css), '不得出现裸 hex 色值'
    assert not re.findall(r'\brgba?\(', css), '不得出现裸 rgb/rgba 色值'
    for token in ('var(--c-ink)', 'var(--c-muted)', 'var(--c-line)',
                  'var(--r-card)', 'var(--sp-6)', 'var(--ff-num)'):
        assert token in css, '缺令牌引用 %s' % token


def test_css_comments_and_braces_balanced():
    """CSS 注释解析级审计：注释块数闭合、花括号平衡（防「注释吞规则」）。"""
    css = _read(CSS)
    assert css.count('/*') == css.count('*/'), '存在未闭合或提前闭合的注释块'
    assert css.count('{') == css.count('}'), '花括号不平衡'


def test_interactive_layers_restore_pointer_events():
    """第十六节核心：所有交互层显式 auto，禁用态仍 none。"""
    css = _read(CSS)
    assert re.search(r'\.cv-page\s*\{[\s\S]{0,300}?pointer-events:\s*auto\s*;', css), \
        '页面容器必须恢复 pointer-events: auto'
    assert re.search(r'\.cv-page\s+button,[\s\S]{0,400}?pointer-events:\s*auto\s*;', css), \
        '按钮/表单/label/summary 兜底规则必须存在'
    assert re.search(r'\.cv-page\s+button:disabled,[\s\S]{0,120}?pointer-events:\s*none\s*;', css), \
        '禁用态必须保留 pointer-events: none'


def test_hidden_file_input_is_clickable():
    """设计稿 §5.2：隐藏文件域仍可命中，覆盖整个上传按钮区域。"""
    css = _read(CSS)
    m = re.search(r'\.cv-upload__input\s*\{([^}]*)\}', css)
    assert m, '缺隐藏文件域规则'
    body = m.group(1)
    for decl in ('position: absolute', 'inset: 0', 'opacity: 0',
                 'cursor: pointer', 'pointer-events: auto'):
        assert decl in body, '隐藏文件域缺声明：%s' % decl
    m2 = re.search(r'\.cv-upload\s*\{([^}]*)\}', css)
    assert m2 and 'position: relative' in m2.group(1) and 'overflow: hidden' in m2.group(1)


def test_upload_uses_label_and_change_event():
    """上传按钮不得挂 onclick（选文件前读空 files 会误报且看似点不动）。"""
    tpl, js, att = _read(TPL), _read(JS), _read(ATT_JS)
    assert re.search(r'<label[^>]*cv-upload[^>]*>', tpl), '上传控件须为 label 包裹'
    assert re.search(r'<input[^>]*id="attFile"[^>]*>', tpl), '隐藏 input 须保留 id="attFile"'
    assert 'onclick="onAttachmentUpload()"' not in tpl
    assert "addEventListener('change'" in js, '须由 change 事件触发上传'
    assert 'window.onAttachmentUpload' in js
    assert 'emptyHtml' in att, '092 共用脚本须支持设计稿空态且缺省行为不变'
    assert 'esc(emptyText)' in att, '未传 emptyHtml 时须维持原纯文本空态'
    assert 'window.initAttachments = initAttachments' in att, \
        'SPA 用 new Function 重放脚本，顶层声明不挂 window → 须显式导出，否则内联初始化抛 ReferenceError'


def test_spa_boot_compatible():
    """SPA 伪导航用 new Function 执行脚本，readyState 已 complete —— 纯 DOMContentLoaded 会失效。"""
    js = _read(JS)
    assert 'function boot()' in js
    assert "document.readyState === 'loading'" in js
    assert "document.addEventListener('DOMContentLoaded', boot)" in js
    assert re.search(r'\}\s*else\s*\{\s*boot\(\);', js), '非 loading 态须立即 boot'


def test_modal_keeps_guarded_shell():
    """不引入绕过 .modal-content 的自定义内容层（detail_v2 守卫白名单口径）。"""
    tpl = _read(TPL)
    assert 'class="sc-modal"' in tpl
    assert 'd2-shell' not in tpl and 'ed2-shell' not in tpl


def test_contract_ids_and_denoised_tooltips():
    """089 门禁硬断言的 id 与口径收纳（长文案进 title）不得丢失。"""
    tpl = _read(TPL)
    for ident in ('civSessions', 'civPersonVisits', 'civStatYear',
                  'addConventionBtn', 'editConventionBtn', 'conventionHistoryBody',
                  'conventionHistoryWrap', 'conventionForm', 'attTbody', 'attFile'):
        assert 'id="%s"' % ident in tpl, '缺契约 id：%s' % ident
    assert 'title="口径：年度场次' in tpl
    assert 'title="口径：年度人次' in tpl
    assert '<ol class="cv-convention__list">' not in tpl, '条款由 JS 按真实数据渲染，不写死'
