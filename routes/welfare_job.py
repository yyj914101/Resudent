from flask import Blueprint, render_template, request, jsonify, send_file, session
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from models import (db, Resident, WelfareEmployment, WelfareFlexSubsidy,
                    WelfareJob)
from utils.decorators import login_required
from utils.helpers import json_response, paginate_query, log_operation, export_excel
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers
from utils.dict_enum import get_enum
from utils.masking import mask_id_card, mask_phone, mask_bank_card  # R12：列表/导出恒打码
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import io
import builtins
import logging

logger = logging.getLogger(__name__)

welfare_bp = Blueprint('welfare', __name__)

# R12 字段级加密管线（复用 utils.sensitive_fields，消除公益岗台账明文孤岛）
_WELFARE_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
    'bank_info': ('bank_info_enc', 'bank_info_hash', None),
}
_decrypt, _set_enc, _clause = make_sensitive_helpers(WelfareJob, _WELFARE_SENS_FIELDS, key_holder)


@welfare_bp.route('/')
@login_required
def page_list():
    # 075 RC-06：副标题「村名 · 10 类民政对象 · 在册 N 人」
    from utils.agg_cache import get_civil_objects_count
    return render_template('welfare_job/list.html', civil_headcount=get_civil_objects_count())


def _welfare_to_dict(item, mask=True):
    """单条序列化（R-12：list 与 detail 共用；列表/导出恒打码，详情明文）。"""
    id_card, phone, bank_info = (_decrypt(item, 'id_card'),
                                 _decrypt(item, 'phone'),
                                 _decrypt(item, 'bank_info'))
    if mask:
        id_card, phone, bank_info = mask_id_card(id_card), mask_phone(phone), mask_bank_card(bank_info)
    return {
        'id': item.id,
        'name': item.name,
        'village_group': item.village_group,
        'id_card': id_card,
        'phone': phone,
        'job_type': item.job_type,
        'start_date': item.start_date.isoformat() if item.start_date else '',
        'end_date': item.end_date.isoformat() if item.end_date else '',
        'monthly_amount': item.monthly_amount,
        'bank_info': bank_info,
    }


@welfare_bp.route('/api/list')
@login_required
def api_list():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    per_page = limit

    query = WelfareJob.query

    name = request.args.get('name', '', type=str).strip()
    village_group = request.args.get('village_group', '', type=str).strip()
    job_type = request.args.get('job_type', '', type=str).strip()
    id_card = request.args.get('id_card', '', type=str).strip()

    if name:
        query = query.filter(WelfareJob.name.like(f'%{name}%'))
    if village_group:
        query = query.filter(WelfareJob.village_group == village_group)
    if job_type:
        query = query.filter(WelfareJob.job_type == job_type)
    if id_card:
        query = query.filter(WelfareJob.id_card_hash == sha256_hash(id_card.strip().upper()))

    query = query.order_by(WelfareJob.id.desc())

    items, total, pages = paginate_query(query, page=page, per_page=per_page)

    data = [_welfare_to_dict(item) for item in items]

    return jsonify({
        'success': True,
        'data': data,
        'total': total,
        'page': page,
        'pages': pages
    })


@welfare_bp.route('/api/detail/<int:item_id>')
@login_required
def api_detail(item_id):
    """单条详情（R-12：编辑改走 detail）。"""
    item = WelfareJob.query.get(item_id)
    if not item:
        return json_response(False, '记录不存在')
    return json_response(True, data=_welfare_to_dict(item, mask=False))


