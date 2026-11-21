#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m029_key_personnel_categories.py — 094 B-9 重点人员字典补种（幂等迁移）。

三类重点人员台账（社区矫正 / 刑满释放安置帮教 / 社区戒毒康复）复用
governance 配置驱动机制，零 DDL：本迁移只做字典 DML（dict_categories /
dict_items），不建表、不改列。

- 新库由 app.py 启动守卫 ensure_enum_dicts（种子真源 utils/dict_seed.ENUM_SEEDS）
  建齐；本迁移用于存量库补种与显式重放。
- 缺分类建分类、缺项补项；已存在项不重复、不覆盖；重复执行结果一致。
- 字面量与 utils/dict_seed.ENUM_SEEDS 对应 7 分类保持同值（不得另造枚举真源）。

执行（先备份库）：
    python scripts/migrations/m029_key_personnel_categories.py --db shucun.db
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
    '重点人员类别': ['社区矫正人员', '刑满释放安置帮教人员', '社区戒毒康复人员'],
    '矫正类别': ['管制', '缓刑', '假释', '暂予监外执行'],
    '帮教类型': ['刑满释放', '假释', '暂予监外执行', '其他'],
    '戒毒阶段': ['社区戒毒', '社区康复', '解除'],
    '矫正管控状态': ['正常', '重点管控', '脱管', '已解除'],
    '帮教管控状态': ['正常', '重点帮教', '失联', '已解除'],
    '戒毒管控状态': ['正常', '重点管控', '脱失', '已解除'],
}


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def run(db_path=None) -> dict:
    conn = sqlite3.connect(_db_path(db_path))
    stats = {'new_categories': [], 'new_items': 0, 'skipped_categories': []}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        if not {row[1] for row in conn.execute('PRAGMA table_info(dict_categories)')}:
            raise RuntimeError('dict_categories 表不存在：请先在目标库 create_all')
        if not {row[1] for row in conn.execute('PRAGMA table_info(dict_items)')}:
            raise RuntimeError('dict_items 表不存在：请先在目标库 create_all')

        max_sort = conn.execute(
            'SELECT COALESCE(MAX(sort_order), 0) FROM dict_categories').fetchone()[0] or 0
        for name, labels in CATEGORIES.items():
            row = conn.execute(
                'SELECT id FROM dict_categories WHERE name = ?', (name,)).fetchone()
            if row:
                cat_id = row[0]
                stats['skipped_categories'].append(name)
            else:
                max_sort += 10
                cur = conn.execute(
                    'INSERT INTO dict_categories (name, description, is_active, sort_order, created_at) '
                    'VALUES (?, ?, 1, ?, ?)', (name, name, max_sort, now))
                cat_id = cur.lastrowid
                stats['new_categories'].append(name)
            existing = {r[0] for r in conn.execute(
                'SELECT label FROM dict_items WHERE category_id = ?', (cat_id,))}
            for idx, label in enumerate(labels):
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    args = parser.parse_args()
    stats = run(args.db)
    print('[m029] 新建分类：%s' % (', '.join(stats['new_categories']) or '无'))
    print('[m029] 新增字典项：%s' % stats['new_items'])
    print('[m029] 已有分类跳过：%s' % (', '.join(stats['skipped_categories']) or '无'))


if __name__ == '__main__':
    main()
