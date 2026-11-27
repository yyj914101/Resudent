# -*- coding: utf-8 -*-
"""T20-4 自定义查询构建器（R9-E2，owner=utils/）。

多条件组合筛选（居民/对象分类/补贴/走访，AND/OR）→ 预览（脱敏）+ 导出 Excel。
- 字段/操作符双白名单（防注入）；非白名单 → ValueError（端点转 400）
- 条件树 JSON：{"logic":"AND","conditions":[{field,op,value} | {logic,...}]}
- 主实体 = 居民（Resident）；对象/补贴/走访经 EXISTS 子查询关联
- 敏感字段（id_card）走 sha256 等值/前缀桶检索，输出脱敏（明文仅 view_sensitive）
"""
import json
from datetime import datetime, date

from models import db
from models.resident import Resident

# ---- 字段白名单：field → (实体, 处理函数) ----
# 值统一经 _coerce 转换；非法字段/操作符抛 ValueError

_OPS = {'eq', 'contains', 'gt', 'lt', 'between', 'in'}


def _parse_between(v):
    """between 值：{"from":x,"to":y} 或 [x,y]。"""
    if isinstance(v, dict):
        return v.get('from'), v.get('to')
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return v[0], v[1]
    raise ValueError('between 需要 {from,to} 或 [x,y]')


def _parse_in(v):
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, str):
        return [x.strip() for x in v.split(',') if x.strip()]
    raise ValueError('in 需要列表')


# ---- 各实体条件 → SQLAlchemy 过滤表达式（返回可 .filter 的表达式） ----

def _resident_field(field, op, value):
    if field == 'resident.name':
        col = Resident.name
        return _apply_op(col, op, value, like=True)
    if field == 'resident.id_card':
        from utils.crypto import sha256_hash, sha256_prefix_hash
        raw = str(value).strip()
        if op != 'eq':
            raise ValueError('身份证仅支持精确匹配（eq）')
        return Resident.id_card_hash == sha256_hash(raw)
    if field == 'resident.gender':
        return _apply_op(Resident.gender, op, value, like=True)
    if field == 'resident.age':
        # 年龄 → birth_date 区间（按当前日期回推）
        today = date.today()
        if op == 'gt':
            return Resident.birth_date < date(today.year - int(value), today.month, today.day)
        if op == 'lt':
            return Resident.birth_date > date(today.year - int(value), today.month, today.day)
        if op == 'between':
            lo, hi = _parse_between(value)
            expr = None
            if hi is not None:
                expr = Resident.birth_date < date(today.year - int(hi), today.month, today.day)
            if lo is not None:
                lo_expr = Resident.birth_date > date(today.year - int(lo), today.month, today.day)
                expr = expr & lo_expr if expr is not None else lo_expr
            return expr
        raise ValueError('年龄支持 gt/lt/between')
    if field == 'resident.status':
        return _apply_op(Resident.status, op, value)
    if field == 'resident.grid':
        return _apply_op(Resident.village_group, op, value, like=True)
    if field == 'resident.tag':
        # 标签：ResidentTag EXISTS（status=有效 且 tag_code in）
        from models import ResidentTag
        tags = _parse_in(value)
        sub = (db.session.query(ResidentTag.id)
               .filter(ResidentTag.resident_id == Resident.id,
                       ResidentTag.status == '有效',
                       ResidentTag.tag_code.in_(tags)))
        return db.exists(sub)
    raise ValueError(f'未知居民字段: {field}')


def _sub_field(expr_factory, field, op, value):
    """对象/补贴/走访子表 EXISTS 条件（expr_factory(resident_id) → 过滤表达式）。"""
    sub = db.session.query(db.literal(1)).filter(expr_factory())
    return db.exists(sub)


