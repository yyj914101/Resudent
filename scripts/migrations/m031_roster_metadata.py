#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m031_roster_metadata.py — 099 台账元数据预置（幂等迁移）。

为 7 张业务台账在软表引擎元数据表 dynamic_table 各预置一行（零 DDL）：
基础设施分布 / 重点场所年度缴费 / 养老保险参保名册 / 环卫保洁队伍 /
环卫设施点位 / 环卫整治记录 / 文明实践活动记录。

⚠️ m031 是本批 7 张台账 dynamic_table 行的**唯一预置点**，
   m038/m039/m040 等后续迁移不得再插同 code 行。
字段（dynamic_field 行）不在本迁移预置——按 ADR-025 决策 3，
由首次上传引导确认生成。

铁律：
- 不插任何 dynamic_field 行；
- 不删不改任何既有行（code 已存在即跳过，不覆盖不更新）；
- 不建表、不改动 DDL（dynamic_table 结构真源：同目录 m024_dynamic_tables.sql）。

执行（先备份库）：
    python scripts/migrations/m031_roster_metadata.py --db shucun.db
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

# 与 m024_dynamic_tables.sql 的 dynamic_table 列一一对应（除自增 id）。
# 列顺序：code, name, module_key, group_name, description,
#         permission_key, created_by, created_at, updated_at
# permission_key 沿用引擎默认 'dynatable'（丙方案，二期再细分表级权限）。
ROSTER_TABLES = [
    ('shop_facility', '基础设施分布', 'shop', '人口与台账', '基础设施分布台账'),
    ('shop_annual_fee', '重点场所年度缴费', 'shop', '人口与台账', '重点场所年度缴费台账'),
    ('pension_roster', '养老保险参保名册', 'pension', '社会保障', '养老保险参保名册台账'),
    ('sanitation_team', '环卫保洁队伍', 'sanitation', '民生服务', '环卫保洁队伍台账'),
    ('sanitation_site', '环卫设施点位', 'sanitation', '民生服务', '环卫设施点位台账'),
    ('sanitation_cleanup', '环卫整治记录', 'sanitation', '民生服务', '环卫整治记录台账'),
    ('civilization_activity', '文明实践活动记录', 'civilization', '人口与台账', '文明实践活动记录台账'),
]

PERMISSION_KEY = 'dynatable'
CREATED_BY = 'm031'


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def run(db_path=None) -> dict:
    conn = sqlite3.connect(_db_path(db_path))
    stats = {'inserted': [], 'skipped': []}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        # 守卫：dynamic_table 不存在则拒绝（结构真源 m024 / create_all）
        cols = {row[1] for row in conn.execute('PRAGMA table_info(dynamic_table)')}
        if not cols:
            raise RuntimeError('dynamic_table 表不存在：请先在目标库 create_all '
                               '或执行 scripts/migrations/m024_dynamic_tables.sql')

        # 幂等预置：code 已存在即跳过，不覆盖不更新；只插元数据行，
        # 不插 dynamic_field（字段由首次上传引导确认生成，ADR-025 决策 3）。
        for code, name, module_key, group_name, description in ROSTER_TABLES:
            exists = conn.execute(
                'SELECT id FROM dynamic_table WHERE code = ?', (code,)).fetchone()
            if exists:
                stats['skipped'].append(code)
                continue
            conn.execute(
                'INSERT INTO dynamic_table '
                '(code, name, module_key, group_name, description, '
                ' permission_key, created_by, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (code, name, module_key, group_name, description,
                 PERMISSION_KEY, CREATED_BY, now, now))
            stats['inserted'].append(code)
        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    args = parser.parse_args()
    stats = run(args.db)
    print('[m031] 新预置台账：%s' % (', '.join(stats['inserted']) or '无'))
    print('[m031] 已存在跳过：%s' % (', '.join(stats['skipped']) or '无'))


if __name__ == '__main__':
    main()
