from flask import Blueprint, render_template, request, jsonify, send_file
from models import db, MedicalInsurance, Resident, DictCategory, DictItem
from utils.decorators import login_required
from utils.helpers import json_response, paginate_query, log_operation, export_excel
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from utils import medical_field_catalog as mfc
from utils.dict_seed import DICT_SEEDS
from sqlalchemy import or_, text
from datetime import datetime
import io

medical_bp = Blueprint('medical_insurance', __name__)

# R12 字段级加密管线（复用 utils.sensitive_fields，消除医保缴费台账明文孤岛）
_MEDICAL_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'contact_phone': ('contact_phone_enc', 'contact_phone_hash', None),
    'mobile_phone': ('mobile_phone_enc', 'mobile_phone_hash', None),
}
_decrypt, _set_enc, _clause = make_sensitive_helpers(MedicalInsurance, _MEDICAL_SENS_FIELDS, key_holder)


def _resident_plain(resident, field):
    """居民档案敏感字段明文（Resident 已字段级加密，原列 NULL，解密 _enc）。"""
    enc = getattr(resident, field + '_enc', None)
    if enc:
        try:
            return key_holder.decrypt(enc)
        except Exception:
            return ''
    return getattr(resident, field, '') or ''


def _resident_id_hash(resident):
    """居民身份证哈希：优先 id_card_hash，回退明文 sha256（迁移过渡态兼容）。"""
    h = getattr(resident, 'id_card_hash', None)
    if h:
        return h
    raw = _resident_plain(resident, 'id_card')
    return sha256_hash(raw.upper()) if raw else None


# ── ext_data 动态字段读写（054 复核 P2-7：目录声明 sensitive 的字段不得明文落库）──
def _ext_get(ext, key):
    """ext_data 读取：sensitive 字段解密，解密失败返回空（不抛给调用方）。"""
    v = ext.get(key)
    if not v:
        return ''
    if key in mfc.SENSITIVE_EXT_KEYS:
        try:
            return key_holder.decrypt(v)
        except Exception:
            return ''
    return v


def _merge_ext(ext, getter):
    """把动态字段并入 ext_data；getter(key) 取原始值。空值删除该键，sensitive 字段加密。"""
    for k in mfc.EXT_DATA_KEYS:
        v = getter(k)
        v = v.strip() if isinstance(v, str) else v
        if not v:
            ext.pop(k, None)
        elif k in mfc.SENSITIVE_EXT_KEYS:
            ext[k] = key_holder.encrypt(v)
        else:
            ext[k] = v
    return ext

from utils.medical_field_catalog import YES_NO
DEFAULT_INSURANCE_STATUS = list(DICT_SEEDS['医保参保情况'])
DEFAULT_STATE_IDENTITIES = ['\u4f4e\u4fdd\u5bf9\u8c61', '\u7279\u56f0\u4f9b\u517b', '\u91cd\u5ea6\u6b8b\u75be', '\u8131\u8d2b\u4eba\u53e3', '\u76d1\u6d4b\u5bf9\u8c61', '\u5176\u4ed6']
DEFAULT_DEDUCTION_REASONS = ['\u6b7b\u4ea1', '\u8fc1\u51fa', '\u91cd\u590d\u53c2\u4fdd', '\u5f02\u5730\u53c2\u4fdd', '\u670d\u5175\u5f79', '\u670d\u5211', '\u5176\u4ed6']
DEFAULT_EMPLOYEE_STATUS = ['\u5728\u804c', '\u9000\u4f11', '\u505c\u4fdd', '\u5931\u4e1a']
DATA_SOURCES = ['\u533b\u4fdd\u4e0b\u53d1', '\u5c45\u6c11\u5e93\u8865\u5f55', '\u624b\u5de5\u65b0\u589e']
LINK_STATUSES = ['\u5df2\u5173\u8054\u5c45\u6c11', '\u672a\u5173\u8054\u5c45\u6c11', '\u8eab\u4efd\u8bc1\u4e0d\u4e00\u81f4', '\u59d3\u540d\u4e0d\u4e00\u81f4']
VERIFY_STATUSES = ['\u672a\u6838\u5bf9', '\u5df2\u6838\u5bf9', '\u6709\u7591\u95ee']
BENEFIT_CERT_STATUSES = list(DICT_SEEDS['医保待遇认证状态'])
COL_ADDS = {
    'data_source': 'VARCHAR(30) DEFAULT "\u533b\u4fdd\u4e0b\u53d1"',
    'resident_id': 'INTEGER',
    'link_status': 'VARCHAR(30) DEFAULT "\u672a\u5173\u8054\u5c45\u6c11"',
    'import_batch': 'VARCHAR(100)',
    'verify_status': 'VARCHAR(30) DEFAULT "\u672a\u6838\u5bf9"',
    'benefit_cert_status': 'VARCHAR(30) DEFAULT "未认证"',
    'exception_note': 'VARCHAR(300)',
    # 054-B：三分类键 + 动态字段 JSON（字段目录驱动）
    'insurance_type': 'VARCHAR(20) DEFAULT "resident"',
    'ext_data': 'TEXT',
}


