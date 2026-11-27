# -*- coding: utf-8 -*-
"""tests/test_062_veteran_structure.py — 退伍军人页 v4.0 结构守卫（062 P5 + 075 RC-18 + 082 B-3）。

真源：062 任务书 P5（验收 3「colspan 按表头列数画」）+ 设计文档 §5–§7；
075 RC-18 整改（样图 v7-14）：筛选区 11 项大卡 → 紧凑工具栏（单搜索框 q 组合检索，
300ms 防抖）+ 动作按钮入工具栏；表格上方独立列计数行「已选 X / 共 N 列」；
返回链接 + 副标题「N 列超宽表 · 列选择器 + 横向滚动」。
082 B-3：新增「重点群体类别」列（24→25 列，默认已选 16→17），同步列选择索引。
093 B-8：新增「退役类别」列（25→26 列，默认已选 17→18），同步列选择索引。
守护：
  1) 独立页 0 胶囊（同待办口径，反例断言）
  2) 返回链接 + 页头（标题 + RC-18 副标题）
  3) 紧凑工具栏：单 q 搜索 + 重点群体筛选 + 4 动作（导入/下载模板/导出/新增，唯一 primary）
  4) 列计数行：已选 17 / 共 25 列 · 横向滚动查看全部 + 列选择挂载点
  5) 25 列宽表：列头逐列比对 + dense + min-width + 横滚
  6) 分页：pageStat + pagination
  7) 3 弹窗：编辑（24 字段）/ 详情 / 导入 + 冻结 id
  8) JS：colspan=25（无 24）、scColToggle fixed [0,24] + defaultHidden 6 列、既有守卫不破
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VET_COLS = ['序号', '姓名', '身份证号码', '性别', '民族', '政治面貌', '文化程度', '户口所在地',
            '所在组', '家庭详细地址', '联系方式', '亲属姓名', '亲属联系方式', '入伍年月',
            '退役年月', '服役年限', '原服役部队', '原服役兵种', '职务/军衔', '退伍证件编号',
            '户籍性质', '安置情况', '退役类别', '重点群体类别', '备注', '操作']

# 075 RC-18：默认隐藏 6 列（0-based：亲属姓名/原服役部队/原服役兵种/职务军衔/安置情况/备注）
# 082 B-3：备注因新增列右移，索引 22→23；093 B-8：退役类别插入后再右移 23→24
DEFAULT_HIDDEN = [11, 16, 17, 18, 21, 24]

VET_IDS = [
    'searchForm', 'q', 'veteranColHost', 'veteranColCount', 'tableBody', 'pagination',
    'exportBtn', 'btnAdd', 'formModal', 'saveForm', 'editId', 'formModalTitle', 'btnSave',
    'fName', 'fIdCard', 'fGender', 'fNation', 'fPolitics', 'fEducation', 'fHouseholdReg',
    'fRegisteredResidence', 'fGrid', 'fHomeAddress', 'fPhone', 'fRelativeName', 'fRelativePhone',
    'fDischargeCertNo', 'fEnlistDate', 'fDischargeDate', 'fServiceYears', 'fServiceUnit',
    'fServiceBranch', 'fServiceRank', 'fKeyGroup', 'filterKeyGroup',
    'detailModal', 'detailModalBody', 'detailModalTitle',
    'importModal', 'importFile', 'btnImport', 'nameSuggest',
]

FORM_FIELDS = ['name', 'id_card', 'gender', 'nation', 'politics_status', 'education',
               'household_reg_type', 'registered_residence', 'grid', 'home_address', 'phone',
               'relative_name', 'relative_phone', 'discharge_cert_no', 'enlist_date',
               'discharge_date', 'service_years', 'placement_status', 'key_group_category',
               'service_unit', 'service_branch', 'service_rank', 'remark']


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


def _html(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    resp = client.get('/veteran/')
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


def _js():
    with open(os.path.join(BASE, 'static', 'js', 'pages', 'veteran_list.js'), encoding='utf-8') as f:
        return f.read()


def _block(html, start, end):
    i = html.index(start)
    j = html.index(end, i)
    assert i < j
    return html[i:j]


def test_standalone_no_chips(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    assert 'class="sc-chips"' not in html, '退伍为独立页，不应挂模块胶囊'


def test_backlink_and_page_head(tmp_path, monkeypatch):
    """075 RC-18：页头（标题 + 样图副标题），页头不再挂动作。
    2026-11-19：主理人拍板移除「← 返回设置中心」sc-backlink（原 v7-14 样图口径作废）。"""
    html = _html(tmp_path, monkeypatch)
    assert 'sc-backlink' not in html and '返回设置中心' not in html, \
        '回链已拍板移除，不应再出现'
    head = _block(html, '<header class="sc-page-head">', '</header>')
    assert '退伍军人档案' in head
    assert '26 列超宽表 · 列选择器 + 横向滚动' in head
    assert 'sc-btn' not in head, '动作已下沉工具栏（v7-14），页头只留标题/副标题'


def test_toolbar_compact_and_actions(tmp_path, monkeypatch):
    """075 RC-18：紧凑工具栏 = 单 q 搜索 + 4 动作（导入/下载模板/导出/新增，唯一 primary）。"""
    html = _html(tmp_path, monkeypatch)
    form = _block(html, '<form class="sc-toolbar" id="searchForm"', '</form>')
    assert 'name="q"' in form and '姓名 / 身份证 / 政治面貌 / 兵种' in form
    for frag in ('导入', '下载模板', '导出', '新增'):
        assert frag in form, f'工具栏缺动作 {frag}'
    assert form.count('sc-btn--primary') == 1, '工具栏唯一 primary（新增）'
    assert '导出 Excel' not in form, 'v7-14 动作文案为「导出」'


def test_col_count_row_and_host(tmp_path, monkeypatch):
    """075 RC-18：表格上方独立列计数行「已选 16 / 共 24 列 · 横向滚动查看全部」+ 列选择挂载点。"""
    html = _html(tmp_path, monkeypatch)
    assert 'class="sc-table-toolbar"' in html, '缺列计数行容器'
    assert 'id="veteranColCount"' in html
    assert '已选 18 / 共 26 列 · 横向滚动查看全部' in html
    assert 'id="veteranColHost"' in html


def test_table_24_columns_scroll(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    assert 'id="dataTable"' in html
    assert 'class="sc-table sc-table--dense"' in html
    assert 'min-width:2350px' in html
    assert 'sc-table-wrap' in html
    tr = re.search(r'<thead class="sc-table__head">(.*?)</thead>', html, re.S).group(1)
    cols = re.findall(r'<th class="sc-table__th[^"]*"[^>]*>([^<]+)</th>', tr)
    assert cols == VET_COLS, f'清单 26 列，实得 {cols}'


def test_pagination_stat(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    assert 'id="pageStat"' in html and 'id="pagination"' in html
    assert 'sc-pagination__pager' in html


def test_modals_and_frozen_ids(tmp_path, monkeypatch):
    html = _html(tmp_path, monkeypatch)
    for dom_id in VET_IDS:
        assert f'id="{dom_id}"' in html, f'冻结契约缺 #{dom_id}'
    assert html.count('class="sc-modal') >= 3
    form = _block(html, 'id="saveForm"', '</form>')
    for name in FORM_FIELDS:
        assert f'name="{name}"' in form, f'编辑表单缺字段 {name}'
    assert len(re.findall(r'class="sc-field(?: [^"]*)?"', form)) == 24
    assert form.count('sc-field__required') == 13
    imp = html[html.index('id="importModal"'):]
    # 2026-11-19 换壳：导入模式下拉改 pw-radio-card 单选卡（name="importMode" 契约不变）
    assert 'id="importFile"' in imp
    assert 'type="radio" name="importMode"' in imp
    assert 'value="append"' in imp and 'value="overwrite"' in imp


def test_js_colspan_24_and_col_toggle():
    js = _js()
    assert 'colspan="26"' in js
    assert 'colspan="25"' not in js, '验收 3：colspan 必须按表头 26 列画'
    assert "scColToggle.init('#dataTable'" in js
    assert 'fixed: [0, 25]' in js
    # 075 RC-18：默认隐藏 6 列（样图 16/24）+ 计数行 + 「列选择」文案 + 300ms 防抖
    assert f'defaultHidden: {DEFAULT_HIDDEN}' in js
    assert "countHost: '#veteranColCount'" in js
    assert "toggleLabel: '列选择'" in js
    assert '300' in js, 'q 搜索 300ms 防抖'
    assert 'sc-btn--text' in js and 'class="btn ' not in js, 'BS 行内按钮残留'
    assert "'#pageStat'" in js and "'共 ' + (d.total" in js
    assert '/veteran/api/list' in js and '/veteran/api/save' in js


def test_existing_guards_preserved():
    js = _js()
    assert "select.cs-native').trigger('refresh.cs')" in js
    assert js.count('empty()') >= 2, 'loadDict 两分支先 empty（GLB-24）'
    # 代码评审整改（P3）后导出形式升级：window.* 直挂收敛为 SC.exportPageGlobals
    # （注册进 _pageGlobalKeys，spa.js 换页 clearPageGlobals 自动清理），导出符号契约不变。
    m = re.search(r'window\.SC\.exportPageGlobals\(\{([^}]*)\}\)', js)
    assert m, 'SC.exportPageGlobals 全局导出缺失'
    for name in ('showImport', 'showAdd', 'showEdit', 'showDetail', 'load'):
        assert re.search(r'\b%s\s*:' % name, m.group(1)), f'全局导出缺失 {name}'
