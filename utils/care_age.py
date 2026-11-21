# -*- coding: utf-8 -*-
"""utils.care_age — 一老一小动态归类年龄计算（T13，单一真值）。

规格依据（PRD v1.2.3 R2-E1/R2-E2 + 详细设计 v1.1 §2.3）：
- 高龄老人：birth_date 动态计算（默认 ≥80，阈值可配置），不落库、不建派生表、不打标签
- 儿童候选池：<18 岁；仅「留守/困境」政策性入册（child_type 落库），<18 全体仅候选池
- 年龄计算用「精确日差」口径（对齐 PRD §2.3 julianday 精确日差，非年差近似）
- age_class_override：人工确认覆盖值优先，30 天保护期内不被动态计算冲掉（C3）

owner map：年龄/归类业务判断唯一落本模块（禁在 routes/ 内重写年差公式）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

ELDERLY_THRESHOLD_DEFAULT = 80
CHILD_AGE_MAX = 18          # <18 为儿童候选池
OVERRIDE_PROTECT_DAYS = 30  # override 30 天保护窗口（C3）


def elderly_threshold() -> int:
    """高龄阈值（可配，默认 80）；读 config.Config.ELDERLY_AGE_THRESHOLD。"""
    try:
        from config import Config
        return int(getattr(Config, 'ELDERLY_AGE_THRESHOLD', ELDERLY_THRESHOLD_DEFAULT))
    except Exception:
        return ELDERLY_THRESHOLD_DEFAULT


def calculate_age(birth_date) -> int | None:
    """精确年龄（周岁）：按 (今天 - 出生日) 天数差 // 365.2425 取整，非年差近似。

    兼容 datetime/date/str（YYYY-MM-DD）；无 birth_date 返回 None。
    """
    if birth_date is None:
        return None
    if isinstance(birth_date, datetime):
        birth_date = birth_date.date()
    elif isinstance(birth_date, str):
        s = birth_date.strip()
        try:
            birth_date = datetime.strptime(s, '%Y-%m-%d').date()
        except ValueError:
            try:
                birth_date = datetime.strptime(s[:10], '%Y-%m-%d').date()
            except ValueError:
                return None
    if not isinstance(birth_date, date):
        return None
    today = date.today()
    if birth_date > today:
        return 0
    days = (today - birth_date).days
    return int(days // 365.2425)


def is_elderly(birth_date, threshold: int | None = None) -> bool:
    th = threshold if threshold is not None else elderly_threshold()
    age = calculate_age(birth_date)
    return age is not None and age >= th


def is_child(birth_date) -> bool:
    age = calculate_age(birth_date)
    return age is not None and age < CHILD_AGE_MAX


def parse_override(override_json, now: datetime | None = None) -> dict | None:
    """解析 age_class_override JSON；30 天保护期外返回 None（失效回退动态计算）。

    结构：{"override_class": "高龄", "reason": "...", "by": "...", "time": "ISO"}
    非法 JSON / 缺 override_class / 超 30 天 → None。
    """
    if not override_json:
        return None
    try:
        d = json.loads(override_json) if isinstance(override_json, str) else override_json
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not d.get('override_class'):
        return None
    now = now or datetime.now()
    ts = d.get('time')
    if ts:
        try:
            t = datetime.fromisoformat(str(ts))
            if now - t > timedelta(days=OVERRIDE_PROTECT_DAYS):
                return None  # 超保护期，失效
        except (ValueError, TypeError):
            pass
    return d


def classify_age(birth_date, override: dict | None = None) -> dict:
    """动态归类（单一真值）：返回 {'age', 'is_elderly', 'is_child', 'class'}。

    override 有效时：class = override['override_class']（覆盖值优先，30 天内不被冲掉）。
    class ∈ 高龄 / 儿童 / 无（成人非高龄）。
    """
    age = calculate_age(birth_date)
    result = {
        'age': age,
        'is_elderly': is_elderly(birth_date),
        'is_child': is_child(birth_date),
        'class': '无',
    }
    if override:
        ov_class = override.get('override_class')
        if ov_class in ('高龄', '儿童'):
            result['class'] = ov_class
            return result
    if result['is_elderly']:
        result['class'] = '高龄'
    elif result['is_child']:
        result['class'] = '儿童'
    return result
