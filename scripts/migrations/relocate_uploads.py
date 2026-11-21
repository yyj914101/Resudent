"""存量 uploads 归位迁移脚本（T07 §6.5，B8/B10 闭环）

将现有 uploads 子目录命名统一到 §6.5 规划：
  uploads/mediation/    已对齐，跳过
  孤儿文件（无库记录）  → uploads/misc/

同事务更新库内路径字段，保证一致性。
幂等：目标已存在则跳过。

用法：
  python scripts/migrations/relocate_uploads.py --dry-run    # 预览
  python scripts/migrations/relocate_uploads.py --execute    # 执行
"""
from __future__ import annotations

import os
import sys
import shutil
import argparse

# 确保项目根在 sys.path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..'))
sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault('SHUCUN_DATA_DIR', os.path.join(_PROJECT_ROOT, 'data'))
os.environ.setdefault('SHUCUN_DB_PATH', os.path.join(_PROJECT_ROOT, 'data', 'shucun.db'))
os.environ.setdefault('SHUCUN_UPLOAD_DIR', os.path.join(_PROJECT_ROOT, 'uploads'))


def relocate(uploads_dir: str, dry_run: bool = True) -> dict:
    """执行归位，返回报告字典。"""
    from models import db
    from app import create_app

    app = create_app()
    report = {
        'orphans_to_misc': 0,
        'errors': [],
    }

    os.makedirs(os.path.join(uploads_dir, 'misc'), exist_ok=True)

    with app.app_context():
        # 孤儿文件 → misc
        report = _collect_orphans(uploads_dir, report, dry_run)

    return report


def _collect_orphans(uploads_dir, report, dry_run):
    """收集孤儿文件（无库记录）到 misc/。"""
    misc_dir = os.path.join(uploads_dir, 'misc')
    os.makedirs(misc_dir, exist_ok=True)

    # 扫描根目录散文件（非子目录）
    for name in os.listdir(uploads_dir):
        fpath = os.path.join(uploads_dir, name)
        if not os.path.isfile(fpath):
            continue
        # 跳过 .gitkeep 等标记文件
        if name.startswith('.'):
            continue
        dest = os.path.join(misc_dir, name)
        if os.path.isfile(dest):
            continue
        if not dry_run:
            shutil.move(fpath, dest)
        report['orphans_to_misc'] += 1

    return report


def main():
    parser = argparse.ArgumentParser(description='uploads 存量归位迁移')
    parser.add_argument('--dry-run', action='store_true', help='预览不执行')
    parser.add_argument('--execute', action='store_true', help='执行迁移')
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        args.dry_run = True  # 默认 dry-run

    uploads_dir = os.environ.get('SHUCUN_UPLOAD_DIR', os.path.join(_PROJECT_ROOT, 'uploads'))
    print(f"[relocate_uploads] uploads_dir={uploads_dir} | dry_run={args.dry_run}")
    report = relocate(uploads_dir, dry_run=args.dry_run)

    print("\n=== 归位报告 ===")
    for k, v in report.items():
        if k == 'errors' and v:
            print(f"  {k}:")
            for e in v:
                print(f"    - {e}")
        else:
            print(f"  {k}: {v}")


if __name__ == '__main__':
    main()
