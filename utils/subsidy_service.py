# -*- coding: utf-8 -*-
"""utils.subsidy_service — 补贴防误发资格判定核心（T14-2，Sprint3 第一批）。

规格依据（PRD v1.2.3 R4-E1~E6 + C4 集合定义 L397 + R4-E6 流出放行 L387）：
- `check_eligibility` 为唯一判定入口（T14-3 登记接线依赖此契约，稳定签名）
- C4 项目-资格映射：只校验与本项目资格相关的记录，不做全表连坐
- R4-E3：死亡（status=deceased）或关联业务记录软失效 → 拦截（eligible=False）
- R4-E4：项目停用 → 拦截
- R4-E6：flow_status=流出 允许登记但显著提示（eligible=True + detail.flow_out=True）
- 年龄边界：project.age_min/age_max 为适用边界；高龄津贴按动态年龄阈值（config.ELDERLY_AGE_THRESHOLD，默认 ≥80）

owner map：补贴资格判定业务唯一落本模块（禁在 routes/ 内重写判定逻辑）。
流动性判断一律走 utils.resident_flow.is_outflow（A4 红线，scan_flow_judge 静态拦截裸比较）。
"""
from __future__ import annotations

import json

from utils.care_age import calculate_age, elderly_threshold
from utils.resident_flow import is_outflow

# 枚举白名单（单一真值；模板/路由经本模块取用，禁散落硬编码）
PROJECT_TYPES = ('高龄津贴', '困难老人补助', '低保金', '残疾补贴', '其他')
CYCLES = ('月', '季', '年', '一次性')
PROJECT_STATUSES = ('启用', '停用')

# 业务记录软失效状态值（R5-E1③ m002 统一口径）
STATUS_ACTIVE = '有效'

# C4 项目-资格映射（PRD L397：只校验与本项目资格相关的记录，不做全表连坐）
# kind: dynamic_age=动态年龄(高龄津贴) / care_target / dibao / disabled / tag(其他→标签)
C4_PROJECT_MAP = {
    '高龄津贴': {'kind': 'dynamic_age'},
    '困难老人补助': {'kind': 'care_target'},
    '低保金': {'kind': 'dibao'},
    '残疾补贴': {'kind': 'disabled'},
    '其他': {'kind': 'tag'},
}

# 无 resident_id 台账（special_support/unsupported_children），按身份证哈希 / 姓名+出生日期匹配
# 注：T16 回填标签前仅作 C4 未命中时的补充路径（R3 挂账项），U2 映射细目到位后可收紧
SPECIAL_NO_RESIDENT_ID = ('特困供养', '事实无人抚养儿童')


