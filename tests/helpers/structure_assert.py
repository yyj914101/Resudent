# -*- coding: utf-8 -*-
"""tests/helpers/structure_assert.py — SSR 输出结构断言共享基建（061 S1 抽取）。

来源：tests/test_resident_add_structure.py 的 _StructureParser（035 门禁补强，034 验收
R-034-01）。背景：SSR 快照是文本 diff，对标签非法嵌套零感知——游离 </div> 会让解析期
form 被隐式提前闭合、tab-pane 被弹出 .tab-content，快照与 md5 全程无感。本解析器以
HTMLParser 模拟浏览器「闭合标签弹出至最近同名开标签」的隐式闭合语义，暴露结构真相：

  - stray_div_close      游离 </div>（栈内已无 div 可弹）——浏览器会向外层弹 div，
                         造成结构错位；末尾等量合法闭合沦为游离 → 两处游离必成对出现
  - implicit_form_close  form 被其他闭合连带弹出（隐式提前闭合）
  - stack                解析结束仍开着的标签（未闭合元素）
  - elements             {id: ElementInfo}——所有带 id 的元素（void 标签只记录不进栈）
                         （tag / class / 最近 div 祖先的 class / 是否处于 form 内）

断言函数（多页复用的公共检查，061 五页结构断言消费）：
  - assert_balanced(r)    深度账平衡 + form 完整（游离/未闭合/隐式闭合三项全查）
  - assert_tabs(r, ids)   Bootstrap tab-pane：存在、是 .tab-content 直接子节点
  - assert_modals(r, ids) 弹窗存在且带 modal class
  - panes(r) / modals(r)  按 class 过滤的 {id: ElementInfo} 视图
"""
import re
from html.parser import HTMLParser

_VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
              'link', 'meta', 'param', 'source', 'track', 'wbr'}


class ElementInfo:
    __slots__ = ('tag', 'classes', 'parent_classes', 'in_form')

    def __init__(self, tag, classes, parent_classes, in_form):
        self.tag = tag
        self.classes = classes or ''
        self.parent_classes = parent_classes or ''
        self.in_form = in_form


class StructureParser(HTMLParser):
    """模拟浏览器隐式闭合：闭合标签弹出至最近同名开标签并连带闭合其上节点。

    - 游离 </div>（栈内已无 div 可弹）：记入 stray_div_close。
    - form 被其他闭合连带弹出：记入 implicit_form_close。
    - 游离 </form>：HTML 规范规定忽略，不计不平衡。
    - 带 id 元素只在首次出现时记录（重复 id 本身属页面错误，由快照/人工把关）。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []               # [(tag, attrs_dict)]
        self.stray_div_close = 0      # 游离 </div> 计数
        self.implicit_form_close = 0  # form 隐式提前闭合计数
        self.elements = {}            # id -> ElementInfo

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        eid = a.get('id')
        if eid and eid not in self.elements:
            parent = next(
                ((t, aa) for t, aa in reversed(self.stack) if t == 'div'),
                (None, None))
            self.elements[eid] = ElementInfo(
                tag, a.get('class'), (parent[1] or {}).get('class'),
                any(t == 'form' for t, _ in self.stack))
        if tag in _VOID_TAGS:
            return  # void 元素自闭合：只记录不进栈
        self.stack.append((tag, a))

    def handle_endtag(self, tag):
        if tag in _VOID_TAGS:
            return
        matched = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                matched = i
                break
        if matched is None:
            if tag == 'div':
                self.stray_div_close += 1
            return
        popped = self.stack[matched:]
        del self.stack[matched:]
        if tag != 'form':
            for t, _a in popped[1:]:
                if t == 'form':
                    self.implicit_form_close += 1


def analyze(html):
    """feed + close 一把跑完，返回 StructureParser 实例。"""
    p = StructureParser()
    p.feed(html)
    p.close()
    return p


def panes(report):
    """所有 tab-pane 元素：{id: ElementInfo}。"""
    return {eid: info for eid, info in report.elements.items()
            if 'tab-pane' in info.classes}


def modals(report):
    """所有 modal 元素：{id: ElementInfo}。"""
    return {eid: info for eid, info in report.elements.items()
            if 'modal' in info.classes}


def assert_balanced(report):
    """深度账平衡 + form 完整（游离 </div> / 未闭合 <div> / form 隐式提前闭合）。"""
    assert report.stray_div_close == 0, \
        f'存在 {report.stray_div_close} 个游离 </div>（深度账目不平，结构必错位）'
    leftover = [t for t, _ in report.stack if t == 'div']
    assert not leftover, f'存在 {len(leftover)} 个未闭合 <div>'
    assert report.implicit_form_close == 0, 'form 被隐式提前闭合（游离闭合连带弹出）'


def assert_tabs(report, pane_ids):
    """每个 pane 必须存在且是 .tab-content 的直接子节点。"""
    found = panes(report)
    for pid in pane_ids:
        info = found.get(pid)
        assert info is not None, f'#{pid} tab-pane 未渲染'
        assert 'tab-content' in info.parent_classes, \
            (f'#{pid} 必须是 .tab-content 的直接子节点'
             f'（实际父节点 class：{info.parent_classes!r}）')


def assert_modals(report, modal_ids):
    """每个 modal 必须存在且带 modal class。"""
    found = modals(report)
    for mid in modal_ids:
        assert mid in found, f'弹窗 #{mid} 未渲染'


_SEL_RE = re.compile(r"\$\s*\(\s*(['\"])(.+?)\1", re.S)
_ON_RE = re.compile(r"\.on\s*\(\s*(['\"])(.+?)\1\s*,\s*(['\"])(.+?)\3", re.S)
_ID_RE = re.compile(r"#([A-Za-z_][A-Za-z0-9_-]*)")


def extract_js_ids(path):
    """从页面 JS 的 jQuery 选择器提取承重墙 DOM id（单一真源）。

    覆盖 `$('#id ...')` 与 `$(document).on('evt', '#id ...', ...)` 两种写法；
    同一 selector 串里的多个 #id（如 `#a, #b`）全部计入。
    """
    src = open(path, encoding='utf-8').read()
    found = set()
    for m in _SEL_RE.finditer(src):
        found.update(_ID_RE.findall(m.group(2)))
    for m in _ON_RE.finditer(src):
        found.update(_ID_RE.findall(m.group(4)))
    return found
