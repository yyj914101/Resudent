# -*- coding: utf-8 -*-
"""utils.org_sync — 机构管理软表 → OrganizationMember 自动静默单向反哺（方案 A · 补丁 C-1）。

设计红线：
  - 软表 V2 承担三委/小组长/居民代表花名册的日常维护与 A4 套打；
  - 挂入 `organization` 模块的软表在批量建表、追加导入或行编辑保存时，自动调用
    `sync_org_table_to_members(table)` 单向同步至 `organization_members`；
  - 去重策略：优先按 `id_card_hash` 匹配，无身份证号时按 `org_type + name` 匹配；
  - 只新增/更新现任成员（`is_current=True`），绝不删除或篡改历史离任记录（`is_current=False`）。
"""
import re


_ORG_TYPE_HINTS = (
    ('党委', '社区党委'),
    ('支部', '社区党委'),
    ('监委', '居务监督委员会'),
    ('监督', '居务监督委员会'),
    ('居委', '居民委员会'),
    ('村委', '村民委员会'),
    ('小组长', '居民小组长'),
    ('组长', '居民小组长'),
    ('代表', '居民代表'),
)


def infer_org_type(table_name: str, fallback: str = '社区组织') -> str:
    """从软表名称推断 OrganizationMember.org_type。"""
    s = str(table_name or '').strip()
    for kw, label in _ORG_TYPE_HINTS:
        if kw in s:
            return label
    return s[:40] if s else fallback


def extract_member_payload(row: dict, fields: list, default_org_type: str) -> dict:
    """按列名语义从解码后的软表行提取机构成员标准字段（不依赖固定 code）。"""
    out = {
        'org_type': default_org_type,
        'name': '',
        'position': '',
        'id_card': '',
        'phone': '',
        'politics_status': '',
    }
    for f in fields:
        dn = re.sub(r'\s+', '', str(f.get('display_name') or ''))
        val = str(row.get(f['code']) or '').strip()
        if not val:
            continue
        if '姓名' in dn and not out['name']:
            out['name'] = val[:50]
        elif any(k in dn for k in ('职务', '职位', '岗位')) and not out['position']:
            out['position'] = val[:50]
        elif any(k in dn for k in ('身份证', '证件号')) and not out['id_card']:
            out['id_card'] = val[:18]
        elif any(k in dn for k in ('电话', '手机', '联系方式')) and not out['phone']:
            out['phone'] = val[:120]
        elif '政治面貌' in dn and not out['politics_status']:
            out['politics_status'] = val[:20]
        elif any(k in dn for k in ('机构', '组织', '委员会')) and '成员' not in dn:
            out['org_type'] = val[:50]
    return out


def sync_org_table_to_members(table) -> dict:
    """将单张 module_key='organization' 的软表静默单向同步到 OrganizationMember。

    返回 {'inserted': int, 'updated': int, 'skipped': int}。
    """
    if not table or table.module_key != 'organization':
        return {'inserted': 0, 'updated': 0, 'skipped': 0}

    from models import DynamicField, DynamicRecord, OrganizationMember, db
    from utils import softtable_engine as eng
    from utils.crypto import key_holder, sha256_hash
    from utils.sensitive_fields import make_sensitive_helpers

    fields_rows = (DynamicField.query.filter_by(table_id=table.id)
                   .order_by(DynamicField.order_index.asc(), DynamicField.id.asc()).all())
    specs = [{
        'code': f.code,
        'display_name': f.display_name,
        'data_type': f.data_type,
        'sensitivity': f.sensitivity,
        'dict_category_code': f.dict_category_code,
    } for f in fields_rows]
    records = DynamicRecord.query.filter_by(table_id=table.id).order_by(DynamicRecord.id.asc()).all()
    if not specs or not records:
        return {'inserted': 0, 'updated': 0, 'skipped': 0}

    decoded = eng.decode_records(specs, records, reveal=True)
    sens_cfg = {
        'id_card': ('id_card_enc', 'id_card_hash', None),
        'phone': ('phone_enc', 'phone_hash', None),
    }
    _, _set_enc, _ = make_sensitive_helpers(OrganizationMember, sens_cfg, key_holder)
    default_org = infer_org_type(table.name)

    inserted = updated = skipped = 0
    for row in decoded:
        data = extract_member_payload(row, specs, default_org)
        if not data['name']:
            skipped += 1
            continue

        existing = None
        if data['id_card']:
            h = sha256_hash(data['id_card'].upper())
            existing = OrganizationMember.query.filter_by(
                id_card_hash=h, org_type=data['org_type']
            ).first()
            if not existing:
                existing = OrganizationMember.query.filter_by(id_card_hash=h).first()
        if not existing:
            existing = OrganizationMember.query.filter_by(
                org_type=data['org_type'], name=data['name']
            ).first()

        if existing:
            if data['position']:
                existing.position = data['position']
            if data['politics_status']:
                existing.politics_status = data['politics_status']
            if data['id_card']:
                _set_enc(existing, 'id_card', data['id_card'])
            if data['phone']:
                _set_enc(existing, 'phone', data['phone'])
            updated += 1
        else:
            rec = OrganizationMember(
                org_type=data['org_type'],
                name=data['name'],
                position=data['position'],
                politics_status=data['politics_status'],
                is_current=True,
            )
            if data['id_card']:
                _set_enc(rec, 'id_card', data['id_card'])
            if data['phone']:
                _set_enc(rec, 'phone', data['phone'])
            db.session.add(rec)
            inserted += 1

    db.session.commit()
    return {'inserted': inserted, 'updated': updated, 'skipped': skipped}
