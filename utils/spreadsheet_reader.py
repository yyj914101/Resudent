# -*- coding: utf-8 -*-
"""utils/spreadsheet_reader.py — 全站统一表格上传解析器（单一真源）。

统一支持 5 种表格上传格式：
  .xlsx / .xlsm  → openpyxl（read_only=True, data_only=True）
  .xls           → xlrd（97-2003 二进制）+ SpreadsheetML 2003 XML 兜底
  .xlsb          → pyxlsb（Excel 二进制工作簿）
  .csv           → 标准库 csv（UTF-8-SIG / GB18030 / GBK 自动识别）

对外接口：
  SPREADSHEET_EXTS              ('.xlsx', '.xls', '.xlsb', '.csv', '.xlsm')
  SPREADSHEET_ACCEPT            '.xlsx,.xls,.xlsb,.csv,.xlsm'
  SPREADSHEET_EXT_LABEL         '.xlsx / .xls / .xlsb / .csv / .xlsm'
  is_allowed_spreadsheet(name)  -> bool
  preserve_spreadsheet_ext(orig, cleaned_base) -> str
  validate_spreadsheet_magic(filename, data) -> (ok, err_msg)
  read_spreadsheet_matrix(filename, source) -> list[list[Any]]
  read_spreadsheet_table(filename, source) -> (headers, rows)
  read_spreadsheet_dicts(filename, source) -> (headers, list[dict])
  count_spreadsheet_rows(path, limit) -> (count, over_limit)
"""
from __future__ import annotations

import csv
import io
import os
import xml.etree.ElementTree as ET
from typing import Any, List, Tuple

SPREADSHEET_EXTS = ('.xlsx', '.xls', '.xlsb', '.csv', '.xlsm')
SPREADSHEET_ACCEPT = ','.join(SPREADSHEET_EXTS)
SPREADSHEET_EXT_LABEL = ' / '.join(SPREADSHEET_EXTS)
SPREADSHEET_ERR_MSG = f'仅支持 {SPREADSHEET_EXT_LABEL} 格式文件'

_ZIP_MAGICS = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')
_OLE2_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


def get_ext(filename: str) -> str:
    return os.path.splitext(str(filename or '').strip())[1].lower()


def is_allowed_spreadsheet(filename: str) -> bool:
    return get_ext(filename) in SPREADSHEET_EXTS


def preserve_spreadsheet_ext(original_filename: str, cleaned_base: str) -> str:
    """secure_filename 清洗纯中文名后可能丢失扩展名，按原始允许扩展名强保后缀。"""
    ext = get_ext(original_filename)
    if ext not in SPREADSHEET_EXTS:
        ext = '.xlsx'
    base = (cleaned_base or '').strip() or 'upload'
    if not base.lower().endswith(ext):
        base = os.path.splitext(base)[0] + ext if get_ext(base) in SPREADSHEET_EXTS else (base + ext)
    return base


def validate_spreadsheet_magic(filename: str, data: bytes) -> Tuple[bool, str]:
    """按扩展名校验魔数/编码，防止改后缀伪造文件。"""
    ext = get_ext(filename)
    if ext not in SPREADSHEET_EXTS:
        return False, SPREADSHEET_ERR_MSG
    if not data:
        return False, '上传文件为空'
    if ext in ('.xlsx', '.xlsm', '.xlsb'):
        if len(data) < 4 or data[:4] not in _ZIP_MAGICS:
            return False, f'文件内容不是有效的 {ext}（zip 头校验失败）'
        return True, ''
    if ext == '.xls':
        head = data[:512].lstrip()
        if (len(data) >= 8 and data[:8] == _OLE2_MAGIC) or head.startswith(b'<?xml') or b'<Workbook' in head:
            return True, ''
        return False, '文件内容不是有效的 .xls 文件'
    # .csv
    sample = data[:65536]
    if b'\x00' in sample:
        return False, 'CSV 文件包含非法二进制字符'
    for enc in ('utf-8-sig', 'gb18030', 'gbk'):
        try:
            sample.decode(enc)
            return True, ''
        except UnicodeDecodeError:
            continue
    return False, 'CSV 编码无法识别（支持 UTF-8 / GB18030 / GBK）'


