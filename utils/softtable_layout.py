# -*- coding: utf-8 -*-
"""utils.softtable_layout — 软表引擎 V2 版式快照（LayoutSpec）存储与 1:1 原样 Excel 导出。

零数据库迁移：每张软表的 LayoutSpec 原子落盘在 DATA_DIR/softtable_layouts/<code>.json，
历史软表缺省自动生成标准版式，支持 version_ts 乐观并发锁，导出完整保留合并大标题、
多 Sheet、单页纵向多区块、左右并排双表、汇总公式、A4 纸张与跨页重复表头（print_title_rows）。
"""
import io
import json
import os
import re
from datetime import datetime, timedelta

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

_SAFE_CODE_RE = re.compile(r'^[a-z0-9_]{2,64}$')
# 134b：进程内 version_ts 单调钟（跨 SoftTableLayoutStore 实例共享，保乐观锁/克隆比对成立）
_LAST_VERSION_TS = None
_THIN = Side(border_style='thin', color='000000')
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEADER_FILL = PatternFill(start_color='F5F5F5', end_color='F5F5F5', fill_type='solid')

_FONT_CN_MAP = {
    "SimSun, '宋体', serif": '宋体',
    "FangSong, '仿宋', serif": '仿宋',
    "'Microsoft YaHei', sans-serif": '微软雅黑',
    "SimHei, '黑体', sans-serif": '黑体',
    '宋体': '宋体',
    '仿宋': '仿宋',
    '微软雅黑': '微软雅黑',
    '黑体': '黑体',
}


class LayoutConflictError(Exception):
    """乐观并发冲突：版式已被其他经办人更新。"""


class SoftTableLayoutStore:
    """零数据库迁移的 LayoutSpec 本地 JSON 存储。"""

    def __init__(self, base_dir):
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)

    @staticmethod
    def _validate_code(code):
        c = str(code or '').strip()
        if not _SAFE_CODE_RE.match(c):
            raise ValueError(f'非法的软表标识：{code}')
        return c

    def _path(self, code):
        return os.path.join(self.base_dir, f'{self._validate_code(code)}.json')

    def exists(self, code):
        try:
            return os.path.isfile(self._path(code))
        except ValueError:
            return False

    def get_layout(self, code, fallback_title='', col_count=1):
        path = self._path(code)
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as fh:
                return json.load(fh)
        n = max(1, int(col_count or 1))
        pct = round(100.0 / n, 2)
        pcts = [pct] * n
        pcts[-1] = round(100.0 - pct * (n - 1), 2)
        return {
            'version_ts': '',
            'layout_mode': 'standard',
            'workbook_group': fallback_title or code,
            'sheet_order': 0,
            'panel_role': None,
            'panel_title': None,
            'combine_on_single_page': False,
            'period_tag': '',
            'paper_orient': 'landscape' if n >= 8 else 'portrait',
            'paper_margin': 'normal',
            'print_repeat_header': True,
            'title_rows': [{'text': fallback_title or code, 'is_meta': False, 'font_px': 18, 'bold': True}],
            'footer_rows': [],
            'col_pcts': pcts,
            'row_heights': {},
            'cell_styles': {},
            'summary_rules': [],
            'merge_export_cols': [],
        }

    def save_layout(self, code, payload, expected_version_ts=None):
        path = self._path(code)
        if expected_version_ts is not None and os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as fh:
                current = json.load(fh)
            cur_ts = current.get('version_ts') or ''
            if cur_ts and expected_version_ts != cur_ts:
                raise LayoutConflictError('该台账版式已被他人修改，请刷新后重试')

        data = dict(payload or {})
        # 134b：version_ts 进程内严格单调——Windows 计时粒度粗，快速连写两次
        # datetime.now() 可落同一刻度（实测碰撞率 54%），乐观锁与克隆比对将静默失效。
        # 模块级 _LAST_VERSION_TS 兜底：新时刻不晚于上一发出时刻即步进 1 微秒，
        # 跨文件、跨实例也保序。
        global _LAST_VERSION_TS
        new_ts = datetime.now()
        if _LAST_VERSION_TS is not None and new_ts <= _LAST_VERSION_TS:
            new_ts = _LAST_VERSION_TS + timedelta(microseconds=1)
        _LAST_VERSION_TS = new_ts
        data['version_ts'] = new_ts.strftime('%Y-%m-%dT%H:%M:%S.%f')
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return data

    def clone_layout_for_period(self, src_code, dst_code, period_tag, new_title=None):
        src = self.get_layout(src_code)
        cloned = dict(src)
        cloned['period_tag'] = str(period_tag or '').strip()
        if new_title and cloned.get('title_rows'):
            trs = [dict(t) for t in cloned['title_rows']]
            trs[0]['text'] = new_title
            cloned['title_rows'] = trs
        return self.save_layout(dst_code, cloned)

    def delete_layout(self, code):
        path = self._path(code)
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False


