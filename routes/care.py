from flask import Blueprint, render_template, request, session, jsonify, send_file, g
from models import db, SpecialSupport, UnsupportedChild, CareTarget, ElderServiceRecord, Resident, TodoItem
from utils.decorators import login_required
from utils.helpers import paginate_query, json_response, log_operation, export_excel
from utils.masking import child_rows_guard, filter_child_rows
from utils.care_age import classify_age, parse_override
from utils.tag_backfill import sync_resident_tags, invalidate_tag, _tag_for_care_target
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from datetime import datetime, date
# 092 模板附件区：服务收口（三蓝图唯一真源，禁复制实现）
from utils import attachment_service
from utils.attachment_service import attachment_api

care_bp = Blueprint('care', __name__)

# R12 字段级加密管线（复用 utils.sensitive_fields，消除特困/孤儿/关爱台账明文孤岛）
_SPECIAL_SENS_FIELDS = {
    'household_id_card': ('household_id_card_enc', 'household_id_card_hash', None),
    'bank_card': ('bank_card_enc', 'bank_card_hash', None),
    'account_holder_id_card': ('account_holder_id_card_enc', 'account_holder_id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
_special_decrypt, _special_set_enc, _special_clause = make_sensitive_helpers(SpecialSupport, _SPECIAL_SENS_FIELDS, key_holder)

_CHILD_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
_child_decrypt, _child_set_enc, _child_clause = make_sensitive_helpers(UnsupportedChild, _CHILD_SENS_FIELDS, key_holder)

_TARGET_SENS_FIELDS = {'guardian_phone': ('guardian_phone_enc', 'guardian_phone_hash', None)}
_target_decrypt, _target_set_enc, _target_clause = make_sensitive_helpers(CareTarget, _TARGET_SENS_FIELDS, key_holder)


def _serialize_special(r):
    """特困供养序列化（敏感字段走解密）。"""
    return {
        'id': r.id, 'village_group': r.village_group or '',
        'household_name': r.household_name or '',
        'household_id_card': _special_decrypt(r, 'household_id_card'),
        'beneficiary_count': r.beneficiary_count or 1,
        'guarantee_amount': r.guarantee_amount or 0,
        'support_mode': r.support_mode or '',
        'support_institution': r.support_institution or '',
        'start_date': r.start_date,
        'change_time': r.change_time,
        'bank_card': _special_decrypt(r, 'bank_card'),
        'payment_month': r.payment_month or '',
        'account_holder': r.account_holder or '',
        'account_holder_identity': r.account_holder_identity or '',
        'account_holder_id_card': _special_decrypt(r, 'account_holder_id_card'),
        'bank_type': r.bank_type or '',
        'address': r.address or '',
        'registered_address': r.registered_address or '',
        'phone': _special_decrypt(r, 'phone'),
        'self_care_ability': r.self_care_ability or '',
    }


def _serialize_child(r):
    """事实无人抚养儿童序列化（敏感字段走解密；身份证推导性别）。"""
    idc = _child_decrypt(r, 'id_card')
    gender = r.gender or ''
    if not gender and idc and len(idc) >= 18:
        try:
            gender = '男' if int(idc[16]) % 2 == 1 else '女'
        except Exception:
            gender = ''
    return {
        'id': r.id, 'village_group': r.village_group or '',
        'amount': r.amount or 0,
        'name': r.name or '',
        'id_card': idc,
        'gender': gender,
        # GLB-46：详情弹窗渲染出生日期，序列化必须回传（原缺失导致恒显示空）
        'birth_date': r.birth_date.strftime('%Y-%m-%d') if r.birth_date else '',
        'father_status': r.father_status or '',
        'mother_status': r.mother_status or '',
        'health_status': r.health_status or '',
        'cardholder_relation': r.cardholder_relation or '',
        'phone': _child_decrypt(r, 'phone'),
        'address': r.address or '',
    }


def _special_filtered_query(args):
    name = args.get('name', '').strip()
    id_card = args.get('id_card', '').strip()
    query = SpecialSupport.query.order_by(SpecialSupport.id.desc())
    if name:
        query = query.filter(db.or_(SpecialSupport.household_name.like(f'%{name}%'), SpecialSupport.account_holder.like(f'%{name}%')))
    if id_card:
        v = id_card.strip().upper()
        query = query.filter(db.or_(
            SpecialSupport.household_id_card_hash == sha256_hash(v),
            SpecialSupport.account_holder_id_card_hash == sha256_hash(v),
        ))
    return query


def _child_filtered_query(args):
    name = args.get('name', '').strip()
    id_card = args.get('id_card', '').strip()
    query = UnsupportedChild.query.order_by(UnsupportedChild.id.desc())
    if name:
        query = query.filter(UnsupportedChild.name.like(f'%{name}%'))
    if id_card:
        query = query.filter(UnsupportedChild.id_card_hash == sha256_hash(id_card.strip().upper()))
    return query

@care_bp.route('/')
@login_required
def care_list():
    # D-21 C：取消关爱对象入口页，单页 + 3 子类胶囊，默认落特困供养
    # 075 RC-06：副标题「村名 · 10 类民政对象 · 在册 N 人」
    from utils.agg_cache import get_civil_objects_count
    return render_template('care/special_list.html', civil_headcount=get_civil_objects_count())

# ═══════════════════ 特困供养人员 ═══════════════════

@care_bp.route('/special')
@login_required
def special_list():
    from utils.agg_cache import get_civil_objects_count
    return render_template('care/special_list.html', civil_headcount=get_civil_objects_count())

@care_bp.route('/api/special/list')
@login_required
def api_special_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    query = _special_filtered_query(request.args)
    items, total, pages = paginate_query(query, page, limit)
    data = [_serialize_special(r) for r in items]
    for d in data:
        sd = d.get('start_date')
        if isinstance(sd, (datetime, date)):
            d['start_date'] = sd.strftime('%Y/%m/%d')
        ct = d.get('change_time')
        if isinstance(ct, (datetime, date)):
            d['change_time'] = ct.strftime('%Y/%m/%d')
    return json_response(success=True, data={'data': data, 'total': total, 'pages': pages, 'page': page, 'limit': limit})

@care_bp.route('/api/special/detail/<int:did>')
@login_required
def api_special_detail(did):
    r = SpecialSupport.query.get(did)
    if not r: return json_response(False, '记录不存在')
    data = _serialize_special(r)
    sd = data.get('start_date')
    if isinstance(sd, (datetime, date)):
        data['start_date'] = sd.strftime('%Y/%m/%d')
    ct = data.get('change_time')
    if isinstance(ct, (datetime, date)):
        data['change_time'] = ct.strftime('%Y/%m/%d')
    # B13 权限口径：身份证类字段仅启动口令解锁后展示（与 dibao 详情一致）
    data['unlocked'] = key_holder.is_initialized
    return json_response(success=True, data=data)

@care_bp.route('/api/special/save', methods=['POST'])
@login_required
def api_special_save():
    form = request.form; did = form.get('id', type=int)
    r = SpecialSupport.query.get(did) if did else SpecialSupport()
    if not did: db.session.add(r)
    for f in ['village_group','household_name','support_mode','support_institution','payment_month','account_holder','account_holder_identity','bank_type','address','registered_address','self_care_ability']:
        setattr(r, f, form.get(f, '').strip())
    for f in ['household_id_card', 'bank_card', 'account_holder_id_card', 'phone']:
        _special_set_enc(r, f, form.get(f, ''))
    r.beneficiary_count = form.get('beneficiary_count', 1, type=int)
    r.guarantee_amount = form.get('guarantee_amount', 0, type=float)
    try: r.start_date = datetime.strptime(form.get('start_date').strip(), '%Y-%m-%d').date() if form.get('start_date') else None
    except Exception: r.start_date = None
    try: r.change_time = datetime.strptime(form.get('change_time').strip(), '%Y-%m-%d').date() if form.get('change_time') else None
    except Exception: r.change_time = None
    try:
        # T16-2 事件驱动：保存时自动回填「特困供养」标签（R3-E2，household_id_card hash 匹配，幂等）
        sync_resident_tags(r, 'special_support')
        db.session.commit(); log_operation(session.get('username',''), '特困供养', 'save', f'保存: {r.household_name}'); return json_response(True, '保存成功', data={'id': r.id})
    except Exception as e: db.session.rollback(); return json_response(False, f'保存失败: {str(e)}')

@care_bp.route('/api/special/delete/<int:did>', methods=['POST'])
@login_required
def api_special_delete(did):
    r = SpecialSupport.query.get(did)
    if not r: return json_response(False, '记录不存在')
    try: db.session.delete(r); db.session.commit(); return json_response(True, '删除成功')
    except Exception: db.session.rollback(); return json_response(False, '删除失败')

@care_bp.route('/api/special/template')
@login_required
def api_special_template():
    import openpyxl; from openpyxl.styles import Font, PatternFill; import io
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = '特困供养导入模板'
    headers = ['所在组','户主姓名','户主身份证号','发放人数','保障金额','供养方式','供养机构','开始享受时间','变更时间','银行账号','发放月份','开户人','开户人身份','开户人身份证号','开户银行种类','家庭地址','户籍地址','联系电话','自理能力']
    ws.append(headers)
    for ci in range(1, len(headers)+1):
        cell = ws.cell(row=1, column=ci); cell.font = Font(bold=True, color='FFFFFF'); cell.fill = PatternFill(start_color='1a73e8', end_color='1a73e8', fill_type='solid')
        ws.column_dimensions[chr(64+ci)].width = max(14, len(headers[ci-1])*2+2)
    output = io.BytesIO(); wb.save(output); output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='特困供养导入模板.xlsx')

@care_bp.route('/api/special/import', methods=['POST'])
@login_required
def api_special_import():
    file = request.files.get('file'); mode = request.form.get('mode', 'append')
    try:
        import openpyxl; buf = __import__('io').BytesIO(file.read()); wb = openpyxl.load_workbook(buf); ws = wb.active
        rows = [r for r in ws.iter_rows(min_row=2, values_only=True)]
    except Exception as e: return json_response(False, '文件解析失败')
    col = {}; [col.update({h.strip(): i}) for i, h in enumerate([c.value for c in ws[1]]) if h]
    def gv(cn): idx = col.get(cn); return str(rows[ri][idx]).strip() if idx is not None and rows[ri][idx] is not None else ''
    imported=0; updated=0; skipped=0; errors=[]
    for ri, row in enumerate(rows):
        if not row or all(c is None for c in row): continue
        hn = gv('户主姓名')
        if not hn: errors.append(f'第{ri+2}行: 户主姓名空'); continue
        existing = SpecialSupport.query.filter_by(household_name=hn, household_id_card_hash=sha256_hash(gv('户主身份证号').upper())).first() if gv('户主身份证号') else None
        if existing and mode == 'append': skipped+=1; continue
        r = existing if existing and mode=='overwrite' else SpecialSupport()
        if not existing or mode!='overwrite': db.session.add(r)
        r.village_group=gv('所在组'); r.household_name=hn; _special_set_enc(r, 'household_id_card', gv('户主身份证号'))
        try: r.beneficiary_count=int(gv('发放人数')) if gv('发放人数') else 1
        except Exception: r.beneficiary_count=1
        try: r.guarantee_amount=float(gv('保障金额')) if gv('保障金额') else 0
        except Exception: r.guarantee_amount=0
        r.support_mode=gv('供养方式'); r.support_institution=gv('供养机构')
        def _pd(v):
            if not v: return None
            from datetime import date as dt_date
            if isinstance(v, datetime): return v.date()
            if isinstance(v, dt_date): return v
            s=str(v).strip()
            for fmt in ('%Y-%m-%d','%Y/%m/%d','%Y.%m.%d','%Y年%m月%d日'):
                try: return datetime.strptime(s,fmt).date()
                except Exception: pass
            return None
        r.start_date=_pd(rows[ri][col.get('开始享受时间')] if col.get('开始享受时间') is not None else None)
        r.change_time=_pd(rows[ri][col.get('变更时间')] if col.get('变更时间') is not None else None)
        _special_set_enc(r, 'bank_card', gv('银行账号'))
        r.payment_month=gv('发放月份')
        r.account_holder=gv('开户人'); r.account_holder_identity=gv('开户人身份')
        _special_set_enc(r, 'account_holder_id_card', gv('开户人身份证号'))
        r.bank_type=gv('开户银行种类'); r.address=gv('家庭地址'); r.registered_address=gv('户籍地址')
        _special_set_enc(r, 'phone', gv('联系电话')); r.self_care_ability=gv('自理能力')
        try: db.session.flush()
        except Exception as e: db.session.rollback(); errors.append(f'第{ri+2}行: {str(e)}'); continue
        if existing and mode=='overwrite': updated+=1
        else: imported+=1
    try: db.session.commit()
    except Exception: db.session.rollback(); return json_response(False,'导入失败')
    msg=f'新增{imported}条'; 
    if updated: msg+=f', 更新{updated}条'
    if skipped: msg+=f', 跳过{skipped}条'
    if errors: msg+='<br>失败'+str(len(errors))+'条:<br>'+'<br>'.join(errors[:10])
    log_operation(session.get('username',''), '特困供养', 'import', msg)
    return json_response(success=len(errors)==0, msg=msg)

@care_bp.route('/api/special/export')
@login_required
def api_special_export():
    headers = ['所在组','户主姓名','户主身份证号','发放人数','保障金额','供养方式','供养机构','开始享受时间','变更时间','银行账号','发放月份','开户人','开户人身份','开户人身份证号','开户银行种类','家庭地址','户籍地址','联系电话','自理能力']
    rows = [[getattr(r, 'village_group',''),getattr(r,'household_name',''),_special_decrypt(r,'household_id_card'),getattr(r,'beneficiary_count',1),getattr(r,'guarantee_amount',0),getattr(r,'support_mode',''),getattr(r,'support_institution',''),(r.start_date.strftime('%Y/%m/%d') if r.start_date else ''),(r.change_time.strftime('%Y/%m/%d') if r.change_time else ''),_special_decrypt(r,'bank_card'),getattr(r,'payment_month',''),getattr(r,'account_holder',''),getattr(r,'account_holder_identity',''),_special_decrypt(r,'account_holder_id_card'),getattr(r,'bank_type',''),getattr(r,'address',''),getattr(r,'registered_address',''),_special_decrypt(r,'phone'),getattr(r,'self_care_ability','')] for r in _special_filtered_query(request.args).all()]
    return export_excel(headers, rows, '特困供养人员.xlsx')

# ═══════════════════ 事实无人抚养儿童 ═══════════════════

@care_bp.route('/child')
@login_required
def child_list():
    from utils.agg_cache import get_civil_objects_count
    return render_template('care/child_list.html', civil_headcount=get_civil_objects_count())

@care_bp.route('/api/child/list')
@login_required
def api_child_list():
    page = request.args.get('page', 1, type=int); limit = request.args.get('limit', 20, type=int)
    query = _child_filtered_query(request.args)
    items, total, pages = paginate_query(query, page, limit)
    data = [_serialize_child(r) for r in items]
    return json_response(success=True, data={'data': data, 'total': total, 'pages': pages, 'page': page, 'limit': limit})

@care_bp.route('/api/child/detail/<int:did>')
@login_required
def api_child_detail(did):
    r = UnsupportedChild.query.get(did)
    if not r: return json_response(False, '记录不存在')
    d = _serialize_child(r)
    # B13 权限口径：身份证类字段仅启动口令解锁后展示（与 dibao 详情一致）
    d['unlocked'] = key_holder.is_initialized
    return json_response(success=True, data=d)

@care_bp.route('/api/child/save', methods=['POST'])
@login_required
def api_child_save():
    form = request.form; did = form.get('id', type=int)
    r = UnsupportedChild.query.get(did) if did else UnsupportedChild()
    if not did: db.session.add(r)
    for f in ['village_group','name','gender','father_status','mother_status','health_status','cardholder_relation','address']:
        setattr(r, f, form.get(f, '').strip())
    _child_set_enc(r, 'id_card', form.get('id_card', ''))
    _child_set_enc(r, 'phone', form.get('phone', ''))
    r.amount = form.get('amount', 0, type=float)
    try: r.birth_date = datetime.strptime(form.get('birth_date').strip(), '%Y-%m-%d').date() if form.get('birth_date') else None
    except Exception: r.birth_date = None
    try: db.session.commit(); log_operation(session.get('username',''), '事实无人抚养儿童', 'save', f'保存: {r.name}'); return json_response(True, '保存成功', data={'id': r.id})
    except Exception as e: db.session.rollback(); return json_response(False, f'保存失败: {str(e)}')

@care_bp.route('/api/child/delete/<int:did>', methods=['POST'])
@login_required
def api_child_delete(did):
    r = UnsupportedChild.query.get(did)
    if not r: return json_response(False, '记录不存在')
    try: db.session.delete(r); db.session.commit(); return json_response(True, '删除成功')
    except Exception: db.session.rollback(); return json_response(False, '删除失败')

@care_bp.route('/api/child/template')
@login_required
def api_child_template():
    import openpyxl; from openpyxl.styles import Font, PatternFill; import io
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = '事实无人抚养儿童导入模板'
    headers = ['所在组','发放金额（元）','儿童姓名','身份证号码','性别','出生日期','父亲情况','母亲情况','儿童身体情况','持卡人与本人关系','联系电话','家庭地址']
    ws.append(headers)
    for ci in range(1, len(headers)+1):
        cell = ws.cell(row=1, column=ci); cell.font = Font(bold=True, color='FFFFFF'); cell.fill = PatternFill(start_color='1a73e8', end_color='1a73e8', fill_type='solid')
        ws.column_dimensions[chr(64+ci)].width = max(14, len(headers[ci-1])*2+2)
    output = io.BytesIO(); wb.save(output); output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='事实无人抚养儿童导入模板.xlsx')

