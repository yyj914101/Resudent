# -*- coding: utf-8 -*-
"""routes/civilization.py — 089 文明实践站（civilization 新蓝图 + 公约固定表 + 软表活动记录）。

参照 088 pension 的「门面页 + 软表挂载门面」薄路由范式（ADR-027：固定菜单组 →
module_key → dynamic_table.code）与 083A party 的固定表 CRUD：
- 数据层 ADR-028 三分法：活动记录走软表 civilization_activity（m031 已预置元数据行，
  字段由首次上传引导确认生成，ADR-025 决策 3，零物理 DDL）；居民公约走固定小表
  civ_convention（m040 建表，「同一时刻仅一个现行」= 部分唯一索引 + 应用层换版
  同事务互斥双保险）。
- 本文件只做「参数提取 → 转调门面/引擎 → JSON / 渲染」：加密/指纹/字段推断全部在
  utils/roster_facade.py 与 utils/softtable_engine.py（零平行实现）；
- 两指标（年度场次 civilization_sessions / 年度人次 civilization_person_visits）
  唯一取数入口 MetricsEngine.query（utils/metrics.py 注册表），页面代理 /api/stats
  与统一统计入口 /api/stats/query、引擎直调三处同值；
- 权限走业务模块位 BLUEPRINT_MODULES['civilization'] + 视图命名关键字
  （list/add/edit/delete/upload/create/export）由 enforce_role_permissions 自动推断；
- 公约/活动附件走 092 module_attachment（module_key='civilization'），
  收口 utils/attachment_service.py，禁复制实现。
"""
import json
import os
import secrets

from flask import Blueprint, abort, jsonify, render_template, request, send_file, session

from models import CivConvention, db
from utils.decorators import login_required
from utils.helpers import json_response, log_operation, export_excel
from utils.metrics import MetricsEngine

civilization_bp = Blueprint('civilization', __name__)

# ================================================================== 门面页 + 指标代理

# 软表承载的文明实践活动记录（m031 预置行；字段由首次上传引导确认生成，ADR-025 决策 3）
CIV_ACTIVITY_CODE = 'civilization_activity'

# 一页纸 v1.2 §1 活动记录 8 列规范列（引导文案；A-001~A-008 人工核对全确认，无待确认列）
CIV_CANONICAL_COLUMNS = ['活动日期', '活动主题', '活动类型', '参与人数',
                         '活动地点', '活动内容', '照片', '记录人']


@civilization_bp.route('/')
@login_required
def index():
    """门面页：年度场次/人次汇总卡 + 公约卡（现行版本 + 历史列表）+ 活动记录入口。"""
    from utils import roster_facade as facade
    table = facade.get_table('civilization', CIV_ACTIVITY_CODE)
    roster = {
        'code': CIV_ACTIVITY_CODE,
        'name': table.name if table else '文明实践活动记录',
        'built': bool(table and facade.is_built(table)),
    }
    return render_template('civilization/index.html', roster=roster,
                           canonical_columns=CIV_CANONICAL_COLUMNS)


@civilization_bp.route('/api/stats')
@login_required
def api_stats():
    """指标查询代理：唯一取数入口 MetricsEngine.query（页面/看板/引擎三处同值）。

    可选 year 查询参数（默认自然年口径由前端传入当前年份；不带 year = 全部年度）。
    """
    filters = {}
    year = (request.args.get('year') or '').strip()
    if year:
        filters['year'] = year
    try:
        sessions = MetricsEngine.query('civilization_sessions', filters=filters)
        person_visits = MetricsEngine.query('civilization_person_visits', filters=filters)
    except ValueError as exc:
        return json_response(False, str(exc)), 400
    return json_response(True, '', data={
        'sessions': {'total': sessions['total'], 'data': sessions['data'],
                     'buckets': sessions['buckets'], 'label': sessions['label']},
        'person_visits': {'total': person_visits['total'], 'data': person_visits['data'],
                          'buckets': person_visits['buckets'],
                          'label': person_visits['label']},
    })


# ================================================================== 居民公约（固定表 CRUD + 换版流）
# 唯一现行约束双保险：m040 部分唯一索引（DB 兜底）+ 本段换版同事务互斥（应用层）。

def _convention_to_dict(row):
    return {
        'id': row.id,
        'title': row.title or '',
        'content': row.content or '',
        'attachment_path': row.attachment_path or '',
        'is_current': bool(row.is_current),
        'created_by': row.created_by or '',
        'created_at': row.created_at.strftime('%Y-%m-%d %H:%M') if row.created_at else '',
        'updated_at': row.updated_at.strftime('%Y-%m-%d %H:%M') if row.updated_at else '',
    }


