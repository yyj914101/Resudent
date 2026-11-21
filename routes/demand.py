# -*- coding: utf-8 -*-
"""routes/demand.py — 090A 民生诉求（demand 新蓝图，登记→办结两跳状态机）。

实现范式复用 086 信访（governance_petition：受理→办理→办结闭环），差异按一页纸
v1.1（docs/design/090A-民生诉求-一页纸字段清单-模板-20261106.md）收敛：
- 无业务单号（主键自增 id）、无办理期限、无答复意见、无满意度；
- 状态机两跳：待受理→已办结 / 待受理→不予受理（非法迁移 409 且不落库）；
- 「不予受理」随迁 remark（须写明依据）；「已办结」随迁 close_date（≥ 受理日期）；
- 枚举口径 096：字典 value 落库 / label 展示（「诉求来源」value 编码见 D-01），
  来源/类别提交值不在字典取值内 → 400 语义失败（V-02）；
- 敏感字段 R12 双列（contact / id_card），列表与导出恒打码，身份证不外显（F-006）；
- 三指标经 MetricsEngine 唯一取数（utils/metrics.py demand_stats / demand_close_rate /
  demand_avg_close_days），/api/stats 代理与统一统计入口、引擎直调三处同值。
"""
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, session
from sqlalchemy.exc import SQLAlchemyError

from models import Demand, DemandHandle, db
from utils.crypto import key_holder, sha256_hash
from utils.decorators import admin_required, login_required
from utils.helpers import json_response, log_operation
from utils.masking import mask_address, mask_name, mask_phone, user_can_view_sensitive
from utils.sensitive_fields import make_sensitive_helpers

demand_bp = Blueprint('demand', __name__)

# ── 状态机（唯一真源；一页纸 v1.1 §四：两跳，非法迁移 409 且不落库）──
DEMAND_TRANSITIONS = {
    ('待受理', '已办结'),
    ('待受理', '不予受理'),
}
DEMAND_STATUSES = ('待受理', '已办结', '不予受理')

# ── 字典分类（options 下发 value/label 对；value 落库、label 展示，096 口径）──
DEMAND_DICT_CATEGORIES = ('诉求来源', '诉求类别', '诉求办理状态')

# ── 敏感字段（R12 双列口径；两套列各一组，禁跨模型复用）──
_DEMAND_SENS_FIELDS = {
    'contact': ('contact_enc', 'contact_hash', None),
    'id_card': ('id_card_enc', 'id_card_hash', None),
}
_demand_decrypt, _demand_set_enc, _demand_clause = make_sensitive_helpers(
    Demand, _DEMAND_SENS_FIELDS, key_holder)


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


def _set_enc_guarded(setter, item, field, raw):
    """写敏感字段：掩码回显值（含 '*'）不落库，避免把打码串加密成密文（086 同款）。"""
    value = _text(raw)
    if '*' in value:
        return
    setter(item, field, value)


def _set_plain_guarded(item, attr, raw):
    """写明文 PII 列（姓名/住址）：掩码回显值（含 '*'）不落库，防打码串覆盖明文。

    仅用于编辑既有记录；新增记录不存在可被覆盖的明文。
    """
    value = _text(raw)
    if '*' in value:
        return
    setattr(item, attr, value)


def _dict_pairs():
    """三分类 [{value, label}]（唯一来源 dict_items，请求级缓存 get_dict_items）。"""
    from utils.helpers import get_dict_items
    return {name: [{'value': i.value, 'label': i.label}
                   for i in get_dict_items(name)]
            for name in DEMAND_DICT_CATEGORIES}


def _label_maps():
    """三分类 value→label 映射（列表/详情/导出展示用）。"""
    return {name: {pair['value']: pair['label'] for pair in pairs}
            for name, pairs in _dict_pairs().items()}


