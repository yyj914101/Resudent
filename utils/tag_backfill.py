# -*- coding: utf-8 -*-
"""T16-2 标签回填唯一真值（R3-E2，owner=utils/）。

- backfill_tags(db)：存量全量回填（幂等可重跑，修正口径版）——迁移自 scripts/migrations/run_migration.py::_backfill_tags
- sync_resident_tags(record, category)：事件驱动单记录同步（保存/软失效时由蓝图调用）
- ensure_tag / invalidate_tag：幂等打标 / 标签软失效
- ensure_tag_dict()：幂等补录「特困供养」标签字典项

口径（对齐详细设计 §2.3 双轨 + PRD R3-E1/C4 + T14 SPECIAL_NO_RESIDENT_ID）：
- dibao → 低保
- disabled → 残疾
- care_target：老人 → 困难老人；儿童+留守 → 留守儿童；儿童+困境 → 困境儿童
- special_support → 特困供养（household_id_card sha256 hash 匹配居民，T09 决策 2）
- household_property ∈ {脱贫户, 监测户, 五保户} → 户属性标签（保留既有口径）
仅 status='有效' 记录回填；幂等以 UNIQUE(resident_id, tag_code, status) + 有效态查重实现
（source_hash 语义对齐——计划稿 source_hash 表述以本实现为准，未新增 DDL）。
"""
from datetime import datetime

from models import db, Resident, ResidentTag

TAG_DIBAO = '低保'
TAG_DISABLED = '残疾'
TAG_CARE_ELDER = '困难老人'
TAG_CARE_LIUSHOU = '留守儿童'
TAG_CARE_KUNJING = '困境儿童'
TAG_SPECIAL = '特困供养'
HOUSEHOLD_TAG_PROPS = ('脱贫户', '监测户', '五保户')

# 分类 → (模型键, 固定标签, 身份证匹配字段)
CATEGORY_TAG_MAP = {
    'dibao': ('dibao', TAG_DIBAO, 'id_card'),
    'disabled': ('disabled', TAG_DISABLED, 'id_card'),
    'care_target': ('care_target', None, None),  # 标签按 target_type/child_type 动态
    'special_support': ('special_support', TAG_SPECIAL, 'household_id_card'),
}


def _tag_for_care_target(record):
    """CareTarget 动态标签（对齐 _backfill_tags 修正口径）。"""
    if record.target_type == '老人':
        return TAG_CARE_ELDER
    if record.target_type == '儿童':
        if record.child_type == '留守':
            return TAG_CARE_LIUSHOU
        if record.child_type == '困境':
            return TAG_CARE_KUNJING
    return None


def _match_resident(record, category):
    """resident_id 直连优先；否则身份证/户主身份证 sha256 hash 匹配（对齐 T14 _match_without_resident_id）。

    匹配过程不输出明文身份证（脱敏红线）；身份证统一 upper（对齐 residents 写库口径）。
    优先用加密哈希列直接匹配（已接入字段级加密的模型如 dibao）；无哈希列则回退明文 sha256。
    """
    rid = getattr(record, 'resident_id', None)
    if rid:
        return Resident.query.get(rid)
    id_field = CATEGORY_TAG_MAP[category][2]
    if id_field:
        hash_col = getattr(record, f'{id_field}_hash', None)
        if hash_col:
            return Resident.query.filter_by(id_card_hash=hash_col).first()
        raw = getattr(record, id_field, None)
        if raw:
            from utils.crypto import sha256_hash
            h = sha256_hash(str(raw).strip().upper())
            return Resident.query.filter_by(id_card_hash=h).first()
    return None


def _tags_for_record(record, category):
    """记录 → [(resident_id, tag_code)] 列表。"""
    res = _match_resident(record, category)
    if not res:
        return []
    if category == 'care_target':
        tag = _tag_for_care_target(record)
        return [(res.id, tag)] if tag else []
    _, tag, _ = CATEGORY_TAG_MAP[category]
    return [(res.id, tag)]


