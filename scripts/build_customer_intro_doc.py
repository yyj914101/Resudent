from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "村务综合管理系统_客户介绍_V2.45.docx"

BLUE = RGBColor(31, 78, 121)
DEEP_BLUE = RGBColor(18, 52, 86)
MUTED = RGBColor(95, 111, 129)
LIGHT_BLUE = "EAF2F8"
LIGHT_GRAY = "F3F6FA"
WHITE = RGBColor(255, 255, 255)


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_text(cell, text, bold=False, color=None, size=10.5):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(text)
    run.bold = bold
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    run.font.size = Pt(size)
    if color:
        run.font.color.rgb = color
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def set_run_font(run, size=None, bold=None, color=None):
    run.font.name = "Microsoft YaHei"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color:
        run.font.color.rgb = color


def add_paragraph(doc, text="", size=10.5, color=None, bold=False, after=6, align=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = 1.2
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color)
    return p


def add_heading(doc, text, level=1):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    p.paragraph_format.space_after = Pt(6)
    run = p.add_run(text)
    set_run_font(run, size=16 if level == 1 else 13, bold=True, color=BLUE if level == 1 else DEEP_BLUE)
    return p


def add_bullet(doc, text):
    p = doc.add_paragraph(style=None)
    p.paragraph_format.left_indent = Cm(0.65)
    p.paragraph_format.first_line_indent = Cm(-0.3)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.18
    r = p.add_run("• ")
    set_run_font(r, size=10.5, color=BLUE)
    r2 = p.add_run(text)
    set_run_font(r2, size=10.5)
    return p


