"""routes.certificate — 证明开具（042：迁入 exe V2.45.2 完整能力，电子公章彻底删除）。

端点（ADR-016：蓝图已在 BLUEPRINT_MODULES 归类，动作位 view/add/edit/delete/export 自动推断）：
- GET  /certificate/                         三 Tab 工作台
- GET  /certificate/api/templates            模板列表
- GET  /certificate/api/template/<id>        模板详情
- POST /certificate/api/template/create      新建模板
- POST /certificate/api/template/update/<id> 更新模板（版本 +1）
- POST /certificate/api/template/status/<id> 启用/停用
- POST /certificate/api/template/delete/<id> 删除（系统模板/有记录不可删）
- GET  /certificate/api/residents            居民检索（回填）
- POST /certificate/api/issue/create         开具（生成 ZM 编号）
- GET  /certificate/api/issues               记录筛选/分页
- GET  /certificate/api/issue/<id>           记录详情
- POST /certificate/api/issue/void/<id>      作废
- GET  /certificate/api/issue/word/<id>      导出 Word
- POST /certificate/api/generate             旧入口 302 → issue/word/<id>

拍板记录（2026-10-21）：① 电子公章彻底删除（无表/端点/UI/字段）；② 旧端点移除，
api_generate 保留 302；③ 视觉重令牌；④ certificate.edit/export 权限位；⑤ CSS 内联。
"""
import html
import io
import json
import re
from datetime import datetime, timedelta
from html.parser import HTMLParser

from flask import (Blueprint, render_template, request, send_file, session,
                   redirect, url_for)
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from models import (CertificateIssue, CertificateTemplate, Resident, User, db)
from utils.decorators import login_required
from utils.helpers import json_response, log_operation, paginate_query
from utils.masking import header_kind, mask_field, mask_text, user_can_view_sensitive
from utils.certificate_catalog import (DEFAULT_PAGE_SETTINGS, FIELD_CATALOG,
                                       SYSTEM_PLACEHOLDERS,
                                       SYSTEM_PLACEHOLDER_KEYS)

cert_bp = Blueprint('certificate', __name__)


def _current_user():
    user_id = session.get('user_id')
    if user_id:
        return User.query.get(user_id)
    return None


def _operator_name():
    user = _current_user()
    if user:
        return user.real_name or user.username
    return session.get('username') or ''


def _json_load(value, default):
    try:
        parsed = json.loads(value or '')
        if isinstance(parsed, type(default)):
            return parsed
        return default
    except (TypeError, ValueError):
        return default


ALLOWED_TAGS = {'b', 'i', 'p', 's', 'u', 'br', 'em', 'h1', 'h2', 'h3', 'li',
                'ol', 'ul', 'div', 'font', 'span', 'strong', 'blockquote'}
BLOCKED_TAGS = {'link', 'meta', 'embed', 'style', 'iframe', 'object', 'script'}
ALLOWED_STYLES = {'letter-spacing', 'color', 'font-size', 'font-style', 'margin-top',
                  'text-align', 'font-family', 'font-weight', 'line-height',
                  'margin-left', 'text-indent', 'margin-right', 'margin-bottom',
                  'text-decoration'}


def _sanitize_style(style):
    cleaned = []
    for declaration in (style or '').split(';'):
        if ':' not in declaration:
            continue
        name, value = declaration.split(':', 1)
        name = name.strip().lower()
        value = value.strip()
        if name not in ALLOWED_STYLES:
            continue
        if re.search(r'url\s*\(|expression|javascript:', value, re.I):
            continue
        if len(value) > 80:
            continue
        cleaned.append(f'{name}:{value}')
    return ';'.join(cleaned)


class CertificateHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in BLOCKED_TAGS:
            self.blocked_depth += 1
            return
        if self.blocked_depth or tag not in ALLOWED_TAGS:
            return
        clean_attrs = []
        for name, value in attrs:
            name = name.lower()
            if name == 'style':
                value = _sanitize_style(value)
                if not value:
                    continue
                clean_attrs.append(('style', value))
            else:
                if tag != 'font':
                    continue
                if name not in {'face', 'size', 'color'}:
                    continue
                value = re.sub(r'[^#(),.%\w\u4e00-\u9fff\-\s]', '', value or '')[:50]
                if not value:
                    continue
                clean_attrs.append((name, value))
        attr_text = ''.join(f' {name}="{html.escape(value, True)}"'
                            for name, value in clean_attrs)
        self.parts.append(f'<{tag}{attr_text}>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() != 'br':
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in BLOCKED_TAGS:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if not self.blocked_depth and tag in ALLOWED_TAGS and tag != 'br':
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.blocked_depth:
            self.parts.append(html.escape(data))


def sanitize_template_html(content):
    sanitizer = CertificateHTMLSanitizer()
    sanitizer.feed(content or '')
    sanitizer.close()
    return ''.join(sanitizer.parts).strip()


def _normalize_page_settings(raw):
    raw = raw if isinstance(raw, dict) else {}

    def bounded(value, default, minimum, maximum):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def number(key, default, minimum, maximum):
        return bounded(raw.get(key, default), default, minimum, maximum)

    orientation = raw.get('orientation') if raw.get('orientation') in ('portrait', 'landscape') else 'portrait'
    default_font = DEFAULT_PAGE_SETTINGS['font_name']
    font_name = re.sub(r'[^\w\u4e00-\u9fff\-\s]',
                       '', str(raw.get('font_name') or default_font))[:30] or default_font
    return {
        'orientation': orientation,
        'margin_top': number('margin_top', DEFAULT_PAGE_SETTINGS['margin_top'], 5, 50),
        'margin_right': number('margin_right', DEFAULT_PAGE_SETTINGS['margin_right'], 5, 50),
        'margin_bottom': number('margin_bottom', DEFAULT_PAGE_SETTINGS['margin_bottom'], 5, 50),
        'margin_left': number('margin_left', DEFAULT_PAGE_SETTINGS['margin_left'], 5, 50),
        'font_name': font_name,
        'font_size': number('font_size', DEFAULT_PAGE_SETTINGS['font_size'], 5, 42),
        'line_height': number('line_height', DEFAULT_PAGE_SETTINGS['line_height'], 1, 3),
    }


def _normalize_fields(raw_fields, content):
    allowed_types = {'date', 'text', 'number', 'select', 'resident', 'textarea'}
    normalized = []
    seen = set()
    if not isinstance(raw_fields, list):
        raw_fields = []
    for raw in raw_fields:
        if not isinstance(raw, dict):
            continue
        key = re.sub(r'[{}<>]', '', str(raw.get('key') or raw.get('label') or '')).strip()[:40]
        if not key or key in seen or key in SYSTEM_PLACEHOLDER_KEYS:
            continue
        if key in FIELD_CATALOG:
            item = dict(FIELD_CATALOG[key])
            item['label'] = str(raw.get('label') or item['label']).strip()[:40]
            item['required'] = bool(raw.get('required', item['required']))
        else:
            field_type = raw.get('type') if raw.get('type') in allowed_types else 'text'
            item = {'key': key, 'label': str(raw.get('label') or key).strip()[:40],
                    'type': field_type, 'source': '', 'resident_group': '',
                    'required': bool(raw.get('required')), 'readonly': False,
                    'options': []}
            options = raw.get('options') or []
            if isinstance(options, str):
                options = [part.strip() for part in re.split(r'[,，\n]', options) if part.strip()]
            item['options'] = [str(option).strip()[:50] for option in options[:30]
                               if str(option).strip()]
        normalized.append(item)
        seen.add(key)
    for placeholder in re.findall(r'{{\s*([^{}]+?)\s*}}', content or ''):
        key = placeholder.strip()[:40]
        if key and key not in seen and key not in SYSTEM_PLACEHOLDER_KEYS:
            normalized.append(_field(key))
            seen.add(key)
    return normalized


def _format_date_cn(value):
    value = str(value or '').strip()
    match = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})', value)
    if not match:
        return value
    return f'{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日'


def _render_content(content, values):
    def replace(match):
        key = match.group(1).strip()
        value = values.get(key, '')
        if value is None or str(value).strip() == '':
            value = '______'
        return html.escape(str(value)).replace('\n', '<br>')

    return re.sub(r'{{\s*([^{}]+?)\s*}}', replace, content or '')


def _template_payload(item, include_content=True):
    settings = _normalize_page_settings(_json_load(item.page_settings, {}))
    data = {
        'id': item.id, 'name': item.name or '', 'cert_type': item.cert_type or '',
        'field_schema': _json_load(item.field_schema, []), 'page_settings': settings,
        'is_custom': bool(item.is_custom),
        'is_active': True if item.is_active is None else bool(item.is_active),
        'version': item.version or 1, 'created_by': item.created_by or '',
        'updated_by': item.updated_by or '',
        'updated_at': item.updated_at.strftime('%Y-%m-%d %H:%M') if item.updated_at else '',
        'usage_count': CertificateIssue.query.filter_by(template_id=item.id).count(),
    }
    if include_content:
        data['template_content'] = item.template_content or ''
    return data