@care_bp.route('/api/child/import', methods=['POST'])
@login_required
def api_child_import():
    file = request.files.get('file'); mode = request.form.get('mode', 'append')
    try: import openpyxl; buf = __import__('io').BytesIO(file.read()); wb = openpyxl.load_workbook(buf); ws = wb.active; rows = [r for r in ws.iter_rows(min_row=2, values_only=True)]
    except Exception: return json_response(False, '文件解析失败')
    col = {}; [col.update({h.strip(): i}) for i, h in enumerate([c.value for c in ws[1]]) if h]
    def gv(cn): idx = col.get(cn); return str(rows[ri][idx]).strip() if idx is not None and rows[ri][idx] is not None else ''
    imported=0; updated=0; skipped=0; errors=[]
    for ri, row in enumerate(rows):
        if not row or all(c is None for c in row): continue
        nm = gv('儿童姓名')
        if not nm: errors.append(f'第{ri+2}行: 姓名空'); continue
        idc = gv('身份证号码')
        existing = UnsupportedChild.query.filter_by(name=nm, id_card_hash=sha256_hash(idc.upper())).first() if idc else None
        if existing and mode=='append': skipped+=1; continue
        r = existing if existing and mode=='overwrite' else UnsupportedChild()
        if not existing or mode!='overwrite': db.session.add(r)
        r.village_group=gv('所在组'); r.name=nm; _child_set_enc(r, 'id_card', idc); r.gender=gv('性别')
        try: r.amount=float(gv('发放金额（元）')) if gv('发放金额（元）') else 0
        except Exception: r.amount=0
        try: r.birth_date=datetime.strptime(gv('出生日期'),'%Y-%m-%d').date() if gv('出生日期') else None
        except Exception: r.birth_date=None
        r.father_status=gv('父亲情况'); r.mother_status=gv('母亲情况'); r.health_status=gv('儿童身体情况')
        r.cardholder_relation=gv('持卡人与本人关系'); _child_set_enc(r, 'phone', gv('联系电话')); r.address=gv('家庭地址')
        try: db.session.flush()
        except Exception as e: db.session.rollback(); errors.append(f'第{ri+2}行: {str(e)}'); continue
        if existing and mode=='overwrite': updated+=1
        else: imported+=1
    try: db.session.commit()
    except Exception: db.session.rollback(); return json_response(False,'导入失败')
    msg=f'新增{imported}条'
    if updated: msg+=f', 更新{updated}条'
    if skipped: msg+=f', 跳过{skipped}条'
    if errors: msg+='<br>失败'+str(len(errors))+'条'
    log_operation(session.get('username',''), '事实无人抚养儿童', 'import', msg)
    return json_response(success=len(errors)==0, msg=msg)

