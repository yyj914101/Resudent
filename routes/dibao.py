from flask import Blueprint, render_template, request, session, jsonify, send_file
from models import db, Dibao, Resident
from utils.decorators import login_required
from utils.helpers import paginate_query, json_response, log_operation, export_excel
from utils.tag_backfill import sync_resident_tags
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from datetime import datetime, date

dibao_bp = Blueprint('dibao', __name__)

# R12 字段级加密管线（复用 utils.sensitive_fields；P1-1 同类整改扩展：消除低保台账明文孤岛）
_DIBAO_SENS_FIELDS = {
    'household_id_card': ('household_id_card_enc', 'household_id_card_hash', None),
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'account_holder_id_card': ('account_holder_id_card_enc', 'account_holder_id_card_hash', None),
    'bank_card': ('bank_card_enc', 'bank_card_hash', None),
}
_dibao_decrypt, _dibao_set_enc, _dibao_clause = make_sensitive_helpers(
    Dibao, _DIBAO_SENS_FIELDS, key_holder)


def _dibao_filtered_query(args):
    name = args.get('name', '').strip()
    id_card = args.get('id_card', '').strip()
    dibao_type = args.get('dibao_type', '').strip()
    support_status = args.get('support_status', '').strip()

    query = Dibao.query.order_by(Dibao.id.desc())
    if name:
        query = query.filter(db.or_(Dibao.name.like(f'%{name}%'), Dibao.household_name.like(f'%{name}%')))
    if id_card:
        # P1-1 同类整改：身份证检索改走 sha256 等值（消除明文 LIKE）
        v = id_card.strip().upper()
        query = query.filter(db.or_(
            Dibao.id_card_hash == sha256_hash(v),
            Dibao.household_id_card_hash == sha256_hash(v),
        ))
    if dibao_type:
        query = query.filter(Dibao.dibao_type == dibao_type)
    if support_status:
        query = query.filter(Dibao.support_status == support_status)
    return query


def _dibao_master_phone(r):
    """主档优先读取联系电话（不向低保表冗余 phone 列，居民主档修改即刻生效）。"""
    res = r.resident
    if res is None and r.id_card_hash:
        res = Resident.query.filter_by(id_card_hash=r.id_card_hash).first()
    if res is None:
        return ''
    if res.phone_enc and key_holder.is_initialized:
        try:
            return key_holder.decrypt(res.phone_enc) or ''
        except Exception:
            return ''
    return res.phone or ''


def _serialize_dibao(r):
    """低保记录序列化（敏感字段走解密；联系电话按主档联动实时读取）。"""
    return {
        'id': r.id, 'region': r.region or '',
        'phone': _dibao_master_phone(r),
        'household_name': r.household_name or '', 'household_id_card': _dibao_decrypt(r, 'household_id_card'),
        'name': r.name or '', 'id_card': _dibao_decrypt(r, 'id_card'),
        'is_head': r.is_head or '', 'relationship': r.relationship or '',
        'start_date': r.start_date.strftime('%Y-%m-%d') if r.start_date else '',
        'change_time': r.change_time.strftime('%Y-%m-%d') if r.change_time else '',
        'dibao_type': r.dibao_type or '', 'sub_category': r.sub_category or '',
        'beneficiary_count': r.beneficiary_count or 1,
        'household_amount': r.household_amount or 0,
        'payment_month': r.payment_month or '',
        'account_holder': r.account_holder or '',
        'account_holder_identity': r.account_holder_identity or '',
        'account_holder_id_card': _dibao_decrypt(r, 'account_holder_id_card'),
        'bank_card': _dibao_decrypt(r, 'bank_card'), 'bank_type': r.bank_type or '',
        'address': r.address or '', 'registered_address': r.registered_address or '',
        'health_status': r.health_status or '', 'disability_type': r.disability_type or '',
        'poverty_registered': r.poverty_registered or '',
        'is_deceased': r.is_deceased or '',
        'support_status': r.support_status or '已认定',
    }


@dibao_bp.route('/')
@login_required
def dibao_list():
    # 075 RC-06：副标题「村名 · 10 类民政对象 · 在册 N 人」，计数走 agg_cache 去重口径
    from utils.agg_cache import get_civil_objects_count
    return render_template('dibao/list.html', civil_headcount=get_civil_objects_count())

@dibao_bp.route('/api/list')
@login_required
def api_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    query = _dibao_filtered_query(request.args)

    items, total, pages = paginate_query(query, page, limit)
    data = [_serialize_dibao(r) for r in items]
    return json_response(success=True, data={
        'data': data, 'total': total, 'pages': pages, 'page': page, 'limit': limit,
    })


