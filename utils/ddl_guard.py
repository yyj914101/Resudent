# -*- coding: utf-8 -*-
"""utils.ddl_guard — 数据库管理面板安全基建（025，owner=utils/）。

为 025（🟢只读体检 + 🟡低风险操作）提供全部安全闸，并为 026（🔴受控 DDL）
沉淀 rebuid_table（即 copy_table 的「改列重建」子集）可复用的底层：
- validate_identifier()   DDL 标识符白名单（沿用 API-06 正则）+ SQLite 关键字黑名单
- is_app_table()          应用表识别（db.metadata.tables 比对——代码硬编码表名）
- preflight_backup()      结构写操作前置 `.wbak` 加密备份钩子（失败即中止）
- plan() / execute()      黄级操作两段式：先出 SQL 计划（前端确认弹窗展示），再执行
- ensure_audit_table() / audit()   ddl_audit_log 幂等建表（DDL 真源 = m011 SQL 文件）+ 审计落库

红线（任务书 025 + ADR-018）：
- 应用表一律只读：加字段/建删索引/重命名等结构变更硬拒绝并引导轨 A；
  复制表对应用表仅允许作为只读源（副本落为面板自建的非应用表）。
- 凡写结构（🟡级 + VACUUM）执行前必须先完成 .wbak 备份且成功，备份失败即中止。
- 技术预验证结论（2026-10-14，SQLite 3.45.1）：dbstat 虚拟表可用；sqlite3 iterdump
  无 filter 参数 → 复制表走 PRAGMA + sqlite_master 重建路径（本模块实现）。
"""
from __future__ import annotations

import json
import os
import re

from sqlalchemy import text

from models import db

# 021 API-06 口径沿用：DDL 标识符白名单（小写字母/数字/下划线开头非数字）
_IDENT_RE = re.compile(r'^[a-z_][a-z0-9_]*$')

# SQLite 关键字黑名单（纵深防御：正则已挡引号/空格/注释符，再挡语义关键字）
_SQLITE_KEYWORDS = {
    'select', 'insert', 'update', 'delete', 'drop', 'create', 'alter', 'table',
    'index', 'view', 'trigger', 'from', 'where', 'join', 'into', 'values',
    'set', 'order', 'group', 'by', 'having', 'union', 'except', 'intersect',
    'and', 'or', 'not', 'null', 'primary', 'key', 'foreign', 'references',
    'constraint', 'vacuum', 'attach', 'detach', 'begin', 'commit', 'rollback',
    'transaction', 'pragma', 'reindex', 'analyze', 'explain', 'case', 'when',
    'then', 'else', 'end', 'like', 'limit', 'offset', 'as', 'is', 'in', 'on',
    'to', 'default', 'check', 'unique',
}

# 🟡 建表/加字段的列类型白名单（SQLite 亲和类型 + 受限模式；无对应语义一律拒绝）
COLTYPE_RE = re.compile(
    r'^(TEXT|INTEGER|REAL|NUMERIC|BLOB|BOOLEAN|DATETIME|DATE|VARCHAR\(\d{1,4}\))$')

_DEFAULT_NUM_RE = re.compile(r'^-?\d+(\.\d+)?$')
_DEFAULT_QUOTED_RE = re.compile(r"^'(?:[^']|'')*'$")
_DEFAULT_TOKENS = {'CURRENT_TIMESTAMP', 'CURRENT_DATE', 'CURRENT_TIME',
                   'NULL', 'TRUE', 'FALSE'}

AUDIT_TABLE = 'ddl_audit_log'
AUDIT_RETENTION_SHOWN = 50  # 面板页仅展示最近 50 条审计

_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'scripts', 'migrations')
_M011_SQL = os.path.join(_MIGRATIONS_DIR, 'm011_ddl_audit.sql')

APP_TABLE_REFUSE_MSG = (
    '目标为应用表（系统代码使用），面板内一律只读，结构变更已拒绝。'
    '请走轨 A：在 scripts/migrations/ 落 DDL 脚本并由 leader 审核执行。')
AUTOMATIC_INDEX_REFUSE_MSG = (
    '自动索引（主键/唯一约束）由 SQLite 维护，不可删除')


class GuardError(ValueError):
    """面板操作被安全闸拒绝（HTTP 400，msg 直接对用户展示）。"""


# ============ 标识符与表识别 ============

def validate_identifier(name, kind='表'):
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise GuardError(
            f"非法{kind}名称（须匹配 ^[a-z_][a-z0-9_]*$，防注入）：{name!r}")
    if name.lower() in _SQLITE_KEYWORDS:
        raise GuardError(f"非法{kind}名称（SQLite 关键字）：{name!r}")
    if len(name) > 64:
        raise GuardError(f"非法{kind}名称（长度超 64）：{name!r}")
    return name


def table_exists(name):
    validate_identifier(name, '表')
    row = db.session.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"
    ), {'n': name}).first()
    return row is not None


def index_exists(name):
    validate_identifier(name, '索引')
    row = db.session.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=:n"
    ), {'n': name}).first()
    return row is not None


def list_tables():
    rows = db.session.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")).fetchall()
    return [r[0] for r in rows]


def is_app_table(name):
    """应用表识别：SQLAlchemy metadata 注册（模型代码硬编码表名）即为应用表。"""
    return name in db.metadata.tables


# ============ 审计表（DDL 真源 = m011 文件，运行时幂等兜底） ============

def ensure_audit_table():
    """幂等建 ddl_audit_log。DDL 唯一真源为 scripts/migrations/m011_ddl_audit.sql
    （轨 A）；此处仅作存量库运行时兜底（app.py 补列同款先例），不另写 DDL。"""
    if table_exists(AUDIT_TABLE):
        return
    if not os.path.isfile(_M011_SQL):
        raise GuardError(
            '缺少轨 A 迁移脚本 m011_ddl_audit.sql，无法初始化 DDL 审计表')
    with open(_M011_SQL, encoding='utf-8') as f:
        sql = f.read()
    raw = db.engine.raw_connection()
    try:
        raw.executescript(sql)
        raw.commit()
    finally:
        raw.close()