@care_bp.route('/api/child/export')
@login_required
def api_child_export():
    headers = ['所在组','发放金额（元）','儿童姓名','身份证号码','性别','父亲情况','母亲情况','儿童身体情况','持卡人与本人关系','联系电话','家庭地址']
    rows = [[getattr(r,'village_group',''),getattr(r,'amount',0),getattr(r,'name',''),_child_decrypt(r,'id_card'),getattr(r,'gender',''),getattr(r,'father_status',''),getattr(r,'mother_status',''),getattr(r,'health_status',''),getattr(r,'cardholder_relation',''),_child_decrypt(r,'phone'),getattr(r,'address','')] for r in _child_filtered_query(request.args).all()]
    return export_excel(headers, rows, '事实无人抚养儿童.xlsx')


# ═══════════════════ 一老一小关怀台账（CareTarget，T13） ═══════════════════

def _target_filtered_query(args):
    """CareTarget 分页查询（join residents 取 birth_date/网格/村组，动态归类）。"""
    query = CareTarget.query.join(Resident, CareTarget.resident_id == Resident.id)
    # 软失效过滤：默认只看有效
    if not args.get('include_invalid'):
        query = query.filter(CareTarget.status == '有效')
    # 分类筛选：target_type 老人/儿童
    tt = args.get('target_type', '').strip()
    if tt:
        query = query.filter(CareTarget.target_type == tt)
    # 网格村组
    vg = args.get('village_group', '').strip()
    if vg:
        query = query.filter(Resident.village_group == vg)
    # 姓名（居民姓名）
    name = args.get('name', '').strip()
    if name:
        query = query.filter(Resident.name.like(f'%{name}%'))
    # 儿童类型/老人类型
    ct = args.get('child_type', '').strip()
    if ct:
        query = query.filter(CareTarget.child_type == ct)
    et = args.get('elder_type', '').strip()
    if et:
        query = query.filter(CareTarget.elder_type == et)
    # B-7 服务事项/办理状态（EXISTS 子查询，叠加既有筛选；儿童无记录自然被过滤）
    si = args.get('service_item', '').strip()
    if si:
        query = query.filter(db.exists().where(db.and_(
            ElderServiceRecord.care_target_id == CareTarget.id,
            ElderServiceRecord.service_item == si)))
    ss = args.get('service_status', '').strip()
    if ss:
        query = query.filter(db.exists().where(db.and_(
            ElderServiceRecord.care_target_id == CareTarget.id,
            ElderServiceRecord.service_status == ss)))
    return query.order_by(CareTarget.id.desc())


