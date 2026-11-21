#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m043_youshu.py — 优抚对象台账建表 + 救助类别字典补种（幂等迁移，2026-11-16）。

一张表（轨 A：存量库脚本执行；新库由 create_all 直建表体，本脚本重放幂等）：
- youshu  优抚对象（因伤病残涉核退役军人的优待补贴）
  字段（主理人派单）：姓名 / 身份证 / 电话 / 救助类别 / 优抚金额 / 是否死亡 / 保障状态。
  身份证与电话按 R12 加密管线存列：明文列仅 ORM 形态（写入后置 NULL），
  真值走 _enc，检索走 _hash（routes/youshu.py 的 sensitive 管线消费）。

字典 1 分类（与 utils/dict_seed.DICT_SEEDS 同值，唯一真源不得另造枚举）：
- 优抚对象救助类别：因病退役 / 因残退役 / 涉核工程
  （用户要求「选项可在系统中修改」→ 走字典分类，字典管理页可增删改项）

约束：
- 只做 CREATE TABLE IF NOT EXISTS + 字典守卫 DML，不改既有表、不改既有列。
- 缺分类建分类、缺项补项；已存在项不重复、不覆盖；重复执行结果一致。

执行（先备份库）：
    python scripts/migrations/m043_youshu.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import os
import sqlite3
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

TABLE = 'youshu'

DDL = """
CREATE TABLE IF NOT EXISTS youshu (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(50),
  id_card VARCHAR(18),
  id_card_enc TEXT,
  id_card_hash VARCHAR(64),
  phone VARCHAR(30),
  phone_enc TEXT,
  phone_hash VARCHAR(64),
  care_category VARCHAR(20),
  amount FLOAT,
  is_deceased VARCHAR(10) DEFAULT '否',
  support_status VARCHAR(10) DEFAULT '已认定',
  remark TEXT,
  created_at DATETIME,
  updated_at DATETIME,
  status VARCHAR(10) NOT NULL DEFAULT '有效',
  invalid_reason VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS ix_youshu_name ON youshu (name);
CREATE INDEX IF NOT EXISTS ix_youshu_id_card_hash ON youshu (id_card_hash);
CREATE INDEX IF NOT EXISTS ix_youshu_care_category ON youshu (care_category);
"""

# 字典守卫 DML（值 = 标签 = DICT_SEEDS 字面量；分类/条目均 WHERE NOT EXISTS 幂等）
CATEGORIES = {
    '优抚对象救助类别': ['因病退役', '因残退役', '涉核工程'],
}

DICT_DML = """
INSERT INTO dict_categories (name, description, is_active, sort_order, created_at)
SELECT '{name}', '{desc}', 1,
       COALESCE((SELECT MAX(sort_order) FROM dict_categories), 0) + 10,
       datetime('now', 'localtime')
WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = '{name}');

INSERT INTO dict_items (category_id, label, value, sort_order, is_active, created_at)
SELECT c.id, seed.label, seed.value, seed.sort_order, 1, datetime('now', 'localtime')
FROM dict_categories c, (
{seed_rows}
) AS seed
WHERE c.name = '{name}'
  AND NOT EXISTS (
    SELECT 1 FROM dict_items di
    WHERE di.category_id = c.id AND di.label = seed.label
  );
"""


def _db_path(explicit=None) -> str:
    if explicit:
        return explicit
    if os.environ.get('SHUCUN_DB_PATH'):
        return os.environ['SHUCUN_DB_PATH']
    from config import DB_PATH
    return DB_PATH


def _seed_rows(labels) -> str:
    rows = []
    for idx, label in enumerate(labels, start=1):
        head = 'SELECT' if idx == 1 else '    SELECT'
        rows.append("%s '%s' AS label, '%s' AS value, %d AS sort_order" % (head, label, label, idx))
    return '\n    UNION ALL\n'.join(rows)


def _dict_dml() -> str:
    return '\n'.join(
        DICT_DML.format(name=name, desc='%s（优抚对象救助类别，可在字典管理修改）' % name,
                        seed_rows=_seed_rows(labels))
        for name, labels in CATEGORIES.items()
    )


def run(db_path=None) -> dict:
    path = _db_path(db_path)
    conn = sqlite3.connect(path)
    stats = {'db': path, 'table_created': False, 'new_categories': [], 'new_items': 0}
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'dict_categories' not in tables or 'dict_items' not in tables:
            raise RuntimeError('dict_categories / dict_items 表不存在：请先在目标库 create_all')

        names_in = ','.join("'%s'" % n for n in CATEGORIES)
        stats['table_created'] = TABLE not in tables
        before_cats = {r[0] for r in conn.execute(
            "SELECT name FROM dict_categories WHERE name IN (%s)" % names_in)}
        before_items = conn.execute(
            "SELECT COUNT(*) FROM dict_items WHERE category_id IN "
            "(SELECT id FROM dict_categories WHERE name IN (%s))" % names_in).fetchone()[0]

        conn.executescript(DDL)
        conn.executescript(_dict_dml())

        after_cats = {r[0] for r in conn.execute(
            "SELECT name FROM dict_categories WHERE name IN (%s)" % names_in)}
        after_items = conn.execute(
            "SELECT COUNT(*) FROM dict_items WHERE category_id IN "
            "(SELECT id FROM dict_categories WHERE name IN (%s))" % names_in).fetchone()[0]
        stats['new_categories'] = sorted(after_cats - before_cats)
        stats['new_items'] = after_items - before_items
        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    import argparse
    ap = argparse.ArgumentParser(description='m043 优抚对象建表 + 字典补种（幂等）')
    ap.add_argument('--db', default=None, help='目标库路径；缺省 SHUCUN_DB_PATH / config.DB_PATH')
    args = ap.parse_args()
    stats = run(args.db)
    print('m043 完成：%s' % stats)
    return 0


if __name__ == '__main__':
    sys.exit(main())
