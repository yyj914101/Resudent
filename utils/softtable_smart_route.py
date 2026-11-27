# -*- coding: utf-8 -*-
"""utils.softtable_smart_route — 软表 V2.5 懒人一键智能传表分流器（补丁 C-1）。

职责：根据上传 Excel/CSV 的文件名与解析出的全部列名指纹，自动识别三层归属：
  1. builtin_softtable — 命中 m031 预置 7 张指标联动内置软表（防止另建重名表导致大屏统计归零）；
  2. physical_table    — 命中强联动核心物理底账（低保/残疾/优抚/党员/居民主档），引导至权威模块导入；
  3. softtable_v2      — 走软表引擎 V2 极速建表，并自动推荐最贴合的挂载模块（recommended_module）。
"""
import re


# 7 张 m031 内置软表指纹（与 utils/dynatable_modules.py exclude_codes 严格一致）
_BUILTIN_RULES = [
    {
        'code': 'pension_roster',
        'module_key': 'pension',
        'name': '养老保险参保名册',
        'url': '/pension/',
        'name_hints': ('养老保险', '城乡居民养老', '养老参保', '养老缴费'),
        'col_hints': ('参保状态', '缴费档次', '养老保险', '参保年度', '领取状态'),
        'min_col_hits': 2,
    },
    {
        'code': 'shop_facility',
        'module_key': 'shop',
        'name': '基础设施分布',
        'url': '/shop/facility',
        'name_hints': ('基础设施', '设施分布', '公共设施'),
        'col_hints': ('设施名称', '设施类型', '使用状态', '所在位置', '管护责任人'),
        'min_col_hits': 2,
    },
    {
        'code': 'shop_annual_fee',
        'module_key': 'shop',
        'name': '重点场所年度缴费',
        'url': '/shop/annual-fee',
        'name_hints': ('年度缴费', '重点场所缴费', '商铺缴费'),
        'col_hints': ('缴费年度', '缴费金额', '场所名称', '缴费状态'),
        'min_col_hits': 2,
    },
    {
        'code': 'sanitation_team',
        'module_key': 'sanitation',
        'name': '环卫队伍名册',
        'url': '/sanitation/roster/sanitation_team/',
        'name_hints': ('环卫队伍', '保洁员', '环卫人员', '清扫保洁'),
        'col_hints': ('负责片区', '保洁', '环卫', '在岗状态'),
        'min_col_hits': 2,
    },
    {
        'code': 'sanitation_site',
        'module_key': 'sanitation',
        'name': '环卫点位台账',
        'url': '/sanitation/roster/sanitation_site/',
        'name_hints': ('环卫点位', '垃圾点位', '垃圾收集点', '公厕'),
        'col_hints': ('点位名称', '垃圾桶', '清运频次', '点位类型'),
        'min_col_hits': 2,
    },
    {
        'code': 'sanitation_cleanup',
        'module_key': 'sanitation',
        'name': '环境清理记录',
        'url': '/sanitation/roster/sanitation_cleanup/',
        'name_hints': ('环卫清理', '环境清理', '垃圾清运', '集中整治'),
        'col_hints': ('清理日期', '清理区域', '清运量', '整改情况'),
        'min_col_hits': 2,
    },
    {
        'code': 'civilization_activity',
        'module_key': 'civilization',
        'name': '文明实践站活动记录',
        'url': '/civilization/roster/civilization_activity/',
        'name_hints': ('文明实践', '新时代文明', '志愿服务活动'),
        'col_hints': ('活动主题', '活动时间', '参与人数', '志愿'),
        'min_col_hits': 2,
    },
    # Patch C-2：迁入软表 V2 的 8 张综治/走访固定内置软表
    # 141 收紧：列指纹须 ≥3 个专属列同时命中（或文件名命中）——各表恰有 3~4 个专属指纹；
    # 原阈值 2 会把「姓名/职务/联系电话」这类通用名单误判成内置治保表，导致挂载模块选错。
    {
        'code': 'gov_mental_health',
        'module_key': 'governance',
        'name': '严重精神障碍患者台账',
        'url': '/governance/roster/gov_mental_health/',
        'name_hints': ('严重精神障碍', '精神障碍', '精障'),
        'col_hints': ('随访日期', '包保人', '走访/随访情况', '随访情况'),
        'min_col_hits': 3,
    },
    {
        'code': 'gov_security_committee',
        'module_key': 'governance',
        'name': '治保委员会花名册',
        'url': '/governance/roster/gov_security_committee/',
        'name_hints': ('治保委员会', '治保会', '治保委员'),
        'col_hints': (),  # 141：职务/地址/电话全为泛化词，仅文件名可判,
        'min_col_hits': 3,
    },
    {
        'code': 'gov_criminal',
        'module_key': 'governance',
        'name': '刑事人员台账',
        'url': '/governance/roster/gov_criminal/',
        'name_hints': ('刑事人员', '刑满释放', '服刑人员', '安置帮教'),
        'col_hints': ('人员状态', '刑满释放日期', '帮教类型', '原判刑期'),
        'min_col_hits': 3,
    },
    {
        'code': 'gov_road_hazard',
        'module_key': 'governance',
        'name': '道路交通安全隐患台账',
        'url': '/governance/roster/gov_road_hazard/',
        'name_hints': ('道交隐患', '道路交通安全隐患', '道路隐患'),
        'col_hints': ('路段名称', '隐患类型', '隐患等级', '整改状态'),
        'min_col_hits': 3,
    },
    {
        'code': 'gov_key_vehicle',
        'module_key': 'governance',
        'name': '重点车辆驾驶人名册',
        'url': '/governance/roster/gov_key_vehicle/',
        'name_hints': ('重点车辆', '驾驶人名册', '道交车辆'),
        'col_hints': ('号牌号码', '车辆类型', '证照情况', '在册状态'),
        'min_col_hits': 3,
    },
    {
        'code': 'gov_guide_station',
        'module_key': 'governance',
        'name': '交通安全劝导站',
        'url': '/governance/roster/gov_guide_station/',
        'name_hints': ('劝导站', '交通劝导'),
        'col_hints': ('站点名称', '劝导员', '运行状态'),
        'min_col_hits': 3,
    },
    {
        'code': 'gov_stability_risk',
        'module_key': 'governance',
        'name': '社会稳定风险排查化解记录',
        'url': '/governance/roster/gov_stability_risk/',
        'name_hints': ('社会稳定风险', '稳控', '风险排查'),
        'col_hints': ('排查日期', '风险类别', '工作措施'),
        'min_col_hits': 3,
    },
    {
        'code': 'gov_special_visit',
        'module_key': 'governance',
        'name': '特困人员走访记录',
        'url': '/governance/roster/gov_special_visit/',
        'name_hints': ('特困人员走访', '特困走访'),
        'col_hints': ('走访日期', '包保人', '走访情况'),
        'min_col_hits': 3,
    },
]

