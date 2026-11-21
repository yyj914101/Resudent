from flask import Blueprint, render_template, request, session, jsonify, send_file
from models import db, Disabled, DisabledServiceRecord
from utils.decorators import login_required
from utils.helpers import paginate_query, json_response, log_operation, export_excel
from utils.tag_backfill import sync_resident_tags
from utils.crypto import key_holder, sha256_hash
from utils.dict_enum import get_enum
from utils.sensitive_fields import make_sensitive_helpers
from datetime import datetime
# 092 模板附件区：服务收口（三蓝图唯一真源，禁复制实现）
from utils import attachment_service
from utils.attachment_service import attachment_api

disabled_bp = Blueprint('disabled', __name__)

# R12 字段级加密管线（复用 utils.sensitive_fields，消除残疾人台账明文孤岛）
_DISABLED_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
    'guardian_phone': ('guardian_phone_enc', 'guardian_phone_hash', None),
}
_decrypt, _set_enc, _clause = make_sensitive_helpers(Disabled, _DISABLED_SENS_FIELDS, key_holder)
from utils.masking import mask_id_card, mask_phone  # R12：列表/导出恒打码


_SERVICE_SUMMARY_DEFAULTS = {
    'service_item': '', 'service_status': '', 'service_progress': '',
    'service_owner': '', 'service_date': '', 'service_count': 0,
}


def _service_fields(s):
    """单条服务记录展示字段：空值统一 ''，日期输出 ISO 文本。"""
    return {
        'service_item': s.service_item or '',
        'service_status': s.service_status or '',
        'service_progress': s.service_progress or '',
        'service_owner': s.service_owner or '',
        'service_date': s.service_date.strftime('%Y-%m-%d') if s.service_date else '',
    }


def _service_row(s):
    row = {'id': s.id}
    row.update(_service_fields(s))
    row['remark'] = s.remark or ''
    return row


def _empty_service_summary():
    return dict(_SERVICE_SUMMARY_DEFAULTS)


def _service_summary(disabled_ids):
    """一次批量查询：disabled_id -> 最近一条服务记录汇总 + 条数（避免 N+1）。

    最近一条 = service_date 最大、同日 id 最大；service_date 为 NULL 的排最后
    （NULL 不作为最近记录）。空值统一输出 ''。
    """
    summary = {did: _empty_service_summary() for did in disabled_ids}
    if not disabled_ids:
        return summary
    records = (DisabledServiceRecord.query
               .filter(DisabledServiceRecord.disabled_id.in_(disabled_ids))
               .order_by(DisabledServiceRecord.service_date.is_(None),
                         DisabledServiceRecord.service_date.desc(),
                         DisabledServiceRecord.id.desc())
               .all())
    for s in records:
        entry = summary.get(s.disabled_id)
        if entry is None:
            continue
        entry['service_count'] += 1
        if entry['service_count'] == 1:
            entry.update(_service_fields(s))
    return summary


def _service_records_query(did):
    return (DisabledServiceRecord.query
            .filter_by(disabled_id=did)
            .order_by(DisabledServiceRecord.service_date.is_(None),
                      DisabledServiceRecord.service_date.desc(),
                      DisabledServiceRecord.id.desc()))


def _disabled_filtered_query(args):
    name = args.get('name', '').strip()
    id_card = args.get('id_card', '').strip()
    village = args.get('village', '').strip()
    dtype = args.get('dtype', '').strip()
    service_item = args.get('service_item', '').strip()
    service_status = args.get('service_status', '').strip()

    query = Disabled.query.order_by(Disabled.id.desc())
    if name:
        query = query.filter(Disabled.name.like(f'%{name}%'))
    if id_card:
        query = query.filter(Disabled.id_card_hash == sha256_hash(id_card.strip().upper()))
    if village:
        query = query.filter(Disabled.village_group == village)
    if dtype:
        query = query.filter(Disabled.disability_type == dtype)
    if service_item:
        query = query.filter(db.exists().where(
            DisabledServiceRecord.disabled_id == Disabled.id,
            DisabledServiceRecord.service_item == service_item))
    if service_status:
        query = query.filter(db.exists().where(
            DisabledServiceRecord.disabled_id == Disabled.id,
            DisabledServiceRecord.service_status == service_status))
    return query


@disabled_bp.route('/')
@login_required
def disabled_list():
    # 075 RC-06：副标题「村名 · 10 类民政对象 · 在册 N 人」
    from utils.agg_cache import get_civil_objects_count
    return render_template('disabled/list.html', civil_headcount=get_civil_objects_count())

