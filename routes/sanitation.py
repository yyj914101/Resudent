# -*- coding: utf-8 -*-
"""routes/sanitation.py — 090C 环卫工作（sanitation 门面 + 三软表 + 两指标）。

参照 088 pension 的「门面页 + 软表挂载薄路由」范式（ADR-027：固定菜单组 → module_key →
dynamic_table.code），数据层为软表承载（零 DDL）：sanitation_team / sanitation_site /
sanitation_cleanup 三张表行由 m031 预置，字段由首次上传引导确认生成（ADR-025 决策 3）。

本文件只做「参数提取 → 转调门面/引擎 → JSON / 渲染」：
- 加密/指纹/字段推断全部在 utils/roster_facade.py 与 utils/softtable_engine.py（零平行实现）；
- 两指标（M-01 在岗人数合计 sanitation_team_headcount / M-02 点位数量 sanitation_site_count，
  一页纸 v1.2 §5）唯一取数入口 MetricsEngine.query（utils/metrics.py 注册表），页面代理
  /api/stats 与统一统计入口 /api/stats/query、引擎直调三处同值；
- 权限走业务模块位 BLUEPRINT_MODULES['sanitation'] + 视图命名关键字
  （list/add/edit/upload/create/export/delete）由 enforce_role_permissions 自动推断。
"""
import json
import os
import secrets

from flask import Blueprint, abort, jsonify, render_template, request, session

import config  # noqa: E402  （软表上传临时目录归属 config，与 pension/shop 同款）
from models import db
from utils.decorators import login_required
from utils.helpers import json_response, log_operation, export_excel
from utils.metrics import MetricsEngine

sanitation_bp = Blueprint('sanitation', __name__)

# ================================================================== 门面页 + 指标代理

# 软表承载的环卫三册（m031 预置行；字段由首次上传引导确认生成，ADR-025 决策 3）
SANITATION_TEAM_CODE = 'sanitation_team'
SANITATION_SITE_CODE = 'sanitation_site'
SANITATION_CLEANUP_CODE = 'sanitation_cleanup'

_SANITATION_TABLES = (
    (SANITATION_TEAM_CODE, '环卫保洁队伍'),
    (SANITATION_SITE_CODE, '环卫设施点位'),
    (SANITATION_CLEANUP_CODE, '环卫整治记录'),
)

# 一页纸 v1.2 §1-§3 建议列（引导文案；队伍类型/点位类型已按 R-01/R-02 删除，
# 「照片」列按 §8 走附件区、仅存路径/附件引用，不在 Excel 表头引导中展开）
TEAM_CANONICAL_COLUMNS = ['队伍名称', '负责人', '联系电话', '在岗人数', '负责片区', '备注']
SITE_CANONICAL_COLUMNS = ['点位名称', '所在位置', '数量/容量', '使用状态', '责任人', '备注']
CLEANUP_CANONICAL_COLUMNS = ['整治日期', '整治地点/片区', '整治类型', '参与人数', '整治内容', '记录人']


@sanitation_bp.route('/')
@login_required
def index():
    """门面页：两指标卡（M-01/M-02）+ 三软表入口 + 建册引导/空态。"""
    from utils import roster_facade as facade
    rosters = []
    for code, fallback_name in _SANITATION_TABLES:
        table = facade.get_table('sanitation', code)
        rosters.append({
            'code': code,
            'name': table.name if table else fallback_name,
            'built': bool(table and facade.is_built(table)),
        })
    return render_template('sanitation/index.html', rosters=rosters,
                           team_columns=TEAM_CANONICAL_COLUMNS,
                           site_columns=SITE_CANONICAL_COLUMNS,
                           cleanup_columns=CLEANUP_CANONICAL_COLUMNS)


@sanitation_bp.route('/api/stats')
@login_required
def api_stats():
    """指标查询代理：唯一取数入口 MetricsEngine.query（页面/看板/引擎三处同值）。"""
    try:
        headcount = MetricsEngine.query('sanitation_team_headcount')
        site_count = MetricsEngine.query('sanitation_site_count')
    except ValueError as exc:
        return json_response(False, str(exc)), 400
    return json_response(True, '', data={
        'headcount': {'total': headcount['total'], 'data': headcount['data'],
                      'label': headcount['label']},
        'site_count': {'total': site_count['total'], 'data': site_count['data'],
                       'label': site_count['label']},
    })


# ================================================================== 090C 软表挂载门面（薄路由，088 pension 同款）
# 业务判断全部收口在 utils/roster_facade.py；本段只做「参数提取 → 转调门面 → 包装」。

from utils import roster_facade as facade  # noqa: E402
from utils import softtable_engine as eng  # noqa: E402

from utils.roster_routes import register_roster_routes as _register_roster_routes  # noqa: E402
_register_roster_routes(sanitation_bp, module_key='sanitation', group_name='民生服务',
                        log_module='环卫工作', nav_module='sanitation')