@dibao_bp.route('/api/detail/<int:did>')
@login_required
def api_detail(did):
    """Get full detail of a dibao record."""
    r = Dibao.query.get(did)
    if not r:
        return json_response(False, '记录不存在')
    data = _serialize_dibao(r)
    # B13 权限口径：身份证类字段仅在启动口令解锁（key_holder 持钥）后展示，
    # 测试通道/锁定态由前端隐藏字段本体（非置空），unlocked 标志随详情下发。
    data['unlocked'] = key_holder.is_initialized
    return json_response(success=True, data=data)

@dibao_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    form = request.form if request.form else (request.json or {})
    did = form.get('id', type=int)

    def parse_date(v):
        if not v: return None
        try: return datetime.strptime(v.strip(), '%Y-%m-%d').date()
        except Exception: return None

    if did:
        r = Dibao.query.get(did)
        if not r: return json_response(False, '记录不存在')
    else:
        r = Dibao()

    for f in ['region','household_name','name','is_head','relationship',
              'dibao_type','sub_category','payment_month','account_holder','account_holder_identity',
              'bank_type','address','registered_address',
              'health_status','disability_type','poverty_registered','is_deceased','remark',
              'support_status']:
        setattr(r, f, form.get(f, '').strip())
    # 敏感字段走字段级加密（P1-1 同类整改：消除低保台账明文孤岛）
    for f in ['household_id_card', 'id_card', 'account_holder_id_card', 'bank_card']:
        _dibao_set_enc(r, f, form.get(f, ''))
    _ic = (form.get('id_card') or '').strip()
    if _ic and not r.resident_id:
        _m = Resident.query.filter_by(id_card_hash=sha256_hash(_ic.upper())).first()
        if _m:
            r.resident_id = _m.id

    r.beneficiary_count = form.get('beneficiary_count', 1, type=int)
    r.household_amount = form.get('household_amount', 0, type=float)
    r.start_date = parse_date(form.get('start_date'))
    r.change_time = parse_date(form.get('change_time'))

    if not did: db.session.add(r)
    try:
        # T16-2 事件驱动：保存时自动回填「低保」标签（R3-E2，幂等）
        sync_resident_tags(r, 'dibao')
        db.session.commit()
        log_operation(session.get('username',''), '低保管理', 'add' if not did else 'edit', f'保存低保: {r.name}')
        return json_response(True, '保存成功', data={'id': r.id})
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')

@dibao_bp.route('/api/delete/<int:did>', methods=['POST'])
@login_required
def api_delete(did):
    r = Dibao.query.get(did)
    if not r: return json_response(False, '记录不存在')
    try:
        db.session.delete(r); db.session.commit()
        log_operation(session.get('username',''), '低保管理', 'delete', f'删除低保 ID:{did}')
        return json_response(True, '删除成功')
    except Exception:
        db.session.rollback()
        return json_response(False, '删除失败')

@dibao_bp.route('/api/template')
@login_required
def api_template():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    import io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '低保导入模板'
    headers = [
        '所属所在组','户主姓名','户主身份证号','成员姓名','成员身份证号',
        '是否户主','与户主关系','开始享受时间','变更时间','救助类别',
        '救助细类','享受人口数','户救助金额','发放月份','开户人',
        '开户人身份','开户人身份证号','银行账号','开户银行种类',
        '家庭地址','户籍地址','健康状况','残疾类型','建档立卡贫困对象','是否死亡成员'
    ]
    ws.append(headers)
    for ci in range(1, len(headers)+1):
        cell = ws.cell(row=1, column=ci)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(start_color='1a73e8', end_color='1a73e8', fill_type='solid')
        ws.column_dimensions[chr(64+ci)].width = max(12, len(headers[ci-1])*2+2)
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    log_operation(session.get('username',''), '低保管理', 'template', '下载低保导入模板')
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True, download_name='低保导入模板.xlsx')

