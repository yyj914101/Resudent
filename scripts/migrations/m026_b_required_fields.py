#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m026_b_required_fields.py — 082 B 必补字段加列（ADR-018 轨 A 幂等迁移）。

任务 082 三个字段：
- B-1：``medical_insurance_payments.benefit_cert_status``（医保待遇认证状态，
  字典分类「医保待遇认证状态」= 已认证 / 未认证 / 待认证）；
  列 DDL ``VARCHAR(30) DEFAULT '未认证'``，SQLite 对存量行自动回填默认值。
  ⚠️ **上线须业务核对一轮**：存量「未认证」是缺省值而非事实断言，若居民实际已认证，
  统计会系统性低估；业务确认后按实际值批量修正（本迁移不猜测、不做二次回填）。
- B-2：救助动态状态（字典分类「救助动态状态」= 正常 / 停发 / 新增）；
  **零 DDL**——该字段无独立列，取值仅落字典分类，由 m026 之外的
  由 m026 之外的 ``utils.dict_seed.ensure_enum_dicts`` 幂等补录（三分类已并入 DICT_SEEDS）。
- B-3：``veteran.key_group_category``（退役重点群体，字典分类「退役重点群体」）；
  列 DDL ``VARCHAR(40)``，可空；**保守不回填**，存量行保持 NULL。

本迁移只加列，不删旧列、不做 UPDATE 回填。SQLite 不支持
``ADD COLUMN IF NOT EXISTS``，故按 m021/m023 的 PRAGMA 检查式加列，
重复执行只跳过、不报错。

新库由 create_all 建齐；存量库经本脚本（轨 A 手工迁移）幂等补列。

执行（先备份库）：
    python scripts/migrations/m026_b_required_fields.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

TABLES = {
    'medical_insurance_payments': [
        ('benefit_cert_status', "VARCHAR(30) DEFAULT '未认证'"),
    ],
    'veteran': [
        ('key_group_category', 'VARCHAR(40)'),
    ],
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
    stats = {'added_columns': [], 'skipped_columns': []}
    try:
        for table, columns in TABLES.items():
            cols = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
            if not cols:
                raise RuntimeError(f'{table} 表不存在：请先在目标库 create_all')
            for name, ddl in columns:
                if name in cols:
                    stats['skipped_columns'].append(name)
                    continue
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
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
    print('[m026] 加列：%s' % (', '.join(stats['added_columns']) or '无'))
    print('[m026] 已有列跳过：%s' % (', '.join(stats['skipped_columns']) or '无'))


if __name__ == '__main__':
    main()
