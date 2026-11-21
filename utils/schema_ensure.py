# -*- coding: utf-8 -*-
"""utils.schema_ensure — schema 幂等补列统一入口（P2-4 整改收敛）。

背景：原先 mediation.py / medical_insurance.py / todo.py 各自散落
「PRAGMA table_info + ALTER TABLE ADD COLUMN」样板代码，schema 演进存在多套真源，
违背宪法 §1.6「结构变更一律落 DDL 脚本 + 版本表」。

整改：结构变更真源收敛到 scripts/migrations/（DDL 脚本 + run_migration 幂等兜底）；
本模块仅提供「存量库运行时幂等补列」的通用工具，供各路由复用，消除重复样板。
"""
from __future__ import annotations

import re

from sqlalchemy import text

from models import db

# 021 API-06：DDL 标识符白名单。本模块以 f-string 拼 PRAGMA/ALTER 语句，
# 表/列名若混入用户输入即 DDL 注入；当前调用方全部为硬编码字面量，
# 此校验是把「只进合法标识符」固化为入口契约（AGENTS §1 防补丁式开发）。
_IDENT_RE = re.compile(r'^[a-z_][a-z0-9_]*$')


def _check_ident(kind: str, name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(f"非法{kind}标识符（须匹配 ^[a-z_][a-z0-9_]*$）：{name!r}")
    return name


def ensure_columns(table: str, columns: dict) -> int:
    """幂等补列：columns 为 {列名: DDL 片段}，如 {'completed_by': 'VARCHAR(50)'}。

    - 先 PRAGMA table_info 检查，缺列则 ALTER TABLE ADD COLUMN
    - 有变更则 commit；返回本次补列数
    - table/列名过 _IDENT_RE 白名单（021 API-06），不合法即 ValueError
    """
    _check_ident("表", table)
    if not columns:
        return 0
    for col in columns:
        _check_ident("列", col)
    rows = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
    cols = {row[1] for row in rows}
    added = 0
    for col, ddl in columns.items():
        if col not in cols:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            added += 1
    if added:
        db.session.commit()
    return added


def table_columns(table: str) -> set:
    """返回表当前列名集合（PRAGMA table_info）。"""
    _check_ident("表", table)
    rows = db.session.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}
