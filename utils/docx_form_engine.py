# -*- coding: utf-8 -*-
"""utils.docx_form_engine — 通用 Word (.docx) 表单引擎（零硬编码解析 + 原位回写导出 + 文件型持久化）。

设计原则（高复用、零数据库迁移）：
1. 任意 `.docx` 上传后，直接按 OOXML 文档顺序解析段落（w:p）与表格（w:tbl），保留合并单元格
   （w:gridSpan / w:vMerge）、列宽比例（w:gridCol → 归一化 100%）、行高（w:trHeight）、
   对齐方式（w:jc / w:vAlign）、字体/字号/加粗、复选框（□/☐/☑/☒）及纸张方向/页边距。
2. 支持「智能字段自动绑定 + 纯自定义手填」双模式，并支持在页面中微调行高、列宽、字体、
   字号、加粗、水平/垂直对齐及固定 A4 纸张（纵向 210mm / 横向 297mm）单页宽适配。
3. 导出时在原始 `.docx` 模板上原位回写（python-docx），完整保留公文原版边框、印章留白与排版。
"""
import io
import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W = '{' + W_NS + '}'

# 支持自动回填的居民主档 / 残疾人 / 低保字段目录（前端模板绑定下拉框共用）
FIELD_OPTIONS = [
    ('', '（不绑定 · 纯自定义手填）'),
    ('name', '居民姓名'),
    ('gender', '性别'),
    ('birth_date', '出生日期'),
    ('age', '年龄'),
    ('id_card', '身份证号'),
    ('phone', '联系电话'),
    ('nation', '民族'),
    ('marital_status', '婚姻状况'),
    ('household_address', '户籍地址'),
    ('address', '居住地址'),
    ('village_group', '所在组'),
    ('family_size', '家庭人口'),
    ('disability_type', '残疾类别'),
    ('disability_level', '残疾等级'),
    ('disability_no', '残疾人证号'),
    ('dibao_no', '低保证号/救助类别'),
    ('bank_card', '银行/社保卡账号'),
    ('bank_name', '开户银行名称'),
    ('bank_holder', '开户人姓名'),
    ('guardian_name', '监护人姓名'),
    ('guardian_relation', '监护人与申请人关系'),
    ('guardian_phone', '监护人联系电话'),
    ('guardian_address', '监护人地址'),
]

AUTO_BIND_RULES = [
    # ⚠️ 顺序即优先级：长词/限定词必须排在短词之前（'开户人姓名' 先于 '姓名'，
    # 否则 '姓名' 子串先命中，开户人会被错绑成本人姓名）。
    ('申请人姓名', 'name'),
    ('开户人姓名', 'bank_holder'),
    ('监护人姓名', 'guardian_name'),
    ('监护人电话', 'guardian_phone'),
    ('监护人关系', 'guardian_relation'),
    ('姓名', 'name'),
    ('性别', 'gender'),
    ('出生日期', 'birth_date'),
    ('年龄', 'age'),
    ('婚姻状况', 'marital_status'),
    ('民族', 'nation'),
    ('残疾类别', 'disability_type'),
    ('残疾等级', 'disability_level'),
    ('户籍地址', 'household_address'),
    ('居住地址', 'address'),
    ('身份证号', 'id_card'),
    ('身份证号码', 'id_card'),
    ('残疾人证号', 'disability_no'),
    ('残疾证号', 'disability_no'),
    ('家庭人口', 'family_size'),
    ('联系电话', 'phone'),
    ('银行账号', 'bank_card'),
    ('银行社保卡账号', 'bank_card'),
    ('开户银行名称', 'bank_name'),
    ('开户银行', 'bank_name'),
    ('低保证号', 'dibao_no'),
    ('救助类别', 'dibao_no'),
    # 兜底词（排在全部具体词之后）
    ('家庭住址', 'address'),
    ('现住址', 'address'),
    ('住址', 'address'),
    ('地址', 'address'),
    ('联系方式', 'phone'),
    ('电话', 'phone'),
]

GUARDIAN_REMAP = {
    'name': 'guardian_name',
    'phone': 'guardian_phone',
    'household_address': 'guardian_address',
    'address': 'guardian_address',
}

FONT_FAMILY_MAP = {
    "SimSun, '宋体', serif": '宋体',
    "FangSong, '仿宋', serif": '仿宋',
    "'Microsoft YaHei', sans-serif": '微软雅黑',
    "SimHei, '黑体', sans-serif": '黑体',
    '宋体': '宋体',
    '仿宋': '仿宋',
    '微软雅黑': '微软雅黑',
    '黑体': '黑体',
}

