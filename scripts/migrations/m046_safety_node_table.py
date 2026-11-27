#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m046_safety_node_table.py — 安全工作档案树支持挂载软表节点（幂等迁移）。

为 safety_nodes 表增加 table_code 列及索引 ix_safety_nodes_table_code，
使 node_type 支持 folder | file | table 三种同构节点：
每个安全工作文件夹下既可上传文书档案（file），也可直接新建/关联结构化软表台账（table）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def run(db_path=None) -> dict:
    conn = sqlite3.connect(_db_path(db_path))
    stats = {'added_column': False, 'created_index': False}
    try:
        cols = {row[1] for row in conn.execute('PRAGMA table_info(safety_nodes)')}
        if not cols:
            raise RuntimeError('safety_nodes 表不存在：请先执行 m012_safety_nodes.sql 或 create_all')
        if 'table_code' not in cols:
            conn.execute('ALTER TABLE safety_nodes ADD COLUMN table_code VARCHAR(60)')
            stats['added_column'] = True
        conn.execute(
            'CREATE INDEX IF NOT EXISTS ix_safety_nodes_table_code '
            'ON safety_nodes(table_code)'
        )
        stats['created_index'] = True
        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    args = parser.parse_args()
    stats = run(args.db)
    print('[m046] added_column=%s created_index=%s' % (stats['added_column'], stats['created_index']))


if __name__ == '__main__':
    main()