def _load_bytes_and_ext(filename: str, source) -> Tuple[bytes, str]:
    """支持传入路径 str/PathLike、bytes、或文件流（FileStorage / BytesIO / BufferedReader）。"""
    if isinstance(source, (str, os.PathLike)):
        path_str = str(source)
        ext = get_ext(filename) or get_ext(path_str)
        with open(path_str, 'rb') as fh:
            return fh.read(), ext
    if isinstance(source, (bytes, bytearray)):
        return bytes(source), get_ext(filename)
    # file-like object
    if hasattr(source, 'seek'):
        try:
            source.seek(0)
        except Exception:
            pass
    data = source.read()
    if isinstance(data, str):
        data = data.encode('utf-8')
    ext = get_ext(filename) or get_ext(getattr(source, 'filename', '') or getattr(source, 'name', ''))
    return data, ext


def _parse_spreadsheetml_xml(data: bytes) -> List[List[Any]]:
    ns = {'ss': 'urn:schemas-microsoft-com:office:spreadsheet'}
    root = ET.fromstring(data)
    out_rows = []
    for row_el in root.findall('.//ss:Worksheet[1]//ss:Table/ss:Row', ns):
        row_vals = []
        col_cursor = 1
        for cell_el in row_el.findall('ss:Cell', ns):
            idx_attr = cell_el.attrib.get('{urn:schemas-microsoft-com:office:spreadsheet}Index')
            if idx_attr:
                target_idx = int(idx_attr)
                while col_cursor < target_idx:
                    row_vals.append('')
                    col_cursor += 1
            data_el = cell_el.find('ss:Data', ns)
            row_vals.append('' if (data_el is None or data_el.text is None) else str(data_el.text))
            col_cursor += 1
        out_rows.append(row_vals)
    return out_rows


def _read_csv_matrix(data: bytes) -> List[List[Any]]:
    text = None
    for enc in ('utf-8-sig', 'gb18030', 'gbk'):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError('CSV 编码无法识别（支持 UTF-8 / GB18030 / GBK）')
    return [list(r) for r in csv.reader(io.StringIO(text))]


def _read_xls_matrix(data: bytes) -> List[List[Any]]:
    stripped = data.lstrip()
    if stripped.startswith(b'<?xml') or b'<Workbook' in stripped[:512]:
        return _parse_spreadsheetml_xml(data)
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError('服务器未安装 xlrd，无法解析 .xls；请执行 pip install xlrd 或另存为 .xlsx') from exc
    book = xlrd.open_workbook(file_contents=data)
    sheet = book.sheet_by_index(0)
    rows = []
    for rx in range(sheet.nrows):
        row_vals = []
        for cx in range(sheet.ncols):
            val = sheet.cell_value(rx, cx)
            if isinstance(val, float) and val.is_integer():
                row_vals.append(int(val))
            else:
                row_vals.append(val)
        rows.append(row_vals)
    return rows


def _read_xlsb_matrix(data: bytes) -> List[List[Any]]:
    try:
        import pyxlsb
    except ImportError as exc:
        raise RuntimeError('服务器未安装 pyxlsb，无法解析 .xlsb；请执行 pip install pyxlsb 或另存为 .xlsx') from exc
    rows = []
    with pyxlsb.open_workbook(io.BytesIO(data)) as wb:
        sheet_names = list(wb.sheets)
        if not sheet_names:
            return []
        with wb.get_sheet(sheet_names[0]) as sheet:
            for row in sheet.rows():
                rows.append([cell.v for cell in row])
    return rows


