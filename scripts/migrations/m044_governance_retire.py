#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m044_governance_retire.py — 综治台账收缩：两类重点人员下线 + 刑事人员合并（幂等迁移，2026-11-16）。

背景（主理人派单）：综治管理胶囊按「人员管理 / 场所与行业 / 矛盾调处」重排，
「社区矫正」「社区戒毒康复」入口、路由、数据与相关代码一并下线；
「刑满释放帮教」改名「刑事人员」，服刑人员与刑满释放人员同表存放，
由「人员状态」字段（字典「刑事人员状态」）区分，页面工具栏按同名字段筛选。

本脚本（轨 A：存量库执行；新库由 create_all 直建表体，重放幂等）：
1. 归档：把待下线的两类台账行导出 JSON 到 temp/archive/（gitignore 目录，先备份后删）
2. 删除 governance_ledgers 中 category ∈ {community-correction, drug-rehab} 的行
3. 改名：category resettlement → criminal
4. 回填：criminal 行 payload 缺 personnel_status 时补「刑满释放人员」
   （历史行语义即刑满释放人员，保证筛选口径下不出现"无状态"孤儿行）
5. 字典：下线「矫正类别 / 戒毒阶段 / 矫正管控状态 / 戒毒管控状态」四个分类及其条目；
   收敛「重点人员类别」为 服刑人员 / 刑满释放人员；补种「刑事人员状态」

约束：
- 只删本脚本声明要下线的行与字典，不改其他分类、不改表结构（零 DDL）。
- 归档文件名带时间戳；无待删行时不写文件。
- 重复执行结果一致（已删行不存在、已改名不再命中、已回填不再命中）。

执行（建议先备份库）：
    python scripts/migrations/m044_governance_retire.py --db shucun.db
或依赖 SHUCUN_DB_PATH（默认 config.DB_PATH）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, _ROOT)

LEDGER_TABLE = 'governance_ledgers'
RETIRED_CATEGORIES = ('community-correction', 'drug-rehab')
RENAMED_CATEGORY = ('resettlement', 'criminal')

RETIRED_DICT_CATEGORIES = ('矫正类别', '戒毒阶段', '矫正管控状态', '戒毒管控状态')
KEY_PERSONNEL_DICT = '重点人员类别'
KEY_PERSONNEL_ITEMS = ('服刑人员', '刑满释放人员')
CRIMINAL_STATUS_DICT = '刑事人员状态'
CRIMINAL_STATUS_ITEMS = ('服刑人员', '刑满释放人员')
DEFAULT_BACKFILL_STATUS = '刑满释放人员'

ARCHIVE_DIR = os.path.join(_ROOT, 'temp', 'archive')

