# -*- coding: utf-8 -*-
"""routes/pension.py — 088 养老保险（pension 新蓝图 + 软表参保名册 + 两指标）。

参照 medical_insurance 的「门面页」体验与 099 shop 的「软表挂载门面」薄路由范式
（ADR-027：固定菜单组 → module_key → dynamic_table.code），数据层为软表承载：
pension_roster（m031 预置）参保名册，列语义按字段清单-v1 §3.4.2 养老段规范列名。

本文件只做「参数提取 → 转调门面/引擎 → JSON / 渲染」：
- 加密/指纹/字段推断全部在 utils/roster_facade.py 与 utils/softtable_engine.py（零平行实现）；
- 两指标（参保率 pension_coverage / 认证完成率 pension_cert_rate）唯一取数入口
  MetricsEngine.query（utils/metrics.py 注册表），页面代理 /api/stats 与
  统一统计入口 /api/stats/query、引擎直调三处同值；
- 权限走业务模块位 BLUEPRINT_MODULES['pension'] + 视图命名关键字
  （list/add/edit/upload/create/export）由 enforce_role_permissions 自动推断。
"""
import json
import os
import secrets

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for

from models import db
from utils.decorators import login_required
from utils.helpers import json_response, log_operation, export_excel
from utils.metrics import MetricsEngine

pension_bp = Blueprint('pension', __name__)

# ================================================================== 门面页 + 指标代理

# 软表承载的参保名册（m031 预置行；字段由首次上传引导确认生成，ADR-025 决策 3）
PENSION_ROSTER_CODE = 'pension_roster'

# 字段清单-v1 §3.4.2 养老段规范列（引导文案；[占位]/待确认列不猜字段）
PENSION_CANONICAL_COLUMNS = ['姓名', '身份证号', '参保类型', '参保状态', '缴费年度',
                             '待遇认证状态']
PENSION_UNCONFIRMED_COLUMNS = ['参保地']  # 上级是否要求单列待定（§五待确认）


@pension_bp.route('/')
@login_required
def index():
    """养老保险一级页：直接渲染农保大体量物理台账（与医保参保管理同构，无软表建册二级跳转）。"""
    _med._ensure_columns()
    _med._ensure_medical_dicts()
    return render_template('pension/ledger.html')


@pension_bp.route('/api/stats')
@login_required
def api_stats():
    """指标查询代理：唯一取数入口 MetricsEngine.query（页面/看板/引擎三处同值）。"""
    try:
        coverage = MetricsEngine.query('pension_coverage')
        cert_rate = MetricsEngine.query('pension_cert_rate')
    except ValueError as exc:
        return json_response(False, str(exc)), 400
    return json_response(True, '', data={
        'coverage': {'total': coverage['total'], 'data': coverage['data'],
                     'buckets': coverage['buckets'], 'label': coverage['label']},
        'cert_rate': {'total': cert_rate['total'], 'data': cert_rate['data'],
                      'buckets': cert_rate['buckets'], 'label': cert_rate['label']},
    })


# ================================================================== 088 软表挂载门面（薄路由，099 shop 同款）
# 业务判断全部收口在 utils/roster_facade.py；本段只做「参数提取 → 转调门面 → 包装」。

import config  # noqa: E402  （与 shop.py 门面段同款：临时目录归属 config）

from utils import roster_facade as facade  # noqa: E402
from utils import softtable_engine as eng  # noqa: E402

from utils.roster_routes import register_roster_routes as _register_roster_routes  # noqa: E402
_register_roster_routes(pension_bp, module_key='pension', group_name='社会保障',
                        log_module='养老保险', nav_module='social')


# ================================================================== Step2：农保大体量物理表 + A1 异步分批导入
# 口径暂与居民医保一致，复用 MedicalInsurance 物理表，以 insurance_type='rural_pension' 严格隔离。
from datetime import datetime as _dt  # noqa: E402
from sqlalchemy import or_ as _or  # noqa: E402
from models import MedicalInsurance, Resident  # noqa: E402
from utils import medical_field_catalog as _mfc  # noqa: E402
from utils.crypto import sha256_hash as _sha256  # noqa: E402
from utils.helpers import paginate_query as _paginate_query  # noqa: E402
from utils.domain_batch import register_domain_batch_routes as _register_domain_batch_routes  # noqa: E402
from routes import medical_insurance as _med  # noqa: E402