def _read_xlsx_matrix(data: bytes) -> List[List[Any]]:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.active
        return [list(row or ()) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def read_spreadsheet_matrix(filename: str, source) -> List[List[Any]]:
    """统一读取任意支持的表格文件为二维列表（保留原始行顺序与空行）。"""
    data, ext = _load_bytes_and_ext(filename, source)
    if not data:
        raise ValueError('上传文件为空')
    if ext not in SPREADSHEET_EXTS:
        # 按魔数兜底识别无后缀路径
        if len(data) >= 8 and data[:8] == _OLE2_MAGIC:
            ext = '.xls'
        elif len(data) >= 4 and data[:4] in _ZIP_MAGICS:
            ext = '.xlsx'
        else:
            raise ValueError(SPREADSHEET_ERR_MSG)

    if ext == '.csv':
        return _read_csv_matrix(data)
    if ext == '.xls':
        return _read_xls_matrix(data)
    if ext == '.xlsb':
        return _read_xlsb_matrix(data)
    # .xlsx / .xlsm
    return _read_xlsx_matrix(data)


def _non_empty_count(row: Sequence[Any]) -> int:
    return sum(1 for c in (row or ()) if c is not None and str(c).strip() != '')


def _detect_header_row_index(non_empty_rows: List[List[Any]]) -> int:
    """自动跳过顶部大标题行 / 填报说明元数据行（Patch C-3）。

    保守规则（零误伤普通 1 列 / 2 列极简表）：
    - 仅在前 6 行非空行内探测；
    - 若当前行非空单元格数 == 1，且后续 4 行内存在非空单元格数 >= 2 的行 → 判为大标题行跳过；
    - 若当前行非空单元格数 == 2，且后续 4 行内存在非空单元格数 >= 4 的行 → 判为顶部副标题/填报说明行跳过；
    - 否则当前行即为真实表头行。
    """
    limit = min(len(non_empty_rows), 6)
    for idx in range(limit):
        cur_cnt = _non_empty_count(non_empty_rows[idx])
        lookahead = non_empty_rows[idx + 1: min(len(non_empty_rows), idx + 5)]
        max_next = max((_non_empty_count(r) for r in lookahead), default=0)
        if cur_cnt == 1 and max_next >= 2:
            continue
        if cur_cnt == 2 and max_next >= 4:
            continue
        return idx
    return 0


def read_spreadsheet_table(filename: str, source) -> Tuple[List[str], List[List[Any]]]:
    """读取表头行与其余非空数据行（list[list]），自动跳过顶部合并大标题行并清理表头换行符。"""
    matrix = read_spreadsheet_matrix(filename, source)
    non_empty: List[List[Any]] = []
    for raw in matrix:
        if raw is None or all(c is None or str(c).strip() == '' for c in raw):
            continue
        non_empty.append(list(raw))
    if not non_empty:
        return [], []
    hdr_idx = _detect_header_row_index(non_empty)
    headers = [
        '' if c is None else str(c).replace('\r', '').replace('\n', '').strip()
        for c in non_empty[hdr_idx]
    ]
    rows = non_empty[hdr_idx + 1:]
    return headers, rows


def read_spreadsheet_dicts(filename: str, source) -> Tuple[List[str], List[dict]]:
    """读取首行为表头、其余非空行为 dict 列表（对齐 import_engine.read_xlsx 契约）。"""
    headers, rows = read_spreadsheet_table(filename, source)
    if not headers:
        return [], []
    dict_rows = []
    for raw in rows:
        dict_rows.append({
            headers[i]: (raw[i] if i < len(raw) else None)
            for i in range(len(headers))
        })
    return headers, dict_rows


def count_spreadsheet_rows(path: str, limit: int) -> Tuple[int, bool]:
    """统计数据行数（不含表头），达到 limit 即早停返回 (count, True)。

    语义对齐旧 count_rows_xlsx（034 行数护栏）：count >= limit 即判超限
    （调用处传 MAX_IMPORT_ROWS + 1 → 恰好 MAX_IMPORT_ROWS 行放行，边界行不漏拦）。
    """
    ext = get_ext(path)
    if ext in ('.xlsx', '.xlsm'):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            n = 0
            for idx, _row in enumerate(ws.iter_rows(values_only=True)):
                if idx == 0:
                    continue
                n += 1
                if n >= limit:
                    return n, True
            return n, False
        finally:
            wb.close()
    _headers, rows = read_spreadsheet_table(path, path)
    n = len(rows)
    return n, (n >= limit)
