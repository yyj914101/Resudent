#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m038_pension.py — 088 养老保险字典补录（幂等迁移）。

为养老段补 3 个字典分类：养老保险类型 / 养老保险参保状态 / 养老待遇认证状态。
取值与 utils/dict_seed.DICT_SEEDS 同值（唯一真源），不得另造枚举；
命名对齐医保段既有口径（参保情况/待遇认证状态），值不复用医保字典（独立分类）。

⚠️ dynamic_table 元数据行不在本迁移处理——m031 已预置 pension_roster 行
   （m031 是台账元数据行的唯一预置点），本迁移与 m031 的 dynamic_table 行零重复；
   dynamic_field 行按 ADR-025 决策 3 由首次上传引导确认生成，同样不在此预置。

铁律：
- 只做 dict_categories / dict_items 的幂等补录（NOT EXISTS，已存在即跳过）；
- 不插 dynamic_table / dynamic_field 行；
- 不删不改任何既有行。

执行（先备份库）：
    python scripts/migrations/m038_pension.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

# 与 utils/dict_seed.DICT_SEEDS 的 088 段同值（唯一真源，label=value）
PENSION_DICT_SEEDS = [
    ('养老保险类型', ['职工养老', '城乡居民养老', '其他'], '088 养老保险：参保类型'),
    ('养老保险参保状态', ['正常参保', '暂停参保', '未参保'], '088 养老保险：参保状态'),
    ('养老待遇认证状态', ['已认证', '未认证', '待认证'], '088 养老保险：待遇认证'),
]


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def run(db_path=None) -> dict:
    conn = sqlite3.connect(_db_path(db_path))
    stats = {'categories': [], 'items': [], 'skipped_categories': [],
             'skipped_items': []}
    try:
        # 守卫：字典表不存在则拒绝（create_all 真源）
        cols = {row[1] for row in conn.execute('PRAGMA table_info(dict_categories)')}
        if not cols:
            raise RuntimeError('dict_categories 表不存在：请先在目标库 create_all')
        cols = {row[1] for row in conn.execute('PRAGMA table_info(dict_items)')}
        if not cols:
            raise RuntimeError('dict_items 表不存在：请先在目标库 create_all')

        for name, labels, description in PENSION_DICT_SEEDS:
            # 分类幂等：缺则建（sort_order 接队尾），已存在不改
            row = conn.execute(
                'SELECT id FROM dict_categories WHERE name = ?', (name,)).fetchone()
            if row is None:
                next_sort = conn.execute(
                    'SELECT COALESCE(MAX(sort_order), 0) + 10 FROM dict_categories'
                ).fetchone()[0]
                cur = conn.execute(
                    'INSERT INTO dict_categories (name, description, sort_order, is_active) '
                    'VALUES (?, ?, ?, 1)', (name, description, next_sort))
                cat_id = cur.lastrowid
                stats['categories'].append(name)
            else:
                cat_id = row[0]
                stats['skipped_categories'].append(name)

            # 条目幂等：按分类 + label 查重（label=value，与 DICT_SEEDS 同值）
            for idx, label in enumerate(labels):
                exists = conn.execute(
                    'SELECT id FROM dict_items WHERE category_id = ? AND label = ?',
                    (cat_id, label)).fetchone()
                if exists:
                    stats['skipped_items'].append('%s:%s' % (name, label))
                    continue
                conn.execute(
                    'INSERT INTO dict_items '
                    '(category_id, label, value, sort_order, is_active) '
                    'VALUES (?, ?, ?, ?, 1)', (cat_id, label, label, idx))
                stats['items'].append('%s:%s' % (name, label))
        conn.commit()
    finally:
        conn.close()
    return stats


def dump_sql() -> str:
    """导出等价 SQL（供 sqlite3 CLI 手工重放；NOT EXISTS 幂等同款语义）。"""
    lines = []
    for name, labels, description in PENSION_DICT_SEEDS:
        lines.append("-- %s（%s）" % (name, description))
        lines.append(
            "INSERT INTO dict_categories (name, description, sort_order, is_active) "
            "SELECT '%s', '%s', COALESCE(MAX(sort_order), 0) + 10, 1 "
            "FROM dict_categories "
            "WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = '%s');"
            % (name, description, name))
        for idx, label in enumerate(labels):
            lines.append(
                "INSERT INTO dict_items (category_id, label, value, sort_order, is_active) "
                "SELECT c.id, '%s', '%s', %d, 1 FROM dict_categories c "
                "WHERE c.name = '%s' AND NOT EXISTS ("
                "  SELECT 1 FROM dict_items i WHERE i.category_id = c.id AND i.label = '%s');"
                % (label, label, idx, name, label))
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
    print('[m038] 新建分类：%s' % (', '.join(stats['categories']) or '无'))
    print('[m038] 新增条目：%s' % (', '.join(stats['items']) or '无'))
    print('[m038] 已存在跳过：分类 %d 个 / 条目 %d 条'
          % (len(stats['skipped_categories']), len(stats['skipped_items'])))


if __name__ == '__main__':
    main()
