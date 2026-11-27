from flask import Blueprint, render_template, request, session, send_file
from models import db, Veteran, Resident, DictCategory, DictItem
from utils.decorators import login_required, admin_required
from utils.helpers import json_response, paginate_query, log_operation, export_excel
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from utils.dict_enum import get_enum
from datetime import datetime, date
import math

veteran_bp = Blueprint('veteran', __name__, template_folder='../templates')

# R12 字段级加密管线（复用 utils.sensitive_fields，消除退役军人台账明文孤岛）
_VETERAN_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
    'relative_phone': ('relative_phone_enc', 'relative_phone_hash', None),
}
_decrypt, _set_enc, _clause = make_sensitive_helpers(Veteran, _VETERAN_SENS_FIELDS, key_holder)
from utils.masking import mask_id_card, mask_phone  # R12：列表/导出恒打码


# ─── Helpers ───────────────────────────────────────────────────────────

def _calc_service_years(enlist_date, discharge_date):
    """Calculate service years between two dates."""
    if not enlist_date or not discharge_date:
        return None
    try:
        years = discharge_date.year - enlist_date.year
        if (discharge_date.month, discharge_date.day) < (enlist_date.month, enlist_date.day):
            years -= 1
        return max(years, 0)
    except Exception:
        return None


def _parse_date(val):
    if not val:
        return None
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y/%m/%d', '%Y/%m'):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except Exception:
            pass
    return None


def _id_card_to_gender(id_card):
    """Extract gender from ID card (18-digit)."""
    if not id_card or len(id_card) < 18:
        return ''
    try:
        return '男' if int(id_card[16]) % 2 == 1 else '女'
    except Exception:
        return ''


def _id_card_to_birth(id_card):
    """Extract birth date from ID card (18-digit)."""
    if not id_card or len(id_card) < 14:
        return None
    try:
        return datetime.strptime(id_card[6:14], '%Y%m%d').date()
    except Exception:
        return None


def _get_max_serial():
    """Get max serial_no."""
    result = db.session.query(db.func.max(Veteran.serial_no)).scalar()
    return result if result else 0


# ─── Page Routes ──────────────────────────────────────────────────────

@veteran_bp.route('/')
@login_required
def page_list():
    return render_template('veteran/list.html')


# ─── AJAX: List ───────────────────────────────────────────────────────