def add_feature_table(doc, rows):
    table = doc.add_table(rows=1, cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    widths = [Cm(3.4), Cm(5.2), Cm(7.2)]
    headers = ["功能板块", "核心能力", "客户价值"]
    for idx, cell in enumerate(table.rows[0].cells):
        cell.width = widths[idx]
        set_cell_shading(cell, LIGHT_BLUE)
        set_cell_text(cell, headers[idx], bold=True, color=DEEP_BLUE, size=10)
    for row in rows:
        cells = table.add_row().cells
        for idx, value in enumerate(row):
            cells[idx].width = widths[idx]
            set_cell_text(cells[idx], value, size=9.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return table


def add_kv_table(doc, rows):
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for key, value in rows:
        cells = table.add_row().cells
        cells[0].width = Cm(4.2)
        cells[1].width = Cm(11.6)
        set_cell_shading(cells[0], LIGHT_GRAY)
        set_cell_text(cells[0], key, bold=True, color=DEEP_BLUE, size=9.5)
        set_cell_text(cells[1], value, size=9.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
    return table


def setup_document():
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(0.82)
    section.right_margin = Inches(0.82)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal.font.size = Pt(10.5)

    header = section.header.paragraphs[0]
    header.text = "村务综合管理系统 V2.45 | 客户介绍"
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for run in header.runs:
        set_run_font(run, size=8.5, color=MUTED)

    footer = section.footer.paragraphs[0]
    footer.text = "适用于村级事务数字化管理、基层信息台账、便民服务和局域网部署场景"
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in footer.runs:
        set_run_font(run, size=8, color=MUTED)
    return doc


def build_doc():
    doc = setup_document()

    add_paragraph(doc, "村务综合管理系统", size=26, bold=True, color=DEEP_BLUE, after=4, align=WD_ALIGN_PARAGRAPH.CENTER)
    add_paragraph(doc, "面向村级事务、居民信息、便民服务和基层治理的一体化管理平台", size=13.5, color=MUTED, after=12, align=WD_ALIGN_PARAGRAPH.CENTER)

    table = doc.add_table(rows=1, cols=4)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    labels = [("居民台账", "集中管理"), ("医保缴费", "年度统计"), ("综治调解", "闭环留痕"), ("局域网部署", "授权访问")]
    for i, (top, bottom) in enumerate(labels):
        cell = table.rows[0].cells[i]
        set_cell_shading(cell, LIGHT_BLUE)
        set_cell_text(cell, f"{top}\n{bottom}", bold=True, color=DEEP_BLUE, size=10)

    add_heading(doc, "一、系统定位", 1)
    add_paragraph(
        doc,
        "村务综合管理系统是一套面向基层村级组织的本地化管理软件，围绕“居民基础信息、民生对象管理、医保缴费台账、证明开具、综治调解、公益岗位、会议党建、数据备份、权限管理”等日常工作，帮助村级工作人员把分散台账、纸质资料和重复统计工作统一到一个系统中管理。",
        after=8,
    )
    add_paragraph(
        doc,
        "系统支持单机使用，也支持经授权后的局域网多电脑访问，适合村委办公室、便民服务窗口、网格员协同录入和多岗位查询使用。",
        after=8,
    )

    add_heading(doc, "二、重点功能亮点", 1)
    add_feature_table(
        doc,
        [
            ("居民信息管理", "居民档案、身份证号码、所在组、家庭关系、人员状态等信息集中维护。", "形成统一居民基础库，减少重复录入，为医保、证明、调解等模块提供数据关联。"),
            ("医保缴费管理", "支持医保部门名单导入、居民信息自动匹配、年度筛选、缴费状态查询、批量设置已缴费、结果导出。", "便于掌握当前年度缴费进度、未缴人员、各组缴费比例和核减情况。"),
            ("证明开具", "支持常用证明开具、A4 正规排版、打印和导出。", "提升便民服务效率，减少手工编辑证明格式的时间。"),
            ("综治调解", "新增案件、详情、编辑、办结、图片附件上传和案件资料留存。", "方便矛盾纠纷处理全过程留痕，便于后续查询和汇总。"),
            ("公益岗位管理", "岗位类型、所在组等字典化查询，支持人员台账和岗位信息维护。", "提高公益岗人员管理规范性，减少表格分散维护。"),
            ("数据看板", "首页展示居民、医保缴费、各组缴费比例、核减人数等关键数据。", "管理人员进入系统即可快速掌握重点工作进展。"),
        ],
    )

    add_heading(doc, "三、围绕基层日常工作的完整模块", 1)
    add_bullet(doc, "居民管理：建立村级基础人口信息库，支持按姓名、身份证号、所在组等条件查询。")
    add_bullet(doc, "民政对象管理：低保、残疾、关爱对象、退役军人等对象信息可分类管理。")
    add_bullet(doc, "医保管理：围绕年度缴费、部门名单、居民匹配、缴费状态、批量导入导出开展管理。")
    add_bullet(doc, "基层治理：综治调解、矛盾纠纷、风险等级、办理状态等信息统一归档。")
    add_bullet(doc, "组织与党建：党组织、党员、团员、会议记录、待办事项等工作留痕管理。")
    add_bullet(doc, "证明开具：常用村务证明资料可在系统内维护和输出。")
    add_bullet(doc, "系统设置：角色权限、用户管理、字典分类、授权管理和数据备份等配置集中管理。")

    add_heading(doc, "四、医保缴费管理能力", 1)
    add_paragraph(doc, "医保缴费是村级年度工作中高频且容易反复核对的事项，系统将医保名单、居民库和缴费状态结合起来，形成可查询、可统计、可导出的年度台账。")
    add_kv_table(
        doc,
        [
            ("年度管理", "首页和医保页面均可围绕当前年度查看，也可按年度切换统计口径。"),
            ("名单导入", "支持按模板批量导入医保部门下发名单，减少手工录入。"),
            ("居民匹配", "根据姓名、身份证号码、所在组等字段自动关联居民基础信息。"),
            ("缴费处理", "支持查询未缴/已缴，勾选或全选后批量设置缴费状态。"),
            ("统计导出", "查询结果可导出，便于上报、核对和线下留档。"),
        ],
    )

    add_heading(doc, "五、便民服务与材料输出", 1)
    add_paragraph(doc, "系统内置证明开具和打印导出能力，已按 A4 纸张进行正规排版。工作人员可基于居民信息快速生成证明材料，减少重复输入姓名、身份证号、所在组等基础信息。")
    add_bullet(doc, "支持证明内容打印，适合窗口现场办理。")
    add_bullet(doc, "支持导出文件，便于留存和转发。")
    add_bullet(doc, "排版统一，减少手工调整格式带来的差异。")

    add_heading(doc, "六、权限、授权与部署方式", 1)
    add_kv_table(
        doc,
        [
            ("账号权限", "支持管理员、角色和权限控制，不同岗位可按职责访问对应功能。"),
            ("授权管理", "未授权时仅允许服务端电脑本机访问；导入授权后才开放局域网访问。"),
            ("部署方式", "支持 Windows 本地安装，数据保存在当前 Windows 用户目录，便于备份和迁移。"),
            ("局域网客户端", "其他电脑可使用独立客户端输入服务器 IP 和端口，连接后打开登录页面。"),
            ("数据安全", "提供数据备份目录和日志目录，便于日常维护和问题排查。"),
        ],
    )

    add_heading(doc, "七、适用场景", 1)
    add_bullet(doc, "村委会日常居民信息台账维护。")
    add_bullet(doc, "年度医保缴费名单核对、缴费状态统计和分组进度管理。")
    add_bullet(doc, "便民服务窗口快速开具证明。")
    add_bullet(doc, "综治调解、矛盾纠纷和案件附件资料归档。")
    add_bullet(doc, "村级多岗位在同一局域网内协同查询和录入。")

    add_heading(doc, "八、系统价值", 1)
    add_paragraph(
        doc,
        "通过村务综合管理系统，村级工作人员可以把多个 Excel 表、纸质资料、临时统计和重复录入工作集中到统一平台中处理。系统既保留基层工作中常用的查询、导入、导出和打印习惯，又通过居民库关联、字典管理、数据看板和权限控制提升规范性。对于需要多电脑协同的场景，可通过授权方式开放局域网访问，在兼顾便利性的同时保留授权边界。",
        after=8,
    )
    add_paragraph(doc, "版本：V2.45", size=9, color=MUTED, after=0, align=WD_ALIGN_PARAGRAPH.RIGHT)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    build_doc()
