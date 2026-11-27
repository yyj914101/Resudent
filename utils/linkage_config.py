# -*- coding: utf-8 -*-
"""utils/linkage_config.py — 「设置中心 → 数据联动与补贴口径」可视化配置存储与合并引擎（零 DB 迁移）。

持久化：复用「系统设置」字典分类（DictCategory/DictItem），存两个 JSON 键：
- linkage_policy_subsidies_json：政策性补贴聚合口径覆盖 + 软表补贴挂载列表
- linkage_resident_sync_json：居民主档修改跨模块同步字段映射覆盖

运行时按「可视化配置 > 代码出厂默认（POLICY_SUBSIDY_REGISTRY / FIELD_MAP）」合并；
支持一键恢复出厂默认，保存前强校验所选列名在目标模型/软表中真实存在。
"""
from __future__ import annotations

import copy
import json

from sqlalchemy import inspect as sa_inspect

from models import DictCategory, DictItem, DynamicField, DynamicTable, db
from utils.settings_store import get_settings_value

KEY_POLICY_CONFIG = 'linkage_policy_subsidies_json'
KEY_SYNC_CONFIG = 'linkage_resident_sync_json'

UNIT_OPTIONS = ('元/月', '元/户', '元/季', '元/年', '元/次')

MODULE_CN_NAMES = {
    'dibao': '低保人员',
    'disabled': '残疾人',
    'youshu': '优抚对象',
    'special_support': '特困供养',
    'unsupported_child': '事实无人抚养儿童',
    'unsupported_children': '事实无人抚养儿童',
    'welfare_job': '公益性岗位',
    'care_targets': '关爱对象',
    'medical_insurance_payments': '医保参保',
    'death_reports': '死亡上报',
    'veteran': '退役军人',
    'party_member': '党员管理',
    'organization_member': '机构成员',
    'welfare_employment': '就业服务',
    'election_voter': '选民名册',
}

SYNC_FIELD_LABELS = {
    'name': '姓名',
    'phone': '联系电话',
    'bank_card': '银行卡号',
    'birth_date': '出生日期',
    'gender': '性别',
    'nation': '民族',
    'address': '家庭住址',
    'village_group': '所在组',
}


def _set_setting(key: str, value: str):
    cat = DictCategory.query.filter_by(name='系统设置').first()
    if not cat:
        cat = DictCategory(name='系统设置', description='系统运行配置项', sort_order=99)
        db.session.add(cat)
        db.session.flush()
    item = DictItem.query.filter_by(category_id=cat.id, label=key).first()
    if item:
        item.value = value
        item.is_active = True
    else:
        db.session.add(DictItem(category_id=cat.id, label=key, value=value, is_active=True, sort_order=99))
    db.session.commit()


def _delete_setting(key: str):
    cat = DictCategory.query.filter_by(name='系统设置').first()
    if not cat:
        return
    item = DictItem.query.filter_by(category_id=cat.id, label=key).first()
    if item:
        db.session.delete(item)
        db.session.commit()


def _load_json_setting(key: str):
    raw = get_settings_value(key, '')
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _model_columns_meta(model_name: str):
    """通过 SQLAlchemy 元数据读取模型真实列（排除内部 _enc/_hash 密文列）。"""
    import models as _models
    cls = getattr(_models, model_name, None)
    if cls is None:
        return []
    out = []
    mapper = sa_inspect(cls)
    for col in mapper.columns:
        cname = col.key
        if cname in ('id', 'created_at', 'updated_at', 'created_by') or cname.endswith('_enc') or cname.endswith('_hash'):
            continue
        tname = str(col.type).upper()
        is_num = any(k in tname for k in ('FLOAT', 'INT', 'NUMERIC', 'DECIMAL', 'REAL'))
        out.append({'name': cname, 'type': 'number' if is_num else 'text'})
    return out


def _softtable_catalog():
    """列出已建成软表及其字段（供可视化挂载补贴软表）。"""
    tables = DynamicTable.query.order_by(DynamicTable.id.desc()).all()
    out = []
    for t in tables:
        flds = (DynamicField.query.filter_by(table_id=t.id)
                .order_by(DynamicField.sort_order.asc(), DynamicField.id.asc()).all())
        if not flds:
            continue
        out.append({
            'code': t.code,
            'name': t.name,
            'module_key': t.module_key,
            'fields': [{
                'code': f.code,
                'display_name': f.display_name,
                'data_type': f.data_type,
                'sensitivity': f.sensitivity,
            } for f in flds],
        })
    return out


