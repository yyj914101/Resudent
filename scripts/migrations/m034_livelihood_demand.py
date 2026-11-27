#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m034_livelihood_demand.py — 090A 民生诉求建表 + 字典补录（幂等迁移）。

建 demand（受理主表）+ demand_handle（处理记录子表，级联删除），
补「诉求来源 / 诉求类别 / 诉求办理状态」3 个字典分类：
- 字面量唯一真源 utils/dict_seed.DICT_SEEDS（本脚本只读取不另造枚举，m027 先例）；
- 「诉求来源」value 编码（一页纸 v1.1 D-01：12345/visit/grid/phone/onsite/other），
  其余分类 value=label；「诉求满意度」整组随一页纸 D-04 删除，不落库。

铁律：
- 只做 CREATE TABLE/INDEX IF NOT EXISTS + 字典守卫 DML（NOT EXISTS，已存在即跳过）；
- 不改既有表/列（结构真源 models/demand.py 与 create_all 对齐）；
- 幂等可重放（验收 6）。

执行（先备份库）：
    python scripts/migrations/m034_livelihood_demand.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

# 090A 三个字典分类（取值/排序读 DICT_SEEDS 唯一真源，元组 (label, value) 见 088/090A 段）
DEMAND_DICT_CATEGORIES = ('诉求来源', '诉求类别', '诉求办理状态')

# 列结构对齐 models/demand.py（除自增 id 与 relationship）；
# 敏感列 R12 双列口径：明文列 + *_enc 密文 + *_hash 等值检索（hash 均非唯一）。
DDL = """
CREATE TABLE IF NOT EXISTS demand (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source VARCHAR(30) NOT NULL,
    category VARCHAR(30) NOT NULL,
    petitioner_name VARCHAR(50) NOT NULL,
    contact VARCHAR(30),
    contact_enc TEXT,
    contact_hash VARCHAR(64),
    id_card VARCHAR(20),
    id_card_enc TEXT,
    id_card_hash VARCHAR(64),
    address VARCHAR(200),
    summary TEXT NOT NULL,
    accept_date DATE NOT NULL,
    case_officer VARCHAR(50),
    status VARCHAR(30) NOT NULL DEFAULT '待受理',
    close_date DATE,
    remark TEXT,
    created_by VARCHAR(50),
    created_at DATETIME,
    updated_at DATETIME
);
CREATE INDEX IF NOT EXISTS ix_demand_source ON demand (source);
CREATE INDEX IF NOT EXISTS ix_demand_category ON demand (category);
CREATE INDEX IF NOT EXISTS ix_demand_accept_date ON demand (accept_date);
CREATE INDEX IF NOT EXISTS ix_demand_status ON demand (status);
CREATE INDEX IF NOT EXISTS ix_demand_contact_hash ON demand (contact_hash);
CREATE INDEX IF NOT EXISTS ix_demand_id_card_hash ON demand (id_card_hash);
CREATE TABLE IF NOT EXISTS demand_handle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    demand_id INTEGER NOT NULL,
    handle_date DATE NOT NULL,
    handler VARCHAR(50) NOT NULL,
    action TEXT NOT NULL,
    created_at DATETIME,
    FOREIGN KEY (demand_id) REFERENCES demand (id)
);
CREATE INDEX IF NOT EXISTS ix_demand_handle_demand_id ON demand_handle (demand_id);
"""


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def _demand_seeds():
    """DICT_SEEDS → [(分类名, [(label, value)...])...]（只读取唯一真源，缺分类即报错）。"""
    from utils.dict_seed import DICT_SEEDS, seed_pair
    try:
        return [(name, [seed_pair(item) for item in DICT_SEEDS[name]])
                for name in DEMAND_DICT_CATEGORIES]
    except KeyError as exc:
        raise RuntimeError(f'utils/dict_seed.DICT_SEEDS 缺少分类 {exc}: '
                           '请先更新字典真源') from exc


