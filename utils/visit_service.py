# -*- coding: utf-8 -*-
"""utils.visit_service — 走访计划口径与待访候选装配（Sprint5 T18-1 唯一 owner）。

规格依据（Sprint5 §12.3 口径锚点 + PRD v1.2.3 R7-E2 L554 / R1-E4 L281，
2026-08-27 主理人拍板）：
- 应访对象 = ResidentTag(status='有效') 命中政策性标签集合的居民
- 统一排除：死亡（status='deceased'）、流出（flow_status='流出'，exclude_outflow）
  （默认入户口径；电话走访保留流出对象留给 T18-2 visit_type 落地）
- scope_params 为空 → 默认全部政策性标签（POLICY_TAGS 单源常量）

owner map：走访口径/候选装配唯一落本模块（禁在 routes/ 内重写判定逻辑）。
流动性判断一律走 utils.resident_flow.exclude_outflow（A4 红线，scan_flow_judge 静态拦截裸比较）。
"""
from __future__ import annotations

import json
from datetime import date, datetime

from models import Resident, ResidentTag, VisitRecord, db
from utils.masking import static_scan_text
from utils.resident_flow import exclude_outflow

# 政策性标签集合（单源常量：对齐 init_db '居民标签' 11 项 + 特困供养；Sprint5 §12.3）
POLICY_TAGS = (
    '低保', '残疾', '困难老人', '留守儿童', '困境儿童',
    '特困供养', '独居老人', '高龄老人', '脱贫户', '监测户', '五保户',
)

# 对象类型 → 标签组（plan_type=按对象类型）
OBJECT_TYPE_GROUPS = {
    '老人': ('困难老人', '独居老人', '高龄老人'),
    '儿童': ('留守儿童', '困境儿童'),
    '其他': ('低保', '残疾', '特困供养', '脱贫户', '监测户', '五保户'),
}

# 枚举白名单（单一真值；路由/模板经本模块取用，禁散落硬编码）
PLAN_TYPES = ('按区域', '按对象类型', '按标签')
PLAN_STATUSES = ('草稿', '进行中', '已完成')
AREA_FIELDS = ('village_group', 'grid')
VISIT_TYPES = ('入户', '电话', '其他')          # T18-2：走访方式（R7-E2）
PHOTO_ALLOWED = ('png', 'jpg', 'jpeg', 'gif', 'webp')  # T18-2：照片白名单（对齐 mediation）
PHOTO_MAX_BYTES = 5 * 1024 * 1024              # T18-2：单张 ≤5MB

# 儿童标签 → child_type（行级可见判定，对齐 masking.SENSITIVE_CHILD_TYPES）
_CHILD_TAGS = {'留守儿童': '留守', '困境儿童': '困境'}
_ELDER_TAGS = ('困难老人', '独居老人', '高龄老人')


