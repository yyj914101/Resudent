# -*- coding: utf-8 -*-
"""tests/test_certificate_structure.py — certificate/list 结构断言（061 S6 换壳护栏）。

证明开具是最复杂页：727 行模板 + 内联 JS（042 决策不拆文件）。JS 以 74 个 id
（静态标记 + 内联脚本全部 $() 引用）+ .issue-meta-input/.page-setting/
.cert-resident-hint/.cert-editor-toolbar 类钩子 + [data-bs-target] 页签绑定锚定 DOM。
061 S6 换壳（页头 + 065 模块胶囊 tools 组 + 3 页签换 sc-chips + 内联样式令牌化）
只许改视觉壳层。本断言（解析器＝tests/helpers/structure_assert.py）：
  1) 深度账平衡；
  2) JS 承重墙 74 id 全集存在；
  3) 3 tab-pane 落 .tab-content + 4 弹窗 + 3 张 A4 纸节点 + 富文本工具栏命令 ≥13；
  4) 壳层落位：页头 + 模块胶囊（tools 组，active=certificate）+ 页签 sc-chips 形态
     + 共享壳样式接入。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from helpers.structure_assert import analyze, assert_balanced, assert_modals

REQUIRED_IDS = [
    # 开具 pane：命令栏 + 动态字段 + 开具信息 + A4 预览
    'issueTemplateSelect', 'issueTemplateMeta', 'btnClearIssue',
    'btnRefreshPreview', 'btnIssueCertificate', 'issueDynamicFields',
    'issueIssuer', 'issueUnit', 'issueUnitHistory', 'issueDate',
    'issuePurpose', 'issueCopies', 'issuePreviewStatus', 'issuePaper',
    # 模板管理 pane：列表 + 富文本编辑器 + 字段配置 + 页面设置
    'btnNewTemplate', 'templateKeyword', 'templateList',
    'editorTemplateName', 'editorCertType', 'certTypeHistory', 'editorActive',
    'editorBlock', 'editorFontName', 'editorFontSize', 'editorColor',
    'variableBar', 'templateEditor', 'editorFieldBody', 'newFieldLabel',
    'newFieldType', 'btnAddField', 'btnGovPagePreset',
    'pageOrientation', 'pageFontName', 'pageFontSize', 'pageLineHeight',
    'pageMarginTop', 'pageMarginBottom', 'pageMarginLeft', 'pageMarginRight',
    'editorTemplateVersion', 'btnCopyTemplate', 'btnToggleTemplate',
    'btnDeleteTemplate', 'btnSaveTemplate',
    # 记录 pane：筛选 + 表格 + 分页
    'recordKeyword', 'recordCertType', 'recordIssuer', 'recordDateStart',
    'recordDateEnd', 'recordStatus', 'btnSearchRecords', 'recordBody',
    'recordTotal', 'recordPagination',
    # 4 弹窗 + 打印根
    'residentSelectModal', 'residentResultList',
    'issueSuccessModal', 'successSerialNo', 'successSummary',
    'btnSuccessPrint', 'btnSuccessWord', 'btnSuccessRecords',
    'recordDetailModal', 'recordSummary', 'recordPaper',
    'btnRecordPrint', 'btnRecordWord',
    'voidModal', 'voidForm', 'voidRecordId', 'voidReason',
    'certPrintRoot', 'certPrintPaper',
    # pane 容器
    'issuePane', 'templatePane', 'recordPane',
]

MODAL_IDS = ['residentSelectModal', 'issueSuccessModal',
             'recordDetailModal', 'voidModal']


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
    html = client.get('/certificate/').get_data(as_text=True)
    return html, analyze(html)


class TestCertificateStructure:

    def test_balance_and_js_hooks(self, tmp_path, monkeypatch):
        """深度账平衡 + JS 承重墙 74 id 一个不少 + 4 弹窗齐全。"""
        html, r = _get_report(tmp_path, monkeypatch)
        assert_balanced(r)
        missing = [nid for nid in REQUIRED_IDS if nid not in r.elements]
        assert not missing, f'JS 承重墙 id 缺失：{missing}'
        assert_modals(r, MODAL_IDS)

    def test_tabs_and_papers(self, tmp_path, monkeypatch):
        """3 页签 pane 落 .tab-content + 3 张 A4 纸 + 富文本工具栏命令 ≥13。"""
        html, r = _get_report(tmp_path, monkeypatch)
        for pid in ('issuePane', 'templatePane', 'recordPane'):
            info = r.elements[pid]
            assert 'tab-pane' in info.classes and 'tab-content' in info.parent_classes, \
                f'#{pid} 必须是 .tab-content 下的 tab-pane'
        for paper in ('issuePaper', 'templateEditor', 'recordPaper', 'certPrintPaper'):
            assert 'cert-paper' in r.elements[paper].classes, \
                f'#{paper} 必须带 .cert-paper（A4 纸节点）'
        assert 'contenteditable' in html, '模板编辑器须可编辑（contenteditable 属性）'
        assert html.count('data-command=') >= 13, '富文本工具栏命令数不足（§9：撤销/重做/字体/对齐等）'

    def test_shell_refit_and_module_chips(self, tmp_path, monkeypatch):
        """S6 换壳落位：页头 + 工作台页签（2026-11-02 cd54ebb 定稿：sc-subtabs 下划线式，
        data-bs-target 钩子保留）+ 共享壳样式。

        2026-11-16：撤 065 tools 胶囊组（死亡上报 / 导入批次升为侧栏入口），本页只剩工作台页签。
        """
        html, _r = _get_report(tmp_path, monkeypatch)
        assert 'sc-page-head' in html, '缺 v4.0 页头'
        assert 'css/pages/workflow_shared.css' in html, '未接入 061 共享壳样式'
        assert '#tab=death' not in html and '#tab=import-batch' not in html, \
            'tools 胶囊组应已撤除（入口改由侧栏承载）'
        # 073 验收补修：cd54ebb 页签形态从「sc-chip 胶囊」改为「sc-subtabs 下划线式」，
        # 三页签数量与 data-bs-target 钩子契约不变（守护意图等价承载）
        assert html.count('sc-subtabs') == 1, '工作台页签应为 sc-subtabs 容器（仅证明开具页渲染）'
        assert html.count('data-bs-toggle="tab"') == 3, '工作台应恰 3 个文字页签'
        for pane in ('issuePane', 'templatePane', 'recordPane'):
            assert f'data-bs-target="#{pane}"' in html, f'页签缺 data-bs-target 钩子（#{pane}）'