def _issue_payload(item, include_content=False):
    settings = _normalize_page_settings(_json_load(item.page_settings, {}))
    data = {
        'id': item.id, 'serial_no': item.serial_no, 'template_id': item.template_id,
        'template_name': item.template_name or '',
        'template_version': item.template_version or 1, 'cert_type': item.cert_type or '',
        'resident_id': item.resident_id, 'subject_name': item.subject_name or '',
        'subject_id_card': item.subject_id_card or '', 'issuer_name': item.issuer_name or '',
        'issue_unit': item.issue_unit or '', 'purpose': item.purpose or '',
        'copies': item.copies or 1,
        'issued_at': item.issued_at.strftime('%Y-%m-%d %H:%M:%S') if item.issued_at else '',
        'status': item.status or 'issued', 'voided_by': item.voided_by or '',
        'voided_at': item.voided_at.strftime('%Y-%m-%d %H:%M:%S') if item.voided_at else '',
        'void_reason': item.void_reason or '',
    }
    if include_content:
        form_data = _json_load(item.form_data, {})
        rendered = item.rendered_content or ''
        # 043 R-042-01：无 view_sensitive 时在此收敛脱敏（全局 MaskFilter 只认 key 后缀，
        # form_data 是中文键、rendered_content 是自由文本，两者都命中不了）。
        if not user_can_view_sensitive(_current_user()):
            cleaned = {}
            for key, value in form_data.items():
                kind = header_kind(key) if isinstance(value, str) else None
                cleaned[key] = mask_field(value, kind) if kind else value
            form_data = cleaned
            rendered = mask_text(rendered)
        data.update({'form_data': form_data, 'rendered_content': rendered,
                     'page_settings': settings})
    return data


def _next_serial_no(now):
    prefix = now.strftime('ZM%Y%m%d')
    latest = (CertificateIssue.query
              .filter(CertificateIssue.serial_no.like(f'{prefix}%'))
              .order_by(CertificateIssue.serial_no.desc()).first())
    sequence = 1
    if latest:
        try:
            sequence = int(latest.serial_no[-4:]) + 1
        except (TypeError, ValueError):
            sequence = CertificateIssue.query.filter(
                CertificateIssue.serial_no.like(f'{prefix}%')).count() + 1
    return f'{prefix}{sequence:04d}'


@cert_bp.route('/', endpoint='list')
@login_required
def certificate_list():
    return render_template('certificate/list.html', field_catalog=FIELD_CATALOG,
                           system_placeholders=SYSTEM_PLACEHOLDERS,
                           default_page_settings=DEFAULT_PAGE_SETTINGS)


@cert_bp.route('/api/templates')
@login_required
def api_templates():
    keyword = request.args.get('keyword', '').strip()
    include_inactive = request.args.get('include_inactive', '0') == '1'
    query = CertificateTemplate.query
    if keyword:
        query = query.filter(or_(CertificateTemplate.name.contains(keyword),
                                 CertificateTemplate.cert_type.contains(keyword)))
    if not include_inactive:
        query = query.filter(CertificateTemplate.is_active != False)  # noqa: E712
    items = query.order_by(CertificateTemplate.is_active.desc(),
                           CertificateTemplate.updated_at.desc(),
                           CertificateTemplate.id.desc()).all()
    return json_response(True, data=[_template_payload(item) for item in items])


@cert_bp.route('/api/template/<int:item_id>')
@login_required
def api_template_detail(item_id):
    item = CertificateTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    return json_response(True, data=_template_payload(item))


def _read_template_request():
    payload = request.get_json(silent=True) or request.form.to_dict()
    name = str(payload.get('name') or '').strip()
    cert_type = str(payload.get('cert_type') or '').strip()
    content = sanitize_template_html(str(payload.get('template_content') or ''))
    fields = payload.get('field_schema', [])
    settings = payload.get('page_settings', {})
    if isinstance(fields, str):
        fields = _json_load(fields, [])
    if isinstance(settings, str):
        settings = _json_load(settings, {})
    fields = _normalize_fields(fields, content)
    settings = _normalize_page_settings(settings)
    if not name:
        return None, '请输入模板名称'
    if not cert_type:
        return None, '请输入证明类别'
    if not content or not re.sub('<[^>]+>', '', content).strip():
        return None, '模板正文不能为空'
    return {'name': name[:100], 'cert_type': cert_type[:50],
            'template_content': content,
            'field_schema': json.dumps(fields, ensure_ascii=False),
            'page_settings': json.dumps(settings, ensure_ascii=False),
            'is_active': bool(payload.get('is_active', True))}, ''


