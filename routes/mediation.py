from flask import Blueprint, render_template, request, jsonify, send_from_directory, send_file
from models import db, Mediation
from utils.decorators import login_required
from utils.helpers import json_response, paginate_query, log_operation, export_excel
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from datetime import datetime, date
from sqlalchemy import text
from werkzeug.utils import secure_filename
from config import Config
import builtins
import io
import json
import os
import uuid
import zipfile

mediation_bp = Blueprint('mediation', __name__)
ALLOWED_IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# R12 字段级加密管线（复用 utils.sensitive_fields，消除综治调解台账明文孤岛）
_MEDIATION_SENS_FIELDS = {
    'applicant_id_card': ('applicant_id_card_enc', 'applicant_id_card_hash', None),
    'respondent_id_card': ('respondent_id_card_enc', 'respondent_id_card_hash', None),
}
_decrypt, _set_enc, _clause = make_sensitive_helpers(Mediation, _MEDIATION_SENS_FIELDS, key_holder)


def _ensure_mediation_columns():
    """P2-4 整改：复用 utils.schema_ensure 幂等补列（消除散落 PRAGMA+ALTER 样板）。"""
    from utils.schema_ensure import ensure_columns
    ensure_columns('mediations', {
        'attachments': 'TEXT',
        'applicant_id_card': 'VARCHAR(18)',
        'applicant_village_group': 'VARCHAR(50)',
        'respondent_id_card': 'VARCHAR(18)',
    })


def _allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTS


def _attachment_dir():
    path = os.path.join(Config.UPLOAD_FOLDER, 'mediation')
    os.makedirs(path, exist_ok=True)
    return path


def _load_attachments(item):
    raw = getattr(item, 'attachments', None)
    if not raw:
        return []
    if isinstance(raw, builtins.list):
        return raw
    data = json.loads(raw)
    return data if isinstance(data, builtins.list) else []


def _safe_attachments(item):
    try:
        return _load_attachments(item)
    except Exception:
        return []


def _serialize_item(item):
    attachments = _safe_attachments(item)
    return {
        'id': item.id,
        'case_no': item.case_no,
        'dispute_type': item.dispute_type or '',
        'location': item.location or '',
        'applicant': item.applicant or '',
        'applicant_id_card': _decrypt(item, 'applicant_id_card'),
        'applicant_village_group': getattr(item, 'applicant_village_group', '') or '',
        'respondent': item.respondent or '',
        'respondent_id_card': _decrypt(item, 'respondent_id_card'),
        'people_count': item.people_count or 1,
        'status': item.status or '',
        'start_date': item.start_date.isoformat() if item.start_date else '',
        'end_date': item.end_date.isoformat() if item.end_date else '',
        'mediator': item.mediator or '',
        'mediation_date': item.mediation_date.isoformat() if item.mediation_date else '',
        'content': item.content or '',
        'result': item.result or '',
        'return_reason': item.return_reason or '',
        'attachments': attachments,
        'attachment_count': len(attachments),
    }


