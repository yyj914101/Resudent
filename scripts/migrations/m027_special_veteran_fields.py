#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m027_special_veteran_fields.py — 093 B-6/B-7/B-8 特殊群体服务与退役类别（ADR-018 轨 A 幂等迁移）。

任务 093 数据底座三部分：
- B-6：残疾人服务事项 1:N 服务记录表 ``disabled_service_records``
  （``disabled_id`` 外键 + 索引；列含 service_item / service_status /
  service_progress / service_owner / service_date / remark / 时间戳）。
  不设软失效 status 列，服务记录直接物理删除。
- B-7：老年人服务事项 1:N 服务记录表 ``elder_service_records``
  （``care_target_id`` 外键 + 索引，其余字段与 B-6 同构）。
- B-8：``veteran.retire_category``（退役类别，字典分类「退役类别」）；
  列 DDL ``VARCHAR(30)``，可空；**存量不猜测、不回填**，保持 NULL。

另补种字典四分类（残疾人服务事项 / 老年人服务事项 / 服务办理状态 / 退役类别），
字面量唯一真源为 ``utils.dict_seed.DICT_SEEDS``，本脚本只读取不另造枚举。
缺分类建分类、缺项补项；已存在的不重复、不覆盖。

幂等性（ADR-018 轨 A）：建表用 ``CREATE TABLE IF NOT EXISTS``、索引用
``CREATE INDEX IF NOT EXISTS``、加列按 m021/m023/m026 的 PRAGMA 检查式，
重复执行只跳过、不报错。新库由 create_all 建齐；存量库经本脚本手工迁移。

执行（先备份库）：
    python scripts/migrations/m027_special_veteran_fields.py --db shucun.db
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

from utils.dict_seed import DICT_SEEDS

TABLES_DDL = {
    'disabled_service_records': """
        CREATE TABLE IF NOT EXISTS disabled_service_records (
            id INTEGER PRIMARY KEY,
            disabled_id INTEGER NOT NULL REFERENCES disabled_persons(id),
            service_item VARCHAR(30) NOT NULL,
            service_status VARCHAR(20) NOT NULL DEFAULT '待受理',
            service_progress VARCHAR(200),
            service_owner VARCHAR(50),
            service_date DATE,
            remark VARCHAR(200),
            created_at DATETIME,
            updated_at DATETIME
        )""",
    'elder_service_records': """
        CREATE TABLE IF NOT EXISTS elder_service_records (
            id INTEGER PRIMARY KEY,
            care_target_id INTEGER NOT NULL REFERENCES care_targets(id),
            service_item VARCHAR(30) NOT NULL,
            service_status VARCHAR(20) NOT NULL DEFAULT '待受理',
            service_progress VARCHAR(200),
            service_owner VARCHAR(50),
            service_date DATE,
            remark VARCHAR(200),
            created_at DATETIME,
            updated_at DATETIME
        )""",
}

# 子表 → 父表（父表缺失时提示先 create_all，与 m026 错误风格一致）
PARENT_TABLES = {
    'disabled_service_records': 'disabled_persons',
    'elder_service_records': 'care_targets',
}

INDEXES = {
    'idx_disabled_service_records_disabled': ('disabled_service_records', 'disabled_id'),
    'idx_elder_service_records_care_target': ('elder_service_records', 'care_target_id'),
}

