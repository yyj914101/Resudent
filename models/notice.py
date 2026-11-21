# -*- coding: utf-8 -*-
"""models.notice — 补贴公示（045）：模板 + 月度记录 两模型与表格数据契约。

表格数据契约（table_schema / table_data 同构，前端编辑器与上传解析共用）：
    {"columns": [{"key": "c1", "label": "姓名", "width": 120}, ...],
     "rows": [["张三", "男", ...], ...]}

行是**数组**，按 columns 顺序对齐；不支合并单元格/公式（拍板⑤）。
本模块是表格语义的唯一 owner：归一化、默认结构、上传列名匹配都在这里，
路由只做 HTTP 映射（AGENTS §7 / codebase-design：深模块 + 小接口）。
"""
import json

from . import db
from datetime import datetime


# ── 十类内置模板定义（单一真源：种子与「新建默认结构」共用） ──────────────
# 分组仅供页面左侧分类列表使用（方案 A：侧栏 1 项，页内 3 组折叠）。
NOTICE_GROUPS = [
    ('救助类', [
        ('dibao', '低保补助人员明细公示'),
        ('special_support', '特困供养人员明细表'),
        ('temp_relief', '临时救助人员明细表'),
        ('disabled_living', '困难残疾人生活补贴人员明细表'),
    ]),
    ('福利类', [
        ('elderly', '高龄补贴人员明细表'),
        ('housing', '季度住房租赁补贴家庭明细表'),
        ('nursing', '重度残疾人护理人员明细表'),
        ('planning', '计划生育奖励扶助人员明细表'),
    ]),
    ('优抚就业类', [
        ('veteran', '优抚恤人员明细表'),
        ('public_welfare', '公益性岗位人员明细表'),
    ]),
]

NOTICE_TYPE_NAMES = {key: name for _g, items in NOTICE_GROUPS for key, name in items}

DEFAULT_TEMPLATES = {
    'dibao': ['序号', '户主姓名', '家庭人口', '保障类别', '月补助金额（元）', '备注'],
    'special_support': ['序号', '姓名', '性别', '供养方式', '供养标准（元/月）', '备注'],
    'temp_relief': ['序号', '姓名', '性别', '救助原因', '救助金额（元）', '救助时间', '备注'],
    'disabled_living': ['序号', '姓名', '性别', '残疾等级', '生活补贴（元/月）', '备注'],
    'elderly': ['序号', '姓名', '性别', '年龄', '补贴标准（元/月）', '备注'],
    'housing': ['序号', '户主姓名', '家庭人口', '补贴面积（㎡）', '补贴金额（元/季）', '备注'],
    'nursing': ['序号', '姓名', '性别', '残疾类别', '残疾等级', '护理补贴（元/月）', '备注'],
    'planning': ['序号', '姓名', '性别', '扶助类型', '奖励标准（元/年）', '备注'],
    'veteran': ['序号', '姓名', '性别', '优抚类别', '抚恤标准（元/月）', '备注'],
    'public_welfare': ['序号', '姓名', '性别', '岗位类型', '上岗时间', '月补贴（元）', '备注'],
}


def _json_load(value, default):
    try:
        parsed = json.loads(value or '')
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def default_table(notice_type):
    """按类型生成默认表格结构（表头 + 1 个空行）。"""
    labels = DEFAULT_TEMPLATES.get(notice_type) or ['序号', '姓名', '金额（元）', '备注']
    columns = [{'key': f'c{i + 1}', 'label': label, 'width': 120}
               for i, label in enumerate(labels)]
    return {'columns': columns, 'rows': [[''] * len(columns)]}


