# -*- coding: utf-8 -*-
"""A1 单机后台任务 PoC 测试（T05 验收）。

验证 P-11 三指标（详细设计 §8 T05 验收标准）：
1. POST 立即返回（非阻塞）：提交任务后主线程不等待任务完成
2. 轮询轨迹完整：pending→running→done
3. 失败可重进：注入失败 → 记录原因 → 重试成功
+ 并发互斥：同 task_type 已在运行 → 立即 failed
"""
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

import pytest


@pytest.fixture()
def app_ctx(isolated_app_factory):
    """GLB-05/17：隔离环境由 conftest 工厂统一建立（不再模块级 setdefault 固化 /tmp 路径）。"""
    app = isolated_app_factory()
    with app.app_context():
        yield app


def test_async_task_full_flow(app_ctx):
    """非阻塞 + 轮询轨迹 + 落库可追溯。"""
    from utils import async_tasks
    from utils.async_tasks import create_task, get_task, run_async, _update
    from models.task import AsyncTask
    from models import db

    def slow_worker(task_id, **kw):
        for i in range(1, 6):
            time.sleep(0.02)
            _update(task_id, progress=i * 20)
        _update(task_id, status="done", progress=100,
                result_summary="完成", finished_at=datetime.now())

    task = create_task("test_flow", "tester", "流程测试")
    db.session.commit()
    tid = task.task_id

    t0 = time.perf_counter()
    run_async("test_flow", tid, slow_worker)
    submit_ms = (time.perf_counter() - t0) * 1000
    # 非阻塞：提交耗时远小于任务总时长（5*20ms=100ms）
    assert submit_ms < 80, f"提交耗时 {submit_ms:.1f}ms 过长，可能阻塞"

    # 轮询轨迹
    states = []
    for _ in range(100):
        t = get_task(tid)
        states.append((t.status, t.progress))
        if t.status in ("done", "failed"):
            break
        time.sleep(0.01)
    assert states[-1] == ("done", 100)
    assert ("pending", 0) in states or ("running", 0) in states
    assert any(s[0] == "running" for s in states), "应观察到 running 中间态"
    # 落库可追溯
    assert get_task(tid) is not None


def test_async_task_failure_retry(app_ctx):
    """失败可重进：注入失败 → failed 记录原因 → 重试成功。"""
    from utils.async_tasks import create_task, get_task, run_async, _update
    from models import db

    def fail_once_worker(task_id, **kw):
        raise RuntimeError("模拟磁盘写入失败（注入）")

    # 首次：失败
    t1 = create_task("test_fail", "tester", "失败注入")
    db.session.commit()
    run_async("test_fail", t1.task_id, fail_once_worker)
    for _ in range(100):
        if get_task(t1.task_id).status in ("done", "failed"):
            break
        time.sleep(0.01)
    assert get_task(t1.task_id).status == "failed"
    assert "模拟磁盘写入失败" in (get_task(t1.task_id).result_summary or "")

    # 重试：成功
    t2 = create_task("test_fail", "tester", "重试")
    db.session.commit()
    run_async("test_fail", t2.task_id, _ok_worker)
    for _ in range(100):
        if get_task(t2.task_id).status in ("done", "failed"):
            break
        time.sleep(0.01)
    assert get_task(t2.task_id).status == "done"


def _ok_worker(task_id, **kw):
    from utils.async_tasks import _update
    import time
    time.sleep(0.01)
    _update(task_id, status="done", progress=100, result_summary="成功",
            finished_at=datetime.now())


def test_async_task_concurrency_mutex(app_ctx):
    """并发互斥：同 task_type 已在运行 → 新任务立即 failed。"""
    from utils.async_tasks import create_task, get_task, run_async, _update
    from models import db
    import threading

    hold = threading.Event()

    def hold_worker(task_id, **kw):
        hold.wait(timeout=2)
        _update(task_id, status="done", progress=100, result_summary="释放",
                finished_at=datetime.now())

    t1 = create_task("test_mutex", "tester", "占用")
    db.session.commit()
    run_async("test_mutex", t1.task_id, hold_worker)
    time.sleep(0.05)  # 确保第一个已占用锁

    t2 = create_task("test_mutex", "tester", "冲突")
    db.session.commit()
    run_async("test_mutex", t2.task_id, hold_worker)  # 应被互斥拒绝
    for _ in range(50):
        if get_task(t2.task_id).status in ("done", "failed"):
            break
        time.sleep(0.01)
    assert get_task(t2.task_id).status == "failed"
    assert "并发互斥" in (get_task(t2.task_id).result_summary or "")

    hold.set()  # 释放第一个
    for _ in range(100):
        if get_task(t1.task_id).status in ("done", "failed"):
            break
        time.sleep(0.01)
    assert get_task(t1.task_id).status == "done"
