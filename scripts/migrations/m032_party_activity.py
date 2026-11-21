#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m032_party_activity.py — 083A 党务活动建表 + 字典补种（幂等迁移）。

两张表（轨 A：存量库脚本执行；新库由 create_all 直建，本脚本可重放）：
- party_activity         党务活动主表（三类活动固定单表多类型，§9.4 裁决不走软表）
- party_activity_review  民主评议逐人子表（年度+党员唯一，等次 upsert）

字典 DML 与 utils/dict_seed.DICT_SEEDS 同值（唯一真源，不得另造枚举），共 2 分类：
- 党务活动类型：党员教育 / 组织生活 / 党风廉政（立项规划 §三.083A「三合一」）
- 民主评议等次：优秀 / 合格 / 基本合格 / 不合格（【待确认-上级口径】候选值）

新库由 app.py 启动守卫 ensure_enum_dicts 建齐；本迁移用于存量库补种与显式重放。

约束：
- 只做 CREATE TABLE/INDEX IF NOT EXISTS + 字典守卫 DML，不改既有表、不改既有列
  （party_members 零改动，复用 080 四维）。
- 缺分类建分类、缺项补项；已存在项不重复、不覆盖；重复执行结果一致。
- 不采集主持人身份证 / 联系电话（任务书约束，无 R12 加密列）。

执行（先备份库）：
    python scripts/migrations/m032_party_activity.py --db shucun.db
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

# 字典用值（2 分类，与 utils/dict_seed.DICT_SEEDS 同值）
CATEGORIES = {
    '党务活动类型': ['党员教育', '组织生活', '党风廉政'],
    '民主评议等次': ['优秀', '合格', '基本合格', '不合格'],
}

DDL = """
CREATE TABLE IF NOT EXISTS party_activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  activity_type VARCHAR(30) NOT NULL,
  title VARCHAR(100) NOT NULL,
  activity_date DATE NOT NULL,
  host VARCHAR(50),
  expected_count INTEGER DEFAULT 0,
  actual_count INTEGER DEFAULT 0,
  branch VARCHAR(100),
  content TEXT,
  attachment_path VARCHAR(200),
  remark TEXT,
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_party_activity_activity_type
  ON party_activity (activity_type);
CREATE INDEX IF NOT EXISTS ix_party_activity_activity_date
  ON party_activity (activity_date);
CREATE INDEX IF NOT EXISTS ix_party_activity_branch
  ON party_activity (branch);

CREATE TABLE IF NOT EXISTS party_activity_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  year INTEGER NOT NULL,
  party_member_id INTEGER NOT NULL,
  grade VARCHAR(30) NOT NULL,
  review_date DATE,
  remark TEXT,
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME,
  FOREIGN KEY (party_member_id) REFERENCES party_members (id)
);

-- 年度 + 党员唯一（模型 UniqueConstraint uq_party_review_year_member 对齐；重复提交走 upsert）
CREATE UNIQUE INDEX IF NOT EXISTS uq_party_review_year_member
  ON party_activity_review (year, party_member_id);
CREATE INDEX IF NOT EXISTS ix_party_activity_review_party_member_id
  ON party_activity_review (party_member_id);
CREATE INDEX IF NOT EXISTS ix_party_activity_review_year
  ON party_activity_review (year);
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
        DICT_DML.format(name=name, desc='%s（083A 党务活动台账）' % name,
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
                    (name, '%s（083A 党务活动台账）' % name, now))
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
    print('[m032] 目标库：%s' % stats['db'])
    print('[m032] 新建分类：%s' % (', '.join(stats['new_categories']) or '无'))
    print('[m032] 新增字典项：%s' % stats['new_items'])
    print('[m032] 已有分类跳过：%s' % (', '.join(stats['skipped_categories']) or '无'))


if __name__ == '__main__':
    main()
