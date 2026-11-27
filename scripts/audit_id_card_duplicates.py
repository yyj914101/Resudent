#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_id_card_duplicates.py — GLB-08①：residents.id_card_hash 存量重复审计（只读）。

用途：执行 m020_resident_id_card_hash_unique.sql **之前**人工预检——
唯一索引对重复 hash 会直接建失败（天然守卫），本脚本提前给出重复清单，
便于先合并/清理居民档案再加约束。

退出码：0 = 无重复（可安全执行 m020）；1 = 存在重复（清单见 stdout）。
用法：python scripts/audit_id_card_duplicates.py --db /path/to/shucun.db
"""
import argparse
import os
import sqlite3
import sys

SQL = """
SELECT id_card_hash, COUNT(*) AS n, GROUP_CONCAT(id || ':' || COALESCE(name, '')) AS rows_
  FROM residents
 WHERE id_card_hash IS NOT NULL AND id_card_hash != ''
 GROUP BY id_card_hash
HAVING COUNT(*) > 1
 ORDER BY n DESC, id_card_hash
"""


def main():
    parser = argparse.ArgumentParser(description='residents.id_card_hash 重复审计（只读）')
    parser.add_argument('--db', required=True, help='SQLite 库文件路径')
    args = parser.parse_args()
    if not os.path.exists(args.db):
        print(f'[audit] 库不存在：{args.db}')
        return 2
    conn = sqlite3.connect(args.db)
    try:
        dupes = conn.execute(SQL).fetchall()
    finally:
        conn.close()
    if not dupes:
        print('[audit] 无重复 id_card_hash（可安全执行 m020 唯一索引迁移）')
        return 0
    print(f'[audit] 发现 {len(dupes)} 组重复 id_card_hash（先合并/清理，再执行 m020）：')
    for h, n, rows_ in dupes:
        print(f'  {h}  ×{n}  ->  {rows_}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
