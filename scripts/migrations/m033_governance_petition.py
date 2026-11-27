#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m033_governance_petition.py — 086 信访维稳建表 + 平安/信访字典补种（幂等迁移）。

两张表（轨 A：存量库脚本执行；新库由 create_all 直建，本脚本可重放）：
- governance_petition         信访件受理主表（状态/办结/满意度落主表；accept_date 供超期预警）
- governance_petition_handle  办理记录子表（只增不改，级联随主表删除）

字典 DML 与 utils/dict_seed.DICT_SEEDS 同值（唯一真源，不得另造枚举），共 12 分类
（与 m037 的 5 类合计 17 类，覆盖 084/086 新增字典）：
- 信访 4 类（本迁移自有表用值）：信访来源 / 信访诉求类别 / 信访办理状态 / 信访满意度
- 平安扩展 8 类（道路隐患闭环 + 道交在册登记用值，表由 086 各自迁移建；此处只补字典，
  与本脚本同值重复插入安全：分类/条目均 WHERE NOT EXISTS 守卫，不覆盖既有项）。
  其中「隐患等级」与 m028 已建的「安全隐患等级」是两个分类名，互不覆盖。

新库由 app.py 启动守卫 ensure_enum_dicts 建齐；本迁移用于存量库补种与显式重放。

约束：
- 只做 CREATE TABLE/INDEX IF NOT EXISTS + 字典守卫 DML，不改既有表、不改既有列。
- 缺分类建分类、缺项补项；已存在项不重复、不覆盖；重复执行结果一致。
- 信访人 contact / id_card 接 R12 字段级加密：明文列加密后置 NULL，密文落 *_enc，
  等值检索走 *_hash；*_hash 不加 unique —— 同一人可多次来信来访。

执行（先备份库）：
    python scripts/migrations/m033_governance_petition.py --db shucun.db
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

# 信访自有表用值（4 分类）
PETITION_CATEGORIES = {
    '信访来源': ['来信', '来访', '网上', '电话', '转办', '其他'],
    '信访诉求类别': ['邻里纠纷', '土地宅基地', '惠农补贴', '涉法涉诉', '干部作风', '其他'],
    '信访办理状态': ['待受理', '办理中', '已办结', '不予受理'],
    '信访满意度': ['满意', '基本满意', '不满意', '未评价'],
}

# 086 道交/平安扩展用值（8 分类）：道路隐患闭环 + 道交在册登记，表由 086 各自迁移建，此处只补字典
SAFETY_CATEGORIES = {
    '道路隐患类型': ['路面破损', '视距不良', '临水临崖', '缺少护栏', '照明缺失', '标志标线缺失', '其他'],
    '隐患等级': ['一般', '较大', '重大'],
    '整改状态': ['未整改', '整改中', '已整改', '已验收'],
    '道交登记类型': ['车辆', '驾驶人'],
    '车辆类型': ['小型客车', '面包车', '货车', '三轮车', '摩托车', '拖拉机', '其他'],
    '证照情况': ['证照齐全', '逾期未检', '无证', '其他'],
    '在册状态': ['在册', '已注销', '已迁出'],
    '运行状态': ['正常', '暂停', '已撤销'],
}

CATEGORIES = {}
CATEGORIES.update(PETITION_CATEGORIES)
CATEGORIES.update(SAFETY_CATEGORIES)