def _rp_query():
    q = MedicalInsurance.query.filter(MedicalInsurance.insurance_type == _mfc.PENSION_INSURANCE_TYPE)
    year = request.args.get('payment_year', type=int)
    keyword = (request.args.get('keyword') or '').strip()
    village_group = (request.args.get('village_group') or '').strip()
    is_paid = (request.args.get('is_paid') or '').strip()
    insurance_status = (request.args.get('insurance_status') or '').strip()
    link_status = (request.args.get('link_status') or '').strip()
    verify_status = (request.args.get('verify_status') or '').strip()
    data_source = (request.args.get('data_source') or '').strip()
    benefit_cert_status = (request.args.get('benefit_cert_status') or '').strip()
    if year:
        q = q.filter(MedicalInsurance.payment_year == year)
    if keyword:
        like = f'%{keyword}%'
        q = q.filter(_or(MedicalInsurance.name.like(like),
                         MedicalInsurance.id_card_hash == _sha256(keyword.upper())))
    if village_group:
        q = q.filter(MedicalInsurance.village_group == village_group)
    if is_paid:
        q = q.filter(MedicalInsurance.is_paid == is_paid)
    if insurance_status:
        q = q.filter(MedicalInsurance.insurance_status == insurance_status)
    if link_status:
        q = q.filter(MedicalInsurance.link_status == link_status)
    if verify_status:
        q = q.filter(MedicalInsurance.verify_status == verify_status)
    if data_source:
        q = q.filter(MedicalInsurance.data_source == data_source)
    if benefit_cert_status:
        q = q.filter(MedicalInsurance.benefit_cert_status == benefit_cert_status)
    return q


@pension_bp.route('/ledger')
@login_required
def ledger():
    """兼容旧书签：重定向至 /pension/ 农保大表台账首页。"""
    return redirect(url_for('pension.index'))


@pension_bp.route('/api/options')
@login_required
def api_pension_options():
    _med._ensure_medical_dicts()
    groups = [r[0] for r in db.session.query(Resident.village_group).filter(Resident.village_group.isnot(None)).distinct().all() if r[0]]
    years = [r[0] for r in db.session.query(MedicalInsurance.payment_year).filter(
        MedicalInsurance.insurance_type == _mfc.PENSION_INSURANCE_TYPE,
        MedicalInsurance.payment_year.isnot(None),
    ).distinct().order_by(MedicalInsurance.payment_year.desc()).all()]
    cur_y = _dt.now().year
    if cur_y not in years:
        years.insert(0, cur_y)
    return json_response(True, data={
        'years': years,
        'groups': sorted(set(groups)),
        'insurance_status': _med._dict_values('医保参保情况', _med.DEFAULT_INSURANCE_STATUS),
        'state_paid_identity': _med._dict_values('医保国家代缴身份', _med.DEFAULT_STATE_IDENTITIES),
        'deduction_reason': _med._dict_values('医保核减原因', _med.DEFAULT_DEDUCTION_REASONS),
        'data_sources': _med._dict_values('医保数据来源', _med.DATA_SOURCES),
        'link_statuses': _med._dict_values('医保关联状态', _med.LINK_STATUSES),
        'verify_statuses': _med._dict_values('医保核对状态', _med.VERIFY_STATUSES),
        'benefit_cert_statuses': _med._dict_values('医保待遇认证状态', _med.BENEFIT_CERT_STATUSES),
        'insurance_type': _mfc.PENSION_INSURANCE_TYPE,
        'field_catalog': {
            'fields': {_mfc.PENSION_INSURANCE_TYPE: _mfc.pension_fields()},
        },
    })


@pension_bp.route('/api/list')
@login_required
def api_pension_list():
    _med._ensure_columns()
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    items, total, pages = _paginate_query(_rp_query().order_by(MedicalInsurance.id.desc()), page=page, per_page=limit)
    return jsonify({'success': True, 'data': [_med._serialize(i) for i in items],
                    'total': total, 'page': page, 'pages': pages})


@pension_bp.route('/api/detail/<int:item_id>')
@login_required
def api_pension_detail(item_id):
    item = MedicalInsurance.query.filter_by(id=item_id, insurance_type=_mfc.PENSION_INSURANCE_TYPE).first()
    if not item:
        return json_response(False, '记录不存在')
    return json_response(True, data=_med._serialize(item))


