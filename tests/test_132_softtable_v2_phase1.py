# -*- coding: utf-8 -*-
"""tests/test_132_softtable_v2_phase1.py — 软表引擎 V2 第一阶段测试（4 形态 + 6 短板 + 1:1 导出 + 端点契约）。

注：外发补丁原文件名 test_127_softtable_v2_phase1.py（按补丁作者序号），与主仓任务 127
（上传格式全站扩展）撞号，套打时按主仓任务号重命名为 test_132（AGENTS.md 测试运行规约）。
"""
import importlib.util
import io
import os
import sys
import tempfile

import openpyxl
import pytest
from openpyxl.comments import Comment
from openpyxl.styles import Font


def _load_mod(rel_path, mod_name):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    full = os.path.join(root, rel_path)
    spec = importlib.util.spec_from_file_location(mod_name, full)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wb_bytes(wb):
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_titled_multisheet_xlsx():
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = '党委成员花名册'
    ws1.merge_cells('A1:D1')
    ws1['A1'] = '党委成员花名册'
    ws1.append(['姓名', '性别', '身份证号码', '备注'])
    ws1.append(['林蛟', '男', '511324199301011234', ''])
    ws1.append(['许期刚', '男', '511324197502022345', ''])

    ws2 = wb.create_sheet('居民小组长花名册')
    ws2.merge_cells('A1:D1')
    ws2['A1'] = '居民小组长花名册'
    ws2.append(['所在小组', '姓名', '联系电话', '备注'])
    # 数据区纵向合并测试（二组跨 2 行）
    ws2.append(['二组', '许期刚', '13800001111', ''])
    ws2.append([None, '张爱民', '13800002222', ''])
    ws2.merge_cells('A3:A4')
    # 批注与删除线语义测试
    ws2['B4'].comment = Comment('已核实', '经办人')
    ws2['B4'].font = Font(strike=True)
    # 表尾汇总行测试
    ws2.append(['合计', '=COUNTA(B3:B4)', '', ''])
    return _wb_bytes(wb)


def _build_vertical_sections_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.merge_cells('A1:D1')
    ws['A1'] = '社区党委/居委会组成人员'
    ws.append(['姓名', '职务', '分工', '联系电话'])
    ws.append(['林蛟', '书记、主任', '负责全面工作', '13900001111'])
    ws.merge_cells('A4:D4')
    ws['A4'] = '居务监督委员会'
    ws.append(['姓名', '职务', '联系电话'])
    ws.append(['许期刚', '主任', '13900002222'])
    return _wb_bytes(wb)


def _build_side_by_side_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '增减变动表'
    ws.merge_cells('A1:G1')
    ws['A1'] = '高龄津贴人员增减变动表'
    ws.merge_cells('A2:G2')
    ws['A2'] = '村（社区）盖章：    填报人：    报送时间：2026年11月'
    ws.merge_cells('A3:D3')
    ws['A3'] = '新 增'
    ws.merge_cells('E3:G3')
    ws['E3'] = '减 少'
    ws.append(['序号', '姓名', '身份证号码', '电话号码', '序号', '姓名', '死亡时间'])
    # 空表样：无数据行，验证空表推断
    return _wb_bytes(wb)


def test_parse_multisheet_ffill_comment_strike_and_summary():
    parser = _load_mod('utils/softtable_v2_parser.py', 'softtable_v2_parser')
    raw = _build_titled_multisheet_xlsx()
    res = parser.parse_workbook_v2('1、社区三委花名册.xlsx', io.BytesIO(raw))

    assert res['sheet_count'] == 2
    sh1, sh2 = res['sheets']
    assert sh1['mode'] == 'standard_titled'
    t1 = sh1['tables'][0]
    assert t1['row_count'] == 2
    assert t1['fields'][2]['sensitivity'] == 'identity'

    # 验证纵合并 ffill、批注与删除线提取、表尾汇总行转 summary_rules
    t2 = sh2['tables'][0]
    assert t2['row_count'] == 2  # 合计行未混入数据行
    assert t2['rows'][1][0] == '二组'  # ffill 自动补齐
    assert '批注:已核实' in t2['rows'][1][3]
    assert '原表删除线' in t2['rows'][1][3]
    assert len(t2['layout']['summary_rules']) == 1
    assert t2['layout']['summary_rules'][0]['op'] == 'count'
    assert 'f00' in t2['layout']['merge_export_cols']


