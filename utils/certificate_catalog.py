"""utils.certificate_catalog — 证明开具目录与系统模板种子（042 单一定义源）。

接口（供 routes.certificate / scripts.migrations / app.py 共用）：
- FIELD_CATALOG / SYSTEM_PLACEHOLDERS / DEFAULT_PAGE_SETTINGS：模板编辑器与开具表单的字段目录
- DEFAULT_TEMPLATE_DEFINITIONS：3 个系统模板（迁移种子与启动补种同源）
- ensure_certificate_seed()：一次性幂等补种（只 SELECT/INSERT/UPDATE，不建表/不加列）
"""
import html
import json

from sqlalchemy import text

from models import CertificateTemplate, db

DEFAULT_PAGE_SETTINGS = {
    'orientation': 'portrait', 'margin_top': 37, 'margin_right': 26,
    'margin_bottom': 35, 'margin_left': 28,
    'font_name': '仿宋_GB2312', 'font_size': 16, 'line_height': 1.75,
}

SYSTEM_PLACEHOLDERS = [
    {'key': '证明编号', 'label': '证明编号'}, {'key': '开具人', 'label': '开具人'},
    {'key': '开具单位', 'label': '开具单位'}, {'key': '开具日期', 'label': '开具日期'},
    {'key': '申请用途', 'label': '申请用途'}, {'key': '份数', 'label': '份数'},
]
SYSTEM_PLACEHOLDER_KEYS = {item['key'] for item in SYSTEM_PLACEHOLDERS}

_SCHEMA_COLUMNS = {'field_schema', 'page_settings', 'is_active', 'version',
                   'created_by', 'updated_by'}


def _catalog_field(label, source, field_type='text', group='primary',
                   required=False, readonly=True):
    return {'key': label, 'label': label, 'type': field_type, 'source': source,
            'resident_group': group, 'required': required,
            'readonly': readonly, 'options': []}


FIELD_CATALOG = {
    '姓名': _catalog_field('姓名', 'name', 'resident', required=True, readonly=False),
    '性别': _catalog_field('性别', 'gender'),
    '身份证号': _catalog_field('身份证号', 'id_card', required=True),
    '出生日期': _catalog_field('出生日期', 'birth_date'),
    '年龄': _catalog_field('年龄', 'age'),
    '民族': _catalog_field('民族', 'nation'),
    '联系电话': _catalog_field('联系电话', 'phone'),
    '家庭住址': _catalog_field('家庭住址', 'address'),
    '所在组': _catalog_field('所在组', 'village_group'),
    '所在网格': _catalog_field('所在网格', 'grid'),
    '户主姓名': _catalog_field('户主姓名', 'household_name'),
    '户编号': _catalog_field('户编号', 'household_code'),
    '户口类型': _catalog_field('户口类型', 'household_type'),
    '户属性': _catalog_field('户属性', 'household_property'),
    '与户主关系': _catalog_field('与户主关系', 'relationship'),
    '政治面貌': _catalog_field('政治面貌', 'politics_status'),
    '文化程度': _catalog_field('文化程度', 'education'),
    '婚姻状况': _catalog_field('婚姻状况', 'marital_status'),
    '关系人姓名': _catalog_field('关系人姓名', 'name', 'resident', 'secondary', True, False),
    '关系人性别': _catalog_field('关系人性别', 'gender', group='secondary'),
    '关系人身份证号': _catalog_field('关系人身份证号', 'id_card', group='secondary', required=True),
    '关系人家庭住址': _catalog_field('关系人家庭住址', 'address', group='secondary'),
    '关系人联系电话': _catalog_field('关系人联系电话', 'phone', group='secondary'),
    '关系人所在组': _catalog_field('关系人所在组', 'village_group', group='secondary'),
}