@disabled_bp.route('/api/list')
@login_required
def api_list():
    page = max(request.args.get('page', 1, type=int), 1)
    limit = 20
    query = _disabled_filtered_query(request.args)

    items, total, pages = paginate_query(query, page, limit)
    if pages and page > pages:
        page = pages
        items, total, pages = paginate_query(query, page, limit)
    summaries = _service_summary([r.id for r in items])
    data = []
    for r in items:
        row = {
            'id': r.id, 'village_group': r.village_group or '',
            'name': r.name or '', 'id_card': mask_id_card(_decrypt(r, 'id_card')),
            'gender': r.gender or '', 'nation': r.nation or '',
            'education': r.education or '', 'phone': mask_phone(_decrypt(r, 'phone')),
            'registered_address': r.registered_address or '',
            'guardian_name': r.guardian_name or '',
            'guardian_relation': r.guardian_relation or '',
            'guardian_phone': mask_phone(_decrypt(r, 'guardian_phone')),
            'cert_no': r.cert_no or '',
            'disability_type': r.disability_type or '',
            'disability_level': r.disability_level or '',
            'disability_detail': r.disability_detail or '',
            'subsidy_living': r.subsidy_living or 0,
            'subsidy_nursing': r.subsidy_nursing or 0,
        }
        row.update(summaries.get(r.id) or _empty_service_summary())
        data.append(row)
    return json_response(success=True, data={
        'data': data, 'total': total, 'pages': pages, 'page': page, 'limit': limit,
    })

@disabled_bp.route('/api/detail/<int:did>')
@login_required
def api_detail(did):
    r = Disabled.query.get(did)
    if not r: return json_response(False, '记录不存在')
    data = {
        'id': r.id, 'village_group': r.village_group or '',
        'name': r.name or '', 'id_card': _decrypt(r, 'id_card'),
        'gender': r.gender or '', 'nation': r.nation or '',
        'education': r.education or '', 'phone': _decrypt(r, 'phone'),
        'registered_address': r.registered_address or '',
        'guardian_name': r.guardian_name or '',
        'guardian_relation': r.guardian_relation or '',
        'guardian_phone': _decrypt(r, 'guardian_phone'),
        'cert_no': r.cert_no or '',
        'disability_type': r.disability_type or '',
        'disability_level': r.disability_level or '',
        'disability_detail': r.disability_detail or '',
        'subsidy_living': r.subsidy_living or 0,
        'subsidy_nursing': r.subsidy_nursing or 0,
    }
    data.update(_service_summary([did]).get(did) or _empty_service_summary())
    data['services'] = [_service_row(s) for s in _service_records_query(did).all()]
    # B13 权限口径：身份证类字段仅启动口令解锁后展示（与 dibao 详情一致）
    data['unlocked'] = key_holder.is_initialized
    return json_response(success=True, data=data)

@disabled_bp.route('/api/services/<int:did>')
@login_required
def api_services(did):
    if not Disabled.query.get(did):
        return json_response(False, '记录不存在')
    return json_response(True, '', [_service_row(s) for s in _service_records_query(did).all()])

