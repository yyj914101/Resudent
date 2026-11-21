#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m041_party_development.py — 083B 党员发展建表 + 字典补种（幂等迁移）。

两张表（轨 A：存量库脚本执行；新库由 create_all 直建，本脚本可重放）：
- party_development      五阶段发展名册主表（固定表，ADR-025「有流程不入软表」）
- party_development_log  阶段迁移日志子表（只增不改；转正回写留改前快照）

字典 DML 与 utils/dict_seed.DICT_SEEDS 同值（唯一真源，不得另造枚举），共 1 分类：
- 党员发展阶段：申请人 / 积极分子 / 发展对象 / 预备党员 / 正式党员（状态机真源）

新库由 app.py 启动守卫 ensure_enum_dicts 建齐；本迁移用于存量库补种与显式重放。

约束：
- 只做 CREATE TABLE/INDEX IF NOT EXISTS + 字典守卫 DML，不改既有表、不改既有列。
- party_development.id_card_hash 唯一（一人一条发展记录）；身份证/联系电话接
  R12（明文列加密后置 NULL）；party_members 零结构改动（转正回写只 UPDATE 两列）。
- 缺分类建分类、缺项补项；已存在项不重复、不覆盖；重复执行结果一致。

执行（先备份库）：
    python scripts/migrations/m041_party_development.py --db shucun.db
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

# 字典用值（1 分类，与 utils/dict_seed.DICT_SEEDS 同值）
CATEGORIES = {
    '党员发展阶段': ['申请人', '积极分子', '发展对象', '预备党员', '正式党员'],
}

DDL = """
CREATE TABLE IF NOT EXISTS party_development (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(50) NOT NULL,
  id_card VARCHAR(18),
  id_card_enc TEXT,
  id_card_hash VARCHAR(64),
  gender VARCHAR(10),
  phone VARCHAR(20),
  phone_enc TEXT,
  phone_hash VARCHAR(64),
  branch VARCHAR(100),
  stage VARCHAR(30) NOT NULL DEFAULT '申请人',
  apply_date DATE,
  activist_date DATE,
  develop_date DATE,
  probate_date DATE,
  full_member_date DATE,
  introducer VARCHAR(100),
  remark TEXT,
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

-- 一人一条发展记录（模型注释对齐；hash 为 NULL 时不参与唯一约束，R-084-01 同因）
CREATE UNIQUE INDEX IF NOT EXISTS uq_party_development_id_card_hash
  ON party_development (id_card_hash) WHERE id_card_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_party_development_stage
  ON party_development (stage);
CREATE INDEX IF NOT EXISTS ix_party_development_branch
  ON party_development (branch);

CREATE TABLE IF NOT EXISTS party_development_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dev_id INTEGER NOT NULL,
  from_stage VARCHAR(30) NOT NULL,
  to_stage VARCHAR(30) NOT NULL,
  stage_date DATE,
  operator VARCHAR(50),
  writeback_before TEXT,
  created_at DATETIME,
  FOREIGN KEY (dev_id) REFERENCES party_development (id)
);

CREATE INDEX IF NOT EXISTS ix_party_development_log_dev_id
  ON party_development_log (dev_id);
"""

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
        DICT_DML.format(name=name, desc='%s（083B 党员发展）' % name,
                        seed_rows=_seed_rows(labels))
        for name, labels in CATEGORIES.items()
    )


def run(db_path=None) -> dict:
    path = _db_path(db_path)
    conn = sqlite3.connect(path)
    stats = {'db': path, 'new_categories': [], 'new_items': 0, 'skipped_categories': []}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'dict_categories' not in tables or 'dict_items' not in tables:
            raise RuntimeError('dict_categories / dict_items 表不存在：请先在目标库 create_all')

        conn.executescript(DDL)

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
                    (name, '%s（083B 党员发展）' % name, now))
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
        conn.commit()
    finally:
        conn.close()
    return stats


def dump_sql(db_path=None) -> str:
    """导出纯 SQL（供 sqlite3 CLI 手工重放；同样幂等）。"""
    return DDL + '\n' + _dict_dml()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    parser.add_argument('--print-sql', action='store_true', help='只输出等价 SQL，不写库')
    args = parser.parse_args()
    if args.print_sql:
        print(dump_sql(args.db))
        return
    stats = run(args.db)
    print('[m041] 目标库：%s' % stats['db'])
    print('[m041] 新建分类：%s' % (', '.join(stats['new_categories']) or '无'))
    print('[m041] 新增字典项：%s' % stats['new_items'])
    print('[m041] 已有分类跳过：%s' % (', '.join(stats['skipped_categories']) or '无'))


if __name__ == '__main__':
    main()
