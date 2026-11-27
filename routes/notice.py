# -*- coding: utf-8 -*-
"""routes.notice — 补贴公示（045）：模板中心 + 月度公示记录（新蓝图）。

端点（ADR-016：`notice` 已入 BLUEPRINT_MODULES，动作位 view/add/edit/delete/export 自动推断）：
- GET  /notice/                            公示工作台（左侧 3 组分类 + 右侧模板/记录）
- GET  /notice/api/templates               模板列表（可按 notice_type / 关键词过滤）
- GET  /notice/api/template/<id>           模板详情
- POST /notice/api/template/create         新建模板（可只传 notice_type → 套内置默认三栏公示表头）
- POST /notice/api/template/update/<id>    更新模板（名称/表名/表格结构）
- POST /notice/api/template/reset/<id>     恢复为对应类型的标准公示表格式
- POST /notice/api/template/status/<id>    启用/停用
- POST /notice/api/template/delete/<id>    删除（有记录则拒绝，避免悬空引用）
- GET  /notice/api/records                 记录列表（按 notice_type / period / 状态过滤）
- POST /notice/api/record/generate         按月生成（支持沿用上期名单，自动生成「社区+y年m月+台账名」副标题）
- POST /notice/api/record/import           上传 Excel/CSV → 自动跳过标题行 + 表头匹配（兼容三栏与单栏自动折行）
- POST /notice/api/record/update/<id>      更新记录（支持修改年月 period、副标题 title、表格数据与表尾备注）
- GET  /notice/api/record/<id>             记录详情
- POST /notice/api/record/status/<id>      draft ↔ published
- POST /notice/api/record/delete/<id>      删除记录
- GET  /notice/api/record/export/<id>      导出 1:1 真实公示栏格式 Excel（文件名：y年m月+对应台账名.xlsx）

表格语义（归一化 / 默认结构 / 年月标题 / 导出文件名 / 上传列名匹配）全部在 models.notice，
本文件只做 HTTP 映射与文件解析/导出（AGENTS §7 owner 边界）。
"""
import json
import re

from flask import (Blueprint, render_template, request, send_file, session)

from models import NoticeRecord, NoticeTemplate, db
from models.notice import (
    DEFAULT_HEADER_TITLE,
    DEFAULT_VILLAGE_NAME,
    NOTICE_GROUPS,
    NOTICE_TYPE_NAMES,
    _norm_label,
    add_custom_group,
    add_custom_notice_type,
    build_export_filename,
    default_table,
    format_period_cn,
    get_all_notice_groups,
    get_all_notice_type_names,
    match_upload_rows,
    normalize_table,
    parse_period,
    remove_custom_notice_type,
    render_subtitle,
    update_subtitle_period,
)
from utils.decorators import login_required
from utils.helpers import json_response, log_operation

notice_bp = Blueprint('notice', __name__)

_PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')
_FOOTER_NOTE_HINTS = ('监督', '咨询电话', '举报电话', '上级文件', '可自定义编辑', '说明：', '备注：', '注：')


def _default_village_name():
    """读取系统设置中的村/社区名称（未配置回退「石垭社区」）。"""
    try:
        from models import DictCategory, DictItem
        item = DictItem.query.join(DictCategory).filter(
            DictCategory.name == '系统设置',
            DictItem.label == 'village_name',
        ).first()
        if item and (item.value or '').strip():
            return item.value.strip()
    except Exception:
        pass
    return DEFAULT_VILLAGE_NAME


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


def _set_text_cell(ws, row, col, value):
    cell = ws.cell(row=row, column=col, value='' if value is None else str(value))
    cell.data_type = 's'
    return cell


def _operator_name():
    return session.get('username') or ''


def _template_payload(item):
    return {
        'id': item.id,
        'name': item.name,
        'notice_type': item.notice_type,
        'type_name': get_all_notice_type_names().get(item.notice_type, item.notice_type),
        'title': item.title or item.name,
        'schema': item.schema_dict(),
        'is_custom': bool(item.is_custom),
        'is_active': bool(item.is_active),
        'version': item.version or 1,
        'updated_at': item.updated_at.strftime('%Y-%m-%d %H:%M') if item.updated_at else '',
    }