@dibao_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    file = request.files.get('file')
    if not file: return json_response(False, '请选择文件')
    mode = request.form.get('mode', 'append')

    try:
        from utils.spreadsheet_reader import read_spreadsheet_table
        headers_list, rows = read_spreadsheet_table(file.filename or 'upload.xlsx', file)
    except Exception as e:
        return json_response(False, '文件解析失败: ' + str(e))
    from utils.header_aliases import build_header_index
    col = build_header_index(headers_list, domain='dibao')

    required = ['成员姓名']
    missing = [r for r in required if r not in col]
    if missing:
        return json_response(False, '缺少必要列: ' + ', '.join(missing))

    def gv(name):
        idx = col.get(name)
        if idx is None: return ''
        val = rows[row_idx][idx]
        return str(val).strip() if val is not None else ''

    def pd(v):
        """Parse date from Excel cell - handles datetime, date, float (serial), and various string formats."""
        if v is None: return None
        from datetime import date as dt_date
        # Already a datetime/date object (openpyxl converts Excel serial dates to datetime)
        if isinstance(v, datetime): return v.date()
        if isinstance(v, dt_date): return v
        # Excel serial date number (float like 44927)
        if isinstance(v, (int, float)) and v > 1:
            from datetime import timedelta
            try:
                base = datetime(1899, 12, 30)
                return (base + timedelta(days=int(v))).date()
            except Exception:
                pass
        s = str(v).strip()
        if not s: return None
        # Remove time portion if present (e.g. '2024-01-15 00:00:00')
        s = s.split(' ')[0]
        for fmt in ('%Y-%m-%d','%Y/%m/%d','%Y.%m.%d','%Y年%m月%d日','%Y-%m','%Y/%m','%Y.%m','%Y年%m月'):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.date()
            except Exception:
                pass
        return None

    imported = 0; updated = 0; skipped = 0; errors = []

    for row_idx, row in enumerate(rows):
        if not row or all(c is None for c in row): continue
        name = gv('成员姓名')
        id_card = gv('成员身份证号')
        if not name:
            errors.append(f'第{row_idx+2}行: 成员姓名空')
            continue

        existing = None
        if id_card:
            existing = Dibao.query.filter_by(id_card_hash=sha256_hash(id_card.upper()), name=name).first()

        if existing:
            if mode == 'append':
                skipped += 1; continue

        r = existing if existing and mode == 'overwrite' else Dibao()
        if not existing or mode != 'overwrite':
            db.session.add(r)

        r.region = gv('所属所在组') or gv('所属地区')
        r.household_name = gv('户主姓名')
        _dibao_set_enc(r, 'household_id_card', gv('户主身份证号'))
        r.name = name
        _dibao_set_enc(r, 'id_card', id_card)
        if id_card and not r.resident_id:
            _m = Resident.query.filter_by(id_card_hash=sha256_hash(id_card.upper())).first()
            if _m:
                r.resident_id = _m.id
        r.is_head = gv('是否户主')
        r.relationship = gv('与户主关系')
        r.start_date = pd(gv('开始享受时间'))
        r.change_time = pd(gv('变更时间'))
        r.dibao_type = gv('救助类别')
        r.sub_category = gv('救助细类')
        try: r.beneficiary_count = int(gv('享受人口数')) if gv('享受人口数') else 1
        except Exception: r.beneficiary_count = 1
        try: r.household_amount = float(gv('户救助金额')) if gv('户救助金额') else 0
        except Exception: r.household_amount = 0
        # 发放月份：兼容日期格式输入（如 2024-01-01 -> 2024-01）
        payment_month_raw = gv('发放月份')
        if payment_month_raw and '-' in payment_month_raw:
            parts = payment_month_raw.split('-')
            if len(parts) >= 2:
                payment_month_raw = parts[0] + '-' + parts[1].zfill(2)
        r.payment_month = payment_month_raw
        r.account_holder = gv('开户人')
        r.account_holder_identity = gv('开户人身份')
        _dibao_set_enc(r, 'account_holder_id_card', gv('开户人身份证号'))
        _dibao_set_enc(r, 'bank_card', gv('银行账号'))
        r.bank_type = gv('开户银行种类')
        r.address = gv('家庭地址')
        r.registered_address = gv('户籍地址')
        r.health_status = gv('健康状况')
        r.disability_type = gv('残疾类型')
        r.poverty_registered = gv('建档立卡贫困对象')
        r.is_deceased = gv('是否死亡成员')

        try:
            db.session.flush()
            if existing and mode == 'overwrite': updated += 1
            else: imported += 1
        except Exception as e:
            db.session.rollback()
            errors.append(f'第{row_idx+2}行 ({name}): {str(e)}')

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return json_response(False, '导入失败')

    msg = f'新增{imported}条'
    if updated: msg += f', 更新{updated}条'
    if skipped: msg += f', 跳过{skipped}条'
    if errors:
        msg += '<br>失败' + str(len(errors)) + '条:<br>' + '<br>'.join(errors[:10])

    log_operation(session.get('username',''), '低保管理', 'import', msg)
    return json_response(success=len(errors)==0, msg=msg, data={
        'imported': imported, 'updated': updated, 'skipped': skipped, 'errors': errors[:30],
    })

