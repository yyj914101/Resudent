# -*- coding: utf-8 -*-
"""tests/test_safety_structure.py — safety/archive 结构断言（061 S2 换壳护栏）。

安全工作页 942 行 safety_archive.js 以约 50 个 id/class 直接锚定 DOM（承重墙），
061 S2 换壳只许改视觉壳层（sc-page-head + workflow_shared.css 套令牌），不许动钩子。
本断言三层（解析器＝tests/helpers/structure_assert.py，061 S1 抽取）：
  1) 深度账平衡：无游离 </div> / 未闭合 <div> / form 隐式提前闭合；
  2) JS 承重墙 id 全集存在（从 safety_archive.js 选择器动态提取，
     含 void 标签 input/img——解析器已记录带 id 的 void 元素）；
  3) 壳层骨架：5 弹窗 + 树/三视图/批量条/搜索容器 + class 钩子
     （.search-results / .batch-bar / .safety-tree）+ 页头与共享壳样式已接入。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from helpers.structure_assert import analyze, assert_balanced, assert_modals, extract_js_ids

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.path.join(_ROOT, 'static', 'js', 'pages', 'safety_archive.js')
REQUIRED_IDS = sorted(extract_js_ids(JS_PATH))

MODAL_IDS = ['newFolderModal', 'renameModal', 'moveModal',
             'confirmDeleteModal', 'lightboxModal']


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
    html = client.get('/safety/').get_data(as_text=True)
    return html, analyze(html)


class TestSafetyArchiveStructure:

    def test_balance_and_js_hooks(self, tmp_path, monkeypatch):
        """深度账平衡 + JS 承重墙 id 一个不少（safety_archive.js 单一真源提取，换壳动钩子即红）。"""
        html, r = _get_report(tmp_path, monkeypatch)
        assert_balanced(r)
        missing = [nid for nid in REQUIRED_IDS if nid not in r.elements]
        assert not missing, f'JS 承重墙 id 缺失：{missing}'

    def test_modals_and_containers(self, tmp_path, monkeypatch):
        """5 弹窗 + class 钩子（搜索面板 / 批量条 / 树容器）齐全且落位正确。"""
        html, r = _get_report(tmp_path, monkeypatch)
        assert_modals(r, MODAL_IDS)
        for nid in ('searchResults', 'searchResultsMobile'):
            assert 'search-results' in r.elements[nid].classes, \
                f'#{nid} 必须带 .search-results（JS 按 class 定位搜索面板）'
        assert 'batch-bar' in r.elements['batchBar'].classes, \
            '#batchBar 必须带 .batch-bar（批量条样式作用域）'
        assert 'safety-tree' in r.elements['safetyTree'].classes, \
            '#safetyTree 必须带 .safety-tree（树组件样式作用域）'
        # 平铺视图路径列由 JS 按 .flat-col 显隐，表头列不可丢
        assert 'flat-col' in html, '平铺视图路径列 .flat-col 表头缺失'

    def test_shell_refit_applied(self, tmp_path, monkeypatch):
        """S2 换壳落位：页头 + 共享壳样式接入（extra_css 经 GLB-02 注入不丢）。"""
        html, _r = _get_report(tmp_path, monkeypatch)
        assert 'sc-page-head' in html, '缺 v4.0 页头（.sc-page-head）'
        assert 'css/pages/workflow_shared.css' in html, '未接入 061 共享壳样式'