DICT_ITEM_INSERT = """
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


def _qlist(values) -> str:
    return ','.join("'%s'" % value for value in values)


def _seed_rows(labels) -> str:
    rows = []
    for idx, label in enumerate(labels, start=1):
        head = 'SELECT' if idx == 1 else '    SELECT'
        rows.append("%s '%s' AS label, '%s' AS value, %d AS sort_order" % (head, label, label, idx))
    return '\n    UNION ALL\n'.join(rows)


def _dict_item_dml(name, labels) -> str:
    return DICT_ITEM_INSERT.format(name=name, seed_rows=_seed_rows(labels))


def _tables(conn) -> set:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _archive_rows(conn, rows) -> str:
    """把待删行原样导出；返回归档文件路径（无行返回空串）。"""
    if not rows:
        return ''
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    path = os.path.join(ARCHIVE_DIR, 'governance-retired-%s.json' % stamp)
    payload = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'reason': '综治台账下线：社区矫正 / 社区戒毒康复（m044）',
        'retired_categories': list(RETIRED_CATEGORIES),
        'row_count': len(rows),
        'rows': rows,
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def _collect_retired_rows(conn):
    cols = ('id', 'category', 'name', 'record_date', 'payload', 'created_by', 'created_at')
    sql = ('SELECT %s FROM %s WHERE category IN (%s) ORDER BY id'
           % (', '.join(cols), LEDGER_TABLE, _qlist(RETIRED_CATEGORIES)))
    rows = []
    for record in conn.execute(sql):
        item = dict(zip(cols, record))
        item['created_at'] = str(item['created_at'] or '')
        rows.append(item)
    return rows


def _cleanup_dicts(conn) -> dict:
    """字典下线 + 收敛 + 补种；返回各类计数。"""
    stats = {'dict_categories_removed': 0, 'dict_items_removed': 0,
             'key_personnel_items_removed': 0, 'dict_items_added': 0}

    # ── 1) 整类下线（先删条目再删分类）──
    names_in = _qlist(RETIRED_DICT_CATEGORIES)
    cat_ids = [row[0] for row in conn.execute(
        "SELECT id FROM dict_categories WHERE name IN (%s)" % names_in)]
    if cat_ids:
        stats['dict_items_removed'] = conn.execute(
            "SELECT COUNT(*) FROM dict_items WHERE category_id IN (%s)" % _qlist(cat_ids)
        ).fetchone()[0]
        conn.execute("DELETE FROM dict_items WHERE category_id IN (%s)" % _qlist(cat_ids))
        stats['dict_categories_removed'] = conn.execute(
            "DELETE FROM dict_categories WHERE id IN (%s)" % _qlist(cat_ids)).rowcount

    # ── 2) 重点人员类别收敛为新两项（旧三值里的两类已随模块下线）──
    row = conn.execute("SELECT id FROM dict_categories WHERE name = ?",
                       (KEY_PERSONNEL_DICT,)).fetchone()
    if row:
        stale = conn.execute(
            "SELECT id FROM dict_items WHERE category_id = ? AND label NOT IN (%s)"
            % _qlist(KEY_PERSONNEL_ITEMS), (row[0],)).fetchall()
        if stale:
            stats['key_personnel_items_removed'] = conn.execute(
                "DELETE FROM dict_items WHERE id IN (%s)"
                % _qlist([item[0] for item in stale])).rowcount
    conn.execute(
        "INSERT INTO dict_categories (name, description, is_active, sort_order, created_at) "
        "SELECT ?, ?, 1, COALESCE((SELECT MAX(sort_order) FROM dict_categories), 0) + 10, "
        "datetime('now', 'localtime') "
        "WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = ?)",
        (KEY_PERSONNEL_DICT, '重点人员类别（刑事人员两类，可在字典管理修改）', KEY_PERSONNEL_DICT))

    # ── 3) 补种「刑事人员状态」与新条目（幂等）──
    conn.execute(
        "INSERT INTO dict_categories (name, description, is_active, sort_order, created_at) "
        "SELECT ?, ?, 1, COALESCE((SELECT MAX(sort_order) FROM dict_categories), 0) + 10, "
        "datetime('now', 'localtime') "
        "WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = ?)",
        (CRIMINAL_STATUS_DICT, '刑事人员状态（服刑 / 刑满释放，可在字典管理修改）',
         CRIMINAL_STATUS_DICT))

    before = conn.execute(
        "SELECT COUNT(*) FROM dict_items WHERE category_id IN "
        "(SELECT id FROM dict_categories WHERE name IN (%s))"
        % _qlist((KEY_PERSONNEL_DICT, CRIMINAL_STATUS_DICT))).fetchone()[0]
    conn.executescript(_dict_item_dml(KEY_PERSONNEL_DICT, KEY_PERSONNEL_ITEMS))
    conn.executescript(_dict_item_dml(CRIMINAL_STATUS_DICT, CRIMINAL_STATUS_ITEMS))
    after = conn.execute(
        "SELECT COUNT(*) FROM dict_items WHERE category_id IN "
        "(SELECT id FROM dict_categories WHERE name IN (%s))"
        % _qlist((KEY_PERSONNEL_DICT, CRIMINAL_STATUS_DICT))).fetchone()[0]
    stats['dict_items_added'] = after - before
    return stats


def run(db_path=None) -> dict:
    path = _db_path(db_path)
    conn = sqlite3.connect(path)
    stats = {'db': path, 'archived': '', 'rows_removed': 0, 'rows_renamed': 0,
             'rows_backfilled': 0}
    try:
        tables = _tables(conn)
        if 'dict_categories' not in tables or 'dict_items' not in tables:
            raise RuntimeError('dict_categories / dict_items 表不存在：请先在目标库 create_all')

        if LEDGER_TABLE in tables:
            retired = _collect_retired_rows(conn)
            stats['archived'] = _archive_rows(conn, retired)
            stats['rows_removed'] = conn.execute(
                "DELETE FROM %s WHERE category IN (%s)"
                % (LEDGER_TABLE, _qlist(RETIRED_CATEGORIES))).rowcount

            stats['rows_renamed'] = conn.execute(
                "UPDATE %s SET category = ? WHERE category = ?"
                % LEDGER_TABLE, (RENAMED_CATEGORY[1], RENAMED_CATEGORY[0])).rowcount

            valid_payload = "COALESCE(NULLIF(payload, ''), '{}')"
            backfill_sql = (
                "UPDATE %s SET payload = json_set(%s, '$.\"personnel_status\"', ?) "
                "WHERE category = ? "
                "AND json_valid(%s) "
                "AND json_extract(%s, '$.\"personnel_status\"') IS NULL"
                % (LEDGER_TABLE, valid_payload, valid_payload, valid_payload)
            )
            stats['rows_backfilled'] = conn.execute(
                backfill_sql, (DEFAULT_BACKFILL_STATUS, RENAMED_CATEGORY[1])).rowcount

        stats.update(_cleanup_dicts(conn))
        conn.commit()
    finally:
        conn.close()
    return stats


def main():
    import argparse
    ap = argparse.ArgumentParser(description='m044 综治台账下线与刑事人员合并（幂等）')
    ap.add_argument('--db', default=None, help='目标库路径；缺省 SHUCUN_DB_PATH / config.DB_PATH')
    args = ap.parse_args()
    stats = run(args.db)
    print('m044 完成：%s' % stats)
    return 0


if __name__ == '__main__':
    sys.exit(main())
