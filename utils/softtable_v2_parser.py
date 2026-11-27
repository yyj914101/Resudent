# -*- coding: utf-8 -*-
"""utils.softtable_v2_parser — 软表引擎 V2 结构感知解析器（零新增第三方依赖）。

通吃四类基层生产 Excel 形态：
  1. standard          — 规整二维表
  2. standard_titled   — 带顶部合并大标题/副标题的多 Sheet 花名册
  3. vertical_sections — 单页纵向多区块异构小表（如 1.1 人员与分工名单）
  4. side_by_side      — 双层表头 + 左右并排独立双表（如 高龄津贴增减变动表）

并内建六项生产短板兜底：
  - 幽灵空列/空行裁剪（防止 ws.max_column=16384 超限）
  - 数据区纵向合并格自动向下填充（ffill）并记录可还原合并列
  - 表尾汇总行/公式（SUM/COUNT/AVERAGE）转声明式 summary_rules + 复杂公式静态值提示
  - 2–3 层复合表头扁平拼接 + 人工指定表头起止行（header_overrides）+ 前 10 行原始预览
  - 单元格批注（comment）与删除线（strike）自动提取并入「备注」列
  - 空表样（0 数据行）按列名关键词自动推断数据类型与敏感分级
"""
import io
import os
import re
from datetime import date, datetime

import openpyxl
from openpyxl.utils import get_column_letter


SENSITIVE_KEYWORDS = ('身份证', '证件号', '电话', '手机', '联系方式', '银行', '卡号', '账号', '社保卡')
DATE_KEYWORDS = ('日期', '时间', '出生', '入党', '死亡', '年月')
NUMBER_KEYWORDS = ('序号', '年龄', '金额', '人数', '标准', '收入', '补贴', '人口')
SUMMARY_ROW_HINTS = ('合计', '总计', '小计', '汇总', '共计')
FORMULA_SUMMARY_RE = re.compile(r'^=\s*(SUM|COUNTA?|AVERAGE)\s*\(', re.I)

_MAX_SCAN_COLS = 64
_MAX_SCAN_ROWS = 200000


def _clean_str(val):
    if val is None:
        return ''
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d')
    if isinstance(val, date):
        return val.strftime('%Y-%m-%d')
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()


def _norm_key(text):
    return re.sub(r'[\s\u3000*＊:：（）()]', '', str(text or ''))


_STRICT_DATE_FORMATS = ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y%m%d', '%Y年%m月%d日')
_TEXT_OVERRIDE_KEYWORDS = ('月份', '期号', '类型', '类别', '名称', '原因', '备注', '说明', '标准', '级别', '等级', '状态', '编号', '编码', '证号', '代码', '邮编')


def _is_strict_number(v: str) -> bool:
    s = v.replace(',', '').strip()
    if not s:
        return False
    # 前导零编号（如 0012）保真为文本，避免丢前导零
    if re.match(r'^0\d+$', s):
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


def _is_strict_date(v: str) -> bool:
    s = v.strip()
    for fmt in _STRICT_DATE_FORMATS:
        try:
            datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _infer_field_from_header(header_name, sample_vals=None):
    """样本实测优先 + fail-safe to text：有数据时必须全量非空样本 100% 通过强转才判 number/date。"""
    raw = str(header_name or '').strip()
    clean = _norm_key(raw)
    sensitivity = 'identity' if any(k in clean for k in SENSITIVE_KEYWORDS) else 'none'
    data_type = 'text'
    if sensitivity == 'none':
        non_empty = [str(v).strip() for v in (sample_vals or []) if str(v or '').strip()]
        is_text_named = any(k in clean for k in _TEXT_OVERRIDE_KEYWORDS)
        is_date_named = any(k in clean for k in DATE_KEYWORDS)
        if non_empty:
            # 有真实样本：全量样本 100% 校验，任意 1 行不匹配即保真降级为 text；
            # 日期/时间类列名绝不允许被误判为 number（防止 1978.07 被 float() 当成小数）。
            if not is_text_named and not is_date_named and all(_is_strict_number(v) for v in non_empty):
                data_type = 'number'
            elif not is_text_named and all(_is_strict_date(v) for v in non_empty):
                data_type = 'date'
            else:
                data_type = 'text'
        else:
            # 0 行空表样：按列名关键词兜底，显式排除文本后缀列
            if is_text_named:
                data_type = 'text'
            elif any(k in clean for k in DATE_KEYWORDS):
                data_type = 'date'
            elif any(k in clean for k in NUMBER_KEYWORDS):
                data_type = 'number'
    return {
        'display_name': raw or '未命名列',
        'clean_name': clean,
        'data_type': data_type,
        'sensitivity': sensitivity,
        'is_required': False,
    }


