# -*- coding: utf-8 -*-
"""tests/test_dynatable_upload_ui_guard.py — 台账建表「上传 Excel 建表」页 UI 源码级守卫（2026-11-16）。

背景（ui-adjustments-20261101.md 第十六节）：透明/自定义交互层极易踩
`pointer-events` 继承链，出现「看着能点、点了没反应」；SSR 快照只做文本 diff，
对 CSS 交互属性零感知。本次按 ADR-022 设计稿把原生 file 域改成「透明铺满拖拽区」，
风险点随之集中到该覆盖层，故拆成可自动回归的源码断言（运行时命中另由浏览器实测佐证：
getComputedStyle + elementFromPoint + 真实点击 + 拖拽 drop）。

守卫维度：
  1. 离线自包含：零外链，页面样式走 extra_css 本地文件；
  2. 令牌纪律：页面 CSS 零裸色值（内联 SVG 箭头用 %23 编码，不算裸值）；
  3. 指针事件：整个页面 CSS 不得出现 pointer-events: none；透明 file 域必须
     absolute 铺满 + opacity:0（点击穿透实现方式），容器不得拦截；
  4. JS 契约：承重墙 id 不变、拖拽落文件走 DataTransfer、重置清空回显；
  5. 结构契约：顶对齐标签结构（label 在控件之前、字段容器纵向排列）。
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, 'templates', 'dynatable', 'upload.html')
CSS = os.path.join(ROOT, 'static', 'css', 'pages', 'dynatable_upload.css')
JS = os.path.join(ROOT, 'static', 'js', 'pages', 'dynatable_upload.js')

# dynatable_upload.js 直接按这些 id 取值；改版不得改名
REQUIRED_IDS = ['uploadForm', 'f_file', 'f_name', 'f_code', 'f_module', 'f_group',
                'f_desc', 'submitBtn', 'resetBtn', 'uploadMsg', 'dropZone', 'fileName']


def _read(path):
    with io.open(path, encoding='utf-8') as fh:
        return fh.read()


def test_offline_selfcontained_and_local_css():
    """离线自包含：静态资源全部本地，页面级 CSS 由 extra_css 引入。"""
    tpl = _read(TPL)
    assert 'cdn.tailwindcss.com' not in tpl, '不得引 Tailwind CDN（全站零外链）'
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', tpl), '页面不得引用外链资源'
    assert '{% block extra_css %}' in tpl
    assert "filename='css/pages/dynatable_upload.css'" in tpl, '未接入页面级样式'


def test_css_all_colors_are_tokens():
    """页面 CSS 只承载版式；色值/圆角/间距一律 var() 引用令牌。

    下拉框的真实控件是 app.js 包出的 .custom-select__trigger（原生 select 被 .cs-native
    置 display:none），故本页把描边/圆角/焦点态收在触发器上，与 .sc-input 同口径。
    """
    css = _read(CSS)
    bare = re.findall(r'#[0-9a-fA-F]{3,8}\b', css)
    assert not bare, '页面 CSS 出现裸色值：%s' % bare
    for token in ('var(--c-ink-2)', 'var(--c-muted)', 'var(--c-line)', 'var(--r-card)',
                  'var(--r-ctrl)'):
        assert token in css, '缺令牌引用：%s' % token
    # 自定义下拉触发器必须被本页接管，否则与同列输入框圆角/描边不一致
    trig = re.search(r'\.custom-select__trigger\s*\{(.*?)\}', css, re.S)
    assert trig, '缺 .custom-select__trigger 覆盖规则'
    body = trig.group(1)
    assert 'border-radius: var(--r-ctrl)' in body, '下拉触发器须与输入框同圆角'
    assert 'border-color: var(--c-line)' in body, '下拉触发器须与输入框同描边'


def test_no_pointer_events_none():
    """第十六节红线：页面任何层级都不得关掉指针事件（禁用态也没有）。"""
    css = _read(CSS)
    assert 'pointer-events: none' not in css, \
        '页面 CSS 出现 pointer-events:none —— 会复现「按钮/表单看着在、点不动」'
    assert 'pointer-events: none' not in _read(TPL)


def test_transparent_file_input_covers_drop_zone():
    """透明 file 域 = 点击穿透的唯一实现：absolute 铺满 + opacity:0 + cursor:pointer。"""
    css = _read(CSS)
    m = re.search(r'\.dt-drop__input\s*\{(.*?)\}', css, re.S)
    assert m, '缺 .dt-drop__input 规则'
    body = m.group(1)
    assert 'position: absolute' in body, '透明 file 域必须绝对定位铺满拖拽区'
    assert 'inset: 0' in body or ('top: 0' in body and 'left: 0' in body), '须铺满四边'
    assert 'opacity: 0' in body, '原生域必须透明（外观由容器承担）'
    assert 'cursor: pointer' in body, '透明域须显式 pointer 光标'
    # 容器本身不得裁剪或拦截
    zone = re.search(r'\.dt-drop\s*\{(.*?)\}', css, re.S)
    assert zone and 'position: relative' in zone.group(1), '拖拽区须为 relative 容器'
    assert 'overflow: hidden' not in css, '容器不得裁剪透明域（否则边角点不到）'
    tpl = _read(TPL)
    assert 'class="dt-drop__input"' in tpl and 'id="f_file"' in tpl, \
        '模板须把 id=f_file 放在拖拽区内的透明域上'


def test_js_contract_ids_and_dragdrop():
    """JS 承重墙：id 集合、DataTransfer 落文件、重置清空回显。"""
    tpl = _read(TPL)
    for i in REQUIRED_IDS:
        assert 'id="%s"' % i in tpl, '改版丢失 JS 承重墙 id：%s' % i
    js = _read(JS)
    assert "document.getElementById('f_file')" in js
    assert 'DataTransfer' in js and 'drop' in js, '缺拖拽落文件实现（与文案「拖拽」一致）'
    assert 'renderFileName' in js, '缺已选文件名回显'
    assert "$('#resetBtn')" in js and 'reset()' in js, '重置须同时清空文件与回显'
    assert 'pointer-events' not in js, '交互不得靠 JS 改指针事件实现'


def test_top_aligned_label_structure():
    """设计稿口径：标签在输入框正上方（字段容器纵向排列），不再用通用横向 .sc-field。"""
    tpl = _read(TPL)
    assert 'sc-field' not in tpl, '通用横向字段布局应替换为顶对齐结构'
    assert tpl.count('class="dt-field"') >= 4
    # 每个字段块内 label 必须出现在控件之前
    for field_id in ('f_name', 'f_code', 'f_module', 'f_group', 'f_desc'):
        idx = tpl.find('id="%s"' % field_id)
        assert idx != -1
        head = tpl[:idx]
        assert head.rfind('dt-label') > head.rfind('</div>'), \
            '#%s 的 label 必须紧随同级容器开头（顶对齐）' % field_id
    css = _read(CSS)
    m = re.search(r'\.dt-field\s*\{(.*?)\}', css, re.S)
    assert m and 'flex-direction: column' in m.group(1), '字段容器须纵向排列'


def test_render_smoke(isolated_app_factory):
    """渲染层：页面 200 且透明 file 域、双列容器、底栏都在真渲染里。"""
    app = isolated_app_factory()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/dynatable/upload')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for frag in ('dt-drop__input', 'dt-grid2', 'dt-foot', 'dynatable_upload.css'):
        assert frag in html, '渲染缺：%s' % frag
