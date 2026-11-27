# -*- coding: utf-8 -*-
"""utils.async_tasks — A1 单机后台任务框架（T05/T02）。

规格依据（详细设计 v1.1 §1.3 + PRD 12.1 A1）：
- 进程内 worker 线程 + async_task 状态表（pending→running→done/failed）
- 同类任务互斥（进程内锁，防并发重入）
- 前端轮询 status/progress/result_summary
- 任务落库可追溯；失败可重进

移植自 m0-verification/verify/p11_async_task.py（P-11 PASS），
接入主工程：async_task 表由 models/ 声明（SQLAlchemy），worker 通过 db.session 写库。
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime

from models import db
from models.task import AsyncTask

# 进程内互斥锁：按 task_type 防并发重入（PRD A1 ④）
_task_locks: dict[str, threading.Lock] = {}
_task_locks_guard = threading.Lock()


def _lock_for(task_type: str) -> threading.Lock:
    with _task_locks_guard:
        return _task_locks.setdefault(task_type, threading.Lock())


def is_task_busy(task_type: str) -> bool:
    """判断同类后台任务锁当前是否被占用（供调用方前置拒入，避免落孤儿 pending 批次）。"""
    return _lock_for(task_type).locked()


def sync_failed_pending_batches(batches) -> int:
    """自愈：将 status=='pending' 但绑定 AsyncTask 已 failed 的业务批次同步回写 failed。"""
    pending = [b for b in (batches or []) if getattr(b, "status", None) == "pending" and getattr(b, "task_id", None)]
    if not pending:
        return 0
    tids = list({b.task_id for b in pending})
    failed_tasks = {
        t.task_id: t for t in AsyncTask.query.filter(
            AsyncTask.task_id.in_(tids), AsyncTask.status == "failed"
        ).all()
    }
    if not failed_tasks:
        return 0
    healed = 0
    now = datetime.now()
    for b in pending:
        t = failed_tasks.get(b.task_id)
        if t is not None:
            b.status = "failed"
            if hasattr(b, "finished_at") and not b.finished_at:
                b.finished_at = t.finished_at or now
            healed += 1
    if healed:
        db.session.commit()
    return healed


def create_task(task_type: str, operator: str, title: str = "") -> AsyncTask:
    """创建任务记录（pending）并返回模型实例（未提交，由调用方统一 commit）。"""
    task = AsyncTask(
        task_id=uuid.uuid4().hex[:12],
        task_type=task_type,
        title=title or task_type,
        status="pending",
        progress=0,
        operator=operator,
    )
    db.session.add(task)
    return task


def get_task(task_id: str) -> AsyncTask | None:
    """查询任务（强制过期重读，避免同 session identity map 返回旧缓存）。

    后台线程写库后，主线程/轮询须看到最新状态；expire_all 保证每次读最新。
    """
    db.session.expire_all()
    return AsyncTask.query.filter_by(task_id=task_id).first()


def _update(task_id: str, **fields) -> None:
    task = AsyncTask.query.filter_by(task_id=task_id).first()
    if task is None:
        return
    for k, v in fields.items():
        setattr(task, k, v)
    db.session.commit()
    db.session.expire_all()  # 提交后清除缓存，供其他线程/轮询读到最新


def run_async(task_type: str, task_id: str, fn, *args, **kwargs):
    """提交后台任务。

    语义：
    - 同 task_type 已在运行 → 立即 failed（并发互斥）
    - 否则起 daemon 线程执行 fn(task_id, *args, **kwargs)，执行体自行调 _update 更新进度
    - fn 内抛异常 → failed 并记录原因（可重进）

    用法：
        task = create_task("import_3000", operator, "导入")
        db.session.commit()
        run_async("import_3000", task.task_id, my_import_fn, file_path=...)

    SQLAlchemy 上下文：worker 线程内自动 push 应用上下文（app ctx），
    fn 内部如需访问 db 直接用 db.session 即可（同一线程）。
    """
    lock = _lock_for(task_type)
    if not lock.acquire(blocking=False):
        _update(task_id, status="failed",
                result_summary="并发互斥：同类任务已在运行",
                finished_at=datetime.now())
        return False

    def _runner():
        # create_app() 移入 worker 线程：避免主线程提交时被
        # db.create_all() 等重量级初始化污染，保证 run_async 真非阻塞。
        # R-035-03：这里只是借 app context，不是进程启动——跳过启动期清理，
        # 否则每个后台任务都会把正在执行的导入批次误标 aborted。
        from app import create_app  # 延迟导入避免循环依赖
        _app = create_app(skip_startup_hooks=True)
        try:
            with _app.app_context():
                _update(task_id, status="running", started_at=datetime.now())
                fn(task_id, *args, **kwargs)
        except Exception as e:  # 结构化失败记录（禁裸吞错）
            try:
                with _app.app_context():
                    _update(task_id, status="failed",
                            result_summary=f"错误: {type(e).__name__}: {e}",
                            finished_at=datetime.now())
            except Exception:
                pass
        finally:
            lock.release()

    threading.Thread(target=_runner, daemon=True, name=f"async-{task_type}").start()
    return True


# ============ 周期任务（T07：temp/exports 清理，B11 闭环） ============

_periodic_threads: list = []


def register_periodic(task_type: str, fn, interval_seconds: int = 86400):
    """注册周期任务（daemon 线程 + sleep 循环）。

    语义：
    - 启动后立即执行一次，随后每 interval_seconds 秒执行一次
    - daemon 线程，进程退出即终止
    - 异常不中断循环（记录后继续）

    用法（app.py create_app 末尾）：
        from utils.async_tasks import register_periodic, cleanup_temp_exports
        register_periodic("cleanup_exports", cleanup_temp_exports, 86400)
    """
    def _loop():
        while True:
            try:
                fn()
            except Exception as e:  # 禁裸吞错，但周期任务不中断
                import sys
                print(f"[periodic:{task_type}] error: {type(e).__name__}: {e}",
                      file=sys.stderr)
            import time as _time
            _time.sleep(interval_seconds)

    t = threading.Thread(target=_loop, daemon=True, name=f"periodic-{task_type}")
    t.start()
    _periodic_threads.append(t)
    return t


def cleanup_temp_exports():
    """清理 temp/exports/ 下 >7 天的导出文件（B11 闭环）。"""
    import os
    import time

    # 优先从环境变量读（测试隔离友好），回退到 Config
    exports_dir = os.environ.get('SHUCUN_TEMP_DIR')
    if exports_dir:
        exports_dir = os.path.join(exports_dir, 'exports')
    else:
        from config import Config
        exports_dir = getattr(Config, 'EXPORTS_DIR', None)
    if not exports_dir or not os.path.isdir(exports_dir):
        return {'deleted': 0, 'skipped': 0}

    now = time.time()
    max_age = 7 * 24 * 3600  # 7 天
    deleted = 0
    skipped = 0

    for name in os.listdir(exports_dir):
        fpath = os.path.join(exports_dir, name)
        if not os.path.isfile(fpath):
            continue
        try:
            mtime = os.path.getmtime(fpath)
            if now - mtime > max_age:
                os.remove(fpath)
                deleted += 1
            else:
                skipped += 1
        except OSError:
            skipped += 1

    if deleted:
        import sys
        print(f"[cleanup_temp_exports] deleted {deleted} files (>7d), "
              f"skipped {skipped}", file=sys.stderr)

    return {'deleted': deleted, 'skipped': skipped}


def cleanup_temp_imports():
    """清理 temp/imports/ 下 >7 天的明文导入临时文件（§2.5 第 7 步，T02 闭环）。"""
    import os
    import time

    imports_dir = os.environ.get('SHUCUN_TEMP_DIR')
    if imports_dir:
        imports_dir = os.path.join(imports_dir, 'imports')
    else:
        from config import Config
        imports_dir = getattr(Config, 'IMPORTS_DIR', None)
    if not imports_dir or not os.path.isdir(imports_dir):
        return {'deleted': 0, 'skipped': 0}

    now = time.time()
    max_age = 7 * 24 * 3600  # 7 天
    deleted = 0
    skipped = 0
    for name in os.listdir(imports_dir):
        fpath = os.path.join(imports_dir, name)
        if not os.path.isfile(fpath):
            continue
        try:
            if now - os.path.getmtime(fpath) > max_age:
                os.remove(fpath)
                deleted += 1
            else:
                skipped += 1
        except OSError:
            skipped += 1
    if deleted:
        import sys
        print(f"[cleanup_temp_imports] deleted {deleted} files (>7d), "
              f"skipped {skipped}", file=sys.stderr)
    return {'deleted': deleted, 'skipped': skipped}


# ============ T19：到期预警惰性巡检（R8-E0 lazy_check，A1 架构） ============

def run_lazy_check(task_id, **kw):
    """lazy_check 处理器：三触发点全量扫描生成预警（异步，不进首页请求链）。

    由 run_async 在 worker 线程调用；异常由 run_async 捕获置 failed。
    """
    from utils.warn_service import generate_warnings
    _update(task_id, status="running", progress=10, started_at=datetime.now())
    result = generate_warnings()
    _update(task_id, status="done", progress=100,
            result_summary=f"预警巡检完成，新增 {result.get('new', 0)} 条",
            finished_at=datetime.now())
    return result


def lazy_check_periodic():
    """每日周期触发（register_periodic 86400s）：创建 lazy_check 任务并异步执行。

    前置检查锁状态避免启动期撞车产生多余 failed 噪音行；并发窗口内仍由 run_async 兜底互斥。
    """
    if is_task_busy("lazy_check"):
        return
    task = create_task("lazy_check", "system", "到期预警巡检")
    db.session.commit()
    run_async("lazy_check", task.task_id, run_lazy_check)


# ============ T20-3：备份惰性提醒（R9-E4，每日首启补检） ============

def backup_reminder_check():
    """每日首启检查：距上次 .wbak 备份超配置周期（默认 7 天）→ 首页待办提醒。

    - 周期线程独立于请求线程，异步执行不阻塞首页（巡检期间 P95<2s）
    - 幂等：已有未处置的 backup 待办不重复建
    - 配置：DictItem 系统设置 backup_reminder_days（默认 7）
    """
    import os
    import time

    from config import Config
    backup_dir = getattr(Config, 'BACKUP_FOLDER', None) or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backups')

    # 读配置周期（默认 7 天）
    reminder_days = 7
    try:
        from utils.settings_store import get_settings_value
        raw = get_settings_value('backup_reminder_days', '7')
        reminder_days = max(1, int(raw))
    except (TypeError, ValueError):
        reminder_days = 7

    # 最近备份时间
    from utils.backup_crypto import latest_wbak
    latest = latest_wbak(backup_dir)
    overdue = False
    days_since = None
    if latest is None:
        overdue = True  # 从未备份
    else:
        days_since = (time.time() - latest[1]) / 86400.0
        overdue = days_since > reminder_days

    if not overdue:
        return {'overdue': False}

    # 幂等：已有未处置 backup 待办则跳过
    from models.todo import TodoItem
    existing = TodoItem.query.filter_by(todo_type='backup', status='todo').first()
    if existing:
        return {'overdue': True, 'existing': True}

    if latest is None:
        title = '从未执行加密备份'
        content = f'系统尚未生成 .wbak 加密备份，请尽快在「数据库安全」页执行备份（建议周期 {reminder_days} 天）'
    else:
        title = f'距上次加密备份已超 {int(days_since)} 天'
        content = f'最近备份：{os.path.basename(latest[0])}（{time.strftime("%Y-%m-%d %H:%M", time.localtime(latest[1]))}），请及时执行 .wbak 加密备份'
    db.session.add(TodoItem(title=title, content=content, todo_type='backup',
                            status='todo'))
    db.session.commit()
    return {'overdue': True, 'created': True}
