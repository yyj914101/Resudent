# -*- coding: utf-8 -*-
"""utils/attachment_service.py — 092 模板附件区收口服务（save/list/delete/resolve_path）。

单一真源（092 任务书验收 4：三处路由均调用同一 attachment_service，禁止复制实现）：
disabled/care/subsidy 三蓝图只传参调用，上传/下载/删除/列表的全部业务与安全逻辑
（白名单、MIME 双检、大小上限、路径规范、跨模块校验）都收在本模块。

存储规范（092 任务书工作项 1）：
- 目录：uploads/attachments/<module_key>/<yyyyMM>/<uuid4 hex>.<ext>
- 原始文件名只入库（original_name），不落盘；落盘名由本模块生成（32 位 hex uuid +
  白名单扩展名，防路径穿越双保险）。
- uploads/attachments 不进 app.py UPLOAD_SUBDIR_PERM_MAP（同 safety 红线），
  文件访问一律走 /<bp>/api/attachments/<id>/download 端点，通用 serve_upload 不可触达。

安全（092 任务书工作项 4）：
- 白名单类型 + MIME 双检（扩展名 → 期望 MIME，content_type 不符即拒）；
- 大小上限默认 10MB，Config.ATTACHMENT_MAX_SIZE 可配；
- module_key 仅允许 ^[a-z0-9_]+$（module_key 进路径，防穿越）；
- resolve_path 内 normalize + 前缀断言，stored_name 必须匹配落盘名正则；
- 跨模块读取/删除：module_key 与记录不符 → PermissionError（路由转 403 JSON）。

错误约定（092 任务书工作项 2）：越界/非法抛 ValueError（路由转 json_response(False, msg)），
跨模块抛 PermissionError（路由转 403）；attachment_api 装饰器统一转译，路由零 try/except。
"""
import os
import re
import uuid
from datetime import datetime
from functools import wraps

from config import Config
from models import db, ModuleAttachment
from utils.helpers import json_response, log_operation

# 092 白名单：pdf/xlsx/xls/docx/doc/png/jpg（扩展名 → 标准 MIME，单一真值）
ALLOWED_EXTS = {
    'pdf': 'application/pdf',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'xls': 'application/vnd.ms-excel',
    'xlsb': 'application/vnd.ms-excel.sheet.binary.macroEnabled.12',
    'xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
    'csv': 'text/csv',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'doc': 'application/msword',
    'png': 'image/png',
    'jpg': 'image/jpeg',
}

# MIME 双检放行表：同扩展名下浏览器/系统可能给出的等价 MIME（office 旧版、通用流）。
# content_type 为空或 octet-stream（部分浏览器对 office 文档的兜底）不作为拒绝依据。
_COMPATIBLE_MIME = {
    'xlsx': {'application/octet-stream'},
    'xls': {'application/octet-stream', 'application/excel'},
    'xlsb': {'application/octet-stream', 'application/vnd.ms-excel'},
    'xlsm': {'application/octet-stream', 'application/vnd.ms-excel'},
    'csv': {'application/octet-stream', 'text/plain', 'application/vnd.ms-excel'},
    'docx': {'application/octet-stream'},
    'doc': {'application/octet-stream', 'application/vnd.ms-word'},
    'pdf': {'application/octet-stream', 'application/x-pdf', 'application/force-download'},
    'png': {'application/octet-stream', 'image/x-png', 'image/apng'},
    'jpg': {'application/octet-stream', 'image/jpg', 'image/pjpeg'},
}

DEFAULT_MAX_SIZE = 10 * 1024 * 1024  # 10MB（可配，先例 ELDERLY_AGE_THRESHOLD / MAX_CONTENT_LENGTH）

# 落盘名必须由本模块生成：32 位 hex（uuid4）+ 白名单扩展名（防路径穿越双保险，m012/safety 同款）
_STORED_NAME_RE = re.compile(r'^[0-9a-f]{32}\.[a-z0-9]{1,10}$')
# module_key 进存储路径，仅允许小写字母数字下划线（'..' / '/' / '\\' 一律拒）
_MODULE_KEY_RE = re.compile(r'^[a-z0-9_]+$')

_WHITELIST_MSG = '仅支持 ' + '、'.join('.' + e for e in ALLOWED_EXTS) + ' 格式'


