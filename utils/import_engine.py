# -*- coding: utf-8 -*-
"""utils.import_engine — K3 居民名单导入引擎（T01/T02 业务逻辑唯一 owner）。

规格依据（详细设计 v1.1 §2.2 / §2.5 / §8 T01/T02 + PRD R1-E2）：
- 三级匹配：精确别名 → 模板规则 → difflib 模糊(0.6)
- 阈值守卫：未映射比例 >50% 或 新增字段 >10 → blocked（admin 确认豁免）
- 校验预览：必填/格式/身份证去重（库内 + 名单内），展示全脱敏
- 导入管线：A1 后台任务，每批 ≤1000 行一个事务，失败行进暂存区不阻断整批，
  敏感字段（id_card/phone/bank_card）**直接写密文**（_enc/_hash，原列置 NULL）。

宪法约束：业务逻辑唯一落本模块（不新建 services/）；加密走 utils/crypto；
脱敏走 utils/masking；模型读写走 models/（schema owner）。

注：import-engine-r1e2 原型已丢失（2026-08-13 评估），本模块按 §2.2 重建；
14 测试正式移植归 T12（Sprint2）。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from datetime import datetime, date

from models import (db, ImportBatch, ImportFieldMapping, ImportRowError,
                    ImportFieldRegistry, ImportProfile, Resident, ResidentTag)
from models.schema_governance import SchemaChangeRequest, DataModelVersion

# ---------------------------------------------------------------
# 目标字段目录（居民台账 residents 列，对齐 models/resident.py）
# ---------------------------------------------------------------
# code, label, aliases, data_type, required, sensitive, dict_category
FIELD_CATALOG = [
    ('name', '姓名', ['居民姓名'], 'string', True, None, None),
    ('id_card', '身份证号', ['身份证', '证件号码', '公民身份号码'], 'string', True, 'id_card', None),
    ('birth_date', '出生日期', ['生日'], 'date', False, None, None),
    ('gender', '性别', [], 'string', False, None, None),
    ('nation', '民族', [], 'enum', False, None, '民族'),
    ('phone', '联系电话', ['电话', '手机号', '联系方式'], 'string', False, 'phone', None),
    ('village_group', '所在组', ['村组', '村民小组', '小组'], 'enum', False, None, '村组'),
    ('grid', '所在网格', ['网格'], 'string', False, None, None),
    ('address', '家庭住址', ['家庭地址', '住址', '地址'], 'string', False, None, None),
    ('household_name', '户主姓名', ['户主', '户主名'], 'string', False, None, None),
    ('household_code', '户编号', ['户号', '户编码'], 'string', False, None, None),
    ('relationship', '与户主关系', ['关系'], 'enum', False, None, '与户主关系'),
    ('household_type', '户口类型', ['户籍类型'], 'enum', False, None, '户口类型'),
    ('politics_status', '政治面貌', ['政治身份'], 'enum', False, None, '政治面貌'),
    ('education', '学历', ['文化程度'], 'enum', False, None, '学历'),
    ('occupation', '职业', ['工作'], 'string', False, None, None),
    ('health_status', '健康状态', ['健康状况', '身体状况'], 'enum', False, None, '健康状态'),
    ('household_property', '户属性', ['户类型', '家庭类型'], 'enum', False, None, '户属性'),
    ('bank_name', '开户行', ['开户银行'], 'enum', False, None, '开户行'),
    ('bank_card', '银行卡号', ['银行账号', '卡号', '账号'], 'string', False, 'bank_card', None),
    ('marital_status', '婚姻状况', ['婚姻状态'], 'enum', False, None, '婚姻状况'),
    ('military_status', '兵役状况', ['兵役状态', '兵役'], 'enum', False, None, '兵役状况'),
    ('status', '人员状态', ['状态', '居民状态'], 'enum', False, None, '人员状态'),
    ('remark', '备注', ['说明'], 'string', False, None, None),
]
# 034 P1：FIELD_CATALOG 成为唯一真源（补 status 特列 24 项）；模板列与映射目标全部由它派生

_CATALOG_MAP = {f[0]: f for f in FIELD_CATALOG}

# 主字段 code 集合（统计口径门：仅主字段或已审批注册字段可入统计）
FIELD_CATALOG_CODES = frozenset(_CATALOG_MAP.keys())

# 值域映射（最小实现，六条兼容性验收第 2 条）：源值（镇级名单口径）→ 标准值（库内口径）
# 未命中映射的值原样透传（不阻断、不报错，交由后续校验/人工核对）
# 079 A0：military_status 目标值改为经 _military_status_domain() 从「兵役状况」字典
# 等值绑定（懒解析，映射结果与旧硬编码逐条相等），其余键无枚举语义保持静态
VALUE_DOMAIN_MAP = {
    'gender': {'M': '男', 'F': '女', 'm': '男', 'f': '女',
               '1': '男', '2': '女', '0': '未知'},
    'nation': {'01': '汉族', '汉族': '汉族'},
    'marital_status': {'1': '未婚', '2': '已婚', '3': '离异', '4': '丧偶'},
}


def _military_status_domain() -> dict:
    """079 A0 / 080 A-3：兵役状况值域映射。

    源码 '1'/'2' 为镇级名单口径（1=未服 / 2=已服），与字典序无关；
    目标值经 dict_enum 与「兵役状况」字典 label 等值绑定（缺项/无上下文回退种子），
    映射结果与收敛前硬编码逐条相等（等价断言见 tests/test_079_a0_enum_consolidation.py）。

    080 A-3 补充文本别名（目标值仍走字典等值绑定，语义不变）：
    未服役 → get_enum_value('兵役状况', '未服兵役')
    已服役 → get_enum_value('兵役状况', '已服兵役')
    退伍/复员 → get_enum_value('兵役状况', '退役')；服役中 → get_enum_value('兵役状况', '现役')
    未命中值仍由 apply_value_domain 原样透传。
    """
    from utils.dict_enum import get_enum_value
    no_service = get_enum_value('兵役状况', '未服兵役')
    served = get_enum_value('兵役状况', '已服兵役')
    retired = get_enum_value('兵役状况', '退役')
    active = get_enum_value('兵役状况', '现役')
    return {
        '1': no_service, '2': served,
        no_service: no_service, served: served,
        retired: retired, active: active,
        '未服役': no_service, '已服役': served,
        '退伍': retired, '复员': retired,
        '服役中': active,
    }

# 模板规则层（镇级名单模板常见表头，区别于字段目录别名，命中标 matched_by='template'）
TEMPLATE_RULES = {
    '身份证号码': 'id_card',
    '手机号码': 'phone',
    '出生年月': 'birth_date',
    '居住地址': 'address',
}

# 政策性户属性 → 派生标签（对齐 T09 _backfill_tags 口径）
TAG_HOUSEHOLD_PROPS = ('脱贫户', '监测户', '五保户')

UNMAPPED_RATIO_MAX = 0.5
NEW_FIELD_MAX = 10
BATCH_SIZE = 1000
MAX_IMPORT_SIZE = 50 * 1024 * 1024   # 034 P1：双模式统一 50MB（与全局 MAX_CONTENT_LENGTH 一致）
MAX_IMPORT_ROWS = 200000             # 行数护栏：>20 万拒绝
SLOW_ROW_HINT = 50000                # >5 万行提示耗时
TEMPLATE_REQUIRED_CODES = ('name', 'id_card', 'relationship', 'household_name')  # 034 目标2/验收2

# 034 P1：人员状态归一（A4 迁出语义并入内核一处；快导旧 _normalize_import_status 的 ''
# 空洞不再落到既有行——空状态：新增行 normal、既有行保留原值〔覆盖语义修正 P4-②〕）
# 079 A0：'流出' 键为 flow_status 字典取值（等值绑定），经 _status_map() 动态并入（进程缓存）
_STATUS_MAP = {
    '正常': 'normal', 'normal': 'normal',
    '死亡': 'deceased', 'deceased': 'deceased',
    '迁出': 'moved_out', 'moved_out': 'moved_out',
}

_status_map_cache = None


def _status_map() -> dict:
    """079 A0：归一表（含 flow_status 字典读出的「流出」键；缺项/无上下文回退种子，值不变）。"""
    global _status_map_cache
    if _status_map_cache is None:
        from utils.dict_enum import get_enum_value
        _status_map_cache = dict(_STATUS_MAP)
        _status_map_cache[get_enum_value('流动状态', '流出')] = 'moved_out'
    return _status_map_cache

# ---------------------------------------------------------------
# 表头规范化与三级匹配
# ---------------------------------------------------------------


def _normalize_header(h) -> str:
    if h is None:
        return ''
    s = str(h).strip()
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:            # 全角空格
            out.append(' ')
        elif 0xFF01 <= code <= 0xFF5E:  # 全角字符 → 半角
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    s = ''.join(out)
    return re.sub(r'[\s\-_/\\()（）【】\[\]：:、,，。.]+', '', s)


def _catalog_index():
    """(norm_label_or_alias -> code) 映射（exact 匹配用）。"""
    idx = {}
    for code, label, aliases, *_ in FIELD_CATALOG:
        idx[_normalize_header(label)] = code
        for a in aliases:
            idx[_normalize_header(a)] = code
    return idx


_EXACT_INDEX = _catalog_index()


def match_field(header) -> tuple[str | None, str]:
    """三级匹配返回 (target_code, matched_by)；matched_by ∈ exact/template/fuzzy/unknown。"""
    norm = _normalize_header(header)
    if not norm:
        return None, 'unknown'
    # ① 精确别名
    code = _EXACT_INDEX.get(norm)
    if code:
        return code, 'exact'
    # ② 模板规则
    for tkey, tcode in TEMPLATE_RULES.items():
        if norm == _normalize_header(tkey):
            return tcode, 'template'
    # ③ difflib 模糊（对所有 label/alias 取最佳）
    best_code, best_ratio = None, 0.0
    for ccode, label, aliases, *_ in FIELD_CATALOG:
        for cand in [label] + aliases:
            r = difflib.SequenceMatcher(None, norm, _normalize_header(cand)).ratio()
            if r > best_ratio:
                best_ratio, best_code = r, ccode
    if best_ratio >= 0.6:
        return best_code, 'fuzzy'
    return None, 'unknown'


def build_mapping(headers) -> dict:
    """对源表头跑三级匹配，返回逐列映射 + 阈值守卫判定（供步2展示/保存）。"""
    columns = []
    for h in headers:
        code, matched_by = match_field(h)
        meta = _CATALOG_MAP.get(code) if code else None
        columns.append({
            'source': h,
            'target': code,
            'target_label': meta[1] if meta else None,
            'matched_by': matched_by,
            'sensitive': meta[5] if meta else None,
            'data_type': meta[3] if meta else None,
            'required': bool(meta[4]) if meta else False,
            'dict_category': meta[6] if meta else None,
        })
    blocked, thresholds, reason = compute_threshold(headers, columns)
    return {
        'columns': columns,
        'catalog': [{
            'code': f[0], 'label': f[1], 'data_type': f[3],
            'required': bool(f[4]), 'sensitive': f[5], 'dict_category': f[6],
        } for f in FIELD_CATALOG],
        'threshold': {'blocked': blocked, 'thresholds': thresholds, 'reason': reason},
    }


def compute_threshold(headers, columns) -> tuple[bool, dict, str | None]:
    """阈值守卫：未映射比例 >50% 或 新增字段 >10 → blocked。"""
    total = len(headers)
    unmapped = [c for c in columns if c['target'] is None]
    unmapped_count = len(unmapped)
    ratio = (unmapped_count / total) if total else 0.0
    new_field_count = unmapped_count  # 未映射列即候选新增字段（ext_data / 字段注册中心）
    thresholds = {
        'total_cols': total,
        'unmapped': unmapped_count,
        'ratio': round(ratio, 4),
        'new_fields': new_field_count,
    }
    blocked = (ratio > UNMAPPED_RATIO_MAX) or (new_field_count > NEW_FIELD_MAX)
    reason = None
    if blocked:
        parts = []
        if ratio > UNMAPPED_RATIO_MAX:
            parts.append(f'未映射比例 {ratio:.0%} > {UNMAPPED_RATIO_MAX:.0%}')
        if new_field_count > NEW_FIELD_MAX:
            parts.append(f'新增字段 {new_field_count} > {NEW_FIELD_MAX}')
        reason = '；'.join(parts)
    return blocked, thresholds, reason


# ---------------------------------------------------------------
# 036 P3：导入方案 Profile（映射复用 + catalog_version 绑定）
# ---------------------------------------------------------------

STANDARD_PROFILE_KEY = 'standard_v1'


def field_catalog_version() -> str:
    """FIELD_CATALOG 结构短 hash：字段增删/改名/别名变化 → 版本自动变化。"""
    payload = [[f[0], f[1], list(f[2]), f[3], bool(f[4]), f[5], f[6]]
               for f in FIELD_CATALOG]
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    return 'v1-' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]


CATALOG_VERSION = field_catalog_version()


def standard_profile_mapping() -> list[dict]:
    """standard_v1 内置映射（24 项）：模板列与模板模式映射共用 FIELD_CATALOG 派生。"""
    return [{'header': f[1], 'field': f[0], 'transform': 'map'}
            for f in FIELD_CATALOG]


def profile_key_for_mapping(mapping) -> str:
    """自建方案 key：同映射复用同一 key（custom_<hash>），重复保存不产生第二行。"""
    raw = json.dumps(mapping or [], ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'))
    return 'custom_' + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]


def save_profile(name, mapping, created_by=None, default_policy='skip',
                 required_permission='special.import_batch', profile_key=None,
                 is_builtin=False):
    """保存/更新方案（UNIQUE profile_key 幂等 upsert）；内置方案不被自建覆盖。"""
    if not name or not str(name).strip():
        raise ValueError('方案名称不能为空')
    if not mapping:
        raise ValueError('映射不能为空')
    key = profile_key or profile_key_for_mapping(mapping)
    rec = ImportProfile.query.filter_by(profile_key=key).first()
    if rec is not None and rec.is_builtin and not is_builtin:
        return None
    now = datetime.now()
    if rec is None:
        rec = ImportProfile(profile_key=key, created_at=now, created_by=created_by)
        db.session.add(rec)
    rec.name = str(name).strip()[:120]
    rec.is_builtin = bool(is_builtin or rec.is_builtin)
    rec.catalog_version = CATALOG_VERSION
    rec.mapping_json = json.dumps(mapping, ensure_ascii=False, separators=(',', ':'))
    rec.default_policy = default_policy if default_policy in ('skip', 'update_non_empty') else 'skip'
    rec.required_permission = required_permission
    # created_by 仅在新建时写入：同一映射被他人重复保存时保留原创建者，
    # 避免可见性归属（list_profiles 按 created_by 过滤）被后保存者静默夺取。
    rec.updated_at = now
    db.session.commit()
    return rec


def get_profile(profile_key):
    return ImportProfile.query.filter_by(profile_key=profile_key).first()


def list_profiles(username=None, is_admin=False):
    """可见性：内置只读全员可见；自建仅创建者可见；admin 可见全部。"""
    rows = ImportProfile.query.order_by(ImportProfile.is_builtin.desc(),
                                        ImportProfile.id.desc()).all()
    if is_admin:
        return rows
    return [p for p in rows if p.is_builtin or (username and p.created_by == username)]


def mapping_json_from_batch(batch_id) -> list[dict]:
    """当前批次血缘 → 方案映射结构 [{header, field, transform}]。"""
    out = []
    for src, m in load_mapping(batch_id).items():
        kind = m.get('kind')
        if kind == 'ignore':
            transform = 'ignore'
        elif m.get('storage') == 'json_ext':
            transform = 'ext'
        else:
            transform = 'map'
        out.append({'header': src, 'field': m.get('target'), 'transform': transform})
    return out


def apply_profile_mapping(columns, mapping) -> tuple[list[dict], list[str]]:
    """把方案映射覆盖到当前表头（按 header 精确匹配）；返回 (columns, 未命中表头)。"""
    by_header = {str(e.get('header')): e for e in (mapping or [])
                 if isinstance(e, dict) and e.get('header')}
    unmatched = []
    for col in columns:
        e = by_header.get(col.get('source'))
        if not e:
            unmatched.append(col.get('source'))
            continue
        action = e.get('transform') or 'map'
        field = e.get('field')
        col['matched_by'] = 'preset'
        if action in ('ignore', 'ext'):
            col['target'] = None
            col['target_label'] = None
            col['action'] = action
            continue
        meta = _CATALOG_MAP.get(field)
        if meta:
            col.update(target=field, target_label=meta[1], action='map',
                       sensitive=meta[5], data_type=meta[3],
                       required=bool(meta[4]), dict_category=meta[6])
        else:
            # 方案字段已不在目录（版本漂移）：降级扩展列，避免写不存在的列
            col.update(target=None, target_label=None, action='ext')
    return columns, unmatched


def batch_catalog_version(batch):
    """批次模板版本快照（独立列 catalog_version，R-036-02 起不再从 profile_key 解析）。"""
    return getattr(batch, 'catalog_version', None) or None


def batch_version_mismatch(batch) -> bool:
    """批次模板版本与当前目录版本不一致（导入前提示，不失败）。"""
    v = batch_catalog_version(batch)
    return bool(v and v != CATALOG_VERSION)


# ---------------------------------------------------------------
# 034 P1：单一 schema 真源 + 内核化批次入口
# ---------------------------------------------------------------


def template_field_map() -> list[dict]:
    """由 FIELD_CATALOG 派生模板列（034 P1 删手写清单；036 R-036-09 起与 standard_v1 同口径）。

    [{label, key, dict, required}] —— 列顺序=目录顺序（人员状态在备注后，兼容旧模板）；
    dict 用 catalog 的 dict_category（真实字典分类名；无则 None 不生成下拉）。
    R-036-09（2026-10-21 主理人拍板选项②）：`grid`（所在网格）加回模板列，模板下载、
    standard_v1、映射页三者同为 24 项；034 的「模板 23 列」契约自此作废。
    """
    out = []
    for code, label, _aliases, _dtype, required, _sensitive, dict_cat in FIELD_CATALOG:
        out.append({
            'label': label,
            'key': code,
            'dict': dict_cat,
            'required': bool(required),
        })
    return out


def _is_zip_magic(head: bytes) -> bool:
    """xlsx 即 zip：校验 PK 头（022 上传防护同款，防改后缀伪装）。"""
    return len(head) >= 4 and head[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')


def count_rows_xlsx(path, limit=None) -> int:
    """read_only 流式计数数据行（不含表头），达到 limit 即早停返回 limit。

    返回 (数据行数, 是否超限)。用于 034 行数护栏（>20 万拒绝 / >5 万提示）。
    """
    if limit is None:
        limit = MAX_IMPORT_ROWS + 1  # 运行时取值（monkeypatch/测试友好）
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    count = 0
    over = False
    it = ws.iter_rows(values_only=True)
    try:
        next(it)  # 表头
    except StopIteration:
        wb.close()
        return 0, False
    for raw in it:
        if raw is None or all(c is None or str(c).strip() == '' for c in raw):
            continue
        count += 1
        if count >= limit:
            over = True
            break
    wb.close()
    return count, over


def start_batch(file_storage, operator_id, mode='custom') -> tuple[ImportBatch, str | None, bool]:
    """034 P1：文件接收 + 建批次抽入内核（resident_enhance.import_upload 与薄化 api_import 共用）。

    返回 (batch, error_msg, was_duplicate)：error_msg 非空 = 校验失败（格式/大小/行数护栏），
    batch=None；was_duplicate=True = source_hash 命中既有批次（幂等一）。
    """
    import hashlib
    import uuid
    from werkzeug.utils import secure_filename
    from config import Config

    from utils.spreadsheet_reader import (
        count_spreadsheet_rows,
        is_allowed_spreadsheet,
        preserve_spreadsheet_ext,
        validate_spreadsheet_magic,
        SPREADSHEET_ERR_MSG,
    )
    filename = file_storage.filename or ''
    if not is_allowed_spreadsheet(filename):
        return None, SPREADSHEET_ERR_MSG, False
    data = file_storage.read()
    if len(data) > MAX_IMPORT_SIZE:
        return None, '文件超过 50MB 上限，请分批导入', False
    ok_magic, magic_err = validate_spreadsheet_magic(filename, data)
    if not ok_magic:
        return None, magic_err, False

    source_hash = hashlib.sha256(data).hexdigest()
    dup = ImportBatch.query.filter_by(source_hash=source_hash).first()
    if dup:
        # 142：判重按状态分流——0 行残留批次（取消/中断后停在 pending/mapped/aborted/blocked）
        # 不再死锁：作废旧批次（保留报告可查），放行重建新批次。done/running 或已入库的
        # 批次维持幂等语义（034 幂等一）。
        _imported_rows = (dup.success_rows or 0) + (dup.updated_rows or 0)
        _stale = dup.status in ('pending', 'mapped', 'aborted', 'blocked') and _imported_rows == 0
        if _stale:
            dup.status = 'aborted'
            dup.block_reason = '同文件重传替换（142：0 行残留批次作废重建）'
            dup.finished_at = datetime.now()
            db.session.commit()
            # 不 return：继续走下方正常建批流程
        else:
            dup.mode = mode  # 034：同文件不同模式入口复用到同一批次时收敛到最新入口
            # 036 F4（R-036-02）：模式收敛时同步 profile_key / catalog_version，
            # 避免「mode 已改 custom、profile_key 仍残留 standard_v1」的不一致态
            dup.profile_key = STANDARD_PROFILE_KEY if mode == 'template' else None
            dup.catalog_version = CATALOG_VERSION if mode == 'template' else None
            db.session.commit()
            return dup, None, True

    imports_dir = getattr(Config, 'IMPORTS_DIR', None)
    if not imports_dir:
        imports_dir = os.path.join(os.environ.get('SHUCUN_TEMP_DIR', ''), 'imports')
    os.makedirs(imports_dir, exist_ok=True)
    # secure_filename 会剥前导点：纯中文文件名（如 名单.xlsx）清洗后可能无扩展名，
    # 而 openpyxl 按扩展名识别格式——落盘必须强保 .xlsx 后缀。
    base = preserve_spreadsheet_ext(filename, secure_filename(filename) or 'upload')
    dest = os.path.join(imports_dir, f"{uuid.uuid4().hex[:12]}_{base}")
    with open(dest, 'wb') as out:
        out.write(data)

    try:
        row_count, over = count_spreadsheet_rows(dest, MAX_IMPORT_ROWS + 1)
    except Exception as exc:
        try:
            os.remove(dest)
        except OSError:
            pass
        return None, f'文件解析失败：{exc}', False
    if over:
        try:
            os.remove(dest)
        except OSError:
            pass
        return None, '文件超过 20 万行上限，请分批导入', False

    batch = ImportBatch(
        batch_id=uuid.uuid4().hex[:12],
        source_file=filename,
        source_path=dest,
        source_hash=source_hash,
        operator=operator_id,
        status='pending',
        mode=mode,
        # 036 F4：模板批次绑定 standard_v1 纯键 + 版本快照独立列（R-036-02 修复，
        # 版本不再塞进 profile_key，避免破坏与 import_profiles.profile_key 的 join）
        profile_key=(STANDARD_PROFILE_KEY if mode == 'template' else None),
        catalog_version=(CATALOG_VERSION if mode == 'template' else None),
    )
    db.session.add(batch)
    db.session.commit()
    return batch, None, False


def apply_preset_mapping(batch_id, headers) -> dict:
    """034 P1：模板模式预设映射（catalog 精确别名匹配，不做模糊）。

    命中 → 落 import_field_mapping（matched_by='template'）；缺失必需列 → 返回 missing 列表
    且不落映射（由路由置 blocked）；模板外列 → extra 列表（忽略并计数提示）。
    返回 {'ok': bool, 'missing': [...], 'extra': [...], 'matched': [...]}
    """
    from models import ImportFieldMapping
    exact = _catalog_index()
    matched, extra, seen_targets = [], [], set()
    for h in headers:
        norm = _normalize_header(h)
        if not norm:
            continue  # 空白列忽略
        code = exact.get(norm)
        if code:
            if code in seen_targets:
                return {'ok': False, 'missing': [],
                        'extra': [], 'matched': [],
                        'error': f'表头重名映射歧义：{h}'}
            seen_targets.add(code)
            matched.append((h, code))
        else:
            extra.append(h)

    miss = [c for c in TEMPLATE_REQUIRED_CODES if c not in seen_targets]
    if miss:
        return {'ok': False, 'missing': miss, 'extra': extra,
                'matched': matched, 'error': None}

    ImportFieldMapping.query.filter_by(batch_id=batch_id).delete()
    for h, code in matched:
        meta = _CATALOG_MAP[code]
        db.session.add(ImportFieldMapping(
            batch_id=batch_id, source_field=h, target_field=code,
            target_table='residents', storage='column', mapping_kind='rename',
            matched_by='template',
            required=1 if (meta and meta[4]) or code in TEMPLATE_REQUIRED_CODES else 0))
    db.session.commit()
    return {'ok': True, 'missing': [], 'extra': extra, 'matched': matched, 'error': None}


# ---------------------------------------------------------------
# Excel 读取
# ---------------------------------------------------------------


def read_xlsx(path) -> tuple[list[str], list[dict]]:
    """读取 xlsx：首行为表头，其余为数据行（dict: 表头 -> 单元格值）。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    try:
        first = next(it)
    except StopIteration:
        wb.close()
        return [], []
    headers = [str(c).strip() if c is not None else '' for c in first]
    rows = []
    for raw in it:
        if raw is None or all(c is None or str(c).strip() == '' for c in raw):
            continue
        rows.append({headers[i]: raw[i] for i in range(len(headers)) if i < len(raw)})
    wb.close()
    return headers, rows