def _convention_payload(payload):
    """表单载荷 → (title, content, attachment_path)；标题必填 ≤100 字（V-06）。"""
    title = (payload.get('title') or '').strip()
    if not title:
        return None, None, None, '请填写公约标题'
    if len(title) > 100:
        return None, None, None, '公约标题不能超过 100 字'
    content = (payload.get('content') or '').strip() or None
    attachment_path = (payload.get('attachment_path') or '').strip() or None
    if attachment_path and len(attachment_path) > 255:
        return None, None, None, '附件引用过长（上限 255 字符）'
    return title, content, attachment_path, None


@civilization_bp.route('/api/conventions', methods=['GET'])
@login_required
def api_conventions():
    """公约列表：current（现行，可 None）+ history（历史按创建时间倒序）。"""
    current = CivConvention.query.filter_by(is_current=True).first()
    history = (CivConvention.query.filter_by(is_current=False)
               .order_by(CivConvention.created_at.desc(), CivConvention.id.desc()).all())
    return json_response(True, '', data={
        'current': _convention_to_dict(current) if current else None,
        'history': [_convention_to_dict(row) for row in history],
    })


@civilization_bp.route('/api/conventions', methods=['POST'])
@login_required
def api_convention_create():
    """新增公约版本（换版流）：新版本置为现行，旧现行同事务自动置历史。

    顺序：先 UPDATE 降旧现行、后 INSERT 新现行（同一事务），任一语句违反
    uq_civ_convention_current 部分唯一索引即整体回滚（并发竞态被捕获）。
    """
    payload = request.get_json(silent=True) or {}
    title, content, attachment_path, err = _convention_payload(payload)
    if err:
        return json_response(False, err)
    CivConvention.query.filter(CivConvention.is_current.is_(True)).update(
        {'is_current': False}, synchronize_session=False)
    row = CivConvention(title=title, content=content,
                        attachment_path=attachment_path,
                        is_current=True, created_by=session.get('username', ''))
    db.session.add(row)
    db.session.commit()
    log_operation(session.get('username', ''), '文明实践', 'add',
                  '新增公约版本 #%s「%s」（旧现行已自动转历史）' % (row.id, title))
    return json_response(True, '已保存，新版本为现行', data=_convention_to_dict(row))


@civilization_bp.route('/api/conventions/<int:cid>', methods=['POST'])
@login_required
def api_convention_edit(cid):
    """编辑公约：仅现行版本可编辑；历史版本只读 → 403（任务书验收 2）。"""
    row = CivConvention.query.get(cid)
    if row is None:
        return json_response(False, '公约版本不存在'), 404
    if not row.is_current:
        return json_response(False, '历史版本只读，不可编辑'), 403
    payload = request.get_json(silent=True) or {}
    title, content, attachment_path, err = _convention_payload(payload)
    if err:
        return json_response(False, err)
    row.title = title
    row.content = content
    row.attachment_path = attachment_path
    db.session.commit()
    log_operation(session.get('username', ''), '文明实践', 'edit',
                  '编辑现行公约 #%s「%s」' % (row.id, title))
    return json_response(True, '已保存', data=_convention_to_dict(row))


@civilization_bp.route('/api/conventions/<int:cid>/delete', methods=['POST'])
@login_required
def api_convention_delete(cid):
    """删除公约版本：仅历史版本可删（清错行）；现行版本不可删，须先新增版本转历史。"""
    row = CivConvention.query.get(cid)
    if row is None:
        return json_response(False, '公约版本不存在'), 404
    if row.is_current:
        return json_response(False, '现行版本不可删除，请先新增版本将其转为历史'), 400
    db.session.delete(row)
    db.session.commit()
    log_operation(session.get('username', ''), '文明实践', 'delete',
                  '删除历史公约 #%s「%s」' % (cid, row.title))
    return json_response(True, '已删除')


# ================================================================== 089 软表挂载门面（薄路由，088 pension 同款）
# 业务判断全部收口在 utils/roster_facade.py；本段只做「参数提取 → 转调门面 → 包装」。

import config  # noqa: E402  （与 shop.py / pension.py 门面段同款：临时目录归属 config）

from utils import roster_facade as facade  # noqa: E402
from utils import softtable_engine as eng  # noqa: E402

from utils.roster_routes import register_roster_routes as _register_roster_routes  # noqa: E402
_register_roster_routes(civilization_bp, module_key='civilization', group_name='人口与台账',
                        log_module='文明实践', nav_module='civilization')


# ═══════════════════ 092 模板附件区（收口 utils/attachment_service，禁复制实现） ═══════════════════
# 公约附件与活动照片统一挂 module_key='civilization'；正文 Text 兜底 + 附件引用为增强。

from utils import attachment_service  # noqa: E402
from utils.attachment_service import attachment_api  # noqa: E402


from utils.attachment_service import register_attachment_routes as _register_attachment_routes  # noqa: E402
_register_attachment_routes(civilization_bp, 'civilization')