@cert_bp.route('/api/template/create', methods=['POST'])
@login_required
def api_template_create():
    data, error = _read_template_request()
    if error:
        return json_response(False, error)
    operator = _operator_name()
    item = CertificateTemplate(**{**data, 'is_custom': True, 'version': 1,
                                  'created_by': operator, 'updated_by': operator})
    db.session.add(item)
    db.session.commit()
    log_operation(session.get('username', ''), '证明开具', 'add',
                  f'新增证明模板: {item.name}')
    return json_response(True, '模板已保存', data=_template_payload(item))


@cert_bp.route('/api/template/update/<int:item_id>', methods=['POST'])
@login_required
def api_template_update(item_id):
    item = CertificateTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    data, error = _read_template_request()
    if error:
        return json_response(False, error)
    changed = any(getattr(item, key) != value for key, value in data.items())
    for key, value in data.items():
        setattr(item, key, value)
    if changed:
        item.version = (item.version or 1) + 1
    item.updated_by = _operator_name()
    db.session.commit()
    log_operation(session.get('username', ''), '证明开具', 'edit',
                  f'修改证明模板: {item.name} V{item.version}')
    return json_response(True, '模板已更新', data=_template_payload(item))


@cert_bp.route('/api/template/status/<int:item_id>', methods=['POST'])
@login_required
def api_template_status(item_id):
    item = CertificateTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    item.is_active = not bool(item.is_active)
    item.updated_by = _operator_name()
    db.session.commit()
    action_text = '启用' if item.is_active else '停用'
    log_operation(session.get('username', ''), '证明开具', 'edit',
                  f'{action_text}证明模板: {item.name}')
    return json_response(True, f'模板已{action_text}', data=_template_payload(item))


@cert_bp.route('/api/template/delete/<int:item_id>', methods=['POST'])
@login_required
def api_template_delete(item_id):
    item = CertificateTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    if not item.is_custom:
        return json_response(False, '系统默认模板不能删除，可选择停用')
    if CertificateIssue.query.filter_by(template_id=item.id).first():
        return json_response(False, '该模板已有开具记录，不能删除，可选择停用')
    name = item.name
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username', ''), '证明开具', 'delete',
                  f'删除证明模板: {name}')
    return json_response(True, '模板已删除')


@cert_bp.route('/api/residents')
@login_required
def api_residents():
    keyword = request.args.get('keyword', '').strip()
    if not keyword:
        return json_response(False, '请输入姓名或身份证号')
    # 042 语义适配：仓库人员状态为 normal/deceased/moved_out（exe 原写 '死亡'）
    # 身份证为 Fernet 加密列：短关键字按姓名；完整号码同时命中明文列与 hash 列。
    from utils.crypto import key_holder, sha256_hash
    query = Resident.query.filter(~Resident.status.in_(['deceased', 'moved_out']))
    conditions = [Resident.name.contains(keyword)]
    if len(keyword) >= 15:
        conditions.append(Resident.id_card == keyword)
        conditions.append(Resident.id_card_hash == sha256_hash(keyword))
    query = query.filter(or_(*conditions))
    residents = query.order_by(Resident.name.asc(), Resident.id.asc()).limit(20).all()
    now = datetime.now()
    def _plain(plain_value, enc_value):
        if plain_value:
            return plain_value
        if not enc_value:
            return ''
        try:
            return key_holder.decrypt(enc_value)
        except Exception:
            return ''

    data = []
    for item in residents:
        id_card = _plain(item.id_card, getattr(item, 'id_card_enc', None))
        phone = _plain(item.phone, getattr(item, 'phone_enc', None))
        age = ''
        if item.birth_date:
            age = now.year - item.birth_date.year - (
                (now.month, now.day) < (item.birth_date.month, item.birth_date.day))
        data.append({
            'id': item.id, 'name': item.name or '', 'id_card': id_card,
            'gender': item.gender or '',
            'birth_date': item.birth_date.strftime('%Y-%m-%d') if item.birth_date else '',
            'age': age, 'nation': item.nation or '', 'phone': phone,
            'village_group': item.village_group or '', 'grid': item.grid or '',
            'address': item.address or '', 'household_name': item.household_name or '',
            'household_code': item.household_code or '',
            'household_type': item.household_type or '',
            'household_property': item.household_property or '',
            'relationship': item.relationship or '',
            'politics_status': item.politics_status or '',
            'education': item.education or '', 'marital_status': item.marital_status or '',
        })
    return json_response(True, data=data, total=len(data))