def _record_payload(item):
    year, month = parse_period(item.period)
    return {
        'id': item.id,
        'template_id': item.template_id,
        'notice_type': item.notice_type,
        'type_name': get_all_notice_type_names().get(item.notice_type, item.notice_type),
        'period': item.period,
        'year': year,
        'month': month,
        'period_cn': format_period_cn(item.period),
        'title': item.title,
        'export_filename': build_export_filename(item.notice_type, item.period, item.title),
        'table_data': item.data_dict(),
        'status': item.status,
        'created_by': item.created_by or '',
        'updated_at': item.updated_at.strftime('%Y-%m-%d %H:%M') if item.updated_at else '',
    }


# ─── 页面 ────────────────────────────────────────────────────────────
def _catalog_payload():
    from sqlalchemy import func
    groups = [{'label': label,
               'types': [{'key': key, 'name': name} for key, name in items]}
              for label, items in get_all_notice_groups()]
    count_rows = db.session.query(
        NoticeRecord.notice_type, func.count(NoticeRecord.id)
    ).group_by(NoticeRecord.notice_type).all()
    type_counts = {t: int(c) for t, c in count_rows}
    return groups, type_counts


@notice_bp.route('/')
@login_required
def list_page():
    groups, type_counts = _catalog_payload()
    return render_template(
        'notice/list.html',
        notice_groups=groups,
        type_counts=type_counts,
        default_village=_default_village_name(),
    )


@notice_bp.route('/api/catalog')
@login_required
def api_catalog():
    groups, type_counts = _catalog_payload()
    return json_response(True, data={'groups': groups, 'type_counts': type_counts})


@notice_bp.route('/api/group/create', methods=['POST'])
@login_required
def api_group_create():
    """新增自定义公示分类分组（如「资产类」），可选同步创建该分类下第一张公示表。"""
    payload = request.get_json(silent=True) or request.form.to_dict()
    group_name = (payload.get('group_name') or payload.get('name') or '').strip()
    table_name = (payload.get('table_name') or '').strip()
    preset = (payload.get('preset') or 'common').strip()
    try:
        if table_name:
            created = add_custom_notice_type(group_name, table_name, preset=preset)
            schema = default_table(
                created['key'], village_name=_default_village_name(), preset=created['preset']
            )
            tpl = NoticeTemplate(
                name=created['name'], notice_type=created['key'], title=created['name'],
                table_schema=json.dumps(schema, ensure_ascii=False),
                is_custom=True, is_active=True, version=1,
                created_by=_operator_name(), updated_by=_operator_name(),
            )
            db.session.add(tpl)
            db.session.commit()
            log_operation(_operator_name(), '补贴公示', 'add',
                          f'新增公示分类及表：{group_name} / {table_name}')
            groups, type_counts = _catalog_payload()
            return json_response(True, '分类与公示表已创建', {
                'group': group_name, 'type': created, 'template': _template_payload(tpl),
                'groups': groups, 'type_counts': type_counts,
            })
        added_group = add_custom_group(group_name)
    except ValueError as e:
        return json_response(False, str(e))
    log_operation(_operator_name(), '补贴公示', 'add', f'新增公示分类：{added_group}')
    groups, type_counts = _catalog_payload()
    return json_response(True, f'分类「{added_group}」已创建', {
        'group': added_group, 'groups': groups, 'type_counts': type_counts,
    })


