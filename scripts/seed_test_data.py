#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GUI 测试数据种子脚本（环境准备用，不属于业务代码）。

要点：
- 敏感字段（身份证/电话/银行卡）一律走 utils.sensitive_fields.set_encrypted_field
  加密写入 *_enc/_hash 列、原列置 NULL——与路由层写入口径完全一致，杜绝明文孤岛。
- 启动口令经 utils.crypto.key_holder 初始化/解锁，key_meta.json 落在
  SHUCUN_DATA_DIR（默认 = 项目根目录），GUI 测试启动服务时用同一口令解锁。
- 刻意不写旧表 subsidies：该表非空且 migration_record 无 done 记录会触发
  全局维护模式（app.enforce_migration_mode），锁死业务页面。补贴数据走
  新体系 subsidy_project / subsidy_record。
- 幂等：residents 已有数据时拒绝重复播种（--wipe 先清业务表再播种）。

用法：
    python3 scripts/seed_test_data.py [--password Shucun2026] [--wipe]
"""
import os
import sys
import json
import argparse
from datetime import date, datetime, timedelta

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
os.environ.setdefault('SHUCUN_DATA_DIR', BASE_DIR)
os.environ.setdefault('SHUCUN_DB_PATH', os.path.join(BASE_DIR, 'shucun.db'))
os.environ.setdefault('SHUCUN_UPLOAD_DIR', os.path.join(BASE_DIR, 'uploads'))
os.environ.setdefault('SHUCUN_BACKUP_DIR', os.path.join(BASE_DIR, 'backups'))
os.environ.setdefault('SHUCUN_TEMP_DIR', os.path.join(BASE_DIR, 'temp'))
sys.path.insert(0, BASE_DIR)

from app import create_app
from models import (
    db, User, Role, DictCategory, DictItem, Resident, ResidentTag,
    Dibao, Disabled, CareTarget, SpecialSupport, UnsupportedChild,
    PartyMember, PartyFee, PartyPosition, PartyTransfer,
    OrganizationMember, Veteran,
    SubsidyProject, SubsidyRecord, WelfareJob, Mediation,
    MedicalInsurance,
    TodoItem, DeathReport, VisitPlan, VisitRecord,
    WarnRecord, CertificateTemplate,
)
from utils.crypto import key_holder, load_key_meta, save_key_meta
from utils.sensitive_fields import set_encrypted_field

# ── 各模型敏感字段映射（与 routes/* 中 _SENS 定义逐一对应，勿改口径） ──
MAP_RESIDENT = {
    'id_card': ('id_card_enc', 'id_card_hash', 'id_card_prefix_hash'),
    'phone': ('phone_enc', 'phone_hash', 'phone_prefix_hash'),
    'bank_card': ('bank_card_enc', 'bank_card_hash', None),
}
MAP_DIBAO = {
    'household_id_card': ('household_id_card_enc', 'household_id_card_hash', None),
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'account_holder_id_card': ('account_holder_id_card_enc', 'account_holder_id_card_hash', None),
    'bank_card': ('bank_card_enc', 'bank_card_hash', None),
}
MAP_DISABLED = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
    'guardian_phone': ('guardian_phone_enc', 'guardian_phone_hash', None),
}
MAP_SPECIAL = {
    'household_id_card': ('household_id_card_enc', 'household_id_card_hash', None),
    'bank_card': ('bank_card_enc', 'bank_card_hash', None),
    'account_holder_id_card': ('account_holder_id_card_enc', 'account_holder_id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
MAP_CHILD = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}
MAP_TARGET = {'guardian_phone': ('guardian_phone_enc', 'guardian_phone_hash', None)}
MAP_PERSON = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
}  # party / organization / building 通用
MAP_VETERAN = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
    'relative_phone': ('relative_phone_enc', 'relative_phone_hash', None),
}
MAP_WELFARE = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'phone': ('phone_enc', 'phone_hash', None),
    'bank_info': ('bank_info_enc', 'bank_info_hash', None),
}
MAP_MEDIATION = {
    'applicant_id_card': ('applicant_id_card_enc', 'applicant_id_card_hash', None),
    'respondent_id_card': ('respondent_id_card_enc', 'respondent_id_card_hash', None),
}
MAP_MEDICAL = {
    'id_card': ('id_card_enc', 'id_card_hash', None),
    'contact_phone': ('contact_phone_enc', 'contact_phone_hash', None),
    'mobile_phone': ('mobile_phone_enc', 'mobile_phone_hash', None),
}
MAP_DEATH = {'id_card': ('id_card_enc', 'id_card_hash', None)}


def enc(instance, field_map, kh, **values):
    """按映射批量加密写字段（原列置 NULL）。"""
    for field, value in values.items():
        if value:
            set_encrypted_field(instance, field, value, field_map, kh)


# ── 证件号/手机号/银行卡生成（合法校验位，内容全部虚构） ──
_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_CHECK_CODES = '10X98765432'
_REGION = '450225'  # 融水苗族自治县（号码虚构）
_id_seq = [100]
_phone_seq = [0]
_card_seq = [0]


def make_id_card(birth: date, male: bool) -> str:
    _id_seq[0] += 2
    n = _id_seq[0] | 1 if male else _id_seq[0] & ~1
    body = f'{_REGION}{birth:%Y%m%d}{n:03d}'
    s = sum(int(c) * w for c, w in zip(body, _WEIGHTS))
    return body + _CHECK_CODES[s % 11]


def make_phone() -> str:
    _phone_seq[0] += 1
    prefixes = ['138', '158', '187', '159', '136', '177', '135', '188']
    return f"{prefixes[_phone_seq[0] % len(prefixes)]}7746{_phone_seq[0]:04d}"


def make_bank_card() -> str:
    _card_seq[0] += 1
    return f'6228480402564{_card_seq[0]:05d}8'  # 19 位，虚构


# ── 人员数据（姓名、证件号均为虚构测试数据） ──
# key, 户号, 姓名, 男?, 出生日期, 与户主关系, 所在组
PEOPLE = [
    ('weijianguo',  '思榜-001', '韦建国', True,  date(1962, 5, 12),  '户主本人', '思榜'),
    ('liangyufang', '思榜-001', '梁玉芳', False, date(1965, 3, 8),   '配偶',     '思榜'),
    ('weiwenjun',   '思榜-001', '韦文军', True,  date(1988, 11, 2),  '子女',     '思榜'),
    ('lanhaisheng', '思榜-002', '蓝海生', True,  date(1944, 2, 15),  '户主本人', '思榜'),
    ('lanxiaoyu',   '思榜-002', '蓝小雨', False, date(2016, 7, 20),  '孙女',     '思榜'),
    ('jiazhiming',  '思榜-003', '贾志明', True,  date(1978, 4, 22),  '户主本人', '思榜'),
    ('moliping',    '思榜-003', '莫丽萍', False, date(1980, 9, 15),  '配偶',     '思榜'),
    ('jiayuxin',    '思榜-003', '贾雨欣', False, date(2015, 6, 1),   '子女',     '思榜'),
    ('jiayutong',   '思榜-003', '贾雨彤', False, date(2018, 12, 10), '子女',     '思榜'),
    ('liangtianyou', '旧寨-001', '梁天佑', True,  date(1955, 1, 30),  '户主本人', '旧寨'),
    ('huangxiulan', '旧寨-001', '黄秀兰', False, date(1958, 8, 18),  '配偶',     '旧寨'),
    ('luofengxian', '旧寨-002', '罗凤仙', False, date(1941, 4, 9),   '户主本人', '旧寨'),
    ('lanxianfeng', '思榜-004', '蓝先锋', True,  date(1985, 10, 5),  '户主本人', '思榜'),
    ('wuchunyan',   '思榜-004', '吴春燕', False, date(1987, 5, 28),  '配偶',     '思榜'),
    ('lanxiaohu',   '思榜-004', '蓝小虎', True,  date(2010, 3, 16),  '子女',     '思榜'),
    ('modayong',    '旧寨-003', '莫大勇', True,  date(1970, 7, 7),   '户主本人', '旧寨'),
    ('shilanhua',   '旧寨-003', '石兰花', False, date(1972, 11, 23), '配偶',     '旧寨'),
    ('weiguisheng', '旧寨-004', '韦贵生', True,  date(1996, 9, 9),   '户主本人', '旧寨'),
    ('qinmeiying',  '思榜-005', '覃美英', False, date(1968, 2, 14),  '户主本人', '思榜'),
    ('weizhonghun', '思榜-006', '韦忠魂', True,  date(1950, 6, 25),  '户主本人', '思榜'),
]

HOUSEHOLDS = {
    '思榜-001': ('韦建国', '一般农户'), '思榜-002': ('蓝海生', '五保户'),
    '思榜-003': ('贾志明', '脱贫户'),   '旧寨-001': ('梁天佑', '一般农户'),
    '旧寨-002': ('罗凤仙', '一般农户'), '思榜-004': ('蓝先锋', '监测户'),
    '旧寨-003': ('莫大勇', '一般农户'), '旧寨-004': ('韦贵生', '一般农户'),
    '思榜-005': ('覃美英', '脱贫户'),   '思榜-006': ('韦忠魂', '一般农户'),
}

TAGS = {
    'lanhaisheng': ['残疾', '独居老人', '高龄老人', '五保户', '特困供养'],
    'lanxiaoyu': ['困境儿童'],
    'jiazhiming': ['脱贫户'],
    'moliping': ['脱贫户'],
    'jiayuxin': ['留守儿童'],
    'jiayutong': ['困境儿童'],
    'liangtianyou': ['低保'],
    'huangxiulan': ['残疾'],
    'luofengxian': ['低保', '困难老人', '高龄老人'],
    'lanxianfeng': ['监测户'],
    'wuchunyan': ['监测户'],
    'lanxiaohu': ['留守儿童'],
    'qinmeiying': ['低保', '残疾', '脱贫户'],
    'weizhonghun': ['低保'],
}

# key: (flow_status, status, 婚姻, 兵役, 学历, 健康, 政治面貌)
EXTRA_RESIDENT = {
    'weijianguo':  ('常住', 'normal', '已婚', '已服兵役', '初中', '健康', '中共党员'),
    'liangyufang': ('常住', 'normal', '已婚', '未服兵役', '小学', '一般', '群众'),
    'weiwenjun':   ('流出', 'normal', '未婚', '未服兵役', '大专', '健康', '群众'),
    'lanhaisheng': ('常住', 'normal', '丧偶', '未服兵役', '文盲', '残疾', '群众'),
    'lanxiaoyu':   ('常住', 'normal', '未婚', '未服兵役', '无',   '健康', '群众'),
    'jiazhiming':  ('常住', 'normal', '已婚', '已服兵役', '初中', '健康', '群众'),
    'moliping':    ('常住', 'normal', '已婚', '未服兵役', '小学', '健康', '群众'),
    'jiayuxin':    ('常住', 'normal', '未婚', '未服兵役', '小学', '健康', '群众'),
    'jiayutong':   ('常住', 'normal', '未婚', '未服兵役', '无',   '一般', '群众'),
    'liangtianyou': ('常住', 'normal', '已婚', '退役', '小学', '长期慢性病', '群众'),
    'huangxiulan': ('常住', 'normal', '已婚', '未服兵役', '小学', '残疾', '群众'),
    'luofengxian': ('常住', 'normal', '丧偶', '未服兵役', '文盲', '长期慢性病', '群众'),
    'lanxianfeng': ('常住', 'normal', '已婚', '已服兵役', '高中/中专', '健康', '中共党员'),
    'wuchunyan':   ('常住', 'normal', '已婚', '未服兵役', '初中', '健康', '群众'),
    'lanxiaohu':   ('常住', 'normal', '未婚', '未服兵役', '无',   '健康', '共青团员'),
    'modayong':    ('常住', 'normal', '已婚', '退役', '初中', '健康', '中共党员'),
    'shilanhua':   ('常住', 'normal', '已婚', '未服兵役', '小学', '一般', '群众'),
    'weiguisheng': ('流入', 'normal', '未婚', '已服兵役', '大专', '健康', '共青团员'),
    'qinmeiying':  ('常住', 'normal', '离异', '未服兵役', '初中', '残疾', '群众'),
    'weizhonghun': ('常住', 'deceased', '丧偶', '未服兵役', '小学', '重大疾病', '群众'),
}

BANKS = ['农村信用合作社', '广西农村商业银行', '中国邮政储蓄银行', '中国农业银行', '桂林银行']

ADD_DICT = {
    '原服役兵种': ['陆军', '海军', '空军', '火箭军', '战略支援部队', '联勤保障部队', '武警部队', '其他'],
    '户籍性质': ['农业户口', '非农业户口'],
    '安置情况': ['自主就业', '安排工作', '退休', '供养', '其他'],
    '机构类型': ['村两委', '党支部', '村监督委员会', '共青团', '妇联', '民兵连', '其他'],
    '组织职务': ['书记', '副书记', '委员', '主任', '副主任', '主席', '副主席', '连长', '指导员', '其他'],
    # GLB-44：与 init_db 对齐，补特困供养表单下拉
    '供养方式': ['集中供养', '分散供养', '亲属供养', '其他'],
    '供养机构': ['镇敬老院', '县福利院', '村级照料中心', '其他'],
}

# 清理顺序（子表在前，父表在后）
WIPE_ORDER = [
    WarnRecord, SubsidyRecord, SubsidyProject,
    VisitRecord, VisitPlan, ResidentTag,
    CareTarget, SpecialSupport, UnsupportedChild,
    Dibao, Disabled, PartyFee, PartyPosition, PartyTransfer, PartyMember,
    OrganizationMember, Veteran, WelfareJob,
    Mediation, MedicalInsurance,
    TodoItem, DeathReport, CertificateTemplate,
    Resident,
]


def ensure_key_holder(password: str):
    meta = load_key_meta()
    if meta is None:
        meta = key_holder.initialize(password)
        save_key_meta(meta)
        print(f'[密钥] 已初始化 key_meta.json（启动口令：{password}）')
    else:
        salt = bytes.fromhex(meta['salt_hex'])
        iterations = int(meta.get('iterations', 310_000))
        if not key_holder.unlock(password, salt, iterations,
                                 check_token=meta.get('check_token', '')):
            raise SystemExit('[错误] key_meta.json 已存在且口令不匹配，请用正确的 --password')
        print('[密钥] 已用既有 key_meta.json 解锁')


def ensure_admin():
    user = User.query.filter_by(username='admin').first()
    if user is None:
        user = User(username='admin', real_name='超级管理员', role_id=1,
                    is_active=True, must_change_password=False)
        user.set_password('admin123')
        db.session.add(user)
        print('[用户] 创建 admin/admin123')
        return
    from werkzeug.security import check_password_hash
    if not check_password_hash(user.password_hash, 'admin123'):
        user.set_password('admin123')
        user.must_change_password = False
        print('[用户] admin 原口令未知，已重置为 admin123')
    else:
        user.must_change_password = False
        print('[用户] admin 口令确认为 admin123')


def ensure_clerk():
    if User.query.filter_by(username='clerk01').first():
        print('[用户] clerk01 已存在，跳过')
        return
    role = Role.query.filter_by(name='民政主任').first() or Role.query.get(2)
    user = User(username='clerk01', real_name='张经办', role_id=role.id,
                is_active=True, must_change_password=False)
    user.set_password('Ab123456')
    db.session.add(user)
    print(f'[用户] 创建 clerk01/Ab123456（角色：{role.name}）')


def ensure_dict():
    """补齐缺失字典分类（对齐 init_db.py），并补 居民标签/特困供养（同 ensure_tag_dict 口径）。"""
    added = []
    for name, items in ADD_DICT.items():
        if DictCategory.query.filter_by(name=name).first():
            continue
        max_sort = db.session.query(db.func.max(DictCategory.sort_order)).scalar() or 0
        cat = DictCategory(name=name, description=name, sort_order=max_sort + 10)
        db.session.add(cat)
        db.session.flush()
        for idx, label in enumerate(items):
            db.session.add(DictItem(category_id=cat.id, label=label, value=label,
                                    sort_order=idx))
        added.append(name)
    tag_cat = DictCategory.query.filter_by(name='居民标签').first()
    if tag_cat and not DictItem.query.filter_by(category_id=tag_cat.id,
                                                value='特困供养').first():
        db.session.add(DictItem(category_id=tag_cat.id, label='特困供养',
                                value='特困供养', sort_order=99))
        added.append('居民标签/特困供养')
    print('[字典] 补齐：%s' % ('、'.join(added) if added else '无缺失'))


def today_age(birth):
    t = date.today()
    return t.year - birth.year - ((t.month, t.day) < (birth.month, birth.day))


def seed_residents(kh):
    residents = {}
    today = date.today()
    for idx, (key, hcode, name, male, birth, rel, group) in enumerate(PEOPLE):
        flow, status, marital, military, edu, health, politics = EXTRA_RESIDENT[key]
        r = Resident(
            name=name, gender='男' if male else '女', birth_date=birth,
            household_name=HOUSEHOLDS[hcode][0], household_code=hcode,
            village_group=group, grid=f'{group}网格',
            address=f'广西融水苗族自治县融水镇三合村{group}屯{idx * 3 + 5}号',
            relationship=rel, household_type='农业户口',
            household_property=HOUSEHOLDS[hcode][1],
            politics_status=politics, education=edu, health_status=health,
            marital_status=marital, military_status=military,
            nation='苗族' if key in ('lanhaisheng', 'luofengxian') else '汉族',
            occupation='务农',
            flow_status=flow, status=status, source='手工',
            status_date=today - timedelta(days=14) if status == 'deceased' else None,
            status_reason='病故' if status == 'deceased' else None,
        )
        id_card = make_id_card(birth, male)
        phone = make_phone()
        bank = make_bank_card()
        r._seed_plain_id = id_card
        r._seed_plain_phone = phone
        r._seed_plain_bank = bank
        enc(r, MAP_RESIDENT, kh, id_card=id_card, phone=phone, bank_card=bank)
        r.bank_name = BANKS[idx % len(BANKS)]
        db.session.add(r)
        db.session.flush()
        residents[key] = r
        for tag in TAGS.get(key, []):
            db.session.add(ResidentTag(resident_id=r.id, tag_code=tag,
                                       source='手动', status='有效', operator=1))
    db.session.flush()
    print(f"[居民] {len(residents)} 人（死亡 1、流出 1），标签 {sum(len(v) for v in TAGS.values())} 条")
    return residents


def seed_dibao(kh, R):
    # (key, 类别, 是否户主, 与户主关系, 户金额, 人数, 开始享受, 户主key)
    rows = [
        ('liangtianyou', 'A类', True, '户主本人', 1450.0, 2, date(2024, 3, 1), 'liangtianyou'),
        ('huangxiulan', 'A类', False, '配偶', 1450.0, 2, date(2024, 3, 1), 'liangtianyou'),
        ('luofengxian', 'B类', True, '户主本人', 780.0, 1, date(2023, 10, 1), 'luofengxian'),
        ('qinmeiying', 'C类', True, '户主本人', 520.0, 1, date(2025, 1, 1), 'qinmeiying'),
        ('weizhonghun', 'B类', True, '户主本人', 760.0, 1, date(2024, 6, 1), 'weizhonghun'),
    ]
    for key, dtype, is_head, rel, amount, count, start, head_key in rows:
        r = R[key]
        head = R[head_key]
        d = Dibao(
            resident_id=r.id, region='融水镇三合村', grid=r.grid,
            household_name=head.name, name=r.name, is_head='是' if is_head else '否',
            relationship=rel, start_date=start, change_time=date(2026, 1, 10),
            dibao_type=dtype, sub_category='农村低保', beneficiary_count=count,
            household_amount=amount, payment_month='2026-08',
            account_holder=head.name, account_holder_identity='户主',
            bank_type=r.bank_name, address=r.address, registered_address=r.address,
            health_status=r.health_status,
            poverty_registered='是' if '脱贫户' in TAGS.get(key, []) else '否',
            is_deceased='是' if r.status == 'deceased' else '否', status='有效',
        )
        enc(d, MAP_DIBAO, kh,
            household_id_card=head._seed_plain_id, id_card=r._seed_plain_id,
            account_holder_id_card=head._seed_plain_id, bank_card=r._seed_plain_bank)
        db.session.add(d)
    db.session.flush()
    print('[低保] 5 条（4 户，A/B/C 类覆盖，含 1 名死亡在册）')


def seed_disabled(kh, R):
    rows = [
        ('huangxiulan', '听力', '三级', '双耳听力下降，佩戴助听器', 60.0, 0.0),
        ('qinmeiying', '肢体', '二级', '右腿截肢，行动不便', 100.0, 80.0),
        ('lanhaisheng', '视力', '二级', '白内障晚期，视力模糊', 100.0, 80.0),
    ]
    for i, (key, dtype, level, detail, living, nursing) in enumerate(rows, start=1):
        r = R[key]
        d = Disabled(
            resident_id=r.id, village_group=r.village_group, name=r.name,
            gender=r.gender, nation=r.nation, education=r.education,
            registered_address=r.address,
            cert_no=f'G450225{r.birth_date:%Y%m%d}{i:03d}',
            disability_type=dtype, disability_level=level, disability_detail=detail,
            subsidy_living=living, subsidy_nursing=nursing, status='有效',
        )
        enc(d, MAP_DISABLED, kh, id_card=r._seed_plain_id,
            phone=r._seed_plain_phone, guardian_phone=make_phone())
        db.session.add(d)
    db.session.flush()
    print('[残疾] 3 条（听力/肢体/视力，含两项补贴）')


def seed_care(kh, R):
    targets = [
        ('jiayuxin', '留守儿童', '儿童', None, '留守', '贾志明',
         '广东东莞某电子厂', '三合村小学', '父母长期在外务工', '学习辅导缺人督促', '已同意'),
        ('jiayutong', '困境儿童', '儿童', None, '困境', '莫丽萍',
         '', '三合村幼儿园', '家庭经济困难', '学前教育费用压力', '已同意'),
        ('lanxiaohu', '留守儿童', '儿童', None, '留守', '蓝先锋',
         '柳州某建筑工地', '融水镇中心小学', '父亲外出务工', '周末看护缺位', '待补录'),
        ('lanxiaoyu', '困境儿童', '儿童', None, '困境', '蓝海生',
         '', '', '事实无人抚养', '生活照料与心理关爱', '已同意'),
        ('lanhaisheng', '独居老人', '老人', None, None, '蓝先锋（侄）',
         '', '', '独居无子女照料', '日常探视与助医', '已同意'),
        ('luofengxian', '困难老人', '老人', '困难', None, '石兰花（邻居）',
         '', '', '高龄独居收入低', '助餐与居家养老服务', '已同意'),
    ]
    for key, ctype, ttype, etype, child_type, guardian, workplace, school, reason, need, consent in targets:
        t = CareTarget(
            resident_id=R[key].id, name=R[key].name, care_type=ctype,
            age=today_age(R[key].birth_date), target_type=ttype, elder_type=etype,
            child_type=child_type, guardian=guardian, parent_workplace=workplace,
            school=school, main_difficulty=reason, difficulty_reason=reason,
            need=need, source='手工建档', guardian_consent=consent,
            guardian_consent_time=datetime.now() - timedelta(days=20),
            guardian_consent_way='书面', status='有效',
        )
        enc(t, MAP_TARGET, kh, guardian_phone=make_phone())
        db.session.add(t)
    r = R['lanhaisheng']
    sp = SpecialSupport(
        village_group=r.village_group, household_name='蓝海生',
        beneficiary_count=1, guarantee_amount=865.0, support_mode='分散供养',
        support_institution='', start_date=date(2020, 6, 1),
        change_time=date(2026, 1, 5), payment_month='2026-08',
        account_holder='蓝海生', account_holder_identity='本人',
        bank_type=r.bank_name, address=r.address, registered_address=r.address,
        self_care_ability='半自理', status='有效', remark='特困供养人员台账',
    )
    enc(sp, MAP_SPECIAL, kh, household_id_card=r._seed_plain_id,
        bank_card=r._seed_plain_bank, account_holder_id_card=r._seed_plain_id,
        phone=r._seed_plain_phone)
    db.session.add(sp)
    c = R['lanxiaoyu']
    uc = UnsupportedChild(
        village_group=c.village_group, amount=1200.0, name=c.name, gender=c.gender,
        birth_date=c.birth_date, father_status='死亡', mother_status='改嫁失联',
        health_status='健康', cardholder_relation='祖父', address=c.address,
        remark='事实无人抚养儿童基本生活补贴',
    )
    enc(uc, MAP_CHILD, kh, id_card=c._seed_plain_id, phone=c._seed_plain_phone)
    db.session.add(uc)
    db.session.flush()
    print('[关爱] 关爱对象 6 + 特困供养 1 + 事实无人抚养儿童 1')


def seed_org_party(kh, R):
    orgs = [
        ('weijianguo', '村两委', '主任', date(2018, 1, 1)),
        ('lanxianfeng', '党支部', '书记', date(2020, 1, 1)),
        ('modayong', '村监督委员会', '委员', date(2021, 3, 1)),
        ('liangyufang', '妇联', '主席', date(2019, 6, 1)),
    ]
    for key, otype, pos, start in orgs:
        r = R[key]
        o = OrganizationMember(
            org_type=otype, name=r.name, position=pos,
            politics_status=r.politics_status, start_date=start, is_current=True)
        enc(o, MAP_PERSON, kh, id_card=r._seed_plain_id, phone=r._seed_plain_phone)
        db.session.add(o)

    branches = {
        'weijianguo': '中国共产党融水苗族自治县融水镇三合村思榜屯支部委员会',
        'lanxianfeng': '中国共产党融水苗族自治县融水镇三合村思榜屯支部委员会',
        'modayong': '中国共产党融水苗族自治县融水镇三合村旧寨屯支部委员会',
        'qinmeiying': '中国共产党融水苗族自治县融水镇三合村思榜屯支部委员会',
    }
    party_rows = []
    for key, branch in branches.items():
        r = R[key]
        p = PartyMember(
            name=r.name, gender=r.gender, birth_date=r.birth_date, nation=r.nation,
            education=r.education, current_address=r.address,
            join_date=date(2008, 6, 20) if key != 'lanxianfeng' else date(2012, 7, 1),
            confirm_date=date(2009, 6, 20) if key != 'lanxianfeng' else date(2013, 7, 1),
            member_type='正式党员', branch=branch,
            member_identity='正式党员', member_mobility='在家',
            employment_status='在职', party_status='在册')
        enc(p, MAP_PERSON, kh, id_card=r._seed_plain_id, phone=r._seed_plain_phone)
        db.session.add(p)
        party_rows.append(p)
    db.session.flush()
    for p in party_rows:
        for month in (6, 7, 8):
            db.session.add(PartyFee(
                member_id=p.id, fee_year=2026, fee_month=month,
                monthly_income=3200.0, amount=2.0, paid_amount=2.0, status='normal'))
    db.session.add(PartyPosition(
        member_id=party_rows[1].id, position_name='支部书记',
        start_date=date(2020, 1, 1), remark='现任思榜屯支部书记'))
    db.session.add(PartyTransfer(
        member_id=party_rows[0].id, transfer_type='转入',
        transfer_date=date(2015, 4, 10),
        from_branch='中国共产党融水苗族自治县融水镇机关支部委员会',
        to_branch=branches['weijianguo'], reason='工作调动'))
    db.session.flush()
    print('[组织] 村组织 4、党员 4（党费 12）、党内职务/组织关系各 1')


def seed_veteran(kh, R):
    vets = [
        ('modayong', date(1990, 12, 1), date(1994, 12, 1), 4, '陆军', '中士',
         '原广州军区某集团军', '退字第1994012号', '韦雪梅', ''),
        ('liangtianyou', date(1974, 1, 10), date(1979, 3, 10), 5, '陆军', '上等兵',
         '原广西军区某边防团', '退字第1979033号', '梁小勇', '重点优抚对象'),
    ]
    for i, (key, en, dis, years, branch, rank, unit, cert, rname, remark) in enumerate(vets, start=1):
        r = R[key]
        v = Veteran(
            name=r.name, gender=r.gender, nation=r.nation,
            politics_status=r.politics_status, education=r.education,
            registered_residence=r.address, home_address=r.address, grid=r.grid,
            relative_name=rname, enlist_date=en, discharge_date=dis,
            service_years=years, service_unit=unit, service_branch=branch,
            service_rank=rank, discharge_cert_no=cert,
            household_reg_type='农业户口', placement_status='自主就业',
            serial_no=i, remark=remark)
        enc(v, MAP_VETERAN, kh, id_card=r._seed_plain_id, phone=r._seed_plain_phone,
            relative_phone=make_phone())
        db.session.add(v)
    db.session.flush()
    print('[退役军人] 2 条')


def seed_subsidy(kh, R):
    p1 = SubsidyProject(name='高龄津贴', project_type='高龄津贴', cycle='月',
                        default_amount=60, age_min=80, status='启用',
                        target_tags=json.dumps(['高龄老人'], ensure_ascii=False),
                        create_by=1, remark='80 周岁以上老年人')
    p2 = SubsidyProject(name='困难老人补助', project_type='困难老人补助', cycle='月',
                        default_amount=50, age_min=60, age_max=79, status='启用',
                        target_tags=json.dumps(['困难老人'], ensure_ascii=False),
                        create_by=1, remark='60-79 周岁困难老年人')
    p3 = SubsidyProject(name='低保金', project_type='低保金', cycle='月',
                        default_amount=320, status='启用',
                        target_tags=json.dumps(['低保'], ensure_ascii=False), create_by=1)
    p4 = SubsidyProject(name='残疾人两项补贴', project_type='残疾补贴', cycle='月',
                        default_amount=80, status='启用',
                        target_tags=json.dumps(['残疾'], ensure_ascii=False), create_by=1)
    db.session.add_all([p1, p2, p3, p4])
    db.session.flush()
    globals()['PROJECT_IDS'] = {'高龄津贴': p1.id, '困难老人补助': p2.id,
                                '低保金': p3.id, '残疾补贴': p4.id}

    recs = []
    for key in ('lanhaisheng', 'luofengxian'):
        for month in ('2026-06', '2026-07', '2026-08'):
            recs.append((R[key], p1, month, 60.0))
    for key in ('liangtianyou', 'luofengxian', 'qinmeiying', 'weizhonghun'):
        for month in ('2026-07', '2026-08'):
            recs.append((R[key], p3, month, 320.0))
    for key in ('huangxiulan', 'qinmeiying'):
        recs.append((R[key], p4, '2026-08', 80.0))
    for r, p, period, amount in recs:
        y, m = period.split('-')
        db.session.add(SubsidyRecord(
            resident_id=r.id, project_id=p.id,
            issue_date=date(int(y), int(m), 15), period=period, amount=amount,
            channel='镇级直发', source='normal', status='正常'))
    db.session.flush()
    print(f'[补贴] 项目 4 个，发放记录 {len(recs)} 条')


def seed_daily(kh, R):
    w1 = WelfareJob(name='莫大勇', village_group='旧寨', job_type='护林员',
                    start_date=date(2025, 1, 1), monthly_amount=800.0,
                    bank_type='农村信用合作社')
    enc(w1, MAP_WELFARE, kh, id_card=R['modayong']._seed_plain_id,
        phone=R['modayong']._seed_plain_phone, bank_info=make_bank_card())
    w2 = WelfareJob(name='吴春燕', village_group='思榜', job_type='保洁员',
                    start_date=date(2025, 7, 1), monthly_amount=600.0,
                    bank_type='中国邮政储蓄银行')
    enc(w2, MAP_WELFARE, kh, id_card=R['wuchunyan']._seed_plain_id,
        phone=R['wuchunyan']._seed_plain_phone, bank_info=make_bank_card())
    db.session.add_all([w1, w2])

    m1 = Mediation(
        case_no='MT20260528001', dispute_type='土地纠纷', location='旧寨屯东面旱地',
        applicant=R['liangtianyou'].name, applicant_village_group='旧寨',
        respondent=R['modayong'].name, people_count=2, status='closed',
        start_date=date(2026, 5, 28), end_date=date(2026, 6, 2),
        mediator='蓝先锋', mediation_date=date(2026, 6, 2),
        content='双方因旱地地界争议发生口角，申请村级调解。',
        result='经现场丈量并查阅承包台账，重新钉桩确认地界，双方签字认可。')
    enc(m1, MAP_MEDIATION, kh, applicant_id_card=R['liangtianyou']._seed_plain_id,
        respondent_id_card=R['modayong']._seed_plain_id)
    m2 = Mediation(
        case_no='MT20260828001', dispute_type='邻里纠纷', location='思榜屯道场旁',
        applicant=R['weijianguo'].name, applicant_village_group='思榜',
        respondent=R['lanxianfeng'].name, people_count=2, status='mediating',
        start_date=date(2026, 8, 28), mediator='莫大勇',
        content='因排水沟占用引发邻里矛盾，正在调解中。')
    enc(m2, MAP_MEDIATION, kh, applicant_id_card=R['weijianguo']._seed_plain_id,
        respondent_id_card=R['lanxianfeng']._seed_plain_id)
    db.session.add_all([m1, m2])

    med_rows = [
        ('weijianguo', True, '是'), ('liangtianyou', True, '是'),
        ('luofengxian', True, '是'), ('qinmeiying', True, '否'),
        ('weiwenjun', False, '是'), ('jiazhiming', False, '否'),
        ('lanxianfeng', False, '是'), ('modayong', False, '否'),
    ]
    for i, (key, linked, paid) in enumerate(med_rows, start=1):
        r = R[key]
        mi = MedicalInsurance(
            seq_no=i, name=r.name, gender=r.gender, address=r.address,
            village_group=r.village_group, household_name=r.household_name,
            household_property=r.household_property, payment_year=2026,
            insurance_status='正常参保', is_paid=paid, is_state_paid='否',
            is_deductible='否', is_relocated='否', data_source='医保下发',
            verify_status='未核对', resident_id=r.id if linked else None,
            link_status='已关联居民' if linked else '未关联居民')
        enc(mi, MAP_MEDICAL, kh, id_card=r._seed_plain_id,
            contact_phone=f'0772-58{1000 + i}', mobile_phone=r._seed_plain_phone)
        db.session.add(mi)

    db.session.add(TodoItem(
        title='9月10日前完成高龄津贴发放名单核对', todo_type='补贴管理',
        status='todo', remind_time=datetime(2026, 9, 9, 9, 0),
        content='与 8 月银行回盘对账，确认蓝海生、罗凤仙到账情况。'))
    db.session.add(TodoItem(
        title='跟进贾雨欣助学申请材料', todo_type='关爱服务', status='todo',
        remind_time=datetime(2026, 9, 15, 17, 0)))
    db.session.add(TodoItem(
        title='更新村务公开栏第三期内容', todo_type='村务公开', status='done',
        remind_time=datetime(2026, 8, 28, 9, 0), completed_by='admin',
        completed_at=datetime(2026, 8, 30, 16, 20)))

    r = R['weizhonghun']
    d = DeathReport(
        village='三合村', village_group='思榜', deceased_name=r.name,
        address=r.address, gender='男', age=today_age(r.birth_date), nation='汉族',
        death_date=date(2026, 8, 20), person_type='低保户',
        remark='家属口头报丧，待村级确认并走停发流程。',
        report_time=datetime(2026, 8, 21, 9, 0), report_unit='三合村村民委员会',
        reporter='梁玉芳', cascade_status='pending')
    enc(d, MAP_DEATH, kh, id_card=r._seed_plain_id)
    db.session.add(d)
    db.session.flush()
    print('[日常台账] 公益岗 2、调解 2、医保 8、务工 3、风险 2、'
          '待办 3、死亡报告 1')


def seed_visit(kh, R):
    pids = globals().get('PROJECT_IDS', {})
    p1 = VisitPlan(plan_name='2026 年第三季度低保残疾人入户走访', plan_type='按标签',
                   scope_params=json.dumps({'tags': ['低保', '残疾']}, ensure_ascii=False),
                   target_count=6, status='进行中', created_by=1,
                   created_at=datetime(2026, 8, 1, 9, 0))
    p2 = VisitPlan(plan_name='重阳节高龄老人走访（草稿）', plan_type='按对象类型',
                   scope_params=json.dumps({'types': ['老人']}, ensure_ascii=False),
                   target_count=2, status='草稿', created_by=1,
                   created_at=datetime(2026, 9, 1, 10, 0))
    db.session.add_all([p1, p2])
    db.session.flush()
    visits = [
        ('weijianguo', date(2026, 8, 12), '入户', 0, None),
        ('liangtianyou', date(2026, 8, 15), '入户', 1,
         '申请提高低保补助标准，已登记上报。'),
        ('huangxiulan', date(2026, 8, 18), '电话', 0, None),
    ]
    for key, vdate, vtype, has_need, need in visits:
        db.session.add(VisitRecord(
            plan_id=p1.id, resident_id=R[key].id, visit_date=vdate, visitor_id=1,
            visit_type=vtype,
            content='完成季度走访，家庭情况基本稳定。' if not has_need
            else '入户了解困难诉求并登记。',
            has_need=has_need, need_desc=need, create_by=1,
            create_time=datetime.combine(vdate, datetime.min.time())))
    if pids.get('低保金'):
        db.session.add(WarnRecord(
            resident_id=R['liangtianyou'].id, project_id=pids['低保金'],
            warn_type='周期到期', trigger_reason='低保金 2026-08 发放后超周期未见新发放',
            status='未处置', create_time=datetime(2026, 9, 1, 8, 30)))
    if pids.get('困难老人补助'):
        db.session.add(WarnRecord(
            resident_id=R['luofengxian'].id, project_id=pids['困难老人补助'],
            warn_type='年龄边界',
            trigger_reason='居民罗凤仙 85 周岁，超出「困难老人补助」年龄上限 79',
            status='未处置', create_time=datetime(2026, 9, 1, 8, 30)))
    db.session.flush()
    print('[走访/预警] 走访计划 2（进行中+草稿）、走访记录 3、预警 2（未处置）')


def seed_certs():
    if CertificateTemplate.query.count():
        print('[证明模板] 已存在，跳过')
        return
    db.session.add_all([
        CertificateTemplate(name='常住证明（默认）', cert_type='residence',
                            template_content='兹证明 {name}（身份证号：{id_card}），'
                                             '现居住于 {address}，特此证明。\n\n{date}'),
        CertificateTemplate(name='亲属关系证明（默认）', cert_type='kinship',
                            template_content='兹证明 {name_a}（身份证号：{id_card_a}）与 '
                                             '{name_b}（身份证号：{id_card_b}）系 {relation} 关系。\n\n{date}'),
        CertificateTemplate(name='死亡证明（默认）', cert_type='death',
                            template_content='兹证明 {name}（身份证号：{id_card}），'
                                             '于 {death_date} 因 {death_reason} 去世。\n\n{date}'),
    ])
    print('[证明模板] 3 个默认模板')


def verify():
    import sqlite3
    conn = sqlite3.connect(os.environ['SHUCUN_DB_PATH'])
    cur = conn.cursor()
    print('\n===== 各表行数 =====')
    tables = ['residents', 'resident_tag', 'dibao', 'disabled_persons', 'care_targets',
              'special_support', 'unsupported_children', 'party_members', 'party_fees',
              'organization_members', 'veteran', 'subsidy_project',
              'subsidy_record', 'welfare_jobs', 'mediations',
              'medical_insurance_payments',
              'todo_items', 'death_reports',
              'visit_plan', 'visit_record', 'warn_record', 'certificate_templates']
    for t in tables:
        print(f'{t}: {cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]}')
    bad_plain = cur.execute(
        'SELECT COUNT(*) FROM residents WHERE id_card IS NOT NULL '
        'OR phone IS NOT NULL OR bank_card IS NOT NULL').fetchone()[0]
    enc_ok = cur.execute(
        'SELECT COUNT(*) FROM residents WHERE id_card_enc IS NOT NULL '
        'AND id_card_hash IS NOT NULL AND id_card_prefix_hash IS NOT NULL').fetchone()[0]
    print(f'\n[residents 加密抽检] 原列残留明文: {bad_plain}（应为 0）；'
          f'密文+哈希齐全: {enc_ok}（应等于居民数）')
    for name, ln, h in cur.execute(
            'SELECT name, LENGTH(id_card_enc), id_card_hash FROM residents '
            'ORDER BY id LIMIT 2'):
        print(f'  样本 {name}: 密文 {ln} 字符, hash {h[:12]}…')
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--password', default='Shucun2026',
                    help='启动口令（≥8 位，含字母和数字）')
    ap.add_argument('--wipe', action='store_true', help='先清空业务表再播种')
    args = ap.parse_args()

    ensure_key_holder(args.password)
    app = create_app()
    with app.app_context():
        if Resident.query.count() > 0:
            if not args.wipe:
                raise SystemExit('[中止] residents 已有数据。如需重播请加 --wipe')
            print('[wipe] 清空业务表…')
            for model in WIPE_ORDER:
                model.query.delete(synchronize_session=False)
            db.session.commit()

        ensure_admin()
        ensure_clerk()
        ensure_dict()
        R = seed_residents(key_holder)
        seed_dibao(key_holder, R)
        seed_disabled(key_holder, R)
        seed_care(key_holder, R)
        seed_org_party(key_holder, R)
        seed_veteran(key_holder, R)
        seed_subsidy(key_holder, R)
        seed_daily(key_holder, R)
        seed_visit(key_holder, R)
        seed_certs()
        db.session.commit()
        verify()
        print('\n[完成] 测试数据已写入 shucun.db')


if __name__ == '__main__':
    main()
