# -*- coding: utf-8 -*-
"""routes.word_form — 通用 Word (.docx) 表单中心（上传任意 .docx 自动解析 + A4 版式微调 + 居民回填 + 原位导出）。

权限（ADR-016）：在 utils/permissions.BLUEPRINT_MODULES 复用 'notice' 模块位，零角色迁移。
"""
import io
import os
import re

from flask import Blueprint, render_template, request, send_file, session

from models import Dibao, Disabled, Resident
from utils.decorators import login_required
from utils.docx_form_engine import (
    FIELD_OPTIONS,
    WordFormStore,
    build_resident_autofill_dict,
    render_docx_with_values,
)
from utils.helpers import json_response, log_operation

word_form_bp = Blueprint('word_form', __name__)

_MAX_DOCX_BYTES = 10 * 1024 * 1024  # 10MB
_MAX_SCHEMA_BLOCKS = 200
_MAX_TABLE_ROWS = 500


def _get_store():
    # 函数内延迟导入：与 utils/device_auth.py 同惯例，且测试 monkeypatch 模块级 DATA_DIR 可生效
    from config import DATA_DIR
    return WordFormStore(os.path.join(DATA_DIR, 'word_forms'))


def _operator_name():
    return session.get('username') or ''


def _safe_download_name(title):
    cleaned = re.sub(r'[\\/:*?"<>|]+', '_', (title or '表单导出').strip())
    return (cleaned[:60] or '表单导出') + '.docx'


@word_form_bp.route('/')
@login_required
def index():
    return render_template('word_form/index.html', field_options=FIELD_OPTIONS)


@word_form_bp.route('/api/templates')
@login_required
def api_list_templates():
    return json_response(data=_get_store().list_templates())


@word_form_bp.route('/api/template/upload', methods=['POST'])
@login_required
def api_upload_template():
    f = request.files.get('file')
    if not f or not f.filename:
        return json_response(False, '请选择 .docx Word 表单文件')
    if not f.filename.lower().endswith('.docx'):
        return json_response(False, '仅支持 .docx 格式的 Word 表单（旧版 .doc 请先另存为 .docx）')
    raw = f.read()
    if len(raw) > _MAX_DOCX_BYTES:
        return json_response(False, '文件大小超过 10MB 限制')
    title = (request.form.get('title') or '').strip()
    try:
        tpl = _get_store().create_template_from_docx(
            raw,
            filename=f.filename,
            title=title or None,
            created_by=session.get('username', ''),
        )
    except ValueError as exc:
        return json_response(False, str(exc))
    except Exception as exc:
        return json_response(False, f'解析 .docx 失败：{exc}')

    log_operation(_operator_name(), 'Word表单', 'add',
                  f"上传表单模板「{tpl['title']}」", detail=f'模板 {tpl["id"]}')
    return json_response(True, f"已自动解析表单模板：{tpl['title']}", data=tpl)


@word_form_bp.route('/api/template/<tpl_id>')
@login_required
def api_get_template(tpl_id):
    try:
        tpl = _get_store().get_template(tpl_id)
    except ValueError as exc:
        return json_response(False, str(exc))
    if not tpl:
        return json_response(False, '表单模板不存在')
    return json_response(data=tpl)


@word_form_bp.route('/api/template/<tpl_id>/schema', methods=['POST'])
@login_required
def api_update_template_schema(tpl_id):
    payload = request.get_json(silent=True) or {}
    try:
        tpl = _get_store().update_template_schema(tpl_id, payload)
    except ValueError as exc:
        return json_response(False, str(exc))
    if not tpl:
        return json_response(False, '表单模板不存在')
    log_operation(_operator_name(), 'Word表单', 'edit',
                  f"更新模板版式「{tpl['title']}」", detail=f'模板 {tpl_id}')
    return json_response(True, '模板版式与字段绑定已保存', data=tpl)


@word_form_bp.route('/api/template/<tpl_id>/delete', methods=['POST'])
@login_required
def api_delete_template(tpl_id):
    try:
        ok = _get_store().delete_template(tpl_id)
    except ValueError as exc:
        return json_response(False, str(exc))
    if not ok:
        return json_response(False, '表单模板不存在')
    log_operation(_operator_name(), 'Word表单', 'delete', '删除表单模板', detail=f'模板 {tpl_id}')
    return json_response(True, '模板已删除')


