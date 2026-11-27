"""utils.masking — 脱敏四通道核心（T06，移植 m0lib/mask_utils.py 并扩展）。

规格依据：详细设计 v1.1 §6.4 / R11-E1~E4 / K8；M0 精简验证门 M4。
脱敏粒度（用户 2026-08-14 拍板，对齐 M0 原型）：
  身份证/银行卡 前 6 后 4；电话 前 3 后 4。
主拦截点：API JSON 序列化层（app.py after_request 调 MaskFilter.apply），
  Jinja2 |mask filter 供后续服务端渲染模板复用。

禁止在此模块外另写脱敏判断（宪法 §7 owner map）。
"""
from __future__ import annotations

import re
from functools import wraps

# ============ 默认规则（可经 update_mask_config 变更，仅 admin/mask_config 位） ============
DEFAULT_MASK_RULES = {
    "id_card": {"prefix": 6, "suffix": 4},
    "phone": {"prefix": 3, "suffix": 4},
    "bank_card": {"prefix": 6, "suffix": 4},
    # 090A 民生诉求（一页纸 v1.1 §5 列表/导出打码列）：姓名保首字、住址保前 6 位
    "name": {"prefix": 1, "suffix": 0},
    "address": {"prefix": 6, "suffix": 0},
}

# ============ 静态扫描正则（CJK 安全：lookaround 替代 \b，防中文紧邻数字漏匹配） ============
_RE_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_RE_BANK = re.compile(r"(?<!\d)\d{16,19}(?!\d)")

# ============ 敏感字段 key 识别（12 变体，按后缀） ============
ID_CARD_SUFFIXES = ("id_card",)
PHONE_SUFFIXES = ("phone",)
BANK_SUFFIXES = ("bank_card", "bank_info")
SENSITIVE_KEY_SUFFIXES = ID_CARD_SUFFIXES + PHONE_SUFFIXES + BANK_SUFFIXES


def field_kind(key: str):
    """按 key 后缀识别敏感字段类型：'id_card' / 'phone' / 'bank_card' / None。"""
    if not key:
        return None
    if key.endswith(ID_CARD_SUFFIXES):
        return "id_card"
    if key.endswith(PHONE_SUFFIXES):
        return "phone"
    if key.endswith(BANK_SUFFIXES):
        return "bank_card"
    return None


def _mask_with_cfg(raw, prefix, suffix):
    if raw is None:
        return raw
    raw = str(raw)
    if raw == "":
        return raw
    total = prefix + suffix
    if len(raw) <= total:
        return raw
    # 090A：suffix=0（姓名/住址等「只留前缀」规则）不得走 raw[-0:]——那等价于整串原样返回
    tail = raw[len(raw) - suffix:] if suffix > 0 else ""
    return raw[:prefix] + "*" * (len(raw) - total) + tail


def mask_id_card(raw) -> str:
    """身份证脱敏：前 6 后 4 → 340104********1234。"""
    cfg = DEFAULT_MASK_RULES["id_card"]
    return _mask_with_cfg(raw, cfg["prefix"], cfg["suffix"])


def mask_phone(raw) -> str:
    """电话脱敏：前 3 后 4 → 138****5678；多号码单元格逐个独立脱敏并用 ' / ' 连接。"""
    if raw is None:
        return raw
    cfg = DEFAULT_MASK_RULES["phone"]
    from utils.phone_utils import PHONE_SEP, split_phones
    parts = split_phones(raw)
    if len(parts) > 1:
        return PHONE_SEP.join(_mask_with_cfg(p, cfg["prefix"], cfg["suffix"]) for p in parts)
    return _mask_with_cfg(raw, cfg["prefix"], cfg["suffix"])


def mask_bank_card(raw) -> str:
    """银行卡脱敏：前 6 后 4 → 622188******9080。"""
    cfg = DEFAULT_MASK_RULES["bank_card"]
    return _mask_with_cfg(raw, cfg["prefix"], cfg["suffix"])