def _normalize_col_pcts(raw_widths):
    if not raw_widths:
        return []
    vals = [max(4.0, float(w or 12.0)) for w in raw_widths]
    total = sum(vals) or 1.0
    pcts = [max(3.0, round(v / total * 100.0, 2)) for v in vals]
    diff = round(100.0 - sum(pcts), 2)
    pcts[-1] = max(3.0, round(pcts[-1] + diff, 2))
    return pcts


def _is_summary_row(row_vals, formula_row=None):
    non_empty = [v for v in row_vals if v != '']
    if not non_empty:
        return False
    first_txt = _norm_key(non_empty[0])
    if any(first_txt.startswith(h) for h in SUMMARY_ROW_HINTS):
        return True
    if formula_row:
        if any(isinstance(f, str) and FORMULA_SUMMARY_RE.match(f) for f in formula_row):
            return True
    return False


def _extract_summary_rules(row_vals, formula_row, col_offset=0):
    rules = []
    if not formula_row:
        return rules
    for ci, fval in enumerate(formula_row):
        if isinstance(fval, str):
            m = FORMULA_SUMMARY_RE.match(fval)
            if m:
                op_raw = m.group(1).upper()
                op = 'count' if op_raw.startswith('COUNT') else ('avg' if op_raw == 'AVERAGE' else 'sum')
                rules.append({
                    'col_idx': ci,
                    'field_code': f'f{ci:02d}',
                    'op': op,
                    'label': '合计' if op == 'sum' else ('计数' if op == 'count' else '均值'),
                })
    return rules


def _inject_semantic_notes(fields, rows, row_notes):
    """将批注与删除线语义并入「备注」列（无备注列则自动追加「原表批注备注」列）。"""
    if not any(row_notes):
        return fields, rows
    remark_idx = None
    for idx, f in enumerate(fields):
        if '备注' in f['clean_name'] or '说明' in f['clean_name']:
            remark_idx = idx
            break
    if remark_idx is None:
        fields = list(fields) + [{
            'display_name': '原表批注备注',
            'clean_name': '原表批注备注',
            'data_type': 'text',
            'sensitivity': 'none',
            'is_required': False,
        }]
        remark_idx = len(fields) - 1
        rows = [list(r) + [''] for r in rows]

    out_rows = []
    for r, notes in zip(rows, row_notes):
        r2 = list(r)
        if notes:
            joined = '；'.join(notes)
            r2[remark_idx] = (r2[remark_idx] + '；' + joined) if r2[remark_idx] else joined
        out_rows.append(r2)
    return fields, out_rows


def _make_base_layout(mode, workbook_group, sheet_order, title_rows, col_pcts,
                      paper_orient='landscape', panel_role=None, panel_title=None,
                      summary_rules=None, merge_export_cols=None, footer_rows=None,
                      combine_on_single_page=False):
    return {
        'layout_mode': mode,
        'workbook_group': workbook_group,
        'sheet_order': sheet_order,
        'panel_role': panel_role,
        'panel_title': panel_title,
        'combine_on_single_page': combine_on_single_page,
        'period_tag': '',
        'paper_orient': paper_orient,
        'paper_margin': 'normal',
        'print_repeat_header': True,
        'title_rows': title_rows or [],
        'footer_rows': footer_rows or [],
        'col_pcts': col_pcts or [],
        'row_heights': {},
        'cell_styles': {},
        'summary_rules': summary_rules or [],
        'merge_export_cols': merge_export_cols or [],
    }


