# -*- coding: utf-8 -*-
"""tests/test_medical_insurance_structure.py — medical_insurance/list 结构断言（061 S3 换壳护栏）。

医保缴费页 250 行 medical_insurance.js 以 id/.nav-link/.data-type 直接锚定 DOM；
061 S3 换壳（页头 + sc-chips 页签 + workflow_shared 作用域令牌 + mini-stat KPI 重塑）
只许改视觉壳层。本断言三层（解析器＝tests/helpers/structure_assert.py）：
  1) 深度账平衡；
  2) JS 承重墙 id 全集存在（medical_insurance.js 静态扫描）+ 两弹窗；
  3) 壳层落位：页头 + 共享/页面样式接入 + 险种筛选（2026-11-16 三分类页签取消后改为
     工具栏下拉，名单合并为单表；本文件同步改断言口径）。
列结构不在此断言：列由 utils/medical_field_catalog.py 目录派生（test_medical_insurance_catalog.py）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from helpers.structure_assert import analyze, assert_balanced, assert_modals

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_IDS = [
    # 险种筛选 / 统计 / 表格
    'insuranceTypeInput', 'statsYear', 'medicalStats',
    'tableHead', 'tableBody', 'searchForm', 'searchYear', 'searchSource',
    'searchLink', 'searchVerify', 'searchGroup', 'pagerBar', 'pagination',
    'templateLink',
    # 新增/导出表单弹窗（#formTypeSel = 新增态险种下拉）
    'formModal', 'formTitle', 'editId', 'formInsuranceType', 'formTypeSel', 'formFields',
    'saveForm',
    # 导入弹窗（#importType = 导入险种）
    'importModal', 'importForm', 'importBatchInput', 'importType',
]


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
    html = client.get('/medical-insurance/').get_data(as_text=True)
    return html, analyze(html)


def _select_html(html, sid):
    """取 id=sid 的 <select>…</select> 片段。"""
    idx = html.find(f'id="{sid}"')
    assert idx != -1, f'#{sid} 缺失'
    start = html.rfind('<select', 0, idx)
    end = html.find('</select>', idx)
    assert start != -1 and end != -1, f'#{sid} 不是完整 select'
    return html[start:end + len('</select>')]


class TestMedicalInsuranceStructure:

    def test_balance_and_js_hooks(self, tmp_path, monkeypatch):
        """深度账平衡 + JS 承重墙 id 一个不少 + 两弹窗齐全。"""
        html, r = _get_report(tmp_path, monkeypatch)
        assert_balanced(r)
        missing = [nid for nid in REQUIRED_IDS if nid not in r.elements]
        assert not missing, f'JS 承重墙 id 缺失：{missing}'
        assert_modals(r, ['formModal', 'importModal'])

    def test_type_filter_replaces_tabs(self, tmp_path, monkeypatch):
        """2026-11-16：三分类页签取消 → 险种改为筛选下拉（全部 + 三类），名单合并为单表。"""
        html, r = _get_report(tmp_path, monkeypatch)
        assert 'insuranceTabs' not in r.elements, '分类页签应已移除'
        assert 'sc-chip nav-link' not in html, '页面不应再出现分类胶囊页签'
        assert html.count('id="tableHead"') == 1, '全量数据须落在同一张表格'
        sel = _select_html(html, 'insuranceTypeInput')
        assert 'name="insurance_type"' in sel, '险种筛选须随 #searchForm 序列化下发'
        assert '全部险种' in sel, '缺「全部险种」选项（默认合并展示）'
        for val, label in (('resident', '居民医保'), ('employee', '职工医保'), ('other', '其他人员')):
            assert f'<option value="{val}">{label}</option>' in sel, f'{label} 筛选项缺失'

    def test_type_filter_default_all(self, tmp_path, monkeypatch):
        """首屏默认「全部险种」—— 无预选项，符合合并展示要求。"""
        html, _r = _get_report(tmp_path, monkeypatch)
        sel = _select_html(html, 'insuranceTypeInput')
        assert 'selected' not in sel, '险种筛选不应预选某项（默认全部）'

    def test_js_single_table_and_filter_wiring(self):
        """源码级：单表实例 + 险种列 + 筛选联动（SSR 文本 diff 对 JS 行为零感知）。"""
        js_path = os.path.join(ROOT, 'static', 'js', 'pages', 'medical_insurance.js')
        with open(js_path, encoding='utf-8') as f:
            js = f.read()
        assert 'switchType' not in js, '分类切换逻辑应随页签一并移除'
        assert "$('#insuranceTypeInput').on('change'" in js, '缺险种筛选联动'
        assert "field: 'insurance_type', title: '险种'" in js, '缺「险种」列定义'
        assert "'all'" in js and 'data-mi-type' in js, '列宽锚点应切到单表（all）'
        assert "$('#importType')" in js, '导入须取弹窗内险种选择'
        assert "$('#formTypeSel')" in js, '新增弹窗须按头部险种下拉装配字段'

    def test_export_union_headers(self, tmp_path, monkeypatch):
        """导出：全部险种 → 表头取三类并集并前置「险种」；指定险种 → 单类表头（行为不变）。"""
        import io

        import openpyxl

        app = _make_app(tmp_path, monkeypatch)
        client = app.test_client()
        with client.session_transaction() as s:
            s['user_id'] = 1
        resp = client.get('/medical-insurance/api/export')
        assert resp.status_code == 200
        headers = [c.value for c in openpyxl.load_workbook(io.BytesIO(resp.data)).active[1]]
        assert headers[0] == '险种', '全部险种导出须前置「险种」列'
        for label in ('待遇认证状态', '工作单位', '医保类型说明'):
            assert label in headers, f'并集表头缺 {label}（专属字段被整列丢弃）'
        resp2 = client.get('/medical-insurance/api/export?insurance_type=employee')
        headers2 = [c.value for c in openpyxl.load_workbook(io.BytesIO(resp2.data)).active[1]]
        assert '工作单位' in headers2 and '待遇认证状态' not in headers2, \
            '指定险种应维持单类表头'

    def test_shell_refit_applied(self, tmp_path, monkeypatch):
        """S3 换壳落位：页头 + 共享壳样式 + 页面样式接入。"""
        html, _r = _get_report(tmp_path, monkeypatch)
        assert 'sc-page-head' in html, '缺 v4.0 页头（.sc-page-head）'
        assert 'css/pages/workflow_shared.css' in html, '未接入 061 共享壳样式'
        assert 'css/pages/medical_insurance.css' in html, '未接入页面样式'