DDL = """
CREATE TABLE IF NOT EXISTS governance_petition (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  petition_no VARCHAR(30) NOT NULL,
  source VARCHAR(30),
  petitioner_name VARCHAR(50) NOT NULL,
  contact VARCHAR(30),
  contact_enc TEXT,
  contact_hash VARCHAR(64),
  id_card VARCHAR(18),
  id_card_enc TEXT,
  id_card_hash VARCHAR(64),
  address VARCHAR(200),
  category VARCHAR(30),
  summary TEXT,
  accept_date DATE,
  due_date DATE,
  case_officer VARCHAR(50),
  status VARCHAR(30) DEFAULT '待受理',
  close_date DATE,
  reply TEXT,
  satisfaction VARCHAR(30),
  remark TEXT,
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

-- 信访编号唯一（对齐模型 petition_no unique=True, index=True；编号由业务生成，防重复登记）
CREATE UNIQUE INDEX IF NOT EXISTS ix_governance_petition_petition_no
  ON governance_petition (petition_no);
CREATE INDEX IF NOT EXISTS ix_governance_petition_category
  ON governance_petition (category);
CREATE INDEX IF NOT EXISTS ix_governance_petition_accept_date
  ON governance_petition (accept_date);
CREATE INDEX IF NOT EXISTS ix_governance_petition_status
  ON governance_petition (status);

CREATE TABLE IF NOT EXISTS governance_petition_handle (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  petition_id INTEGER NOT NULL,
  handle_date DATE,
  handler VARCHAR(50),
  action TEXT,
  result TEXT,
  next_plan TEXT,
  created_at DATETIME,
  FOREIGN KEY (petition_id) REFERENCES governance_petition (id)
);

CREATE INDEX IF NOT EXISTS ix_governance_petition_handle_petition_id
  ON governance_petition_handle (petition_id);
"""

# 字典守卫 DML（值 = 标签 = DICT_SEEDS 字面量；分类/条目均 WHERE NOT EXISTS 幂等）
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
        DICT_DML.format(name=name, desc='%s（086 信访维稳 / 086 道交/平安扩展）' % name,
                        seed_rows=_seed_rows(labels))
        for name, labels in CATEGORIES.items()
    )


def run(db_path=None) -> dict:
    path = _db_path(db_path)
    conn = sqlite3.connect(path)
    stats = {'db': path, 'new_categories': [], 'new_items': 0, 'skipped_categories': []}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if 'dict_categories' not in tables or 'dict_items' not in tables:
            raise RuntimeError('dict_categories / dict_items 表不存在：请先在目标库 create_all')

        conn.executescript(DDL)

        before = {row[0] for row in conn.execute(
            "SELECT name FROM dict_categories WHERE name IN (%s)"
            % ','.join("'%s'" % n for n in CATEGORIES))}
        for name, labels in CATEGORIES.items():
            row = conn.execute('SELECT id FROM dict_categories WHERE name = ?', (name,)).fetchone()
            if row:
                cat_id = row[0]
                if name in before:
                    stats['skipped_categories'].append(name)
            else:
                cur = conn.execute(
                    'INSERT INTO dict_categories (name, description, is_active, sort_order, created_at) '
                    'VALUES (?, ?, 1, '
                    'COALESCE((SELECT MAX(sort_order) FROM dict_categories), 0) + 10, ?)',
                    (name, '%s（086 信访维稳 / 086 道交/平安扩展）' % name, now))
                cat_id = cur.lastrowid
                stats['new_categories'].append(name)
            existing = {r[0] for r in conn.execute(
                'SELECT label FROM dict_items WHERE category_id = ?', (cat_id,))}
            for idx, label in enumerate(labels, start=1):
                if label in existing:
                    continue
                conn.execute(
                    'INSERT INTO dict_items (category_id, label, value, sort_order, is_active, created_at) '
                    'VALUES (?, ?, ?, ?, 1, ?)', (cat_id, label, label, idx, now))
                stats['new_items'] += 1
        conn.commit()
    finally:
        conn.close()
    return stats


def dump_sql(db_path=None) -> str:
    """导出纯 SQL（供 sqlite3 CLI 手工重放；同样幂等）。"""
    return DDL + '\n' + _dict_dml()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', help='目标 SQLite 路径（默认 SHUCUN_DB_PATH / config.DB_PATH）')
    parser.add_argument('--print-sql', action='store_true', help='只输出等价 SQL，不写库')
    args = parser.parse_args()
    if args.print_sql:
        print(dump_sql(args.db))
        return
    stats = run(args.db)
    print('[m033] 目标库：%s' % stats['db'])
    print('[m033] 新建分类：%s' % (', '.join(stats['new_categories']) or '无'))
    print('[m033] 新增字典项：%s' % stats['new_items'])
    print('[m033] 已有分类跳过：%s' % (', '.join(stats['skipped_categories']) or '无'))


if __name__ == '__main__':
    main()