def read_csv_rows(path) -> tuple[list[str], list[dict]]:
    """读取 csv：首行为表头，其余为数据行（dict: 表头 -> 单元格值）。

    105 契约扩展（2026-11-19）：编码 utf-8-sig / gbk 自适应；单元格统一为文本，
    经 catalog.cell_text 规范化后与 xlsx 口径一致。
    """
    import csv as _csv
    data = open(path, 'rb').read()
    text = None
    for enc in ('utf-8-sig', 'gbk'):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError('无法识别文件编码（仅支持 UTF-8 / GBK）')
    it = _csv.reader(text.splitlines())
    raw_rows = [r for r in it if any(str(c).strip() for c in r)]
    if not raw_rows:
        return [], []
    headers = [str(c).strip() for c in raw_rows[0]]
    rows = []
    for raw in raw_rows[1:]:
        rows.append({headers[i]: (raw[i] if i < len(raw) else None)
                     for i in range(len(headers))})
    return headers, rows


def read_xls_rows(path) -> tuple[list[str], list[dict]]:
    """读取 xls（97-2003）：首行为表头，其余为数据行；依赖 xlrd>=2.0（仅 .xls，与 openpyxl 分工互补）。"""
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError('服务器未安装 xlrd，无法解析 .xls；请将名单另存为 .xlsx') from exc
    wb = xlrd.open_workbook(path)
    ws = wb.sheet_by_index(0)
    if ws.nrows == 0:
        return [], []
    headers = [str(c).strip() if c is not None else '' for c in ws.row_values(0)]
    rows = []
    for r in range(1, ws.nrows):
        raw = ws.row_values(r)
        if raw is None or all(c is None or str(c).strip() == '' for c in raw):
            continue
        rows.append({headers[i]: raw[i] for i in range(len(headers)) if i < len(raw)})
    return headers, rows


