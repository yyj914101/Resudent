# -*- coding: utf-8 -*-
"""tests/test_128_multi_phone_plan_a.py — 方案 A：单格多电话规范化、逐号脱敏与主号哈希检索回归测试。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.phone_utils import normalize_phones, primary_phone, split_phones
from utils.masking import mask_phone


def test_split_and_normalize_multi_phone():
    raw = "19150597883\n19160571883\n15378386091\n15378386205\n15351268973"
    parts = split_phones(raw)
    assert len(parts) == 5
    assert primary_phone(raw) == "19150597883"
    norm = normalize_phones(raw)
    assert norm == "19150597883 / 19160571883 / 15378386091 / 15378386205 / 15351268973"
    assert "\n" not in norm

    # 重复号码自动去重 + 兼容中文顿号/逗号/斜杠
    assert normalize_phones("13800138000、13900139000，13800138000 / 0773-7228309") == (
        "13800138000 / 13900139000 / 0773-7228309"
    )
    # 单号保持原样
    assert normalize_phones("13800138000") == "13800138000"
    assert normalize_phones("") == ""


def test_mask_phone_per_number():
    # 单号行为与历史完全一致
    assert mask_phone("13800138000") == "138****8000"
    assert mask_phone("") == ""
    assert mask_phone(None) is None

    # 多号逐个脱敏，不再把中间号码吞成一长串星号
    raw = "19150597883\n19160571883\n15378386091"
    masked = mask_phone(raw)
    assert masked == "191****7883 / 191****1883 / 153****6091"
    assert "19150597883" not in masked
    assert "19160571883" not in masked
    assert "15378386091" not in masked
