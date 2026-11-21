# -*- coding: utf-8 -*-
"""utils/softtable_batch.py — 107 软表批量导入分批执行体（模式 A｜ADR-031/ADR-025）

「大表不阻塞请求」：确认页提交后建 A1 任务（task_type=softtable_batch），后台按
BATCH_SIZE 分批调 roster_facade.import_records（校验/加密/指纹全在引擎与门面口径内，
每批一提交），失败行只进 softtable_batch_error（零原值），进度经 _update 单调递增。

106 编排层约定对齐：
- 五端点范式：上传/确认复用既有 api_upload/api_meta（token 通道）；本模块提供
  import-execute / import-status / import-report / import-export-failed（routes/dynatable.py）。
- 幂等三件套：判重键 (module_key, table_code, source_hash)（路由层查）；进程重启残留
  running 批次由启动钩子 mark_aborted_stale 标 aborted（app.py）；整批重跑不产生重复
  记录——success_rows>0 的失败批次拒绝重跑（execute 端点 409），0 行入库的失败批次
  可重进（execute 复用同批次行、清旧错误行、起新 A1 任务）。
- 状态映射：AsyncTask.pending/running/done/failed ↔ 批次 pending/running/done/failed；
  aborted 仅表示进程重启遗留，不是 AsyncTask 状态。
- 台账元数据（dynamic_table/dynamic_field）由 execute 端点经 facade.create_table 同步建
  （几行元数据，秒级），后台只灌数据——任务失败时台账已存在、部分行入库即"部分成功"，
  重进语义见上；跨模块 code 经 facade.get_table 一律按不存在处理（ADR-027）。

阈值依据（BATCH_SIZE / ASYNC_ROW_THRESHOLD / MAX_UPLOAD_BYTES 维持 20MB）见
docs/tasks/107-RESULT.md——本文件常量与 RESULT 数字一一对应。
"""
from __future__ import annotations

import os
from datetime import datetime

from models import SofttableBatchError, SofttableBatchImport, db
from utils import roster_facade as facade
from utils import softtable_engine as eng

# A1 任务类型（106 §3.1 登记项：模式 A、执行体=本文件 run_softtable_batch、
# 批次模型=softtable_batch_import/error、业务唯一键=表 code 全局唯一 +
# 判重键 (module_key, table_code, source_hash)、失败行表=softtable_batch_error、
# 权限归类=dynatable（ADR-016，方法位自动映射））
TASK_TYPE = 'softtable_batch'

# 每批行数：一批 = 一次事务（500 行约亚秒级，SQLite 写锁不长期占用，
# 进度回执每 ≤500 行更新一次，轮询平滑）。依据实测见 107-RESULT.md。
BATCH_SIZE = 500

# 异步路由阈值：数据行数 > 阈值走 import-execute 异步分批，否则走 api_create 同步
# （模式 A 的小表性能分支）。单一真源在本文件，经 api_meta 以 async_min_rows 下发前端，
# 前端不维护第二份常量。依据实测见 107-RESULT.md。
ASYNC_ROW_THRESHOLD = 2000


def add_batch_error(batch_id, row_no, field_name, reason):
    """失败行进暂存表（只存 行号+列名+原因，不存任何行原值——无明文可泄）。"""
    db.session.add(SofttableBatchError(
        batch_id=batch_id, row_no=int(row_no),
        field_name=(str(field_name) if field_name else '')[:100],
        reason=str(reason)[:300]))


def mark_aborted_stale():
    """进程重启后，残留 running 的软表导入批次标 aborted（幂等三件套之一）。

    仅允许在进程启动路径调用（app.py 启动钩子）；worker 线程
    create_app(skip_startup_hooks=True) 不会触发本函数（106 §2.2）。
    """
    stale = SofttableBatchImport.query.filter_by(status='running').all()
    for batch in stale:
        batch.status = 'aborted'
    if stale:
        db.session.commit()
    return len(stale)


