# -*- coding: utf-8 -*-
"""routes/safety.py — 安全工作模块·一期核心闭环（030 交付物 1）

新蓝图 safety_bp（/safety）：档案目录树（懒加载/折叠）+ 上传下载 + 搜索。
- ADR-016：BLUEPRINT_MODULES['safety']='safety'，GET→view / POST→edit / upload→add 自动映射。
- 树结构只存 DB、磁盘永远扁平：文件落盘 uploads/safety/{uuid}.{ext}（mediation 附件模式）。
- 🔴 红线：uploads/safety 不得加入 app.py UPLOAD_SUBDIR_PERM_MAP，文件访问一律走节点 id 端点。
- 写操作（上传/新建目录/删除）落 OperationLog（log_operation，对齐全站惯例）。
- 一期端点（030）：children / path / list / folder(new·delete) / file/upload / file(delete)
  / search（仅全树，scope 预留）/ download / preview。
- 二期端点（031）：folder·file 的 rename·move、batch_delete（delete 权限）/ batch_move（edit）、
  平铺 flat、打包 zip（tempfile delete=False + 后置清理）；download/preview ASCII 回退名兜底（P3-2）。
"""
import os
import re
import uuid
from datetime import datetime
from urllib.parse import quote

from flask import Blueprint, render_template, request, session, send_file
from sqlalchemy import case, func

from config import Config
from models import db
from models.safety import SafetyNode
from utils.decorators import login_required
from utils.helpers import json_response, log_operation, paginate_query

safety_bp = Blueprint('safety', __name__)

ROOT_NAME = '安全工作'
TREE_CHUNK = 50          # 单层限流：每次 children 只返 50，余量由 offset + 「加载更多」续取
SEARCH_LIMIT = 100       # 全树搜索返回上限
MAX_FILE_SIZE = 50 * 1024 * 1024  # 单文件上限（config MAX_CONTENT_LENGTH 同值兜底）

# 上传白名单（拍板三类收敛）：文档 | 表格 | 图片 → 扩展名 → MIME
ALLOWED_EXTS = {
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pdf': 'application/pdf',
    'txt': 'text/plain',
    'wps': 'application/kswps',
    'xls': 'application/vnd.ms-excel',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'csv': 'text/csv',
    'et': 'application/kset',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'gif': 'image/gif',
    'bmp': 'image/bmp',
    'webp': 'image/webp',
}
# preview 内联仅白名单图片与 PDF；其余一律 attachment（030 审阅 P10）
INLINE_PREVIEW_MIME = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
    'gif': 'image/gif', 'bmp': 'image/bmp', 'webp': 'image/webp',
    'pdf': 'application/pdf',
}

# 落盘名必须由本模块生成：32 位 hex（uuid4）+ 白名单扩展名（防路径穿越双保险）
_STORED_NAME_RE = re.compile(r'^[0-9a-f]{32}\.[a-z0-9]{1,10}$')


def _clean_original_name(raw_name):
    """清洗上传原始文件名（展示/下载用）。

    不用 secure_filename：其默认剥离非 ASCII 字符，会吞掉中文文件名。
    此处仅做三件事：去路径（防斜杠注入）、去控制字符、兜底空名。
    XSS 由前端 esc 单源转义（文件名字符串只进展示层，不进磁盘路径）。
    """
    raw = (raw_name or '').replace('\\', '/').strip()
    name = raw.rsplit('/', 1)[-1].strip() or '未命名文件'
    return ''.join(ch for ch in name if ord(ch) >= 32)


def _upload_dir():
    path = os.path.join(Config.UPLOAD_FOLDER, 'safety')
    os.makedirs(path, exist_ok=True)
    return path


def _m012_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'scripts', 'migrations', 'm012_safety_nodes.sql')


def _apply_m012():
    """幂等执行 m012（建表 IF NOT EXISTS + 20 类种子守卫插入）。"""
    with open(_m012_path(), encoding='utf-8') as f:
        sql = f.read()
    raw = db.engine.raw_connection()
    try:
        raw.executescript(sql)
        raw.commit()
    finally:
        raw.close()


def _ensure_safety_seed():
    """应用层幂等种子：表空时执行 m012；有数据零开销跳过。

    放在蓝图级（app 级 enforce_role_permissions 之后）：无权限请求被 app 级提前拦截，
    不会触发播种；测试隔离 DB（每用例新 app）每次首个请求自动补齐种子。
    """
    if SafetyNode.query.first() is None:
        _apply_m012()


