# -*- coding: utf-8 -*-
"""治理台账分类配置真源（P1-1：消除 utils.metrics -> routes.governance 反向依赖）。"""

def _field(key, label, kind='text', required=False, options=None, wide=False,
           option_category=None):
    field = {'key': key, 'label': label, 'kind': kind, 'required': required,
             'options': options or [], 'wide': wide}
    # 094：字典下拉来源（渲染期由 get_enum 注入 options；代码不写枚举字面量）。
    # 旧 4 类不传此参数 → 序列化结果与改造前逐字一致。
    if option_category:
        field['option_category'] = option_category
    return field


CATEGORY_CONFIG = {
    'special-visit': {
        'title': '特困人员走访记录', 'icon': 'bi-house-heart',
        'description': '记录特困人员走访时间、包保人及走访情况。',
        # 2026-11-16：特困人员走访迁入「走访关爱」独立入口 → 胶囊组换成 visitcare
        'tab_module': 'visitcare',
        'fields': [
            _field('record_date', '走访日期', 'date', True),
            _field('name', '姓名', required=True),
            _field('id_card', '身份证号'),
            _field('village_group', '所在组'),
            _field('phone', '联系电话'),
            _field('responsible_person', '包保人'),
            _field('visit_situation', '走访情况', 'textarea', wide=True),
        ],
        'columns': ['record_date', 'name', 'id_card', 'village_group', 'phone',
                    'responsible_person', 'visit_situation'],
    },
    'stability-risk': {
        'title': '社会稳定风险排查化解记录', 'icon': 'bi-exclamation-diamond',
        'description': '对社会稳定风险事项进行排查、研判、化解和留痕管理。',
        'fields': [
            _field('record_date', '排查日期', 'date', True),
            _field('name', '当事人姓名', required=True),
            _field('id_card', '身份证号'),
            _field('village_group', '所在组'),
            _field('work_unit', '工作单位或住址'),
            _field('phone', '联系方式'),
            _field('risk_category', '风险类别'),
            _field('basic_situation', '基本情况', 'textarea', wide=True),
            _field('work_measures', '工作措施', 'textarea', wide=True),
            _field('remark', '备注', 'textarea', wide=True),
        ],
        'columns': ['record_date', 'name', 'village_group', 'phone', 'risk_category',
                    'basic_situation', 'work_measures'],
    },
    'mental-health': {
        'title': '严重精神障碍患者台账', 'icon': 'bi-clipboard2-pulse',
        'description': '记录严重精神障碍患者包保、走访及随访情况。',
        'fields': [
            _field('record_date', '随访日期', 'date', True),
            _field('name', '姓名', required=True),
            _field('id_card', '身份证号'),
            _field('village_group', '所在组'),
            _field('phone', '联系电话'),
            _field('responsible_person', '包保人'),
            _field('visit_situation', '走访/随访情况', 'textarea', wide=True),
            _field('remark', '备注', 'textarea', wide=True),
        ],
        'columns': ['record_date', 'name', 'id_card', 'village_group', 'phone',
                    'responsible_person', 'visit_situation'],
    },
    'security-committee': {
        'title': '治保委员会花名册', 'icon': 'bi-people-fill',
        'description': '管理治保委员会成员、职务及联系方式。',
        'fields': [
            _field('name', '姓名', required=True),
            _field('position', '职务', required=True),
            _field('id_card', '身份证号'),
            _field('phone', '联系电话'),
            _field('address', '居住地址'),
            _field('remark', '备注', 'textarea', wide=True),
        ],
        'columns': ['name', 'position', 'id_card', 'phone', 'address', 'remark'],
    },
    # ── 2026-11-16：刑事人员（原「刑满释放帮教」扩为两类人员，payload 零 DDL）──
    # 服刑人员 / 刑满释放人员同表存放，由「人员状态」字段（字典「刑事人员状态」）区分；
    # 页面工具栏另提供同名字段筛选（config['filters']），列表与导出都按筛选值过滤。
    'criminal': {
        'title': '刑事人员台账', 'icon': 'bi-person-lines-fill',
        'description': '统一管理服刑人员与刑满释放人员：基本信息、走访帮扶及动态管控。',
        'filters': [
            {'key': 'personnel_status', 'label': '人员状态', 'option_category': '刑事人员状态'},
        ],
        'fields': [
            _field('personnel_status', '人员状态', 'select', True,
                   option_category='刑事人员状态'),
            _field('name', '姓名', required=True),
            _field('id_card', '身份证号'),
            _field('village_group', '所在组'),
            _field('phone', '联系电话'),
            _field('address', '现居住地址', wide=True),
            _field('record_date', '走访日期', 'date', True),
            _field('responsible_person', '包保/帮教责任人'),
            _field('visit_situation', '走访情况', 'textarea', wide=True),
            _field('help_measures', '帮扶措施', 'textarea', wide=True),
            _field('release_date', '刑满释放日期', 'date'),
            _field('help_type', '帮教类型', 'select', option_category='帮教类型'),
            _field('original_sentence', '原判刑期'),
            _field('control_status', '管控状态', 'select', option_category='帮教管控状态'),
            _field('control_measures', '动态管控措施', 'textarea', wide=True),
            _field('remark', '备注', 'textarea', wide=True),
        ],
        'columns': ['record_date', 'name', 'personnel_status', 'village_group',
                    'release_date', 'help_type', 'control_status', 'responsible_person'],
    },
    # ── 086 平安扩展：道交三类（字段全落 payload，零新端点、零新物理表）──
    'road-hazard': {
        'title': '道路交通安全隐患台账', 'icon': 'bi-signpost-split',
        'description': '记录道路安全隐患排查、整改措施及整改验收情况。',
        'fields': [
            _field('record_date', '登记日期', 'date', True),
            _field('road_name', '路段名称', required=True),
            _field('location', '起止位置'),
            _field('hazard_type', '隐患类型', 'select', True,
                   option_category='道路隐患类型'),
            _field('hazard_level', '隐患等级', 'select', option_category='隐患等级'),
            _field('found_date', '发现日期', 'date', True),
            _field('measure', '整改措施', 'textarea', wide=True),
            _field('fixed_date', '整改完成日期', 'date'),
            _field('fix_status', '整改状态', 'select', True, option_category='整改状态'),
            _field('owner', '责任人'),
            _field('owner_phone', '联系电话'),
            _field('remark', '备注', 'textarea', wide=True),
        ],
        'columns': ['record_date', 'road_name', 'location', 'hazard_type',
                    'hazard_level', 'fix_status', 'owner'],
    },
    'key-vehicle': {
        'title': '重点车辆驾驶人名册', 'icon': 'bi-truck',
        'description': '登记重点车辆及驾驶人基础信息、证照和在册状态。',
        'fields': [
            _field('record_date', '登记日期', 'date', True),
            _field('record_type', '登记类型', 'select', True,
                   option_category='道交登记类型'),
            _field('name', '所有人或驾驶人', required=True),
            _field('plate_no', '号牌号码'),
            _field('vehicle_type', '车辆类型', 'select', option_category='车辆类型'),
            _field('phone', '联系电话'),
            _field('license_status', '证照情况', 'select', option_category='证照情况'),
            _field('register_date', '初次登记日期', 'date'),
            _field('record_status', '在册状态', 'select', option_category='在册状态'),
            _field('remark', '备注', 'textarea', wide=True),
        ],
        'columns': ['record_date', 'record_type', 'name', 'plate_no', 'vehicle_type',
                    'license_status', 'record_status'],
    },
    'guide-station': {
        'title': '交通安全劝导站', 'icon': 'bi-cone-striped',
        'description': '管理交通安全劝导站点位置、劝导员及运行状态。',
        'fields': [
            _field('record_date', '登记日期', 'date', True),
            _field('station_name', '站点名称', required=True),
            _field('location', '位置'),
            _field('staff_name', '劝导员'),
            _field('staff_phone', '联系电话'),
            _field('setup_date', '设立日期', 'date'),
            _field('run_status', '运行状态', 'select', option_category='运行状态'),
            _field('remark', '备注', 'textarea', wide=True),
        ],
        'columns': ['record_date', 'station_name', 'location', 'staff_name', 'run_status'],
    },
}
