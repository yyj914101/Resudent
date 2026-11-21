import json
from datetime import datetime
from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy import false, func, or_
from sqlalchemy.exc import IntegrityError
from models import db, GovernanceLedger, GovernancePetition, GovernancePetitionHandle
from utils.decorators import login_required
from utils.helpers import export_excel, json_response, log_operation, paginate_query
from utils.crypto import key_holder, sha256_hash
from utils.dict_enum import get_enum
from utils.masking import mask_id_card, mask_phone, user_can_view_sensitive
from utils.sensitive_fields import make_sensitive_helpers

governance_bp = Blueprint('governance', __name__)

# R12 字段级加密管线（对齐 mediation/subsidy 先例，消除综治台账明文孤岛）
_GOV_SENS_FIELDS = {'id_card': ('id_card_enc', 'id_card_hash', None)}
_decrypt, _set_enc, _clause = make_sensitive_helpers(GovernanceLedger, _GOV_SENS_FIELDS, key_holder)

# 通用列（存独立列），其余字段落 payload(JSON)
COMMON_KEYS = {'name', 'phone', 'id_card', 'record_date'}


from utils.governance_catalog import CATEGORY_CONFIG, _field  # noqa: E402


def _config_or_404(category):
    return CATEGORY_CONFIG.get(category)


def _resolved_config(config):
    """094：把 option_category 字段解析为字典 options（不修改模块级配置）。

    2026-11-16：filters（工具栏筛选）同源解析——筛选项也走字典单一真源，
    页面不出现硬编码枚举。返回的 dict 恒带 filters 键（无筛选的类别为空列表）。
    """
    fields = []
    for field in config['fields']:
        resolved = dict(field)
        dict_name = resolved.get('option_category')
        if dict_name:
            resolved['options'] = [{'value': label, 'label': label}
                                   for label in get_enum(dict_name)]
        fields.append(resolved)
    filters = []
    for item in config.get('filters') or []:
        resolved = dict(item)
        dict_name = resolved.get('option_category')
        if dict_name:
            resolved['options'] = [{'value': label, 'label': label}
                                   for label in get_enum(dict_name)]
        filters.append(resolved)
    config = dict(config)
    config['fields'] = fields
    config['filters'] = filters
    return config


def _text(value):
    return str(value or '').strip()


def _parse_date(value, label):
    raw = _text(value)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f'{label}格式应为YYYY-MM-DD')


def _payload_from_form(config):
    payload = {}
    for field in config['fields']:
        key = field['key']
        value = _text(request.form.get(key))
        # R-047-03：必填校验覆盖全部字段（含 name/record_date 等通用列），不只 payload 列
        if field.get('required') and not value:
            raise ValueError(f'{field["label"]}不能为空')
        if field.get('option_category') and value:
            # 094：枚举单一真源——只接受字典内取值，非法值保存失败且不写库
            if value not in get_enum(field['option_category']):
                raise ValueError(f'{field["label"]}取值不合法')
        if field['kind'] == 'date' and value:
            _parse_date(value, field['label'])
        if key in COMMON_KEYS:
            continue
        payload[key] = value
    return payload


def _serialize(item):
    try:
        payload = json.loads(item.payload or '{}')
    except (TypeError, ValueError):
        payload = {}
    data = {
        'id': item.id,
        'category': item.category,
        'name': item.name or '',
        'id_card': _decrypt(item, 'id_card'),
        'phone': item.phone or '',
        'record_date': item.record_date.isoformat() if item.record_date else '',
        'created_by': item.created_by or '',
        'created_at': item.created_at.strftime('%Y-%m-%d %H:%M') if item.created_at else '',
    }
    data.update(payload)
    return data


