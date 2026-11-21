"""报表导出蓝图（T07 §4.4 独立下载通道 + T17-3/4 统计表与花名册）

工程师红线：导出文件禁经 serve_upload，防 G6 破口。
- POST /report/export：服务端生成 → 落 temp/exports/<uuid>.xlsx → 返回 download_url
- GET /report/download/<token>：校验+消费令牌 → send_file 直出
- GET /report/stats-table：一老一小统计表预览（T17-3）
- GET /report/roster：花名册预览（脱敏）（T17-4）
- POST /report/export-excel：统计表/花名册 Excel 导出（T17-3/4）

注：本蓝图提供通用导出通道框架；各业务模块的导出逻辑仍由各自路由调用
utils/helpers.py::export_excel 完成（BytesIO 直出，已含脱敏）。
需要走 token 通道的大文件/异步导出走本蓝图的 POST /report/export。
"""
from flask import (Blueprint, request, jsonify, session, send_file,
                   render_template, current_app, g)
from utils.export_token import export_token_store
from utils.helpers import log_operation
from utils.permissions import user_has_permission
from utils.masking import child_rows_guard
from models import User

report_bp = Blueprint('report', __name__, template_folder='../templates')


@report_bp.route('/export', methods=['POST'])
def export_file():
    """通用导出通道：生成文件 → 落 temp/exports → 返回 download_url。

    请求参数（JSON 或 form）：
    - file_path: 已生成的导出文件绝对路径（由调用方生成并落盘）
    - module: 业务模块名（用于权限校验 + 日志）
    - filename: 下载显示名

    返回：{success, data: {download_url: "/report/download/<token>"}}
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401

    user = User.query.get(session.get('user_id'))
    if not user or not user.is_active:
        return jsonify({'success': False, 'msg': '未登录'}), 401

    data = request.get_json(silent=True) or request.form
    file_path = (data.get('file_path') or '').strip()
    module = (data.get('module') or '').strip()
    filename = (data.get('filename') or 'export.xlsx').strip()

    if not file_path or not module:
        return jsonify({'success': False, 'msg': '缺少 file_path 或 module'}), 400

    # 权限校验：调用方须有 <module>.export 权限
    perm_key = f'{module}.export'
    if user.role_id != 1 and not user_has_permission(user, perm_key):
        return jsonify({'success': False, 'msg': '权限不足'}), 403

    # 纵深（M4.4）：file_path 必须位于 temp/exports（禁任意服务端文件读取面）
    import os
    from config import Config
    env_tmp = os.environ.get('SHUCUN_TEMP_DIR')
    exp_dir = os.path.join(env_tmp, 'exports') if env_tmp else getattr(Config, 'EXPORTS_DIR', None)
    if not exp_dir:
        exp_dir = os.path.join(Config.TEMP_DIR, 'exports')
    exp_dir = os.path.realpath(exp_dir)
    if not os.path.realpath(file_path).startswith(exp_dir + os.sep):
        return jsonify({'success': False, 'msg': '导出文件路径不在导出通道内'}), 400
    if not os.path.isfile(file_path):
        return jsonify({'success': False, 'msg': '导出文件不存在或已过期'}), 404

    token = export_token_store.issue(user.id, file_path, module)
    log_operation(user.username, module, 'export',
                  f'导出文件 {filename}（token 通道）')
    return jsonify({
        'success': True,
        'data': {'download_url': f'/report/download/{token}'}
    })


@report_bp.route('/download/<token>', methods=['GET'])
def export_download(token):
    """一次性下载：校验令牌 → send_file 直出 → 消费令牌。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401

    user = User.query.get(session.get('user_id'))
    if not user or not user.is_active:
        return jsonify({'success': False, 'msg': '未登录'}), 401

    ok, file_path, module = export_token_store.verify(token, user.id)
    if not ok:
        return jsonify({'success': False, 'msg': '下载链接无效或已过期'}), 403

    # 纵深权限（详细设计 §4.4 L927）：下载须有模块 export 权限（T17-4 补齐）
    if user.role_id != 1 and not user_has_permission(user, f'{module}.export'):
        return jsonify({'success': False, 'msg': '权限不足'}), 403

    import os
    if not os.path.isfile(file_path):
        export_token_store.consume(token)
        return jsonify({'success': False, 'msg': '文件不存在或已清理'}), 404

    export_token_store.consume(token)
    log_operation(user.username, module, 'download',
                  f'下载导出文件（已消费）')
    return send_file(file_path, as_attachment=True,
                     download_name=os.path.basename(file_path))