def _ensure_columns():
    """P2-4 整改：复用 utils.schema_ensure 幂等补列（消除散落 PRAGMA+ALTER 样板）。"""
    from utils.schema_ensure import ensure_columns, table_columns
    if not table_columns('medical_insurance_payments'):
        db.create_all()
    ensure_columns('medical_insurance_payments', COL_ADDS)


def _clean(v):
    return str(v).strip() if v is not None else ''


def _to_int(v, default=None):
    try:
        if v is None or str(v).strip() == '':
            return default
        return int(float(str(v).strip()))
    except Exception:
        return default


def _find_resident(name='', id_card=''):
    id_card = _clean(id_card)
    name = _clean(name)
    if id_card:
        r = Resident.query.filter(Resident.id_card_hash == sha256_hash(id_card.upper())).first()
        if r:
            return r
    if name:
        return Resident.query.filter(Resident.name == name).first()
    return None


def _fill_from_resident(item, resident):
    if not resident:
        item.link_status = '\u672a\u5173\u8054\u5c45\u6c11'
        return item
    item.resident_id = resident.id
    item.link_status = '\u5df2\u5173\u8054\u5c45\u6c11'
    if item.name and resident.name and item.name != resident.name:
        item.link_status = '\u59d3\u540d\u4e0d\u4e00\u81f4'
    r_id_hash = _resident_id_hash(resident)
    if item.id_card_hash and r_id_hash and item.id_card_hash != r_id_hash:
        item.link_status = '\u8eab\u4efd\u8bc1\u4e0d\u4e00\u81f4'
    item.name = item.name or resident.name
    if not item.id_card_hash:
        _set_enc(item, 'id_card', _resident_plain(resident, 'id_card'))
    item.gender = item.gender or resident.gender
    item.address = item.address or resident.address
    item.village_group = item.village_group or resident.village_group
    r_phone = _resident_plain(resident, 'phone')
    if not item.contact_phone_enc:
        _set_enc(item, 'contact_phone', r_phone)
    if not item.mobile_phone_enc:
        _set_enc(item, 'mobile_phone', r_phone)
    item.household_name = item.household_name or resident.household_name
    item.household_property = item.household_property or resident.household_property
    return item