@veteran_bp.route('/api/list')
@login_required
def api_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    name = request.args.get('name', '').strip()
    id_card = request.args.get('id_card', '').strip()
    service_branch = request.args.get('service_branch', '').strip()
    politics_status = request.args.get('politics_status', '').strip()
    placement_status = request.args.get('placement_status', '').strip()
    key_group_category = request.args.get('key_group_category', '').strip()
    grid = request.args.get('grid', '').strip()
    enlist_date_from = request.args.get('enlist_date_from', '').strip()
    enlist_date_to = request.args.get('enlist_date_to', '').strip()
    discharge_date_from = request.args.get('discharge_date_from', '').strip()
    discharge_date_to = request.args.get('discharge_date_to', '').strip()
    service_years_min = request.args.get('service_years_min', type=int)
    service_years_max = request.args.get('service_years_max', type=int)
    retire_category = request.args.get('retire_category', '').strip()

    query = Veteran.query

    # 075 RC-18：紧凑工具栏单搜索框组合检索（姓名/政治面貌/兵种模糊 + 完整身份证号哈希等值）；
    # 离散筛选参数保留（向后兼容，导出链接等既有调用方不动）
    q = request.args.get('q', '').strip()
    if q:
        conds = [Veteran.name.like(f'%{q}%'),
                 Veteran.politics_status.like(f'%{q}%'),
                 Veteran.service_branch.like(f'%{q}%')]
        probe = q.upper()
        if len(probe) >= 15 and probe[:-1].isdigit() and probe[-1] in '0123456789X':
            conds.append(Veteran.id_card_hash == sha256_hash(probe))
        query = query.filter(db.or_(*conds))

    if name:
        query = query.filter(Veteran.name.like(f'%{name}%'))
    if id_card:
        query = query.filter(Veteran.id_card_hash == sha256_hash(id_card.strip().upper()))
    if service_branch:
        query = query.filter(Veteran.service_branch == service_branch)
    if politics_status:
        query = query.filter(Veteran.politics_status == politics_status)
    if placement_status:
        query = query.filter(Veteran.placement_status == placement_status)
    if retire_category:
        query = query.filter(Veteran.retire_category == retire_category)
    if key_group_category:
        query = query.filter(Veteran.key_group_category == key_group_category)
    if grid:
        query = query.filter(Veteran.grid == grid)
    if enlist_date_from:
        query = query.filter(Veteran.enlist_date >= _parse_date(enlist_date_from))
    if enlist_date_to:
        query = query.filter(Veteran.enlist_date <= _parse_date(enlist_date_to))
    if discharge_date_from:
        query = query.filter(Veteran.discharge_date >= _parse_date(discharge_date_from))
    if discharge_date_to:
        query = query.filter(Veteran.discharge_date <= _parse_date(discharge_date_to))
    if service_years_min is not None:
        query = query.filter(Veteran.service_years >= service_years_min)
    if service_years_max is not None:
        query = query.filter(Veteran.service_years <= service_years_max)

    query = query.order_by(Veteran.serial_no.asc(), Veteran.id.asc())
    items, total, pages = paginate_query(query, page, limit)

    data = []
    for v in items:
        data.append({
            'id': v.id,
            'serial_no': v.serial_no,
            'name': v.name or '',
            'id_card': mask_id_card(_decrypt(v, 'id_card')),
            'gender': v.gender or '',
            'nation': v.nation or '',
            'politics_status': v.politics_status or '',
            'education': v.education or '',
            'registered_residence': v.registered_residence or '',
            'grid': v.grid or '',
            'home_address': v.home_address or '',
            'phone': mask_phone(_decrypt(v, 'phone')),
            'relative_name': v.relative_name or '',
            'relative_phone': mask_phone(_decrypt(v, 'relative_phone')),
            'enlist_date': v.enlist_date.strftime('%Y-%m') if v.enlist_date else '',
            'discharge_date': v.discharge_date.strftime('%Y-%m') if v.discharge_date else '',
            'service_years': v.service_years,
            'service_unit': v.service_unit or '',
            'service_branch': v.service_branch or '',
            'service_rank': v.service_rank or '',
            'discharge_cert_no': v.discharge_cert_no or '',
            'household_reg_type': v.household_reg_type or '',
            'placement_status': v.placement_status or '',
            'retire_category': v.retire_category or '',
            'key_group_category': v.key_group_category or '',
            'remark': v.remark or '',
            'created_at': v.created_at.strftime('%Y-%m-%d %H:%M') if v.created_at else '',
        })

    return json_response(success=True, data={
        'data': data, 'total': total, 'pages': pages, 'page': page, 'limit': limit
    })


# ─── AJAX: Detail ─────────────────────────────────────────────────────

@veteran_bp.route('/api/detail/<int:vid>')
@login_required
def api_detail(vid):
    v = Veteran.query.get(vid)
    if not v:
        return json_response(False, '记录不存在')

    return json_response(success=True, data={
        'id': v.id,
        'serial_no': v.serial_no,
        'name': v.name or '',
        'id_card': _decrypt(v, 'id_card'),
        'gender': v.gender or '',
        'nation': v.nation or '',
        'politics_status': v.politics_status or '',
        'education': v.education or '',
        'registered_residence': v.registered_residence or '',
        'grid': v.grid or '',
        'home_address': v.home_address or '',
        'phone': _decrypt(v, 'phone'),
        'relative_name': v.relative_name or '',
        'relative_phone': _decrypt(v, 'relative_phone'),
        'enlist_date': v.enlist_date.strftime('%Y-%m') if v.enlist_date else '',
        'discharge_date': v.discharge_date.strftime('%Y-%m') if v.discharge_date else '',
        'service_years': v.service_years,
        'service_unit': v.service_unit or '',
        'service_branch': v.service_branch or '',
        'service_rank': v.service_rank or '',
        'discharge_cert_no': v.discharge_cert_no or '',
        'household_reg_type': v.household_reg_type or '',
        'placement_status': v.placement_status or '',
        'retire_category': v.retire_category or '',
        'key_group_category': v.key_group_category or '',
        'remark': v.remark or '',
    })


# ─── AJAX: Save ───────────────────────────────────────────────────────