def _require_report_view():
    """report.view 手动校验（纵深；BLUEPRINT_MODULES 全局映射已上，双保险）。"""
    user = User.query.get(session.get('user_id'))
    if user.role_id != 1 and not user_has_permission(user, 'report.view'):
        return None
    return user


@report_bp.route('/stats-table', methods=['GET'])
@child_rows_guard
def stats_table():
    """一老一小统计表预览（T17-3）：四分类 × 网格 × 年龄段，含合计。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    user = _require_report_view()
    if not user:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
                request.accept_mimetypes.best == 'application/json':
            return jsonify({'success': False, 'msg': '权限不足'}), 403
        return render_template('errors/403.html'), 403

    from utils.agg_cache import get_stats_table
    grid = (request.args.get('grid') or '').strip() or None
    # 081：统计表统一走 MetricsEngine（get_stats_table 已薄封装），口径/结构不变
    data = get_stats_table(grid)
    return render_template('report/stats_table.html', data=data, grid=grid)


# ============ T17-4 花名册装配（口径与 agg_cache/dashboard 同源，owner 在 utils） ============

_ROSTER_TAG_CATS = {
    '困难': ('hard', '困难老人', '老人', ''),
    '留守': ('left', '留守儿童', '儿童', '留守'),
    '困境': ('distressed', '困境儿童', '儿童', '困境'),
}
_ROSTER_VULN_CATS = {'低保': 'dibao', '残疾': 'disabled', '特困': 'tekun', '其他困难': 'special'}
_ROSTER_EXTRA_CATS = {
    '儿童': ('child', '儿童'),
    '困难家庭': ('hard_family', '困难家庭'),
    '走访对象': ('visit', '走访对象'),
    '补贴对象': ('subsidy', '补贴对象'),
}
# 居民维度类别（有出生日期/住址列）→ _HEADERS_ELDERLY 风格（T21-1：扩展类别同口径）
_ROSTER_ELDERLY_STYLE = ('高龄', *_ROSTER_TAG_CATS, *_ROSTER_EXTRA_CATS)
_HEADERS_ELDERLY = ['姓名', '身份证', '性别', '出生日期', '住址', '网格', '分类', '备注']
_HEADERS_VULN = ['姓名', '身份证', '性别', '年龄', '网格', '分类', '标签', '备注']


def _can_view_sensitive(user) -> bool:
    """明文导出判定（对齐 helpers.export_excel：admin 或 view_sensitive 特殊位）。"""
    from utils.permissions import user_has_special
    return bool(user and (user.role_id == 1 or user_has_special(user, 'view_sensitive')))


def _decrypt_id_card(resident) -> str:
    """读居民身份证（仅解密 _enc；P2-2 整改：移除明文兜底，杜绝明文绕过）。"""
    from utils.crypto import key_holder
    enc = getattr(resident, 'id_card_enc', None)
    if enc:
        try:
            return key_holder.decrypt(enc)
        except Exception:
            return ''
    if getattr(resident, 'id_card', None):
        import logging
        logging.getLogger('shucun').warning(
            '检测到未加密敏感字段 id_card（id=%s），请执行数据迁移清除明文残留',
            getattr(resident, 'id', None))
    return ''


def _elderly_rows(grid=None):
    """高龄花名册行（birth_date 动态 + override，口径同 agg_cache.roster_id_sets）。"""
    from utils.agg_cache import roster_id_sets
    from models import Resident
    ids = roster_id_sets()['elderly']
    if not ids:
        return []
    q = Resident.query.filter(Resident.id.in_(ids))
    if grid:
        q = q.filter(Resident.village_group == grid)
    return [{'name': r.name or '', 'id_card': _decrypt_id_card(r), 'gender': r.gender or '',
             'birth_date': r.birth_date, 'address': r.address or '', 'grid': r.village_group or '',
             'remark': r.remark or '', 'category': '高龄', 'target_type': '老人', 'child_type': ''}
            for r in q.all()]


def _tag_rows(cat_key, grid=None):
    """困难/留守/困境花名册行（resident_tag 标签口径）。"""
    from utils.agg_cache import roster_id_sets
    from models import Resident
    key, label, tt, ct = _ROSTER_TAG_CATS[cat_key]
    ids = roster_id_sets()[key]
    if not ids:
        return []
    q = Resident.query.filter(Resident.id.in_(ids))
    if grid:
        q = q.filter(Resident.village_group == grid)
    return [{'name': r.name or '', 'id_card': _decrypt_id_card(r), 'gender': r.gender or '',
             'birth_date': r.birth_date, 'address': r.address or '', 'grid': r.village_group or '',
             'remark': r.remark or '', 'category': label, 'target_type': tt, 'child_type': ct}
            for r in q.all()]


def _vuln_rows(cat_key, grid=None):
    """弱势群体花名册行（低保/残疾/特困/其他困难，四表 status='有效'；标签列=detail）。"""
    from routes.resident import _VULN_MODEL_MAP, _vuln_row_dict
    name, model = _VULN_MODEL_MAP[cat_key]
    rows = []
    for row in model.query.filter(model.status == '有效').all():
        d = _vuln_row_dict(row, cat_key)
        d['category'] = name
        d['target_type'] = getattr(row, 'target_type', '') or ''
        d['child_type'] = getattr(row, 'child_type', '') or ''
        d['birth_date'] = None            # 弱势群体以年龄列展示（对齐 _vuln_row_dict）
        d['remark'] = d['detail'] or ''
        rows.append(d)
    if grid:
        rows = [r for r in rows if r['grid'] == grid]
    return rows


def _resident_id_rows(key, label, grid=None):
    """扩展类别花名册行（T21-1：儿童/困难家庭/走访对象/补贴对象，roster_id_sets 口径）。

    行内 target_type/child_type 由 CareTarget 批量 enrich（Z3 儿童行级过滤依据，
    与 _tag_rows/_elderly_rows 正交；困难家庭/走访/补贴无儿童标记自然不触发行级过滤）。
    """
    from utils.agg_cache import roster_id_sets
    from models import Resident
    ids = roster_id_sets()[key]
    if not ids:
        return []
    q = Resident.query.filter(Resident.id.in_(ids))
    if grid:
        q = q.filter(Resident.village_group == grid)
    residents = q.all()
    # 儿童类别 enrich 政策性儿童标记（CareTarget 有效，Z3 行级过滤依据）
    child_map = {}
    if key == 'child':
        from models import CareTarget
        child_map = {cid: ct for cid, ct in CareTarget.query.filter(
            CareTarget.status == '有效', CareTarget.resident_id.in_(ids)
        ).with_entities(CareTarget.resident_id, CareTarget.child_type).all()}
    rows = []
    for r in residents:
        is_child = key == 'child'
        rows.append({'name': r.name or '', 'id_card': _decrypt_id_card(r),
                     'gender': r.gender or '', 'birth_date': r.birth_date,
                     'address': r.address or '', 'grid': r.village_group or '',
                     'remark': r.remark or '', 'category': label,
                     'target_type': '儿童' if is_child else '',
                     'child_type': child_map.get(r.id, '') if is_child else ''})
    return rows


def _roster_rows(category, grid=None):
    """花名册行装配（category：高龄/困难/留守/困境/低保/残疾/特困/其他困难/儿童/困难家庭/走访对象/补贴对象）。"""
    if category in _ROSTER_EXTRA_CATS:
        key, label = _ROSTER_EXTRA_CATS[category]
        return _resident_id_rows(key, label, grid)
    if category == '高龄':
        return _elderly_rows(grid)
    if category in _ROSTER_TAG_CATS:
        return _tag_rows(category, grid)
    if category in _ROSTER_VULN_CATS:
        return _vuln_rows(_ROSTER_VULN_CATS[category], grid)
    return []


def _roster_headers(category):
    return _HEADERS_ELDERLY if category in _ROSTER_ELDERLY_STYLE else _HEADERS_VULN


def _roster_sheet_rows(category, rows):
    """行 dict → 按 headers 顺序的 list（导出/预览共用）。"""
    if category in _ROSTER_ELDERLY_STYLE:
        return [[r.get('name', ''), r.get('id_card', ''), r.get('gender', ''),
                 (r.get('birth_date') or '').isoformat() if r.get('birth_date') else '',
                 r.get('address', ''), r.get('grid', ''), r.get('category', ''), r.get('remark', '')]
                for r in rows]
    # 弱势群体：标签列=detail（低保类型/残疾类型等级等），备注列=行备注（M4.4 修正键名错位）
    return [[r.get('name', ''), r.get('id_card', ''), r.get('gender', ''), r.get('age', ''),
             r.get('grid', ''), r.get('category', ''), r.get('detail', ''), r.get('remark', '')]
            for r in rows]


def _summary_row(category, n):
    """花名册统计汇总行（R3 验收 4 / R6 验收 #2：明细+人数统计行）。"""
    if category in _ROSTER_ELDERLY_STYLE:
        return ['人数统计', '', '', '', '', '', f'{category} 共 {n} 人', '']
    return ['人数统计', '', '', '', '', f'{category} 共 {n} 人', '', '']