def _parent_filter(parent_id):
    """0/None → 根层（parent_id IS NULL）；正整数按 id 匹配。"""
    if parent_id:
        return SafetyNode.parent_id == parent_id
    return SafetyNode.parent_id.is_(None)


def _folder_child_counts(folder_ids):
    if not folder_ids:
        return {}
    rows = (db.session.query(SafetyNode.parent_id, func.count(SafetyNode.id))
            .filter(SafetyNode.parent_id.in_(folder_ids))
            .group_by(SafetyNode.parent_id).all())
    return dict(rows)


def _bump_sort(key_parent_id):
    """同级 sort_order 最大值 +1（新目录/文件落位末尾）。"""
    q = db.session.query(func.max(SafetyNode.sort_order))
    q = q.filter(SafetyNode.parent_id == key_parent_id) if key_parent_id \
        else q.filter(SafetyNode.parent_id.is_(None))
    return (q.scalar() or 0) + 1


def _serialize(node, child_count=0):
    d = {
        'id': node.id,
        'name': node.name,
        'node_type': node.node_type,
        'sort_order': node.sort_order,
        'created_at': node.created_at.strftime('%Y-%m-%d %H:%M') if node.created_at else '',
        'updated_at': node.updated_at.strftime('%Y-%m-%d %H:%M') if node.updated_at else '',
    }
    if node.node_type == 'folder':
        d['child_count'] = child_count
        d['has_children'] = child_count > 0
    else:
        d['ext'] = node.ext or ''
        d['size'] = node.size or 0
        d['mime'] = node.mime or ''
    return d


def _chain_of(node_id):
    """节点 → 根的父链（祖先在前，节点在末）；深度护栏防脏数据死循环。"""
    chain = []
    seen = set()
    node = SafetyNode.query.get(node_id)
    while node is not None and node.id not in seen and len(chain) < 64:
        seen.add(node.id)
        chain.append({'id': node.id, 'name': node.name, 'node_type': node.node_type})
        node = SafetyNode.query.get(node.parent_id) if node.parent_id else None
    chain.reverse()
    return chain


# ---------------- 031 二期共享辅助 ----------------

def _ancestor_ids(node_id):
    """node_id 及其全部祖先 id（自下而上）；移动环检测用（含自身）。"""
    ids = []
    seen = set()
    node = SafetyNode.query.get(node_id)
    while node is not None and node.id not in seen and len(ids) < 64:
        seen.add(node.id)
        ids.append(node.id)
        node = SafetyNode.query.get(node.parent_id) if node.parent_id else None
        if node is None:
            break
    return ids


def _walk_under(folder_id):
    """深度优先收集 folder_id 子树下全部文件节点：[(node, 相对路径)]（相对该目录）。"""
    out = []

    def walk(pid, rel):
        for child in (SafetyNode.query.filter_by(parent_id=pid)
                      .order_by(SafetyNode.sort_order.asc(), SafetyNode.id.asc()).all()):
            relp = child.name if not rel else rel + '/' + child.name
            if child.node_type == 'folder':
                walk(child.id, relp)
            else:
                out.append((child, relp))

    walk(folder_id, '')
    return out


def _content_disposition(name, default_ext):
    """P3-2：中文名经 RFC 5987 filename* 下发；ASCII 回退名兜底为 download.<ext>（不再空名）。"""
    ascii_name = f'download.{default_ext}'
    return f"attachment; filename={ascii_name}; filename*=UTF-8''{quote(name, safe='')}"


def _parse_ids():
    """028 批量惯例：ids[]（或 ids）去重取整。"""
    raw = request.form.getlist('ids[]') or request.form.getlist('ids')
    out = []
    for v in raw:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv not in out:
            out.append(iv)
    return out


def _move_node(node, target):
    """移动节点到 target 目录（环检测 + 类型校验）；返回 (ok, err_msg)。"""
    if target is None or target.node_type != 'folder':
        return False, '目标目录不存在'
    if node.id == target.id:
        return False, '不能移动到自身'
    if node.node_type == 'folder' and node.id in _ancestor_ids(target.id):
        return False, '不能移动到自己的子目录内'
    node.parent_id = target.id
    node.updated_at = datetime.now()
    return True, ''