@cert_bp.route('/api/issue/create', methods=['POST'])
@login_required
def api_issue_create():
    payload = request.get_json(silent=True) or {}
    template_id = payload.get('template_id')
    item = CertificateTemplate.query.get(template_id)
    if not item:
        return json_response(False, '请选择有效证明模板')
    if item.is_active is False:
        return json_response(False, '该模板已停用')
    fields = _json_load(item.field_schema, [])
    raw_values = payload.get('values') if isinstance(payload.get('values'), dict) else {}
    # exe 反编译误写 str('').strip()，会让所有字段变空；此处按原语义取实际值
    values = {str(key): str(value).strip() for key, value in raw_values.items() if value}
    from utils.import_engine import id_card_checksum_ok, validate_id_card
    missing, invalid = [], ''
    for field in fields:
        key = field.get('key', '')
        value = values.get(key, '')
        if field.get('required') and not value:
            missing.append(field.get('label') or key)
            continue
        if not value:
            continue
        label = field.get('label') or key
        if key.endswith('身份证号'):
            value = value.upper()
            values[key] = value
            if len(value) == 15:
                invalid = f'{label}为 15 位旧号，请升位为 18 位'
            elif not validate_id_card(value)[0] or not id_card_checksum_ok(value):
                invalid = f'{label}格式或校验位不正确'
        elif key in ('姓名', '关系人姓名'):
            if len(value) > 50:
                invalid = f'{label}须为 1–50 字'
        elif field.get('type') == 'date':
            try:
                parsed = datetime.strptime(value, '%Y-%m-%d')
            except ValueError:
                invalid = f'{label}日期格式不正确'
            else:
                if key == '出生日期' and parsed.date() > datetime.now().date():
                    invalid = '出生日期不得晚于今天'
                values[key] = _format_date_cn(value)
        limit = 2000 if field.get('type') == 'textarea' else 200
        if len(value) > limit or '{{' in value or '}}' in value:
            invalid = f'{label}内容不合规'
        if invalid:
            break
    if missing:
        return json_response(False, '请填写：' + '、'.join(missing))
    if invalid:
        return json_response(False, invalid), 400

    now = datetime.now()
    operator = _operator_name()
    issue_date = str(payload.get('issue_date') or now.strftime('%Y-%m-%d')).strip()
    issuer_name = str(payload.get('issuer_name') or operator).strip()[:50]
    issue_unit = str(payload.get('issue_unit') or '').strip()[:150]
    purpose = str(payload.get('purpose') or '').strip()[:200]
    try:
        copies = max(1, min(99, int(payload.get('copies') or 1)))
    except (TypeError, ValueError):
        copies = 1
    user = _current_user()
    subject_name = values.get('姓名') or values.get('关系人姓名') or '未填写姓名'
    subject_id_card = values.get('身份证号') or values.get('关系人身份证号') or ''
    resident_ids = payload.get('resident_ids') if isinstance(payload.get('resident_ids'), dict) else {}
    resident_id = resident_ids.get('primary')
    try:
        resident_id = int(resident_id) if resident_id else None
    except (TypeError, ValueError):
        resident_id = None
    if resident_id:
        # 044 R-042-07：姓名 + 身份证 hash 双比对（不解密）；任一不符按手工件放行
        from utils.crypto import sha256_hash
        linked = Resident.query.get(resident_id)
        id_ok = bool(linked and subject_id_card and
                     sha256_hash(str(subject_id_card).strip().upper()) == (linked.id_card_hash or ''))
        if not linked or linked.name != str(subject_name).strip() or not id_ok:
            resident_id = None
    issue_page_settings = _normalize_page_settings(_json_load(item.page_settings, {}))
    issue = None
    for _attempt in range(3):
        serial_no = _next_serial_no(now)
        values.update({'证明编号': serial_no, '开具人': issuer_name,
                       '开具单位': issue_unit, '开具日期': _format_date_cn(issue_date),
                       '申请用途': purpose, '份数': copies})
        rendered = _render_content(item.template_content or '', values)
        issue = CertificateIssue(
            serial_no=serial_no, template_id=item.id, template_name=item.name,
            template_version=item.version or 1, cert_type=item.cert_type or '',
            resident_id=resident_id, subject_name=str(subject_name)[:50],
            subject_id_card=str(subject_id_card)[:18],
            form_data=json.dumps(values, ensure_ascii=False), rendered_content=rendered,
            page_settings=json.dumps(issue_page_settings, ensure_ascii=False),
            issuer_id=user.id if user else None, issuer_name=issuer_name,
            issue_unit=issue_unit, purpose=purpose, copies=copies,
            issued_at=now, status='issued')
        db.session.add(issue)
        try:
            db.session.commit()
            break
        except IntegrityError:
            # 044 R-042-03：并发抢号 → 回滚重取（3 次仍冲突返回 409，不裸 500）
            db.session.rollback()
            issue = None
    if issue is None:
        return json_response(False, '编号生成冲突，请重试'), 409
    log_operation(session.get('username', ''), '证明开具', 'add',
                  f'开具证明: {serial_no} {item.name} {issue.subject_name}',
                  detail=json.dumps({'serial_no': serial_no,
                                     'template_version': issue.template_version,
                                     'copies': copies, 'purpose': purpose},
                                    ensure_ascii=False))
    return json_response(True, '证明开具成功', data=_issue_payload(issue, include_content=True))


