from flask import Blueprint, render_template, request, session, jsonify
from models import db, OrganizationMember
from utils.decorators import login_required
from utils.helpers import paginate_query, json_response, log_operation, export_excel
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from datetime import datetime

org_bp = Blueprint('organization', __name__)

# R12 字段级加密管线（复用 utils.sensitive_fields，消除组织成员台账明文孤岛）
_ORG_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
_decrypt, _set_enc, _clause = make_sensitive_helpers(OrganizationMember, _ORG_SENS_FIELDS, key_holder)


@org_bp.route('/')
@login_required
def list():
    # 075 RC-19：副标题「共 N 人 · 现任 M 人」（is_current 为现任口径）
    total = OrganizationMember.query.count()
    current = OrganizationMember.query.filter(OrganizationMember.is_current == True).count()  # noqa: E712
    return render_template('organization/list.html', head_total=total, head_current=current)


@org_bp.route('/api/list')
@login_required
def api_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    org_type = request.args.get('org_type', '').strip()
    is_current = request.args.get('is_current', '').strip()
    name = request.args.get('name', '').strip()

    query = OrganizationMember.query.order_by(OrganizationMember.is_current.desc(), OrganizationMember.id.desc())

    if org_type:
        query = query.filter(OrganizationMember.org_type == org_type)
    if is_current != '':
        val = True if is_current in ('1', 'true', 'True') else False
        query = query.filter(OrganizationMember.is_current == val)
    if name:
        query = query.filter(OrganizationMember.name.like(f'%{name}%'))

    items, total, pages = paginate_query(query, page, limit)
    data = []
    for m in items:
        data.append({
            'id': m.id,
            'org_type': m.org_type or '',
            'name': m.name or '',
            'position': m.position or '',
            'id_card': _decrypt(m, 'id_card'),
            'phone': _decrypt(m, 'phone'),
            'politics_status': m.politics_status or '',
            'start_date': m.start_date.strftime('%Y-%m-%d') if m.start_date else '',
            'end_date': m.end_date.strftime('%Y-%m-%d') if m.end_date else '',
            'is_current': m.is_current,
        })

    return jsonify({
        'success': True,
        'data': data,
        'total': total,
        'page': page,
        'limit': limit,
        'pages': pages,
    })


@org_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    record_id = request.form.get('id', type=int)
    org_type = request.form.get('org_type', '').strip()
    name = request.form.get('name', '').strip()
    position = request.form.get('position', '').strip()
    id_card = request.form.get('id_card', '').strip()
    phone = request.form.get('phone', '').strip()
    politics_status = request.form.get('politics_status', '').strip()
    start_date_str = request.form.get('start_date', '').strip()
    end_date_str = request.form.get('end_date', '').strip()
    is_current = request.form.get('is_current', '0') in ('1', 'true', 'True')

    start_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            return json_response(False, '开始日期格式错误')

    end_date = None
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return json_response(False, '结束日期格式错误')

    try:
        if record_id:
            record = OrganizationMember.query.get(record_id)
            if not record:
                return json_response(False, '记录不存在')
            record.org_type = org_type
            record.name = name
            record.position = position
            _set_enc(record, 'id_card', id_card)
            _set_enc(record, 'phone', phone)
            record.politics_status = politics_status
            record.start_date = start_date
            record.end_date = end_date
            record.is_current = is_current
            log_operation(session.get('username', ''), '组织管理', '编辑', f'编辑组织成员: {name}')
        else:
            record = OrganizationMember(
                org_type=org_type, name=name, position=position,
                politics_status=politics_status,
                start_date=start_date, end_date=end_date, is_current=is_current,
            )
            _set_enc(record, 'id_card', id_card)
            _set_enc(record, 'phone', phone)
            db.session.add(record)
            log_operation(session.get('username', ''), '组织管理', '新增', f'新增组织成员: {name}')

        db.session.commit()
        return json_response(True, '保存成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')


@org_bp.route('/api/delete/<int:record_id>', methods=['POST'])
@login_required
def api_delete(record_id):
    record = OrganizationMember.query.get(record_id)
    if not record:
        return json_response(False, '记录不存在')
    try:
        name = record.name
        db.session.delete(record)
        db.session.commit()
        log_operation(session.get('username', ''), '组织管理', '删除', f'删除组织成员: {name}')
        return json_response(True, '删除成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'删除失败: {str(e)}')


@org_bp.route('/api/export')
@login_required
def api_export():
    org_type = request.args.get('org_type', '').strip()
    is_current = request.args.get('is_current', '').strip()

    query = OrganizationMember.query.order_by(OrganizationMember.is_current.desc(), OrganizationMember.id.desc())
    if org_type:
        query = query.filter(OrganizationMember.org_type == org_type)
    if is_current != '':
        val = True if is_current in ('1', 'true', 'True') else False
        query = query.filter(OrganizationMember.is_current == val)

    items = query.all()
    headers = ['机构分类', '姓名', '职务', '身份证号', '电话',
               '政治面貌', '任职开始日期', '任职结束日期', '是否现任']
    rows = []
    for m in items:
        rows.append([
            m.org_type or '', m.name or '', m.position or '',
            _decrypt(m, 'id_card'), _decrypt(m, 'phone'), m.politics_status or '',
            m.start_date.strftime('%Y-%m-%d') if m.start_date else '',
            m.end_date.strftime('%Y-%m-%d') if m.end_date else '',
            '是' if m.is_current else '否',
        ])

    return export_excel(headers, rows, filename='组织成员.xlsx')
