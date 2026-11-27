# -*- coding: utf-8 -*-
"""routes.metrics — 统一统计取数端点（任务 081：Task 3）。

规格依据：`.trae/specs/build-c-stats-metrics-engine/spec.md` §「统一统计 API」。
- 唯一入口 ``GET /api/stats/query``（app.py 注册 url_prefix='/api/stats'）；
- 路由只做参数解析/鉴权/响应组装，统计口径全部由 ``utils.metrics`` 拥有，
  本模块**禁止**重写任何统计口径；
- 鉴权按 ADR-016/ADR-023 归类：``metrics.query`` 端点级「登录即可」
  （``utils/permissions.ENDPOINT_MODULES`` 登记 None），
  端点内再按指标登记的 ``permission`` 模块位二次校验。
"""
import json

from flask import Blueprint, jsonify, request, session

from models import User
from utils.decorators import login_required
from utils.metrics import MetricsEngine
from utils.permissions import user_has_permission

metrics_bp = Blueprint('metrics', __name__)

_RESERVED_ARGS = {'metric_key', 'dims', 'filters', 'format'}


def _parse_filters() -> dict:
    """filters 解析：显式 JSON 对象优先，否则其余查询参数即 filters。

    显式传入但非 JSON 对象 → ValueError（由调用方统一转 400）。
    """
    raw = request.args.get('filters')
    if raw is not None and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            raise ValueError('filters 必须是 JSON 对象') from None
        if not isinstance(parsed, dict):
            raise ValueError('filters 必须是 JSON 对象')
        return parsed
    return {key: value for key, value in request.args.items()
            if key not in _RESERVED_ARGS}


@metrics_bp.route('/query', methods=['GET'])
@login_required
def query():
    """统一统计查询：metric_key 必填，dims 逗号分隔，filters JSON 对象或同名参数。"""
    metric_key = (request.args.get('metric_key') or '').strip()
    if not metric_key:
        return jsonify({'success': False, 'msg': 'metric_key 必填'}), 400

    try:
        metric = MetricsEngine.get_metric(metric_key)
        filters = _parse_filters()
    except ValueError as e:
        return jsonify({'success': False, 'msg': str(e)}), 400

    user = User.query.get(session.get('user_id'))
    if not user:
        return jsonify({'success': False, 'msg': '未登录'}), 401
    permission = metric.get('permission')
    if user.role_id != 1 and permission and \
            not user_has_permission(user, f'{permission}.view'):
        return jsonify({'success': False, 'msg': '权限不足'}), 403

    dims = request.args.get('dims')
    format_ = request.args.get('format') or 'json'
    try:
        data = MetricsEngine.query(metric_key, dims=dims, filters=filters,
                                   format=format_)
    except ValueError as e:
        return jsonify({'success': False, 'msg': str(e)}), 400
    return jsonify({'success': True, 'data': data})