def _parse_target_tags(raw) -> list:
    """解析 target_tags（Text=JSON 数组）；坏 JSON / 空 → []（读侧容错）。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    try:
        tags = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(tags, list):
        return []
    return [str(t).strip() for t in tags if str(t).strip()]


def resident_lifecycle(resident) -> dict:
    """生命周期判定：{'deceased','flow_out','reasons'}。

    - status=='deceased' → deceased=True（E3 硬拦截）
    - 流出 → flow_out=True（E6 放行 + 显著提示），唯一判定来源 is_outflow
    """
    out = {'deceased': False, 'flow_out': False, 'reasons': []}
    if getattr(resident, 'status', None) == 'deceased':
        out['deceased'] = True
        out['reasons'].append('该居民已死亡，禁止登记补贴')
    if is_outflow(resident):
        out['flow_out'] = True
        out['reasons'].append('该居民已流出，请确认发放资格')
    return out


def _match_c4(project, resident, detail: dict):
    """C4 项目-资格映射判定。返回 (ok: bool, reason: str|None)。

    只查与本项目资格相关的记录（不做全表连坐）；「其他」类型无 target_tags 视为无标签约束。
    """
    kind = C4_PROJECT_MAP.get(getattr(project, 'project_type', ''), {}).get('kind')
    rid = getattr(resident, 'id', None)

    if kind == 'dynamic_age':
        age = detail.get('age')
        if age is None:
            return False, '居民缺出生日期，无法判定高龄津贴资格'
        if age >= elderly_threshold():
            return True, None
        return False, f'年龄 {age} 未达高龄津贴阈值 {elderly_threshold()}'

    if kind == 'care_target':
        from models import CareTarget
        hit = CareTarget.query.filter_by(
            resident_id=rid, target_type='老人', elder_type='困难', status=STATUS_ACTIVE).first()
        if hit:
            return True, None
        return False, '困难老人补助资格记录不存在或已失效'

    if kind == 'dibao':
        from sqlalchemy import or_
        from models import Dibao
        hit = Dibao.query.filter(
            Dibao.resident_id == rid,
            Dibao.status == STATUS_ACTIVE,
            or_(Dibao.is_deceased.is_(None), Dibao.is_deceased != '是'),
        ).first()
        if hit:
            return True, None
        return False, '低保金资格记录不存在、已失效或成员已死亡'

    if kind == 'disabled':
        from models import Disabled
        hit = Disabled.query.filter_by(resident_id=rid, status=STATUS_ACTIVE).first()
        if hit:
            return True, None
        return False, '残疾补贴资格记录不存在或已失效'

    if kind == 'tag':
        tags = _parse_target_tags(getattr(project, 'target_tags', None))
        if not tags:
            return True, None  # 无标签约束：生命周期/年龄校验通过即可
        from models import ResidentTag
        hits = ResidentTag.query.filter(
            ResidentTag.resident_id == rid,
            ResidentTag.status == STATUS_ACTIVE,
            ResidentTag.tag_code.in_(tags),
        ).all()
        if hits:
            detail['matched_tags'] = [t.tag_code for t in hits]
            return True, None
        return False, f'居民未命中适用标签（{", ".join(tags)}）'

    return False, '未知补贴项目类型，无法判定资格'


def _match_without_resident_id(project, resident):
    """无 resident_id 台账匹配（C4 未命中时的补充路径）。

    - 特困供养（special_support）：household_id_card sha256 哈希 == resident.id_card_hash
    - 事实无人抚养儿童（unsupported_children）：id_card 哈希优先；name+birth_date 兜底
    返回 (ok, kind|None)；kind ∈ 'special_support' / 'unsupported_child'。
    匹配过程不输出明文身份证（脱敏红线）。
    """
    from utils.crypto import sha256_hash

    tags = _parse_target_tags(getattr(project, 'target_tags', None))
    want = [t for t in tags if t in SPECIAL_NO_RESIDENT_ID] if tags else []
    if not want:
        return False, None

    id_hash = getattr(resident, 'id_card_hash', None)

    if '特困供养' in want:
        from models import SpecialSupport
        hit = SpecialSupport.query.filter_by(status=STATUS_ACTIVE).all()
        for sp in hit:
            if sp.household_id_card and id_hash and sha256_hash(sp.household_id_card) == id_hash:
                return True, 'special_support'
        return False, '特困供养名单未匹配到该居民'

    if '事实无人抚养儿童' in want:
        from models import UnsupportedChild
        children = UnsupportedChild.query.all()
        name = getattr(resident, 'name', '')
        birth = getattr(resident, 'birth_date', None)
        for c in children:
            if c.id_card and id_hash and sha256_hash(c.id_card) == id_hash:
                return True, 'unsupported_child'
            if c.name and c.name == name and c.birth_date and birth and c.birth_date == birth:
                return True, 'unsupported_child'
        return False, '事实无人抚养儿童名单未匹配到该居民'

    return False, None


def check_eligibility(resident, project, now=None) -> dict:
    """C4 项目-资格映射判定（唯一入口，不抛异常，收集 reasons）。

    参数：resident（Resident 实例）、project（SubsidyProject 实例）。
    返回契约（T14-3 登记接线依赖）：
        {'eligible': bool,
         'reasons': [str, ...],          # 拦截原因 / 流出提示
         'detail': {'age','flow_out','deceased','matched_tags','c4',
                    'special_support','unsupported_child'}}
    """
    result = {
        'eligible': True,
        'reasons': [],
        'detail': {
            'age': None,
            'flow_out': False,
            'deceased': False,
            'matched_tags': [],
            'c4': None,
            'special_support': False,
            'unsupported_child': False,
        },
    }
    detail = result['detail']

    if resident is None or project is None:
        result['eligible'] = False
        result['reasons'].append('居民或项目不存在')
        return result

    # 1. 生命周期（E3 死亡硬拦截 / E6 流出放行+提示）
    lc = resident_lifecycle(resident)
    detail['deceased'] = lc['deceased']
    detail['flow_out'] = lc['flow_out']
    if lc['deceased']:
        result['eligible'] = False
    result['reasons'].extend(lc['reasons'])

    # 2. 项目停用（E4）
    if getattr(project, 'status', None) == '停用':
        result['eligible'] = False
        result['reasons'].append(f'补贴项目「{getattr(project, "name", "")}」已停用，禁止新增发放记录')

    if not result['eligible']:
        return result

    # 3. 年龄边界（适用边界；birth_date 明文可直接计算）
    age = calculate_age(getattr(resident, 'birth_date', None))
    detail['age'] = age
    age_min = getattr(project, 'age_min', None)
    age_max = getattr(project, 'age_max', None)
    if age is not None:
        if age_min is not None and age < age_min:
            result['eligible'] = False
            result['reasons'].append(f'年龄 {age} 低于项目适用下限 {age_min}')
        if age_max is not None and age > age_max:
            result['eligible'] = False
            result['reasons'].append(f'年龄 {age} 超过项目适用上限 {age_max}')
    else:
        kind = C4_PROJECT_MAP.get(getattr(project, 'project_type', ''), {}).get('kind')
        if kind == 'dynamic_age':
            result['eligible'] = False
            result['reasons'].append('居民缺出生日期，无法判定高龄津贴资格')
    if not result['eligible']:
        return result

    # 4. C4 项目-资格映射（防误发红线：对象仍在适用标签范围）
    c4_ok, c4_reason = _match_c4(project, resident, detail)
    detail['c4'] = c4_reason
    if not c4_ok:
        special_ok, special_kind = _match_without_resident_id(project, resident)
        if special_ok:
            if special_kind == 'special_support':
                detail['special_support'] = True
            else:
                detail['unsupported_child'] = True
        else:
            result['eligible'] = False
            result['reasons'].append(c4_reason or '资格判定未命中')

    return result


# ==================== T14-5 批量登记（E5，Sprint3 第三批） ====================

# Excel 列名（模板固定，无字段映射向导）
BATCH_COL_ID_CARD = '身份证号'
BATCH_COL_PERIOD = '发放期间'
BATCH_COL_AMOUNT = '金额'


def _batch_parse_period(raw) -> str | None:
    """批量行发放期间解析（YYYY-MM）；非法返回 None。"""
    s = str(raw or '').strip()
    if len(s) != 7 or s[4] != '-':
        return None
    try:
        y, m = int(s[:4]), int(s[5:])
    except ValueError:
        return None
    if not (1 <= m <= 12):
        return None
    return s


def _batch_parse_amount(raw) -> float | None:
    """批量行金额解析（≥0，最多两位小数）；空/非法返回 None。

    P3-6 整改：改用 Decimal 校验两位小数，避免 round(v,2)!=v 的浮点精度陷阱。
    """
    from decimal import Decimal, InvalidOperation
    s = str(raw or '').strip()
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if d < 0 or d > Decimal('99999999.99'):
        return None
    if d.as_tuple().exponent < -2:  # 超过两位小数 → 非法
        return None
    return float(d)


def run_subsidy_batch(task_id: str, batch_id: str) -> None:
    """T14-5 补贴批量登记 A1 处理函数（run_async 约定：fn(task_id, **kwargs)，已处 app context 内）。

    逐行：身份证 sha256 匹配 residents.id_card_hash → check_eligibility 防误发 →
    通过写 SubsidyRecord(source='normal')；失败行进 SubsidyBatchError（身份证脱敏）。
    每行 source_hash 幂等：sha256(f"{resident_id}:{project_id}:{period}")（跨批次唯一，防重复登记）。
    """
    from datetime import datetime
    from models import db, Resident, SubsidyProject, SubsidyRecord, SubsidyBatchImport, SubsidyBatchError
    from utils.crypto import sha256_hash, key_holder
    from utils.masking import mask_id_card
    from utils.async_tasks import _update
    from utils.import_engine import read_import_file
    from utils.helpers import log_operation

    batch = SubsidyBatchImport.query.filter_by(batch_id=batch_id).first()
    if not batch:
        raise RuntimeError(f'批量登记批次不存在：{batch_id}')
    if batch.status == 'done':
        return  # 幂等：已完成批次不重跑

    project = SubsidyProject.query.get(batch.project_id)
    if not project:
        batch.status = 'failed'
        db.session.commit()
        _update(task_id, status='failed', result_summary='补贴项目不存在')
        return

    batch.status = 'running'
    db.session.commit()

    headers, rows = read_import_file(batch.source_path)
    total = len(rows)
    batch.total_rows = total
    db.session.commit()

    _update(task_id, progress=0, result_summary=f'开始批量登记 {total} 行')

    success = 0
    fail = 0

    for idx, row in enumerate(rows, start=2):  # Excel 行号从 2 起（首行表头）
        try:
            id_card = str(row.get(BATCH_COL_ID_CARD, '') or '').strip()
            if not id_card:
                _add_batch_error(batch_id, idx, '', '身份证号为空')
                fail += 1
                continue

            # 身份证哈希匹配居民（不落明文）
            id_hash = sha256_hash(id_card)
            resident = Resident.query.filter_by(id_card_hash=id_hash).first()
            if not resident:
                _add_batch_error(batch_id, idx, mask_id_card(id_card), '居民不存在')
                fail += 1
                continue

            # 期间/金额解析
            period = _batch_parse_period(row.get(BATCH_COL_PERIOD, ''))
            if period is None:
                _add_batch_error(batch_id, idx, mask_id_card(id_card), '发放期间格式不正确（YYYY-MM）')
                fail += 1
                continue
            amount = _batch_parse_amount(row.get(BATCH_COL_AMOUNT, ''))
            if amount is None:
                amount = float(project.default_amount) if project.default_amount is not None else 0.0

            # 防误发校验（唯一判定入口）
            eligibility = check_eligibility(resident, project)
            if not eligibility['eligible']:
                reason = '；'.join(eligibility['reasons'])
                _add_batch_error(batch_id, idx, mask_id_card(id_card), reason)
                log_operation(batch.operator and str(batch.operator) or '', 'subsidy',
                              'blocked_subsidy',
                              f'批量拦截: {resident.name} → {project.name}',
                              detail=reason, result='blocked')
                fail += 1
                continue

            # 行级 source_hash 幂等（防重复）：不含 batch_id，跨批次同居民同期间唯一
            row_hash = sha256_hash(f"{resident.id}:{project.id}:{period}")
            if SubsidyRecord.query.filter_by(source_hash=row_hash).first():
                _add_batch_error(batch_id, idx, mask_id_card(id_card), '该居民该期间已登记（幂等跳过）')
                fail += 1
                continue

            rec = SubsidyRecord(
                resident_id=resident.id, project_id=project.id,
                issue_date=datetime.now().date(), period=period, amount=amount,
                channel='镇级直发', source='normal', status='正常',
                source_hash=row_hash, create_by=batch.operator,
            )
            db.session.add(rec)
            success += 1

        except Exception as e:
            db.session.rollback()
            _add_batch_error(batch_id, idx, mask_id_card(str(row.get(BATCH_COL_ID_CARD, ''))),
                             f'行处理失败：{type(e).__name__}: {e}')
            fail += 1

    # 每批一个事务提交（这里整体提交；≤1000 行规模单事务足够）
    db.session.commit()
    db.session.expire_all()

    batch = SubsidyBatchImport.query.filter_by(batch_id=batch_id).first()
    batch.status = 'done'
    batch.success_rows = success
    batch.fail_rows = fail
    batch.finished_at = datetime.now()
    db.session.commit()

    summary = json.dumps({'total': total, 'success': success, 'fail': fail}, ensure_ascii=False)
    _update(task_id, status='done', progress=100,
            result_summary=f'批量登记完成：{summary}', finished_at=datetime.now())


def _add_batch_error(batch_id, row_no, id_card_masked, reason):
    """失败行进 SubsidyBatchError 暂存区（身份证已脱敏）。"""
    from models import db, SubsidyBatchError
    db.session.add(SubsidyBatchError(
        batch_id=batch_id, row_no=row_no,
        id_card_masked=id_card_masked or '',
        reason=str(reason)[:300]))


def mark_aborted_stale():
    """进程重启后，残留 running 的补贴批量登记批次标 aborted（幂等三件套之一，108 对齐）。

    aborted 批次可重新 execute（record_import_execute 不挡 aborted），行级
    source_hash 幂等保证整批重跑不产生重复登记（106 §6.1）。
    """
    from models import db, SubsidyBatchImport
    stale = SubsidyBatchImport.query.filter_by(status='running').all()
    for batch in stale:
        batch.status = 'aborted'
    if stale:
        db.session.commit()
    return len(stale)

# ==================== 声明式政策性补贴聚合注册表（后期切换生产真实字段只改此清单） ====================

POLICY_SUBSIDY_REGISTRY = [
    {
        'module': 'dibao',
        'model': 'Dibao',
        'locate': 'resident_or_hash',
        'hash_field': 'id_card_hash',
        'active_status': '有效',
        'items': [
            {
                'name': '低保救助',
                'amount_field': 'household_amount',
                'category_fields': ('dibao_type', 'sub_category'),
                'default_category': '低保对象',
                'period_field': 'payment_month',
                'note_field': 'support_status',
                'unit': '元/户',
                'require_positive': False,
            },
        ],
    },
    {
        'module': 'disabled',
        'model': 'Disabled',
        'locate': 'resident_or_hash',
        'hash_field': 'id_card_hash',
        'active_status': '有效',
        'items': [
            {
                'name': '残疾人困难生活补贴',
                'amount_field': 'subsidy_living',
                'category_fields': ('disability_type', 'disability_level'),
                'default_category': '残疾人',
                'unit': '元/月',
                'require_positive': True,
            },
            {
                'name': '残疾人重度护理补贴',
                'amount_field': 'subsidy_nursing',
                'category_fields': ('disability_type', 'disability_level'),
                'default_category': '残疾人',
                'unit': '元/月',
                'require_positive': True,
            },
        ],
    },
    {
        'module': 'youshu',
        'model': 'Youshu',
        'locate': 'hash',
        'hash_field': 'id_card_hash',
        'active_status': '有效',
        'items': [
            {
                'name': '优抚对象补贴',
                'amount_field': 'amount',
                'category_fields': ('care_category',),
                'default_category': '优抚对象',
                'note_field': 'support_status',
                'unit': '元/月',
                'require_positive': False,
            },
        ],
    },
    {
        'module': 'special_support',
        'model': 'SpecialSupport',
        'locate': 'hash',
        'hash_field': 'household_id_card_hash',
        'active_status': '有效',
        'items': [
            {
                'name': '特困供养保障',
                'amount_field': 'guarantee_amount',
                'category_fields': ('support_mode',),
                'default_category': '特困人员',
                'period_field': 'payment_month',
                'unit': '元/月',
                'require_positive': False,
            },
        ],
    },
    {
        'module': 'unsupported_child',
        'model': 'UnsupportedChild',
        'locate': 'hash',
        'hash_field': 'id_card_hash',
        'active_status': None,
        'items': [
            {
                'name': '事实无人抚养儿童补贴',
                'amount_field': 'amount',
                'category_fields': (),
                'default_category': '困境儿童',
                'unit': '元/月',
                'require_positive': False,
            },
        ],
    },
    {
        'module': 'welfare_job',
        'model': 'WelfareJob',
        'locate': 'hash',
        'hash_field': 'id_card_hash',
        'active_status': None,
        'end_date_field': 'end_date',
        'items': [
            {
                'name': '公益性岗位补贴',
                'amount_field': 'monthly_amount',
                'category_fields': ('job_type',),
                'default_category': '公益岗',
                'unit': '元/月',
                'require_positive': False,
            },
        ],
    },
]


def _collect_softtable_subsidies(resident, softtable_subsidies):
    """按可视化挂载的软表补贴配置，按身份证指纹/明文匹配居民并提取金额。"""
    if not softtable_subsidies:
        return []
    from models import DynamicField, DynamicRecord, DynamicTable
    from utils.crypto import key_holder
    from utils.softtable_engine import derive_hmac_key, identity_digest, normalize_identity

    id_plain = ''
    if getattr(resident, 'id_card_enc', None) and key_holder.is_initialized:
        try:
            id_plain = key_holder.decrypt(resident.id_card_enc) or ''
        except Exception:
            id_plain = ''
    if not id_plain:
        id_plain = getattr(resident, 'id_card', None) or ''
    norm_id = normalize_identity(id_plain)
    if not norm_id:
        return []

    hmac_key = derive_hmac_key(key_holder.get_key_bytes()) if key_holder.is_initialized else None
    out = []
    for st in softtable_subsidies:
        if not st.get('enabled', True):
            continue
        tbl = DynamicTable.query.filter_by(code=st.get('table_code')).first()
        if not tbl:
            continue
        id_col = st.get('identity_field') or ''
        amt_col = st.get('amount_field') or ''
        cat_col = st.get('category_field') or ''
        if not id_col or not amt_col:
            continue
        id_fld = DynamicField.query.filter_by(table_id=tbl.id, code=id_col).first()
        is_ident = bool(id_fld and id_fld.sensitivity == 'identity')
        want_dig = identity_digest(hmac_key, id_col, norm_id) if (is_ident and hmac_key) else None

        for rec in DynamicRecord.query.filter_by(table_id=tbl.id).all():
            try:
                data = json.loads(rec.data or '{}')
            except ValueError:
                data = {}
            matched = False
            if is_ident:
                if want_dig:
                    try:
                        dig = json.loads(rec.digest or '{}')
                    except ValueError:
                        dig = {}
                    matched = (dig.get(id_col) == want_dig)
            else:
                matched = (normalize_identity(data.get(id_col)) == norm_id)
            if not matched:
                continue
            try:
                amt = float(data.get(amt_col) or 0.0)
            except (TypeError, ValueError):
                amt = 0.0
            cat_val = str(data.get(cat_col) or '').strip() if cat_col else tbl.name
            out.append({
                'module': f'softtable:{tbl.code}',
                'name': st.get('name') or tbl.name,
                'category': cat_val or tbl.name,
                'amount': round(amt, 2),
                'period': '',
                'unit': st.get('unit', '元/月'),
                'note': '扩展软表',
            })
    return out


def collect_policy_subsidies(resident):
    """按可视化生效的政策性补贴口径（固定表 + 挂载软表）实时聚合居民在享补贴与总和。"""
    from datetime import date as _date
    from sqlalchemy import or_
    import models as _models
    from utils.linkage_config import get_effective_policy_registry

    rid = resident.id
    ich = getattr(resident, 'id_card_hash', None) or ''
    today = _date.today()
    results = []

    eff_registry, softtable_subsidies = get_effective_policy_registry()
    for spec in eff_registry:
        active_items = [it for it in spec['items'] if it.get('enabled', True)]
        if not active_items:
            continue
        cls = getattr(_models, spec['model'], None)
        if cls is None:
            continue
        conds = []
        if spec['locate'] == 'resident_or_hash' and hasattr(cls, 'resident_id'):
            conds.append(cls.resident_id == rid)
        hash_col = getattr(cls, spec.get('hash_field') or '', None)
        if ich and hash_col is not None:
            conds.append(hash_col == ich)
        if not conds:
            continue
        q = cls.query.filter(or_(*conds))
        if spec.get('active_status') and hasattr(cls, 'status'):
            q = q.filter(cls.status == spec['active_status'])

        end_field = spec.get('end_date_field')
        for row in q.all():
            if end_field:
                ed = getattr(row, end_field, None)
                if ed and ed < today:
                    continue
            for item_cfg in active_items:
                amt = float(getattr(row, item_cfg['amount_field'], None) or 0.0)
                if item_cfg.get('require_positive') and amt <= 0:
                    continue
                cat_parts = [str(getattr(row, f, '') or '').strip()
                             for f in item_cfg.get('category_fields', ())]
                category = ' / '.join(p for p in cat_parts if p) or item_cfg.get('default_category', '')
                period = (str(getattr(row, item_cfg['period_field'], '') or '').strip()
                          if item_cfg.get('period_field') else '')
                note = (str(getattr(row, item_cfg['note_field'], '') or '').strip()
                        if item_cfg.get('note_field') else '')
                results.append({
                    'module': spec['module'],
                    'name': item_cfg['name'],
                    'category': category,
                    'amount': round(amt, 2),
                    'period': period,
                    'unit': item_cfg.get('unit', '元/月'),
                    'note': note,
                })

    results.extend(_collect_softtable_subsidies(resident, softtable_subsidies))
    total = round(sum(it['amount'] for it in results), 2)
    return {'items': results, 'total': total, 'count': len(results)}
