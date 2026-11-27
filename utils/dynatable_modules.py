# -*- coding: utf-8 -*-
"""utils.dynatable_modules — 软表「菜单归属」模块键 ↔ 中文标签与页内胶囊挂载真源（2026-11-16）。

背景：`dynamic_table.module_key` 是持久化值 + 后端校验白名单（routes/dynatable.py 建表时
按它校验），**键名不得翻译、不得改名**，否则历史台账归属失效、门面层
（utils/roster_facade.get_table(module_key, code)）失效。

但一线使用人员不读英文，界面一律经 `label_for()` 展示中文：键 = 值，中文 = 展示。
新增业务模块时只改本文件的 `MODULE_LABELS` 与 `TAB_GROUP_CONFIG`，页面/接口/名册页自动跟随。

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
    'organization': '机构管理',   # 党建/组织架构（/organization/）
    'civil': '民政对象管理',      # 民生保障 → 民政对象管理（/dibao/ 等）
    'dynatable': '自定义台账',     # 不挂业务页，仅在「台账建表」中维护
}

# 合法的归属键（建表校验白名单；顺序与下拉一致）
MODULE_KEYS = tuple(MODULE_LABELS)

# 页内胶囊组（module_tabs 的 module_key） ↔ 软表 module_key 挂载映射真源：
# - exclude_codes：m031 预置且已有固定入口的内置台账，不重复追加为动态胶囊；
# - row_label：多行带标签栅格组（governance）追加新行时的行标签，单行组为 None；
# - overview：原无胶囊条的独立模块页，存在动态台账时首枚自动补「概览」返回胶囊。
# 注：safety 由第二批安全工作目录树承载（folder + file + table），此处不追加页内胶囊。
TAB_GROUP_CONFIG = {
    'social': {
        'module_key': 'pension',
        'endpoint': 'pension.roster_page',
        'exclude_codes': frozenset({'pension_roster'}),
        'row_label': None,
        'overview': None,
    },
    'resident': {
        'module_key': 'shop',
        'endpoint': 'shop.roster_page',
        'exclude_codes': frozenset({'shop_facility', 'shop_annual_fee'}),
        'row_label': None,
        'overview': None,
    },
    'governance': {
        'module_key': 'governance',
        'endpoint': 'governance.roster_page',
        'exclude_codes': frozenset({
            'gov_special_visit', 'gov_stability_risk', 'gov_mental_health',
            'gov_security_committee', 'gov_criminal', 'gov_road_hazard',
            'gov_key_vehicle', 'gov_guide_station',
        }),
        'row_label': '扩展台账',
        'overview': None,
    },
    'sanitation': {
        'module_key': 'sanitation',
        'endpoint': 'sanitation.roster_page',
        'exclude_codes': frozenset({'sanitation_team', 'sanitation_site', 'sanitation_cleanup'}),
        'row_label': None,
        'overview': {
            'key': 'index',
            'label': '环卫概览',
            'title': '环卫工作概览',
            'endpoint': 'sanitation.index',
        },
    },
    'org': {
        'module_key': 'organization',
        'endpoint': 'dynatable.list_page',
        'exclude_codes': frozenset(),
        'row_label': None,
        'overview': None,
    },
    'civil': {
        'module_key': 'civil',
        'endpoint': 'dynatable.list_page',
        'exclude_codes': frozenset(),
        'row_label': '扩展台账',
        'overview': None,
    },
    'civilization': {
        'module_key': 'civilization',
        'endpoint': 'civilization.roster_page',
        'exclude_codes': frozenset({'civilization_activity'}),
        'row_label': None,
        'overview': {
            'key': 'index',
            'label': '实践站概览',
            'title': '文明实践站概览',
            'endpoint': 'civilization.index',
        },
    },
}

_MODULE_TO_TAB_GROUP = {cfg['module_key']: tg for tg, cfg in TAB_GROUP_CONFIG.items()}


def tab_group_for(module_key):
    """软表 module_key → 对应的页内胶囊组名（无挂载映射则返回 None）。"""
    return _MODULE_TO_TAB_GROUP.get((module_key or '').strip())


def is_builtin_roster_code(module_key, code):
    """判断 code 是否属于该模块已具备固定入口的内置预置台账。"""
    tg = tab_group_for(module_key)
    if not tg:
        return False
    return (code or '').strip() in TAB_GROUP_CONFIG[tg]['exclude_codes']


def label_for(module_key):
    """键 → 中文标签；空值给占位符，未知键原样回显（不静默吞掉异常数据）。"""
    key = (module_key or '').strip()
    if not key:
        return '未指定'
    return MODULE_LABELS.get(key, key)


def module_options():
    """下拉选项：[{'value': 键, 'label': 中文}]。值必须是键，提交给后端仍是英文键。"""
    return [{'value': key, 'label': label} for key, label in MODULE_LABELS.items()]
