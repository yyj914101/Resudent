# -*- coding: utf-8 -*-
"""utils.resident_sync — 主档变更事件驱动副本同步（022）。

主档 api_save 编辑白名单字段后，把最新值回写所有关联专项模块副本，同事务强一致。
- 定位：强耦合按 resident_id；中耦合（medical）按 resident_id + 仅已关联行；松散耦合按 id_card_hash（hash 空回退明文 sha256，R-4 过渡态兜底）。
- 排除：death_reports 已确认行（cascade_status='done'）不回写——拍板 #2 历史文书保持快照（审阅修复 2026-10-12）。
- 敏感字段（phone/bank_card）回写 = 复制主档密文/哈希列（同 key_holder，解密一致），副本明文列置 NULL。
- 事务纪律：不自行 commit，由调用方（路由）统一 commit/except rollback；失败后由调用方另起独立事务补写 failed 日志（对齐 CascadeService 范式）。
- 冲突策略：主档覆盖（last-write-wins）不变；GLB-06 起回写前比对副本分叉并
  另写 status='conflict' 留痕行（不阻断），追溯界面见 /resident/sync-log。
"""
from __future__ import annotations

import logging

_log = logging.getLogger('shucun')

# 参与同步的主档字段白名单（id_card 拍板 #4 不纳入）
SYNC_FIELDS = ('name', 'phone', 'bank_card', 'birth_date', 'gender',
               'nation', 'address', 'village_group')

# 敏感字段（回写复制密文列；日志脱敏）
SENSITIVE_FIELDS = ('phone', 'bank_card')

# 副本表映射：主档字段 → 副本列名（列表 = 写多列，如 medical 双电话）。
# locate：resident_id=强耦合；medical=中耦合(仅已关联行)；hash=松散耦合(id_card_hash)。
# hash 定位默认 hash 列 id_card_hash / 明文列 id_card，special_support 特例用 household_*。
FIELD_MAP = [
    {'table': 'dibao', 'model': 'Dibao', 'locate': 'resident_id',
     'fields': {'name': 'name', 'bank_card': 'bank_card', 'address': 'address',
                'village_group': 'grid'}},  # 拍板 #5：所在组→所在网格
    {'table': 'care_targets', 'model': 'CareTarget', 'locate': 'resident_id',
     'fields': {'name': 'name'}},
    {'table': 'disabled', 'model': 'Disabled', 'locate': 'resident_id',
     'fields': {'name': 'name', 'phone': 'phone', 'gender': 'gender',
                'nation': 'nation', 'village_group': 'village_group'}},
    {'table': 'medical_insurance_payments', 'model': 'MedicalInsurance', 'locate': 'medical',
     'fields': {'name': 'name', 'phone': ['contact_phone', 'mobile_phone'],
                'gender': 'gender', 'address': 'address', 'village_group': 'village_group'}},
    {'table': 'special_support', 'model': 'SpecialSupport', 'locate': 'hash',
     'hash_field': 'household_id_card_hash', 'plain_field': 'household_id_card',
     'fields': {'phone': 'phone', 'bank_card': 'bank_card', 'address': 'address',
                'village_group': 'village_group'}},
    {'table': 'unsupported_children', 'model': 'UnsupportedChild', 'locate': 'hash',
     'fields': {'name': 'name', 'phone': 'phone', 'gender': 'gender',
                'birth_date': 'birth_date', 'address': 'address'}},
    {'table': 'death_reports', 'model': 'DeathReport', 'locate': 'hash',
     'exclude': {'cascade_status': 'done'},  # 拍板 #2：已确认死亡登记保持历史快照，不回写
     'fields': {'name': 'deceased_name', 'gender': 'gender', 'nation': 'nation',
                'address': 'address', 'village_group': 'village_group'}},
    {'table': 'veteran', 'model': 'Veteran', 'locate': 'hash',
     'fields': {'name': 'name', 'phone': 'phone', 'gender': 'gender', 'nation': 'nation'}},
    {'table': 'welfare_job', 'model': 'WelfareJob', 'locate': 'hash',
     'fields': {'name': 'name', 'phone': 'phone', 'village_group': 'village_group'}},
    {'table': 'party_member', 'model': 'PartyMember', 'locate': 'hash',
     'fields': {'name': 'name', 'phone': 'phone', 'gender': 'gender',
                'birth_date': 'birth_date', 'nation': 'nation'}},
    {'table': 'organization_member', 'model': 'OrganizationMember', 'locate': 'hash',
     'fields': {'name': 'name', 'phone': 'phone'}},
    {'table': 'youshu', 'model': 'Youshu', 'locate': 'hash',
     'fields': {'name': 'name', 'phone': 'phone'}},
    {'table': 'welfare_employment', 'model': 'WelfareEmployment', 'locate': 'resident_id',
     'fields': {'name': 'name', 'phone': 'phone', 'gender': 'gender',
                'village_group': 'village_group'}},
    {'table': 'election_voter', 'model': 'ElectionVoter', 'locate': 'hash',
     'plain_field': None,
     'fields': {'name': 'name', 'phone': 'phone', 'village_group': 'group_name'}},
]