def _upsert_item(row, source, batch):
    year = _to_int(row.get('payment_year'), datetime.now().year)
    id_card = _clean(row.get('id_card'))
    name = _clean(row.get('name'))
    item = None
    if id_card:
        item = MedicalInsurance.query.filter_by(payment_year=year, id_card_hash=sha256_hash(id_card.upper())).first()
    if not item and name:
        item = MedicalInsurance.query.filter_by(payment_year=year, name=name).first()
    created = item is None
    item = item or MedicalInsurance()
    item.seq_no = _to_int(row.get('seq_no'), item.seq_no)
    item.name = name or item.name
    _set_enc(item, 'id_card', id_card)
    item.gender = _clean(row.get('gender')) or item.gender
    item.address = _clean(row.get('address')) or item.address
    item.village_group = _clean(row.get('village_group')) or item.village_group
    _set_enc(item, 'contact_phone', _clean(row.get('contact_phone')))
    _set_enc(item, 'mobile_phone', _clean(row.get('mobile_phone')))
    item.payment_year = year
    item.household_name = _clean(row.get('household_name')) or item.household_name
    item.household_property = _clean(row.get('household_property')) or item.household_property
    item.insurance_status = _clean(row.get('insurance_status')) or item.insurance_status or DEFAULT_INSURANCE_STATUS[0]
    item.is_paid = _clean(row.get('is_paid')) or item.is_paid or '\u5426'
    item.is_state_paid = _clean(row.get('is_state_paid')) or item.is_state_paid or '\u5426'
    item.state_paid_identity = _clean(row.get('state_paid_identity')) or item.state_paid_identity
    item.is_deductible = _clean(row.get('is_deductible')) or item.is_deductible or '\u5426'
    item.deduction_reason = _clean(row.get('deduction_reason')) or item.deduction_reason
    item.is_relocated = _clean(row.get('is_relocated')) or item.is_relocated or '\u5426'
    item.data_source = source or item.data_source or DATA_SOURCES[0]
    item.import_batch = batch or item.import_batch
    item.verify_status = _clean(row.get('verify_status')) or item.verify_status or VERIFY_STATUSES[0]
    _bcs = _clean(row.get('benefit_cert_status'))
    item.benefit_cert_status = _bcs if _bcs in BENEFIT_CERT_STATUSES else (item.benefit_cert_status or '未认证')
    item.exception_note = _clean(row.get('exception_note')) or item.exception_note
    item.remark = _clean(row.get('remark')) or item.remark
    # 054-B：导入时按目录收集分类键 + 动态字段
    itype = _clean(row.get('insurance_type')) or mfc.DEFAULT_INSURANCE_TYPE
    item.insurance_type = itype if itype in mfc.INSURANCE_TYPE_VALUES else mfc.DEFAULT_INSURANCE_TYPE
    ext = mfc.read_ext_data(item.ext_data)
    item.ext_data = mfc.write_ext_data(_merge_ext(ext, lambda k: _clean(row.get(k))))
    _fill_from_resident(item, _find_resident(item.name, id_card))
    db.session.add(item)
    return created


def _ensure_dict(category, values):
    cat = DictCategory.query.filter_by(name=category).first()
    if not cat:
        cat = DictCategory(name=category, description=category, sort_order=500)
        db.session.add(cat)
        db.session.flush()
    else:
        cat.description = category
    existing = {i.label for i in DictItem.query.filter_by(category_id=cat.id).all()}
    for idx, val in enumerate(values):
        if val not in existing:
            db.session.add(DictItem(category_id=cat.id, label=val, value=val, sort_order=idx))
    db.session.commit()


def _remove_bad_medical_dicts():
    for cat in DictCategory.query.filter(DictCategory.name.like('%?%')).all():
        items = DictItem.query.filter_by(category_id=cat.id).all()
        labels = {i.label for i in items}
        if labels and all('?' in (v or '') for v in labels):
            DictItem.query.filter_by(category_id=cat.id).delete()
            db.session.delete(cat)
    db.session.commit()


def _ensure_medical_dicts():
    _remove_bad_medical_dicts()
    _ensure_dict('\u533b\u4fdd\u53c2\u4fdd\u60c5\u51b5', DEFAULT_INSURANCE_STATUS)
    _ensure_dict('\u533b\u4fdd\u56fd\u5bb6\u4ee3\u7f34\u8eab\u4efd', DEFAULT_STATE_IDENTITIES)
    _ensure_dict('\u533b\u4fdd\u6838\u51cf\u539f\u56e0', DEFAULT_DEDUCTION_REASONS)
    _ensure_dict('\u804c\u5de5\u533b\u4fdd\u72b6\u6001', DEFAULT_EMPLOYEE_STATUS)
    _ensure_dict('\u533b\u4fdd\u6570\u636e\u6765\u6e90', DATA_SOURCES)
    _ensure_dict('\u533b\u4fdd\u5173\u8054\u72b6\u6001', LINK_STATUSES)
    _ensure_dict('\u533b\u4fdd\u6838\u5bf9\u72b6\u6001', VERIFY_STATUSES)
    _ensure_dict('医保待遇认证状态', BENEFIT_CERT_STATUSES)


def _dict_values(category, fallback):
    cat = DictCategory.query.filter_by(name=category).first()
    if not cat:
        return fallback
    vals = [i.value for i in DictItem.query.filter_by(category_id=cat.id, is_active=True).order_by(DictItem.sort_order).all()]
    return vals or fallback