def _service_summary(target_ids):
    """批量取各关爱对象最近一条服务记录与记录数（避免列表 N+1）。

    最近一条规则：service_date 倒序、同日 id 倒序（B-7 同 B-6）。
    """
    ids = {tid for tid in (target_ids or []) if tid}
    latest, counts = {}, {}
    if not ids:
        return latest, counts
    rows = (ElderServiceRecord.query
            .filter(ElderServiceRecord.care_target_id.in_(ids))
            .order_by(ElderServiceRecord.care_target_id.asc(),
                      ElderServiceRecord.service_date.desc(),
                      ElderServiceRecord.id.desc())
            .all())
    for s in rows:
        counts[s.care_target_id] = counts.get(s.care_target_id, 0) + 1
        latest.setdefault(s.care_target_id, s)
    return latest, counts


def _service_row_dict(s):
    """老年人服务事项记录序列化。"""
    return {
        'id': s.id,
        'service_item': s.service_item or '',
        'service_status': s.service_status or '',
        'service_progress': s.service_progress or '',
        'service_owner': s.service_owner or '',
        'service_date': s.service_date.strftime('%Y-%m-%d') if s.service_date else '',
        'remark': s.remark or '',
    }


def _elder_target_or_error(did):
    """取老年人关爱对象；不存在/非老人返回 (None, 错误响应)。"""
    r = CareTarget.query.get(did)
    if not r:
        return None, json_response(False, '记录不存在')
    if r.target_type != '老人':
        return None, json_response(False, '仅老年人可登记服务事项')
    return r, None