def run_softtable_batch(task_id, batch_id):
    """软表分批导入 A1 处理函数（run_async 约定：fn(task_id, **kwargs)，已处 app context 内）。

    台账与字段由 execute 端点经 facade.create_table 预先建好；本函数只灌数据：
    解析源文件 → 按 BATCH_SIZE 分批 facade.import_records（每批一提交）→
    失败行入 softtable_batch_error → 进度/计数经 _update 回执。
    """
    from utils.async_tasks import _update

    batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
    if batch is None:
        raise RuntimeError('软表导入批次不存在：%s' % batch_id)
    if batch.status == 'done':
        return                                     # 幂等：已完成批次不重跑

    # 门面边界（ADR-027）：跨 module_key 的 code 一律按不存在处理
    table = facade.get_table(batch.module_key, batch.table_code)
    if table is None:
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='台账不存在或归属不符（跨模块 code 已拒绝）')
        return

    specs = facade.field_specs(table)
    if not specs:
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='台账尚无字段行（未建成），无法导入')
        return

    # 重进防护：已有行入库的批次重跑会重复入库（软表无业务唯一键，仅 identity 有指纹
    # 且不覆盖全字段），0 行入库的失败批次由 execute 端点重置后重进，不会走到这里
    if (batch.status in ('failed', 'aborted')) and (batch.success_rows or 0) > 0:
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='该批次已有 %d 行入库，整批重跑会产生重复记录；'
                               '请更换台账编码后重新导入，或修改文件内容后重新上传' % batch.success_rows)
        return

    if not batch.source_path or not os.path.exists(batch.source_path):
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='源文件已失效，请重新上传后再执行导入')
        return

    try:
        with open(batch.source_path, 'rb') as fh:
            _headers, rows = eng.parse_table_file(batch.source_file or 'upload.xlsx', fh)
    except eng.SoftTableError as exc:
        batch.status = 'failed'
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='文件解析失败：%s' % exc)
        return

    total = len(rows)
    batch.status = 'running'
    batch.total_rows = total
    db.session.commit()
    _update(task_id, progress=0, result_summary='开始分批导入 %d 行' % total)

    success = 0
    fail = 0
    try:
        for start in range(0, total, BATCH_SIZE):
            chunk = rows[start:start + BATCH_SIZE]
            # 门面口径内写库：build_records（校验/加密/指纹）+ 插 dynamic_record + commit
            imported, errors = facade.import_records(
                table, specs, chunk, skip_error_rows=True, created_by=batch.operator)
            success += imported
            # e['row'] 为块内 1 基数据行号 → 加块偏移得全文件数据行号（与同步路径
            # api_create 错误提示「第 N 行」同口径；表头本身不占号）
            for e in errors:
                add_batch_error(batch_id, start + e['row'], e.get('field', ''), e.get('msg', ''))
            fail += len(errors)
            db.session.commit()
            done_rows = min(start + BATCH_SIZE, total)
            _update(task_id, progress=min(99, int(done_rows * 100 / total)),
                    result_summary='已处理 %d/%d 行（成功 %d，失败 %d）'
                                   % (done_rows, total, success, fail))
    except Exception as exc:                       # 批间异常：批次收口 failed，计数与库一致
        db.session.rollback()
        batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
        batch.status = 'failed'
        batch.success_rows = success
        batch.fail_rows = fail
        batch.finished_at = datetime.now()
        db.session.commit()
        _update(task_id, status='failed', finished_at=datetime.now(),
                result_summary='分批导入中止（已入库 %d 行）：%s: %s'
                               % (success, type(exc).__name__, exc))
        return

    batch = SofttableBatchImport.query.filter_by(batch_id=batch_id).first()
    batch.status = 'done'
    batch.success_rows = success
    batch.fail_rows = fail
    batch.finished_at = datetime.now()
    db.session.commit()
    # 完成即清源文件（temp/softtable/ token 件）；失败批次保留源文件供 0 行重进
    try:
        if os.path.exists(batch.source_path):
            os.remove(batch.source_path)
    except OSError:
        pass
    _update(task_id, status='done', progress=100,
            result_summary='导入完成：成功 %d 行，失败 %d 行' % (success, fail),
            finished_at=datetime.now())
