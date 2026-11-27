# -*- coding: utf-8 -*-
"""tests/test_071_shell_topbar_banner.py — 071 壳层两点整改回归。

覆盖：
  1) 顶栏不再渲染页面标题（样图 §1.2）；#pageTitle 隐藏保留在内容区，承载 page_title 文本
  2) 提示条 7 天内到期口径：remind_time ∈ [now, now+7d] 且未办结才计数；
     文案 `N 条待办将在 7 天内到期` + 最近一条 ` · <title> <MM-DD> 截止`；
     无窗口内数据整条不渲染；过期 / 窗口外 / 已办结不计
"""
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    for k, v in {
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_LAN_AUTHORIZED": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _login(client):
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # 首装种子 admin


def _seed_todo(app, title, remind_time, status='todo'):
    from models import db, TodoItem
    with app.app_context():
        db.session.add(TodoItem(title=title, todo_type='review', status=status,
                                remind_time=remind_time))
        db.session.commit()


def test_topbar_has_no_page_title(tmp_path, monkeypatch):
    """顶栏内无任何标题元素；#pageTitle 隐藏保留在内容区（spa.js 依赖）。"""
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    _login(client)
    html = client.get('/dashboard/').get_data(as_text=True)
    topbar = re.search(r'<nav class="sc-topbar">(.*?)</nav>', html, re.S)
    assert topbar, 'sc-topbar 未渲染'
    assert '<h1' not in topbar.group(1)
    assert 'pageTitle' not in topbar.group(1)
    # 保留的隐藏标题仍在内容区，文本承载 page_title（070 起 dashboard 标题改为「首页」）
    assert html.count('id="pageTitle"') == 1
    hidden = re.search(r'<h1 id="pageTitle">([^<]*)</h1>', html)
    assert hidden and '首页' in hidden.group(1)


def test_banner_counts_due_within_seven_days(tmp_path, monkeypatch):
    """窗口内未办结计数；逾期/窗口外/已办结不计；文案对齐样图 §1.3。"""
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    _login(client)
    now = datetime.now()
    _seed_todo(app, '低保年度复核', now + timedelta(days=3))
    _seed_todo(app, '高龄津贴确认', now + timedelta(days=30))
    _seed_todo(app, '已办结事项', now + timedelta(days=1), status='done')
    _seed_todo(app, '逾期未办', now - timedelta(days=2))
    html = client.get('/dashboard/').get_data(as_text=True)
    assert '1 条待办将在 7 天内到期' in html
    assert '低保年度复核' in html and '截止' in html
    assert '尚未办结' not in html


def test_banner_hidden_without_due_items(tmp_path, monkeypatch):
    """无 7 天内到期数据时整条不渲染（含全部已过期 / 未定时间 / 无待办的场景）。"""
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    _login(client)
    now = datetime.now()
    _seed_todo(app, '逾期未办', now - timedelta(days=1))
    _seed_todo(app, '远期事项', now + timedelta(days=30))
    html = client.get('/dashboard/').get_data(as_text=True)
    assert 'sc-banner' not in html