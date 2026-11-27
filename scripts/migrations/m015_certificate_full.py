#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m015_certificate_full.py — 042 证明开具完整能力（P1 数据层，轨 A 幂等迁移）。

任务书原名 m012_certificate_full.sql；本仓 m012 已被 030 安全工作模块占用，
顺延 m015。SQLite 不支持 ADD COLUMN IF NOT EXISTS，为保证「重复执行不报错」，
沿用 scripts/migrations/run_migration.py 的 PRAGMA 检查式加列，文件后缀为 .py。

执行（先备份库）：
    .venv/bin/python scripts/migrations/m015_certificate_full.py --db shucun.db
或依赖环境变量 SHUCUN_DB_PATH（默认 config.DB_PATH）。

范围：certificate_templates +6 列；新建 certificate_issues 表与索引；
3 个系统模板种子（与 routes.certificate.DEFAULT_TEMPLATE_DEFINITIONS 同源）。
不含电子公章（拍板①：不建 certificate_seals、无 seal 列）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

TEMPLATE_COLUMNS = [
    ('field_schema', "TEXT DEFAULT '[]'"),
    ('page_settings', "TEXT DEFAULT '{}'"),
    ('is_active', 'BOOLEAN DEFAULT 1'),
    ('version', 'INTEGER DEFAULT 1'),
    ('created_by', 'VARCHAR(50)'),
    ('updated_by', 'VARCHAR(50)'),
]

ISSUES_DDL = """
CREATE TABLE IF NOT EXISTS certificate_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial_no VARCHAR(32) NOT NULL UNIQUE,
  template_id INTEGER REFERENCES certificate_templates(id),
  template_name VARCHAR(100) NOT NULL,
  template_version INTEGER DEFAULT 1,
  cert_type VARCHAR(50),
  resident_id INTEGER REFERENCES residents(id),
  subject_name VARCHAR(50) NOT NULL,
  subject_id_card VARCHAR(18),
  form_data TEXT,
  rendered_content TEXT,
  page_settings TEXT,
  issuer_id INTEGER REFERENCES users(id),
  issuer_name VARCHAR(50),
  issue_unit VARCHAR(150),
  purpose VARCHAR(200),
  copies INTEGER DEFAULT 1,
  issued_at DATETIME,
  status VARCHAR(20) DEFAULT 'issued',
  voided_by VARCHAR(50),
  voided_at DATETIME,
  void_reason VARCHAR(300),
  created_at DATETIME
)
"""
INDEX_DDL = [
    'CREATE UNIQUE INDEX IF NOT EXISTS ix_certificate_issues_serial_no '
    'ON certificate_issues (serial_no)',
    'CREATE INDEX IF NOT EXISTS ix_certificate_issues_issued_at '
    'ON certificate_issues (issued_at)',
    'CREATE INDEX IF NOT EXISTS ix_certificate_issues_template_id '
    'ON certificate_issues (template_id)',
]


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def run(db_path=None) -> dict:
    from utils.certificate_catalog import (DEFAULT_PAGE_SETTINGS,
                                           DEFAULT_TEMPLATE_DEFINITIONS)
    conn = sqlite3.connect(_db_path(db_path))
    stats = {'added_columns': [], 'skipped_columns': [], 'seeded': []}
    try:
        cols = {row[1] for row in conn.execute('PRAGMA table_info(certificate_templates)')}
        if not cols:
            raise RuntimeError('certificate_templates 表不存在：请先在目标库 create_all')
        for name, ddl in TEMPLATE_COLUMNS:
            if name in cols:
                stats['skipped_columns'].append(name)
                continue
            conn.execute(f'ALTER TABLE certificate_templates ADD COLUMN {name} {ddl}')
            stats['added_columns'].append(name)
        conn.execute(ISSUES_DDL)
        for statement in INDEX_DDL:
            conn.execute(statement)
        # 仅空表种 3 个默认模板；非空库由 app 启动钩子 ensure_certificate_seed()
        # 统一做旧行升级/补种（避免旧 residence/death/kinship 行与默认模板并存成 6 行）。
        table_empty = conn.execute(
            'SELECT COUNT(*) FROM certificate_templates').fetchone()[0] == 0
        for definition in DEFAULT_TEMPLATE_DEFINITIONS:
            if not table_empty:
                break
            conn.execute(
                'INSERT INTO certificate_templates '
                '(name, cert_type, template_content, field_schema, page_settings, '
                ' is_custom, is_active, version, created_by, updated_by, '
                ' created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, 0, 1, 1, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)',
                (definition['name'], definition['cert_type'], definition['content'],
                 json.dumps(definition['fields'], ensure_ascii=False),
                 json.dumps(DEFAULT_PAGE_SETTINGS, ensure_ascii=False),
                 '系统', '系统'))
            stats['seeded'].append(definition['name'])
        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    args = parser.parse_args()
    stats = run(args.db)
    print('[m015] 加列：%s' % (', '.join(stats['added_columns']) or '无'))
    print('[m015] 已有列跳过：%s' % (', '.join(stats['skipped_columns']) or '无'))
    print('[m015] 种子：%s' % (', '.join(stats['seeded']) or '无（已存在）'))


if __name__ == '__main__':
    main()