@dibao_bp.route('/api/export')
@login_required
def api_export():
    items = _dibao_filtered_query(request.args).all()
    headers = ['所属所在组','户主姓名','户主身份证号','成员姓名','成员身份证号','是否户主','与户主关系',
               '开始享受时间','变更时间','救助类别','救助细类','享受人口数','户救助金额','发放月份',
               '开户人','开户人身份','开户人身份证号','银行账号','开户银行种类','家庭地址','户籍地址',
               '健康状况','残疾类型','建档立卡贫困对象','是否死亡成员']
    rows = []
    for r in items:
        rows.append([r.region or '', r.household_name or '', _dibao_decrypt(r, 'household_id_card'),
            r.name or '', _dibao_decrypt(r, 'id_card'), r.is_head or '', r.relationship or '',
            r.start_date.strftime('%Y-%m-%d') if r.start_date else '',
            r.change_time.strftime('%Y-%m-%d') if r.change_time else '',
            r.dibao_type or '', r.sub_category or '',
            r.beneficiary_count or 1, r.household_amount or 0, r.payment_month or '',
            r.account_holder or '', r.account_holder_identity or '', _dibao_decrypt(r, 'account_holder_id_card'),
            _dibao_decrypt(r, 'bank_card'), r.bank_type or '',
            r.address or '', r.registered_address or '',
            r.health_status or '', r.disability_type or '',
            r.poverty_registered or '', r.is_deceased or ''])
    log_operation(session.get('username',''), '低保管理', 'export', f'导出{len(rows)}条')
    return export_excel(headers, rows, '低保数据.xlsx')

# ================================================================== 第三批-下：低保 A1 异步分批导入
from utils.domain_batch import register_domain_batch_routes as _register_domain_batch_routes  # noqa: E402


def _dibao_parse_date(v):
    if v is None or v == '':
        return None
    from datetime import date as dt_date, timedelta
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, dt_date):
        return v
    if isinstance(v, (int, float)) and v > 1:
        try:
            return (datetime(1899, 12, 30) + timedelta(days=int(v))).date()
        except Exception:
            pass
    s = str(v).strip().split(' ')[0]
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y年%m月%d日', '%Y-%m', '%Y/%m'):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None


def _dibao_row_handler(raw_row, ctx):
    def gv(k):
        val = raw_row.get(k)
        return str(val).strip() if val is not None else ''

    if not any(gv(k) for k in raw_row):
        return 'skipped'
    name = gv('成员姓名')
    if not name:
        raise ValueError('成员姓名不能为空')
    id_card = gv('成员身份证号')
    mode = ctx.get('mode') or 'append'
    existing = (Dibao.query.filter_by(id_card_hash=sha256_hash(id_card.upper()), name=name).first()
                if id_card else None)
    if existing and mode == 'append':
        return 'skipped'
    r = existing if (existing and mode == 'overwrite') else Dibao()
    if not existing or mode != 'overwrite':
        db.session.add(r)
    r.region = gv('所属所在组') or gv('所属地区')
    r.household_name = gv('户主姓名')
    _dibao_set_enc(r, 'household_id_card', gv('户主身份证号'))
    r.name = name
    _dibao_set_enc(r, 'id_card', id_card)
    if id_card and not r.resident_id:
        _m = Resident.query.filter_by(id_card_hash=sha256_hash(id_card.upper())).first()
        if _m:
            r.resident_id = _m.id
    r.is_head = gv('是否户主')
    r.relationship = gv('与户主关系')
    r.start_date = _dibao_parse_date(raw_row.get('开始享受时间'))
    r.change_time = _dibao_parse_date(raw_row.get('变更时间'))
    r.dibao_type = gv('救助类别')
    r.sub_category = gv('救助细类')
    try:
        r.beneficiary_count = int(gv('享受人口数')) if gv('享受人口数') else 1
    except Exception:
        r.beneficiary_count = 1
    try:
        r.household_amount = float(gv('户救助金额')) if gv('户救助金额') else 0
    except Exception:
        r.household_amount = 0
    pm = gv('发放月份')
    if pm and '-' in pm:
        parts = pm.split('-')
        if len(parts) >= 2:
            pm = parts[0] + '-' + parts[1].zfill(2)
    r.payment_month = pm
    r.account_holder = gv('开户人')
    r.account_holder_identity = gv('开户人身份')
    _dibao_set_enc(r, 'account_holder_id_card', gv('开户人身份证号'))
    _dibao_set_enc(r, 'bank_card', gv('银行账号'))
    r.bank_type = gv('开户银行种类')
    r.address = gv('家庭地址')
    r.registered_address = gv('户籍地址')
    r.health_status = gv('健康状况')
    r.disability_type = gv('残疾类型')
    r.poverty_registered = gv('建档立卡贫困对象')
    r.is_deceased = gv('是否死亡成员')
    db.session.flush()
    return 'updated' if (existing and mode == 'overwrite') else 'inserted'


_register_domain_batch_routes(dibao_bp, domain='dibao', log_module='低保管理',
                              row_handler=_dibao_row_handler)