@pension_bp.route('/api/save', methods=['POST'])
@login_required
def api_pension_save():
    _med._ensure_columns()
    item_id = request.form.get('id', 0, type=int)
    if item_id:
        item = MedicalInsurance.query.filter_by(id=item_id, insurance_type=_mfc.PENSION_INSURANCE_TYPE).first()
        if not item:
            return json_response(False, '记录不存在')
    else:
        item = MedicalInsurance(insurance_type=_mfc.PENSION_INSURANCE_TYPE)
    item.name = (request.form.get('name') or '').strip()
    id_card_raw = (request.form.get('id_card') or '').strip()
    if not item.name:
        return json_response(False, '姓名不能为空')
    _med._set_enc(item, 'id_card', id_card_raw)
    item.gender = (request.form.get('gender') or '').strip()
    item.address = (request.form.get('address') or '').strip()
    item.village_group = (request.form.get('village_group') or '').strip()
    phone_val = (request.form.get('contact_phone') or '').strip()
    _med._set_enc(item, 'contact_phone', phone_val)
    _med._set_enc(item, 'mobile_phone', (request.form.get('mobile_phone') or phone_val).strip())
    item.payment_year = request.form.get('payment_year', type=int) or _dt.now().year
    item.household_name = (request.form.get('household_name') or '').strip()
    item.household_property = (request.form.get('household_property') or '').strip()
    item.insurance_status = (request.form.get('insurance_status') or '').strip() or _med.DEFAULT_INSURANCE_STATUS[0]
    item.is_paid = (request.form.get('is_paid') or '否').strip() or '否'
    item.is_state_paid = (request.form.get('is_state_paid') or '否').strip() or '否'
    item.state_paid_identity = (request.form.get('state_paid_identity') or '').strip()
    item.is_deductible = (request.form.get('is_deductible') or '否').strip() or '否'
    item.deduction_reason = (request.form.get('deduction_reason') or '').strip()
    item.is_relocated = (request.form.get('is_relocated') or '否').strip() or '否'
    item.verify_status = (request.form.get('verify_status') or item.verify_status or _med.VERIFY_STATUSES[0]).strip()
    item.exception_note = (request.form.get('exception_note') or '').strip()
    bcs = (request.form.get('benefit_cert_status') or '').strip()
    item.benefit_cert_status = bcs if bcs in _med.BENEFIT_CERT_STATUSES else '未认证'
    item.remark = (request.form.get('remark') or '').strip()
    item.data_source = (request.form.get('data_source') or _med.DATA_SOURCES[2]).strip() or _med.DATA_SOURCES[2]
    item.insurance_type = _mfc.PENSION_INSURANCE_TYPE
    _med._fill_from_resident(item, _med._find_resident(item.name, id_card_raw))
    db.session.add(item)
    db.session.commit()
    from utils.agg_cache import invalidate as _metrics_invalidate
    _metrics_invalidate()
    log_operation(session.get('username', ''), '农保参保管理', 'edit' if item_id else 'add', f'保存农保参保记录：{item.name}')
    return json_response(True, '保存成功', data={'id': item.id})


@pension_bp.route('/api/delete/<int:item_id>', methods=['POST'])
@login_required
def api_pension_delete(item_id):
    item = MedicalInsurance.query.filter_by(id=item_id, insurance_type=_mfc.PENSION_INSURANCE_TYPE).first()
    if not item:
        return json_response(False, '记录不存在')
    name = item.name
    db.session.delete(item)
    db.session.commit()
    from utils.agg_cache import invalidate as _metrics_invalidate
    _metrics_invalidate()
    log_operation(session.get('username', ''), '农保参保管理', 'delete', f'删除农保参保记录：{name}')
    return json_response(True, '已删除')


@pension_bp.route('/api/batch-paid', methods=['POST'])
@login_required
def api_pension_batch_paid():
    ids = request.form.getlist('ids[]') or request.form.getlist('ids')
    clean_ids = []
    for raw in ids:
        try:
            clean_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not clean_ids:
        return json_response(False, '请先勾选需要设置的记录')
    items = MedicalInsurance.query.filter(
        MedicalInsurance.id.in_(clean_ids),
        MedicalInsurance.insurance_type == _mfc.PENSION_INSURANCE_TYPE,
    ).all()
    for item in items:
        item.is_paid = '是'
    db.session.commit()
    from utils.agg_cache import invalidate as _metrics_invalidate
    _metrics_invalidate()
    msg = f'已将 {len(items)} 条农保记录设置为已缴费'
    log_operation(session.get('username', ''), '农保参保管理', 'batch_paid', msg)
    return json_response(True, msg, data={'updated': len(items)})