# 强联动核心物理底账指纹（排除「高龄津贴增减变动表 / 高龄补贴发放明细」等周期性 V2 报送表）
_PHYSICAL_RULES = [
    {
        'key': 'dibao',
        'name': '低保对象底账',
        'url': '/dibao/',
        'name_hints': ('低保对象', '低保花名册', '低保户', '最低生活保障'),
        'col_hints': ('低保类别', '保障人数', '月保障金额', '低保证号'),
        'min_col_hits': 2,
    },
    {
        'key': 'disabled',
        'name': '残疾人底账',
        'url': '/disabled/',
        'name_hints': ('残疾人名册', '残疾人花名册', '持证残疾人'),
        'col_hints': ('残疾证号', '残疾类别', '残疾等级'),
        'min_col_hits': 2,
    },
    {
        'key': 'youshu',
        'name': '优抚对象底账',
        'url': '/youshu/',
        'name_hints': ('优抚对象', '退役军人优抚'),
        'col_hints': ('优抚类别', '抚恤标准', '伤残等级'),
        'min_col_hits': 2,
    },
    {
        'key': 'party',
        'name': '党员管理底账',
        'url': '/party/',
        'name_hints': ('党员花名册', '全体党员名册', '党员基本信息'),
        'col_hints': ('所在支部', '转正日期', '党费标准', '党籍状态'),
        'min_col_hits': 2,
    },
    {
        'key': 'resident',
        'name': '居民主档',
        'url': '/resident/',
        'name_hints': ('全员人口', '居民信息总表', '户籍人口花名册'),
        'col_hints': ('与户主关系', '户号', '户籍性质', '居住状态'),
        'min_col_hits': 2,
    },
]

