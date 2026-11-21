# -*- coding: utf-8 -*-
"""侧栏菜单单一真源（v5 扁平 IA：综合工作 + 四大板块 + 数据管理）。

纯数据模块：不 import Flask，不承载权限判定。权限过滤在 app.py
`_visible_nav_groups()` 中按 `_NAV_ITEM_GUARDS` 完成（未登记的项默认可见）。

102 重排：对齐领导「社区工作内容框架」——组名四字简称不带序号；
年度缴费（101 已融合店铺页）撤出侧栏经店铺页按钮进入；
养老保险并入社会保障页内胶囊（宿主 /medical-insurance/）。

2026-11-16 修订（主理人指示）：
- 死亡上报 从「证明开具」页胶囊组移出 → 社区治理组独立入口；
- 导入批次 从「证明开具」页胶囊组移出 → 新增「数据管理」组；
- 台账建表（/dynatable/，原完全隐藏只能手输 URL）→ 同一「数据管理」组，与导入批次并列。
  两项均为数据维护类，权限门见 app.py `_NAV_ITEM_GUARDS`。

2026-11-16 第二轮修订（综治分类重排 + 走访分出）：
- 社区治理组在「综治管理」之后新增一级入口「走访关爱」（/visit/plans/），
  承载特困人员走访 / 走访计划 / 走访记录 三枚页内胶囊，与综治管理平级；
- 综治管理组不再包含上述三项走访功能（其胶囊已迁出，见 templates 宏 visitcare 组）。
"""

NAV_GROUPS = [
    {'key': 'general', 'label': '综合工作', 'items': [
        {'endpoint': 'dashboard.index', 'label': '首页', 'icon': 'house'},
        {'endpoint': 'todo.list', 'label': '待办提醒', 'icon': 'list-todo'},
    ]},
    {'key': 'party', 'label': '党建工作', 'items': [
        {'endpoint': 'organization.list', 'label': '机构管理', 'icon': 'building-2'},
        {'endpoint': 'election.index', 'label': '选举管理', 'icon': 'check2-square'},
        {'endpoint': 'civilization.index', 'label': '文明实践站', 'icon': 'landmark'},
    ]},
    {'key': 'welfare', 'label': '民生保障', 'items': [
        # 102：医保参保管理改名「社会保障」；页内胶囊承载医保 / 养老切换
        {'endpoint': 'medical_insurance.list', 'label': '社会保障', 'icon': 'heart-pulse'},
        {'endpoint': 'dibao.dibao_list', 'label': '民政对象管理', 'icon': 'heart-handshake'},
    ]},
    {'key': 'governance', 'label': '社区治理', 'items': [
        {'endpoint': 'resident.list', 'label': '居民管理', 'icon': 'users'},
        {'endpoint': 'mediation.list', 'label': '综治管理', 'icon': 'messages-square'},
        # 2026-11-16：特困人员走访 / 走访计划 / 走访记录 从综治管理移出，另立一级入口
        # 「走访关爱」（宿主页 = 走访计划，页内三枚胶囊），与综治管理平级。
        {'endpoint': 'visit.list_page', 'label': '走访关爱', 'icon': 'heart-handshake'},
        {'endpoint': 'safety.index', 'label': '安全工作', 'icon': 'folder-open'},
        {'endpoint': 'safety.emergency', 'label': '应急管理', 'icon': 'list-checks'},
        {'endpoint': 'sanitation.index', 'label': '环卫工作', 'icon': 'wrench'},
        # 2026-11-16：死亡上报从「证明开具」胶囊组移出，按业务归属（居民生命状态变更）
        # 回到社区治理组做独立入口，不再依赖证明开具页才能进入。
        {'endpoint': 'death_report.list', 'label': '死亡上报', 'icon': 'book-open'},
    ]},
    {'key': 'public', 'label': '便民服务', 'items': [
        # 102：退伍军人管理改名「退役军人服务」（取代 093-D1 显示名沿用口径）
        {'endpoint': 'veteran.page_list', 'label': '退役军人服务', 'icon': 'shield-check'},
        {'endpoint': 'certificate.list', 'label': '证明开具', 'icon': 'scroll-text'},
        {'endpoint': 'demand.demand_page', 'label': '民生诉求', 'icon': 'inbox'},
        {'endpoint': 'notice.list_page', 'label': '补贴公示', 'icon': 'megaphone'},
    ]},
    # 2026-11-16：新增「数据管理」组——承载数据维护类入口。
    # 台账建表（原 /dynatable/ 完全无界面入口，只能手输 URL）与导入批次（102 撤出侧栏后
    # 曾仅能经证明开具页胶囊进入）在此并列，权限门见 app.py `_NAV_ITEM_GUARDS`。
    {'key': 'data', 'label': '数据管理', 'items': [
        {'endpoint': 'dynatable.index', 'label': '台账建表', 'icon': 'database'},
        {'endpoint': 'resident_enhance.import_batches_page', 'label': '导入批次', 'icon': 'database-zap'},
    ]},
]

NAV_SETTINGS = {'endpoint': 'settings.center', 'label': '设置', 'icon': 'settings'}