# ---------------- 页面 ----------------

@safety_bp.route('/')
@login_required
def index():
    return render_template('safety/archive.html')


# ---------------- 树懒加载 ----------------

@safety_bp.route('/api/children')
@login_required
def api_children():
    parent_id = request.args.get('parent_id', type=int) or 0
    offset = max(request.args.get('offset', 0, type=int), 0)
    if parent_id:
        parent = SafetyNode.query.get(parent_id)
        if parent is None:
            return json_response(False, '目录不存在')
        if parent.node_type != 'folder':
            return json_response(True, data={'items': [], 'total': 0, 'has_more': False, 'offset': 0})
    base = SafetyNode.query.filter(_parent_filter(parent_id))
    total = base.count()
    items = (base.order_by(SafetyNode.sort_order.asc(), SafetyNode.id.asc())
             .offset(offset).limit(TREE_CHUNK).all())
    counts = _folder_child_counts([n.id for n in items if n.node_type == 'folder'])
    data = {
        'items': [_serialize(n, counts.get(n.id, 0)) for n in items],
        'total': total,
        'has_more': offset + len(items) < total,
        'offset': offset,
    }
    return json_response(True, data=data)


# ---------------- 面包屑父链 ----------------

@safety_bp.route('/api/path')
@login_required
def api_path():
    node_id = request.args.get('id', type=int)
    if not node_id:
        return json_response(True, data={'items': []})
    node = SafetyNode.query.get(node_id)
    if not node:
        return json_response(False, '节点不存在')
    return json_response(True, data={'items': _chain_of(node.id)})


# ---------------- 当前层内容列表（分页） ----------------

@safety_bp.route('/api/list')
@login_required
def api_list():
    node_id = request.args.get('node_id', type=int) or 0
    page = max(request.args.get('page', 1, type=int), 1)
    sort = request.args.get('sort', '') or 'default'
    node = None
    if node_id:
        node = SafetyNode.query.get(node_id)
        if node is None:
            return json_response(False, '目录不存在')
        if node.node_type != 'folder':
            return json_response(False, '目标不是目录')
    base = SafetyNode.query.filter(_parent_filter(node_id))
    folder_first = case((SafetyNode.node_type == 'folder', 0), else_=1)
    if sort == 'name':
        ordered = base.order_by(folder_first, SafetyNode.name.asc(), SafetyNode.id.asc())
    elif sort == 'date':
        ordered = base.order_by(folder_first, SafetyNode.updated_at.desc(), SafetyNode.id.asc())
    elif sort == 'size':
        ordered = base.order_by(folder_first, SafetyNode.size.desc(), SafetyNode.id.asc())
    else:
        ordered = base.order_by(folder_first, SafetyNode.sort_order.asc(), SafetyNode.id.asc())
    items, total, pages = paginate_query(ordered, page=page, per_page=20)
    counts = _folder_child_counts([n.id for n in items if n.node_type == 'folder'])
    current = (node and {'id': node.id, 'name': node.name, 'node_type': node.node_type}) \
        or {'id': 0, 'name': ROOT_NAME, 'node_type': 'folder'}
    data = {
        'current': current,
        'items': [_serialize(n, counts.get(n.id, 0)) for n in items],
        'total': total,
        'page': page,
        'pages': pages,
    }
    return json_response(True, data=data)


# ---------------- 搜索（一期仅全树；scope 参数预留不实现） ----------------

@safety_bp.route('/api/search')
@login_required
def api_search():
    q = (request.args.get('q') or '').strip()
    if not q:
        return json_response(False, '请输入搜索关键词')
    rows = (SafetyNode.query.filter(SafetyNode.name.like(f'%{q}%'))
            .order_by(SafetyNode.name.asc(), SafetyNode.id.asc())
            .limit(SEARCH_LIMIT).all())
    items = []
    for n in rows:
        chain = _chain_of(n.id)
        path = ' › '.join([ROOT_NAME] + [c['name'] for c in chain])
        items.append({
            'id': n.id,
            'name': n.name,
            'node_type': n.node_type,
            'path': path,
        })
    return json_response(True, data={'items': items, 'total': len(items)})


# ---------------- 目录：新建 / 删除（031 扩展 rename·move） ----------------

