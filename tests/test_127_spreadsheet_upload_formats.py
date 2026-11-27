# -*- coding: utf-8 -*-
"""tests/test_127_spreadsheet_upload_formats.py — 全站表格上传五格式统一扩展契约测试。

覆盖：
- utils/spreadsheet_reader.py：.xlsx / .xlsm / .xls（含 XML 兜底）/ .csv / .xlsb 校验与解析
- preserve_spreadsheet_ext：纯中文文件名清洗后保留原始扩展名
- utils/softtable_engine.py：parse_table_file 五格式放行
- utils/domain_batch.py / utils/roster_routes.py / utils/attachment_service.py / routes/safety.py 扩展名集合
- 全站 templates/ 表格上传 input accept 属性一致性扫描
"""
import glob
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.spreadsheet_reader import (
    SPREADSHEET_ACCEPT,
    SPREADSHEET_EXTS,
    is_allowed_spreadsheet,
    preserve_spreadsheet_ext,
    read_spreadsheet_dicts,
    read_spreadsheet_table,
    validate_spreadsheet_magic,
)


def _make_xlsx_bytes(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_xls_xml_bytes(rows):
    body = []
    for r in rows:
        cells = ''.join(f'<Cell><Data ss:Type="String">{c}</Data></Cell>' for c in r)
        body.append(f'<Row>{cells}</Row>')
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<?mso-application progid="Excel.Sheet"?>\n'
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"'
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
        '<Worksheet ss:Name="Sheet1"><Table>' + ''.join(body) + '</Table></Worksheet></Workbook>'
    )
    return xml.encode('utf-8')


def test_allowed_exts_and_preserve_ext():
    assert set(SPREADSHEET_EXTS) == {'.xlsx', '.xls', '.xlsb', '.csv', '.xlsm'}
    for ext in SPREADSHEET_EXTS:
        assert is_allowed_spreadsheet(f'测试名单{ext}') is True
        assert preserve_spreadsheet_ext(f'测试名单{ext}', 'upload') == f'upload{ext}'
    assert is_allowed_spreadsheet('bad.exe') is False


def test_read_xlsx_xlsm_xls_csv():
    sample = [['姓名', '金额'], ['张三', '100'], ['李四', '200']]
    xlsx_data = _make_xlsx_bytes(sample)

    # .xlsx & .xlsm
    for name in ('a.xlsx', 'b.xlsm'):
        ok, err = validate_spreadsheet_magic(name, xlsx_data)
        assert ok is True, err
        hdrs, rows = read_spreadsheet_table(name, xlsx_data)
        assert hdrs == ['姓名', '金额']
        assert len(rows) == 2 and rows[0][0] == '张三'

    # .xls (SpreadsheetML XML)
    xls_data = _make_xls_xml_bytes(sample)
    ok, err = validate_spreadsheet_magic('c.xls', xls_data)
    assert ok is True, err
    hdrs, dicts = read_spreadsheet_dicts('c.xls', xls_data)
    assert hdrs == ['姓名', '金额']
    assert dicts[1]['姓名'] == '李四' and dicts[1]['金额'] == '200'

    # .csv (GB18030)
    csv_data = '姓名,金额\n王五,300\n'.encode('gb18030')
    ok, err = validate_spreadsheet_magic('d.csv', csv_data)
    assert ok is True, err
    hdrs, dicts = read_spreadsheet_dicts('d.csv', csv_data)
    assert hdrs == ['姓名', '金额'] and dicts[0]['姓名'] == '王五'


def test_xlsb_magic_validation():
    ok, _ = validate_spreadsheet_magic('e.xlsb', b'PK\x03\x04dummy')
    assert ok is True
    bad_ok, _ = validate_spreadsheet_magic('e.xlsb', b'NOTZIP')
    assert bad_ok is False


def test_all_engine_constants_aligned():
    import utils.attachment_service as att
    import utils.domain_batch as dbatch
    import routes.dynatable as dyn
    import routes.safety as safety
    import routes.shop as shop

    expected = set(SPREADSHEET_EXTS)
    assert set(dbatch.ALLOWED_EXT) == expected
    assert set(dyn.ALLOWED_EXT) == expected
    assert set(safety._SAFETY_TABLE_EXT) == expected
    assert set(shop._ROSTER_ALLOWED_EXT) == expected
    for ext in ('xlsx', 'xls', 'xlsb', 'csv', 'xlsm'):
        assert ext in att.ALLOWED_EXTS
        assert ext in safety.ALLOWED_EXTS


def test_templates_spreadsheet_accept_aligned():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in sorted(glob.glob(os.path.join(root, 'templates', '**', '*.html'), recursive=True)):
        html = open(path, encoding='utf-8').read()
        for m in re.finditer(r'accept="([^"]+)"', html):
            val = m.group(1)
            if '.xlsx' in val and '.pdf' not in val:
                assert val in (SPREADSHEET_ACCEPT, SPREADSHEET_ACCEPT + ',.docx'), f'{path}: expected {SPREADSHEET_ACCEPT}, got {val}'
