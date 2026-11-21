# -*- coding: utf-8 -*-
"""T16-2 标签回填独立入口（存量全量回填，幂等可重跑）。

用法：
    python3 scripts/backfill_tags.py --dry-run   # 预览：回填前后 resident_tag 计数 + 预计 added/skipped
    python3 scripts/backfill_tags.py --execute   # 执行：幂等全量回填 + 标签字典补录 + 计数 diff
    python3 scripts/backfill_tags.py --report    # 仅输出当前 resident_tag 计数（不改库）

对齐 run_migration.main() 模式（env 默认值 + create_app）；唯一真值逻辑在 utils/tag_backfill.py。
"""
import argparse
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


def _count_tags():
    """当前 resident_tag 有效标签计数（按 tag_code 分组）。"""
    from models import db, ResidentTag
    rows = db.session.query(ResidentTag.tag_code, db.func.count(ResidentTag.id)).filter(
        ResidentTag.status == '有效').group_by(ResidentTag.tag_code).all()
    return {tag: cnt for tag, cnt in rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='T16-2 标签回填（存量全量，幂等可重跑）')
    parser.add_argument('--dry-run', action='store_true', help='预览不执行（计数 + 预计 added/skipped）')
    parser.add_argument('--execute', action='store_true', help='执行回填 + 标签字典补录')
    parser.add_argument('--report', action='store_true', help='仅输出当前标签计数（不改库）')
    args = parser.parse_args(argv)

    if not (args.dry_run or args.execute or args.report):
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
    from utils.tag_backfill import backfill_tags, ensure_tag_dict

    app = create_app()
    report = {'step': 'preview'}

    with app.app_context():
        before = _count_tags()
        if args.report:
            report = {'step': 'report', 'tags_before': before}
        elif args.execute:
            dict_stat = ensure_tag_dict()
            fill_stat = backfill_tags()
            after = _count_tags()
            report = {
                'step': 'done',
                'tags_before': before,
                'tags_after': after,
                'fill': fill_stat,
                'dict': dict_stat,
                'diff': {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after)},
            }
        else:  # dry-run：预览计数（不写库）
            # 仅统计可回填的潜在标签数（只读，不落库）
            from models import db, Resident, ResidentTag
            from models.dibao import Dibao
            from models.disabled import Disabled
            from models.care_target import CareTarget, SpecialSupport
            from utils.tag_backfill import _tags_for_record
            potential = {}
            for cat, model in (('dibao', Dibao), ('disabled', Disabled),
                               ('care_target', CareTarget), ('special_support', SpecialSupport)):
                for rec in model.query.filter(model.status == '有效').all():
                    for _rid, tag in _tags_for_record(rec, cat):
                        potential[tag] = potential.get(tag, 0) + 1
            for r in Resident.query.filter(
                    Resident.household_property.in_(('脱贫户', '监测户', '五保户'))).all():
                potential[r.household_property] = potential.get(r.household_property, 0) + 1
            existing = ResidentTag.query.filter(ResidentTag.status == '有效').count()
            report = {
                'step': 'preview',
                'tags_before': before,
                'potential_by_tag': potential,
                'potential_total': sum(potential.values()),
                'existing_valid': existing,
                'note': 'dry-run 不写库',
            }

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