def _validate_dict_value(category, value):
    """V-02：枚举提交值必须在字典取值内（400 语义失败）。"""
    pairs = _dict_pairs().get(category, [])
    if value not in {pair['value'] for pair in pairs}:
        raise ValueError(f'{category}取值不在字典内')


def _demand_query():
    return Demand.query


def _demand_to_dict(item, can_view_sensitive=False, with_plain=False):
    """单条序列化：value→label 展示映射；PII 列恒打码（detail 且有权时给明文）。

    身份证号不外显（F-006 列表/导出 ✗）：仅详情且 view_sensitive 返回。
    """
    labels = _label_maps()
    contact_plain = _demand_decrypt(item, 'contact')
    id_card_plain = _demand_decrypt(item, 'id_card')
    row = {
        'id': item.id,
        'source': item.source,
        'source_label': labels.get('诉求来源', {}).get(item.source, item.source),
        'category': item.category,
        'category_label': labels.get('诉求类别', {}).get(item.category, item.category),
        'status': item.status,
        'status_label': labels.get('诉求办理状态', {}).get(item.status, item.status),
        'accept_date': item.accept_date.strftime('%Y-%m-%d') if item.accept_date else '',
        'close_date': item.close_date.strftime('%Y-%m-%d') if item.close_date else '',
        'case_officer': item.case_officer or '',
        'summary': item.summary or '',
        'remark': item.remark or '',
        'created_by': item.created_by or '',
        # PII-identity / PII-contact：无明文权限恒打码（一页纸 §5）
        'petitioner_name': item.petitioner_name if can_view_sensitive
        else mask_name(item.petitioner_name),
        'contact': contact_plain if can_view_sensitive else mask_phone(contact_plain),
        'address': item.address if can_view_sensitive else mask_address(item.address),
        'has_id_card': bool(id_card_plain),
        'handle_count': item.handles.count(),
    }
    if with_plain and can_view_sensitive:
        row['id_card'] = id_card_plain or ''
    return row


# ── 页面 + 指标代理 ──────────────────────────────────────────────

@demand_bp.route('/')
@login_required
def demand_page():
    """民生诉求门面页（列表 + 登记 + 推进 + 处理记录）。"""
    return render_template('demand/index.html')


@demand_bp.route('/api/stats')
@login_required
def api_stats():
    """指标查询代理：唯一取数入口 MetricsEngine.query（三处同值）。"""
    from utils.metrics import MetricsEngine
    try:
        stats = MetricsEngine.query('demand_stats')
        rate = MetricsEngine.query('demand_close_rate')
        days = MetricsEngine.query('demand_avg_close_days')
    except ValueError as exc:
        return json_response(False, str(exc)), 400
    return json_response(True, '', data={
        'stats': {'total': stats['total'], 'buckets': stats['buckets'],
                  'label': stats['label']},
        'close_rate': rate['data'],
        'avg_close_days': days['data'],
    })


# ── API（静态规则在动态段之前；view 名决定权限动作位，宪法 §11 规则 5）──

@demand_bp.route('/api/options')
@login_required
def api_options():
    """字典三分类（value/label 对）——列表筛选与表单下拉同源。"""
    return json_response(True, '', data=_dict_pairs())


def _apply_demand_filters(query, args):
    """列表/导出共用的筛选（P2-1：导出必须与列表同口径，含受理/办结日期区间）。"""
    status = _text(args.get('status'))
    if status:
        query = query.filter(Demand.status == status)
    source = _text(args.get('source'))
    if source:
        query = query.filter(Demand.source == source)
    category = _text(args.get('category'))
    if category:
        query = query.filter(Demand.category == category)
    keyword = _text(args.get('keyword'))
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(db.or_(Demand.petitioner_name.like(like),
                                    Demand.summary.like(like)))
    accept_from = _parse_date(args.get('accept_from'), '受理日期')
    if accept_from:
        query = query.filter(Demand.accept_date >= accept_from)
    accept_to = _parse_date(args.get('accept_to'), '受理日期')
    if accept_to:
        query = query.filter(Demand.accept_date <= accept_to)
    close_from = _parse_date(args.get('close_from'), '办结日期')
    if close_from:
        query = query.filter(Demand.close_date >= close_from)
    close_to = _parse_date(args.get('close_to'), '办结日期')
    if close_to:
        query = query.filter(Demand.close_date <= close_to)
    return query