def ensure_tag(resident_id, tag_code, source='自动'):
    """幂等打标：有效态存在跳过（UNIQUE 防重），否则创建。返回 'added'/'skipped'。"""
    if not resident_id or not tag_code:
        return 'skipped'
    exists = ResidentTag.query.filter_by(
        resident_id=resident_id, tag_code=tag_code, status='有效').first()
    if exists:
        return 'skipped'
    db.session.add(ResidentTag(resident_id=resident_id, tag_code=tag_code,
                               source=source, status='有效'))
    return 'added'


def invalidate_tag(resident_id, tag_code, reason='来源失效'):
    """标签软失效（对齐 /api/tag/remove 语义）。返回失效条数。"""
    if not resident_id or not tag_code:
        return 0
    tags = ResidentTag.query.filter_by(
        resident_id=resident_id, tag_code=tag_code, status='有效').all()
    for t in tags:
        t.status = '失效'
        t.invalid_reason = reason
        t.invalid_time = datetime.now()
    return len(tags)


def sync_resident_tags(record, category):
    """事件驱动单记录同步：记录 status='有效' → 确保标签；status='失效' → 标签软失效。

    由 dibao/disabled/care 蓝图保存端点（commit 前）调用。
    返回 {'synced', 'added', 'invalidated'}。
    """
    tags = _tags_for_record(record, category)
    if getattr(record, 'status', '有效') == '失效':
        invalidated = sum(
            invalidate_tag(rid, tag, reason=getattr(record, 'invalid_reason', None) or '来源失效')
            for rid, tag in tags)
        return {'synced': 0, 'added': 0, 'invalidated': invalidated}
    added = 0
    for rid, tag in tags:
        if ensure_tag(rid, tag) == 'added':
            added += 1
    return {'synced': len(tags), 'added': added, 'invalidated': 0}


def backfill_tags(_db=None):
    """存量全量回填（幂等可重跑）。仅 status='有效' 记录。

    _db 参数仅兼容 run_migration 调用点（六步链第③步传 db），
    实现用模块级全局 db.session（避免参数遮蔽）。
    返回 {'added', 'skipped', 'by_category': {标签: 命中数}}。
    """
    from models.dibao import Dibao
    from models.disabled import Disabled
    from models.care_target import CareTarget, SpecialSupport

    stat = {'added': 0, 'skipped': 0, 'by_category': {}}

    def _apply(category, records):
        for rec in records:
            for rid, tag in _tags_for_record(rec, category):
                if ensure_tag(rid, tag) == 'added':
                    stat['added'] += 1
                else:
                    stat['skipped'] += 1
                stat['by_category'][tag] = stat['by_category'].get(tag, 0) + 1

    _apply('dibao', Dibao.query.filter(Dibao.status == '有效').all())
    _apply('disabled', Disabled.query.filter(Disabled.status == '有效').all())
    _apply('care_target', CareTarget.query.filter(CareTarget.status == '有效').all())
    _apply('special_support', SpecialSupport.query.filter(SpecialSupport.status == '有效').all())
    # 户属性标签（保留既有口径，与 import_engine 一致）
    for r in Resident.query.filter(
            Resident.household_property.in_(HOUSEHOLD_TAG_PROPS)).all():
        if ensure_tag(r.id, r.household_property) == 'added':
            stat['added'] += 1
        else:
            stat['skipped'] += 1
        stat['by_category'][r.household_property] = stat['by_category'].get(r.household_property, 0) + 1

    db.session.commit()
    return stat


def ensure_tag_dict():
    """幂等补录『特困供养』到『居民标签』字典（存量库）。

    标签字典无外键强制，但 /api/tag/add 手动打标经 _valid_tag_codes 校验字典——
    补录后手动打标「特困供养」可用。返回 {'added', 'skipped'}。
    """
    from models import DictCategory, DictItem
    cat = DictCategory.query.filter_by(name='居民标签').first()
    if not cat:
        return {'added': 0, 'skipped': 0}
    exists = DictItem.query.filter_by(category_id=cat.id, value='特困供养').first()
    if exists:
        return {'added': 0, 'skipped': 1}
    max_sort = db.session.query(db.func.max(DictItem.sort_order)).filter_by(
        category_id=cat.id).scalar() or 0
    db.session.add(DictItem(category_id=cat.id, label='特困供养', value='特困供养',
                            sort_order=max_sort + 10))
    db.session.commit()
    return {'added': 1, 'skipped': 0}