MARGIN_MM_MAP = {
    'narrow': 10.0,
    'normal': 15.0,
    'wide': 20.0,
}

_SAFE_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{4,64}$')
# 勾选框字符族：□☐ 未勾选；☑☒ 已勾选（民政表单常见 3 种写法，缺一即漏识别）
_CHECKBOX_RE = re.compile(r'[□☐☑☒]')
CHECKBOX_UNCHECKED = '□'
CHECKBOX_CHECKED = '☑'
_PLACEHOLDER_CELL_RE = re.compile(r'^\s*(年\s*月\s*日|级|人|元)\s*$')
# 单元格内填空下划线（如「姓名：____」「电话：____」）视为可填写位
_FILL_LINE_RE = re.compile(r'_{2,}')
_EDITABLE_PARA_RE = re.compile(r'_{2,}|XX|年\s*月\s*日|^\s{2,}村')


def _clean_label(text):
    return re.sub(r'[\s*＊:：（）()]', '', text or '')


def _match_auto_bind(label_text, in_guardian_section=False):
    lbl = _clean_label(label_text)
    if not lbl:
        return ''
    for kw, field_key in AUTO_BIND_RULES:
        if kw in lbl:
            if in_guardian_section and field_key in GUARDIAN_REMAP:
                return GUARDIAN_REMAP[field_key]
            return field_key
    return ''


def _normalize_col_pcts(raw_widths):
    """将任意列宽数组归一化为总和严格 100.00%（每列不低于 2.5%）。"""
    if not raw_widths:
        return []
    vals = [max(1.0, float(w or 1.0)) for w in raw_widths]
    total = sum(vals) or 1.0
    pcts = [max(2.5, round((v / total) * 100.0, 2)) for v in vals]
    diff = round(100.0 - sum(pcts), 2)
    pcts[-1] = max(2.5, round(pcts[-1] + diff, 2))
    return pcts


def _extract_para_text(p_el):
    parts = []
    for node in p_el.iter():
        if node.tag == W + 't' and node.text:
            parts.append(node.text)
        elif node.tag == W + 'tab':
            parts.append('    ')
        elif node.tag in (W + 'br', W + 'cr'):
            parts.append('\n')
    return ''.join(parts)


def _extract_align(p_el, default='left'):
    jc = p_el.find(f'.//{W}jc')
    if jc is None:
        return default
    val = (jc.get(W + 'val') or '').lower()
    if val in ('left', 'start'):
        return 'left'
    if val == 'center':
        return 'center'
    if val in ('right', 'end'):
        return 'right'
    if val in ('both', 'distribute'):
        return 'justify'
    return default


def _extract_font_pt(el, default=12.0):
    sz = el.find(f'.//{W}sz')
    if sz is not None:
        raw = sz.get(W + 'val')
        if raw and raw.isdigit():
            return round(int(raw) / 2.0, 1)
    return default


def _extract_font_family(el):
    rf = el.find(f'.//{W}rFonts')
    if rf is not None:
        east = (rf.get(W + 'eastAsia') or rf.get(W + 'ascii') or '').strip()
        if '仿宋' in east or 'FangSong' in east:
            return "FangSong, '仿宋', serif"
        if '黑体' in east or 'SimHei' in east:
            return "SimHei, '黑体', sans-serif"
        if '微软雅黑' in east or 'Microsoft YaHei' in east:
            return "'Microsoft YaHei', sans-serif"
    return "SimSun, '宋体', serif"


def _extract_bold(el):
    b = el.find(f'.//{W}b')
    if b is None:
        return False
    val = (b.get(W + 'val') or '1').lower()
    return val not in ('0', 'false', 'off')


def _extract_valign(tc_el):
    va = tc_el.find(f'.//{W}vAlign')
    if va is None:
        return 'middle'
    val = (va.get(W + 'val') or '').lower()
    if val == 'top':
        return 'top'
    if val == 'bottom':
        return 'bottom'
    return 'middle'


def _twips_to_px(twips):
    # 1 inch = 1440 twips = 96 px → 1 px = 15 twips
    return max(20, min(260, int(round(int(twips) / 15.0))))


