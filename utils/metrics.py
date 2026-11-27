# -*- coding: utf-8 -*-
"""utils.metrics — C 统计输出统一指标引擎（任务 081：Task 1+2）。

规格依据：`.trae/specs/build-c-stats-metrics-engine/spec.md`、
`docs/统计口径接入-任务书框架-20261103.md` §五、ADR-016/ADR-023。

深模块契约：
- 指标注册表 ``METRIC_REGISTRY`` 是统计口径的**单一真源**
  （label/definition/dims/source/aggregator/cache_ttl/sensitive/permission/output/handler）；
- ``MetricsEngine.query(metric_key, dims, filters, format)`` 是唯一取数入口，
  返回统一 ``MetricResult`` 字典；
- ``Aggregator`` 协议是内部 seam：``SqlAggregator``（固定模型）与
  ``TemplateAdapter``（notice 模板类指标）；``SoftTableAggregator`` 本期不接；
- 缓存复用 ``utils.agg_cache.get/put``，key 含固定口径版本 + 字典内容签名；
- 脱敏统一走 ``utils.masking`` 三函数（本模块不另写打码规则）。

owner map：统计口径落 ``utils/``（routes/ 只接线，禁在路由内重写口径）；
不改模型、不改枚举值、不引入新依赖。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

# ============ 口径常量（Task 4 agg_cache 与 Task 3 测试共同读取） ============
CACHE_VERSION = 'v1.0-081'
DEFAULT_TTL = 45

# 花名册类别（标签口径）——与 utils/agg_cache.py 现有硬编码逐条一致。
# elderly 非 tag（动态年龄口径），tag_code 置 None 供调用方统一取键。
ROSTER_CATEGORIES = {
    'hard': {'tag_code': '困难老人', 'label': '困难老人',
             'target_type': '老人', 'child_type': '',
             'stats_key': '困难', 'stats_name': '困难老人'},
    'left': {'tag_code': '留守儿童', 'label': '留守儿童',
             'target_type': '儿童', 'child_type': '留守',
             'stats_key': '留守', 'stats_name': '留守儿童'},
    'distressed': {'tag_code': '困境儿童', 'label': '困境儿童',
                   'target_type': '儿童', 'child_type': '困境',
                   'stats_key': '困境', 'stats_name': '困境儿童'},
    'elderly': {'tag_code': None, 'label': '高龄',
                'target_type': '老人', 'child_type': '',
                'stats_key': '高龄', 'stats_name': '高龄老人'},
}

# 户属性三值（T17-1 困难家庭口径）+ 脱贫户单项口径
HOUSEHOLD_HARD_PROPERTIES = ('脱贫户', '监测户', '五保户')
POVERTY_ALLEVIATED_PROPERTY = '脱贫户'

# 统计表年龄段分箱（T17-3 主理人拍板 4 段；与 agg_cache 数值一致）
STATS_AGE_BANDS = ('0-17', '18-59', '60-79', '80+')

# 首页年龄分布（7 段，保留既有 bin/年差口径）与首页人口结构（6 段，070 页面级规格）
AGE_DISTRIBUTION_LABELS = ('0-18', '19-30', '31-40', '41-50', '51-60', '61-80', '80+')
AGE_DISTRIBUTION_BINS = ((0, 18), (19, 30), (31, 40), (41, 50),
                         (51, 60), (61, 80), (81, 200))
AGE_STRUCTURE_LABELS = ('0-17', '18-35', '36-59', '60-79', '80-89', '90+')
AGE_STRUCTURE_BINS = ((0, 17), (18, 35), (36, 59), (60, 79), (80, 89), (90, 200))

# 094 重点人员（综治台账 category）；2026-11-16：社区矫正 / 社区戒毒康复下线，
# 原「刑满释放帮教」扩为「刑事人员」（category = criminal，两类人员按人员状态筛选）
KEY_PERSONNEL_CATEGORIES = ('criminal',)

# 注册表九项声明字段（测试断言入口）
SCHEMA_KEYS = ('label', 'definition', 'dims', 'source', 'aggregator',
               'cache_ttl', 'sensitive', 'permission', 'output')


# ============ 统一返回结构 ============

@dataclass
class MetricResult:
    """统一指标返回结构（看板/报表/模块页三调用方共用）。"""
    metric_key: str
    label: str
    definition: str
    total: int | float
    buckets: list
    data: dict
    sources: list
    cache_ttl: float
    cache_version: str
    generated_at: str
    masked: bool
    dims: list
    filters: dict

    def to_dict(self) -> dict:
        """按字段顺序输出同名字典（JSON 可直接序列化）。"""
        return {
            'metric_key': self.metric_key,
            'label': self.label,
            'definition': self.definition,
            'total': self.total,
            'buckets': self.buckets,
            'data': self.data,
            'sources': self.sources,
            'cache_ttl': self.cache_ttl,
            'cache_version': self.cache_version,
            'generated_at': self.generated_at,
            'masked': self.masked,
            'dims': self.dims,
            'filters': self.filters,
        }


# ============ 聚合器适配（内部 seam） ============

class Aggregator(Protocol):
    """聚合器协议：把一条指标定义解析为 total/buckets/data。"""

    def aggregate(self, metric: dict, dims: list[str], filters: dict) -> dict:
        ...


class SqlAggregator:
    """固定模型聚合器：直接调用注册表 handler 做 GROUP BY / COUNT。"""
    kind = 'sql'

    def aggregate(self, metric: dict, dims: list[str], filters: dict) -> dict:
        return metric['handler'](dims, filters)


class TemplateAdapter:
    """notice 模板类适配器（kind='template'，便于测试识别）。

    与 SqlAggregator 同走 handler；仅语义上限定用于公示模板记录计数，
    不把 notice 当业务主数据真源。
    """
    kind = 'template'

    def aggregate(self, metric: dict, dims: list[str], filters: dict) -> dict:
        return metric['handler'](dims, filters)


_SQL = SqlAggregator()
_TEMPLATE = TemplateAdapter()


# ============ 脱敏（identity 键统一打码，幂等） ============

def _mask_value(key, value):
    from utils.masking import mask_bank_card, mask_id_card, mask_phone
    if not isinstance(value, str):
        return value
    name = str(key or '').lower()
    if 'id_card' in name:
        return mask_id_card(value)
    if 'phone' in name:
        return mask_phone(value)
    if 'bank_card' in name:
        return mask_bank_card(value)
    return value


def _mask_recursive(obj, key=None):
    if isinstance(obj, dict):
        return {k: _mask_recursive(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_recursive(v, key) for v in obj]
    return _mask_value(key, obj)


def mask_metric_payload(payload, can_view_sensitive: bool = False):
    """递归脱敏指标结果中的身份字段（计数指标无身份字段 → 结果幂等）。

    can_view_sensitive=True 时原样返回（是否允许明文由调用方按指标
    ``sensitive`` 决定；本模块计数指标不含身份字段）。
    """
    if can_view_sensitive:
        return payload
    return _mask_recursive(payload)


_IDENTITY_KEY_HINTS = ('id_card', 'phone', 'bank_card')


def contains_identity_keys(payload) -> bool:
    """结果是否含 identity 键（用于如实标记 MetricResult.masked，避免恒 True）。"""
    if isinstance(payload, dict):
        for key, value in payload.items():
            name = str(key or '').lower()
            if any(hint in name for hint in _IDENTITY_KEY_HINTS):
                return True
            if contains_identity_keys(value):
                return True
    elif isinstance(payload, list):
        return any(contains_identity_keys(item) for item in payload)
    return False


# ============ handler 工具函数 ============

def _text(value) -> str:
    return str(value or '').strip()


def _round2(value) -> float:
    return round(float(value or 0), 2)


def _apply_date_filters(query, model, filters):
    """按 record_date 施加 from_date/to_date（ISO 日期，非法格式结构化报错）。"""
    for key, op in (('from_date', 'ge'), ('to_date', 'le')):
        raw = _text(filters.get(key))
        if not raw:
            continue
        try:
            parsed = date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f'{key} 格式应为 YYYY-MM-DD') from None
        if op == 'ge':
            query = query.filter(model.record_date >= parsed)
        else:
            query = query.filter(model.record_date <= parsed)
    return query


def _buckets_from_counts(counts: dict, labels: dict | None = None) -> list:
    """计数 → 统一桶结构（按计数降序、key 升序；label 缺省等于 key）。"""
    labels = labels or {}
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{'key': key, 'label': labels.get(key, key), 'count': count}
            for key, count in ordered]


# ============ C 指标 handler（输入 dims/filters，输出 total/buckets/data） ============

def _handler_dashboard_counts(dims, filters):
    """看板核心计数（T17-1 双轨口径，纯函数 compute_dashboard_counts 复用）。"""
    from utils.agg_cache import compute_dashboard_counts
    counts = compute_dashboard_counts()
    return {
        'total': counts.get('total_population', 0),
        'buckets': [],
        'data': counts,
    }


def _handler_stats_table(dims, filters):
    """一老一小统计表（T17-3：四分类 × 网格 × 年龄段，含合计）。"""
    from utils.agg_cache import compute_stats_table
    grid = _text(filters.get('grid')) or None
    data = compute_stats_table(grid)
    return {
        'total': data.get('grand_total', 0),
        'buckets': [],
        'data': data,
    }


_PARTY_DIM_FIELDS = ('member_identity', 'member_mobility',
                     'employment_status', 'party_status', 'branch',
                     'party_category')


def _party_category(member) -> str:
    """080 A-1 组合口径：退休 > 流动 > 在家（互斥三分类，已转出由调用方排除）。"""
    if _text(getattr(member, 'employment_status', '')) == '退休':
        return '退休'
    if _text(getattr(member, 'member_mobility', '')) == '流动':
        return '流动'
    return '在家'


def _party_dim_value(member, dim: str) -> str:
    """党员维度取值：party_category 走组合口径；branch 空归「未分配党支部」，其余空值归「未填写」。"""
    if dim == 'party_category':
        return _party_category(member)
    raw = _text(getattr(member, dim, ''))
    if raw:
        return raw
    return '未分配党支部' if dim == 'branch' else '未填写'


def _handler_party_members(dims, filters):
    """C-1：党员按类别/支部统计（默认排除已转出）。"""
    from models import PartyMember

    members = PartyMember.query.all()
    status_filter = _text(filters.get('party_status'))
    rows = []
    for member in members:
        status = _text(member.party_status)
        if status_filter:
            if status != status_filter:
                continue
        elif status == '已转出':
            continue  # 默认排除已转出（R-080-04 口径）
        skip = False
        for dim in _PARTY_DIM_FIELDS:
            if dim not in filters:
                continue
            want = _text(filters.get(dim))
            if want and _party_dim_value(member, dim) != want:
                skip = True
                break
        if not skip:
            rows.append(member)

    dims = list(dims) or ['member_identity']
    counts = {}
    labels = {}
    for member in rows:
        values = [_party_dim_value(member, dim) for dim in dims]
        key = values[0] if len(values) == 1 else ' | '.join(values)
        labels[key] = key if len(values) == 1 else ' · '.join(values)
        counts[key] = counts.get(key, 0) + 1

    return {
        'total': len(rows),
        'buckets': _buckets_from_counts(counts, labels),
        'data': {},
    }


def _handler_party_summary(dims, filters):
    """C-1 兼容：完全复刻 routes/party.py::api_summary 现有计算。"""
    from models import db, PartyMember, Resident

    members = PartyMember.query.all()
    on_roll = sum(1 for m in members if m.party_status != '已转出')
    # R-080-04：formal/probation/floating 均排除已转出
    formal = sum(1 for m in members if m.member_identity == '正式党员'
                 and m.party_status != '已转出')
    probation = sum(1 for m in members if m.member_identity == '预备党员'
                    and m.party_status != '已转出')
    floating = sum(1 for m in members if m.member_mobility == '流动'
                   and m.party_status != '已转出')
    transferred = sum(1 for m in members if m.party_status == '已转出')
    retired = sum(1 for m in members
                  if m.employment_status == '退休' and m.party_status != '已转出')

    subq = db.session.query(PartyMember.id_card_hash).filter(
        PartyMember.id_card_hash.isnot(None)).subquery()
    auto = Resident.query.filter(
        Resident.politics_status.in_(['中共党员', '中共预备党员']),
        ~Resident.id_card_hash.in_(db.select(subq.c.id_card_hash)),
    ).count()

    branch_counts = {}
    for m in members:
        if m.party_status == '已转出':
            continue
        branch = _text(m.branch) or '未分配党支部'
        branch_counts[branch] = branch_counts.get(branch, 0) + 1
    if auto:
        branch_counts['未分配党支部'] = branch_counts.get('未分配党支部', 0) + auto
    branch_stats = [
        {'branch': branch, 'count': count}
        for branch, count in sorted(branch_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    data = {
        'on_roll': on_roll, 'formal': formal, 'probation': probation,
        'floating': floating, 'retired': retired, 'transferred': transferred,
        'auto': auto, 'total_archived': on_roll + auto,
        'branch_stats': branch_stats,
        'unassigned_branch': branch_counts.get('未分配党支部', 0),
    }
    return {
        'total': data['total_archived'],
        'buckets': [{'key': s['branch'], 'label': s['branch'], 'count': s['count']}
                    for s in branch_stats],
        'data': data,
    }


def _governance_titles() -> dict:
    """治理台账类别标题（自 utils.governance_catalog 导入，消除 utils→routes 反向依赖）。"""
    from utils.governance_catalog import CATEGORY_CONFIG
    return {key: cfg.get('title') or key for key, cfg in CATEGORY_CONFIG.items()}


def _governance_buckets(targets: list, filters: dict) -> dict:
    """按 category 分组治理台账计数（Patch C-2：统一从软表 V2 dynamic_table/dynamic_record 取数）。"""
    import json as _json
    from models import DynamicRecord, DynamicTable
    from utils.governance_catalog import GOV_CATEGORY_CODES

    if not targets:
        return {'total': 0, 'buckets': [], 'data': {}}

    req_cat = _text(filters.get('category'))
    active_targets = [c for c in targets if (not req_cat or c == req_cat)]
    from_date = _text(filters.get('from_date'))[:10]
    to_date = _text(filters.get('to_date'))[:10]
    # 141：口径对齐旧 _apply_date_filters——非法日期格式结构化报错
    for key, raw in (('from_date', from_date), ('to_date', to_date)):
        if raw:
            try:
                date.fromisoformat(raw)
            except ValueError:
                raise ValueError(f'{key} 格式应为 YYYY-MM-DD') from None

    code_to_cat = {
        GOV_CATEGORY_CODES[c]: c
        for c in active_targets if c in GOV_CATEGORY_CODES
    }
    counts = {category: 0 for category in targets}

    if code_to_cat:
        tables = DynamicTable.query.filter(DynamicTable.code.in_(list(code_to_cat))).all()
        tid_to_cat = {t.id: code_to_cat[t.code] for t in tables if t.code in code_to_cat}
        if tid_to_cat:
            rec_rows = (
                DynamicRecord.query
                .filter(DynamicRecord.table_id.in_(list(tid_to_cat)))
                .with_entities(DynamicRecord.table_id, DynamicRecord.data, DynamicRecord.created_at)
                .all()
            )
            for tid, raw_data, created_at in rec_rows:
                cat = tid_to_cat.get(tid)
                if not cat:
                    continue
                if from_date or to_date:
                    # 141：口径对齐旧实现——日期筛选只认 record_date，缺日期的行不计入
                    rec_date = ''
                    if raw_data:
                        try:
                            rec_date = _text(_json.loads(raw_data).get('record_date'))[:10]
                        except Exception:
                            rec_date = ''
                    if not rec_date:
                        continue
                    if from_date and (not rec_date or rec_date < from_date):
                        continue
                    if to_date and (not rec_date or rec_date > to_date):
                        continue
                counts[cat] = counts.get(cat, 0) + 1

    titles = _governance_titles()
    buckets = [{'key': category, 'label': titles.get(category, category),
                'count': count} for category, count in counts.items()]
    return {'total': sum(counts.values()), 'buckets': buckets, 'data': {}}


def _handler_governance_ledgers(dims, filters):
    """C-2：治理台账（全部 category）分组统计。"""
    requested = _text(filters.get('category'))
    if requested:
        targets = [requested]
    else:
        targets = list(_governance_titles())
    return _governance_buckets(targets, filters)


def _handler_governance_key_personnel(dims, filters):
    """C-2/094：仅统计刑事人员（服刑人员 + 刑满释放人员，category=criminal）。"""
    requested = _text(filters.get('category'))
    if requested:
        targets = [requested] if requested in KEY_PERSONNEL_CATEGORIES else []
    else:
        targets = list(KEY_PERSONNEL_CATEGORIES)
    return _governance_buckets(targets, filters)


def _grouped_bucket_handler(model, dims, filters, allowed_dims: tuple,
                            default_dim: str) -> dict:
    """通用分组计数：按 dims 分组（多维度以 ' | ' 组合），空值归「未填写」。"""
    dims = list(dims) or [default_dim]
    query = model.query
    for dim in allowed_dims:
        want = _text(filters.get(dim))
        if want:
            query = query.filter(getattr(model, dim) == want)
    columns = [getattr(model, dim) for dim in dims]
    rows = query.with_entities(*columns).all()

    counts = {}
    labels = {}
    for values in rows:
        parts = [_text(value) or '未填写' for value in values]
        key = parts[0] if len(parts) == 1 else ' | '.join(parts)
        labels[key] = parts[0] if len(parts) == 1 else ' · '.join(parts)
        counts[key] = counts.get(key, 0) + 1
    return {'total': len(rows), 'buckets': _buckets_from_counts(counts, labels),
            'data': {}}


def _handler_welfare_jobs(dims, filters):
    """C-3：就业/公益岗按岗位类型或网格分组。"""
    from models import WelfareJob
    return _grouped_bucket_handler(WelfareJob, dims, filters,
                                   ('job_type', 'village_group'), 'job_type')


def _handler_veterans(dims, filters):
    """C-4/082 B-3：退役军人按退役重点群体类别分组（空值归「未填写」）。"""
    from models import Veteran
    from utils.dict_enum import get_enum

    query = Veteran.query
    want = _text(filters.get('key_group_category'))
    if want:
        query = query.filter(Veteran.key_group_category == want)
    rows = query.with_entities(Veteran.key_group_category).all()

    enum_values = list(get_enum('退役重点群体'))
    counts = {value: 0 for value in enum_values}
    for (value,) in rows:
        category = _text(value) or '未填写'
        counts[category] = counts.get(category, 0) + 1

    order = {value: index for index, value in enumerate(enum_values)}
    tail = '未填写'
    keys = sorted(counts, key=lambda key: (order.get(key, len(order)), key != tail, key))
    return {
        'total': len(rows),
        'buckets': [{'key': key, 'label': key, 'count': counts[key]} for key in keys],
        'data': {},
    }


def _handler_party_dues(dims, filters):
    """C-5：党费 H1/H2/年度聚合 + 按月明细（金额保留 2 位小数）。"""
    from models import PartyFee

    raw_year = filters.get('year')
    if raw_year in (None, ''):
        year = datetime.now().year
    else:
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            raise ValueError('year 须为整数年份') from None

    rows = PartyFee.query.filter(PartyFee.fee_year == year).all()
    amounts = {m: 0.0 for m in range(1, 13)}
    paids = {m: 0.0 for m in range(1, 13)}
    for fee in rows:
        month = fee.fee_month
        if not isinstance(month, int) or not 1 <= month <= 12:
            continue
        amounts[month] += float(fee.amount or 0)
        paids[month] += float(fee.paid_amount or 0)

    months = [{'month': m, 'amount': _round2(amounts[m]), 'paid': _round2(paids[m])}
              for m in range(1, 13)]

    def _period(start, end):
        return {'amount': _round2(sum(amounts[m] for m in range(start, end + 1))),
                'paid': _round2(sum(paids[m] for m in range(start, end + 1)))}

    data = {
        'year': year,
        'h1': _period(1, 6),
        'h2': _period(7, 12),
        'annual': _period(1, 12),
        'months': months,
    }
    return {'total': data['annual']['paid'], 'buckets': [], 'data': data}


def _handler_notice_records(dims, filters):
    """notice 公示记录按 notice_type 分组计数（展示模板，不作文档真源）。"""
    from models import NoticeRecord
    from models.notice import NOTICE_TYPE_NAMES

    query = NoticeRecord.query
    for field in ('notice_type', 'period', 'status'):
        want = _text(filters.get(field))
        if want:
            query = query.filter(getattr(NoticeRecord, field) == want)
    rows = query.with_entities(NoticeRecord.notice_type).all()

    counts = {}
    for (value,) in rows:
        key = _text(value) or '未填写'
        counts[key] = counts.get(key, 0) + 1
    labels = {key: NOTICE_TYPE_NAMES.get(key, key) for key in counts}
    return {'total': len(rows), 'buckets': _buckets_from_counts(counts, labels),
            'data': {}}


def _handler_welfare_employment(dims, filters):
    """C-3 扩展（084）：就业状态登记按就业状态/类型/村组分组计数。"""
    from models import WelfareEmployment
    return _grouped_bucket_handler(WelfareEmployment, dims, filters,
                                   ('employment_status', 'employment_type', 'village_group'),
                                   'employment_status')


def _handler_welfare_flex_subsidy(dims, filters):
    """C-3 扩展（084）：灵活就业补贴申报按年度/状态/类型分组计数。"""
    from models import WelfareFlexSubsidy
    return _grouped_bucket_handler(WelfareFlexSubsidy, dims, filters,
                                   ('apply_year', 'apply_status', 'subsidy_type'),
                                   'apply_status')


def _handler_welfare_flex_subsidy_amount(dims, filters):
    """C-3 扩展（084）：灵活就业补贴申报金额合计（Decimal 应用层累加，不走 SQL float SUM）。"""
    from models import WelfareFlexSubsidy
    allowed = ('apply_year', 'apply_status', 'subsidy_type')
    dims = [dim for dim in (dims or []) if dim in allowed]
    query = WelfareFlexSubsidy.query
    for dim in allowed:
        raw = _text(filters.get(dim))
        if not raw:
            continue
        if dim == 'apply_year':
            try:
                raw = int(raw)
            except (TypeError, ValueError):
                raise ValueError('apply_year 格式应为整数') from None
        query = query.filter(getattr(WelfareFlexSubsidy, dim) == raw)

    rows = query.all()
    total_amount = sum((row.amount or Decimal('0.00') for row in rows),
                       Decimal('0.00'))
    counts, amounts, labels = {}, {}, {}
    for row in rows:
        if dims:
            parts = [_text(getattr(row, dim)) or '未填写' for dim in dims]
            key = parts[0] if len(parts) == 1 else ' | '.join(parts)
            label = parts[0] if len(parts) == 1 else ' · '.join(parts)
        else:
            key = label = '合计'
        counts[key] = counts.get(key, 0) + 1
        amounts[key] = amounts.get(key, Decimal('0.00')) + (row.amount or Decimal('0.00'))
        labels[key] = label

    buckets = []
    for bucket in _buckets_from_counts(counts, labels):
        bucket['amount'] = str(amounts.get(bucket['key'], Decimal('0.00')).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP))
        buckets.append(bucket)
    return {
        'total': len(rows),
        'buckets': buckets,
        'data': {
            'row_count': len(rows),
            'total_amount': str(total_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)),
        },
    }


def _handler_governance_petition(dims, filters):
    """C-2 扩展（086）：信访事项按办理状态/诉求类别/来源分组计数。"""
    from models import GovernancePetition
    return _grouped_bucket_handler(GovernancePetition, dims, filters,
                                   ('status', 'category', 'source'), 'status')


def _handler_governance_petition_rate(dims, filters):
    """C-2 扩展（086）：信访受理件数 / 已办结件数 / 办结率（Decimal 两位）。"""
    from models import GovernancePetition
    allowed = ('status', 'category', 'source')
    dims = [dim for dim in (dims or []) if dim in allowed]
    query = GovernancePetition.query

    accept_year = _text(filters.get('accept_year'))
    if accept_year:
        try:
            year = int(accept_year)
        except (TypeError, ValueError):
            raise ValueError('accept_year 格式应为整数') from None
        query = query.filter(GovernancePetition.accept_date >= date(year, 1, 1))
        query = query.filter(GovernancePetition.accept_date <= date(year, 12, 31))
    for dim in ('category', 'source'):
        raw = _text(filters.get(dim))
        if raw:
            query = query.filter(getattr(GovernancePetition, dim) == raw)

    rows = query.all()
    total = len(rows)
    closed = sum(1 for row in rows if _text(row.status) == '已办结')
    rate = (Decimal('0.00') if total == 0 else
            (Decimal(closed) / Decimal(total)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP))

    counts, labels = {}, {}
    for row in rows:
        if dims:
            parts = [_text(getattr(row, dim)) or '未填写' for dim in dims]
            key = parts[0] if len(parts) == 1 else ' | '.join(parts)
            label = parts[0] if len(parts) == 1 else ' · '.join(parts)
        else:
            key = label = '合计'
        counts[key] = counts.get(key, 0) + 1
        labels[key] = label
    return {
        'total': total,
        'buckets': _buckets_from_counts(counts, labels),
        'data': {
            'total': total,
            'closed': closed,
            'rate': str(rate),
        },
    }


# ============ 088 养老保险（软表承载：pension_roster 名册取数） ============

# 软表承载的养老名册列（display_name → 语义列，字段清单-v1 §3.4.2 规范列名）
_PENSION_ROSTER_CODE = 'pension_roster'
_PENSION_STATUS_COLUMN = '参保状态'
_PENSION_CERT_COLUMN = '待遇认证状态'


def _pension_roster_rows():
    """pension_roster 软表名册行 → [{display_name: 存储值}]（未建表/空表 → []）。

    列语义按 display_name 对齐（软表字段 code 由首次上传确认生成，ADR-025 决策 3，
    code 不稳定而规范列名稳定，见字段清单-v1 §3.4.2）；身份标识列不解密，
    指标只用参保/认证状态两类非敏感列，不触 R12 管线。
    """
    from models import DynamicField, DynamicRecord, DynamicTable
    table = DynamicTable.query.filter_by(code=_PENSION_ROSTER_CODE).first()
    if table is None:
        return []
    fields = (DynamicField.query.filter_by(table_id=table.id)
              .order_by(DynamicField.sort_order.asc(), DynamicField.id.asc()).all())
    name_of = {f.code: f.display_name for f in fields}
    if not name_of:
        return []
    rows = []
    for record in DynamicRecord.query.filter_by(table_id=table.id).all():
        try:
            data = json.loads(record.data or '{}')
        except ValueError:
            data = {}
        rows.append({name_of[code]: value for code, value in data.items()
                     if code in name_of})
    return rows


def _pension_rate_data(rows, status_column, insured_value):
    """参保率口径：正常参保行数 / 名册总行数（Decimal 两位，ROUND_HALF_UP）。"""
    total = len(rows)
    insured = sum(1 for row in rows
                  if _text(row.get(status_column)) == insured_value)
    rate = (Decimal('0.00') if total == 0 else
            (Decimal(insured) / Decimal(total)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP))
    return total, insured, rate


def _pension_buckets(rows, column, empty_label=None):
    """按状态列分桶；empty_label 给定时空值计入该桶（展示层兜底，不回填库）。"""
    counts, labels = {}, {}
    for row in rows:
        raw = _text(row.get(column))
        key = raw if raw else (empty_label or '未填写')
        counts[key] = counts.get(key, 0) + 1
        labels[key] = key
    return _buckets_from_counts(counts, labels)


def _pension_physical_rows():
    """读取农保物理大表（MedicalInsurance, insurance_type='rural_pension'）状态列。"""
    from models import MedicalInsurance
    items = MedicalInsurance.query.filter_by(insurance_type='rural_pension').all()
    if not items:
        return []
    return [{
        _PENSION_STATUS_COLUMN: getattr(r, 'insurance_status', '') or '',
        _PENSION_CERT_COLUMN: getattr(r, 'benefit_cert_status', '') or '',
    } for r in items]


def _handler_pension_coverage(dims, filters):
    """088/Step2：养老保险参保率 = 参保状态「正常参保」行数 / 总行数（优先物理大表，空表回退软表）。"""
    rows = _pension_physical_rows() or _pension_roster_rows()
    total, insured, rate = _pension_rate_data(rows, _PENSION_STATUS_COLUMN, '正常参保')
    return {
        'total': total,
        'buckets': _pension_buckets(rows, _PENSION_STATUS_COLUMN),
        'data': {
            'total': total,
            'insured': insured,
            'rate': str(rate),
        },
    }


def _handler_shop_venue_total(dims, filters):
    """085 M-01：场所总数 = shops（固定表）行数；主数据复用 shops，无第三张软表。"""
    from models import Shop
    total = Shop.query.count()
    return {'total': total, 'buckets': {}, 'data': {'total': total}}


def _handler_shop_facility_active(dims, filters):
    """085 M-02：在用设施数 = 软表 shop_facility 中「使用状态=在用」行数。

    列语义按 display_name 对齐（同 _pension_roster_rows 口径：code 不稳定、
    规范列名稳定）；未建表 / 无「使用状态」列 → 0，不回填库。
    """
    from models import DynamicField, DynamicRecord, DynamicTable
    table = DynamicTable.query.filter_by(code='shop_facility').first()
    if table is None:
        return {'total': 0, 'buckets': {}, 'data': {'total': 0, 'active': 0}}
    name_of = {f.code: f.display_name for f in
               DynamicField.query.filter_by(table_id=table.id).all()}
    status_code = next((c for c, n in name_of.items() if n == '使用状态'), None)
    total = 0
    active = 0
    if status_code:
        for record in DynamicRecord.query.filter_by(table_id=table.id).all():
            try:
                data = json.loads(record.data or '{}')
            except ValueError:
                continue
            total += 1
            if str(data.get(status_code, '')).strip() == '在用':
                active += 1
    return {'total': total, 'buckets': {}, 'data': {'total': total, 'active': active}}


def _handler_party_activity(dims, filters):
    """C-1 扩展（083A）：党务活动场次按年度/支部/类型分组，含应到实到合计。

    聚合与明细同源：场次 = 行数、应到/实到合计 = 明细列求和（禁止独立口径二次计算）。
    year 维度从 activity_date 派生（活动表无冗余年份列）。
    """
    from models import PartyActivity
    allowed = ('year', 'branch', 'activity_type')
    dims = [dim for dim in (dims or []) if dim in allowed]
    query = PartyActivity.query

    want_year = _text(filters.get('year'))
    if want_year:
        try:
            year = int(want_year)
        except (TypeError, ValueError):
            raise ValueError('year 格式应为整数') from None
        query = query.filter(PartyActivity.activity_date >= date(year, 1, 1))
        query = query.filter(PartyActivity.activity_date <= date(year, 12, 31))
    for dim in ('branch', 'activity_type'):
        raw = _text(filters.get(dim))
        if raw:
            query = query.filter(getattr(PartyActivity, dim) == raw)

    rows = query.all()
    counts, expected_sums, actual_sums, labels = {}, {}, {}, {}
    for row in rows:
        year_value = str(row.activity_date.year) if row.activity_date else '未填写'
        parts = []
        for dim in dims:
            parts.append(year_value if dim == 'year'
                         else (_text(getattr(row, dim)) or '未填写'))
        key = parts[0] if len(parts) == 1 else ' | '.join(parts)
        labels[key] = parts[0] if len(parts) == 1 else ' · '.join(parts)
        counts[key] = counts.get(key, 0) + 1
        expected_sums[key] = expected_sums.get(key, 0) + (row.expected_count or 0)
        actual_sums[key] = actual_sums.get(key, 0) + (row.actual_count or 0)

    buckets = []
    for bucket in _buckets_from_counts(counts, labels):
        bucket['expected_sum'] = expected_sums.get(bucket['key'], 0)
        bucket['actual_sum'] = actual_sums.get(bucket['key'], 0)
        buckets.append(bucket)
    return {
        'total': len(rows),
        'buckets': buckets,
        'data': {
            'session_count': len(rows),
            'expected_sum': sum(expected_sums.values()),
            'actual_sum': sum(actual_sums.values()),
        },
    }


def _handler_pension_cert_rate(dims, filters):
    """088/Step2：养老待遇认证完成率 = 「已认证」行数 / 总行数（优先物理大表，空表回退软表）。

    空值口径（任务书工作项 7）：认证状态为空按「未认证」计入分母展示、
    不回填库——本 handler 只读，buckets 里空值单独以「未认证」桶呈现。
    """
    rows = _pension_physical_rows() or _pension_roster_rows()
    total, certified, rate = _pension_rate_data(rows, _PENSION_CERT_COLUMN, '已认证')
    unfilled = sum(1 for row in rows if not _text(row.get(_PENSION_CERT_COLUMN)))
    return {
        'total': total,
        'buckets': _pension_buckets(rows, _PENSION_CERT_COLUMN, empty_label='未认证'),
        'data': {
            'total': total,
            'certified': certified,
            'unfilled': unfilled,
            'rate': str(rate),
        },
    }


# ============ 090A 民生诉求（固定表 demand：两跳状态机三指标） ============

def _demand_rows(filters):
    """按 filters 拉取 Demand 行（accept_year 区间 + category/source/status 等值）。"""
    from models import Demand
    query = Demand.query
    accept_year = _text(filters.get('accept_year'))
    if accept_year:
        try:
            year = int(accept_year)
        except (TypeError, ValueError):
            raise ValueError('accept_year 格式应为整数') from None
        query = query.filter(Demand.accept_date >= date(year, 1, 1))
        query = query.filter(Demand.accept_date <= date(year, 12, 31))
    for dim in ('category', 'source', 'status'):
        raw = _text(filters.get(dim))
        if raw:
            query = query.filter(getattr(Demand, dim) == raw)
    return query.all()


def _handler_demand_stats(dims, filters):
    """090A：民生诉求受理件数（按办理状态/诉求类别/来源分组计数）。"""
    from models import Demand
    return _grouped_bucket_handler(Demand, dims, filters,
                                   ('status', 'category', 'source'), 'status')


def _handler_demand_close_rate(dims, filters):
    """090A：办结率 = 已办结 /（已办结 + 不予受理 + 待受理）= 已办结 / 总受理件数。"""
    rows = _demand_rows(filters)
    total = len(rows)
    closed = sum(1 for row in rows if _text(row.status) == '已办结')
    rate = (Decimal('0.00') if total == 0 else
            (Decimal(closed) / Decimal(total)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP))
    counts, labels = {}, {}
    for row in rows:
        key = _text(row.status) or '未填写'
        counts[key] = counts.get(key, 0) + 1
        labels[key] = key
    return {
        'total': total,
        'buckets': _buckets_from_counts(counts, labels),
        'data': {
            'total': total,
            'closed': closed,
            'rate': str(rate),
        },
    }


def _handler_demand_avg_close_days(dims, filters):
    """090A：平均办结天数 = mean(close_date − accept_date)（仅已办结且双日期齐备的行）。

    Decimal 应用层计算（不走 SQL SUM float，welfare_flex_subsidy_amount 先例）；
    无可计算行时 fail-soft 返回 '0.00'。
    """
    rows = _demand_rows(filters)
    closed = [row for row in rows if _text(row.status) == '已办结']
    dated = [row for row in closed if row.close_date and row.accept_date]
    total_days = sum((Decimal((row.close_date - row.accept_date).days)
                      for row in dated), Decimal('0'))
    avg = (Decimal('0.00') if not dated else
           (total_days / Decimal(len(dated))).quantize(
               Decimal('0.01'), rounding=ROUND_HALF_UP))
    return {
        'total': len(rows),
        'buckets': [],
        'data': {
            'total': len(rows),
            'closed': len(closed),
            'dated': len(dated),
            'avg_days': str(avg),
        },
    }


# ============ 090C 环卫工作（软表承载：sanitation_team / sanitation_site 取数） ============

# 软表承载的环卫两册（m031 预置行；列语义按 display_name 对齐，一页纸 v1.2 §1/§2）
_SANITATION_TEAM_CODE = 'sanitation_team'
_SANITATION_SITE_CODE = 'sanitation_site'
_SANITATION_HEADCOUNT_COLUMN = '在岗人数'


def _sanitation_table_rows(code):
    """sanitation_* 软表行 → [{display_name: 存储值}]（未建表/空表 → []）。

    与 _pension_roster_rows 同口径：软表字段 code 由首次上传确认生成不稳定（ADR-025
    决策 3），列语义按 display_name 对齐；指标只用「在岗人数」等非敏感列，不触 R12 管线。
    """
    from models import DynamicField, DynamicRecord, DynamicTable
    table = DynamicTable.query.filter_by(code=code).first()
    if table is None:
        return []
    fields = (DynamicField.query.filter_by(table_id=table.id)
              .order_by(DynamicField.sort_order.asc(), DynamicField.id.asc()).all())
    name_of = {f.code: f.display_name for f in fields}
    if not name_of:
        return []
    rows = []
    for record in DynamicRecord.query.filter_by(table_id=table.id).all():
        try:
            data = json.loads(record.data or '{}')
        except ValueError:
            data = {}
        rows.append({name_of[code]: value for code, value in data.items()
                     if code in name_of})
    return rows


def _handler_sanitation_team_headcount(dims, filters):
    """090C M-01：队伍在岗人数合计 = Σ「在岗人数」（sanitation_team 软表）。

    口径（一页纸 §5 M-01 / §9 V-03）：空值不参与求和；非数值/负数不计入（fail-soft）。
    """
    rows = _sanitation_table_rows(_SANITATION_TEAM_CODE)
    total = len(rows)
    headcount = Decimal('0')
    empty = 0
    for row in rows:
        raw = _text(row.get(_SANITATION_HEADCOUNT_COLUMN))
        if not raw:
            empty += 1
            continue
        try:
            value = Decimal(raw)
            if not value.is_finite():   # P1：NaN / ±Infinity 不得进入比较或 int() 累加
                raise ValueError('非有限数值')
            if value < 0:
                empty += 1
                continue
        except (ValueError, ArithmeticError):
            empty += 1
            continue
        headcount += value
    return {
        'total': total,
        'buckets': [],
        'data': {
            'total': total,
            'sum': int(headcount),
            'empty': empty,
        },
    }


def _handler_sanitation_site_count(dims, filters):
    """090C M-02：点位数量合计 = sanitation_site 软表行数（一页纸 §5 M-02 方案A）。

    「数量/容量」列（SS-004）保留展示但不参与统计（R-04 裁定）。
    """
    rows = _sanitation_table_rows(_SANITATION_SITE_CODE)
    return {
        'total': len(rows),
        'buckets': [],
        'data': {
            'total': len(rows),
        },
    }


def _handler_party_review_grade(dims, filters):
    """C-1 扩展（083A）：民主评议逐人等次按年度/等次分组计数（分布 = 逐人求和）。"""
    from models import PartyActivityReview
    from utils.dict_enum import get_enum
    allowed = ('year', 'grade')
    dims = [dim for dim in (dims or []) if dim in allowed]
    query = PartyActivityReview.query

    want_year = _text(filters.get('year'))
    if want_year:
        try:
            query = query.filter(PartyActivityReview.year == int(want_year))
        except (TypeError, ValueError):
            raise ValueError('year 格式应为整数') from None
    want_grade = _text(filters.get('grade'))
    if want_grade:
        query = query.filter(PartyActivityReview.grade == want_grade)

    rows = query.with_entities(PartyActivityReview.year, PartyActivityReview.grade).all()
    counts, labels = {}, {}
    for row_year, row_grade in rows:
        parts = [(str(row_year) if row_year else '未填写') if dim == 'year'
                 else (_text(row_grade) or '未填写') for dim in dims]
        key = parts[0] if len(parts) == 1 else ' | '.join(parts)
        labels[key] = parts[0] if len(parts) == 1 else ' · '.join(parts)
        counts[key] = counts.get(key, 0) + 1

    if dims == ['grade'] or (dims and 'grade' in dims and 'year' not in dims):
        # 等次单维分布按字典序排列（与 _handler_veterans 同法）
        enum_values = list(get_enum('民主评议等次'))
        order = {value: index for index, value in enumerate(enum_values)}
        tail = '未填写'
        keys = sorted(counts, key=lambda k: (order.get(k, len(order)), k != tail, k))
        buckets = [{'key': k, 'label': labels.get(k, k), 'count': counts[k]} for k in keys]
    else:
        buckets = _buckets_from_counts(counts, labels)
    return {
        'total': len(rows),
        'buckets': buckets,
        'data': {
            'review_count': len(rows),
        },
    }


# ============ 089 文明实践站（软表承载：civilization_activity 活动记录取数） ============

# 软表承载的活动记录列（display_name → 语义列，一页纸 v1.2 §1 规范列名）
_CIV_ACTIVITY_CODE = 'civilization_activity'
_CIV_DATE_COLUMN = '活动日期'
_CIV_COUNT_COLUMN = '参与人数'
_CIV_TYPE_COLUMN = '活动类型'


def _civilization_activity_rows():
    """civilization_activity 软表活动行 → [{display_name: 存储值}]（未建表/空表 → []）。

    列语义按 display_name 对齐（软表字段 code 由首次上传确认生成，ADR-025 决策 3，
    code 不稳定而一页纸规范列名稳定）；活动记录全部为非敏感列，不触 R12 管线。
    """
    from models import DynamicField, DynamicRecord, DynamicTable
    table = DynamicTable.query.filter_by(code=_CIV_ACTIVITY_CODE).first()
    if table is None:
        return []
    fields = (DynamicField.query.filter_by(table_id=table.id)
              .order_by(DynamicField.sort_order.asc(), DynamicField.id.asc()).all())
    name_of = {f.code: f.display_name for f in fields}
    if not name_of:
        return []
    rows = []
    for record in DynamicRecord.query.filter_by(table_id=table.id).all():
        try:
            data = json.loads(record.data or '{}')
        except ValueError:
            data = {}
        rows.append({name_of[code]: value for code, value in data.items()
                     if code in name_of})
    return rows


def _civilization_year_rows(rows, filters):
    """year 筛选（整数，非法值报错口径同 party handler）→ 过滤后的活动行。

    活动日期为软表 date 列，存储值 'YYYY-MM-DD'；年份取前 4 位，
    空/异形日期不落入任何年度（未填写，不参与年度求和）。
    """
    want_year = _text(filters.get('year'))
    if not want_year:
        return rows
    try:
        year = int(want_year)
    except (TypeError, ValueError):
        raise ValueError('year 格式应为整数') from None
    prefix = '%d-' % year
    return [row for row in rows
            if _text(row.get(_CIV_DATE_COLUMN)).startswith(prefix)]


def _civilization_buckets(rows, column):
    """按列分桶（活动类型分布口径；空值计「未填写」桶）。"""
    counts, labels = {}, {}
    for row in rows:
        key = _text(row.get(column)) or '未填写'
        counts[key] = counts.get(key, 0) + 1
        labels[key] = key
    return _buckets_from_counts(counts, labels)


def _handler_civilization_sessions(dims, filters):
    """089：年度场次 = 指定年度内文明实践活动行数（M-01，聚合 = 明细行数）。"""
    rows = _civilization_year_rows(_civilization_activity_rows(), filters)
    return {
        'total': len(rows),
        'buckets': _civilization_buckets(rows, _CIV_TYPE_COLUMN),
        'data': {
            'sessions': len(rows),
        },
    }


def _handler_civilization_person_visits(dims, filters):
    """089：年度人次合计 = Σ 参与人数（M-02，Q-B 裁定：逐场累加不去重）。"""
    rows = _civilization_year_rows(_civilization_activity_rows(), filters)
    person_visits = 0
    for row in rows:
        try:
            person_visits += int(row.get(_CIV_COUNT_COLUMN) or 0)
        except (TypeError, ValueError):
            continue  # 非整数参与人数不计入人次（展示层兜底，不回填库）
    return {
        'total': len(rows),
        'buckets': _civilization_buckets(rows, _CIV_TYPE_COLUMN),
        'data': {
            'person_visits': person_visits,
        },
    }


# ============ 指标注册表（单一真源） ============

METRIC_REGISTRY = {
    'dashboard_counts': {
        'label': '看板核心计数',
        'definition': 'T17-1 双轨口径：活跃居民（排除死亡/流出）+ 标签/户属性/高龄动态口径',
        'dims': [],
        'source': ['residents', 'party_members', 'dibao', 'disabled',
                   'special_support', 'welfare_jobs', 'veteran', 'subsidy_records',
                   'resident_tag', 'care_target', 'visit_records'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': None,
        'output': 'object',
        'handler': _handler_dashboard_counts,
    },
    'stats_table': {
        'label': '一老一小统计表',
        'definition': 'T17-3：行=高龄/困难/留守/困境四分类，列=网格×年龄段（0-17/18-59/60-79/80+）',
        'dims': ['grid'],
        'source': ['residents', 'resident_tag', 'care_target'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'report',
        'output': 'table',
        'handler': _handler_stats_table,
    },
    'party_members': {
        'label': '党员分类统计',
        'definition': ('C-1：按身份/流动/在职/在册/支部及三分类组合（退休>流动>在家）分组，'
                       '默认排除已转出（R-080-04）；两列均空兜底为在家'),
        'dims': ['member_identity', 'member_mobility', 'employment_status',
                 'party_status', 'branch', 'party_category'],
        'source': ['party_members'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'party',
        'output': 'buckets',
        'handler': _handler_party_members,
    },
    'party_summary': {
        'label': '党员情况汇总',
        'definition': 'C-1 兼容：复刻 /party/api/summary（在册/正式/预备/流动/退休/已转出/自动关联/建档/支部）',
        'dims': [],
        'source': ['party_members', 'residents'],
        'aggregator': _SQL,
        # 兼容指标：旧 /party/api/summary 无缓存（写后立即可见），TTL=0 关闭缓存
        'cache_ttl': 0,
        'sensitive': False,
        'permission': 'party',
        'output': 'object',
        'handler': _handler_party_summary,
    },
    'governance_ledgers': {
        'label': '治理台账分类统计',
        'definition': 'C-2：按治理台账 category 分组计数，标题取 CATEGORY_CONFIG',
        'dims': ['category'],
        'source': ['dynamic_table', 'dynamic_record'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'mediation',
        'output': 'buckets',
        'handler': _handler_governance_ledgers,
    },
    'governance_key_personnel': {
        'label': '治理重点人员统计',
        'definition': 'C-2/094：统计刑事人员台账（服刑人员 + 刑满释放人员，category=criminal）',
        'dims': ['category'],
        'source': ['dynamic_table', 'dynamic_record'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'mediation',
        'output': 'buckets',
        'handler': _handler_governance_key_personnel,
    },
    'welfare_jobs': {
        'label': '就业/公益岗统计',
        'definition': 'C-3：按岗位类型或网格分组计数',
        'dims': ['job_type', 'village_group'],
        'source': ['welfare_jobs'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'welfare',
        'output': 'buckets',
        'handler': _handler_welfare_jobs,
    },
    'veterans': {
        'label': '退役军人重点群体统计',
        'definition': 'C-4/082 B-3：按退役重点群体类别（字典「退役重点群体」）分组，空值归未填写',
        'dims': ['key_group_category'],
        'source': ['veteran'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'veteran',
        'output': 'buckets',
        'handler': _handler_veterans,
    },
    'party_dues': {
        'label': '党费聚合',
        'definition': 'C-5：按年度聚合党费 H1/H2/年度应缴与实缴，并输出按月明细',
        'dims': ['year'],
        'source': ['party_fees', 'party_members'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'party',
        'output': 'object',
        'handler': _handler_party_dues,
    },
    'notice_records': {
        'label': '公示记录统计',
        'definition': 'notice 模板记录计数：按公示类型分组（展示模板，非业务主数据真源）',
        'dims': ['notice_type'],
        'source': ['notice_records', 'notice_templates'],
        'aggregator': _TEMPLATE,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'notice',
        'output': 'buckets',
        'handler': _handler_notice_records,
    },
    # ── 084 就业服务（C-3 扩展；分组计数 + 金额合计）──
    'welfare_employment': {
        'label': '就业状态分布',
        'definition': 'C-3 扩展：就业服务登记按就业状态/类型/组别分组',
        'dims': ['employment_status', 'employment_type', 'village_group'],
        'source': ['welfare_employment'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'welfare',
        'output': 'buckets',
        'handler': _handler_welfare_employment,
    },
    'welfare_flex_subsidy': {
        'label': '灵活就业补贴申报',
        'definition': 'C-3 扩展：补贴申报按申报年度/状态/类型分组计数',
        'dims': ['apply_year', 'apply_status', 'subsidy_type'],
        'source': ['welfare_flex_subsidy'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'welfare',
        'output': 'buckets',
        'handler': _handler_welfare_flex_subsidy,
    },
    'welfare_flex_subsidy_amount': {
        'label': '灵活就业补贴金额合计',
        'definition': 'C-3 扩展：申报金额按年度/状态/类型汇总（Decimal 应用层累加，保留 2 位）',
        'dims': ['apply_year', 'apply_status', 'subsidy_type'],
        'source': ['welfare_flex_subsidy'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'welfare',
        'output': 'object',
        'handler': _handler_welfare_flex_subsidy_amount,
    },
    # ── 086 信访维稳（C-2 扩展；分组计数 + 办结率）──
    'governance_petition': {
        'label': '信访事项统计',
        'definition': 'C-2 扩展：信访事项按办理状态/诉求类别/来源分组计数',
        'dims': ['status', 'category', 'source'],
        'source': ['governance_petition'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'mediation',
        'output': 'buckets',
        'handler': _handler_governance_petition,
    },
    'governance_petition_rate': {
        'label': '信访办结率',
        'definition': 'C-2 扩展：受理件数/已办结件数/办结率（rate=closed/total，Decimal 两位）',
        'dims': ['status', 'category', 'source'],
        'source': ['governance_petition'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'mediation',
        'output': 'object',
        'handler': _handler_governance_petition_rate,
    },
    # ── 088 养老保险（软表承载：pension_roster 名册，列语义按 display_name 对齐）──
    'pension_coverage': {
        'label': '养老保险参保率',
        'definition': ('088：养老保险参保率 = 参保状态「正常参保」行数 / 养老保险参保名册'
                       '（软表 pension_roster）总行数，Decimal 两位；未建册时为 0.00'),
        'dims': [],
        'source': ['dynamic_records'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'pension',
        'output': 'object',
        'handler': _handler_pension_coverage,
    },
    'pension_cert_rate': {
        'label': '养老待遇认证完成率',
        'definition': ('088：养老待遇认证完成率 = 「已认证」行数 / 名册总行数（Decimal 两位）；'
                       '认证状态为空按「未认证」计入分母展示，不回填库'),
        'dims': [],
        'source': ['dynamic_records'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'pension',
        'output': 'object',
        'handler': _handler_pension_cert_rate,
    },
    # ── 085 场所/设施（M-01 场所总数 / M-02 在用设施数，一页纸 §3 口径）──
    'shop_venue_total': {
        'label': '场所总数',
        'definition': ('085 M-01：场所总数 = shops（店铺信息固定表）行数；'
                       '场所主数据复用 shops，不建第三张软表'),
        'dims': [],
        'source': ['shops'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'shop',
        'output': 'object',
        'handler': _handler_shop_venue_total,
    },
    'shop_facility_active': {
        'label': '在用设施数',
        'definition': ('085 M-02：在用设施数 = 基础设施分布（软表 shop_facility）中'
                       '「使用状态=在用」的行数；未建表/无该列时为 0（与 088 未建册口径一致）'),
        'dims': [],
        'source': ['dynamic_records'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'shop',
        'output': 'object',
        'handler': _handler_shop_facility_active,
    },
    # ── 083A 党务活动（C-1 扩展；场次 + 应到实到合计 + 评议等次分布）──
    'party_activity': {
        'label': '党务活动场次统计',
        'definition': ('C-1 扩展：党务活动按年度/支部/类型分组计数，含应到实到合计'
                       '（聚合 = 明细求和；year 从 activity_date 派生）'),
        'dims': ['year', 'branch', 'activity_type'],
        'source': ['party_activity'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'party',
        'output': 'buckets',
        'handler': _handler_party_activity,
    },
    'party_review_grade': {
        'label': '民主评议等次分布',
        'definition': 'C-1 扩展：民主评议逐人等次按年度/等次分组计数（分布 = 逐人记录求和）',
        'dims': ['year', 'grade'],
        'source': ['party_activity_review'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'party',
        'output': 'buckets',
        'handler': _handler_party_review_grade,
    },
    # ── 090A 民生诉求（固定表 demand：受理件数分组 + 办结率 + 平均办结天数）──
    'demand_stats': {
        'label': '民生诉求受理统计',
        'definition': '090A：民生诉求按办理状态/诉求类别/来源分组计数',
        'dims': ['status', 'category', 'source'],
        'source': ['demand'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'demand',
        'output': 'buckets',
        'handler': _handler_demand_stats,
    },
    'demand_close_rate': {
        'label': '民生诉求办结率',
        'definition': ('090A：办结率 = 已办结 /（已办结 + 不予受理 + 待受理）'
                       '（rate=closed/total，Decimal 两位）'),
        'dims': ['status', 'category', 'source'],
        'source': ['demand'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'demand',
        'output': 'object',
        'handler': _handler_demand_close_rate,
    },
    'demand_avg_close_days': {
        'label': '民生诉求平均办结天数',
        'definition': ('090A：已办结且受理/办结日期齐备的诉求 mean(close_date−accept_date)，'
                       'Decimal 应用层计算（不走 SQL SUM float），两位小数'),
        'dims': ['status', 'category', 'source'],
        'source': ['demand'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'demand',
        'output': 'object',
        'handler': _handler_demand_avg_close_days,
    },
    # ── 090C 环卫工作（软表承载：sanitation_team/site，M-01 Σ在岗人数 / M-02 点位行数）──
    'sanitation_team_headcount': {
        'label': '环卫队伍在岗人数合计',
        'definition': ('090C：队伍在岗人数合计 = Σ「在岗人数」（sanitation_team 软表，'
                       '一页纸 §5 M-01）；空值/非数值/负数不参与求和；未建表时为 0'),
        'dims': [],
        'source': ['dynamic_records'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'sanitation',
        'output': 'object',
        'handler': _handler_sanitation_team_headcount,
    },
    'sanitation_site_count': {
        'label': '环卫设施点位数量',
        'definition': ('090C：点位数量合计 = sanitation_site 软表行数（一页纸 §5 M-02 '
                       '方案A；「数量/容量」列保留展示不参与统计）；未建表时为 0'),
        'dims': [],
        'source': ['dynamic_records'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'sanitation',
        'output': 'object',
        'handler': _handler_sanitation_site_count,
    },
    # ── 089 文明实践站（软表承载：civilization_activity 活动记录，列语义按 display_name 对齐）──
    'civilization_sessions': {
        'label': '文明实践活动年度场次',
        'definition': ('089：年度场次 = 指定年度内文明实践活动记录（软表 civilization_activity）'
                       '行数；year 筛选非法值报错，未建册时为 0'),
        'dims': [],
        'source': ['dynamic_records'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'civilization',
        'output': 'object',
        'handler': _handler_civilization_sessions,
    },
    'civilization_person_visits': {
        'label': '文明实践活动年度人次',
        'definition': ('089：年度人次合计 = Σ 参与人数（Q-B 裁定：逐场累加不去重，软表不关联'
                       '实名）；非整数参与人数不计入，未建册时为 0'),
        'dims': [],
        'source': ['dynamic_records'],
        'aggregator': _SQL,
        'cache_ttl': 45,
        'sensitive': False,
        'permission': 'civilization',
        'output': 'object',
        'handler': _handler_civilization_person_visits,
    },
}


# ============ 缓存版本（固定口径版本 + 字典内容签名） ============

def _dict_signature() -> str:
    """字典内容签名：DictCategory/DictItem 的 active 内容 sha256 前 12 位。

    无 app 上下文（模块导入期/单测无夹具）→ 固定回退 'nodb'；
    字典表缺失等可恢复异常同样回退（避免统计入口整体不可用）。
    """
    try:
        from flask import g, has_app_context, has_request_context
        if not has_app_context():
            return 'nodb'
        # 请求级缓存：同一请求内多次取数只算一次签名（缓存命中也不重复 JOIN）；
        # 无请求上下文（测试/脚本）不缓存，保证字典变更可被立即观测。
        if has_request_context():
            cached = getattr(g, '_metrics_dict_signature', None)
            if cached is not None:
                return cached
        from models import db, DictCategory, DictItem
        rows = (db.session.query(DictCategory.name, DictCategory.id,
                                 DictItem.label, DictItem.value, DictItem.sort_order)
                .join(DictItem, DictItem.category_id == DictCategory.id)
                .filter(DictCategory.is_active.is_(True), DictItem.is_active.is_(True))
                .order_by(DictCategory.id, DictItem.sort_order, DictItem.id).all())
        blob = json.dumps([list(row) for row in rows], ensure_ascii=False, sort_keys=True)
        signature = hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]
        if has_request_context():
            g._metrics_dict_signature = signature
        return signature
    except Exception:
        return 'nodb'


# ============ 引擎 ============

def _normalize_dims(raw_dims) -> list:
    """dims 规范化：字符串按逗号切分；去空、去重、保持顺序。"""
    if raw_dims is None:
        return []
    if isinstance(raw_dims, str):
        parts = raw_dims.split(',')
    elif isinstance(raw_dims, (list, tuple)):
        parts = list(raw_dims)
    else:
        parts = [raw_dims]
    dims = []
    seen = set()
    for part in parts:
        value = _text(part)
        if value and value not in seen:
            seen.add(value)
            dims.append(value)
    return dims


def _normalize_filters(raw_filters) -> dict:
    """filters 规范化：只保留非空值（None/空串/空容器丢弃）。"""
    if raw_filters is None:
        return {}
    if not isinstance(raw_filters, dict):
        raise ValueError('filters 必须是 dict')
    filters = {}
    for key, value in raw_filters.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
        elif isinstance(value, (list, tuple, dict)) and not value:
            continue
        filters[key] = value
    return filters


def _cache_key(metric_key: str, dims: list, filters: dict, cache_version: str) -> str:
    """缓存 key = metric_key + dims + filters + cache_version。

    兼容既有缓存 key（T17-2/t17_cache 验收）：dashboard_counts 与 stats_table
    沿用原 ``dashboard:counts`` / ``stats_table:<grid|all>`` 形态；其余指标使用
    ``metrics:<key>:<dims>:<filters>:<version>``。
    """
    from utils.agg_cache import make_key
    if metric_key == 'dashboard_counts':
        return make_key('dashboard', 'counts')
    if metric_key == 'stats_table':
        return make_key('stats_table', filters.get('grid') or 'all')
    payload = json.dumps(filters, sort_keys=True, ensure_ascii=False, default=str)
    return make_key('metrics', metric_key, '|'.join(dims), payload, cache_version)


class MetricsEngine:
    """统计口径引擎：唯一取数入口 + 缓存 + 脱敏。"""

    @classmethod
    def get_metric(cls, metric_key: str) -> dict:
        """按 metric_key 取注册表条目（路由读 permission 用）；未知 → ValueError。"""
        metric = METRIC_REGISTRY.get(metric_key)
        if metric is None:
            raise ValueError(f'未知指标: {metric_key}')
        return metric

    @classmethod
    def query(cls, metric_key: str, dims=None, filters=None, format='json') -> dict:
        """统一取数：校验 → 规范化 → 缓存 → 聚合 → 脱敏 → MetricResult。"""
        metric = cls.get_metric(metric_key)

        if str(format).strip().lower() != 'json':
            raise ValueError(f'不支持的输出格式: {format}（当前仅支持 json）')

        filters = _normalize_filters(filters)

        if dims is None:
            default_dims = list(metric['dims'])[:1]
            normalized_dims = list(default_dims)
        else:
            normalized_dims = _normalize_dims(dims)
            allowed = list(metric['dims'])
            illegal = [dim for dim in normalized_dims if dim not in allowed]
            if illegal:
                allowed_text = '、'.join(allowed) if allowed else '（无）'
                raise ValueError(f'指标 {metric_key} 不支持维度: '
                                 f'{"、".join(illegal)}；允许值: {allowed_text}')

        cache_version = f'{CACHE_VERSION}:{_dict_signature()}'
        key = _cache_key(metric_key, normalized_dims, filters, cache_version)

        ttl = metric['cache_ttl']
        from utils.agg_cache import get, put
        # 兼容 key（dashboard/stats_table 沿用旧 key）额外挂「口径版本守卫」：
        # 字典/口径版本变化 → 守卫 key 不存在 → 视为未命中并重算（验收 5）。
        guard_key = f'{key}|guard:{cache_version}'
        cached = get(key) if ttl > 0 else None
        if cached is not None and ttl > 0 and get(guard_key) is None:
            cached = None
        if cached is None:
            cached = metric['aggregator'].aggregate(metric, normalized_dims, filters)
            if not isinstance(cached, dict):
                cached = {}
            if ttl > 0:
                put(key, cached, ttl)
                put(guard_key, True, ttl)

        aggregated = {
            'total': cached.get('total', 0),
            'buckets': cached.get('buckets') or [],
            'data': cached.get('data') or {},
        }
        masked = contains_identity_keys(aggregated)
        aggregated = mask_metric_payload(copy.deepcopy(aggregated),
                                        can_view_sensitive=False)

        source = metric['source']
        sources = [source] if isinstance(source, str) else list(source)

        result = MetricResult(
            metric_key=metric_key,
            label=metric['label'],
            definition=metric['definition'],
            total=aggregated['total'],
            buckets=aggregated['buckets'],
            data=aggregated['data'],
            sources=sources,
            cache_ttl=metric['cache_ttl'],
            cache_version=cache_version,
            generated_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            masked=masked,
            dims=normalized_dims,
            filters=filters,
        )
        return result.to_dict()