def read_import_file(path) -> tuple[list[str], list[dict]]:
    """按扩展名分发读取器（统一支持 .xlsx / .xls / .xlsb / .csv / .xlsm），接口与 read_xlsx 一致。"""
    from utils.spreadsheet_reader import read_spreadsheet_dicts
    return read_spreadsheet_dicts(path, path)


# ---------------------------------------------------------------
# 值规范化 / 转换
# ---------------------------------------------------------------


def _norm_value(raw):
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, date):
        return raw
    if isinstance(raw, bool):
        return '是' if raw else '否'
    if isinstance(raw, (int, float)):
        # Excel 数字身份证/电话（无格式）会丢前导零，转为整数字符串
        if isinstance(raw, float) and raw.is_integer():
            return str(int(raw))
        return str(raw)
    s = str(raw).strip()
    return s if s else None


def parse_date_value(raw) -> date | None:
    """解析出生日期：datetime/date 直返；字符串兼容 YYYY-MM-DD / YYYY/MM/DD / YYYYMMDD。"""
    v = _norm_value(raw)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v)
    for fmt in ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, '%Y%m%d').date()
        except ValueError:
            return None
    return None


def validate_id_card(value) -> tuple[bool, str]:
    """身份证轻校验：15 或 18 位，17 位数字 + 末位数字/X。返回 (合法, 错误信息)。"""
    if not value:
        return False, '身份证为空'
    s = str(value).strip().upper()
    if len(s) not in (15, 18):
        return False, '身份证长度须为 15 或 18 位'
    if len(s) == 18:
        if not s[:17].isdigit() or not (s[17].isdigit() or s[17] == 'X'):
            return False, '身份证格式非法'
    else:
        if not s.isdigit():
            return False, '身份证格式非法'
    return True, ''


