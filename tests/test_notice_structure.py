# -*- coding: utf-8 -*-
"""tests/test_notice_structure.py — notice/list 结构断言（061 S5 换壳护栏）。

补贴公示是无弹窗工作台（功能清单 §14：删除/发布用 confirm，打印 window.open）。
075/bd374a2 改版后页头改挂 notice-state-pill 状态胶囊（sc-badge 与旧 9 个 id 已退役），
工作台以 tplToolbar / recToolbar 两工具栏（模板模式 ↔ 记录模式一显一隐）锚定 DOM。
承重墙 id 由 notice_list.js 的 jQuery 选择器单一真源提取（extract_js_ids），
不再硬编码清单。本断言（解析器＝tests/helpers/structure_assert.py）：
  1) 深度账平衡；
  2) JS 承重墙 id 全集存在；
  3) 工作台骨架：tplToolbar/recToolbar 两工具栏 + 可编辑表
     + 类型按钮/记录项 class 钩子 + 功能清单 §14 按钮 16 件（≥15 计数）；
  4) 无弹窗（modals(report) 为空——§14 明确无 modal，勿增）；
  5) 壳层落位：页头 + 共享壳样式接入。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from helpers.structure_assert import analyze, assert_balanced, modals, extract_js_ids

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.path.join(_ROOT, 'static', 'js', 'pages', 'notice_list.js')
REQUIRED_IDS = sorted(extract_js_ids(JS_PATH))


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHUCUN_LAN_AUTHORIZED", "1")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    monkeypatch.setattr(config.Config, "IMPORTS_DIR", str(tmp_path / "temp" / "imports"))
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _get_report(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as s:
        s['user_id'] = 1  # admin
    html = client.get('/notice/').get_data(as_text=True)
    return html, analyze(html)


class TestNoticeStructure:

    def test_balance_and_js_hooks(self, tmp_path, monkeypatch):
        """深度账平衡 + JS 承重墙 id 一个不少（notice_list.js 单一真源提取）。"""
        html, r = _get_report(tmp_path, monkeypatch)
        assert_balanced(r)
        missing = [nid for nid in REQUIRED_IDS if nid not in r.elements]
        assert not missing, f'JS 承重墙 id 缺失：{missing}'

    def test_workbench_skeleton(self, tmp_path, monkeypatch):
        """工作台骨架：两工具栏 tplToolbar/recToolbar + 可编辑表 + 类型/记录 class 钩子 + 按钮 ≥15。"""
        html, r = _get_report(tmp_path, monkeypatch)
        assert 'notice-type-btn' in html, '左侧类型按钮 .notice-type-btn 缺失'
        assert {'tplToolbar', 'recToolbar'} <= set(r.elements), \
            '工作台两工具栏 #tplToolbar / #recToolbar 缺失'
        assert 'cell-in' in html, '可编辑单元格 .cell-in 约定缺失（模板/JS 双端依赖）'
        assert html.count('<button') + html.count("<button ") >= 15, \
            '功能清单 §14 按钮 16 件，页内静态按钮不足 15'
        # 无弹窗约定（删除/发布走 confirm）；profileModal 是 base.html 全局件，不算本页
        extra = sorted(set(modals(r)) - {'profileModal'})
        assert not extra, f'§14 明确无 modal，不得新增：{extra}'

    def test_shell_refit_applied(self, tmp_path, monkeypatch):
        """S5 换壳落位：页头 + 共享壳样式 + 内联样式令牌化（零裸色值）。"""
        html, _r = _get_report(tmp_path, monkeypatch)
        assert 'sc-page-head' in html, '缺 v4.0 页头（.sc-page-head）'
        assert 'css/pages/workflow_shared.css' in html, '未接入 061 共享壳样式'
        assert 'class="sc-workflow-page' in html, 'wrapper 未挂 061 作用域类'
        import re
        naked = re.findall(r':\s*#[0-9a-fA-F]{3,6}\b', html.split('<style>', 1)[1].split('</style>', 1)[0])
        assert not naked, f'内联样式残留裸色值：{naked}'
