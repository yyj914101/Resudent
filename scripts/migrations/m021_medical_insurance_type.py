#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m021_medical_insurance_type.py — 医保缴费三分类（054-B，轨 A 幂等迁移）。

新增 insurance_type（居民/职工/其他分类键）+ ext_data（职工/其他专属字段 JSON）。

⚠️ SQLite 不支持 `ADD COLUMN IF NOT EXISTS`。本迁移原为 .sql 裸 ALTER，重复执行
会报 `duplicate column name`，与「幂等」声明不符（054 复核 R-054-5）。现按
m015_certificate_full.py / run_migration.py 的 **PRAGMA 检查式加列** 改写为 .py，
重复执行只跳过、不报错。

执行（先备份库）：
    python scripts/migrations/m021_medical_insurance_type.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。

新库由 create_all 建齐；存量库亦可由 routes.medical_insurance._ensure_columns()
启动时幂等补列（COL_ADDS 已含同两列），本脚本供轨 A 手工迁移用。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

TABLE = 'medical_insurance_payments'
COLUMNS = [
    ('insurance_type', "VARCHAR(20) DEFAULT 'resident'"),
    ('ext_data', 'TEXT'),
]
INDEX_DDL = [
    'CREATE INDEX IF NOT EXISTS idx_medical_insurance_type '
    'ON medical_insurance_payments (insurance_type)',
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
        for statement in INDEX_DDL:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    args = parser.parse_args()
    stats = run(args.db)
    print('[m021] 加列：%s' % (', '.join(stats['added_columns']) or '无'))
    print('[m021] 已有列跳过：%s' % (', '.join(stats['skipped_columns']) or '无'))


if __name__ == '__main__':
    main()