_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK_CODES = '10X98765432'


def id_card_checksum_ok(value) -> bool:
    """18 位身份证校验位（GB 11643）。15 位旧号返回 False（须升位）。"""
    s = str(value or '').strip().upper()
    if len(s) != 18 or not s[:17].isdigit() or not (s[17].isdigit() or s[17] == 'X'):
        return False
    total = sum(int(digit) * weight for digit, weight in zip(s[:17], _ID_WEIGHTS))
    return _ID_CHECK_CODES[total % 11] == s[17]


def id_card_checksum_error(value) -> str | None:
    """GLB-42：导入侧身份证硬校验唯一出口——格式已过 `validate_id_card` 后调用。

    返回 None 表示通过；返回字符串为可直接展示的拒绝原因（15 位旧号须升位）。
    """
    s = str(value or '').strip().upper()
    if len(s) == 15:
        return '身份证为 15 位旧号，请升位为 18 位'
    if not id_card_checksum_ok(s):
        return '身份证校验位不正确（GB 11643）'
    return None


def derive_gender(id_card: str | None) -> str | None:
    if not id_card or len(id_card) != 18:
        return None
    try:
        return '男' if int(id_card[16]) % 2 == 1 else '女'
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------
# 去重（加密态 hash 命中 或 明文原列命中，兼容 T11 前的过渡态）
# ---------------------------------------------------------------