def audit(operator, action, target, sql_text='', before_schema=None,
          after_schema=None, backup_file=None, result='success'):
    """审计落库：ddl_audit_log + OperationLog（user 可见留痕）。返回审计记录 id。"""
    ensure_audit_table()
    db.session.execute(text(
        f'INSERT INTO {AUDIT_TABLE} '
        '(operator, action, target, sql_text, before_schema, after_schema, '
        'backup_file, result) '
        'VALUES (:o, :a, :t, :s, :b, :af, :bk, :r)'
    ), {
        'o': operator or '', 'a': action, 't': target,
        's': (sql_text or '')[:4000],
        'b': _maybe_json(before_schema), 'af': _maybe_json(after_schema),
        'bk': backup_file or '', 'r': result,
    })
    audit_id = db.session.execute(
        text('SELECT last_insert_rowid()')).scalar()
    db.session.commit()
    from utils.helpers import log_operation
    log_operation(operator or '', '数据库管理', action,
                  f'{action} {target}', (sql_text or '')[:500], result)
    return int(audit_id or 0)


def _maybe_json(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


# ============ 前置备份钩子 ============

def preflight_backup(trigger):
    """结构写操作前置 .wbak 加密备份（复用 backup_crypto.create_wbak）。

    备份失败（密钥未解锁/空间不足/预检失败/密钥异常）一律抛 GuardError ⇒ 操作中止。
    返回备份包绝对路径。
    """
    import config as _config
    from utils.backup_crypto import create_wbak
    db_path = _config.DB_PATH
    if not os.path.isfile(db_path):
        raise GuardError('数据库文件不存在，无法前置备份（中止）')
    try:
        wbak_path, _meta = create_wbak(
            db_path, _config.UPLOAD_DIR, _config.BACKUP_DIR,
            meta_extra={'trigger': trigger})
    except ValueError as e:
        raise GuardError(f'前置 .wbak 备份失败，操作已中止：{e}')
    return wbak_path


def last_backup_info():
    from utils.backup_crypto import latest_wbak
    import config as _config
    hit = latest_wbak(_config.BACKUP_DIR)
    if not hit:
        return {'filename': None, 'mtime': None}
    import os as _os
    return {'filename': _os.path.basename(hit[0]), 'mtime': hit[1]}


# ============ 列定义（建表 / 加字段共用，白名单夹持） ============

def _validate_default(value, coltype, notnull):
    """默认值白名单：NULL/CURRENT_*/TRUE/FALSE/数字/引号串/裸字符串（自动加引号转义）。"""
    t = str(value).strip()
    if t == '':
        raise GuardError('默认值不能为空字符串，留空表示无默认值')
    up = t.upper()
    if up in _DEFAULT_TOKENS:
        if notnull and up == 'NULL':
            raise GuardError('NOT NULL 列的默认值不能为 NULL')
        return up
    if _DEFAULT_NUM_RE.match(t):
        return t
    if _DEFAULT_QUOTED_RE.match(t):
        if len(t) > 100:
            raise GuardError('默认值过长（>100）')
        return t
    # 裸字符串 → 单引号包裹 + 内部引号转义
    if len(t) > 50:
        raise GuardError('字符串默认值过长（>50）')
    return "'%s'" % t.replace("'", "''")


def build_column_def(column, for_add=False):
    """单列 DDL 片段生成（白名单校验 + SQLite ADD COLUMN 限制预检）。"""
    if not isinstance(column, dict):
        raise GuardError('列定义格式非法')
    col_name = validate_identifier(column.get('name'), '列')
    col_type = str(column.get('type') or 'TEXT').upper().strip()
    if not COLTYPE_RE.match(col_type):
        raise GuardError(
            '不支持的字段类型：%s（白名单 TEXT/INTEGER/REAL/NUMERIC/BLOB/'
            'VARCHAR(n)/BOOLEAN/DATETIME/DATE）' % col_type)
    is_pk = bool(column.get('pk'))
    notnull = bool(column.get('notnull'))
    default = column.get('default')

    if for_add and is_pk:
        raise GuardError('不支持向已有表添加主键列（SQLite ADD COLUMN 限制）')
    if is_pk and col_type not in ('INTEGER', 'TEXT'):
        raise GuardError('主键列类型仅支持 INTEGER / TEXT')
    if notnull and default in (None, ''):
        raise GuardError('NOT NULL 列必须提供默认值')

    ddl = f'"{col_name}" {col_type}'
    if is_pk:
        ddl += ' PRIMARY KEY'
    elif notnull:
        ddl += ' NOT NULL'
    if default not in (None, ''):
        ddl += ' DEFAULT ' + _validate_default(default, col_type, notnull)
    return ddl


def build_create_table_sql(name, columns):
    """🟡 建表：结构化列清单 → CREATE TABLE（类型白名单 + 单列主键上限 1）。"""
    validate_identifier(name, '表')
    if table_exists(name):
        raise GuardError(f'表已存在：{name}')
    if not isinstance(columns, list) or not (1 <= len(columns) <= 60):
        raise GuardError('列数量须在 1~60 之间')
    seen, pk_count, defs = set(), 0, []
    for c in columns:
        cn = _safe_col_name(c)
        if cn in seen:
            raise GuardError(f'列重复：{cn}')
        seen.add(cn)
        if c.get('pk'):
            pk_count += 1
        defs.append(build_column_def(c))
    if pk_count > 1:
        raise GuardError('仅支持单列主键')
    return f'CREATE TABLE "{name}" (\n  ' + ',\n  '.join(defs) + '\n)'


def _safe_col_name(c):
    if not isinstance(c, dict):
        raise GuardError('列定义格式非法')
    return validate_identifier(c.get('name'), '列')


def build_add_column_sql(table, column):
    """🟡 加字段：ADD COLUMN + 限制预检（应用表拒绝在 plan() 层）。"""
    validate_identifier(table, '表')
    if not table_exists(table):
        raise GuardError(f'表不存在：{table}')
    col_name = validate_identifier(column.get('name'), '列')
    existing = {r[1] for r in db.session.execute(text(
        f"PRAGMA table_info({table})")).fetchall()}
    if col_name in existing:
        raise GuardError(f'列已存在：{table}.{col_name}')
    col_ddl = build_column_def(column, for_add=True)
    return f'ALTER TABLE "{table}" ADD COLUMN {col_ddl}'


def build_create_index_sql(table, name, columns, unique=False):
    """🟡 建索引：多列 ≤5、列须存在、索引名全局唯一。"""
    validate_identifier(table, '表')
    validate_identifier(name, '索引')
    if not table_exists(table):
        raise GuardError(f'表不存在：{table}')
    if index_exists(name):
        raise GuardError(f'索引已存在：{name}')
    if name.startswith('sqlite_'):
        raise GuardError('索引名不允许 sqlite_ 前缀（系统保留）')
    if not isinstance(columns, list) or not (1 <= len(columns) <= 5):
        raise GuardError('索引列数量须在 1~5 之间')
    existing = {r[1] for r in db.session.execute(text(
        f"PRAGMA table_info({table})")).fetchall()}
    quoted = []
    for c in columns:
        cn = validate_identifier(c, '索引列')
        if cn not in existing:
            raise GuardError(f'表中不存在该列：{table}.{cn}')
        quoted.append(f'"{cn}"')
    uniq = 'UNIQUE ' if unique else ''
    return f'CREATE {uniq}INDEX "{name}" ON "{table}" (' + ', '.join(quoted) + ')'


def build_drop_index_sql(name):
    """🟡 删索引：主键/自动索引与唯一约束自动索引保护，应用表拒绝在 plan() 层。"""
    validate_identifier(name, '索引')
    row = db.session.execute(text(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type='index' AND name=:n"
    ), {'n': name}).first()
    if not row:
        raise GuardError(f'索引不存在：{name}')
    if name.startswith('sqlite_autoindex'):
        raise GuardError(AUTOMATIC_INDEX_REFUSE_MSG)
    if (row[1] or '').strip() == '':
        # sql 为空 = 自动索引（主键列/约束自动索引即使名字不标准也拒绝）
        raise GuardError(AUTOMATIC_INDEX_REFUSE_MSG)
    return f'DROP INDEX "{name}"', row[0]


def build_rename_table_sql(old, new):
    """🟡 表重命名：旧表存在、新表不存在。"""
    validate_identifier(old, '表')
    validate_identifier(new, '表')
    if not table_exists(old):
        raise GuardError(f'表不存在：{old}')
    if table_exists(new):
        raise GuardError(f'目标表已存在：{new}')
    return f'ALTER TABLE "{old}" RENAME TO "{new}"'


def build_copy_table_sql(src, dst, with_data=True):
    """🟡 复制表：仅结构或结构+数据（重建路径——iterdump 无 filter 的替代）。

    结构 = PRAGMA table_info 重建简单列定义（主键/NOT NULL/DEFAULT 保留）；
    数据 = INSERT INTO ... SELECT；自定义索引按 sqlite_master 原文重建（表名/索引名替换）。
    限制：复杂约束（CHECK/外键/AUTOINCREMENT 序列）不复制——面板自建表不含此类。
    """
    validate_identifier(src, '表')
    validate_identifier(dst, '表')
    if not table_exists(src):
        raise GuardError(f'源表不存在：{src}')
    if table_exists(dst):
        raise GuardError(f'目标表已存在：{dst}')

    cols = db.session.execute(text(
        f'PRAGMA table_info({src})')).fetchall()
    if not cols:
        raise GuardError(f'源表无列可复制：{src}')
    defs = []
    col_names = []
    for c in cols:
        cn, ct = c[1], (c[2] or '').upper().strip()
        validate_identifier(cn, '列')
        col_names.append(cn)
        # PRAGMA 报告类型可能带长度数字（VARCHAR(100)）→ 规范化后过白名单
        ct_norm = re.sub(r'\(\d+\)', '(n)', ct)
        if not COLTYPE_RE.match(ct_norm):
            raise GuardError(
                f'源表列类型超出复制能力白名单：{src}.{cn}（类型 {ct!r}）')
        d = f'"{cn}" {ct}'
        if c[5]:
            if ct not in ('INTEGER', 'TEXT'):
                raise GuardError(f'源表主键列类型不支持复制：{src}.{cn}')
            d += ' PRIMARY KEY'
        elif c[3]:
            d += ' NOT NULL'
        if c[4] is not None:
            d += ' DEFAULT ' + _copy_default(c[4], cn)
        defs.append(d)

    stmts = [f'CREATE TABLE "{dst}" (\n  ' + ',\n  '.join(defs) + '\n)']
    if with_data:
        quoted = ', '.join(f'"{c}"' for c in col_names)
        stmts.append(f'INSERT INTO "{dst}" ({quoted}) SELECT {quoted} FROM "{src}"')
    # 自定义索引复制（替换表名，索引名加前缀防冲突）
    idx_rows = db.session.execute(text(
        "SELECT name, sql FROM sqlite_master WHERE type='index' "
        "AND tbl_name=:t AND sql IS NOT NULL"), {'t': src}).fetchall()
    for i, (idx_name, idx_sql) in enumerate(idx_rows):
        new_idx = f'{dst}_idx{i + 1}'
        while index_exists(new_idx):
            i += 1
            new_idx = f'{dst}_idx{i + 1}'
        stmts.append(_rewrite_index_sql(idx_sql, src, dst, idx_name, new_idx))
    return stmts  # 多条语句（list）


def _copy_default(dflt, col_name):
    """复制 DEFAULT：字符串/数字原样，其他（表达式）降级剔除并提示。"""
    if isinstance(dflt, (int, float)):
        return str(dflt)
    s = str(dflt)
    if _DEFAULT_NUM_RE.match(s) or _DEFAULT_QUOTED_RE.match(s) \
            or s.upper() in _DEFAULT_TOKENS:
        return s
    raise GuardError(
        f'源表列默认值为表达式，超出复制能力（复制中止）：{col_name}={s}')


def _rewrite_index_sql(idx_sql, src, dst, idx_name, new_idx):
    """索引 SQL 重建：按「可选引号 + 词边界」精确替换索引名与表名（均已过白名单）。"""
    sql = idx_sql or ''
    sql = re.sub(
        r'(?P<q>"?)' + re.escape(idx_name) + r'(?P=q)',
        '"%s"' % new_idx, sql, count=1)
    sql = re.sub(
        r'(?i)(?P<q>"?)\b' + re.escape(src) + r'\b(?P=q)',
        '"%s"' % dst, sql)
    return sql


def build_analyze_sql(table=None):
    """GLB-14：ANALYZE 支持表级（`ANALYZE "table"`）；无参 = 全库。

    表名须是已存在的表（防 typo 静默空跑——SQLite 对不存在的表名会直接返回）。
    """
    if not table:
        return 'ANALYZE'
    validate_identifier(table, '表')
    if not table_exists(table):
        raise GuardError(f'表不存在：{table}')
    return f'ANALYZE "{table}"'


def build_vacuum_sql():
    return 'VACUUM'


# ============ 计划（plan，仅生成 SQL 供前端确认展示） ============

def plan(action, payload):
    """黄级操作计划：入参校验 + 安全判定 + 生成将执行的 SQL（不执行、不备份）。

    返回 {'sql': str|list, 'target': str, 'app_table': bool, 'warning': str|None}
    """
    payload = payload or {}
    if action == 'create_table':
        name = validate_identifier(payload.get('name'), '表')
        return {'sql': build_create_table_sql(name, payload.get('columns') or []),
                'target': name, 'app_table': False, 'warning': None}
    if action == 'add_column':
        table = validate_identifier(payload.get('table'), '表')
        return {'sql': build_add_column_sql(table, payload.get('column') or {}),
                'target': table, 'app_table': is_app_table(table),
                'warning': _app_warning(table)}
    if action == 'create_index':
        table = validate_identifier(payload.get('table'), '表')
        return {'sql': build_create_index_sql(table, payload.get('name'),
                                              payload.get('columns') or [],
                                              bool(payload.get('unique'))),
                'target': table, 'app_table': is_app_table(table),
                'warning': _app_warning(table)}
    if action == 'drop_index':
        name = validate_identifier(payload.get('name'), '索引')
        sql, tbl = build_drop_index_sql(name)
        return {'sql': sql, 'target': f'{tbl}.{name}',
                'app_table': is_app_table(tbl), 'warning': _app_warning(tbl)}
    if action == 'rename_table':
        old = validate_identifier(payload.get('old'), '表')
        validate_identifier(payload.get('new'), '表')
        return {'sql': build_rename_table_sql(old, payload.get('new')),
                'target': old, 'app_table': is_app_table(old),
                'warning': _app_warning(old)}
    if action == 'copy_table':
        src = validate_identifier(payload.get('src'), '表')
        dst = validate_identifier(payload.get('dst'), '表')
        sql = build_copy_table_sql(src, dst, bool(payload.get('with_data', True)))
        warn = None
        if is_app_table(src):
            warn = ('源表为应用表：复制只读取源表、不修改源表，副本（%s）'
                    '为面板自建表、可自由操作。' % dst)
        return {'sql': sql, 'target': f'{src} → {dst}',
                'app_table': False, 'warning': warn}
    if action == 'analyze':
        table = (payload.get('table') or '').strip() or None    # GLB-14：可选表级
        target = f'表 {table}' if table else '全库'
        return {'sql': build_analyze_sql(table), 'target': target,
                'app_table': False, 'warning': 'ANALYZE 更新统计信息（只读统计，不改数据）'}
    if action == 'vacuum':
        return {'sql': build_vacuum_sql(), 'target': '全库（VACUUM）',
                'app_table': False,
                'warning': 'VACUUM 需独占锁，执行期间可能短暂阻塞其他访问，建议低峰执行'}
    raise GuardError(f'未知操作类型：{action}')


def _app_warning(table):
    if is_app_table(table):
        return ('此表由系统代码使用，操作可能导致功能异常。'
                '面板内应用表一律只读，结构变更应走轨 A migration。')
    return None


# ============ 执行（execute：备份 → 执行 → 审计） ============

def snapshot_table_schema(table):
    """表结构摘要（before/after 审计用）：列清单 + 建表 SQL。"""
    validate_identifier(table, '表')
    sql_row = db.session.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"
    ), {'n': table}).first()
    cols = db.session.execute(text(
        f"PRAGMA table_info({table})")).fetchall()
    return json.dumps({
        'sql': sql_row[0] if sql_row else None,
        'columns': [{'name': c[1], 'type': c[2], 'notnull': c[3],
                     'default': c[4], 'pk': c[5]} for c in cols],
    }, ensure_ascii=False)


