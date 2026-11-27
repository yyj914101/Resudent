# -*- coding: utf-8 -*-
"""utils/roster_facade.py — 软表挂载门面（099 / ADR-027 软表挂载门面）

「固定菜单组 → module_key → dynamic_table.code」的挂载语义收口在此：
业务蓝图（shop / pension / …）只写「取参数 → 调这里 → 包 JSON / 渲染」的薄路由，
加密/指纹、字段推断、筛选取值**全部复用 utils/softtable_engine.py 既有接口**，
本层零平行实现；权限走业务模块位（BLUEPRINT_MODULES），不在 dynatable 蓝图借道。

关键语义：
- get_table 强制 module_key 归属校验：跨模块 code 一律按不存在处理（调用方转 404）；
- m031 预置的「只有元数据行、还没有字段」的表按**未建表**处理（is_built=False → 引导页），
  建表时复用该预置行补字段，不另起新行（迁移是元数据行唯一预置点，门面不插重行）；
- 「已建」判定 = 存在 dynamic_field 行；重复建已建 code → SoftTableError（含「已存在」）。
"""
from __future__ import annotations

import json

from models import DynamicField, DynamicRecord, DynamicTable, db
from utils import softtable_engine as eng
from utils.crypto import key_holder
from utils.dynatable_modules import (
    MODULE_KEYS,
    TAB_GROUP_CONFIG,
    is_builtin_roster_code,
    tab_group_for,
)

# 门面可挂载的模块键：135 起直接引用 dynatable_modules.MODULE_KEYS 单一真源——
# 本文件原为 099 期硬编码元组（7 键），134 给 dynatable_modules 补 organization/civil
# 后此处未同步，门面建表校验「未知菜单归属」把两个新挂载键挡死（GUI 实机播种抓到）。

# 列表上限（与 dynatable api_records 同口径）：超出即截断并置 truncated 标记
MAX_LIST_ROWS = 20000


# -------------------------------------------------------------- 密钥/指纹（dynatable L102-113 同源，不 import routes）

def _hmac_key():
    """KeyHolder 密钥经 HKDF 派生 HMAC 子密钥；未解锁 → None。"""
    if not key_holder.is_initialized:
        return None
    return eng.derive_hmac_key(key_holder.get_key_bytes())


def _digest_fn():
    key = _hmac_key()
    if not key:
        return None
    return lambda code, value: eng.identity_digest(key, code, value)


# -------------------------------------------------------------- 取表/字段规格

def get_table(module_key, code):
    """按 code 取表；不存在或 module_key 不匹配（跨模块）→ None，调用方转 404。"""
    table = DynamicTable.query.filter_by(code=(code or '').strip()).first()
    if table is None or table.module_key != module_key:
        return None
    return table


def list_dynamic_tabs(tab_group):
    """返回指定页内胶囊组应自动追加的动态软表项（单请求级缓存，零 N+1）。

    仅返回：module_key 匹配 + 非内置预置 code + 已建成（存在 DynamicField 行）的软表，
    按 id 升序排列确保胶囊位置稳定。返回 dict：
      {'items': [...], 'row_label': str|None, 'overview': dict|None}
    """
    cfg = TAB_GROUP_CONFIG.get((tab_group or '').strip())
    if not cfg:
        return {'items': [], 'row_label': None, 'overview': None}

    cache = None
    try:
        from flask import g, has_request_context
        if has_request_context():
            cache = getattr(g, '_dynamic_tabs_cache', None)
            if cache is None:
                cache = {}
                g._dynamic_tabs_cache = cache
            if tab_group in cache:
                return cache[tab_group]
    except Exception:
        cache = None

    tables = (DynamicTable.query
              .filter_by(module_key=cfg['module_key'])
              .order_by(DynamicTable.id.asc()).all())
    candidates = [t for t in tables if t.code not in cfg['exclude_codes']]
    items = []
    if candidates:
        cand_ids = [t.id for t in candidates]
        built_ids = {
            row[0] for row in db.session.query(DynamicField.table_id)
            .filter(DynamicField.table_id.in_(cand_ids))
            .distinct().all()
        }
        group_counts = {}
        for t in candidates:
            if t.id in built_ids and (t.group_name or '').strip():
                gname = t.group_name.strip()
                group_counts[gname] = group_counts.get(gname, 0) + 1
        seen_groups = set()
        for t in candidates:
            if t.id not in built_ids:
                continue
            gname = (t.group_name or '').strip()
            if gname and group_counts.get(gname, 0) > 1:
                if gname in seen_groups:
                    continue
                seen_groups.add(gname)
                label = gname
                title = f'{gname}（含 {group_counts[gname]} 张子表）'
            else:
                label = t.name
                title = t.description or t.name
            items.append({
                'key': 'dt:' + t.code,
                'label': label,
                'title': title,
                'endpoint': cfg['endpoint'],
                'kwargs': {'code': t.code},
            })

    result = {
        'items': items,
        'row_label': cfg.get('row_label'),
        'overview': cfg.get('overview'),
    }
    if cache is not None:
        cache[tab_group] = result
    return result


