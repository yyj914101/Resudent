# -*- coding: utf-8 -*-
"""080 A-6：统一救助类别字典 + 各来源映射规则（单一真源读取）。

只定义映射规则，不做统计/导出接线（归 081）；不改主表数据。
字典取值唯一真源为 ``utils.dict_seed.DICT_SEEDS`` / 运行时 ``dict_items``，
本模块不得新增枚举字面量（映射规则中的目标 label 仅供等值绑定校验）。

接口：
    map_rescue_category(source, value=None) -> str | None   来源→统一救助类别
    rescue_values()                         -> tuple[str,...]  字典取值

不可判定返回 None（调用方保留原值，不猜）。
"""
from __future__ import annotations

RESCUE_DICT = '救助类别'

# 公示/模板来源键 → 统一救助类别。十类模板中 public_welfare（公益性岗位）
# 不在救助口径，故不列入（未列来源一律返回 None，不猜）。
NOTICE_TO_RESCUE = {
    'dibao': '低保',
    'special_support': '特困供养',
    'temp_relief': '临时救助',
    'disabled_living': '残疾',
    'elderly': '高龄',
    'housing': '廉租房',
    'nursing': '残疾',
    'planning': '计生',
    'veteran': '优抚',
}


def rescue_values():
    """字典取值 tuple（get_enum('救助类别')，无上下文回退种子）。"""
    from utils.dict_enum import get_enum
    return tuple(get_enum(RESCUE_DICT))


def map_rescue_category(source, value=None):
    """来源→统一救助类别。不可判定返回 None（调用方保留原值，不猜）。

    source: 'dibao' | 'disabled' | 'care_target' | 'notice'
    - dibao：value 等值命中「救助类别」字典 → value；否则 None
    - disabled：固定 '残疾'
    - care_target：value=='高龄' → '高龄'；其余 None（留守/困境儿童不猜归孤儿）
    - notice：NOTICE_TO_RESCUE.get(value)；public_welfare 等未列来源 → None
    """
    if source == 'dibao':
        return value if value in rescue_values() else None
    if source == 'disabled':
        return '残疾'
    if source == 'care_target':
        return '高龄' if value == '高龄' else None
    if source == 'notice':
        return NOTICE_TO_RESCUE.get(value)
    return None