def _id_card_exists(id_card, id_card_hash) -> bool:
    """库内身份证去重：优先密文 hash 等值，回退明文原列（过渡态兼容）。

    P2-3 红旗项整改：id_card 统一大写归一后再 hash/比对，与通用管线
    （utils/sensitive_fields.set_encrypted_field 的 id_card.upper()）口径一致，
    消除末位校验码 X/x 大小写不同导致的 hash 错配。
    """
    from utils.crypto import sha256_hash
    id_card_norm = str(id_card or '').strip().upper()
    h = id_card_hash or (sha256_hash(id_card_norm) if id_card_norm else None)
    if h and Resident.query.filter(Resident.id_card_hash == h).first():
        return True
    if id_card_norm and Resident.query.filter(Resident.id_card == id_card_norm).first():
        return True
    return False


# ---------------------------------------------------------------
# 校验预览（dry-run，全脱敏）
# ---------------------------------------------------------------


def validate_rows(batch_id, headers, raw_rows, column_map,
                  required_codes=('name', 'id_card')) -> dict:
    """dry-run 校验：必填/格式/身份证去重（库内 + 名单内）+ 户编号冲突。返回统计 + 脱敏明细。

    034 P1：required_codes 参数化（模板模式传入 TEMPLATE_REQUIRED_CODES——
    姓名/身份证/与户主关系/户主名；自定义保持姓名/身份证）。
    035 R-034-05 口径统一（与 run_import 回执一致）：
    - 库内/名单内重复为**信息桶**（错误明细带 bucket='dup'，计入 duplicate/list_dup），
      不计入 fail——覆盖更新模式下既有居民本就应执行（更新），预扣失败会把可执行行
      错误地排除出执行集合；
    - 硬失败（必填缺失/身份证格式/出生日期非法/户编号冲突）计 fail（bucket='fail'）；
      户编号冲突与 run_import 同规则（仅对「身份证不在库内且不在名单内重复」的新建行
      检查），使预览能看到执行才会判的失败；
    - 行四分类互斥：valid（无任何错误）/ fail（≥1 硬失败）/ 库内重复 / 名单内重复，
      total = valid + fail + duplicate + list_dup。
    """
    from utils.crypto import sha256_hash
    from utils.masking import mask_id_card, mask_phone, mask_bank_card

    errors = []
    seen_hashes = set()
    valid = 0
    fail = 0
    duplicate = 0
    list_dup = 0

    for idx, row in enumerate(raw_rows, start=2):  # Excel 行号从 2 起（首行表头）
        mapped, _ext = _apply_row(row, column_map)
        id_card = mapped.get('id_card')
        id_card_s = str(id_card).strip().upper() if id_card else ''
        hard = False        # 本行是否存在硬失败（bucket='fail'）
        # 必填（034：参数化必填集合）
        for required_code in required_codes:
            v = mapped.get(required_code)
            if v is None or str(v).strip() == '':
                errors.append({
                    'row_no': idx, 'field': required_code, 'error_type': 'required',
                    'reason': f'必填字段缺失：{_CATALOG_MAP[required_code][1]}',
                    'value': '', 'bucket': 'fail',
                })
                hard = True
        # 身份证格式
        if id_card_s:
            ok, msg = validate_id_card(id_card_s)
            if not ok:
                errors.append({
                    'row_no': idx, 'field': 'id_card', 'error_type': 'type',
                    'reason': msg, 'value': mask_id_card(id_card_s), 'bucket': 'fail',
                })
                hard = True
            else:
                checksum_err = id_card_checksum_error(id_card_s)
                if checksum_err:
                    # GLB-42：错校验位/15 位旧号在预览即判硬失败，与执行口径一致
                    errors.append({
                        'row_no': idx, 'field': 'id_card', 'error_type': 'type',
                        'reason': checksum_err, 'value': mask_id_card(id_card_s),
                        'bucket': 'fail',
                    })
                    hard = True
                    fail += 1
                    continue
                h = sha256_hash(id_card_s)
                if _id_card_exists(id_card_s, h):
                    # 035 R-034-05：库内重复为信息桶（覆盖更新模式本就应执行），不计失败
                    duplicate += 1
                    errors.append({
                        'row_no': idx, 'field': 'id_card', 'error_type': 'row',
                        'reason': '库内已有该身份证', 'value': mask_id_card(id_card_s),
                        'bucket': 'dup',
                    })
                elif h in seen_hashes:
                    list_dup += 1
                    errors.append({
                        'row_no': idx, 'field': 'id_card', 'error_type': 'row',
                        'reason': '名单内身份证重复', 'value': mask_id_card(id_card_s),
                        'bucket': 'dup',
                    })
                else:
                    seen_hashes.add(h)
                    # 户编号冲突（035 R-034-05：与 run_import 同规则——仅对新建行检查，
                    # 使预览能看到执行才会判的失败）
                    hc = str(mapped.get('household_code') or '').strip()
                    hh_name = str(mapped.get('household_name') or '').strip()
                    if hc:
                        other = Resident.query.filter_by(household_code=hc).first()
                        if other and (other.household_name or '') != hh_name:
                            errors.append({
                                'row_no': idx, 'field': 'household_code',
                                'error_type': 'row',
                                'reason': f'户编号冲突：{hc} 已属于 {other.household_name}',
                                'value': hc, 'bucket': 'fail',
                            })
                            hard = True
        # 出生日期格式
        if mapped.get('birth_date') is not None:
            if parse_date_value(mapped.get('birth_date')) is None:
                errors.append({
                    'row_no': idx, 'field': 'birth_date', 'error_type': 'type',
                    'reason': '出生日期格式非法（应为 YYYY-MM-DD）',
                    'value': str(mapped.get('birth_date')), 'bucket': 'fail',
                })
                hard = True
        # 行四分类（互斥）：硬失败 / 仅重复（信息桶，已在上方计数）/ 干净行
        if hard:
            fail += 1
        elif not any(e['row_no'] == idx for e in errors):
            valid += 1

    return {
        'total': len(raw_rows),
        'valid': valid,
        'fail': fail,
        'duplicate': duplicate,
        'list_dup': list_dup,
        'errors': errors,
    }


# ---------------------------------------------------------------
# 映射持久化（步2 保存）与读取
# ---------------------------------------------------------------


def save_mapping(batch_id, columns) -> None:
    """落 import_field_mapping（血缘）。columns: [{source, action(map/ext/ignore), target}]"""
    for c in columns:
        source = c.get('source')
        action = c.get('action', 'map')
        target = c.get('target')
        matched_by = c.get('matched_by', 'manual')
        if action == 'ignore':
            m = ImportFieldMapping(batch_id=batch_id, source_field=source,
                                   target_field=None, target_table='residents',
                                   storage=None, mapping_kind='ignore',
                                   matched_by=matched_by, required=0)
        elif action == 'ext':
            m = ImportFieldMapping(batch_id=batch_id, source_field=source,
                                   target_field=source, target_table='residents',
                                   storage='json_ext', mapping_kind='ext',
                                   matched_by=matched_by, required=0)
        else:  # map
            meta = _CATALOG_MAP.get(target)
            m = ImportFieldMapping(batch_id=batch_id, source_field=source,
                                   target_field=target, target_table='residents',
                                   storage='column', mapping_kind='rename',
                                   matched_by=matched_by,
                                   required=1 if (meta and meta[4]) else 0)
        db.session.add(m)


