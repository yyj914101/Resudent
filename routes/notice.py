# -*- coding: utf-8 -*-
"""routes.notice — 补贴公示（045）：模板中心 + 月度公示记录（新蓝图）。

端点（ADR-016：`notice` 已入 BLUEPRINT_MODULES，动作位 view/add/edit/delete/export 自动推断）：
- GET  /notice/                            公示工作台（左侧 3 组分类 + 右侧模板/记录）
- GET  /notice/api/templates               模板列表（可按 notice_type / 关键词过滤）
- GET  /notice/api/template/<id>           模板详情
- POST /notice/api/template/create         新建模板（可只传 notice_type → 套内置默认表头）
- POST /notice/api/template/update/<id>    更新模板（名称/表名/表格结构）
- POST /notice/api/template/status/<id>    启用/停用
- POST /notice/api/template/delete/<id>    删除（有记录则拒绝，避免悬空引用）
- GET  /notice/api/records                 记录列表（按 notice_type / period / 状态过滤）
- POST /notice/api/record/generate         按月生成（模板快照 → draft）
- POST /notice/api/record/import           上传 Excel/CSV → 表头匹配 → 返回行数据（不落库）
- POST /notice/api/record/update/<id>      更新记录（表名/表格数据）
- GET  /notice/api/record/<id>             记录详情
- POST /notice/api/record/status/<id>      draft ↔ published
- POST /notice/api/record/delete/<id>      删除记录
- GET  /notice/api/record/export/<id>      导出 Excel

表格语义（归一化 / 默认结构 / 上传列名匹配）全部在 models.notice，
本文件只做 HTTP 映射与文件解析（AGENTS §7 owner 边界）。
"""
import json
import re

from flask import (Blueprint, render_template, request, send_file, session)

from models import NoticeRecord, NoticeTemplate, db
from models.notice import (NOTICE_GROUPS, NOTICE_TYPE_NAMES,
                           match_upload_rows, normalize_table)
from utils.decorators import login_required
from utils.helpers import json_response, log_operation

notice_bp = Blueprint('notice', __name__)

_PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def _append_text_row(ws, values):
    """R-045-04：整行写入后强制文本类型（inlineStr）。

    `=` `+` `-` `@` 开头的字符串若按公式/数值写入会触发 Excel 公式注入；
    前置单引号虽安全但会显示可见的 `'`。改为事后置 data_type='s'：
    值保持原样、XML 写成 `<c t="inlineStr">`，Excel 按文本展示且不执行。
    """
    ws.append(values)
    for idx, value in enumerate(values, 1):
        if isinstance(value, str):
            ws.cell(row=ws.max_row, column=idx).data_type = 's'


def _operator_name():
    return session.get('username') or ''


def _template_payload(item):
    return {
        'id': item.id,
        'name': item.name,
        'notice_type': item.notice_type,
        'type_name': NOTICE_TYPE_NAMES.get(item.notice_type, item.notice_type),
        'title': item.title or item.name,
        'schema': item.schema_dict(),
        'is_custom': bool(item.is_custom),
        'is_active': bool(item.is_active),
        'version': item.version or 1,
        'updated_at': item.updated_at.strftime('%Y-%m-%d %H:%M') if item.updated_at else '',
    }


def _record_payload(item):
    return {
        'id': item.id,
        'template_id': item.template_id,
        'notice_type': item.notice_type,
        'type_name': NOTICE_TYPE_NAMES.get(item.notice_type, item.notice_type),
        'period': item.period,
        'title': item.title,
        'table_data': item.data_dict(),
        'status': item.status,
        'created_by': item.created_by or '',
        'updated_at': item.updated_at.strftime('%Y-%m-%d %H:%M') if item.updated_at else '',
    }


# ─── 页面 ────────────────────────────────────────────────────────────
@notice_bp.route('/')
@login_required
def list_page():
    groups = [{'label': label,
               'types': [{'key': key, 'name': name} for key, name in items]}
              for label, items in NOTICE_GROUPS]
    # 075 RC-16：左侧分类计数（v7-07「N 条」，按 NoticeRecord 实数 group_by）
    from sqlalchemy import func
    count_rows = db.session.query(
        NoticeRecord.notice_type, func.count(NoticeRecord.id)
    ).group_by(NoticeRecord.notice_type).all()
    type_counts = {t: int(c) for t, c in count_rows}
    return render_template('notice/list.html', notice_groups=groups, type_counts=type_counts)


# ─── 模板 ────────────────────────────────────────────────────────────
@notice_bp.route('/api/templates')
@login_required
def api_templates():
    notice_type = request.args.get('notice_type', '').strip()
    keyword = request.args.get('keyword', '').strip()
    include_inactive = request.args.get('include_inactive', '0') == '1'
    query = NoticeTemplate.query
    if notice_type:
        query = query.filter(NoticeTemplate.notice_type == notice_type)
    if keyword:
        query = query.filter(NoticeTemplate.name.contains(keyword))
    if not include_inactive:
        query = query.filter(NoticeTemplate.is_active.is_(True))
    items = query.order_by(NoticeTemplate.is_active.desc(),
                           NoticeTemplate.updated_at.desc(),
                           NoticeTemplate.id.desc()).all()
    return json_response(True, data=[_template_payload(i) for i in items])