def _parse_openpyxl_sheet(ws_val, ws_f, sname, workbook_group, sheet_order, override=None):
    """结构感知解析单个 openpyxl Worksheet（双视图：值 + 公式/批注/删除线/合并格）。"""
    warnings = []
    merges = [
        (rng.min_row, rng.min_col, rng.max_row, rng.max_col)
        for rng in ws_val.merged_cells.ranges
    ]

    scan_c = min(ws_val.max_column or 1, _MAX_SCAN_COLS)
    scan_r = min(ws_val.max_row or 1, _MAX_SCAN_ROWS)

    max_r, max_c = 0, 0
    grid = []
    f_grid = []
    notes_grid = []

    for r in range(1, scan_r + 1):
        row_v, row_f, row_n = [], [], []
        last_c = 0
        for c in range(1, scan_c + 1):
            cell_v = ws_val.cell(r, c)
            cell_f = ws_f.cell(r, c)
            txt = _clean_str(cell_v.value)
            fval = cell_f.value
            if isinstance(fval, str) and fval.startswith('='):
                if not txt:
                    txt = fval
            if txt != '':
                last_c = c
            # 收集批注与删除线语义
            c_notes = []
            if cell_f.comment and cell_f.comment.text:
                ct = cell_f.comment.text.strip().replace('\n', ' ')
                if ct:
                    c_notes.append(f'批注:{ct}')
            if cell_f.font and cell_f.font.strike and txt:
                c_notes.append(f'原表删除线({txt})')
            row_v.append(txt)
            row_f.append(fval)
            row_n.append(c_notes)
        if last_c > 0:
            max_r = max(max_r, r)
            max_c = max(max_c, last_c)
        grid.append(row_v)
        f_grid.append(row_f)
        notes_grid.append(row_n)

    if max_r == 0 or max_c == 0:
        return {
            'sheet_name': sname,
            'mode': 'empty',
            'mode_label': '空白工作表',
            'ghost_cols_trimmed': max(0, (ws_val.max_column or 0) - max_c),
            'preview_rows': [],
            'warnings': ['该工作表为空'],
            'tables': [],
        }

    valid_merges = [m for m in merges if m[1] <= max_c and m[0] <= max_r]
    for m in valid_merges:
        if m[3] <= scan_c:
            max_c = max(max_c, m[3])

    grid = [r[:max_c] for r in grid[:max_r]]
    f_grid = [r[:max_c] for r in f_grid[:max_r]]
    notes_grid = [r[:max_c] for r in notes_grid[:max_r]]
    ghost_trimmed = max(0, (ws_val.max_column or max_c) - max_c)
    if ghost_trimmed > 0:
        warnings.append(f'已自动裁剪 {ghost_trimmed} 个尾部幽灵空列')

    preview_rows = [list(r) for r in grid[:10]]

    # 提取列宽
    col_widths = []
    for c in range(1, max_c + 1):
        dim = ws_val.column_dimensions.get(get_column_letter(c))
        col_widths.append(dim.width if (dim and dim.width) else 12.0)

    def full_row_merge(r1):
        for m in valid_merges:
            if m[0] == r1 and m[2] == r1 and m[1] == 1 and m[3] >= max_c - 1:
                return True
        return False

    def row_h_merges(r1):
        ms = [m for m in valid_merges if m[0] == r1 and m[2] == r1 and (m[3] - m[1]) >= 1]
        ms.sort(key=lambda x: x[1])
        return ms

    # 数据区纵向合并格向下填充（ffill），并记录合并列
    def apply_vertical_ffill(start_r1, end_r1, c_start1, c_end1):
        merged_cols = set()
        for (mr1, mc1, mr2, mc2) in valid_merges:
            if mr2 > mr1 and mr1 >= start_r1 and mr1 <= end_r1 and mc1 >= c_start1 and mc1 <= c_end1:
                top_val = grid[mr1 - 1][mc1 - 1]
                if top_val != '':
                    merged_cols.add(mc1 - c_start1)
                    for rr in range(mr1 + 1, min(mr2, end_r1) + 1):
                        if grid[rr - 1][mc1 - 1] == '':
                            grid[rr - 1][mc1 - 1] = top_val
        return sorted(merged_cols)

    # 人工指定表头起止行兜底（解决 3 层表头 / 斜线表头）
    if override and override.get('header_start'):
        h_start = max(1, min(max_r, int(override['header_start'])))
        h_end = max(h_start, min(max_r, int(override.get('header_end') or h_start)))
        title_rows = [
            {'row': r, 'text': ' '.join(v for v in grid[r - 1] if v), 'is_meta': ('：' in grid[r - 1][0] or ':' in grid[r - 1][0])}
            for r in range(1, h_start) if any(grid[r - 1])
        ]
        # 扁平拼接多行表头
        flat_hdrs = []
        for c in range(max_c):
            parts = []
            for hr in range(h_start, h_end + 1):
                val = grid[hr - 1][c]
                if not val:
                    # 向左找横向合并父级表头
                    for (mr1, mc1, mr2, mc2) in valid_merges:
                        if mr1 <= hr <= mr2 and mc1 <= (c + 1) <= mc2:
                            val = grid[mr1 - 1][mc1 - 1]
                            break
                if val and (not parts or parts[-1] != val):
                    parts.append(val)
            flat_hdrs.append('-'.join(parts) if parts else f'列{c + 1}')

        m_cols = apply_vertical_ffill(h_end + 1, max_r, 1, max_c)
        data_rows, row_notes, summary_rules = [], [], []
        for dr in range(h_end, max_r):
            rv = grid[dr][:max_c]
            fv = f_grid[dr][:max_c]
            if not any(rv):
                continue
            if _is_summary_row(rv, fv):
                summary_rules.extend(_extract_summary_rules(rv, fv))
                continue
            data_rows.append(rv)
            rn = [n for cell_ns in notes_grid[dr][:max_c] for n in cell_ns]
            row_notes.append(rn)
        fields = [
            _infer_field_from_header(h, [r[i] for r in data_rows])
            for i, h in enumerate(flat_hdrs)
        ]
        fields, data_rows = _inject_semantic_notes(fields, data_rows, row_notes)
        col_pcts = _normalize_col_pcts(col_widths[:len(fields)])
        orient = 'landscape' if len(fields) >= 8 else 'portrait'
        layout = _make_base_layout(
            'standard_titled' if title_rows else 'standard',
            workbook_group, sheet_order, title_rows, col_pcts,
            paper_orient=orient, summary_rules=summary_rules,
            merge_export_cols=[f'f{c:02d}' for c in m_cols],
        )
        return {
            'sheet_name': sname,
            'mode': 'manual_header',
            'mode_label': f'人工指定表头（R{h_start}–R{h_end}）',
            'ghost_cols_trimmed': ghost_trimmed,
            'preview_rows': preview_rows,
            'warnings': warnings,
            'tables': [{
                'suggested_name': sname,
                'fields': fields,
                'rows': data_rows,
                'row_count': len(data_rows),
                'layout': layout,
            }],
        }

    # 收集顶部全行合并标题/公文元信息行
    title_rows = []
    r_ptr = 1
    while r_ptr <= max_r and full_row_merge(r_ptr):
        txt = grid[r_ptr - 1][0]
        title_rows.append({
            'row': r_ptr,
            'text': txt,
            'is_meta': ('：' in txt or ':' in txt or '报送时间' in txt),
            'font_px': 12 if ('：' in txt or ':' in txt) else 20,
            'bold': not ('：' in txt or ':' in txt),
        })
        r_ptr += 1

    # 标题与表头之间常夹有空行（最常见的排版）：跳过之，否则下方 4 个形态分支会把空行
    # 当表头 —— 标准表 eff_cols 缩到 1（整表只剩 1 列、真表头被当成首条数据），
    # 并排双表/多区块判定同样错位，且静默无警告。
    while r_ptr <= max_r and not any(grid[r_ptr - 1][:max_c]):
        r_ptr += 1

    # 形态 4：横向并排双表 + 双层表头（如高龄津贴增减变动表）
    h_ms = row_h_merges(r_ptr) if r_ptr <= max_r else []
    if len(h_ms) >= 2 and (r_ptr + 1) <= max_r:
        hdr_row = grid[r_ptr]  # 1-based r_ptr + 1
        tables = []
        for pi, (mr1, mc1, mr2, mc2) in enumerate(h_ms):
            raw_pname = grid[mr1 - 1][mc1 - 1]
            clean_pname = _norm_key(raw_pname) or f'分栏{pi + 1}'
            m_cols = apply_vertical_ffill(r_ptr + 2, max_r, mc1, mc2)
            p_rows, p_notes, p_sum = [], [], []
            for dr in range(r_ptr + 1, max_r):
                rv = [grid[dr][c - 1] for c in range(mc1, mc2 + 1)]
                fv = [f_grid[dr][c - 1] for c in range(mc1, mc2 + 1)]
                if not any(rv):
                    continue
                if _is_summary_row(rv, fv):
                    p_sum.extend(_extract_summary_rules(rv, fv))
                    continue
                p_rows.append(rv)
                rn = [n for c in range(mc1, mc2 + 1) for n in notes_grid[dr][c - 1]]
                p_notes.append(rn)
            p_fields = [
                _infer_field_from_header(hdr_row[c - 1], [r[idx] for r in p_rows])
                for idx, c in enumerate(range(mc1, mc2 + 1))
            ]
            p_fields, p_rows = _inject_semantic_notes(p_fields, p_rows, p_notes)
            p_pcts = _normalize_col_pcts(col_widths[mc1 - 1:mc1 - 1 + len(p_fields)])
            layout = _make_base_layout(
                'side_by_side', workbook_group, sheet_order * 10 + pi,
                title_rows if pi == 0 else [], p_pcts,
                paper_orient='landscape',
                panel_role='left' if pi == 0 else 'right',
                panel_title=raw_pname,
                summary_rules=p_sum,
                merge_export_cols=[f'f{c:02d}' for c in m_cols],
                combine_on_single_page=True,
            )
            tables.append({
                'suggested_name': f'{sname}-{clean_pname}',
                'fields': p_fields,
                'rows': p_rows,
                'sample_rows': p_rows[:3],
                'row_count': len(p_rows),
                'layout': layout,
            })
        return {
            'sheet_name': sname,
            'mode': 'side_by_side',
            'mode_label': f'横向并排双表 + 双层表头（{len(tables)} 栏）',
            'ghost_cols_trimmed': ghost_trimmed,
            'preview_rows': preview_rows,
            'warnings': warnings,
            'tables': tables,
        }

    # 形态 3：单页纵向多区块异构小表（如 1.1 人员名单）
    sec_starts = []
    if title_rows:
        sec_starts.append((title_rows[-1]['row'], title_rows[-1]['text']))
    for rr in range(r_ptr, max_r + 1):
        if full_row_merge(rr):
            sec_starts.append((rr, grid[rr - 1][0]))

    if len(sec_starts) >= 2:
        tables = []
        for si, (sr, stitle) in enumerate(sec_starts):
            end_r = sec_starts[si + 1][0] - 1 if si + 1 < len(sec_starts) else max_r
            if sr + 1 > end_r:
                continue
            # 区块标题与表头之间也可能夹空行：表头 = 标题之后第一个非空行，数据自表头下一行起
            hdr_r = sr + 1
            while hdr_r <= end_r and not any(grid[hdr_r - 1][:max_c]):
                hdr_r += 1
            if hdr_r > end_r:
                continue
            raw_hdr = grid[hdr_r - 1]
            eff_cols = len(raw_hdr)
            while eff_cols > 1 and raw_hdr[eff_cols - 1] == '':
                eff_cols -= 1
            m_cols = apply_vertical_ffill(hdr_r + 1, end_r, 1, eff_cols)
            s_rows, s_notes, s_sum = [], [], []
            for dr in range(hdr_r, end_r):
                rv = grid[dr][:eff_cols]
                fv = f_grid[dr][:eff_cols]
                if not any(rv):
                    continue
                if _is_summary_row(rv, fv):
                    s_sum.extend(_extract_summary_rules(rv, fv))
                    continue
                s_rows.append(rv)
                rn = [n for cell_ns in notes_grid[dr][:eff_cols] for n in cell_ns]
                s_notes.append(rn)
            s_fields = [
                _infer_field_from_header(raw_hdr[i], [r[i] for r in s_rows])
                for i in range(eff_cols)
            ]
            s_fields, s_rows = _inject_semantic_notes(s_fields, s_rows, s_notes)
            s_pcts = _normalize_col_pcts(col_widths[:len(s_fields)])
            layout = _make_base_layout(
                'vertical_sections', workbook_group, sheet_order * 10 + si,
                [{'row': sr, 'text': stitle, 'is_meta': False, 'font_px': 16, 'bold': True}],
                s_pcts, paper_orient='portrait',
                summary_rules=s_sum,
                merge_export_cols=[f'f{c:02d}' for c in m_cols],
                combine_on_single_page=True,
            )
            tables.append({
                'suggested_name': stitle[:40] or f'{sname}-区块{si + 1}',
                'fields': s_fields,
                'rows': s_rows,
                'sample_rows': s_rows[:3],
                'row_count': len(s_rows),
                'layout': layout,
            })
        return {
            'sheet_name': sname,
            'mode': 'vertical_sections',
            'mode_label': f'单页纵向多区块（{len(tables)} 段异构小表）',
            'ghost_cols_trimmed': ghost_trimmed,
            'preview_rows': preview_rows,
            'warnings': warnings,
            'tables': tables,
        }

    # 形态 1 / 2：标准二维表或带合并大标题单表
    hdr_r = min(r_ptr, max_r)
    raw_hdr = grid[hdr_r - 1]
    eff_cols = len(raw_hdr)
    while eff_cols > 1 and raw_hdr[eff_cols - 1] == '':
        eff_cols -= 1
    m_cols = apply_vertical_ffill(hdr_r + 1, max_r, 1, eff_cols)
    if m_cols:
        warnings.append(f'检测到 {len(m_cols)} 列存在纵向合并单元格，已自动向下填充补齐每行数据')

    data_rows, row_notes, summary_rules, inline_formula_cnt = [], [], [], 0
    for dr in range(hdr_r, max_r):
        rv = grid[dr][:eff_cols]
        fv = f_grid[dr][:eff_cols]
        if not any(rv):
            continue
        if _is_summary_row(rv, fv):
            summary_rules.extend(_extract_summary_rules(rv, fv))
            continue
        if any(isinstance(f, str) and f.startswith('=') for f in fv):
            inline_formula_cnt += 1
        data_rows.append(rv)
        rn = [n for cell_ns in notes_grid[dr][:eff_cols] for n in cell_ns]
        row_notes.append(rn)

    if inline_formula_cnt:
        warnings.append(f'有 {inline_formula_cnt} 行包含行内 Excel 公式，已保留计算快照值')
    if any(row_notes):
        warnings.append('检测到原表批注或删除线，已自动提取并入备注列')

    fields = [
        _infer_field_from_header(raw_hdr[i], [r[i] for r in data_rows])
        for i in range(eff_cols)
    ]
    fields, data_rows = _inject_semantic_notes(fields, data_rows, row_notes)
    col_pcts = _normalize_col_pcts(col_widths[:len(fields)])
    orient = 'landscape' if len(fields) >= 8 else 'portrait'
    mode = 'standard_titled' if title_rows else 'standard'
    layout = _make_base_layout(
        mode, workbook_group, sheet_order, title_rows, col_pcts,
        paper_orient=orient, summary_rules=summary_rules,
        merge_export_cols=[f'f{c:02d}' for c in m_cols],
    )
    return {
        'sheet_name': sname,
        'mode': mode,
        'mode_label': '带合并大标题标准表' if title_rows else '标准二维表',
        'ghost_cols_trimmed': ghost_trimmed,
        'preview_rows': preview_rows,
        'warnings': warnings,
        'tables': [{
            'suggested_name': sname,
            'fields': fields,
            'rows': data_rows,
            'sample_rows': data_rows[:3],
            'row_count': len(data_rows),
            'layout': layout,
        }],
    }


