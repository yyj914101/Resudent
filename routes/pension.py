# -*- coding: utf-8 -*-
"""routes/pension.py — 088 养老保险（pension 新蓝图 + 软表参保名册 + 两指标）。

参照 medical_insurance 的「门面页」体验与 099 shop 的「软表挂载门面」薄路由范式
（ADR-027：固定菜单组 → module_key → dynamic_table.code），数据层为软表承载：
pension_roster（m031 预置）参保名册，列语义按字段清单-v1 §3.4.2 养老段规范列名。

本文件只做「参数提取 → 转调门面/引擎 → JSON / 渲染」：
- 加密/指纹/字段推断全部在 utils/roster_facade.py 与 utils/softtable_engine.py（零平行实现）；
- 两指标（参保率 pension_coverage / 认证完成率 pension_cert_rate）唯一取数入口
  MetricsEngine.query（utils/metrics.py 注册表），页面代理 /api/stats 与
  统一统计入口 /api/stats/query、引擎直调三处同值；
- 权限走业务模块位 BLUEPRINT_MODULES['pension'] + 视图命名关键字
  （list/add/edit/upload/create/export）由 enforce_role_permissions 自动推断。
"""
import json
import os
import secrets

from flask import Blueprint, abort, jsonify, render_template, request, session

from models import db
from utils.decorators import login_required
from utils.helpers import json_response, log_operation, export_excel
from utils.metrics import MetricsEngine

pension_bp = Blueprint('pension', __name__)

# ================================================================== 门面页 + 指标代理

# 软表承载的参保名册（m031 预置行；字段由首次上传引导确认生成，ADR-025 决策 3）
PENSION_ROSTER_CODE = 'pension_roster'

# 字段清单-v1 §3.4.2 养老段规范列（引导文案；[占位]/待确认列不猜字段）
PENSION_CANONICAL_COLUMNS = ['姓名', '身份证号', '参保类型', '参保状态', '缴费年度',
                             '待遇认证状态']
PENSION_UNCONFIRMED_COLUMNS = ['参保地']  # 上级是否要求单列待定（§五待确认）


@pension_bp.route('/')
@login_required
def index():
    """养老保页面（2026-11-16 取消二级布局）：

    - 已建册 → 直接渲染参保名册台账（复用 components/roster/roster_page.html，与「医保参保管理」
      同构：页内胶囊 + 页头动作 + 指标卡 + 筛选工具条 + 表格 + 分页），点菜单即见数据，无需二次跳转；
    - 未建册 → 门面引导页（指标卡 + 建册上传 + 规范列指引），保留 088 首次建册口径。

    台账数据端点仍是 /pension/roster/<code>/api/*（roster_page.html 的 __ST_ROSTER__ 契约不变）。
    """
    from utils import roster_facade as facade
    table = facade.get_table('pension', PENSION_ROSTER_CODE)
    roster = {
        'code': PENSION_ROSTER_CODE,
        'name': table.name if table else '养老保险参保名册',
        'built': bool(table and facade.is_built(table)),
    }
    fields = facade.fields_with_options(table) if roster['built'] else []
    if fields:
        return render_template(
            'components/roster/roster_page.html',
            table=table, code=PENSION_ROSTER_CODE, fields=fields,
            roster_prefix='/pension',
            nav_module='social', nav_active='pension',
            page_title='养老保险',
            page_meta='参保名册台账 · 参保率与待遇认证进度同源（指标卡 / 看板 / 引擎三处同值）',
            show_back=False,                       # 已是一级页，无上级业务页可返回
            page_class='pension-page sc-workflow-page',
            page_css='css/pages/pension.css',
            stats_include='pension/_stats.html',
            stats_script='js/pages/pension.js',
        )
    return render_template('pension/index.html', roster=roster,
                           canonical_columns=PENSION_CANONICAL_COLUMNS,
                           unconfirmed_columns=PENSION_UNCONFIRMED_COLUMNS)


@pension_bp.route('/api/stats')
@login_required
def api_stats():
    """指标查询代理：唯一取数入口 MetricsEngine.query（页面/看板/引擎三处同值）。"""
    try:
        coverage = MetricsEngine.query('pension_coverage')
        cert_rate = MetricsEngine.query('pension_cert_rate')
    except ValueError as exc:
        return json_response(False, str(exc)), 400
    return json_response(True, '', data={
        'coverage': {'total': coverage['total'], 'data': coverage['data'],
                     'buckets': coverage['buckets'], 'label': coverage['label']},
        'cert_rate': {'total': cert_rate['total'], 'data': cert_rate['data'],
                      'buckets': cert_rate['buckets'], 'label': cert_rate['label']},
    })


# ================================================================== 088 软表挂载门面（薄路由，099 shop 同款）
# 业务判断全部收口在 utils/roster_facade.py；本段只做「参数提取 → 转调门面 → 包装」。

import config  # noqa: E402  （与 shop.py 门面段同款：临时目录归属 config）

from utils import roster_facade as facade  # noqa: E402
from utils import softtable_engine as eng  # noqa: E402

from utils.roster_routes import register_roster_routes as _register_roster_routes  # noqa: E402
_register_roster_routes(pension_bp, module_key='pension', group_name='社会保障', log_module='养老保险')
