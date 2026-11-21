# -*- coding: utf-8 -*-
"""utils/softtable_engine.py — 软表引擎内核（078 交付物 3）

深模块：全部业务判断收敛在此，路由只做「取参数 → 调这里 → 包 JSON」。
模块内**不含任何 CREATE/ALTER/DROP TABLE**（验收 1：业务台账零物理 DDL）。

对外接口（6 个，调用方只需认识这些）：
    parse_table_file(filename, stream) -> (headers, rows)        解析 xlsx/csv
    suggest_fields(headers, rows)      -> list[dict]             类型/敏感推断（仅供确认页）
    normalize_fields(raw)              -> list[dict]             用户确认后的字段规格校验
    build_records(fields, rows)        -> (records, errors)      行校验 + identity 加密 + HMAC
    decode_records(fields, records, reveal=False)                解密/打码 + 反序列化
    select_rows(rows, fields, query)   -> list[dict]             内存筛选/排序（不碰 json_extract）
另附：identity_digest / mask_identity / validate_table_code / FIELD_CODE_RE。

加密口径（ADR-025 决策 4）：
- 值加密走进程内 KeyHolder（口令派生、密钥不落盘）；
- 查重走 **HMAC-SHA256**，密钥由 KeyHolder 密钥经 HKDF 派生（与 Fernet 密钥不同源用途），
  并以 `字段code\\x00` 作 domain separation —— 不复用裸 sha256_hash（低熵证件号彩虹表风险）。
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import hmac
import io
import json
import re
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Sequence, Tuple

FIELD_CODE_RE = re.compile(r'^[a-z0-9_]{1,60}$')
TABLE_CODE_RE = re.compile(r'^[a-z][a-z0-9_]{1,59}$')

from models.dynamic_table import DATA_TYPES, SENSITIVITY_LEVELS

_HMAC_SALT = b'shucun-softtable-hmac-v1'
_HMAC_INFO = b'identity-digest'
_DONT_AMOUNT = ('期号', '编号', '序号', '排序')
_AMOUNT_HINTS = ('金额', '基数', '补贴', '标准', '费用', '工资', '缴费额')
_IDENTITY_HINTS = ('证件', '身份证')
_TEXT_HINTS = ('姓名', '名称', '地址', '备注', '说明', '家庭住址')

_DATE_FORMATS = ('%Y-%m-%d', '%Y/%m/%d', '%Y.%m.%d', '%Y%m%d', '%Y年%m月%d日',
                 '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S')

MAX_ROWS = 200000
MAX_FIELDS = 200


class SoftTableError(Exception):
    """引擎层业务错误（路由统一转 400，不透出内部细节）。"""


# ---------------------------------------------------------------- 代码/标识

def validate_table_code(code: str) -> str:
    """表英文标识白名单（API-06 同源口径）：小写字母开头，仅 [a-z0-9_]。"""
    code = (code or '').strip()
    if not TABLE_CODE_RE.match(code):
        raise SoftTableError('表标识只能用「小写字母开头 + 小写字母/数字/下划线」，最长 60 位')
    return code


def validate_field_code(code: str) -> str:
    code = (code or '').strip()
    if not FIELD_CODE_RE.match(code):
        raise SoftTableError('字段标识只能用「小写字母/数字/下划线」，最长 60 位')
    return code


def next_field_code(existing: Iterable[str]) -> str:
    """f00 起稳定自动序号（ADR-025 决策 3：不做拼音、不需用户确认）。"""
    used = {c for c in existing if c}
    n = 0
    while True:
        code = 'f%02d' % n
        if code not in used:
            return code
        n += 1


# ---------------------------------------------------------------- 解析

def parse_table_file(filename: str, stream) -> Tuple[List[str], List[List[Any]]]:
    """解析 .xlsx / .csv，返回 (表头, 数据行)。表头去空白、去空列。"""
    name = (filename or '').lower()
    if name.endswith('.xlsx') or name.endswith('.xlsm'):
        headers, rows = _parse_xlsx(stream)
    elif name.endswith('.csv'):
        headers, rows = _parse_csv(stream)
    else:
        raise SoftTableError('只支持 .xlsx / .csv 文件')
    if not headers:
        raise SoftTableError('未读到表头，请确认第一行为字段名')
    if len(headers) > MAX_FIELDS:
        raise SoftTableError('字段数超过上限 %d' % MAX_FIELDS)
    if len(rows) > MAX_ROWS:
        raise SoftTableError('数据行数超过上限 %d' % MAX_ROWS)
    return headers, rows


def _parse_xlsx(stream):
    from openpyxl import load_workbook
    try:
        wb = load_workbook(filename=stream, read_only=True, data_only=True)
    except Exception:
        raise SoftTableError('Excel 解析失败：文件可能损坏或不是 .xlsx')
    ws = wb.active
    rows = []
    headers = []
    for idx, raw in enumerate(ws.iter_rows(values_only=True)):
        cells = list(raw or ())
        if idx == 0:
            headers = ['' if c is None else str(c).strip() for c in cells]
            continue
        if all(c is None or str(c).strip() == '' for c in cells):
            continue  # 跳过全空行
        rows.append(cells)
    wb.close()
    return _trim_columns(headers, rows)


def _parse_csv(stream):
    data = stream.read() if hasattr(stream, 'read') else stream
    if isinstance(data, bytes):
        for enc in ('utf-8-sig', 'utf-8', 'gbk'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise SoftTableError('CSV 解码失败：请另存为 UTF-8 或 GBK 编码')
    else:
        text = data
    reader = list(csv.reader(io.StringIO(text)))
    if not reader:
        return [], []
    headers = [h.strip() for h in reader[0]]
    rows = [r for r in reader[1:] if any((c or '').strip() for c in r)]
    return _trim_columns(headers, rows)


def _trim_columns(headers, rows):
    """去掉完全空白的尾部列，并按表头长度对齐每行。"""
    width = len(headers)
    while width and not headers[width - 1]:
        width -= 1
    headers = headers[:width]
    norm_rows = []
    for r in rows:
        r = list(r)[:width]
        r += [None] * (width - len(r))
        norm_rows.append(r)
    return headers, norm_rows


# ---------------------------------------------------------------- 推断

def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == '')


def _as_text(v) -> str:
    if isinstance(v, _dt.datetime):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, _dt.date):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return '' if v is None else str(v).strip()


def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    s = _as_text(v).replace(',', '')
    if not s:
        return False
    return bool(re.match(r'^-?\d+(\.\d+)?$', s))


def _try_date(v) -> bool:
    if isinstance(v, (_dt.datetime, _dt.date)):
        return True
    s = _as_text(v)
    for fmt in _DATE_FORMATS:
        try:
            _dt.datetime.strptime(s, fmt)
            return True
        except ValueError:
            continue
    return False


def _looks_yyyymm(v) -> bool:
    s = _as_text(v)
    if not re.match(r'^\d{6}$', s):
        return False
    year, month = int(s[:4]), int(s[4:])
    return 1900 <= year <= 2100 and 1 <= month <= 12


def _looks_code(vals: Sequence[str]) -> bool:
    """字母+数字编号模式（RY001 / A-1024 / ZK2026001）。"""
    pat = re.compile(r'^[A-Za-z]{1,6}[-_]?\d{2,}$')
    hits = sum(1 for v in vals if pat.match(v))
    return bool(vals) and hits == len(vals)


def infer_sensitivity(header: str) -> str:
    """敏感级别自动命中（可手动调整）。

    「缴费期号」类字段名同时含「缴费」与「期号」——期号/编号/序号优先排除，
    否则会被误判为 amount（README §七 养老复跑实测坑，本函数即其修正）。
    """
    h = (header or '').strip()
    if any(k in h for k in _IDENTITY_HINTS):
        return 'identity'
    if any(k in h for k in _DONT_AMOUNT):
        return 'none'
    if any(k in h for k in _AMOUNT_HINTS):
        return 'amount'
    return 'none'


def infer_column(header: str, values: Sequence[Any]) -> Tuple[str, str]:
    """单列类型推断，返回 (data_type, hint)。hint 为空表示无需人工额外处理。

    4 条修正来自技术验证实测（docs/verification/softtable-spike/README.md §五）：
      ① 纯数字且长度 ≥15 → text（防精度丢失/前导零）
      ② 字段名含「姓名」→ text
      ③ 字母+数字编号模式 → text
      ④ 6 位疑似年月（YYYYMM）→ 默认 text 并提示确认
    另：期号/编号类字段名不参与 amount 敏感性判定（见 infer_sensitivity）。
    """
    vals = [_as_text(v) for v in values if not _is_blank(v)]
    # 表头可能含全角空格（「姓　名」），匹配前压缩空白，展示名保持原样
    h = re.sub(r'\s+', '', header or '')
    if not vals:
        return 'text', '空列，默认文本'
    if any(k in h for k in _TEXT_HINTS):
        return 'text', '姓名/名称/地址类字段强制文本，避免被误判为枚举'
    if any(k in h for k in _IDENTITY_HINTS):
        return 'text', '证件号类字段按文本存（并自动归为 identity 加密）'

    if all(_is_number(v) for v in vals):
        if any(len(re.sub(r'\D', '', v)) >= 15 for v in vals):
            return 'text', '纯数字且长度≥15，按文本存（防精度/前导零丢失）'
        if any(_looks_yyyymm(v) for v in vals):
            return 'text', '疑似 6 位年月期号，默认文本，请确认'
        return 'number', ''

    if _looks_code(vals):
        return 'text', '字母+数字编号模式，按文本存'

    if all(_try_date(v) for v in vals):
        return 'date', ''

    distinct = len(set(vals))
    if distinct <= 10 and max(len(v) for v in vals) <= 20 and len(vals) >= distinct:
        return 'enum', '疑似枚举，需选择对应字典分类后可建枚举列'
    return 'text', ''


def suggest_fields(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    """给出建表建议（**仅建议**，必须经人工确认页才能建表——验收 3）。"""
    out = []
    for i, h in enumerate(headers):
        col = [r[i] if i < len(r) else None for r in rows]
        dtype, hint = infer_column(h, col)
        out.append({
            'code': 'f%02d' % i,
            'display_name': (h or ('列%d' % (i + 1))).strip(),
            'data_type': dtype,
            'dict_category_code': None,
            'sensitivity': infer_sensitivity(h),
            'is_required': False,
            'sort_order': i,
            'hint': hint,
        })
    return out


# ---------------------------------------------------------------- 字段规格

def _enum_items(category: str) -> Tuple[Tuple[str, str], ...]:
    """枚举列的唯一可用条目源：(label, value)，仅启用项并保持 sort_order。

    分类缺失/全停用/无 app 上下文 → 空元组（调用方必须报错，不得跳过）。
    """
    if not category:
        return ()
    try:
        from utils.helpers import get_dict_items
        return tuple((i.label, i.value) for i in get_dict_items(category))
    except RuntimeError:
        return ()


def normalize_fields(raw: Any) -> List[Dict[str, Any]]:
    """校验并归一「用户确认后」的字段规格。任何非法项 → SoftTableError。"""
    if not isinstance(raw, list) or not raw:
        raise SoftTableError('至少需要一个字段')
    if len(raw) > MAX_FIELDS:
        raise SoftTableError('字段数超过上限 %d' % MAX_FIELDS)
    fields, seen = [], set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SoftTableError('字段规格格式错误')
        code = validate_field_code(item.get('code'))
        if code in seen:
            raise SoftTableError('字段标识重复：%s' % code)
        seen.add(code)
        name = (item.get('display_name') or '').strip()
        if not name:
            raise SoftTableError('第 %d 列缺少列名' % (i + 1))
        dtype = (item.get('data_type') or 'text').strip()
        if dtype not in DATA_TYPES:
            raise SoftTableError('未知字段类型：%s' % dtype)
        sens = (item.get('sensitivity') or 'none').strip()
        if sens not in SENSITIVITY_LEVELS:
            raise SoftTableError('未知敏感级别：%s' % sens)
        category = (item.get('dict_category_code') or '').strip() or None
        if dtype == 'enum':
            # 单一真源：enum 必须落在真实存在的 dict_items 分类上，不收自由文本。
            # 注意不能用 get_enum 判存在性——它在分类缺失时会回退 ENUM_SEEDS（幽灵分类）。
            if not category:
                raise SoftTableError('枚举字段「%s」必须选择字典分类，或改为文本类型' % name)
            # 口径与 coerce_value 统一：分类缺失或条目全停用都 fail-closed，文案同源。
            if not _enum_items(category):
                raise SoftTableError('字典分类「%s」不存在或没有可用选项' % category)
        fields.append({
            'code': code,
            'display_name': name[:100],
            'data_type': dtype,
            'dict_category_code': category,
            'sensitivity': sens,
            'is_required': bool(item.get('is_required')),
            'sort_order': i,
        })
    return fields


# ---------------------------------------------------------------- 加密/指纹

def derive_hmac_key(key_bytes: bytes) -> bytes:
    """由 KeyHolder 密钥 HKDF 派生 HMAC 专用子密钥（与 Fernet 用途分离）。"""
    return hmac.new(_HMAC_SALT, key_bytes + _HMAC_INFO, hashlib.sha256).digest()


def normalize_identity(value: str) -> str:
    """证件号归一：去空格、统一大写（仅用于指纹与加密前原文）。"""
    return re.sub(r'\s+', '', str(value or '')).upper()


def identity_digest(hmac_key: bytes, field_code: str, value: str) -> str:
    """HMAC-SHA256 + domain separation：不同字段指纹互不可关联。"""
    msg = ('%s\0%s' % (field_code, normalize_identity(value))).encode('utf-8')
    return hmac.new(hmac_key, msg, hashlib.sha256).hexdigest()


def mask_identity(value: str) -> str:
    """打码：保留前 3 后 4（设计 §五 示例 110***********1234）。"""
    s = str(value or '')
    if len(s) <= 7:
        return '*' * len(s)
    return s[:3] + '*' * (len(s) - 7) + s[-4:]


# ---------------------------------------------------------------- 行转换

def coerce_value(field: Dict[str, Any], raw: Any) -> Any:
    """按字段类型归一单元格值；非法 → SoftTableError（调用方收集为行级错误）。"""
    dtype = field['data_type']
    if _is_blank(raw):
        return None
    if dtype == 'number':
        s = _as_text(raw).replace(',', '')
        try:
            val = float(s)
        except ValueError:
            raise SoftTableError('「%s」不是数字' % field['display_name'])
        return int(val) if val.is_integer() else val
    if dtype == 'date':
        if isinstance(raw, _dt.datetime):
            return raw.strftime('%Y-%m-%d')
        if isinstance(raw, _dt.date):
            return raw.strftime('%Y-%m-%d')
        s = _as_text(raw)
        for fmt in _DATE_FORMATS:
            try:
                return _dt.datetime.strptime(s, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        raise SoftTableError('「%s」不是日期' % field['display_name'])
    if dtype == 'enum':
        cat = field.get('dict_category_code')
        items = _enum_items(cat)
        if not items:
            # fail-closed：分类不存在或为空时，绝不收自由文本
            raise SoftTableError('字典分类「%s」不存在或没有可用选项' % (cat or '（未指定）'))
        raw_text = _as_text(raw)
        # 归一口径：优先按 label 匹配（Excel 补录常给展示名），其次按 value 匹配
        # （roster.js 表单 select 提交 option value）。085 缴费项目 value≠label
        # （clean_fee）此前未暴露该双口径——label-only 字典两路等价，行为不变。
        value = next((v for lb, v in items if lb == raw_text), None)
        if value is None:
            value = next((v for lb, v in items if v == raw_text), None)
        if value is None:
            raise SoftTableError('「%s」不在字典分类 %s 的取值内' % (raw_text, cat))
        return value
    return _as_text(raw)


def build_records(fields: Sequence[Dict[str, Any]], rows: Sequence[Sequence[Any]],
                  encrypt=None, hmac_key=None) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    """逐行转换 → 返回 (可入库记录, 行级错误)。identity 字段加密并写指纹。

    encrypt: 可注入的加密函数（str->str），默认取进程内 key_holder.encrypt。
    """
    needs_crypto = any(f.get('sensitivity') == 'identity' for f in fields)
    if needs_crypto:
        from utils.crypto import key_holder
        if hmac_key is None:
            if not key_holder.is_initialized:
                raise SoftTableError('密钥未解锁，无法写入身份标识字段；请重新登录后再导入')
            hmac_key = derive_hmac_key(key_holder.get_key_bytes())
        if encrypt is None:
            encrypt = key_holder.encrypt
    else:
        hmac_key = None

    records, errors = [], []
    for idx, row in enumerate(rows):
        data, digest, failed = {}, {}, False
        for i, field in enumerate(fields):
            raw = row[i] if i < len(row) else None
            try:
                value = coerce_value(field, raw)
            except SoftTableError as exc:
                errors.append({'row': idx + 1, 'field': field['display_name'], 'msg': str(exc)})
                failed = True
                continue
            if value is None:
                if field.get('is_required'):
                    errors.append({'row': idx + 1, 'field': field['display_name'],
                                   'msg': '必填项为空'})
                    failed = True
                continue
            if field['sensitivity'] == 'identity':
                digest[field['code']] = identity_digest(hmac_key, field['code'], str(value))
                data[field['code']] = encrypt(str(value))
            else:
                data[field['code']] = value
        if not failed:
            records.append({
                'data': json.dumps(data, ensure_ascii=False, sort_keys=True),
                'digest': json.dumps(digest, ensure_ascii=False, sort_keys=True) if digest else None,
            })
    return records, errors


def decode_records(fields: Sequence[Dict[str, Any]], records: Sequence[Any],
                   reveal: bool = False, decrypt=None) -> List[Dict[str, Any]]:
    """反序列化 + 解密/打码。identity 默认打码，reveal=True 才解密明文。"""
    if decrypt is None:
        from utils.crypto import key_holder
        decrypt = key_holder.decrypt
    out = []
    for rec in records:
        try:
            data = json.loads(rec.data or '{}')
        except ValueError:
            data = {}
        try:
            digest = json.loads(rec.digest or '{}')
        except ValueError:
            digest = {}
        row = {'id': rec.id, '__digest__': digest, 'created_by': rec.created_by,
               'created_at': rec.created_at.strftime('%Y-%m-%d %H:%M') if rec.created_at else '',
               'updated_at': rec.updated_at.strftime('%Y-%m-%d %H:%M') if rec.updated_at else ''}
        for field in fields:
            code = field['code']
            value = data.get(code)
            if value is None:
                row[code] = ''
                continue
            if field['sensitivity'] == 'identity':
                if reveal:
                    try:
                        row[code] = decrypt(str(value))
                    except Exception:
                        row[code] = '（无法解密）'
                else:
                    row[code] = mask_identity(_safe_decrypt_for_mask(decrypt, value))
            else:
                row[code] = value
        out.append(row)
    return out


def _safe_decrypt_for_mask(decrypt, value) -> str:
    """打码需要长度信息，故仍需解密；失败时退化为「密文前缀打码」。"""
    try:
        return decrypt(str(value))
    except Exception:
        return '*' * 12


# ---------------------------------------------------------------- 筛选/排序

def select_rows(rows: Sequence[Dict[str, Any]], fields: Sequence[Dict[str, Any]],
                filters: Dict[str, Any] = None, sort_field: str = None,
                sort_dir: str = 'asc',
                digest_fn=None) -> List[Dict[str, Any]]:
    """内存筛选 + 排序（设计 §六）。

    filters 支持三种形状（按字段敏感级别自动选择语义）：
        等值/包含：{'f01': '张三'}
        区间：    {'f09': {'min': 100, 'max': 2000}}
    identity 字段只能等值（密码学约束，不可范围/模糊/排序）。
    """
    spec = {f['code']: f for f in fields}
    result = list(rows)
    for code, cond in (filters or {}).items():
        field = spec.get(code)
        if not field or cond in (None, ''):
            continue
        ident = field['sensitivity'] == 'identity'
        if isinstance(cond, dict):
            lo, hi = cond.get('min'), cond.get('max')
            def _in_range(row, lo=lo, hi=hi, code=code):
                v = row.get(code)
                if v in (None, ''):
                    return False
                try:
                    num = float(str(v).replace(',', ''))
                except ValueError:
                    return str(lo or '') <= str(v) <= str(hi or '')
                if lo not in (None, '') and num < float(lo):
                    return False
                if hi not in (None, '') and num > float(hi):
                    return False
                return True
            result = [r for r in result if _in_range(r)]
        elif ident:
            # 打码值不可比：identity 一律走指纹等值（digest_fn 由调用方按当前密钥注入）
            needle = str(cond).strip()
            target = (digest_fn(code, needle) if digest_fn else '').upper()
            result = [r for r in result
                      if target and str((r.get('__digest__') or {}).get(code, '')).upper() == target]
        else:
            needle = str(cond).strip().lower()
            result = [r for r in result if needle in str(r.get(code, '')).lower()]

    if sort_field and sort_field in spec and spec[sort_field]['sensitivity'] != 'identity':
        reverse = str(sort_dir).lower() == 'desc'
        def _key(row):
            v = row.get(sort_field)
            try:
                return (0, float(str(v).replace(',', '')), '')
            except (TypeError, ValueError):
                return (1, 0.0, str(v or ''))
        result = sorted(result, key=_key, reverse=reverse)
    return result


def dict_categories_for_enum() -> List[Dict[str, str]]:
    """枚举字段可选的字典分类（单一真源：dict_categories，不落副本）。"""
    from models import DictCategory
    rows = DictCategory.query.order_by(DictCategory.sort_order.asc(),
                                       DictCategory.id.asc()).all()
    # dict_categories 无 code 列（唯一键是 name），故 dict_category_code 存分类名
    return [{'code': c.name, 'name': c.name} for c in rows]


# ---------------------------------------------------------------- 透视（085 展示层变换）

def _pivot_decimal(value):
    """单元格值 → Decimal（金额两位内精度）；空值/非数值返回 None（跳过该格，不计 0）。"""
    text = str(value).replace(',', '').strip() if value is not None else ''
    if not text:
        return None
    try:
        dec = Decimal(text)
    except Exception:
        return None
    # NaN/Infinity 虽可被 Decimal 解析，但不可参与金额求和/格式化 → 按非数值跳过
    return dec if dec.is_finite() else None


def pivot_rows(rows: Sequence[Dict[str, Any]], row_key: str, col_key: str,
               value_key: str) -> Dict[str, Any]:
    """085 年度缴费透视：把「场所 × 项目 × 年度」纵向明细变换成「行 × 列」矩阵。

    纯函数（展示层变换，不改存储、不生成物理列——ADR-025 决策 3 / 085 验收 6）：
    - 行 = row_key 去重，保持首次出现顺序（调用方预先按场所排好序即场所序）；
    - 列 = col_key 去重升序（年度数值列自动出现，跨年无需改代码——验收 3）；
    - 单元格 = 该行该列 value_key 求和（Decimal；空值/非数值不计入——一页纸 V-05）；
    - col_totals = 列小计（= 该列全部明细求和），grand_total = 全明细求和。

    返回 {'row_keys': [...], 'col_keys': [...], 'cells': {row: {col: Decimal}},
          'col_totals': {col: Decimal}, 'grand_total': Decimal}；空明细 → 全空结构。
    """
    row_keys: List[Any] = []
    seen_rows = set()
    col_keys: List[Any] = []
    seen_cols = set()
    cells: Dict[Any, Dict[Any, Decimal]] = {}
    grand_total = Decimal(0)

    for row in rows or []:
        rk = row.get(row_key)
        ck = row.get(col_key)
        if rk is None or rk == '' or ck is None or ck == '':
            continue
        amount = _pivot_decimal(row.get(value_key))
        if amount is None:
            # R-085-05：空值/非数值跳过整格——不创建行列、不计 0（与「不计入」口径一致）
            continue
        if rk not in seen_rows:
            seen_rows.add(rk)
            row_keys.append(rk)
            cells[rk] = {}
        if ck not in seen_cols:
            seen_cols.add(ck)
            col_keys.append(ck)
        cells[rk][ck] = cells[rk].get(ck, Decimal(0)) + amount
        grand_total += amount

    def _col_sort_key(value):
        """列升序：数值列按数值（年度 2024 < 2025），非数值列按字符串接队尾。"""
        try:
            return (0, Decimal(str(value)), '')
        except Exception:
            return (1, Decimal(0), str(value))

    col_keys.sort(key=_col_sort_key)
    col_totals = {ck: sum((cells.get(rk, {}).get(ck, Decimal(0)) for rk in row_keys),
                          Decimal(0)) for ck in col_keys}
    return {'row_keys': row_keys, 'col_keys': col_keys, 'cells': cells,
            'col_totals': col_totals, 'grand_total': grand_total}