_MODEL_CLASSES = {}


def _model_cls(name):
    """模型名 → 类（函数内 import，防循环引用，对齐 CascadeService 范式）。"""
    if name not in _MODEL_CLASSES:
        from models import (Dibao, CareTarget, Disabled,
                            MedicalInsurance, SpecialSupport, UnsupportedChild,
                            DeathReport, Veteran, WelfareJob, PartyMember,
                            OrganizationMember, Youshu, WelfareEmployment,
                            ElectionVoter)
        _MODEL_CLASSES.update({
            'Dibao': Dibao, 'CareTarget': CareTarget, 'Disabled': Disabled,
            'MedicalInsurance': MedicalInsurance, 'SpecialSupport': SpecialSupport,
            'UnsupportedChild': UnsupportedChild, 'DeathReport': DeathReport,
            'Veteran': Veteran, 'WelfareJob': WelfareJob,
            'PartyMember': PartyMember, 'OrganizationMember': OrganizationMember,
            'Youshu': Youshu, 'WelfareEmployment': WelfareEmployment,
            'ElectionVoter': ElectionVoter,
        })
    return _MODEL_CLASSES[name]


def _mask(value, kind):
    """日志脱敏：敏感字段走 masking；非敏感原样；截断防超列宽。"""
    if value is None:
        return None
    if kind:
        from utils.masking import mask_field
        return str(mask_field(value, kind))[:100]
    return str(value)[:100]


def _write_field(row, resident, src_field, dst_col):
    """单字段回写。敏感字段复制主档密文/哈希列；明文字段直接 setattr。"""
    if src_field in SENSITIVE_FIELDS:
        if hasattr(type(row), dst_col):
            setattr(row, dst_col, None)
        setattr(row, dst_col + '_enc', getattr(resident, src_field + '_enc', None))
        setattr(row, dst_col + '_hash', getattr(resident, src_field + '_hash', None))
    else:
        setattr(row, dst_col, getattr(resident, src_field))


def _locate(spec, model, resident):
    """按 spec['locate'] 定位需回写的副本行，再按 spec['exclude'] 排除（拍板 #2 历史快照）。"""
    rid = resident.id
    locate = spec['locate']
    if locate == 'resident_id':
        rows = list(model.query.filter_by(resident_id=rid).all())
        # 按稳定锚点 id_card_hash 回退定位历史/导入行，并顺手回填 resident_id
        target = resident.id_card_hash
        if target and hasattr(model, 'id_card_hash'):
            seen_ids = {r.id for r in rows}
            for r in model.query.filter_by(id_card_hash=target).all():
                if r.id not in seen_ids:
                    if getattr(r, 'resident_id', None) is None:
                        r.resident_id = rid
                    rows.append(r)
                    seen_ids.add(r.id)
    elif locate == 'medical':
        # 仅已关联行；未关联的医保下发原始数据保持原样供核对
        rows = model.query.filter_by(resident_id=rid, link_status='已关联居民').all()
    else:
        # hash 定位（松散耦合）：hash 等值（SQL）+ hash 空但明文残留的回退（R-4 过渡态）
        from utils.crypto import sha256_hash
        hash_field = spec.get('hash_field', 'id_card_hash')
        plain_field = spec.get('plain_field', 'id_card')
        target = resident.id_card_hash
        rows = []
        if target:
            rows = list(model.query.filter_by(**{hash_field: target}).all())
            # ponytail: 过渡态兜底只扫 hash 为空的行（罕见），数据量小可接受
            if plain_field and hasattr(model, plain_field):
                for r in model.query.filter_by(**{hash_field: None}).all():
                    raw = getattr(r, plain_field, None)
                    if raw and sha256_hash(str(raw).strip().upper()) == target:
                        rows.append(r)
    # 通用排除：命中排除条件的行不回写（death_reports 已确认行，拍板 #2）
    for col, val in (spec.get('exclude') or {}).items():
        rows = [r for r in rows if getattr(r, col, None) != val]
    return rows