def is_dynamic_tab_table(module_key, code):
    """判断 (module_key, code) 是否应以页内胶囊形态展示（即属于已映射模块的非内置软表）。"""
    return bool(tab_group_for(module_key) and not is_builtin_roster_code(module_key, code))


def field_specs(table):
    """ORM 字段行 → 引擎可消费的纯 dict 规格（dynatable._field_specs 同款形态）。"""
    rows = (DynamicField.query.filter_by(table_id=table.id)
            .order_by(DynamicField.sort_order.asc(), DynamicField.id.asc()).all())
    return [{'id': f.id, 'code': f.code, 'display_name': f.display_name,
             'data_type': f.data_type, 'dict_category_code': f.dict_category_code,
             'sensitivity': f.sensitivity, 'is_required': bool(f.is_required),
             'sort_order': f.sort_order} for f in rows]


def _enum_options(category):
    """枚举列可选值 [{value, label}]，唯一来源 dict_items（get_dict_items，请求级缓存）。"""
    from utils.helpers import get_dict_items
    return [{'value': i.value, 'label': i.label} for i in get_dict_items(category)]


def fields_with_options(table):
    """页面字段规格 + 枚举列 options（运行时读 dict_items，不落取值副本）。"""
    specs = []
    for spec in field_specs(table):
        view = dict(spec)
        view['options'] = (_enum_options(spec['dict_category_code'])
                           if spec['data_type'] == 'enum' and spec['dict_category_code'] else [])
        specs.append(view)
    return specs


def is_built(table):
    """「已建」判定：存在字段行才算建成（m031 空预置行 → 未建，走引导页）。"""
    return db.session.query(DynamicField.id).filter_by(table_id=table.id).first() is not None


def _rows_of(table):
    return (DynamicRecord.query.filter_by(table_id=table.id)
            .order_by(DynamicRecord.id.asc()).all())


def _load_json(blob):
    try:
        return json.loads(blob or '{}')
    except ValueError:
        return {}


# -------------------------------------------------------------- 二次追加导入列匹配与投影

def match_append_headers(specs, headers):
    """按列名自动匹配已有软表字段（精确 display_name → 精确 code）。

    返回 (field_matches, unmatched_headers, missing_required)：
    - field_matches: [{code, display_name, data_type, is_required, sensitivity, matched_index}]
    - unmatched_headers: 未命中任何字段的 Excel 表头列表
    - missing_required: 未自动匹配的必填字段 display_name 列表
    """
    norm_headers = [(i, (h or '').strip()) for i, h in enumerate(headers or [])]
    used_indices = set()
    field_matches = []
    missing_required = []

    for spec in specs:
        target_name = (spec.get('display_name') or '').strip()
        target_code = (spec.get('code') or '').strip()
        matched_idx = None
        for idx, h in norm_headers:
            if idx in used_indices:
                continue
            if h == target_name:
                matched_idx = idx
                break
        if matched_idx is None:
            for idx, h in norm_headers:
                if idx in used_indices:
                    continue
                if h == target_code:
                    matched_idx = idx
                    break
        if matched_idx is not None:
            used_indices.add(matched_idx)
        elif spec.get('is_required'):
            missing_required.append(target_name or target_code)
        field_matches.append({
            'code': target_code,
            'display_name': target_name,
            'data_type': spec.get('data_type', 'text'),
            'is_required': bool(spec.get('is_required')),
            'sensitivity': spec.get('sensitivity', 'none'),
            'matched_index': matched_idx,
        })

    unmatched_headers = [h for idx, h in norm_headers if idx not in used_indices and h]
    return field_matches, unmatched_headers, missing_required


def normalize_column_map(specs, headers, raw_map):
    """校验并规范化前端提交的列映射 {field_code: header_index | None}。"""
    header_len = len(headers or [])
    if not isinstance(raw_map, dict):
        # 未传 column_map 时回退自动匹配
        matches, _unmatched, _missing = match_append_headers(specs, headers)
        raw_map = {m['code']: m['matched_index'] for m in matches}

    clean = {}
    mapped_count = 0
    for spec in specs:
        code = spec['code']
        val = raw_map.get(code)
        if val in (None, '', -1, '-1'):
            if spec.get('is_required'):
                raise eng.SoftTableError('必填字段「%s」未指定对应的 Excel 列' % spec['display_name'])
            clean[code] = None
            continue
        try:
            idx = int(val)
        except (TypeError, ValueError):
            raise eng.SoftTableError('字段「%s」的列序号无效' % spec['display_name'])
        if idx < 0 or idx >= header_len:
            raise eng.SoftTableError('字段「%s」的列序号超出文件列数范围' % spec['display_name'])
        clean[code] = idx
        mapped_count += 1

    if mapped_count == 0:
        raise eng.SoftTableError('请至少映射一个导入列')
    return clean