@welfare_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    from flask import session
    try:
        item_id = request.form.get('id', 0, type=int)
        name = request.form.get('name', '').strip()
        if not name:
            return json_response(False, '姓名不能为空')

        village_group = request.form.get('village_group', '').strip()
        id_card = request.form.get('id_card', '').strip()
        phone = request.form.get('phone', '').strip()
        job_type = request.form.get('job_type', '').strip()
        monthly_amount = request.form.get('monthly_amount', 0, type=float)
        bank_info = request.form.get('bank_info', '').strip()

        start_date_str = request.form.get('start_date', '').strip()
        end_date_str = request.form.get('end_date', '').strip()
        start_date = None
        end_date = None
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            except ValueError:
                return json_response(False, '上岗日期格式不正确')
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                return json_response(False, '离岗日期格式不正确')

        if item_id:
            item = WelfareJob.query.get(item_id)
            if not item:
                return json_response(False, '记录不存在')
            item.name = name
            item.village_group = village_group
            _set_enc(item, 'id_card', id_card)
            _set_enc(item, 'phone', phone)
            item.job_type = job_type
            item.start_date = start_date
            item.end_date = end_date
            item.monthly_amount = monthly_amount
            _set_enc(item, 'bank_info', bank_info)
            log_operation(session.get('username', ''), 'welfare', 'edit',
                          f'编辑公益岗: {name}')
        else:
            item = WelfareJob(
                name=name,
                village_group=village_group,
                job_type=job_type,
                start_date=start_date,
                end_date=end_date,
                monthly_amount=monthly_amount,
            )
            _set_enc(item, 'id_card', id_card)
            _set_enc(item, 'phone', phone)
            _set_enc(item, 'bank_info', bank_info)
            db.session.add(item)
            log_operation(session.get('username', ''), 'welfare', 'add',
                          f'新增公益岗: {name}')

        db.session.commit()
        return json_response(True, '保存成功')

    except Exception:
        db.session.rollback()
        logger.exception('公益岗保存失败')
        return json_response(False, '保存失败，请稍后重试或联系管理员')


@welfare_bp.route('/api/delete/<int:id>', methods=['POST'])
@login_required
def api_delete(id):
    from flask import session
    item = WelfareJob.query.get(id)
    if not item:
        return json_response(False, '记录不存在')
    name = item.name
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username', ''), 'welfare', 'delete',
                  f'删除公益岗: {name}')
    return json_response(True, '已删除')


@welfare_bp.route('/api/export')
@login_required
def api_export():
    query = WelfareJob.query

    name = request.args.get('name', '', type=str).strip()
    village_group = request.args.get('village_group', '', type=str).strip()
    job_type = request.args.get('job_type', '', type=str).strip()
    # GLB-47：导出与筛选同口径——身份证条件按 hash 列匹配（R12，禁明文 LIKE）
    id_card = request.args.get('id_card', '', type=str).strip()

    if name:
        query = query.filter(WelfareJob.name.like(f'%{name}%'))
    if village_group:
        query = query.filter(WelfareJob.village_group == village_group)
    if job_type:
        query = query.filter(WelfareJob.job_type == job_type)
    if id_card:
        query = query.filter(WelfareJob.id_card_hash == sha256_hash(id_card.upper()))

    items = query.order_by(WelfareJob.id.desc()).all()

    headers = ['序号', '姓名', '所在组', '身份证号', '电话', '岗位类型',
               '上岗日期', '离岗日期', '月补贴金额', '银行信息']
    rows = []
    for i, item in enumerate(items, 1):
        rows.append([
            i,
            item.name,
            item.village_group or '',
            mask_id_card(_decrypt(item, 'id_card')),
            mask_phone(_decrypt(item, 'phone')),
            item.job_type or '',
            item.start_date.isoformat() if item.start_date else '',
            item.end_date.isoformat() if item.end_date else '',
            item.monthly_amount or 0,
            mask_bank_card(_decrypt(item, 'bank_info')),
        ])

    return export_excel(headers, rows, filename='公益岗.xlsx')