def _serialize(item):
    ext = mfc.read_ext_data(item.ext_data)
    ext_plain = {k: _ext_get(ext, k) for k in mfc.EXT_DATA_KEYS}
    return {
        'id': item.id,
        'seq_no': item.seq_no or '',
        'name': item.name or '',
        'id_card': _decrypt(item, 'id_card'),
        'gender': item.gender or '',
        'address': item.address or '',
        'village_group': item.village_group or '',
        'contact_phone': _decrypt(item, 'contact_phone'),
        'mobile_phone': _decrypt(item, 'mobile_phone'),
        'payment_year': item.payment_year or '',
        'household_name': item.household_name or '',
        'household_property': item.household_property or '',
        'insurance_status': item.insurance_status or '',
        'is_paid': item.is_paid or '\u5426',
        'is_state_paid': item.is_state_paid or '\u5426',
        'state_paid_identity': item.state_paid_identity or '',
        'is_deductible': item.is_deductible or '\u5426',
        'deduction_reason': item.deduction_reason or '',
        'is_relocated': item.is_relocated or '\u5426',
        'data_source': item.data_source or DATA_SOURCES[0],
        'resident_id': item.resident_id or '',
        'link_status': item.link_status or LINK_STATUSES[1],
        'import_batch': item.import_batch or '',
        'verify_status': item.verify_status or VERIFY_STATUSES[0],
        'benefit_cert_status': item.benefit_cert_status or '未认证',
        'exception_note': item.exception_note or '',
        'remark': item.remark or '',
        'insurance_type': item.insurance_type or mfc.DEFAULT_INSURANCE_TYPE,
        # 054-B：职工/其他专属字段扁平化进响应（前端按目录渲染，不写死 key）
        'ext_data': ext_plain,
        **ext_plain,
    }


def _query():
    q = MedicalInsurance.query
    year = request.args.get('payment_year', type=int)
    name = request.args.get('name', '').strip()
    id_card = request.args.get('id_card', '').strip()
    village_group = request.args.get('village_group', '').strip()
    is_paid = request.args.get('is_paid', '').strip()
    is_state_paid = request.args.get('is_state_paid', '').strip()
    is_deductible = request.args.get('is_deductible', '').strip()
    insurance_status = request.args.get('insurance_status', '').strip()
    data_source = request.args.get('data_source', '').strip()
    link_status = request.args.get('link_status', '').strip()
    verify_status = request.args.get('verify_status', '').strip()
    benefit_cert_status = request.args.get('benefit_cert_status', '').strip()
    insurance_type = request.args.get('insurance_type', '').strip()
    keyword = request.args.get('keyword', '').strip()
    if year:
        q = q.filter(MedicalInsurance.payment_year == year)
    if keyword:
        # 2026-11-02 组合检索：姓名模糊 OR 身份证号全号（hash 等值）
        like = f'%{keyword}%'
        q = q.filter(or_(MedicalInsurance.name.like(like),
                         MedicalInsurance.id_card_hash == sha256_hash(keyword.strip().upper())))
    if name:
        q = q.filter(MedicalInsurance.name.like(f'%{name}%'))
    if id_card:
        q = q.filter(MedicalInsurance.id_card_hash == sha256_hash(id_card.strip().upper()))
    if village_group:
        q = q.filter(MedicalInsurance.village_group == village_group)
    if is_paid:
        q = q.filter(MedicalInsurance.is_paid == is_paid)
    if is_state_paid:
        q = q.filter(MedicalInsurance.is_state_paid == is_state_paid)
    if is_deductible:
        q = q.filter(MedicalInsurance.is_deductible == is_deductible)
    if insurance_status:
        q = q.filter(MedicalInsurance.insurance_status == insurance_status)
    if data_source:
        q = q.filter(MedicalInsurance.data_source == data_source)
    if link_status:
        q = q.filter(MedicalInsurance.link_status == link_status)
    if verify_status:
        q = q.filter(MedicalInsurance.verify_status == verify_status)
    if benefit_cert_status:
        q = q.filter(MedicalInsurance.benefit_cert_status == benefit_cert_status)
    if insurance_type:
        q = q.filter(MedicalInsurance.insurance_type == insurance_type)
    return q