def _field(key, field_type=None, required=None, readonly=None, **extra):
    base = dict(FIELD_CATALOG.get(key, {
        'key': key, 'label': key, 'type': 'text', 'source': '',
        'resident_group': '', 'required': False, 'readonly': False, 'options': []}))
    if field_type is not None:
        base['type'] = field_type
    if required is not None:
        base['required'] = required
    if readonly is not None:
        base['readonly'] = readonly
    base.update(extra)
    return base


DEFAULT_TEMPLATE_DEFINITIONS = [
    {
        'name': '居住证明（默认）', 'cert_type': '居住证明',
        'fields': [_field('姓名'), _field('性别'), _field('身份证号'),
                   _field('家庭住址'), _field('联系电话')],
        'content': (
            '\n<div style="text-align:center;font-family:方正小标宋简体;font-size:22pt;font-weight:400;line-height:1.35;margin-bottom:32px;">居住证明</div>\n'
            '<p style="font-size:16pt;line-height:1.75;text-indent:2em;">兹有我村村民 {{姓名}}，性别 {{性别}}，身份证号码：{{身份证号}}，家庭住址：{{家庭住址}}，本人长期居住在本村生活，情况属实。</p>\n'
            '<p style="font-size:16pt;line-height:1.75;text-indent:2em;">特此证明！</p>\n'
            '<div style="text-align:right;font-size:16pt;line-height:1.75;margin-top:56px;">\n'
            '<div>经办人：{{开具人}}</div><div>联系电话：{{联系电话}}</div><div>{{开具单位}}</div><div>{{开具日期}}</div>\n</div>'),
    },
    {
        'name': '死亡证明（默认）', 'cert_type': '死亡证明',
        'fields': [_field('姓名'), _field('性别'), _field('身份证号'), _field('家庭住址'),
                   _field('死亡日期', 'date', True, False),
                   _field('死亡地点', 'text', True, False)],
        'content': (
            '\n<div style="text-align:center;font-family:方正小标宋简体;font-size:22pt;font-weight:400;line-height:1.35;margin-bottom:32px;">死亡证明</div>\n'
            '<p style="font-size:16pt;line-height:1.75;text-indent:2em;">兹有我村村民 {{姓名}}，性别 {{性别}}，身份证号码：{{身份证号}}，家庭住址：{{家庭住址}}。经核实，该人于{{死亡日期}}在{{死亡地点}}去世，情况属实。</p>\n'
            '<p style="font-size:16pt;line-height:1.75;text-indent:2em;">特此证明！</p>\n'
            '<div style="text-align:right;font-size:16pt;line-height:1.75;margin-top:56px;">\n'
            '<div>经办人：{{开具人}}</div><div>{{开具单位}}</div><div>{{开具日期}}</div>\n</div>'),
    },
    {
        'name': '亲属关系证明（默认）', 'cert_type': '亲属关系证明',
        'fields': [_field('姓名'), _field('性别'), _field('身份证号'), _field('家庭住址'),
                   _field('关系人姓名'), _field('关系人性别'), _field('关系人身份证号'),
                   _field('关系人家庭住址'), _field('与本人关系', 'text', True, False)],
        'content': (
            '\n<div style="text-align:center;font-family:方正小标宋简体;font-size:22pt;font-weight:400;line-height:1.35;margin-bottom:32px;">亲属关系证明</div>\n'
            '<p style="font-size:16pt;line-height:1.75;text-indent:2em;">兹有我村村民 {{姓名}}，性别 {{性别}}，身份证号码：{{身份证号}}，家庭住址：{{家庭住址}}。</p>\n'
            '<p style="font-size:16pt;line-height:1.75;text-indent:2em;">{{关系人姓名}}，性别 {{关系人性别}}，身份证号码：{{关系人身份证号}}，家庭住址：{{关系人家庭住址}}。</p>\n'
            '<p style="font-size:16pt;line-height:1.75;text-indent:2em;">二人为 {{与本人关系}} 关系，情况属实。</p>\n'
            '<p style="font-size:16pt;line-height:1.75;text-indent:2em;">特此证明！</p>\n'
            '<div style="text-align:right;font-size:16pt;line-height:1.75;margin-top:56px;">\n'
            '<div>经办人：{{开具人}}</div><div>{{开具单位}}</div><div>{{开具日期}}</div>\n</div>'),
    },
]