def _target_row_dict(r, latest_service=None, service_count=0):
    """CareTarget 序列化（含动态归类 dynamic_class + 关联居民基础信息 + B-7 服务摘要）。"""
    resident = r.resident
    birth_date = resident.birth_date if resident else None
    info = classify_age(birth_date, parse_override(r.age_class_override))
    svc = latest_service if r.target_type == '老人' else None
    svc_d = _service_row_dict(svc) if svc else {}
    return {
        'id': r.id,
        'resident_id': r.resident_id,
        'name': resident.name if resident else (r.name or ''),
        'village_group': resident.village_group if resident else '',
        'grid': resident.grid if resident else '',
        'birth_date': birth_date.strftime('%Y-%m-%d') if birth_date else '',
        'age': info['age'],
        'dynamic_class': info['class'],          # 动态归类（不落库）
        'target_type': r.target_type or '',
        'elder_type': r.elder_type or '',
        'child_type': r.child_type or '',
        'source': r.source or '',
        'guardian_consent': r.guardian_consent or '',
        'difficulty_reason': r.difficulty_reason or '',
        'need': r.need or '',
        'status': r.status or '有效',
        # B-7 服务摘要（非老人行一律空值/0）
        'service_item': svc_d.get('service_item', ''),
        'service_status': svc_d.get('service_status', ''),
        'service_progress': svc_d.get('service_progress', ''),
        'service_owner': svc_d.get('service_owner', ''),
        'service_date': svc_d.get('service_date', ''),
        'service_count': int(service_count or 0) if r.target_type == '老人' else 0,
    }


