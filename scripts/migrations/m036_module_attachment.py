#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m036_module_attachment.py — 092 模板附件区通用附件表建表（幂等迁移）。

一张表（轨 A：存量库手工/脚本执行；新库由 create_all 直建，本脚本可重放）：
- module_attachments   通用模块级附件（disabled/care/subsidy 三模块「办事流程模板」，
                       立项规划 §十一裁决 8：不复用 SafetyNode 档案树）

约束：
- 只做 CREATE TABLE/INDEX IF NOT EXISTS，不改既有表、不改既有列、无 DML。
- 文件正文不落库：original_name 只入库不落盘，stored_name 由
  utils/attachment_service.py 生成（uuid4 hex + 白名单扩展名）。
- 索引统一在此建 idx_* 前缀（模型侧不建 index=True，R-093-01 双索引漂移纪律），
  新库（create_all 建表 + 本脚本重放）与存量库索引集合一致。

执行（先备份库）：
    python scripts/migrations/m036_module_attachment.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）；--print-sql 供 sqlite3 CLI 重放。
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

DDL = """
CREATE TABLE IF NOT EXISTS module_attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  module_key VARCHAR(30) NOT NULL,
  target_type VARCHAR(30),
  target_id INTEGER,
  original_name VARCHAR(255) NOT NULL,
  stored_name VARCHAR(64) NOT NULL,
  ext VARCHAR(10),
  size INTEGER,
  mime VARCHAR(120),
  uploaded_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_module_attachments_module
  ON module_attachments (module_key);
CREATE INDEX IF NOT EXISTS idx_module_attachments_target
  ON module_attachments (target_type, target_id);
"""


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def run(db_path=None) -> dict:
    path = _db_path(db_path)
    conn = sqlite3.connect(path)
    stats = {'db': path, 'table_created': False,
             'indexes_created': [], 'indexes_kept': 0}
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        stats['table_created'] = 'module_attachments' not in tables
        # 先采集 DDL 前的既有索引集合，执行后做差集才是「本次新建」
        have_before = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
        conn.executescript(DDL)
        want = {'idx_module_attachments_module', 'idx_module_attachments_target'}
        stats['indexes_created'] = sorted(want - have_before)
        stats['indexes_kept'] = len(want) - len(stats['indexes_created'])
        conn.commit()
    finally:
        conn.close()
    return stats


def dump_sql(db_path=None) -> str:
    """导出纯 SQL（供 sqlite3 CLI 手工重放；同样幂等）。"""
    return DDL


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    parser.add_argument('--print-sql', action='store_true', help='只输出等价 SQL，不写库')
    args = parser.parse_args()
    if args.print_sql:
        print(dump_sql(args.db))
        return
    stats = run(args.db)
    print('[m036] 目标库：%s' % stats['db'])
    print('[m036] 建表：%s' % ('新建 module_attachments' if stats['table_created'] else '已存在，跳过'))
    print('[m036] 新建索引：%s' % (', '.join(stats['indexes_created']) or '无'))
    print('[m036] 已有索引保持：%d 个' % stats['indexes_kept'])


if __name__ == '__main__':
    main()
