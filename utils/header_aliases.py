# -*- coding: utf-8 -*-
"""utils.header_aliases — 核心物理表批量导入表头别名自动匹配（补丁 C-3）。

与居民主档 utils/import_engine.py FIELD_CATALOG 口径对齐，
让低保、残疾人、特困/困境儿童/关爱对象、党员、退役军人、公益岗、店铺等核心物理表
在保留原有零迁移架构的前提下，自动兼容外部下发 Excel 的常见列名变体、星号/必填后缀与换行符。
"""
import re
from typing import Dict, Iterable, List, Optional, Sequence

_CLEAN_SUFFIX_RE = re.compile(r'[\s*＊]+|[（(]\s*(?:必填|选填|可空|自动|系统)\s*[)）]')


def clean_header_text(raw: object) -> str:
    """清洗单个表头：去换行、去首尾空白、去常见星号/（必填）标注。"""
    s = '' if raw is None else str(raw)
    s = re.sub(r'[\r\n\t]+', '', s).strip()
    if not s:
        return ''
    cleaned = _CLEAN_SUFFIX_RE.sub('', s).strip()
    return cleaned or s


# 通用跨模块别名表：canonical_label -> (候选别名...)
# 注意：精确命中永远优先于别名命中；且同表内已被其他规范列占用的列名不会被误抢。
COMMON_ALIASES: Dict[str, Sequence[str]] = {
    '姓名': ('居民姓名', '本人姓名', '人员姓名', '对象姓名', '党员姓名', '成员姓名', '申请人姓名'),
    '身份证号': ('身份证号码', '身份证', '公民身份号码', '证件号码', '证件号'),
    '身份证号码': ('身份证号', '身份证', '公民身份号码', '证件号码', '证件号'),
    '性别': ('人员性别',),
    '民族': ('族别',),
    '出生日期': ('出生年月', '生日'),
    '联系电话': ('手机号码', '手机号', '电话', '电话号码', '联系方式', '本人联系方式', '联系手机'),
    '手机号码': ('手机号', '联系电话', '电话', '电话号码', '联系方式', '本人联系方式'),
    '家庭住址': ('家庭详细地址', '家庭地址', '现居住地址', '居住地址', '详细地址', '住址', '地址'),
    '所在组': ('村组', '村民小组', '小组', '所属组', '所属村组'),
    '所在网格': ('网格', '所属网格'),
    '开户行': ('开户银行', '发卡行', '银行名称'),
    '银行卡号': ('银行账号', '账号', '卡号', '一卡通账号', '社保卡号'),
    '备注': ('说明', '备注说明', '其他说明'),
}

# 各核心物理表专属别名（仅在对应 domain 下生效，避免跨语义误匹配）
DOMAIN_ALIASES: Dict[str, Dict[str, Sequence[str]]] = {
    'dibao': {
        '成员姓名': ('姓名', '居民姓名', '对象姓名', '人员姓名', '低保对象姓名', '申请人姓名'),
        '户主姓名': ('户主', '户主名'),
        '户主身份证号': ('户主身份证号码', '户主身份证', '户主证件号'),
        '身份证号': ('成员身份证号', '成员身份证号码', '身份证号码', '身份证', '公民身份号码', '证件号码'),
        '月保障金额': ('月补助金额', '保障金额', '低保金额', '月发放金额', '金额'),
        '保障人数': ('享受人数', '低保人数', '人口数'),
        '保障类型': ('低保类型', '低保类别', '保障类别', '类别'),
        '保障状态': ('在保状态', '状态'),
        '审批日期': ('纳入日期', '认定日期', '批准日期'),
    },
    'disabled': {
        '姓名': ('残疾人姓名', '居民姓名', '人员姓名', '对象姓名'),
        '残疾证号': ('残疾人证号', '证件编号', '证号'),
        '残疾类别': ('残疾类型', '类别'),
        '残疾等级': ('等级', '伤残等级'),
        '发证日期': ('办证日期', '领证日期'),
    },
    'care_special': {
        '户主姓名': ('户主', '特困人员姓名', '姓名', '对象姓名'),
        '户主身份证号': ('户主身份证号码', '户主身份证', '身份证号', '身份证号码', '公民身份号码'),
        '成员姓名': ('姓名', '对象姓名', '人员姓名'),
        '成员身份证号': ('身份证号', '身份证号码', '公民身份号码'),
        '供养方式': ('供养类型', '分散/集中'),
        '照料护理等级': ('护理等级', '自理能力', '失能等级'),
        '月保障金额': ('月补助金额', '供养金', '保障金额', '金额'),
    },
    'care_child': {
        '儿童姓名': ('姓名', '孩子姓名', '困境儿童姓名', '对象姓名'),
        '身份证号码': ('身份证号', '儿童身份证号', '儿童身份证号码', '身份证', '公民身份号码', '证件号码'),
        '监护人姓名': ('监护人', '家长姓名', '抚养人'),
        '监护人电话': ('监护人联系电话', '家长电话', '联系电话', '手机号码', '手机号', '电话'),
        '困境类型': ('儿童类型', '类别'),
        '月保障金额': ('月补助金额', '补助标准', '金额'),
    },
    'care_target': {
        '姓名': ('对象姓名', '关爱对象姓名', '居民姓名', '人员姓名'),
        '关爱类别': ('对象类别', '类别', '类型'),
        '包保人': ('包保责任人', '联系人', '帮扶人'),
    },
    'party': {
        '姓名': ('党员姓名', '居民姓名', '人员姓名'),
        '手机号码': ('手机号', '联系电话', '电话', '电话号码', '联系方式'),
        '入党时间': ('入党日期', '加入党组织日期'),
        '转正时间': ('转正日期',),
        '所在支部': ('党支部', '所属支部', '支部名称'),
        '文化程度': ('学历',),
        '家庭住址': ('现居住地址', '居住地址', '住址', '地址'),
    },
    'veteran': {
        '姓名': ('退役军人姓名', '居民姓名', '人员姓名'),
        '身份证号码': ('身份证号', '身份证', '公民身份号码', '证件号码', '证件号'),
        '本人联系方式': ('联系电话', '手机号码', '手机号', '电话', '联系方式'),
        '家庭详细地址': ('家庭住址', '家庭地址', '现居住地址', '居住地址', '住址', '地址'),
        '文化程度': ('学历',),
        '户口所在地': ('户籍地址', '户籍所在地'),
        '入伍年月': ('入伍时间', '入伍日期'),
        '退役年月': ('退役时间', '退役日期', '退伍年月', '退伍时间'),
        '原服役部队': ('服役部队', '部队番号'),
        '退役类别': ('人员类别', '退伍类别'),
    },
    'welfare_job': {
        '姓名': ('人员姓名', '在岗人员姓名', '居民姓名'),
        '岗位名称': ('岗位', '公益岗位名称', '岗位类型'),
        '上岗时间': ('上岗日期', '入职时间', '聘用日期'),
        '月补贴金额': ('补贴金额', '月工资', '补贴标准', '金额'),
        '在岗状态': ('岗位状态', '状态'),
    },
    'shop': {
        '店铺名称': ('商户名称', '场所名称', '门店名称', '企业名称', '名称'),
        '负责人': ('经营者', '法定代表人', '法人', '老板', '联系人', '姓名'),
        '联系电话': ('手机号', '手机号码', '电话', '联系方式', '负责人电话'),
        '经营地址': ('店铺地址', '场所地址', '详细地址', '地址', '所在位置'),
        '行业类别': ('经营类型', '行业类型', '经营类别', '类别'),
        '统一社会信用代码': ('营业执照号', '信用代码', '注册号'),
    },
}


