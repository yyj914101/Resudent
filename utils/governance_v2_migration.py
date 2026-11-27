# -*- coding: utf-8 -*-
"""utils.governance_v2_migration — 第一代 GovernanceLedger → 软表引擎 V2 无损幂等迁移。

Patch C-2：
1. 为 8 类综治台账幂等初始化固定 code 的软表（DynamicTable + DynamicField），
   归属 module_key='governance'，纳入内置表白名单防误删、防胶囊重复。
2. 若 SQLite 中仍存在历史 `governance_ledgers` 物理表，则逐行无损迁入对应软表
   （含 id_card_enc 解密后重新按软表 identity 字段加密与写 HMAC 指纹），
   通过 data JSON 内 `_legacy_gov_id` 标记确保重复执行 100% 幂等，不执行 DROP TABLE。

无损保证（141 套打补缺陷 #21）：
- **密钥未就绪不迁**：KeyHolder 进程启动期为锁定态，此时旧密文解不开、原列又是 NULL，
  旧逻辑会回退成空串并把该行标记为「已迁」——身份证永久丢失。现改为：待迁行中只要
  有一行含身份材料（id_card / id_card_enc / payload.id_card），整批推迟（不落任何行、
  不打标记），待解锁后的懒触发点重试；全程绝不把身份标识明文写入软表。
- **解密失败不迁该行**：密钥就绪但解不开（换过口令/密文损坏）→ 跳过并计数，旧表不动，
  下次执行再试；存在失败行时不落「完成标记」。
- **空值不落键**：遵循软表引擎约定（缺键 = 空），避免空身份证被引擎显示成 `************`。
- **完成标记落库**：`data_model_version(model_name='governance_ledger_v2_migration')`，
  随数据库备份走；落标记后不再扫描旧表——用户删除某条迁移记录后重启不会「复活」。
- 建表与迁数据分属两个事务：旧表结构异常只影响迁数据，不连累 8 张表初始化。
"""
import json
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from models import DynamicField, DynamicRecord, DynamicTable, db
from utils.governance_catalog import CATEGORY_CONFIG, GOV_CATEGORY_CODES

_logger = logging.getLogger('shucun')

# 「历史数据已全部迁入」的库内标记（DataModelVersion 唯一键 = model_name + version）
_MARKER_MODEL = 'governance_ledger_v2_migration'
_MARKER_VERSION = 1

_LEGACY_SELECT = (
    'SELECT id, category, subtype, name, id_card, id_card_enc, phone, '
    'record_date, payload, created_by, created_at, updated_at '
    'FROM governance_ledgers ORDER BY id ASC'
)


def _field_data_type(field_cfg):
    kind = field_cfg.get('kind') or 'text'
    if kind == 'date':
        return 'date'
    if kind == 'select' and field_cfg.get('option_category'):
        return 'enum'
    return 'text'


def _field_sensitivity(field_cfg):
    return 'identity' if field_cfg.get('key') == 'id_card' else 'none'


def _identity_keys(category):
    return {f['key'] for f in (CATEGORY_CONFIG.get(category, {}).get('fields') or [])
            if _field_sensitivity(f) == 'identity'}


def _ensure_single_table(category, cfg):
    """确保单个综治分类的 V2 软表及列定义已存在；返回 (table, created_bool)。"""
    code = GOV_CATEGORY_CODES[category]
    table = DynamicTable.query.filter_by(code=code).first()
    created = False
    if table is None:
        table = DynamicTable(
            code=code,
            name=cfg.get('title') or category,
            module_key='governance',
            group_name='综治管理',
            description=cfg.get('description') or '',
            permission_key='dynatable',
            created_by='system',
        )
        db.session.add(table)
        db.session.flush()
        created = True

    existing_fields = {
        f.code for f in DynamicField.query.filter_by(table_id=table.id).all()
    }
    for idx, fcfg in enumerate(cfg.get('fields') or []):
        fcode = fcfg['key']
        if fcode in existing_fields:
            continue
        db.session.add(DynamicField(
            table_id=table.id,
            code=fcode,
            display_name=fcfg.get('label') or fcode,
            data_type=_field_data_type(fcfg),
            dict_category_code=fcfg.get('option_category') or None,
            sensitivity=_field_sensitivity(fcfg),
            is_required=bool(fcfg.get('required')),
            sort_order=idx,
        ))
    return table, created


def _legacy_table_exists():
    row = db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='governance_ledgers'")
    ).first()
    return bool(row)


