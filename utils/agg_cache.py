# -*- coding: utf-8 -*-
"""utils.agg_cache — 进程内聚合缓存（T17-2，详细设计 v1.1 §5.2 L961 落位）。

规格（真源：详细设计 L961 + §2.3 口径映射表 L225-229）：
- 进程内字典缓存 `/api/dashboard/stats` 聚合结果，**TTL=45s**
  （与前端 60s 轮询对齐区间 30–60s 内取值）
- key = 「口径版本 + 参数」（CACHE_VERSION 内嵌，口径变更须升版本号）
- 写操作**不主动失效**（TTL 到期自然刷新，简化口径已评审通过）
- 缓存内容**不含敏感明文**（仅计数）；角色不分 key（计数对全体登录用户一致；
  若未来缓存含明细，须按 can_view_sensitive 分 key）

owner map：dashboard 聚合计数唯一落本模块（routes/ 只接线，禁在路由内重写口径）。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from sqlalchemy import func

from .care_age import elderly_threshold, parse_override
from .resident_flow import exclude_outflow
# 081：标签集/户属性/年龄段分箱口径以指标注册表为单一真源（metrics 不在模块级
# 反向 import agg_cache，仅在 handler 内延迟 import，故此处无循环导入）。
from .metrics import (ROSTER_CATEGORIES, HOUSEHOLD_HARD_PROPERTIES,
                      POVERTY_ALLEVIATED_PROPERTY, AGE_DISTRIBUTION_BINS,
                      AGE_STRUCTURE_LABELS, AGE_STRUCTURE_BINS,
                      STATS_AGE_BANDS as _METRICS_STATS_AGE_BANDS)

# 口径版本：口径映射/缓存键语义变更时递增（对齐详细设计「key=口径版本+参数」）
CACHE_VERSION = 'v1.2'
CACHE_TTL = 45  # 秒，与前端 60s 轮询对齐（30–60s 区间内取值）

_store = {}  # key -> (monotonic_deadline, value)
_lock = threading.Lock()


def make_key(*parts) -> str:
    """缓存 key = 口径版本 + 参数段（如 'v1.1:dashboard:counts'）。"""
    return ':'.join([CACHE_VERSION] + [str(p) for p in parts])


def get(key):
    """取缓存；已过期条目自动删除并返回 None。"""
    with _lock:
        item = _store.get(key)
        if item is None:
            return None
        deadline, value = item
        if time.monotonic() > deadline:
            _store.pop(key, None)
            return None
        return value


def put(key, value, ttl: float | None = None):
    """写缓存；ttl 缺省用 CACHE_TTL。命名 put 规避内置 set 遮蔽。"""
    ttl = CACHE_TTL if ttl is None else ttl
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def invalidate(key=None):
    """失效缓存。当前无调用方（写操作不主动失效，详细设计 L961 已评审）；预留。"""
    with _lock:
        if key is None:
            _store.clear()
        else:
            _store.pop(key, None)


def get_or_compute(key, fn):
    """缓存命中直接返回；未命中（或过期）在**锁外**执行 fn 计算后写入。

    计算在锁外（threading.Lock 非重入，避免自死锁）；计算完成后锁内
    二次检查（防 thundering herd：他线程已写入则用其值），再落缓存。
    fn 须在 Flask app context 内执行（请求链路天然满足；测试中须在
    app.app_context() 内调用）。
    """
    value = get(key)
    if value is not None:
        return value
    computed = fn()
    with _lock:
        item = _store.get(key)
        if item is not None and time.monotonic() <= item[0]:
            return item[1]  # 计算期间他线程已写入（且未过期），以其为准
        _store[key] = (time.monotonic() + CACHE_TTL, computed)
    return computed


# ============ dashboard 聚合计数（T17-1 双轨口径落位） ============

def _active_resident_ids() -> set:
    """活跃居民 id 集（排除死亡 + 流出；A4/居民流动性唯一口径 resident_flow）。"""
    from models import Resident
    q = exclude_outflow(
        Resident.query.filter(Resident.status != 'deceased')
    ).with_entities(Resident.id)
    return {rid for (rid,) in q.all()}


def _tag_id_set(code: str, active_ids: set) -> set:
    """resident_tag 标签居民 id 集（A5 政策性口径：tag_code + status=有效）。

    JOIN 活跃居民集双保险（死亡/流出软失效标签已置失效，此处再过滤）。
    与 _tag_count 同源（计数 = len(id 集)）。
    """
    if not active_ids:
        return set()
    from models import db, Resident, ResidentTag
    active_sub = Resident.query.filter(Resident.id.in_(active_ids)).with_entities(Resident.id).subquery()
    rows = db.session.query(ResidentTag.resident_id).join(
        active_sub, ResidentTag.resident_id == active_sub.c.id
    ).filter(
        ResidentTag.tag_code == code,
        ResidentTag.status == '有效',
    ).all()
    return {rid for (rid,) in rows}


def _tag_count(code: str, active_ids: set) -> int:
    """resident_tag 标签计数（A5 政策性口径：tag_code + status=有效）。"""
    return len(_tag_id_set(code, active_ids))


def _child_id_set(active_ids: set) -> set:
    """儿童 id 集（一老一小·小：CareTarget target_type='儿童' + status='有效'，含留守/困境）。

    与 _elderly_id_set 的 override 儿童剔除正交：本集为政策性入册儿童（T13 口径）。
    """
    if not active_ids:
        return set()
    from models import CareTarget
    rows = CareTarget.query.filter(
        CareTarget.target_type == '儿童',
        CareTarget.status == '有效',
    ).with_entities(CareTarget.resident_id).all()
    return {rid for (rid,) in rows} & active_ids


def _hard_family_id_set(active_ids: set) -> set:
    """困难家庭 id 集（居民户属性 household_property：脱贫户/监测户/五保户）。

    死亡/流出经 exclude_outflow 排除（A4 居民流动性唯一口径）。
    """
    if not active_ids:
        return set()
    from models import Resident
    rows = exclude_outflow(
        Resident.query.filter(
            Resident.status != 'deceased',
            Resident.household_property.in_(HOUSEHOLD_HARD_PROPERTIES),
        )
    ).with_entities(Resident.id).all()
    return {rid for (rid,) in rows} & active_ids


def _visit_id_set(active_ids: set) -> set:
    """走访对象 id 集（VisitRecord 有走访记录的居民去重，不限定走访类型/时间）。"""
    if not active_ids:
        return set()
    from models import db, VisitRecord
    rows = db.session.query(VisitRecord.resident_id).distinct().all()
    return {rid for (rid,) in rows} & active_ids


def _subsidy_id_set(active_ids: set) -> set:
    """补贴对象 id 集（SubsidyRecord 发放记录去重，status='正常'；停发不列）。"""
    if not active_ids:
        return set()
    from models import db, SubsidyRecord
    rows = db.session.query(SubsidyRecord.resident_id).filter(
        SubsidyRecord.status == '正常',
    ).distinct().all()
    return {rid for (rid,) in rows} & active_ids


def _elderly_threshold_from_dict() -> int:
    """高龄阈值：DictCategory「高龄老人阈值」优先，缺失/异常回退 care_age（默认 80）。"""
    from models.dict_model import DictItem, DictCategory
    try:
        cat = DictCategory.query.filter_by(name='高龄老人阈值').first()
        if cat:
            item = DictItem.query.filter_by(category_id=cat.id).first()
            if item:
                return int(item.value)
    except (ValueError, TypeError):
        pass
    return elderly_threshold()


def _elderly_id_set(active_ids: set) -> set:
    """高龄居民 id 集（详细设计 §2.3 L225）：
    birth_date 动态计算（julianday 精确日差，阈值可配默认 80）
    + care_target.age_class_override 覆盖值优先（30 天保护期，超期失效回退）。
    公式：(A ∪ override_高龄) - override_儿童
    """
    if not active_ids:
        return set()
    from models import db, Resident
    th = _elderly_threshold_from_dict()
    # 动态高龄集 A：精确日差（与 care_age.calculate_age 数学等价：
    # days//365.2425 >= th  ⟺  days >= th*365.2425，th 为整数）
    dyn = exclude_outflow(
        Resident.query.filter(
            Resident.status != 'deceased',
            Resident.birth_date.isnot(None),
            (func.julianday(func.date('now', 'localtime')) - func.julianday(Resident.birth_date))
            >= th * 365.2425,
        )
    ).with_entities(Resident.id)
    a_ids = {rid for (rid,) in dyn.all()}

    # override 集（CareTarget 有效 + age_class_override 非空，经 parse_override 30 天保护）
    from models import CareTarget
    override_rows = CareTarget.query.filter(
        CareTarget.status == '有效',
        CareTarget.age_class_override.isnot(None),
        CareTarget.age_class_override != '',
    ).with_entities(CareTarget.resident_id, CareTarget.age_class_override).all()
    override_elderly = set()
    override_child = set()
    for rid, ov in override_rows:
        parsed = parse_override(ov)
        if not parsed:
            continue
        cls = parsed.get('override_class')
        if cls == '高龄':
            override_elderly.add(rid)
        elif cls == '儿童':
            override_child.add(rid)
    override_elderly &= active_ids  # 仅统计活跃居民（软失效/死亡已排除）
    override_child &= active_ids
    return (a_ids | override_elderly) - override_child


def _elderly_count(active_ids: set) -> int:
    """高龄老人数（与 _elderly_id_set 同源）。"""
    return len(_elderly_id_set(active_ids))


def _age_distribution(active_ids: set) -> list:
    """年龄分布（保留既有 7 段 bin 与年差口径，仅换活跃居民过滤；纯计数）。"""
    if not active_ids:
        return [0, 0, 0, 0, 0, 0, 0]
    from models import db, Resident
    rows = Resident.query.filter(Resident.id.in_(active_ids), Resident.birth_date.isnot(None))\
        .with_entities(Resident.birth_date).all()
    bins = AGE_DISTRIBUTION_BINS
    now = datetime.now()
    dist = [0, 0, 0, 0, 0, 0, 0]
    for (bd,) in rows:
        age = now.year - bd.year
        for i, (lo, hi) in enumerate(bins):
            if lo <= age <= hi:
                dist[i] += 1
                break
    return dist


def _age_structure(active_ids: set) -> dict:
    """首页人口结构柱状（070）：6 段真实分箱 + 劳动/非劳动年龄口径。

    分箱对齐 spec-01：0-17 / 18-35 / 36-59 / 60-79 / 80-89 / 90+；
    劳动年龄 = 18-35 + 36-59（样图绿色段），其余为非劳动年龄。
    """
    labels = list(AGE_STRUCTURE_LABELS)
    bins = AGE_STRUCTURE_BINS
    if not active_ids:
        return {'labels': labels, 'counts': [0] * len(labels), 'labor': 0, 'nonlabor': 0, 'unknown': 0}
    from models import Resident
    rows = Resident.query.filter(Resident.id.in_(list(active_ids)), Resident.birth_date.isnot(None))\
        .with_entities(Resident.birth_date).all()
    now = datetime.now()
    counts = [0] * len(labels)
    for (bd,) in rows:
        age = now.year - bd.year
        for i, (lo, hi) in enumerate(bins):
            if lo <= age <= hi:
                counts[i] += 1
                break
    labor = counts[1] + counts[2]
    return {'labels': labels, 'counts': counts,
            'labor': labor, 'nonlabor': sum(counts) - labor,
            'unknown': max(len(active_ids) - sum(counts), 0)}


def _key_object_structure(active_ids: set) -> dict:
    """首页人口构成（070）：重点对象去重后按优先级分桶，百分比合计 100%。

    桶序：低保·残疾 → 高龄老人 → 留守·困境儿童 → 其他对象。
    每桶互斥（后桶扣除前桶），总人数 = 重点对象并集；数据全部来自实库。
    """
    from models import Resident, Dibao, Disabled, CareTarget, SpecialSupport
    active = set(active_ids)
    if not active:
        return {'total_people': 0, 'total_households': 0, 'items': []}

    def _linked_ids(model):
        rows = model.query.filter(model.status == '有效', model.resident_id.isnot(None))\
            .with_entities(model.resident_id).all()
        return {rid for (rid,) in rows if rid} & active

    dibao = _linked_ids(Dibao)
    disabled = _linked_ids(Disabled)
    care = _linked_ids(CareTarget)

    # special_support 无 resident_id：按户主身份证 hash 关联居民（与弱势台账同口径）
    hashes = [h for (h,) in SpecialSupport.query.filter(
        SpecialSupport.status == '有效',
        SpecialSupport.household_id_card_hash.isnot(None),
    ).with_entities(SpecialSupport.household_id_card_hash).all() if h]
    special = set()
    if hashes:
        special = {rid for (rid,) in Resident.query.filter(
            Resident.id.in_(list(active)),
            Resident.id_card_hash.in_(hashes),
        ).with_entities(Resident.id).all()}

    elderly = _elderly_id_set(active)
    left = _tag_id_set(ROSTER_CATEGORIES['left']['tag_code'], active)
    distressed = _tag_id_set(ROSTER_CATEGORIES['distressed']['tag_code'], active)
    hard_family = _hard_family_id_set(active)
    visit = _visit_id_set(active)
    subsidy = _subsidy_id_set(active)

    key = dibao | disabled | care | special | elderly | left | distressed | hard_family | visit | subsidy
    b1 = dibao | disabled
    b2 = elderly - b1
    b3 = (left | distressed) - b1 - b2
    b4 = key - b1 - b2 - b3
    total = len(key)

    households = 0
    if key:
        households = Resident.query.filter(
            Resident.id.in_(list(key)), Resident.household_code.isnot(None),
        ).with_entities(Resident.household_code).distinct().count()

    def _pct(n):
        return round(n * 100.0 / total, 1) if total else 0.0

    items = [
        {'key': 'dibao_disabled', 'label': '低保·残疾', 'count': len(b1), 'pct': _pct(len(b1))},
        {'key': 'elderly', 'label': '高龄老人', 'count': len(b2), 'pct': _pct(len(b2))},
        {'key': 'child', 'label': '留守·困境儿童', 'count': len(b3), 'pct': _pct(len(b3))},
        {'key': 'other', 'label': '其他对象', 'count': len(b4), 'pct': _pct(len(b4))},
    ]
    return {'total_people': total, 'total_households': households, 'items': items}


def _review_progress(active_ids: set) -> list:
    """首页复核进度（070）：三项真实字段进度。

    低保复核依赖 067 的 dibao.support_status；该列不存在时置 available=False（占位，
    不伪造百分比）。高龄认证/残疾评定按本库既有字段计算。
    """
    from models import db, Resident, Dibao, Disabled
    cols = {r[1] for r in db.session.execute(db.text('PRAGMA table_info(dibao)')).fetchall()}
    has_support_status = 'support_status' in cols

    dibao_total = Dibao.query.filter(Dibao.status == '有效').count()
    dibao_done = None
    if has_support_status:
        dibao_done = Dibao.query.filter(Dibao.status == '有效', Dibao.support_status == '已认定').count()

    elderly_ids = _elderly_id_set(active_ids)
    elderly_total = len(elderly_ids)
    elderly_done = 0
    if elderly_ids:
        elderly_done = Resident.query.filter(
            Resident.id.in_(list(elderly_ids)), Resident.birth_date.isnot(None),
        ).count()

    disabled_total = Disabled.query.filter(Disabled.status == '有效').count()
    disabled_done = Disabled.query.filter(
        Disabled.status == '有效', Disabled.disability_level.isnot(None),
        Disabled.disability_level != '',
    ).count()

    def _row(label, done, total, available=True, note=''):
        return {'label': label, 'done': done, 'total': total,
                'available': available, 'note': note}

    return [
        _row('低保复核', dibao_done, dibao_total, has_support_status,
             '' if has_support_status else '待 067 状态字段'),
        _row('高龄认证', elderly_done, elderly_total),
        _row('残疾评定', disabled_done, disabled_total),
    ]


def compute_dashboard_counts() -> dict:
    """dashboard 核心计数聚合（纯计数，无敏感明文；T17-1 双轨口径）。

    返回字段与 dashboard_stats 返回 data 的计数部分对应；实时部分
    （group_stats/medical/户属性/户口类型/民族）由 routes 拼装。
    """
    from models import db, Resident, PartyMember, Dibao, Disabled,\
        SpecialSupport, WelfareJob, SubsidyRecord, Veteran

    active_ids = _active_resident_ids()
    base = Resident.query.filter(Resident.id.in_(active_ids)) if active_ids else Resident.query.filter(False)

    total_population = len(active_ids)
    total_households = base.with_entities(Resident.household_code).distinct().count() if active_ids else 0
    male_count = base.filter(Resident.gender == '男').count() if active_ids else 0
    female_count = base.filter(Resident.gender == '女').count() if active_ids else 0
    poverty_alleviated = base.filter(
        Resident.household_property == POVERTY_ALLEVIATED_PROPERTY
    ).count() if active_ids else 0

    current_year = datetime.now().year
    total_subsidy = db.session.query(func.coalesce(func.sum(SubsidyRecord.amount), 0)).filter(
        SubsidyRecord.status == '正常',
        SubsidyRecord.period.like(f'{current_year}-%'),
    ).scalar() or 0

    counts = {
        'total_population': total_population,
        'total_households': total_households,
        'male_count': male_count,
        'female_count': female_count,
        'party_count': PartyMember.query.count(),
        'dibao_count': Dibao.query.filter(Dibao.status == '有效').count(),
        'disabled_count': Disabled.query.filter(Disabled.status == '有效').count(),
        'special_support_count': SpecialSupport.query.filter(SpecialSupport.status == '有效').count(),
        'welfare_count': WelfareJob.query.count(),
        # AUD-04：样板第 5 格「退役军人」计数（复用退伍档案表；screen.py 会 pop 掉本键）
        'veteran_count': Veteran.query.count(),
        'poverty_alleviated': poverty_alleviated,
        'elderly_count': _elderly_count(active_ids),
        'hard_elderly_count': _tag_count(ROSTER_CATEGORIES['hard']['tag_code'], active_ids),
        'left_behind_count': _tag_count(ROSTER_CATEGORIES['left']['tag_code'], active_ids),
        'distressed_child_count': _tag_count(ROSTER_CATEGORIES['distressed']['tag_code'], active_ids),
        'total_subsidy': float(total_subsidy),
        'age_distribution': _age_distribution(active_ids),
        # 070 首页页面级规格
        'age_structure': _age_structure(active_ids),
        'population_composition': _key_object_structure(active_ids),
        'review_progress': _review_progress(active_ids),
        'data_updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    return counts


def get_dashboard_counts() -> dict:
    """dashboard 计数入口（081：统一走 MetricsEngine，缓存 TTL 45s）。"""
    from utils.metrics import MetricsEngine
    return MetricsEngine.query('dashboard_counts')['data']


def compute_civil_objects_count() -> int:
    """075 RC-06：民政对象「在册 N 人」——对象查询胶囊 7 页源表按人去重（Q5：数据听实库）。

    覆盖六张源表：低保 / 残疾 / 特困供养(SpecialSupport) / 事实无人抚养儿童(UnsupportedChild)
    / 一老一小(CareTarget) / 公益岗(WelfareJob)；弱势群体台账为聚合视图，不作源表重复计入。
    去重键（对齐 resident._vuln_stats 先例）：居民关联 → ('r', id)；
    无关联但有身份证哈希 → ('h', hash)；都无 → ('row', 表, 行 id)。
    """
    from models import db, Dibao, Disabled, SpecialSupport, UnsupportedChild, CareTarget, WelfareJob

    seen = set()
    sources = (
        (Dibao, True), (Disabled, True), (SpecialSupport, True),
        (CareTarget, True), (UnsupportedChild, False), (WelfareJob, False),
    )
    for model, has_status in sources:
        q = model.query
        if has_status:
            q = q.filter(model.status == '有效')
        for row in q.all():
            rid = getattr(row, 'resident_id', None)
            if rid:
                seen.add(('r', rid))
                continue
            id_hash = getattr(row, 'id_card_hash', None) or getattr(row, 'household_id_card_hash', None)
            if id_hash:
                seen.add(('h', id_hash))
                continue
            seen.add(('row', model.__tablename__, row.id))
    return len(seen)


def get_civil_objects_count() -> int:
    """民政对象在册人数入口（缓存 45s，同 dashboard 口径管理）。"""
    return get_or_compute(make_key('civil', 'objects_count'), compute_civil_objects_count)


# ============ 统计表口径（T17-3，report 蓝图；与 dashboard 同源防漂移） ============

# 统计表年龄段分箱（T17-3 主理人拍板 4 段；80 为固定分箱边界，与高龄阈值可配相互独立）
# 081：内容/顺序由指标注册表常量派生（保持既有列表形态供模板/导出使用）
STATS_AGE_BANDS = list(_METRICS_STATS_AGE_BANDS)
_STATS_BAND_ELDERLY_EDGE = 80  # 80+ 段（分箱语义，固定）
_STATS_BAND_MID_EDGE = 60      # 60-79 段（对齐我国老年人法定起点）


def _stats_age_band(age: int | None) -> str:
    """年龄 → 统计表年龄段（0-17/18-59/60-79/80+）。"""
    from .care_age import CHILD_AGE_MAX
    if age is None:
        return STATS_AGE_BANDS[-1]  # 无出生日期归 80+（极少，占位不丢数）
    if age < CHILD_AGE_MAX:
        return STATS_AGE_BANDS[0]
    if age < _STATS_BAND_MID_EDGE:
        return STATS_AGE_BANDS[1]
    if age < _STATS_BAND_ELDERLY_EDGE:
        return STATS_AGE_BANDS[2]
    return STATS_AGE_BANDS[3]


def compute_stats_table(grid: str | None = None) -> dict:
    """一老一小统计表（T17-3，主理人拍板：行=四分类，列=网格×年龄段，含合计）。

    口径（详细设计 §2.3 L233 + PRD L466/L479）：
    - 高龄：birth_date 动态 + age_class_override（与 _elderly_id_set 同源）
    - 困难/留守/困境：resident_tag 标签口径（status='有效'）
    - 年龄段：birth_date 动态计算（care_age.calculate_age 精确日差，4 段分箱）
    - grid=None 传全部；NULL village_group 居民跳过（与 group_stats 一致，合计=有效网格人口）

    返回：{'age_bands': [...], 'grids': [...], 'rows': [{'category','name','cells':{grid:[4]},'total'}],
           'totals': {grid: [4]}, 'grand_total': int}
    """
    from models import Resident
    from .care_age import calculate_age
    active_ids = _active_resident_ids()
    elderly_ids = _elderly_id_set(active_ids)
    hard_ids = _tag_id_set(ROSTER_CATEGORIES['hard']['tag_code'], active_ids)
    left_ids = _tag_id_set(ROSTER_CATEGORIES['left']['tag_code'], active_ids)
    dist_ids = _tag_id_set(ROSTER_CATEGORIES['distressed']['tag_code'], active_ids)

    q = Resident.query.filter(
        Resident.id.in_(active_ids),
        Resident.birth_date.isnot(None),
        Resident.village_group.isnot(None),
    )
    if grid:
        q = q.filter(Resident.village_group == grid)
    rows_data = q.with_entities(Resident.id, Resident.village_group, Resident.birth_date).all()

    grids = sorted({g for _, g, _ in rows_data})
    # 081：四分类行由指标注册表派生（顺序 高龄/困难/留守/困境，键与行标题保持不变）
    cats = [(ROSTER_CATEGORIES[cat_key]['stats_key'],
             ROSTER_CATEGORIES[cat_key]['stats_name'], ids)
            for cat_key, ids in (('elderly', elderly_ids), ('hard', hard_ids),
                                 ('left', left_ids), ('distressed', dist_ids))]
    matrix = {key: {g: [0] * len(STATS_AGE_BANDS) for g in grids} for key, _, _ in cats}

    for rid, g, bd in rows_data:
        age = calculate_age(bd)
        bi = STATS_AGE_BANDS.index(_stats_age_band(age))
        for key, _, ids in cats:
            if rid in ids:
                matrix[key][g][bi] += 1

    rows = [{'category': key, 'name': name,
             'cells': {g: matrix[key][g] for g in grids},
             'total': sum(sum(v) for v in matrix[key].values())}
            for key, name, _ in cats]
    totals = {g: [sum(matrix[k][g][i] for k, _, _ in cats) for i in range(len(STATS_AGE_BANDS))]
              for g in grids}
    grand_total = sum(r['total'] for r in rows)
    return {'age_bands': STATS_AGE_BANDS, 'grids': grids,
            'rows': rows, 'totals': totals, 'grand_total': grand_total}


def get_stats_table(grid: str | None = None) -> dict:
    """统计表入口（081：统一走 MetricsEngine，缓存 key 含 grid 与口径版本）。"""
    from utils.metrics import MetricsEngine
    return MetricsEngine.query('stats_table', filters={'grid': grid} if grid else {})['data']


def roster_id_sets() -> dict:
    """花名册居民 id 集（T17-4 + T21-1 全类别扩展；与统计表/dashboard 同源，口径唯一落本模块）。

    返回 {'active', 'elderly', 'hard', 'left', 'distressed', 'child',
          'hard_family', 'visit', 'subsidy'}。
    不缓存（导出/预览要求实时最新数据，计算毫秒级）。
    """
    active = _active_resident_ids()
    return {
        'active': active,
        'elderly': _elderly_id_set(active),
        'hard': _tag_id_set(ROSTER_CATEGORIES['hard']['tag_code'], active),
        'left': _tag_id_set(ROSTER_CATEGORIES['left']['tag_code'], active),
        'distressed': _tag_id_set(ROSTER_CATEGORIES['distressed']['tag_code'], active),
        'child': _child_id_set(active),
        'hard_family': _hard_family_id_set(active),
        'visit': _visit_id_set(active),
        'subsidy': _subsidy_id_set(active),
    }