def _query(category, config=None):
    query = GovernanceLedger.query.filter_by(category=category)
    keyword = _text(request.args.get('keyword'))
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(or_(
            GovernanceLedger.name.like(like),
            GovernanceLedger.phone.like(like),
            GovernanceLedger.payload.like(like),
            GovernanceLedger.id_card_hash == sha256_hash(keyword.upper()),
        ))
    # 2026-11-16：工具栏筛选（config['filters']）——payload 是 JSON 文本，
    # 走 SQLite json_extract 精确匹配；筛选值必须落在字典内，非法值直接空集，
    # 不把任意字符串拼进 SQL（键名来自服务端配置，非用户输入）。
    for item in (config or {}).get('filters') or []:
        value = _text(request.args.get(item['key']))
        if not value:
            continue
        dict_name = item.get('option_category')
        if dict_name and value not in get_enum(dict_name):
            return query.filter(false())
        query = query.filter(
            func.json_extract(GovernanceLedger.payload, '$."%s"' % item['key']) == value)
    return query


# ══════════════════════════════════════════════════════════════════════════════
# 086 信访维稳：受理 — 办理 — 办结 两表闭环（主表 + 办理记录子表）
# 结构真源：models/governance_petition.py（m033 建表）
# 权限：沿用 governance 蓝图的 mediation 模块位（utils/permissions.BLUEPRINT_MODULES）
# 脱敏口径：**列表与导出恒打码**；**详情仅 view_sensitive 用户取明文**
#   （contact 不在 app.py after_request 的键后缀名单内，故在路由层显式处理）。
#
# 路由顺序铁律：本块必须定义在 `/<category>/` 动态段之前，
# 否则 /governance/petition/ 会被解析为 category='petition'。
# ══════════════════════════════════════════════════════════════════════════════

# 信访人联系方式 / 身份证号字段级加密（两套列各一组，禁跨模型复用）
_PETITION_SENS_FIELDS = {
    'contact': ('contact_enc', 'contact_hash', None),
    'id_card': ('id_card_enc', 'id_card_hash', None),
}
_pet_decrypt, _pet_set_enc, _pet_clause = make_sensitive_helpers(
    GovernancePetition, _PETITION_SENS_FIELDS, key_holder)

# 页面下拉字典（与 utils/dict_seed 同源）
PETITION_DICT_CATEGORIES = ('信访来源', '信访诉求类别', '信访办理状态', '信访满意度')

# 办理状态机（唯一真源）：待受理 → 办理中 → 已办结；办理中/待受理可转不予受理
PETITION_TRANSITIONS = {
    ('待受理', '办理中'),
    ('待受理', '不予受理'),
    ('办理中', '已办结'),
    ('办理中', '不予受理'),
}


def _iso(value):
    return value.isoformat() if value else ''


def _handle_to_dict(handle):
    return {
        'id': handle.id,
        'petition_id': handle.petition_id,
        'handle_date': _iso(handle.handle_date),
        'handler': handle.handler or '',
        'action': handle.action or '',
        'result': handle.result or '',
        'next_plan': handle.next_plan or '',
        'created_at': handle.created_at.strftime('%Y-%m-%d %H:%M') if handle.created_at else '',
    }


def _petition_to_dict(item, mask=True, with_handles=False):
    """序列化：contact / id_card 打码（app.py after_request 只覆盖 *_phone|*_id_card 键，
    contact 不在其后缀名单内，故列表/详情均在路由层显式打码）。"""
    contact = _pet_decrypt(item, 'contact')
    id_card = _pet_decrypt(item, 'id_card')
    if mask:
        contact, id_card = mask_phone(contact), mask_id_card(id_card)
    data = {
        'id': item.id,
        'petition_no': item.petition_no or '',
        'source': item.source or '',
        'petitioner_name': item.petitioner_name or '',
        'contact': contact,
        'id_card': id_card,
        'address': item.address or '',
        'category': item.category or '',
        'summary': item.summary or '',
        'accept_date': _iso(item.accept_date),
        'due_date': _iso(item.due_date),
        'case_officer': item.case_officer or '',
        'status': item.status or '',
        'close_date': _iso(item.close_date),
        'reply': item.reply or '',
        'satisfaction': item.satisfaction or '',
        'remark': item.remark or '',
        'created_by': item.created_by or '',
    }
    if with_handles:
        data['handles'] = [_handle_to_dict(handle) for handle in item.handles.all()]
    return data