def normalize_table(value, notice_type='dibao'):
    """接受 dict / JSON 字符串，返回合法表格结构；任何畸形输入退回默认结构。

    接口契约：columns 至少 1 列；每行长度按列数截断/补齐；label 去空白且非空。
    """
    if isinstance(value, str):
        value = _json_load(value, {})
    if not isinstance(value, dict):
        return default_table(notice_type)
    raw_cols = value.get('columns')
    if not isinstance(raw_cols, list) or not raw_cols:
        return default_table(notice_type)
    columns = []
    for i, col in enumerate(raw_cols):
        col = col if isinstance(col, dict) else {}
        label = str(col.get('label') or '').strip() or f'列{i + 1}'
        try:
            width = int(col.get('width') or 120)
        except (TypeError, ValueError):
            width = 120
        columns.append({'key': str(col.get('key') or f'c{i + 1}'),
                        'label': label, 'width': max(40, min(width, 600))})
    n = len(columns)
    rows = []
    for row in value.get('rows') or []:
        if not isinstance(row, list):
            continue
        cells = ['' if c is None else str(c) for c in row][:n]
        cells += [''] * (n - len(cells))
        rows.append(cells)
    return {'columns': columns, 'rows': rows}


def match_upload_rows(columns, headers, raw_rows):
    """上传文件表头 → 模板列匹配（纯函数，路由只负责解析文件）。

    返回 {'rows': [...], 'matched': [列序号], 'unmatched': [文件表头]}。
    规则：表头去空白后与 columns[].label 精确匹配；未匹配列不填充（不追加新列）。
    """
    labels = [str((c or {}).get('label') or '').strip() for c in columns]
    index = {}
    for i, label in enumerate(labels):
        index.setdefault(label, i)
    matched = {}
    unmatched = []
    for h_idx, header in enumerate(headers or []):
        label = str(header or '').strip()
        if label in index and index[label] not in matched:
            matched[index[label]] = h_idx
        elif label:
            unmatched.append(label)
    rows = []
    for raw in raw_rows or []:
        cells = [''] * len(labels)
        for col_idx, h_idx in matched.items():
            if h_idx < len(raw):
                cells[col_idx] = '' if raw[h_idx] is None else str(raw[h_idx])
        if any(c.strip() for c in cells):
            rows.append(cells)
    return {'rows': rows, 'matched': sorted(matched), 'unmatched': unmatched}


class NoticeTemplate(db.Model):
    __tablename__ = 'notice_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    notice_type = db.Column(db.String(30), index=True)
    title = db.Column(db.String(150))          # 自定义表名（打印/记录渲染用）
    table_schema = db.Column(db.Text, default='{}')
    is_custom = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    version = db.Column(db.Integer, default=1)
    created_by = db.Column(db.String(50))
    updated_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def schema_dict(self):
        return normalize_table(self.table_schema, self.notice_type or 'dibao')


class NoticeRecord(db.Model):
    __tablename__ = 'notice_records'
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('notice_templates.id'), index=True)
    notice_type = db.Column(db.String(30), index=True)
    period = db.Column(db.String(7), index=True)      # YYYY-MM
    title = db.Column(db.String(150))                 # 生成时快照
    table_data = db.Column(db.Text, default='{}')
    status = db.Column(db.String(20), default='draft')
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def data_dict(self):
        return normalize_table(self.table_data, self.notice_type or 'dibao')


def ensure_notice_seed():
    """一次性幂等种子：补齐 10 类内置模板（按 notice_type 守卫，可重跑）。

    只做 SELECT/INSERT，不建表、不加列（DDL 见 scripts/migrations/m016_notice.sql）。
    表未建时静默返回 0，不阻断请求/启动。
    """
    try:
        existing = {t.notice_type for t in NoticeTemplate.query.all()}
    except Exception:
        db.session.rollback()
        return 0
    added = 0
    for _group, items in NOTICE_GROUPS:
        for notice_type, name in items:
            if notice_type in existing:
                continue
            db.session.add(NoticeTemplate(
                name=name, notice_type=notice_type, title=name,
                table_schema=json.dumps(default_table(notice_type), ensure_ascii=False),
                is_custom=False, is_active=True, version=1))
            added += 1
    if added:
        db.session.commit()
    return added
