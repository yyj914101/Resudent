# -*- coding: utf-8 -*-
"""models.notice — 补贴公示（045）：模板 + 月度记录 两模型与表格数据契约。

表格数据契约（table_schema / table_data 同构，前端编辑器与上传解析共用）：
    {
      "columns": [{"key": "c1", "label": "户主姓名", "width": 112}, ...],
      "rows": [["张三", "2", "800", ...], ...],
      "layout": "tri_column",
      "header_title": "补助资金公开表",
      "subtitle_tpl": "{village}{year}年{month}月享受低保补助人员明细公示",
      "village_name": "石垭社区",
      "footer_notes": ["监督咨询电话：..."]
    }

行是**数组**，按 columns 顺序对齐；支持 9 列三栏并排公示表（3 栏 × 3 列）与自定义列扩展。
本模块是表格语义的唯一 owner：归一化、默认结构、年月标题渲染、导出文件名、上传列名匹配都在这里，
路由只做 HTTP 映射（AGENTS §7 / codebase-design：深模块 + 小接口）。
"""
import json
import re
import uuid
from datetime import datetime

from . import db

DEFAULT_VILLAGE_NAME = '石垭社区'
DEFAULT_HEADER_TITLE = '补助资金公开表'

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
        ('nursing', '重度残疾人护理补贴人员明细表'),
        ('planning', '计划生育奖励扶助人员明细表'),
    ]),
    ('优抚就业类', [
        ('veteran', '优待抚恤人员明细表'),
        ('public_welfare', '公益性岗位人员明细表'),
    ]),
]

NOTICE_TYPE_NAMES = {key: name for _g, items in NOTICE_GROUPS for key, name in items}
_CATALOG_SETTING_KEY = 'notice_custom_catalog'


def load_custom_catalog():
    """从系统设置 DictItem 读取自定义公示分类与自定义公示表目录（零数据库迁移）。"""
    default = {'groups': [], 'types': []}
    try:
        from models import DictCategory, DictItem
        item = DictItem.query.join(DictCategory).filter(
            DictCategory.name == '系统设置',
            DictItem.label == _CATALOG_SETTING_KEY,
        ).first()
        if not item or not item.value:
            return default
        parsed = json.loads(item.value)
        if not isinstance(parsed, dict):
            return default
        groups = [str(g).strip() for g in (parsed.get('groups') or []) if str(g or '').strip()]
        types = []
        for t in (parsed.get('types') or []):
            if not isinstance(t, dict):
                continue
            k = str(t.get('key') or '').strip()
            n = str(t.get('name') or '').strip()
            g = str(t.get('group') or '').strip()
            if k and n and g:
                types.append({
                    'key': k[:30],
                    'name': n[:100],
                    'group': g[:40],
                    'preset': 'dibao' if t.get('preset') == 'dibao' else 'common',
                })
        return {'groups': groups, 'types': types}
    except Exception:
        return default


def save_custom_catalog(catalog):
    """持久化自定义公示分类与自定义公示表目录到系统设置。"""
    from models import DictCategory, DictItem
    cat = DictCategory.query.filter_by(name='系统设置').first()
    if not cat:
        cat = DictCategory(name='系统设置', is_system=True)
        db.session.add(cat)
        db.session.flush()
    item = DictItem.query.filter_by(category_id=cat.id, label=_CATALOG_SETTING_KEY).first()
    payload = json.dumps({
        'groups': list(dict.fromkeys(catalog.get('groups') or [])),
        'types': list(catalog.get('types') or []),
    }, ensure_ascii=False)
    if not item:
        item = DictItem(category_id=cat.id, label=_CATALOG_SETTING_KEY, value=payload, sort_order=99)
        db.session.add(item)
    else:
        item.value = payload
    db.session.commit()


def get_all_notice_groups():
    """返回合并后的全部分类分组（内置 3 组 + 自定义分组，每组内含内置与自定义公示表）。"""
    ordered = []
    by_label = {}
    for label, items in NOTICE_GROUPS:
        entry = [label, list(items)]
        ordered.append(entry)
        by_label[label] = entry

    custom = load_custom_catalog()
    for g in custom['groups']:
        if g not in by_label:
            entry = [g, []]
            ordered.append(entry)
            by_label[g] = entry

    seen_keys = {k for _l, items in ordered for k, _n in items}
    for t in custom['types']:
        if t['key'] in seen_keys:
            continue
        if t['group'] not in by_label:
            entry = [t['group'], []]
            ordered.append(entry)
            by_label[t['group']] = entry
        by_label[t['group']][1].append((t['key'], t['name']))
        seen_keys.add(t['key'])

    return [(label, items) for label, items in ordered]