def _write_xlsx_file(path, headers, rows, can_view_sensitive):
    """落盘 temp/exports/<name>.xlsx（mask_export_rows 脱敏 + openpyxl）。"""
    from utils.masking import mask_export_rows
    rows = mask_export_rows(headers, rows, can_view_sensitive)
    import openpyxl
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    for col_idx, header in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = max(15, len(str(header)) * 2 + 2)
    wb.save(path)
    return rows


def _issue_export(user, path, module, summary, detail):
    """发 token + 记 log（含明文标记，US-5/R11-E4）。"""
    token = export_token_store.issue(user.id, path, module)
    log_operation(user.username, module, 'export', summary, detail=detail)
    return jsonify({'success': True,
                    'data': {'download_url': f'/report/download/{token}'}})


@report_bp.route('/roster', methods=['GET'])
@child_rows_guard
def roster_view():
    """花名册预览（脱敏；T17-4）。筛选：category + grid；分页。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    user = _require_report_view()
    if not user:
        return render_template('errors/403.html'), 403

    from utils.masking import filter_child_rows, mask_id_card
    category = (request.args.get('category') or '高龄').strip()
    grid = (request.args.get('grid') or '').strip() or None
    try:
        page = max(1, int(request.args.get('page') or 1))
    except ValueError:
        page = 1
    per = 20

    raw = _roster_rows(category, grid)
    # Z3 落点 3：行级可见性（view_sensitive 明文权不豁免行级，G-2）
    visible = filter_child_rows(raw, g.can_see_children)
    total = len(visible)
    total_pages = max(1, (total + per - 1) // per)
    start = (page - 1) * per
    page_rows = visible[start:start + per]
    # 服务端预脱敏（无明文权时身份证脱敏；行级与字段级正交）
    if not _can_view_sensitive(user):
        for r in page_rows:
            if r.get('id_card'):
                r['id_card'] = mask_id_card(r['id_card'])

    grids = sorted({r['grid'] for r in raw if r['grid']})
    cats = ['高龄', *_ROSTER_TAG_CATS, *_ROSTER_VULN_CATS, *_ROSTER_EXTRA_CATS]
    return render_template('report/roster.html', rows=page_rows, category=category,
                           grid=grid, page=page, total_pages=total_pages, total=total,
                           grids=grids, cats=cats, headers=_roster_headers(category))


@report_bp.route('/export-excel', methods=['POST'])
@child_rows_guard
def export_excel():
    """统计表/花名册 Excel 导出（T17-3/4；temp/exports + token + 留痕含明文标记）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    user = User.query.get(session.get('user_id'))
    if not user or not user.is_active:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    if user.role_id != 1 and not user_has_permission(user, 'report.export'):
        return jsonify({'success': False, 'msg': '权限不足'}), 403

    from utils.masking import filter_child_rows
    import os, uuid
    data = request.get_json(silent=True) or request.form
    type_ = (data.get('type') or 'roster').strip()
    category = (data.get('category') or '高龄').strip()
    grid = (data.get('grid') or '').strip() or None

    # 导出目录：环境变量优先（测试隔离友好，与 cleanup_temp_exports 一致），回退 Config
    from config import Config
    env_tmp = os.environ.get('SHUCUN_TEMP_DIR')
    exp_dir = os.path.join(env_tmp, 'exports') if env_tmp else getattr(Config, 'EXPORTS_DIR', None)
    if not exp_dir:
        exp_dir = os.path.join(Config.TEMP_DIR, 'exports')
    os.makedirs(exp_dir, exist_ok=True)

    can_plain = _can_view_sensitive(user)
    filename = f"{'含明文_' if can_plain else ''}{uuid.uuid4().hex}.xlsx"
    path = os.path.join(exp_dir, filename)

    if type_ == 'stats':
        from utils.agg_cache import get_stats_table
        # 081：导出统计表复用 MetricsEngine 结果（get_stats_table 已薄封装），列名/脱敏不变
        st = get_stats_table(grid)
        headers = ['对象分类'] + [f'{g}·{b}' for g in st['grids'] for b in st['age_bands']] + ['合计']
        rows = []
        for r in st['rows']:
            cells = [r['name']] + [v for g in st['grids'] for v in r['cells'][g]] + [r['total']]
            rows.append(cells)
        rows.append(['合计'] + [v for g in st['grids'] for v in st['totals'][g]] + [st['grand_total']])
        module = 'report'
        summary = f'导出统计表 {len(rows) - 1} 分类'
        sheet_rows = rows
    else:
        raw = _roster_rows(category, grid)
        visible = filter_child_rows(raw, g.can_see_children)   # Z3：行级过滤（不豁免明文权）
        headers = _roster_headers(category)
        sheet_rows = _roster_sheet_rows(category, visible)
        # 统计汇总行/人数统计行（R3 验收 4 / R6 验收 #2：明细+统计行）
        sheet_rows.append(_summary_row(category, len(visible)))
        module = 'report'
        summary = f'花名册导出 {category} {len(visible)} 行'

    # 脱敏 + 落盘（花名册逐行脱敏；统计表纯计数无明文）
    _write_xlsx_file(path, headers, sheet_rows, can_plain)
    plain_mark = '是' if can_plain else '否'
    return _issue_export(user, path, module, summary, detail=f'含明文={plain_mark}')