def _mask_petition_detail():
    """详情是否打码：**列表/导出恒打码，详情仅 `view_sensitive` 用户取明文**。

    判定走 `utils.masking.user_can_view_sensitive`（admin role_id=1 或持
    `special.view_sensitive` 位）——脱敏判断的唯一入口，本模块不另写规则。
    无 view_sensitive 的用户拿不到明文联系方式；其对掩码值的原样保存会被
    `_set_enc_guarded` 跳过（不写入打码串）。
    """
    from flask import g
    return not user_can_view_sensitive(g.get('current_user'))


def _set_enc_guarded(setter, item, field, raw):
    """写敏感字段：掩码回显值（含 '*'）不落库，避免把打码串加密成密文。"""
    value = _text(raw)
    if '*' in value:
        return
    setter(item, field, value)


def _petition_query():
    query = GovernancePetition.query
    keyword = _text(request.args.get('keyword'))
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(or_(
            GovernancePetition.petition_no.like(like),
            GovernancePetition.petitioner_name.like(like),
            GovernancePetition.summary.like(like),
            GovernancePetition.address.like(like),
            GovernancePetition.case_officer.like(like),
            GovernancePetition.contact_hash == sha256_hash(keyword),
            GovernancePetition.id_card_hash == sha256_hash(keyword.upper()),
        ))
    for field in ('status', 'category', 'source'):
        want = _text(request.args.get(field))
        if want:
            query = query.filter(getattr(GovernancePetition, field) == want)
    return query


@governance_bp.route('/petition/')
@login_required
def petition_page():
    return render_template('governance/petition.html')


@governance_bp.route('/petition/api/options')
@login_required
def petition_api_options():
    """下拉数据源：来源 / 诉求类别 / 办理状态 / 满意度（字典 label 列表）。"""
    return json_response(True, '', data={
        'dicts': {name: list(get_enum(name)) for name in PETITION_DICT_CATEGORIES},
    })


@governance_bp.route('/petition/api/list')
@login_required
def petition_api_list():
    page = max(request.args.get('page', 1, type=int), 1)
    limit = min(max(request.args.get('limit', 20, type=int), 1), 100)
    query = _petition_query().order_by(GovernancePetition.accept_date.desc(),
                                       GovernancePetition.id.desc())
    items, total, _pages = paginate_query(query, page, limit)
    return json_response(True, '', data=[_petition_to_dict(item) for item in items],
                         total=total)


@governance_bp.route('/petition/api/detail/<int:petition_id>')
@login_required
def petition_api_detail(petition_id):
    item = GovernancePetition.query.get(petition_id)
    if not item:
        return json_response(False, '记录不存在', data=None)
    return json_response(True, '', data=_petition_to_dict(
        item, mask=_mask_petition_detail(), with_handles=True))