def _merged_aliases(domain: Optional[str]) -> Dict[str, Sequence[str]]:
    merged: Dict[str, List[str]] = {k: list(v) for k, v in COMMON_ALIASES.items()}
    if domain and domain in DOMAIN_ALIASES:
        for k, vals in DOMAIN_ALIASES[domain].items():
            cur = merged.setdefault(k, [])
            for a in vals:
                if a not in cur:
                    cur.append(a)
    return merged


def build_header_index(headers_list: Iterable[object], domain: Optional[str] = None) -> Dict[str, int]:
    """构建表头名 → 列索引映射，自动兼容换行/星号清洗与常见别名。

    规则：
    1. 原始表头 strip 与清洗版（去换行/去* / 去(必填)）均直接写入；
    2. 对 domain + 通用别名字典中未直接命中的规范列名，按优先级回退匹配其别名；
    3. 精确命中永远不被别名覆盖。
    """
    col: Dict[str, int] = {}
    cleaned_to_idx: Dict[str, int] = {}
    for i, raw in enumerate(headers_list or []):
        if raw is None:
            continue
        raw_s = str(raw).strip()
        if not raw_s:
            continue
        col.setdefault(raw_s, i)
        clean_s = clean_header_text(raw_s)
        if clean_s:
            col.setdefault(clean_s, i)
            cleaned_to_idx.setdefault(clean_s, i)

    for canonical, aliases in _merged_aliases(domain).items():
        if canonical in col:
            continue
        for alias in aliases:
            idx = col.get(alias)
            if idx is None:
                idx = cleaned_to_idx.get(clean_header_text(alias))
            if idx is not None:
                col[canonical] = idx
                break
    return col


def normalize_domain_row(raw_row: dict, domain: Optional[str] = None) -> dict:
    """为异步 domain_batch 的单行 dict 补充清洗键与规范别名键（不破坏原有键）。"""
    if not isinstance(raw_row, dict):
        return {}
    out = dict(raw_row)
    cleaned_vals: Dict[str, object] = {}
    for k, v in raw_row.items():
        if k is None:
            continue
        ks = str(k).strip()
        if ks and ks not in out:
            out[ks] = v
        ck = clean_header_text(ks)
        if ck:
            if ck not in out:
                out[ck] = v
            cleaned_vals.setdefault(ck, v)

    for canonical, aliases in _merged_aliases(domain).items():
        if canonical in out and out[canonical] not in (None, ''):
            continue
        for alias in aliases:
            if alias in out and out[alias] not in (None, ''):
                out[canonical] = out[alias]
                break
            ca = clean_header_text(alias)
            if ca in cleaned_vals and cleaned_vals[ca] not in (None, ''):
                out[canonical] = cleaned_vals[ca]
                break
    return out