def max_size() -> int:
    """单文件上限（字节）：Config.ATTACHMENT_MAX_SIZE 可配，缺省 10MB。"""
    size = getattr(Config, 'ATTACHMENT_MAX_SIZE', None)
    return size if isinstance(size, int) and size > 0 else DEFAULT_MAX_SIZE


def storage_root() -> str:
    return os.path.join(Config.UPLOAD_FOLDER, 'attachments')


def _clean_original_name(raw_name):
    """清洗上传原始文件名（展示/下载用，只入库不落盘）。

    不用 secure_filename：其默认剥离非 ASCII 字符，会吞掉中文文件名。
    仅去路径（防斜杠注入）、去控制字符、兜底空名；XSS 由前端 esc 单源转义
    （文件名只进展示层，不进磁盘路径）。
    """
    raw = (raw_name or '').replace('\\', '/').strip()
    name = raw.rsplit('/', 1)[-1].strip() or '未命名文件'
    return ''.join(ch for ch in name if ord(ch) >= 32)


def _module_dir(module_key):
    """<root>/<module_key>/<yyyyMM>/，按月分目录；makedirs 幂等。"""
    path = os.path.join(storage_root(), module_key, datetime.now().strftime('%Y%m'))
    os.makedirs(path, exist_ok=True)
    return path