@safety_bp.route('/api/folder', methods=['POST'])
@login_required
def api_folder():
    action = (request.form.get('action') or '').strip()
    if action == 'new':
        parent_id = request.form.get('parent_id', type=int) or 0
        name = (request.form.get('name') or '').strip()
        if not name:
            return json_response(False, '目录名不能为空')
        if len(name) > 120:
            return json_response(False, '目录名不能超过 120 字')
        if parent_id:
            parent = SafetyNode.query.get(parent_id)
            if parent is None or parent.node_type != 'folder':
                return json_response(False, '上级目录不存在')
        key = parent_id or None
        dup = (SafetyNode.query
               .filter(_parent_filter(key), SafetyNode.node_type == 'folder',
                       SafetyNode.name == name).first())
        if dup:
            return json_response(False, '同级已存在同名目录')
        node = SafetyNode(
            parent_id=key, name=name, node_type='folder',
            sort_order=_bump_sort(key),
            created_by=session.get('username', ''))
        db.session.add(node)
        db.session.commit()
        log_operation(session.get('username', ''), ROOT_NAME, 'folder_new', f'新建目录: {name}')
        return json_response(True, '新建成功', {'id': node.id})
    if action == 'delete':
        node_id = request.form.get('id', type=int)
        node = SafetyNode.query.get(node_id) if node_id else None
        if not node:
            return json_response(False, '目录不存在')
        if node.node_type != 'folder':
            return json_response(False, '目标不是目录')
        child_count = SafetyNode.query.filter_by(parent_id=node.id).count()
        if child_count:
            return json_response(False, '目录非空，无法删除（请先清空子项）')
        name = node.name
        db.session.delete(node)
        db.session.commit()
        log_operation(session.get('username', ''), ROOT_NAME, 'folder_delete', f'删除目录: {name}')
        return json_response(True, '已删除')
    if action == 'rename':
        node_id = request.form.get('id', type=int)
        node = SafetyNode.query.get(node_id) if node_id else None
        if not node or node.node_type != 'folder':
            return json_response(False, '目录不存在')
        name = (request.form.get('name') or '').strip()
        if not name or len(name) > 120:
            return json_response(False, '目录名不能为空且不超过 120 字')
        dup = (SafetyNode.query
               .filter(_parent_filter(node.parent_id), SafetyNode.node_type == 'folder',
                       SafetyNode.name == name, SafetyNode.id != node.id).first())
        if dup:
            return json_response(False, '同级已存在同名目录')
        old_name, node.name = node.name, name
        node.updated_at = datetime.now()
        db.session.commit()
        log_operation(session.get('username', ''), ROOT_NAME, 'folder_rename',
                      f'目录重命名: {old_name} → {name}')
        return json_response(True, '已重命名')
    if action == 'move':
        node_id = request.form.get('id', type=int)
        target_id = request.form.get('target_id', type=int)
        node = SafetyNode.query.get(node_id) if node_id else None
        target = SafetyNode.query.get(target_id) if target_id else None
        if not node or node.node_type != 'folder':
            return json_response(False, '目录不存在')
        ok, err = _move_node(node, target)
        if not ok:
            return json_response(False, err)
        db.session.commit()
        log_operation(session.get('username', ''), ROOT_NAME, 'folder_move',
                      f'移动目录: {node.name} → {target.name}')
        return json_response(True, '已移动')
    return json_response(False, '未知操作')

    # ---------------- 文件：上传 ----------------

