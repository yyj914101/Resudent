# -*- coding: utf-8 -*-
"""tests/test_dynatable_module_labels.py — 软表「菜单归属」中文化守卫（2026-11-16）。

背景：`module_key` 是持久化值 + 建表校验白名单，键名必须保持英文；但一线使用人员读不懂
英文，故界面一律经 utils/dynatable_modules.label_for() 展示中文。本文件钉住四件事：
  1) 映射完整性：每个合法键都有中文标签，标签非纯 ASCII（即确实中文化）；
  2) 兜底口径：空值给「未指定」、未知键原样回显（不吞数据）；
  3) 上传页：<option> 的 value 仍是英文键、显示文本是中文（提交链路不变）；
  4) 列表接口：每条记录同时下发 module_key（原值）与 module_label（中文），
     前端列表列才能在中文化的同时保留排障用的原值。
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL_UPLOAD = os.path.join(ROOT, 'templates', 'dynatable', 'upload.html')
JS_INDEX = os.path.join(ROOT, 'static', 'js', 'pages', 'dynatable_index.js')

from utils.dynatable_modules import MODULE_KEYS, MODULE_LABELS, label_for, module_options


def _read(path):
    with io.open(path, encoding='utf-8') as fh:
        return fh.read()


def test_every_key_has_chinese_label():
    """映射完整性：键集合与标签集合一一对应，标签必须是中文（含非 ASCII 字符）。"""
    assert set(MODULE_KEYS) == set(MODULE_LABELS)
    assert len(MODULE_KEYS) == len(set(MODULE_KEYS)), '键重复'
    for key in MODULE_KEYS:
        label = MODULE_LABELS[key]
        assert label and not label.isascii(), '归属键 %s 缺中文标签（当前：%r）' % (key, label)
        assert len(label) <= 8, '标签应简洁（≤8 字）：%s → %s' % (key, label)
    # 键本身保持英文（持久化值不得翻译）
    assert all(k.isascii() for k in MODULE_KEYS), '键名不得改为中文（会破坏历史数据归属）'


def test_label_fallbacks():
    """空值/未知键不得抛错：空 → 未指定；未知 → 原样回显（不静默吞数据）。"""
    assert label_for('') == '未指定'
    assert label_for(None) == '未指定'
    assert label_for('pension') == MODULE_LABELS['pension']
    assert label_for('legacy_module') == 'legacy_module'
    assert label_for('pension ') == MODULE_LABELS['pension'], '首尾空格应被忽略'


def test_module_options_value_is_key_label_is_chinese():
    """下拉选项：value=英文键（提交给后端），label=中文（界面显示）。"""
    opts = module_options()
    assert [o['value'] for o in opts] == list(MODULE_KEYS), '选项顺序应与白名单一致'
    for o in opts:
        assert o['value'].isascii()
        assert not o['label'].isascii()


def test_upload_template_renders_label_from_mapping():
    """模板不得再直出英文键：选项文本取自 module_options，且不再引用旧的 module_keys。"""
    tpl = _read(TPL_UPLOAD)
    assert 'module_options' in tpl, '上传页须按映射渲染中文选项'
    assert 'module_keys' not in tpl, '旧变量 module_keys 已退役（会直出英文键）'
    assert "value=\"{{ opt['value'] }}\"" in tpl, '提交值必须仍是英文键'
    assert "{{ opt['label'] }}" in tpl, '显示文本必须是中文标签'


def test_index_js_prefers_label():
    """列表「菜单归属」列优先用中文标签，缺失时回落原键（老接口/烟测数据兼容）。"""
    js = _read(JS_INDEX)
    assert 'module_label || t.module_key' in js, '列表列须优先中文标签'


def test_upload_page_and_api_localized(isolated_app_factory):
    """渲染级：上传页选项中文且值英文；/api/list 每行都带 module_label。"""
    app = isolated_app_factory()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1

    html = client.get('/dynatable/upload').get_data(as_text=True)
    select = re.search(r'<select[^>]*id="f_module"[^>]*>(.*?)</select>', html, re.S).group(1)
    values = re.findall(r'<option value="([^"]+)"', select)
    texts = re.findall(r'<option value="[^"]+">([^<]+)</option>', select)
    assert values == list(MODULE_KEYS), '值必须是英文键且顺序一致'
    for key, text in zip(values, texts):
        assert text.strip() == MODULE_LABELS[key], '选项 %s 显示 %r，应为 %r' % (
            key, text.strip(), MODULE_LABELS[key])

    # 建一行台账后查接口：中文标签随行下发
    with app.app_context():
        from models import DynamicTable, db
        db.session.add(DynamicTable(code='label_probe', name='归属中文探针',
                                    module_key='sanitation', group_name='民生服务'))
        db.session.commit()
    body = client.get('/dynatable/api/list').get_json()
    row = next(x for x in body['data'] if x['code'] == 'label_probe')
    assert row['module_key'] == 'sanitation', '原键必须保留（排障/编辑用）'
    assert row['module_label'] == MODULE_LABELS['sanitation'], '未下发中文标签'
