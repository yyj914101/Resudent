#!/usr/bin/env python3
"""preclean_id_card.py — B6 存量重复身份证预清洗（迁移第 0 步）

扫描 residents.id_card / veteran.id_card 的重复值、空值，输出清单供人工处置。
处置完成后方可建 id_card_hash UNIQUE 索引（run_migration.py 会在建唯一索引前检查）。

用法：
    python scripts/migrations/preclean_id_card.py --dry-run     # 默认：仅输出清单
    python scripts/migrations/preclean_id_card.py --check        # 检查是否已清洗（重复/空值=0 则 PASS）

退出码：0=PASS（无重复/空值），1=存在重复或空值（需人工处置）。
"""
from __future__ import annotations

import os
import sys
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
sys.path.insert(0, _PROJECT_ROOT)


def scan(app, db, Model, field_name):
    """扫描单表的重复/空值，返回 (重复清单, 空值清单)。"""
    from collections import Counter
    rows = Model.query.all()
    duplicates = []
    empties = []
    counter = Counter()
    for r in rows:
        v = getattr(r, field_name, None)
        if not v or not str(v).strip():
            empties.append(r.id)
            continue
        v = str(v).strip()
        counter[v] += 1
    for v, cnt in counter.items():
        if cnt > 1:
            ids = [r.id for r in rows if str(getattr(r, field_name, '')).strip() == v]
            duplicates.append((v, cnt, ids))
    return duplicates, empties


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='B6 存量重复身份证预清洗')
    parser.add_argument('--check', action='store_true', help='仅检查（输出退出码）')
    args = parser.parse_args(argv)

    # 环境初始化（复用 run_server 的目录结构）
    os.environ.setdefault('SHUCUN_DATA_DIR', os.path.join(_PROJECT_ROOT, 'data'))
    os.environ.setdefault('SHUCUN_DB_PATH', os.path.join(_PROJECT_ROOT, 'data', 'shucun.db'))
    os.environ.setdefault('SHUCUN_UPLOAD_DIR', os.path.join(_PROJECT_ROOT, 'uploads'))
    os.environ.setdefault('SHUCUN_BACKUP_DIR', os.path.join(_PROJECT_ROOT, 'backups'))
    os.environ.setdefault('SHUCUN_LOG_DIR', os.path.join(_PROJECT_ROOT, 'logs'))
    os.environ.setdefault('SHUCUN_TEMP_DIR', os.path.join(_PROJECT_ROOT, 'temp'))
    os.environ.setdefault('SHUCUN_LICENSE_FILE', os.path.join(_PROJECT_ROOT, 'license.key'))
    for d in ('data', 'uploads', 'backups', 'logs', 'temp'):
        os.makedirs(os.path.join(_PROJECT_ROOT, d), exist_ok=True)

    from app import create_app
    from models import db, Resident, Veteran

    app = create_app()
    total_dup = 0
    total_empty = 0

    with app.app_context():
        for label, Model, field in (
            ('residents.id_card', Resident, 'id_card'),
            ('veteran.id_card', Veteran, 'id_card'),
        ):
            duplicates, empties = scan(app, db, Model, field)
            total_dup += len(duplicates)
            total_empty += len(empties)
            print(f"\n=== {label} ===")
            print(f"  重复值：{len(duplicates)} 组")
            for v, cnt, ids in duplicates:
                print(f"    - {v} 出现 {cnt} 次，ids={ids[:10]}")
            print(f"  空值：{len(empties)} 行，ids={empties[:20]}")

    print(f"\n=== 汇总 ===")
    print(f"重复值组数：{total_dup} | 空值行数：{total_empty}")
    if total_dup == 0 and total_empty == 0:
        print("PASS：无重复/空值，可建 id_card_hash 唯一索引")
        return 0
    print("FAIL：存在重复或空值，需人工处置后方可建唯一索引")
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