def _object_field(field, op, value):
    from models.dibao import Dibao
    from models.disabled import Disabled
    from models.care_target import CareTarget, SpecialSupport
    from utils.crypto import sha256_hash

    def _dibao():
        if field == 'dibao.status':
            return db.and_(Dibao.resident_id == Resident.id, _apply_op(Dibao.status, op, value))
        return db.and_(Dibao.resident_id == Resident.id, _apply_op(Dibao.dibao_type, op, value, like=True))

    def _disabled():
        if field == 'disabled.status':
            return db.and_(Disabled.resident_id == Resident.id, _apply_op(Disabled.status, op, value))
        return db.and_(Disabled.resident_id == Resident.id, _apply_op(Disabled.disability_type, op, value, like=True))

    def _care():
        if field == 'care_target.type':
            return db.and_(CareTarget.resident_id == Resident.id, _apply_op(CareTarget.target_type, op, value))
        return db.and_(CareTarget.resident_id == Resident.id, _apply_op(CareTarget.child_type, op, value))

    def _special():
        # 无 resident_id：按 household_id_card_hash 直接匹配集合（R12 加密后原列 NULL，直接用哈希列）
        hashes = set()
        for sp in SpecialSupport.query.filter(SpecialSupport.status == '有效').all():
            if sp.support_mode and _match_value(sp.support_mode, op, value) and sp.household_id_card_hash:
                hashes.add(sp.household_id_card_hash)
        if not hashes:
            return Resident.id_card_hash == '__none__'
        return Resident.id_card_hash.in_(hashes)

    if field.startswith('dibao'):
        return db.exists(_dibao())
    if field.startswith('disabled'):
        return db.exists(_disabled())
    if field.startswith('care_target'):
        return db.exists(_care())
    if field.startswith('special_support'):
        return db.exists(_special())
    raise ValueError(f'未知对象字段: {field}')


def _subsidy_field(field, op, value):
    from models.subsidy_project import SubsidyRecord, SubsidyProject

    def _sub():
        base = SubsidyRecord.resident_id == Resident.id
        if field == 'subsidy.project':
            if op == 'eq':
                return db.and_(base, SubsidyRecord.project_id == int(value))
            if op == 'in':
                ids = [int(x) for x in _parse_in(value)]
                return db.and_(base, SubsidyRecord.project_id.in_(ids))
            raise ValueError('补贴项目支持 eq/in')
        if field == 'subsidy.period':
            return db.and_(base, _apply_op(SubsidyRecord.period, op, value))
        if field == 'subsidy.amount':
            return db.and_(base, _apply_op(SubsidyRecord.amount, op, value))
        raise ValueError(f'未知补贴字段: {field}')
    return db.exists(_sub())


def _visit_field(field, op, value):
    from models.visit import VisitRecord, VisitPlan

    def _sub():
        if field in ('visit.date', 'visit.type'):
            base = VisitRecord.resident_id == Resident.id
            if field == 'visit.date':
                d = value
                if not isinstance(d, (date, datetime)):
                    d = datetime.strptime(str(value), '%Y-%m-%d').date()
                return db.and_(base, _apply_op(VisitRecord.visit_date, op, d))
            return db.and_(base, _apply_op(VisitRecord.visit_type, op, value))
        if field == 'visit.status':
            # 走访状态在 VisitPlan
            return db.and_(VisitPlan.id == VisitRecord.plan_id,
                           VisitPlan.status == str(value),
                           VisitRecord.resident_id == Resident.id)
        raise ValueError(f'未知走访字段: {field}')
    return db.exists(_sub())


def _match_value(actual, op, value):
    """Python 侧值匹配（special_support 预计算用）。"""
    if op == 'eq':
        return str(actual) == str(value)
    if op == 'contains':
        return str(value) in str(actual)
    if op == 'in':
        return str(actual) in {str(x) for x in _parse_in(value)}
    return False