@pension_bp.route('/api/generate-from-residents', methods=['POST'])
@login_required
def api_pension_generate_from_residents():
    _med._ensure_columns()
    year = request.form.get('payment_year', type=int) or _dt.now().year
    group = (request.form.get('village_group') or '').strip()
    q = Resident.query.filter(~Resident.status.in_(['deceased', 'moved_out']))
    if group:
        q = q.filter(Resident.village_group == group)
    created = skipped = 0
    for r in q.all():
        if r.id_card_hash and MedicalInsurance.query.filter_by(
            payment_year=year, id_card_hash=r.id_card_hash, insurance_type=_mfc.PENSION_INSURANCE_TYPE
        ).first():
            skipped += 1
            continue
        item = MedicalInsurance(
            payment_year=year, name=r.name, data_source=_med.DATA_SOURCES[1],
            insurance_status=_med.DEFAULT_INSURANCE_STATUS[0], is_paid='否',
            is_state_paid='否', is_deductible='否', is_relocated='否',
            verify_status=_med.VERIFY_STATUSES[0], benefit_cert_status='未认证',
            insurance_type=_mfc.PENSION_INSURANCE_TYPE,
        )
        _med._set_enc(item, 'id_card', _med._resident_plain(r, 'id_card'))
        _med._fill_from_resident(item, r)
        db.session.add(item)
        created += 1
    db.session.commit()
    from utils.agg_cache import invalidate as _metrics_invalidate
    _metrics_invalidate()
    msg = f'从居民库补充生成农保记录 {created} 条，已存在跳过 {skipped} 条'
    log_operation(session.get('username', ''), '农保参保管理', 'generate', msg)
    return json_response(True, msg, data={'created': created, 'skipped': skipped})


@pension_bp.route('/api/ledger-stats')
@login_required
def api_pension_ledger_stats():
    year = request.args.get('payment_year', type=int) or _dt.now().year
    q = MedicalInsurance.query.filter_by(payment_year=year, insurance_type=_mfc.PENSION_INSURANCE_TYPE)
    total = q.count()
    paid = q.filter(MedicalInsurance.is_paid == '是').count()
    certified = q.filter(MedicalInsurance.benefit_cert_status == '已认证').count()
    state_paid = q.filter(MedicalInsurance.is_state_paid == '是').count()
    return json_response(True, data={
        'year': year, 'total': total, 'paid': paid,
        'paid_rate': round(paid / total * 100, 1) if total else 0,
        'certified': certified,
        'cert_rate': round(certified / total * 100, 1) if total else 0,
        'state_paid': state_paid,
    })


@pension_bp.route('/api/template')
@login_required
def api_pension_template():
    fields = _mfc.pension_fields()
    headers = [f['label'] for f in fields]
    return export_excel([], headers, '农保参保导入模板.xlsx')


@pension_bp.route('/api/export')
@login_required
def api_pension_export():
    fields = _mfc.pension_fields()
    headers = [f['label'] for f in fields]
    items = _rp_query().order_by(MedicalInsurance.id.desc()).all()
    rows = []
    for it in items:
        s = _med._serialize(it)
        rows.append([s.get(f['key'], '') for f in fields])
    log_operation(session.get('username', ''), '农保参保管理', 'export', f'导出农保参保名单 {len(rows)} 条')
    return export_excel(rows, headers, '农保参保名单.xlsx')


def _rp_inspect_headers(headers, form):
    label_to_key = _mfc.pension_label_to_key_map()
    unknown = [h for h in (headers or []) if h and h not in label_to_key]
    return {'unknown_headers': unknown, 'insurance_type': _mfc.PENSION_INSURANCE_TYPE}


def _rp_batch_pre_run(batch, headers, ctx):
    _med._ensure_columns()
    ctx['label_to_key'] = _mfc.pension_label_to_key_map()
    ctx['import_batch'] = (ctx.get('import_batch') or '').strip() or f'{_dt.now().year}年农保部门下发名单'


def _rp_batch_row_handler(raw_row, ctx):
    label_to_key = ctx['label_to_key']
    row = {}
    for h, val in raw_row.items():
        k = label_to_key.get((h or '').strip())
        if k:
            row[k] = val
    if not any(_med._clean(v) for v in row.values()):
        return 'skipped'
    row['import_batch'] = _med._clean(row.get('import_batch')) or ctx['import_batch']
    row['insurance_type'] = _mfc.PENSION_INSURANCE_TYPE
    if not _med._clean(row.get('name')) and not _med._clean(row.get('id_card')):
        return 'skipped'
    created = _med._upsert_item(row, '农保下发', row['import_batch'])
    return 'inserted' if created else 'updated'


_register_domain_batch_routes(
    pension_bp,
    domain='pension',
    log_module='农保参保管理',
    pre_run=_rp_batch_pre_run,
    row_handler=_rp_batch_row_handler,
    inspect_headers=_rp_inspect_headers,
)
