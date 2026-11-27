# -*- coding: utf-8 -*-
"""utils.pdf_export — T21-2 花名册 PDF 导出（reportlab + 思源黑体，R10）。

红线对齐：
- owner map：版式/字体业务逻辑唯一落本模块（routes/ 只做接线，禁 services/）
- 脱敏（R10-E3）：无明文权限出脱敏版 PDF（mask_export_rows，与 Excel 同规则）
- 导出通道（R10-E2/§11-8）：本模块只构建 PDF 文件，发 token 由 routes 层完成
- 字体（M6.0 C2）：Noto Sans SC（思源黑体，SIL OFL 1.1）随包预置 static/fonts/，
  reportlab TTFont 仅支持 TrueType（静态 TTF，非 OTF/可变字体）

R10 版式：A4 纵向；页眉含村名/表名/生成日期；分页页码（第 X 页/共 Y 页）；
统计汇总行加粗；末页预留盖章/签字位（R10-E4，不含电子签章）。
"""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

_FONT_REGULAR = 'NotoSC'
_FONT_BOLD = 'NotoSC-Bold'
_REGISTERED = False


def _font_paths():
    """字体路径（config 配置；frozen 分支由 BASE_DIR 兼容）。"""
    from config import Config
    return (getattr(Config, 'FONT_REGULAR_PATH', None),
            getattr(Config, 'FONT_BOLD_PATH', None))


def register_fonts():
    """注册思源黑体（幂等；缺字体文件抛 ValueError 可读错，页面给出可操作提示）。"""
    global _REGISTERED
    if _REGISTERED:
        return True
    regular, bold = _font_paths()
    missing = [p for p in (regular, bold) if not p or not os.path.isfile(p)]
    if missing:
        raise ValueError(
            'PDF 字体缺失（static/fonts/ 下应含 NotoSansSC-Regular.ttf 与 '
            'NotoSansSC-Bold.ttf，SIL OFL 开源可随包分发）：' + '、'.join(str(p) for p in missing))
    pdfmetrics.registerFont(TTFont(_FONT_REGULAR, regular))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold))
    _REGISTERED = True
    return True


class _NumberedCanvas(pdf_canvas.Canvas):
    """双遍渲染：A4 底部居中「第 X 页 / 共 Y 页」（R10 分页页码）。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number(total)
            super().showPage()
        super().save()

    def _draw_page_number(self, total):
        self.setFont(_FONT_REGULAR, 9)
        self.drawCentredString(A4[0] / 2.0, 10 * mm,
                               f'第 {self._pageNumber} 页 / 共 {total} 页')


def build_roster_pdf(headers, rows, meta, path):
    """构建 A4 纵向花名册 PDF（R10-E1~E4）。

    参数：
      headers: list[str] 表头（与 R6 Excel 一致，mask_export_rows 按表头定位敏感列）
      rows:    list[list] 明细行（**含统计汇总行**，由调用方组装，末行加粗）
      meta:    dict{village, title, generated_at, operator, has_plain}
      path:    输出文件绝对路径（temp/exports/<uuid>.pdf）

    返回 path；字体缺失抛 ValueError（调用方转 500 可读提示）。
    """
    register_fonts()
    # R10-E3：无明文权限 → 脱敏版 PDF（身份证前6后4 等，与 Excel 同规则）
    from utils.masking import mask_export_rows
    has_plain = bool(meta.get('has_plain'))
    rows = mask_export_rows(headers, rows, has_plain)

    doc = BaseDocTemplate(path, pagesize=A4,
                          leftMargin=18 * mm, rightMargin=18 * mm,
                          topMargin=22 * mm, bottomMargin=18 * mm,
                          title=meta.get('title', '民政对象花名册'))
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='main')
    doc.addPageTemplates([PageTemplate(id='page', frames=[frame])])

    story = []
    # 页眉：村名 / 表名 / 生成日期 + 操作人（R10 版式「表头含村名/表名/生成日期」）
    title_style = ParagraphStyle('title', fontName=_FONT_BOLD, fontSize=16,
                                 leading=22, alignment=1, spaceAfter=4)
    sub_style = ParagraphStyle('sub', fontName=_FONT_REGULAR, fontSize=10,
                               leading=14, alignment=1,
                               textColor=colors.HexColor('#444444'))
    story.append(Paragraph(meta.get('village', ''), title_style))
    story.append(Paragraph(meta.get('title', '民政对象花名册'), sub_style))
    story.append(Paragraph(
        f'生成时间：{meta.get("generated_at", "")}　操作人：{meta.get("operator", "")}'
        + ('　（含明文版）' if has_plain else ''),
        sub_style))
    story.append(Spacer(1, 6))

    # 数据表：表头跨页（repeatRows=1，R10 分页）；统计行（末行）加粗
    data = [headers] + rows
    col_w = [doc.width / max(1, len(headers))] * len(headers)
    tbl = Table(data, colWidths=col_w, repeatRows=1)
    style = [
        ('FONTNAME', (0, 0), (-1, -1), _FONT_REGULAR),   # body 默认思源黑体（防 Helvetica 豆腐块）
        ('FONTNAME', (0, 0), (-1, 0), _FONT_BOLD),       # 表头加粗
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EEEEEE')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#999999')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    if rows:
        last = len(data) - 1
        style += [('FONTNAME', (0, last), (-1, last), _FONT_BOLD),
                  ('BACKGROUND', (0, last), (-1, last), colors.HexColor('#F5F5F5'))]
    tbl.setStyle(TableStyle(style))
    story.append(tbl)
    story.append(Spacer(1, 14))

    # R10-E4：盖章/签字位预留（不含电子签章）
    stamp_style = ParagraphStyle('stamp', fontName=_FONT_REGULAR, fontSize=10,
                                 leading=14, alignment=2)
    story.append(Paragraph('盖章/签字位：＿＿＿＿＿＿＿＿＿＿＿＿＿＿', stamp_style))

    doc.build(story, canvasmaker=_NumberedCanvas)
    return path
