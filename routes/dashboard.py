from flask import Blueprint, render_template, jsonify, session
from models import db, Resident, Mediation, MedicalInsurance
from utils.decorators import login_required
from utils.helpers import get_dict_items
from utils.resident_flow import exclude_outflow
from utils.agg_cache import get_dashboard_counts
from utils.metrics import AGE_DISTRIBUTION_LABELS
from sqlalchemy import func
from datetime import datetime

dashboard_bp = Blueprint('dashboard', __name__)


def _active_query():
    """活跃居民查询（A4/居民流动性唯一口径：resident_flow.exclude_outflow + 非死亡）。"""
    return exclude_outflow(Resident.query).filter(Resident.status != 'deceased')

@dashboard_bp.route('/')
@login_required
def index():
    # 070：页头副标题使用界面设置系统标题 + 聚合时间（与 /api/dashboard/stats 同源）
    # 075 RC-02（Q1 裁定）：村名取 village_name 设置项（未配置回退「数据更新至」口径），
    # 数据日期中文格式对齐样图（2026年10月24日），不再拿系统名顶替村名
    counts = get_dashboard_counts()
    raw_dt = counts.get('data_updated_at', '')          # 'YYYY-MM-DD HH:MM'
    date_part = raw_dt.split(' ')[0] if raw_dt else ''
    if date_part:
        y, m, d = date_part.split('-')
        data_date_cn = f'{int(y)}年{int(m)}月{int(d)}日'
    else:
        data_date_cn = ''
    return render_template(
        'dashboard/dashboard.html',
        data_date_cn=data_date_cn,
        data_updated_at=raw_dt,
    )

def _stats_payload():
    """统计响应体（dashboard 与大屏共用；R-048-06 抽纯函数，键集合与基线等价）。"""
    now = datetime.now()
    current_year = now.year

    # 核心计数（T17-1 双轨口径）：081 起 core counts 统一走 MetricsEngine
    # （get_dashboard_counts 已薄封装为 MetricsEngine.query('dashboard_counts')）
    counts = get_dashboard_counts()

    # ===== 实时部分（不进缓存） =====
    active = _active_query()
    total_population = counts['total_population']
    total_households = counts['total_households']
    active_sub = active.with_entities(Resident.id, Resident.village_group, Resident.gender,
                                      Resident.household_code, Resident.household_property,
                                      Resident.household_type, Resident.nation).subquery()

    # 所在组分人口+户数
    group_stats = []
    groups = db.session.query(active_sub.c.village_group).group_by(active_sub.c.village_group).all()
    for (g,) in groups:
        label = g or '未分组'
        gq = db.session.query(active_sub).filter(active_sub.c.village_group == g)
        pop = gq.count()
        hh = gq.with_entities(active_sub.c.household_code).distinct().count()
        male = gq.filter(active_sub.c.gender == '男').count()
        female = gq.filter(active_sub.c.gender == '女').count()
        group_stats.append({'name': label, 'population': pop, 'households': hh, 'male': male, 'female': female})

    # 纠纷办结率
    total_cases = Mediation.query.count()
    closed_cases = Mediation.query.filter(Mediation.status == 'closed').count()
    closure_rate = round(closed_cases / total_cases * 100, 1) if total_cases > 0 else 0

    medical_year = current_year
    medical_q = MedicalInsurance.query.filter(MedicalInsurance.payment_year == medical_year)
    medical_total = medical_q.count()
    medical_paid = medical_q.filter(MedicalInsurance.is_paid == '\u662f').count()
    medical_state_paid = medical_q.filter(MedicalInsurance.is_state_paid == '\u662f').count()
    medical_deductible = medical_q.filter(MedicalInsurance.is_deductible == '\u662f').count()
    medical_paid_rate = round(medical_paid / medical_total * 100, 1) if medical_total else 0
    medical_group_stats = []
    medical_groups = db.session.query(MedicalInsurance.village_group).filter(MedicalInsurance.payment_year == medical_year).group_by(MedicalInsurance.village_group).all()
    for (mg,) in medical_groups:
        if not mg:
            continue
        total_g = MedicalInsurance.query.filter(MedicalInsurance.payment_year == medical_year, MedicalInsurance.village_group == mg).count()
        paid_g = MedicalInsurance.query.filter(MedicalInsurance.payment_year == medical_year, MedicalInsurance.village_group == mg, MedicalInsurance.is_paid == '\u662f').count()
        medical_group_stats.append({'name': mg, 'total': total_g, 'paid': paid_g, 'rate': round(paid_g / total_g * 100, 1) if total_g else 0})

    # 户属性统计 — 从字典动态读取
    prop_items = get_dict_items('户属性')
    prop_types = [(i.label, i.value) for i in prop_items]  # (label, value) 结构
    household_prop_stats = {}
    for label, value in prop_types:
        pq = db.session.query(active_sub).filter(active_sub.c.household_property == value)
        people_count = pq.count()
        household_count = pq.with_entities(active_sub.c.household_code).distinct().count()
        household_prop_stats[label] = {'count': household_count, 'people_count': people_count, 'value': value}
    total_with_prop = sum(v['count'] for v in household_prop_stats.values())
    for label in household_prop_stats:
        household_prop_stats[label]['pct'] = round(household_prop_stats[label]['count'] / total_households * 100, 1) if total_households > 0 else 0

    household_type_items = get_dict_items('\u6237\u53e3\u7c7b\u578b')
    household_type_stats = {}
    for item in household_type_items:
        label = item.label
        value = item.value
        tq = db.session.query(active_sub).filter(active_sub.c.household_type == value)
        people_count = tq.count()
        household_count = tq.with_entities(active_sub.c.household_code).distinct().count()
        household_type_stats[label] = {
            'count': household_count,
            'people_count': people_count,
            'value': value,
            'pct': round(people_count / total_population * 100, 1) if total_population > 0 else 0
        }

    # 民族分布
    nation_stats = []
    nations = db.session.query(active_sub.c.nation, db.func.count(active_sub.c.id).label('cnt')).filter(
        active_sub.c.nation.isnot(None)
    ).group_by(active_sub.c.nation).order_by(db.desc('cnt')).all()
    for n, cnt in nations:
        if n:
            nation_stats.append({'name': n, 'count': cnt, 'pct': round(cnt / total_population * 100, 1) if total_population else 0})

    return {
        **counts,
        'group_stats': group_stats,
        'closure_rate': closure_rate,
        'age_labels': list(AGE_DISTRIBUTION_LABELS),
        'household_prop_stats': household_prop_stats,
        'household_type_stats': household_type_stats,
        'nation_stats': nation_stats,
        'medical_year': medical_year,
        'medical_total': medical_total,
        'medical_paid': medical_paid,
        'medical_paid_rate': medical_paid_rate,
        'medical_state_paid': medical_state_paid,
        'medical_deductible': medical_deductible,
        'medical_group_stats': medical_group_stats,
    }


@dashboard_bp.route('/api/dashboard/stats')
@login_required
def dashboard_stats():
    return jsonify({'success': True, 'data': _stats_payload()})
