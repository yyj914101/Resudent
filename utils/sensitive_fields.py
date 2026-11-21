# -*- coding: utf-8 -*-
"""utils.sensitive_fields — 通用敏感字段加密读写/检索管线（R12 复用重构）。

背景：routes/resident.py 内 `_decrypt_field` / `_set_encrypted_field` / `_sensitive_clause`
三函数本是泛型实现（getattr/setattr + _SENS_FIELDS 映射），但被模块级 `Resident` /
`_SENS_FIELDS` 绑定，导致 Dibao / Disabled 等台账模型无法复用，只能各自
明文读写（"明文孤岛"）。

整改：将三函数泛化抽离为通用工具，通过 `make_sensitive_helpers` 工厂按模型生成
绑定的 (decrypt, set_encrypted, clause) 三件套，消除各模块重复实现。

口径（对齐 R12 / 原 resident.py 实现）：
- 读：仅解密 _enc；残留明文只告警不泄露（P2-2 整改）
- 写：加密写 _enc/_hash/prefix_hash，原列置 NULL；id_card 统一大写
- 检索：sha256 等值 / 前缀桶 + 应用层解密二次过滤（保持 LIKE 子串语义）
"""
from __future__ import annotations

import logging

from sqlalchemy import or_

from utils.crypto import sha256_hash, sha256_prefix_hash

_log = logging.getLogger("shucun")


def decrypt_field(instance, field, field_map, key_holder) -> str:
    """读敏感字段：仅解密 _enc；残留明文只告警不泄露（P2-2 整改）。"""
    enc_col = field_map[field][0]
    enc = getattr(instance, enc_col, None)
    if enc:
        try:
            return key_holder.decrypt(enc)
        except Exception:
            return ""
    if getattr(instance, field, None):
        _log.warning(
            "检测到未加密敏感字段 %s（id=%s），请执行数据迁移清除明文残留",
            field, getattr(instance, "id", None))
    return ""


def set_encrypted_field(instance, field, value, field_map, key_holder) -> None:
    """写敏感字段：加密写 _enc/_hash/prefix_hash，原列置 NULL。空值跳过。"""
    value = str(value or "").strip()
    if not value:
        return
    if field.endswith("id_card"):
        value = value.upper()  # 写/搜统一大写，防末位 X 大小写导致 hash 错配
    setattr(instance, field, None)
    enc_col, hash_col, prefix_col = field_map[field]
    setattr(instance, enc_col, key_holder.encrypt(value))
    setattr(instance, hash_col, sha256_hash(value))
    if prefix_col:
        setattr(instance, prefix_col, sha256_prefix_hash(value, 6))


def sensitive_clause(query, model, field, raw, field_map, key_holder, decrypt_fn):
    """敏感字段搜索：返回 (query, post_filter_fn|None)。

    - 精确等值 → sha256_hash 等值（SQL 层）
    - >=6 位 → sha256_prefix_hash(前6) 候选桶 + 应用层解密二次过滤
    - <6 位 → 仅精确等值（避免桶过大）
    """
    v = str(raw or "").strip()
    if not v:
        return query, None
    if field.endswith("id_card"):
        v = v.upper()
    enc_col, hash_col, prefix_col = field_map[field]
    exact = getattr(model, hash_col) == sha256_hash(v)
    if len(v) >= 6 and prefix_col:
        bucket = getattr(model, prefix_col) == sha256_prefix_hash(v[:6], 6)
        q2 = query.filter(or_(exact, bucket))

        def post_filter(rows, value=v):
            return [r for r in rows if value in (decrypt_fn(r, field) or "")]

        return q2, post_filter
    return query.filter(exact), None


def make_sensitive_helpers(model, field_map, key_holder):
    """为指定模型生成绑定的 (decrypt, set_encrypted, clause) 三件套。

    用法：
        decrypt, set_enc, clause = make_sensitive_helpers(Dibao, DIBAO_SENS_FIELDS, key_holder)
    """
    def decrypt(instance, field):
        return decrypt_field(instance, field, field_map, key_holder)

    def set_encrypted(instance, field, value):
        set_encrypted_field(instance, field, value, field_map, key_holder)

    def clause(query, field, raw):
        return sensitive_clause(query, model, field, raw, field_map, key_holder, decrypt)

    return decrypt, set_encrypted, clause