def _is_finalized():
    """历史数据是否已全部迁入（库内标记；标记表不可用时按「未完成」处理，退化为逐行幂等）。"""
    try:
        from models.schema_governance import DataModelVersion
        return DataModelVersion.query.filter_by(
            model_name=_MARKER_MODEL, version=_MARKER_VERSION).first() is not None
    except SQLAlchemyError:
        db.session.rollback()
        return False


def _mark_finalized(change_log):
    try:
        from models.schema_governance import DataModelVersion
        if DataModelVersion.query.filter_by(
                model_name=_MARKER_MODEL, version=_MARKER_VERSION).first() is None:
            db.session.add(DataModelVersion(
                model_name=_MARKER_MODEL, version=_MARKER_VERSION, change_log=change_log))
    except SQLAlchemyError:
        # 标记只是加速与「防复活」手段，写不进去不影响数据正确性
        db.session.rollback()


def _existing_legacy_ids(table_id):
    ids = set()
    for (raw_json,) in db.session.query(DynamicRecord.data).filter_by(table_id=table_id).all():
        if not raw_json or '_legacy_gov_id' not in raw_json:
            continue
        try:
            lid = json.loads(raw_json).get('_legacy_gov_id')
            if lid is not None:
                ids.add(int(lid))
        except Exception:
            continue
    return ids


def _row_needs_key(identity_keys, id_card, id_card_enc, payload_raw):
    """该行是否含身份标识材料（需要密钥才能无损迁移）。"""
    if (id_card and str(id_card).strip()) or (id_card_enc and str(id_card_enc).strip()):
        return True
    try:
        parsed = json.loads(payload_raw or '{}')
    except Exception:
        return False
    if not isinstance(parsed, dict):
        return False
    return any(str(parsed.get(k) or '').strip() for k in identity_keys)


def _normalize_enum(field_cfg, value):
    """旧版按 get_enum() 成员校验存值；软表 V2 的 enum 单元格要字典 value → label 命中则归一为 value。"""
    cat = field_cfg.get('option_category')
    if not cat:
        return value
    try:
        from utils.dict_enum import get_enum_value
        mapped = get_enum_value(cat, value)
        return mapped or value
    except Exception:
        return value


def _parse_dt(val):
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val.strip():
        try:
            return datetime.fromisoformat(val.strip()[:19])
        except Exception:
            return datetime.now()
    return datetime.now()


def _key_ready():
    from utils.crypto import key_holder
    return bool(key_holder.is_initialized and key_holder.get_key_bytes())