def parse_docx_bytes(docx_bytes, title=None, filename=None):
    """通用解析任意 `.docx` 字节流，返回前端统一渲染契约（blocks + paper 设置）。"""
    if not docx_bytes:
        raise ValueError('空的 .docx 文件')
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
            if 'word/document.xml' not in zf.namelist():
                raise ValueError('不是有效的 .docx 文件（缺少 word/document.xml）')
            xml_bytes = zf.read('word/document.xml')
    except zipfile.BadZipFile as exc:
        raise ValueError('无效的 .docx 压缩包格式') from exc

    root = ET.fromstring(xml_bytes)
    body = root.find(W + 'body')
    if body is None:
        raise ValueError('文档结构异常（缺少 w:body）')

    # 解析纸张方向与页边距（默认锁定 A4）
    paper_orient = 'portrait'
    paper_margin = 'normal'
    sect_pr = body.find(W + 'sectPr')
    if sect_pr is not None:
        pg_sz = sect_pr.find(W + 'pgSz')
        if pg_sz is not None:
            orient = (pg_sz.get(W + 'orient') or '').lower()
            w_val = int(pg_sz.get(W + 'w') or 11906)
            h_val = int(pg_sz.get(W + 'h') or 16838)
            if orient == 'landscape' or w_val > h_val:
                paper_orient = 'landscape'
        pg_mar = sect_pr.find(W + 'pgMar')
        if pg_mar is not None:
            left_twips = int(pg_mar.get(W + 'left') or 850)
            left_mm = left_twips / 56.7
            if left_mm <= 12.5:
                paper_margin = 'narrow'
            elif left_mm >= 18.0:
                paper_margin = 'wide'
            else:
                paper_margin = 'normal'

    blocks = []
    tbl_idx = 0
    para_idx = 0
    inferred_title = ''

    for child in list(body):
        if child.tag == W + 'p':
            txt = _extract_para_text(child)
            if not txt.strip():
                continue
            font_pt = _extract_font_pt(child, 12.0)
            align = _extract_align(child, 'left')
            bold = _extract_bold(child)
            font_family = _extract_font_family(child)
            if not inferred_title and para_idx == 0:
                inferred_title = txt.strip()[:40]
            font_px = max(10, min(32, int(round(font_pt * 1.25))))
            blocks.append({
                'type': 'paragraph',
                'p_idx': para_idx,
                'text': txt,
                'align': align,
                'bold': bold,
                'font_pt': font_pt,
                'font_px': font_px,
                'font_family': font_family,
                'editable': bool(_EDITABLE_PARA_RE.search(txt)),
                # 解析原值快照：导出时据此判定「用户是否真改过格式」，未改动则原版零触碰
                'orig': {'align': align, 'bold': bold,
                         'font_px': font_px, 'font_family': font_family},
            })
            para_idx += 1

        elif child.tag == W + 'tbl':
            tbl_grid = child.find(W + 'tblGrid')
            raw_ws = []
            if tbl_grid is not None:
                for gc in tbl_grid.findall(W + 'gridCol'):
                    raw_ws.append(int(gc.get(W + 'w') or 1000))

            trs = child.findall(W + 'tr')
            grid_rows = []
            row_heights = {}
            max_cols = len(raw_ws)

            for ri, tr in enumerate(trs):
                tr_h = tr.find(f'{W}trPr/{W}trHeight')
                if tr_h is not None and (tr_h.get(W + 'val') or '').isdigit():
                    row_heights[str(ri)] = _twips_to_px(int(tr_h.get(W + 'val')))
                cells = []
                cur_ci = 0
                for tc in tr.findall(W + 'tc'):
                    tc_pr = tc.find(W + 'tcPr')
                    colspan = 1
                    vmerge = None
                    if tc_pr is not None:
                        gs = tc_pr.find(W + 'gridSpan')
                        if gs is not None and (gs.get(W + 'val') or '').isdigit():
                            colspan = max(1, int(gs.get(W + 'val')))
                        vm = tc_pr.find(W + 'vMerge')
                        if vm is not None:
                            vmerge = (vm.get(W + 'val') or 'continue').lower()

                    paras = tc.findall(W + 'p')
                    cell_text = '\n'.join(_extract_para_text(p).rstrip() for p in paras).strip()
                    align = _extract_align(paras[0], 'center') if paras else 'center'
                    valign = _extract_valign(tc)
                    font_pt = _extract_font_pt(tc, 10.5)
                    font_px = max(10, min(32, int(round(font_pt * 1.15))))
                    bold = _extract_bold(tc)
                    font_family = _extract_font_family(tc)
                    has_cb = bool(_CHECKBOX_RE.search(cell_text))
                    is_editable = (
                        cell_text == ''
                        or has_cb
                        or bool(_PLACEHOLDER_CELL_RE.match(cell_text))
                        or bool(_FILL_LINE_RE.search(cell_text))
                        or len(cell_text) > 36
                    )

                    cells.append({
                        'id': f't{tbl_idx}_r{ri}_c{cur_ci}',
                        'ri': ri,
                        'ci': cur_ci,
                        'colspan': colspan,
                        'rowspan': 1,
                        'vmerge': vmerge,
                        'skip': False,
                        'text': cell_text,
                        'align': align,
                        'valign': valign,
                        'font_px': font_px,
                        'font_family': font_family,
                        'bold': bold,
                        'editable': is_editable,
                        'has_checkbox': has_cb,
                        'bind': '',
                        'orig': {'align': align, 'valign': valign, 'bold': bold,
                                 'font_px': font_px, 'font_family': font_family},
                    })
                    cur_ci += colspan
                if cur_ci > max_cols:
                    max_cols = cur_ci
                grid_rows.append(cells)

            # 计算 vMerge rowspan 并标记被合并续格
            active_vm = {}
            for row in grid_rows:
                for c in row:
                    if c['vmerge'] == 'restart':
                        active_vm[c['ci']] = c
                    elif c['vmerge'] == 'continue' and c['ci'] in active_vm:
                        active_vm[c['ci']]['rowspan'] += 1
                        c['skip'] = True
                    else:
                        active_vm.pop(c['ci'], None)

            # 过滤续格并做左侧标签 → 右侧值格自动绑定推断
            clean_rows = []
            in_guardian_section = False
            for row in grid_rows:
                vis_row = [
                    {k: v for k, v in c.items() if k not in ('vmerge', 'skip')}
                    for c in row if not c['skip']
                ]
                row_all_text = ' '.join(c['text'] for c in vis_row)
                if '监护人' in row_all_text:
                    in_guardian_section = True
                if '审查意见' in row_all_text or '审批意见' in row_all_text:
                    in_guardian_section = False

                for idx, c in enumerate(vis_row):
                    if c['editable'] and not c['has_checkbox'] and idx > 0:
                        left_cell = vis_row[idx - 1]
                        if not left_cell['editable']:
                            guessed = _match_auto_bind(left_cell['text'], in_guardian_section)
                            if guessed:
                                c['bind'] = guessed
                clean_rows.append(vis_row)

            if len(raw_ws) < max_cols:
                raw_ws.extend([1000] * (max_cols - len(raw_ws)))
            col_pcts = _normalize_col_pcts(raw_ws[:max_cols])

            blocks.append({
                'type': 'table',
                'tbl_idx': tbl_idx,
                'col_pcts': col_pcts,
                'row_heights': row_heights,
                'rows': clean_rows,
            })
            tbl_idx += 1

    final_title = (title or '').strip()
    if not final_title and filename:
        final_title = re.sub(r'\.docx$', '', os.path.basename(filename), flags=re.I).strip()
    if not final_title:
        final_title = inferred_title or '未命名 Word 表单'

    return {
        'title': final_title,
        'filename': os.path.basename(filename or (final_title + '.docx')),
        'paper_orient': paper_orient,
        'paper_margin': paper_margin,
        'blocks': blocks,
    }


