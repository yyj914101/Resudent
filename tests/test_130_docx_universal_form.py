# -*- coding: utf-8 -*-
"""tests.test_130_docx_universal_form — 通用 Word (.docx) 表单引擎（任务 130）。

覆盖：任意 .docx 零硬编码解析（合并格/列宽比例/复选框/自动绑定推断）、
原位回写导出（A4 单页宽、行高、格式微调、未填写格保真）、文件型存储 CRUD 与路径穿越防护、
蓝图注册与 ADR-016 权限模块位归类。
"""
import importlib.util
import io
import os
import tempfile

from docx import Document
from docx.oxml.ns import qn


def _load_engine():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, 'utils', 'docx_form_engine.py')
    spec = importlib.util.spec_from_file_location('docx_form_engine', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_sample_approval_docx():
    """构造含标题段落、合并格、复选框、监护人区块的真实审批表 .docx 字节流。"""
    doc = Document()
    p_title = doc.add_paragraph('困难残疾人生活补贴申请审批表')
    p_title.alignment = 1  # center

    tbl = doc.add_table(rows=5, cols=4)
    # 第 0 行：申请人姓名 | (空) | 性别 | (空)
    tbl.cell(0, 0).text = '申请人姓名*'
    tbl.cell(0, 1).text = ''
    tbl.cell(0, 2).text = '性别*'
    tbl.cell(0, 3).text = ''
    # 第 1 行：户籍地址 | (横向合并 3 格)
    tbl.cell(1, 0).text = '户籍地址*'
    tbl.cell(1, 1).merge(tbl.cell(1, 3))
    tbl.cell(1, 1).text = ''
    # 第 2 行：账户类型复选框（跨 4 格）
    tbl.cell(2, 0).merge(tbl.cell(2, 3))
    tbl.cell(2, 0).text = '本人账户□  他人账户□*'
    # 第 3-4 行：监护人信息纵向合并 + 姓名 + 联系电话
    tbl.cell(3, 0).merge(tbl.cell(4, 0))
    tbl.cell(3, 0).text = '监护人信息'
    tbl.cell(3, 1).text = '姓名'
    tbl.cell(3, 2).text = ''
    tbl.cell(3, 3).text = ''
    tbl.cell(4, 1).text = '联系电话*'
    tbl.cell(4, 2).merge(tbl.cell(4, 3))
    tbl.cell(4, 2).text = ''

    # 显式写入 tblGrid 列宽，模拟真实 Word 文档
    tbl_grid = tbl._tbl.find(qn('w:tblGrid'))
    if tbl_grid is not None:
        for gc, w in zip(tbl_grid.findall(qn('w:gridCol')), [2000, 2500, 2000, 2500]):
            gc.set(qn('w:w'), str(w))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_multi_paragraph_cell_docx():
    """构造单元格内多段落 + 局部加粗 run + 已勾选 ☑ 的文档，用于验证导出保真与勾选字符族。"""
    doc = Document()
    tbl = doc.add_table(rows=2, cols=2)
    cell = tbl.cell(0, 0)
    first_p = cell.paragraphs[0]
    first_p.add_run('填表说明：')
    bold_run = first_p.add_run('正文加粗')
    bold_run.bold = True
    cell.add_paragraph('填表说明第二段')
    tbl.cell(0, 1).text = '☑ 已勾选'
    tbl.cell(1, 0).text = '申请人姓名'
    tbl.cell(1, 1).text = ''
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_parse_universal_docx_and_auto_bind():
    eng = _load_engine()
    raw = _build_sample_approval_docx()
    schema = eng.parse_docx_bytes(raw, filename='困难残疾人生活补贴申请审批表.docx')

    assert schema['title'] == '困难残疾人生活补贴申请审批表'
    assert schema['paper_orient'] == 'portrait'
    assert len(schema['blocks']) == 2
    assert schema['blocks'][0]['type'] == 'paragraph'

    tbl = schema['blocks'][1]
    assert tbl['type'] == 'table'
    assert abs(sum(tbl['col_pcts']) - 100.0) < 0.05
    assert len(tbl['rows']) == 5

    # 验证自动绑定与监护人区块重映射
    r0 = tbl['rows'][0]
    assert r0[1]['bind'] == 'name'
    assert r0[3]['bind'] == 'gender'
    r1 = tbl['rows'][1]
    assert r1[1]['colspan'] == 3
    assert r1[1]['bind'] == 'household_address'
    r2 = tbl['rows'][2]
    assert r2[0]['has_checkbox'] is True
    assert r2[0]['editable'] is True
    r3 = tbl['rows'][3]
    assert r3[0]['rowspan'] == 2
    assert r3[2]['bind'] == 'guardian_name'
    r4 = tbl['rows'][4]
    assert r4[1]['bind'] == 'guardian_phone'


def test_inplace_writeback_values_formatting_and_a4_fit():
    eng = _load_engine()
    raw = _build_sample_approval_docx()
    schema = eng.parse_docx_bytes(raw, filename='审批表.docx')

    # 模拟前端版式微调：横向 A4 + 窄边距 + 字体/字号/加粗/对齐 + 行高 + 列宽 + 增行
    schema['paper_orient'] = 'landscape'
    schema['paper_margin'] = 'narrow'
    schema['blocks'][0]['font_family'] = "SimHei, '黑体', sans-serif"
    schema['blocks'][0]['font_px'] = 24
    schema['blocks'][0]['bold'] = True

    tbl = schema['blocks'][1]
    tbl['col_pcts'] = [20.0, 30.0, 20.0, 30.0]
    tbl['row_heights'] = {'0': 48}
    name_cell = tbl['rows'][0][1]
    name_cell['font_family'] = "FangSong, '仿宋', serif"
    name_cell['font_px'] = 16
    name_cell['bold'] = True
    name_cell['align'] = 'left'
    name_cell['valign'] = 'bottom'

    # 追加 1 行空白填报行
    tbl['rows'].append([
        {'id': 't0_r5_c0', 'ri': 5, 'ci': 0, 'colspan': 1, 'rowspan': 1, 'text': '', 'align': 'center', 'valign': 'middle', 'font_px': 12, 'bold': False, 'editable': True, 'has_checkbox': False, 'bind': ''},
        {'id': 't0_r5_c1', 'ri': 5, 'ci': 1, 'colspan': 1, 'rowspan': 1, 'text': '', 'align': 'center', 'valign': 'middle', 'font_px': 12, 'bold': False, 'editable': True, 'has_checkbox': False, 'bind': ''},
        {'id': 't0_r5_c2', 'ri': 5, 'ci': 2, 'colspan': 1, 'rowspan': 1, 'text': '', 'align': 'center', 'valign': 'middle', 'font_px': 12, 'bold': False, 'editable': True, 'has_checkbox': False, 'bind': ''},
        {'id': 't0_r5_c3', 'ri': 5, 'ci': 3, 'colspan': 1, 'rowspan': 1, 'text': '', 'align': 'center', 'valign': 'middle', 'font_px': 12, 'bold': False, 'editable': True, 'has_checkbox': False, 'bind': ''},
    ])

    values = {
        name_cell['id']: '陈明贵',
        # □ → ☑ 由前端点击写入（此处在数据层验证回写结果，不依赖浏览器）
        tbl['rows'][2][0]['id']: '本人账户☑  他人账户□*',
        't0_r5_c0': '备注追加行',
    }
    out_bytes = eng.render_docx_with_values(raw, schema, values=values)

    out_doc = Document(io.BytesIO(out_bytes))
    # A4 横向：297mm × 210mm
    assert round(out_doc.sections[0].page_width.mm) == 297
    assert round(out_doc.sections[0].page_height.mm) == 210
    assert round(out_doc.sections[0].left_margin.mm) == 10

    out_tbl = out_doc.tables[0]
    assert len(out_tbl.rows) == 6
    assert out_tbl.cell(0, 1).text.strip() == '陈明贵'
    assert out_tbl.cell(0, 1).paragraphs[0].runs[0].bold is True
    assert '本人账户☑' in out_tbl.cell(2, 0).text
    assert out_tbl.cell(5, 0).text.strip() == '备注追加行'

    # 验证导出表格列宽严格贴合 A4 横向窄边距可打印宽（297 - 20 = 277mm ≈ 15704 twips）
    gcs = out_tbl._tbl.find(qn('w:tblGrid')).findall(qn('w:gridCol'))
    total_twips = sum(int(gc.get(qn('w:w'))) for gc in gcs)
    expected_twips = int(round(277.0 * 56.6929))
    assert abs(total_twips - expected_twips) <= 5


def test_checkbox_family_and_unfilled_cell_fidelity():
    """☑ 必须被识别为复选框；未填写单元格的段落结构不得被导出压平。"""
    eng = _load_engine()
    assert eng._CHECKBOX_RE.search('□')
    assert eng._CHECKBOX_RE.search('☐')
    assert eng._CHECKBOX_RE.search('☑')
    assert eng._CHECKBOX_RE.search('☒')

    raw = _build_multi_paragraph_cell_docx()
    schema = eng.parse_docx_bytes(raw, filename='多段落.docx')
    tbl = schema['blocks'][0]
    assert tbl['type'] == 'table'
    cb_cell = tbl['rows'][0][1]
    assert cb_cell['has_checkbox'] is True, '已勾选 ☑ 的单元格必须进入复选框渲染分支'
    assert tbl['rows'][1][1]['bind'] == 'name'

    # 不填报任何值 → 原版多段落单元格零触碰（段落数、run 边界、局部加粗全部保留）
    out = eng.render_docx_with_values(raw, schema, values={})
    out_tbl = Document(io.BytesIO(out)).tables[0]
    preserved = out_tbl.cell(0, 0)
    assert len(preserved.paragraphs) == 2, '未填写格的段落数必须保留'
    assert '填表说明：' in preserved.text
    assert '填表说明第二段' in preserved.text
    first_runs = preserved.paragraphs[0].runs
    assert len(first_runs) == 2, '局部 run 边界必须保留'
    assert first_runs[0].bold is not True
    assert first_runs[1].bold is True, '局部加粗不得被未填写格的样式回写抹平'

    # 勾选状态回写：☑ → □ 生效
    out2 = eng.render_docx_with_values(raw, schema, values={cb_cell['id']: '□ 已勾选'})
    assert '□ 已勾选' in Document(io.BytesIO(out2)).tables[0].cell(0, 1).text


def test_word_form_store_crud_and_path_safety():
    eng = _load_engine()
    raw = _build_sample_approval_docx()
    with tempfile.TemporaryDirectory() as tmp:
        store = eng.WordFormStore(tmp)
        tpl = store.create_template_from_docx(raw, filename='审批表.docx', created_by='admin')
        assert len(store.list_templates()) == 1
        assert store.get_template(tpl['id'])['title'] == '审批表'
        assert store.get_template_docx_bytes(tpl['id']) == raw

        rec = store.save_record(tpl['id'], '陈明贵申请表', {'t0_r0_c1': '陈明贵'}, operator='admin')
        assert len(store.list_records(tpl['id'])) == 1
        assert store.get_record(rec['id'])['values']['t0_r0_c1'] == '陈明贵'

        # 路径穿越 ID 必须拒绝
        try:
            store.get_template('../etc/passwd')
            assert False, 'should reject traversal id'
        except ValueError:
            pass

        assert store.delete_record(rec['id']) is True
        assert store.delete_template(tpl['id']) is True
        assert store.list_templates() == []


def test_blueprint_registered_and_permission_module_mapped(isolated_app_factory):
    """蓝图可注册（models 导入正确）+ ADR-016 模块位归类 + 登录守卫生效。"""
    from utils.permissions import BLUEPRINT_MODULES

    assert BLUEPRINT_MODULES.get('word_form') == 'notice', \
        'word_form 复用 notice 权限模块位（ADR-016 默认拒绝，不得漏归类）'

    application = isolated_app_factory()
    rules = {r.rule for r in application.url_map.iter_rules()}
    assert '/word-form/' in rules
    assert '/word-form/api/export-docx' in rules

    resp = application.test_client().get('/word-form/')
    assert resp.status_code in (302, 401, 403), '未登录访问必须被登录守卫拦截'


def test_append_row_covers_full_grid_after_vertical_merge():
    """末行为 vMerge 续格时，末行可见格数 < 列数 → 新增行必须按全列重建，否则缺列格填值丢失。

    前端 `wfBtnAddRow` 已按 `col_pcts` 列数逐列重建（此处按其产出构造 schema 行），
    导出侧 `add_row()` 按 tblGrid 建满列，二者必须对齐。
    """
    eng = _load_engine()
    doc = Document()
    t = doc.add_table(rows=3, cols=2)
    t.cell(0, 0).text = 'A'
    t.cell(0, 1).text = 'B'
    t.cell(1, 0).merge(t.cell(2, 0))
    t.cell(1, 0).text = '纵向合并'
    t.cell(1, 1).text = 'x'
    t.cell(2, 1).text = 'y'
    buf = io.BytesIO()
    doc.save(buf)
    raw = buf.getvalue()

    schema = eng.parse_docx_bytes(raw, filename='vm.docx')
    tbl = [b for b in schema['blocks'] if b['type'] == 'table'][0]
    assert tbl['rows'][1][0]['rowspan'] == 2
    assert len(tbl['rows'][2]) == 1, '末行续格被过滤后只剩 1 个可见格（残行）'

    new_ri = len(tbl['rows'])
    total_cols = len(tbl['col_pcts'])
    tbl['rows'].append([
        {'id': 't0_r%d_c%d' % (new_ri, ci), 'ri': new_ri, 'ci': ci, 'colspan': 1, 'rowspan': 1,
         'text': '', 'align': 'center', 'valign': 'middle', 'font_px': 12,
         'bold': False, 'editable': True, 'has_checkbox': False, 'bind': ''}
        for ci in range(total_cols)
    ])
    out = eng.render_docx_with_values(
        raw, schema, values={'t0_r3_c0': '新行左', 't0_r3_c1': '新行右'})
    out_tbl = Document(io.BytesIO(out)).tables[0]
    assert len(out_tbl.rows) == 4
    assert out_tbl.cell(3, 0).text.strip() == '新行左', '全列重建后新行首格填值必须落盘'
    assert out_tbl.cell(3, 1).text.strip() == '新行右'
    assert out_tbl.cell(1, 0).text.strip() == '纵向合并', '原合并格内容不得被新行污染'


def test_auto_bind_longest_keyword_wins():
    """AUTO_BIND_RULES 顺序纪律：长词优先——'开户人姓名' 不得被 '姓名' 抢先绑成本人。"""
    eng = _load_engine()
    assert eng._match_auto_bind('开户人姓名') == 'bank_holder'
    assert eng._match_auto_bind('开户人姓名*') == 'bank_holder'
    assert eng._match_auto_bind('申请人姓名') == 'name'
    assert eng._match_auto_bind('监护人姓名') == 'guardian_name'
    assert eng._match_auto_bind('监护人电话') == 'guardian_phone'
    assert eng._match_auto_bind('家庭住址') == 'address'
    assert eng._match_auto_bind('开户银行名称') == 'bank_name'
    assert eng._match_auto_bind('联系电话') == 'phone'
    # 监护人区块内的通用词按区块换位
    assert eng._match_auto_bind('联系电话', in_guardian_section=True) == 'guardian_phone'
    assert eng._match_auto_bind('', in_guardian_section=True) == ''


def test_entry_demoted_to_civil_report_capsule(isolated_app_factory):
    """131：入口从侧栏一级降级为「民政对象 · 报表」页内胶囊（侧栏与顶部胶囊同步）。

    130 的教训保留在此：侧栏只改 sidebar_menu.html 的 twolayer 回退分支 = 永不可见，
    真源是 utils/nav_spec.py；131 起该入口彻底离开侧栏，改由 module_tabs 的 civil 报表行承载。
    """
    from utils.nav_spec import NAV_GROUPS

    public = next(g for g in NAV_GROUPS if g['key'] == 'public')
    assert 'word_form.index' not in [i['endpoint'] for i in public['items']], \
        '侧栏一级入口应已撤下（改由民政对象页「报表」行胶囊承载）'

    app = isolated_app_factory()
    with app.app_context():
        from models import User
        admin = User.query.filter_by(username='admin').first()
        admin_id, admin_name = admin.id, admin.username
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['username'] = admin_name

    html = client.get('/dibao/').get_data(as_text=True)
    assert '>报表审批<' in html, '民政对象页必须渲染「报表审批」胶囊'
    assert 'href="/word-form/#tab=word-form"' in html
    assert 'sc-nav-item" href="/word-form/"' not in html, '侧栏不应再有 Word 表单独立入口'

    # 本页自身同样渲染该组胶囊并高亮（侧栏与顶部胶囊同步）
    page = client.get('/word-form/').get_data(as_text=True)
    assert 'class="sc-chip is-active" href="/word-form/#tab=word-form"' in page


def test_all_paragraph_and_label_cell_text_editable_writeback():
    """131：全段落 + 全单元格（含标题、表头标签格）都可写值并原位回写。"""
    eng = _load_engine()
    raw = _build_sample_approval_docx()
    schema = eng.parse_docx_bytes(raw, filename='审批表.docx')
    label_cell = schema['blocks'][1]['rows'][0][0]  # 原本为非填报标签格「申请人姓名*」
    assert label_cell['editable'] is False, '标签格本不在「可填格」白名单内'
    values = {
        'p_0': '2026年困难残疾人生活补贴申请审批表（修订版）',
        label_cell['id']: '申领人姓名*',
    }
    out_bytes = eng.render_docx_with_values(raw, schema, values=values)
    out_doc = Document(io.BytesIO(out_bytes))
    assert out_doc.paragraphs[0].text.strip() == '2026年困难残疾人生活补贴申请审批表（修订版）'
    assert out_doc.tables[0].cell(0, 0).text.strip() == '申领人姓名*'


def test_frontend_inline_editing_wiring():
    """131：前端必须是 contenteditable 全文本编辑（旧 input/textarea 白名单方案已撤）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = open(os.path.join(root, 'static', 'js', 'pages', 'word_form.js'), encoding='utf-8').read()
    for must in ('contenteditable="true"', 'data-edit-para', 'data-edit-cell', 'extractEditableText'):
        assert must in js, '缺少全文本编辑接线：%s' % must
    for gone in ('wf-cell-input', 'wf-doc-para-in', 'data-para-key'):
        assert gone not in js, '旧白名单编辑钩子残留：%s' % gone
    # 复选框仍是字符切换（□↔☑），且不触发整页重渲染丢光标
    assert "cb.textContent = wasChecked ? UNCHECKED : CHECKED" in js