def parse_scope_params(raw):
    """解析/校验 scope_params（Text JSON 或 dict）→ (params_dict, err_msg)。

    None/空串 → (None, '')（调用方按默认全部政策性标签处理）。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None, ''
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return None, 'scope_params 必须是合法 JSON'
    if not isinstance(data, dict):
        return None, 'scope_params 必须是 JSON 对象'
    return data, ''


def resolve_tags(plan_type, params):
    """按 plan_type 解析应匹配的标签集合（空/非法 → 默认全部政策性标签）。"""
    params = params or {}
    if plan_type == '按标签':
        tags = params.get('tags') or []
        return [t for t in tags if t in POLICY_TAGS] or list(POLICY_TAGS)
    if plan_type == '按对象类型':
        types = params.get('types') or []
        tags = []
        for t in types:
            tags.extend(OBJECT_TYPE_GROUPS.get(t, ()))
        return tags or list(POLICY_TAGS)
    # 按区域：区域不过滤标签，默认全标签
    return list(POLICY_TAGS)


def build_candidate_query(plan_type, params, exclude_flow=True):
    """待访候选查询：ResidentTag(有效) 命中标签 ∩ 非死亡（∩ 非流出），按 plan_type 追加区域过滤。

    exclude_flow=True 为入户口径（统一排除流出，T18-1）；False 为电话口径
    （R1-E4：流出仅入户排除、电话走访保留，T18-2）。
    """
    tags = resolve_tags(plan_type, params)
    query = Resident.query.join(
        ResidentTag, ResidentTag.resident_id == Resident.id
    ).filter(
        ResidentTag.status == '有效',
        ResidentTag.tag_code.in_(tags),
        Resident.status != 'deceased',
    )
    if plan_type == '按区域':
        params = params or {}
        area_field = params.get('area_field') or 'village_group'
        if area_field not in AREA_FIELDS:
            area_field = 'village_group'
        values = params.get('values') or []
        if values:
            query = query.filter(getattr(Resident, area_field).in_(values))
    if exclude_flow:
        return exclude_outflow(query)
    return query


def candidate_ids(plan_type, params):
    """待访候选 resident_id 集合（去重，入户口径：排除流出）。"""
    query = build_candidate_query(plan_type, params, exclude_flow=True)
    return {r[0] for r in query.with_entities(Resident.id).distinct().all()}


def candidate_ids_allow_outflow(plan_type, params):
    """电话口径候选集合（R1-E4：流出仅入户排除、电话走访保留；死亡仍拒）。"""
    query = build_candidate_query(plan_type, params, exclude_flow=False)
    return {r[0] for r in query.with_entities(Resident.id).distinct().all()}


def _classify(tags):
    """按命中标签推导 target_type/child_type（供 filter_child_rows 行级可见）。"""
    if '留守儿童' in tags or '困境儿童' in tags:
        ct = '留守' if '留守儿童' in tags else '困境'
        return '儿童', ct
    if any(t in _ELDER_TAGS for t in tags):
        return '老人', ''
    return '', ''


def _calc_age(birth_date):
    if not birth_date:
        return None
    today = date.today()
    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def candidate_rows(ids):
    """待访候选明细行（仅脱敏可展示字段；id_card/phone 等敏感字段不出清单）。

    行含 target_type/child_type 供 filter_child_rows 做儿童行级可见过滤。
    """
    ids = list(ids or [])
    if not ids:
        return []
    residents = Resident.query.filter(Resident.id.in_(ids)).all()
    # 命中标签（有效）按 resident 分组
    tags_by_res = {}
    tag_rows = ResidentTag.query.filter(
        ResidentTag.resident_id.in_(ids), ResidentTag.status == '有效'
    ).with_entities(ResidentTag.resident_id, ResidentTag.tag_code).all()
    for rid, code in tag_rows:
        tags_by_res.setdefault(rid, []).append(code)

    rows = []
    for r in residents:
        tags = sorted(tags_by_res.get(r.id, []))
        target_type, child_type = _classify(tags)
        rows.append({
            'resident_id': r.id,
            'name': r.name,
            'gender': r.gender or '',
            'birth_date': r.birth_date.strftime('%Y-%m-%d') if r.birth_date else '',
            'age': _calc_age(r.birth_date),
            'village_group': r.village_group or '',
            'grid': r.grid or '',
            'tags': tags,
            'target_type': target_type,
            'child_type': child_type,
        })
    return rows


# ============ T18-2 连续补录 ============

def resident_child_type(rid):
    """该居民命中标签中的儿童子类型（'留守'/'困境'/''，行级权限判定用）。"""
    tags = [code for (code,) in ResidentTag.query.filter(
        ResidentTag.resident_id == rid, ResidentTag.status == '有效'
    ).with_entities(ResidentTag.tag_code).all()]
    _, child_type = _classify(tags)
    return child_type


def _scan_sensitive(*texts):
    """任一文本含身份证/电话/银行卡字面明文 → True（scan_mask 纪律）。"""
    for t in texts:
        if not t:
            continue
        hit = static_scan_text(t)
        if hit['id_card'] or hit['phone'] or hit['bank_card']:
            return True
    return False


def validate_batch_row(rid, resident, row, candidates_in, candidates_flow_in, can_see_children):
    """单行校验（纯函数，不触库写）→ (visit_type 判定用的字段, err_msg)。

    返回 (visit_type, err_msg)：err_msg 非空即该行失败。
    """
    visit_type = (row.get('visit_type') or '').strip()
    if visit_type not in VISIT_TYPES:
        return None, '走访方式不合法'
    visit_date_raw = row.get('visit_date') or ''
    try:
        visit_date = datetime.strptime(str(visit_date_raw), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None, '走访日期格式不正确（YYYY-MM-DD）'
    if resident is None:
        return None, '居民不存在'
    if getattr(resident, 'status', '') == 'deceased':
        return None, '该居民已死亡，禁止补录'
    content = str(row.get('content') or '')
    need_desc = str(row.get('need_desc') or '')
    if _scan_sensitive(content, need_desc):
        return None, '内容含敏感明文，请脱敏后提交'
    # T18-4：photos 写侧路径白名单（仅 uploads/visit/ 相对路径，防路径注入）
    if not _photos_valid(row.get('photos')):
        return None, '照片路径不合法'
    # R1-E4：流出仅入户类型排除，电话走访保留（死亡已在上层拦截）
    if visit_type == '入户':
        if rid not in candidates_in:
            return None, '该居民不在应访对象内'
    elif rid not in candidates_flow_in:
        return None, '该居民不在应访对象内'
    # 儿童行级权限（留守/困境儿童）
    if not can_see_children and resident_child_type(rid):
        return None, '无权补录留守/困境儿童走访记录'
    return visit_type, ''


def save_batch_records(plan, rows, operator_id, can_see_children=False):
    """批量补录：先全量行级校验 → 单事务 upsert → 统计。

    幂等键 (plan_id, resident_id, visit_date, visit_type)：已存在则更新
    content/has_need/need_desc/photos/visitor_id（用户拍板：同键 upsert）。
    can_see_children 由路由层 @child_rows_guard 置入（儿童行级权限）。
    返回 {total, success, inserted, updated, errors:[{row,msg}], completed_count}。
    """
    from models import Resident as _R

    total = len(rows or [])
    errors = []
    valid = []  # [(rid, resident, visit_type, row)]
    inserted = updated = 0

    params, _ = parse_scope_params(plan.scope_params)
    params = params or {}
    candidates_in = candidate_ids(plan.plan_type, params)
    candidates_flow_in = candidate_ids_allow_outflow(plan.plan_type, params)

    for idx, row in enumerate(rows or [], start=1):
        rid = row.get('resident_id') if isinstance(row, dict) else None
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            errors.append({'row': idx, 'msg': '缺少 resident_id'})
            continue
        resident = _R.query.get(rid)
        visit_type, err = validate_batch_row(rid, resident, row, candidates_in,
                                             candidates_flow_in, can_see_children)
        if err:
            errors.append({'row': idx, 'msg': err})
            continue
        valid.append((rid, resident, visit_type, row))

    if valid:
        for rid, resident, visit_type, row in valid:
            try:
                visit_date = datetime.strptime(str(row.get('visit_date')), '%Y-%m-%d').date()
            except (ValueError, TypeError):
                continue  # validate 已拦，防御
            rec = VisitRecord.query.filter_by(
                plan_id=plan.id, resident_id=rid,
                visit_date=visit_date, visit_type=visit_type,
            ).first()
            if rec:
                rec.content = str(row.get('content') or '')
                rec.has_need = 1 if row.get('has_need') else 0
                rec.need_desc = str(row.get('need_desc') or '')
                rec.photos = _dump_photos(row.get('photos'))
                rec.visitor_id = operator_id
                rec.create_by = operator_id
                updated += 1
            else:
                rec = VisitRecord(
                    plan_id=plan.id, resident_id=rid, visit_date=visit_date,
                    visitor_id=operator_id, visit_type=visit_type,
                    content=str(row.get('content') or ''),
                    has_need=1 if row.get('has_need') else 0,
                    need_desc=str(row.get('need_desc') or ''),
                    photos=_dump_photos(row.get('photos')),
                    create_by=operator_id,
                )
                db.session.add(rec)
                inserted += 1
        db.session.commit()

    return {
        'total': total,
        'success': inserted + updated,
        'inserted': inserted,
        'updated': updated,
        'errors': errors,
        'completed_count': completed_count(plan.id),
    }


def _photos_valid(raw):
    """photos 元素白名单校验（写侧，T18-4）：必须 visit/ 前缀、非 / 开头、不含 ..。

    与读侧 photos_to_urls 过滤对称（DDL 注释：仅 uploads/visit/ 相对路径）。
    """
    if not raw:
        return True
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            return False
    else:
        items = raw
    if not isinstance(items, list):
        return False
    for p in items:
        name = str(p).strip()
        if not name.startswith('visit/') or name.startswith('/') or '..' in name:
            return False
    return True


def _dump_photos(raw):
    """photos 归一化：仅保留 uploads/visit/ 相对路径字符串，落库 JSON 数组。"""
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            items = [raw]
    else:
        items = raw
    paths = [str(p) for p in items if str(p).strip()]
    return json.dumps(paths, ensure_ascii=False) if paths else None


def completed_count(plan_id):
    """完成度分子：该计划已补录的**去重居民数**（K4 口径，一对象多次走访计 1）。"""
    return db.session.query(VisitRecord.resident_id).filter(
        VisitRecord.plan_id == plan_id
    ).distinct().count()


# ============ T18-3 历史追溯 ============

def photos_to_urls(raw):
    """photos JSON 数组 → [{name, url}]。

    仅收 `visit/` 前缀相对路径（T18-2 落库白名单），拒绝 `../`/绝对路径；
    url 拼 /uploads/ 前缀（serve_upload visit.view 已映射，404 由 SVG 占位兜底）。
    """
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            items = []
    else:
        items = raw
    urls = []
    for p in items:
        name = str(p).strip()
        if not name.startswith('visit/') or name.startswith('/') or '..' in name:
            continue
        urls.append({'name': name, 'url': '/uploads/' + name})
    return urls


def child_type_map(rids):
    """批量：resident_id → child_type（'留守'/'困境'，仅命中儿童标签），行级权限判定用。"""
    rids = list(rids or [])
    if not rids:
        return {}
    rows = ResidentTag.query.filter(
        ResidentTag.resident_id.in_(rids), ResidentTag.status == '有效'
    ).with_entities(ResidentTag.resident_id, ResidentTag.tag_code).all()
    tags_by_rid = {}
    for rid, code in rows:
        tags_by_rid.setdefault(rid, []).append(code)
    result = {}
    for rid, tags in tags_by_rid.items():
        _, ct = _classify(tags)
        if ct:
            result[rid] = ct
    return result


def coverage_summary(plan_id=None):
    """走访覆盖率统计（去重居民口径；用户拍板 G2/K4 同口径）。

    按计划（scope='plan'）：应访=plan.target_count，已访=completed_count(plan_id)。
    全局（scope='global'）：应访=全量政策性标签有效居民（candidate_ids('按标签', None)，
        排除死亡+流出）；已访=visit_record 去重居民 ∩ 应访集合（流出电话等库外记录不虚增分子）。
    分母 0 → rate 0（对齐 dashboard closure_rate 范式）。
    """
    if plan_id is not None:
        from models import VisitPlan
        plan = VisitPlan.query.get(plan_id)
        if not plan:
            total = visited = 0
        else:
            total = plan.target_count or 0
            visited = completed_count(plan_id)
        scope = 'plan'
    else:
        ids = candidate_ids('按标签', None)
        total = len(ids)
        visited = 0
        if ids:
            visited = db.session.query(VisitRecord.resident_id).filter(
                VisitRecord.resident_id.in_(list(ids))
            ).distinct().count()
        scope = 'global'
    rate = round(visited / total * 100, 1) if total else 0
    return {
        'scope': scope, 'plan_id': plan_id,
        'total': total, 'visited': visited,
        'coverage_rate': rate, 'completion_rate': rate,
    }