def get_all_notice_type_names():
    """返回内置 + 自定义全部公示类型的 {key: name} 映射。"""
    return {key: name for _g, items in get_all_notice_groups() for key, name in items}


def add_custom_group(group_name):
    """新增自定义公示分类分组（如「资产类」）。"""
    name = str(group_name or '').strip()[:40]
    if not name:
        raise ValueError('请输入分类名称')
    existing_labels = {label for label, _items in get_all_notice_groups()}
    if name in existing_labels:
        raise ValueError(f'分类「{name}」已存在')
    catalog = load_custom_catalog()
    catalog['groups'].append(name)
    save_custom_catalog(catalog)
    return name


def add_custom_notice_type(group_name, table_name, preset='common'):
    """在指定分类（支持已有分类或新分类）下新增一张自定义公示表。"""
    g_name = str(group_name or '').strip()[:40]
    t_name = str(table_name or '').strip()[:100]
    if not g_name:
        raise ValueError('请选择或输入所属分类')
    if not t_name:
        raise ValueError('请输入公示表名称')
    all_names = get_all_notice_type_names()
    if t_name in all_names.values():
        raise ValueError(f'公示表「{t_name}」已存在')
    catalog = load_custom_catalog()
    builtin_labels = {label for label, _items in NOTICE_GROUPS}
    if g_name not in builtin_labels and g_name not in catalog['groups']:
        catalog['groups'].append(g_name)
    key = 'cst_' + uuid.uuid4().hex[:8]
    p_key = 'dibao' if preset == 'dibao' else 'common'
    catalog['types'].append({'key': key, 'name': t_name, 'group': g_name, 'preset': p_key})
    save_custom_catalog(catalog)
    return {'key': key, 'name': t_name, 'group': g_name, 'preset': p_key}


def remove_custom_notice_type(notice_type):
    """删除自定义公示类型（仅当其为 cst_ 前缀且无模板/记录时调用）。"""
    if not str(notice_type or '').startswith('cst_'):
        return
    catalog = load_custom_catalog()
    before = len(catalog['types'])
    catalog['types'] = [t for t in catalog['types'] if t.get('key') != notice_type]
    if len(catalog['types']) != before:
        save_custom_catalog(catalog)

# 历史旧版名称映射（用于老库内置模板平滑更正）
LEGACY_TYPE_NAMES = {
    'nursing': '重度残疾人护理人员明细表',
    'veteran': '优抚恤人员明细表',
}

