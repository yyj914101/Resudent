#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m037_employment_service.py — 084 就业服务建表 + 字典补种（幂等迁移）。

两张表（轨 A：存量库手工/脚本执行；新库由 create_all 直建，本脚本可重放）：
- welfare_employment      就业状态登记（一人一条当前状态，village_group/employment_status 供按组与
                          按状态统计索引）
- welfare_flex_subsidy    灵活就业补贴申报（同人 + 同年度 + 同补贴类型唯一，防重复申报/发放）

字典 DML 与 utils/dict_seed.DICT_SEEDS 同值（唯一真源，不得另造枚举）：
就业状态 / 就业类型 / 就业行业 / 灵活就业补贴类型 / 补贴申报状态 共 5 分类。
新库由 app.py 启动守卫 ensure_enum_dicts 建齐；本迁移用于存量库补种与显式重放。

约束：
- 只做 CREATE TABLE/INDEX IF NOT EXISTS + 字典守卫 DML，不改既有表、不改既有列。
- 已知「表已存在」的两种来源：create_all（新库）与本脚本（存量库）。uq_flex_subsidy
  在 create_all 库里已由模型 UniqueConstraint 落成同列 sqlite_autoindex_*，故本脚本
  先探测同列唯一约束、存在则复用不重建，避免同一张表叠两个等价唯一索引。
- 缺分类建分类、缺项补项；已存在项不重复、不覆盖；重复执行结果一致。
- id_card / phone 接 R12 字段级加密：明文列无 default，加密后置 NULL，密文落 *_enc，
  等值检索走 *_hash（*_hash 可空 → SQLite 唯一索引允许多个 NULL，未登记证件不互相冲突）。

执行（先备份库）：
    python scripts/migrations/m037_employment_service.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

CATEGORIES = {
    '就业状态': ['已就业', '灵活就业', '未就业', '失业', '其他'],
    '就业类型': ['单位就业', '个体经营', '灵活就业', '公益性岗位', '其他'],
    '就业行业': ['农林牧渔', '建筑', '制造', '批发零售', '住宿餐饮', '居民服务', '其他'],
    '灵活就业补贴类型': ['社保补贴', '岗位补贴', '一次性补贴', '其他'],
    '补贴申报状态': ['待申报', '已申报', '已审核', '已发放', '未通过'],
}

DDL = """
CREATE TABLE IF NOT EXISTS welfare_employment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(50) NOT NULL,
  id_card VARCHAR(18),
  id_card_enc TEXT,
  id_card_hash VARCHAR(64),
  gender VARCHAR(10),
  village_group VARCHAR(50),
  phone VARCHAR(30),
  phone_enc TEXT,
  phone_hash VARCHAR(64),
  employment_status VARCHAR(30),
  employment_type VARCHAR(30),
  employer VARCHAR(100),
  industry VARCHAR(30),
  work_location VARCHAR(100),
  start_date DATE,
  update_date DATE,
  resident_id INTEGER,
  remark TEXT,
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_welfare_employment_id_card_hash
  ON welfare_employment (id_card_hash);
CREATE INDEX IF NOT EXISTS ix_welfare_employment_village_group
  ON welfare_employment (village_group);
CREATE INDEX IF NOT EXISTS ix_welfare_employment_employment_status
  ON welfare_employment (employment_status);
CREATE INDEX IF NOT EXISTS ix_welfare_employment_resident_id
  ON welfare_employment (resident_id);

CREATE TABLE IF NOT EXISTS welfare_flex_subsidy (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(50) NOT NULL,
  id_card VARCHAR(18),
  id_card_enc TEXT,
  id_card_hash VARCHAR(64),
  resident_id INTEGER,
  apply_year INTEGER NOT NULL,
  batch_no VARCHAR(30),
  subsidy_type VARCHAR(30) NOT NULL,
  amount NUMERIC(10, 2),
  apply_status VARCHAR(30) DEFAULT '待申报',
  apply_date DATE,
  review_date DATE,
  issue_date DATE,
  reject_reason VARCHAR(200),
  remark TEXT,
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_welfare_flex_subsidy_id_card_hash
  ON welfare_flex_subsidy (id_card_hash);
CREATE INDEX IF NOT EXISTS ix_welfare_flex_subsidy_resident_id
  ON welfare_flex_subsidy (resident_id);
CREATE INDEX IF NOT EXISTS ix_welfare_flex_subsidy_apply_year
  ON welfare_flex_subsidy (apply_year);
CREATE INDEX IF NOT EXISTS ix_welfare_flex_subsidy_apply_status
  ON welfare_flex_subsidy (apply_status);
-- 同人 + 同年度 + 同补贴类型唯一（id_card_hash 为 NULL 的行不受约束，SQLite 语义）。
-- 注意：本索引不在 DDL 里直建，改由 _ensure_flex_unique() 条件创建 ——
-- 库若已由 create_all 建表（模型 UniqueConstraint 落成同列 sqlite_autoindex_*），
-- 直建会在同表上叠加一个等价冗余唯一索引（功能无害但 schema 双形态），故先探测后建。
-- 年度进度看板：年度 + 状态
CREATE INDEX IF NOT EXISTS idx_flex_year_status
  ON welfare_flex_subsidy (apply_year, apply_status);
"""