@disabled_bp.route('/api/services/<int:did>/save', methods=['POST'])
@login_required
def api_service_save(did):
    if not Disabled.query.get(did):
        return json_response(False, '记录不存在')
    form = request.form if request.form else (request.json or {})
    sid = form.get('id')
    try:
        sid = int(sid) if sid not in (None, '') else None
    except (TypeError, ValueError):
        return json_response(False, '服务记录不合法')

    service_item = (form.get('service_item') or '').strip()
    service_status = (form.get('service_status') or '').strip() or '待受理'
    service_progress = (form.get('service_progress') or '').strip()
    service_owner = (form.get('service_owner') or '').strip()
    service_date_text = (form.get('service_date') or '').strip()
    remark = (form.get('remark') or '').strip()

    if service_item not in get_enum('残疾人服务事项'):
        return json_response(False, '服务事项不合法')
    if service_status not in get_enum('服务办理状态'):
        return json_response(False, '办理状态不合法')

    service_date = None
    if service_date_text:
        try:
            service_date = datetime.strptime(service_date_text, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return json_response(False, '服务日期格式不正确')

    if sid is not None:
        r = DisabledServiceRecord.query.filter_by(id=sid, disabled_id=did).first()
        if not r:
            return json_response(False, '服务记录不存在')
    else:
        r = DisabledServiceRecord(disabled_id=did)
        db.session.add(r)

    r.service_item = service_item
    r.service_status = service_status
    r.service_progress = service_progress
    r.service_owner = service_owner
    r.service_date = service_date
    r.remark = remark
    try:
        db.session.commit()
        log_operation(session.get('username', ''), '残疾人服务',
                      'edit' if sid is not None else 'add', f'{service_item} / {service_status}')
        return json_response(True, '保存成功')
    except Exception:
        db.session.rollback()
        return json_response(False, '保存失败')

@disabled_bp.route('/api/services/<int:did>/delete/<int:sid>', methods=['POST'])
@login_required
def api_service_delete(did, sid):
    r = DisabledServiceRecord.query.filter_by(id=sid, disabled_id=did).first()
    if not r:
        return json_response(False, '服务记录不存在')
    try:
        db.session.delete(r); db.session.commit()
        log_operation(session.get('username', ''), '残疾人服务', 'delete', f'删除服务记录 ID:{sid}')
        return json_response(True, '删除成功')
    except Exception:
        db.session.rollback()
        return json_response(False, '删除失败')

@disabled_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    form = request.form if request.form else (request.json or {})
    did = form.get('id', type=int)
    if did:
        r = Disabled.query.get(did)
        if not r: return json_response(False, '记录不存在')
    else:
        r = Disabled()

    fields = ['village_group','name','gender','nation','education',
              'registered_address','guardian_name','guardian_relation',
              'cert_no','disability_type','disability_level','disability_detail']
    for f in fields:
        setattr(r, f, form.get(f, '').strip())
    # 敏感字段走字段级加密（R12，消除残疾人台账明文孤岛）
    for f in ['id_card', 'phone', 'guardian_phone']:
        _set_enc(r, f, form.get(f, ''))
    r.subsidy_living = form.get('subsidy_living', 0, type=float)
    r.subsidy_nursing = form.get('subsidy_nursing', 0, type=float)

    if not did: db.session.add(r)
    try:
        # T16-2 事件驱动：保存时自动回填「残疾」标签（R3-E2，幂等）
        sync_resident_tags(r, 'disabled')
        db.session.commit()
        log_operation(session.get('username',''), '残疾人管理', 'add' if not did else 'edit', f'保存: {r.name}')
        return json_response(True, '保存成功', data={'id': r.id})
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')

@disabled_bp.route('/api/delete/<int:did>', methods=['POST'])
@login_required
def api_delete(did):
    r = Disabled.query.get(did)
    if not r: return json_response(False, '记录不存在')
    try:
        # 093 B-6：同一事务先清子表服务记录再删父记录，避免孤儿数据
        DisabledServiceRecord.query.filter_by(disabled_id=did).delete(synchronize_session=False)
        db.session.delete(r); db.session.commit()
        log_operation(session.get('username',''), '残疾人管理', 'delete', f'删除 ID:{did}')
        return json_response(True, '删除成功')
    except Exception:
        db.session.rollback()
        return json_response(False, '删除失败')

@disabled_bp.route('/api/template')
@login_required
def api_template():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    import io
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '残疾人员导入模板'
    headers = ['所在组','姓名','身份证号','性别','民族','文化程度','手机','户籍地地址',
               '监护人姓名','监护人与残疾人关系','监护人手机','残疾证号','残疾类别','残疾等级','残疾详情',
               '困难生活补贴','重度护理补贴']
    ws.append(headers)
    for ci in range(1, len(headers)+1):
        cell = ws.cell(row=1, column=ci)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(start_color='1a73e8', end_color='1a73e8', fill_type='solid')
        ws.column_dimensions[chr(64+ci)].width = max(14, len(headers[ci-1])*2+2)
    output = io.BytesIO()
    wb.save(output); output.seek(0)
    log_operation(session.get('username',''), '残疾人管理', 'template', '下载导入模板')
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True, download_name='残疾人员导入模板.xlsx')