@veteran_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    form = request.form if request.form else (request.json or {})
    vid = form.get('id', type=int)

    name = form.get('name', '').strip()
    id_card = form.get('id_card', '').strip()

    if not name:
        return json_response(False, '姓名不能为空')
    if not id_card:
        return json_response(False, '身份证号码不能为空')
    if len(id_card) != 18 or (not id_card[:-1].isdigit() and id_card[-1].upper() not in '0123456789X'):
        return json_response(False, '身份证号码必须为18位')
    if not form.get('registered_residence', '').strip():
        return json_response(False, '户口所在地不能为空')
    if not form.get('home_address', '').strip():
        return json_response(False, '家庭详细地址不能为空')
    if not form.get('phone', '').strip():
        return json_response(False, '本人联系方式不能为空')
    phone = form.get('phone', '').strip()
    if not phone.isdigit() or len(phone) < 7:
        return json_response(False, '请输入有效的手机号码')
    if not form.get('relative_name', '').strip():
        return json_response(False, '主要亲属姓名不能为空')
    rel_phone = form.get('relative_phone', '').strip()
    if not rel_phone:
        return json_response(False, '主要亲属联系方式不能为空')
    if not rel_phone.isdigit() or len(rel_phone) < 7:
        return json_response(False, '请输入有效的亲属手机号码')
    if not form.get('enlist_date', '').strip():
        return json_response(False, '入伍年月不能为空')
    if not form.get('discharge_date', '').strip():
        return json_response(False, '退役年月不能为空')
    if not form.get('service_unit', '').strip():
        return json_response(False, '原服役部队不能为空')
    if not form.get('service_rank', '').strip():
        return json_response(False, '原服役职务/军衔不能为空')
    if not form.get('discharge_cert_no', '').strip():
        return json_response(False, '退伍证件编号不能为空')

    # 093 B-8：退役类别为独立字段，取值以字典为真源（空值允许；非法值零写库拒绝）
    retire_category = form.get('retire_category', '').strip()
    if retire_category and retire_category not in get_enum('退役类别'):
        return json_response(False, '退役类别取值不合法')

    try:
        if vid:
            v = Veteran.query.get(vid)
            if not v:
                return json_response(False, '记录不存在')
        else:
            v = Veteran()
            v.serial_no = _get_max_serial() + 1
            db.session.add(v)

        v.name = name
        _set_enc(v, 'id_card', id_card)
        v.gender = form.get('gender', '').strip() or _id_card_to_gender(id_card)
        v.nation = form.get('nation', '汉族').strip()
        v.politics_status = form.get('politics_status', '').strip()
        v.education = form.get('education', '').strip()
        v.registered_residence = form.get('registered_residence', '').strip()
        v.grid = form.get('grid', '').strip()
        v.home_address = form.get('home_address', '').strip()
        _set_enc(v, 'phone', phone)
        v.relative_name = form.get('relative_name', '').strip()
        _set_enc(v, 'relative_phone', rel_phone)

        enlist_date = _parse_date(form.get('enlist_date'))
        discharge_date = _parse_date(form.get('discharge_date'))
        v.enlist_date = enlist_date
        v.discharge_date = discharge_date

        # Auto-calculate service years
        raw_service_years = form.get('service_years', type=int)
        if raw_service_years is not None:
            v.service_years = raw_service_years
        else:
            v.service_years = _calc_service_years(enlist_date, discharge_date)

        v.service_unit = form.get('service_unit', '').strip()
        v.service_branch = form.get('service_branch', '').strip()
        v.service_rank = form.get('service_rank', '').strip()
        v.discharge_cert_no = form.get('discharge_cert_no', '').strip()
        v.household_reg_type = form.get('household_reg_type', '').strip()
        v.placement_status = form.get('placement_status', '').strip()
        v.retire_category = retire_category
        v.key_group_category = form.get('key_group_category', '').strip()
        v.remark = form.get('remark', '').strip()

        db.session.commit()
        action = 'edit' if vid else 'add'
        log_operation(
            user_name=session.get('username', ''),
            module='退伍军人管理',
            action=action,
            summary=f"{'编辑' if vid else '新增'}退伍军人: {v.name}"
        )
        return json_response(True, '保存成功', data={'id': v.id})
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')


# ─── AJAX: Delete ─────────────────────────────────────────────────────