@notice_bp.route('/api/template/<int:item_id>')
@login_required
def api_template_detail(item_id):
    item = NoticeTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    return json_response(True, data=_template_payload(item))


@notice_bp.route('/api/template/create', methods=['POST'])
@login_required
def api_template_create():
    payload = request.get_json(silent=True) or request.form.to_dict()
    notice_type = (payload.get('notice_type') or '').strip()
    if notice_type not in NOTICE_TYPE_NAMES:
        return json_response(False, '公示类型无效')
    name = (payload.get('name') or NOTICE_TYPE_NAMES[notice_type]).strip()
    title = (payload.get('title') or name).strip()
    schema = normalize_table(payload.get('schema'), notice_type)
    item = NoticeTemplate(name=name, notice_type=notice_type, title=title,
                          table_schema=json.dumps(schema, ensure_ascii=False),
                          is_custom=True, is_active=True, version=1,
                          created_by=_operator_name(), updated_by=_operator_name())
    db.session.add(item)
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'add', f'新建公示模板：{name}')
    return json_response(True, '模板已创建', {'item': _template_payload(item)})


@notice_bp.route('/api/template/update/<int:item_id>', methods=['POST'])
@login_required
def api_template_update(item_id):
    item = NoticeTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    payload = request.get_json(silent=True) or request.form.to_dict()
    if payload.get('name') is not None:
        item.name = str(payload['name']).strip() or item.name
    if payload.get('title') is not None:
        item.title = str(payload['title']).strip() or item.name
    if payload.get('schema') is not None:
        item.table_schema = json.dumps(
            normalize_table(payload.get('schema'), item.notice_type),
            ensure_ascii=False)
    item.version = (item.version or 1) + 1
    item.updated_by = _operator_name()
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'edit', f'更新公示模板：{item.name}')
    return json_response(True, '模板已保存', {'item': _template_payload(item)})


@notice_bp.route('/api/template/status/<int:item_id>', methods=['POST'])
@login_required
def api_template_status(item_id):
    item = NoticeTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    payload = request.get_json(silent=True) or {}
    target = payload.get('is_active')
    item.is_active = bool(target) if target is not None else not item.is_active
    item.updated_by = _operator_name()
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'edit',
                  f'{"启用" if item.is_active else "停用"}公示模板：{item.name}')
    return json_response(True, '状态已更新', {'item': _template_payload(item)})


@notice_bp.route('/api/template/delete/<int:item_id>', methods=['POST'])
@login_required
def api_template_delete(item_id):
    item = NoticeTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    if NoticeRecord.query.filter_by(template_id=item_id).first():
        return json_response(False, '该模板已有公示记录，请先删除相关记录')
    name = item.name
    db.session.delete(item)
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'delete', f'删除公示模板：{name}')
    return json_response(True, '模板已删除')


# ─── 记录 ────────────────────────────────────────────────────────────
@notice_bp.route('/api/records')
@login_required
def api_records():
    notice_type = request.args.get('notice_type', '').strip()
    period = request.args.get('period', '').strip()
    status = request.args.get('status', '').strip()
    query = NoticeRecord.query
    if notice_type:
        query = query.filter(NoticeRecord.notice_type == notice_type)
    if period:
        query = query.filter(NoticeRecord.period == period)
    if status:
        query = query.filter(NoticeRecord.status == status)
    items = query.order_by(NoticeRecord.period.desc(), NoticeRecord.id.desc()).all()
    return json_response(True, data=[_record_payload(i) for i in items])


@notice_bp.route('/api/record/generate', methods=['POST'])
@login_required
def api_record_generate():
    payload = request.get_json(silent=True) or request.form.to_dict()
    try:
        template_id = int(payload.get('template_id') or 0)
    except (TypeError, ValueError):
        template_id = 0
    period = str(payload.get('period') or '').strip()
    if not _PERIOD_RE.match(period):
        return json_response(False, '公示月份格式应为 YYYY-MM')
    template = NoticeTemplate.query.get(template_id)
    if not template:
        return json_response(False, '模板不存在')
    existing = NoticeRecord.query.filter_by(template_id=template_id, period=period).first()
    if existing:
        return json_response(False, '该模板该月份已生成公示', _record_payload(existing))
    record = NoticeRecord(
        template_id=template.id, notice_type=template.notice_type, period=period,
        title=template.title or template.name,
        table_data=json.dumps(template.schema_dict(), ensure_ascii=False),
        status='draft', created_by=_operator_name())
    db.session.add(record)
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'edit',
                  f'生成公示：{record.title}（{period}）')
    return json_response(True, '公示已生成', {'item': _record_payload(record)})


