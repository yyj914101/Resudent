# -*- coding: utf-8 -*-
"""utils.medical_field_catalog — 医保缴费三分类字段目录（054-B 唯一真源）。

设计（对齐 utils.certificate_catalog 的单一定义源模式）：
- INSURANCE_TYPES：三分类枚举（resident 居民 / employee 职工 / other 其他）
- COMMON_FIELDS：三 tab 通用字段（映射 MedicalInsurance 模型列）
- TYPE_FIELDS：各分类专属字段；resident 映射模型列，employee/other 存 ext_data JSON
- EXT_DATA_KEYS：需要走 ext_data 的字段 key 集合（非模型列）

口径（任务书 §约束 2）：模型/模板/JS/导入表头**均从本目录派生**，禁止硬编码字段。
外部表格结构变化 → 只改本目录 + 注册，不动代码。
"""
import json

INSURANCE_TYPES = [
    {'value': 'resident', 'label': '居民医保'},
    {'value': 'employee', 'label': '职工医保'},
    {'value': 'other', 'label': '其他'},
]
INSURANCE_TYPE_VALUES = {t['value'] for t in INSURANCE_TYPES}
DEFAULT_INSURANCE_TYPE = 'resident'
# 农保（城乡居民养老保险）标记类型：复用医保物理大表与字段口径，仅以 insurance_type 严格隔离
PENSION_INSURANCE_TYPE = 'rural_pension'
PENSION_INSURANCE_LABEL = '城乡居民养老（农保）'


YES_NO = ['是', '否']


def _f(key, label, type_='string', required=False, sensitive=None, dict_=None,
       aliases=None, options=None, default='', in_list=False):
    """字段定义。

    dict_   —— 字典分类名（走 dictStore，需库里有该分类）
    options —— 静态选项（无需字典分类，如「是/否」；优先于 dict）
    aliases —— 旧模板/外部表格的表头别名（导入时按 label 映射，别名一并认）
    """
    return {'key': key, 'label': label, 'type': type_, 'required': required,
            'sensitive': sensitive, 'dict': dict_, 'aliases': list(aliases or []),
            'options': list(options or []), 'default': default, 'in_list': in_list}


# 公共字段（映射模型列）
COMMON_FIELDS = [
    _f('seq_no', '序号', 'int'),
    _f('name', '姓名', required=True, in_list=True),
    _f('id_card', '身份证号', required=True, sensitive='id_card',
       aliases=['身份证号码'], in_list=True),
    _f('gender', '性别', options=['男', '女'], in_list=True),
    _f('contact_phone', '联系电话', sensitive='phone'),
    _f('mobile_phone', '手机号码', sensitive='phone'),
    _f('payment_year', '缴费年度', 'int', in_list=True),
    _f('data_source', '数据来源', dict_='医保数据来源', in_list=True),
    _f('link_status', '关联状态', dict_='医保关联状态', in_list=True),
    _f('verify_status', '核对状态', dict_='医保核对状态', in_list=True),
    _f('import_batch', '导入批次'),
    _f('exception_note', '异常说明'),
    _f('remark', '备注', 'text'),
]

# 居民医保专属（映射模型列，现有字段）
RESIDENT_FIELDS = [
    _f('village_group', '所在组', dict_='村组', in_list=True),
    _f('address', '住址'),
    _f('household_name', '户主姓名'),
    _f('household_property', '户属性', dict_='户属性'),
    _f('insurance_status', '参保情况', dict_='医保参保情况', in_list=True),
    _f('benefit_cert_status', '待遇认证状态', dict_='医保待遇认证状态', in_list=True),
    _f('is_paid', '是否已缴', options=YES_NO, default='否', aliases=['是否已缴纳'], in_list=True),
    _f('is_state_paid', '是否国家代缴', options=YES_NO, default='否', in_list=True),
    _f('state_paid_identity', '国家代缴身份', dict_='医保国家代缴身份'),
    _f('is_deductible', '是否可核减', options=YES_NO, default='否', in_list=True),
    _f('deduction_reason', '核减原因', dict_='医保核减原因'),
    _f('is_relocated', '是否异地搬迁', options=YES_NO, default='否'),
]

# 职工医保专属（存 ext_data JSON）
EMPLOYEE_FIELDS = [
    _f('work_unit', '工作单位', in_list=True),
    _f('insured_place', '参保地', in_list=True),
    _f('payment_base', '缴费基数', 'float', in_list=True),
    _f('personal_payment', '个人缴费金额', 'float', in_list=True),
    _f('employer_payment', '单位缴费金额', 'float', in_list=True),
    _f('employee_status', '职工医保状态', dict_='职工医保状态', in_list=True),
]

# 其他（灵活兜底，存 ext_data JSON）
OTHER_FIELDS = [
    _f('insurance_desc', '医保类型说明', 'text', in_list=True),
]

TYPE_FIELDS = {
    'resident': RESIDENT_FIELDS,
    'employee': EMPLOYEE_FIELDS,
    'other': OTHER_FIELDS,
}

# 走 ext_data 的字段 key（employee/other 专属字段，非模型列）
EXT_DATA_KEYS = frozenset(
    f['key'] for f in (EMPLOYEE_FIELDS + OTHER_FIELDS)
)

# 所有字段 key（用于导入表头/序列化兜底）
ALL_FIELD_KEYS = frozenset(
    f['key'] for f in (COMMON_FIELDS + RESIDENT_FIELDS + EMPLOYEE_FIELDS + OTHER_FIELDS)
)

# ext_data 里声明为 sensitive 的字段 → 加密类型（P2-7：动态字段不得绕过 R12）
SENSITIVE_EXT_KEYS = {
    f['key']: f['sensitive']
    for f in (EMPLOYEE_FIELDS + OTHER_FIELDS) if f.get('sensitive')
}


def fields_for(insurance_type):
    """某分类的完整字段列表（公共 + 专属），按展示顺序。"""
    return COMMON_FIELDS + TYPE_FIELDS.get(insurance_type, RESIDENT_FIELDS)


def field_map(insurance_type):
    return {f['key']: f for f in fields_for(insurance_type)}


def label_to_key_map(insurance_type):
    """表头 label → 字段 key，含旧模板别名（054 复核 P1-4：旧表头不得被静默丢弃）。"""
    out = {}
    for f in fields_for(insurance_type):
        out[f['label']] = f['key']
        for alias in f['aliases']:
            out[alias] = f['key']
    return out


def read_ext_data(ext_data):
    """ext_data JSON → dict（容错）。"""
    if not ext_data:
        return {}
    try:
        v = json.loads(ext_data)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def write_ext_data(mapping):
    """dict → ext_data JSON 字符串（空 dict 返回 None）。"""
    return json.dumps(mapping, ensure_ascii=False) if mapping else None


def pension_fields():
    """农保（城乡居民养老）字段清单：暂与居民医保完全一致。"""
    return COMMON_FIELDS + RESIDENT_FIELDS


def pension_label_to_key_map():
    """农保导入表头映射：暂与居民医保一致（含别名字典）。"""
    return label_to_key_map(DEFAULT_INSURANCE_TYPE)