def db_size_breakdown():
    import config as _config
    total = 0
    parts = {}
    for suffix in ('', '-wal', '-shm'):
        p = _config.DB_PATH + suffix
        if os.path.isfile(p):
            sz = os.path.getsize(p)
            parts[p] = sz
            total += sz
    return total, parts


def execute(action, payload, operator):
    """黄级操作执行流程：plan 校验 → 应用表闸 → 前置备份 → 执行 → 审计 → 日志。

    返回 {'backup_file', 'sql', 'target', 'before', 'after'}。
    """
    ensure_audit_table()
    plan_info = plan(action, payload)
    if plan_info.get('app_table'):
        raise GuardError(APP_TABLE_REFUSE_MSG)
    sql = plan_info['sql']
    sql_text = sql if isinstance(sql, str) else '\n'.join(sql)
    target = plan_info['target']

    # 前置备份（凡写结构 🟡 级 + VACUUM 均强制；成功才放行）
    backup_path = preflight_backup(f'db_admin:{action}')

    before = after = None
    try:
        if action == 'create_table':
            before = None
            db.session.execute(text(sql_text))
            db.session.commit()
            after = snapshot_table_schema(payload.get('name'))
        elif action == 'add_column':
            table = payload.get('table')
            before = snapshot_table_schema(table)
            db.session.execute(text(sql_text))
            db.session.commit()
            after = snapshot_table_schema(table)
        elif action == 'create_index':
            before = snapshot_index_sql(payload.get('name'))
            db.session.execute(text(sql_text))
            db.session.commit()
            after = snapshot_index_sql(payload.get('name'))
        elif action == 'drop_index':
            before = snapshot_index_sql(payload.get('name'))
            db.session.execute(text(sql_text))
            db.session.commit()
            after = None
        elif action == 'rename_table':
            before = snapshot_table_schema(payload.get('old'))
            db.session.execute(text(sql_text))
            db.session.commit()
            after = snapshot_table_schema(payload.get('new'))
        elif action == 'copy_table':
            before = None
            for stmt in sql:  # list of statements
                db.session.execute(text(stmt))
            db.session.commit()
            after = snapshot_table_schema(payload.get('dst'))
        elif action == 'analyze':
            before = db_size_breakdown()[0]
            db.session.execute(text(sql))     # GLB-14：sql 可能是表级 ANALYZE "table"
            db.session.commit()
            after = db_size_breakdown()[0]
        elif action == 'vacuum':
            before = db_size_breakdown()
            _vacuum_raw()
            after = db_size_breakdown()
        else:
            raise GuardError(f'未知操作类型：{action}')
    except GuardError:
        raise
    except Exception as e:  # DDL 失败：审计 failed 留痕后抛出（不吞错）
        try:
            db.session.rollback()
            audit(operator, action, target, sql_text,
                  before_schema=before, backup_file=os.path.basename(backup_path),
                  result='failed')
        except Exception:
            pass
        raise GuardError(f'操作执行失败，事务已回滚：{e}')

    audit(operator, action, target, sql_text,
          before_schema=before, after_schema=after,
          backup_file=os.path.basename(backup_path))
    result = {'backup_file': os.path.basename(backup_path),
              'sql': sql_text, 'target': target}
    if action == 'vacuum':
        # 验收口径：VACUUM 执行后展示库体积前后对比（含 wal/shm 分量）
        result['size_before'] = before[0]
        result['size_after'] = after[0]
    return result