@veteran_bp.route('/api/delete/<int:vid>', methods=['POST'])
@login_required
def api_delete(vid):
    v = Veteran.query.get(vid)
    if not v:
        return json_response(False, '记录不存在')
    name = v.name
    try:
        db.session.delete(v)
        db.session.commit()
        log_operation(
            user_name=session.get('username', ''),
            module='退伍军人管理',
            action='delete',
            summary=f'删除退伍军人: {name}'
        )
        return json_response(True, '删除成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'删除失败: {str(e)}')


# ─── AJAX: Resident Lookup ────────────────────────────────────────────

@veteran_bp.route('/api/lookup')
@login_required
def api_lookup():
    """Look up resident by name or id_card for auto-fill."""
    name = request.args.get('name', '').strip()
    id_card = request.args.get('id_card', '').strip()
    # Allow searching by name fragment
    if not name:
        return json_response(success=True, data=[])
    q = Resident.query.filter(Resident.name.like(f'%{name}%'))
    items = q.limit(10).all()
    data = []
    for r in items:
        data.append({
            'id': r.id, 'name': r.name or '',
            'id_card': (key_holder.decrypt(r.id_card_enc) if getattr(r, 'id_card_enc', None) else ''),
            'gender': r.gender or '', 'nation': r.nation or '',
            'phone': (key_holder.decrypt(r.phone_enc) if getattr(r, 'phone_enc', None) else ''),
            'address': r.address or '',
        })
    return json_response(success=True, data=data)


# ─── AJAX: Export ─────────────────────────────────────────────────────

@veteran_bp.route('/api/export')
@login_required
def api_export():
    name = request.args.get('name', '').strip()
    id_card = request.args.get('id_card', '').strip()
    service_branch = request.args.get('service_branch', '').strip()
    politics_status = request.args.get('politics_status', '').strip()
    placement_status = request.args.get('placement_status', '').strip()
    key_group_category = request.args.get('key_group_category', '').strip()
    grid = request.args.get('grid', '').strip()
    enlist_date_from = request.args.get('enlist_date_from', '').strip()
    enlist_date_to = request.args.get('enlist_date_to', '').strip()
    discharge_date_from = request.args.get('discharge_date_from', '').strip()
    discharge_date_to = request.args.get('discharge_date_to', '').strip()
    service_years_min = request.args.get('service_years_min', type=int)
    service_years_max = request.args.get('service_years_max', type=int)
    retire_category = request.args.get('retire_category', '').strip()

    query = Veteran.query
    if name: query = query.filter(Veteran.name.like(f'%{name}%'))
    if id_card: query = query.filter(Veteran.id_card_hash == sha256_hash(id_card.strip().upper()))
    if service_branch: query = query.filter(Veteran.service_branch == service_branch)
    if politics_status: query = query.filter(Veteran.politics_status == politics_status)
    if placement_status: query = query.filter(Veteran.placement_status == placement_status)
    if retire_category: query = query.filter(Veteran.retire_category == retire_category)
    if key_group_category: query = query.filter(Veteran.key_group_category == key_group_category)
    if grid: query = query.filter(Veteran.grid == grid)
    if enlist_date_from: query = query.filter(Veteran.enlist_date >= _parse_date(enlist_date_from))
    if enlist_date_to: query = query.filter(Veteran.enlist_date <= _parse_date(enlist_date_to))
    if discharge_date_from: query = query.filter(Veteran.discharge_date >= _parse_date(discharge_date_from))
    if discharge_date_to: query = query.filter(Veteran.discharge_date <= _parse_date(discharge_date_to))
    if service_years_min is not None: query = query.filter(Veteran.service_years >= service_years_min)
    if service_years_max is not None: query = query.filter(Veteran.service_years <= service_years_max)

    query = query.order_by(Veteran.serial_no.asc(), Veteran.id.asc())
    items = query.all()

    headers = [
        '序号', '姓名', '身份证号码', '性别', '民族', '政治面貌', '文化程度',
        '户口所在地', '所在网格', '家庭详细地址', '本人联系方式', '主要亲属姓名', '主要亲属联系方式',
        '入伍年月', '退役年月', '服役年限', '原服役部队', '原服役兵种',
        '原服役职务/军衔', '退伍证件编号', '户籍性质', '安置情况', '重点群体类别', '备注',
        '退役类别'
    ]
    rows = []
    for v in items:
        rows.append([
            v.serial_no or '',
            v.name or '',
            mask_id_card(_decrypt(v, 'id_card')),
            v.gender or '',
            v.nation or '',
            v.politics_status or '',
            v.education or '',
            v.registered_residence or '',
            v.grid or '',
            v.home_address or '',
            mask_phone(_decrypt(v, 'phone')),
            v.relative_name or '',
            mask_phone(_decrypt(v, 'relative_phone')),
            v.enlist_date.strftime('%Y-%m') if v.enlist_date else '',
            v.discharge_date.strftime('%Y-%m') if v.discharge_date else '',
            v.service_years if v.service_years is not None else '',
            v.service_unit or '',
            v.service_branch or '',
            v.service_rank or '',
            v.discharge_cert_no or '',
            v.household_reg_type or '',
            v.placement_status or '',
            v.key_group_category or '',
            v.remark or '',
            v.retire_category or '',
        ])

    log_operation(
        user_name=session.get('username', ''),
        module='退伍军人管理',
        action='export',
        summary=f'导出退伍军人数据 {len(items)}条'
    )
    return export_excel(headers, rows, '退伍军人信息.xlsx')


# ─── AJAX: Import Template ────────────────────────────────────────────

@veteran_bp.route('/api/template')
@login_required
def api_template():
    from utils.helpers import import_excel_template_year
    field_map = [
        {'label': '姓名', 'key': 'name', 'dict': None, 'required': True},
        {'label': '身份证号码', 'key': 'id_card', 'dict': None, 'required': True},
        {'label': '性别', 'key': 'gender', 'dict': '性别', 'required': False},
        {'label': '民族', 'key': 'nation', 'dict': '民族', 'required': False},
        {'label': '政治面貌', 'key': 'politics_status', 'dict': '政治面貌', 'required': False},
        {'label': '文化程度', 'key': 'education', 'dict': '学历', 'required': False},
        {'label': '户口所在地', 'key': 'registered_residence', 'dict': None, 'required': False},
        {'label': '所在网格', 'key': 'grid', 'dict': '网格', 'required': False},
        {'label': '家庭详细地址', 'key': 'home_address', 'dict': None, 'required': False},
        {'label': '本人联系方式', 'key': 'phone', 'dict': None, 'required': False},
        {'label': '主要亲属姓名', 'key': 'relative_name', 'dict': None, 'required': False},
        {'label': '主要亲属联系方式', 'key': 'relative_phone', 'dict': None, 'required': False},
        {'label': '入伍年月', 'key': 'enlist_date', 'dict': None, 'required': False},
        {'label': '退役年月', 'key': 'discharge_date', 'dict': None, 'required': False},
        {'label': '服役年限', 'key': 'service_years', 'dict': None, 'required': False},
        {'label': '原服役部队', 'key': 'service_unit', 'dict': None, 'required': False},
        {'label': '原服役兵种', 'key': 'service_branch', 'dict': '原服役兵种', 'required': False},
        {'label': '原服役职务/军衔', 'key': 'service_rank', 'dict': None, 'required': False},
        {'label': '退伍证件编号', 'key': 'discharge_cert_no', 'dict': None, 'required': False},
        {'label': '户籍性质', 'key': 'household_reg_type', 'dict': '户籍性质', 'required': False},
        {'label': '安置情况', 'key': 'placement_status', 'dict': '安置情况', 'required': False},
        {'label': '退役类别', 'key': 'retire_category', 'dict': '退役类别', 'required': False},
        {'label': '重点群体类别', 'key': 'key_group_category', 'dict': '退役重点群体', 'required': False},
        {'label': '备注', 'key': 'remark', 'dict': None, 'required': False},
    ]
    return import_excel_template_year(field_map, '退伍军人导入模板.xlsx')


# ─── AJAX: Import ─────────────────────────────────────────────────────

@veteran_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    file = request.files.get('file')
    if not file:
        return json_response(False, '请选择文件')

    mode = request.form.get('mode', 'append')

    try:
        from utils.spreadsheet_reader import read_spreadsheet_table
        headers, _rows = read_spreadsheet_table(file.filename or 'upload.xlsx', file)
        rows_iter = iter(_rows)
    except Exception as e:
        return json_response(False, f'文件解析失败: {str(e)}')
    col_map = {
        'name': '姓名', 'id_card': '身份证号码', 'gender': '性别',
        'nation': '民族', 'politics_status': '政治面貌', 'education': '文化程度',
        'registered_residence': '户口所在地', 'grid': '所在网格', 'home_address': '家庭详细地址',
        'phone': '本人联系方式', 'relative_name': '主要亲属姓名',
        'relative_phone': '主要亲属联系方式', 'enlist_date': '入伍年月',
        'discharge_date': '退役年月', 'service_years': '服役年限',
        'service_unit': '原服役部队', 'service_branch': '原服役兵种',
        'service_rank': '原服役职务/军衔', 'discharge_cert_no': '退伍证件编号',
        'household_reg_type': '户籍性质', 'placement_status': '安置情况',
        'retire_category': '退役类别',
        'key_group_category': '重点群体类别',
        'remark': '备注',
    }
    from utils.header_aliases import build_header_index
    header_idx = build_header_index(headers, domain='veteran')

    def get_val(field_key):
        label = col_map.get(field_key)
        if label is None or label not in header_idx:
            return ''
        val = row[header_idx[label]]
        return str(val).strip() if val is not None else ''

    imported = 0
    updated = 0
    skipped = 0
    errors = []
    max_serial = _get_max_serial()

    for row_idx, raw_row in enumerate(rows_iter, start=2):
        row = list(raw_row)
        if all(cell is None for cell in row):
            continue

        name = get_val('name')
        id_card = get_val('id_card')
        if not name:
            errors.append(f'第{row_idx}行: 姓名不能为空')
            continue
        if not id_card:
            errors.append(f'第{row_idx}行: 身份证号码不能为空')
            continue
        retire_category = get_val('retire_category')
        if retire_category and retire_category not in get_enum('退役类别'):
            errors.append(f'第{row_idx}行: 退役类别取值不合法')
            continue

        existing = Veteran.query.filter_by(id_card_hash=sha256_hash(id_card.upper())).first()
        if existing:
            if mode == 'append':
                skipped += 1
                continue
            elif mode == 'overwrite':
                v = existing
            else:
                skipped += 1
                continue
        else:
            v = Veteran()
            max_serial += 1
            v.serial_no = max_serial
            db.session.add(v)

        v.name = name
        _set_enc(v, 'id_card', id_card)
        v.gender = get_val('gender') or _id_card_to_gender(id_card)
        v.nation = get_val('nation') or '汉族'
        v.politics_status = get_val('politics_status')
        v.education = get_val('education')
        v.registered_residence = get_val('registered_residence')
        v.grid = get_val('grid')
        v.home_address = get_val('home_address')
        _set_enc(v, 'phone', get_val('phone'))
        v.relative_name = get_val('relative_name')
        _set_enc(v, 'relative_phone', get_val('relative_phone'))
        v.enlist_date = _parse_date(get_val('enlist_date'))
        v.discharge_date = _parse_date(get_val('discharge_date'))
        raw_years = get_val('service_years')
        try:
            v.service_years = int(raw_years) if raw_years else _calc_service_years(v.enlist_date, v.discharge_date)
        except Exception:
            v.service_years = _calc_service_years(v.enlist_date, v.discharge_date)
        v.service_unit = get_val('service_unit')
        v.service_branch = get_val('service_branch')
        v.service_rank = get_val('service_rank')
        v.discharge_cert_no = get_val('discharge_cert_no')
        v.household_reg_type = get_val('household_reg_type')
        v.placement_status = get_val('placement_status')
        v.retire_category = retire_category
        v.key_group_category = get_val('key_group_category')
        v.remark = get_val('remark')

        try:
            db.session.flush()
            if existing and mode == 'overwrite':
                updated += 1
            else:
                imported += 1
        except Exception as e:
            db.session.rollback()
            errors.append(f'第{row_idx}行 ({name}): {str(e)}')
            continue

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'导入失败: {str(e)}')

    log_operation(
        user_name=session.get('username', ''),
        module='退伍军人管理',
        action='import',
        summary=f'导入退伍军人 {imported}条新增, {updated}条更新, {skipped}条跳过'
    )

    msg_parts = []
    if imported: msg_parts.append(f'新增 {imported} 条')
    if updated: msg_parts.append(f'覆盖更新 {updated} 条')
    if skipped: msg_parts.append(f'跳过(重复) {skipped} 条')
    msg = '、'.join(msg_parts) if msg_parts else '无变更'
    if errors:
        msg += f'<br>失败 {len(errors)} 条:<br>' + '<br>'.join(errors[:20])
        if len(errors) > 20:
            msg += f'<br>...等共{len(errors)}条错误'

    return json_response(success=(len(errors) == 0), msg=msg, data={
        'imported': imported, 'updated': updated, 'skipped': skipped, 'errors': errors[:50],
    })