def _set_run_font(run, font_family=None, font_px=None, bold=None):
    cn_name = FONT_FAMILY_MAP.get(font_family or '', '')
    if cn_name:
        run.font.name = cn_name
        r_pr = run._element.get_or_add_rPr()
        r_fonts = r_pr.find(qn('w:rFonts'))
        if r_fonts is None:
            r_fonts = OxmlElement('w:rFonts')
            r_pr.insert(0, r_fonts)
        r_fonts.set(qn('w:eastAsia'), cn_name)
        r_fonts.set(qn('w:ascii'), cn_name)
        r_fonts.set(qn('w:hAnsi'), cn_name)
    if font_px:
        pt_val = max(7.5, min(28.0, round(float(font_px) * 0.75, 1)))
        run.font.size = Pt(pt_val)
    if bold is not None:
        run.bold = bool(bold)


def _set_para_align(para, align_str):
    mapping = {
        'left': WD_ALIGN_PARAGRAPH.LEFT,
        'center': WD_ALIGN_PARAGRAPH.CENTER,
        'right': WD_ALIGN_PARAGRAPH.RIGHT,
        'justify': WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    if align_str in mapping:
        para.alignment = mapping[align_str]


def _set_cell_valign(tc, valign_str):
    mapping = {
        'top': WD_ALIGN_VERTICAL.TOP,
        'middle': WD_ALIGN_VERTICAL.CENTER,
        'bottom': WD_ALIGN_VERTICAL.BOTTOM,
    }
    if valign_str in mapping:
        tc.vertical_alignment = mapping[valign_str]


def _set_tc_width_twips(tc_el, twips):
    tc_pr = tc_el.find(qn('w:tcPr'))
    if tc_pr is None:
        tc_pr = OxmlElement('w:tcPr')
        tc_el.insert(0, tc_pr)
    tc_w = tc_pr.find(qn('w:tcW'))
    if tc_w is None:
        tc_w = OxmlElement('w:tcW')
        tc_pr.append(tc_w)
    tc_w.set(qn('w:w'), str(int(twips)))
    tc_w.set(qn('w:type'), 'dxa')


def _set_tr_height_twips(tr_el, twips):
    tr_pr = tr_el.find(qn('w:trPr'))
    if tr_pr is None:
        tr_pr = OxmlElement('w:trPr')
        tr_el.insert(0, tr_pr)
    tr_h = tr_pr.find(qn('w:trHeight'))
    if tr_h is None:
        tr_h = OxmlElement('w:trHeight')
        tr_pr.append(tr_h)
    tr_h.set(qn('w:val'), str(int(twips)))
    tr_h.set(qn('w:hRule'), 'atLeast')


def _apply_table_col_widths_a4(tbl, col_pcts, printable_width_mm):
    """按归一化百分比将表格列宽严格回写到 A4 可打印单页宽内（tblGrid + 每格 tcW）。"""
    if not col_pcts:
        return
    pcts = _normalize_col_pcts(col_pcts)
    total_twips = int(round(printable_width_mm * 56.6929))
    col_twips = [max(240, int(round(total_twips * (p / 100.0)))) for p in pcts]
    diff = total_twips - sum(col_twips)
    col_twips[-1] = max(240, col_twips[-1] + diff)

    tbl_el = tbl._tbl
    tbl_pr = tbl_el.find(qn('w:tblPr'))
    if tbl_pr is not None:
        tbl_w = tbl_pr.find(qn('w:tblW'))
        if tbl_w is None:
            tbl_w = OxmlElement('w:tblW')
            tbl_pr.append(tbl_w)
        tbl_w.set(qn('w:w'), str(total_twips))
        tbl_w.set(qn('w:type'), 'dxa')

    tbl_grid = tbl_el.find(qn('w:tblGrid'))
    if tbl_grid is not None:
        gcs = tbl_grid.findall(qn('w:gridCol'))
        for idx, gc in enumerate(gcs):
            if idx < len(col_twips):
                gc.set(qn('w:w'), str(col_twips[idx]))

    for tr in tbl_el.findall(qn('w:tr')):
        cur_ci = 0
        for tc in tr.findall(qn('w:tc')):
            gs = tc.find(f'{W}tcPr/{W}gridSpan')
            span = int(gs.get(W + 'val')) if (gs is not None and (gs.get(W + 'val') or '').isdigit()) else 1
            w_sum = sum(col_twips[cur_ci:cur_ci + span]) if cur_ci < len(col_twips) else 1000
            _set_tc_width_twips(tc, w_sum)
            cur_ci += span


def _clear_para_runs(para):
    for child in list(para._p):
        if child.tag != qn('w:pPr'):
            para._p.remove(child)


_FMT_KEYS = ('font_px', 'font_family', 'bold')


def _fmt_dirty(meta):
    """用户是否改过字体/字号/加粗（与解析原值比对）。"""
    orig = meta.get('orig') or {}
    return any(meta.get(k) != orig.get(k) for k in _FMT_KEYS)


def _style_para_runs(para, meta):
    """未填写段落：仅在与解析原值不同时才动对齐/字体，原版格式零触碰。"""
    orig = meta.get('orig') or {}
    if meta.get('align') != orig.get('align'):
        _set_para_align(para, meta.get('align'))
    if _fmt_dirty(meta):
        for run in para.runs:
            _set_run_font(
                run,
                font_family=meta.get('font_family'),
                font_px=meta.get('font_px'),
                bold=meta.get('bold'),
            )


def _apply_cell_style(cell_obj, cell_meta):
    """未填写单元格：只回写用户改过的对齐/字体，保留原段落结构与局部格式。"""
    orig = cell_meta.get('orig') or {}
    if cell_meta.get('valign') != orig.get('valign'):
        _set_cell_valign(cell_obj, cell_meta.get('valign'))
    if cell_meta.get('align') != orig.get('align') or _fmt_dirty(cell_meta):
        for para in cell_obj.paragraphs:
            _style_para_runs(para, cell_meta)


def _replace_cell_text(cell_obj, text_val, cell_meta):
    """已填写单元格：清空后写入新值（多行按段落拆分）并套用填报格式。"""
    lines = str(text_val if text_val is not None else '').split('\n')
    # 清空多余段落，保留首段以继承原样式
    while len(cell_obj.paragraphs) > 1:
        p = cell_obj.paragraphs[-1]._element
        p.getparent().remove(p)
    first_p = cell_obj.paragraphs[0]
    _clear_para_runs(first_p)
    for idx, line in enumerate(lines):
        p = first_p if idx == 0 else cell_obj.add_paragraph()
        _set_para_align(p, cell_meta.get('align'))
        run = p.add_run(line)
        _set_run_font(
            run,
            font_family=cell_meta.get('font_family'),
            font_px=cell_meta.get('font_px'),
            bold=cell_meta.get('bold'),
        )
    _set_cell_valign(cell_obj, cell_meta.get('valign'))


def _replace_para_text(para, text_val, meta):
    """已填写段落：清空 run 后写入新文本并套用格式。"""
    _clear_para_runs(para)
    _set_para_align(para, meta.get('align'))
    run = para.add_run(str(text_val or ''))
    _set_run_font(
        run,
        font_family=meta.get('font_family'),
        font_px=meta.get('font_px'),
        bold=meta.get('bold'),
    )


def render_docx_with_values(original_docx_bytes, schema, values=None):
    """在原始 `.docx` 模板上原位回写填报值与版式微调，输出 1:1 真实 `.docx` 字节流。"""
    doc = Document(io.BytesIO(original_docx_bytes))
    vals = values or {}
    blocks = (schema or {}).get('blocks') or []

    # 1. 固定 A4 纸张方向与页边距
    orient = (schema or {}).get('paper_orient', 'portrait')
    margin_key = (schema or {}).get('paper_margin', 'normal')
    margin_mm = MARGIN_MM_MAP.get(margin_key, 15.0)
    page_w_mm = 297.0 if orient == 'landscape' else 210.0
    page_h_mm = 210.0 if orient == 'landscape' else 297.0
    printable_w_mm = max(120.0, page_w_mm - 2.0 * margin_mm)

    for sec in doc.sections:
        sec.orientation = WD_ORIENT.LANDSCAPE if orient == 'landscape' else WD_ORIENT.PORTRAIT
        sec.page_width = Mm(page_w_mm)
        sec.page_height = Mm(page_h_mm)
        sec.left_margin = Mm(margin_mm)
        sec.right_margin = Mm(margin_mm)
        sec.top_margin = Mm(margin_mm)
        sec.bottom_margin = Mm(margin_mm)

    # 2. 按文档顺序回写非空段落（文本 + 字体/字号/加粗/对齐）
    non_empty_paras = [p for p in doc.paragraphs if (p.text or '').strip()]
    para_blocks = [(bi, b) for bi, b in enumerate(blocks) if b.get('type') == 'paragraph']
    for order_i, (bi, p_blk) in enumerate(para_blocks):
        if order_i >= len(non_empty_paras):
            break
        p_obj = non_empty_paras[order_i]
        p_key = f'p_{bi}'
        if p_key in vals:
            _replace_para_text(p_obj, vals.get(p_key), p_blk)
        else:
            # 未改动段落：保留原文字与 run 结构，仅应用对齐/字体微调
            _style_para_runs(p_obj, p_blk)

    # 3. 回写各表格（支持末行增行、合并格坐标映射、文字样式、行高、A4 单页宽列宽）
    table_blocks = [b for b in blocks if b.get('type') == 'table']
    for t_i, t_blk in enumerate(table_blocks):
        if t_i >= len(doc.tables):
            break
        tbl = doc.tables[t_i]
        schema_rows = t_blk.get('rows') or []

        # 如果前端追加了空白行，同步补齐 Word 表格行
        # （python-docx add_row() 按 tblGrid 新建整行：无 vMerge / 无沿用格式，
        #   故前端新增行必须覆盖全列，否则缺列的格没有 meta、填值无处落盘）
        while len(tbl.rows) < len(schema_rows):
            tbl.add_row()

        # 构造 (ri, ci) -> python-docx Cell 映射（按真实 gridSpan 累加列坐标）
        coord_map = {}
        for ri, row in enumerate(tbl.rows):
            cur_ci = 0
            tr_el = row._tr
            tcs = tr_el.findall(qn('w:tc'))
            for tc_el in tcs:
                gs = tc_el.find(f'{W}tcPr/{W}gridSpan')
                span = int(gs.get(W + 'val')) if (gs is not None and (gs.get(W + 'val') or '').isdigit()) else 1
                # 通过 python-docx _Cell 包装该 tc 元素
                from docx.table import _Cell
                coord_map[(ri, cur_ci)] = _Cell(tc_el, tbl)
                cur_ci += span

        row_heights = t_blk.get('row_heights') or {}
        for ri, s_row in enumerate(schema_rows):
            rh_px = row_heights.get(str(ri)) or row_heights.get(ri)
            if rh_px and ri < len(tbl.rows):
                _set_tr_height_twips(tbl.rows[ri]._tr, int(round(float(rh_px) * 15.0)))
            for cell_meta in s_row:
                key = (cell_meta.get('ri', ri), cell_meta.get('ci', 0))
                cell_obj = coord_map.get(key)
                if cell_obj is None:
                    continue
                cid = cell_meta.get('id', '')
                if cid in vals:
                    _replace_cell_text(cell_obj, vals.get(cid), cell_meta)
                else:
                    # 未填写：原版单元格内容与段落结构零触碰，只回写版式微调
                    _apply_cell_style(cell_obj, cell_meta)

        _apply_table_col_widths_a4(tbl, t_blk.get('col_pcts') or [], printable_w_mm)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def build_resident_autofill_dict(resident, disabled=None, dibao=None, family_size=None):
    """将 Resudent 居民主档 + 残疾人/低保子档转换为通用表单自动回填字典。"""
    def _dec(obj, field):
        """按 R12 口径只读 ``*_enc`` 解密回填；明文残留不回显，锁定态/异常返回空串。"""
        if obj is None:
            return ''
        enc_val = getattr(obj, f'{field}_enc', None)
        if not enc_val:
            return ''
        try:
            from utils.crypto import key_holder
            return str(key_holder.decrypt(enc_val) or '')
        except Exception:
            return ''

    bdate = getattr(resident, 'birth_date', None)
    bdate_cn = bdate.strftime('%Y年%m月%d日') if bdate else ''
    age_str = ''
    if bdate:
        today = datetime.today()
        age = today.year - bdate.year - ((today.month, today.day) < (bdate.month, bdate.day))
        age_str = str(max(0, age))

    addr = getattr(resident, 'address', '') or ''
    reg_addr = (getattr(disabled, 'registered_address', '') if disabled else '') or addr

    return {
        'name': getattr(resident, 'name', '') or '',
        'gender': getattr(resident, 'gender', '') or '',
        'birth_date': bdate_cn,
        'age': age_str,
        'id_card': _dec(resident, 'id_card'),
        'phone': _dec(resident, 'phone'),
        'nation': getattr(resident, 'nation', '') or '',
        'marital_status': getattr(resident, 'marital_status', '') or '',
        'household_address': reg_addr,
        'address': addr,
        'village_group': getattr(resident, 'village_group', '') or '',
        'family_size': str(family_size) if family_size else '',
        'disability_type': (getattr(disabled, 'disability_type', '') if disabled else '') or '',
        'disability_level': (getattr(disabled, 'disability_level', '') if disabled else '') or '',
        'disability_no': (getattr(disabled, 'cert_no', '') if disabled else '') or '',
        'dibao_no': (getattr(dibao, 'dibao_type', '') if dibao else '') or '',
        'bank_card': _dec(resident, 'bank_card'),
        'bank_name': getattr(resident, 'bank_name', '') or '',
        'bank_holder': getattr(resident, 'name', '') or '',
        'guardian_name': (getattr(disabled, 'guardian_name', '') if disabled else '') or '',
        'guardian_relation': (getattr(disabled, 'guardian_relation', '') if disabled else '') or '',
        'guardian_phone': _dec(disabled, 'guardian_phone') if disabled else '',
        'guardian_address': addr,
    }


class WordFormStore:
    """零数据库迁移的本地文件型存储（保存在 DATA_DIR/word_forms）。"""

    def __init__(self, base_dir):
        self.base_dir = os.path.abspath(base_dir)
        self.tpl_dir = os.path.join(self.base_dir, 'templates')
        self.rec_dir = os.path.join(self.base_dir, 'records')
        os.makedirs(self.tpl_dir, exist_ok=True)
        os.makedirs(self.rec_dir, exist_ok=True)

    @staticmethod
    def _validate_id(item_id):
        if not item_id or not _SAFE_ID_RE.match(str(item_id)):
            raise ValueError('非法的表单标识')
        return str(item_id)

    @staticmethod
    def _atomic_write_json(path, payload):
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def create_template_from_docx(self, docx_bytes, filename, title=None, created_by=''):
        schema = parse_docx_bytes(docx_bytes, title=title, filename=filename)
        tpl_id = 'wf_' + uuid.uuid4().hex[:12]
        tdir = os.path.join(self.tpl_dir, tpl_id)
        os.makedirs(tdir, exist_ok=True)
        with open(os.path.join(tdir, 'original.docx'), 'wb') as f:
            f.write(docx_bytes)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        meta = {
            'id': tpl_id,
            'title': schema['title'],
            'filename': schema['filename'],
            'paper_orient': schema['paper_orient'],
            'paper_margin': schema['paper_margin'],
            'blocks': schema['blocks'],
            'created_by': created_by,
            'created_at': now,
            'updated_at': now,
        }
        self._atomic_write_json(os.path.join(tdir, 'schema.json'), meta)
        return meta

    def list_templates(self):
        items = []
        if not os.path.isdir(self.tpl_dir):
            return items
        for name in sorted(os.listdir(self.tpl_dir)):
            spath = os.path.join(self.tpl_dir, name, 'schema.json')
            if not os.path.isfile(spath):
                continue
            try:
                with open(spath, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                tbl = next((b for b in d.get('blocks', []) if b.get('type') == 'table'), None)
                items.append({
                    'id': d['id'],
                    'title': d.get('title', ''),
                    'filename': d.get('filename', ''),
                    'paper_orient': d.get('paper_orient', 'portrait'),
                    'paper_margin': d.get('paper_margin', 'normal'),
                    'rows_count': len(tbl['rows']) if tbl else 0,
                    'cols_count': len(tbl['col_pcts']) if tbl else 0,
                    'updated_at': d.get('updated_at', ''),
                })
            except Exception:
                continue
        items.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
        return items

    def get_template(self, tpl_id):
        tid = self._validate_id(tpl_id)
        spath = os.path.join(self.tpl_dir, tid, 'schema.json')
        if not os.path.isfile(spath):
            return None
        with open(spath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_template_docx_bytes(self, tpl_id):
        tid = self._validate_id(tpl_id)
        dpath = os.path.join(self.tpl_dir, tid, 'original.docx')
        if not os.path.isfile(dpath):
            return None
        with open(dpath, 'rb') as f:
            return f.read()

    def update_template_schema(self, tpl_id, updates):
        tpl = self.get_template(tpl_id)
        if not tpl:
            return None
        for k in ('title', 'paper_orient', 'paper_margin', 'blocks'):
            if k in updates and updates[k] is not None:
                tpl[k] = updates[k]
        tpl['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        spath = os.path.join(self.tpl_dir, tpl['id'], 'schema.json')
        self._atomic_write_json(spath, tpl)
        return tpl

    def delete_template(self, tpl_id):
        tid = self._validate_id(tpl_id)
        tdir = os.path.join(self.tpl_dir, tid)
        if not os.path.isdir(tdir):
            return False
        shutil.rmtree(tdir)
        return True

    def save_record(self, template_id, title, values, schema_snapshot=None, resident_id=None, rec_id=None, operator=''):
        tid = self._validate_id(template_id)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if rec_id:
            rid = self._validate_id(rec_id)
            existing = self.get_record(rid) or {}
            created_at = existing.get('created_at', now)
        else:
            rid = 'rec_' + uuid.uuid4().hex[:12]
            created_at = now
        rec = {
            'id': rid,
            'template_id': tid,
            'title': (title or '').strip() or '未命名填报记录',
            'resident_id': resident_id,
            'values': values or {},
            'schema_snapshot': schema_snapshot,
            'operator': operator,
            'created_at': created_at,
            'updated_at': now,
        }
        self._atomic_write_json(os.path.join(self.rec_dir, f'{rid}.json'), rec)
        return rec

    def list_records(self, template_id=None):
        items = []
        if not os.path.isdir(self.rec_dir):
            return items
        for fname in sorted(os.listdir(self.rec_dir)):
            if not fname.endswith('.json'):
                continue
            try:
                with open(os.path.join(self.rec_dir, fname), 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if template_id and d.get('template_id') != template_id:
                    continue
                items.append({
                    'id': d['id'],
                    'template_id': d.get('template_id', ''),
                    'title': d.get('title', ''),
                    'resident_id': d.get('resident_id'),
                    'operator': d.get('operator', ''),
                    'updated_at': d.get('updated_at', ''),
                })
            except Exception:
                continue
        items.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
        return items

    def get_record(self, rec_id):
        rid = self._validate_id(rec_id)
        rpath = os.path.join(self.rec_dir, f'{rid}.json')
        if not os.path.isfile(rpath):
            return None
        with open(rpath, 'r', encoding='utf-8') as f:
            return json.load(f)

    def delete_record(self, rec_id):
        rid = self._validate_id(rec_id)
        rpath = os.path.join(self.rec_dir, f'{rid}.json')
        if not os.path.isfile(rpath):
            return False
        os.remove(rpath)
        return True