@demand_bp.route('/api/list')
@login_required
def api_list():
    """分页列表：状态/来源/类别等值、诉求人/摘要模糊、受理/办结日期区间（一页纸 §5）。

    P1-2：列表 PII 恒打码（明文仅 detail+view_sensitive，对齐 086 口径）。
    P2-4：分页读前端 `limit` 并夹取 1~100，防整表倾卸。
    """
    from utils.helpers import paginate_query
    query = _apply_demand_filters(_demand_query(), request.args)
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    limit = request.args.get('limit', 20, type=int)
    limit = min(max(20 if limit is None else limit, 1), 100)
    items, total, pages = paginate_query(query.order_by(Demand.id.desc()), page, limit)
    return json_response(True, '', data={
        'items': [_demand_to_dict(item, can_view_sensitive=False) for item in items],
        'total': total, 'page': page, 'pages': pages,
    })


def _current_user():
    from models import User
    return User.query.get(session.get('user_id')) if session.get('user_id') else None


@demand_bp.route('/api/detail/<int:demand_id>')
@login_required
def api_detail(demand_id):
    """详情：主表全列 + 处理记录（时间正序）；明文仅 view_sensitive。"""
    item = Demand.query.get(demand_id)
    if not item:
        return json_response(False, '记录不存在')
    user = _current_user()
    can_view = user_can_view_sensitive(user)
    data = _demand_to_dict(item, can_view, with_plain=True)
    data['handles'] = [{
        'id': handle.id,
        'handle_date': handle.handle_date.strftime('%Y-%m-%d') if handle.handle_date else '',
        'handler': handle.handler or '',
        'action': handle.action or '',
    } for handle in item.handles]
    return json_response(True, '', data=data)


def _demand_payload_from_form():
    return {
        'source': _text(request.form.get('source')),
        'category': _text(request.form.get('category')),
        'petitioner_name': _text(request.form.get('petitioner_name')),
        'contact': _text(request.form.get('contact')),
        'id_card': _text(request.form.get('id_card')),
        'address': _text(request.form.get('address')),
        'summary': _text(request.form.get('summary')),
        'accept_date': _text(request.form.get('accept_date')),
        'case_officer': _text(request.form.get('case_officer')),
        'close_date': _text(request.form.get('close_date')),
        'remark': _text(request.form.get('remark')),
    }


