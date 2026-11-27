# -*- coding: utf-8 -*-
"""utils/phone_utils.py — 多电话号码规范化与主号提取（方案 A 单一真源）。

生产数据中单个 Excel 单元格常含多个换行/符号分隔的手机号（如 5 个手机号换行共存）。
本模块提供统一的清洗、去重、标准拼接与主号提取，供导入、敏感字段加密哈希、脱敏与检索共用：
  - 存储格式：单号原样保留；多号统一以 ' / ' 拼接（避免单行输入框吞掉换行导致号码粘连）
  - 检索哈希：以第 1 个主号码计算 phone_hash / phone_prefix_hash（保证主号 SQL 索引命中）
  - 脱敏展示：配合 utils/masking.py:mask_phone 对每个号码逐一前 3 后 4 脱敏
"""
from __future__ import annotations

import re
from typing import List

PHONE_SEP = ' / '
_SPLIT_RE = re.compile(r'[\r\n,，;；、/|｜\s]+')


def split_phones(raw) -> List[str]:
    """拆分单元格中的单个或多个电话号码，去空白并按出现顺序去重。"""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []
    parts = [p.strip() for p in _SPLIT_RE.split(text) if p and p.strip()]
    seen = set()
    result: List[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def normalize_phones(raw) -> str:
    """规范化电话字符串：单号原样返回，多号统一用 ' / ' 拼接。"""
    parts = split_phones(raw)
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    return PHONE_SEP.join(parts)


def primary_phone(raw) -> str:
    """提取第 1 个主号码（供 phone_hash / phone_prefix_hash 计算）。"""
    parts = split_phones(raw)
    return parts[0] if parts else ''
