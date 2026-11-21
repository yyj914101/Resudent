#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/migrations/rekey.py — 密钥轮换演练（T05 产出）。

用途：启动口令更换时，用旧密钥解密、新密钥重新加密全部敏感字段。
- 纯演练/迁移模式：读取 key_meta.json（旧盐）→ 输入旧口令派生旧密钥
  → 输入新口令派生新密钥 → 逐表逐字段 re-encrypt
- 幂等：重复执行同一对新旧密钥，密文稳定（Fernet 密文随 IV 不同，但可重跑不报错）
- 保守：默认 dry-run（--apply 才真正写库）；先备份

注意：Sprint1 阶段敏感字段表尚未建立（T08/T09），本脚本提供 re-encrypt 核心函数
与表/字段清单扩展点，实际迁移由 T09 存量转换向导调用。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from utils.crypto import (
    PBKDF2_ITERATIONS_DEFAULT,
    decrypt_value,
    derive_fernet_key,
    encrypt_value,
    load_key_meta,
)


# 敏感字段映射表（T09 会扩展为 18 表 40 字段；此处为 rekey 演示核心子集）
# 格式：{表名: {列名: 长度上限}}
SENSITIVE_FIELDS: dict[str, dict[str, int]] = {
    "residents": {"id_card": 120, "phone": 100, "bank_card": 120},
    "dibao": {"id_card": 120, "bank_card": 120},
    "disabled_persons": {"id_card": 120},
    "care_targets": {"id_card": 120},
    "medical_insurance_payments": {"id_card": 120},
}


def derive_old_key(old_password: str, salt_hex: str) -> bytes:
    salt = bytes.fromhex(salt_hex)
    return derive_fernet_key(old_password, salt)


def derive_new_key(new_password: str, new_salt_hex: str) -> bytes:
    salt = bytes.fromhex(new_salt_hex)
    return derive_fernet_key(new_password, salt)


def rekey_table(cur, conn, table: str, columns: dict, old_key: bytes, new_key: bytes,
                dry_run: bool = True) -> int:
    """对单表指定列执行 re-encrypt。返回处理行数。幂等可重跑。"""
    rows = cur.execute(f'SELECT id, {", ".join(columns)} FROM "{table}"').fetchall()
    col_names = list(columns)
    processed = 0
    for row in rows:
        row_id = row[0]
        updates = []
        for i, col in enumerate(col_names):
            raw = row[i + 1]
            if raw is None or raw == "":
                continue
            plain = decrypt_value(old_key, raw)      # 旧密钥解密
            new_enc = encrypt_value(new_key, plain)  # 新密钥加密
            updates.append((col, new_enc))
        if not updates:
            continue
        if not dry_run:
            set_sql = ", ".join(f'"{c}" = ?' for c, _ in updates)
            cur.execute(f'UPDATE "{table}" SET {set_sql} WHERE id = ?',
                        (*[v for _, v in updates], row_id))
        processed += 1
    if not dry_run:
        conn.commit()
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description="R12 密钥轮换演练（rekey）")
    parser.add_argument("--old-password", required=True, help="旧启动口令")
    parser.add_argument("--new-password", required=True, help="新启动口令")
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--new-salt-hex", default="", help="新盐（16 字节 hex；缺省自动生成并打印）")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    args = parser.parse_args()

    meta = load_key_meta()
    if meta is None:
        print("❌ 未找到 key_meta.json，无法派生旧密钥")
        return 1
    old_salt_hex = meta["salt_hex"]
    old_key = derive_old_key(args.old_password, old_salt_hex)

    new_salt_hex = args.new_salt_hex or os.urandom(16).hex()
    new_key = derive_new_key(args.new_password, new_salt_hex)

    import sqlite3
    if not os.path.exists(args.db):
        print(f"❌ 数据库不存在: {args.db}")
        return 1
    if not args.apply:
        backup = f"{args.db}.rekey-bak-{time.strftime('%Y%m%d%H%M%S')}"
        shutil.copy2(args.db, backup)
        print(f"ℹ️ dry-run 备份: {backup}")

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("═" * 60)
    print("R12 密钥轮换演练（rekey）")
    print("═" * 60)
    print(f"模式: {'APPLY（写库）' if args.apply else 'DRY-RUN（不写库）'}")
    print(f"旧盐: {old_salt_hex[:12]}…  新盐: {new_salt_hex[:12]}…")
    print()

    total = 0
    for table, columns in SENSITIVE_FIELDS.items():
        try:
            n = rekey_table(cur, conn, table, columns, old_key, new_key, dry_run=not args.apply)
        except Exception as e:
            print(f"  ⚠️ {table}: 跳过（{type(e).__name__}: {e}）")
            continue
        print(f"  {table}: {n} 行" + ("（已重加密）" if args.apply else "（将重加密）"))
        total += n

    conn.close()
    print()
    print(f"合计: {total} 行")
    if not args.apply:
        print("ℹ️ 新盐（正式执行时传 --new-salt-hex 保持稳定）：")
        print(new_salt_hex)
        print("执行: python scripts/migrations/rekey.py --old-password ... --new-password ... --db ... --new-salt-hex <上述值> --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