@care_bp.route('/target')
@login_required
def target_list():
    from utils.agg_cache import get_civil_objects_count
    return render_template('care/target_list.html', civil_headcount=get_civil_objects_count())


@care_bp.route('/api/target/list')
@login_required
@child_rows_guard
def api_target_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    query = _target_filtered_query(request.args)
    items, total, pages = paginate_query(query, page, limit)
    # 儿童行级可见过滤（非民政主任/admin 过滤留守/困境儿童明细）
    items = filter_child_rows(items, g.get('can_see_children', False))
    latest, counts = _service_summary([r.id for r in items])
    data = [_target_row_dict(r, latest.get(r.id), counts.get(r.id, 0)) for r in items]
    return json_response(success=True, data={'data': data, 'total': total, 'pages': pages, 'page': page, 'limit': limit})


def _is_child_row(r):
    """CareTarget 是否为留守/困境儿童行（行级可见判定）。"""
    return r.target_type == '儿童' and r.child_type in ('留守', '困境')


def _claimed_class(target_type, elder_type):
    """镇级名单声称的归类（用于冲突检测），返回 高龄/儿童/None。"""
    if elder_type == '高龄':
        return '高龄'
    if target_type == '儿童':
        return '儿童'
    return None


@care_bp.route('/api/target/save', methods=['POST'])
@login_required
def api_target_save():
    form = request.form
    did = form.get('id', type=int)
    r = CareTarget.query.get(did) if did else CareTarget()
    if not did:
        db.session.add(r)

    target_type = form.get('target_type', '').strip()
    child_type = form.get('child_type', '').strip()
    # 儿童仅政策性入册（业务规则下沉模型静态方法）
    _entry_err = CareTarget.validate_child_entry(target_type, child_type)
    if _entry_err:
        return json_response(False, _entry_err)

    resident_id = form.get('resident_id', type=int)
    if not resident_id:
        return json_response(False, '请选择关联居民')

    r.resident_id = resident_id
    r.target_type = target_type
    r.elder_type = form.get('elder_type', '').strip()
    r.child_type = child_type
    r.source = form.get('source', '').strip()
    r.guardian = form.get('guardian', '').strip()
    _target_set_enc(r, 'guardian_phone', form.get('guardian_phone', ''))
    r.difficulty_reason = form.get('difficulty_reason', '').strip()
    r.need = form.get('need', '').strip()
    r.main_difficulty = form.get('main_difficulty', '').strip()

    # guardian_consent 待补录：不阻断（缺省视为待补录）
    consent = form.get('guardian_consent', '').strip()
    r.guardian_consent = consent if consent else '待补录'

    # 冲突检测（T13-4）：镇级名单声称归类与动态计算不符 → 提示人工确认
    resident = Resident.query.get(resident_id)
    if resident and r.source == '镇级名单':
        info = classify_age(resident.birth_date, None)
        claimed = _claimed_class(target_type, r.elder_type)
        if claimed and claimed != info['class']:
            # 不落库，返回冲突提示（前端确认后走 override 端点）
            db.session.rollback()
            return json_response(True, '检测到归类冲突，需人工确认', data={
                'conflict': True,
                'computed': info['class'],
                'claimed': claimed,
                'age': info['age'],
            })

    try:
        db.session.flush()
        # T16-2 事件驱动：保存时自动回填「困难老人/留守儿童/困境儿童」标签（R3-E2，幂等）
        sync_resident_tags(r, 'care_target')
        # 待补录 → 建待办（幂等：已存在同 related_id 待办则不重复）
        if r.guardian_consent == '待补录':
            _ensure_consent_todo(r)
        db.session.commit()
        log_operation(session.get('username', ''), '关爱对象', 'save', f'保存: {r.target_type}')
        return json_response(True, '保存成功', data={'id': r.id, 'conflict': False})
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')