LEGACY_PLACEHOLDERS = {
    'name': '姓名', 'id_card': '身份证号', 'address': '家庭住址',
    'date': '开具日期', 'name_a': '姓名', 'id_card_a': '身份证号',
    'name_b': '关系人姓名', 'id_card_b': '关系人身份证号',
    'relation': '与本人关系', 'death_date': '死亡日期', 'death_reason': '死亡原因',
}


def legacy_content_to_html(content):
    """旧纯文本模板 → HTML 段落（{{}} 占位符保留）。"""
    content = content or ''
    for old_key, new_key in LEGACY_PLACEHOLDERS.items():
        content = content.replace('{' + old_key + '}', '{{' + new_key + '}}')
    lines = [line.strip() for line in content.splitlines()]
    return ''.join((f'{html.escape(line)}</p>' if line else '<p><br></p>')
                   for line in lines)


def ensure_certificate_seed():
    """一次性幂等种子（042 P1-7）：补齐 3 个系统模板；旧版系统模板就位/转 HTML。

    只做 SELECT/INSERT/UPDATE，不建表、不加列（DDL 见 scripts/migrations/m015）。
    存量库未迁移（缺 6 列）时静默跳过，不阻断应用启动。
    """
    try:
        cols = {row[1] for row in db.session.execute(
            text('PRAGMA table_info(certificate_templates)')).fetchall()}
    except Exception:
        db.session.rollback()
        return False
    if not _SCHEMA_COLUMNS.issubset(cols):
        return False  # 未执行 m015：启动不写库，导入前由迁移提示兜底
    existing = {item.name: item for item in CertificateTemplate.query.all()}
    definitions = {item['name']: item for item in DEFAULT_TEMPLATE_DEFINITIONS}
    # 旧版系统模板（exe 前口径 cert_type=residence/death/kinship，内容为空）就地升级，
    # 避免与新增默认模板并存成 6 行。
    legacy_types = {'residence': '居住证明（默认）', 'death': '死亡证明（默认）',
                    'kinship': '亲属关系证明（默认）'}
    changed = False
    for item in CertificateTemplate.query.all():
        if item.is_custom:
            continue
        target = legacy_types.get(item.cert_type or '') or (
            item.name if item.name in definitions else None)
        if not target:
            continue
        definition = definitions[target]
        if item.name != definition['name']:
            item.name = definition['name']
            changed = True
        if item.cert_type != definition['cert_type']:
            item.cert_type = definition['cert_type']
            changed = True
        if not item.template_content or '<' not in item.template_content:
            item.template_content = definition['content']
            changed = True
        if not item.field_schema or item.field_schema == '[]':
            item.field_schema = json.dumps(definition['fields'], ensure_ascii=False)
            changed = True
        if not item.page_settings:
            item.page_settings = json.dumps(DEFAULT_PAGE_SETTINGS, ensure_ascii=False)
            changed = True
        if item.is_active is None:
            item.is_active = True
            changed = True
        if not item.version:
            item.version = 1
            changed = True
        existing[definition['name']] = item
    for name, definition in definitions.items():
        if name in existing:
            continue
        db.session.add(CertificateTemplate(
            name=name, cert_type=definition['cert_type'],
            template_content=definition['content'],
            field_schema=json.dumps(definition['fields'], ensure_ascii=False),
            page_settings=json.dumps(DEFAULT_PAGE_SETTINGS, ensure_ascii=False),
            is_custom=False, is_active=True, version=1,
            created_by='系统', updated_by='系统'))
        changed = True
    for item in existing.values():
        if item.template_content and '<' not in item.template_content:
            item.template_content = legacy_content_to_html(item.template_content)
            changed = True
    if changed:
        db.session.commit()
    return True
