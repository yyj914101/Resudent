# -*- coding: utf-8 -*-
"""tests/test_080_a_enum_alignment.py — 080 A 轨枚举/映射对齐验收。

红→绿：四项枚举/映射在 080 代码前不存在或为旧值 → 本文件至少 6 例红；
绿态断言如下（R-1 单一真源 / A-1 党员维度 / A-3 兵役别名 / A-4 医保 /
A-5 机构类型 / A-6 救助类别与映射）。
"""
import io
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXPECTED_SEEDS = {
    '人员类别': ['正式党员', '预备党员', '流动党员', '已转出'],
    '流动状态': ['常住', '流入', '流出'],
    '兵役状况': ['未服兵役', '已服兵役', '退役', '现役'],
    # 094 B-9 / 2026-11-16：重点人员收敛为「刑事人员」两类——
    # 社区矫正、社区戒毒康复两类人员整类下线，「矫正类别 / 戒毒阶段 / 矫正管控状态 /
    # 戒毒管控状态」四个字典同步移除（存量库由 m044 清理）
    '重点人员类别': ['服刑人员', '刑满释放人员'],
    '刑事人员状态': ['服刑人员', '刑满释放人员'],
    '帮教类型': ['刑满释放', '假释', '暂予监外执行', '其他'],
    '帮教管控状态': ['正常', '重点帮教', '失联', '已解除'],
    '党员身份': ['正式党员', '预备党员'],
    '党员流动状态': ['在家', '流动'],
    '党员在职状态': ['在职', '退休'],
    '党员在册状态': ['在册', '已转出'],
    '医保参保情况': ['正常参保', '暂停参保', '未参保'],
    '机构类型': ['村两委', '党支部', '村监督委员会', '共青团', '妇联', '民兵连',
                 '其他', '志愿者协会', '残疾人协会', '关工委'],
    # R-4 字典拍板（2026-11-09）：救助类别按低保记录实际业务域 = A~E 类；
    # 农村低保 / 城市低保下沉为「救助细类」。旧十类口径（低保/特困供养/高龄…）作废。
    '救助类别': ['A类', 'B类', 'C类', 'D类', 'E类'],
    '救助细类': ['农村低保', '城市低保'],
    # 优抚对象（2026-11-16）：因病退役 / 因残退役 / 涉核工程
    '优抚对象救助类别': ['因病退役', '因残退役', '涉核工程'],
    # 082 B 三分类（收敛并入 DICT_SEEDS）
    '医保待遇认证状态': ['已认证', '未认证', '待认证'],
    '救助动态状态': ['正常', '停发', '新增'],
    '退役重点群体': ['参战参试', '带病回乡', '伤残军人', '烈士遗属', '因公牺牲军人遗属',
                     '病故军人遗属', '在乡老复员军人', '其他'],
    # 093 B-6/B-7/B-8 四分类（服务事项 + 办理状态 + 退役类别）
    '残疾人服务事项': ['残疾证代办', '康复服务对接', '两项补贴申报'],
    '老年人服务事项': ['老年证代办', '高龄补贴申报', '居家养老对接'],
    '服务办理状态': ['待受理', '办理中', '已完成', '已终止'],
    '退役类别': ['义务兵退役', '士官退役', '军官退役', '转业', '复员', '离退休', '其他'],
    # 084 就业服务五分类（DICT_SEEDS 唯一真源，随 084 并入）
    '就业状态': ['已就业', '灵活就业', '未就业', '失业', '其他'],
    '就业类型': ['单位就业', '个体经营', '灵活就业', '公益性岗位', '其他'],
    '就业行业': ['农林牧渔', '建筑', '制造', '批发零售', '住宿餐饮', '居民服务', '其他'],
    '灵活就业补贴类型': ['社保补贴', '岗位补贴', '一次性补贴', '其他'],
    '补贴申报状态': ['待申报', '已申报', '已审核', '已发放', '未通过'],
    # 086 平安扩展十二分类（道交 8 + 信访 4）
    '道路隐患类型': ['路面破损', '视距不良', '临水临崖', '缺少护栏', '照明缺失',
                     '标志标线缺失', '其他'],
    '隐患等级': ['一般', '较大', '重大'],
    '整改状态': ['未整改', '整改中', '已整改', '已验收'],
    '道交登记类型': ['车辆', '驾驶人'],
    '车辆类型': ['小型客车', '面包车', '货车', '三轮车', '摩托车', '拖拉机', '其他'],
    '证照情况': ['证照齐全', '逾期未检', '无证', '其他'],
    '在册状态': ['在册', '已注销', '已迁出'],
    '运行状态': ['正常', '暂停', '已撤销'],
    '信访来源': ['来信', '来访', '网上', '电话', '转办', '其他'],
    '信访诉求类别': ['邻里纠纷', '土地宅基地', '惠农补贴', '涉法涉诉', '干部作风', '其他'],
    '信访办理状态': ['待受理', '办理中', '已办结', '不予受理'],
    '信访满意度': ['满意', '基本满意', '不满意', '未评价'],
    # 088 养老保险：参保类型 + 参保状态 + 待遇认证（镜像 DICT_SEEDS，键集相等断言）
    '养老保险类型': ['职工养老', '城乡居民养老', '其他'],
    '养老保险参保状态': ['正常参保', '暂停参保', '未参保'],
    '养老待遇认证状态': ['已认证', '未认证', '待认证'],
    # 083A 党务活动两分类（R-083A-P2-1：字典分类扩容同步闭集契约，键集相等不放宽）
    '党务活动类型': ['党员教育', '组织生活', '党风廉政'],
    '民主评议等次': ['优秀', '合格', '基本合格', '不合格'],
    # 083B 党员发展五阶段（同上：分类扩容同步闭集契约）
    '党员发展阶段': ['申请人', '积极分子', '发展对象', '预备党员', '正式党员'],
    # 090B 公共事务·选举三分类（一页纸 v1.2 §5 字面量；镜像 DICT_SEEDS，键集相等断言）
    '选举届次状态': ['筹备', '选民登记', '投票', '结果公示', '已完成'],
    '选民登记状态': ['已登记', '未登记', '委托投票', '外出'],
    '当选职务': ['村委会主任', '村委会副主任', '村委会委员', '其他'],
    # 090A 民生诉求三分类（镜像 DICT_SEEDS；「诉求来源」(label, value) 元组形态，value 编码见一页纸 D-01）
    '诉求来源': [('12345 热线', '12345'), ('走访', 'visit'), ('网格', 'grid'),
                ('电话', 'phone'), ('来访', 'onsite'), ('其他', 'other')],
    '诉求类别': ['邻里纠纷', '土地宅基地', '其他'],
    '诉求办理状态': ['待受理', '已办结', '不予受理'],
    # 090C 环卫工作一分类（镜像 DICT_SEEDS，闭集键集相等不放宽；软表类型字段已按 R-01/R-02 删除）
    '整治记录类型': ['日常保洁', '垃圾清运', '专项整治', '其他'],
    # 085 场所/设施：设施类型 3 分类（R-085-02，与 DICT_SEEDS 逐字一致）
    '设施类型': ['清洁点', '消防栓', '路灯'],
    # 089 文明实践站（镜像 DICT_SEEDS 089 段；不建「公约状态」字典，is_current 唯一真源）
    '文明活动类型': ['理论宣讲', '教育服务', '文化服务', '科技科普', '健身体育'],
}