@word_form_bp.route('/api/residents/search')
@login_required
def api_search_residents():
    q = (request.args.get('q') or '').strip()
    query = Resident.query.filter(Resident.status == 'normal')
    if q:
        query = query.filter(Resident.name.contains(q))
    rows = query.order_by(Resident.id.desc()).limit(25).all()
    items = []
    for r in rows:
        label = f'{r.name}（{r.gender or "-"} · {r.village_group or "未分组"}）'
        items.append({'id': r.id, 'name': r.name, 'label': label})
    return json_response(data=items)


@word_form_bp.route('/api/residents/<int:resident_id>/autofill')
@login_required
def api_resident_autofill(resident_id):
    resident = Resident.query.get(resident_id)
    if not resident:
        return json_response(False, '居民不存在')
    disabled = Disabled.query.filter_by(resident_id=resident.id).first()
    dibao = Dibao.query.filter_by(resident_id=resident.id).first()
    family_size = None
    if resident.household_code:
        family_size = Resident.query.filter_by(
            household_code=resident.household_code,
            status='normal',
        ).count()
    data = build_resident_autofill_dict(resident, disabled=disabled, dibao=dibao, family_size=family_size)
    return json_response(data=data)


@word_form_bp.route('/api/records')
@login_required
def api_list_records():
    tpl_id = (request.args.get('template_id') or '').strip() or None
    return json_response(data=_get_store().list_records(template_id=tpl_id))


@word_form_bp.route('/api/record/<rec_id>')
@login_required
def api_get_record(rec_id):
    try:
        rec = _get_store().get_record(rec_id)
    except ValueError as exc:
        return json_response(False, str(exc))
    if not rec:
        return json_response(False, '填报记录不存在')
    return json_response(data=rec)


@word_form_bp.route('/api/record/save', methods=['POST'])
@login_required
def api_save_record():
    payload = request.get_json(silent=True) or {}
    tpl_id = (payload.get('template_id') or '').strip()
    if not tpl_id:
        return json_response(False, '缺少 template_id')
    try:
        rec = _get_store().save_record(
            template_id=tpl_id,
            title=payload.get('title') or '',
            values=payload.get('values') or {},
            schema_snapshot=payload.get('schema_snapshot'),
            resident_id=payload.get('resident_id'),
            rec_id=payload.get('id') or None,
            operator=session.get('username', ''),
        )
    except ValueError as exc:
        return json_response(False, str(exc))
    log_operation(_operator_name(), 'Word表单', 'add',
                  f"保存填报记录「{rec['title']}」", detail=f'记录 {rec["id"]}')
    return json_response(True, '表单记录已保存', data=rec)


@word_form_bp.route('/api/record/<rec_id>/delete', methods=['POST'])
@login_required
def api_delete_record(rec_id):
    try:
        ok = _get_store().delete_record(rec_id)
    except ValueError as exc:
        return json_response(False, str(exc))
    if not ok:
        return json_response(False, '填报记录不存在')
    log_operation(_operator_name(), 'Word表单', 'delete', '删除填报记录', detail=f'记录 {rec_id}')
    return json_response(True, '填报记录已删除')


@word_form_bp.route('/api/export-docx', methods=['POST'])
@login_required
def api_export_docx():
    payload = request.get_json(silent=True) or {}
    tpl_id = (payload.get('template_id') or '').strip()
    if not tpl_id:
        return json_response(False, '缺少 template_id')
    store = _get_store()
    try:
        raw_docx = store.get_template_docx_bytes(tpl_id)
        tpl = store.get_template(tpl_id)
    except ValueError as exc:
        return json_response(False, str(exc))
    if not raw_docx or not tpl:
        return json_response(False, '表单模板不存在或原文件缺失')

    schema = payload.get('schema') or tpl
    values = payload.get('values') or {}
    # 防御前端提交超大 schema：渲染按行补建 Word 表格行，行数失控会耗尽内存（worker 4GB 约束）
    blocks = schema.get('blocks') or []
    if len(blocks) > _MAX_SCHEMA_BLOCKS or any(
        len(b.get('rows') or []) > _MAX_TABLE_ROWS
        for b in blocks
        if isinstance(b, dict) and isinstance(b.get('rows') or [], list)
    ):
        return json_response(False, '表单结构过大（超过 200 块 / 500 行），已拒绝导出')
    export_title = (payload.get('title') or schema.get('title') or tpl.get('title') or '表单导出').strip()

    try:
        out_bytes = render_docx_with_values(raw_docx, schema, values=values)
    except Exception as exc:
        return json_response(False, f'导出原版 .docx 失败：{exc}')

    log_operation(_operator_name(), 'Word表单', 'export',
                  f"导出「{export_title}」", detail=f'模板 {tpl_id}')
    return send_file(
        io.BytesIO(out_bytes),
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True,
        download_name=_safe_download_name(export_title),
    )