@welfare_bp.route('/api/template')
@login_required
def api_template():
    """Download Excel import template for welfare jobs."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '公益岗导入模板'
    headers = ['姓名','所在组','身份证号','电话','岗位类型','上岗日期','离岗日期','月补贴金额','银行信息']
    ws.append(headers)
    for ci in range(1, len(headers)+1):
        cell = ws.cell(row=1, column=ci)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(start_color='1a73e8', end_color='1a73e8', fill_type='solid')
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    log_operation(session.get('username',''), '公益岗管理', 'template', '下载公益岗导入模板')
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    as_attachment=True, download_name='公益岗导入模板.xlsx')


@welfare_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    """Import welfare job records from uploaded Excel file."""
    file = request.files.get('file')
    if not file:
        return json_response(False, '请选择文件')
    mode = request.form.get('mode', 'append')

    try:
        import openpyxl
        wb = openpyxl.load_workbook(file)
        ws = wb.active
        rows = builtins.list(ws.iter_rows(min_row=2, values_only=True))
    except Exception as e:
        return json_response(False, f'文件解析失败: {str(e)}')

    headers_list = [cell.value for cell in ws[1]]
    col = {}
    for i, h in enumerate(headers_list):
        if h: col[h.strip()] = i

    def gv(name):
        val = rows[row_idx][col[name]] if name in col else None
        return str(val).strip() if val is not None else ''

    def pd(v):
        if v is None: return None
        from datetime import date as dt_date
        if isinstance(v, datetime): return v.date()
        if isinstance(v, dt_date): return v
        if isinstance(v, (int, float)) and v > 1:
            from datetime import timedelta
            try:
                base = datetime(1899, 12, 30)
                return (base + timedelta(days=int(v))).date()
            except Exception:
                pass
        s = str(v).strip()
        if not s: return None
        s = s.split(' ')[0]
        for fmt in ('%Y-%m-%d','%Y/%m/%d','%Y.%m.%d','%Y年%m月%d日'):
            try: return datetime.strptime(s, fmt).date()
            except Exception: pass
        return None

    imported = 0; updated = 0; skipped = 0; errors = []

    for row_idx, row in enumerate(rows):
        if not row or all(c is None for c in row): continue
        name = gv('姓名')
        if not name:
            errors.append(f'第{row_idx+2}行: 姓名不能为空')
            continue

        id_card = gv('身份证号')
        existing = None
        if id_card:
            existing = WelfareJob.query.filter_by(id_card_hash=sha256_hash(id_card.upper()), name=name).first()
        if not existing:
            existing = WelfareJob.query.filter_by(name=name).first()

        if existing:
            if mode == 'append':
                skipped += 1; continue
            r = existing
        else:
            r = WelfareJob()
            db.session.add(r)

        r.name = name
        r.village_group = gv('所在组')
        _set_enc(r, 'id_card', id_card)
        _set_enc(r, 'phone', gv('电话'))
        r.job_type = gv('岗位类型')
        r.start_date = pd(gv('上岗日期'))
        r.end_date = pd(gv('离岗日期'))
        try: r.monthly_amount = float(gv('月补贴金额')) if gv('月补贴金额') else 0
        except Exception: r.monthly_amount = 0
        _set_enc(r, 'bank_info', gv('银行信息'))

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

    log_operation(session.get('username',''), '公益岗管理', 'import', f'导入公益岗 {imported}新增 {updated}更新 {skipped}跳过')
    return json_response(success=len(errors)==0, msg=msg, data={
        'imported': imported, 'updated': updated, 'skipped': skipped, 'errors': errors[:30],
    })


# ══════════════════════════════════════════════════════════════════════════════
# 084 就业服务：就业状态登记 + 灵活就业补贴申报（双台账同页，tab 切换）
# 结构真源：models/welfare_employment.py、models/welfare_flex_subsidy.py
# 口径：列表打码 / 详情解密回填 / 状态迁移唯一入口 / 导出服务端生成
# ══════════════════════════════════════════════════════════════════════════════

TAB_EMPLOYMENT = 'employment'
TAB_SUBSIDY = 'subsidy'
EMPLOYMENT_TABS = (TAB_EMPLOYMENT, TAB_SUBSIDY)

# 每张新表各建一套敏感字段 helper（R12 字段级加密，禁跨模型复用绑定）
_EMPLOYMENT_SENS_FIELDS = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
_emp_decrypt, _emp_set_enc, _emp_clause = make_sensitive_helpers(
    WelfareEmployment, _EMPLOYMENT_SENS_FIELDS, key_holder)

_SUBSIDY_SENS_FIELDS = {'id_card': ('id_card_enc', 'id_card_hash', None)}
_sub_decrypt, _sub_set_enc, _sub_clause = make_sensitive_helpers(
    WelfareFlexSubsidy, _SUBSIDY_SENS_FIELDS, key_holder)

# 页面下拉字典（与 utils/dict_seed 同源，不写字面量枚举）
EMPLOYMENT_DICT_CATEGORIES = ('就业状态', '就业类型', '就业行业',
                              '灵活就业补贴类型', '补贴申报状态')

# 补贴申报状态机（唯一真源）：任何回退/已发放再改均非法
SUBSIDY_TRANSITIONS = {
    ('待申报', '已申报'),
    ('已申报', '已审核'),
    ('已审核', '已发放'),
    ('待申报', '未通过'),
    ('已申报', '未通过'),
    ('已审核', '未通过'),
}


def _text(value):
    return str(value or '').strip()


def _tab_from_request(default=TAB_EMPLOYMENT):
    """tab 归一：非法值回落默认台账（读接口容错，写接口另行校验）。"""
    tab = _text(request.values.get('tab')).lower()
    return tab if tab in EMPLOYMENT_TABS else default


def _parse_date_field(raw, label):
    """日期解析：空 → None；非法 → ValueError('XX格式应为YYYY-MM-DD')。"""
    value = _text(raw)
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(f'{label}格式应为YYYY-MM-DD') from None


def _amount_text(value):
    """金额输出统一两位小数字符串（Numeric(10,2) → Decimal）。"""
    if value is None or value == '':
        return ''
    try:
        return f'{value:.2f}'
    except (TypeError, ValueError):
        return str(value)


def _parse_amount(raw):
    """补贴金额：≥0，归一到两位小数（四舍五入）；空 → None。"""
    value = _text(raw)
    if not value:
        return None
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError('申报金额格式不正确') from None
    if amount < 0:
        raise ValueError('申报金额不得小于0')
    return amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _set_enc_guarded(setter, item, field, raw):
    """写敏感字段：掩码回显值（含 '*'）不落库，避免打码串被加密成密文。

    详情回填后直接保存时前端可能把 '340104********1234' 原样提交，
    此处跳过即保留原密文（等价「不修改」）。
    """
    value = _text(raw)
    if '*' in value:
        return
    setter(item, field, value)


def _normalize_id_card(raw, existing_hash=None):
    """身份证必填 + 宽松格式校验（15/18 位；18 位末位可为 X/x）。

    背景（R-084-01）：`welfare_employment.id_card_hash` 与 `uq_flex_subsidy` 是防重
    唯一键，而 SQLite 唯一索引对 NULL 不去重——证件号为空时防重形同虚设。
    模型列不能改 `nullable=False`（会破坏 R12「加密后原列置 NULL」约定），
    故必填校验落在路由层。

    返回：规范化（大写）证件号；返回 `''` 表示「保持原密文」（掩码回显场景）。
    """
    value = _text(raw)
    if not value:
        raise ValueError('身份证号不能为空')
    if '*' in value:
        # 掩码回显只在「该记录已有密文」时可放行，否则视为非法输入（防新建落空 hash）
        if existing_hash:
            return ''
        raise ValueError('身份证号格式不正确')
    body = value.upper()
    if len(body) == 15:
        valid = body.isdigit()
    elif len(body) == 18:
        valid = body[:17].isdigit() and (body[17].isdigit() or body[17] == 'X')
    else:
        valid = False
    if not valid:
        raise ValueError('身份证号格式不正确')
    return body


def _iso(value):
    return value.isoformat() if value else ''
def _employment_to_dict(item, mask=True):
    id_card = _emp_decrypt(item, 'id_card')
    phone = _emp_decrypt(item, 'phone')
    if mask:
        id_card, phone = mask_id_card(id_card), mask_phone(phone)
    return {
        'id': item.id,
        'name': item.name or '',
        'gender': item.gender or '',
        'village_group': item.village_group or '',
        'id_card': id_card,
        'phone': phone,
        'employment_status': item.employment_status or '',
        'employment_type': item.employment_type or '',
        'employer': item.employer or '',
        'industry': item.industry or '',
        'work_location': item.work_location or '',
        'start_date': _iso(item.start_date),
        'update_date': _iso(item.update_date),
        'remark': item.remark or '',
    }


def _subsidy_to_dict(item, mask=True):
    id_card = _sub_decrypt(item, 'id_card')
    if mask:
        id_card = mask_id_card(id_card)
    return {
        'id': item.id,
        'name': item.name or '',
        'id_card': id_card,
        'apply_year': item.apply_year,
        'batch_no': item.batch_no or '',
        'subsidy_type': item.subsidy_type or '',
        'amount': _amount_text(item.amount),
        'apply_status': item.apply_status or '',
        'apply_date': _iso(item.apply_date),
        'review_date': _iso(item.review_date),
        'issue_date': _iso(item.issue_date),
        'reject_reason': item.reject_reason or '',
        'remark': item.remark or '',
    }


def _model_for_tab(tab):
    return WelfareFlexSubsidy if tab == TAB_SUBSIDY else WelfareEmployment


def _employment_query():
    """就业登记列表查询（keyword 身份证走 hash 等值，禁明文 LIKE）。"""
    query = WelfareEmployment.query
    keyword = _text(request.args.get('keyword'))
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(or_(
            WelfareEmployment.name.like(like),
            WelfareEmployment.village_group.like(like),
            WelfareEmployment.employer.like(like),
            WelfareEmployment.work_location.like(like),
            WelfareEmployment.industry.like(like),
            WelfareEmployment.id_card_hash == sha256_hash(keyword.upper()),
        ))
    for field in ('village_group', 'employment_status', 'employment_type'):
        want = _text(request.args.get(field))
        if want:
            query = query.filter(getattr(WelfareEmployment, field) == want)
    return query


def _subsidy_query():
    """补贴申报列表查询。"""
    query = WelfareFlexSubsidy.query
    keyword = _text(request.args.get('keyword'))
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(or_(
            WelfareFlexSubsidy.name.like(like),
            WelfareFlexSubsidy.batch_no.like(like),
            WelfareFlexSubsidy.subsidy_type.like(like),
            WelfareFlexSubsidy.id_card_hash == sha256_hash(keyword.upper()),
        ))
    for field in ('apply_status', 'subsidy_type'):
        want = _text(request.args.get(field))
        if want:
            query = query.filter(getattr(WelfareFlexSubsidy, field) == want)
    apply_year = request.args.get('apply_year', type=int)
    if apply_year:
        query = query.filter(WelfareFlexSubsidy.apply_year == apply_year)
    return query


@welfare_bp.route('/employment/')
@login_required
def employment_page():
    """084 就业服务页面（就业登记 / 补贴申报双 tab）。"""
    return render_template('employment/employment.html')


@welfare_bp.route('/employment/api/options')
@login_required
def employment_api_options():
    """页面下拉数据源：村组列表 + 字典项（分类名 → label 列表）。"""
    village_groups = []
    try:
        rows = (db.session.query(Resident.village_group)
                .filter(Resident.village_group.isnot(None),
                        Resident.village_group != '')
                .distinct()
                .order_by(Resident.village_group)
                .limit(50)
                .all())
        village_groups = [str(row[0]).strip() for row in rows
                          if str(row[0] or '').strip()]
    except Exception as exc:
        logger.warning('就业服务村组下拉降级：%s', exc)
        village_groups = []  # 降级：村组取数失败不阻塞页面（下拉可手填）
    return json_response(True, '', data={
        'village_groups': village_groups,
        'dicts': {name: list(get_enum(name)) for name in EMPLOYMENT_DICT_CATEGORIES},
    })


@welfare_bp.route('/employment/api/list')
@login_required
def employment_api_list():
    tab = _tab_from_request()
    page = max(request.args.get('page', 1, type=int), 1)
    limit = min(max(request.args.get('limit', 20, type=int), 1), 100)

    if tab == TAB_SUBSIDY:
        query = _subsidy_query().order_by(WelfareFlexSubsidy.apply_year.desc(),
                                          WelfareFlexSubsidy.id.desc())
        items, total, _pages = paginate_query(query, page, limit)
        data = [_subsidy_to_dict(item) for item in items]
    else:
        query = _employment_query().order_by(WelfareEmployment.update_date.desc(),
                                             WelfareEmployment.id.desc())
        items, total, _pages = paginate_query(query, page, limit)
        data = [_employment_to_dict(item) for item in items]

    return json_response(True, '', data=data, total=total)


@welfare_bp.route('/employment/api/detail/<int:item_id>')
@login_required
def employment_api_detail(item_id):
    """单条详情（编辑回填）：返回解密原值，app.py after_request 对非 view_sensitive 二次脱敏。"""
    tab = _tab_from_request()
    item = _model_for_tab(tab).query.get(item_id)
    if not item:
        return json_response(False, '记录不存在')
    if tab == TAB_SUBSIDY:
        return json_response(True, '', data=_subsidy_to_dict(item, mask=False))
    return json_response(True, '', data=_employment_to_dict(item, mask=False))


def _save_employment(item_id):
    """就业登记保存：校验顺序 name → 身份证 → 枚举 → 日期格式。

    可写白名单（表单键，其余键一律忽略）：
    name / gender / village_group / id_card / phone / employment_status /
    employment_type / employer / industry / work_location / start_date /
    update_date / remark。created_by 由登录会话写入，不接受表单覆盖。
    id_card 必填且 15/18 位（R-084-01：hash 为 NULL 时唯一约束不生效）。
    """
    name = _text(request.form.get('name'))
    if not name:
        raise ValueError('姓名不能为空')

    item = WelfareEmployment.query.get(item_id) if item_id else None
    if item_id and not item:
        raise ValueError('记录不存在')
    id_card = _normalize_id_card(
        request.form.get('id_card'),
        existing_hash=(item.id_card_hash if item else None))

    employment_status = _text(request.form.get('employment_status'))
    if not employment_status:
        raise ValueError('就业状态不能为空')
    if employment_status not in get_enum('就业状态'):
        raise ValueError('就业状态取值不合法')

    employment_type = _text(request.form.get('employment_type'))
    if employment_type and employment_type not in get_enum('就业类型'):
        raise ValueError('就业类型取值不合法')

    industry = _text(request.form.get('industry'))
    if industry and industry not in get_enum('就业行业'):
        raise ValueError('所属行业取值不合法')

    start_date = _parse_date_field(request.form.get('start_date'), '就业起始日期')
    update_date = _parse_date_field(request.form.get('update_date'), '状态更新日期')

    action = 'edit' if item else 'add'
    if not item:
        item = WelfareEmployment()

    item.name = name
    item.gender = _text(request.form.get('gender'))
    item.village_group = _text(request.form.get('village_group'))
    if id_card:
        _emp_set_enc(item, 'id_card', id_card)
    _set_enc_guarded(_emp_set_enc, item, 'phone', request.form.get('phone'))
    item.employment_status = employment_status
    item.employment_type = employment_type
    item.employer = _text(request.form.get('employer'))
    item.industry = industry
    item.work_location = _text(request.form.get('work_location'))
    item.start_date = start_date
    item.update_date = update_date
    item.remark = _text(request.form.get('remark'))
    db.session.add(item)
    return item, action


def _save_subsidy(item_id):
    """补贴申报保存：校验顺序 name → 身份证 → 年度/类型/金额/状态 → 日期格式 → 日期先后。

    可写白名单（表单键，其余键一律忽略）：
    name / id_card / apply_year / batch_no / subsidy_type / amount / apply_date /
    review_date / issue_date / reject_reason / remark。created_by 由登录会话写入。
    id_card 必填且 15/18 位（R-084-01：`uq_flex_subsidy` 对 NULL hash 不去重）。
    `apply_status`：**只读校验、不写入**——值必须在字典内（非法值仍失败），
    但状态位只经 /api/transition 状态机推进（R-084-02），避免保存接口绕过状态机。
    """
    name = _text(request.form.get('name'))
    if not name:
        raise ValueError('姓名不能为空')

    item = WelfareFlexSubsidy.query.get(item_id) if item_id else None
    if item_id and not item:
        raise ValueError('记录不存在')
    id_card = _normalize_id_card(
        request.form.get('id_card'),
        existing_hash=(item.id_card_hash if item else None))

    raw_year = _text(request.form.get('apply_year'))
    if not raw_year:
        raise ValueError('申报年度不能为空')
    try:
        apply_year = int(raw_year)
    except (TypeError, ValueError):
        raise ValueError('申报年度应为整数年份') from None
    if not 2000 <= apply_year <= 2100:
        raise ValueError('申报年度应在2000~2100之间')

    subsidy_type = _text(request.form.get('subsidy_type'))
    if not subsidy_type:
        raise ValueError('补贴类型不能为空')
    if subsidy_type not in get_enum('灵活就业补贴类型'):
        raise ValueError('补贴类型取值不合法')

    amount = _parse_amount(request.form.get('amount'))

    # 只校验取值合法性，不写入（状态变更唯一入口为 /api/transition）
    apply_status = _text(request.form.get('apply_status'))
    if apply_status and apply_status not in get_enum('补贴申报状态'):
        raise ValueError('申报状态取值不合法')

    apply_date = _parse_date_field(request.form.get('apply_date'), '申报日期')
    review_date = _parse_date_field(request.form.get('review_date'), '审核日期')
    issue_date = _parse_date_field(request.form.get('issue_date'), '发放日期')

    if apply_date and review_date and review_date < apply_date:
        raise ValueError('审核日期不得早于申报日期')
    if review_date and issue_date and issue_date < review_date:
        raise ValueError('发放日期不得早于审核日期')
    if apply_date and issue_date and not review_date and issue_date < apply_date:
        raise ValueError('发放日期不得早于申报日期')

    action = 'edit' if item else 'add'
    if not item:
        item = WelfareFlexSubsidy()

    item.name = name
    if id_card:
        _sub_set_enc(item, 'id_card', id_card)
    item.apply_year = apply_year
    item.batch_no = _text(request.form.get('batch_no'))
    item.subsidy_type = subsidy_type
    item.amount = amount
    # 084：状态只经 /api/transition 推进；新建固定落「待申报」，编辑时忽略表单状态位
    # （否则可通过保存接口绕过状态机；R-084-02）
    if item.id is None:
        item.apply_status = '待申报'
    item.apply_date = apply_date
    item.review_date = review_date
    item.issue_date = issue_date
    item.reject_reason = _text(request.form.get('reject_reason'))
    item.remark = _text(request.form.get('remark'))
    db.session.add(item)
    return item, action


@welfare_bp.route('/employment/api/save', methods=['POST'])
@login_required
def employment_api_save():
    from flask import session
    tab = _text(request.values.get('tab')).lower()
    if tab not in EMPLOYMENT_TABS:
        return json_response(False, '台账类型不支持')
    item_id = request.form.get('id', 0, type=int)
    try:
        if tab == TAB_SUBSIDY:
            item, action = _save_subsidy(item_id)
            dup_msg = '该人员本年度该补贴类型已申报'
        else:
            item, action = _save_employment(item_id)
            dup_msg = '该身份证已存在就业登记，请改为编辑'
        if action == 'add':
            item.created_by = session.get('username', '') or None
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return json_response(False, dup_msg)
    except ValueError as exc:
        db.session.rollback()
        return json_response(False, str(exc))
    except Exception:
        db.session.rollback()
        logger.exception('就业服务保存失败（tab=%s）', tab)
        return json_response(False, '保存失败，请稍后重试或联系管理员')

    log_operation(session.get('username', ''), 'welfare', action,
                  f'{tab} 保存: {item.name}(id={item.id})')
    return json_response(True, '保存成功')


@welfare_bp.route('/employment/api/delete/<int:item_id>', methods=['POST'])
@login_required
def employment_api_delete(item_id):
    from flask import session
    tab = _tab_from_request()
    item = _model_for_tab(tab).query.get(item_id)
    if not item:
        return json_response(False, '记录不存在')
    summary = f'{tab} 删除: {item.name}(id={item.id})'
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username', ''), 'welfare', 'delete', summary)
    return json_response(True, '已删除')


@welfare_bp.route('/employment/api/transition', methods=['POST'])
@login_required
def employment_api_transition():
    """补贴申报状态迁移（唯一入口）：非法迁移返回 HTTP 409。

    R-086-03 同源一致：迁移表单可携带随迁字段 `issue_date`（迁「已发放」用）与
    `reject_reason`（迁「未通过」用），本次提交有值则随状态一起原子落库；
    提交值为空则沿用库内值。校验全部前置完成后才写库——失败不落库。
    """
    from flask import session
    tab = _tab_from_request()
    if tab != TAB_SUBSIDY:
        return json_response(False, '仅补贴申报支持状态迁移')

    item_id = request.form.get('id', 0, type=int)
    to_status = _text(request.form.get('to_status'))
    item = WelfareFlexSubsidy.query.get(item_id) if item_id else None
    if not item:
        return json_response(False, '记录不存在')
    if not to_status:
        return json_response(False, '目标状态不能为空')

    current = _text(item.apply_status) or '待申报'
    if (current, to_status) not in SUBSIDY_TRANSITIONS:
        return jsonify({'success': False,
                        'msg': f'非法状态迁移: {current}→{to_status}'}), 409

    # ── 随迁字段解析（只算候选值，不写库；解析失败即整体失败）──
    raw_issue = _text(request.form.get('issue_date'))
    raw_reject = _text(request.form.get('reject_reason'))
    issue_date = item.issue_date
    reject_reason = _text(item.reject_reason)
    try:
        if raw_issue:
            issue_date = _parse_date_field(raw_issue, '发放日期')
    except ValueError as exc:
        db.session.rollback()
        return json_response(False, str(exc))
    if raw_reject:
        reject_reason = raw_reject

    if to_status == '已发放':
        if not issue_date:
            return json_response(False, '请先填写发放日期')
        if item.review_date and issue_date < item.review_date:
            return json_response(False, '发放日期不得早于审核日期')
        if not item.review_date and item.apply_date and issue_date < item.apply_date:
            return json_response(False, '发放日期不得早于申报日期')
    if to_status == '未通过' and not reject_reason:
        return json_response(False, '请填写未通过原因')

    # ── 校验通过后才落库（原子：状态 + 随迁字段一次 commit）──
    # 随迁字段只在对应终态迁移上落库：已发放→issue_date，未通过→reject_reason；
    # 其它迁移即使误带也不写入（防弹窗复用时的陈旧预填串号）。
    if to_status == '已发放':
        item.issue_date = issue_date
    if to_status == '未通过':
        item.reject_reason = reject_reason
    item.apply_status = to_status
    db.session.commit()
    log_operation(session.get('username', ''), 'welfare', 'transition',
                  f'补贴状态迁移: {item.name} {current}→{to_status}')
    return json_response(True, f'状态已更新为{to_status}', data={
        'apply_status': item.apply_status,
        'issue_date': _iso(item.issue_date),
        'reject_reason': item.reject_reason or '',
    })


@welfare_bp.route('/employment/api/export')
@login_required
def employment_api_export():
    """导出（身份证/电话打码口径与列表一致，服务端 openpyxl 生成）。"""
    from flask import session
    tab = _tab_from_request()
    stamp = datetime.now().strftime('%Y%m%d')

    if tab == TAB_SUBSIDY:
        items = _subsidy_query().order_by(WelfareFlexSubsidy.apply_year.desc(),
                                          WelfareFlexSubsidy.id.desc()).all()
        headers = ['序号', '姓名', '身份证号', '申报年度', '申报批次', '补贴类型',
                   '申报金额', '申报状态', '申报日期', '审核日期', '发放日期',
                   '未通过原因', '备注']
        rows = []
        for index, item in enumerate(items, 1):
            rows.append([
                index,
                item.name or '',
                mask_id_card(_sub_decrypt(item, 'id_card')),
                item.apply_year or '',
                item.batch_no or '',
                item.subsidy_type or '',
                _amount_text(item.amount),
                item.apply_status or '',
                _iso(item.apply_date),
                _iso(item.review_date),
                _iso(item.issue_date),
                item.reject_reason or '',
                item.remark or '',
            ])
        filename = f'灵活就业补贴申报_{stamp}.xlsx'
    else:
        items = _employment_query().order_by(WelfareEmployment.update_date.desc(),
                                             WelfareEmployment.id.desc()).all()
        headers = ['序号', '姓名', '性别', '所在组', '身份证号', '联系电话',
                   '就业状态', '就业类型', '就业单位或经营内容', '所属行业',
                   '务工地点', '就业起始日期', '状态更新日期', '备注']
        rows = []
        for index, item in enumerate(items, 1):
            rows.append([
                index,
                item.name or '',
                item.gender or '',
                item.village_group or '',
                mask_id_card(_emp_decrypt(item, 'id_card')),
                mask_phone(_emp_decrypt(item, 'phone')),
                item.employment_status or '',
                item.employment_type or '',
                item.employer or '',
                item.industry or '',
                item.work_location or '',
                _iso(item.start_date),
                _iso(item.update_date),
                item.remark or '',
            ])
        filename = f'就业服务_{stamp}.xlsx'

    log_operation(session.get('username', ''), 'welfare', 'export',
                  f'导出{filename}（{len(rows)} 行）')
    return export_excel(headers, rows, filename=filename)