@notice_bp.route('/api/type/create', methods=['POST'])
@login_required
def api_type_create():
    """在指定分类（已有或新建）下新建一张公示表及其初始模板。"""
    payload = request.get_json(silent=True) or request.form.to_dict()
    group_name = (payload.get('group_name') or '').strip()
    table_name = (payload.get('table_name') or payload.get('name') or '').strip()
    preset = (payload.get('preset') or 'common').strip()
    try:
        created = add_custom_notice_type(group_name, table_name, preset=preset)
    except ValueError as e:
        return json_response(False, str(e))
    schema = default_table(
        created['key'], village_name=_default_village_name(), preset=created['preset']
    )
    tpl = NoticeTemplate(
        name=created['name'], notice_type=created['key'], title=created['name'],
        table_schema=json.dumps(schema, ensure_ascii=False),
        is_custom=True, is_active=True, version=1,
        created_by=_operator_name(), updated_by=_operator_name(),
    )
    db.session.add(tpl)
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'add',
                  f'在「{group_name}」下新建公示表：{table_name}')
    groups, type_counts = _catalog_payload()
    return json_response(True, f'公示表「{table_name}」已创建', {
        'type': created, 'template': _template_payload(tpl),
        'groups': groups, 'type_counts': type_counts,
    })


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
    all_type_names = get_all_notice_type_names()
    if notice_type not in all_type_names:
        return json_response(False, '公示类型无效')
    name = (payload.get('name') or all_type_names[notice_type]).strip()
    title = (payload.get('title') or name).strip()
    schema = normalize_table(
        payload.get('schema'), notice_type, village_name=_default_village_name()
    )
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
            normalize_table(
                payload.get('schema'), item.notice_type, village_name=_default_village_name()
            ),
            ensure_ascii=False)
    item.version = (item.version or 1) + 1
    item.updated_by = _operator_name()
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'edit', f'更新公示模板：{item.name}')
    return json_response(True, '模板已保存', {'item': _template_payload(item)})


@notice_bp.route('/api/template/reset/<int:item_id>', methods=['POST'])
@login_required
def api_template_reset(item_id):
    """将模板一键恢复为对应类型的标准三栏公示栏格式。"""
    item = NoticeTemplate.query.get(item_id)
    if not item:
        return json_response(False, '模板不存在')
    canonical = get_all_notice_type_names().get(item.notice_type)
    if canonical and not item.is_custom:
        item.name = canonical
        item.title = canonical
    item.table_schema = json.dumps(
        default_table(item.notice_type, village_name=_default_village_name()),
        ensure_ascii=False,
    )
    item.version = (item.version or 1) + 1
    item.updated_by = _operator_name()
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'edit', f'恢复标准公示格式：{item.name}')
    return json_response(True, '已恢复为标准公示表格式', {'item': _template_payload(item)})


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
    ntype = item.notice_type
    db.session.delete(item)
    db.session.commit()
    if str(ntype or '').startswith('cst_') and not NoticeTemplate.query.filter_by(notice_type=ntype).first():
        remove_custom_notice_type(ntype)
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

    village = _default_village_name()
    copy_latest = str(payload.get('copy_latest') or '').lower() in ('1', 'true', 'yes')
    table_data = template.schema_dict()
    if copy_latest:
        latest = NoticeRecord.query.filter_by(
            notice_type=template.notice_type
        ).order_by(NoticeRecord.period.desc(), NoticeRecord.id.desc()).first()
        if latest:
            prev_data = latest.data_dict()
            if prev_data.get('columns'):
                table_data = prev_data

    rec_village = table_data.get('village_name') or village
    title = render_subtitle(
        template.notice_type,
        period,
        village_name=rec_village,
        subtitle_tpl=table_data.get('subtitle_tpl', ''),
    )
    record = NoticeRecord(
        template_id=template.id, notice_type=template.notice_type, period=period,
        title=title,
        table_data=json.dumps(table_data, ensure_ascii=False),
        status='draft', created_by=_operator_name())
    db.session.add(record)
    db.session.commit()
    log_operation(_operator_name(), '补贴公示', 'edit',
                  f'生成公示：{record.title}（{period}）')
    return json_response(True, '公示已生成', {'item': _record_payload(record)})


def _is_footer_note_row(row):
    """识别真实公示表底部的合并备注/监督电话行，避免误当人员数据导入。"""
    non_empty = [(i, str(c or '').strip()) for i, c in enumerate(row) if str(c or '').strip()]
    if len(non_empty) != 1:
        return False
    col_idx, text = non_empty[0]
    if col_idx != 0:
        return False
    if any(hint in text for hint in _FOOTER_NOTE_HINTS):
        return True
    return len(text) >= 18


