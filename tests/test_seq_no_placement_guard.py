# -*- coding: utf-8 -*-
"""序号胶囊归属守卫（2026-11-11）。

主理人调整：居民医保编辑页的「序号: #N」胶囊移除（该功能无需序号），
同一展示迁到【民政对象管理·低保】详情弹窗，用作每份档案存档时的独立编号
（Dibao.id —— 入库分配的主键，唯一且不随列表排序变化）。

硬约束：本次调整不得外溢到其它页面/模块，且医保缴费编辑的提交载荷必须保持
seq_no（隐藏 input）不变，否则后端 /save 会把原有 seq_no 覆盖成空。
本文件用源码级断言钉住这四点，回退或越界即红。
"""
import io
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, 'static', 'js', 'pages')
MEDICAL_JS = os.path.join(JS_DIR, 'medical_insurance.js')
DIBAO_JS = os.path.join(JS_DIR, 'dibao_list.js')


def _read(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read()


def _func_body(src, signature):
    """取 `function name(...)` 起、花括号平衡到 `}` 的函数体。"""
    i = src.index(signature)
    j = src.index('{', i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    raise AssertionError('%s 函数体未闭合' % signature)


def test_medical_insurance_edit_has_no_seq_pill():
    body = _func_body(_read(MEDICAL_JS), 'function mi2Header(')
    assert '序号: #' not in body, '居民医保编辑页不应再渲染序号胶囊'
    assert "#formPills').html(pills.join(''))" in body, '其余身份胶囊（姓名/类型/年度）须保留'


def test_medical_insurance_hidden_seq_payload_kept():
    src = _read(MEDICAL_JS)
    assert "'seq_no'" in src and 'MI2_META_KEYS' in src, (
        'seq_no 隐藏载荷必须保留，否则缴费编辑保存会把序号覆盖为空（原有流程回归）')


def test_dibao_detail_shows_archive_seq_from_id():
    body = _func_body(_read(DIBAO_JS), 'function openDetail(')
    assert "'序号: #' + d.id" in body, (
        '低保详情须以 Dibao.id 作为档案存档独立编号，不得改用列表行号或新增 DDL 字段')


def test_seq_pill_not_leaked_to_other_modules():
    leaked = []
    for name in os.listdir(JS_DIR):
        if not name.endswith('.js') or name == 'dibao_list.js':
            continue
        if '序号: #' in _read(os.path.join(JS_DIR, name)):
            leaked.append(name)
    assert not leaked, '序号胶囊只允许出现在低保模块，越界文件：%s' % leaked