@demand_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    """新增（固定「待受理」）/ 编辑；状态仅接受合法迁移（R-086-02 共用前置，防绕行）。"""
    payload = _demand_payload_from_form()
    demand_id = request.form.get('id', type=int) or None
    try:
        # 必填（一页纸 §1：来源/类别/诉求人/摘要/受理日期）
        for field, label in (('source', '诉求来源'), ('category', '诉求类别'),
                             ('petitioner_name', '诉求人姓名'), ('summary', '诉求内容摘要')):
            if not payload[field]:
                raise ValueError(f'请填写{label}')
        # V-02：来源/类别只能取字典值（value 落库）
        _validate_dict_value('诉求来源', payload['source'])
        _validate_dict_value('诉求类别', payload['category'])

        accept_date = _parse_date(payload['accept_date'], '受理日期')
        if not accept_date:
            raise ValueError('请填写受理日期')
        close_date = _parse_date(payload['close_date'], '办结日期')

        item = Demand.query.get(demand_id) if demand_id else None
        if demand_id and not item:
            return json_response(False, '记录不存在')

        # 状态机（R-086-02：save 与 transition 共用同一前置，防绕行）
        current = _text(item.status) if item else '待受理'
        wanted_status = _text(request.form.get('status')) or current
        if wanted_status == current:
            close_date_effective = close_date or (item.close_date if item else None)
        elif (current, wanted_status) not in DEMAND_TRANSITIONS:
            return json_response(False, f'非法状态迁移: {current}→{wanted_status}')
        else:
            close_date_effective = close_date
        if wanted_status == '已办结':
            effective = close_date_effective or (item.close_date if item else None)
            if not effective:
                raise ValueError('办结前请填写办结日期')
            base_accept = accept_date or (item.accept_date if item else None)
            if base_accept and effective < base_accept:
                raise ValueError('办结日期不得早于受理日期')
        if wanted_status == '不予受理':
            # S-02 前置：不予受理须写明依据（与 transition 同口径）
            if not _text(payload['remark']) and not (item and _text(item.remark)):
                raise ValueError('不予受理请填写依据（备注）')

        if item is None:
            item = Demand(source=payload['source'], category=payload['category'],
                          petitioner_name=payload['petitioner_name'],
                          summary=payload['summary'], accept_date=accept_date,
                          status='待受理',  # 新增固定「待受理」（一页纸 F-012）
                          created_by=session.get('username', ''))
            db.session.add(item)
        else:
            item.source = payload['source']
            item.category = payload['category']
            # P1-1：无权用户编辑时姓名/住址为打码串，含 '*' 一律跳写，防静默覆盖明文
            _set_plain_guarded(item, 'petitioner_name', payload['petitioner_name'])
            item.summary = payload['summary']
            item.accept_date = accept_date
            # 状态与随迁字段只在合法迁移时写入（与 transition 同口径）
            if wanted_status != current:
                item.status = wanted_status
                if wanted_status == '已办结':
                    item.close_date = close_date_effective
        item.case_officer = payload['case_officer']
        _set_plain_guarded(item, 'address', payload['address'])
        item.remark = payload['remark']
        _set_enc_guarded(_demand_set_enc, item, 'contact', payload['contact'])
        _set_enc_guarded(_demand_set_enc, item, 'id_card', payload['id_card'])
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return json_response(False, str(exc))
    except SQLAlchemyError as exc:
        db.session.rollback()
        return json_response(False, '保存失败：%s' % exc)
    log_operation(session.get('username', ''), 'demand',
                  'edit' if demand_id else 'add',
                  f'民生诉求保存: id={item.id} {current}→{wanted_status}')
    return json_response(True, '已保存', data={'id': item.id, 'status': item.status})


@demand_bp.route('/api/transition', methods=['POST'])
@login_required
def api_transition():
    """状态流转（两跳）：非法迁移 409 且不落库；随迁字段仅终态迁移写入。"""
    item = Demand.query.get(request.form.get('id', type=int))
    if not item:
        return json_response(False, '记录不存在')
    to_status = _text(request.form.get('to_status'))
    if to_status not in DEMAND_STATUSES:
        return json_response(False, f'目标状态不合法: {to_status}')
    current = _text(item.status) or '待受理'
    if (current, to_status) not in DEMAND_TRANSITIONS:
        return (jsonify({'success': False,
                         'msg': f'非法状态迁移: {current}→{to_status}'}), 409)
    try:
        if to_status == '已办结':
            # S-03：close_date 必填且 ≥ accept_date（随迁字段，仅此迁移写入）
            close_date = _parse_date(request.form.get('close_date'), '办结日期')
            if not close_date:
                return json_response(False, '办结前请填写办结日期')
            if item.accept_date and close_date < item.accept_date:
                return json_response(False, '办结日期不得早于受理日期')
            item.close_date = close_date
        elif to_status == '不予受理':
            # S-02：remark 须写明依据（随迁字段，仅此迁移写入）
            remark = _text(request.form.get('remark'))
            if not remark:
                return json_response(False, '不予受理请填写依据（备注）')
            item.remark = remark
        else:
            return (jsonify({'success': False,
                             'msg': f'非法状态迁移: {current}→{to_status}'}), 409)
        item.status = to_status
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return json_response(False, str(exc))
    except SQLAlchemyError as exc:
        db.session.rollback()
        return json_response(False, '流转失败：%s' % exc)
    log_operation(session.get('username', ''), 'demand', 'transition',
                  f'民生诉求状态迁移: id={item.id} {current}→{to_status}')
    return json_response(True, '已流转', data={
        'id': item.id, 'status': item.status,
        'close_date': item.close_date.strftime('%Y-%m-%d') if item.close_date else '',
    })


