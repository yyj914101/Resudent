# -*- coding: utf-8 -*-
"""utils.dynatable_modules — 软表「菜单归属」模块键 ↔ 中文标签的单一真源（2026-11-16）。

背景：`dynamic_table.module_key` 是持久化值 + 后端校验白名单（routes/dynatable.py 建表时
按它校验），**键名不得翻译、不得改名**，否则历史台账归属失效、门面层
（utils/roster_facade.get_table(module_key, code)）失效。

但一线使用人员不读英文，界面一律经 `label_for()` 展示中文：键 = 值，中文 = 展示。
新增业务模块时只改本文件的 `MODULE_LABELS`，页面/接口/名册页自动跟随。

口径：中文名与侧栏/页内菜单的可见名称一致（用户按菜单名认领台账归属），
`dynatable` 表示「不挂任何业务页的独立台账」，故用「自定义台账」而非英文原值。
"""

# 键顺序即「菜单归属」下拉顺序（沿用 078 上线时的顺序，不随新增模块打乱既有位置）
MODULE_LABELS = {
    'pension': '养老保险',        # 社会保障 → 养老保险（/pension/）
    'shop': '店铺信息',           # 居民管理 → 店铺信息（/shop/）
    'safety': '安全工作',         # 社区治理 → 安全工作（/safety/）
    'governance': '综治管理',     # 社区治理 → 综治管理（/mediation/）
    'sanitation': '环卫工作',     # 社区治理 → 环卫工作（/sanitation/）
    'civilization': '文明实践站',  # 党建工作 → 文明实践站（/civilization/）
    'dynatable': '自定义台账',     # 不挂业务页，仅在「台账建表」中维护
}

# 合法的归属键（建表校验白名单；顺序与下拉一致）
MODULE_KEYS = tuple(MODULE_LABELS)


def label_for(module_key):
    """键 → 中文标签；空值给占位符，未知键原样回显（不静默吞掉异常数据）。"""
    key = (module_key or '').strip()
    if not key:
        return '未指定'
    return MODULE_LABELS.get(key, key)


def module_options():
    """下拉选项：[{'value': 键, 'label': 中文}]。值必须是键，提交给后端仍是英文键。"""
    return [{'value': key, 'label': label} for key, label in MODULE_LABELS.items()]
