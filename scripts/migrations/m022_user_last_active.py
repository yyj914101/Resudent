#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m022_user_last_active.py — users 补 last_active 列（057b，轨 A 幂等迁移）。

G-02 在线口径 = 心跳：shell.js 每 30s POST /api/heartbeat 刷新当前用户
last_active；/api/online-count 返回 now - last_active < 60s 的用户数。

新库由 create_all 建齐；存量库亦由 app.py 启动块 ensure_columns 幂等补列，
本脚本供轨 A 手工迁移用（宪法 §1.6：结构变更一律落 DDL 脚本）。
任务书原文写「m021」，该号已被 m021_medical_insurance_type.py 占用，顺延为 m022。

执行（先备份库）：
    python scripts/migrations/m022_user_last_active.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

TABLE = 'users'
COLUMNS = [
    ('last_active', 'DATETIME'),
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
    print('[m022] 加列：%s' % (', '.join(stats['added_columns']) or '无'))
    print('[m022] 已有列跳过：%s' % (', '.join(stats['skipped_columns']) or '无'))


if __name__ == '__main__':
    main()
