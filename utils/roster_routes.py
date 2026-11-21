# -*- coding: utf-8 -*-
"""花名册（Roster）标准 8 端点蓝图注册工厂（P1-2）。

独立于 utils/roster_facade.py 门面层，保持门面 API（get_table / create_table /
import_records / list_rows / save_row / export_rows / MODULE_KEYS 等）完全不变。
"""
import json
import os
import secrets
import uuid

import config
from flask import abort, jsonify, render_template, request, session
from utils import roster_facade as facade
from utils.decorators import login_required
from utils.helpers import export_excel, json_response, log_operation
from utils.permissions import current_user_has_permission
from utils import softtable_engine as eng


def register_roster_routes(bp, *, module_key: str, group_name: str, log_module: str,
                           allowed_ext=('.xlsx', '.xlsm', '.csv'),
                           max_upload_bytes: int = 20 * 1024 * 1024):
    """将 8 个标准花名册端点统一挂载到业务蓝图，消除跨模块重复拷贝。"""
    _ROSTER_MODULE_KEY = module_key
    _ROSTER_PREFIX = '/' + module_key
    _ROSTER_GROUP_NAME = group_name
    _LOG_MODULE = log_module
    _ROSTER_ALLOWED_EXT = allowed_ext
    _ROSTER_MAX_UPLOAD_BYTES = max_upload_bytes

    def _roster_who():
        return session.get('username', '')


    def _roster_temp_dir():
        """上传临时目录（与 dynatable/shop/pension 同款 <TEMP_DIR>/softtable；token 建表后即删）。"""
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
        return filters


    @bp.route('/roster/<code>/')
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
                                   roster_prefix=_ROSTER_PREFIX,
                                   meta={'name': table.name, 'module_key': table.module_key,
                                         'description': table.description or ''})
        return render_template('components/roster/roster_page.html',
                               table=table, code=code, fields=fields,
                               roster_prefix=_ROSTER_PREFIX, back_url=_ROSTER_PREFIX + '/')


    @bp.route('/roster/<code>/upload')
    @login_required
    def roster_upload_page(code):
        """上传与字段确认页（GET → 所属模块 view）：未建表引导页与台账页共用同一上传流程的独立入口。"""
        table = facade.get_table(_ROSTER_MODULE_KEY, code)
        if table is None:
            abort(404)
        return render_template('components/roster/roster_upload.html', code=code,
                               roster_prefix=_ROSTER_PREFIX,
                               meta={'name': table.name, 'module_key': table.module_key})


    @bp.route('/roster/<code>/api/list')
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
        try:
            row = facade.save_row(table, rid, values, created_by=_roster_who())
        except eng.SoftTableError as exc:
            return json_response(False, str(exc))
        log_operation(_roster_who(), _LOG_MODULE, 'edit' if rid else 'add',
                      '台账 %s（%s）%s记录 #%s' % (table.name, code,
                                                  '编辑' if rid else '新增', row.get('id')))
        return json_response(True, '已保存', data=row)


    @bp.route('/roster/<code>/api/record', methods=['POST'])
    @login_required
    def roster_record_add(code):
        """新增一行（视图名含 add → 所属模块 add 动作位）。"""
        return _roster_record_save(code, None)


    @bp.route('/roster/<code>/api/record/<int:rid>', methods=['POST'])
    @login_required
    def roster_record_edit(code, rid):
        """更新一行（视图名含 edit → 所属模块 edit 动作位）。"""
        return _roster_record_save(code, rid)


    @bp.route('/roster/<code>/api/upload', methods=['POST'])
    @login_required
    def roster_upload(code):
        """上传 Excel/CSV → 引擎解析 + 字段推断，token + meta 落临时目录（建表后即删）。"""
        raw = request.files.get('file')
        if not raw or not raw.filename:
            return json_response(False, '请选择要上传的 .xlsx / .csv 文件')
        filename = raw.filename
        ext = os.path.splitext(filename)[1].lower()
        if ext not in _ROSTER_ALLOWED_EXT:
            return json_response(False, '只支持 .xlsx / .csv 文件')
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


    @bp.route('/roster/<code>/api/create', methods=['POST'])
    @login_required
    def roster_create(code):
        """确认字段后建表：code 由 URL 决定（不取 payload），module/group 为所属模块门面固定值；
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

        log_operation(_roster_who(), _LOG_MODULE, 'add', '软表建表 %s（%s）' % (name, code),
                      '字段 %d 个，导入 %d 行' % (len(fields), imported))
        meta_file = _roster_meta_path(token)
        if os.path.exists(meta_file):
            os.remove(meta_file)
        if os.path.exists(path):
            os.remove(path)
        return json_response(True, '建表完成，已导入 %d 行' % imported,
                             data={'code': code, 'imported': imported, 'skipped': len(errors)})


    @bp.route('/roster/<code>/api/export')
    @login_required
    def roster_export(code):
        """按当前筛选导出 xlsx（096 口径）；活动记录无 identity 列，统一走门面打码口径。"""
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
        log_operation(_roster_who(), _LOG_MODULE, 'export',
                      '导出台账 %s（%s）%d 行' % (table.name, code, len(rows)))
        # identity 列已在 decode_records 打码/解密 → already_masked=True 防二次脱敏（R-096b-01）
        return export_excel(headers, rows, '%s.xlsx' % table.name,
                            can_view_sensitive=can_view_sensitive, already_masked=True)