def load_mapping(batch_id) -> dict:
    """读取映射 → {source_field: {'target': code, 'storage': 'column'|'json_ext', 'kind': ...}}"""
    out = {}
    for r in ImportFieldMapping.query.filter_by(batch_id=batch_id).all():
        if r.mapping_kind == 'ignore':
            out[r.source_field] = {'target': None, 'storage': None, 'kind': 'ignore'}
        elif r.storage == 'json_ext':
            out[r.source_field] = {'target': r.source_field, 'storage': 'json_ext', 'kind': 'ext'}
        else:
            out[r.source_field] = {'target': r.target_field, 'storage': 'column', 'kind': r.mapping_kind}
    return out


def _apply_row(raw_row, column_map) -> tuple[dict, dict]:
    """把原始行按映射转成 {目标字段: 值} + ext_data dict。"""
    values = {}
    ext = {}
    for src, val in raw_row.items():
        m = column_map.get(src)
        if not m or m['kind'] == 'ignore':
            continue
        v = _norm_value(val)
        if v is None:
            continue
        if m['storage'] == 'json_ext':
            ext[src] = str(v)
        elif m['target']:
            values[m['target']] = apply_value_domain(m['target'], v)
    return values, ext


def apply_value_domain(code, value):
    """值域映射（六条兼容性验收第 2 条）：源值 → 标准值，未命中原样透传。"""
    if value is None:
        return None
    # 079 A0：military_status 目标值走字典等值绑定（懒解析），其余键保持静态映射
    dom = _military_status_domain() if code == 'military_status' else VALUE_DOMAIN_MAP.get(code)
    if not dom:
        return value
    key = str(value).strip()
    return dom.get(key, value)


# ---------------------------------------------------------------
# 导入执行（A1 worker）
# ---------------------------------------------------------------


# ---------------------------------------------------------------
# 034 P1：唯一写入通道（apply_resident_row / 户编号分配 / run_import 扩展）
# ---------------------------------------------------------------

# 079 A0：flow_status 写入值收敛到 resident_flow 唯一权威（dict 读，缺项回退种子）。
# ⚠️ 旧本地名 FLOW_IN 语义是「入库默认=常住」，与 resident_flow.FLOW_IN（'流入'）不同，
#    故改名引用 FLOW_RESIDENT，三个赋值点语义逐处核对未变（见 079 RESULT 对照表）。
from utils.resident_flow import FLOW_RESIDENT, FLOW_OUT  # noqa: E402


def _normalize_import_status_value(raw) -> str | None:
    """人员状态归一：空 → None（由调用方按 created/existing 决策）；未知 → None（行错误）。"""
    s = str(raw or '').strip()
    if not s:
        return None
    return _status_map().get(s.lower()) or _status_map().get(s)


def allocate_household_code(household_name: str, hh_state: dict) -> str:
    """034 P1：新增行户编号分配——批内同户主名复用；否则从计数器生成 H####。

    hh_state = {'map': {户主名: code}, 'counter': [n]}（counter 由调用方以 DB 最大号初始化）。
    """
    m = hh_state['map']
    if household_name in m:
        return m[household_name]
    hh_state['counter'][0] += 1
    code = f"H{hh_state['counter'][0]:04d}"
    m[household_name] = code
    return code


def apply_resident_row(resident, values, created: bool, policy: str, hh_state: dict,
                        ext: dict | None = None) -> list:
    """034 P1：唯一写入通道——新增/覆盖更新共用的行级写入（写路径唯一，宪法红线）。

    - created=True：全新 Resident（已挂 session）写全量；空状态 → normal、空户编号 → 分配、
      flow_status 默认常住、建档来源=导入。
    - created=False + policy='update_non_empty'：**空单元格不覆盖既有值**（P4-① 逐列判断，
      敏感列同样适用）；空状态 → 保留原值（P4-②）；空户编号 → 保留（P4-③）；
      户编号显式值 → 覆盖。
    - 返回错误消息列表（非空则本行不应 flush）。
    """
    errors = []
    id_card_s = str(values.get('id_card') or '').strip().upper()
    if created:
        resident.name = str(values.get('name') or '').strip()
        resident.id_card = None  # 原列置 NULL（不落明文）
        resident.id_card_enc = _encrypt(id_card_s)
        resident.id_card_hash = _sha256(id_card_s)
        resident.id_card_prefix_hash = _sha256_prefix(id_card_s, 6)
        resident.source = '导入'
        resident.flow_status = FLOW_RESIDENT
    # 敏感字段（created 或 update 显式非空才写；空值不覆盖既有——P4-①）
    from utils.phone_utils import normalize_phones, primary_phone
    phone_s = normalize_phones(values.get('phone'))
    if (created or policy == 'update_non_empty') and phone_s:
        p_main = primary_phone(phone_s)
        resident.phone = None
        resident.phone_enc = _encrypt(phone_s)
        resident.phone_hash = _sha256(p_main)
        resident.phone_prefix_hash = _sha256_prefix(p_main, 6)
    bank_s = str(values.get('bank_card') or '').strip()
    if (created or policy == 'update_non_empty') and bank_s:
        resident.bank_card = None
        resident.bank_card_enc = _encrypt(bank_s)
        resident.bank_card_hash = _sha256(bank_s)
    # 普通字段（P4-①：update 模式空值不覆盖）
    for code in ('household_name', 'household_code', 'gender', 'nation',
                 'village_group', 'grid', 'address', 'relationship',
                 'household_type', 'politics_status', 'education',
                 'occupation', 'health_status', 'household_property',
                 'bank_name', 'marital_status', 'military_status', 'remark'):
        v = values.get(code)
        if v is None:
            continue
        sv = str(v).strip()
        if created:
            setattr(resident, code, sv)
        elif policy == 'update_non_empty' and sv:
            setattr(resident, code, sv)
    # 人员状态特列（P4-② + A4 迁出语义：迁出 → status normal + flow 流出）
    norm = _normalize_import_status_value(values.get('status'))
    if created:
        if norm == 'moved_out':
            resident.status = 'normal'
            resident.flow_status = FLOW_OUT
        elif norm == 'deceased':
            resident.status = 'deceased'
        else:
            resident.status = 'normal'
    elif norm is not None and policy == 'update_non_empty':
        if norm == 'deceased':
            resident.status = 'deceased'
        elif norm == 'moved_out':
            resident.status = 'normal'
            resident.flow_status = FLOW_OUT
        else:
            resident.status = 'normal'
    # 户编号特列（P4-③：覆盖模式下空值保留；新增且空 → 分配；显式值 → created/update 统一采用）
    hc = str(values.get('household_code') or '').strip()
    household_name = str(values.get('household_name') or '').strip()
    if created and not hc and household_name:
        resident.household_code = allocate_household_code(household_name, hh_state)
    bd = parse_date_value(values.get('birth_date'))
    if bd:
        if created:
            resident.birth_date = bd
        elif policy == 'update_non_empty':
            resident.birth_date = bd
    if created and not resident.gender:
        resident.gender = derive_gender(id_card_s)
    # 扩展列（ext_data）：新增整存；覆盖模式非空扩展合并（不整体覆盖既有扩展）
    if ext:
        if created:
            resident.ext_data = json.dumps(ext, ensure_ascii=False)
        elif policy == 'update_non_empty':
            old = {}
            try:
                if resident.ext_data:
                    old = json.loads(resident.ext_data) or {}
            except (TypeError, ValueError):
                old = {}
            old.update(ext)
            resident.ext_data = json.dumps(old, ensure_ascii=False)
    return errors


def _encrypt(plain: str) -> str:
    from utils.crypto import key_holder
    if not key_holder.is_initialized:
        raise RuntimeError('密钥未初始化')
    return key_holder.encrypt(plain)


