# -*- coding: utf-8 -*-
"""tests/test_resident_add_structure.py — resident/add 关键节点结构断言（035 门禁补强）。

背景（034 验收 R-034-01）：SSR 快照是文本 diff，对标签非法嵌套零感知——add.html 曾因
add.html:198 与 _import_wizard_body.html:54 两处游离 </div>，解析期 form 被隐式提前闭合、
#import/#custom 被弹出 .tab-content（Bootstrap 以 `.tab-content > .tab-pane` 直接子选择器
控制显隐 → 批量导入区常驻可见），快照与 md5 全程无感。

本断言以共享解析器（tests/helpers/structure_assert.py，061 S1 自本文件抽取，语义逐项
保持不变）模拟浏览器「闭合标签弹出至最近同名开标签」的隐式闭合语义，对 /resident/add
的 SSR 输出做四项结构检查（修复前红、修复后绿，作为回归护栏留存）：
  1) 无游离 </div>、无遗留未闭合 <div>（深度账目平衡）；
  2) form 无隐式提前闭合、#submitBtn 位于 form 标签对之内；
  3) #manual/#import/#custom 三个 tab-pane 的 DOM 父节点均为 .tab-content；
  4) ?tab=manual|template|custom 服务端初始态仅对应 pane 带 show active。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from helpers.structure_assert import analyze, assert_balanced, assert_tabs


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


class TestAddWorkbenchStructure:

    def test_structure_balance_and_panes(self, tmp_path, monkeypatch):
        """R-034-01 回归护栏：深度账平衡 + form 完整 + submitBtn 在 form 内 + 三 pane 落 .tab-content。"""
        app = _make_app(tmp_path, monkeypatch)
        client = app.test_client()
        with client.session_transaction() as s:
            s['user_id'] = 1  # admin（三 pane 全渲染）
        html = client.get('/resident/add?tab=custom').get_data(as_text=True)
        r = analyze(html)
        assert_balanced(r)
        assert r.elements['submitBtn'].in_form, \
            '#submitBtn 必须位于 #addResidentForm 标签对之内'
        assert_tabs(r, ('manual', 'import', 'custom'))

    @pytest.mark.parametrize('tab,visible', [
        ('manual', 'manual'), ('template', 'import'), ('custom', 'custom')])
    def test_initial_tab_single_pane_visible(self, tmp_path, monkeypatch, tab, visible):
        """R-034-01：?tab= 三态服务端初始渲染——仅对应 pane 带 show active（另两个 display:none）。"""
        app = _make_app(tmp_path, monkeypatch)
        client = app.test_client()
        with client.session_transaction() as s:
            s['user_id'] = 1
        html = client.get(f'/resident/add?tab={tab}').get_data(as_text=True)
        r = analyze(html)
        found = {pid for pid, info in r.elements.items()
                 if 'tab-pane' in info.classes}
        assert found == {'manual', 'import', 'custom'}, \
            f'admin 应渲染全部三个 tab-pane，实际：{sorted(found)}'
        for pid in found:
            cls = r.elements[pid].classes
            expected = (pid == visible)
            assert ('show active' in cls) is expected, \
                f'tab={tab} 时 #{pid} 初始可见性错误：{cls!r}'

    def test_bank_fields_nesting(self, tmp_path, monkeypatch):
        """061 S4：开户行/银行卡号回归「功能清单 §2 注意」的 div 嵌套错位——
        两字段必须同处一个 row 的 col-md-6（修复前 #rBankCard 父节点是 .mb-2、
        且 185/190 两处提前闭合吞掉居民信息列与外层 row）。"""
        app = _make_app(tmp_path, monkeypatch)
        client = app.test_client()
        with client.session_transaction() as s:
            s['user_id'] = 1
        html = client.get('/resident/add').get_data(as_text=True)
        r = analyze(html)
        assert 'col-md-6' in r.elements['rBankName'].parent_classes and \
            'col-md-6' in r.elements['rBankCard'].parent_classes, \
            ('开户行/银行卡号必须同处 .row 的 col-md-6'
             f'（实际父节点：{r.elements["rBankName"].parent_classes!r} /'
             f' {r.elements["rBankCard"].parent_classes!r}）')

    def test_shell_refit_and_module_chips(self, tmp_path, monkeypatch):
        """061 S4：v4.0 壳层落位——页头 + 065 模块胶囊（resident 组，active=add）
        + 模式页签 sc-chips 形态 + 四步向导步骤条完整。"""
        app = _make_app(tmp_path, monkeypatch)
        client = app.test_client()
        with client.session_transaction() as s:
            s['user_id'] = 1
        html = client.get('/resident/add').get_data(as_text=True)
        assert 'sc-page-head' in html, '缺 v4.0 页头'
        assert 'css/pages/workflow_shared.css' in html, '未接入 061 共享壳样式'
        assert '#tab=query' in html and '店铺信息' in html and '#tab=add' in html, \
            '缺 resident 模块胶囊（065 module_tabs 宏产物）'
        # 模式页签：sc-chip 与 nav-link 类共存（Bootstrap tab 插件 + #addTabs 委托锚点零改动）
        assert html.count('sc-chip nav-link') == 3, '模式页签应恰 3 个（admin 含自定义导入）'
        # 2026-11-16：录入方式胶囊独立成块级行。.sc-chips 是 inline-flex，缺容器会与
        # 模块胶囊挤在同一行（实测同 top=198）→ 用块级包装器强制换行
        assert 'add-mode-bar' in html, '录入方式胶囊缺独立行容器（会与模块胶囊同排）'
        # 四步向导步骤条（R-061-C3：无 handoff 规格，按 sc-chips 变体取值）
        for step in range(1, 5):
            assert f'data-step="{step}"' in html, f'向导步骤条缺第 {step} 步'