def test_parse_vertical_sections_and_side_by_side_empty_template():
    parser = _load_mod('utils/softtable_v2_parser.py', 'softtable_v2_parser')

    # 单页纵向多区块
    res_v = parser.parse_workbook_v2('1.1、人员名单.xlsx', io.BytesIO(_build_vertical_sections_xlsx()))
    sh_v = res_v['sheets'][0]
    assert sh_v['mode'] == 'vertical_sections'
    assert len(sh_v['tables']) == 2
    assert len(sh_v['tables'][0]['fields']) == 4
    assert len(sh_v['tables'][1]['fields']) == 3

    # 横向并排双表 + 空表推断
    res_s = parser.parse_workbook_v2('高龄津贴增减变动表.xlsx', io.BytesIO(_build_side_by_side_xlsx()))
    sh_s = res_s['sheets'][0]
    assert sh_s['mode'] == 'side_by_side'
    assert len(sh_s['tables']) == 2
    left_t, right_t = sh_s['tables']
    assert len(left_t['fields']) == 4 and left_t['fields'][2]['sensitivity'] == 'identity'
    assert len(right_t['fields']) == 3 and right_t['fields'][2]['data_type'] == 'date'
    assert left_t['row_count'] == 0


def test_manual_multilevel_header_override():
    parser = _load_mod('utils/softtable_v2_parser.py', 'softtable_v2_parser')
    raw = _build_side_by_side_xlsx()
    res = parser.parse_workbook_v2(
        '高龄津贴.xlsx',
        io.BytesIO(raw),
        header_overrides={'增减变动表': {'header_start': 3, 'header_end': 4}},
    )
    sh = res['sheets'][0]
    assert sh['mode'] == 'manual_header'
    hdrs = [f['display_name'] for f in sh['tables'][0]['fields']]
    assert hdrs[0] == '新 增-序号'
    assert hdrs[4] == '减 少-序号'


def test_layout_store_optimistic_lock_and_group_export():
    layout_mod = _load_mod('utils/softtable_layout.py', 'softtable_layout')
    parser = _load_mod('utils/softtable_v2_parser.py', 'softtable_v2_parser')

    with tempfile.TemporaryDirectory() as tmp:
        store = layout_mod.SoftTableLayoutStore(tmp)
        saved = store.save_layout('org_party', {'layout_mode': 'standard_titled', 'col_pcts': [50, 50]})
        assert saved['version_ts']
        # 乐观锁冲突必须抛 LayoutConflictError
        try:
            store.save_layout('org_party', {'col_pcts': [40, 60]}, expected_version_ts='stale_ts')
            assert False, 'should raise LayoutConflictError'
        except layout_mod.LayoutConflictError:
            pass
        # 携带正确 version_ts 允许更新
        saved2 = store.save_layout('org_party', {'col_pcts': [40, 60]}, expected_version_ts=saved['version_ts'])
        assert saved2['col_pcts'] == [40, 60]

    # 1:1 导出测试（单表含公式汇总 + 纵合并还原 + print_title_rows）
    res = parser.parse_workbook_v2('1、社区三委花名册.xlsx', io.BytesIO(_build_titled_multisheet_xlsx()))
    t2 = res['sheets'][1]['tables'][0]
    xlsx_bytes = layout_mod.export_styled_table_xlsx(
        [f['display_name'] for f in t2['fields']], t2['rows'], t2['layout'], '居民小组长花名册'
    )
    out_wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=False)
    out_ws = out_wb.active
    assert out_ws.print_title_rows == '$1:$2' or out_ws.print_title_rows == '1:2'
    # 最后一行应包含真实 Excel 汇总公式
    last_row_vals = [out_ws.cell(out_ws.max_row, c).value for c in range(1, 5)]
    assert any(isinstance(v, str) and v.startswith('=COUNTA(') for v in last_row_vals)

    # 横向并排双表拼装导出测试
    res_s = parser.parse_workbook_v2('高龄津贴增减变动表.xlsx', io.BytesIO(_build_side_by_side_xlsx()))
    bundles = [
        (t['suggested_name'], [f['display_name'] for f in t['fields']], [['1', '陈明贵', '5113****1234', '138****1111']], t['layout'])
        for t in res_s['sheets'][0]['tables']
    ]
    sbs_bytes = layout_mod.export_workbook_group_xlsx(bundles, '高龄津贴增减变动表')
    sbs_wb = openpyxl.load_workbook(io.BytesIO(sbs_bytes))
    sbs_ws = sbs_wb.active
    assert sbs_ws['A1'].value == '高龄津贴人员增减变动表'
    assert sbs_ws['A3'].value == '新 增'
    assert sbs_ws['E3'].value == '减 少'