# 093 B-6/B-7/B-8 新增的四个字典分类（字面量取自 DICT_SEEDS，不另造真源）
NEW_CATEGORY_NAMES = ('残疾人服务事项', '老年人服务事项', '服务办理状态', '退役类别')


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def _seed_dicts(conn, stats) -> None:
    """对 093 四分类做幂等补种（分类缺失插入、项缺失插入、已存在跳过）。"""
    if not {row[1] for row in conn.execute('PRAGMA table_info(dict_categories)')}:
        raise RuntimeError('dict_categories 表不存在：请先在目标库 create_all')
    if not {row[1] for row in conn.execute('PRAGMA table_info(dict_items)')}:
        raise RuntimeError('dict_items 表不存在：请先在目标库 create_all')

    try:
        categories = {name: DICT_SEEDS[name] for name in NEW_CATEGORY_NAMES}
    except KeyError as exc:
        raise RuntimeError(f'utils/dict_seed.DICT_SEEDS 缺少分类 {exc}：请先更新字典真源') from exc

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    max_sort = conn.execute(
        'SELECT COALESCE(MAX(sort_order), 0) FROM dict_categories').fetchone()[0] or 0
    for name, labels in categories.items():
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
                stats['skipped_items'] += 1
                continue
            conn.execute(
                'INSERT INTO dict_items (category_id, label, value, sort_order, is_active, created_at) '
                'VALUES (?, ?, ?, ?, 1, ?)', (cat_id, label, label, idx, now))
            existing.add(label)
            stats['new_items'] += 1


def run(db_path=None) -> dict:
    conn = sqlite3.connect(_db_path(db_path))
    stats = {
        'created_tables': [],
        'skipped_tables': [],
        'created_indexes': [],
        'skipped_indexes': [],
        'added_columns': [],
        'skipped_columns': [],
        'new_categories': [],
        'skipped_categories': [],
        'new_items': 0,
        'skipped_items': 0,
    }
    try:
        # 1) B-6/B-7 服务记录表 + 索引
        for table, ddl in TABLES_DDL.items():
            exists = bool({row[1] for row in conn.execute(f'PRAGMA table_info({table})')})
            if exists:
                stats['skipped_tables'].append(table)
            else:
                parent = PARENT_TABLES[table]
                if not {row[1] for row in conn.execute(f'PRAGMA table_info({parent})')}:
                    raise RuntimeError(f'{parent} 表不存在：请先在目标库 create_all')
                conn.execute(ddl)
                stats['created_tables'].append(table)

        for index_name, (table, column) in INDEXES.items():
            idx_names = {row[1] for row in conn.execute(f'PRAGMA index_list({table})')}
            if index_name in idx_names:
                stats['skipped_indexes'].append(index_name)
                continue
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column})')
            stats['created_indexes'].append(index_name)

        # 2) B-8 退役军人退役类别加列（PRAGMA 检查式，重复执行跳过）
        veteran_cols = {row[1] for row in conn.execute('PRAGMA table_info(veteran)')}
        if not veteran_cols:
            raise RuntimeError('veteran 表不存在：请先在目标库 create_all')
        if 'retire_category' in veteran_cols:
            stats['skipped_columns'].append('retire_category')
        else:
            conn.execute('ALTER TABLE veteran ADD COLUMN retire_category VARCHAR(30)')
            stats['added_columns'].append('retire_category')

        # 3) 字典四分类幂等补种
        _seed_dicts(conn, stats)

        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    args = parser.parse_args()
    stats = run(args.db)
    print('[m027] 新建表：%s' % (', '.join(stats['created_tables']) or '无'))
    print('[m027] 已有表跳过：%s' % (', '.join(stats['skipped_tables']) or '无'))
    print('[m027] 新建索引：%s' % (', '.join(stats['created_indexes']) or '无'))
    print('[m027] 已有索引跳过：%s' % (', '.join(stats['skipped_indexes']) or '无'))
    print('[m027] 加列：%s' % (', '.join(stats['added_columns']) or '无'))
    print('[m027] 已有列跳过：%s' % (', '.join(stats['skipped_columns']) or '无'))
    print('[m027] 新建字典分类：%s' % (', '.join(stats['new_categories']) or '无'))
    print('[m027] 新增字典项：%s' % stats['new_items'])
    print('[m027] 已有字典分类跳过：%s' % (', '.join(stats['skipped_categories']) or '无'))
    print('[m027] 已有字典项跳过：%s' % stats['skipped_items'])


if __name__ == '__main__':
    main()