def parse_workbook_v2(filename, stream, header_overrides=None):
    """统一入口：解析任意 .xlsx/.xlsm/.xls/.xlsb/.csv，返回完整 V2 工作簿结构树。"""
    ext = os.path.splitext(str(filename or '').strip())[1].lower()
    raw_bytes = stream.read() if hasattr(stream, 'read') else bytes(stream)
    raw_stem = os.path.splitext(os.path.basename(filename or '未命名台账'))[0].strip()
    # 清洗文件名中残留的扩展名与常见副本标记（如 （电子档）(1).xlsx最新）
    cleaned_stem = re.sub(r'\.(xlsx|xls|xlsm|xlsb|csv)', '', raw_stem, flags=re.I)
    cleaned_stem = re.sub(r'[（(]\s*(电子档|副本|复制|最新|\d+)\s*[）)]', '', cleaned_stem)
    cleaned_stem = re.sub(r'(最新|副本)+$', '', cleaned_stem).strip(' _-·')
    workbook_group = cleaned_stem or raw_stem or '未命名台账'
    overrides = header_overrides or {}

    if ext in ('.xlsx', '.xlsm'):
        wb_val = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        wb_f = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=False)
        sheets = []
        for idx, sname in enumerate(wb_val.sheetnames):
            sh = _parse_openpyxl_sheet(
                wb_val[sname], wb_f[sname], sname,
                workbook_group=workbook_group,
                sheet_order=idx,
                override=overrides.get(sname),
            )
            if sh['tables']:
                sheets.append(sh)
        wb_val.close()
        wb_f.close()
        return {
            'filename': os.path.basename(filename or ''),
            'workbook_group': workbook_group,
            'sheet_count': len(sheets),
            'sheets': sheets,
        }

    # 回退：.xls / .xlsb / .csv 使用统一矩阵读取器
    from utils.spreadsheet_reader import read_spreadsheet_matrix
    matrix = read_spreadsheet_matrix(filename, io.BytesIO(raw_bytes))
    non_empty = [[_clean_str(c) for c in row] for row in matrix if any(_clean_str(c) for c in row)]
    if not non_empty:
        return {'filename': os.path.basename(filename or ''), 'workbook_group': workbook_group, 'sheet_count': 0, 'sheets': []}
    hdr = non_empty[0]
    eff = len(hdr)
    while eff > 1 and hdr[eff - 1] == '':
        eff -= 1
    rows = [r[:eff] + [''] * max(0, eff - len(r)) for r in non_empty[1:]]
    fields = [_infer_field_from_header(hdr[i], [r[i] for r in rows]) for i in range(eff)]
    col_pcts = _normalize_col_pcts([12.0] * len(fields))
    layout = _make_base_layout(
        'standard', workbook_group, 0,
        [{'row': 1, 'text': workbook_group, 'is_meta': False, 'font_px': 18, 'bold': True}],
        col_pcts, paper_orient='landscape' if len(fields) >= 8 else 'portrait',
    )
    return {
        'filename': os.path.basename(filename or ''),
        'workbook_group': workbook_group,
        'sheet_count': 1,
        'sheets': [{
            'sheet_name': workbook_group,
            'mode': 'standard',
            'mode_label': '标准二维表',
            'ghost_cols_trimmed': 0,
            'preview_rows': non_empty[:10],
            'warnings': [],
            'tables': [{
                'suggested_name': workbook_group,
                'fields': fields,
                'rows': rows,
                'sample_rows': rows[:3],
                'row_count': len(rows),
                'layout': layout,
            }],
        }],
    }
