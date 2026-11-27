# -*- coding: utf-8 -*-
"""T16-3 死亡软失效联动（R3-E4，owner=utils/）。

居民死亡（residents.status='deceased'）后，弱势群体关联软失效：
- dibao / disabled / care_target：resident_id 直连，status='有效' → '失效' + invalid_reason
- special_support：无 resident_id，按居民 id_card_hash 反向匹配（sha256_hash(household_id_card)==id_card_hash，对齐 T16-2 正向口径）
- resident_tag：该居民全部有效标签软失效（复用 tag_backfill.invalidate_tag 语义，批量处理）

边界（U3 拍板，M4.0）：不物理删除；不碰补贴停发/告知单/48h 撤回（T15 CascadeService）；
不写 death_report（T15 级联载体）；不做复活恢复（T15 撤回机制范围）。
幂等：仅 status='有效' 的记录/标签被更新，重复调用无副作用。
"""
from datetime import datetime

from models import db, ResidentTag


def soft_invalidate_vulnerable(resident_id, reason='死亡注销'):
    """居民死亡后弱势群体四表 + resident_tag 联动软失效（幂等）。

    返回 {'dibao': n, 'disabled': n, 'care_target': n, 'special_support': n, 'tags': n}。
    调用方需在事务提交前调用（与居民状态变更同一事务）。
    """
    result = {'dibao': 0, 'disabled': 0, 'care_target': 0, 'special_support': 0, 'tags': 0}
    if not resident_id:
        return result

    # ① resident_id 直连三表（仅有效记录软失效，幂等）
    from models.dibao import Dibao
    from models.disabled import Disabled
    from models.care_target import CareTarget

    for model, key in ((Dibao, 'dibao'), (Disabled, 'disabled'), (CareTarget, 'care_target')):
        rows = model.query.filter_by(resident_id=resident_id, status='有效').all()
        for r in rows:
            r.status = '失效'
            r.invalid_reason = reason
        result[key] = len(rows)

    # ② special_support：按居民 id_card_hash 反向匹配（无 resident_id）
    from models.resident import Resident
    from models.care_target import SpecialSupport
    resident = Resident.query.get(resident_id)
    if resident and getattr(resident, 'id_card_hash', None):
        from utils.crypto import sha256_hash

        def _sp_hash(sp):
            # R12 过渡态兼容：优先哈希列，回退明文列（测试/存量未迁移数据）
            if getattr(sp, 'household_id_card_hash', None):
                return sp.household_id_card_hash
            raw = getattr(sp, 'household_id_card', None)
            return sha256_hash(str(raw).strip().upper()) if raw else None

        rows = [sp for sp in SpecialSupport.query.filter_by(status='有效').all()
                if _sp_hash(sp) == resident.id_card_hash]
        for sp in rows:
            sp.status = '失效'
            sp.invalid_reason = reason
        result['special_support'] = len(rows)

    # ③ resident_tag：该居民全部有效标签软失效
    tags = ResidentTag.query.filter_by(resident_id=resident_id, status='有效').all()
    now = datetime.now()
    for t in tags:
        t.status = '失效'
        t.invalid_reason = reason
        t.invalid_time = now
    result['tags'] = len(tags)

    return result