def _sha256(plain: str) -> str:
    from utils.crypto import sha256_hash
    return sha256_hash(plain)


def _sha256_prefix(plain: str, n: int) -> str:
    from utils.crypto import sha256_prefix_hash
    return sha256_prefix_hash(plain, n)


def find_resident_by_id_card(id_card_s: str):
    """库内身份证查重（仅一次查询：hash 命中或明文原列过渡态）。"""
    h = _sha256(id_card_s)
    r = Resident.query.filter(Resident.id_card_hash == h).first()
    if r:
        return r
    return Resident.query.filter(Resident.id_card == id_card_s).first()


def run_import(task_id: str, batch_id: str) -> None:
    """A1 导入处理函数（run_async 约定：fn(task_id, **kwargs)，已处 app context 内）。

    034 P1 扩展：
    - 双模式共用（mode=template|custom，本函数不区分；映射早已落库）；
    - policy 从 batch.policy 读取（execute 端点落库；缺省 skip）——新增/覆盖统一走
      apply_resident_row 唯一写入通道；更新数记 updated_rows（四计数口径：成功=新增+更新）。
    - 每批 ≤1000 行一个事务；失败行进暂存区不阻断整批；敏感字段加密写入；
      每批后回写游标（断点恢复）；done 幂等。
    """
    from utils.async_tasks import _update

    batch = ImportBatch.query.filter_by(batch_id=batch_id).first()
    if not batch:
        raise RuntimeError(f'导入批次不存在：{batch_id}')
    if batch.status == 'done':
        return  # 幂等：已完成的批次不再重跑

    # 035 R-034-03③：续跑幂等——起跑清空本批失败行暂存区，避免重跑叠加明细
    # （预览失败行自 R-034-05 起统一在起跑时落账，同受此清理保护）。
    ImportRowError.query.filter_by(batch_id=batch_id).delete()

    policy = batch.policy or 'skip'
    batch.status = 'running'
    batch.started_at = datetime.now()
    db.session.commit()

    column_map = load_mapping(batch_id)
    headers, raw_rows = read_import_file(batch.source_path)
    total = len(raw_rows)
    batch.total_rows = total
    db.session.commit()

    _update(task_id, progress=0, result_summary=f'开始导入 {total} 行')

    # 035 R-034-05：预览口径对齐——起跑先跑与步3同源的 validate_rows，预览硬失败行
    # 直接排除出执行集合（回执失败数与预览「失败 M」同口径起算，杜绝执行阶段重复
    # 判 fail 与「预览失败/执行成功」的坏出生日期类口径漂移）；库内/名单内重复为
    # 信息桶不排除（覆盖更新模式仍可更新既有居民）。预览失败明细落暂存区供报告与
    # 失败行导出。
    required_codes = (TEMPLATE_REQUIRED_CODES
                      if batch.mode == 'template' else ('name', 'id_card'))
    preview = validate_rows(batch_id, headers, raw_rows, column_map,
                            required_codes=required_codes)
    excluded = set()
    for e in preview['errors']:
        if e.get('bucket') == 'dup':
            continue
        excluded.add(e['row_no'])
        raw = raw_rows[e['row_no'] - 2] if 0 <= e['row_no'] - 2 < len(raw_rows) else []
        _add_row_error(batch_id, e['row_no'], e.get('field'), e.get('error_type'),
                       e.get('reason'), raw)
    db.session.commit()

    success = 0
    updated = 0
    fail = len(excluded)   # 与预览失败数同口径（035 R-034-05）
    skip = 0
    seen_hashes = set()
    new_field_count = batch.new_field_count or 0
    # 户编号分配上下文：批内 {户主名: code} + 计数器（从库内最大 H 号续起）
    max_code = db.session.query(db.func.max(Resident.household_code)).scalar()
    counter = 0
    if max_code and str(max_code).startswith('H'):
        try:
            counter = int(str(max_code)[1:])
        except ValueError:
            counter = 0
    hh_state = {'map': {}, 'counter': [counter]}

    for start in range(0, total, BATCH_SIZE):
        chunk = raw_rows[start:start + BATCH_SIZE]
        for idx, row in enumerate(chunk, start=start + 2):  # Excel 行号
            if idx in excluded:
                # 035 R-034-05：预览已判硬失败的行不再进入执行（明细已于起跑落账）
                continue
            try:
                values, ext = _apply_row(row, column_map)
                id_card = values.get('id_card')
                id_card_s = str(id_card).strip().upper() if id_card else ''
                # 必填校验
                if not values.get('name') or not id_card_s:
                    _add_row_error(batch_id, idx, 'row', 'required', '必填字段缺失（姓名/身份证）', row)
                    fail += 1
                    continue
                ok, msg = validate_id_card(id_card_s)
                if not ok:
                    _add_row_error(batch_id, idx, 'id_card', 'type', msg, row)
                    fail += 1
                    continue
                checksum_err = id_card_checksum_error(id_card_s)
                if checksum_err:
                    # GLB-42：执行侧同口径拒绝，避免预览放行、执行入库
                    _add_row_error(batch_id, idx, 'id_card', 'type', checksum_err, row)
                    fail += 1
                    continue
                h = _sha256(id_card_s)
                # 去重：库内 + 名单内（名单内重复按跳过口径，与措辞一致）
                if h in seen_hashes:
                    skip += 1
                    continue
                seen_hashes.add(h)

                existing = find_resident_by_id_card(id_card_s)
                if existing is None:
                    # 户编号显式值冲突检测（同编号不同户主名 → 行错误）
                    hc = str(values.get('household_code') or '').strip()
                    hh_name = str(values.get('household_name') or '').strip()
                    if hc:
                        other = Resident.query.filter_by(household_code=hc).first()
                        if other and (other.household_name or '') != hh_name:
                            _add_row_error(batch_id, idx, 'household_code', 'row',
                                           f'户编号冲突：{hc} 已属于 {other.household_name}', row)
                            fail += 1
                            continue
                    resident = Resident()
                    db.session.add(resident)
                    apply_resident_row(resident, values, True, policy, hh_state, ext)
                    success += 1
                elif policy == 'update_non_empty':
                    from utils.resident_sync import ResidentSyncService
                    from utils.crypto import key_holder as _kh
                    def _res_plain(obj, fld):
                        enc = getattr(obj, fld + '_enc', None)
                        if enc and _kh.is_initialized:
                            try:
                                return _kh.decrypt(enc) or ''
                            except Exception:
                                return ''
                        return getattr(obj, fld, '') or ''
                    _old_snap = ResidentSyncService.snapshot_plain(existing, _res_plain)
                    apply_resident_row(existing, values, False, policy, hh_state, ext)
                    _new_snap = ResidentSyncService.snapshot_plain(existing, _res_plain)
                    _sync_changes = ResidentSyncService.diff(_old_snap, _new_snap)
                    if _sync_changes:
                        ResidentSyncService.propagate(existing, _sync_changes, operator=str(batch.operator or ''))
                    success += 1
                    updated += 1
                else:
                    skip += 1
                    continue

                db.session.flush()  # 拿 resident.id 供标签回填

                # 派生政策性标签（仅新增行；T09 口径）
                if existing is None:
                    hp = values.get('household_property')
                    if hp and str(hp).strip() in TAG_HOUSEHOLD_PROPS:
                        db.session.add(ResidentTag(
                            resident_id=resident.id, tag_code=str(hp).strip(),
                            source='导入', status='有效'))
            except Exception as e:
                db.session.rollback()
                _add_row_error(batch_id, idx, None, 'row', f'行处理失败：{type(e).__name__}: {e}', row)
                fail += 1

        # 每批一个事务提交 + 游标回写
        db.session.commit()
        db.session.expire_all()
        batch = ImportBatch.query.filter_by(batch_id=batch_id).first()
        batch.success_rows = success
        batch.updated_rows = updated
        batch.fail_rows = fail
        batch.skip_rows = skip
        batch.new_field_count = new_field_count
        db.session.commit()

        progress = min(int((start + len(chunk)) / total * 100), 100) if total else 100
        _update(task_id, progress=progress,
                result_summary=f'已处理 {start + len(chunk)}/{total}（成功 {success}/更新 {updated}/失败 {fail}/跳过 {skip}）')

    # 完成
    batch = ImportBatch.query.filter_by(batch_id=batch_id).first()
    batch.status = 'done'
    batch.success_rows = success
    batch.updated_rows = updated
    batch.fail_rows = fail
    batch.skip_rows = skip
    batch.finished_at = datetime.now()
    db.session.commit()

    # 新增字段（ext 列）登记字段注册中心 draft（六条兼容性验收第 1 条）
    ext_fields = _collect_ext_fields(column_map)
    if ext_fields:
        register_new_fields(batch_id, ext_fields, batch.operator)
        batch = ImportBatch.query.filter_by(batch_id=batch_id).first()
        batch.new_field_count = len(ext_fields)
        db.session.commit()

    summary = json.dumps({
        'total': total, 'success': success, 'updated': updated,
        'fail': fail, 'skip': skip, 'new_fields': new_field_count,
    }, ensure_ascii=False)
    _update(task_id, status='done', progress=100,
            result_summary=f'导入完成：{summary}', finished_at=datetime.now())