@medical_bp.route('/')
@login_required
def list():
    _ensure_columns()
    _ensure_medical_dicts()
    return render_template('medical_insurance/list.html')


@medical_bp.route('/api/options')
@login_required
def api_options():
    _ensure_medical_dicts()
    groups = [r[0] for r in db.session.query(Resident.village_group).filter(Resident.village_group.isnot(None)).distinct().all() if r[0]]
    years = [r[0] for r in db.session.query(MedicalInsurance.payment_year).filter(MedicalInsurance.payment_year.isnot(None)).distinct().order_by(MedicalInsurance.payment_year.desc()).all()]
    current_year = datetime.now().year
    if current_year not in years:
        years.insert(0, current_year)
    return json_response(True, data={
        'yes_no': YES_NO,
        'groups': sorted(set(groups)),
        'years': years,
        'insurance_status': _dict_values('\u533b\u4fdd\u53c2\u4fdd\u60c5\u51b5', DEFAULT_INSURANCE_STATUS),
        'state_paid_identity': _dict_values('\u533b\u4fdd\u56fd\u5bb6\u4ee3\u7f34\u8eab\u4efd', DEFAULT_STATE_IDENTITIES),
        'deduction_reason': _dict_values('\u533b\u4fdd\u6838\u51cf\u539f\u56e0', DEFAULT_DEDUCTION_REASONS),
        'data_sources': _dict_values('\u533b\u4fdd\u6570\u636e\u6765\u6e90', DATA_SOURCES),
        'link_statuses': _dict_values('\u533b\u4fdd\u5173\u8054\u72b6\u6001', LINK_STATUSES),
        'verify_statuses': _dict_values('\u533b\u4fdd\u6838\u5bf9\u72b6\u6001', VERIFY_STATUSES),
        'benefit_cert_statuses': _dict_values('医保待遇认证状态', BENEFIT_CERT_STATUSES),
        # 054-B：三分类 + 字段目录（前端按目录动态渲染表单/表格，不写死字段 key）
        'insurance_types': mfc.INSURANCE_TYPES,
        'field_catalog': {
            'common': mfc.COMMON_FIELDS,
            'types': {t: mfc.TYPE_FIELDS.get(t, []) for t in mfc.INSURANCE_TYPE_VALUES},
            # 完整字段（common + 专属），前端表单/列表/导出统一按它渲染（054 复核 P1-3）
            'fields': {t: mfc.fields_for(t) for t in mfc.INSURANCE_TYPE_VALUES},
            'ext_data_keys': sorted(mfc.EXT_DATA_KEYS),
        },
    })


@medical_bp.route('/api/list')
@login_required
def api_list():
    _ensure_columns()
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    items, total, pages = paginate_query(_query().order_by(MedicalInsurance.id.desc()), page=page, per_page=limit)
    return jsonify({'success': True, 'data': [_serialize(i) for i in items], 'total': total, 'page': page, 'pages': pages})


@medical_bp.route('/api/detail/<int:item_id>')
@login_required
def api_detail(item_id):
    item = MedicalInsurance.query.get(item_id)
    if not item:
        return json_response(False, '\u8bb0\u5f55\u4e0d\u5b58\u5728')
    return json_response(True, data=_serialize(item))


