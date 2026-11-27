import io
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, send_file, session
from models import db, Shop, DynamicRecord
from utils.decorators import login_required
from utils.helpers import json_response, paginate_query, log_operation, export_excel
from utils.crypto import key_holder, sha256_hash
from utils.sensitive_fields import make_sensitive_helpers

shop_bp = Blueprint('shop', __name__)

# R12 字段级加密管线（身份证号；电话为非敏感字段，明文）
_SHOP_SENS_FIELDS = {'id_card': ('id_card_enc', 'id_card_hash', None)}
_decrypt, _set_enc, _clause = make_sensitive_helpers(Shop, _SHOP_SENS_FIELDS, key_holder)

IMPORT_HEADERS = ['店铺名称', '街道名', '门牌号', '商户姓名', '身份证号', '电话']


def _shop_to_dict(item):
    """单条序列化（list/detail 共用）。"""
    return {
        'id': item.id,
        'name': item.name or '',
        'street': item.street or '',
        'house_number': item.house_number or '',
        'owner_name': item.owner_name or '',
        'id_card': _decrypt(item, 'id_card'),
        'phone': item.phone or '',
    }


def _build_query():
    query = Shop.query
    keyword = request.args.get('keyword', '').strip()
    id_card = request.args.get('id_card', '').strip()
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(db.or_(
            Shop.name.like(like),
            Shop.street.like(like),
            Shop.house_number.like(like),
            Shop.owner_name.like(like),
            Shop.phone.like(like),
        ))
    if id_card:
        query = query.filter(Shop.id_card_hash == sha256_hash(id_card.upper()))
    return query


@shop_bp.route('/')
@login_required
def list_page():
    # 101：年度缴费融合——缴费件就绪态下传页面（列表列/筛选/三按钮/弹窗共用一份上下文）
    ctx = _fee_ui_context()
    fee_ctx = {'year': ctx['year'], 'item_display': _FEE_UI_ITEM_NAME} if ctx and ctx.get('built') else None
    return render_template('shop/list.html', fee_ctx=fee_ctx)


@shop_bp.route('/api/list')
@login_required
def api_list():
    page = max(request.args.get('page', 1, type=int), 1)
    limit = min(max(request.args.get('limit', 20, type=int), 1), 100)
    query = _build_query().order_by(Shop.id.desc())
    # 101：当年缴费状态（软表就绪时逐店嵌入）；fee_status 筛选在全量店集上先滤后分页
    fee_ctx = _fee_ui_context()
    fee_ready = bool(fee_ctx and fee_ctx.get('built'))
    status_map = {}
    if fee_ready:
        status_map = _fee_status_map(fee_ctx['table'], fee_ctx['year'])
        fee_status_filter = request.args.get('fee_status', '').strip()
        if fee_status_filter in ('paid', 'unpaid'):
            want_paid = fee_status_filter == 'paid'
            keep_ids = [sid for sid, name in
                        Shop.query.with_entities(Shop.id, Shop.name).all()
                        if (name in status_map) == want_paid]
            query = query.filter(Shop.id.in_(keep_ids or [0]))
    items, total, pages = paginate_query(query, page=page, per_page=limit)
    data = [_shop_to_dict(item) for item in items]
    if fee_ready:
        for item in data:
            hit = status_map.get(item['name'])
            item['fee'] = ({'status': '已缴', 'amount': hit['amount'], 'year': fee_ctx['year']}
                           if hit else {'status': '未缴', 'amount': None,
                                        'year': fee_ctx['year']})
    return json_response(True, '', data=data, total=total)


@shop_bp.route('/api/detail/<int:item_id>')
@login_required
def api_detail(item_id):
    item = Shop.query.get(item_id)
    if not item:
        return json_response(False, '记录不存在')
    return json_response(True, data=_shop_to_dict(item))


@shop_bp.route('/api/save', methods=['POST'])
@login_required
def api_save():
    try:
        item_id = request.form.get('id', 0, type=int)
        name = request.form.get('name', '').strip()
        if not name:
            return json_response(False, '店铺名称不能为空')

        if item_id:
            item = Shop.query.get(item_id)
            if not item:
                return json_response(False, '记录不存在')
            action = 'edit'
        else:
            item = Shop()
            db.session.add(item)
            action = 'add'

        item.name = name
        item.street = request.form.get('street', '').strip()
        item.house_number = request.form.get('house_number', '').strip()
        item.owner_name = request.form.get('owner_name', '').strip()
        _set_enc(item, 'id_card', request.form.get('id_card', ''))
        item.phone = request.form.get('phone', '').strip()
        db.session.commit()
        log_operation(session.get('username', ''), '店铺信息', action, f'{"编辑" if action == "edit" else "新增"}店铺: {name}')
        return json_response(True, '保存成功')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'保存失败: {str(e)}')