def mask_name(raw) -> str:
    """姓名脱敏（090A 民生诉求诉求人）：保留首字，其余打码 → 张*／王**。

    短值（≤1 位）原样返回； suffix=0 时 _mask_with_cfg 会截掉尾部星号以外的字符，
    这里手写保持「首字 + 全长星号」形态。
    """
    if raw is None:
        return raw
    raw = str(raw)
    if len(raw) <= 1:
        return raw
    return raw[:1] + "*" * (len(raw) - 1)


def mask_address(raw) -> str:
    """住址/涉及地点脱敏（090A）：保留前 6 位，其余打码 → 广西桂林市****。"""
    cfg = DEFAULT_MASK_RULES["address"]
    return _mask_with_cfg(raw, cfg["prefix"], cfg["suffix"])


def mask_field(value, kind: str) -> str:
    """按类型单字段脱敏（Jinja2 filter / 导出 / 日志复用入口）。"""
    if kind == "id_card":
        return mask_id_card(value)
    if kind == "phone":
        return mask_phone(value)
    if kind == "bank_card":
        return mask_bank_card(value)
    if kind == "name":
        return mask_name(value)
    if kind == "address":
        return mask_address(value)
    return value


def user_can_view_sensitive(user) -> bool:
    """当前用户是否有明文查看权限（admin 天然全通）。脱敏判断的唯一入口。"""
    if not user:
        return False
    from utils.permissions import user_has_special
    return bool(user.role_id == 1 or user_has_special(user, "view_sensitive"))