@medical_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    from flask import session
    _ensure_columns()
    item_id = request.form.get('id', 0, type=int)
    item = MedicalInsurance.query.get(item_id) if item_id else MedicalInsurance()
    if item_id and not item:
        return json_response(False, '\u8bb0\u5f55\u4e0d\u5b58\u5728')
    item.seq_no = request.form.get('seq_no', type=int)
    item.name = request.form.get('name', '').strip()
    id_card_raw = request.form.get('id_card', '').strip()
    _set_enc(item, 'id_card', id_card_raw)
    item.gender = request.form.get('gender', '').strip()
    item.address = request.form.get('address', '').strip()
    item.village_group = request.form.get('village_group', '').strip()
    _set_enc(item, 'contact_phone', request.form.get('contact_phone', ''))
    _set_enc(item, 'mobile_phone', request.form.get('mobile_phone', ''))
    item.payment_year = request.form.get('payment_year', type=int) or datetime.now().year
    item.household_name = request.form.get('household_name', '').strip()
    item.household_property = request.form.get('household_property', '').strip()
    item.insurance_status = request.form.get('insurance_status', '').strip()
    item.is_paid = request.form.get('is_paid', '\u5426').strip() or '\u5426'
    item.is_state_paid = request.form.get('is_state_paid', '\u5426').strip() or '\u5426'
    item.state_paid_identity = request.form.get('state_paid_identity', '').strip()
    item.is_deductible = request.form.get('is_deductible', '\u5426').strip() or '\u5426'
    item.deduction_reason = request.form.get('deduction_reason', '').strip()
    item.is_relocated = request.form.get('is_relocated', '\u5426').strip() or '\u5426'
    item.data_source = request.form.get('data_source', DATA_SOURCES[2]).strip() or DATA_SOURCES[2]
    item.import_batch = request.form.get('import_batch', '').strip()
    item.verify_status = request.form.get('verify_status', VERIFY_STATUSES[0]).strip() or VERIFY_STATUSES[0]
    _bcs = request.form.get('benefit_cert_status', '').strip()
    item.benefit_cert_status = _bcs if _bcs in BENEFIT_CERT_STATUSES else (item.benefit_cert_status or '未认证')
    item.exception_note = request.form.get('exception_note', '').strip()
    item.remark = request.form.get('remark', '').strip()
    # 054-B：分类键 + 动态字段（职工/其他专属字段走 ext_data JSON，字段目录驱动）
    itype = request.form.get('insurance_type', '').strip() or mfc.DEFAULT_INSURANCE_TYPE
    item.insurance_type = itype if itype in mfc.INSURANCE_TYPE_VALUES else mfc.DEFAULT_INSURANCE_TYPE
    ext = mfc.read_ext_data(item.ext_data)
    item.ext_data = mfc.write_ext_data(_merge_ext(ext, lambda k: request.form.get(k, '')))
    if not item.name:
        return json_response(False, '\u8bf7\u586b\u5199\u59d3\u540d')
    if not id_card_raw:
        return json_response(False, '\u8bf7\u586b\u5199\u8eab\u4efd\u8bc1\u53f7\u7801')
    _fill_from_resident(item, _find_resident(item.name, id_card_raw))
    db.session.add(item)
    db.session.commit()
    log_operation(session.get('username', ''), '\u533b\u4fdd\u7f34\u8d39', 'edit' if item_id else 'add', f'\u4fdd\u5b58\u533b\u4fdd\u7f34\u8d39: {item.name}')
    return json_response(True, '\u4fdd\u5b58\u6210\u529f', data={'id': item.id})


@medical_bp.route('/api/delete/<int:item_id>', methods=['POST'])
@login_required
def api_delete(item_id):
    item = MedicalInsurance.query.get(item_id)
    if not item:
        return json_response(False, '\u8bb0\u5f55\u4e0d\u5b58\u5728')
    db.session.delete(item)
    db.session.commit()
    return json_response(True, '\u5220\u9664\u6210\u529f')


@medical_bp.route('/api/batch-paid', methods=['POST'])
@login_required
def api_batch_paid():
    from flask import session
    ids = request.form.getlist('ids[]') or request.form.getlist('ids')
    clean_ids = []
    for raw in ids:
        try:
            clean_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not clean_ids:
        return json_response(False, '\u8bf7\u5148\u52fe\u9009\u9700\u8981\u8bbe\u7f6e\u7684\u8bb0\u5f55')
    items = MedicalInsurance.query.filter(MedicalInsurance.id.in_(clean_ids)).all()
    for item in items:
        item.is_paid = '\u662f'
    db.session.commit()
    msg = f'\u5df2\u5c06{len(items)}\u6761\u8bb0\u5f55\u8bbe\u7f6e\u4e3a\u5df2\u7f34\u8d39'
    log_operation(session.get('username', ''), '\u533b\u4fdd\u7f34\u8d39', 'batch_paid', msg)
    return json_response(True, msg, data={'updated': len(items)})


