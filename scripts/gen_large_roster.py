#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成大规模居民名单测试文件（环境准备用，不属于业务代码）。

用途：为居民批量导入向导（/import/upload）提供一份**干净基线**数据集——
300+ 行、23 列标准字段、零脏数据，用于验证大批量导入的吞吐、去重、
派生标签、聚合统计与页面渲染，不掺任何异常边界行。

与 gen_import_test_files.py 的分工：
- gen_import_test_files.py（A~E）＝**字段映射与守卫边界**测试，行数少、场景刁钻；
- 本脚本（F）＝**规模与完整性**测试，行数多、数据干净。
两者互不覆盖，各自独立可重跑。

数据特征（均为虚构，不含任何真实居民信息）：
- 身份证号 450225 + 合法校验位，序号在未占用段内跳段发放（141~999，
  扣除种子与 A~E 已占段，见 SEQ_FREE_BLOCKS），不会被库内去重
  （_id_card_exists 的 SHA-256 等值）跳过；性别经奇偶调整后实际序号
  可能落入相邻段，由名单内去重 + 库内冲突双保险兜底（审阅修复口径）。
- 性别与身份证第 17 位奇偶严格一致（derive_gender 口径）。
- 户属性按 80/10/7/3 铺 一般农户/脱贫户/监测户/五保户，覆盖
  TAG_HOUSEHOLD_PROPS 派生标签；五保户一律独居高龄户。
- 年龄跨 1930s~2010s，覆盖「一老一小」关爱场景；学历/婚姻/兵役/
  健康/政治面貌按年龄与关系推导，不出现逻辑矛盾。
- 枚举列取值全部取自库内 dict_items 实际字典值，不造字典外的新值。

用法：
    python3 scripts/gen_large_roster.py                # 生成 320 行
    python3 scripts/gen_large_roster.py --rows 500     # 换规模
    python3 scripts/gen_large_roster.py --verify-only  # 只跑自检不出文件