@disabled_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    file = request.files.get('file')
    if not file: return json_response(False, '请选择文件')
    mode = request.form.get('mode', 'append')

    try:
        import openpyxl
        buf = __import__('io').BytesIO(file.read())
        wb = openpyxl.load_workbook(buf)
        ws = wb.active
        rows = [r for r in ws.iter_rows(min_row=2, values_only=True)]
    except Exception as e:
        return json_response(False, '文件解析失败: ' + str(e))

    col = {}
    for i, h in enumerate([cell.value for cell in ws[1]]):
        if h: col[h.strip()] = i

    if '姓名' not in col:
        return json_response(False, '缺少"姓名"列')

    def gv(cn):
        idx = col.get(cn)
        if idx is None: return ''
        v = rows[ri][idx]
        return str(v).strip() if v is not None else ''

    imported = 0; updated = 0; skipped = 0; errors = []

    for ri, row in enumerate(rows):
        if not row or all(c is None for c in row): continue
        name = gv('姓名')
        id_card = gv('身份证号') or gv('身份证')
        if not name: errors.append(f'第{ri+2}行: 姓名空'); continue

        existing = None
        if id_card: existing = Disabled.query.filter_by(id_card_hash=sha256_hash(id_card.upper())).first()

        if existing:
            if mode == 'append': skipped += 1; continue
        r = existing if existing and mode == 'overwrite' else Disabled()
        if not existing or mode != 'overwrite': db.session.add(r)

        r.name = name
        _set_enc(r, 'id_card', id_card)
        r.village_group = gv('所在组') or gv('所属所在组')
        r.gender = gv('性别'); r.nation = gv('民族')
        r.education = gv('文化程度')
        _set_enc(r, 'phone', gv('手机'))
        r.registered_address = gv('户籍地地址')
        r.guardian_name = gv('监护人姓名')
        r.guardian_relation = gv('监护人与残疾人关系')
        _set_enc(r, 'guardian_phone', gv('监护人手机'))
        r.cert_no = gv('残疾证号')
        r.disability_type = gv('残疾类别')
        r.disability_level = gv('残疾等级')
        r.disability_detail = gv('残疾详情')
        try: r.subsidy_living = float(gv('困难生活补贴')) if gv('困难生活补贴') else 0
        except Exception: r.subsidy_living = 0
        try: r.subsidy_nursing = float(gv('重度护理补贴')) if gv('重度护理补贴') else 0
        except Exception: r.subsidy_nursing = 0

        try:
            db.session.flush()
            if existing and mode == 'overwrite': updated += 1
            else: imported += 1
        except Exception as e:
            db.session.rollback(); errors.append(f'第{ri+2}行 ({name}): {str(e)}')

    try: db.session.commit()
    except Exception: db.session.rollback(); return json_response(False, '导入失败')

    msg = f'新增{imported}条'
    if updated: msg += f', 更新{updated}条'
    if skipped: msg += f', 跳过{skipped}条'
    if errors: msg += '<br>失败' + str(len(errors)) + '条:<br>' + '<br>'.join(errors[:10])
    log_operation(session.get('username',''), '残疾人管理', 'import', msg)
    return json_response(success=len(errors)==0, msg=msg, data={
        'imported': imported, 'updated': updated, 'skipped': skipped, 'errors': errors[:30],
    })

@disabled_bp.route('/api/export')
@login_required
def api_export():
    items = _disabled_filtered_query(request.args).all()
    headers = ['所在组','姓名','身份证号','性别','民族','文化程度','手机','户籍地地址',
               '监护人姓名','监护人与残疾人关系','监护人手机','残疾证号','残疾类别','残疾等级','残疾详情',
               '困难生活补贴','重度护理补贴','服务事项','办理状态','办理进度','责任人','服务日期']
    summaries = _service_summary([r.id for r in items])
    rows = []
    for r in items:
        svc = summaries.get(r.id) or _empty_service_summary()
        rows.append([r.village_group or '', r.name or '', mask_id_card(_decrypt(r, 'id_card')),
                     r.gender or '', r.nation or '', r.education or '', mask_phone(_decrypt(r, 'phone')),
                     r.registered_address or '',
                     r.guardian_name or '', r.guardian_relation or '', mask_phone(_decrypt(r, 'guardian_phone')),
                     r.cert_no or '', r.disability_type or '', r.disability_level or '',
                     r.disability_detail or '',
                     r.subsidy_living or 0, r.subsidy_nursing or 0,
                     svc['service_item'], svc['service_status'], svc['service_progress'],
                     svc['service_owner'], svc['service_date']])
    log_operation(session.get('username',''), '残疾人管理', 'export', f'导出{len(rows)}条')
    return export_excel(headers, rows, '残疾人员数据.xlsx')


# ═══════════════════ 092 模板附件区（收口 utils/attachment_service，禁复制实现） ═══════════════════

from utils.attachment_service import register_attachment_routes as _register_attachment_routes  # noqa: E402
_register_attachment_routes(disabled_bp, 'disabled')