@medical_bp.route('/api/export')
@login_required
def api_export():
    items = _query().order_by(MedicalInsurance.id.desc()).all()
    # 054 复核 P1-3：表头与列值均由字段目录派生（common + 当前分类），不再硬编码居民列
    # 2026-11-16：三分类合并为单表后，「未指定险种」= 全部险种 → 表头取三类字段并集
    # 并前置「险种」列，避免导出全部数据时职工/其他专属字段被整列丢弃。
    itype = request.args.get('insurance_type', '').strip()
    types = [itype] if itype in mfc.INSURANCE_TYPE_VALUES else [t['value'] for t in mfc.INSURANCE_TYPES]
    fields, seen = [], set()
    for t in types:
        for f in mfc.fields_for(t):
            if f['key'] in seen:
                continue
            seen.add(f['key'])
            fields.append(f)
    type_labels = {t['value']: t['label'] for t in mfc.INSURANCE_TYPES}
    headers = ['\u9669\u79cd'] + [f['label'] for f in fields]
    rows = []
    for idx, item in enumerate(items, 1):
        flat = _serialize(item)
        row = [type_labels.get(flat.get('insurance_type'), flat.get('insurance_type', ''))]
        for f in fields:
            v = flat.get(f['key'], '')
            row.append(idx if (f['key'] == 'seq_no' and not v) else v)
        rows.append(row)
    return export_excel(headers, rows, '\u533b\u4fdd\u7f34\u8d39.xlsx')


@medical_bp.route('/api/template')
@login_required
def api_template():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    # 054-B：表头从字段目录派生（新增字段只需改目录，无需改代码）
    itype = request.args.get('insurance_type', '').strip() or mfc.DEFAULT_INSURANCE_TYPE
    if itype not in mfc.INSURANCE_TYPE_VALUES:
        itype = mfc.DEFAULT_INSURANCE_TYPE
    # 054-R2 P2-1：序号已由字段目录 COMMON_FIELDS.seq_no 提供，不再显式前缀，避免模板出现双「序号」
    headers = [f['label'] for f in mfc.fields_for(itype)] + ['\u4fdd\u9669\u7c7b\u578b']
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '\u533b\u4fdd\u5bfc\u5165\u6a21\u677f'
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(1, c)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='2563EB')
        ws.column_dimensions[get_column_letter(c)].width = 18
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='\u533b\u4fdd\u7f34\u8d39\u5bfc\u5165\u6a21\u677f.xlsx')


@medical_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    from flask import session
    _ensure_columns()
    file = request.files.get('file')
    if not file:
        return json_response(False, '\u8bf7\u9009\u62e9\u5bfc\u5165\u6587\u4ef6')
    batch = request.form.get('import_batch', '').strip() or f'{datetime.now().year}\u5e74\u533b\u4fdd\u90e8\u95e8\u4e0b\u53d1\u540d\u5355'
    import openpyxl
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.active
    # 054-B：导入表头从字段目录派生（label → key 映射），兼容旧模板硬编码表头
    itype = request.form.get('insurance_type', '').strip() or mfc.DEFAULT_INSURANCE_TYPE
    if itype not in mfc.INSURANCE_TYPE_VALUES:
        itype = mfc.DEFAULT_INSURANCE_TYPE
    # 054 复核 P1-4：label 映射含旧模板别名；未识别表头告警而非静默丢弃
    label_to_key = mfc.label_to_key_map(itype)
    label_to_key['\u4fdd\u9669\u7c7b\u578b'] = 'insurance_type'
    header_row = [str(c.value).strip() if c.value else '' for c in ws[1]]
    keys = [label_to_key.get(h) for h in header_row]
    if not any(keys):
        # 无表头映射则回退旧顺序（兼容旧无表头/旧模板）
        keys = ['seq_no', 'name', 'id_card', 'gender', 'address', 'village_group', 'contact_phone', 'mobile_phone', 'payment_year', 'household_name', 'household_property', 'insurance_status', 'is_paid', 'is_state_paid', 'state_paid_identity', 'is_deductible', 'deduction_reason', 'is_relocated', 'import_batch', 'verify_status', 'exception_note', 'remark']
        unknown_headers = []
    else:
        unknown_headers = [h for h, k in zip(header_row, keys) if h and not k]
    imported = updated = skipped = 0
    errors = []
    for row_no, values in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        if not values or not any(_clean(v) for v in values):
            continue
        row = {k: values[i] if (k and i < len(values)) else '' for i, k in enumerate(keys) if k}
        row['import_batch'] = _clean(row.get('import_batch')) or batch
        row['insurance_type'] = _clean(row.get('insurance_type')) or itype
        if not _clean(row.get('name')) and not _clean(row.get('id_card')):
            skipped += 1
            errors.append(f'{row_no}: \u7f3a\u5c11\u59d3\u540d\u6216\u8eab\u4efd\u8bc1')
            continue
        try:
            created = _upsert_item(row, DATA_SOURCES[0], row['import_batch'])
            if created:
                imported += 1
            else:
                updated += 1
        except Exception as exc:
            skipped += 1
            errors.append(f'{row_no}: {exc}')
    db.session.commit()
    msg = f'\u5bfc\u5165\u5b8c\u6210\uff0c\u65b0\u589e{imported}\u6761\uff0c\u66f4\u65b0{updated}\u6761\uff0c\u8df3\u8fc7{skipped}\u6761'
    if unknown_headers:
        msg += '\uff1b\u672a\u8bc6\u522b\u8868\u5934\u5df2\u5ffd\u7565\uff1a' + '\u3001'.join(unknown_headers[:5])
    log_operation(session.get('username', ''), '\u533b\u4fdd\u7f34\u8d39', 'import', msg)
    return json_response(True, msg, data={'imported': imported, 'updated': updated, 'skipped': skipped, 'errors': errors[:30], 'unknown_headers': unknown_headers[:20]})