def snapshot_index_sql(name):
    row = db.session.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=:n"
    ), {'n': name}).first()
    return row[0] if row else None


def _vacuum_raw():
    """VACUUM 必须运行于事务之外（SQLite 限制）：独立 dbapi 连接 + 自动提交。"""
    raw = db.engine.raw_connection()
    try:
        conn = raw.driver_connection
        old_iso = getattr(conn, 'isolation_level', None)
        try:
            conn.isolation_level = None
            conn.execute('VACUUM')
        finally:
            try:
                conn.isolation_level = old_iso
            except Exception:
                pass
    finally:
        raw.close()


# ============ 只读体检 ============

def _pragma(pragma, table=None):
    if table:
        validate_identifier(table, '表')
        return db.session.execute(text(
            f'PRAGMA {pragma}("{table}")')).fetchall()
    return db.session.execute(text(f'PRAGMA {pragma}')).fetchall()


def overview():
    import config as _config
    total, parts = db_size_breakdown()
    page_count = _pragma('page_count')[0][0]
    page_size = _pragma('page_size')[0][0]
    freelist = _pragma('freelist_count')[0][0]
    journal_mode = _pragma('journal_mode')[0][0]
    names = list_tables()
    total_rows = 0
    for n in names:
        try:
            total_rows += db.session.execute(text(
                f'SELECT COUNT(*) FROM "{n}"')).scalar() or 0
        except Exception:
            pass
    last = last_backup_info()
    return {
        'db_file': _config.DB_PATH,
        'size_bytes': total,
        'size_parts': {os.path.basename(k): v for k, v in parts.items()},
        'page_count': page_count, 'page_size': page_size,
        'freelist_count': freelist, 'journal_mode': journal_mode,
        'table_count': len(names), 'total_rows': total_rows,
        'last_backup': last['filename'], 'last_backup_mtime': last['mtime'],
    }