def project_rows_by_map(specs, headers, rows, column_map):
    """按 column_map 将原始 Excel 行重排投影为与 specs 顺序一一对应的行矩阵。"""
    clean_map = normalize_column_map(specs, headers, column_map)
    projected = []
    for row in rows:
        row_len = len(row)
        out = []
        for spec in specs:
            idx = clean_map.get(spec['code'])
            if idx is None or idx >= row_len:
                out.append(None)
            else:
                out.append(row[idx])
        projected.append(out)
    return projected


# -------------------------------------------------------------- 列表/保存

def list_rows(table, page=1, limit=20, sort=None, order='asc', filters=None):
    """列表口径同 dynatable api_records：截断 → 解码（identity 打码）→ 筛选排序 → 分页剥指纹。

    返回 {'data': [...], 'total': N, 'truncated': bool}。
    """
    specs = field_specs(table)
    records = _rows_of(table)
    truncated = len(records) > MAX_LIST_ROWS
    if truncated:
        records = records[:MAX_LIST_ROWS]
    decoded = eng.decode_records(specs, records, reveal=False)
    rows = eng.select_rows(decoded, specs, filters or {}, sort, order or 'asc',
                           digest_fn=_digest_fn())
    page = max(int(page or 1), 1)
    limit = min(max(int(limit or 20), 1), 200)
    start = (page - 1) * limit
    # 指纹只在服务端参与筛选，不回传前端
    data = [{k: v for k, v in r.items() if k != '__digest__'}
            for r in rows[start:start + limit]]
    return {'data': data, 'total': len(rows), 'truncated': truncated}


def save_row(table, rid, values, created_by=None):
    """整行新增（rid=None）/ 更新；逐字段 coerce_value、identity 加密+指纹全在引擎口径内。

    values: {字段code: 原始值}；更新时未提交的字段保持原值，提交 None/'' 即清空该格。
    identity 掩码回填防护：编辑表单回填的是打码展示值，用户未改动时提交值 == 掩码，
    此时该字段跳过写入保持原密文（不得以掩码覆盖密文）。
    返回保存后的整行展示值（identity 经 decode_records 按 mask_identity 打码）。
    SoftTableError 直接上抛（类型/枚举取值/必填/密钥未解锁），由路由转错误响应。
    """
    specs = field_specs(table)
    values = values if isinstance(values, dict) else {}
    record = None
    if rid is not None:
        record = DynamicRecord.query.filter_by(id=rid, table_id=table.id).first()
        if record is None:
            raise eng.SoftTableError('记录不存在')
        data = _load_json(record.data)
        digest = _load_json(record.digest)
        ident_codes = {s['code'] for s in specs if s['sensitivity'] == 'identity'}
        if ident_codes:
            current = eng.decode_records(specs, [record], reveal=False)[0]
            values = {k: v for k, v in values.items()
                      if not (k in ident_codes and v == current.get(k, ''))}
    else:
        data, digest = {}, {}

    # identity 写入前置拦截（文案与 dynatable api_update_record 同源）
    writing_identity = any(spec['sensitivity'] == 'identity'
                           and values.get(spec['code']) not in (None, '')
                           for spec in specs)
    hmac_key = None
    if writing_identity:
        hmac_key = _hmac_key()
        if not hmac_key or not key_holder.is_initialized:
            raise eng.SoftTableError('密钥未解锁，无法写入身份标识字段；请重新登录')

    for spec in specs:
        code = spec['code']
        if code not in values:
            continue  # 未提交的字段：更新时保持原值，新增时留空
        value = eng.coerce_value(spec, values.get(code))
        if value is None:
            data.pop(code, None)
            digest.pop(code, None)
        elif spec['sensitivity'] == 'identity':
            data[code] = key_holder.encrypt(str(value))
            digest[code] = eng.identity_digest(hmac_key, code, str(value))
        else:
            data[code] = value
            digest.pop(code, None)

    # 必填终检以保存后的整行为准（整行口径；dynatable 单元格口径逐格拦）
    for spec in specs:
        if spec['is_required'] and data.get(spec['code']) in (None, ''):
            raise eng.SoftTableError('「%s」是必填项' % spec['display_name'])

    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    digest_blob = json.dumps(digest, ensure_ascii=False, sort_keys=True) if digest else None
    if record is None:
        record = DynamicRecord(table_id=table.id, data=payload, digest=digest_blob,
                               created_by=created_by)
        db.session.add(record)
    else:
        record.data = payload
        record.digest = digest_blob
    db.session.commit()
    decoded = eng.decode_records(specs, [record], reveal=False)
    return {k: v for k, v in decoded[0].items() if k != '__digest__'}


