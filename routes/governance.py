import json
from datetime import datetime
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import false, func, or_
from sqlalchemy.exc import IntegrityError
from models import db, GovernancePetition, GovernancePetitionHandle
from utils.decorators import login_required
from utils.helpers import export_excel, json_response, log_operation, paginate_query
from utils.crypto import key_holder, sha256_hash
from utils.dict_enum import get_enum
from utils.masking import mask_id_card, mask_phone, user_can_view_sensitive
from utils.sensitive_fields import make_sensitive_helpers

governance_bp = Blueprint('governance', __name__)

from utils.governance_catalog import CATEGORY_CONFIG, GOV_CATEGORY_CODES  # noqa: E402


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
    """Patch C-2：历史 /governance/<category>/ 入口永久 302 跳转至软表 V2 对应工作台。"""
    code = GOV_CATEGORY_CODES.get(category)
    if not code:
        return render_template('errors/404.html'), 404
    # 141：兼容入口同时承担懒触发——确保 8 张固定表在位，并按需补迁历史数据
    # （密钥锁定时自动推迟，不写空值/明文）。
    from utils.governance_v2_migration import ensure_governance_v2_tables
    try:
        ensure_governance_v2_tables(migrate_legacy=True)
    except Exception:  # noqa: BLE001 — 自愈失败不阻断跳转
        db.session.rollback()
    return redirect(url_for('governance.roster_page', code=code), code=302)


# ================================================================== 软表挂载门面（第一批闭环）
from utils.roster_routes import register_roster_routes as _register_roster_routes  # noqa: E402
_register_roster_routes(governance_bp, module_key='governance', group_name='综治管理',
                        log_module='综治管理', nav_module='governance')