def run(db_path=None) -> dict:
    seeds = _demand_seeds()
    conn = sqlite3.connect(_db_path(db_path))
    stats = {'tables': [], 'categories': [], 'items': [],
             'skipped_tables': [], 'skipped_categories': [], 'skipped_items': 0}
    try:
        # 守卫：字典表不存在则拒绝（create_all 真源）
        cols = {row[1] for row in conn.execute('PRAGMA table_info(dict_categories)')}
        if not cols:
            raise RuntimeError('dict_categories 表不存在：请先在目标库 create_all')

        conn.executescript(DDL)
        conn.commit()
        for table in ('demand', 'demand_handle'):
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            (stats['tables'] if exists else stats['skipped_tables']).append(table)

        for name, pairs in seeds:
            # 分类幂等：缺则建（sort_order 接队尾），已存在不改
            row = conn.execute(
                'SELECT id FROM dict_categories WHERE name = ?', (name,)).fetchone()
            if row is None:
                next_sort = conn.execute(
                    'SELECT COALESCE(MAX(sort_order), 0) + 10 FROM dict_categories'
                ).fetchone()[0]
                cur = conn.execute(
                    'INSERT INTO dict_categories (name, description, sort_order, is_active) '
                    'VALUES (?, ?, ?, 1)', (name, '090A 民生诉求：%s' % name, next_sort))
                cat_id = cur.lastrowid
                stats['categories'].append(name)
            else:
                cat_id = row[0]
                stats['skipped_categories'].append(name)

            # 条目幂等：按分类 + label 查重；value 按种子（诉求来源编码，其余 =label）
            for idx, (label, value) in enumerate(pairs):
                exists = conn.execute(
                    'SELECT id FROM dict_items WHERE category_id = ? AND label = ?',
                    (cat_id, label)).fetchone()
                if exists:
                    stats['skipped_items'] += 1
                    continue
                conn.execute(
                    'INSERT INTO dict_items '
                    '(category_id, label, value, sort_order, is_active) '
                    'VALUES (?, ?, ?, ?, 1)', (cat_id, label, value, idx))
                stats['items'].append('%s:%s' % (name, label))
        conn.commit()
    finally:
        conn.close()
    return stats


def dump_sql() -> str:
    """导出等价 SQL（供 sqlite3 CLI 手工重放；IF NOT EXISTS / NOT EXISTS 幂等同款语义）。"""
    seeds = _demand_seeds()
    lines = [DDL]
    for name, pairs in seeds:
        lines.append("-- %s（090A 民生诉求）" % name)
        lines.append(
            "INSERT INTO dict_categories (name, description, sort_order, is_active) "
            "SELECT '%s', '090A 民生诉求：%s', COALESCE(MAX(sort_order), 0) + 10, 1 "
            "FROM dict_categories "
            "WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = '%s');"
            % (name, name, name))
        for idx, (label, value) in enumerate(pairs):
            lines.append(
                "INSERT INTO dict_items (category_id, label, value, sort_order, is_active) "
                "SELECT c.id, '%s', '%s', %d, 1 FROM dict_categories c "
                "WHERE c.name = '%s' AND NOT EXISTS ("
                "  SELECT 1 FROM dict_items i WHERE i.category_id = c.id AND i.label = '%s');"
                % (label, value, idx, name, label))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    parser.add_argument('--print-sql', action='store_true', help='打印等价 SQL 后退出')
    args = parser.parse_args()
    if args.print_sql:
        print(dump_sql())
        return
    stats = run(args.db)
    print('[m034] 建表：%s' % (', '.join(stats['tables']) or '无'))
    print('[m034] 新建分类：%s' % (', '.join(stats['categories']) or '无'))
    print('[m034] 新增条目：%s' % (', '.join(stats['items']) or '无'))
    print('[m034] 已存在跳过：表 %d / 分类 %d / 条目 %d'
          % (len(stats['skipped_tables']), len(stats['skipped_categories']),
             stats['skipped_items']))


if __name__ == '__main__':
    main()