def _parse_upload(file_storage, expected_columns=None):
    """解析 .xlsx / .xls / .xlsb / .csv / .xlsm → (headers, rows, footer_notes)。

    智能跳过真实公示表前导的合并大标题/副标题行（如「补助资金公开表」「石垭社区2026年10月…」），
    并分离底部的合并备注行。
    """
    from utils.spreadsheet_reader import (
        SPREADSHEET_ERR_MSG,
        is_allowed_spreadsheet,
        read_spreadsheet_matrix,
        validate_spreadsheet_magic,
    )

    data = file_storage.read()
    if not data:
        raise ValueError('上传文件为空')
    filename = file_storage.filename or ''
    if not is_allowed_spreadsheet(filename):
        raise ValueError(SPREADSHEET_ERR_MSG)
    ok_magic, magic_err = validate_spreadsheet_magic(filename, data)
    if not ok_magic:
        raise ValueError(magic_err)
    try:
        raw_matrix = read_spreadsheet_matrix(filename, data)
    except Exception as exc:
        raise ValueError(f'文件无法解析：{exc}')
    rows = [['' if c is None else str(c) for c in row] for row in raw_matrix]
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        raise ValueError('文件没有可用数据行')

    # 在前 10 行中定位与模板表头匹配度最高的表头行（跳过前导合并大标题/副标题）
    header_idx = 0
    if expected_columns:
        expected_norms = {_norm_label((c or {}).get('label')) for c in expected_columns if (c or {}).get('label')}
        best_score = -1
        for idx, candidate in enumerate(rows[:10]):
            score = sum(1 for cell in candidate if _norm_label(cell) in expected_norms)
            if score > best_score:
                best_score = score
                header_idx = idx
            if best_score >= len(expected_columns):
                break

    headers = rows[header_idx]
    body_rows = rows[header_idx + 1:]

    # 剥离末尾的合并备注行（如低保监督电话、高龄新标准说明）
    footer_notes = []
    while body_rows and _is_footer_note_row(body_rows[-1]):
        footer_notes.insert(0, str(body_rows.pop()[0]).strip())

    return headers, body_rows, footer_notes


@notice_bp.route('/api/record/import', methods=['POST'])
@login_required
def api_record_import():
    file_storage = request.files.get('file')
    if not file_storage or not file_storage.filename:
        return json_response(False, '请选择 .xlsx / .xls / .xlsb / .csv / .xlsm 文件')
    raw_columns = request.form.get('columns')
    try:
        columns_in = json.loads(raw_columns) if raw_columns else None
    except ValueError:
        return json_response(False, '模板列定义格式错误')
    columns = normalize_table({'columns': columns_in} if columns_in else None,
                              'dibao')['columns']
    try:
        headers, rows, footer_notes = _parse_upload(file_storage, expected_columns=columns)
    except ValueError as e:
        return json_response(False, str(e))
    if not any(str((c or {}).get('label') or '').strip() for c in columns):
        return json_response(False, '模板列名为空，无法匹配')
    result = match_upload_rows(columns, headers, rows)
    if not result['matched']:
        return json_response(False, '上传表头与模板列名没有任何匹配：'
                                   + '、'.join(str(h) for h in headers[:8]))
    if footer_notes:
        result['footer_notes'] = footer_notes
    return json_response(True, '解析成功', result)