输出：测试数据/批量导入/F_大规模名单_<N>行.xlsx
"""
import argparse
import os
import random
import sqlite3
import sys
from datetime import date

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)

from openpyxl import Workbook

OUT_DIR = os.path.join(BASE_DIR, '测试数据', '批量导入')

# ── 身份证生成（合法校验位，内容虚构）──
# 与 gen_import_test_files.py 同口径：性别决定序号奇偶。
_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_CHECK_CODES = '10X98765432'
ID_PREFIX = '450225'
# 身份证第 15~17 位是 3 位序号，可用空间仅 000~999。已占用段见各测试文件
# README（种子 103~140、A 201~208、B 301~306、C 401~404、D 501~506、
# E 601~605），下面按顺序取剩余空段，可容纳 825 行。
SEQ_FREE_BLOCKS = [(141, 199), (209, 299), (307, 399), (405, 499),
                   (507, 599), (606, 699), (700, 999)]
SEQ_CAPACITY = sum(b - a + 1 for a, b in SEQ_FREE_BLOCKS)

# ── 表头：全部用 FIELD_CATALOG 的标准 label，步2 应 100% exact 命中 ──
HEADERS = [
    '姓名', '身份证号', '出生日期', '性别', '民族', '联系电话',
    '所在组', '所在网格', '家庭住址', '户主姓名', '户编号',
    '与户主关系', '户口类型', '政治面貌', '学历', '职业',
    '健康状态', '户属性', '开户行', '银行卡号', '婚姻状况',
    '兵役状况', '备注',
]

# ── 取值池（枚举项全部来自库内 dict_items，字符串项为业务常识值）──
VILLAGES = ['思榜', '旧寨']
SURNAMES = ['韦', '梁', '贾', '莫', '蓝', '石', '吴', '覃', '赵', '卢',
            '马', '潘', '郑', '冯', '黄', '谭', '陆', '韩', '罗', '程']
GIVEN_CHARS = ['日', '春', '大', '小', '保', '秀', '金', '银', '桂', '兰',
               '建', '文', '志', '国', '美', '玉', '福', '寿', '明', '清']
NATIONS = ['汉', '壮', '苗', '瑶', '侗', '仫佬', '毛南', '回', '彝', '水']
RELATIONS = ['配偶', '子女', '父母', '儿媳', '女婿', '孙子', '孙女',
             '兄弟姐妹', '孙子女', '其他亲属']
# 字典里「孙子 / 孙女」是分开的值，生成时先按「孙子女」占位，再按性别落到具体值
RELATION_SPLIT = {'孙子女': ('孙子', '孙女')}
POLITICS = ['中共党员', '中共预备党员', '共青团员', '群众', '民主党派', '无党派人士']
EDUCATION = ['研究生', '本科', '大专', '高中/中专', '初中', '小学', '文盲']
HEALTH = ['健康', '一般', '长期慢性病', '重大疾病', '残疾']
HOUSEHOLD_PROP = ['一般农户', '脱贫户', '监测户', '五保户']
BANKS = ['中国农业银行', '中国邮政储蓄银行', '广西农村商业银行',
         '农村信用合作社', '中国工商银行', '中国建设银行']
HOUSEHOLD_TYPE = ['农业户口', '农业户口', '农业户口', '非农业户口', '统一居民户口']
MARITAL = ['未婚', '已婚', '离异', '丧偶']
MILITARY = ['未服兵役', '已服兵役', '退役', '现役']
OCCUPATION = ['务农', '外出务工', '个体经营', '小学教师', '村医',
              '护林员', '保洁员', '退役军人', '学生', '学龄前儿童', '无业']
CARE_FOCUS = ['低保', '残疾', '困难老人', '留守儿童', '困境儿童',
              '独居老人', '高龄老人', '特困供养']


def make_id(seq: int, birth: date, male: bool) -> str:
    """生成 18 位身份证（含合法校验位）。内容为虚构号码。"""
    n = (seq | 1) if male else (seq & ~1)
    body = f'{ID_PREFIX}{birth:%Y%m%d}{n:03d}'
    s = sum(int(c) * w for c, w in zip(body, _WEIGHTS))
    return body + _CHECK_CODES[s % 11]


class SeqAllocator:
    """在未占用的 3 位序号段上顺序发放，越界即报错（不静默截断）。"""

    def __init__(self):
        self.blocks = [list(b) for b in SEQ_FREE_BLOCKS]
        self._validate_blocks()

    @staticmethod
    def _validate_blocks():
        for (a1, b1), (a2, _) in zip(SEQ_FREE_BLOCKS, SEQ_FREE_BLOCKS[1:]):
            if a2 <= b1:
                raise ValueError(f'序号段重叠：{(a1, b1)} 与 {a2} 之后')

    def take(self) -> int:
        if not self.blocks:
            raise RuntimeError(
                f'身份证序号已耗尽（容量 {SEQ_CAPACITY} 行），请调低 --rows')
        blk = self.blocks[0]
        n = blk[0]
        blk[0] += 1
        if blk[0] > blk[1]:
            self.blocks.pop(0)
        return n


class Roster:
    """确定性生成器：同一 --seed 下可完全复现，便于回归对比。"""

    def __init__(self, seed: int = 20260913):
        self.rnd = random.Random(seed)
        self.seq = SeqAllocator()
        self.used_names: set[str] = set()
        self.rows: list[dict] = []

    def rand(self) -> float:
        return self.rnd.random()

    def pick(self, seq):
        return seq[int(self.rand() * len(seq))]

    def chance(self, p: float) -> bool:
        return self.rand() < p

    def between(self, lo: int, hi: int) -> int:
        return lo + int(self.rand() * (hi - lo + 1))

    def name(self) -> str:
        """生成全库唯一的姓名（确定性重试，不依赖随机碰撞概率）。"""
        for _ in range(500):
            n = self.pick(SURNAMES) + ''.join(
                self.pick(GIVEN_CHARS) for _ in range(self.between(1, 2)))
            if n not in self.used_names:
                self.used_names.add(n)
                return n
        raise RuntimeError('姓名池耗尽')

    def phone(self) -> str:
        return '1' + self.pick(['3', '5', '7', '8', '9']) + \
            ''.join(str(self.between(0, 9)) for _ in range(9))

    def bank_card(self) -> str:
        return '62' + ''.join(str(self.between(0, 9)) for _ in range(17))

    def grid(self, village: str) -> str:
        return f'{village}网格{self.between(1, 8):02d}'

    def address(self, village: str, no: int) -> str:
        return ('广西壮族自治区柳州市融水苗族自治县融水镇三合村'
                f'{village}屯{no}号')

    # -- 年龄相关的派生字段，保证数据自洽 --
    def education(self, age: int) -> str:
        if age < 6:
            return ''                      # 学龄前，留空（选填字段）
        if age <= 15:
            return '小学'
        if age <= 18:
            return '初中'
        if age <= 22:
            return '高中/中专'
        if age <= 26:
            return '大专'
        r = self.rand()
        if r < 0.08:
            return '文盲'
        if r < 0.16:
            return '小学'
        if r < 0.34:
            return '初中'
        if r < 0.62:
            return '高中/中专'
        if r < 0.82:
            return '大专'
        if r < 0.94:
            return '本科'
        return '研究生'

    def politics(self, age: int) -> str:
        if age < 14:
            return ''
        if age < 18:
            # 审阅修复 P3#2：未成年不出党员/预备党员（脏数据），仅团员或群众
            return '共青团员' if self.chance(0.55) else '群众'
        if age <= 28 and self.chance(0.45):
            return '共青团员'
        if self.chance(0.16):
            return '中共党员'
        if self.chance(0.04):
            return '中共预备党员'
        r = self.rand()
        if r < 0.02:
            return '民主党派'
        if r < 0.035:
            return '无党派人士'
        return '群众'

    def health(self, age: int) -> str:
        if age < 60:
            return '健康' if self.chance(0.88) else '一般'
        if age < 75:
            r = self.rand()
            if r < 0.52:
                return '健康'
            if r < 0.76:
                return '一般'
            if r < 0.93:
                return '长期慢性病'
            if r < 0.98:
                return '残疾'
            return '重大疾病'
        r = self.rand()
        if r < 0.28:
            return '健康'
        if r < 0.55:
            return '一般'
        if r < 0.82:
            return '长期慢性病'
        if r < 0.94:
            return '残疾'
        return '重大疾病'

    def marital(self, age: int, has_spouse: bool) -> str:
        if age < 22:
            return '未婚'
        if has_spouse:
            return '已婚'
        r = self.rand()
        if r < 0.55:
            return '未婚'
        if r < 0.72:
            return '已婚'
        if r < 0.86:
            return '离异'
        if age >= 55:
            return '丧偶'
        return '未婚'

    def military(self, male: bool, age: int) -> str:
        if not male or age < 18 or age > 60:
            return ''
        if age <= 45 and self.chance(0.22):
            return self.pick(['已服兵役', '退役'])
        return '未服兵役'

    def occupation(self, age: int, prop: str) -> str:
        if age < 6:
            return '学龄前儿童'
        if age <= 18:
            return '学生'
        if age >= 60:
            if prop == '五保户':
                return '无业'
            return self.pick(['务农', '无业', '保洁员'])
        return self.pick(OCCUPATION[:9])

    # -- 成员生成 --
    def member(self, village: str, hh: dict, relation: str,
               birth_year: int) -> dict:
        birth = date(birth_year, self.between(1, 12), self.between(1, 28))
        age = 2026 - birth_year
        male = self.chance(0.52)
        id_card = make_id(self.seq.take(), birth, male)
        has_spouse = relation == '配偶'
        if relation in RELATION_SPLIT:
            m_label, f_label = RELATION_SPLIT[relation]
            relation = m_label if male else f_label
        prop = hh['property']
        row = {
            '姓名': self.name(),
            '身份证号': id_card,
            '出生日期': birth.strftime('%Y-%m-%d'),
            '性别': '男' if male else '女',
            '民族': self.pick(NATIONS),
            '联系电话': self.phone() if self.chance(0.93) else '',
            '所在组': village,
            '所在网格': self.grid(village),
            '家庭住址': self.address(village, hh['no']),
            '户主姓名': hh['name'],
            '户编号': hh['code'],
            '与户主关系': '户主本人' if relation == '户主本人' else relation,
            '户口类型': self.pick(HOUSEHOLD_TYPE),
            '政治面貌': self.politics(age),
            '学历': self.education(age),
            '职业': self.occupation(age, prop),
            '健康状态': self.health(age),
            '户属性': prop,
            '开户行': '',
            '银行卡号': '',
            '婚姻状况': self.marital(age, has_spouse),
            '兵役状况': self.military(male, age),
            '备注': '',
        }
        # 开户行/卡号：政策性户优先覆盖，验证「有卡户」聚合口径
        if self.chance(0.78 if prop in ('脱贫户', '监测户') else 0.35):
            row['开户行'] = self.pick(BANKS)
            row['银行卡号'] = self.bank_card()
        # 备注：仅少数行带，覆盖备注字段的展示与长度
        if self.chance(0.08):
            row['备注'] = self.pick([
                '由村民小组核对上报', '长期在外务工，电话由家人代管',
                '本人行动不便，走访需上门', '2026年新迁入本村',
                '享受低保，需定期复核',
            ])
        return row

    def household(self, target: int) -> int:
        """生成一户，返回实际写入行数。"""
        village = self.pick(VILLAGES)
        self.hh_no = getattr(self, 'hh_no', 0) + 1
        no = self.hh_no
        code = f'{village}-{"2" if village == "思榜" else "3"}{no:02d}'

        # 户属性：五保户必须是独居高龄户
        if self.chance(0.03):
            prop = '五保户'
        elif self.chance(0.11):
            prop = '脱贫户'
        elif self.chance(0.08):
            prop = '监测户'
        else:
            prop = '一般农户'

        hh = {'name': '', 'code': code, 'property': prop, 'no': no}
        head_year = self.between(1948, 1996) if prop != '五保户' else self.between(1938, 1956)
        head_row = self.member(village, hh, '户主本人', head_year)
        hh['name'] = head_row['姓名']
        # 审阅修复 P2：head 行生成时 hh['name'] 还是空串，须回填（对齐种子惯例：户主=本人姓名）
        head_row['户主姓名'] = head_row['姓名']
        self.rows.append(head_row)
        written = 1

        # 成员规划：受剩余行数约束，避免超量
        slots = []
        head_age = 2026 - head_year
        if prop == '五保户':
            pass                                  # 独居
        else:
            if self.chance(0.62):
                slots.append(('配偶', head_year + self.between(-2, 2)))
            if self.chance(0.58):
                slots.append(('子女', 2026 - self.between(1, 24)))
            if head_age >= 45 and self.chance(0.42):
                # 审阅修复 P3#3：父母须比户主年长 ≥14 岁，且落在 66~92 岁龄段
                p_hi = min(1960, head_year - 14)
                if p_hi >= 1934:
                    slots.append(('父母', self.between(1934, p_hi)))
            if head_age >= 55 and self.chance(0.30):
                slots.append(('孙子女', 2026 - self.between(1, 12)))
            if self.chance(0.35):
                slots.append(('兄弟姐妹', head_year + self.between(-6, 6)))
            if self.chance(0.22):
                slots.append(('其他亲属', 2026 - self.between(20, 70)))

        for rel, year in slots:
            if written >= target:
                break
            self.rows.append(self.member(village, hh, rel, year))
            written += 1
        return written

    def build(self, total: int) -> list[dict]:
        while len(self.rows) < total:
            self.household(total - len(self.rows))
        return self.rows[:total]


# ── 自检 ──
def verify(rows: list[dict]) -> bool:
    from utils.import_engine import (build_mapping, match_field,
                                     validate_id_card, derive_gender,
                                     parse_date_value)

    print('── 自检 ──')
    ok = True

    # ① 表头映射 + 阈值守卫
    result = build_mapping(HEADERS)
    columns = result['columns']
    unmapped = [c['source'] for c in columns if c['target'] is None]
    fuzzy = [c['source'] for c in columns if c['matched_by'] != 'exact']
    th = result['threshold']
    t = th['thresholds']
    print(f'  表头映射：{len(columns) - len(unmapped)}/{len(HEADERS)} 列已识别，'
          f'exact {len(columns) - len(fuzzy)} 列，未识别 {len(unmapped)} 列')
    print(f'  阈值守卫：未映射 {t["unmapped"]}/{t["total_cols"]} '
          f'({t["ratio"]:.0%})，新增字段 {t["new_fields"]} → '
          f'{"blocked" if th["blocked"] else "不 blocked，可直接走完四步"}')
    if unmapped:
        ok = False
        print(f'    ✗ 未识别表头：{unmapped}')
    if th['blocked']:
        ok = False
        print(f'    ✗ 阈值守卫触发：{th["reason"]}')

    # ② 逐行校验
    bad_required, bad_id, bad_dup, bad_date, bad_gender = [], [], [], [], []
    seen = set()
    for i, r in enumerate(rows, start=2):
        if not r['姓名'] or not r['身份证号']:
            bad_required.append(i)
            continue
        idc = r['身份证号']
        legal, msg = validate_id_card(idc)
        if not legal:
            bad_id.append((i, msg))
            continue
        # 校验位复算
        body, chk = idc[:17], idc[17].upper()
        expect = _CHECK_CODES[
            sum(int(c) * w for c, w in zip(body, _WEIGHTS)) % 11]
        if chk != expect:
            bad_id.append((i, f'校验位错误 应为 {expect}'))
            continue
        if idc in seen:
            bad_dup.append(i)
        seen.add(idc)
        if parse_date_value(r['出生日期']) is None:
            bad_date.append(i)
        if derive_gender(idc) != r['性别']:
            bad_gender.append(i)

    for label, bucket, extra in (
            ('必填缺失', bad_required, ''),
            ('身份证非法', bad_id, ''),
            ('名单内重复', bad_dup, ''),
            ('出生日期非法', bad_date, ''),
            ('性别与身份证不符', bad_gender, '')):
        if bucket:
            ok = False
            print(f'    ✗ {label}：{len(bucket)} 行 {extra}')
        else:
            print(f'    ✓ {label}：0 行')

    # ②' 户主姓名一致性（审阅修复 P2 增检）：
    #    户主本人行=本人姓名且非空；成员行=同户户主姓名
    bad_hh = []
    head_name_by_code = {r['户编号']: r['姓名'] for r in rows
                         if r['与户主关系'] == '户主本人'}
    for i, r in enumerate(rows, start=2):
        hh_name = (r['户主姓名'] or '').strip()
        head_name = head_name_by_code.get(r['户编号'], '')
        if not hh_name:
            bad_hh.append((i, '户主姓名为空'))
        elif r['与户主关系'] == '户主本人' and hh_name != r['姓名']:
            bad_hh.append((i, f'户主行({hh_name})≠本人({r["姓名"]})'))
        elif r['与户主关系'] != '户主本人' and hh_name != head_name:
            bad_hh.append((i, f'成员行({hh_name})≠同户户主({head_name})'))
    if bad_hh:
        ok = False
        print(f'    ✗ 户主姓名异常：{len(bad_hh)} 行（如 {bad_hh[0]}）')
    else:
        print('    ✓ 户主姓名一致：0 行')

    # ③ 与库内去重口径对齐（只读）
    db_collide = check_db_collision(seen)
    if db_collide is None:
        print('    ⚠ 库内去重校验跳过（无法连接 shucun.db）')
    elif db_collide:
        ok = False
        print(f'    ✗ 与库内既有身份证冲突：{len(db_collide)} 个')
    else:
        print('    ✓ 与库内既有身份证冲突：0 个')

    # ④ 枚举列取值必须命中库内 dict_items（否则导入后成孤儿值）
    dict_bad = check_dict_conformance(rows)
    if dict_bad is None:
        print('    ⚠ 字典一致性校验跳过（无法读取 dict_items）')
    elif dict_bad:
        ok = False
        for col, vals in dict_bad.items():
            print(f'    ✗ {col} 存在字典外取值：{sorted(vals)}')
    else:
        print('    ✓ 枚举列取值全部命中库内字典')

    # ⑤ 分布概览
    print('── 分布 ──')
    # 户属性按「户」统计才有意义：同一户所有成员共享户属性
    hh_props: dict[str, int] = {}
    for r in rows:
        code = r['户编号']
        if code not in hh_props:
            hh_props[code] = r['户属性']
    prop_count: dict[str, int] = {}
    for v in hh_props.values():
        prop_count[v] = prop_count.get(v, 0) + 1
    print(f'  户属性（按户）：'
          + '，'.join(f'{k}({v}户)'
                      for k, v in sorted(prop_count.items(),
                                         key=lambda x: -x[1])))
    for col in ('性别', '所在组'):
        counts: dict[str, int] = {}
        for r in rows:
            counts[r[col]] = counts.get(r[col], 0) + 1
        top = sorted(counts.items(), key=lambda x: -x[1])
        print(f'  {col}（按人）：'
              + '，'.join(f'{k or "（空）"}({v})' for k, v in top))
    sizes: dict[str, int] = {}
    for r in rows:
        sizes[r['户编号']] = sizes.get(r['户编号'], 0) + 1
    dist: dict[int, int] = {}
    for v in sizes.values():
        dist[v] = dist.get(v, 0) + 1
    print(f'  户数：{len(sizes)} 户 / {len(rows)} 人；'
          f'户规模分布 ' + '，'.join(f'{k}人户={v}'
                                for k, v in sorted(dist.items())))
    ages = sorted(2026 - int(r['出生日期'][:4]) for r in rows)
    print(f'  年龄：{ages[0]}~{ages[-1]} 岁，中位 {ages[len(ages) // 2]} 岁，'
          f'60 岁以上 {sum(1 for a in ages if a >= 60)} 人，'
          f'18 岁以下 {sum(1 for a in ages if a < 18)} 人')
    return ok


# 字段 code → 库内字典分类（对齐 import_engine.FIELD_CATALOG 的 dict_category）
DICT_BACKED = {
    '民族': '民族', '所在组': '村组', '与户主关系': '与户主关系',
    '户口类型': '户口类型', '政治面貌': '政治面貌', '学历': '学历',
    '健康状态': '健康状态', '户属性': '户属性', '开户行': '开户行',
    '婚姻状况': '婚姻状况', '兵役状况': '兵役状况',
}


def check_dict_conformance(rows: list[dict]):
    """比对枚举列取值与 dict_items。返回 {列: 字典外取值} 或 None。"""
    db_path = os.environ.get('SHUCUN_DB_PATH',
                             os.path.join(BASE_DIR, 'shucun.db'))
    if not os.path.exists(db_path):
        return None
    try:
        con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        allowed: dict[str, set[str]] = {}
        for col, cat in DICT_BACKED.items():
            allowed[col] = {
                r[0] for r in con.execute(
                    'SELECT i.label FROM dict_items i '
                    'JOIN dict_categories c ON c.id = i.category_id '
                    'WHERE c.name = ?', (cat,))
            }
        con.close()
    except sqlite3.Error:
        return None

    bad: dict[str, set[str]] = {}
    for col, ok_vals in allowed.items():
        if not ok_vals:
            return None                       # 字典分类不存在，跳过而非误报
        for r in rows:
            v = (r.get(col) or '').strip()
            if v and v not in ok_vals:
                bad.setdefault(col, set()).add(v)
    return bad


def check_db_collision(id_cards: set):
    """只读比对库内既有身份证（明文列 + SHA-256 等值）。失败返回 None。"""
    db_path = os.environ.get('SHUCUN_DB_PATH',
                             os.path.join(BASE_DIR, 'shucun.db'))
    if not os.path.exists(db_path):
        return None
    try:
        con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        cols = {r[1] for r in con.execute('PRAGMA table_info(residents)')}
        hits = set()
        if 'id_card' in cols:
            for (v,) in con.execute('SELECT id_card FROM residents '
                                    'WHERE id_card IS NOT NULL'):
                if v and str(v).strip().upper() in id_cards:
                    hits.add(str(v).strip().upper())
        try:
            from utils.crypto import sha256_hash
            if 'id_card_hash' in cols:
                hashes = {sha256_hash(c) for c in id_cards}
                for (h,) in con.execute(
                        'SELECT id_card_hash FROM residents '
                        'WHERE id_card_hash IS NOT NULL'):
                    if h in hashes:
                        hits.add(h)
        except Exception as e:                       # noqa: BLE001
            print(f'    （哈希口径跳过：{type(e).__name__}）')
        con.close()
        return hits
    except sqlite3.Error:
        return None


def write_xlsx(rows: list[dict]) -> str:
    wb = Workbook()
    ws = wb.active
    ws.title = '名单'
    ws.append(HEADERS)
    for r in rows:
        ws.append([r[h] for h in HEADERS])
    path = os.path.join(OUT_DIR, f'F_大规模名单_{len(rows)}行.xlsx')
    wb.save(path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description='生成大规模居民名单测试文件（默认 320 行）')
    ap.add_argument('--rows', type=int, default=320, help='数据行数，默认 320')
    ap.add_argument('--seed', type=int, default=20260913, help='随机种子')
    ap.add_argument('--verify-only', action='store_true',
                    help='只跑自检，不写文件')
    args = ap.parse_args()

    if args.rows < 1:
        print('--rows 必须 ≥ 1')
        return 2

    rows = Roster(args.seed).build(args.rows)
    print(f'── 生成 {len(rows)} 行（seed={args.seed}）──')

    if not verify(rows):
        print('\n✗ 自检未通过，未写出文件。')
        return 1

    if args.verify_only:
        print('\n（--verify-only，未写文件）')
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    path = write_xlsx(rows)
    size = os.path.getsize(path)
    print(f'\n✓ 已写出：{path}（{size:,} 字节，{len(rows)} 行 + 1 行表头）')
    return 0


if __name__ == '__main__':
    sys.exit(main())