@governance_bp.route('/petition/api/save', methods=['POST'])
@login_required
def petition_api_save():
    """信访件保存：新增必填 → 枚举 → 日期格式 → 日期先后 → 编号唯一。

    办理状态只经状态机变更（新增固定「待受理」，编辑仅接受合法迁移），
    防止绕过 /transition 直改状态。
    """
    petition_id = request.form.get('id', 0, type=int)
    item = GovernancePetition.query.get(petition_id) if petition_id else None
    if petition_id and not item:
        return json_response(False, '记录不存在')

    try:
        petition_no = _text(request.form.get('petition_no'))
        petitioner_name = _text(request.form.get('petitioner_name'))
        summary = _text(request.form.get('summary'))
        category = _text(request.form.get('category'))
        source = _text(request.form.get('source'))
        raw_accept_date = _text(request.form.get('accept_date'))

        if not petition_no:
            return json_response(False, '事项编号不能为空')
        if not petitioner_name:
            return json_response(False, '信访人姓名不能为空')
        if not summary:
            return json_response(False, '诉求摘要不能为空')
        if not raw_accept_date:
            return json_response(False, '受理日期不能为空')
        if not category:
            return json_response(False, '诉求类别不能为空')
        if category not in get_enum('信访诉求类别'):
            return json_response(False, '诉求类别取值不合法')
        if not source:
            return json_response(False, '信访来源不能为空')
        if source not in get_enum('信访来源'):
            return json_response(False, '信访来源取值不合法')

        accept_date = _parse_date(raw_accept_date, '受理日期')
        close_date = _parse_date(request.form.get('close_date'), '办结日期')
        due_date = _parse_date(request.form.get('due_date'), '办理期限')

        if close_date and close_date < accept_date:
            return json_response(False, '办结日期不得早于受理日期')

        satisfaction = _text(request.form.get('satisfaction'))
        if satisfaction and satisfaction not in get_enum('信访满意度'):
            return json_response(False, '满意度取值不合法')

        duplicate = GovernancePetition.query.filter(
            GovernancePetition.petition_no == petition_no,
            GovernancePetition.id != (item.id if item else 0)).first()
        if duplicate:
            return json_response(False, '事项编号已存在')

        current_status = _text(item.status) if item else '待受理'
        if not item:
            wanted_status = '待受理'  # 新增一律待受理，状态只经状态机推进
        else:
            wanted_status = _text(request.form.get('status')) or current_status
            if wanted_status not in get_enum('信访办理状态'):
                return json_response(False, '办理状态取值不合法')
            if wanted_status != current_status and \
                    (current_status, wanted_status) not in PETITION_TRANSITIONS:
                return json_response(False,
                                    f'非法状态迁移: {current_status}→{wanted_status}')

        # R-086-02：save 与 transition 共用同一前置条件，禁止经保存接口绕过「办结必须有日期+答复」
        reply = _text(request.form.get('reply'))
        if wanted_status == '已办结' and (not close_date or not reply):
            return json_response(False, '办结前请填写办结日期与答复情况')

        if not item:
            item = GovernancePetition(created_by=session.get('username', '') or None)
            db.session.add(item)

        item.petition_no = petition_no
        item.petitioner_name = petitioner_name
        item.summary = summary
        item.category = category
        item.source = source
        item.accept_date = accept_date
        item.close_date = close_date
        item.due_date = due_date
        item.status = wanted_status
        item.satisfaction = satisfaction
        item.address = _text(request.form.get('address'))
        item.case_officer = _text(request.form.get('case_officer'))
        item.reply = reply
        item.remark = _text(request.form.get('remark'))
        _set_enc_guarded(_pet_set_enc, item, 'contact', request.form.get('contact'))
        _set_enc_guarded(_pet_set_enc, item, 'id_card', request.form.get('id_card'))

        db.session.commit()
    except IntegrityError:
        # 并发下 petition_no 唯一约束兜底（预检为主，唯一索引为终局）
        db.session.rollback()
        return json_response(False, '事项编号已存在')
    except ValueError as exc:
        db.session.rollback()
        return json_response(False, str(exc))
    except Exception as exc:
        db.session.rollback()
        return json_response(False, f'保存失败: {exc}')

    log_operation(session.get('username', ''), 'mediation',
                  'edit' if petition_id else 'add',
                  f'信访件保存: {petition_no}(id={item.id})')
    return json_response(True, '保存成功')


@governance_bp.route('/petition/api/delete/<int:petition_id>', methods=['POST'])
@login_required
def petition_api_delete(petition_id):
    """删除信访件：relationship cascade='all, delete-orphan' 级联删办理记录。"""
    item = GovernancePetition.query.get(petition_id)
    if not item:
        return json_response(False, '记录不存在')
    petition_no = item.petition_no or str(item.id)
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username', ''), 'mediation', 'delete',
                  f'删除信访件: {petition_no}')
    return json_response(True, '已删除')