# uq_flex_subsidy 的列序（与模型 UniqueConstraint 完全一致，用于等价探测）
FLEX_UNIQUE_COLS = ('id_card_hash', 'apply_year', 'subsidy_type')
FLEX_UNIQUE_SQL = ('CREATE UNIQUE INDEX IF NOT EXISTS uq_flex_subsidy '
                   'ON welfare_flex_subsidy (id_card_hash, apply_year, subsidy_type)')

# 字典守卫 DML（值 = 标签 = DICT_SEEDS 字面量；分类/条目均 WHERE NOT EXISTS 幂等）
DICT_DML = """
INSERT INTO dict_categories (name, description, is_active, sort_order, created_at)
SELECT '{name}', '{desc}', 1,
       COALESCE((SELECT MAX(sort_order) FROM dict_categories), 0) + 10,
       datetime('now', 'localtime')
WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = '{name}');

INSERT INTO dict_items (category_id, label, value, sort_order, is_active, created_at)
SELECT c.id, seed.label, seed.value, seed.sort_order, 1, datetime('now', 'localtime')
FROM dict_categories c, (
{seed_rows}
) AS seed
WHERE c.name = '{name}'
  AND NOT EXISTS (
    SELECT 1 FROM dict_items di
    WHERE di.category_id = c.id AND di.label = seed.label
  );
"""


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def _seed_rows(labels) -> str:
    rows = []
    for idx, label in enumerate(labels, start=1):
        head = 'SELECT' if idx == 1 else '    SELECT'
        rows.append("%s '%s' AS label, '%s' AS value, %d AS sort_order" % (head, label, label, idx))
    return '\n    UNION ALL\n'.join(rows)


def _dict_dml() -> str:
    return '\n'.join(
        DICT_DML.format(name=name, desc='084 就业服务：%s' % name, seed_rows=_seed_rows(labels))
        for name, labels in CATEGORIES.items()
    )


def _equivalent_unique_index(conn, table, cols):
    """返回已在 (cols) 上生效的唯一索引名（含 sqlite_autoindex_*），无则 None。

    用于避免在 create_all 建好的库上叠加同列冗余唯一索引：模型侧
    UniqueConstraint 会落成 sqlite_autoindex_<table>_N，名字与本脚本的
    uq_flex_subsidy 不同但约束完全等价。
    """
    wanted = tuple(cols)
    for row in conn.execute('PRAGMA index_list(%s)' % table):
        name, is_unique = row[1], row[2]
        if not is_unique:
            continue
        got = tuple(r[2] for r in conn.execute('PRAGMA index_info(%s)' % name))
        if got == wanted:
            return name
    return None