def _save_uploaded_images(item):
    attachments = _safe_attachments(item)
    for file in request.files.getlist('attachments'):
        if not file or not file.filename:
            continue
        if not _allowed_image(file.filename):
            raise ValueError('\u9644\u4ef6\u4ec5\u652f\u6301 jpg\u3001png\u3001gif\u3001webp \u56fe\u7247\u683c\u5f0f')
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f'{uuid.uuid4().hex}.{ext}'
        file.save(os.path.join(_attachment_dir(), filename))
        attachments.append({
            'filename': filename,
            'original_name': secure_filename(file.filename) or filename,
            'url': f'/mediation/api/attachment/{filename}',
            'uploaded_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })
    item.attachments = json.dumps(attachments, ensure_ascii=False)


def _parse_date(value, label):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f'{label}\u683c\u5f0f\u4e0d\u6b63\u786e')


def _build_query():
    query = Mediation.query
    status = request.args.get('status', '', type=str).strip()
    applicant = request.args.get('applicant', '', type=str).strip()
    respondent = request.args.get('respondent', '', type=str).strip()
    case_no = request.args.get('case_no', '', type=str).strip()
    dispute_type = request.args.get('dispute_type', '', type=str).strip()
    if status:
        query = query.filter(Mediation.status == status)
    if applicant:
        query = query.filter(Mediation.applicant.like(f'%{applicant}%'))
    if respondent:
        query = query.filter(Mediation.respondent.like(f'%{respondent}%'))
    if case_no:
        query = query.filter(Mediation.case_no.like(f'%{case_no}%'))
    if dispute_type:
        query = query.filter(Mediation.dispute_type.like(f'%{dispute_type}%'))
    return query


@mediation_bp.route('/')
@login_required
def list():
    _ensure_mediation_columns()
    # 075 RC-19：副标题「共 N 件 · 调解中 M 件」
    total = Mediation.query.count()
    mediating = Mediation.query.filter(Mediation.status == 'mediating').count()
    return render_template('mediation/list.html', head_total=total, head_current=mediating)


@mediation_bp.route('/api/list')
@login_required
def api_list():
    _ensure_mediation_columns()
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    items, total, pages = paginate_query(_build_query().order_by(Mediation.id.desc()), page=page, per_page=limit)
    return jsonify({'success': True, 'data': [_serialize_item(item) for item in items], 'total': total, 'page': page, 'pages': pages})


@mediation_bp.route('/api/detail/<int:id>')
@login_required
def api_detail(id):
    _ensure_mediation_columns()
    item = Mediation.query.get(id)
    if not item:
        return json_response(False, '\u8bb0\u5f55\u4e0d\u5b58\u5728')
    return json_response(True, data=_serialize_item(item))


@mediation_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    from flask import session
    try:
        _ensure_mediation_columns()
        item_id = request.form.get('id', 0, type=int)
        start_date = _parse_date(request.form.get('start_date'), '\u5f00\u59cb\u65e5\u671f')
        mediation_date = _parse_date(request.form.get('mediation_date'), '\u8c03\u89e3\u65e5\u671f')
        if item_id:
            item = Mediation.query.get(item_id)
            if not item:
                return json_response(False, '\u8bb0\u5f55\u4e0d\u5b58\u5728')
            action = 'edit'
            summary = f'\u7f16\u8f91\u8c03\u89e3\u6848\u4ef6: {item.case_no}'
        else:
            today_str = datetime.now().strftime('%Y%m%d')
            prefix = f'MT{today_str}'
            last_case = Mediation.query.filter(Mediation.case_no.like(f'{prefix}%')).order_by(Mediation.case_no.desc()).first()
            seq = int(last_case.case_no[-3:]) + 1 if last_case and last_case.case_no else 1
            item = Mediation(case_no=f'{prefix}{seq:03d}', status='mediating')
            db.session.add(item)
            db.session.flush()
            action = 'add'
            summary = f'\u65b0\u589e\u8c03\u89e3\u6848\u4ef6: {item.case_no}'

        item.dispute_type = request.form.get('dispute_type', '').strip()
        item.location = request.form.get('location', '').strip()
        item.applicant = request.form.get('applicant', '').strip()
        _set_enc(item, 'applicant_id_card', request.form.get('applicant_id_card', ''))
        item.applicant_village_group = request.form.get('applicant_village_group', '').strip()
        item.respondent = request.form.get('respondent', '').strip()
        _set_enc(item, 'respondent_id_card', request.form.get('respondent_id_card', ''))
        item.people_count = request.form.get('people_count', 1, type=int) or 1
        item.mediator = request.form.get('mediator', '').strip()
        item.content = request.form.get('content', '').strip()
        item.start_date = start_date or item.start_date or date.today()
        item.mediation_date = mediation_date
        _save_uploaded_images(item)

        db.session.commit()
        log_operation(session.get('username', ''), '\u7efc\u6cbb\u8c03\u89e3', action, summary)
        return json_response(True, '\u4fdd\u5b58\u6210\u529f', {'case_no': item.case_no})
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'\u4fdd\u5b58\u5931\u8d25: {str(e)}')


@mediation_bp.route('/api/close/<int:id>', methods=['POST'])
@login_required
def api_close(id):
    from flask import session
    _ensure_mediation_columns()
    item = Mediation.query.get(id)
    if not item:
        return json_response(False, '\u8bb0\u5f55\u4e0d\u5b58\u5728')
    if item.status != 'mediating':
        return json_response(False, '\u53ea\u6709\u8c03\u89e3\u4e2d\u7684\u6848\u4ef6\u624d\u80fd\u529e\u7ed3')
    result = request.form.get('result', '').strip()
    if not result:
        return json_response(False, '\u8bf7\u586b\u5199\u529e\u7ed3\u7ed3\u679c')
    try:
        item.end_date = _parse_date(request.form.get('end_date'), '\u529e\u7ed3\u65e5\u671f') or date.today()
    except ValueError as e:
        return json_response(False, str(e))
    item.result = result
    item.status = 'closed'
    db.session.commit()
    log_operation(session.get('username', ''), '\u7efc\u6cbb\u8c03\u89e3', 'close', f'\u529e\u7ed3\u8c03\u89e3\u6848\u4ef6: {item.case_no}')
    return json_response(True, '\u5df2\u529e\u7ed3')


