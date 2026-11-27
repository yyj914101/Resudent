#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m039_venue_facility.py — 085 场所/设施字典补录（轨 A 幂等迁移）。

范围（085 任务书工作项 2 / 字典一页纸 v1.1 定稿）：
- 「缴费项目」分类：clean_fee（清洁费（街道门店）），value≠label，仅 1 项；
- 「设施类型」分类：清洁点 / 消防栓 / 路灯（value=label，与 utils/dict_seed.DICT_SEEDS 085 段同值）。

⚠️ dynamic_table 元数据行不在本迁移处理——m031 已预置 shop_facility / shop_annual_fee 两行
   （m031 是台账元数据行的唯一预置点），本迁移与 m031 零重复；
   dynamic_field 行按 ADR-025 决策 3 由首次上传引导确认生成，同样不在此预置。

铁律（m038 同款）：
- 只做 dict_categories / dict_items 的幂等补录（已存在即跳过，不删不改既有行）；
- 不插 dynamic_table / dynamic_field 行；
- 新库由 init_db 结构化种子（缴费项目 (value,label) 元组）+ DICT_SEEDS（设施类型）建齐。

执行（先备份库）：
    python scripts/migrations/m039_venue_facility.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

# (分类名, description, [(value, label), ...])；设施类型 value=label，与 DICT_SEEDS 同值
VENUE_DICT_SEEDS = [
    ('缴费项目', '085 场所/设施：重点场所年度缴费项目（一页纸 v1.1 §1）',
     [('clean_fee', '清洁费（街道门店）')]),
    ('设施类型', '085 场所/设施：基础设施分布类型（一页纸 v1.1 §2）',
     [('清洁点', '清洁点'), ('消防栓', '消防栓'), ('路灯', '路灯')]),
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
        # 守卫：字典表不存在则拒绝（create_all 真源，m038 同款）
        cols = {row[1] for row in conn.execute('PRAGMA table_info(dict_categories)')}
        if not cols:
            raise RuntimeError('dict_categories 表不存在：请先在目标库 create_all')
        cols = {row[1] for row in conn.execute('PRAGMA table_info(dict_items)')}
        if not cols:
            raise RuntimeError('dict_items 表不存在：请先在目标库 create_all')

        for name, description, entries in VENUE_DICT_SEEDS:
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

            # 条目幂等：按分类 + value 查重（value 是编码真源；label 可改、code 不变）
            for idx, (value, label) in enumerate(entries):
                exists = conn.execute(
                    'SELECT id FROM dict_items WHERE category_id = ? AND value = ?',
                    (cat_id, value)).fetchone()
                if exists:
                    stats['skipped_items'].append('%s:%s' % (name, value))
                    continue
                conn.execute(
                    'INSERT INTO dict_items '
                    '(category_id, label, value, sort_order, is_active) '
                    'VALUES (?, ?, ?, ?, 1)', (cat_id, label, value, idx))
                stats['items'].append('%s:%s' % (name, value))
        conn.commit()
    finally:
        conn.close()
    return stats


def dump_sql() -> str:
    """导出等价 SQL（供 sqlite3 CLI 手工重放；幂等同款语义）。"""
    lines = []
    for name, description, entries in VENUE_DICT_SEEDS:
        lines.append("-- %s（%s）" % (name, description))
        lines.append(
            "INSERT INTO dict_categories (name, description, sort_order, is_active) "
            "SELECT '%s', '%s', COALESCE(MAX(sort_order), 0) + 10, 1 "
            "FROM dict_categories "
            "WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = '%s');"
            % (name, description, name))
        for idx, (value, label) in enumerate(entries):
            lines.append(
                "INSERT INTO dict_items (category_id, label, value, sort_order, is_active) "
                "SELECT c.id, '%s', '%s', %d, 1 FROM dict_categories c "
                "WHERE c.name = '%s' AND NOT EXISTS ("
                "  SELECT 1 FROM dict_items i WHERE i.category_id = c.id AND i.value = '%s');"
                % (label, value, idx, name, value))
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
    print('[m039] 新建分类：%s' % (', '.join(stats['categories']) or '无'))
    print('[m039] 新增条目：%s' % (', '.join(stats['items']) or '无'))
    print('[m039] 已存在跳过：分类 %d 个 / 条目 %d 条'
          % (len(stats['skipped_categories']), len(stats['skipped_items'])))


if __name__ == '__main__':
    main()