@governance_bp.route('/petition/api/transition', methods=['POST'])
@login_required
def petition_api_transition():
    """办理状态迁移（唯一入口）：非法迁移返回 HTTP 409。

    R-086-03：迁移表单可携带随迁字段 `close_date` / `reply`（前端把办结日期与答复
    放在迁移弹窗里提交），本次提交有值则随状态一起原子落库；提交值为空则沿用库内值。
    校验全部前置完成后才写库——失败不落库，不留半状态。
    """
    petition_id = request.form.get('id', 0, type=int)
    to_status = _text(request.form.get('to_status'))
    item = GovernancePetition.query.get(petition_id) if petition_id else None
    if not item:
        return json_response(False, '记录不存在')
    if not to_status or to_status not in get_enum('信访办理状态'):
        return json_response(False, '目标状态取值不合法')

    current = _text(item.status) or '待受理'
    if (current, to_status) not in PETITION_TRANSITIONS:
        return jsonify({'success': False,
                        'msg': f'非法状态迁移: {current}→{to_status}'}), 409

    # ── 随迁字段解析（不写库，只算候选值；解析失败即整体失败）──
    raw_close = _text(request.form.get('close_date'))
    raw_reply = _text(request.form.get('reply'))
    close_date = item.close_date
    reply = _text(item.reply)
    try:
        if raw_close:
            close_date = _parse_date(raw_close, '办结日期')
    except ValueError as exc:
        db.session.rollback()
        return json_response(False, str(exc))

    if raw_reply:
        reply = raw_reply

    if to_status == '已办结':
        if not close_date or not reply:
            return json_response(False, '办结前请填写办结日期与答复情况')
        if item.accept_date and close_date < item.accept_date:
            return json_response(False, '办结日期不得早于受理日期')

    # ── 校验通过后才落库（原子：状态 + 随迁字段一次 commit）──
    # 随迁字段只在「已办结」这一终态迁移上落库：其它迁移即使误带 close_date/reply
    # 也不写入，避免把 A 件的办结信息串到 B 件（前端弹窗复用时的陈旧预填）。
    if to_status == '已办结':
        item.close_date = close_date
        item.reply = reply
    item.status = to_status
    db.session.commit()
    log_operation(session.get('username', ''), 'mediation', 'transition',
                  f'信访状态迁移: {item.petition_no} {current}→{to_status}')
    return json_response(True, f'状态已更新为{to_status}', data={
        'status': item.status,
        'close_date': _iso(item.close_date),
        'reply': item.reply or '',
    })


@governance_bp.route('/petition/api/handle-save', methods=['POST'])
@login_required
def petition_api_handle_save():
    """追加办理记录（只增不改）：办理日期与办理措施必填。"""
    petition_id = request.form.get('petition_id', 0, type=int)
    item = GovernancePetition.query.get(petition_id) if petition_id else None
    if not item:
        return json_response(False, '信访件不存在')

    raw_date = _text(request.form.get('handle_date'))
    if not raw_date:
        return json_response(False, '办理日期不能为空')
    try:
        handle_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
    except ValueError:
        return json_response(False, '办理日期格式应为YYYY-MM-DD')

    action = _text(request.form.get('action'))
    if not action:
        return json_response(False, '办理措施不能为空')

    handle = GovernancePetitionHandle(
        petition_id=item.id,
        handle_date=handle_date,
        handler=_text(request.form.get('handler')),
        action=action,
        result=_text(request.form.get('result')),
        next_plan=_text(request.form.get('next_plan')),
    )
    db.session.add(handle)
    db.session.commit()
    log_operation(session.get('username', ''), 'mediation', 'handle-add',
                  f'信访办理记录: {item.petition_no}(id={handle.id})')
    return json_response(True, '办理记录已保存', data={'id': handle.id})


@governance_bp.route('/petition/api/export')
@login_required
def petition_api_export():
    """导出（联系方式/身份证号打码，服务端 openpyxl 生成）。"""
    items = _petition_query().order_by(GovernancePetition.accept_date.desc(),
                                       GovernancePetition.id.desc()).all()
    headers = ['序号', '事项编号', '信访来源', '信访人', '联系方式', '身份证号',
               '住址/涉及地点', '诉求类别', '诉求摘要', '受理日期', '办理期限',
               '承办人', '办理状态', '办结日期', '答复情况', '满意度', '备注']
    rows = []
    for index, item in enumerate(items, 1):
        rows.append([
            index,
            item.petition_no or '',
            item.source or '',
            item.petitioner_name or '',
            mask_phone(_pet_decrypt(item, 'contact')),
            mask_id_card(_pet_decrypt(item, 'id_card')),
            item.address or '',
            item.category or '',
            item.summary or '',
            _iso(item.accept_date),
            _iso(item.due_date),
            item.case_officer or '',
            item.status or '',
            _iso(item.close_date),
            item.reply or '',
            item.satisfaction or '',
            item.remark or '',
        ])
    filename = f"信访事项_{datetime.now().strftime('%Y%m%d')}.xlsx"
    log_operation(session.get('username', ''), 'mediation', 'export',
                  f'导出信访事项（{len(rows)} 行）')
    return export_excel(headers, rows, filename)