def _setup_a4_sheet(ws, layout, total_cols, header_row_end=None):
    orient = (layout or {}).get('paper_orient') or 'landscape'
    margin_key = (layout or {}).get('paper_margin') or 'normal'
    m_inch = {'narrow': 0.39, 'normal': 0.59, 'wide': 0.79}.get(margin_key, 0.59)
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE if orient == 'landscape' else ws.ORIENTATION_PORTRAIT
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = m_inch
    ws.page_margins.right = m_inch
    ws.page_margins.top = m_inch
    ws.page_margins.bottom = m_inch
    if (layout or {}).get('print_repeat_header', True) and header_row_end:
        ws.print_title_rows = f'1:{header_row_end}'

    # 按 A4 可打印总字符宽换算列宽
    total_chars = 148.0 if orient == 'landscape' else 96.0
    pcts = (layout or {}).get('col_pcts') or []
    if len(pcts) != total_cols:
        pcts = [100.0 / max(1, total_cols)] * total_cols
    for idx, pct in enumerate(pcts, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = max(6.0, round(total_chars * (float(pct) / 100.0), 1))


def _apply_cell_style(cell, style_dict=None, is_header=False):
    st = style_dict or {}
    fname = _FONT_CN_MAP.get(st.get('font_family') or '', '宋体')
    fsize = max(8, min(24, round(float(st.get('font_px') or (12 if not is_header else 13)) * 0.78)))
    bold = bool(st.get('bold')) if 'bold' in st else is_header
    cell.font = Font(name=fname, size=fsize, bold=bold)
    h_align = st.get('align') or 'center'
    v_align = st.get('valign') or 'center'
    if v_align == 'middle':
        v_align = 'center'
    cell.alignment = Alignment(horizontal=h_align, vertical=v_align, wrap_text=True)
    cell.border = _BORDER
    if is_header:
        cell.fill = _HEADER_FILL


def _write_single_table_to_ws(ws, headers, rows, layout, start_row=1, col_offset=0):
    """将单张软表按 LayoutSpec 写入指定 Worksheet，返回结束行号。"""
    n_cols = max(1, len(headers))
    r_cur = start_row

    # 1. 顶部标题与公文元信息行
    for tr in (layout or {}).get('title_rows') or []:
        ws.merge_cells(start_row=r_cur, start_column=col_offset + 1, end_row=r_cur, end_column=col_offset + n_cols)
        c = ws.cell(r_cur, col_offset + 1, tr.get('text') or '')
        is_meta = bool(tr.get('is_meta'))
        c.font = Font(name='宋体', size=10 if is_meta else 16, bold=not is_meta)
        c.alignment = Alignment(horizontal='left' if is_meta else 'center', vertical='center', wrap_text=True)
        ws.row_dimensions[r_cur].height = 22 if is_meta else 30
        r_cur += 1

    # 2. 表头行
    hdr_row = r_cur
    styles = (layout or {}).get('cell_styles') or {}
    for ci, h in enumerate(headers):
        cell = ws.cell(hdr_row, col_offset + ci + 1, h)
        _apply_cell_style(cell, styles.get(f'h:{ci}'), is_header=True)
    ws.row_dimensions[hdr_row].height = 24
    r_cur += 1

    # 3. 数据行
    data_start = r_cur
    row_heights = (layout or {}).get('row_heights') or {}
    for ri, row_vals in enumerate(rows):
        for ci in range(n_cols):
            val = row_vals[ci] if ci < len(row_vals) else ''
            cell = ws.cell(r_cur, col_offset + ci + 1, val)
            _apply_cell_style(cell, styles.get(f'{ri}:{ci}'), is_header=False)
        rh_px = row_heights.get(str(ri)) or row_heights.get(ri)
        ws.row_dimensions[r_cur].height = round(float(rh_px) * 0.75, 1) if rh_px else 22
        r_cur += 1
    data_end = r_cur - 1

    # 4. 可选：按相邻同值纵向还原合并格
    merge_cols = (layout or {}).get('merge_export_cols') or []
    if data_end > data_start and merge_cols:
        for col_code in merge_cols:
            m = re.match(r'^f(\d+)$', str(col_code))
            if not m:
                continue
            ci = int(m.group(1))
            if ci >= n_cols:
                continue
            run_start = data_start
            prev_val = ws.cell(data_start, col_offset + ci + 1).value
            for rr in range(data_start + 1, data_end + 2):
                cur_val = ws.cell(rr, col_offset + ci + 1).value if rr <= data_end else None
                if cur_val != prev_val or rr > data_end:
                    if prev_val not in (None, '') and (rr - 1) > run_start:
                        ws.merge_cells(start_row=run_start, start_column=col_offset + ci + 1,
                                       end_row=rr - 1, end_column=col_offset + ci + 1)
                    run_start = rr
                    prev_val = cur_val

    # 5. 声明式表尾汇总行（写入真实 Excel 公式 =SUM / =COUNTA / =AVERAGE）
    summary_rules = (layout or {}).get('summary_rules') or []
    if summary_rules and data_end >= data_start:
        for ci in range(n_cols):
            cell = ws.cell(r_cur, col_offset + ci + 1, '')
            _apply_cell_style(cell, {'bold': True}, is_header=True)
        ws.cell(r_cur, col_offset + 1, summary_rules[0].get('label') or '合计')
        for rule in summary_rules:
            ci = rule.get('col_idx')
            if ci is None:
                m = re.match(r'^f(\d+)$', str(rule.get('field_code') or ''))
                ci = int(m.group(1)) if m else None
            if ci is None or ci >= n_cols:
                continue
            col_l = get_column_letter(col_offset + ci + 1)
            op = (rule.get('op') or 'sum').lower()
            fn = 'COUNTA' if op == 'count' else ('AVERAGE' if op == 'avg' else 'SUM')
            ws.cell(r_cur, col_offset + ci + 1, f'={fn}({col_l}{data_start}:{col_l}{data_end})')
        ws.row_dimensions[r_cur].height = 24
        r_cur += 1

    # 6. 表尾备注/落款行
    for fr in (layout or {}).get('footer_rows') or []:
        ws.merge_cells(start_row=r_cur, start_column=col_offset + 1, end_row=r_cur, end_column=col_offset + n_cols)
        c = ws.cell(r_cur, col_offset + 1, fr.get('text') or '')
        c.font = Font(name='宋体', size=10)
        c.alignment = Alignment(horizontal=fr.get('align') or 'left', vertical='center')
        ws.row_dimensions[r_cur].height = 20
        r_cur += 1

    return hdr_row, r_cur - 1


def export_styled_table_xlsx(headers, rows, layout, table_name='台账导出'):
    """按 LayoutSpec 导出单张 1:1 生产格式 .xlsx 字节流。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = re.sub(r'[\\/:*?\[\]]', '_', (table_name or 'Sheet1'))[:31]
    hdr_row, _end = _write_single_table_to_ws(ws, headers, rows, layout)
    _setup_a4_sheet(ws, layout, len(headers), header_row_end=hdr_row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_workbook_group_xlsx(bundles, group_name='台账工作簿'):
    """导出同工作簿组（bundles = [(name, headers, rows, layout), ...]）：
    自动支持多 Sheet、单页纵向多区块、单页横向并排双表三种拼装模式。
    """
    wb = openpyxl.Workbook()
    if not bundles:
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    ordered = sorted(bundles, key=lambda b: (b[3] or {}).get('sheet_order', 0))
    first_layout = ordered[0][3] or {}
    mode = first_layout.get('layout_mode') or 'standard'

    # 模式 A：单页横向并排双表（如高龄津贴增减变动表）
    if mode == 'side_by_side' and len(ordered) >= 2:
        ws = wb.active
        ws.title = re.sub(r'[\\/:*?\[\]]', '_', group_name)[:31]
        left_name, l_hdrs, l_rows, l_lay = ordered[0]
        right_name, r_hdrs, r_rows, r_lay = ordered[1]
        total_cols = len(l_hdrs) + len(r_hdrs)
        r_cur = 1

        for tr in l_lay.get('title_rows') or []:
            ws.merge_cells(start_row=r_cur, start_column=1, end_row=r_cur, end_column=total_cols)
            c = ws.cell(r_cur, 1, tr.get('text') or '')
            is_meta = bool(tr.get('is_meta'))
            c.font = Font(name='宋体', size=10 if is_meta else 16, bold=not is_meta)
            c.alignment = Alignment(horizontal='left' if is_meta else 'center', vertical='center')
            ws.row_dimensions[r_cur].height = 22 if is_meta else 30
            r_cur += 1

        # 分组大表头行
        ws.merge_cells(start_row=r_cur, start_column=1, end_row=r_cur, end_column=len(l_hdrs))
        cL = ws.cell(r_cur, 1, l_lay.get('panel_title') or left_name)
        _apply_cell_style(cL, {'font_px': 14, 'bold': True}, is_header=True)
        ws.merge_cells(start_row=r_cur, start_column=len(l_hdrs) + 1, end_row=r_cur, end_column=total_cols)
        cR = ws.cell(r_cur, len(l_hdrs) + 1, r_lay.get('panel_title') or right_name)
        _apply_cell_style(cR, {'font_px': 14, 'bold': True}, is_header=True)
        for ci in range(1, total_cols + 1):
            ws.cell(r_cur, ci).border = _BORDER
        ws.row_dimensions[r_cur].height = 24
        r_cur += 1

        # 子表头行
        hdr_row = r_cur
        for ci, h in enumerate(l_hdrs + r_hdrs, start=1):
            _apply_cell_style(ws.cell(hdr_row, ci, h), is_header=True)
        ws.row_dimensions[hdr_row].height = 24
        r_cur += 1

        # 左右独立数据行并排写入
        max_len = max(len(l_rows), len(r_rows), 1)
        for ri in range(max_len):
            lv = l_rows[ri] if ri < len(l_rows) else [''] * len(l_hdrs)
            rv = r_rows[ri] if ri < len(r_rows) else [''] * len(r_hdrs)
            for ci, val in enumerate(list(lv) + list(rv), start=1):
                _apply_cell_style(ws.cell(r_cur, ci, val), is_header=False)
            ws.row_dimensions[r_cur].height = 22
            r_cur += 1

        comb_pcts = [round(p * len(l_hdrs) / total_cols, 2) for p in (l_lay.get('col_pcts') or [100.0 / len(l_hdrs)] * len(l_hdrs))] + \
                    [round(p * len(r_hdrs) / total_cols, 2) for p in (r_lay.get('col_pcts') or [100.0 / len(r_hdrs)] * len(r_hdrs))]
        comb_layout = dict(l_lay, col_pcts=comb_pcts, paper_orient='landscape')
        _setup_a4_sheet(ws, comb_layout, total_cols, header_row_end=hdr_row)

    # 模式 B：单页纵向多区块（如 1.1 人员名单）
    elif mode == 'vertical_sections' and first_layout.get('combine_on_single_page'):
        ws = wb.active
        ws.title = re.sub(r'[\\/:*?\[\]]', '_', group_name)[:31]
        r_cur = 1
        max_cols = max(len(b[1]) for b in ordered)
        for name, hdrs, rows, lay in ordered:
            _hdr, end_r = _write_single_table_to_ws(ws, hdrs, rows, lay, start_row=r_cur)
            r_cur = end_r + 2
        _setup_a4_sheet(ws, first_layout, max_cols)

    # 模式 C：多 Sheet 工作簿（如 1、社区三委、小组长、居民代表成员花名册.xlsx）
    else:
        for idx, (name, hdrs, rows, lay) in enumerate(ordered):
            ws = wb.active if idx == 0 else wb.create_sheet()
            ws.title = re.sub(r'[\\/:*?\[\]]', '_', name or f'Sheet{idx + 1}')[:31]
            hdr_row, _end = _write_single_table_to_ws(ws, hdrs, rows, lay)
            _setup_a4_sheet(ws, lay, len(hdrs), header_row_end=hdr_row)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def is_masked_placeholder(value) -> bool:
    """检测输入是否为脱敏占位符（防止 A4 行内编辑误将 **** 覆写进加密字段）。"""
    s = str(value or '').strip()
    return bool(s and ('***' in s or '****' in s))


def map_resident_to_softtable_row(fields, resident_info, next_seq=1) -> dict:
    """将居民主档字典按列名语义自动映射到软表字段 code -> value。"""
    info = resident_info or {}
    row = {}
    for f in fields or []:
        code = f.get('code')
        clean = re.sub(r'[\s\u3000*＊:：（）()]', '', str(f.get('display_name') or ''))
        if not code:
            continue
        if clean == '序号':
            row[code] = str(next_seq)
        elif '姓名' in clean:
            row[code] = info.get('name') or ''
        elif '性别' in clean:
            row[code] = info.get('gender') or ''
        elif '年龄' in clean:
            row[code] = info.get('age') or ''
        elif '出生' in clean:
            row[code] = info.get('birth_date') or ''
        elif '身份证' in clean or '证件号' in clean:
            row[code] = info.get('id_card') or ''
        elif '电话' in clean or '手机' in clean:
            row[code] = info.get('phone') or ''
        elif '户籍' in clean:
            row[code] = info.get('household_address') or info.get('address') or ''
        elif '地址' in clean:
            row[code] = info.get('address') or ''
        elif '小组' in clean or '组别' in clean:
            row[code] = info.get('village_group') or ''
        elif '民族' in clean:
            row[code] = info.get('nation') or ''
        elif '文化' in clean or '学历' in clean:
            row[code] = info.get('education') or ''
        elif '政治' in clean:
            row[code] = info.get('politics_status') or ''
        elif '入党' in clean:
            row[code] = info.get('join_date') or ''
        elif '银行' in clean or '卡号' in clean or '账号' in clean:
            row[code] = info.get('bank_card') or ''
    return row