# -------------------------------------------------------------- 建表/导入

def create_table(module_key, code, name, group_name=None, fields=None,
                 created_by=None, description=None):
    """建表：validate_table_code + normalize_fields + module_key 白名单 + 查重。

    - 同模块 code 已建（有字段行）→ SoftTableError（含「已存在」，路由转 409）；
    - code 被其他模块占用 → SoftTableError（跨模块隔离，不静默复用）；
    - m031 预置的空元数据行视为挂载点：复用该行补字段，不另起新行；
    - 只落 DynamicTable + DynamicField（permission_key 沿用引擎默认 'dynatable'），
      **不导数据**——数据由调用方经 import_records / build_records 写入。
    """
    code = eng.validate_table_code(code)
    fields = eng.normalize_fields(fields or [])
    if module_key not in MODULE_KEYS:
        raise eng.SoftTableError('未知菜单归属：%s' % module_key)
    name = (name or '').strip()
    if not name:
        raise eng.SoftTableError('请填写台账名称')

    existing = DynamicTable.query.filter_by(code=code).first()
    if existing is not None:
        if existing.module_key != module_key:
            raise eng.SoftTableError('表标识 %s 已存在（属其他模块），请换一个' % code)
        if is_built(existing):
            raise eng.SoftTableError('表标识 %s 已存在，请勿重复建表' % code)
        # 预置空行就地补全（name/group/description 以确认页提交为准）
        existing.name = name
        existing.group_name = group_name
        if description is not None:
            existing.description = description
        table = existing
    else:
        table = DynamicTable(code=code, name=name, module_key=module_key,
                             group_name=group_name, description=description,
                             permission_key='dynatable', created_by=created_by)
        db.session.add(table)
    db.session.flush()  # 先拿 table_id，字段行随后的 commit 一并落库
    for spec in fields:
        db.session.add(DynamicField(
            table_id=table.id, code=spec['code'], display_name=spec['display_name'],
            data_type=spec['data_type'], dict_category_code=spec['dict_category_code'],
            sensitivity=spec['sensitivity'], is_required=spec['is_required'],
            sort_order=spec['sort_order']))
    db.session.commit()
    return table


def import_records(table, fields, rows, skip_error_rows=False, created_by=None):
    """导入辅助：eng.build_records（校验/加密/指纹全在引擎）→ 插 DynamicRecord。

    返回 (imported, errors)；有错误行且未勾选跳过时**一行都不插**
    （调用方先 build_records 预检再建表，避免留下半截已建空台账）。
    """
    records, errors = eng.build_records(fields, rows)
    if errors and not skip_error_rows:
        return 0, errors
    for rec in records:
        db.session.add(DynamicRecord(table_id=table.id, data=rec['data'],
                                     digest=rec['digest'], created_by=created_by))
    db.session.commit()
    return len(records), errors


# -------------------------------------------------------------- 导出

def export_rows(table, can_view_sensitive, filters=None, sort=None, order='asc'):
    """导出口径同 096 api_export_records：decode_records(reveal=can_view_sensitive)
    → select_rows → 枚举列 value→label 映射。

    返回 (headers, rows_matrix)；identity 列已在 decode_records 按 can_view_sensitive
    打码/解密，调用方 export_excel 应传 already_masked=True 防二次脱敏（R-096b-01）。
    """
    specs = field_specs(table)
    decoded = eng.decode_records(specs, _rows_of(table), reveal=bool(can_view_sensitive))
    rows = eng.select_rows(decoded, specs, filters or {}, sort, order or 'asc',
                           digest_fn=_digest_fn())
    enum_labels = {}
    for spec in specs:
        if spec['data_type'] == 'enum' and spec['dict_category_code']:
            enum_labels[spec['code']] = {opt['value']: opt['label']
                                         for opt in _enum_options(spec['dict_category_code'])}
    headers = [spec['display_name'] for spec in specs]
    matrix = []
    for row in rows:
        matrix.append([enum_labels.get(spec['code'], {}).get(row.get(spec['code'], ''),
                                                            row.get(spec['code'], ''))
                       for spec in specs])
    return headers, matrix
