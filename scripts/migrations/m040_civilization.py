#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m040_civilization.py — 089 文明实践站建表 + 字典补种（幂等迁移）。

一张表（轨 A：存量库脚本执行；新库由 create_all 直建表体 + 本脚本重放补索引，
索引集合与存量库一致，m036 先例）：
- civ_convention  居民公约版本固定表（「同一时刻仅一个现行」→ 部分唯一索引；
  版本号/生效日期已按一页纸 v1.2 裁定删除，历史按 created_at 排序）

唯一现行约束（任务书验收 1，DB + 应用层双保险）：
- DB 侧：CREATE UNIQUE INDEX uq_civ_convention_current ON civ_convention (is_current)
  WHERE is_current = 1 —— 部分唯一索引只约束现行行，历史行不限制；
- 应用侧：routes/civilization.py 换版同事务互斥（新增现行先把旧行置历史）。
  模型侧不声明该索引（R-093-01 双索引漂移纪律，索引唯一来源本 DDL）。

字典 DML 与 utils/dict_seed.DICT_SEEDS 同值（唯一真源，不得另造枚举），共 1 分类：
- 文明活动类型：理论宣讲 / 教育服务 / 文化服务 / 科技科普 / 健身体育（Q-A 裁定）

⚠️ dynamic_table 元数据行不在本迁移处理——m031 已预置 civilization_activity 行
   （m031 是台账元数据行的唯一预置点，m040 不得再插同 code 行）；
   dynamic_field 行按 ADR-025 决策 3 由首次上传引导确认生成，同样不在此预置。
   不建「公约状态」字典（Q-01 方案 A：is_current 唯一真源）。

约束：
- 只做 CREATE TABLE/INDEX IF NOT EXISTS + 字典守卫 DML，不改既有表、不改既有列。
- 缺分类建分类、缺项补项；已存在项不重复、不覆盖；重复执行结果一致。

执行（先备份库）：
    python scripts/migrations/m040_civilization.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）；--print-sql 供 sqlite3 CLI 重放。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

# 字典用值（1 分类，与 utils/dict_seed.DICT_SEEDS 089 段同值）
CATEGORIES = {
    '文明活动类型': ['理论宣讲', '教育服务', '文化服务', '科技科普', '健身体育'],
}

DDL = """
CREATE TABLE IF NOT EXISTS civ_convention (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title VARCHAR(100) NOT NULL,
  content TEXT,
  attachment_path VARCHAR(255),
  is_current BOOLEAN NOT NULL DEFAULT 0,
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

-- 唯一现行（任务书交付物）：部分唯一索引只约束 is_current = 1 的行，
-- 历史行（is_current = 0）不限制；模型侧不声明，索引唯一来源本 DDL。
CREATE UNIQUE INDEX IF NOT EXISTS uq_civ_convention_current
  ON civ_convention (is_current) WHERE is_current = 1;
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
        DICT_DML.format(name=name, desc='%s（089 文明实践站活动记录）' % name,
                        seed_rows=_seed_rows(labels))
        for name, labels in CATEGORIES.items()
    )


def run(db_path=None) -> dict:
    path = _db_path(db_path)
    conn = sqlite3.connect(path)
    stats = {'db': path, 'table_created': False, 'index_created': False,
             'new_categories': [], 'new_items': 0, 'skipped_categories': []}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'dict_categories' not in tables or 'dict_items' not in tables:
            raise RuntimeError('dict_categories / dict_items 表不存在：请先在目标库 create_all')

        # 先采集 DDL 前的表/索引集合，执行后做差集才是「本次新建」（m036 同款）
        stats['table_created'] = 'civ_convention' not in tables
        have_before = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
        conn.executescript(DDL)
        stats['index_created'] = 'uq_civ_convention_current' not in have_before

        for name, labels in CATEGORIES.items():
            row = conn.execute('SELECT id FROM dict_categories WHERE name = ?', (name,)).fetchone()
            if row:
                cat_id = row[0]
                if name in {r[0] for r in conn.execute(
                        'SELECT name FROM dict_categories WHERE name = ?', (name,))}:
                    stats['skipped_categories'].append(name)
            else:
                cur = conn.execute(
                    'INSERT INTO dict_categories (name, description, is_active, sort_order, created_at) '
                    'VALUES (?, ?, 1, '
                    'COALESCE((SELECT MAX(sort_order) FROM dict_categories), 0) + 10, ?)',
                    (name, '%s（089 文明实践站活动记录）' % name, now))
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
    print('[m040] 目标库：%s' % stats['db'])
    print('[m040] 建表：%s' % ('新建 civ_convention' if stats['table_created'] else '已存在，跳过'))
    print('[m040] 唯一现行索引：%s' % ('本次新建 uq_civ_convention_current'
                                     if stats['index_created'] else '已存在，跳过'))
    print('[m040] 新建分类：%s' % (', '.join(stats['new_categories']) or '无'))
    print('[m040] 新增字典项：%s' % stats['new_items'])
    print('[m040] 已有分类跳过：%s' % (', '.join(stats['skipped_categories']) or '无'))


if __name__ == '__main__':
    main()