@shop_bp.route('/api/delete/<int:item_id>', methods=['POST'])
@login_required
def api_delete(item_id):
    item = Shop.query.get(item_id)
    if not item:
        return json_response(False, '记录不存在')
    name = item.name
    db.session.delete(item)
    db.session.commit()
    log_operation(session.get('username', ''), '店铺信息', 'delete', f'删除店铺: {name}')
    return json_response(True, '已删除')


@shop_bp.route('/api/export')
@login_required
def api_export():
    items = _build_query().order_by(Shop.id.desc()).all()
    headers = ['序号'] + IMPORT_HEADERS
    rows = []
    for i, item in enumerate(items, 1):
        rows.append([
            i, item.name or '', item.street or '', item.house_number or '',
            item.owner_name or '', _decrypt(item, 'id_card'), item.phone or '',
        ])
    return export_excel(headers, rows, filename='店铺信息.xlsx')


@shop_bp.route('/api/template')
@login_required
def api_template():
    """下载店铺信息 Excel 导入模板。"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '店铺信息导入模板'
    ws.append(IMPORT_HEADERS)
    for ci in range(1, len(IMPORT_HEADERS) + 1):
        cell = ws.cell(row=1, column=ci)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(start_color='1a73e8', end_color='1a73e8', fill_type='solid')
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    log_operation(session.get('username', ''), '店铺信息', 'template', '下载店铺信息导入模板')
    return send_file(output,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='店铺信息导入模板.xlsx')


@shop_bp.route('/api/import', methods=['POST'])
@login_required
def api_import():
    """从上传的 Excel 批量导入店铺信息（append 跳过重复 / overwrite 覆盖）。"""
    file = request.files.get('file')
    if not file:
        return json_response(False, '请选择文件')
    mode = request.form.get('mode', 'append')
    # R-047-04：模式/扩展名/zip 头护栏（对齐 034/045 导入口径）
    if mode not in ('append', 'overwrite'):
        return json_response(False, '导入模式无效（append|overwrite）')
    from utils.spreadsheet_reader import (
        SPREADSHEET_ERR_MSG,
        is_allowed_spreadsheet,
        read_spreadsheet_table,
        validate_spreadsheet_magic,
    )
    if not is_allowed_spreadsheet(file.filename or ''):
        return json_response(False, SPREADSHEET_ERR_MSG)
    data = file.read()
    ok_magic, magic_err = validate_spreadsheet_magic(file.filename or '', data)
    if not ok_magic:
        return json_response(False, magic_err)

    try:
        headers_list, rows = read_spreadsheet_table(file.filename or 'upload.xlsx', data)
    except Exception as exc:
        return json_response(False, f'文件解析失败：{exc}')

    headers_list = headers_list
    from utils.header_aliases import build_header_index
    col = build_header_index(headers_list, domain='shop')

    def gv(row, name):
        val = row[col[name]] if name in col else None
        return str(val).strip() if val is not None else ''

    imported = 0
    updated = 0
    skipped = 0
    errors = []

    for row_idx, row in enumerate(rows):
        if not row or all(c is None for c in row):
            continue
        name = gv(row, '店铺名称')
        if not name:
            errors.append(f'第{row_idx + 2}行: 店铺名称不能为空')
            continue

        id_card = gv(row, '身份证号')
        existing = None
        if id_card:
            existing = Shop.query.filter_by(id_card_hash=sha256_hash(id_card.upper())).first()
        if not existing:
            existing = Shop.query.filter_by(name=name).first()

        if existing:
            if mode == 'append':
                skipped += 1
                continue
            is_update = True
        else:
            is_update = False

        try:
            # R-047-05：逐行保存点——单行失败只回滚本行，不拖累本批已处理行
            with db.session.begin_nested():
                r = existing or Shop()
                if existing is None:
                    db.session.add(r)
                r.name = name
                r.street = gv(row, '街道名')
                r.house_number = gv(row, '门牌号')
                r.owner_name = gv(row, '商户姓名')
                _set_enc(r, 'id_card', id_card)
                r.phone = gv(row, '电话')
            if is_update and mode == 'overwrite':
                updated += 1
            else:
                imported += 1
        except Exception as e:
            errors.append(f'第{row_idx + 2}行 ({name}): {str(e)}')

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return json_response(False, '导入失败')

    msg = f'新增{imported}条'
    if updated:
        msg += f', 更新{updated}条'
    if skipped:
        msg += f', 跳过{skipped}条'
    if errors:
        msg += '<br>失败' + str(len(errors)) + '条:<br>' + '<br>'.join(errors[:10])

    log_operation(session.get('username', ''), '店铺信息', 'import',
                  f'导入店铺 {imported}新增 {updated}更新 {skipped}跳过')
    return json_response(success=len(errors) == 0, msg=msg, data={
        'imported': imported, 'updated': updated, 'skipped': skipped, 'errors': errors[:30],
    })


# ================================================================== 099 软表挂载门面（薄路由，只追加）
# 业务判断全部收口在 utils/roster_facade.py（ADR-027：固定菜单 → module_key → code），
# 本段只做「参数提取 → 转调门面 → json_response 包装」。权限不手写：
# BLUEPRINT_MODULES['shop'] 模块位 + 视图命名关键字（list/add/edit/upload/create/export）
# 由 enforce_role_permissions 自动推断动作位（宪法 §11 规则 5）。
import config
import json
import os
import secrets

from flask import abort

from utils import roster_facade as facade
from utils import softtable_engine as eng

_ROSTER_MODULE_KEY = 'shop'        # 门面挂载语义：module_key / group_name 由蓝图固定传入
_ROSTER_GROUP_NAME = '人口与台账'
_ROSTER_ALLOWED_EXT = ('.xlsx', '.xls', '.xlsb', '.csv', '.xlsm')
_ROSTER_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _roster_who():
    return session.get('username', '')


def _roster_temp_dir():
    """上传临时目录（与 dynatable 同款 <TEMP_DIR>/softtable；token 建表后即删）。"""
    path = os.path.join(config.TEMP_DIR, 'softtable')
    os.makedirs(path, exist_ok=True)
    return path


def _roster_token_file(token):
    """token → (文件路径, 扩展名)；仅接受 hex 字符，防路径穿越。"""
    if not token or not isinstance(token, str) or not all(c in '0123456789abcdef' for c in token):
        return None
    for ext in _ROSTER_ALLOWED_EXT:
        path = os.path.join(_roster_temp_dir(), token + ext)
        if os.path.exists(path):
            return path, ext
    return None


def _roster_meta_path(token):
    return os.path.join(_roster_temp_dir(), token + '.meta.json')


def _roster_meta_name(token):
    """从 meta 取原始文件名（决定解析器分支：xlsx / csv）。"""
    meta_file = _roster_meta_path(token)
    if os.path.exists(meta_file):
        with open(meta_file, encoding='utf-8') as fh:
            return json.load(fh).get('filename', 'upload.xlsx')
    return 'upload.xlsx'


def _roster_filters():
    """f_ 前缀查询参数 → 引擎筛选条件（含 _min/_max 区间），口径同 dynatable api_records。"""
    filters, ranges = {}, {}
    for key, value in request.args.items():
        if key.startswith('f_') and key.endswith('_min'):
            ranges.setdefault(key[2:-4], {})['min'] = value
        elif key.startswith('f_') and key.endswith('_max'):
            ranges.setdefault(key[2:-4], {})['max'] = value
        elif key.startswith('f_'):
            filters[key[2:]] = value
    filters.update({k: v for k, v in ranges.items() if v})
    q_kw = (request.args.get('q') or '').strip()
    if q_kw:
        filters['__q__'] = q_kw
    return filters


@shop_bp.route('/roster/<code>/')
@login_required
def roster_page(code):
    """门面页：已建（有字段行）→ 台账页；未建（m031 预置空行）→ 200 引导页；
    跨模块 / 未知 code（facade.get_table 归 None）→ 404。"""
    table = facade.get_table(_ROSTER_MODULE_KEY, code)
    if table is None:
        abort(404)
    fields = facade.fields_with_options(table)
    if not fields:
        # meta 传纯 dict（模板 __ST_ROSTER__ 会 tojson 序列化，ORM 对象不可序列化）
        return render_template('components/roster/roster_bootstrap.html', code=code,
                               meta={'name': table.name, 'module_key': table.module_key,
                                     'description': table.description or ''})
    extra_ctx = {}
    if facade.is_dynamic_tab_table(_ROSTER_MODULE_KEY, code):
        extra_ctx = {'nav_module': 'resident', 'nav_active': 'dt:' + code, 'show_back': False}
    return render_template('components/roster/roster_page.html',
                           table=table, code=code, fields=fields, **extra_ctx)


@shop_bp.route('/roster/<code>/upload')
@login_required
def roster_upload_page(code):
    """上传与字段确认页（GET → shop.view）：未建表引导页与台账页共用同一上传流程的独立入口。"""
    table = facade.get_table(_ROSTER_MODULE_KEY, code)
    if table is None:
        abort(404)
    return render_template('components/roster/roster_upload.html', code=code,
                           meta={'name': table.name, 'module_key': table.module_key})


@shop_bp.route('/roster/<code>/api/list')
@login_required
def roster_list(code):
    """分页/筛选/排序；truncated 标记列表已按 MAX_LIST_ROWS 截断。"""
    table = facade.get_table(_ROSTER_MODULE_KEY, code)
    if table is None:
        return json_response(False, '台账不存在')
    result = facade.list_rows(table,
                              page=request.args.get('page', 1, type=int),
                              limit=request.args.get('limit', 20, type=int),
                              sort=request.args.get('sort') or None,
                              order=request.args.get('dir') or 'asc',
                              filters=_roster_filters())
    # json_response 无扩展位，truncated 随统一响应体一并返回
    return jsonify({'success': True, 'msg': '', 'data': result['data'],
                    'total': result['total'], 'truncated': result['truncated']})


def _roster_record_save(code, rid):
    """record 新增/更新共用体：rid=None/0 新增（roster.js 拼 /api/record/0 表示新增），
    否则更新；SoftTableError 转错误响应。"""
    rid = rid or None
    table = facade.get_table(_ROSTER_MODULE_KEY, code)
    if table is None:
        return json_response(False, '台账不存在')
    payload = request.get_json(silent=True) or {}
    values = payload.get('values')
    if not isinstance(values, dict):
        values = {k: v for k, v in payload.items() if k not in ('id', 'rid')}
    # 085 年度缴费重复拦截（一页纸 V-02：同场所+项目+年度唯一，保存路）
    if code == ANNUAL_FEE_CODE:
        dup = _fee_dup_error(table, values=values, exclude_rid=rid)
        if dup:
            return json_response(False, dup)
    try:
        row = facade.save_row(table, rid, values, created_by=_roster_who())
    except eng.SoftTableError as exc:
        return json_response(False, str(exc))
    log_operation(_roster_who(), '店铺信息', 'edit' if rid else 'add',
                  '台账 %s（%s）%s记录 #%s' % (table.name, code,
                                              '编辑' if rid else '新增', row.get('id')))
    return json_response(True, '已保存', data=row)


@shop_bp.route('/roster/<code>/api/record', methods=['POST'])
@login_required
def roster_record_add(code):
    """新增一行（视图名含 add → shop.add 动作位）。"""
    return _roster_record_save(code, None)


@shop_bp.route('/roster/<code>/api/record/<int:rid>', methods=['POST'])
@login_required
def roster_record_edit(code, rid):
    """更新一行（视图名含 edit → shop.edit 动作位）。"""
    return _roster_record_save(code, rid)


@shop_bp.route('/roster/<code>/api/upload', methods=['POST'])
@login_required
def roster_upload(code):
    """上传 Excel/CSV → 引擎解析 + 字段推断，token + meta 落临时目录（建表后即删）。"""
    raw = request.files.get('file')
    if not raw or not raw.filename:
        return json_response(False, '请选择要上传的 .xlsx / .csv 文件')
    filename = raw.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ROSTER_ALLOWED_EXT:
        return json_response(False, '仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式文件')
    raw.stream.seek(0, os.SEEK_END)
    size = raw.stream.tell()
    raw.stream.seek(0)
    if size > _ROSTER_MAX_UPLOAD_BYTES:
        return json_response(False, '文件超过 20MB 上限，请拆分后再上传')

    token = secrets.token_hex(16)
    dest = os.path.join(_roster_temp_dir(), token + ext)
    raw.save(dest)
    try:
        with open(dest, 'rb') as fh:
            headers, rows = eng.parse_table_file(filename, fh)
        suggestions = eng.suggest_fields(headers, rows)
    except eng.SoftTableError as exc:
        if os.path.exists(dest):
            os.remove(dest)
        return json_response(False, str(exc))

    meta = {'filename': filename, 'headers': headers, 'suggestions': suggestions,
            'row_count': len(rows), 'uploaded_by': _roster_who(), 'code': code}
    with open(_roster_meta_path(token), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False)
    sample = [[eng._as_text(c) for c in row[:len(headers)]] for row in rows[:5]]
    return json_response(True, '', data={
        'token': token, 'filename': filename, 'headers': headers,
        'suggestions': suggestions, 'row_count': len(rows), 'sample': sample,
        'dict_categories': eng.dict_categories_for_enum(),
    })


@shop_bp.route('/roster/<code>/api/create', methods=['POST'])
@login_required
def roster_create(code):
    """确认字段后建表：code 由 URL 决定（不取 payload），module/group 为 shop 门面固定值；
    重复建已建台账 → 409（防误操作，不覆盖）。"""
    payload = request.get_json(silent=True) or {}
    token = (payload.get('token') or '').strip()
    found = _roster_token_file(token)
    if not found:
        return json_response(False, '上传凭证已失效，请重新上传文件')
    path, _ext = found
    try:
        fields = eng.normalize_fields(payload.get('fields'))
    except eng.SoftTableError as exc:
        return json_response(False, str(exc))
    name = (payload.get('name') or '').strip()
    if not name:
        return json_response(False, '请填写台账名称')

    table = facade.get_table(_ROSTER_MODULE_KEY, code)
    if table is not None and facade.is_built(table):
        return (json_response(False, '该台账已存在，请勿重复建表'), 409)

    try:
        with open(path, 'rb') as fh:
            _headers, rows = eng.parse_table_file(_roster_meta_name(token), fh)
        # 先验后建（dynatable api_create 同款门禁）：错误行未勾选跳过 → 不建表不留半截
        _records, errors = eng.build_records(fields, rows)
    except eng.SoftTableError as exc:
        return json_response(False, str(exc))
    if errors and not payload.get('skip_error_rows'):
        return json_response(False, '有 %d 行数据不合规，未建表（可勾选「跳过错误行」后重试）' % len(errors),
                             data={'errors': errors[:50], 'error_total': len(errors)})

    # 085 年度缴费重复拦截（一页纸 V-02：同场所+项目+年度唯一，导入路；
    # 拦截在 build_records 之后——枚举 label→value、年度已归一，与库内真值同口径。
    # 重复属数据冲突而非格式错误，不可「跳过错误行」放行）
    if code == ANNUAL_FEE_CODE:
        dup = _fee_dup_error(table=None, records=_records, fields=fields)
        if dup:
            db.session.rollback()
            return json_response(False, dup)

    try:
        table = facade.create_table(_ROSTER_MODULE_KEY, code, name, _ROSTER_GROUP_NAME,
                                    fields, created_by=_roster_who())
        imported, _errs = facade.import_records(table, fields, rows, skip_error_rows=True,
                                                created_by=_roster_who())
    except eng.SoftTableError as exc:
        db.session.rollback()
        return json_response(False, str(exc))
    except Exception as exc:
        db.session.rollback()
        return json_response(False, '建表失败：%s' % exc)

    log_operation(_roster_who(), '店铺信息', 'add', '软表建表 %s（%s）' % (name, code),
                  '字段 %d 个，导入 %d 行' % (len(fields), imported))
    meta_file = _roster_meta_path(token)
    if os.path.exists(meta_file):
        os.remove(meta_file)
    if os.path.exists(path):
        os.remove(path)
    return json_response(True, '建表完成，已导入 %d 行' % imported,
                         data={'code': code, 'imported': imported, 'skipped': len(errors)})


@shop_bp.route('/roster/<code>/api/export')
@login_required
def roster_export(code):
    """按当前筛选导出 xlsx（096 口径）；identity 列由门面按 can_view_sensitive 打码/解密。"""
    table = facade.get_table(_ROSTER_MODULE_KEY, code)
    if table is None:
        return json_response(False, '台账不存在')
    from models import User
    from utils.permissions import user_has_special
    user = User.query.get(session.get('user_id')) if session.get('user_id') else None
    can_view_sensitive = bool(user and (user.role_id == 1
                                        or user_has_special(user, 'view_sensitive')))
    headers, rows = facade.export_rows(table, can_view_sensitive,
                                       filters=_roster_filters(),
                                       sort=request.args.get('sort') or None,
                                       order=request.args.get('dir') or 'asc')
    log_operation(_roster_who(), '店铺信息', 'export',
                  '导出台账 %s（%s）%d 行' % (table.name, code, len(rows)))
    # identity 列已在 decode_records 打码/解密 → already_masked=True 防二次脱敏（R-096b-01）
    return export_excel(headers, rows, '%s.xlsx' % table.name,
                        can_view_sensitive=can_view_sensitive, already_masked=True)

from utils.roster_routes import register_roster_append_routes as _register_roster_append_routes  # noqa: E402
_register_roster_append_routes(shop_bp, module_key=_ROSTER_MODULE_KEY, log_module='店铺信息')


# ================================================================== 085 场所/设施（软表两册 + 年度缴费透视）
# 基础设施分布 = 固定 code 薄包装（复用 roster_page 渲染管线，零新页面骨架）；
# 年度缴费 = 透视页（行=场所、列=年度，eng.pivot_rows 展示层变换）+ 明细台账入口。
# 唯一拦截（一页纸 V-02/Q-04）：同场所+项目+年度唯一，保存（_roster_record_save）
# 与导入（roster_create）两路都拦；重复属数据冲突，不可「跳过错误行」放行。

FACILITY_CODE = 'shop_facility'
ANNUAL_FEE_CODE = 'shop_annual_fee'
# 唯一键三列按 display_name 定位（软表字段 code 由建表生成不固定；一页纸 §2 定稿列名）
_FEE_KEY_NAMES = ('场所名称', '缴费项目', '缴费年度')
_FEE_VALUE_NAME = '实缴金额'
# 透视空表时的年度骨架（一页纸 Q-01：历史补录范围 2024~2026）
_FEE_DEFAULT_YEARS = (2024, 2025, 2026)


def _fee_key_codes(specs):
    """display_name → 唯一键三列 code；任一列缺失（表结构与一页纸不符）→ None（放行不误伤）。"""
    by_name = {s['display_name']: s['code'] for s in specs}
    codes = tuple(by_name.get(name) for name in _FEE_KEY_NAMES)
    return codes if all(codes) else None


def _norm_year(value):
    """年度归一：2025 / '2025' / '2025.0' → '2025'；不可解析回退原文 strip。"""
    try:
        return str(int(float(str(value).strip())))
    except (TypeError, ValueError):
        return str(value or '').strip()


def _fee_triple(codes, data):
    """data dict（code→值）→ 归一化三元组；缺列返回 None。"""
    if not codes:
        return None
    name = str(data.get(codes[0], '') or '').strip()
    item = str(data.get(codes[1], '') or '').strip()
    year = _norm_year(data.get(codes[2]))
    if not name or not item or not year:
        return None
    return (name, item, year)


def _fee_dup_error(table=None, values=None, records=None, fields=None, exclude_rid=None):
    """shop_annual_fee 唯一拦截（保存/导入两路共用）。

    - 保存路：values = {字段code: 原始值}，exclude_rid 排除被编辑行自身；
    - 导入路：records = eng.build_records 产物（coercion 后真值），fields 为确认字段
      （display_name→code 定位列），与库内 + 批内双向查重；
    - 表未建 / 三列不齐 → None（放行，交由必填/建表校验兜底）。
    """
    if table is not None:
        specs = facade.field_specs(table)
        codes = _fee_key_codes(specs)
    else:
        by_name = {f['display_name']: f['code'] for f in (fields or [])}
        codes = tuple(by_name.get(name) for name in _FEE_KEY_NAMES)
        if not all(codes):
            return None
    incoming = []
    if values is not None:
        triple = _fee_triple(codes, values)
        incoming = [triple] if triple else []
    else:
        for rec in records or []:
            try:
                data = json.loads(rec['data'] or '{}')
            except (ValueError, TypeError):
                continue
            triple = _fee_triple(codes, data)
            if triple:
                incoming.append(triple)

    existing = set()
    if table is not None:
        for rec in DynamicRecord.query.filter_by(table_id=table.id).all():
            if exclude_rid is not None and rec.id == exclude_rid:
                continue
            try:
                data = json.loads(rec.data or '{}')
            except ValueError:
                continue
            triple = _fee_triple(codes, data)
            if triple:
                existing.add(triple)

    seen = set()
    for triple in incoming:
        if triple in existing or triple in seen:
            return ('同场所+项目+年度重复：「%s」在该年度的缴费记录已存在（一页纸 V-02 唯一规则）'
                    % triple[0])
        seen.add(triple)
    return None


def _fee_all_rows(table):
    """全量明细（透视/统计取数）：翻页聚合 list_rows（limit 200 为门面上限），
    truncated 即按 MAX_LIST_ROWS 截断——村务规模远达不到，到顶即如实标记。"""
    rows, total, page = [], None, 1
    while True:
        result = facade.list_rows(table, page=page, limit=200)
        total = result['total']
        rows.extend(result['data'])
        if result['truncated'] or len(rows) >= total or not result['data']:
            break
        page += 1
    return rows, total, len(rows) >= (total or 0)


def _fee_pivot(table):
    """透视计算：行=场所（shops 序 + 未匹配场所接队尾）、列=年度升序、格=实缴合计。"""
    specs = facade.field_specs(table)
    by_name = {s['display_name']: s['code'] for s in specs}
    name_code, year_code, value_code = (by_name.get(n) for n in
                                        ('场所名称', '缴费年度', _FEE_VALUE_NAME))
    if not all((name_code, year_code, value_code)):
        return None  # 表结构与一页纸不符 → 页面显示引导文案，不硬渲染
    rows, total, truncated = _fee_all_rows(table)
    pivot = eng.pivot_rows(rows, row_key=name_code, col_key=year_code,
                           value_key=value_code)
    # 行 = 场所（shops 全量，一页纸 §3：无缴费记录的场所也占行，空格画 —）；
    # 明细里与 shops 不同名的场所接队尾（数据可见性优先，不静默丢弃）。
    shop_names = [s.name for s in Shop.query.order_by(Shop.name.asc()).all()
                  if s.name]
    ordered = list(shop_names)
    ordered += [k for k in pivot['row_keys'] if k not in set(shop_names)]
    cols = pivot['col_keys'] or list(_FEE_DEFAULT_YEARS)
    return {'pivot': pivot, 'ordered_rows': ordered, 'cols': cols,
            'detail_total': total, 'truncated': truncated,
            'shop_count': len(shop_names)}


def _roster_uninitialized_page(code, name):
    """R-085-01：fresh-app（未执行 m031）时台账元数据缺失 → 200 未初始化页。

    侧栏「年度缴费」与 shop 胶囊入口不得出现 404 死链（test_073 入口可达门禁）；
    独立共享组件（不改 roster_bootstrap，避免既有引导页快照出现空白符漂移）。
    """
    return render_template('components/roster/roster_uninitialized.html',
                           code=code,
                           meta={'name': name, 'module_key': _ROSTER_MODULE_KEY,
                                 'description': '台账元数据未初始化'})


@shop_bp.route('/facility/')
@login_required
def facility_page():
    """085 基础设施分布名册（固定 code）：复用 roster_page 渲染管线（含未建引导页）。"""
    if facade.get_table(_ROSTER_MODULE_KEY, FACILITY_CODE) is None:
        return _roster_uninitialized_page(FACILITY_CODE, '基础设施分布')
    return roster_page(FACILITY_CODE)


@shop_bp.route('/annual-fee/')
@login_required
def annual_fee_page():
    """085 重点场所年度缴费透视页：行=场所、列=年度（展示层 pivot，无横向动态列）。"""
    table = facade.get_table(_ROSTER_MODULE_KEY, ANNUAL_FEE_CODE)
    if table is None:
        return _roster_uninitialized_page(ANNUAL_FEE_CODE, '重点场所年度缴费')
    if not facade.is_built(table):
        # 未建表 → 复用引导页（上传→确认字段→建表，数据随建表导入）
        return roster_page(ANNUAL_FEE_CODE)
    context = _fee_pivot(table)
    if context is None:
        return roster_page(ANNUAL_FEE_CODE)  # 列名与一页纸不符 → 回台账页兜底
    return render_template('shop/annual_fee_pivot.html',
                           table_name=table.name, code=ANNUAL_FEE_CODE, **context)


@shop_bp.route('/annual-fee/export')
@login_required
def annual_fee_export():
    """透视矩阵导出（一页纸 §4：年度缴费页导出 = 明细 + 场所×年度透视）。"""
    table = facade.get_table(_ROSTER_MODULE_KEY, ANNUAL_FEE_CODE)
    if table is None:
        return json_response(False, '台账不存在')
    if not facade.is_built(table):
        return json_response(False, '台账尚未建立，请先完成上传建表')
    context = _fee_pivot(table)
    if context is None:
        return json_response(False, '台账列名与口径不符，请检查字段（场所名称/缴费年度/实缴金额）')
    pivot = context['pivot']
    headers = ['场所名称'] + ['%s年' % c for c in context['cols']]
    matrix = []
    for rk in context['ordered_rows']:
        matrix.append([rk] + ['%.2f' % pivot['cells'].get(rk, {}).get(c, 0)
                              for c in context['cols']])
    matrix.append(['小计'] + ['%.2f' % pivot['col_totals'].get(c, 0)
                             for c in context['cols']])
    log_operation(_roster_who(), '店铺信息', 'export',
                  '导出年度缴费透视 %d 场所 × %d 年度' % (len(context['ordered_rows']),
                                                       len(context['cols'])))
    return export_excel(headers, matrix, '%s透视.xlsx' % table.name)


# ================================================================== 101 年度缴费融合
# 店铺信息页成为年度清洁费完整业务入口：列表当年状态列 + 详情历年记录/登记 +
# 页头批量登记/上传/透视按钮（透视/上传降级为按钮直达的报表，独立入口由 102 撤除）。
# 数据层零迁移（ADR-028）：字段由首次上传确认生成，全部按 display_name 等值定位；
# 界面固定「年度清洁费」，数据层「缴费项目」字段保留（枚举值经字典解析，界面隐藏）。

_FEE_ITEM_DICT = '缴费项目'
_FEE_ITEM_FALLBACK = 'clean_fee'   # m039 一页纸 v1.1 §1 定稿值（字典多值时优先命中；缺字典回退）
_FEE_UI_ITEM_NAME = '年度清洁费'    # 101 界面固定文案（仅展示，不写入数据）
_FEE_DETAIL_OPTIONAL = ('缴纳日期', '票据号', '备注')  # 详情可选列（确认字段含则显，零 DDL 不新增）


def _fee_fixed_item_value():
    """登记写入的「缴费项目」枚举值：字典单值即用之；多值优先 clean_fee；缺字典回退定稿值。

    coerce_value 双口径（label/value 皆收、存 value），此处直接给 value，
    保证界面隐藏项目选择后写入值稳定（101 验收：界面固定年度清洁费）。
    """
    from utils.helpers import get_dict_items
    items = get_dict_items(_FEE_ITEM_DICT)
    if not items:
        return _FEE_ITEM_FALLBACK
    if len(items) == 1:
        return items[0].value
    for i in items:
        if i.value == _FEE_ITEM_FALLBACK:
            return i.value
    return items[0].value


def _fee_ui_context():
    """101 深接缝：店铺页年度缴费上下文（列表列/详情/登记三处共用的唯一状态源）。

    - None                        → 元数据在但列名与一页纸不符（页面降级，不渲染缴费件）
    - {'built': False, ...}       → 未初始化（m031 未跑）/ 未建表（首次上传前）
    - {'built': True, ...}        → 就绪：table + codes + year + fixed_item_value
    """
    year = datetime.now().year
    table = facade.get_table(_ROSTER_MODULE_KEY, ANNUAL_FEE_CODE)
    if table is None:
        return {'built': False, 'uninitialized': True, 'year': year}
    if not facade.is_built(table):
        return {'built': False, 'uninitialized': False, 'year': year}
    specs = facade.field_specs(table)
    by_name = {s['display_name']: s['code'] for s in specs}
    codes = _fee_key_codes(specs)
    amount_code = by_name.get(_FEE_VALUE_NAME)
    if not codes or not amount_code:
        return None
    optional = {n: by_name[n] for n in _FEE_DETAIL_OPTIONAL if by_name.get(n)}
    return {'built': True, 'uninitialized': False, 'year': year, 'table': table,
            'venue_code': codes[0], 'item_code': codes[1], 'year_code': codes[2],
            'amount_code': amount_code, 'optional': optional,
            'item_value': _fee_fixed_item_value()}


def _fee_rows_by_shop(table):
    """全量缴费明细按场所归组：{场所名: [{year, amount, created_at, values}]}（101 列表列/详情共用）。

    单遍取数：_fee_all_rows 的解码行已含 created_at（decode_records 注入），无需二次查询。
    """
    rows, _total, _truncated = _fee_all_rows(table)
    specs = facade.field_specs(table)
    by_name = {s['display_name']: s['code'] for s in specs}
    venue_code = by_name.get('场所名称')
    item_code = by_name.get('缴费项目')
    year_code = by_name.get('缴费年度')
    amount_code = by_name.get(_FEE_VALUE_NAME)
    grouped = {}
    for row in rows:
        name = str(row.get(venue_code, '') or '').strip()
        if not name:
            continue
        grouped.setdefault(name, []).append({
            'year': _norm_year(row.get(year_code)),
            'item': row.get(item_code, ''),
            'amount': row.get(amount_code),
            'created_at': row.get('created_at', ''),
            'values': row,
        })
    return grouped


def _fee_status_map(table, year):
    """当年状态映射 {场所名: {'status': '已缴'|'未缴', 'amount': 合计}}。

    口径：该场所当年存在任一缴费记录即「已缴」（金额=当年记录合计；无金额记录计 0）。
    「减免」口径台账未配置（确认字段无减免列）→ 两态呈现（101 交付物 1 预案）。
    """
    grouped = _fee_rows_by_shop(table)
    status = {}
    year_s = str(year)
    for name, rows in grouped.items():
        in_year = [r for r in rows if r['year'] == year_s]
        if not in_year:
            continue
        amount = sum(float(r['amount'] or 0) for r in in_year)
        status[name] = {'status': '已缴', 'amount': amount}
    return status


@shop_bp.route('/annual-fee/detail')
@login_required
def annual_fee_detail():
    """101：单店铺历年缴费记录（详情弹窗取数；GET → shop.view 动作位自动映射）。"""
    ctx = _fee_ui_context()
    if not ctx or not ctx.get('built'):
        return json_response(False, '年度缴费台账尚未建立，请先在「年度缴费」入口完成上传建表')
    name = request.args.get('name', '').strip()
    if not name:
        return json_response(False, '缺少场所名称')
    grouped = _fee_rows_by_shop(ctx['table'])
    rows = sorted(grouped.get(name, []), key=lambda r: r['year'], reverse=True)
    records = []
    for r in rows:
        item = {'year': r['year'], 'amount': r['amount'], 'created_at': r['created_at']}
        for disp, code in ctx['optional'].items():
            item[disp] = r['values'].get(code, '')
        records.append(item)
    return json_response(True, data={
        'name': name, 'year': ctx['year'], 'records': records,
        'codes': {'venue': ctx['venue_code'], 'item': ctx['item_code'],
                  'year': ctx['year_code'], 'amount': ctx['amount_code'],
                  'optional': ctx['optional']},
        'item_value': ctx['item_value'], 'item_display': _FEE_UI_ITEM_NAME,
    })


@shop_bp.route('/annual-fee/batch-add', methods=['POST'])
@login_required
def annual_fee_batch_add():
    """101：按年度批量登记（页头「按年度批量登记」；POST 含 add → shop.add 动作位）。

    body: {'year': 2026, 'rows': [{'name': 场所, 'amount': 金额}, ...]}
    逐行走 facade.save_row（coerce/必填/枚举校验同口径）+ _fee_dup_error 查重；
    重复行跳过不阻断，返回 ok/skipped/failed 明细。
    """
    ctx = _fee_ui_context()
    if not ctx or not ctx.get('built'):
        return json_response(False, '年度缴费台账尚未建立，请先在「年度缴费」入口完成上传建表')
    payload = request.get_json(silent=True) or {}
    year = _norm_year(payload.get('year') or ctx['year'])
    rows = payload.get('rows')
    if not isinstance(rows, list) or not rows:
        return json_response(False, '缺少批量登记行')
    item_value = ctx['item_value']
    ok, skipped, failed = 0, [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get('name', '') or '').strip()
        if not name:
            continue
        values = {ctx['venue_code']: name, ctx['item_code']: item_value,
                  ctx['year_code']: year, ctx['amount_code']: row.get('amount', '')}
        dup = _fee_dup_error(table=ctx['table'], values=values)
        if dup:
            skipped.append({'name': name, 'reason': '该年度缴费记录已存在'})
            continue
        try:
            facade.save_row(ctx['table'], None, values, created_by=_roster_who())
            ok += 1
        except eng.SoftTableError as exc:
            failed.append({'name': name, 'error': str(exc)})
    log_operation(_roster_who(), '店铺信息', 'add',
                  '按年度批量登记 %s 年度清洁费：成功 %d 跳过 %d 失败 %d'
                  % (year, ok, len(skipped), len(failed)))
    return json_response(True, '批量登记完成：成功 %d，跳过 %d，失败 %d'
                         % (ok, len(skipped), len(failed)),
                         data={'ok': ok, 'skipped': skipped, 'failed': failed})
