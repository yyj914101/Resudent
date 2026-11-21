import os, json, io
from datetime import datetime, date
from flask import jsonify, send_file, g
from models import db, DictCategory, DictItem, OperationLog, User

def json_response(success=True, msg='', data=None, total=0):
    """Standard JSON response wrapper.

    R-103-04：total 恒含（0 也是合法统计值）——原 `if total:` 在 total=0 时省略键，
    前端直读 resp.total 得 undefined，空表页脚渲染「共 undefined 条」
    （实证 safety_checks.js 空表首屏）。显式传 total=None 仍省略键。
    """
    resp = {'success': success, 'msg': msg}
    if data is not None:
        resp['data'] = data
    if total is not None:
        resp['total'] = total
    return jsonify(resp)

def get_dict_items(category_name):
    """Get active dictionary items for a category.

    027 D4：Flask g 请求级缓存——同一请求内重复查询（列表页多列同分类）只打一次 DB。
    """
    cache = g.get('_dict_items_cache')
    if cache is None:
        cache = g._dict_items_cache = {}
    if category_name not in cache:
        cat = DictCategory.query.filter_by(name=category_name).first()
        cache[category_name] = (DictItem.query.filter_by(category_id=cat.id, is_active=True)
                                .order_by(DictItem.sort_order).all() if cat else [])
    return cache[category_name]

def get_dict_value(category_name, label):
    """Get the value for a label in a dictionary category."""
    cat = DictCategory.query.filter_by(name=category_name).first()
    if not cat:
        return None
    item = DictItem.query.filter_by(category_id=cat.id, label=label).first()
    return item.value if item else None

def paginate_query(query, page=1, per_page=20):
    """Paginate a SQLAlchemy query, return (items, total, pages)."""
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page
    return items, total, pages

def log_operation(user_name, module, action, summary, detail='', result='success'):
    """Record an operation log.（T06：detail 落库前脱敏，防敏感明文入日志）"""
    from utils.masking import mask_text
    log = OperationLog(
        user_name=user_name,
        module=module,
        action=action,
        summary=summary,
        detail=mask_text(str(detail))[:2000] if detail else '',
        result=result
    )
    db.session.add(log)
    db.session.commit()


def write_export_xlsx(headers, rows, module='death_report', filename='export.xlsx'):
    """生成 xlsx 落盘 temp/exports/<uuid>.xlsx，返回 (abs_path, download_url)。

    T15-3/T15-4 新导出点通用通道（T07 规范，禁 serve_upload）：
    脱敏在组装 rows 时由调用方按 mask_export_rows 处理；此处只落盘 + 发 token。
    需要当前请求上下文（session.user_id / username）。
    """
    import os
    import uuid
    from flask import session
    from config import Config
    from utils.export_token import export_token_store

    env_tmp = os.environ.get('SHUCUN_TEMP_DIR')
    exp_dir = os.path.join(env_tmp, 'exports') if env_tmp else getattr(Config, 'EXPORTS_DIR', None)
    if not exp_dir:
        exp_dir = os.path.join(Config.TEMP_DIR, 'exports')
    os.makedirs(exp_dir, exist_ok=True)

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    for col_idx, header in enumerate(headers, 1):
        ws.column_dimensions[chr(64 + col_idx)].width = max(15, len(str(header)) * 2 + 2)

    abs_path = os.path.join(exp_dir, f'{uuid.uuid4().hex}.xlsx')
    wb.save(abs_path)

    from models import User
    u = User.query.get(session.get('user_id'))
    token_url = export_token_store.issue(u.id, abs_path, module) if u else None

    log_operation(session.get('username', ''), module, 'export',
                  f'导出文件 {filename}（token 通道）')
    return abs_path, token_url

def _append_excel_row(ws, values):
    """R-047-09：整行写入后把字符串强制为文本单元格（inlineStr）。

    `=` `+` `-` `@` 开头的字符串按公式写入会被 Excel 执行；前置单引号虽安全但会
    显示可见的 `'`。改为事后置 data_type='s'：值原样、不执行、无数值类型降级。
    """
    ws.append(values)
    for idx, value in enumerate(values, 1):
        if isinstance(value, str):
            ws.cell(row=ws.max_row, column=idx).data_type = 's'


def export_excel(headers, rows, filename='export.xlsx', can_view_sensitive=None, already_masked=False):
    """Generate an Excel file for download.

    headers: list of column header names
    rows: list of lists (data rows)
    T06：服务端唯一拦截点——无 view_sensitive 权限时对敏感列脱敏；
    明文导出仅限 view_sensitive 角色（下载名标记「含明文」）。
    already_masked：调用方已按 can_view_sensitive 打码，跳过二次脱敏（避免套两层），
    仅保留文件名标记语义（R-096b-01）。
    """
    try:
        from utils.masking import mask_export_rows
        if can_view_sensitive is None:
            from flask import session
            from utils.permissions import user_has_special
            user = User.query.get(session.get('user_id')) if session.get('user_id') else None
            can_view_sensitive = bool(user and (user.role_id == 1 or user_has_special(user, 'view_sensitive')))
        if not already_masked:
            rows = mask_export_rows(headers, rows, can_view_sensitive)
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for row in rows:
            _append_excel_row(ws, list(row))
        # Auto-adjust column widths
        # 2026-11-16：chr(64+n) 只在 A–Z（≤26 列）成立，超 26 列会产出 '[' 等非法列名
        # （医保「全部险种」导出取三类字段并集已超 26 列）→ 统一用 openpyxl 的列号换算
        from openpyxl.utils import get_column_letter
        for col_idx, header in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = max(15, len(str(header)) * 2 + 2)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        download_name = ('含明文_' + filename) if can_view_sensitive else filename
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        as_attachment=True, download_name=download_name)
    except ImportError:
        return json_response(False, '导出模块未安装，请执行: pip install openpyxl')

def import_excel_template_year(field_map, filename='import_template.xlsx', version=None):
    """Generate an Excel import template with dropdown validation for dict fields.
    field_map: list of dicts [{'label':'姓名','key':'name','dict':None}, {'label':'政治面貌','key':'politics','dict':'政治面貌'}]
    version: 可选模板/目录版本（036 F4），写入第二个「模板版本」工作表；为空则保持原单表行为。
    """
    try:
        import openpyxl
        from openpyxl.worksheet.datavalidation import DataValidation

        wb = openpyxl.Workbook()
        ws = wb.active
        headers = [f['label'] for f in field_map]
        ws.append(headers)

        # Add data validation for dictionary fields
        for col_idx, field in enumerate(field_map, 1):
            if field.get('dict'):
                items = get_dict_items(field['dict'])
                if items:
                    dv = DataValidation(
                        type="list",
                        formula1=f'"{",".join(i.label for i in items)}"',
                        allow_blank=True
                    )
                    dv.error = f'请选择合法的{field["label"]}值'
                    dv.errorTitle = '输入错误'
                    ws.add_data_validation(dv)
                    dv.add(f'{chr(64 + col_idx)}2:{chr(64 + col_idx)}1048576')

        # Set column widths
        for col_idx, header in enumerate(headers, 1):
            ws.column_dimensions[chr(64 + col_idx)].width = max(18, len(str(header)) * 2 + 2)

        if version:
            # 036 F4：版本随模板文件走（不影响首个数据表，read_xlsx 只读 active）
            meta = wb.create_sheet('模板版本')
            meta.append(['catalog_version', version])
            meta.append(['说明', '导入时若与当前字段目录版本不一致，系统会提示复核映射'])

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        as_attachment=True, download_name=filename)
    except ImportError:
        return json_response(False, '导出模块未安装')