def _parse_upload(file_storage):
    """解析 .xlsx / .csv → (headers, rows)；失败抛 ValueError（中文可读原因）。"""
    import csv
    import io

    data = file_storage.read()
    if not data:
        raise ValueError('上传文件为空')
    filename = (file_storage.filename or '').lower()
    if filename.endswith('.csv'):
        text = None
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError('CSV 编码无法识别（支持 UTF-8 / GB18030）')
        rows = [row for row in csv.reader(io.StringIO(text))]
    else:
        if not (len(data) >= 4 and data[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
            raise ValueError('仅支持 .xlsx / .csv 文件')
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            raise ValueError('Excel 文件无法解析')
        ws = wb.active
        rows = [['' if c is None else str(c) for c in row]
                for row in ws.iter_rows(values_only=True)]
        wb.close()
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise ValueError('文件没有可用数据行')
    return rows[0], rows[1:]


@notice_bp.route('/api/record/import', methods=['POST'])
@login_required
def api_record_import():
    file_storage = request.files.get('file')
    if not file_storage or not file_storage.filename:
        return json_response(False, '请选择 Excel（.xlsx）或 CSV 文件')
    raw_columns = request.form.get('columns')
    try:
        columns_in = json.loads(raw_columns) if raw_columns else None
    except ValueError:
        return json_response(False, '模板列定义格式错误')
    columns = normalize_table({'columns': columns_in} if columns_in else None,
                              'dibao')['columns']
    try:
        headers, rows = _parse_upload(file_storage)
    except ValueError as e:
        return json_response(False, str(e))
    if not any(str((c or {}).get('label') or '').strip() for c in columns):
        return json_response(False, '模板列名为空，无法匹配')
    result = match_upload_rows(columns, headers, rows)
    if not result['matched']:
        return json_response(False, '上传表头与模板列名没有任何匹配：'
                                   + '、'.join(str(h) for h in headers[:8]))
    return json_response(True, '解析成功', result)


@notice_bp.route('/api/record/update/<int:item_id>', methods=['POST'])
@login_required
def api_record_update(item_id):
    record = NoticeRecord.query.get(item_id)
    if not record:
        return json_response(False, '公示记录不存在')
    payload = request.get_json(silent=True) or request.form.to_dict()
    if payload.get('title') is not None:
        record.title = str(payload['title']).strip() or record.title
    if payload.get('table_data') is not None:
        record.table_data = json.dumps(
            normalize_table(payload.get('table_data'), record.notice_type),
            ensure_ascii=False)
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'edit',
                  f'保存公示记录：{record.title}（{record.period}）')
    return json_response(True, '公示已保存', {'item': _record_payload(record)})


@notice_bp.route('/api/record/<int:item_id>')
@login_required
def api_record_detail(item_id):
    record = NoticeRecord.query.get(item_id)
    if not record:
        return json_response(False, '公示记录不存在')
    return json_response(True, data=_record_payload(record))


@notice_bp.route('/api/record/status/<int:item_id>', methods=['POST'])
@login_required
def api_record_status(item_id):
    record = NoticeRecord.query.get(item_id)
    if not record:
        return json_response(False, '公示记录不存在')
    payload = request.get_json(silent=True) or {}
    target = str(payload.get('status') or '').strip()
    if target not in ('draft', 'published'):
        return json_response(False, '状态无效（draft|published）')
    record.status = target
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'edit',
                  f'{"发布" if target == "published" else "撤回"}公示：{record.title}（{record.period}）')
    return json_response(True, '状态已更新', {'item': _record_payload(record)})


@notice_bp.route('/api/record/delete/<int:item_id>', methods=['POST'])
@login_required
def api_record_delete(item_id):
    record = NoticeRecord.query.get(item_id)
    if not record:
        return json_response(False, '公示记录不存在')
    title, period = record.title, record.period
    db.session.delete(record)
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'delete',
                  f'删除公示记录：{title}（{period}）')
    return json_response(True, '公示记录已删除')


@notice_bp.route('/api/record/export/<int:item_id>')
@login_required
def api_record_export(item_id):
    import io

    import openpyxl
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    record = NoticeRecord.query.get(item_id)
    if not record:
        return json_response(False, '公示记录不存在')
    table = record.data_dict()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '补贴公示'
    _append_text_row(ws, [record.title or '补贴公示'])
    ws['A1'].font = Font(size=14, bold=True)
    ws['A1'].alignment = Alignment(horizontal='center')
    _append_text_row(ws, [c['label'] for c in table['columns']])
    for cell in ws[2]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal='center')
    for row in table['rows']:
        _append_text_row(ws, row)
    for idx in range(len(table['columns'])):
        ws.column_dimensions[get_column_letter(idx + 1)].width = max(
            8, min(int((int(table['columns'][idx].get('width') or 120)) / 10), 40))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    log_operation(_operator_name(), '补贴公示', 'export',
                  f'导出公示：{record.title}（{record.period}）')
    filename = f'{(record.title or "补贴公示")}_{record.period}.xlsx'
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