def get_effective_policy_registry():
    """返回合并可视化覆盖后的 (fixed_registry, softtable_subsidies)。"""
    from utils.subsidy_service import POLICY_SUBSIDY_REGISTRY
    base = copy.deepcopy(POLICY_SUBSIDY_REGISTRY)
    saved = _load_json_setting(KEY_POLICY_CONFIG) or {}
    overrides = saved.get('fixed_overrides') or {}
    softtables = saved.get('softtable_subsidies') or []

    effective = []
    for spec in base:
        mod = spec['module']
        items_out = []
        for idx, it in enumerate(spec['items']):
            item_key = f'{mod}:{idx}'
            ov = overrides.get(item_key) or {}
            merged = dict(it)
            merged['item_key'] = item_key
            merged['enabled'] = bool(ov.get('enabled', True))
            if ov.get('name'):
                merged['name'] = str(ov['name']).strip()[:60]
            if ov.get('amount_field'):
                merged['amount_field'] = str(ov['amount_field']).strip()
            if ov.get('unit') in UNIT_OPTIONS:
                merged['unit'] = ov['unit']
            if 'period_field' in ov:
                merged['period_field'] = (ov.get('period_field') or '').strip() or None
            items_out.append(merged)
        spec_copy = dict(spec)
        spec_copy['items'] = items_out
        effective.append(spec_copy)
    return effective, softtables


def get_effective_field_map():
    """返回合并可视化覆盖后的主档同步 FIELD_MAP。"""
    from utils.resident_sync import FIELD_MAP, SYNC_FIELDS
    base = copy.deepcopy(FIELD_MAP)
    saved = _load_json_setting(KEY_SYNC_CONFIG) or {}
    overrides = saved.get('tables') or {}

    effective = []
    for spec in base:
        tname = spec['table']
        ov = overrides.get(tname) or {}
        enabled = bool(ov.get('enabled', True))
        if not enabled:
            continue
        fields_ov = ov.get('fields')
        if isinstance(fields_ov, dict):
            new_fields = {}
            for src_f, dst in fields_ov.items():
                if src_f in SYNC_FIELDS and dst:
                    new_fields[src_f] = dst
            spec['fields'] = new_fields
        effective.append(spec)
    return effective


def build_config_view_data():
    """组装设置中心可视化配置页所需的完整数据与元信息。"""
    from utils.resident_sync import FIELD_MAP, SYNC_FIELDS
    eff_policy, softtable_subsidies = get_effective_policy_registry()
    saved_sync = _load_json_setting(KEY_SYNC_CONFIG) or {}
    sync_ov = saved_sync.get('tables') or {}

    policy_rows = []
    model_cols = {}
    for spec in eff_policy:
        mname = spec['model']
        if mname not in model_cols:
            model_cols[mname] = _model_columns_meta(mname)
        for it in spec['items']:
            policy_rows.append({
                'item_key': it['item_key'],
                'module': spec['module'],
                'module_label': MODULE_CN_NAMES.get(spec['module'], spec['module']),
                'model': mname,
                'enabled': it.get('enabled', True),
                'name': it['name'],
                'amount_field': it['amount_field'],
                'period_field': it.get('period_field') or '',
                'unit': it.get('unit', '元/月'),
            })

    sync_rows = []
    for spec in FIELD_MAP:
        tname = spec['table']
        mname = spec['model']
        if mname not in model_cols:
            model_cols[mname] = _model_columns_meta(mname)
        ov = sync_ov.get(tname) or {}
        cur_fields = ov.get('fields') if isinstance(ov.get('fields'), dict) else spec['fields']
        sync_rows.append({
            'table': tname,
            'table_label': MODULE_CN_NAMES.get(tname, tname),
            'model': mname,
            'locate': spec['locate'],
            'enabled': bool(ov.get('enabled', True)),
            'fields': cur_fields,
            'default_fields': spec['fields'],
        })

    return {
        'policy_items': policy_rows,
        'softtable_subsidies': softtable_subsidies,
        'sync_tables': sync_rows,
        'sync_field_labels': SYNC_FIELD_LABELS,
        'sync_fields': list(SYNC_FIELDS),
        'model_columns': model_cols,
        'softtables': _softtable_catalog(),
        'unit_options': list(UNIT_OPTIONS),
        'has_custom_policy': _load_json_setting(KEY_POLICY_CONFIG) is not None,
        'has_custom_sync': _load_json_setting(KEY_SYNC_CONFIG) is not None,
    }


