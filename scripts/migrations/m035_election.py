#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m035_election.py — 090B 公共事务·选举三表建表 + 字典补种（幂等迁移）。

三张固定表（ADR-028 判①：届次有状态机 → 固定表；轨 A：存量库手工/脚本执行；
新库由 create_all 直建，本脚本可重放；一页纸字段清单 v1.2 定稿后才允许合并）：
- election_round     届次（状态机 筹备→选民登记→投票→结果公示→已完成，§6）
- election_voter     选民名册（同届次同人唯一 uq_election_voter_round_hash；V-007 凭证号已删除）
- election_result    当选结果（E-004 得票 ≥0）

字典 DML 与 utils/dict_seed.DICT_SEEDS 的 090B 段同值（唯一真源，不得另造枚举）：
选举届次状态 / 选民登记状态 / 当选职务 共 3 分类。新库由 app.py 启动守卫
ensure_enum_dicts 建齐；本迁移用于存量库补种与显式重放。

约束（对齐 m037/m038）：
- 只做 CREATE TABLE/INDEX IF NOT EXISTS + 字典守卫 DML，不改既有表、不改既有列。
- uq_election_voter_round_hash：模型侧 UniqueConstraint 在 create_all 库落成同列
  sqlite_autoindex_*，本脚本先探测同列唯一约束、存在则复用不重建（_equivalent_unique_index），
  保证两条建库路径最终 schema 一致。
- 缺分类建分类、缺项补项；已存在项不重复、不覆盖；重复执行结果一致。

执行（先备份库）：
    python scripts/migrations/m035_election.py --db shucun.db
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

# 与 utils/dict_seed.DICT_SEEDS 的 090B 段同值（唯一真源，label=value）
ELECTION_DICT_SEEDS = [
    ('选举届次状态', ['筹备', '选民登记', '投票', '结果公示', '已完成']),
    ('选民登记状态', ['已登记', '未登记', '委托投票', '外出']),
    ('当选职务', ['村委会主任', '村委会副主任', '村委会委员', '其他']),
]

DDL = """
CREATE TABLE IF NOT EXISTS election_round (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round_name VARCHAR(100) NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE,
  register_start DATE NOT NULL,
  register_end DATE NOT NULL,
  vote_date DATE NOT NULL,
  status VARCHAR(30) DEFAULT '筹备',
  remark TEXT,
  created_at DATETIME,
  updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS election_voter (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round_id INTEGER NOT NULL,
  name VARCHAR(50) NOT NULL,
  id_card_enc TEXT,
  id_card_hash VARCHAR(64) NOT NULL,
  group_name VARCHAR(50),
  register_status VARCHAR(30) DEFAULT '未登记',
  is_out BOOLEAN,
  proxy_name VARCHAR(50),
  mobile_box BOOLEAN,
  phone_enc TEXT,
  phone_hash VARCHAR(64),
  resident_hash VARCHAR(64),
  remark TEXT,
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_election_voter_round
  ON election_voter (round_id);
CREATE INDEX IF NOT EXISTS idx_election_voter_hash
  ON election_voter (id_card_hash);

CREATE TABLE IF NOT EXISTS election_result (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  round_id INTEGER NOT NULL,
  position VARCHAR(30) NOT NULL,
  winner_name VARCHAR(50) NOT NULL,
  winner_id_card_hash VARCHAR(64),
  votes INTEGER DEFAULT 0,
  is_elected BOOLEAN,
  notice_date DATE,
  remark TEXT,
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_election_result_round
  ON election_result (round_id);
"""

# uq_election_voter_round_hash 的列序（与模型 UniqueConstraint 完全一致，用于等价探测）
VOTER_UNIQUE_COLS = ('round_id', 'id_card_hash')
VOTER_UNIQUE_SQL = ('CREATE UNIQUE INDEX IF NOT EXISTS uq_election_voter_round_hash '
                    'ON election_voter (round_id, id_card_hash)')

# 字典守卫 DML（值 = 标签 = 一页纸 §5 字面量；分类/条目均 WHERE NOT EXISTS 幂等）
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
        DICT_DML.format(name=name, desc='090B 公共事务·选举：%s' % name, seed_rows=_seed_rows(labels))
        for name, labels in ELECTION_DICT_SEEDS
    )


def _equivalent_unique_index(conn, table, cols):
    """返回已在 (cols) 上生效的唯一索引名（含 sqlite_autoindex_*），无则 None。

    用于避免在 create_all 建好的库上叠加同列冗余唯一索引（m037 同款）。
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
    stats = {'db': path, 'tables_created': [], 'uq_election_voter_round_hash': '',
             'new_categories': [], 'skipped_categories': [], 'new_items': 0}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'dict_categories' not in tables or 'dict_items' not in tables:
            raise RuntimeError('dict_categories / dict_items 表不存在：请先在目标库 create_all')

        # 先采集 DDL 前的表/索引集合，执行后做差集才是「本次新建」
        tables_before = set(tables)
        conn.executescript(DDL)
        stats['tables_created'] = sorted(
            {'election_round', 'election_voter', 'election_result'} - tables_before)

        # uq_election_voter_round_hash：先探测同列唯一约束，已存在（create_all 建的
        # sqlite_autoindex_*）则复用不重建，保证两条建库路径的最终 schema 一致。
        existing_uq = _equivalent_unique_index(conn, 'election_voter', VOTER_UNIQUE_COLS)
        if existing_uq:
            stats['uq_election_voter_round_hash'] = 'reuse:%s' % existing_uq
        else:
            conn.execute(VOTER_UNIQUE_SQL)
            stats['uq_election_voter_round_hash'] = 'created:uq_election_voter_round_hash'

        # 字典守卫补种（Python 版为执行真源；DML 版本供 sqlite3 CLI 人工重放）
        before = {row[0] for row in conn.execute(
            "SELECT name FROM dict_categories WHERE name IN (%s)"
            % ','.join("'%s'" % n for n, _ in ELECTION_DICT_SEEDS))}
        for name, labels in ELECTION_DICT_SEEDS:
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
                    (name, '090B 公共事务·选举：%s' % name, now))
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
    """导出纯 SQL（供 sqlite3 CLI 手工重放；同样幂等）。

    注意：纯 SQL 无法探测「同列唯一约束是否已存在」，故唯一索引一律输出。
    若目标库是由 create_all 建的（已有同列 sqlite_autoindex_*），执行后会多一个
    等价冗余唯一索引（不影响唯一性语义）；要完全对齐请改用
    `python scripts/migrations/m035_election.py --db <库>`，它会先探测后建。
    """
    return (DDL + '\n'
            '-- 若库由 create_all 建表（同列唯一约束已存在），下一行会产生等价冗余索引\n'
            + VOTER_UNIQUE_SQL + ';\n\n' + _dict_dml())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    parser.add_argument('--print-sql', action='store_true', help='只输出等价 SQL，不写库')
    args = parser.parse_args()
    if args.print_sql:
        print(dump_sql(args.db))
        return
    stats = run(args.db)
    print('[m035] 目标库：%s' % stats['db'])
    print('[m035] 新建表：%s' % (', '.join(stats['tables_created']) or '无（已存在，跳过）'))
    print('[m035] 唯一索引 uq_election_voter_round_hash：%s' % stats['uq_election_voter_round_hash'])
    print('[m035] 新建分类：%s' % (', '.join(stats['new_categories']) or '无'))
    print('[m035] 新增字典项：%s' % stats['new_items'])
    print('[m035] 已有分类跳过：%s' % (', '.join(stats['skipped_categories']) or '无'))


if __name__ == '__main__':
    main()