def _ensure_consent_todo(r):
    """监护人同意待补录 → 建待办（幂等）。"""
    existing = TodoItem.query.filter_by(
        todo_type='care_guardian_consent', related_id=r.id, status='todo').first()
    if existing:
        return
    db.session.add(TodoItem(
        title='监护人同意待补录',
        content=f'关爱对象 #{r.id} 监护人同意待补录',
        todo_type='care_guardian_consent',
        status='todo',
        related_id=r.id,
    ))


@care_bp.route('/api/target/delete/<int:did>', methods=['POST'])
@login_required
def api_target_delete(did):
    r = CareTarget.query.get(did)
    if not r:
        return json_response(False, '记录不存在')
    r.status = '失效'
    r.invalid_reason = request.form.get('reason', '').strip() or '手工失效'
    # T16-2 事件驱动：软失效联动标签软失效（R3-E4，对齐 /api/tag/remove 语义）
    tag = _tag_for_care_target(r)
    if tag and r.resident_id:
        invalidate_tag(r.resident_id, tag, reason=r.invalid_reason)
    db.session.commit()
    log_operation(session.get('username', ''), '关爱对象', 'delete', f'失效: {r.id}')
    return json_response(True, '已失效')


@care_bp.route('/api/target/consent/<int:did>', methods=['POST'])
@login_required
def api_target_consent(did):
    """T13-5：监护人同意补录（写 consent + 置待办 done）。"""
    r = CareTarget.query.get(did)
    if not r:
        return json_response(False, '记录不存在')
    r.guardian_consent = '已同意'
    r.guardian_consent_time = datetime.now()
    r.guardian_consent_way = request.form.get('way', '').strip()
    db.session.commit()
    # 置关联待办 done
    for t in TodoItem.query.filter_by(todo_type='care_guardian_consent',
                                      related_id=r.id, status='todo').all():
        t.status = 'done'
        t.completed_by = session.get('username', '')
        t.completed_at = datetime.now()
    db.session.commit()
    log_operation(session.get('username', ''), '关爱对象', 'consent', f'监护人同意补录: {r.id}')
    return json_response(True, '已补录')


# ── B-7 老年人服务事项（elder_service_records，仅 target_type='老人'） ──

@care_bp.route('/api/target/services/<int:did>')
@login_required
def api_target_services(did):
    """老年人服务事项列表（service_date 倒序，同日 id 倒序）。"""
    r, err = _elder_target_or_error(did)
    if err:
        return err
    rows = (ElderServiceRecord.query
            .filter_by(care_target_id=did)
            .order_by(ElderServiceRecord.service_date.desc(),
                      ElderServiceRecord.id.desc())
            .all())
    return json_response(success=True, data=[_service_row_dict(s) for s in rows])


@care_bp.route('/api/target/services/<int:did>/save', methods=['POST'])
@login_required
def api_target_service_save(did):
    """登记/编辑老年人服务事项（枚举校验，非法值零写库）。"""
    from utils.dict_enum import get_enum
    r, err = _elder_target_or_error(did)
    if err:
        return err
    form = request.form
    sid = form.get('id', type=int)
    item = form.get('service_item', '').strip()
    status_v = form.get('service_status', '').strip() or '待受理'
    if not item:
        return json_response(False, '请选择服务事项')
    if item not in get_enum('老年人服务事项'):
        return json_response(False, '服务事项不合法')
    if status_v not in get_enum('服务办理状态'):
        return json_response(False, '办理状态不合法')
    service_date = None
    raw_date = form.get('service_date', '').strip()
    if raw_date:
        try:
            service_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            return json_response(False, '服务日期格式不正确（应为 YYYY-MM-DD）')
    s = None
    if sid:
        s = ElderServiceRecord.query.get(sid)
        if not s or s.care_target_id != did:
            return json_response(False, '记录不存在')
    else:
        s = ElderServiceRecord(care_target_id=did)
        db.session.add(s)
    s.service_item = item
    s.service_status = status_v
    s.service_progress = form.get('service_progress', '').strip()
    s.service_owner = form.get('service_owner', '').strip()
    s.service_date = service_date
    s.remark = form.get('remark', '').strip()
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')
    log_operation(session.get('username', ''), '关爱对象', 'service_save', f'服务事项: {r.id}/{s.id}')
    return json_response(True, '保存成功', data={'id': s.id})


@care_bp.route('/api/target/services/<int:did>/delete/<int:sid>', methods=['POST'])
@login_required
def api_target_service_delete(did, sid):
    """删除老年人服务事项（校验归属后物理删除）。"""
    r, err = _elder_target_or_error(did)
    if err:
        return err
    s = ElderServiceRecord.query.get(sid)
    if not s or s.care_target_id != did:
        return json_response(False, '记录不存在')
    try:
        db.session.delete(s)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return json_response(False, '删除失败')
    log_operation(session.get('username', ''), '关爱对象', 'service_delete', f'删除服务事项: {r.id}/{sid}')
    return json_response(True, '删除成功')