@safety_bp.route('/api/file/upload', methods=['POST'])
@login_required
def api_file_upload():
    node_id = request.form.get('node_id', type=int) or 0
    key = node_id or None
    if node_id:
        parent = SafetyNode.query.get(node_id)
        if parent is None or parent.node_type != 'folder':
            return json_response(False, '目标目录不存在')
    uploads = request.files.getlist('files')
    if not uploads or all(not f or not f.filename for f in uploads):
        return json_response(False, '未选择文件')
    dirpath = _upload_dir()
    results, ok_count = [], 0
    for f in uploads:
        if not f or not f.filename:
            continue
        clean = _clean_original_name(f.filename)
        ext = clean.rsplit('.', 1)[1].lower() if '.' in clean else ''
        if ext not in ALLOWED_EXTS:
            results.append({'ok': False, 'name': clean,
                            'msg': f'不支持的文件类型 .{ext or "未知"}'})
            continue
        stored = f'{uuid.uuid4().hex}.{ext}'
        abs_path = os.path.join(dirpath, stored)
        f.save(abs_path)
        size = os.path.getsize(abs_path)
        if size > MAX_FILE_SIZE:
            try:
                os.remove(abs_path)
            except OSError:
                pass
            results.append({'ok': False, 'name': clean, 'msg': '文件超过 50MB 上限'})
            continue
        duplicate = (SafetyNode.query
                     .filter(_parent_filter(key), SafetyNode.name == clean)
                     .first() is not None)
        node = SafetyNode(
            parent_id=key, name=clean, node_type='file',
            sort_order=_bump_sort(key),
            stored_name=stored, original_name=clean, ext=ext,
            size=size, mime=ALLOWED_EXTS[ext],
            created_by=session.get('username', ''))
        db.session.add(node)
        db.session.flush()  # 立刻取到自增 id 供结果回传
        ok_count += 1
        results.append({'ok': True, 'id': node.id, 'name': clean,
                        'duplicate': duplicate,
                        'msg': '已上传（目录内已有同名文件）' if duplicate else '上传成功'})
    db.session.commit()
    if ok_count:
        log_operation(session.get('username', ''), ROOT_NAME, 'upload',
                      f'上传 {ok_count} 个文件')
    return json_response(True, '', {'results': results})


# ---------------- 文件：删除（031 扩展 rename·move） ----------------

@safety_bp.route('/api/file', methods=['POST'])
@login_required
def api_file():
    action = (request.form.get('action') or '').strip()
    node_id = request.form.get('id', type=int)
    node = SafetyNode.query.get(node_id) if node_id else None
    if action == 'delete':
        if not node:
            return json_response(False, '文件不存在')
        if node.node_type != 'file':
            return json_response(False, '目标不是文件')
        name = node.name
        if node.stored_name and _STORED_NAME_RE.match(node.stored_name):
            try:
                os.remove(os.path.join(_upload_dir(), node.stored_name))
            except OSError:
                pass
        db.session.delete(node)
        db.session.commit()
        log_operation(session.get('username', ''), ROOT_NAME, 'file_delete', f'删除文件: {name}')
        return json_response(True, '已删除')
    if action == 'rename':
        if not node or node.node_type != 'file':
            return json_response(False, '文件不存在')
        name = (request.form.get('name') or '').strip()
        if not name or len(name) > 120:
            return json_response(False, '文件名不能为空且不超过 120 字')
        old_name = node.name
        node.name = name
        node.original_name = name
        node.updated_at = datetime.now()
        db.session.commit()
        log_operation(session.get('username', ''), ROOT_NAME, 'file_rename',
                      f'文件重命名: {old_name} → {name}')
        return json_response(True, '已重命名')
    if action == 'move':
        if not node or node.node_type != 'file':
            return json_response(False, '文件不存在')
        target_id = request.form.get('target_id', type=int)
        target = SafetyNode.query.get(target_id) if target_id else None
        ok, err = _move_node(node, target)
        if not ok:
            return json_response(False, err)
        db.session.commit()
        log_operation(session.get('username', ''), ROOT_NAME, 'file_move',
                      f'移动文件: {node.name} → {target.name}')
        return json_response(True, '已移动')
    return json_response(False, '未知操作')


# ---------------- 031 批量端点（028 惯例：ids[] + 白名单语义 + batch_done 留痕） ----------------

@safety_bp.route('/api/file/batch_delete', methods=['POST'])
@login_required
def api_file_batch_delete():
    """批量删除（路径含 delete 字样 → 自动归类 safety.delete）。

    文件：DB 行 + 磁盘文件一并移除；空目录允许删除；非空目录拒绝并计入失败回执。
    """
    ids = _parse_ids()
    if not ids:
        return json_response(False, '请先勾选要删除的项目')
    nodes = SafetyNode.query.filter(SafetyNode.id.in_(ids)).all()
    ok, failed = [], []
    for n in nodes:
        if n.node_type == 'folder':
            if SafetyNode.query.filter_by(parent_id=n.id).count():
                failed.append({'id': n.id, 'name': n.name, 'msg': '目录非空，无法删除'})
                continue
        else:
            if n.stored_name and _STORED_NAME_RE.match(n.stored_name):
                try:
                    os.remove(os.path.join(_upload_dir(), n.stored_name))
                except OSError:
                    pass
        db.session.delete(n)
        ok.append({'id': n.id, 'name': n.name})
    db.session.commit()
    log_operation(session.get('username', ''), ROOT_NAME, 'batch_done',
                  f'批量删除 成功 {len(ok)} 失败 {len(failed)}')
    return json_response(True, f'成功 {len(ok)} 项，失败 {len(failed)} 项',
                         {'success': len(ok), 'failed': failed})


