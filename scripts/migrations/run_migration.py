#!/usr/bin/env python3
"""run_migration.py — R12 存量加密转换六步链执行器（T09）

六步链（§5.5）：
  ① 明文备份
  ② 18 表 41 字段敏感列加密转换（原列置 NULL，禁止明文密文双写）
  ③ resident_tag 基础回填（household_property/dibao/disabled/care_targets → 标签）
  ④ subsidies → subsidy_record 迁移（legacy_id 幂等 + 孤儿 + 兜底项目）
  ⑤ 总对账（G-4：旧表总额 = 新表 + 孤儿总额，按 year 分组）
  ⑥ 写 migration_record(done) 原子切换

幂等与断点：加密以「原列已 NULL 且 _enc 有值」跳过；subsidy 迁移以 legacy_id UNIQUE 跳过；
每批 ≤500 行一个事务，中断未提交回滚、已提交保留，续跑按幂等键续接。

用法：
    python scripts/migrations/run_migration.py --dry-run     # 预览（不执行）
    python scripts/migrations/run_migration.py --execute    # 执行六步链
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import argparse
import time
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
sys.path.insert(0, _PROJECT_ROOT)

BATCH_SIZE = 500
LEGACY_PROJECT_NAME = '存量补贴（legacy）'


def load_inventory() -> dict:
    """读取敏感字段盘点清单（18 表 41 字段）。"""
    path = os.path.join(_SCRIPT_DIR, 'field_inventory.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _column_exists(conn, table, column) -> bool:
    from sqlalchemy import text
    rows = conn.execute(text("PRAGMA table_info({})".format(table))).fetchall()
    return any(r[1] == column for r in rows)


def _ensure_veteran_idcard_nullable(db) -> int:
    """R12 整改：veteran.id_card 旧 schema 为 NOT NULL + UNIQUE，加密后原列须置 NULL。

    若检测到 NOT NULL 约束，则重建 veteran 表（id_card 置空可写，唯一约束迁至 id_card_hash），
    返回重建行数；否则返回 0。幂等：重建一次后 id_card 已 nullable，重跑跳过。
    """
    from sqlalchemy import text, inspect

    insp = inspect(db.engine)
    if 'veteran' not in insp.get_table_names():
        return 0
    cols = insp.get_columns('veteran')
    colmap = {c['name']: c for c in cols}
    if colmap.get('id_card', {}).get('nullable', True):
        return 0  # 已 nullable，无需重建

    from models.veteran import Veteran

    # ① 拷贝旧表数据（原始列顺序）
    old_cols = [c['name'] for c in cols]
    col_list = ', '.join('"%s"' % c for c in old_cols)
    rows = db.session.execute(text(f'SELECT {col_list} FROM veteran')).fetchall()

    # ② 旧表改名 → 用新模型建表（id_card 可 NULL，唯一约束在 id_card_hash）
    db.session.execute(text('ALTER TABLE veteran RENAME TO veteran_old_tmp'))
    Veteran.__table__.create(db.engine, checkfirst=True)

    # ③ 拷贝公共列（新模型含旧列 + 新增 _enc/_hash 列；旧数据仅回填旧列）
    new_cols = set(Veteran.__table__.columns.keys())
    common = [c for c in old_cols if c in new_cols]
    if common:
        ins_cols = ', '.join('"%s"' % c for c in common)
        placeholders = ', '.join(':%s' % c for c in common)
        for row in rows:
            params = dict(zip(common, row))
            db.session.execute(text(
                f'INSERT INTO veteran ({ins_cols}) VALUES ({placeholders})'), params)

    # ④ 删除旧表
    db.session.execute(text('DROP TABLE veteran_old_tmp'))
    db.session.commit()
    return len(rows)


def ensure_sensitive_columns(db) -> dict:
    """确保 _enc/_hash 列与索引存在（m003 无 ADD COLUMN IF NOT EXISTS 的幂等兜底）。"""
    from sqlalchemy import text
    inventory = load_inventory()
    stats = {'added_columns': 0, 'skipped_columns': 0, 'rebuilt_veteran': 0}

    stats['rebuilt_veteran'] = _ensure_veteran_idcard_nullable(db)

    for t in inventory['tables']:
        table = t['table']
        for field in t['fields']:
            for suffix in ('_enc', '_hash'):
                col = f'{field}{suffix}'
                if not _column_exists(db.session, table, col):
                    db.session.execute(text(
                        f'ALTER TABLE "{table}" ADD COLUMN "{col}" TEXT'
                    ))
                    stats['added_columns'] += 1
                else:
                    stats['skipped_columns'] += 1
        # residents 前缀桶（模糊匹配候选）
        if t.get('id_card_prefix_hash'):
            col = 'id_card_prefix_hash'
            if not _column_exists(db.session, table, col):
                db.session.execute(text(
                    f'ALTER TABLE "{table}" ADD COLUMN "{col}" VARCHAR(64)'
                ))
                stats['added_columns'] += 1
    db.session.commit()
    return stats


def _encrypt_field(db, key_holder, table, field) -> int:
    """加密单表单字段（原始 SQL，模型无 _enc/_hash 列）。返回转换行数。"""
    from sqlalchemy import text
    from utils.crypto import sha256_hash, sha256_prefix_hash
    total = 0
    while True:
        rows = db.session.execute(text(
            f'SELECT id, "{field}" FROM "{table}" '
            f'WHERE "{field}" IS NOT NULL AND "{field}_enc" IS NULL '
            f'ORDER BY id LIMIT {BATCH_SIZE}'
        )).fetchall()
        if not rows:
            break
        for row_id, plaintext in rows:
            if plaintext is None or str(plaintext) == '':
                continue
            plain = str(plaintext).strip()
            enc = key_holder.encrypt(plain)
            h = sha256_hash(plain)
            db.session.execute(text(
                f'UPDATE "{table}" SET "{field}_enc"=:enc, '
                f'"{field}_hash"=:h, "{field}"=NULL WHERE id=:id'
            ), {'enc': enc, 'h': h, 'id': row_id})
            # residents.id_card 前缀桶
            if table == 'residents' and field == 'id_card':
                prefix = sha256_prefix_hash(plain, 6)
                db.session.execute(text(
                    'UPDATE residents SET id_card_prefix_hash=:p WHERE id=:id'
                ), {'p': prefix, 'id': row_id})
        db.session.commit()
        total += len(rows)
    return total


def _backfill_tags(db) -> dict:
    """③ resident_tag 基础回填（防看板清零）。

    T16-2 起唯一真值迁移至 utils/tag_backfill.py::backfill_tags（修正口径：
    care_target 老人→困难老人、补留守儿童、special_support→特困供养 hash 匹配、
    仅 status=有效 回填），本函数仅作兼容转发，防双写漂移（C1 条件）。
    """
    from utils.tag_backfill import backfill_tags as _backfill
    return _backfill(db)


def _ensure_legacy_project(db) -> int:
    """确保兜底项目「存量补贴（legacy）」存在，返回 project_id。"""
    from models.subsidy_project import SubsidyProject
    p = SubsidyProject.query.filter_by(name=LEGACY_PROJECT_NAME).first()
    if p:
        return p.id
    p = SubsidyProject(name=LEGACY_PROJECT_NAME, project_type='其他',
                       cycle='年', status='启用',
                       remark='存量补贴迁移兜底项目（T09 自动预建）')
    db.session.add(p)
    db.session.commit()
    return p.id


def _migrate_subsidies(db, migration_no: str) -> dict:
    """④ subsidies → subsidy_record 迁移（地力补贴台账已移除，2026-10-23：无存量待迁移）。"""
    return {'migrated': 0, 'orphan_id_card_empty': 0,
            'orphan_no_resident_match': 0, 'skipped_legacy_exists': 0}


def _log_orphan(migration_no, s, orphan_type):
    """孤儿记录（显式计数，不静默丢弃）。"""
    from models.migration import MigrationOrphanLog
    import json as _json
    raw = _json.dumps({
        'id': s.id, 'year': s.year, 'household_name': s.household_name,
        'id_card': s.id_card, 'village_group': s.village_group,
        'amount': s.amount, 'pay_date': str(s.pay_date),
    }, ensure_ascii=False)
    from models.migration import MigrationOrphanLog
    from models import db
    db.session.add(MigrationOrphanLog(
        migration_no=migration_no, source_table='subsidies', source_id=s.id,
        orphan_type=orphan_type, raw_row=raw, amount=s.amount,
        year=s.year, handled=0))


def _reconcile(db) -> dict:
    """⑤ G-4 对账（地力补贴台账已移除，2026-10-23：无 subsidies 旧表待对账）。"""
    return {'ok': True, 'per_year': {}}


def _backup_db(db_path: str, backup_dir: str) -> str:
    """① 明文备份。"""
    os.makedirs(backup_dir, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(backup_dir, f'pre_migration_{ts}.db')
    if os.path.exists(db_path):
        shutil.copy2(db_path, dst)
    return dst


def _apply_freeze_triggers(db) -> int:
    """④.5 旧表冻结触发器（地力补贴台账已移除，2026-10-23：无 subsidies 待冻结）。"""
    return 0


def run_migration(app, key_holder, dry_run: bool = False) -> dict:
    """六步链主函数。返回迁移报告 dict。"""
    from utils.crypto import CryptoUninitialized
    from models import db
    from models.migration import MigrationRecord

    if not key_holder.is_initialized:
        raise CryptoUninitialized('密钥未解锁：迁移前须先输入启动口令')

    report = {'step': 'init', 'dry_run': dry_run, 'stats': {}, 'reconcile': None}

    with app.app_context():
        # ① 明文备份
        db_path = app.config.get('SQLALCHEMY_DATABASE_URI', '').replace(
            'sqlite:///', '').split('?')[0]
        if db_path and not dry_run:
            report['backup'] = _backup_db(db_path, app.config.get('BACKUP_FOLDER', '.'))

        # 确保结构（m003 幂等兜底）
        report['stats']['columns'] = ensure_sensitive_columns(db)

        if dry_run:
            report['step'] = 'preview'
            return report

        # 写 running 记录
        migration_no = f"mig_{int(time.time() * 1000)}"
        mr = MigrationRecord(migration_no=migration_no, version='v2.45',
                             status='running', started_at=datetime.now())
        db.session.add(mr)
        db.session.commit()

        # ② 加密转换
        inventory = load_inventory()
        encrypt_stats = {}
        for t in inventory['tables']:
            table = t['table']
            for field in t['fields']:
                cnt = _encrypt_field(db, key_holder, table, field)
                encrypt_stats[f'{table}.{field}'] = cnt
        report['stats']['encrypt'] = encrypt_stats

        # ③ resident_tag 回填
        report['stats']['tags'] = _backfill_tags(db)

        # ④ subsidy 迁移
        report['stats']['subsidy'] = _migrate_subsidies(db, migration_no)

        # ④.5 旧表冻结触发器（m005，subsidy 迁移后冻结）
        report['stats']['freeze'] = _apply_freeze_triggers(db)

        # ⑤ 对账
        report['reconcile'] = _reconcile(db)

        # ⑥ 原子切换（仅 G-4 通过才写 done）
        if report['reconcile']['ok']:
            mr.status = 'done'
            mr.reconcile_result = json.dumps(report['reconcile'], ensure_ascii=False)
            mr.row_stats = json.dumps(report['stats'], ensure_ascii=False)
            mr.finished_at = datetime.now()
            report['step'] = 'done'
        else:
            mr.status = 'failed'
            mr.reconcile_result = json.dumps(report['reconcile'], ensure_ascii=False)
            report['step'] = 'failed'
        db.session.commit()

    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='R12 存量加密转换六步链')
    parser.add_argument('--dry-run', action='store_true', help='预览不执行')
    parser.add_argument('--execute', action='store_true', help='执行六步链')
    args = parser.parse_args(argv)
    if not args.dry_run and not args.execute:
        args.dry_run = True

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
    from utils.crypto import key_holder
    app = create_app()
    report = run_migration(app, key_holder, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report['step'] in ('done', 'preview') else 1


if __name__ == '__main__':
    raise SystemExit(main())