# 软表 V2 自动推荐挂载模块关键词
_MODULE_HINTS = [
    ('organization', ('三委', '党委', '居委', '村委', '小组长', '居民代表', '监委会', '分工', '班子', '干部')),
    ('civil', ('高龄', '津贴', '民政', '低保', '特困', '关爱', '优抚', '救助', '残联', '一老一小', '补贴')),
    ('safety', ('消防', '安全', '燃气', '九小场所', '防汛', '隐患', '应急', '排查', '灭火器')),
    ('pension', ('养老', '退休', '社保')),
    ('governance', ('综治', '信访', '维稳', '调解', '矛盾', '治保', '精神障碍', '道交', '劝导站')),
    ('sanitation', ('环卫', '保洁', '垃圾', '绿化')),
    ('civilization', ('文明实践', '志愿', '宣讲')),
    ('shop', ('商铺', '店铺', '商户', '门面', '摊位')),
]


def _collect_headers(parsed: dict) -> list:
    headers = []
    for sh in (parsed.get('sheets') or []):
        for t in (sh.get('tables') or []):
            for f in (t.get('fields') or []):
                dn = re.sub(r'\s+', '', str(f.get('display_name') or ''))
                if dn:
                    headers.append(dn)
    return headers


def detect_smart_route(filename: str, parsed: dict) -> dict:
    """根据文件名与解析出的列头指纹返回智能分流建议。"""
    fname = str(filename or '')
    wgroup = str(parsed.get('workbook_group') or '')
    title_blob = fname + ' ' + wgroup
    headers = _collect_headers(parsed)
    header_blob = ' '.join(headers)

    # 1. 优先识别 7 张内置指标软表（最高防撞车优先级）
    for rule in _BUILTIN_RULES:
        name_hit = any(k in title_blob for k in rule['name_hints'])
        col_hits = sum(1 for k in rule['col_hints'] if k in header_blob)
        if name_hit or col_hits >= rule['min_col_hits']:
            return {
                'kind': 'builtin_softtable',
                'target_code': rule['code'],
                'target_name': rule['name'],
                'target_url': rule['url'],
                'recommended_module': rule['module_key'],
                'reason': f'识别为系统内置「{rule["name"]}」（关联大屏统计指标），建议直接前往既有表追加导入，避免重名建表导致大屏数据归零。',
            }

    # 2. 识别核心物理底账（高龄增减变动表/高龄明细显式豁免，走软表 V2）
    is_elder_report = ('高龄' in title_blob) or ('增减变动' in title_blob)
    if not is_elder_report:
        for rule in _PHYSICAL_RULES:
            name_hit = any(k in title_blob for k in rule['name_hints'])
            col_hits = sum(1 for k in rule['col_hints'] if k in header_blob)
            if name_hit or col_hits >= rule['min_col_hits']:
                return {
                    'kind': 'physical_table',
                    'target_key': rule['key'],
                    'target_name': rule['name'],
                    'target_url': rule['url'],
                    'recommended_module': 'civil' if rule['key'] in ('dibao', 'disabled', 'youshu') else 'dynatable',
                    'reason': f'识别为系统核心物理底账「{rule["name"]}」（关联居民联动与统计），建议前往对应业务模块导入；如确属临时专项汇总表，仍可点击下方继续按软表创建。',
                }

    # 3. 软表 V2 极速建表 + 自动推荐挂载模块
    rec_mod = 'dynatable'
    rec_reason = '未命中特定业务模块关键词，默认归入「自定义台账」。'
    for mod_key, hints in _MODULE_HINTS:
        hit_kw = next((k for k in hints if k in title_blob or k in header_blob), None)
        if hit_kw:
            rec_mod = mod_key
            rec_reason = f'根据关键词「{hit_kw}」自动推荐挂载模块（可在下方一键修改）。'
            break

    return {
        'kind': 'softtable_v2',
        'recommended_module': rec_mod,
        'reason': rec_reason,
    }
