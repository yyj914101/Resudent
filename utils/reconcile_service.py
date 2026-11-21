# -*- coding: utf-8 -*-
"""T15-4 死亡名单对账服务（R5-E3/B4/O3，owner=utils/）。

公安/殡葬死亡名单 → 四分类比对（2026-08-28 主理人拍板，PRD 三分类细化留痕）：
  已登记（名单行，系统档案 status=deceased）
  疑似未登记（名单行，系统在册未死亡）
  无档案（名单行，系统无居民档案）
  名单外（系统 deceased 但名单无，全量补集比对）
季度计划 B4 双触发（季度首启惰性检查 + 导入名单触发）；
源名单 ≤30 天限期删除（U5 已确认默认 ≤30 天，M6.0 C3），仅留比对结果。
幂等：ReconcileTask.source_hash 唯一（NULL 不参与）；处置/清理均可重跑。
"""
import io
import json
import os
from datetime import datetime, date, timedelta

from models import db


# ============ 导入解析 ============

def parse_xlsx(file_bytes):
    """解析对账名单 xlsx（模板：身份证+姓名+死亡日期）。

    缺身份证列抛 ValueError（端点转 400）；跳过空行；死亡日期容错解析。
    返回 [{name, id_card, death_date}]。
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    header = [str(c.value or '').strip() for c in next(ws.iter_rows(min_row=1, max_row=1))]

    def col_idx(*keys):
        for i, h in enumerate(header):
            for k in keys:
                if k in h:
                    return i
        return -1

    i_id = col_idx('身份证', '证件号码')
    if i_id < 0:
        raise ValueError('对账名单缺少身份证列（模板要求：身份证 + 姓名 + 死亡日期）')
    i_name = col_idx('姓名')
    i_death = col_idx('死亡日期', '死亡时间')

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        idc = str(row[i_id] or '').strip()
        if not idc:
            continue
        name = ''
        if i_name >= 0 and i_name < len(row) and row[i_name]:
            name = str(row[i_name]).strip()
        death = None
        if i_death >= 0 and i_death < len(row) and row[i_death]:
            death = _parse_date(row[i_death])
        rows.append({'name': name, 'id_card': idc, 'death_date': death})

    if not rows:
        raise ValueError('名单无有效数据行')
    return rows


def _parse_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y%m%d'):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except (ValueError, TypeError):
            continue
    return None


# ============ 四分类比对（异步执行体） ============

def compare_batch(task_id, reconcile_task_id=None, **kwargs):
    """对账名单比对执行体（run_async 调用，task_id 为 A1 任务 id）。

    名单行 → id_card_hash 检索居民：deceased→已登记 / 在册未死→疑似未登记 /
    无档案→无档案；名单外 = 系统全量 deceased − 名单 hash 集合（全量补集，2026-08-28 拍板）。
    落 reconcile_result（密文+hash）+ result_summary 四类计数 + status='done' + 季度联动。
    """
    from utils.async_tasks import _update
    from models.reconcile import ReconcileTask, ReconcileResult
    from models.resident import Resident
    from models.todo import TodoItem
    from utils.crypto import key_holder, sha256_hash

    rt = ReconcileTask.query.get(reconcile_task_id)
    if not rt:
        _update(task_id, status='failed', result_summary='对账任务不存在')
        return
    if not rt.import_file or not os.path.isfile(rt.import_file):
        _update(task_id, status='failed', result_summary='源名单文件不存在')
        return

    if not key_holder.is_initialized:
        _update(task_id, status='failed', result_summary='密钥未解锁，无法加密写入')
        return

    try:
        with open(rt.import_file, 'rb') as f:
            rows = parse_xlsx(f.read())
    except ValueError as e:
        _update(task_id, status='failed', result_summary=f'名单解析失败: {e}')
        return

    results = []
    list_hashes = set()
    for r in rows:
        h = sha256_hash(r['id_card'])
        list_hashes.add(h)
        resident = Resident.query.filter_by(id_card_hash=h).first()
        if resident and resident.status == 'deceased':
            match = '已登记'
        elif resident:
            match = '疑似未登记'
        else:
            match = '无档案'
        results.append(ReconcileResult(
            task_id=rt.id,
            name=r['name'] or (resident.name if resident else ''),
            death_date=r['death_date'],
            id_card_enc=key_holder.encrypt(r['id_card']),
            id_card_hash=h,
            match_type=match,
        ))

    # 名单外：系统全量 deceased − 名单 hash 集合（全量补集）
    for res in Resident.query.filter_by(status='deceased').all():
        if res.id_card_hash and res.id_card_hash not in list_hashes:
            results.append(ReconcileResult(
                task_id=rt.id,
                name=res.name,
                death_date=None,
                id_card_enc=res.id_card_enc,
                id_card_hash=res.id_card_hash,
                match_type='名单外',
            ))

    for r in results:
        db.session.add(r)

    counts = {'已登记': 0, '疑似未登记': 0, '无档案': 0, '名单外': 0}
    for r in results:
        counts[r.match_type] = counts.get(r.match_type, 0) + 1
    rt.result_summary = json.dumps(counts, ensure_ascii=False)
    rt.status = 'done'

    # 季度联动：同 quarter 的季度计划任务置 done + 待办 dismiss
    if rt.quarter:
        q_task = ReconcileTask.query.filter_by(
            source='季度计划', quarter=rt.quarter, status='pending').first()
        if q_task:
            q_task.status = 'done'
            todo = TodoItem.query.filter_by(todo_type='reconcile', related_id=q_task.id, status='todo').first()
            if todo:
                todo.status = 'dismissed'
                todo.completed_at = datetime.now()

    db.session.commit()
    _update(task_id, status='done', result_summary=rt.result_summary, finished_at=datetime.now())


# ============ 季度计划（B4 双触发：季度首启惰性检查） ============

def current_quarter():
    now = datetime.now()
    return f'{now.year}-Q{(now.month - 1) // 3 + 1}'


def ensure_quarter_task():
    """季度首启检查：本季无对账任务 → 建 source='季度计划' 任务 + 首页待办。幂等。"""
    from models.reconcile import ReconcileTask
    from models.todo import TodoItem

    q = current_quarter()
    exists = ReconcileTask.query.filter_by(quarter=q).first()
    if exists:
        return None
    rt = ReconcileTask(source='季度计划', task_date=date.today(), quarter=q, status='pending')
    db.session.add(rt)
    db.session.flush()
    db.session.add(TodoItem(
        title=f'本季对账未执行（{q}）',
        content='季度首启检查：本季度尚未执行公安/殡葬死亡名单对账，请导入名单或确认无需对账',
        todo_type='reconcile', status='todo', related_id=rt.id,
    ))
    db.session.commit()
    return rt.id


# ============ 源名单 ≤30 天限期删除（U5，M6.0 C3） ============

def purge_source_file(task):
    """手动删源名单：删除导入文件 + 置 source_deleted_at（仅留比对结果）。"""
    import os as _os
    if task.import_file and _os.path.isfile(task.import_file):
        _os.remove(task.import_file)
    task.source_deleted_at = datetime.now()
    db.session.commit()


def cleanup_expired_sources(days=30):
    """周期清理：超 30 天的源名单自动删除（对齐 T07 cleanup_temp_exports 模式）。"""
    from models.reconcile import ReconcileTask

    cutoff = datetime.now() - timedelta(days=days)
    tasks = ReconcileTask.query.filter(
        ReconcileTask.source_deleted_at.is_(None),
        ReconcileTask.import_file.isnot(None),
        ReconcileTask.created_at < cutoff,
    ).all()
    for t in tasks:
        if t.import_file and os.path.isfile(t.import_file):
            os.remove(t.import_file)
        t.source_deleted_at = datetime.now()
    if tasks:
        db.session.commit()
    return len(tasks)


# ============ 存量库补列（DDL 纪律：不手改库文件） ============

def ensure_reconcile_columns():
    """存量库幂等补列（T15-4：source_hash / name / death_date）。

    P2-4 整改：复用 utils.schema_ensure 幂等补列；唯一索引独立幂等创建
    （CREATE UNIQUE INDEX IF NOT EXISTS 天然幂等，无条件执行）。
    """
    from sqlalchemy import text
    from utils.schema_ensure import ensure_columns

    ensure_columns('reconcile_task', {'source_hash': 'VARCHAR(64)'})
    db.session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_reconcile_task_source_hash "
        "ON reconcile_task(source_hash)"))

    ensure_columns('reconcile_result', {
        'name': 'VARCHAR(50)',
        'death_date': 'DATE',
    })
