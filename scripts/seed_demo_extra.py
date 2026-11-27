#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 演示数据补种脚本（环境准备用，不属于业务代码）。

seed_test_data.py 的补充：覆盖其未涉及的 14 个模块，让每个菜单页都有可看数据。
- 固定表走 SQLAlchemy ORM；敏感字段（身份证/电话/联系方式）一律
  utils.sensitive_fields.set_encrypted_field 写 *_enc/_hash、原列置 NULL——
  与路由层写入口径一致，杜绝明文孤岛。
- 软表（078 dynamic_table 引擎）全部经 utils.roster_facade
  （create_table / save_row），字段推断、identity 加密+指纹与页面写入口径完全一致。
- 幂等：每个模块先查行数，已有数据即跳过；--wipe 先清本脚本范围内业务表再播种
  （不触碰 residents 等基础种子）。
- 所有人名/证件号/电话均为虚构测试数据。

用法：.venv-029 python scripts/seed_demo_extra.py [--wipe]
"""
import os
import sys
import json
import argparse
from datetime import date, datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('SHUCUN_DATA_DIR', BASE_DIR)
os.environ.setdefault('SHUCUN_DB_PATH', os.path.join(BASE_DIR, 'shucun.db'))
os.environ.setdefault('SHUCUN_UPLOAD_DIR', os.path.join(BASE_DIR, 'uploads'))
os.environ.setdefault('SHUCUN_BACKUP_DIR', os.path.join(BASE_DIR, 'backups'))
os.environ.setdefault('SHUCUN_TEMP_DIR', os.path.join(BASE_DIR, 'temp'))
sys.path.insert(0, BASE_DIR)

from app import create_app
from models import (
    db, ElectionRound, ElectionVoter, ElectionResult,
    CivConvention, SafetyCheck, SafetyCheckHazard,
    Demand, DemandHandle, NoticeTemplate, NoticeRecord,
    CertificateTemplate, CertificateIssue, Shop,
    GovernancePetition, GovernancePetitionHandle,
    PartyActivity, PartyActivityReview, PartyDevelopment, PartyDevelopmentLog,
    PartyMember, WelfareEmployment, WelfareFlexSubsidy,
    DisabledServiceRecord, ElderServiceRecord, Disabled, CareTarget,
    DynamicTable, DynamicField, DynamicRecord,
)
from utils.crypto import key_holder, load_key_meta
from utils.sensitive_fields import set_encrypted_field
from utils import roster_facade as roster
from utils.helpers import get_dict_items

# ── 敏感字段映射（与各 routes/* _SENS 逐一对应，勿改口径） ──
MAP_ID = {'id_card': ('id_card_enc', 'id_card_hash', None)}
MAP_ID_PHONE = {'id_card': ('id_card_enc', 'id_card_hash', None),
                'phone': ('phone_enc', 'phone_hash', None)}
MAP_CONTACT = {'contact': ('contact_enc', 'contact_hash', None),
               'id_card': ('id_card_enc', 'id_card_hash', None)}

_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_CHECK_CODES = '10X98765432'
_REGION = '450225'
_id_seq = [300]
_phone_seq = [500]


def make_id_card(birth: date, male: bool) -> str:
    _id_seq[0] += 2
    n = _id_seq[0] | 1 if male else _id_seq[0] & ~1
    body = f'{_REGION}{birth:%Y%m%d}{n:03d}'
    s = sum(int(c) * w for c, w in zip(body, _WEIGHTS))
    return body + _CHECK_CODES[s % 11]


def make_phone() -> str:
    _phone_seq[0] += 1
    return f'1387746{_phone_seq[0]:04d}'


def enc(instance, field_map, kh, **values):
    for field, value in values.items():
        if value:
            set_encrypted_field(instance, field, value, field_map, kh)


def dict_first(category: str, fallback: str) -> str:
    """字典首值（value 编码）；字典缺失时回退原文（ORM 直插不校验，展示可接受）。"""
    items = get_dict_items(category)
    return items[0].value if items else fallback


def dict_pick(category: str, label_keyword: str, fallback: str) -> str:
    """按 label 关键字挑字典值，找不到回退首值。"""
    items = get_dict_items(category)
    for i in items:
        if label_keyword in i.label:
            return i.value
    return items[0].value if items else fallback


# ────────────────────────────── 软表构建 ──────────────────────────────

def build_soft(module_key, code, fields, rows, name=None, group=None,
               created_by='admin'):
    """建字段 + 写行（复用 m031 预置元数据行的 name/group，不覆盖丢失）。

    fields: [(display_name, data_type, is_required, sensitivity)]
    rows:   [{display_name: value}, ...]
    name/group: 新建表（无预置行）时显式中文名/组名（如应急'应急管理'）；
            有预置行时省略则沿用预置值。
    """
    existing = DynamicTable.query.filter_by(code=code).first()
    name = existing.name if (existing and existing.name) else (name or code)
    group = existing.group_name if (existing and existing.group_name) else group
    specs = [{'code': f'f{i:02d}', 'display_name': dn, 'data_type': dt,
              'sensitivity': sn, 'is_required': req, 'sort_order': i}
             for i, (dn, dt, req, sn) in enumerate(fields)]
    table = roster.create_table(module_key, code, name, group_name=group,
                                fields=specs, created_by=created_by)
    for row in rows:
        values = {spec['code']: row.get(spec['display_name']) for spec in specs}
        roster.save_row(table, None, values, created_by=created_by)
    print(f'[软表] {name}({code}): {len(fields)} 字段 / {len(rows)} 行')


def soft_built(code) -> bool:
    t = DynamicTable.query.filter_by(code=code).first()
    return bool(t and db.session.query(DynamicField.id).filter_by(table_id=t.id).first())


# ────────────────────────────── 各模块 ──────────────────────────────

def seed_election(kh):
    if ElectionRound.query.count():
        print('[选举] 已有数据，跳过')
        return
    r = ElectionRound(
        round_name='三合村第十二届村民委员会换届选举',
        start_date=date(2026, 1, 1), end_date=date(2029, 12, 31),
        register_start=date(2026, 3, 1), register_end=date(2026, 3, 31),
        vote_date=date(2026, 4, 18), status='已完成',
        remark='全村设中心投票站 1 个、流动票箱 2 个。')
    db.session.add(r)
    db.session.flush()

    voters = [
        ('韦建国', True, date(1962, 5, 12), '思榜', True, None, False),
        ('梁玉芳', False, date(1965, 3, 8), '思榜', False, None, False),
        ('贾志明', True, date(1978, 4, 22), '思榜', False, None, False),
        ('莫丽萍', False, date(1980, 9, 15), '思榜', False, None, False),
        ('梁天佑', True, date(1955, 1, 30), '旧寨', False, None, True),
        ('蓝先锋', True, date(1985, 10, 5), '思榜', False, None, False),
        ('吴春燕', False, date(1987, 5, 28), '思榜', True, '蓝先锋', False),
        ('覃美英', False, date(1968, 2, 14), '思榜', False, None, True),
    ]
    for name, male, birth, group, is_out, proxy, mobile in voters:
        v = ElectionVoter(round_id=r.id, name=name, group_name=group,
                          register_status='已登记', is_out=is_out,
                          proxy_name=proxy, mobile_box=mobile)
        enc(v, MAP_ID_PHONE, kh, id_card=make_id_card(birth, male),
            phone=make_phone())
        db.session.add(v)
    db.session.flush()

    results = [
        ('村委会主任', '韦建国', 342, True),
        ('村委会副主任', '蓝先锋', 318, True),
        ('村委会委员', '莫丽萍', 301, True),
    ]
    for pos, winner, votes, elected in results:
        db.session.add(ElectionResult(
            round_id=r.id, position=pos, winner_name=winner, votes=votes,
            is_elected=elected, notice_date=date(2026, 4, 22),
            remark='得票均过半，当选有效。'))
    db.session.commit()
    print('[选举] 届次 1、选民 8、当选结果 3')


def seed_civilization():
    if CivConvention.query.count():
        print('[文明实践] 已有数据，跳过')
        return
    db.session.add(CivConvention(
        title='三合村村规民约（2026 年修订版）',
        content='一、热爱祖国，热爱家乡，自觉维护村集体利益。\n'
                '二、移风易俗，婚事新办、丧事简办，反对铺张浪费。\n'
                '三、爱护环境卫生，落实门前"三包"，垃圾分类入池。\n'
                '四、邻里和睦，互帮互助，纠纷先行内部调解。\n'
                '五、尊老爱幼，关爱留守老人儿童，弘扬孝道家风。',
        is_current=True, created_by='admin'))
    db.session.commit()
    build_soft('civilization', 'civilization_activity', [
        ('活动名称', 'text', True, 'none'),
        ('活动日期', 'date', True, 'none'),
        ('活动类型', 'text', False, 'none'),
        ('活动地点', 'text', False, 'none'),
        ('参与人数', 'number', False, 'none'),
        ('活动内容', 'text', False, 'none'),
    ], [
        {'活动名称': '移风易俗主题宣讲', '活动日期': '2026-09-05', '活动类型': '宣讲',
         '活动地点': '村委会会议室', '参与人数': 46,
         '活动内容': '宣讲婚丧嫁娶简办新规，签订承诺书 32 份。'},
        {'活动名称': '重阳节敬老爱老活动', '活动日期': '2026-10-21', '活动类型': '节日活动',
         '活动地点': '村文化广场', '参与人数': 88,
         '活动内容': '为全村 80 岁以上老人发放慰问品，组织义诊和文艺演出。'},
        {'活动名称': '人居环境整治志愿行动', '活动日期': '2026-10-12', '活动类型': '志愿服务',
         '活动地点': '思榜屯、旧寨屯', '参与人数': 35,
         '活动内容': '清理沟渠 1.2 公里、卫生死角 9 处，清运垃圾约 2 吨。'},
        {'活动名称': '低保医保政策宣讲会', '活动日期': '2026-08-20', '活动类型': '宣讲',
         '活动地点': '村委会会议室', '参与人数': 52,
         '活动内容': '讲解 2027 年度医保缴费标准与低保动态调整政策。'},
        {'活动名称': '广场舞文化交流活动', '活动日期': '2026-07-15', '活动类型': '文体活动',
         '活动地点': '村文化广场', '参与人数': 30,
         '活动内容': '两支队伍交流展演，丰富群众夜间文化生活。'},
    ])


def seed_pension():
    if soft_built('pension_roster'):
        print('[养老] 台账已建，跳过')
        return
    build_soft('pension', 'pension_roster', [
        ('姓名', 'text', True, 'none'),
        ('身份证号', 'text', True, 'identity'),
        ('参保年度', 'number', True, 'none'),
        ('缴费档次（元）', 'number', False, 'none'),
        ('个人缴费金额（元）', 'number', False, 'none'),
        ('缴费状态', 'text', False, 'none'),
        ('联系电话', 'text', False, 'none'),
    ], [
        {'姓名': '韦建国', '身份证号': make_id_card(date(1962, 5, 12), True),
         '参保年度': 2026, '缴费档次（元）': 300, '个人缴费金额（元）': 300,
         '缴费状态': '已缴费', '联系电话': make_phone()},
        {'姓名': '梁玉芳', '身份证号': make_id_card(date(1965, 3, 8), False),
         '参保年度': 2026, '缴费档次（元）': 300, '个人缴费金额（元）': 300,
         '缴费状态': '已缴费', '联系电话': make_phone()},
        {'姓名': '贾志明', '身份证号': make_id_card(date(1978, 4, 22), True),
         '参保年度': 2026, '缴费档次（元）': 200, '个人缴费金额（元）': 200,
         '缴费状态': '已缴费', '联系电话': make_phone()},
        {'姓名': '黄秀兰', '身份证号': make_id_card(date(1958, 8, 18), False),
         '参保年度': 2026, '缴费档次（元）': 100, '个人缴费金额（元）': 100,
         '缴费状态': '待缴费', '联系电话': make_phone()},
        {'姓名': '韦贵生', '身份证号': make_id_card(date(1996, 9, 9), True),
         '参保年度': 2026, '缴费档次（元）': 200, '个人缴费金额（元）': 200,
         '缴费状态': '已缴费', '联系电话': make_phone()},
    ])


def seed_safety():
    if SafetyCheck.query.count():
        print('[安全检查] 已有数据，跳过')
        return
    c1 = SafetyCheck(
        title='第三季度安全生产综合检查', check_date=date(2026, 9, 12),
        check_type=dict_first('综合安全检查类型', '常规检查'),
        location='全村小卖部、农资店及公共区域', checker='村两委、驻村干部',
        remark='重点检查用电、消防与危房，共发现 2 处隐患。', created_by='admin')
    c2 = SafetyCheck(
        title='国庆节前消防安全专项检查', check_date=date(2026, 9, 28),
        check_type=dict_pick('综合安全检查类型', '节假日', '节假日检查'),
        location='三合街沿街商铺', checker='村义务消防队',
        remark='检查灭火器配置与疏散通道，发现 1 处隐患。', created_by='admin')
    db.session.add_all([c1, c2])
    db.session.flush()
    db.session.add_all([
        SafetyCheckHazard(
            check_id=c1.id, description='小卖部后仓杂物堵塞消防通道',
            level='一般', location='三合街 12 号小卖部',
            rectify_owner='韦文军', rectify_deadline=date(2026, 9, 20),
            status='待整改'),
        SafetyCheckHazard(
            check_id=c1.id, description='农资服务站私拉电线给农机充电',
            level='较大', location='三合街 25 号农资服务站',
            rectify_owner='莫大勇', rectify_deadline=date(2026, 9, 18),
            status='已闭环', rectify_note='已拆除私拉线路，加装专用充电插座并穿管保护。',
            rectified_at=datetime(2026, 9, 16, 15, 30),
            review_note='现场复核合格，予以闭环。', reviewed_at=datetime(2026, 9, 17, 9, 0)),
        SafetyCheckHazard(
            check_id=c2.id, description='理发店灭火器压力不足且超过有效期',
            level='一般', location='三合街 8 号秀兰理发店',
            rectify_owner='黄秀兰', rectify_deadline=date(2026, 10, 10),
            status='待复核', rectify_note='已购置 2 具新灭火器并完成年检。',
            rectified_at=datetime(2026, 10, 8, 11, 0)),
    ])
    db.session.commit()
    print('[安全检查] 检查 2、隐患 3（待整改/待复核/已闭环各态）')


def seed_emergency():
    if soft_built('safety_emergency_team') and soft_built('safety_emergency_supplies'):
        print('[应急] 台账已建，跳过')
        return
    build_soft('safety', 'safety_emergency_team', name='应急工作队伍',
               group='应急管理', fields=[
        ('队伍名称', 'text', True, 'none'),
        ('队长', 'text', False, 'none'),
        ('联系电话', 'text', False, 'none'),
        ('人数', 'number', False, 'none'),
        ('主要职责', 'text', False, 'none'),
    ], [
        {'队伍名称': '三合村义务消防队', '队长': '蓝先锋', '联系电话': make_phone(),
         '人数': 12, '主要职责': '初期火灾扑救、消防宣传与器材维护'},
        {'队伍名称': '三合村防汛抢险队', '队长': '莫大勇', '联系电话': make_phone(),
         '人数': 18, '主要职责': '汛期巡查值守、转移安置与抢险排涝'},
        {'队伍名称': '三合村治安巡逻队', '队长': '韦建国', '联系电话': make_phone(),
         '人数': 10, '主要职责': '夜间巡逻、重点时段值守与纠纷先期处置'},
    ])
    build_soft('safety', 'safety_emergency_supplies', name='应急储备物资',
               group='应急管理', fields=[
        ('物资名称', 'text', True, 'none'),
        ('数量', 'number', False, 'none'),
        ('单位', 'text', False, 'none'),
        ('存放地点', 'text', False, 'none'),
        ('保管人', 'text', False, 'none'),
        ('状态', 'text', False, 'none'),
    ], [
        {'物资名称': '干粉灭火器', '数量': 20, '单位': '具', '存放地点': '村委会器材室',
         '保管人': '蓝先锋', '状态': '完好'},
        {'物资名称': '救生衣', '数量': 30, '单位': '件', '存放地点': '村委会器材室',
         '保管人': '莫大勇', '状态': '完好'},
        {'物资名称': '防汛编织袋', '数量': 500, '单位': '条', '存放地点': '村委仓库',
         '保管人': '莫大勇', '状态': '完好'},
        {'物资名称': '铁锹', '数量': 15, '单位': '把', '存放地点': '村委仓库',
         '保管人': '韦建国', '状态': '完好'},
        {'物资名称': '应急照明灯', '数量': 10, '单位': '台', '存放地点': '村委会器材室',
         '保管人': '蓝先锋', '状态': '2 台待充电'},
    ])


def seed_sanitation():
    if soft_built('sanitation_team'):
        print('[环卫] 台账已建，跳过')
        return
    build_soft('sanitation', 'sanitation_team', [
        ('队伍名称', 'text', True, 'none'),
        ('队长', 'text', False, 'none'),
        ('队员人数', 'number', False, 'none'),
        ('负责区域', 'text', False, 'none'),
        ('工作内容', 'text', False, 'none'),
    ], [
        {'队伍名称': '思榜屯保洁队', '队长': '吴春燕', '队员人数': 5,
         '负责区域': '思榜屯主干道及公共区域',
         '工作内容': '每日清扫、垃圾分类督导、垃圾池巡检'},
        {'队伍名称': '旧寨屯保洁队', '队长': '莫大勇', '队员人数': 4,
         '负责区域': '旧寨屯主干道及河道沿线',
         '工作内容': '隔日清扫、河道漂浮物打捞'},
    ])
    build_soft('sanitation', 'sanitation_site', [
        ('设施名称', 'text', True, 'none'),
        ('设施类型', 'text', False, 'none'),
        ('所在位置', 'text', False, 'none'),
        ('数量', 'number', False, 'none'),
        ('状态', 'text', False, 'none'),
    ], [
        {'设施名称': '思榜屯垃圾池', '设施类型': '垃圾池', '所在位置': '思榜屯道场旁',
         '数量': 2, '状态': '完好'},
        {'设施名称': '分类垃圾桶（思榜）', '设施类型': '垃圾桶', '所在位置': '思榜屯主干道沿线',
         '数量': 30, '状态': '3 个桶盖破损'},
        {'设施名称': '旧寨屯公厕', '设施类型': '公厕', '所在位置': '旧寨屯文化室旁',
         '数量': 1, '状态': '完好'},
        {'设施名称': '挂臂式垃圾清运车', '设施类型': '清运车辆', '所在位置': '村委会车库',
         '数量': 1, '状态': '完好'},
    ])
    build_soft('sanitation', 'sanitation_cleanup', [
        ('清运日期', 'date', True, 'none'),
        ('清运车辆', 'text', False, 'none'),
        ('司机', 'text', False, 'none'),
        ('清运点位', 'text', False, 'none'),
        ('清运量（吨）', 'number', False, 'none'),
    ], [
        {'清运日期': '2026-11-02', '清运车辆': '桂B51888（挂臂车）', '司机': '莫大勇',
         '清运点位': '思榜屯 2 处垃圾池', '清运量（吨）': 1.2},
        {'清运日期': '2026-11-03', '清运车辆': '桂B51888（挂臂车）', '司机': '莫大勇',
         '清运点位': '旧寨屯垃圾池', '清运量（吨）': 0.8},
        {'清运日期': '2026-11-05', '清运车辆': '桂B51888（挂臂车）', '司机': '莫大勇',
         '清运点位': '全村 4 处点位', '清运量（吨）': 1.5},
        {'清运日期': '2026-11-08', '清运车辆': '桂B51888（挂臂车）', '司机': '莫大勇',
         '清运点位': '全村 4 处点位', '清运量（吨）': 1.1},
    ])


def seed_demand(kh):
    if Demand.query.count():
        print('[民生诉求] 已有数据，跳过')
        return
    # (来源, 类别, 状态, 诉求人, 村组, 摘要, 受理日, 办结日, 承办人, 加密信息?)
    rows = [
        (dict_pick('诉求来源', '12345', '12345'), '邻里纠纷', '待受理', '覃美英',
         '思榜',
         '反映邻居家鸡圈紧贴自家院墙，异味大、噪声扰民，要求协调搬迁。',
         date(2026, 11, 3), None, None, True),
        (dict_pick('诉求来源', '走访', 'visit'), '土地宅基地', '已办结', '梁天佑',
         '旧寨',
         '反映旧寨屯东面旱地与莫大勇家地界不清，申请村级核实处理。',
         date(2026, 10, 12), date(2026, 10, 18), '蓝先锋', True),
        (dict_pick('诉求来源', '电话', 'phone'), '其他', '已办结', '罗凤仙',
         '旧寨',
         '电话反映家中水龙头损坏、独居用水不便，请求帮忙联系维修。',
         date(2026, 10, 26), date(2026, 10, 27), '石兰花', False),
        (dict_pick('诉求来源', '来访', 'onsite'), '邻里纠纷', '不予受理', '韦贵生',
         '旧寨',
         '来访反映与韦某宅基地纠纷，该事项已进入司法程序，依法不予受理。',
         date(2026, 10, 8), None, None, False),
    ]
    demands = []
    for i, (src, cat, st, name, vg, summary, accept, close, officer, with_pii) in enumerate(rows):
        d = Demand(
            source=src, category=cat, petitioner_name=name, address=f'三合村{vg}屯',
            summary=summary, accept_date=accept, close_date=close,
            case_officer=officer or '', status=st, created_by='admin',
            remark='该事项已进入司法程序，按《信访工作条例》不予受理。'
            if st == '不予受理' else '')
        if with_pii:  # 部分诉求人补联系方式/身份证（走加密管线）
            enc(d, MAP_CONTACT, kh, contact=make_phone(),
                id_card=make_id_card(date(1968 if i == 0 else 1955, 2, 14), i == 1))
        db.session.add(d)
        demands.append(d)
    db.session.flush()
    # 办理记录：两条已办结诉求各 1-2 条
    db.session.add_all([
        DemandHandle(demand_id=demands[1].id, handle_date=date(2026, 10, 14),
                     handler='蓝先锋', action='会同驻村干部实地丈量争议旱地，查阅承包台账。'),
        DemandHandle(demand_id=demands[1].id, handle_date=date(2026, 10, 18),
                     handler='蓝先锋', action='组织双方调解，重新钉桩确认地界，双方签字认可。',
                     ),
        DemandHandle(demand_id=demands[2].id, handle_date=date(2026, 10, 27),
                     handler='石兰花', action='当日联系水管工上门更换水龙头，费用由村委公益金列支。'),
    ])
    db.session.commit()
    print('[民生诉求] 诉求 4（待受理/已办结×2/不予受理）、办理记录 3')


def seed_notice():
    if NoticeRecord.query.count() > 1:
        print('[补贴公示] 已有数据，跳过')
        return
    for tid, period in ((2, '2026-10'), (5, '2026-10')):
        t = NoticeTemplate.query.get(tid)
        if not t or NoticeRecord.query.filter_by(template_id=tid, period=period).first():
            continue
        db.session.add(NoticeRecord(
            template_id=t.id, notice_type=t.notice_type, period=period,
            title=t.title or t.name,
            table_data=json.dumps(t.schema_dict(), ensure_ascii=False),
            status='draft', created_by='admin'))
    db.session.commit()
    print('[补贴公示] 新增公示记录 2（特困供养/高龄补贴 2026-10 草稿）')


def seed_certificates(kh):
    if CertificateIssue.query.count():
        print('[证明开具] 已有数据，跳过')
        return
    from routes.certificate import _render_content, _format_date_cn
    issue_date = '2026-11-05'
    tpl_res = CertificateTemplate.query.filter_by(cert_type='居住证明').first()
    tpl_kin = CertificateTemplate.query.filter_by(cert_type='亲属关系证明').first()
    plans = []  # (模板, subject, 用途, values[中文键])
    if tpl_res:
        plans.append((tpl_res, '韦建国', '子女入学', {
            '姓名': '韦建国', '性别': '男',
            '身份证号': make_id_card(date(1962, 5, 12), True),
            '家庭住址': '广西融水苗族自治县融水镇三合村思榜屯5号',
            '联系电话': make_phone()}))
    if tpl_kin:
        plans.append((tpl_kin, '贾志明', '入学监护关系证明', {
            '姓名': '贾志明', '性别': '男',
            '身份证号': make_id_card(date(1978, 4, 22), True),
            '家庭住址': '广西融水苗族自治县融水镇三合村思榜屯11号',
            '关系人姓名': '贾雨欣', '关系人性别': '女',
            '关系人身份证号': make_id_card(date(2015, 6, 1), False),
            '关系人家庭住址': '同上', '与本人关系': '父女'}))
    if tpl_res:
        plans.append((tpl_res, '罗凤仙', '高龄津贴申请', {
            '姓名': '罗凤仙', '性别': '女',
            '身份证号': make_id_card(date(1941, 4, 9), False),
            '家庭住址': '广西融水苗族自治县融水镇三合村旧寨屯14号',
            '联系电话': make_phone()}))
    for i, (tpl, subject, purpose, values) in enumerate(plans, start=1):
        serial_no = f'ZM20261105{i:04d}'
        values.update({'证明编号': serial_no, '开具人': '超级管理员',
                       '开具单位': '三合村村民委员会',
                       '开具日期': _format_date_cn(issue_date),
                       '申请用途': purpose, '份数': 1})
        db.session.add(CertificateIssue(
            serial_no=serial_no, template_id=tpl.id, template_name=tpl.name,
            template_version=tpl.version or 1, cert_type=tpl.cert_type or '',
            subject_name=subject,
            subject_id_card=values.get('身份证号') or values.get('关系人身份证号') or '',
            form_data=json.dumps(values, ensure_ascii=False),
            rendered_content=_render_content(tpl.template_content or '', values),
            issuer_name='超级管理员', issue_unit='三合村村民委员会',
            purpose=purpose, copies=1,
            issued_at=datetime(2026, 11, 5, 10, i * 10), status='issued'))
    db.session.commit()
    print(f'[证明开具] 开具记录 {len(plans)}（居住×2、亲属关系×1）')


def seed_shop(kh):
    if Shop.query.count():
        print('[店铺] 已有数据，跳过')
        return
    shops = [
        ('三合村便民小卖部', '三合街', '12号', '韦文军', True, date(1988, 11, 2)),
        ('三合村农资服务站', '三合街', '25号', '莫大勇', True, date(1970, 7, 7)),
        ('秀兰理发店', '三合街', '8号', '黄秀兰', False, date(1958, 8, 18)),
        ('雨欣早餐店', '三合街', '31号', '莫丽萍', False, date(1980, 9, 15)),
    ]
    for name, street, no, owner, male, birth in shops:
        s = Shop(name=name, street=street, house_number=no, owner_name=owner,
                 phone=make_phone())
        enc(s, MAP_ID, kh, id_card=make_id_card(birth, male))
        db.session.add(s)
    db.session.commit()
    build_soft('shop', 'shop_facility', [
        ('场所名称', 'text', True, 'none'),
        ('设施类型', 'text', False, 'none'),
        ('所在位置', 'text', False, 'none'),
        ('数量', 'number', False, 'none'),
        ('完好状态', 'text', False, 'none'),
        ('管理人', 'text', False, 'none'),
    ], [
        {'场所名称': '村委会办公楼', '设施类型': '办公用房', '所在位置': '村委会大院',
         '数量': 1, '完好状态': '完好', '管理人': '韦建国'},
        {'场所名称': '村文化广场', '设施类型': '文体设施', '所在位置': '思榜屯',
         '数量': 1, '完好状态': '完好', '管理人': '梁玉芳'},
        {'场所名称': '篮球场', '设施类型': '文体设施', '所在位置': '村小学旁',
         '数量': 1, '完好状态': '球架油漆脱落', '管理人': '梁玉芳'},
        {'场所名称': '太阳能路灯', '设施类型': '照明设施', '所在位置': '思榜—旧寨连村道',
         '数量': 36, '完好状态': '2 盏不亮', '管理人': '蓝先锋'},
        {'场所名称': '人饮工程水柜', '设施类型': '饮水设施', '所在位置': '旧寨屯后山',
         '数量': 3, '完好状态': '完好', '管理人': '莫大勇'},
    ])
    build_soft('shop', 'shop_annual_fee', [
        ('场所名称', 'text', True, 'none'),
        ('缴费项目', 'text', True, 'none'),
        ('缴费年度', 'number', True, 'none'),
        ('实缴金额（元）', 'number', True, 'none'),
        ('缴费日期', 'date', False, 'none'),
        ('备注', 'text', False, 'none'),
    ], [
        {'场所名称': '三合村便民小卖部', '缴费项目': '垃圾处理费', '缴费年度': 2025,
         '实缴金额（元）': 240, '缴费日期': '2025-03-12'},
        {'场所名称': '三合村便民小卖部', '缴费项目': '垃圾处理费', '缴费年度': 2026,
         '实缴金额（元）': 240, '缴费日期': '2026-03-08'},
        {'场所名称': '三合村农资服务站', '缴费项目': '垃圾处理费', '缴费年度': 2025,
         '实缴金额（元）': 360, '缴费日期': '2025-04-02'},
        {'场所名称': '三合村农资服务站', '缴费项目': '垃圾处理费', '缴费年度': 2026,
         '实缴金额（元）': 360, '缴费日期': '2026-03-21'},
        {'场所名称': '秀兰理发店', '缴费项目': '垃圾处理费', '缴费年度': 2025,
         '实缴金额（元）': 180, '缴费日期': '2025-05-09'},
        {'场所名称': '秀兰理发店', '缴费项目': '垃圾处理费', '缴费年度': 2026,
         '实缴金额（元）': 180, '缴费日期': '2026-04-15',
         '备注': '2026-10-02 现场催缴补录'},
    ])


def seed_governance(kh):
    # 141（C-2）：第一代 GovernanceLedger 已退役，演示台账改走软表 V2 引擎入库
    # （与在线建表同一 build_records 通道：identity 加密 + HMAC 指纹 + 类型/枚举校验）
    from utils import softtable_engine as eng
    from utils.governance_catalog import GOV_CATEGORY_CODES
    from utils.governance_v2_migration import ensure_governance_v2_tables
    ensure_governance_v2_tables(migrate_legacy=False)
    _gov_tids = [t.id for t in DynamicTable.query.filter(
        DynamicTable.code.in_(list(GOV_CATEGORY_CODES.values()))).all()]
    if _gov_tids and DynamicRecord.query.filter(DynamicRecord.table_id.in_(_gov_tids)).count():
        print('[综治台账] 已有数据，跳过')
        return
    rows = [
        # (category, name, record_date, payload, with_id_card?)
        ('special-visit', '蓝海生', date(2026, 10, 15),
         {'village_group': '思榜', 'responsible_person': '蓝先锋',
          'visit_situation': '老人身体尚可，米面油充足；屋顶瓦片松动已登记修缮计划。'}, True),
        ('special-visit', '罗凤仙', date(2026, 11, 5),
         {'village_group': '旧寨', 'responsible_person': '石兰花',
          'visit_situation': '血压偏高，已叮嘱按时服药并预约镇卫生院随访。'}, True),
        ('stability-risk', '贾志明', date(2026, 9, 20),
         {'village_group': '思榜', 'risk_category': '邻里纠纷',
          'basic_situation': '与邻居因排水沟走向存在分歧，情绪平稳。',
          'work_measures': '已上门疏导两次，约定 11 月中旬组织现场协商。',
          'phone': make_phone()}, True),
        ('stability-risk', '韦贵生', date(2026, 10, 8),
         {'village_group': '旧寨', 'risk_category': '土地争议',
          'basic_situation': '宅基地界址争议已进入司法程序，本人配合。',
          'work_measures': '保持每月回访，引导走法律渠道，做好情绪安抚。'}, True),
        ('mental-health', '覃美英', date(2026, 10, 22),
         {'village_group': '思榜', 'responsible_person': '梁玉芳',
          'visit_situation': '近期情绪平稳，服药规律；家属监护到位。'}, False),
        ('mental-health', '梁天佑', date(2026, 11, 2),
         {'village_group': '旧寨', 'responsible_person': '莫大勇',
          'visit_situation': '睡眠欠佳，已协助预约县医院专科门诊。'}, False),
        ('security-committee', '韦建国', None,
         {'position': '主任', 'address': '三合村思榜屯5号',
          'remark': '2026 年 4 月换届当选'}, True),
        ('security-committee', '莫大勇', None,
         {'position': '委员', 'address': '三合村旧寨屯19号',
          'remark': '2026 年 4 月换届当选'}, True),
        # 2026-11-16：刑事人员（服刑 / 刑满释放同表，personnel_status 区分；原三类已下线）
        ('criminal', '韦文军', date(2026, 10, 30),
         {'personnel_status': dict_pick('刑事人员状态', '服刑', '服刑人员'),
          'village_group': '思榜', 'release_date': '',
          'help_type': dict_first('帮教类型', '其他'),
          'control_status': dict_first('帮教管控状态', '正常'),
          'original_sentence': '三年',
          'responsible_person': '韦建国',
          'visit_situation': '在县内服刑，家属定期探视，情绪稳定。',
          'help_measures': '对接司法所，落实家属帮扶与出监衔接。'}, True),
        ('criminal', '韦贵生', date(2026, 10, 18),
         {'personnel_status': dict_pick('刑事人员状态', '刑满释放', '刑满释放人员'),
          'village_group': '旧寨', 'release_date': '2026-05-20',
          'help_type': dict_first('帮教类型', '刑满释放'),
          'control_status': dict_first('帮教管控状态', '正常'),
          'original_sentence': '两年',
          'responsible_person': '蓝先锋',
          'visit_situation': '在县城工地务工，收入稳定，家庭关系融洽。',
          'help_measures': '帮教小组每月走访一次，落实低保复核。'}, True),
        ('road-hazard', '', date(2026, 10, 12),
         {'road_name': '思榜—旧寨连村道 K1+200 弯道', 'location': '旧寨屯口急弯段',
          'hazard_type': dict_first('道路隐患类型', '无护栏'),
          'hazard_level': dict_pick('隐患等级', '较大', '较大'),
          'found_date': '2026-10-12',
          'measure': '弯道外侧加装波形护栏 40 米，增设凸面镜 1 块。',
          'fix_status': dict_pick('整改状态', '施工', '整改中'),
          'owner': '蓝先锋', 'owner_phone': make_phone()}, False),
        ('key-vehicle', '韦贵生', date(2026, 9, 15),
         {'record_type': dict_first('道交登记类型', '摩托车'),
          'plate_no': '桂BGK286', 'vehicle_type': dict_first('车辆类型', '摩托车'),
          'license_status': dict_pick('证照情况', '齐全', '齐全'),
          'register_date': '2021-07-01',
          'record_status': dict_first('在册状态', '在册')}, True),
        ('guide-station', '', date(2026, 8, 1),
         {'station_name': '三合村交通安全劝导站',
          'location': '村口主干道旁', 'members': '蓝先锋、吴春燕',
          'work_days': '圩日及节假日', 'status': dict_first('劝导站运行状态', '正常运行'),
          'remark': '重点时段开展头盔佩戴与超载劝导。'}, False),
    ]
    for cat, name, rdate, payload, with_id in rows:
        payload = dict(payload)
        if with_id:
            payload['id_card'] = make_id_card(date(1975, 1, 1), True)
        tbl = DynamicTable.query.filter_by(code=GOV_CATEGORY_CODES[cat]).first()
        flds = (DynamicField.query.filter_by(table_id=tbl.id)
                .order_by(DynamicField.sort_order.asc(), DynamicField.id.asc()).all())
        specs = [{'code': f.code, 'display_name': f.display_name, 'data_type': f.data_type,
                  'sensitivity': f.sensitivity, 'dict_category_code': f.dict_category_code,
                  'is_required': f.is_required} for f in flds]
        codes = {sp['code'] for sp in specs}
        vals = {'name': name, 'phone': payload.pop('phone', '') or '',
                'record_date': (rdate or date(2026, 10, 1)).isoformat()}
        vals.update({k: v for k, v in payload.items() if k in codes})
        dropped = sorted(set(payload) - codes)
        if dropped:
            print(f'[综治台账] {cat}: 忽略目录外字段 {dropped}')
        recs, errs = eng.build_records(specs, [[vals.get(sp['code']) for sp in specs]])
        if errs:
            print(f'[综治台账] {cat}: 演示行未入库 {errs}')
            continue
        db.session.add(DynamicRecord(table_id=tbl.id, data=recs[0]['data'],
                                     digest=recs[0]['digest'], created_by='admin'))

    p1 = GovernancePetition(
        petition_no='XF2026-0001', source=dict_first('信访来源', '来访'),
        petitioner_name='梁天佑', address='三合村旧寨屯',
        category=dict_pick('信访诉求类别', '土地', '土地宅基地'),
        summary='反映同组村民占用其承包地修建鸡棚，要求责令拆除并恢复原状。',
        accept_date=date(2026, 9, 5), due_date=date(2026, 10, 5),
        case_officer='蓝先锋', status='已办结', close_date=date(2026, 9, 28),
        reply='经查所诉地块系荒坡地非承包地；鸡棚已自行拆除，双方达成和解。',
        satisfaction=dict_first('信访满意度', '满意'), created_by='admin')
    p2 = GovernancePetition(
        petition_no='XF2026-0002', source=dict_pick('信访来源', '电话', '电话'),
        petitioner_name='覃美英', address='三合村思榜屯',
        category=dict_pick('信访诉求类别', '惠农', '惠农补贴'),
        summary='咨询 2026 年低保金发放标准并反映到账金额与预期不符。',
        accept_date=date(2026, 10, 20), due_date=date(2026, 11, 19),
        case_officer='梁玉芳', status='办理中', created_by='admin')
    p3 = GovernancePetition(
        petition_no='XF2026-0003', source=dict_pick('信访来源', '网上', '网上'),
        petitioner_name='韦文军', address='三合村思榜屯',
        category=dict_pick('信访诉求类别', '邻里', '邻里纠纷'),
        summary='反映邻居堆放建材占用公共巷道，影响出行。',
        accept_date=date(2026, 11, 6), case_officer='', status='待受理',
        created_by='admin')
    for p in (p1, p2):
        enc(p, MAP_CONTACT, kh, contact=make_phone(),
            id_card=make_id_card(date(1955 if p is p1 else 1968, 1, 30), p is p1))
    db.session.add_all([p1, p2, p3])
    db.session.flush()
    db.session.add_all([
        GovernancePetitionHandle(
            petition_id=p1.id, handle_date=date(2026, 9, 12), handler='蓝先锋',
            action='会同自然资源所现场核查地块权属与鸡棚占地情况。', result='所诉地块为集体荒坡地。'),
        GovernancePetitionHandle(
            petition_id=p1.id, handle_date=date(2026, 9, 28), handler='蓝先锋',
            action='组织双方调解，鸡棚自行拆除，签订和解协议。', result='案结事了，双方满意。'),
        GovernancePetitionHandle(
            petition_id=p2.id, handle_date=date(2026, 10, 25), handler='梁玉芳',
            action='调取 2026 年 8-10 月低保发放回盘，逐项向信访人解释补助构成。', result='正在核对差额成因。'),
    ])
    db.session.commit()
    print('[综治] 台账 13（10 类各 1-2 条）、信访 3（三态）+ 办理记录 3')


def seed_party(kh):
    if PartyActivity.query.count():
        print('[党务] 活动已有数据，跳过')
        return
    branch_main = '中国共产党融水苗族自治县融水镇三合村思榜屯支部委员会'
    branch_old = '中国共产党融水苗族自治县融水镇三合村旧寨屯支部委员会'
    acts = [
        ('党员教育', '学习贯彻党的二十届四中全会精神', date(2026, 10, 25),
         '蓝先锋', 14, 13, branch_main,
         '集中学习全会公报，围绕乡村振兴展开讨论，形成 3 条建议。'),
        ('组织生活', '第三季度专题组织生活会', date(2026, 9, 28),
         '韦建国', 14, 14, branch_main,
         '对照检查开展批评与自我批评，收取意见建议 6 条。'),
        ('党风廉政', '警示教育片观摩暨廉政谈话', date(2026, 8, 15),
         '莫大勇', 12, 11, branch_old,
         '观看警示教育片，与村两委干部逐一开展廉政提醒谈话。'),
    ]
    for atype, title, adate, host, exp, act, branch, content in acts:
        db.session.add(PartyActivity(
            activity_type=atype, title=title, activity_date=adate, host=host,
            expected_count=exp, actual_count=act, branch=branch,
            content=content, created_by='admin'))
    db.session.flush()
    members = PartyMember.query.order_by(PartyMember.id).limit(2).all()
    if members:
        db.session.add(PartyActivityReview(
            year=2026, party_member_id=members[0].id, grade='优秀',
            review_date=date(2026, 8, 30), remark='带头发家致富，群众口碑好。',
            created_by='admin'))
        db.session.add(PartyActivityReview(
            year=2026, party_member_id=members[1].id, grade='合格',
            review_date=date(2026, 8, 30), remark='按时参加组织生活。', created_by='admin'))

    d1 = PartyDevelopment(
        name='蓝志强', gender='男', branch=branch_main, stage='发展对象',
        apply_date=date(2025, 11, 20), activist_date=date(2026, 5, 10),
        develop_date=date(2026, 10, 1), introducer='韦建国、蓝先锋',
        remark='致富带头人，递交申请书后表现积极。')
    d2 = PartyDevelopment(
        name='梁春花', gender='女', branch=branch_old, stage='申请人',
        apply_date=date(2026, 10, 20), remark='村小学代课教师，已递交入党申请书。')
    enc(d1, MAP_ID_PHONE, kh, id_card=make_id_card(date(1992, 3, 18), True),
        phone=make_phone())
    enc(d2, MAP_ID_PHONE, kh, id_card=make_id_card(date(1998, 12, 5), False),
        phone=make_phone())
    db.session.add_all([d1, d2])
    db.session.flush()
    db.session.add_all([
        PartyDevelopmentLog(dev_id=d1.id, from_stage='申请人', to_stage='积极分子',
                            stage_date=date(2026, 5, 10), operator='韦建国'),
        PartyDevelopmentLog(dev_id=d1.id, from_stage='积极分子', to_stage='发展对象',
                            stage_date=date(2026, 10, 1), operator='韦建国'),
    ])
    db.session.commit()
    print('[党务] 活动 3、评议 2；发展对象 2（发展对象/申请人）+ 阶段日志 2')


def seed_welfare(kh):
    if WelfareEmployment.query.count():
        print('[就业] 已有数据，跳过')
        return
    rows = [
        ('吴春燕', False, date(1987, 5, 28), '灵活就业', '灵活就业', '居民服务',
         '县城家政公司', '县外', date(2025, 7, 1), date(2026, 10, 31)),
        ('韦文军', True, date(1988, 11, 2), '已就业', '单位就业', '建筑',
         '柳州某建筑公司', '县外', date(2024, 3, 1), date(2026, 10, 31)),
        ('韦贵生', True, date(1996, 9, 9), '失业', '其他', '其他',
         '', '本地', date(2026, 9, 15), date(2026, 10, 31)),
    ]
    emps = []
    for name, male, birth, st, etype, ind, employer, loc, start, upd in rows:
        e = WelfareEmployment(
            name=name, gender='男' if male else '女', employment_status=st,
            employment_type=etype, industry=ind, employer=employer,
            work_location=loc, start_date=start, update_date=upd,
            village_group='思榜' if name != '韦贵生' else '旧寨',
            remark='失业人员已登记镇劳保所求职意向。' if st == '失业' else '',
            created_by='admin')
        enc(e, MAP_ID_PHONE, kh, id_card=make_id_card(birth, male),
            phone=make_phone())
        db.session.add(e)
        emps.append(e)
    db.session.add_all([
        WelfareFlexSubsidy(
            name='吴春燕', apply_year=2026, batch_no='融人社批〔2026〕12号',
            subsidy_type=dict_first('灵活就业补贴类型', '社保补贴'),
            amount=4260.0, apply_status='已审核', apply_date=date(2026, 9, 10),
            review_date=date(2026, 10, 15), remark='2026 年度社保补贴，待资金下达后发放。',
            created_by='admin'),
        WelfareFlexSubsidy(
            name='覃美英', apply_year=2025, batch_no='融人社批〔2025〕08号',
            subsidy_type=dict_pick('灵活就业补贴类型', '岗位', '岗位补贴'),
            amount=3600.0, apply_status='已发放', apply_date=date(2025, 8, 20),
            review_date=date(2025, 9, 12), issue_date=date(2025, 12, 28),
            created_by='admin'),
    ])
    db.session.commit()
    print('[就业] 就业台账 3（已就业/灵活就业/失业）、灵活补贴 2（已审核/已发放）')


def seed_service_records():
    if DisabledServiceRecord.query.count() or ElderServiceRecord.query.count():
        print('[关爱服务记录] 已有数据，跳过')
        return
    disabled = Disabled.query.order_by(Disabled.id).limit(2).all()
    items_d = get_dict_items('残疾人服务事项')
    for i, dp in enumerate(disabled):
        item = items_d[i].label if i < len(items_d) else '辅助器具适配'
        db.session.add(DisabledServiceRecord(
            disabled_id=dp.id, service_item=item,
            service_status='已完成' if i == 0 else '办理中',
            service_progress='辅助器具已发放并指导使用。' if i == 0 else '材料审核中。',
            service_owner='梁玉芳', service_date=date(2026, 9, 20),
            remark='残疾人精准康复服务行动。'))
    elders = CareTarget.query.filter(CareTarget.target_type == '老人').limit(2).all()
    items_e = get_dict_items('老年人服务事项')
    for i, ct in enumerate(elders):
        item = items_e[i].label if i < len(items_e) else '居家养老上门服务'
        db.session.add(ElderServiceRecord(
            care_target_id=ct.id, service_item=item,
            service_status='已完成' if i == 0 else '待受理',
            service_progress='助餐助医服务已完成当月上门。' if i == 0 else '',
            service_owner='石兰花', service_date=date(2026, 10, 28),
            remark='政府购买居家养老服务。'))
    db.session.commit()
    print('[关爱服务记录] 残疾人服务 2、老年人服务 2')


# ────────────────────────────── 清理/入口 ──────────────────────────────

FIXED_WIPE_ORDER = [
    DisabledServiceRecord, ElderServiceRecord,
    ElectionVoter, ElectionResult, ElectionRound,
    SafetyCheckHazard, SafetyCheck,
    DemandHandle, Demand,
    GovernancePetitionHandle, GovernancePetition,
    PartyActivityReview, PartyActivity, PartyDevelopmentLog, PartyDevelopment,
    WelfareEmployment, WelfareFlexSubsidy,
    CertificateIssue, Shop, NoticeRecord, CivConvention,
]

SOFT_CODES = ['civilization_activity', 'pension_roster',
              'sanitation_team', 'sanitation_site', 'sanitation_cleanup',
              'shop_facility', 'shop_annual_fee',
              'safety_emergency_team', 'safety_emergency_supplies']


def wipe_extra():
    for model in FIXED_WIPE_ORDER:
        model.query.delete(synchronize_session=False)
    for code in SOFT_CODES:
        t = DynamicTable.query.filter_by(code=code).first()
        if t:
            DynamicRecord.query.filter_by(table_id=t.id).delete(synchronize_session=False)
            DynamicField.query.filter_by(table_id=t.id).delete(synchronize_session=False)
    # 141（C-2）：综治 8 张固定 code 内置表只清演示记录，保留表与列定义
    from utils.governance_catalog import GOV_CATEGORY_CODES
    for t in DynamicTable.query.filter(
            DynamicTable.code.in_(list(GOV_CATEGORY_CODES.values()))).all():
        DynamicRecord.query.filter_by(table_id=t.id).delete(synchronize_session=False)
    db.session.commit()
    print('[wipe] 演示数据表已清空（基础种子未动）')


def ensure_key_holder(password: str):
    meta = load_key_meta()
    if meta is None:
        raise SystemExit('[错误] key_meta.json 不存在，请先运行 seed_test_data.py 初始化')
    salt = bytes.fromhex(meta['salt_hex'])
    iterations = int(meta.get('iterations', 310_000))
    if not key_holder.unlock(password, salt, iterations,
                             check_token=meta.get('check_token', '')):
        raise SystemExit('[错误] 启动口令不匹配，请用正确的 --password')
    print('[密钥] 已解锁')


def verify():
    import sqlite3
    conn = sqlite3.connect(os.environ['SHUCUN_DB_PATH'])
    cur = conn.cursor()
    tables = ['election_round', 'election_voter', 'election_result',
              'civ_convention', 'safety_checks', 'safety_check_hazards',
              'demand', 'demand_handle', 'notice_records', 'certificate_issues',
              'shops', 'governance_petition',   # 141：综治台账已迁软表 V2，行数见下方「软表行数」
              'governance_petition_handle', 'party_activity',
              'party_activity_review', 'party_development',
              'party_development_log', 'welfare_employment',
              'welfare_flex_subsidy', 'disabled_service_records',
              'elder_service_records', 'dynamic_field', 'dynamic_record']
    print('\n===== 演示数据行数 =====')
    for t in tables:
        print(f'{t}: {cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]}')
    print('\n===== 软表行数 =====')
    for code, name, n in cur.execute(
            'SELECT dt.code, dt.name, COUNT(dr.id) FROM dynamic_table dt '
            'LEFT JOIN dynamic_record dr ON dr.table_id = dt.id GROUP BY dt.id'):
        print(f'{name}({code}): {n}')
    bad = cur.execute(
        'SELECT COUNT(*) FROM residents WHERE id_card IS NOT NULL '
        'OR phone IS NOT NULL OR bank_card IS NOT NULL').fetchone()[0]
    print(f'\n[residents 抽检] 原列残留明文: {bad}（应为 0）')
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--password', default='Shucun2026')
    ap.add_argument('--wipe', action='store_true', help='先清空本脚本范围业务表再播种')
    args = ap.parse_args()

    ensure_key_holder(args.password)
    app = create_app()
    with app.app_context():
        if args.wipe:
            wipe_extra()
        seed_election(key_holder)
        seed_civilization()
        seed_pension()
        seed_safety()
        seed_emergency()
        seed_sanitation()
        seed_demand(key_holder)
        seed_notice()
        seed_certificates(key_holder)
        seed_shop(key_holder)
        seed_governance(key_holder)
        seed_party(key_holder)
        seed_welfare(key_holder)
        seed_service_records()
        db.session.commit()
        verify()
        print('\n[完成] 演示数据补种完毕')


if __name__ == '__main__':
    main()