def _add_row_error(batch_id, row_no, field, error_type, reason, raw_row):
    """失败行进暂存区（raw_row 落库前脱敏）。"""
    from utils.masking import mask_text
    safe_raw = json.dumps(raw_row, ensure_ascii=False, default=str)
    db.session.add(ImportRowError(
        batch_id=batch_id, row_no=row_no, field=field,
        error_level='error', error_type=error_type,
        reason=str(reason)[:300], raw_row=mask_text(safe_raw),
        resolved=0))


def resume_import(batch: ImportBatch) -> bool:
    """断点恢复判定：残留 running/aborted 且未 done → 可重进（按身份证 hash 跳过已成功行）。"""
    return batch is not None and batch.status in ('running', 'aborted', 'blocked')


def mark_aborted_stale():
    """进程重启后，残留 running 批次标 aborted（可恢复重进）。"""
    stale = ImportBatch.query.filter_by(status='running').all()
    for b in stale:
        b.status = 'aborted'
    db.session.commit()
    return len(stale)


# ---------------------------------------------------------------
# 字段注册中心 / 血缘 / 审批流 / 数据模型版本号（T12）
# ---------------------------------------------------------------


def _collect_ext_fields(column_map) -> list[str]:
    """从列映射收集 action=ext 的扩展字段（即新增字段，进入 registry）。"""
    return [src for src, m in column_map.items()
            if m.get('storage') == 'json_ext' and m.get('kind') == 'ext']


def register_new_fields(batch_id, ext_fields, operator=None) -> list[str]:
    """新增字段登记 import_field_registry（status=draft）。

    幂等：同 code 已存在则跳过（不重复登记）；返回本次新增的 code 列表。
    definition 落「口径说明/统计用途」占位 JSON，待管理员审批补全。
    """
    registered = []
    for code in ext_fields:
        if not code:
            continue
        if ImportFieldRegistry.query.filter_by(code=code).first():
            continue
        db.session.add(ImportFieldRegistry(
            code=code,
            display_name=code,
            data_type='string',
            source_batch=batch_id,
            definition=json.dumps({
                '口径说明': '', '统计用途': '',
                '来源': '导入新增字段（ext_data，方案A）',
                '待管理员确认': True,
            }, ensure_ascii=False),
            status='draft',
        ))
        registered.append(code)
    if registered:
        db.session.flush()
    return registered


def approve_registry_field(code, approved_by=None, definition=None) -> bool:
    """审批注册字段：draft→approved（留痕 approved_by/approved_at）。

    R-036-11：`definition`（{'口径说明':…, '统计用途':…}）非空时合并写回并清除
    「待管理员确认」；对已 approved 记录重入可作补录（不改状态，只补口径）。
    """
    rec = ImportFieldRegistry.query.filter_by(code=code).first()
    if not rec:
        return False
    if definition:
        try:
            d = json.loads(rec.definition or '{}') if rec.definition else {}
            if not isinstance(d, dict):
                d = {}
        except (TypeError, ValueError):
            d = {}
        d.update({k: str(v).strip()[:200] for k, v in definition.items() if v})
        d['待管理员确认'] = False
        rec.definition = json.dumps(d, ensure_ascii=False)
    rec.status = 'approved'
    rec.approved_by = approved_by
    rec.approved_at = datetime.now()
    db.session.commit()
    return True


def reject_registry_field(code, approved_by=None) -> bool:
    """审批否决注册字段：→rejected（留痕审批人与时间；字段不入统计口径）。"""
    rec = ImportFieldRegistry.query.filter_by(code=code).first()
    if not rec:
        return False
    rec.status = 'rejected'
    rec.approved_by = approved_by
    rec.approved_at = datetime.now()
    db.session.commit()
    return True


def list_registry(status=None, limit=50):
    """查询注册字段（可按 status 过滤：draft/approved/rejected/active）。

    R-036-08③：管理端小列表默认 50 条，上限 200（硬约束：新查询须带上限）。
    """
    q = ImportFieldRegistry.query
    if status:
        q = q.filter_by(status=status)
    return q.order_by(ImportFieldRegistry.id).limit(min(max(int(limit), 1), 200)).all()


def is_statistics_eligible(field_code) -> bool:
    """统计口径门（六条兼容性验收第 5/6 条）。

    仅「主字段」（FIELD_CATALOG 内）或「已 approved/active 的注册字段」放行；
    未登记或仍 draft/rejected 的扩展字段拒绝（不入统计，防口径污染）。
    """
    if field_code in FIELD_CATALOG_CODES:
        return True
    rec = ImportFieldRegistry.query.filter_by(code=field_code).first()
    return bool(rec and rec.status in ('approved', 'active'))


def create_schema_change_request(change_type, payload, requested_by=None,
                                 reason=None) -> str:
    """创建防漂移审批单（schema_change_request，status=pending）。

    change_type ∈ add_field/change_mapping/version_bump；返回 request_no。
    """
    import uuid
    request_no = f"SCR{uuid.uuid4().hex[:12].upper()}"
    db.session.add(SchemaChangeRequest(
        request_no=request_no,
        change_type=change_type,
        payload=json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
        status='pending',
        requested_by=requested_by,
        reason=reason,
    ))
    db.session.commit()
    return request_no


def decide_schema_change(request_no, approve, decided_by=None, reason=None) -> bool:
    """审批防漂移单：pending→approved/rejected（留痕 decided_by/decided_at）。"""
    req = SchemaChangeRequest.query.filter_by(request_no=request_no).first()
    if not req:
        return False
    req.status = 'approved' if approve else 'rejected'
    req.decided_by = decided_by
    req.decided_at = datetime.now()
    if reason:
        req.reason = reason
    db.session.commit()
    return True


def bump_data_model_version(model_name, change_log=None) -> int:
    """数据模型版本 +1 登记（data_model_version）。

    返回新版本号；首次登记版本为 1。列变更/映射规则变更走审批流后调用。
    """
    last = (DataModelVersion.query.filter_by(model_name=model_name)
            .order_by(DataModelVersion.version.desc()).first())
    new_version = (last.version + 1) if last else 1
    db.session.add(DataModelVersion(
        model_name=model_name,
        version=new_version,
        change_log=change_log,
    ))
    db.session.commit()
    return new_version