class ResidentSyncService:
    """主档 → 副本同步（静态方法范式，对齐 CascadeService；禁新建 services/）。"""

    @staticmethod
    def snapshot_plain(resident, decrypt_fn):
        """白名单字段明文快照（敏感字段解密；空值归一化 None），供 diff 用。"""
        snap = {}
        for f in SYNC_FIELDS:
            if f in SENSITIVE_FIELDS:
                snap[f] = decrypt_fn(resident, f) or None
            else:
                snap[f] = getattr(resident, f, None)
        return snap

    @staticmethod
    def diff(old_snap, new_snap):
        """对比快照，返回 {field: (old, new)} 变更集（空归一化避免误报）。"""
        changes = {}
        for f in SYNC_FIELDS:
            if (old_snap.get(f) or None) != (new_snap.get(f) or None):
                changes[f] = (old_snap.get(f), new_snap.get(f))
        return changes

    @staticmethod
    def propagate(resident, changes, operator=None):
        """回写变更字段到全部副本表 + 逐字段写 sync_log。不自行 commit，由调用方统一提交。

        任一步失败抛异常 → 调用方整体 rollback；失败日志由调用方另起独立事务补写。
        affected_tables 按字段实际写入的表计入（审阅修复 P3-3）：
        仅当该字段在本表有映射列且定位到匹配行时才计数，各字段独立留痕。
        冲突检测（GLB-06，原 P3 增强）：回写前比对「副本当前值 ≠ 主档旧值」
        （= 副本在上次同步后被他人改动）。冲突不阻断——按主档覆盖（last-write-wins
        不变），但另写一行 status='conflict' 日志留痕被覆盖的副本值，供追溯界面过滤。
        敏感字段（phone/bank_card）副本存密文列，明文列为 NULL，与主档明文旧值
        不可比，不参与冲突检测（覆盖即密文复制，无语义冲突概念）。
        返回聚合 {table: 影响行数}。
        """
        from models import db, ResidentSyncLog
        import json

        affected = {}                      # 聚合口径（返回值）
        field_affected = {f: {} for f in changes}   # 每字段各自实际影响的表
        conflicts = []                     # GLB-06：{field, table, row_id, copy_value, master_value}
        from utils.linkage_config import get_effective_field_map
        for spec in get_effective_field_map():
            model = _model_cls(spec['model'])
            rows = None                    # 懒定位：首个命中字段才查，无命中字段跳过本表
            for field in changes:
                cols = spec['fields'].get(field)
                if not cols:
                    continue
                if rows is None:
                    rows = _locate(spec, model, resident)
                if not rows:
                    break
                cols = cols if isinstance(cols, (list, tuple)) else [cols]
                master_old = changes[field][0]
                for r in rows:
                    for col in cols:
                        if field not in SENSITIVE_FIELDS:      # GLB-06 冲突比对
                            cur = getattr(r, col, None)
                            if (cur or None) != (master_old or None):
                                conflicts.append({
                                    'field': field, 'table': spec['table'],
                                    'row_id': getattr(r, 'id', None),
                                    'copy_value': cur,
                                    'master_value': changes[field][1],
                                })
                        _write_field(r, resident, field, col)
                field_affected[field][spec['table']] = len(rows)
                affected[spec['table']] = len(rows)

        for field, (old, new) in changes.items():
            kind = field if field in SENSITIVE_FIELDS else None
            db.session.add(ResidentSyncLog(
                resident_id=resident.id,
                field=field,
                old_value=_mask(old, kind),
                new_value=_mask(new, kind),
                affected_tables=json.dumps(field_affected.get(field) or {},
                                           ensure_ascii=False),
                status='success',
                operator=operator or '',
            ))
        for c in conflicts:
            db.session.add(ResidentSyncLog(
                resident_id=resident.id,
                field=c['field'],
                old_value=_mask(c['copy_value'], None),
                new_value=_mask(c['master_value'], None),
                affected_tables=json.dumps({c['table']: 1}, ensure_ascii=False),
                status='conflict',
                error_message=(f"副本值≠主档上次同步值，已按主档覆盖"
                               f"（{c['table']}#{c['row_id']}）")[:200],
                operator=operator or '',
            ))
        return affected

    @staticmethod
    def write_failed_log(resident, changes, error, operator=None):
        """整体 rollback 后另起独立事务补写 failed 日志（否则被回滚连带清掉）。

        error 落 error_message 列（截断 200）供排查（审阅修复 2026-10-12）。
        自身失败静默（不二次抛出），但补日志留痕（审阅修复 P3-7），不影响主档保存结果。
        """
        from models import db, ResidentSyncLog
        try:
            for field, (old, new) in changes.items():
                kind = field if field in SENSITIVE_FIELDS else None
                db.session.add(ResidentSyncLog(
                    resident_id=resident.id,
                    field=field,
                    old_value=_mask(old, kind),
                    new_value=_mask(new, kind),
                    affected_tables='{}',
                    status='failed',
                    error_message=str(error or '')[:200] or None,
                    operator=operator or '',
                ))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            _log.warning('补写居民同步 failed 日志失败（resident_id=%s）: %s',
                         getattr(resident, 'id', None), e)