@medical_bp.route('/api/generate-from-residents', methods=['POST'])
@login_required
def api_generate_from_residents():
    from flask import session
    _ensure_columns()
    year = request.form.get('payment_year', type=int) or datetime.now().year
    group = request.form.get('village_group', '').strip()
    q = Resident.query.filter(~Resident.status.in_(['deceased', 'moved_out']))
    if group:
        q = q.filter(Resident.village_group == group)
    created = skipped = 0
    for r in q.all():
        if r.id_card_hash and MedicalInsurance.query.filter_by(payment_year=year, id_card_hash=r.id_card_hash).first():
            skipped += 1
            continue
        item = MedicalInsurance(payment_year=year, name=r.name, data_source=DATA_SOURCES[1], insurance_status=DEFAULT_INSURANCE_STATUS[0], is_paid='\u5426', is_state_paid='\u5426', is_deductible='\u5426', is_relocated='\u5426', verify_status=VERIFY_STATUSES[0])
        _set_enc(item, 'id_card', _resident_plain(r, 'id_card'))
        _fill_from_resident(item, r)
        db.session.add(item)
        created += 1
    db.session.commit()
    msg = f'\u4ece\u5c45\u6c11\u5e93\u8865\u5145\u751f\u6210{created}\u6761\uff0c\u5df2\u5b58\u5728\u8df3\u8fc7{skipped}\u6761'
    log_operation(session.get('username', ''), '\u533b\u4fdd\u7f34\u8d39', 'generate', msg)
    return json_response(True, msg, data={'created': created, 'skipped': skipped})


@medical_bp.route('/api/stats')
@login_required
def api_stats():
    year = request.args.get('payment_year', type=int) or datetime.now().year
    q = MedicalInsurance.query.filter(MedicalInsurance.payment_year == year)
    total = q.count()
    paid = q.filter(MedicalInsurance.is_paid == '\u662f').count()
    unpaid = q.filter(MedicalInsurance.is_paid != '\u662f').count()
    state_paid = q.filter(MedicalInsurance.is_state_paid == '\u662f').count()
    deductible = q.filter(MedicalInsurance.is_deductible == '\u662f').count()
    group_rows = db.session.query(MedicalInsurance.village_group, db.func.count(MedicalInsurance.id), db.func.sum(db.case((MedicalInsurance.is_paid == '\u662f', 1), else_=0))).filter(MedicalInsurance.payment_year == year).group_by(MedicalInsurance.village_group).all()
    groups = []
    for g, cnt, pcnt in group_rows:
        if not g:
            continue
        pcnt = int(pcnt or 0)
        groups.append({'name': g, 'total': cnt, 'paid': pcnt, 'rate': round(pcnt / cnt * 100, 1) if cnt else 0})
    return json_response(True, data={'year': year, 'total': total, 'paid': paid, 'unpaid': unpaid, 'paid_rate': round(paid / total * 100, 1) if total else 0, 'state_paid': state_paid, 'deductible': deductible, 'group_stats': groups})