def save_policy_config(payload: dict):
    """校验并保存政策性补贴口径配置（固定表覆盖 + 软表挂载）。"""
    from utils.subsidy_service import POLICY_SUBSIDY_REGISTRY
    valid_keys = {}
    for spec in POLICY_SUBSIDY_REGISTRY:
        cols = {c['name'] for c in _model_columns_meta(spec['model'])}
        for idx, _it in enumerate(spec['items']):
            valid_keys[f"{spec['module']}:{idx}"] = cols

    fixed_overrides = {}
    for row in (payload.get('policy_items') or []):
        k = (row.get('item_key') or '').strip()
        if k not in valid_keys:
            continue
        amt_col = (row.get('amount_field') or '').strip()
        if amt_col and amt_col not in valid_keys[k]:
            raise ValueError(f'金额列 {amt_col} 不存在于目标表')
        per_col = (row.get('period_field') or '').strip()
        if per_col and per_col not in valid_keys[k]:
            raise ValueError(f'周期列 {per_col} 不存在于目标表')
        unit = (row.get('unit') or '元/月').strip()
        if unit not in UNIT_OPTIONS:
            unit = '元/月'
        fixed_overrides[k] = {
            'enabled': bool(row.get('enabled', True)),
            'name': (row.get('name') or '').strip()[:60],
            'amount_field': amt_col,
            'period_field': per_col,
            'unit': unit,
        }

    st_list = []
    for st in (payload.get('softtable_subsidies') or []):
        tcode = (st.get('table_code') or '').strip()
        id_col = (st.get('identity_field') or '').strip()
        amt_col = (st.get('amount_field') or '').strip()
        if not tcode or not id_col or not amt_col:
            continue
        tbl = DynamicTable.query.filter_by(code=tcode).first()
        if tbl is None:
            raise ValueError(f'软表 {tcode} 不存在')
        fset = {f.code for f in DynamicField.query.filter_by(table_id=tbl.id).all()}
        if id_col not in fset or amt_col not in fset:
            raise ValueError(f'软表 {tcode} 指定的字段不存在')
        unit = (st.get('unit') or '元/月').strip()
        if unit not in UNIT_OPTIONS:
            unit = '元/月'
        st_list.append({
            'table_code': tcode,
            'name': (st.get('name') or tbl.name).strip()[:60],
            'identity_field': id_col,
            'amount_field': amt_col,
            'category_field': (st.get('category_field') or '').strip() if (st.get('category_field') or '').strip() in fset else '',
            'unit': unit,
            'enabled': bool(st.get('enabled', True)),
        })

    _set_setting(KEY_POLICY_CONFIG, json.dumps({
        'fixed_overrides': fixed_overrides,
        'softtable_subsidies': st_list,
    }, ensure_ascii=False))


def save_sync_config(payload: dict):
    """校验并保存居民主档联动字段映射配置。"""
    from utils.resident_sync import FIELD_MAP, SYNC_FIELDS
    spec_by_table = {s['table']: s for s in FIELD_MAP}
    tables_out = {}
    for row in (payload.get('sync_tables') or []):
        tname = (row.get('table') or '').strip()
        spec = spec_by_table.get(tname)
        if not spec:
            continue
        valid_cols = {c['name'] for c in _model_columns_meta(spec['model'])}
        # 允许模型只有 _enc/_hash 而无明文列的字段（如 ElectionVoter.phone）
        import models as _models
        cls = getattr(_models, spec['model'], None)
        fields_in = row.get('fields') or {}
        clean_fields = {}
        for src_f, dst in fields_in.items():
            if src_f not in SYNC_FIELDS or not dst:
                continue
            dst_list = dst if isinstance(dst, list) else [dst]
            for dcol in dst_list:
                if dcol not in valid_cols and not (cls and hasattr(cls, dcol + '_enc')):
                    raise ValueError(f'{tname} 不存在目标列 {dcol}')
            clean_fields[src_f] = dst
        tables_out[tname] = {
            'enabled': bool(row.get('enabled', True)),
            'fields': clean_fields,
        }
    _set_setting(KEY_SYNC_CONFIG, json.dumps({'tables': tables_out}, ensure_ascii=False))


def reset_linkage_config(scope: str = 'all'):
    """恢复出厂默认配置。"""
    if scope in ('policy', 'all'):
        _delete_setting(KEY_POLICY_CONFIG)
    if scope in ('sync', 'all'):
        _delete_setting(KEY_SYNC_CONFIG)