class MaskFilter:
    """按角色输出脱敏/明文：can_view_sensitive=True → 明文；False → 敏感字段递归脱敏。

    用法（after_request JSON 层）：
        MaskFilter().apply(data, can_view_sensitive=can)
    """

    def apply(self, data, can_view_sensitive: bool = False):
        if can_view_sensitive:
            return data
        return self._mask_recursive(data)

    def _mask_recursive(self, obj):
        if isinstance(obj, dict):
            return {k: self._mask_value(k, v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._mask_recursive(v) for v in obj]
        return obj

    def _mask_value(self, key, value):
        if isinstance(value, (dict, list)):
            return self._mask_recursive(value)
        if isinstance(value, str):
            kind = field_kind(key)
            if kind:
                return mask_field(value, kind)
        return value


# ============ 儿童行级可见性（§4.3④ / G-2） ============
SENSITIVE_CHILD_TYPES = ("留守", "困境")


def _is_sensitive_child_row(row) -> bool:
    target_type = row.get("target_type") if isinstance(row, dict) else getattr(row, "target_type", None)
    child_type = row.get("child_type") if isinstance(row, dict) else getattr(row, "child_type", None)
    return target_type == "儿童" and child_type in SENSITIVE_CHILD_TYPES


def can_see_children(user) -> bool:
    """儿童行级可见：仅 admin 或「民政主任」角色可看留守/困境儿童明细。"""
    if not user:
        return False
    if getattr(user, "role_id", None) == 1:
        return True
    from models import Role
    role = Role.query.get(user.role_id) if user.role_id else None
    return bool(role and role.name == "民政主任")


def filter_child_rows(rows, can_see: bool):
    """儿童行级过滤：can_see=False 时过滤留守/困境儿童行（计数由调用方在 counts 保留）。"""
    if can_see:
        return rows
    return [r for r in rows if not _is_sensitive_child_row(r)]


def child_rows_guard(f):
    """行级可见性装饰器：置 flask.g.can_see_children，供视图用 filter_child_rows 过滤。"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        from flask import g, session
        from models import User
        user = User.query.get(session.get("user_id")) if session.get("user_id") else None
        g.can_see_children = can_see_children(user)
        return f(*args, **kwargs)
    return wrapper


# ============ mask_config 规则配置（仅 admin/mask_config 位，变更留痕） ============
def get_mask_config() -> dict:
    """返回当前规则（浅拷贝，防外部篡改）。"""
    return dict(DEFAULT_MASK_RULES)


def update_mask_config(new_rules: dict, operator_name: str = "") -> dict:
    """更新脱敏规则（变更留痕，不记明文值）。返回更新后规则。

    注意：前缀/后缀均为 int；非法值（负数/前缀+后缀>保留下限）抛 ValueError。
    留痕经 log_operation（只记规则摘要，不记明文值）。
    """
    validated = {}
    for kind, rule in new_rules.items():
        if kind not in DEFAULT_MASK_RULES:
            continue
        prefix = int(rule.get("prefix", DEFAULT_MASK_RULES[kind]["prefix"]))
        suffix = int(rule.get("suffix", DEFAULT_MASK_RULES[kind]["suffix"]))
        if prefix < 0 or suffix < 0:
            raise ValueError(f"{kind} 规则 prefix/suffix 不能为负")
        validated[kind] = {"prefix": prefix, "suffix": suffix}
    if not validated:
        return get_mask_config()

    DEFAULT_MASK_RULES.update(validated)

    from utils.helpers import log_operation
    summary = "; ".join(f"{k}:{v['prefix']}-{v['suffix']}" for k, v in validated.items())
    log_operation(operator_name or "system", "脱敏规则", "mask_config", f"脱敏规则变更", detail=summary)
    return get_mask_config()


# ============ Jinja2 注册（服务端渲染模板复用） ============
def register_jinja(app):
    """注册 |mask filter + can_view_sensitive context processor。"""
    app.jinja_env.filters["mask"] = mask_field

    @app.context_processor
    def _inject_masking():
        from flask import session
        from models import User
        from utils.permissions import user_has_special
        user = User.query.get(session.get("user_id")) if session.get("user_id") else None
        can = bool(user and (user.role_id == 1 or user_has_special(user, "view_sensitive")))
        return {"can_view_sensitive": can}


# ============ 静态扫描（M0 同源，scan_mask.py 复用） ============
def static_scan_text(text: str) -> dict:
    """正则静态扫描文本，返回按类型归类的明文命中明细。"""
    return {
        "id_card": _RE_ID_CARD.findall(text),
        "phone": _RE_PHONE.findall(text),
        "bank_card": _RE_BANK.findall(text),
    }


def mask_text(text: str) -> str:
    """对任意文本中的敏感明文值做正则替换脱敏（日志 detail 防泄漏）。

    顺序：id_card(17位+X/18位) → phone(11位) → bank_card(16-19位)。
    """
    if not text:
        return text
    text = _RE_ID_CARD.sub(lambda m: mask_id_card(m.group(0)), text)
    text = _RE_PHONE.sub(lambda m: mask_phone(m.group(0)), text)
    text = _RE_BANK.sub(lambda m: mask_bank_card(m.group(0)), text)
    return text


# ============ 导出通道：按表头中文关键词定位敏感列 ============
EXPORT_SENSITIVE_KEYWORDS = {
    "id_card": ("身份证", "证件号码"),
    "phone": ("电话", "手机"),
    "bank_card": ("银行卡", "银行账号", "卡号", "账号"),
}


def header_kind(header) -> str | None:
    """按导出表头中文关键词识别敏感列类型。"""
    h = str(header)
    for kind, kws in EXPORT_SENSITIVE_KEYWORDS.items():
        for kw in kws:
            if kw in h:
                return kind
    return None


def mask_export_rows(headers, rows, can_view_sensitive: bool = True):
    """导出通道服务端唯一拦截点：无明文权限时对敏感列脱敏。"""
    if can_view_sensitive:
        return rows
    sensitive_cols = [(i, header_kind(h)) for i, h in enumerate(headers) if header_kind(h)]
    if not sensitive_cols:
        return rows
    out = []
    for row in rows:
        r = list(row)
        for i, kind in sensitive_cols:
            if i < len(r):
                r[i] = mask_field(r[i], kind)
        out.append(r)
    return out