def _migrate_legacy_rows(tables_by_category, stats):
    """把历史 SQLite `governance_ledgers` 表中的记录幂等迁入软表 V2（不删旧表）。

    自带事务：任何异常只回滚本批迁数据，不影响已提交的 8 张表初始化。
    """
    stats.setdefault('rows_migrated', 0)
    try:
        if not _legacy_table_exists() or _is_finalized():
            return
        rows = db.session.execute(text(_LEGACY_SELECT)).fetchall()
        pending = [r for r in rows if r.category in tables_by_category]
        if not pending:
            _mark_finalized('历史 governance_ledgers 表为空，无需迁移（%s）' % datetime.now().isoformat(timespec='seconds'))
            db.session.commit()
            return

        migrated_cache = {cat: _existing_legacy_ids(tbl.id) for cat, tbl in tables_by_category.items()}
        pending = [r for r in pending if int(r.id) not in migrated_cache[r.category]]
        if not pending:
            _mark_finalized('历史 governance_ledgers 共 %d 行均已迁入（%s）' % (
                len(rows), datetime.now().isoformat(timespec='seconds')))
            db.session.commit()
            return

        ident_keys = {cat: _identity_keys(cat) for cat in tables_by_category}
        ready = _key_ready()
        if not ready and any(_row_needs_key(ident_keys[r.category], r.id_card, r.id_card_enc, r.payload)
                             for r in pending):
            # 进程启动期密钥锁定：绝不写空值/明文，整批推迟到解锁后的懒触发点重试
            stats['deferred_locked'] = len(pending)
            _logger.info('治理台账历史数据迁移已推迟：密钥未解锁，待迁 %d 行', len(pending))
            return

        from utils.crypto import key_holder
        from utils import softtable_engine as eng
        hmac_key = eng.derive_hmac_key(key_holder.get_key_bytes()) if ready else None
        fields_by_cat = {cat: {f['key']: f for f in (CATEGORY_CONFIG[cat].get('fields') or [])}
                         for cat in tables_by_category}

        migrated = failed = 0
        for row in pending:
            (_rid, category, subtype, name, id_card, id_card_enc, phone,
             record_date, payload_raw, created_by, created_at, updated_at) = row
            cat_fields = fields_by_cat[category]

            plain_id_card = ''
            if str(id_card_enc or '').strip():
                if not ready:
                    stats['rows_failed'] = failed + 1
                    failed += 1
                    continue
                try:
                    plain_id_card = key_holder.decrypt(str(id_card_enc)) or ''
                except Exception:
                    # 密文解不开：不迁、不标记，旧表原样保留，下次再试
                    _logger.warning('治理台账历史行 #%s 身份证密文无法解密，已跳过待重试', _rid)
                    failed += 1
                    continue
            plain_id_card = plain_id_card or str(id_card or '').strip()

            payload = {}
            if payload_raw:
                try:
                    parsed = json.loads(payload_raw)
                    payload = parsed if isinstance(parsed, dict) else {}
                except Exception:
                    payload = {}

            merged = dict(payload)
            for src_key, col_val in (('name', name), ('phone', phone), ('record_date', record_date),
                                     ('subtype', subtype)):
                if col_val and not str(merged.get(src_key) or '').strip():
                    merged[src_key] = str(col_val)[:10] if src_key == 'record_date' else str(col_val)
            if plain_id_card:
                merged['id_card'] = plain_id_card

            data_dict = {'_legacy_gov_id': int(_rid)}
            digest_dict = {}
            for fkey, fcfg in cat_fields.items():
                val = merged.get(fkey)
                if val is None or str(val).strip() == '':
                    continue  # 空值不落键（引擎约定：缺键 = 空）
                text_val = str(val).strip()
                if _field_sensitivity(fcfg) == 'identity':
                    if not ready:
                        failed += 1          # 理论不可达（上方已整批推迟），双保险
                        data_dict = None
                        break
                    data_dict[fkey] = key_holder.encrypt(text_val)
                    digest_dict[fkey] = eng.identity_digest(hmac_key, fkey, text_val)
                elif fcfg.get('option_category'):
                    data_dict[fkey] = _normalize_enum(fcfg, text_val)
                else:
                    data_dict[fkey] = text_val
            if data_dict is None:
                continue

            # 保留 payload 中未列入目录的额外扩展键，确保零丢字段
            for extra_k, extra_v in merged.items():
                if extra_k not in data_dict and extra_k != 'id_card' and extra_v not in (None, ''):
                    data_dict[extra_k] = extra_v

            db.session.add(DynamicRecord(
                table_id=tables_by_category[category].id,
                data=json.dumps(data_dict, ensure_ascii=False),
                digest=json.dumps(digest_dict, ensure_ascii=False) if digest_dict else None,
                created_by=created_by or 'system',
                created_at=_parse_dt(created_at),
                updated_at=_parse_dt(updated_at),
            ))
            migrated_cache[category].add(int(_rid))
            migrated += 1

        db.session.commit()
        stats['rows_migrated'] = migrated
        stats['rows_failed'] = failed
        if not failed:
            _mark_finalized('历史 governance_ledgers 共 %d 行已全部迁入软表 V2（本次 %d 行，%s）' % (
                len(rows), migrated, datetime.now().isoformat(timespec='seconds')))
            db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        _logger.warning('治理台账历史数据迁移异常（已回滚，下次重试）: %s', exc)
        stats['error'] = str(exc)
    except Exception as exc:  # noqa: BLE001 — 迁移绝不阻断请求
        db.session.rollback()
        _logger.warning('治理台账历史数据迁移异常（已回滚，下次重试）: %s: %s', type(exc).__name__, exc)
        stats['error'] = '%s: %s' % (type(exc).__name__, exc)


def ensure_governance_v2_tables(migrate_legacy=True):
    """幂等确保 8 张综治 V2 软表存在，并按需无损迁入历史 governance_ledgers 数据。"""
    stats = {'tables_created': 0, 'rows_migrated': 0}
    try:
        tables_by_category = {}
        for category, cfg in CATEGORY_CONFIG.items():
            tbl, created = _ensure_single_table(category, cfg)
            tables_by_category[category] = tbl
            if created:
                stats['tables_created'] += 1
        db.session.commit()
    except Exception as exc:  # noqa: BLE001 — 启动期绝不阻断
        db.session.rollback()
        _logger.warning('ensure_governance_v2_tables 建表异常（已回滚，不阻断）: %s', exc)
        stats['error'] = str(exc)
        return stats

    if migrate_legacy:
        _migrate_legacy_rows(tables_by_category, stats)
    return stats


def migrate_legacy_if_pending():
    """懒触发点专用：旧表不存在或已迁完 → 立即返回（≤2 次轻查询）；否则执行幂等迁移。"""
    try:
        if not _legacy_table_exists() or _is_finalized():
            return None
    except SQLAlchemyError:
        db.session.rollback()
        return None
    return ensure_governance_v2_tables(migrate_legacy=True)