# ============ T20-4：自定义查询构建器（R9-E2） ============

@report_bp.route('/custom-query', methods=['GET'])
@child_rows_guard
def custom_query_view():
    """自定义查询页（多条件 AND/OR 组合，预览脱敏）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    user = _require_report_view()
    if not user:
        return render_template('errors/403.html'), 403
    return render_template('report/custom_query.html')


@report_bp.route('/api/custom-query', methods=['POST'])
@child_rows_guard
def custom_query_api():
    """预览：条件树 → 结果行（脱敏，明文仅 view_sensitive）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    user = _require_report_view()
    if not user:
        return jsonify({'success': False, 'msg': '权限不足'}), 403

    from utils.custom_query import run_query
    cond_tree = request.get_json(silent=True) or {}
    try:
        rows = run_query(cond_tree, can_view_sensitive=_can_view_sensitive(user), limit=500)
    except ValueError as e:
        return jsonify({'success': False, 'msg': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'msg': f'查询失败: {e}'}), 400
    return jsonify({'success': True, 'data': rows, 'total': len(rows)})


@report_bp.route('/api/custom-query/export', methods=['POST'])
@child_rows_guard
def custom_query_export():
    """导出 Excel（temp/exports token 通道；脱敏或按权限含明文）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    user = _require_report_view()
    if not user or not user_has_permission(user, 'report.export'):
        return jsonify({'success': False, 'msg': '权限不足'}), 403

    from utils.custom_query import export_rows
    from utils.helpers import write_export_xlsx

    cond_tree = request.get_json(silent=True) or {}
    try:
        headers, data = export_rows(cond_tree, can_view_sensitive=_can_view_sensitive(user), limit=2000)
    except ValueError as e:
        return jsonify({'success': False, 'msg': str(e)}), 400

    _path, token = write_export_xlsx(headers, data, module='report',
                                     filename='自定义查询结果.xlsx')
    if not token:
        return jsonify({'success': False, 'msg': '导出通道未就绪'}), 500
    log_operation(user.username, 'report', 'export',
                  f'自定义查询导出 {len(data)} 行（token 通道）')
    return jsonify({'success': True,
                    'data': {'download_url': f'/report/download/{token}',
                             'rows': len(data)}})


# ============ T21-2：花名册 PDF 导出（R10-E1~E4，reportlab + 思源黑体） ============

@report_bp.route('/export-pdf', methods=['POST'])
@child_rows_guard
def export_pdf():
    """花名册 PDF 导出（T21-2；reportlab + 思源黑体，temp/exports token 通道）。

    R10-E1（PDF 格式）/ R10-E2（留痕含文件名+是否含明文）/ R10-E3（脱敏版）/
    R10-E4（盖章位，build_roster_pdf 内置）；Z3 行级过滤与 Excel 同链路。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    user = User.query.get(session.get('user_id'))
    if not user or not user.is_active:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    if user.role_id != 1 and not user_has_permission(user, 'report.export'):
        return jsonify({'success': False, 'msg': '权限不足'}), 403

    from utils.masking import filter_child_rows
    import os, uuid
    from datetime import datetime
    data = request.get_json(silent=True) or request.form
    category = (data.get('category') or '高龄').strip()
    grid = (data.get('grid') or '').strip() or None

    # 导出目录：环境变量优先（测试隔离友好），回退 Config（与 export_excel 同口径）
    from config import Config
    env_tmp = os.environ.get('SHUCUN_TEMP_DIR')
    exp_dir = os.path.join(env_tmp, 'exports') if env_tmp else getattr(Config, 'EXPORTS_DIR', None)
    if not exp_dir:
        exp_dir = os.path.join(Config.TEMP_DIR, 'exports')
    os.makedirs(exp_dir, exist_ok=True)

    can_plain = _can_view_sensitive(user)
    filename = f"{'含明文_' if can_plain else ''}{uuid.uuid4().hex}.pdf"
    path = os.path.join(exp_dir, filename)

    raw = _roster_rows(category, grid)
    visible = filter_child_rows(raw, g.can_see_children)   # Z3：行级过滤（不豁免明文权）
    headers = _roster_headers(category)
    sheet_rows = _roster_sheet_rows(category, visible)
    sheet_rows.append(_summary_row(category, len(visible)))   # 统计汇总行（PDF 末行加粗）

    try:
        from utils.pdf_export import build_roster_pdf
        from app import _get_system_title
        build_roster_pdf(
            headers, sheet_rows,
            {'village': _get_system_title(),
             'title': f'{category}花名册',
             'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
             'operator': user.username,
             'has_plain': can_plain},
            path)
    except ValueError as e:
        return jsonify({'success': False, 'msg': str(e)}), 500

    summary = f'花名册PDF {category} {len(visible)} 行'
    plain_mark = '是' if can_plain else '否'
    return _issue_export(user, path, 'report', summary,
                         detail=f'含明文={plain_mark}; 文件名={os.path.basename(path)}')