@safety_bp.route('/api/file/batch_move', methods=['POST'])
@login_required
def api_file_batch_move():
    """批量移动（无 delete/edit 词 → POST 默认归类 safety.edit）。环检测逐项拒绝。"""
    ids = _parse_ids()
    if not ids:
        return json_response(False, '请先勾选要移动的项目')
    target_id = request.form.get('target_id', type=int)
    target = SafetyNode.query.get(target_id) if target_id else None
    if target is None or target.node_type != 'folder':
        return json_response(False, '目标目录不存在')
    ok, failed = [], []
    for n in SafetyNode.query.filter(SafetyNode.id.in_(ids)).all():
        moved, err = _move_node(n, target)
        if moved:
            ok.append({'id': n.id, 'name': n.name})
        else:
            failed.append({'id': n.id, 'name': n.name, 'msg': err})
    db.session.commit()
    log_operation(session.get('username', ''), ROOT_NAME, 'batch_done',
                  f'批量移动 成功 {len(ok)} 失败 {len(failed)}')
    return json_response(True, f'成功 {len(ok)} 项，失败 {len(failed)} 项',
                         {'success': len(ok), 'failed': failed})


# ---------------- 2026-11-02 档案统计（页头胶囊：文件数 + 总体积） ----------------

@safety_bp.route('/api/stats')
@login_required
def api_stats():
    files = SafetyNode.query.filter_by(node_type='file')
    count = files.count()
    total_bytes = db.session.query(db.func.coalesce(db.func.sum(SafetyNode.size), 0)).filter_by(node_type='file').scalar()
    return json_response(True, data={'files': count, 'bytes': int(total_bytes or 0)})


# ---------------- 031 平铺视角：子树拉平（仅文件，带完整路径与定位锚点） ----------------

@safety_bp.route('/api/flat')
@login_required
def api_flat():
    node_id = request.args.get('node_id', type=int) or 0
    page = max(request.args.get('page', 1, type=int), 1)
    size = min(max(request.args.get('size', 20, type=int), 1), 500)  # 网格视图一次取全（≤500）
    q = request.args.get('q', '').strip()
    if node_id:
        node = SafetyNode.query.get(node_id)
        if node is None:
            return json_response(False, '目录不存在')
        if node.node_type != 'folder':
            return json_response(False, '目标不是目录')
    entries = _walk_under(node_id if node_id else None)
    if q:
        entries = [(n, rel) for n, rel in entries if q.lower() in n.name.lower()]
    entries.sort(key=lambda x: x[1])
    total = len(entries)
    pages = (total + size - 1) // size
    chunk = entries[(page - 1) * size:page * size]
    items = []
    for n, rel in chunk:
        items.append({
            'id': n.id, 'name': n.name, 'node_type': n.node_type,
            'path': rel, 'ext': n.ext or '', 'size': n.size or 0,
            'mime': n.mime or '',
            'parent_id': n.parent_id,
            'updated_at': n.updated_at.strftime('%Y-%m-%d %H:%M') if n.updated_at else '',
        })
    return json_response(True, data={
        'current': (node and {'id': node.id, 'name': node.name}) or
                   {'id': 0, 'name': ROOT_NAME},
        'items': items, 'total': total, 'page': page, 'pages': pages})


# ---------------- 031 打包下载：目录/勾选集 zip（tempfile delete=False + 后置清理） ----------------