def _apply_op(col, op, value, like=False):
    """通用操作符应用。like=True 时 eq 退化为 contains（文本模糊）。"""
    if op == 'eq':
        if like and isinstance(value, str) and value:
            return col.like(f'%{value}%')
        return col == value
    if op == 'contains':
        return col.like(f'%{value}%')
    if op == 'gt':
        return col > value
    if op == 'lt':
        return col < value
    if op == 'between':
        lo, hi = _parse_between(value)
        expr = None
        if lo is not None:
            expr = col >= lo
        if hi is not None:
            hi_expr = col <= hi
            expr = hi_expr if expr is None else (expr & hi_expr)
        return expr
    if op == 'in':
        return col.in_(_parse_in(value))
    raise ValueError(f'不支持的操作符: {op}')


# ---- 条件树解析 ----

def build_filter(cond_tree):
    """递归解析条件树 → SQLAlchemy 过滤表达式。空树返回 None（不过滤）。"""
    if not cond_tree:
        return None
    logic = (cond_tree.get('logic') or 'AND').upper()
    conds = cond_tree.get('conditions') or []
    if not conds:
        return None
    exprs = []
    for c in conds:
        if 'conditions' in c or 'logic' in c:
            sub = build_filter(c)
            if sub is not None:
                exprs.append(sub)
        else:
            field = (c.get('field') or '').strip()
            op = (c.get('op') or '').strip().lower()
            value = c.get('value')
            if not field or op not in _OPS:
                raise ValueError(f'非法条件：field={field!r} op={op!r}')
            exprs.append(_resolve_field(field, op, value))
    if not exprs:
        return None
    if logic == 'OR':
        from functools import reduce
        return reduce(lambda a, b: a | b, exprs)
    from functools import reduce
    return reduce(lambda a, b: a & b, exprs)


def _resolve_field(field, op, value):
    if field.startswith('resident.'):
        return _resident_field(field, op, value)
    if field.startswith(('dibao.', 'disabled.', 'care_target.', 'special_support.')):
        return _object_field(field, op, value)
    if field.startswith('subsidy.'):
        return _subsidy_field(field, op, value)
    if field.startswith('visit.'):
        return _visit_field(field, op, value)
    raise ValueError(f'未知字段: {field}')


# ---- 查询执行 / 行输出 ----

PREVIEW_COLS = ['姓名', '身份证', '性别', '年龄', '状态', '所在组']


def run_query(cond_tree, can_view_sensitive=False, limit=500):
    """执行查询 → 预览行（脱敏）。返回 [dict]。"""
    from utils.crypto import key_holder
    from utils.masking import mask_id_card

    q = Resident.query
    expr = build_filter(cond_tree)
    if expr is not None:
        q = q.filter(expr)
    rows = []
    today = date.today()
    for r in q.order_by(Resident.village_group, Resident.name).limit(limit).all():
        id_card = ''
        if getattr(r, 'id_card_enc', None):
            try:
                id_card = key_holder.decrypt(r.id_card_enc)
            except Exception:
                id_card = ''
        if not id_card and getattr(r, 'id_card', None):
            # P2-2 整改：移除明文兜底，检测到残留明文仅告警不泄露
            import logging
            logging.getLogger('shucun').warning(
                '检测到未加密敏感字段 id_card（id=%s），请执行数据迁移清除明文残留', r.id)
        if not can_view_sensitive:
            id_card = mask_id_card(id_card) if id_card else ''
        age = ''
        if r.birth_date:
            age = str(today.year - r.birth_date.year)
        rows.append({
            'name': r.name or '',
            'id_card': id_card,
            'gender': r.gender or '',
            'age': age,
            'status': r.status or '',
            'grid': r.village_group or '',
        })
    return rows


def export_rows(cond_tree, can_view_sensitive=False, limit=2000):
    """导出用行列（表头 + 数据）。"""
    rows = run_query(cond_tree, can_view_sensitive=can_view_sensitive, limit=limit)
    data = [[r['name'], r['id_card'], r['gender'], r['age'], r['status'], r['grid']] for r in rows]
    return PREVIEW_COLS, data