# ------------------------------------------------------------------ 端点契约（套打补强：补丁自带 4 例未覆盖任何路由）
# 以下用例走 Flask 测试客户端，隔离库 + KeyHolder 初始化（110~116 同款先例）。

def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / 'shucun.db')
    for key, value in {
        'SHUCUN_DB_PATH': db_path,
        'SHUCUN_TEMP_DIR': str(tmp_path / 'temp'),
        'SHUCUN_UPLOAD_DIR': str(tmp_path / 'uploads'),
        'SHUCUN_BACKUP_DIR': str(tmp_path / 'backups'),
        'SHUCUN_DATA_DIR': str(tmp_path / 'data'),
        'SHUCUN_LOG_DIR': str(tmp_path / 'logs'),
        'SHUCUN_LICENSE_FILE': str(tmp_path / 'license.key'),
        'SHUCUN_LAN_AUTHORIZED': '1',
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(config, 'TEMP_DIR', str(tmp_path / 'temp'))
    monkeypatch.setattr(config, 'UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(config.Config, 'SQLALCHEMY_DATABASE_URI',
                        'sqlite:///%s?check_same_thread=False' % db_path)
    for sub in ('temp', 'uploads', 'backups', 'data', 'logs'):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    return application


@pytest.fixture()
def client(tmp_path, monkeypatch):
    application = _make_app(tmp_path, monkeypatch)
    from utils.crypto import generate_salt, key_holder
    key_holder.initialize('softtable-test-pass', salt=generate_salt())
    c = application.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
    return c


def _upload_v2(client, filename, raw):
    return client.post('/dynatable/api/upload-v2',
                       data={'file': (io.BytesIO(raw), filename)},
                       content_type='multipart/form-data')


def _items_of(parsed, codes=None):
    out, seq = [], 0
    for si, sh in enumerate(parsed['sheets']):
        for ti, t in enumerate(sh['tables']):
            out.append({'sheet_name': sh['sheet_name'], 'table_idx': ti,
                        'name': t['suggested_name'],
                        'code': (codes[seq] if codes else 'v2t_%d' % seq)})
            seq += 1
    return out


def test_upload_v2_rejects_unsupported_and_empty(client):
    assert _upload_v2(client, 'a.exe', b'MZ').get_json()['success'] is False
    assert _upload_v2(client, 'a.xlsx', b'').get_json()['success'] is False


def test_create_batch_multi_sheet_and_group_export_roundtrip(client):
    """多 Sheet 勾选批量建表 → 版式落盘 → 整本导出 1:1 → 导出件可回读。"""
    parsed = _upload_v2(client, '1、社区三委花名册.xlsx',
                        _build_titled_multisheet_xlsx()).get_json()
    assert parsed['success'], parsed
    assert parsed['data']['sheet_count'] == 2
    items = _items_of(parsed['data'], codes=['org_party_v2', 'group_leader_v2'])
    resp = client.post('/dynatable/api/create-batch', json={
        'token': parsed['data']['token'], 'module_key': 'dynatable',
        'group_name': '社区三委花名册', 'items': items,
    }).get_json()
    assert resp['success'], resp
    assert [c['imported'] for c in resp['data']['created']] == [2, 2]

    # 单表导出走样式分支（存在 LayoutSpec）且仍是合法 xlsx
    single = client.get('/dynatable/api/t/org_party_v2/export')
    assert single.status_code == 200 and single.data[:2] == b'PK'
    # 整本导出：一个 Sheet 对应一张子表
    workbook = openpyxl.load_workbook(io.BytesIO(
        client.get('/dynatable/api/group/社区三委花名册/export').data))
    assert workbook.sheetnames == ['党委成员花名册', '居民小组长花名册']
    # 版式读端点 + 乐观锁冲突 409
    layout = client.get('/dynatable/api/t/org_party_v2/layout').get_json()
    assert layout['success'] and layout['data']['version_ts']
    stale = client.post('/dynatable/api/t/org_party_v2/layout',
                        json={'col_pcts': [1], 'expected_version_ts': 'stale'})
    assert stale.status_code == 409


def test_create_batch_blocks_bad_rows_unless_skip_requested(client):
    """错误行口径须与既有 /api/create 一致：未显式要求跳过则整笔拒绝，不得静默丢行。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '花名册'
    ws.append(['姓名', '联系电话', '备注'])
    ws.append(['甲', '13800001111', ''])
    ws.append(['乙', '不是号码', ''])       # 第 3 行：非法号码
    parsed = _upload_v2(client, '号码册.xlsx', _wb_bytes(wb)).get_json()
    t = parsed['data']['sheets'][0]['tables'][0]
    # 人工把电话列声明为 number，制造一条必然失败的行（不依赖推断结果）
    items = [{'sheet_name': '花名册', 'table_idx': 0, 'name': '号码册', 'code': 'bad_rows_v2',
              'fields': [{'code': 'f00', 'display_name': '姓名', 'data_type': 'text'},
                         {'code': 'f01', 'display_name': '联系电话', 'data_type': 'number'},
                         {'code': 'f02', 'display_name': '备注', 'data_type': 'text'}]}]
    body = client.post('/dynatable/api/create-batch', json={
        'token': parsed['data']['token'], 'module_key': 'dynatable', 'items': items,
    }).get_json()
    assert body['success'] is False and '不合规' in body['msg'], body
    assert client.get('/dynatable/api/t/bad_rows_v2').status_code == 404, '被拒后不得留下半截台账'


def test_create_batch_failure_leaves_no_orphan_layout(client, tmp_path):
    """循环中途失败：数据库回滚之外，已落盘的版式文件也必须清掉，避免孤儿版式套到后续同名表。"""
    parsed = _upload_v2(client, '1、社区三委花名册.xlsx',
                        _build_titled_multisheet_xlsx()).get_json()
    items = _items_of(parsed['data'], codes=['first_ok_v2', 'second_fail_v2'])
    # 预占第二个 code，使循环到第 2 张时命中「表标识已存在」
    items[1]['code'] = items[0]['code']
    body = client.post('/dynatable/api/create-batch', json={
        'token': parsed['data']['token'], 'module_key': 'dynatable', 'items': items,
    }).get_json()
    assert body['success'] is False, body
    import os
    layout_dir = os.path.join(str(tmp_path), 'data', 'softtable_layouts')
    left = [n for n in (os.listdir(layout_dir) if os.path.isdir(layout_dir) else []) if n.endswith('.json')]
    assert left == [], '失败批次遗留孤儿版式文件：%s' % left


def test_reparse_v2_with_manual_header_rows(client):
    parsed = _upload_v2(client, '高龄津贴增减变动表.xlsx',
                        _build_side_by_side_xlsx()).get_json()
    assert parsed['data']['sheets'][0]['mode'] == 'side_by_side'
    resp = client.post('/dynatable/api/reparse-v2', json={
        'token': parsed['data']['token'],
        'header_overrides': {'增减变动表': {'header_start': 3, 'header_end': 4}},
    }).get_json()
    assert resp['success'] and resp['data']['sheets'][0]['mode'] == 'manual_header', resp


def test_create_batch_skip_error_rows_reports_skipped(client):
    """显式勾选跳过错误行：建成表，错误行数如实回传，合法行完整入库。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '花名册'
    ws.append(['姓名', '联系电话', '备注'])
    ws.append(['甲', '13800000001', ''])
    ws.append(['乙', '不是号码', ''])
    ws.append(['丙', '13800000003', ''])
    parsed = _upload_v2(client, '号码册.xlsx', _wb_bytes(wb)).get_json()
    items = [{'sheet_name': '花名册', 'table_idx': 0, 'name': '号码册', 'code': 'skip_rows_v2',
              'fields': [{'code': 'f00', 'display_name': '姓名', 'data_type': 'text'},
                         {'code': 'f01', 'display_name': '联系电话', 'data_type': 'number'},
                         {'code': 'f02', 'display_name': '备注', 'data_type': 'text'}]}]
    body = client.post('/dynatable/api/create-batch', json={
        'token': parsed['data']['token'], 'module_key': 'dynatable',
        'items': items, 'skip_error_rows': True,
    }).get_json()
    assert body['success'], body
    created = body['data']['created'][0]
    assert (created['imported'], created['skipped']) == (2, 1), created
    records = client.get('/dynatable/api/t/skip_rows_v2/records').get_json()
    assert records['success'] and len(records['data']) == 2, records


def test_group_export_keeps_title_rows_and_summary_formula(client):
    """整本导出的 1:1 硬指标：print_title_rows 写入 + 汇总公式为真实 Excel 公式。"""
    parsed = _upload_v2(client, '1、社区三委花名册.xlsx',
                        _build_titled_multisheet_xlsx()).get_json()
    items = _items_of(parsed['data'], codes=['rt_party_v2', 'rt_leader_v2'])
    assert client.post('/dynatable/api/create-batch', json={
        'token': parsed['data']['token'], 'module_key': 'dynatable',
        'group_name': '回读工作簿', 'items': items,
    }).get_json()['success']
    exported = client.get('/dynatable/api/group/回读工作簿/export').data
    workbook = openpyxl.load_workbook(io.BytesIO(exported), data_only=False)
    ws = workbook['居民小组长花名册']
    assert ws.print_title_rows in ('$1:$2', '1:2'), ws.print_title_rows
    last = [ws.cell(ws.max_row, c).value for c in range(1, ws.max_column + 1)]
    assert any(isinstance(v, str) and v.startswith('=') for v in last), last
    # 页面渲染：V2 区块与脚本已接入上传页
    page = client.get('/dynatable/upload').get_data(as_text=True)
    assert 'id="v2ImportSection"' in page and 'js/pages/dynatable_v2_upload.js' in page
    # 137（补丁 B-2）：建表成功即跳转统一工作台，上传页不再停留渲染结果链接；
    # 「导出整本」入口迁移到工作台头部与台账中心分组卡（能力不丢，入口换位）。
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    wb = open(os.path.join(root_dir, 'templates', 'components', 'roster', '_softtable_workbench.html'),
              encoding='utf-8').read()
    idx_js = open(os.path.join(root_dir, 'static', 'js', 'pages', 'dynatable_index.js'),
                  encoding='utf-8').read()
    assert '/api/group/' in wb and '导出整本' in wb, '工作台头部丢失整本导出入口（132 能力）'
    assert '/api/group/' in idx_js and '导出整本' in idx_js, '台账中心分组卡丢失整本导出入口（132 能力）'


# ------------------------------------------------------------------ 标题与表头之间夹空行（套打实测缺陷）
# 补丁的 4 个形态分支都隐含「全行合并标题的下一行紧贴内容」。标题下空一行是最常见的排版，
# 此时 r_ptr 停在空行上：标准表把空行当表头 → eff_cols 缩到 1（整表只剩 1 列、真表头被当成
# 第 1 条数据）；并排双表/多区块判定同样错位。且不报错、无警告 —— 静默错解析。

def _blank_line_variants():
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = '党委成员花名册'
    ws1.merge_cells('A1:D1')
    ws1['A1'] = '党委成员花名册'
    ws1.append([])                                   # ← 标题与表头之间的空行
    ws1.append(['姓名', '性别', '身份证号码', '备注'])
    ws1.append(['林蛟', '男', '511324199301011234', ''])
    ws1.append(['许期刚', '男', '511324197502022345', ''])
    ws2 = wb.create_sheet('增减变动表')
    ws2.merge_cells('A1:G1')
    ws2['A1'] = '高龄津贴人员增减变动表'
    ws2.append([])                                   # ← 空行
    ws2.merge_cells('A3:D3')
    ws2['A3'] = '新 增'
    ws2.merge_cells('E3:G3')
    ws2['E3'] = '减 少'
    ws2.append(['序号', '姓名', '身份证号码', '电话号码', '序号', '姓名', '死亡时间'])
    ws3 = wb.create_sheet('人员名单')
    ws3.merge_cells('A1:D1')
    ws3['A1'] = '社区党委/居委会组成人员'
    ws3.append([])                                   # ← 空行
    ws3.append(['姓名', '职务', '分工', '联系电话'])
    ws3.append(['林蛟', '书记、主任', '负责全面工作', '13900001111'])
    ws3.merge_cells('A5:D5')
    ws3['A5'] = '居务监督委员会'
    ws3.append(['姓名', '职务', '联系电话'])
    ws3.append(['许期刚', '主任', '13900002222'])
    return _wb_bytes(wb)


def test_blank_line_between_title_and_header():
    res = parser = None
    parser = _load_mod('utils/softtable_v2_parser.py', 'softtable_v2_parser')
    res = parser.parse_workbook_v2('空白行工作簿.xlsx', io.BytesIO(_blank_line_variants()))

    titled, sbs, secs = res['sheets']
    # 带合并大标题标准表：不得把空行当表头
    assert titled['mode'] == 'standard_titled'
    t = titled['tables'][0]
    assert len(t['fields']) == 4, '标题下空行导致表头识别成空行，整表只剩 %d 列' % len(t['fields'])
    assert [f['display_name'] for f in t['fields']] == ['姓名', '性别', '身份证号码', '备注']
    assert t['row_count'] == 2 and t['fields'][2]['sensitivity'] == 'identity'
    # 左右并排双表：判定行不得停在空行
    assert sbs['mode'] == 'side_by_side', sbs['mode']
    assert [len(x['fields']) for x in sbs['tables']] == [4, 3]
    # 单页纵向多区块：区块表头行不得错位
    assert secs['mode'] == 'vertical_sections', secs['mode']
    assert [len(x['fields']) for x in secs['tables']] == [4, 3]
    assert secs['tables'][0]['row_count'] == 1 and secs['tables'][1]['row_count'] == 1


def test_create_batch_locked_key_returns_readable_error(tmp_path, monkeypatch):
    """KeyHolder 锁定时建含加密列的表：须返回可读业务错误（400 + msg），不得 500。

    GUI 冒烟实测：真实环境 KeyHolder 默认锁定，build_records 抛 SoftTableError
    （「密钥未解锁…」）；预检阶段漏接会冒成 500，前端只显示「批量建表请求失败」。
    """
    application = _make_app(tmp_path, monkeypatch)
    from utils.crypto import key_holder
    key_holder.lock()                      # 显式置锁定态，不依赖前序用例遗留状态
    assert key_holder.is_initialized is False
    c = application.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
    parsed = _upload_v2(c, '1、社区三委花名册.xlsx', _build_titled_multisheet_xlsx()).get_json()
    assert parsed['success'], parsed
    try:
        resp = c.post('/dynatable/api/create-batch', json={
            'token': parsed['data']['token'], 'module_key': 'dynatable',
            'items': _items_of(parsed['data']),
        })
    finally:
        key_holder.initialize('softtable-test-pass', salt=__import__('utils.crypto', fromlist=['generate_salt']).generate_salt())
    assert resp.status_code != 500, resp.get_data(as_text=True)[:300]
    body = resp.get_json()
    assert body['success'] is False and '密钥' in body['msg'], body
    # 被拒后不得留下半截台账
    assert c.get('/dynatable/api/t/v2t_0').status_code == 404
