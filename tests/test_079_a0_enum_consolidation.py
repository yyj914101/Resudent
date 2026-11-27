# -*- coding: utf-8 -*-
"""tests/test_079_a0_enum_consolidation.py — A0 枚举真源收敛取值等价断言（079）。

红→绿：本文件在收敛前代码上运行即红（utils/dict_enum 不存在 → ImportError）。
绿态契约（零行为变化，079 验收 1/2/3）：
  1) 三枚举经字典读后与收敛前硬编码逐条相等（党员类别/流动状态/兵役状况）；
     故意写错的期望值必须不相等（负例对照，防「恒真断言」）。
  2) party 政治面貌映射、import_engine 兵役值域/状态归一、resident_flow 判断
     在收敛后行为与收敛前逐条一致。
  3) 字典分类缺失/为空 → 种子回退（回退值 = 旧常量，行为不变）；
     ensure_enum_dicts 幂等（重跑零新增）。
  4) 散落定义硬编码 grep 归零（白名单：字典种子/读取处/离线脚本/首装种子）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 收敛前硬编码（旧真源，作为等价基线逐字保留在此；079 验收 2 的对照基准）
OLD_MEMBER_TYPES = ('正式党员', '预备党员', '流动党员', '已转出')
OLD_FLOW_VALUES = ('常住', '流入', '流出')
OLD_MILITARY_DOMAIN = {'1': '未服兵役', '2': '已服兵役'}


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    for k, v in {
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_LAN_AUTHORIZED": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    from app import create_app
    application = create_app()  # 启动守卫会跑 ensure_enum_dicts（接线即被本用例覆盖）
    application.config["TESTING"] = True
    return application


def _login(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s['user_id'] = 1
    return client


# ── 1) 三枚举取值等价（字典读 vs 收敛前硬编码） ──────────────────────────

def test_enum_values_equal_old_literals(tmp_path, monkeypatch):
    from utils.dict_enum import get_enum, reset_enum_cache
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        reset_enum_cache()
        assert get_enum('人员类别') == OLD_MEMBER_TYPES
        assert get_enum('流动状态') == OLD_FLOW_VALUES
        assert get_enum('兵役状况') == ('未服兵役', '已服兵役', '退役', '现役')
        # 负例对照：故意写错的期望值必须不相等（证明断言有牙，防恒真）
        assert get_enum('人员类别') != ('正式党员', '预备党员', '流动党员', '已转出X')
        assert get_enum('流动状态') != OLD_FLOW_VALUES[:-1]


def test_enum_fallback_when_category_missing(tmp_path, monkeypatch):
    """字典分类被删 → 种子回退，取值仍与旧常量逐条相等（零行为）。"""
    from models import db, DictCategory, DictItem
    from utils.dict_enum import get_enum, reset_enum_cache
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        cat = DictCategory.query.filter_by(name='流动状态').first()
        assert cat is not None
        DictItem.query.filter_by(category_id=cat.id).delete()
        db.session.delete(cat)
        db.session.commit()
        reset_enum_cache()
        assert get_enum('流动状态') == OLD_FLOW_VALUES
        # 幂等补录恢复
        from utils.dict_seed import ensure_enum_dicts
        cats, items = ensure_enum_dicts()
        assert (cats, items) == (1, 3)
        reset_enum_cache()
        assert get_enum('流动状态') == OLD_FLOW_VALUES


def test_ensure_enum_dicts_idempotent(tmp_path, monkeypatch):
    from utils.dict_seed import ensure_enum_dicts
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        # 启动守卫已补录一次；重跑必须零新增
        assert ensure_enum_dicts() == (0, 0)


# ── 2) 消费方行为等价 ────────────────────────────────────────────────

def test_party_politics_mapping_unchanged(tmp_path, monkeypatch):
    # 080 A-1 拆维度后等价重录：旧混合 member_type → (身份, 流动, 在册) 三元组
    from routes.party import _resident_politics
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        expected = {
            ('预备党员', '在家', '在册'): '中共预备党员',
            ('正式党员', '在家', '在册'): '中共党员',
            ('', '流动', '在册'): '中共党员',
            ('', '', '已转出'): '群众',
        }
        for k, v in expected.items():
            assert _resident_politics(*k) == v, k
        # 未知值 → ''（与旧 .get(member_type, '') 等价）
        assert _resident_politics('不存在', '', '') == ''
        assert _resident_politics('', '不存在', '') == ''
        assert _resident_politics('', '', '') == ''


def test_party_page_renders_dict_options(tmp_path, monkeypatch):
    """080 A-1：人员类别拆四维后，模态框 4 个 select 的 options 由字典渲染。"""
    app = _make_app(tmp_path, monkeypatch)
    client = _login(app)
    html = client.get('/party/').get_data(as_text=True)

    def _select(field_id):
        i = html.index('id="%s"' % field_id)
        j = html.index('</select>', i)
        return html[i:j]

    for field_id in ('fmMemberIdentity', 'fmMemberMobility',
                     'fmEmploymentStatus', 'fmPartyStatus'):
        assert 'id="%s"' % field_id in html, field_id
    assert 'id="fmMemberType"' not in html

    identity = _select('fmMemberIdentity')
    assert '<option value="">请选择</option>' in identity
    assert identity.count('<option>正式党员</option>') == 1
    assert identity.count('<option>预备党员</option>') == 1

    mobility = _select('fmMemberMobility')
    assert '<option value="">请选择</option>' in mobility
    assert '<option>在家</option>' in mobility
    assert '<option>流动</option>' in mobility

    employment = _select('fmEmploymentStatus')
    assert '<option value="">请选择</option>' in employment
    assert '<option>在职</option>' in employment
    assert '<option>退休</option>' in employment

    party_status = _select('fmPartyStatus')
    assert '<option>在册</option>' in party_status
    assert '<option>已转出</option>' in party_status


def test_import_engine_military_domain_unchanged(tmp_path, monkeypatch):
    from utils.import_engine import apply_value_domain
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        for src, std in OLD_MILITARY_DOMAIN.items():
            assert apply_value_domain('military_status', src) == std, src
            assert apply_value_domain('military_status', ' ' + src) == std  # strip 语义不变
        # 未命中原样透传
        assert apply_value_domain('military_status', '9') == '9'
        assert apply_value_domain('military_status', None) is None


def test_import_engine_status_map_includes_flow_out(tmp_path, monkeypatch):
    from utils.import_engine import _normalize_import_status_value
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        assert _normalize_import_status_value('流出') == 'moved_out'
        assert _normalize_import_status_value('迁出') == 'moved_out'
        assert _normalize_import_status_value('正常') == 'normal'
        assert _normalize_import_status_value('deceased') == 'deceased'
        assert _normalize_import_status_value('') is None
        assert _normalize_import_status_value('未知值') is None


def test_resident_flow_constants_and_judgement_unchanged(tmp_path, monkeypatch):
    import utils.resident_flow as rf
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        rf._flow_values_cache = None  # 进程缓存复位后按本库字典定值
        assert rf.FLOW_RESIDENT == '常住'
        assert rf.FLOW_IN == '流入'
        assert rf.FLOW_OUT == '流出'

        class R:  # 鸭子类型桩（与 resident_flow 契约一致）
            def __init__(self, flow):
                self.flow_status = flow

        assert rf.is_outflow(R('流出')) is True
        assert rf.is_outflow(R('常住')) is False
        assert rf.is_outflow(R('流入')) is False
        assert rf.is_outflow(None) is False
        assert rf.is_inflow(R('流入')) is True
        assert rf.is_inflow(R('常住')) is False
        assert rf.flow_label(R('流出')) == '流出'
        assert rf.flow_label(R('未知')) == '未知'
        assert rf.flow_label(None) == ''


def test_resident_flow_validation_message_unchanged(tmp_path, monkeypatch):
    """routes/resident.py 流动状态校验：集合与报文由字典重构后与旧口径逐字相等。"""
    from utils.dict_enum import get_enum
    app = _make_app(tmp_path, monkeypatch)
    with app.app_context():
        assert tuple(get_enum('流动状态')) == ('常住', '流入', '流出')
        assert '流动状态仅支持：' + '/'.join(get_enum('流动状态')) == '流动状态仅支持：常住/流入/流出'


# ── 3) 散落定义硬编码 grep 归零（079 验收 3；白名单见 RESULT 对照表） ────

def test_no_scattered_enum_definitions():
    flow_value_def = re.compile(r"['\"](?:常住|流入|流出)['\"]")
    member_def = re.compile(r"['\"](?:正式党员|预备党员|流动党员|已转出)['\"]")
    military_def = re.compile(r"未服兵役|已服兵役")

    def scan(rel, pattern, allowed):
        path = os.path.join(ROOT, rel)
        src = open(path, encoding='utf-8').read()
        lines = src.splitlines()
        hits = []
        for no, line in enumerate(lines, 1):
            if pattern.search(line) and not any(a in line for a in allowed):
                hits.append(f'{rel}:{no}')
        return hits

    offenders = []
    # flow_status：运行时代码仅允许 dict 读取处（get_enum_value 回退 label 属读取处）
    for rel in ('routes/resident.py', 'routes/dashboard.py', 'utils/agg_cache.py'):
        offenders += scan(rel, flow_value_def, allowed=("get_enum_value(", "get_enum(", "# "))
    # member_type：定义处归零；映射键/统计比较/转正删除等「业务规则使用处」白名单放行
    # （使用处引用具体值是业务逻辑而非取值定义，改动归 080 口径工作——079 RESULT 对照表登记）
    offenders += scan('routes/party.py', member_def,
                      allowed=('politics_by_type', 'member_type', 'sync_resident_politics',
                               '中共预备党员', '中共党员', '群众', '# ', '"""', 'get_enum(',
                               # 080 A-1：维度字段与判定表达式（取值定义仍归 DICT_SEEDS）
                               '_member_type_display', 'member_identity', 'member_mobility',
                               'employment_status', 'party_status',
                               'identity ==', 'mobility ==', 'party_status ==',
                               '_resident_politics',
                               # A-1 列表「人员类别」组合展示的已转出展示值（非定义处）
                               "return '已转出'"))
    # military：仅允许读取处回退 label
    offenders += scan('utils/import_engine.py', military_def, allowed=('get_enum_value(', '# '))
    offenders += scan('models/resident.py', flow_value_def, allowed=('get_enum_value(', '# ', '"""'))
    assert not offenders, f'枚举定义硬编码残留：{offenders}'