@care_bp.route('/api/target/template')
@login_required
def api_target_template():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    import io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '一老一小导入模板'
    headers = ['姓名', '身份证号', '目标类型', '老人类型', '儿童类型', '来源', '困难原因', '需求', '监护人', '监护人同意']
    ws.append(headers)
    for ci in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=ci)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(start_color='1a73e8', end_color='1a73e8', fill_type='solid')
        ws.column_dimensions[chr(64 + ci)].width = max(14, len(headers[ci - 1]) * 2 + 2)
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='一老一小导入模板.xlsx')


@care_bp.route('/api/target/import', methods=['POST'])
@login_required
def api_target_import():
    """T13-1：镇级名单导入建档（独立函数，按身份证 hash 匹配已有居民，不新建居民）。"""
    from utils.crypto import sha256_hash
    file = request.files.get('file')
    try:
        import openpyxl
        import io as _io
        buf = _io.BytesIO(file.read())
        wb = openpyxl.load_workbook(buf)
        ws = wb.active
        rows = [r for r in ws.iter_rows(min_row=2, values_only=True)]
    except Exception:
        return json_response(False, '文件解析失败')
    col = {}
    for i, h in enumerate([c.value for c in ws[1]]):
        if h:
            col[h.strip()] = i

    def _gv(cn, ri):
        idx = col.get(cn)
        return str(rows[ri][idx]).strip() if idx is not None and ri < len(rows) and rows[ri][idx] is not None else ''

    imported = 0
    skipped = 0
    errors = []
    for ri, row in enumerate(rows):
        if not row or all(c is None for c in row):
            continue
        id_card = _gv('身份证号', ri)
        name = _gv('姓名', ri)
        if not id_card and not name:
            errors.append(f'第{ri + 2}行: 姓名/身份证均空')
            continue
        # 按身份证 hash 匹配已有居民
        resident = None
        if id_card:
            h = sha256_hash(id_card.upper())
            resident = Resident.query.filter_by(id_card_hash=h).first()
        if not resident and name:
            resident = Resident.query.filter_by(name=name).first()
        if not resident:
            errors.append(f'第{ri + 2}行: 居民档案不存在（{name or id_card}）')
            continue

        target_type = _gv('目标类型', ri) or ('儿童' if _gv('儿童类型', ri) else ('老人' if _gv('老人类型', ri) else ''))
        # 去重：同人同类型已有建档则跳过
        existing = CareTarget.query.filter_by(resident_id=resident.id, target_type=target_type, status='有效').first()
        if existing:
            skipped += 1
            continue

        r = CareTarget(
            resident_id=resident.id,
            target_type=target_type,
            elder_type=_gv('老人类型', ri),
            child_type=_gv('儿童类型', ri),
            source='镇级名单',
            guardian=_gv('监护人', ri),
            difficulty_reason=_gv('困难原因', ri),
            need=_gv('需求', ri),
            guardian_consent=_gv('监护人同意', ri) or '待补录',
        )
        db.session.add(r)
        try:
            db.session.flush()
            imported += 1
            if r.guardian_consent == '待补录':
                _ensure_consent_todo(r)
        except Exception as e:
            db.session.rollback()
            errors.append(f'第{ri + 2}行: {str(e)}')
            continue
    db.session.commit()
    msg = f'新增{imported}条'
    if skipped:
        msg += f', 跳过{skipped}条'
    if errors:
        msg += f'<br>失败{len(errors)}条:<br>' + '<br>'.join(errors[:10])
    log_operation(session.get('username', ''), '关爱对象', 'import', msg)
    return json_response(success=len(errors) == 0, msg=msg)


@care_bp.route('/api/target/export')
@login_required
@child_rows_guard
def api_target_export():
    """导出（待补录标注「监护人同意待补录」）。"""
    items = _target_filtered_query(request.args).all()
    items = filter_child_rows(items, g.get('can_see_children', False))
    latest, counts = _service_summary([r.id for r in items])
    headers = ['姓名', '目标类型', '老人类型', '儿童类型', '动态归类', '年龄', '所在组', '网格', '来源', '困难原因', '需求', '监护人', '监护人同意', '服务事项', '办理状态', '办理进度', '责任人', '服务日期']
    rows = []
    for r in items:
        d = _target_row_dict(r, latest.get(r.id), counts.get(r.id, 0))
        consent = '监护人同意待补录' if d['guardian_consent'] == '待补录' else d['guardian_consent']
        rows.append([d['name'], d['target_type'], d['elder_type'], d['child_type'],
                     d['dynamic_class'], d['age'], d['village_group'], d['grid'],
                     d['source'], d['difficulty_reason'], d['need'], r.guardian or '', consent,
                     d['service_item'], d['service_status'], d['service_progress'],
                     d['service_owner'], d['service_date']])
    return export_excel(headers, rows, '一老一小关怀台账.xlsx')


# ═══════════════════ 092 模板附件区（收口 utils/attachment_service，禁复制实现） ═══════════════════

from utils.attachment_service import register_attachment_routes as _register_attachment_routes  # noqa: E402
_register_attachment_routes(care_bp, 'care')
