# -*- coding: utf-8 -*-
"""tests/test_dynatable_upload_ui_guard.py — 台账建表「上传建表」页 UI 源码级守卫。

135 迁移注记：守卫对象由 V1 上传页（uploadForm/f_file 系列 + dynatable_upload.js）
随 135 统一 3 步向导重构迁移为 V2.5 向导页（v2Stepper/v2FileInput + dynatable_v2_upload.js），
守卫维度（零外链/令牌/pointer-events 红线/透明域铺满/顶对齐/拖拽落文件）全部保留。

137 迁移注记（补丁 B-2）：上传页落文件交互由「透明 file 域铺满拖拽区」改为
「隐藏原生 file + 显式选择按钮 #v2PickBtn」+ 拖拽区 dragover/drop 双通道；
Step3 字段容器由 .dt-field 换为 .dtv2-field（.dtv2-form-grid 两列顶对齐）。
守卫维度全部保留，仅守卫对象随架构迁移：透明域铺满 → 隐藏域+按钮触发，
顶对齐断言对象改写 v2.css 的 .dtv2-field。

背景（ui-adjustments-20261101.md 第十六节）：透明/自定义交互层极易踩
`pointer-events` 继承链，出现「看着能点、点了没反应」；SSR 快照只做文本 diff，
对 CSS 交互属性零感知。本次按 ADR-022 设计稿把原生 file 域改成「透明铺满拖拽区」，
风险点随之集中到该覆盖层，故拆成可自动回归的源码断言（运行时命中另由浏览器实测佐证：
getComputedStyle + elementFromPoint + 真实点击 + 拖拽 drop）。

守卫维度：
  1. 离线自包含：零外链，页面样式走 extra_css 本地文件；
  2. 令牌纪律：页面 CSS 零裸色值（内联 SVG 箭头用 %23 编码，不算裸值）；
  3. 指针事件：整个页面 CSS 不得出现 pointer-events: none；
  4. JS 契约：承重墙 id 不变、拖拽落文件走 dataTransfer、选文件走隐藏 input + 按钮触发；
  5. 结构契约：顶对齐标签结构（.dtv2-field 纵向排列、label 在控件之前）。
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, 'templates', 'dynatable', 'upload.html')
CSS = os.path.join(ROOT, 'static', 'css', 'pages', 'dynatable_v2.css')
JS = os.path.join(ROOT, 'static', 'js', 'pages', 'dynatable_v2_upload.js')

# 135 统一向导重构：守卫对象改为 dynatable_v2_upload.js 与新承重墙 id
# （V1 uploadForm/f_file 系列已随单轨收敛移除）
# 137 迁移：v2ParseBtn 随「选文件即解析」移除，落文件入口改为隐藏 input + #v2PickBtn
# 140 迁移（补丁 C-1）：3 步向导退役为懒人 1 屏（v2Stepper/v2Step1-3/v2PrevBtn/v2NextBtn
# 移除），新增智能分流条 v2SmartRouteBanner + 1 屏确认区 v2QuickConfirm/v2AdvancedBox/v2ResetBtn。
REQUIRED_IDS = ['v2DropZone', 'v2FileInput', 'v2PickBtn', 'v2FileName', 'v2SheetsBox',
                'v2GroupInput', 'v2PeriodInput', 'v2ModuleSelect', 'v2ModuleHint',
                'v2CombinePage', 'v2Msg', 'v2CreateBatchBtn', 'v2ResetBtn',
                'v2SmartRouteBanner', 'v2SmartRouteTitle', 'v2SmartRouteDesc',
                'v2SmartRouteLink', 'v2SmartRouteAltBtn', 'v2QuickConfirm',
                'v2QuickDiag', 'v2AdvancedBox']
RETIRED_IDS = ['v2Stepper', 'v2Step1', 'v2Step2', 'v2Step3', 'v2PrevBtn', 'v2NextBtn']


def _read(path):
    with io.open(path, encoding='utf-8') as fh:
        return fh.read()


def test_offline_selfcontained_and_local_css():
    """离线自包含：静态资源全部本地，页面级 CSS 由 extra_css 引入。"""
    tpl = _read(TPL)
    assert 'cdn.tailwindcss.com' not in tpl, '不得引 Tailwind CDN（全站零外链）'
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', tpl), '页面不得引用外链资源'
    assert '{% block extra_css %}' in tpl
    assert "filename='css/pages/dynatable_v2.css'" in tpl, '未接入 V2.5 页面级样式'


def test_css_all_colors_are_tokens():
    """页面 CSS 只承载版式；色值/圆角/间距一律 var() 引用令牌。

    下拉框的真实控件是 app.js 包出的 .custom-select__trigger（原生 select 被 .cs-native
    置 display:none），故本页把描边/圆角/焦点态收在触发器上，与 .sc-input 同口径。
    """
    css = _read(CSS)
    # 136 收口：v2.css 全文件零裸色值（var(--x, #fallback) 形态不算——fallback 供
    # 令牌缺失兜底，仍是合法形态）。135 迁移时的 dt-drop 块收窄断言随收口升级。
    bare = [h for h in re.findall(r'#[0-9a-fA-F]{3,8}\b', css)
            if not re.search(r'var\([^)]*%s[^)]*\)' % re.escape(h), css)]
    assert not bare, 'v2.css 出现裸色值（var fallback 之外）：%s' % bare


def test_no_pointer_events_none():
    """第十六节红线：页面任何层级都不得关掉指针事件（禁用态也没有）。"""
    css = _read(CSS)
    assert 'pointer-events: none' not in css, \
        '页面 CSS 出现 pointer-events:none —— 会复现「按钮/表单看着在、点不动」'
    assert 'pointer-events: none' not in _read(TPL)
    assert 'pointer-events: none' not in _read(JS)


def test_hidden_file_input_with_pick_button():
    """137 落文件入口：原生 file 域 hidden，由 #v2PickBtn 显式触发，拖拽区保留双通道。

    透明覆盖层方案（135 #14）随 B-2 改版退役——改为隐藏原生 input + 可见按钮，
    故不再要求 .dt-drop__input 绝对定位规则，改用「入口可达 + 拖拽态样式」双断言。
    """
    tpl = _read(TPL)
    m = re.search(r'<input[^>]*id="v2FileInput"[^>]*>', tpl)
    assert m, '缺 #v2FileInput'
    assert 'hidden' in m.group(0), '原生 file 域须隐藏（外观由拖拽区 + 按钮承担）'
    assert 'id="v2PickBtn"' in tpl, '缺显式选文件按钮 #v2PickBtn'
    css = _read(CSS)
    zone = re.search(r'\.dtv2-dropzone\s*\{(.*?)\}', css, re.S)
    assert zone, '缺 .dtv2-dropzone 规则'
    assert '.dtv2-dropzone.is-dragover' in css, '拖拽悬停态样式缺失（拖拽通道不可见）'
    js = _read(JS)
    assert "pickBtn.addEventListener('click'" in js and 'fileInput.click()' in js, \
        '按钮须显式触发隐藏 file 域（否则「选择本地文件」点了没反应）'
    assert 'overflow: hidden' not in zone.group(1), '拖拽区不得裁剪内容'


def test_js_contract_ids_and_dragdrop():
    """JS 承重墙：id 集合、dataTransfer 落文件、重置清空回显。"""
    tpl = _read(TPL)
    for i in REQUIRED_IDS:
        assert 'id="%s"' % i in tpl, '改版丢失 JS 承重墙 id：%s' % i
    for i in RETIRED_IDS:
        assert 'id="%s"' % i not in tpl, 'C-1 退役 id 不得残留：%s' % i
    js = _read(JS)
    assert "getElementById('v2FileInput')" in js
    assert 'dataTransfer' in js and "'drop'" in js, '缺拖拽落文件实现（与文案「拖拽」一致）'
    assert 'v2FileName' in js, '缺已选文件名回显'
    assert 'pointer-events' not in js, '交互不得靠 JS 改指针事件实现'


def test_top_aligned_label_structure():
    """设计稿口径：标签在输入框正上方（.dtv2-field 纵向排列 + .dtv2-form-grid 两列）。"""
    tpl = _read(TPL)
    assert tpl.count('class="dtv2-field"') >= 3, 'Step3 须用 .dtv2-field 字段容器'
    assert 'dtv2-form-grid' in tpl, 'Step3 须用两列顶对齐网格'
    # 每个字段块内 label 必须出现在控件之前（顶对齐；向导 Step3 字段）
    for field_id in ('v2GroupInput', 'v2PeriodInput', 'v2ModuleSelect'):
        idx = tpl.find('id="%s"' % field_id)
        assert idx != -1
        head = tpl[:idx]
        assert head.rfind('dtv2-field__label') > head.rfind('</div>'), \
            '#%s 的 label 必须紧随同级容器开头（顶对齐）' % field_id
    # 137：纵向布局由页面自有 v2.css 承担（旧 dynatable_upload.css 已脱离本页）
    css = _read(CSS)
    m = re.search(r'\.dtv2-field\s*\{(.*?)\}', css, re.S)
    assert m and 'flex-direction: column' in m.group(1), \
        '字段容器须纵向排列（v2.css .dtv2-field）'


def test_render_smoke(isolated_app_factory):
    """渲染层：页面 200 且落文件入口、两列容器、底栏都在真渲染里。"""
    app = isolated_app_factory()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/dynatable/upload')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="v2DropZone"' in html
    assert 'id="v2FileInput"' in html, '隐藏 file 域在真渲染中'
    assert 'id="v2PickBtn"' in html, '显式选文件按钮在真渲染中'
    assert 'id="v2ModuleSelect"' in html
    for frag in ('dtv2-form-grid', 'dtv2-field__label', 'v2CombinePage', 'v2CreateBatchBtn',
                 'v2SmartRouteBanner', 'v2QuickConfirm', 'v2AdvancedBox', 'v2ResetBtn'):
        assert frag in html, '渲染缺：%s' % frag