@cert_bp.route('/api/issues')
@login_required
def api_issues():
    page = request.args.get('page', 1, type=int)
    limit = min(100, max(10, request.args.get('limit', 20, type=int)))
    keyword = request.args.get('keyword', '').strip()
    cert_type = request.args.get('cert_type', '').strip()
    issuer = request.args.get('issuer', '').strip()
    status = request.args.get('status', '').strip()
    date_start = request.args.get('date_start', '').strip()
    date_end = request.args.get('date_end', '').strip()
    query = CertificateIssue.query
    if keyword:
        query = query.filter(or_(
            CertificateIssue.serial_no.contains(keyword),
            CertificateIssue.subject_name.contains(keyword),
            CertificateIssue.subject_id_card.contains(keyword),
            CertificateIssue.template_name.contains(keyword)))
    if cert_type:
        query = query.filter(CertificateIssue.cert_type == cert_type)
    if issuer:
        query = query.filter(CertificateIssue.issuer_name.contains(issuer))
    if status:
        query = query.filter(CertificateIssue.status == status)
    try:
        if date_start:
            query = query.filter(CertificateIssue.issued_at >= datetime.strptime(date_start, '%Y-%m-%d'))
        if date_end:
            query = query.filter(CertificateIssue.issued_at < datetime.strptime(date_end, '%Y-%m-%d') + timedelta(days=1))
    except ValueError:
        return json_response(False, '日期格式不正确')
    query = query.order_by(CertificateIssue.id.desc())
    items, total, pages = paginate_query(query, page, limit)
    return json_response(True, data={
        'items': [_issue_payload(item) for item in items],
        'total': total, 'pages': max(1, pages), 'page': page})


@cert_bp.route('/api/issue/<int:item_id>')
@login_required
def api_issue_detail(item_id):
    item = CertificateIssue.query.get(item_id)
    if not item:
        return json_response(False, '开具记录不存在')
    return json_response(True, data=_issue_payload(item, include_content=True))


@cert_bp.route('/api/issue/void/<int:item_id>', methods=['POST'])
@login_required
def api_issue_void(item_id):
    item = CertificateIssue.query.get(item_id)
    if not item:
        return json_response(False, '开具记录不存在')
    if item.status == 'voided':
        return json_response(False, '该证明已经作废')
    payload = request.get_json(silent=True) or request.form.to_dict()
    reason = str(payload.get('reason') or '').strip()
    if not reason:
        return json_response(False, '请填写作废原因')
    item.status = 'voided'
    item.voided_by = _operator_name()
    item.voided_at = datetime.now()
    item.void_reason = reason[:300]
    db.session.commit()
    log_operation(session.get('username', ''), '证明开具', 'edit',
                  f'作废证明: {item.serial_no}', detail=reason)
    return json_response(True, '证明已作废', data=_issue_payload(item))


def _parse_css(style):
    result = {}
    for declaration in (style or '').split(';'):
        if ':' in declaration:
            key, value = declaration.split(':', 1)
            result[key.strip().lower()] = value.strip()
    return result