@notice_bp.route('/api/record/update/<int:item_id>', methods=['POST'])
@login_required
def api_record_update(item_id):
    record = NoticeRecord.query.get(item_id)
    if not record:
        return json_response(False, '公示记录不存在')
    payload = request.get_json(silent=True) or request.form.to_dict()

    # 支持修改年份与月份（period: YYYY-MM），联动更新副标题中的年月
    new_period = payload.get('period')
    period_changed = False
    if new_period is not None:
        new_period = str(new_period).strip()
        if not _PERIOD_RE.match(new_period):
            return json_response(False, '公示月份格式应为 YYYY-MM')
        if new_period != record.period:
            conflict = NoticeRecord.query.filter(
                NoticeRecord.template_id == record.template_id,
                NoticeRecord.period == new_period,
                NoticeRecord.id != record.id,
            ).first()
            if conflict:
                return json_response(False, f'{format_period_cn(new_period)}已存在公示记录，请直接切换查看')
            record.period = new_period
            period_changed = True

    if payload.get('table_data') is not None:
        record.table_data = json.dumps(
            normalize_table(
                payload.get('table_data'), record.notice_type, village_name=_default_village_name()
            ),
            ensure_ascii=False)

    if payload.get('title') is not None:
        explicit_title = str(payload['title']).strip()
        if explicit_title:
            if period_changed and explicit_title == record.title:
                data_obj = record.data_dict()
                record.title = update_subtitle_period(
                    explicit_title,
                    record.period,
                    notice_type=record.notice_type,
                    village_name=data_obj.get('village_name') or _default_village_name(),
                    subtitle_tpl=data_obj.get('subtitle_tpl', ''),
                )
            else:
                record.title = explicit_title
    elif period_changed:
        data_obj = record.data_dict()
        record.title = update_subtitle_period(
            record.title,
            record.period,
            notice_type=record.notice_type,
            village_name=data_obj.get('village_name') or _default_village_name(),
            subtitle_tpl=data_obj.get('subtitle_tpl', ''),
        )

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


def build_notice_workbook(record):
    """构建 1:1 对齐真实公示栏格式的 openpyxl.Workbook（纯导出构造器，便于单测验证）。"""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, Side
    from openpyxl.utils import get_column_letter

    table = record.data_dict()
    cols = table['columns']
    ncols = max(1, len(cols))
    last_col_letter = get_column_letter(ncols)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'

    thin = Side(style='thin', color='000000')
    grid_border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Row 1：大标题（合并 A1:I1，微软雅黑 24pt 粗体居中）
    header_title = (table.get('header_title') or DEFAULT_HEADER_TITLE).strip()
    c1 = _set_text_cell(ws, 1, 1, header_title)
    c1.font = Font(name='微软雅黑', size=24, bold=True)
    c1.alignment = Alignment(horizontal='center', vertical='center')
    if ncols > 1:
        ws.merge_cells(f'A1:{last_col_letter}1')
    ws.row_dimensions[1].height = 36

    # Row 2：副标题（合并 A2:I2，宋体 16pt 粗体居中，含社区名+年月+台账名）
    subtitle = (record.title or '').strip() or render_subtitle(
        record.notice_type, record.period,
        village_name=table.get('village_name') or DEFAULT_VILLAGE_NAME,
        subtitle_tpl=table.get('subtitle_tpl', ''),
    )
    c2 = _set_text_cell(ws, 2, 1, subtitle)
    c2.font = Font(name='宋体', size=16, bold=True)
    c2.alignment = Alignment(horizontal='center', vertical='center')
    if ncols > 1:
        ws.merge_cells(f'A2:{last_col_letter}2')
    ws.row_dimensions[2].height = 31

    # Row 3：表头（宋体 12pt 粗体居中自动换行 + 全细边框）
    ws.row_dimensions[3].height = 42
    for idx, col in enumerate(cols, 1):
        cell = _set_text_cell(ws, 3, idx, col.get('label') or '')
        cell.font = Font(name='宋体', size=12, bold=True)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = grid_border

    # Row 4..N：数据行（宋体 12pt 居中 + 全细边框 + 强制文本防公式注入）
    row_height = 27 if record.notice_type == 'dibao' else 26
    data_rows = table.get('rows') or [[''] * ncols]
    cur_row = 4
    for row in data_rows:
        ws.row_dimensions[cur_row].height = row_height
        for idx in range(1, ncols + 1):
            val = row[idx - 1] if idx - 1 < len(row) else ''
            cell = _set_text_cell(ws, cur_row, idx, val)
            cell.font = Font(name='宋体', size=12, bold=False)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = grid_border
        cur_row += 1

    # 表尾备注/监督电话行（每条非空备注合并 A{r}:I{r}，自动换行 + 细边框）
    footer_notes = [str(n).strip() for n in (table.get('footer_notes') or []) if str(n or '').strip()]
    for note in footer_notes:
        cell = _set_text_cell(ws, cur_row, 1, note)
        cell.font = Font(name='宋体', size=11, bold=False)
        cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        for idx in range(1, ncols + 1):
            ws.cell(row=cur_row, column=idx).border = grid_border
        if ncols > 1:
            ws.merge_cells(f'A{cur_row}:{last_col_letter}{cur_row}')
        line_cnt = max(note.count('\n') + 1, int(len(note) / 42) + 1)
        ws.row_dimensions[cur_row].height = max(28, min(120, line_cnt * 20))
        cur_row += 1

    # 列宽：按用户调整后的像素宽度映射为 Excel 字符宽，并强制适配 A4 单页宽度打印
    for idx, col in enumerate(cols, 1):
        px = int(col.get('width') or 100)
        ws.column_dimensions[get_column_letter(idx)].width = max(
            5.5, min(round(px / 9.5, 1), 48.0))

    # A4 打印设置（支持纵向/横向，固定宽度适配 1 页 + 每页重复 1~3 行抬头表头）
    page_size = table.get('page_size') or 'a4_portrait'
    ws.page_setup.orientation = (
        ws.ORIENTATION_LANDSCAPE if page_size == 'a4_landscape' else ws.ORIENTATION_PORTRAIT
    )
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = '1:3'

    return wb