@governance_bp.route('/<category>/')
@login_required
def ledger(category):
    config = _config_or_404(category)
    if not config:
        return render_template('errors/404.html'), 404
    return render_template('governance/ledger.html', category=category,
                           config=_resolved_config(config))


@governance_bp.route('/<category>/api/list')
@login_required
def api_list(category):
    config = _config_or_404(category)
    if not config:
        return json_response(False, '台账类型不存在')
    page = max(request.args.get('page', 1, type=int), 1)
    limit = min(max(request.args.get('limit', 20, type=int), 1), 100)
    query = _query(category, config).order_by(GovernanceLedger.record_date.desc(), GovernanceLedger.id.desc())
    items, total, pages = paginate_query(query, page, limit)
    data = [_serialize(item) for item in items]
    return json_response(True, '', data=data, total=total)


@governance_bp.route('/<category>/api/detail/<int:item_id>')
@login_required
def api_detail(category, item_id):
    item = GovernanceLedger.query.filter_by(id=item_id, category=category).first()
    if not item:
        return json_response(False, '记录不存在', data=None)
    return json_response(True, '', data=_serialize(item))


@governance_bp.route('/<category>/api/save', methods=['POST'])
@login_required
def api_save(category):
    config = _config_or_404(category)
    if not config:
        return json_response(False, '台账类型不存在')
    try:
        payload = _payload_from_form(config)
        item_id = request.form.get('id', 0, type=int)
        item = GovernanceLedger.query.filter_by(id=item_id, category=category).first() if item_id else None
        if item_id and not item:
            return json_response(False, '记录不存在')
        action = 'edit' if item else 'add'
        if not item:
            item = GovernanceLedger(category=category, created_by=session.get('username', ''))
            db.session.add(item)
        item.name = _text(request.form.get('name'))
        _set_enc(item, 'id_card', request.form.get('id_card'))
        item.phone = _text(request.form.get('phone'))
        item.record_date = _parse_date(request.form.get('record_date'), '日期')
        item.payload = json.dumps(payload, ensure_ascii=False)
        db.session.commit()
        log_operation(session.get('username', ''), config['title'], action,
                      f'{config["title"]}: {item.name or item.id}')
        return json_response(True, '保存成功')
    except ValueError as exc:
        return json_response(False, str(exc))
    except Exception as exc:
        db.session.rollback()
        return json_response(False, f'保存失败: {exc}')


@governance_bp.route('/<category>/api/delete/<int:item_id>', methods=['POST'])
@login_required
def api_delete(category, item_id):
    config = _config_or_404(category)
    item = GovernanceLedger.query.filter_by(id=item_id, category=category).first()
    if not config or not item:
        return json_response(False, '记录不存在')
    summary = item.name or str(item.id)
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username', ''), config['title'], 'delete', f'删除: {summary}')
    return json_response(True, '删除成功')


@governance_bp.route('/<category>/api/export')
@login_required
def api_export(category):
    config = _config_or_404(category)
    if not config:
        return json_response(False, '台账类型不存在')
    fields = config['fields']
    headers = ['序号'] + [field['label'] for field in fields] + ['录入人', '录入时间']
    rows = []
    for index, item in enumerate(_query(category, config).order_by(
            GovernanceLedger.record_date.desc(), GovernanceLedger.id.desc()).all(), 1):
        data = _serialize(item)
        values = [data.get(field['key'], '') for field in fields]
        rows.append([index] + values + [data['created_by'], data['created_at']])
    filename = f"{config['title']}_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return export_excel(headers, rows, filename)