def _dbstat_sizes():
    """每表体积（dbstat 虚拟表）；不可用/无数据返回空 dict（降级）。"""
    sizes = {}
    try:
        rows = db.session.execute(text(
            "SELECT name, SUM(pgsize) FROM dbstat "
            "WHERE name NOT LIKE 'sqlite_%' GROUP BY name")).fetchall()
        for name, sz in rows:
            sizes[name] = int(sz or 0)
    except Exception:
        pass
    return sizes


def tables_list():
    sizes = _dbstat_sizes()
    out = []
    for name in list_tables():
        rows = db.session.execute(text(
            f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0
        cols = [r for r in _pragma('table_info', name)]
        idxs = [r for r in _pragma('index_list', name)]
        # PRAGMA index_list 列：(seq, name, unique, origin, partial)
        sqlite_auto = sum(1 for r in idxs if str(r[1]).startswith('sqlite_autoindex'))
        out.append({
            'name': name, 'rows': rows, 'cols': len(cols),
            'indexes': len(idxs) - sqlite_auto,
            'automatic_indexes': sqlite_auto,
            'size_bytes': sizes.get(name, None),
            'is_app': is_app_table(name),
        })
    out.sort(key=lambda x: (not x['is_app'], x['name']))
    return out


def table_detail(name):
    validate_identifier(name, '表')
    if not table_exists(name):
        raise GuardError(f'表不存在：{name}')
    xcols = _pragma('table_xinfo', name)
    columns = [{
        'cid': r[0], 'name': r[1], 'type': r[2], 'notnull': bool(r[3]),
        'default': r[4], 'pk': r[5], 'hidden': r[6],
    } for r in xcols]
    idxs = _pragma('index_list', name)
    indexes = [{
        'name': r[1], 'unique': bool(r[2]), 'origin': r[3],
        'partial': bool(r[4]),
        # PRAGMA index_list 仅 (seq, name, unique, origin, partial) 5 列；
        # 自动索引（主键/唯一约束）名称恒为 sqlite_autoindex_* —— 以名字判自动
        'automatic': str(r[1]).startswith('sqlite_autoindex'),
        'columns': [x[2] for x in _pragma('index_info', r[1])],
    } for r in idxs]
    fks = [{'id': r[0], 'from': r[3], 'table': r[2], 'to': r[4],
            'on_update': r[5], 'on_delete': r[6]}
           for r in _pragma('foreign_key_list', name)]
    sql_row = db.session.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"
    ), {'n': name}).first()
    rows = db.session.execute(text(
        f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0
    sizes = _dbstat_sizes()
    return {
        'name': name,
        'create_sql': sql_row[0] if sql_row else None,
        'columns': columns, 'indexes': indexes, 'foreign_keys': fks,
        'rows': rows, 'size_bytes': sizes.get(name, None),
        'is_app': is_app_table(name),
    }


def integrity_check():
    """完整性检查三件套 + 人话报告。"""
    report = {'ok': True, 'issues': [], 'details': {}}
    label = {'integrity_check': '完整性检查', 'quick_check': '快速检查',
             'foreign_key_check': '外键检查'}
    for pragma in ('integrity_check', 'quick_check', 'foreign_key_check'):
        try:
            rows = _pragma(pragma)
        except Exception as e:
            report['issues'].append(f'{label[pragma]} 执行失败：{e}')
            report['ok'] = False
            continue
        if pragma in ('integrity_check', 'quick_check'):
            msgs = [r[0] for r in rows if r[0] and str(r[0]).lower() != 'ok']
            if msgs:
                report['ok'] = False
                report['issues'].extend(
                    f'{label[pragma]}：{m}' for m in msgs)
            report['details'][pragma] = ('ok' if not msgs else msgs[0])
        else:  # foreign_key_check：无行 = 无违例
            if rows:
                report['ok'] = False
                for r in rows:
                    report['issues'].append(
                        f'外键违例：表 {r[0]} 第 {r[1]} 行 → 父表 {r[2]}')
            report['details'][pragma] = f'{len(rows)} 条违例'
    return report


def reconcile_inventory(inventory_path=None):
    """Schema 对账：实际库 ↔ field_inventory.json（轨 A 敏感字段盘点清单）。"""
    if inventory_path is None:
        inventory_path = os.path.join(_MIGRATIONS_DIR, 'field_inventory.json')
    if not os.path.isfile(inventory_path):
        return {'ok': False, 'error': f'清单文件不存在：{inventory_path}'}
    with open(inventory_path, encoding='utf-8') as f:
        inventory = json.load(f)
    missing_tables, missing_columns = [], []
    checked_tables = 0
    for t in inventory.get('tables', []):
        table = t.get('table', '')
        try:
            validate_identifier(table, '表')
        except GuardError:
            missing_tables.append({'table': table, 'reason': '名称非法'})
            continue
        if not table_exists(table):
            missing_tables.append({'table': table, 'reason': '表不存在'})
            continue
        checked_tables += 1
        existing = {r[1] for r in _pragma('table_info', table)}
        for f in t.get('fields', []):
            if f not in existing:
                missing_columns.append({'table': table, 'field': f})
    return {
        'ok': not missing_tables and not missing_columns,
        'checked_tables': checked_tables,
        'missing_tables': missing_tables,
        'missing_columns': missing_columns,
    }


def audit_list(limit=AUDIT_RETENTION_SHOWN):
    ensure_audit_table()
    rows = db.session.execute(text(
        f'SELECT id, operator, action, target, sql_text, before_schema, '
        f'after_schema, backup_file, result, created_at '
        f'FROM {AUDIT_TABLE} ORDER BY id DESC LIMIT :n'
    ), {'n': int(limit)}).fetchall()
    keys = ('id', 'operator', 'action', 'target', 'sql_text', 'before_schema',
            'after_schema', 'backup_file', 'result', 'created_at')
    return [dict(zip(keys, r)) for r in rows]


# ============ 🔴 红级（026）：删字段 / 改字段（重建引擎）/ 截断 / 删表 + 三重闸 ============

RED_ACTIONS = ('drop_column', 'alter_column', 'truncate_table', 'drop_table')

# 闸 2（影响面确认）：execute 时服务端强制核对输入表名（不信任前端）
CONFIRM_NOT_MATCH_MSG = '确认输入的表名与操作目标不符，已拒绝执行（三重闸·闸 2）'


def _red_target(action, payload):
    """红级操作的目标表名（全部 action 均以表为操作对象）。"""
    return validate_identifier((payload or {}).get('table'), '表')


def _red_refuse_if_app(table):
    if is_app_table(table):
        raise GuardError(APP_TABLE_REFUSE_MSG)
    if not table_exists(table):
        raise GuardError(f'表不存在：{table}')


def _red_impact(table):
    """影响面（闸 2 展示 + 服务端重算）：行数 / 依赖对象（显式索引/外键引用/触发器）。"""
    rows = db.session.execute(text(
        f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
    deps = []
    # 本表显式索引
    for r in _pragma('index_list', table):
        if r[3] == 'c':
            deps.append({'type': '索引', 'name': r[1],
                         'columns': [x[2] for x in _pragma('index_info', r[1])]})
    # 其他表对本表的外键引用
    for other in list_tables():
        if other == table:
            continue
        for fk in _pragma('foreign_key_list', other):
            if fk[2] == table:
                deps.append({'type': '外键引用', 'from': other,
                             'detail': f'{other}.{fk[3]} → {table}.{fk[4]}'})
    # 触发器（重建后按原定义恢复；删表/截断随动）
    trigs = db.session.execute(text(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=:t"
    ), {'t': table}).fetchall()
    for t in trigs:
        deps.append({'type': '触发器', 'name': t[0]})
    return {'table': table, 'rows': rows, 'deps': deps}


def _rebuild_col_def(name, ctype, notnull, default, pk):
    """重建用列定义（源取值 PRAGMA/SQLite 自身，仅用户改的列另有白名单）。"""
    ddl = f'"{name}" {ctype}'
    if pk:
        ddl += ' PRIMARY KEY'
    elif notnull:
        ddl += ' NOT NULL'
    if default is not None:
        ddl += ' DEFAULT ' + _copy_default(default, name)
    return ddl


def _refuse_auto_increment(table):
    """AUTOINCREMENT 序列重建受限：能力边界硬拒绝（面板自建表演练不含此）。"""
    row = db.session.execute(text(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"
    ), {'n': table}).first()
    if row and row[0] and 'AUTOINCREMENT' in (row[0] or '').upper():
        raise GuardError(
            f'表 {table} 含 AUTOINCREMENT 列，重建引擎暂不支持（能力边界，请走轨 A）')


def _rebuild_table(table, changed_column):
    """表重建引擎（SQLite 官方推荐流程）：新建→拷数据→换名→重建触发器/索引。

    全程同一事务：任一步失败整体回滚，原表不受损；
    foreign_keys 在重建期间暂停（OFF），完成后 foreign_key_check 校验，违例回滚。
    ponytail: 复用 PRAGMA 重建路径（025 copy_table 同款），不引第三方工具。
    """
    cols = db.session.execute(text(
        f'PRAGMA table_info({table})')).fetchall()
    old_defs, col_names = [], []
    for c in cols:
        cn, ct = c[1], (c[2] or '').upper().strip()
        validate_identifier(cn, '列')
        ct_norm = re.sub(r'\(\d+\)', '(n)', ct)
        if not COLTYPE_RE.match(ct_norm):
            raise GuardError(
                f'列类型超出重建白名单：{table}.{cn}（{ct!r}）')
        col_names.append(cn)
        old_defs.append((cn, ct, bool(c[3]), c[4], bool(c[5])))
    # 用户改的列：新定义（白名单夹持），主键状态继承原始
    changed_name = validate_identifier(changed_column.get('name'), '列')
    if changed_name not in col_names:
        raise GuardError(f'待改列不存在：{table}.{changed_name}')
    new_col = build_column_def(changed_column)
    old_pk = {c[1]: bool(c[5]) for c in cols}
    if old_pk.get(changed_name):
        # 主键列改类型：SQLite 主键列类型变更风险大，硬拒绝（保留主键语义）
        raise GuardError('不支持修改主键列（保持主键语义，请走轨 A）')

    defs = []
    for (cn, ct, nn, dflt, pk) in old_defs:
        if cn == changed_name:
            defs.append(new_col)
        else:
            defs.append(_rebuild_col_def(cn, ct, nn, dflt, pk))
    tmp = f'{table}__rebuild'
    if table_exists(tmp):
        raise GuardError(f'临时表名冲突：{tmp}（可能上次重建残留）')

    trig_rows = db.session.execute(text(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=:t"
    ), {'t': table}).fetchall()
    idx_rows = db.session.execute(text(
        "SELECT name, sql FROM sqlite_master WHERE type='index' "
        "AND tbl_name=:t AND sql IS NOT NULL"), {'t': table}).fetchall()

    create_sql = 'CREATE TABLE "%s" (\n  %s\n)' % (tmp, ',\n  '.join(defs))
    quoted = ', '.join('"%s"' % c for c in col_names)
    insert_sql = (
        f'INSERT INTO "{tmp}" ({quoted}) SELECT {quoted} FROM "{table}"')
    stmts = [create_sql, insert_sql,
             f'DROP TABLE "{table}"',
             f'ALTER TABLE "{tmp}" RENAME TO "{table}"']

    # 事务内执行（foreign_keys 暂停仅本连接）
    db.session.execute(text('PRAGMA foreign_keys=OFF'))
    try:
        db.session.execute(text(create_sql))
        db.session.execute(text(insert_sql))
        db.session.execute(text(f'DROP TABLE "{table}"'))
        db.session.execute(text(f'ALTER TABLE "{tmp}" RENAME TO "{table}"'))
        # 重建触发器（表名在 RENAME 后已指回原表，原文即可）
        for (tname, tsql) in trig_rows:
            db.session.execute(text(tsql))
            stmts.append(tsql)
        # 重建显式索引（随 DROP 删除）
        for (iname, isql) in idx_rows:
            db.session.execute(text(isql))
            stmts.append(isql)
        # 外键校验（须 ON 才有效）
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        fk_rows = db.session.execute(text('PRAGMA foreign_key_check')).fetchall()
        if fk_rows:
            raise GuardError(
                '重建后外键校验未通过，整体回滚：' + str(fk_rows[:3]))
    except Exception:
        db.session.rollback()
        db.session.execute(text('PRAGMA foreign_keys=ON'))
        db.session.commit()
        raise
    db.session.execute(text('PRAGMA foreign_keys=ON'))
    db.session.commit()
    return stmts


def plan_red(action, payload):
    """红级操作计划（前端闸 2 展示影响面；服务端 execute 时重算，不信任本结果）。"""
    target = _red_target(action, payload)
    _red_refuse_if_app(target)
    impact = _red_impact(target)
    if action == 'drop_column':
        col = validate_identifier((payload or {}).get('column'), '列')
        existing = {r[1] for r in _pragma('table_info', target)}
        if col not in existing:
            raise GuardError(f'列不存在：{target}.{col}')
        if len(existing) <= 1:
            raise GuardError('表仅剩 1 列，不支持删字段（先删表或走轨 A）')
        # 依赖检查：任何索引（含主键/唯一约束自动索引）引用该列 → 拒绝
        for r in _pragma('index_list', target):
            if col in [x[2] for x in _pragma('index_info', r[1])]:
                if str(r[1]).startswith('sqlite_autoindex'):
                    raise GuardError(
                        f'列被主键/唯一约束绑定（自动索引 {r[1]}），无法删除（或走轨 A）')
                raise GuardError(
                    f'列被索引引用，先删除索引 {r[1]}（或走轨 A）')
        for other in list_tables():
            if other == target:
                continue
            for fk in _pragma('foreign_key_list', other):
                if fk[2] == target and (fk[3] == col or fk[4] == col):
                    raise GuardError(
                        f'列被外键引用（{other}.{fk[3]}），删除会破坏引用（或走轨 A）')
        return {'sql': f'ALTER TABLE "{target}" DROP COLUMN "{col}"',
                'target': target, 'rows': impact['rows'], 'deps': impact['deps'],
                'app_table': False}
    if action == 'alter_column':
        col = (payload or {}).get('column') or {}
        col_name = validate_identifier(col.get('name'), '列')
        new_def = build_column_def(col)
        _refuse_auto_increment(target)
        return {'sql': ('表重建引擎（新建→拷数据→换名→重建触发器/索引）：'
                        f'修改 {target}.{col_name} → {new_def}'),
                'target': target, 'rows': impact['rows'], 'deps': impact['deps'],
                'app_table': False, 'rebuild': True,
                'column': col_name}
    if action == 'truncate_table':
        return {'sql': f'DELETE FROM "{target}"', 'target': target,
                'rows': impact['rows'], 'deps': impact['deps'],
                'app_table': False, 'recoverable': '闸 1 备份可整库回滚'}
    if action == 'drop_table':
        # 依赖检查：被外键引用 → 硬拒绝；触发器随删；视图引用仅展示警告
        for other in list_tables():
            if other == target:
                continue
            for fk in _pragma('foreign_key_list', other):
                if fk[2] == target:
                    raise GuardError(
                        f'表被外键引用（{other}），无法删除（先解除引用或走轨 A）')
        return {'sql': f'DROP TABLE "{target}"', 'target': target,
                'rows': impact['rows'], 'deps': impact['deps'],
                'app_table': False}
    raise GuardError(f'未知红级操作：{action}')


def execute_red(action, payload, operator, confirm_name):
    """红级执行（三重闸服务端强制）：

    闸 1：前置 .wbak 备份（失败中止）；闸 2：确认表名核对 + 影响面重算；
    闸 3：执行后返回备份文件/SQL/影响行数/审计编号（前端展示恢复指引）。
    """
    ensure_audit_table()
    plan_info = plan_red(action, payload)  # 应用表/依赖检查在此（服务端重算）
    target = plan_info['target']

    # 闸 1：前置备份
    backup_path = preflight_backup(f'db_admin:red:{action}')

    # 闸 2：输入表名确认（服务端强制，不依赖前端）
    if str(confirm_name or '').strip() != target:
        raise GuardError(CONFIRM_NOT_MATCH_MSG)

    sql_text = plan_info['sql']
    before = snapshot_table_schema(target)
    rows_before = plan_info['rows']
    audit_id = 0
    try:
        if action == 'drop_column':
            db.session.execute(text(sql_text))
            db.session.commit()
        elif action == 'alter_column':
            sql_text = '\n'.join(
                _rebuild_table(target, payload.get('column') or {}))
        elif action == 'truncate_table':
            db.session.execute(text(sql_text))
            db.session.execute(text(
                "DELETE FROM sqlite_sequence WHERE name=:n"), {'n': target})
            db.session.commit()
        elif action == 'drop_table':
            db.session.execute(text(sql_text))
            db.session.commit()
        else:
            raise GuardError(f'未知红级操作：{action}')
    except GuardError:
        raise
    except Exception as e:
        try:
            db.session.rollback()
            audit(operator, f'red_{action}', target, sql_text,
                  before_schema=before,
                  backup_file=os.path.basename(backup_path), result='failed')
        except Exception:
            pass
        raise GuardError(f'操作执行失败，事务已回滚，原表未受损：{e}')

    audit_id = audit(operator, f'red_{action}', target, sql_text,
                     before_schema=before,
                     after_schema=None if action == 'drop_table'
                     else snapshot_table_schema(target),
                     backup_file=os.path.basename(backup_path))
    return {
        'backup_file': os.path.basename(backup_path),
        'sql': sql_text if isinstance(sql_text, str) else '\n'.join(sql_text),
        'target': target, 'rows': rows_before,
        'audit_id': audit_id,
        'recover_tip': (
            f'如需回滚：到「数据库安全」页用备份 {os.path.basename(backup_path)} 恢复整库。'),
    }