def build_notice_xls_bytes(record):
    """构建兼容 Excel 2003+/WPS 的 .xls 文件（SpreadsheetML XML，零额外依赖）。

    1:1 保留合并标题、字体、细边框、用户调整列宽、A4 纵向/横向单页适配及 String 防公式注入。
    """
    from xml.sax.saxutils import escape

    table = record.data_dict()
    cols = table['columns']
    ncols = max(1, len(cols))
    merge_across = max(0, ncols - 1)
    header_title = (table.get('header_title') or DEFAULT_HEADER_TITLE).strip()
    subtitle = (record.title or '').strip() or render_subtitle(
        record.notice_type, record.period,
        village_name=table.get('village_name') or DEFAULT_VILLAGE_NAME,
        subtitle_tpl=table.get('subtitle_tpl', ''),
    )
    data_rows = table.get('rows') or [[''] * ncols]
    footer_notes = [str(n).strip() for n in (table.get('footer_notes') or []) if str(n or '').strip()]
    page_size = table.get('page_size') or 'a4_portrait'
    orient = 'Landscape' if page_size == 'a4_landscape' else 'Portrait'
    row_height = 27 if record.notice_type == 'dibao' else 26

    def _x(val):
        return escape('' if val is None else str(val)).replace('\n', '&#10;')

    col_tags = []
    for col in cols:
        pt_w = max(36, min(round(int(col.get('width') or 100) * 0.72, 1), 320))
        col_tags.append(f'   <Column ss:AutoFitWidth="0" ss:Width="{pt_w}"/>')

    row_xml = [
        f'   <Row ss:Height="36"><Cell ss:MergeAcross="{merge_across}" ss:StyleID="sTitle">'
        f'<Data ss:Type="String">{_x(header_title)}</Data></Cell></Row>',
        f'   <Row ss:Height="31"><Cell ss:MergeAcross="{merge_across}" ss:StyleID="sSub">'
        f'<Data ss:Type="String">{_x(subtitle)}</Data></Cell></Row>',
    ]
    hdr_cells = ''.join(
        f'<Cell ss:StyleID="sHead"><Data ss:Type="String">{_x(c.get("label") or "")}</Data></Cell>'
        for c in cols
    )
    row_xml.append(f'   <Row ss:Height="42">{hdr_cells}</Row>')

    for r in data_rows:
        cells = ''.join(
            f'<Cell ss:StyleID="sCell"><Data ss:Type="String">'
            f'{_x(r[i] if i < len(r) else "")}</Data></Cell>'
            for i in range(ncols)
        )
        row_xml.append(f'   <Row ss:Height="{row_height}">{cells}</Row>')

    for note in footer_notes:
        line_cnt = max(note.count('\n') + 1, int(len(note) / 42) + 1)
        nh = max(28, min(120, line_cnt * 20))
        row_xml.append(
            f'   <Row ss:Height="{nh}"><Cell ss:MergeAcross="{merge_across}" ss:StyleID="sNote">'
            f'<Data ss:Type="String">{_x(note)}</Data></Cell></Row>'
        )

    xml_doc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<?mso-application progid="Excel.Sheet"?>\n'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"\n'
        ' xmlns:o="urn:schemas-microsoft-com:office:office"\n'
        ' xmlns:x="urn:schemas-microsoft-com:office:excel"\n'
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n'
        ' <Styles>\n'
        '  <Style ss:ID="sTitle"><Alignment ss:Horizontal="Center" ss:Vertical="Center"/>'
        '<Font ss:FontName="微软雅黑" ss:Size="24" ss:Bold="1"/></Style>\n'
        '  <Style ss:ID="sSub"><Alignment ss:Horizontal="Center" ss:Vertical="Center"/>'
        '<Font ss:FontName="宋体" ss:Size="16" ss:Bold="1"/></Style>\n'
        '  <Style ss:ID="sHead"><Alignment ss:Horizontal="Center" ss:Vertical="Center" ss:WrapText="1"/>'
        '<Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/></Borders>'
        '<Font ss:FontName="宋体" ss:Size="12" ss:Bold="1"/></Style>\n'
        '  <Style ss:ID="sCell"><Alignment ss:Horizontal="Center" ss:Vertical="Center" ss:WrapText="1"/>'
        '<Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/></Borders>'
        '<Font ss:FontName="宋体" ss:Size="12"/></Style>\n'
        '  <Style ss:ID="sNote"><Alignment ss:Horizontal="Left" ss:Vertical="Center" ss:WrapText="1"/>'
        '<Borders><Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>'
        '<Border ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/></Borders>'
        '<Font ss:FontName="宋体" ss:Size="11"/></Style>\n'
        ' </Styles>\n'
        ' <Worksheet ss:Name="Sheet1">\n'
        '  <Names><NamedRange ss:Name="Print_Titles" ss:RefersTo="=Sheet1!R1:R3"/></Names>\n'
        '  <Table>\n'
        + '\n'.join(col_tags) + '\n'
        + '\n'.join(row_xml) + '\n'
        + '  </Table>\n'
        '  <WorksheetOptions xmlns="urn:schemas-microsoft-com:office:excel">\n'
        '   <PageSetup>\n'
        f'    <Layout x:Orientation="{orient}"/>\n'
        '    <PageMargins x:Bottom="0.4" x:Left="0.35" x:Right="0.35" x:Top="0.4"/>\n'
        '   </PageSetup>\n'
        '   <FitToPage/>\n'
        '   <Print><FitWidth>1</FitWidth><FitHeight>0</FitHeight><PaperSizeIndex>9</PaperSizeIndex></Print>\n'
        '  </WorksheetOptions>\n'
        ' </Worksheet>\n'
        '</Workbook>\n'
    )
    return xml_doc.encode('utf-8')


@notice_bp.route('/api/record/export/<int:item_id>')
@login_required
def api_record_export(item_id):
    import io

    record = NoticeRecord.query.get(item_id)
    if not record:
        return json_response(False, '公示记录不存在')
    fmt = (request.args.get('format') or 'xlsx').strip().lower()
    if fmt not in ('xlsx', 'xls'):
        fmt = 'xlsx'
    filename = build_export_filename(record.notice_type, record.period, record.title, fmt=fmt)
    if fmt == 'xls':
        buf = io.BytesIO(build_notice_xls_bytes(record))
        mime = 'application/vnd.ms-excel'
    else:
        wb = build_notice_workbook(record)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    log_operation(_operator_name(), '补贴公示', 'export',
                  f'导出公示：{filename}（{record.period}）')
    return send_file(buf, as_attachment=True, download_name=filename, mimetype=mime)
