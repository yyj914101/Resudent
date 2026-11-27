#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m025_party_dimensions.py — 党员类别拆维度（080 A-1，轨 A 幂等迁移）。

新增 4 列：member_identity（党员身份）/ member_mobility（党员流动状态）/
employment_status（在职状态）/ party_status（在册状态）。member_type 保留为历史列，不再写入。

回填策略（只对 party_status IS NULL 的未迁移行执行，一条 UPDATE）：
- 正式党员 → 身份=正式党员、流动=在家、在册=在册
- 预备党员 → 身份=预备党员、流动=在家、在册=在册
- 流动党员 → 身份=NULL（不猜身份）、流动=流动、在册=在册
- 已转出   → 身份/流动=NULL、在册=已转出
- 其他/NULL → 身份/流动=NULL、在册=在册
- employment_status 一律 NULL（旧值无信息，不猜）

幂等：第二次执行 updated_rows=0（party_status 已无 NULL 行）。

⚠️ 执行前先备份库。
新库由 create_all 建齐（模型已含 4 列）。
存量库用本脚本手工迁移。启动期由 app._ensure_party_dimensions 应急补列 + 保守回填
（仅对「四维全空」的未迁移行；存在「部分维度有值但状态空」的行时整段跳过并告警，避免覆盖），
轨 A 真源仍为本脚本；发布仍须显式执行本脚本并核对。

执行（先备份库）：
    python scripts/migrations/m025_party_dimensions.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

TABLE = 'party_members'
COLUMNS = [
    ('member_identity', 'VARCHAR(20)'),
    ('member_mobility', 'VARCHAR(20)'),
    ('employment_status', 'VARCHAR(20)'),
    ('party_status', 'VARCHAR(20)'),   # 刻意不加 DEFAULT：NULL 作为「未迁移」标记
]

BACKFILL_SQL = """
UPDATE party_members SET
    member_identity = CASE member_type
        WHEN '正式党员' THEN '正式党员'
        WHEN '预备党员' THEN '预备党员'
        ELSE NULL END,
    member_mobility = CASE member_type
        WHEN '正式党员' THEN '在家'
        WHEN '预备党员' THEN '在家'
        WHEN '流动党员' THEN '流动'
        ELSE NULL END,
    employment_status = NULL,
    party_status = CASE member_type
        WHEN '已转出' THEN '已转出'
        ELSE '在册' END
WHERE party_status IS NULL
"""


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def run(db_path=None) -> dict:
    conn = sqlite3.connect(_db_path(db_path))
    stats = {'added_columns': [], 'skipped_columns': [], 'updated_rows': 0, 'identity_blank': 0}
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
        stats['updated_rows'] = conn.execute(BACKFILL_SQL).rowcount
        stats['identity_blank'] = conn.execute(
            f'SELECT COUNT(*) FROM {TABLE} WHERE member_identity IS NULL').fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    args = parser.parse_args()
    stats = run(args.db)
    print('[m025] 加列：%s' % (', '.join(stats['added_columns']) or '无'))
    print('[m025] 已有列跳过：%s' % (', '.join(stats['skipped_columns']) or '无'))
    print('[m025] 回填行数：%d' % stats['updated_rows'])
    print('[m025] 回填后身份为空行数：%d' % stats['identity_blank'])


if __name__ == '__main__':
    main()