# 历史旧版单栏默认表头（用于识别未修改过的老库种子并自动升级为三栏公示表）
LEGACY_DEFAULT_TEMPLATES = {
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

# ── 真实公示栏三栏并排（9 列）标准定义 ────────────────────────────────────
_DIBAO_UNIT_COLS = [
    ('户主姓名', 112),
    ('保障\n人口数', 76),
    ('保障金额\n（元）', 102),
]

_COMMON_UNIT_COLS = [
    ('组别', 68),
    ('户主姓名', 118),
    ('补助金额\n（元）', 106),
]

DEFAULT_TEMPLATES = {
    'dibao': [label for _ in range(3) for label, _w in _DIBAO_UNIT_COLS],
    'special_support': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
    'temp_relief': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
    'disabled_living': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
    'elderly': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
    'housing': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
    'nursing': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
    'planning': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
    'veteran': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
    'public_welfare': [label for _ in range(3) for label, _w in _COMMON_UNIT_COLS],
}

DEFAULT_COL_WIDTHS = {
    'dibao': [w for _ in range(3) for _label, w in _DIBAO_UNIT_COLS],
}

DEFAULT_SUBTITLE_TPLS = {
    'dibao': '{village}{year}年{month}月享受低保补助人员明细公示',
}

DEFAULT_FOOTER_NOTES = {
    'dibao': [
        '可自定义编辑',
        '监督咨询电话：农村低保7228309；城市低保：7215906；\n'
        '             县低保中心办公室：7228380\n'
        '             乡镇（街道）监督举报电话：7488718',
    ],
    'elderly': [
        '按上级文件要求，从10月1日起，高龄津贴按照新标准执行：'
        '80-89周岁，每人每月40元；90-99周岁，每人每月120元；'
        '100周岁及以上，每人每月300元；',
    ],
}

_PERIOD_PARTS_RE = re.compile(r'^(\d{4})-(0[1-9]|1[0-2])$')
_CN_YM_RE = re.compile(r'\d{4}年\d{1,2}月')
_FILENAME_BAD_RE = re.compile(r'[\\/:*?"<>|\r\n]+')


def parse_period(period):
    """解析 YYYY-MM → (year: int, month: int)；非法时回退当前年月。"""
    m = _PERIOD_PARTS_RE.match(str(period or '').strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    now = datetime.now()
    return now.year, now.month


def format_period_cn(period):
    """'2026-10' → '2026年10月'，'2026-05' → '2026年5月'。"""
    year, month = parse_period(period)
    return f'{year}年{month}月'


def default_subtitle_tpl(notice_type):
    if notice_type in DEFAULT_SUBTITLE_TPLS:
        return DEFAULT_SUBTITLE_TPLS[notice_type]
    name = get_all_notice_type_names().get(notice_type) or '补贴公示'
    return f'{{village}}{{year}}年{{month}}月{name}'


def render_subtitle(notice_type, period, village_name='', subtitle_tpl=''):
    """渲染真实公示表副标题，如：石垭社区2026年10月享受低保补助人员明细公示。"""
    year, month = parse_period(period)
    village = (village_name or '').strip() or DEFAULT_VILLAGE_NAME
    tpl = (subtitle_tpl or '').strip() or default_subtitle_tpl(notice_type)
    try:
        return tpl.format(village=village, year=year, month=month)
    except Exception:
        name = get_all_notice_type_names().get(notice_type) or '补贴公示'
        return f'{village}{year}年{month}月{name}'


def update_subtitle_period(title, new_period, notice_type='', village_name='', subtitle_tpl=''):
    """更改年月时联动更新副标题：保留用户自定义社区名/措辞，仅替换其中的 y年m月。"""
    new_cn = format_period_cn(new_period)
    raw = str(title or '').strip()
    if raw and _CN_YM_RE.search(raw):
        return _CN_YM_RE.sub(new_cn, raw, count=1)
    return render_subtitle(notice_type, new_period, village_name=village_name, subtitle_tpl=subtitle_tpl)


def build_export_filename(notice_type, period, fallback_name='', fmt='xlsx'):
    """导出文件名：y年m月+对应台账名.{xlsx|xls}（如 2026年10月低保补助人员明细公示.xlsx）。"""
    ext = 'xls' if str(fmt or '').lower() == 'xls' else 'xlsx'
    period_cn = format_period_cn(period)
    ledger_name = (get_all_notice_type_names().get(notice_type) or fallback_name or '补贴公示').strip()
    ledger_name = _FILENAME_BAD_RE.sub('_', ledger_name).strip('_') or '补贴公示'
    return f'{period_cn}{ledger_name}.{ext}'


def _norm_label(label):
    """列名归一化比对：去除所有空白与换行，统一全角括号。"""
    s = re.sub(r'\s+', '', str(label or ''))
    return s.replace('(', '（').replace(')', '）')


def _json_load(value, default):
    try:
        parsed = json.loads(value or '')
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def default_table(notice_type, village_name='', preset=''):
    """按类型生成真实公示栏默认表格结构（9 列三栏表头 + 1 个空行 + 抬头与表尾备注）。"""
    eff_type = 'dibao' if preset == 'dibao' else notice_type
    labels = DEFAULT_TEMPLATES.get(eff_type) or [
        label for _ in range(3) for label, _w in _COMMON_UNIT_COLS
    ]
    widths = DEFAULT_COL_WIDTHS.get(eff_type) or [
        w for _ in range(3) for _label, w in _COMMON_UNIT_COLS
    ]
    columns = [
        {'key': f'c{i + 1}', 'label': label,
         'width': widths[i] if i < len(widths) else 110}
        for i, label in enumerate(labels)
    ]
    notes = list(DEFAULT_FOOTER_NOTES.get(notice_type, ['']))
    return {
        'columns': columns,
        'rows': [[''] * len(columns)],
        'layout': 'tri_column' if len(columns) == 9 else 'standard',
        'page_size': 'a4_portrait',
        'header_title': DEFAULT_HEADER_TITLE,
        'subtitle_tpl': default_subtitle_tpl(notice_type),
        'village_name': (village_name or '').strip() or DEFAULT_VILLAGE_NAME,
        'footer_notes': notes,
    }


def _detect_layout(columns, raw_layout=''):
    if raw_layout in ('tri_column', 'standard'):
        return raw_layout
    if len(columns) == 9:
        g1 = [_norm_label(c['label']) for c in columns[:3]]
        g2 = [_norm_label(c['label']) for c in columns[3:6]]
        g3 = [_norm_label(c['label']) for c in columns[6:9]]
        if g1 == g2 == g3:
            return 'tri_column'
    return 'standard'


def normalize_table(value, notice_type='dibao', village_name=''):
    """接受 dict / JSON 字符串，返回合法表格结构；任何畸形输入退回默认结构。

    接口契约：columns 至少 1 列；每行长度按列数截断/补齐；label 去空白且非空；
    保留 header_title / subtitle_tpl / village_name / footer_notes / layout 扩展元数据。
    """
    if isinstance(value, str):
        value = _json_load(value, {})
    if not isinstance(value, dict):
        return default_table(notice_type, village_name=village_name)
    raw_cols = value.get('columns')
    if not isinstance(raw_cols, list) or not raw_cols:
        return default_table(notice_type, village_name=village_name)
    columns = []
    for i, col in enumerate(raw_cols):
        col = col if isinstance(col, dict) else {}
        label = str(col.get('label') or '').strip() or f'列{i + 1}'
        try:
            width = int(col.get('width') or 120)
        except (TypeError, ValueError):
            width = 120
        columns.append({'key': str(col.get('key') or f'c{i + 1}'),
                        'label': label, 'width': max(36, min(width, 600))})
    n = len(columns)
    rows = []
    for row in value.get('rows') or []:
        if not isinstance(row, list):
            continue
        cells = ['' if c is None else str(c) for c in row][:n]
        cells += [''] * (n - len(cells))
        rows.append(cells)

    header_title = str(value.get('header_title') or '').strip() or DEFAULT_HEADER_TITLE
    subtitle_tpl = str(value.get('subtitle_tpl') or '').strip() or default_subtitle_tpl(notice_type)
    v_name = str(value.get('village_name') or '').strip() or (village_name or '').strip() or DEFAULT_VILLAGE_NAME
    raw_notes = value.get('footer_notes')
    if isinstance(raw_notes, list):
        footer_notes = [str(x) for x in raw_notes[:10]]
    elif isinstance(raw_notes, str):
        footer_notes = [raw_notes]
    else:
        footer_notes = list(DEFAULT_FOOTER_NOTES.get(notice_type, []))

    page_size = str(value.get('page_size') or 'a4_portrait').strip()
    if page_size not in ('a4_portrait', 'a4_landscape', 'auto'):
        page_size = 'a4_portrait'

    return {
        'columns': columns,
        'rows': rows,
        'layout': _detect_layout(columns, str(value.get('layout') or '').strip()),
        'page_size': page_size,
        'header_title': header_title,
        'subtitle_tpl': subtitle_tpl,
        'village_name': v_name,
        'footer_notes': footer_notes,
    }


def match_upload_rows(columns, headers, raw_rows):
    """上传文件表头 → 模板列匹配（纯函数，路由只负责解析文件）。

    支持两种上传形态：
    1) 直接匹配（含 9 列三栏重复表头，按出现顺序依次对齐 3 栏）；
    2) 单栏明细表自动折行填充到 9 列三栏模板（如上传 3 列「组别/户主姓名/补助金额（元）」自动每 3 户拼 1 行）。

    返回 {'rows': [...], 'matched': [列序号], 'unmatched': [文件表头]}。
    """
    col_norms = [_norm_label((c or {}).get('label')) for c in columns]
    hdr_norms = [_norm_label(h) for h in (headers or [])]

    # 判断模板是否为 9 列三栏（3 组相同 3 列表头）
    is_tri_tpl = (
        len(col_norms) == 9
        and col_norms[:3] == col_norms[3:6] == col_norms[6:9]
    )

    # 顺序匹配：同一列名在三栏中出现多次时，按顺序依次绑定到第 1/2/3 栏
    buckets = {}
    for i, norm in enumerate(col_norms):
        buckets.setdefault(norm, []).append(i)

    matched = {}  # col_idx -> h_idx
    unmatched = []
    for h_idx, raw_h in enumerate(headers or []):
        h_norm = hdr_norms[h_idx]
        if h_norm in buckets and buckets[h_norm]:
            col_idx = buckets[h_norm].pop(0)
            matched[col_idx] = h_idx
        elif str(raw_h or '').strip():
            unmatched.append(str(raw_h).strip())

    # 情况 2：模板是 9 列三栏，但上传文件只有单栏（仅命中前 3 列且未命中第 2/3 栏）→ 自动折行填满三栏
    if is_tri_tpl and matched and all(ci < 3 for ci in matched):
        unit_items = []
        for raw in raw_rows or []:
            unit = ['', '', '']
            for col_idx, h_idx in matched.items():
                if h_idx < len(raw):
                    unit[col_idx] = '' if raw[h_idx] is None else str(raw[h_idx]).strip()
            if any(c for c in unit):
                unit_items.append(unit)
        rows = []
        for i in range(0, len(unit_items), 3):
            chunk = unit_items[i:i + 3]
            row = []
            for slot in range(3):
                row.extend(chunk[slot] if slot < len(chunk) else ['', '', ''])
            rows.append(row)
        return {'rows': rows, 'matched': sorted(matched), 'unmatched': unmatched}

    # 情况 1：常规逐列匹配（含 9 列三栏直接导入）
    rows = []
    for raw in raw_rows or []:
        cells = [''] * len(col_norms)
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
    title = db.Column(db.String(150))                 # 生成时快照（如：石垭社区2026年10月享受低保补助人员明细公示）
    table_data = db.Column(db.Text, default='{}')
    status = db.Column(db.String(20), default='draft')
    created_by = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def data_dict(self):
        return normalize_table(self.table_data, self.notice_type or 'dibao')


def _is_legacy_untouched_template(tpl):
    """判断是否为旧版未改动的内置模板，便于自动平滑升级为真实三栏公示表。"""
    if tpl.is_custom or (tpl.version or 1) > 1:
        return False
    legacy_labels = LEGACY_DEFAULT_TEMPLATES.get(tpl.notice_type)
    if not legacy_labels:
        return False
    parsed = _json_load(tpl.table_schema, {})
    cols = parsed.get('columns') if isinstance(parsed, dict) else None
    if not isinstance(cols, list):
        return False
    labels = [str((c or {}).get('label') or '').strip() for c in cols]
    return labels == legacy_labels


def ensure_notice_seed():
    """一次性幂等种子：补齐 10 类内置模板，并平滑升级旧版未修改的内置默认模板。

    只做 SELECT/INSERT/UPDATE，不建表、不加列（DDL 见 scripts/migrations/m016_notice.sql）。
    表未建时静默返回 0，不阻断请求/启动。
    """
    try:
        all_items = NoticeTemplate.query.all()
    except Exception:
        db.session.rollback()
        return 0

    existing = {t.notice_type for t in all_items}
    changed = 0

    # 平滑升级老库未改动过的旧内置模板 + 修正错别字名称
    for tpl in all_items:
        canonical_name = NOTICE_TYPE_NAMES.get(tpl.notice_type)
        legacy_name = LEGACY_TYPE_NAMES.get(tpl.notice_type)
        if canonical_name and legacy_name:
            if tpl.name == legacy_name:
                tpl.name = canonical_name
                changed += 1
            if tpl.title == legacy_name:
                tpl.title = canonical_name
                changed += 1
        if _is_legacy_untouched_template(tpl):
            tpl.table_schema = json.dumps(default_table(tpl.notice_type), ensure_ascii=False)
            changed += 1

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

    if added or changed:
        db.session.commit()
    return added