class CertificateDocxParser(HTMLParser):
    BLOCK_TAGS = {'p', 'h1', 'h2', 'h3', 'li', 'div', 'blockquote'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.current = None
        self.inline_stack = []
        self.block_stack = []

    def _finish_block(self):
        if self.current is not None:
            if self.current['runs'] or self.current.get('keep_empty'):
                self.blocks.append(self.current)
            self.current = None

    def _start_block(self, tag, attrs):
        self._finish_block()
        attr_map = dict(attrs)
        parent_style = self.block_stack[-1][1] if self.block_stack else {}
        style = dict(parent_style)
        style.update(_parse_css(attr_map.get('style', '')))
        if tag == 'h1':
            style.setdefault('text-align', 'center')
            style.setdefault('font-size', '26pt')
            style.setdefault('font-weight', '700')
        elif tag == 'h2':
            style.setdefault('font-size', '20pt')
            style.setdefault('font-weight', '700')
        elif tag == 'h3':
            style.setdefault('font-size', '16pt')
            style.setdefault('font-weight', '700')
        self.block_stack.append((tag, style))
        self.current = {'tag': tag, 'style': style, 'runs': [], 'keep_empty': True}
        if tag == 'li':
            self.current['runs'].append({'text': '• ', 'style': {}})

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.BLOCK_TAGS:
            self._start_block(tag, attrs)
            return
        if tag == 'br':
            if self.current is None:
                self.current = {'tag': 'p', 'style': {}, 'runs': [], 'keep_empty': True}
            self.current['runs'].append({'text': '\n', 'style': dict(self._inline_style())})
            return
        style = _parse_css(dict(attrs).get('style', ''))
        if tag in ('strong', 'b'):
            style['font-weight'] = '700'
        if tag in ('em', 'i'):
            style['font-style'] = 'italic'
        if tag == 'u':
            style['text-decoration'] = 'underline'
        if tag == 's':
            style['text-decoration'] = 'line-through'
        if tag == 'font':
            attr_map = dict(attrs)
            if attr_map.get('face'):
                style['font-family'] = attr_map['face']
            if attr_map.get('size'):
                style['font-size-html'] = attr_map['size']
            if attr_map.get('color'):
                style['color'] = attr_map['color']
        if tag in {'b', 'i', 's', 'u', 'em', 'font', 'span', 'strong'}:
            self.inline_stack.append((tag, style))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.BLOCK_TAGS:
            self._finish_block()
            for index in range(len(self.block_stack) - 1, -1, -1):
                if self.block_stack[index][0] == tag:
                    del self.block_stack[index:]
                    return
            return
        for index in range(len(self.inline_stack) - 1, -1, -1):
            if self.inline_stack[index][0] == tag:
                del self.inline_stack[index]
                return

    def _inline_style(self):
        style = {}
        for _, item_style in self.inline_stack:
            style.update(item_style)
        return style

    def handle_data(self, data):
        if self.current is None:
            if not data.strip():
                return
            inherited = self.block_stack[-1][1] if self.block_stack else {}
            self.current = {'tag': 'p', 'style': dict(inherited),
                            'runs': [], 'keep_empty': False}
        self.current['runs'].append({'text': data, 'style': dict(self._inline_style())})

    def close(self):
        super().close()
        self._finish_block()


def _numeric_css(value, default=0):
    match = re.search(r'-?\d+(?:\.\d+)?', str(value or ''))
    return float(match.group()) if match else default


def _css_length_pt(value, em_size, default=0):
    text_value = str(value or '').strip().lower()
    number = _numeric_css(text_value, default)
    if text_value.endswith('rem') or text_value.endswith('em'):
        return number * em_size
    if text_value.endswith('px'):
        return number * 0.75
    if text_value.endswith('mm'):
        return number * 72 / 25.4
    if text_value.endswith('cm'):
        return number * 72 / 2.54
    if text_value.endswith('in'):
        return number * 72
    if text_value.endswith('pc'):
        return number * 12
    return number


def _font_size_pt(style, default):
    if style.get('font-size-html'):
        return {1: 8, 2: 10, 3: 12, 4: 14, 5: 18, 6: 24, 7: 32}.get(
            int(_numeric_css(style['font-size-html'], 4)), default)
    value = style.get('font-size')
    return _css_length_pt(value, default, default) if value else default


def _build_docx(rendered_content, page_settings):
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    settings = _normalize_page_settings(page_settings)
    doc = Document()
    section = doc.sections[0]
    if settings['orientation'] == 'landscape':
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Mm(297)
        section.page_height = Mm(210)
    else:
        section.page_width = Mm(210)
        section.page_height = Mm(297)
    section.top_margin = Mm(settings['margin_top'])
    section.right_margin = Mm(settings['margin_right'])
    section.bottom_margin = Mm(settings['margin_bottom'])
    section.left_margin = Mm(settings['margin_left'])
    normal = doc.styles['Normal']
    normal.font.name = settings['font_name']
    normal.font.size = Pt(settings['font_size'])
    normal._element.rPr.rFonts.set(qn('w:eastAsia'), settings['font_name'])
    parser = CertificateDocxParser()
    parser.feed(rendered_content or '')
    parser.close()
    alignments = {'left': WD_ALIGN_PARAGRAPH.LEFT, 'center': WD_ALIGN_PARAGRAPH.CENTER,
                  'right': WD_ALIGN_PARAGRAPH.RIGHT, 'justify': WD_ALIGN_PARAGRAPH.JUSTIFY}
    for block in parser.blocks:
        style = block['style']
        block_font_size = _font_size_pt(style, settings['font_size'])
        paragraph = doc.add_paragraph()
        paragraph.alignment = alignments.get(style.get('text-align'), WD_ALIGN_PARAGRAPH.LEFT)
        line_height = str(style.get('line-height') or '').strip().lower()
        if re.search(r'(?:pt|px|mm|cm|in|pc|em|rem)$', line_height):
            paragraph.paragraph_format.line_spacing = Pt(
                _css_length_pt(line_height, block_font_size))
        else:
            paragraph.paragraph_format.line_spacing = _numeric_css(
                line_height, settings['line_height'])
        if style.get('text-indent'):
            paragraph.paragraph_format.first_line_indent = Pt(
                _css_length_pt(style['text-indent'], block_font_size))
        if style.get('margin-top'):
            paragraph.paragraph_format.space_before = Pt(
                _css_length_pt(style['margin-top'], block_font_size))
        if style.get('margin-bottom'):
            paragraph.paragraph_format.space_after = Pt(
                _css_length_pt(style['margin-bottom'], block_font_size))
        if style.get('margin-left'):
            paragraph.paragraph_format.left_indent = Pt(
                _css_length_pt(style['margin-left'], block_font_size))
        if style.get('margin-right'):
            paragraph.paragraph_format.right_indent = Pt(
                _css_length_pt(style['margin-right'], block_font_size))
        for run_data in block['runs']:
            run_style = dict(style)
            run_style.update(run_data['style'])
            run = paragraph.add_run(run_data['text'])
            font_name = str(run_style.get('font-family') or settings['font_name']).strip(
                '"\' ').split(',')[0] or settings['font_name']
            run.font.name = font_name
            run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)
            run.font.size = Pt(_font_size_pt(run_style, settings['font_size']))
            run.bold = str(run_style.get('font-weight', '')).lower() in {
                '600', '700', '800', '900', 'bold'}
            run.italic = str(run_style.get('font-style', '')).lower() == 'italic'
            decoration = str(run_style.get('text-decoration', '')).lower()
            run.underline = 'underline' in decoration
            color = str(run_style.get('color') or '').strip().lstrip('#')
            if re.fullmatch(r'[0-9a-fA-F]{6}', color):
                run.font.color.rgb = RGBColor.from_string(color.upper())
    if not parser.blocks:
        doc.add_paragraph('')
    output = io.BytesIO()
    doc.save(output)
    output.seek(0)
    return output


@cert_bp.route('/api/issue/word/<int:item_id>')
@login_required
def api_issue_word(item_id):
    item = CertificateIssue.query.get(item_id)
    if not item:
        return json_response(False, '开具记录不存在')
    output = _build_docx(item.rendered_content or '', _json_load(item.page_settings, {}))
    log_operation(session.get('username', ''), '证明开具', 'export',
                  f'导出证明Word: {item.serial_no}')
    safe_name = re.sub(r'[\\/:*?"<>|]', '', item.subject_name or '证明')
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True, download_name=f'{item.serial_no}_{safe_name}.docx')


@cert_bp.route('/api/generate', methods=['GET', 'POST'])
@login_required
def api_generate():
    """拍板②：旧导出入口保留 302 兼容；无 issue_id 时提示先确认开具。"""
    issue_id = request.values.get('issue_id', type=int)
    if issue_id:
        return redirect(url_for('certificate.api_issue_word', item_id=issue_id), code=302)
    return json_response(False, '请先点击“确认开具”生成留存记录后再导出 Word')