def run(db_path=None) -> dict:
    path = _db_path(db_path)
    conn = sqlite3.connect(path)
    stats = {'db': path, 'new_categories': [], 'new_items': 0,
             'skipped_categories': [], 'uq_flex_subsidy': ''}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'dict_categories' not in tables or 'dict_items' not in tables:
            raise RuntimeError('dict_categories / dict_items 表不存在：请先在目标库 create_all')

        conn.executescript(DDL)

        # uq_flex_subsidy：先探测同列唯一约束，已存在（create_all 建的 sqlite_autoindex_*）
        # 则复用不重建，保证两条建库路径的最终 schema 一致。
        existing_uq = _equivalent_unique_index(conn, 'welfare_flex_subsidy', FLEX_UNIQUE_COLS)
        if existing_uq:
            stats['uq_flex_subsidy'] = 'reuse:%s' % existing_uq
        else:
            conn.execute(FLEX_UNIQUE_SQL)
            stats['uq_flex_subsidy'] = 'created:uq_flex_subsidy'

        before = {row[0] for row in conn.execute(
            "SELECT name FROM dict_categories WHERE name IN (%s)"
            % ','.join("'%s'" % n for n in CATEGORIES))}
        for name, labels in CATEGORIES.items():
            row = conn.execute('SELECT id FROM dict_categories WHERE name = ?', (name,)).fetchone()
            if row:
                cat_id = row[0]
                if name in before:
                    stats['skipped_categories'].append(name)
            else:
                cur = conn.execute(
                    'INSERT INTO dict_categories (name, description, is_active, sort_order, created_at) '
                    'VALUES (?, ?, 1, '
                    'COALESCE((SELECT MAX(sort_order) FROM dict_categories), 0) + 10, ?)',
                    (name, '084 就业服务：%s' % name, now))
                cat_id = cur.lastrowid
                stats['new_categories'].append(name)
            existing = {r[0] for r in conn.execute(
                'SELECT label FROM dict_items WHERE category_id = ?', (cat_id,))}
            for idx, label in enumerate(labels, start=1):
                if label in existing:
                    continue
                conn.execute(
                    'INSERT INTO dict_items (category_id, label, value, sort_order, is_active, created_at) '
                    'VALUES (?, ?, ?, ?, 1, ?)', (cat_id, label, label, idx, now))
                stats['new_items'] += 1
        # 上面 Python 版已建齐；DML 版本仅用于人工 sqlite3 重放（幂等，不产生重复）
        conn.commit()
    finally:
        conn.close()
    return stats


def dump_sql(db_path=None) -> str:
    """导出纯 SQL（供 sqlite3 CLI 手工重放；同样幂等）。

    注意：纯 SQL 无法探测「同列唯一约束是否已存在」，故 uq_flex_subsidy 一律输出。
    若目标库是由 create_all 建的（已有同列 sqlite_autoindex_*），执行后会多一个
    等价冗余唯一索引（不影响唯一性语义，只多一份写入开销）；要完全对齐请改用
    `python scripts/migrations/m037_employment_service.py --db <库>`，它会先探测后建。
    """
    return (DDL + '\n'
            '-- 若库由 create_all 建表（同列唯一约束已存在），下一行会产生等价冗余索引\n'
            + FLEX_UNIQUE_SQL + ';\n\n' + _dict_dml())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    parser.add_argument('--print-sql', action='store_true', help='只输出等价 SQL，不写库')
    args = parser.parse_args()
    if args.print_sql:
        print(dump_sql(args.db))
        return
    stats = run(args.db)
    print('[m037] 目标库：%s' % stats['db'])
    print('[m037] 新建分类：%s' % (', '.join(stats['new_categories']) or '无'))
    print('[m037] 新增字典项：%s' % stats['new_items'])
    print('[m037] 已有分类跳过：%s' % (', '.join(stats['skipped_categories']) or '无'))
    print('[m037] 唯一索引 uq_flex_subsidy：%s' % stats['uq_flex_subsidy'])


if __name__ == '__main__':
    main()