def _build_zip(node_id=None, node_ids=None):
    """构造 zip 临时文件；返回 (tmp_path, zip_name) 或 (None, None)。"""
    import tempfile
    import zipfile

    if node_id:
        node = SafetyNode.query.get(node_id)
        if not node or node.node_type != 'folder':
            return None, None
        collect = _walk_under(node.id)
        zip_name = f'{node.name}.zip'
        prefix = node.name
    else:
        picked = [SafetyNode.query.get(i) for i in (node_ids or [])]
        picked = [n for n in picked if n and n.node_type == 'file']
        if not picked:
            return None, None
        collect = [(n, n.original_name or n.name) for n in picked]
        zip_name = 'safety_files.zip'
        prefix = ''
    fd, tmp_path = tempfile.mkstemp(suffix='.zip')
    os.close(fd)
    try:
        written = 0
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for n, rel in collect:
                if not n.stored_name or not _STORED_NAME_RE.match(n.stored_name):
                    continue
                path = os.path.join(_upload_dir(), n.stored_name)
                if not os.path.exists(path):
                    continue
                arc = (prefix + '/' + rel) if prefix else rel
                zf.write(path, arc)
                written += 1
        if not written:
            os.remove(tmp_path)
            return None, None
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return tmp_path, zip_name


@safety_bp.route('/api/zip')
@login_required
def api_zip():
    node_id = request.args.get('node_id', type=int)
    ids = [int(v) for v in request.args.getlist('ids') if str(v).isdigit()]
    if node_id:
        tmp_path, zip_name = _build_zip(node_id=node_id)
    elif ids:
        tmp_path, zip_name = _build_zip(node_ids=ids)
    else:
        return json_response(False, '请指定目录或勾选文件')
    if not tmp_path:
        return json_response(False, '没有可打包的文件')

    # P2 修复：清理不挂 Flask Response（send_file 为 direct_passthrough，
    # Response 对象不会被服务器关闭，call_on_close/after_this_request 在 Windows
    # 下都拿不到「句柄已释放」的时机），改为包装临时文件句柄：close() 在
    # 服务器发送完毕 / 测试端缓冲完毕时必然被调用 —— 先关句柄再删文件，
    # Windows/Linux 语义一致且幂等可重入。
    class _RemoveOnClose:
        def __init__(self, fh, path):
            object.__setattr__(self, '_fh', fh)
            object.__setattr__(self, '_path', path)

        def __getattr__(self, item):
            return getattr(self._fh, item)

        def close(self):
            try:
                if not self._fh.closed:
                    self._fh.close()
            finally:
                try:
                    os.remove(self._path)
                except OSError:
                    pass

    fh = open(tmp_path, 'rb')
    resp = send_file(_RemoveOnClose(fh, tmp_path), mimetype='application/zip',
                     as_attachment=True, download_name=zip_name or 'download.zip')
    resp.headers['Content-Disposition'] = _content_disposition(zip_name or 'download.zip', 'zip')
    return resp


# ---------------- 下载 / 预览（对外只暴露节点 id，防路径穿越） ----------------

def _load_file_node(node_id):
    node = SafetyNode.query.get(node_id)
    if not node or node.node_type != 'file':
        return None, '文件不存在'
    if not node.stored_name or not _STORED_NAME_RE.match(node.stored_name):
        return None, '文件存储信息异常'
    path = os.path.join(_upload_dir(), node.stored_name)
    if not os.path.exists(path):
        return None, '文件已丢失（磁盘文件不存在）'
    return (node, path), None


@safety_bp.route('/file/<int:node_id>/download')
@login_required
def file_download(node_id):
    result, err = _load_file_node(node_id)
    if err:
        return json_response(False, err), 404
    node, path = result
    resp = send_file(path, as_attachment=True,
                     download_name=node.original_name or node.name,
                     mimetype=node.mime or 'application/octet-stream')
    # P3-2：中文名 ASCII 回退名兜底 download.<ext>（老客户端不再拿到空名）
    resp.headers['Content-Disposition'] = _content_disposition(
        node.original_name or node.name, node.ext or 'bin')
    return resp


@safety_bp.route('/file/<int:node_id>/preview')
@login_required
def file_preview(node_id):
    result, err = _load_file_node(node_id)
    if err:
        return json_response(False, err), 404
    node, path = result
    if node.ext and node.ext in INLINE_PREVIEW_MIME:
        return send_file(path, mimetype=INLINE_PREVIEW_MIME[node.ext],
                         as_attachment=False)
    resp = send_file(path, as_attachment=True,
                     download_name=node.original_name or node.name,
                     mimetype=node.mime or 'application/octet-stream')
    resp.headers['Content-Disposition'] = _content_disposition(
        node.original_name or node.name, node.ext or 'bin')
    return resp