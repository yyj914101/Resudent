# -*- coding: utf-8 -*-
"""utils.warn_service — 到期预警业务唯一 owner（Sprint5 T19，R8）。

规格依据（PRD v1.2.3 R8 L576-602 + Sprint5 §3 T19 + §12.3 口径锚点，2026-08-27 拍板）：
- 三触发点（PRD L588，用户拍板按 PRD）：
  ① 周期到期（cycle→阈值天数：月40/季100/年400，一次性不触发；距最近发放超阈值未见新发放）
  ② 年龄边界（居民周岁 ≥ 项目 age_max）
  ③ 停发确认（项目停用但存在在册对象）
- 在册对象 = 项目下存在 status='正常' 的 subsidy_record 的居民；扫描排除死亡居民
- 幂等：同 (resident_id, project_id, warn_type) 已有 status='未处置' 则跳过
- 预警生成同步写 TodoItem（todo_type='warn'，related_id=warn_id，对齐 auto-reminder 范式）
- 死亡自动关闭（R8-E4）：close_warnings_for_death 由 resident.api_status 死亡分支同事务调用

owner map：预警判定/处置唯一落本模块（禁在 routes/ 内重写判定逻辑）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from models import Resident, SubsidyProject, SubsidyRecord, TodoItem, WarnRecord, db
from utils.care_age import calculate_age

# 周期 → 到期阈值天数（用户拍板：月40/季100/年400；一次性不触发）
CYCLE_DAYS = {'月': 40, '季': 100, '年': 400}

# warn_type 值（单源常量）
WARN_CYCLE = '周期到期'
WARN_AGE = '年龄边界'
WARN_STOP = '停发确认'
WARN_TYPES = (WARN_CYCLE, WARN_AGE, WARN_STOP)

# 状态值域（对齐详细设计 L372 注释）
STATUS_PENDING = '未处置'
STATUS_HANDLED = '已处置'
STATUS_CLOSED = '已关闭'

# todo 接入（todo_type='warn'，对齐 auto-reminder 范式）
TODO_TYPE_WARN = 'warn'
# 死亡自动关闭结果文案（R8-E4）
DEATH_RESULT = '已注销停发'


def cycle_overdue_days(project):
    """项目 cycle → 到期阈值天数；一次性/未知 → None（不触发周期到期）。"""
    return CYCLE_DAYS.get(project.cycle)


def _resident_ids_of_project(project_id):
    """在册居民 id 集合：该项目存在 status='正常' 发放记录的居民（去重）。"""
    rows = SubsidyRecord.query.filter(
        SubsidyRecord.project_id == project_id, SubsidyRecord.status == '正常'
    ).with_entities(SubsidyRecord.resident_id).distinct().all()
    return [r for (r,) in rows]


def _alive(resident):
    """扫描排除死亡居民（预警对象须在册且在世）。"""
    return resident is not None and resident.status != 'deceased'


def _sync_todo(warn):
    """预警生成时同步写 TodoItem（todo_type='warn'，related_id=warn.id）。"""
    resident = Resident.query.get(warn.resident_id)
    project = SubsidyProject.query.get(warn.project_id) if warn.project_id else None
    title = f"{resident.name if resident else '居民'}-{project.name if project else '补贴'}-{warn.warn_type}"
    db.session.add(TodoItem(
        title=title[:100],
        content=warn.trigger_reason or '',
        todo_type=TODO_TYPE_WARN,
        status='todo',
        related_id=warn.id,
    ))


def _pending_keys():
    """已存在未处置预警键集合 {(resident_id, project_id, warn_type)}（幂等判定）。"""
    rows = WarnRecord.query.filter(WarnRecord.status == STATUS_PENDING).with_entities(
        WarnRecord.resident_id, WarnRecord.project_id, WarnRecord.warn_type).all()
    return {(r, p, t) for (r, p, t) in rows}


def generate_warnings():
    """三触发点全量扫描 + 幂等生成预警（惰性巡检 lazy_check 调用）。

    返回 {'new': 新增预警数}；全部写入单事务。
    """
    pending = _pending_keys()
    created = 0

    for p in SubsidyProject.query.all():
        resident_ids = _resident_ids_of_project(p.id)

        # ③ 停发确认：项目停用但存在在册对象（PRD L588）
        if p.status == '停用':
            for rid in resident_ids:
                if (rid, p.id, WARN_STOP) in pending:
                    continue
                res = Resident.query.get(rid)
                if not _alive(res):
                    continue
                w = WarnRecord(resident_id=rid, project_id=p.id, warn_type=WARN_STOP,
                               trigger_reason=f'项目「{p.name}」已停用，仍有在册对象，请确认停发')
                db.session.add(w)
                db.session.flush()
                _sync_todo(w)
                created += 1
            continue

        if p.status != '启用':
            continue

        # ① 周期到期：距最近发放超阈值未见新发放（一次性不触发）
        threshold = cycle_overdue_days(p)
        if threshold is not None:
            for rid in resident_ids:
                if (rid, p.id, WARN_CYCLE) in pending:
                    continue
                res = Resident.query.get(rid)
                if not _alive(res):
                    continue
                last_date = db.session.query(db.func.max(SubsidyRecord.issue_date)).filter(
                    SubsidyRecord.project_id == p.id,
                    SubsidyRecord.resident_id == rid,
                    SubsidyRecord.status == '正常',
                ).scalar()
                if last_date is None:
                    continue
                days = (date.today() - last_date).days
                if days > threshold:
                    w = WarnRecord(resident_id=rid, project_id=p.id, warn_type=WARN_CYCLE,
                                   trigger_reason=f'项目「{p.name}」距最近发放已{days}天'
                                                  f'(周期{p.cycle}，阈值{threshold}天)，未见新发放')
                    db.session.add(w)
                    db.session.flush()
                    _sync_todo(w)
                    created += 1

        # ② 年龄边界：居民周岁 ≥ 项目 age_max（PRD L588）
        if p.age_max is not None:
            for rid in resident_ids:
                if (rid, p.id, WARN_AGE) in pending:
                    continue
                res = Resident.query.get(rid)
                if not _alive(res):
                    continue
                age = calculate_age(res.birth_date)
                if age is not None and age >= p.age_max:
                    w = WarnRecord(resident_id=rid, project_id=p.id, warn_type=WARN_AGE,
                                   trigger_reason=f'年龄{age}已达项目「{p.name}」上限{p.age_max}')
                    db.session.add(w)
                    db.session.flush()
                    _sync_todo(w)
                    created += 1

    db.session.commit()
    return {'new': created}


def handle_warning(warn_id, result=None, operator_id=None):
    """处置预警（未处置 → 已处置），同步 dismiss 关联 warn todo；幂等。"""
    warn = WarnRecord.query.get(warn_id)
    if not warn:
        return False, '预警不存在'
    if warn.status != STATUS_PENDING:
        return False, '预警已处置或已关闭'
    warn.status = STATUS_HANDLED
    warn.handle_result = result or '已处置'
    warn.handled_by = operator_id
    warn.handled_at = datetime.now()
    todo = TodoItem.query.filter_by(todo_type=TODO_TYPE_WARN, related_id=warn.id,
                                    status='todo').first()
    if todo:
        todo.status = 'dismissed'
        todo.completed_at = datetime.now()
        todo.completed_by = str(operator_id) if operator_id else ''
    db.session.commit()
    return True, '处置成功'


def close_warnings_for_death(resident_id):
    """死亡自动关闭（R8-E4）：该居民全部未处置预警 → 已关闭，dismiss 关联 todo；幂等。

    由 resident.api_status 置 deceased 分支同事务调用（不自行 commit 由调用方控制）。
    返回关闭条数。
    """
    warns = WarnRecord.query.filter_by(resident_id=resident_id, status=STATUS_PENDING).all()
    closed = 0
    for w in warns:
        w.status = STATUS_CLOSED
        w.handle_result = DEATH_RESULT
        w.handled_at = datetime.now()
        todo = TodoItem.query.filter_by(todo_type=TODO_TYPE_WARN, related_id=w.id,
                                        status='todo').first()
        if todo:
            todo.status = 'dismissed'
            todo.completed_at = datetime.now()
        closed += 1
    return closed


def overdue_warn_todo_ids():
    """7 日未处置的 warn 类 todo id 集合（US-1 二期口径：7 日未处置置顶标红）。"""
    cutoff = datetime.now() - timedelta(days=7)
    rows = TodoItem.query.filter(
        TodoItem.todo_type == TODO_TYPE_WARN,
        TodoItem.status == 'todo',
        TodoItem.created_at < cutoff,
    ).with_entities(TodoItem.id).all()
    return {r for (r,) in rows}
