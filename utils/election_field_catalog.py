# -*- coding: utf-8 -*-
"""utils/election_field_catalog.py — 105 选民名册导入字段目录（唯一真源）。

依据：`docs/tasks/105-election-voter-batch-import.md` 交付物 1 / ADR-030 决策 4。

固定 9 项，**顺序与显示名逐字固定、不增不删**；导入模板表头、上传行→模型字段映射、
校验提示、失败行回执一律由本目录派生，禁止在模板 / JS / 路由里出现第二份字段清单。

顺序说明（与 `routes/election.py::_validate_voter_form` 的字段顺序不同）：
现有单条登记表单 `mobile_box` 在 `phone` 之前；目录以本表为准（联系电话第 7、
是否票箱第 8），行值按 **key 映射**，不受顺序影响（ADR-030 决策 4 已注明）。
"""

# 目录项：order / label（显示名，逐字固定）/ key（模型字段）/ type / required
FIELDS = (
    {'order': 1, 'label': '姓名', 'key': 'name', 'type': 'text', 'required': True},
    {'order': 2, 'label': '身份证号', 'key': 'id_card', 'type': 'text', 'required': True},
    {'order': 3, 'label': '所在组', 'key': 'group_name', 'type': 'text', 'required': False},
    {'order': 4, 'label': '登记状态', 'key': 'register_status', 'type': 'enum',
     'required': False, 'dict_category': '选民登记状态'},
    {'order': 5, 'label': '是否外出', 'key': 'is_out', 'type': 'bool', 'required': False},
    {'order': 6, 'label': '委托受托人', 'key': 'proxy_name', 'type': 'text', 'required': False},
    {'order': 7, 'label': '联系电话', 'key': 'phone', 'type': 'text', 'required': False},
    {'order': 8, 'label': '是否票箱', 'key': 'mobile_box', 'type': 'bool', 'required': False},
    {'order': 9, 'label': '备注', 'key': 'remark', 'type': 'text', 'required': False},
)

# 派生常量（唯一真源在此；其它模块只读引用）
FIELD_LABELS = tuple(item['label'] for item in FIELDS)
FIELD_KEYS = tuple(item['key'] for item in FIELDS)
LABEL_TO_KEY = {item['label']: item['key'] for item in FIELDS}
KEY_TO_LABEL = {item['key']: item['label'] for item in FIELDS}
BOOL_KEYS = tuple(item['key'] for item in FIELDS if item['type'] == 'bool')
ENUM_KEY_TO_CATEGORY = {item['key']: item['dict_category']
                        for item in FIELDS if item['type'] == 'enum'}

_BOOL_TRUE = ('1', '是', 'true', 'yes', 'y', '√', '✓', 'on')
_BOOL_FALSE = ('0', '否', 'false', 'no', 'n', '×')


def template_headers():
    """导入模板表头（9 列，按目录顺序；无序号/说明列，ADR-030 裁定 2）。"""
    return list(FIELD_LABELS)


def map_headers(headers):
    """上传表头 → {key: 列名}；返回 (mapping, missing_labels)。

    只认目录显示名（逐字），多余列忽略；缺列整批拒绝（避免按错列写库）。
    """
    normalized = [(str(h or '').strip()) for h in headers]
    mapping = {}
    missing = []
    for label in FIELD_LABELS:
        if label in normalized:
            mapping[LABEL_TO_KEY[label]] = label
        else:
            missing.append(label)
    return mapping, missing


def parse_bool(raw):
    """布尔列解析：空 → None（overwrite 时保留库中原值）；无法识别 → ValueError。"""
    s = str(raw if raw is not None else '').strip().lower()
    if s == '':
        return None
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    raise ValueError('布尔列取值须为 是/否（实际: %s）' % raw)


def cell_text(raw):
    """单元格 → 文本：整数值去 .0（Excel 数值身份证/电话常见），空 → ''。"""
    if raw is None:
        return ''
    if isinstance(raw, bool):
        return '是' if raw else '否'
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()
