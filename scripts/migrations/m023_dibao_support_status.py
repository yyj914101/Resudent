#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m023_dibao_support_status.py — dibao 补 support_status 列（067，ADR-024② 幂等迁移）。

低保「保障状态」业务最小集：已认定 / 复审中 / 待核实（v3-01 样图状态徽章列口径）。
- 新库由 create_all 建齐；存量库亦由 app.py 启动块 ensure_columns 幂等补列。
- ADD COLUMN 带 DEFAULT '已认定'：SQLite 对存量行自动回填默认值（存量低保记录
  均为已在册对象，缺省视为已认定），无需单独 UPDATE。

聚合口径：api_stats 的低保户数/人数不区分 support_status（在册口径不变）；
徽章仅作单行状态展示与列表筛选，不参与统计聚合。

执行（先备份库）：
    python scripts/migrations/m023_dibao_support_status.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

TABLE = 'dibao'
COLUMNS = [
    ('support_status', "VARCHAR(10) DEFAULT '已认定'"),
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
    stats = {'added_columns': [], 'skipped_columns': []}
    try:
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info({TABLE})')}
        if not cols:
            raise RuntimeError(f'{TABLE} 表不存在：请先在目标库 create_all')
        for name, ddl in COLUMNS:
            if name in cols:
                stats['skipped_columns'].append(name)
                continue
            conn.execute(f'ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}')
            stats['added_columns'].append(name)
        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    args = parser.parse_args()
    stats = run(args.db)
    print('[m023] 加列：%s' % (', '.join(stats['added_columns']) or '无'))
    print('[m023] 已有列跳过：%s' % (', '.join(stats['skipped_columns']) or '无'))


if __name__ == '__main__':
    main()