def save(module_key, file_storage, uploaded_by=''):
    """上传落盘 + 落库，返回 ModuleAttachment。校验不过抛 ValueError，不落盘不留库。"""
    if not _MODULE_KEY_RE.match(module_key or ''):
        raise ValueError('非法模块标识')
    if file_storage is None or not (file_storage.filename or '').strip():
        raise ValueError('未选择文件')

    original = _clean_original_name(file_storage.filename)
    if '.' not in original:
        raise ValueError(_WHITELIST_MSG)
    ext = original.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(_WHITELIST_MSG)

    # MIME 双检（092 任务书工作项 2）：扩展名白名单已过，再核 content_type
    expected = ALLOWED_EXTS[ext]
    ct = (file_storage.content_type or '').split(';')[0].strip().lower()
    if ct and ct != expected and ct not in _COMPATIBLE_MIME.get(ext, set()):
        raise ValueError('文件内容类型与扩展名不符（%s）' % ct)

    # 大小上限：先探后存再实测（超限拒绝即不落盘；实存超限即刻删除）
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > max_size():
        raise ValueError('文件超过 %dMB 上限' % (max_size() // (1024 * 1024)))

    stored = '%s.%s' % (uuid.uuid4().hex, ext)
    abs_path = os.path.join(_module_dir(module_key), stored)
    file_storage.save(abs_path)
    real_size = os.path.getsize(abs_path)
    if real_size > max_size():
        os.remove(abs_path)
        raise ValueError('文件超过 %dMB 上限' % (max_size() // (1024 * 1024)))

    row = ModuleAttachment(
        module_key=module_key,
        target_type='module',   # 记录级预留：本批不启用 UI
        target_id=None,
        original_name=original,
        stored_name=stored,
        ext=ext,
        size=real_size,
        mime=expected,
        uploaded_by=uploaded_by or '',
    )
    db.session.add(row)
    db.session.commit()
    log_operation(uploaded_by or '', 'attachments', 'upload',
                  '上传模板附件 %s（%s）' % (original, module_key))
    return row


def _serialize(row):
    return {
        'id': row.id,
        'module_key': row.module_key,
        'original_name': row.original_name or '',
        'ext': row.ext or '',
        'size': row.size or 0,
        'mime': row.mime or '',
        'uploaded_by': row.uploaded_by or '',
        'created_at': row.created_at.strftime('%Y-%m-%d %H:%M') if row.created_at else '',
    }


def list_attachments(module_key, target_type='module', target_id=None):
    """模块附件列表（新→旧），返回 dict 列表（前端直接渲染）。"""
    query = ModuleAttachment.query.filter_by(module_key=module_key,
                                             target_type=target_type)
    if target_id is None:
        query = query.filter(ModuleAttachment.target_id.is_(None))
    else:
        query = query.filter(ModuleAttachment.target_id == target_id)
    return [_serialize(r) for r in
            query.order_by(ModuleAttachment.created_at.desc(), ModuleAttachment.id.desc()).all()]


def _get_for_module(module_key, attachment_id):
    """取附件记录并校验模块归属：不存在 ValueError，跨模块 PermissionError。"""
    if not _MODULE_KEY_RE.match(module_key or ''):
        raise ValueError('非法模块标识')
    row = ModuleAttachment.query.get(attachment_id)
    if row is None:
        raise ValueError('附件不存在')
    if row.module_key != module_key:
        raise PermissionError('无权访问其他模块的附件')
    return row


def resolve_path(module_key, attachment_id):
    """下载用：校验归属 + 定位落盘路径，返回 (row, abs_path)。

    stored_name 必须匹配本模块生成的落盘名正则；realpath 归一后必须仍位于
    本模块目录前缀内（normalize + 前缀断言，092 任务书工作项 4）。
    """
    row = _get_for_module(module_key, attachment_id)
    if not _STORED_NAME_RE.match(row.stored_name or ''):
        raise ValueError('附件存储名非法')
    month = (row.created_at.strftime('%Y%m') if row.created_at
             else datetime.now().strftime('%Y%m'))
    abs_path = os.path.realpath(os.path.join(storage_root(), module_key, month,
                                             row.stored_name))
    prefix = os.path.realpath(os.path.join(storage_root(), module_key)) + os.sep
    if not abs_path.startswith(prefix):
        raise ValueError('附件存储路径非法')
    if not os.path.isfile(abs_path):
        raise ValueError('附件文件已丢失')
    return row, abs_path


def delete(module_key, attachment_id, operator=''):
    """删除附件（落库记录为准；落盘文件尽力而为，safety 模块同款口径）。

    文件占用/缺失（如 Windows 下下载响应未关闭时的 WinError 32）不阻断记录删除，
    极端情况遗留孤儿文件由落盘目录按月分目录人工清理，不影响正确性。
    """
    row = _get_for_module(module_key, attachment_id)
    month = (row.created_at.strftime('%Y%m') if row.created_at
             else datetime.now().strftime('%Y%m'))
    abs_path = os.path.realpath(os.path.join(storage_root(), module_key, month,
                                             row.stored_name or ''))
    prefix = os.path.realpath(os.path.join(storage_root(), module_key)) + os.sep
    if abs_path.startswith(prefix) and os.path.isfile(abs_path):
        try:
            os.remove(abs_path)
        except OSError:
            pass
    name = row.original_name
    db.session.delete(row)
    db.session.commit()
    log_operation(operator or '', 'attachments', 'delete',
                  '删除模板附件 %s（%s）' % (name, module_key))


def attachment_api(fn):
    """三蓝图附件路由共用错误转译：ValueError → 400 JSON；PermissionError → 403 JSON。

    路由保持零 try/except 的薄形态（092 任务书工作项 2/3），转译逻辑单源在此。
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            resp = json_response(False, str(e))
            resp.status_code = 400
            return resp
        except PermissionError as e:
            resp = json_response(False, str(e))
            resp.status_code = 403
            return resp
    return wrapper


def register_attachment_routes(bp, module_key: str):
    """P1-2 工厂：将 092 模板附件 4 端点统一挂载到业务蓝图，消除 5 模块重复代码。"""
    from flask import request, session, send_file
    from utils.decorators import login_required

    @bp.route('/api/attachments')
    @login_required
    @attachment_api
    def api_attachment_list():
        return json_response(success=True, data={'data': list_attachments(module_key)})

    @bp.route('/api/attachments/upload', methods=['POST'])
    @login_required
    @attachment_api
    def api_attachment_upload():
        row = save(module_key, request.files.get('file'), session.get('username', ''))
        return json_response(True, '', {'id': row.id, 'original_name': row.original_name})

    @bp.route('/api/attachments/<int:att_id>/download')
    @login_required
    @attachment_api
    def api_attachment_download(att_id):
        row, path = resolve_path(module_key, att_id)
        return send_file(path, as_attachment=True,
                         download_name=row.original_name or row.stored_name,
                         mimetype=row.mime or 'application/octet-stream')

    @bp.route('/api/attachments/<int:att_id>/delete', methods=['POST'])
    @login_required
    @attachment_api
    def api_attachment_delete(att_id):
        delete(module_key, att_id, session.get('username', ''))
        return json_response(True, '删除成功')