# 085 R-085-03：value≠label 的结构化种子例外——不适用 DICT_SEEDS（label-only）结构，
# 真源由 init_db 元组与 scripts/migrations/m039_venue_facility.py 两处同值播种。
STRUCTURED_SEED_EXCEPTIONS = {
    '缴费项目': [('clean_fee', '清洁费（街道门店）')],
}
ORG_TYPE_BASE = EXPECTED_SEEDS['机构类型'][:7]
ORG_TYPE_NEW = EXPECTED_SEEDS['机构类型'][7:]


def _login(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s['user_id'] = 1
    return client


# ── A. R-1 单一真源 ──────────────────────────────────────────────────

def test_dict_seeds_single_source():
    from utils.dict_seed import DICT_SEEDS, ENUM_SEEDS
    # 080 返工 M5-1：恢复「键集相等」硬断言（返工误放宽为 ⊆ 会静默放过多余分类）
    assert set(DICT_SEEDS) == set(EXPECTED_SEEDS)
    for name, labels in EXPECTED_SEEDS.items():
        assert DICT_SEEDS[name] == labels, name
    # 079 兼容别名必须与 DICT_SEEDS 同一对象（非副本）
    assert ENUM_SEEDS is DICT_SEEDS
    assert ENUM_SEEDS == DICT_SEEDS


def test_first_install_references_dict_seeds():
    from utils.dict_seed import DICT_SEEDS
    for rel in ('init_db.py', 'app.py'):
        src = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        assert 'DICT_SEEDS' in src, rel
        assert 'dict_data.update' in src, rel
        # 080 返工 R-080-14：字面量扫描由 3 类硬编码子集扩为 DICT_SEEDS 全类动态遍历
        #（082 并入新分类后若在首装侧回添字面量，此处即红，拦 R-1 回归）
        for category in DICT_SEEDS:
            pattern = re.compile(r"'%s'\s*:\s*\[" % category)
            assert not pattern.search(src), '%s 仍有 %s 字面量' % (rel, category)


def test_ensure_enum_dicts_backfills_org_type(isolated_app_factory):
    from models import db, DictCategory, DictItem
    from utils.dict_seed import ensure_enum_dicts
    from utils.dict_enum import get_enum, reset_enum_cache
    app = isolated_app_factory()
    with app.app_context():
        cat = DictCategory.query.filter_by(name='机构类型').first()
        assert cat is not None
        for label in ORG_TYPE_NEW:
            rows = DictItem.query.filter_by(category_id=cat.id, label=label).all()
            assert len(rows) == 1, label
            db.session.delete(rows[0])
        db.session.commit()
        assert ensure_enum_dicts() == (0, 3)
        assert ensure_enum_dicts() == (0, 0)
        reset_enum_cache()
        values = list(get_enum('机构类型'))
        assert values == EXPECTED_SEEDS['机构类型']
        assert values[:7] == ORG_TYPE_BASE
        assert values[7:] == ORG_TYPE_NEW


# ── B. A-1 党员类别拆维度 ────────────────────────────────────────────

def test_party_columns_added(isolated_app_factory):
    from models import db
    app = isolated_app_factory()
    with app.app_context():
        cols = set(db.metadata.tables['party_members'].columns.keys())
        for col in ('member_identity', 'member_mobility',
                    'employment_status', 'party_status'):
            assert col in cols, col
        assert 'member_type' in cols, '历史列 member_type 必须保留'


def test_m025_backfill_and_idempotent(tmp_path):
    import importlib.util
    db_path = tmp_path / 'm025.db'
    conn = sqlite3.connect(str(db_path))
    conn.execute('CREATE TABLE party_members (id INTEGER PRIMARY KEY, member_type VARCHAR(20))')
    conn.executemany('INSERT INTO party_members (member_type) VALUES (?)',
                     [('正式党员',), ('预备党员',), ('流动党员',), ('已转出',), (None,)])
    conn.commit()
    conn.close()

    spec = importlib.util.spec_from_file_location(
        'm025_party_dimensions',
        os.path.join(ROOT, 'scripts', 'migrations', 'm025_party_dimensions.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    first = mod.run(str(db_path))
    assert first['added_columns'] == ['member_identity', 'member_mobility',
                                      'employment_status', 'party_status']
    assert first['skipped_columns'] == []
    assert first['updated_rows'] == 5
    assert first['identity_blank'] == 3
    second = mod.run(str(db_path))
    assert second['added_columns'] == []
    assert second['skipped_columns'] == ['member_identity', 'member_mobility',
                                         'employment_status', 'party_status']
    assert second['updated_rows'] == 0

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        'SELECT member_type, member_identity, member_mobility, employment_status, '
        'party_status FROM party_members ORDER BY id').fetchall()
    conn.close()
    assert [(r[1], r[2], r[4]) for r in rows] == [
        ('正式党员', '在家', '在册'),
        ('预备党员', '在家', '在册'),
        (None, '流动', '在册'),
        (None, None, '已转出'),
        (None, None, '在册'),
    ]
    assert [r[3] for r in rows] == [None] * 5


def test_politics_mapping_equivalent(isolated_app_factory):
    from routes.party import _resident_politics
    app = isolated_app_factory()
    with app.app_context():
        assert _resident_politics('预备党员', '在家', '在册') == '中共预备党员'
        assert _resident_politics('正式党员', '在家', '在册') == '中共党员'
        assert _resident_politics('', '流动', '在册') == '中共党员'
        assert _resident_politics('', '', '已转出') == '群众'
        assert _resident_politics('不存在', '', '') == ''
        assert _resident_politics('', '', '') == ''


def test_summary_retired_and_legacy_equivalence(isolated_app_factory):
    from models import PartyMember, Resident, db
    app = isolated_app_factory()
    with app.app_context():
        db.session.query(PartyMember).delete()
        db.session.query(Resident).delete()
        db.session.add_all([
            PartyMember(name='正式甲', member_identity='正式党员', member_mobility='在家',
                        employment_status='退休', party_status='在册', branch='A支部'),
            PartyMember(name='正式乙', member_identity='正式党员', member_mobility='在家',
                        employment_status='在职', party_status='在册', branch='B支部'),
            PartyMember(name='预备丙', member_identity='预备党员', member_mobility='在家',
                        party_status='在册', branch=''),
            PartyMember(name='流动丁', member_identity='', member_mobility='流动',
                        party_status='在册', branch='A支部'),
            PartyMember(name='转出戊', member_identity='', member_mobility='',
                        party_status='已转出', branch='A支部'),
            Resident(name='自动己', politics_status='中共党员', id_card_hash='a' * 64),
        ])
        db.session.commit()

    resp = _login(app).get('/party/api/summary')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'], body
    d = body['data']
    assert d['formal'] == 2
    assert d['probation'] == 1
    assert d['floating'] == 1
    assert d['transferred'] == 1
    assert d['retired'] == 1
    assert d['on_roll'] == 4
    assert d['total_archived'] == 5
    assert d['auto'] == 1
    stats = {x['branch']: x['count'] for x in d['branch_stats']}
    assert stats['A支部'] == 2, '已转出人员不得计入支部人数'
    assert stats['B支部'] == 1


# ── C. A-3 兵役状况别名 ──────────────────────────────────────────────

def test_military_alias_normalization(isolated_app_factory):
    from utils.import_engine import apply_value_domain, _military_status_domain
    app = isolated_app_factory()
    with app.app_context():
        mapping = _military_status_domain()
        assert mapping['1'] == '未服兵役'
        assert mapping['2'] == '已服兵役'
        assert mapping['未服役'] == '未服兵役'
        assert mapping['已服役'] == '已服兵役'
        assert mapping['退伍'] == '退役'
        assert mapping['复员'] == '退役'
        assert mapping['服役中'] == '现役'
        assert apply_value_domain('military_status', '1') == '未服兵役'
        assert apply_value_domain('military_status', '2') == '已服兵役'
        assert apply_value_domain('military_status', '未服役') == '未服兵役'
        assert apply_value_domain('military_status', '已服役') == '已服兵役'
        assert apply_value_domain('military_status', '退伍') == '退役'
        assert apply_value_domain('military_status', '复员') == '退役'
        assert apply_value_domain('military_status', '服役中') == '现役'
        assert apply_value_domain('military_status', '9') == '9'
        assert apply_value_domain('military_status', None) is None


# ── D. A-4 医保参保情况 ──────────────────────────────────────────────

def test_insurance_status_single_source(isolated_app_factory):
    from routes.medical_insurance import DEFAULT_INSURANCE_STATUS
    from utils.dict_enum import get_enum, reset_enum_cache
    expected = ['正常参保', '暂停参保', '未参保']
    assert DEFAULT_INSURANCE_STATUS == expected
    app = isolated_app_factory()
    with app.app_context():
        reset_enum_cache()
        assert list(get_enum('医保参保情况')) == expected


# ── E. A-5 机构类型补值 ──────────────────────────────────────────────

def test_org_type_added_values(isolated_app_factory):
    from utils.dict_enum import get_enum, reset_enum_cache
    app = isolated_app_factory()
    with app.app_context():
        reset_enum_cache()
        values = list(get_enum('机构类型'))
        assert values == EXPECTED_SEEDS['机构类型']
        assert values[:7] == ORG_TYPE_BASE


# ── F. A-6 救助类别与来源映射 ────────────────────────────────────────

def test_rescue_dict_and_mapping(isolated_app_factory):
    from utils.dict_enum import get_enum, reset_enum_cache
    from utils.rescue_category import map_rescue_category
    app = isolated_app_factory()
    with app.app_context():
        reset_enum_cache()
        assert list(get_enum('救助类别')) == EXPECTED_SEEDS['救助类别']
        # R-4（2026-11-09）后 dibao 记录的救助类别存 A~E 类，旧十类 label 不再在字典内
        assert map_rescue_category('dibao', 'A类') == 'A类'
        assert map_rescue_category('dibao', '低保') is None
        assert map_rescue_category('disabled', None) == '残疾'
        assert map_rescue_category('care_target', '高龄') == '高龄'
        assert map_rescue_category('care_target', '留守') is None
        assert map_rescue_category('notice', 'dibao') == '低保'
        assert map_rescue_category('notice', 'temp_relief') == '临时救助'
        assert map_rescue_category('notice', 'veteran') == '优抚'
        assert map_rescue_category('notice', 'public_welfare') is None
        assert map_rescue_category('unknown', None) is None


def test_rescue_has_no_stats_wiring():
    for rel in ('utils/agg_cache.py', 'routes/report.py'):
        src = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        assert 'rescue_category' not in src, '%s 不得引用 rescue_category（本任务零接线）' % rel


# ── G. 返工（review-080-20261104 放行条件）────────────────────────────

def test_member_type_parse_legacy_and_combo(isolated_app_factory):
    """R-080-05：导入「人员类别」解析——组合串逆向还原 + 旧单值 m025 同口径。"""
    from routes.party import _parse_member_type
    from utils.dict_enum import reset_enum_cache
    app = isolated_app_factory()
    with app.app_context():
        reset_enum_cache()
        assert _parse_member_type('') == ('', '', '', '')
        assert _parse_member_type('已转出') == ('', '', '', '已转出')
        assert _parse_member_type('正式党员') == ('正式党员', '在家', '', '在册')
        assert _parse_member_type('预备党员') == ('预备党员', '在家', '', '在册')
        assert _parse_member_type('流动党员') == ('', '流动', '', '在册')
        assert _parse_member_type('其他') == ('', '', '', '在册')
        assert _parse_member_type('正式党员 · 在家 · 退休') == ('正式党员', '在家', '退休', '在册')
        assert _parse_member_type('流动') == ('', '流动', '', '在册')
        assert _parse_member_type('退休') == ('', '', '退休', '在册')
        assert _parse_member_type('正式党员 · 流动') == ('正式党员', '流动', '', '在册')


def test_export_import_roundtrip_keeps_dimensions(isolated_app_factory):
    """R-080-05：导出→导入往返四维相等；R-080-19：导出表头「手机号」回导不再 500。"""
    from models import PartyMember, db
    from routes.party import _set_enc
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())
    app = isolated_app_factory()
    with app.app_context():
        db.session.query(PartyMember).delete()
        db.session.commit()
        rows = []
        for name, ic, dims in (
                ('往返甲', '110101199001010011', ('正式党员', '在家', '退休', '在册')),
                ('往返乙', '110101199001010022', ('', '流动', '', '在册')),
                ('往返丙', '110101199001010033', ('', '', '', '已转出')),
                # M4：此前失真的两形态——已转出不再吃掉维度、单身份不再臆造「在家」
                ('往返丁', '110101199001010044', ('正式党员', '流动', '退休', '已转出')),
                ('往返戊', '110101199001010055', ('正式党员', '', '', '在册'))):
            pm = PartyMember(name=name, member_identity=dims[0], member_mobility=dims[1],
                             employment_status=dims[2], party_status=dims[3], branch='A支部')
            _set_enc(pm, 'id_card', ic)
            db.session.add(pm)
            rows.append((name, dims))
        db.session.commit()

    client = _login(app)
    exported = client.get('/party/api/export')
    assert exported.status_code == 200

    resp = client.post('/party/api/import',
                       data={'file': (io.BytesIO(exported.data), 'roundtrip.xlsx'),
                             'mode': 'overwrite'},
                       content_type='multipart/form-data')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['success'], body

    with app.app_context():
        got = {m.name: (m.member_identity, m.member_mobility,
                        m.employment_status, m.party_status)
               for m in PartyMember.query.all()}
    for name, dims in rows:
        assert got.get(name) == dims, '%s 往返维度失真: %r != %r' % (name, got.get(name), dims)


def test_summary_excludes_transferred_from_identity_counts(isolated_app_factory):
    """R-080-04：formal/probation/floating 排除已转出——「转出+保留身份」不计入。"""
    from models import PartyMember, db
    app = isolated_app_factory()
    with app.app_context():
        db.session.query(PartyMember).delete()
        db.session.add_all([
            PartyMember(name='转出保留身份', member_identity='正式党员',
                        member_mobility='流动', party_status='已转出'),
            PartyMember(name='转出预备', member_identity='预备党员', party_status='已转出'),
            PartyMember(name='在册正式', member_identity='正式党员',
                        member_mobility='在家', party_status='在册'),
        ])
        db.session.commit()
    resp = _login(app).get('/party/api/summary')
    assert resp.status_code == 200
    d = resp.get_json()['data']
    assert d['formal'] == 1
    assert d['probation'] == 0
    assert d['floating'] == 0
    assert d['transferred'] == 2
    assert d['on_roll'] == 1


def test_party_dimensions_startup_self_heal(isolated_app_factory):
    """R-080-03：无四维列的存量库 → 启动自愈补列 + m025 同源回填；半迁移态同批治愈。"""
    from sqlalchemy import text
    from models import db
    app = isolated_app_factory()
    with app.app_context():
        # 还原「未跑 m025 的存量库」形态：party_members 无四维列 + 旧值行
        for t in ('party_fees', 'party_transfers', 'party_positions', 'party_members'):
            db.session.execute(text('DROP TABLE IF EXISTS %s' % t))
        db.session.execute(text(
            'CREATE TABLE party_members (id INTEGER PRIMARY KEY, name VARCHAR(50), '
            'member_type VARCHAR(20))'))
        db.session.execute(text(
            "INSERT INTO party_members (name, member_type) VALUES "
            "('甲','正式党员'),('乙','流动党员'),('丙','已转出')"))
        db.session.commit()

        from app import _ensure_party_dimensions
        stats = _ensure_party_dimensions()
        assert stats['backfilled'] == 3
        rows = db.session.execute(text(
            'SELECT member_type, member_identity, member_mobility, employment_status, '
            'party_status FROM party_members ORDER BY id')).fetchall()
        assert [(r[1], r[2], r[4]) for r in rows] == [
            ('正式党员', '在家', '在册'),
            (None, '流动', '在册'),
            (None, None, '已转出'),
        ]
        # 二跑幂等（0 行）
        assert _ensure_party_dimensions()['backfilled'] == 0

        # ── M3：部分维度有值 + party_status 置 NULL 的风险行，回填必须整段跳过、不得覆盖
        db.session.execute(text('UPDATE party_members SET party_status = NULL WHERE id = 1'))
        db.session.commit()
        before = db.session.execute(text(
            'SELECT member_identity, member_mobility, employment_status '
            'FROM party_members WHERE id = 1')).fetchone()
        stats = _ensure_party_dimensions()
        assert stats['backfilled'] == 0            # 风险行存在 → 不执行回填
        assert stats.get('skipped_risky', 0) >= 1
        after = db.session.execute(text(
            'SELECT member_identity, member_mobility, employment_status, party_status '
            'FROM party_members WHERE id = 1')).fetchone()
        assert tuple(after)[:3] == tuple(before)    # 四维原值不变（严禁覆盖）
        assert after[3] is None                     # party_status 不被改写

        # ── 半迁移态（四维全 NULL + party_status NULL）仍按 m025 口径回填治愈
        db.session.execute(text('DELETE FROM party_members WHERE id = 1'))
        db.session.execute(text(
            "INSERT INTO party_members (name, member_type, member_identity, member_mobility, "
            "employment_status, party_status) VALUES ('丁','正式党员',NULL,NULL,NULL,NULL)"))
        db.session.commit()
        assert _ensure_party_dimensions()['backfilled'] == 1
        healed = db.session.execute(text(
            'SELECT member_identity, member_mobility, party_status '
            "FROM party_members WHERE name = '丁'")).fetchone()
        assert tuple(healed) == ('正式党员', '在家', '在册')
