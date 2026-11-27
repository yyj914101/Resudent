#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_multi_phone_hashes.py — 方案 A 历史坏形态清洗工具（任务 129）。

背景（128 之前的存量行）：单格多电话导入时对整段带 \\n 原文算 phone_hash / phone_prefix_hash，
导致主号检索失效。128 套打后新写入已正确（主号 hash），但历史行需要本工具重算修复。

判定坏形态：解密 phone_enc 后：
  ①含换行/多号分隔符（' / ' 以外的 ,，;；、| 等）——导入时未规范化；
  ②或 phone_hash == sha256(解密原文) 且原文为多号拼接（128 前整段 hash 形态）。

修复动作（幂等）：
  normalize = normalize_phones(原文)  → 重写 phone_enc（密文更新，' / ' 拼接去换行）
  phone_hash = sha256(primary_phone)  → 主号哈希
  phone_prefix_hash = sha256(主号前 6 位)
  raw phone 列保持 NULL（128 既定，不回填明文）

适用表：字段映射三元组 (enc, hash, prefix) 与 utils/sensitive_fields 同构的表。
用法（须先停服或确认无并发写；启动口令通过环境变量传入）：
    SHUCUN_UNLOCK_PASSWORD=*** python scripts/fix_multi_phone_hashes.py --dry-run   # 只报告
    SHUCUN_UNLOCK_PASSWORD=*** python scripts/fix_multi_phone_hashes.py --execute   # 执行修复
"""
from __future__ import annotations

import os
import sys
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, '..'))  # scripts/ 的上一级 = 应用根
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)  # config.py 与 sqlite 相对路径按应用根解析

from utils.crypto import key_holder, load_key_meta, sha256_hash, sha256_prefix_hash
from utils.phone_utils import normalize_phones, primary_phone, split_phones

# 表 → (模型名导入路径, phone 字段名)（prefix 列 None = 该表无前缀列）
TARGETS = [
    ('residents', 'models', 'Resident', 'phone'),
    ('care_targets', 'models', 'CareTarget', 'guardian_phone'),
    ('special_support', 'models', 'CareTarget', 'phone'),
    ('unsupported_children', 'models', 'CareTarget', 'phone'),
    ('disabled_persons', 'models', 'Disabled', 'phone'),
    ('party_members', 'models', 'PartyMember', 'phone'),
    ('veteran', 'models', 'Veteran', 'phone'),
    ('welfare_employment', 'models', 'WelfareEmployment', 'phone'),
    ('organization_members', 'models', 'OrganizationMember', 'phone'),
]


def _field_map_for(model, field):
    """从模型上反查 (enc, hash, prefix) 三元组（列名约定：{f}_enc/{f}_hash/{f}_prefix_hash）。"""
    enc = f'{field}_enc'
    hash_col = f'{field}_hash'
    prefix = f'{field}_prefix_hash'
    cols = {c.name for c in model.__table__.columns}
    return (enc, hash_col, prefix if prefix in cols else None)


def _is_multi_phone_raw(decrypted: str) -> bool:
    """解密原文是否为多号形态（含换行或未被 ' / ' 规范化的分隔符）。"""
    if not decrypted:
        return False
    if '\n' in decrypted or '\r' in decrypted:
        return True
    parts = split_phones(decrypted)
    # 多于 1 号且未按 PHONE_SEP 规范化（含中文分隔符）→ 坏形态
    return len(parts) > 1 and ' / ' not in decrypted


def _is_legacy_whole_hash(decrypted: str, current_hash: str) -> bool:
    """128 前整段 hash 形态：hash == sha256(原文) 且原文是多号。"""
    return bool(decrypted) and current_hash == sha256_hash(decrypted) and len(split_phones(decrypted)) > 1


def main():
    parser = argparse.ArgumentParser(description='多电话坏形态 hash 清洗（幂等）')
    parser.add_argument('--dry-run', action='store_true', help='只报告不修复')
    parser.add_argument('--execute', action='store_true', help='执行修复')
    args = parser.parse_args()
    if not (args.dry_run or args.execute):
        parser.error('须指定 --dry-run 或 --execute')

    password = os.environ.get('SHUCUN_UNLOCK_PASSWORD') or ''
    if not password:
        print('缺少 SHUCUN_UNLOCK_PASSWORD 环境变量（启动口令）', file=sys.stderr)
        sys.exit(2)

    from app import create_app
    app = create_app()
    meta = load_key_meta()
    if not meta:
        print('key_meta 不存在（库从未初始化）', file=sys.stderr)
        sys.exit(2)
    ok = key_holder.unlock(password, bytes.fromhex(meta['salt_hex']), meta['iterations'], meta['check_token'])
    if not ok:
        print('启动口令不正确（unlock 失败）', file=sys.stderr)
        sys.exit(2)

    with app.app_context():
        from models import db
        total_bad = 0
        total_fixed = 0
        for table, mod_name, cls_name, field in TARGETS:
            try:
                module = __import__(mod_name, fromlist=[cls_name])
                model = getattr(module, cls_name)
            except (ImportError, AttributeError):
                continue  # 模型不存在（可选模块）跳过
            fm = _field_map_for(model, field)
            if not fm[0] or not hasattr(model, fm[0]):
                continue  # 该模型无此 phone 加密列（同表多字段映射：CareTarget 三表三字段）
            enc_col, hash_col, prefix_col = fm
            rows = model.query.filter(getattr(model, enc_col).isnot(None)).all()
            bad_rows = []
            for inst in rows:
                try:
                    decrypted = key_holder.decrypt(getattr(inst, enc_col) or '')
                except Exception:
                    continue  # 解不开（异钥）跳过并提示
                current_hash = getattr(inst, hash_col) or ''
                if _is_multi_phone_raw(decrypted) or _is_legacy_whole_hash(decrypted, current_hash):
                    bad_rows.append((inst, decrypted))
            if bad_rows:
                print(f'[{table}] 坏形态 {len(bad_rows)}/{len(rows)} 行（字段 {field}）')
            for inst, decrypted in bad_rows:
                norm = normalize_phones(decrypted)
                p_main = primary_phone(norm)
                if args.execute:
                    setattr(inst, enc_col, key_holder.encrypt(norm))
                    setattr(inst, hash_col, sha256_hash(p_main))
                    if prefix_col:
                        setattr(inst, prefix_col, sha256_prefix_hash(p_main, 6))
                    total_fixed += 1
                    print(f'  fix id={inst.id}: {len(split_phones(decrypted))} 号 → 主号 {p_main[:3]}****{p_main[-4:]}')
                else:
                    total_bad += 1
                    print(f'  [dry] id={inst.id}: {len(split_phones(decrypted))} 号待修')
        if args.execute:
            db.session.commit()
            print(f'完成：修复 {total_fixed} 行（幂等：再跑一遍将 0 报告）')
        else:
            print(f'共发现 {total_bad} 行坏形态（dry-run 未改动）')


if __name__ == '__main__':
    main()