@mediation_bp.route('/api/return/<int:id>', methods=['POST'])
@login_required
def api_return(id):
    from flask import session
    _ensure_mediation_columns()
    item = Mediation.query.get(id)
    if not item:
        return json_response(False, '\u8bb0\u5f55\u4e0d\u5b58\u5728')
    if item.status != 'mediating':
        return json_response(False, '\u53ea\u6709\u8c03\u89e3\u4e2d\u7684\u6848\u4ef6\u624d\u80fd\u9000\u56de')
    return_reason = request.form.get('return_reason', '').strip()
    if not return_reason:
        return json_response(False, '\u8bf7\u586b\u5199\u9000\u56de\u539f\u56e0')
    item.return_reason = return_reason
    item.status = 'returned'
    db.session.commit()
    log_operation(session.get('username', ''), '\u7efc\u6cbb\u8c03\u89e3', 'return', f'\u9000\u56de\u8c03\u89e3\u6848\u4ef6: {item.case_no}')
    return json_response(True, '\u5df2\u9000\u56de')


def _excel_bytes(items):
    import openpyxl
    from flask import session
    from models import User
    from utils.masking import mask_export_rows
    from utils.permissions import user_has_special
    status_map = {'mediating': '\u8c03\u89e3\u4e2d', 'closed': '\u5df2\u529e\u7ed3', 'returned': '\u5df2\u9000\u56de'}
    headers = ['\u5e8f\u53f7', '\u6848\u4ef6\u7f16\u53f7', '\u7ea0\u7eb7\u7c7b\u578b', '\u7533\u8bf7\u4eba', '\u8eab\u4efd\u8bc1\u53f7\u7801', '\u6240\u5728\u7ec4', '\u88ab\u7533\u8bf7\u4eba', '\u88ab\u7533\u8bf7\u4eba\u8eab\u4efd\u8bc1\u53f7\u7801', '\u6d89\u53ca\u4eba\u6570', '\u8c03\u89e3\u4eba', '\u5f00\u59cb\u65e5\u671f', '\u72b6\u6001', '\u9644\u4ef6\u6570', '\u4e8b\u53d1\u5730\u70b9', '\u8c03\u89e3\u65e5\u671f', '\u529e\u7ed3\u65e5\u671f', '\u7ea0\u7eb7\u5185\u5bb9', '\u529e\u7ed3\u7ed3\u679c', '\u9000\u56de\u539f\u56e0']
    rows = []
    for i, item in enumerate(items, 1):
        attachments = _safe_attachments(item)
        rows.append([i, item.case_no, item.dispute_type or '', item.applicant or '',
                     _decrypt(item, 'applicant_id_card'),
                     getattr(item, 'applicant_village_group', '') or '',
                     item.respondent or '',
                     _decrypt(item, 'respondent_id_card'),
                     item.people_count or 1, item.mediator or '',
                     item.start_date.isoformat() if item.start_date else '',
                     status_map.get(item.status, item.status), len(attachments),
                     item.location or '',
                     item.mediation_date.isoformat() if item.mediation_date else '',
                     item.end_date.isoformat() if item.end_date else '',
                     item.content or '', item.result or '', item.return_reason or ''])
    # 服务端脱敏唯一拦截点：无 view_sensitive 权限时对身份证列脱敏（复用 mask_export_rows）
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    can_view_sensitive = bool(user and (user.role_id == 1 or user_has_special(user, 'view_sensitive')))
    rows = mask_export_rows(headers, rows, can_view_sensitive)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '\u7efc\u6cbb\u8c03\u89e3'
    ws.append(headers)
    for row in rows:
        ws.append(row)
    widths = [8, 18, 16, 12, 22, 12, 12, 22, 10, 12, 12, 10, 8, 24, 12, 12, 30, 30, 30]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = width
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


@mediation_bp.route('/api/export')
@login_required
def api_export():
    _ensure_mediation_columns()
    items = _build_query().order_by(Mediation.id.desc()).all()
    include_images = request.args.get('include_images', '1') != '0'
    if not include_images:
        data = io.BytesIO(_excel_bytes(items))
        return send_file(
            data,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='\u7efc\u6cbb\u8c03\u89e3.xlsx'
        )

    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('\u7efc\u6cbb\u8c03\u89e3.xlsx', _excel_bytes(items))
        for item in items:
            case_folder = secure_filename(item.case_no or str(item.id)) or str(item.id)
            for att in _safe_attachments(item):
                filename = att.get('filename')
                if not filename:
                    continue
                path = os.path.join(_attachment_dir(), filename)
                if os.path.exists(path):
                    original = secure_filename(att.get('original_name') or filename) or filename
                    zf.write(path, f'\u8c03\u89e3\u56fe\u7247/{case_folder}/{original}')
    output.seek(0)
    return send_file(output, mimetype='application/zip', as_attachment=True, download_name='\u7efc\u6cbb\u8c03\u89e3\u5bfc\u51fa.zip')
