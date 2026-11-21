# -*- coding: utf-8 -*-
"""tests/test_062_mediation_structure.py — 矛盾纠纷化解 v4.0 结构守卫（062 P3）。

真源：照单施工-6页字段清单 §六（14 列 + 4 弹窗）+ 组件-handoff v1.5。
守护：
  1) 065 胶囊：governance 三类分区 9 项（2026-11-16 综治重构，契约同源
     tests/test_module_tabs.py::test_governance_group_three_partitions），
     「矛盾纠纷化解」唯一激活（#tab=mediation）
  2) 页头：sc-page-head + 唯一主按钮（新增案件）+ 导出 ZIP
  3) 表格：sc-table 14 列 + JS 契约 id
  4) 工具条：case_no/status/applicant/respondent/dispute_type + 查询
  5) 4 弹窗：formModal(13 字段) / closeModal(办结结果★) / returnModal(退回原因★) / detailModal
  6) JS：状态徽章 sc- + 文本操作 + 办结/退回仅 mediating 的守卫 + #pageStat（BS badge/btn 残留即红）
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COLUMNS = ['序号', '案件编号', '纠纷类型', '申请人', '身份证号', '所在组',
           '被申请人', '被申请人身份证', '人数', '调解人', '开始日期', '状态', '附件', '操作']


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


def _page_html(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/mediation/')
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _head_section(html):
    m = re.search(r'<header class="sc-page-head">.*?</header>', html, re.S)
    assert m, '缺 sc-page-head 页头'
    return m.group(0)


def test_chips_head_columns_toolbar(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)

    # 2026-11-16 综治重构随动：governance 三类分区 9 枚，矛盾纠纷化解唯一激活
    for label in ('严重精神障碍', '治保委员会', '刑事人员', '道交隐患', '重点车辆',
                  '劝导站', '矛盾纠纷化解', '社会稳定风险', '信访维稳'):
        assert label in html, f'缺胶囊 {label}'
    assert '特困人员走访' not in html, '特困人员走访已迁出综治组（走访关爱）'
    assert html.count('sc-chip is-active') == 1
    assert re.search(r'class="sc-chip is-active"[^>]*href="[^"]*#tab=mediation"', html)

    head = _head_section(html)
    assert '矛盾纠纷化解' in head
    assert head.count('sc-btn--primary') == 1, '页头区主按钮必须唯一（§2.2）'
    assert '新增案件' in head and '导出 ZIP' in head

    for dom_id in ('dataTable', 'tableBody', 'pagination', 'pageStat', 'searchForm'):
        assert f'id="{dom_id}"' in html, f'缺 JS 契约节点 #{dom_id}'
    thead = re.search(r'<thead class="sc-table__head">.*?</thead>', html, re.S)
    assert thead, '缺 sc-table__head'
    ths = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', thead.group(0))
    assert ths == COLUMNS, f'§六 14 列不符：期望 {COLUMNS}，实得 {ths}'

    form = re.search(r'<form class="sc-toolbar"[^>]*>.*?</form>', html, re.S)
    assert form, '缺 sc-toolbar'
    body = form.group(0)
    for frag in ('name="case_no"', 'name="status"', 'name="applicant"',
                 'name="respondent"', 'name="dispute_type"', '查询'):
        assert frag in body, f'工具条缺 {frag}'


def test_four_modals(tmp_path, monkeypatch):
    html = _page_html(tmp_path, monkeypatch)
    for dom_id in ('formModal', 'closeModal', 'returnModal', 'detailModal',
                   'saveForm', 'closeForm', 'returnForm',
                   'editId', 'closeId', 'returnId', 'formModalTitle', 'detailBody',
                   'attachmentsInput', 'currentAttachments'):
        assert f'id="{dom_id}"' in html, f'缺弹窗契约 id #{dom_id}'

    # 主表单：13 字段（11 普通 + content/attachments 两个整行）+ 无必填星（§六 主表单无必填）
    main = html[html.index('id="formModal"'):html.index('id="closeModal"')]
    # ADR-022 胶囊统一改造：允许 gs-form-modal 等附加修饰类（sc-modal sc-modal--lg 基础组合仍必在）
    assert re.search(r'class="sc-modal sc-modal--lg(?: [\w-]+)*"', main)
    fields = re.findall(r'class="sc-field(?: sc-field--[-\w]+)*"', main)
    assert len(fields) == 13, f'§六 主表单 13 字段，实得 {len(fields)}'
    assert main.count('sc-field--full') == 2, '纠纷内容/附件两个整行'
    assert main.count('sc-field__required') == 0, '主表单无必填（§六）'
    for name in ('dispute_type', 'location', 'applicant', 'applicant_id_card',
                 'applicant_village_group', 'respondent', 'respondent_id_card',
                 'people_count', 'mediator', 'start_date', 'mediation_date',
                 'content', 'attachments'):
        assert f'name="{name}"' in main, f'主表单缺字段 {name}'

    # 办结：办结结果必填；退回：退回原因必填
    close = html[html.index('id="closeModal"'):html.index('id="returnModal"')]
    assert 'name="result"' in close and 'required' in close
    assert close.count('sc-field__required') == 1
    ret = html[html.index('id="returnModal"'):html.index('id="detailModal"')]
    assert 'name="return_reason"' in ret and 'required' in ret
    assert ret.count('sc-field__required') == 1


def test_js_badges_actions_and_mediating_guard():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js_path = os.path.join(base, 'static', 'js', 'pages', 'mediation_list.js')
    with open(js_path, encoding='utf-8') as f:
        js = f.read()
    for tone in ('sc-badge--warning', 'sc-badge--success', 'sc-badge--danger', 'sc-badge--primary'):
        assert tone in js, f'缺徽章档 {tone}'
    assert 'sc-row-actions' in js and 'sc-btn--text' in js
    # 验收 4：办结/退回仅「调解中」显示（前端守卫）
    assert "item.status==='mediating'" in js, '缺办结/退回仅调解中的守卫'
    assert 'class="badge' not in js, 'BS 徽章残留'
    assert 'class="btn ' not in js, 'BS 行内按钮残留'
    assert 'pageStat' in js and 'onLoaded' in js, '缺分页统计回填'
    assert 'sc-table' in js, '详情表须用 sc-table'
