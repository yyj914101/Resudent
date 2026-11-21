#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成批量导入功能测试文件（环境准备用）。

覆盖用户要求的两种字段情况 + 阈值守卫边界：
- 文件A 字段名与系统内置字段不一致：表头走「精确别名 / 模板规则 / difflib 模糊」
  三级匹配，性别列用 M/F/1/2 源码测值域映射（VALUE_DOMAIN_MAP）。
- 文件B 数据字段在系统内置字段中不存在：4 个系统外字段（映射阶段未识别，
  可在步2存为扩展字段 ext_data，导入后登记字段注册中心 draft）。
- 文件C 大面积未知字段：未映射比例 70% > 50%，步2触发阈值守卫 blocked，
  需管理员确认豁免（测试守卫与豁免流）。
- 文件D 字段名与系统内置字段不一致（全新人员，与 A 互补）：表头偏重 difflib
  模糊匹配（6 fuzzy + 4 别名 exact），A/B/C 用过的表头一个不复用。
- 文件E 数据字段在系统内置字段中不存在（全新人员，与 B 互补）：3 个全新系统外
  字段（残疾类别/计生奖扶金额/饮水工程编号），且不给性别列，测引擎按身份证
  第 17 位推断性别（derive_gender）。

身份证号均为合法校验位的虚构号码（序号段与种子数据 103~140 区隔：
A=201~208、B=301~306+手工追加 3066~3068、C=401~404、D=501~506、E=601~605）。
输出：测试数据/批量导入/*.xlsx + 自检（用引擎 build_mapping 复核匹配结果）。

用法：python3 scripts/gen_import_test_files.py [--only d,e]
  --only 只生成指定文件（a/b/c/d/e，逗号分隔）。默认全部重建——注意仓库里
  B 文件第 8~10 行是脚本生成后手工追加的异常边界行（重复姓名+非法短电话），
  重建 b 会覆盖丢失；日常增补新场景请用 --only 指定新字母。
"""
import os
import sys
from datetime import date, datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)

from openpyxl import Workbook

OUT_DIR = os.path.join(BASE_DIR, '测试数据', '批量导入')

# ── 身份证生成（合法校验位，内容虚构） ──
_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_CHECK_CODES = '10X98765432'


def make_id(seq: int, birth: date, male: bool) -> str:
    n = seq | 1 if male else seq & ~1
    body = f'450225{birth:%Y%m%d}{n:03d}'
    s = sum(int(c) * w for c, w in zip(body, _WEIGHTS))
    return body + _CHECK_CODES[s % 11]


def write_xlsx(filename: str, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = '名单'
    ws.append(headers)
    for r in rows:
        ws.append(r)
    path = os.path.join(OUT_DIR, filename)
    wb.save(path)
    return path


# ════════════════════════════════════════════════════════════════
# 文件A：字段名与系统内置字段不一致（三级匹配 + 值域映射）
# ════════════════════════════════════════════════════════════════
# 源表头 → 期望目标 / 匹配层级：
#   居民姓名     → name        exact（别名）
#   公民身份号码 → id_card     exact（别名）
#   手机号码     → phone       template（模板规则）
#   出生年月     → birth_date  template（模板规则）
#   性别         → gender      exact；值 M/F/1/2 → 值域映射 男/女
#   村民小组     → village_group exact（别名）
#   户编号信息   → household_code fuzzy（0.75）
#   家庭居住地址 → address     fuzzy（0.8）
#   户主名       → household_name exact（别名）
#   家庭类型     → household_property exact（别名；值含「脱贫户」→ 派生标签）
#   文化程度     → education   exact（别名）
HEADERS_A = ['居民姓名', '公民身份号码', '手机号码', '出生年月', '性别',
             '村民小组', '户编号信息', '家庭居住地址', '户主名', '家庭类型', '文化程度']
PEOPLE_A = [
    ('韦日新', 201, date(1975, 3, 12), 'M', '思榜', '思榜-101', '韦日新', '一般农户', '初中'),
    ('梁春梅', 202, date(1978, 7, 25), 'F', '思榜', '思榜-101', '韦日新', '一般农户', '小学'),
    ('贾大成', 203, date(1982, 11, 3), '1', '思榜', '思榜-102', '贾大成', '脱贫户', '高中/中专'),
    ('莫晓兰', 204, date(1985, 1, 18), '2', '思榜', '思榜-102', '贾大成', '脱贫户', '初中'),
    ('蓝家旺', 205, date(1966, 9, 9), 'M', '旧寨', '旧寨-101', '蓝家旺', '一般农户', '小学'),
    ('石秀珍', 206, date(1970, 4, 2), 'F', '旧寨', '旧寨-101', '蓝家旺', '监测户', '文盲'),
    ('韦保田', 207, date(1958, 6, 15), 'm', '旧寨', '旧寨-102', '韦保田', '一般农户', '初中'),
    ('吴银花', 208, date(1993, 12, 30), 'f', '旧寨', '旧寨-102', '韦保田', '一般农户', '大专'),
]


def build_rows_a():
    rows = []
    for i, (name, seq, birth, gcode, vg, hcode, hname, prop, edu) in enumerate(PEOPLE_A):
        male = gcode in ('M', 'm', '1')
        id_card = make_id(seq, birth, male)
        if i == 0:
            id_card = id_card[:-1] + 'x'   # 末位小写 x：测大写归一
        # 出生年月混合格式：Excel 日期 / YYYYMMDD 字符串 / YYYY/MM/DD 字符串
        if i % 3 == 0:
            birth_cell = datetime(birth.year, birth.month, birth.day)
        elif i % 3 == 1:
            birth_cell = f'{birth:%Y%m%d}'
        else:
            birth_cell = f'{birth:%Y/%m/%d}'
        rows.append([name, id_card, f'1387746{3000 + i:04d}', birth_cell,
                     gcode, vg, hcode,
                     f'广西融水苗族自治县融水镇三合村{vg}屯{11 + i}号',
                     hname, prop, edu])
    return rows


# ════════════════════════════════════════════════════════════════
# 文件B：数据字段在系统内置字段中不存在（未知字段 → ext/忽略）
# ════════════════════════════════════════════════════════════════
# 已知列（exact 主标签）：姓名/身份证号/联系电话/所在组/与户主关系/学历/婚姻状况
# 系统外字段 4 个（difflib 全目录最高比 < 0.6，步2 显示「未识别」）：
#   帮扶责任人、疫苗接种针次、养老金账号、体检结论
# 未映射比例 4/11 ≈ 36% ≤ 50%、新字段 4 ≤ 10 → 不触发 blocked
HEADERS_B = ['姓名', '身份证号', '联系电话', '所在组', '与户主关系', '学历',
             '婚姻状况', '帮扶责任人', '疫苗接种针次', '养老金账号', '体检结论']
PEOPLE_B = [
    ('韦向东', 301, date(1969, 2, 14), '思榜', '户主本人', '初中', '已婚',
     '蓝先锋', '3', '62140011022345678', '血压偏高'),
    ('黄玉连', 302, date(1971, 8, 21), '思榜', '配偶', '小学', '已婚',
     '蓝先锋', '3', '62140011022345679', '健康'),
    ('梁志强', 303, date(1988, 5, 7), '旧寨', '户主本人', '高中/中专', '未婚',
     '莫大勇', '2', '', '健康'),
    ('贾小娟', 304, date(1992, 10, 11), '旧寨', '配偶', '大专', '已婚',
     '莫大勇', '2', '62140011022345680', '健康'),
    ('蓝福安', 305, date(1947, 3, 3), '思榜', '户主本人', '文盲', '丧偶',
     '', '0', '', '腰椎间盘突出'),
    ('罗金凤', 306, date(1952, 6, 26), '思榜', '配偶', '小学', '已婚',
     '', '0', '', '关节炎'),
]


def build_rows_b():
    rows = []
    for i, (name, seq, birth, vg, rel, edu, marital, helper, vax, pension, exam) in enumerate(PEOPLE_B):
        id_card = make_id(seq, birth, name in ('韦向东', '梁志强', '蓝福安'))
        phone = f'1597746{4000 + i:04d}' if i != 4 else ''   # 1 行无电话（选填留空）
        rows.append([name, id_card, phone, vg, rel, edu, marital,
                     helper, vax, pension, exam])
    return rows


# ════════════════════════════════════════════════════════════════
# 文件C：大面积未知字段 → 阈值守卫 blocked（未映射 70% > 50%）
# ════════════════════════════════════════════════════════════════
HEADERS_C = ['姓名', '身份证号', '联系电话',
             '帮扶责任人', '疫苗接种针次', '养老金账号', '体检结论',
             '务工工种', '耕地面积亩', '电表编号']
PEOPLE_C = [
    ('韦承志', 401, date(1974, 4, 8), '思榜'),
    ('梁玉连', 402, date(1976, 9, 17), '思榜'),
    ('贾凡', 403, date(1990, 1, 29), '旧寨'),
    ('莫元斌', 404, date(1963, 11, 5), '旧寨'),
]


def build_rows_c():
    rows = []
    for i, (name, seq, birth, vg) in enumerate(PEOPLE_C):
        id_card = make_id(seq, birth, i % 2 == 0)
        rows.append([name, id_card, f'1877746{5000 + i:04d}', '',
                     '2', '', '健康', '瓦工', '3.5', 'G4502250001'])
    return rows


# ════════════════════════════════════════════════════════════════
# 文件D：字段名与系统内置字段不一致（全新人员；2026-09-13 增补）
# ════════════════════════════════════════════════════════════════
# 与文件A互补：A 偏 exact 别名+模板，D 偏重 difflib 模糊（6 fuzzy + 4 别名 exact），
# 且 A 用过的表头一个不复用。源表头 → 期望目标 / 匹配层级：
#   成员姓名     → name           fuzzy（vs 主标签「姓名」0.67）
#   身份证件号码 → id_card        fuzzy（vs 别名「证件号码」0.8）
#   联系方式     → phone          exact（别名）
#   出生年月日   → birth_date     fuzzy（vs 主标签「出生日期」0.67）
#   性别代码     → gender         fuzzy（vs 主标签「性别」0.67）
#   村组         → village_group  exact（别名）
#   户号         → household_code exact（别名）
#   户主         → household_name exact（别名）
#   职业称谓     → occupation     fuzzy（vs 主标签「职业」0.67）
#   家庭通信地址 → address        fuzzy（vs 别名「家庭地址」0.8）
HEADERS_D = ['成员姓名', '身份证件号码', '联系方式', '出生年月日', '性别代码',
             '村组', '户号', '户主', '职业称谓', '家庭通信地址']
PEOPLE_D = [
    ('覃建波', 501, date(1968, 4, 15), '男', '思榜', '思榜-201', '覃建波', '瓦工'),
    ('陆春花', 502, date(1971, 9, 8), '女', '思榜', '思榜-201', '覃建波', '务农'),
    ('韦忠良', 503, date(1985, 12, 2), '男', '思榜', '思榜-202', '韦忠良', '教师'),
    ('蓝美玉', 504, date(1990, 6, 21), '女', '旧寨', '旧寨-201', '蓝美玉', '个体经营'),
    ('梁俊才', 505, date(1979, 3, 30), '男', '旧寨', '旧寨-201', '梁俊才', '泥水工'),
    ('贾文静', 506, date(1996, 11, 17), '女', '旧寨', '旧寨-202', '梁俊才', '茶农'),
]


def build_rows_d():
    rows = []
    for i, (name, seq, birth, gender, vg, hcode, hname, job) in enumerate(PEOPLE_D):
        id_card = make_id(seq, birth, gender == '男')
        rows.append([name, id_card, f'1377746{7100 + i:04d}',
                     f'{birth:%Y-%m-%d}', gender, vg, hcode, hname, job,
                     f'广西融水苗族自治县融水镇三合村{vg}屯{21 + i}号'])
    return rows


# ════════════════════════════════════════════════════════════════
# 文件E：数据字段在系统内置字段中不存在（全新人员；2026-09-13 增补）
# ════════════════════════════════════════════════════════════════
# 与文件B互补：已知列 7 个（exact 主标签）+ 全新系统外字段 3 个
# （残疾类别/计生奖扶金额/饮水工程编号，difflib 全目录最高比 < 0.6，
# 其中「饮水工程编号」vs 别名「户编号」仅 0.44，不会误命中）。
# 未映射比例 3/10 = 30% ≤ 50%、新字段 3 ≤ 10 → 不触发 blocked；
# 步2 可选「存为扩展字段 ext / 忽略」，导入后 import_field_registry 新增 3 条 draft。
# 附带测试点：不给性别列 → 引擎按身份证第 17 位推断性别（make_id 奇偶序号保证）。
HEADERS_E = ['姓名', '身份证号', '联系电话', '所在组', '与户主关系', '学历',
             '婚姻状况', '残疾类别', '计生奖扶金额', '饮水工程编号']
PEOPLE_E = [
    ('韦桂生', 601, date(1965, 7, 19), True, '思榜', '户主本人', '初中', '已婚',
     '肢体残疾', 960, 'SS-2024-01'),
    ('黄秀兰', 602, date(1968, 2, 25), False, '思榜', '配偶', '小学', '已婚',
     '', 960, 'SS-2024-01'),
    ('蓝海山', 603, date(1983, 10, 6), True, '旧寨', '户主本人', '高中/中专', '未婚',
     '听力残疾', 0, 'SS-2024-02'),
    ('贾丽萍', 604, date(1986, 5, 14), False, '旧寨', '配偶', '初中', '已婚',
     '', 0, 'SS-2024-02'),
    ('梁守业', 605, date(1941, 12, 9), True, '思榜', '户主本人', '文盲', '丧偶',
     '视力残疾', 1200, ''),
]


def build_rows_e():
    rows = []
    for i, (name, seq, birth, male, vg, rel, edu, marital,
            disability, subsidy, water) in enumerate(PEOPLE_E):
        id_card = make_id(seq, birth, male)
        phone = f'1867746{8100 + i:04d}' if i != 4 else ''   # 末行无电话（选填留空）
        rows.append([name, id_card, phone, vg, rel, edu, marital,
                     disability, subsidy, water])
    return rows


FILES = {
    'a': 'A_字段名不一致_三级匹配.xlsx',
    'b': 'B_含系统外字段.xlsx',
    'c': 'C_大面积未知字段_触发守卫.xlsx',
    'd': 'D_字段名不一致_新名单.xlsx',
    'e': 'E_含系统外字段_新名单.xlsx',
}
BUILDERS = {
    'a': (HEADERS_A, build_rows_a),
    'b': (HEADERS_B, build_rows_b),
    'c': (HEADERS_C, build_rows_c),
    'd': (HEADERS_D, build_rows_d),
    'e': (HEADERS_E, build_rows_e),
}


def self_check(keys):
    """用引擎 build_mapping 复核匹配与阈值结果（纯函数，不触库）。"""
    from utils.import_engine import build_mapping
    report = {}
    for k in keys:
        headers, _ = _read(os.path.join(OUT_DIR, FILES[k]))
        m = build_mapping(headers)
        cols = [(c['source'], c['target'], c['matched_by']) for c in m['columns']]
        report[FILES[k]] = {'columns': cols, 'threshold': m['threshold']}
    return report


def row_pure_check(keys):
    """D/E 行数据纯函数复核（不触数据库）：身份证格式 / 出生日期解析 / 性别可推断。"""
    from utils.import_engine import validate_id_card, parse_date_value, derive_gender
    if 'd' in keys:
        ok = True
        for row in build_rows_d():
            valid, msg = validate_id_card(row[1])
            if not valid or parse_date_value(row[3]) is None:
                ok = False
                print(f'  [D] 行数据异常: {row[0]} id={row[1]}（{msg}）birth={row[3]}')
        print(f'  [D] 6 行身份证格式 + 出生日期解析: {"全部通过" if ok else "存在异常"}')
    if 'e' in keys:
        ok = True
        for row in build_rows_e():
            valid, msg = validate_id_card(row[1])
            if not valid or derive_gender(row[1]) not in ('男', '女'):
                ok = False
                print(f'  [E] 行数据异常: {row[0]} id={row[1]}（{msg}）')
        print(f'  [E] 5 行身份证格式 + 性别可由第 17 位推断: {"全部通过" if ok else "存在异常"}')


def _read(path):
    from utils.import_engine import read_xlsx
    return read_xlsx(path)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='生成批量导入功能测试文件')
    parser.add_argument('--only', default='abcde',
                        help='只生成指定文件，逗号分隔，取值 a/b/c/d/e（默认全部；'
                             '重建 b 会覆盖其第 8~10 行手工追加的异常边界行）')
    args = parser.parse_args()
    keys = [s.strip().lower() for s in args.only.split(',') if s.strip()]
    bad = [k for k in keys if k not in FILES]
    if bad:
        raise SystemExit(f'--only 取值非法: {bad}（可选 a/b/c/d/e）')
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = []
    for k in keys:
        headers, builder = BUILDERS[k]
        paths.append(write_xlsx(FILES[k], headers, builder()))
    print('已生成：\n  ' + '\n  '.join(paths))
    print('\n===== 引擎自检（build_mapping 复核） =====')
    for fname, r in self_check(keys).items():
        t = r['threshold']
        flag = f"blocked（{t['reason']}）" if t['blocked'] else 'ok 不阻断'
        print(f"\n[{fname}] 阈值: {t['thresholds']} → {flag}")
        for src, tgt, by in r['columns']:
            print(f'  {src:<8} -> {str(tgt):<16} ({by})')
    print('\n===== 行数据纯函数复核 =====')
    row_pure_check(keys)


if __name__ == '__main__':
    main()