@demand_bp.route('/api/handle-save', methods=['POST'])
@login_required
def api_handle_save():
    """新增处理记录（子表只增不改；V-06 挂靠已存在的诉求单）。"""
    demand_id = request.form.get('demand_id', type=int)
    item = Demand.query.get(demand_id) if demand_id else None
    if not item:
        return json_response(False, '诉求单不存在，无法添加处理记录')
    try:
        handle_date = _parse_date(request.form.get('handle_date'), '处理日期')
        if not handle_date:
            raise ValueError('请填写处理日期')
        handler = _text(request.form.get('handler'))
        if not handler:
            raise ValueError('请填写经办人')
        action = _text(request.form.get('action'))
        if not action:
            raise ValueError('请填写处理措施/内容')
        handle = DemandHandle(demand_id=item.id, handle_date=handle_date,
                              handler=handler, action=action)
        db.session.add(handle)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return json_response(False, str(exc))
    except SQLAlchemyError as exc:
        db.session.rollback()
        return json_response(False, '保存失败：%s' % exc)
    log_operation(session.get('username', ''), 'demand', 'handle-add',
                  f'民生诉求办理记录: id={item.id}(handle={handle.id})')
    return json_response(True, '已保存', data={'id': handle.id})


@demand_bp.route('/api/delete/<int:demand_id>', methods=['POST'])
@login_required
@admin_required
def api_delete(demand_id):
    """删除诉求单（一页纸 §6：仅管理员；子表级联删除）。"""
    item = Demand.query.get(demand_id)
    if not item:
        return json_response(False, '记录不存在')
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username', ''), 'demand', 'delete',
                  f'删除民生诉求: id={demand_id}')
    return json_response(True, '已删除')


@demand_bp.route('/api/export')
@login_required
def api_export():
    """导出 xlsx：列序按一页纸 §5；来源/类别/状态出 label；PII 列恒打码（身份证不外显）。"""
    from utils.helpers import export_excel
    # P2-1：导出复用列表筛选（含受理/办结日期区间），避免「筛后导出仍全表」
    query = _apply_demand_filters(_demand_query(), request.args)

    labels = _label_maps()
    rows = []
    for item in query.order_by(Demand.id.desc()).all():
        contact_plain = _demand_decrypt(item, 'contact')
        rows.append([
            item.accept_date.strftime('%Y-%m-%d') if item.accept_date else '',
            labels.get('诉求来源', {}).get(item.source, item.source),
            labels.get('诉求类别', {}).get(item.category, item.category),
            mask_name(item.petitioner_name),
            mask_phone(contact_plain),
            mask_address(item.address),
            item.summary or '',
            item.case_officer or '',
            labels.get('诉求办理状态', {}).get(item.status, item.status),
            item.close_date.strftime('%Y-%m-%d') if item.close_date else '',
        ])
    headers = ['受理日期', '来源', '类别', '诉求人', '联系方式',
               '住址/涉及地点', '内容摘要', '责任人', '状态', '办结日期']
    log_operation(session.get('username', ''), 'demand', 'export',
                  f'导出民生诉求（{len(rows)} 行）')
    return export_excel(headers, rows,
                        f"民生诉求_{datetime.now().strftime('%Y%m%d')}.xlsx")
