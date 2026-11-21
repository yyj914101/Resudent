# -*- coding: utf-8 -*-
"""tests/test_081_metrics_engine.py — 081 C 统计输出统一指标引擎验收测试。

覆盖（`.trae/specs/build-c-stats-metrics-engine/spec.md` / checklist.md）：
- 指标注册表九项声明完整性 + 两个聚合器适配器；
- `GET /api/stats/query` 正常/未知指标/非法 dims/非法 format/非法 filters；
- 指标级权限与端点漏归默认拒绝（ADR-016/ADR-023）；
- C-1 党员分类/支部 + `/party/api/summary` 键与引擎同值；
- C-2 治理台账全类别 + 重点人员三类独立口径；
- C-3 就业/公益岗、C-4 退役军人重点群体分类计数；
- C-5 党费 H1+H2=年度=12 月明细求和；
- TemplateAdapter notice 记录计数；
- 缓存命中/失效、cache_version（固定口径版本 + 字典签名）；
- 字典签名随 DictItem 变更刷新；
- 计数指标脱敏与无明文；
- 看板/统计表/党员汇总三调用方与引擎同源同值。
"""
import json
import re
import uuid
from datetime import date

import pytest

from utils.metrics import METRIC_REGISTRY, SCHEMA_KEYS, MetricsEngine

ID_CARD_PLAIN = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')
PHONE_PLAIN = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')


@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    """081 缓存是进程级字典，跨 app 共用；每用例前后清空避免串扰。"""
    from utils.agg_cache import invalidate
    invalidate()
    yield
    invalidate()


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # admin 全权限（create_app 首启种子）
    return client


def _perm_client(app, perms):
    """建 Role+User（非 admin），返回已登录 session 的 client。"""
    from models import db, Role, User
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        role = Role(name=f'081角色-{suffix}', permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.commit()
        user = User(username=f'081用户-{suffix}', password_hash='x', role_id=role.id)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid
    return client


def _get_json(client, **params):
    resp = client.get('/api/stats/query',
                      query_string={k: v for k, v in params.items() if v is not None})
    return resp, resp.get_json()


def _bucket_counts(data):
    return {bucket['key']: bucket['count'] for bucket in data['buckets']}


# ============ 注册表 ============

def test_metric_registry_complete():
    assert len(METRIC_REGISTRY) >= 10
    for key, metric in METRIC_REGISTRY.items():
        for field in SCHEMA_KEYS:
            assert field in metric, f'{key} 缺少声明字段 {field}'
        assert isinstance(metric['dims'], list)
        assert isinstance(metric['source'], (list, str))
        assert metric['cache_ttl'] >= 0  # 0 = 兼容指标显式关闭缓存（party_summary）
        assert isinstance(metric['sensitive'], bool)
        assert metric['permission'] is None or isinstance(metric['permission'], str)
        assert metric['output'] in ('object', 'buckets', 'table')
        assert metric['aggregator'].kind in ('sql', 'template')
    assert METRIC_REGISTRY['party_members']['aggregator'].kind == 'sql'
    assert METRIC_REGISTRY['notice_records']['aggregator'].kind == 'template'


# ============ 统一 API ============

def test_query_normal_path(app):
    resp, body = _get_json(_admin_client(app),
                           metric_key='party_members', dims='member_identity')
    assert resp.status_code == 200
    assert body['success'] is True
    data = body['data']
    for field in ('metric_key', 'label', 'definition', 'total', 'buckets',
                  'sources', 'cache_ttl', 'cache_version', 'generated_at'):
        assert field in data, f'MetricResult 缺少 {field}'
    assert data['metric_key'] == 'party_members'
    assert data['dims'] == ['member_identity']
    assert data['total'] == 0
    assert data['buckets'] == []
    assert data['sources'] == ['party_members']


def test_query_rejects_bad_input(app):
    client = _admin_client(app)

    resp, body = _get_json(client, metric_key='no_such_metric')
    assert resp.status_code == 400
    assert body['success'] is False
    assert '未知指标' in body['msg']

    resp, body = _get_json(client, metric_key='party_members', dims='unknown_dim')
    assert resp.status_code == 400
    assert body['success'] is False
    assert '不支持维度' in body['msg']

    resp, body = _get_json(client, metric_key='party_members', format='xml')
    assert resp.status_code == 400
    assert body['success'] is False
    assert 'json' in body['msg']

    resp, body = _get_json(client, metric_key='party_members', filters='{bad json')
    assert resp.status_code == 400
    assert body['success'] is False
    assert 'filters' in body['msg']


# ============ 权限 ============

def test_permission_gate(app):
    denied = _perm_client(app, {'resident.view': True})
    resp, body = _get_json(denied, metric_key='party_members', dims='member_identity')
    assert resp.status_code == 403
    assert body['success'] is False

    allowed = _perm_client(app, {'party.view': True})
    resp, body = _get_json(allowed, metric_key='party_members', dims='member_identity')
    assert resp.status_code == 200
    assert body['success'] is True


def test_endpoint_missing_classification_defaults_to_deny(app):
    from utils import permissions

    client = _perm_client(app, {'party.view': True})
    saved = permissions.ENDPOINT_MODULES.pop('metrics.query')
    try:
        resp, body = _get_json(client, metric_key='party_members', dims='member_identity')
        assert resp.status_code == 403, '漏归端点应被全局鉴权默认拒绝'
        assert body['success'] is False
    finally:
        permissions.ENDPOINT_MODULES['metrics.query'] = saved

    resp, body = _get_json(client, metric_key='party_members', dims='member_identity')
    assert resp.status_code == 200
    assert body['success'] is True


# ============ C-1 党员 ============

def _seed_party_members(app):
    from models import db, PartyMember
    rows = [
        dict(name='党员甲', member_identity='正式党员', party_status='在册',
             branch='一支部', member_mobility='在家', employment_status='在职'),
        dict(name='党员乙', member_identity='预备党员', party_status='在册',
             branch='二支部', member_mobility='在家', employment_status='在职'),
        dict(name='党员丙', member_identity='正式党员', party_status='已转出',
             branch='三支部', member_mobility='流动', employment_status='退休'),
        dict(name='党员丁', member_identity='正式党员', party_status='在册',
             branch='', member_mobility='在家', employment_status='退休'),
    ]
    with app.app_context():
        for row in rows:
            db.session.add(PartyMember(**row))
        db.session.commit()


def test_c1_party_members_and_summary(app):
    _seed_party_members(app)
    client = _admin_client(app)

    resp, body = _get_json(client, metric_key='party_members', dims='member_identity')
    assert resp.status_code == 200
    data = body['data']
    assert data['total'] == 3, '默认应排除已转出党员'
    counts = _bucket_counts(data)
    assert counts.get('正式党员') == 2
    assert counts.get('预备党员') == 1

    resp, body = _get_json(client, metric_key='party_members', dims='branch')
    assert resp.status_code == 200
    branch_data = body['data']
    assert branch_data['total'] == 3
    branch_counts = _bucket_counts(branch_data)
    assert branch_counts == {'一支部': 1, '二支部': 1, '未分配党支部': 1}
    assert '三支部' not in branch_counts, '已转出党员不应计入支部统计'

    summary_resp = client.get('/party/api/summary')
    assert summary_resp.status_code == 200
    summary = summary_resp.get_json()['data']
    assert set(summary) == {'on_roll', 'formal', 'probation', 'floating',
                            'retired', 'transferred', 'auto',
                            'total_archived', 'branch_stats', 'unassigned_branch'}
    assert summary['on_roll'] == 3
    assert summary['formal'] == 2
    assert summary['probation'] == 1
    assert summary['floating'] == 0
    assert summary['retired'] == 1
    assert summary['transferred'] == 1
    assert summary['auto'] == 0
    assert summary['total_archived'] == 3
    assert summary['unassigned_branch'] == 1

    with app.app_context():
        engine_summary = MetricsEngine.query('party_summary')['data']
    assert summary == engine_summary, '/party/api/summary 必须与引擎 party_summary 同值'


def test_c1_party_category_composition(app):
    """080 A-1 移交 081：退休 > 流动 > 在家 互斥三分类（含优先级反例/兜底/转出排除）。"""
    from models import db, PartyMember

    rows = [
        # 退休 + 流动 → 退休（优先级反例，必测）
        dict(name='组合甲', member_identity='正式党员', party_status='在册',
             employment_status='退休', member_mobility='流动'),
        # 在职 + 流动 → 流动
        dict(name='组合乙', member_identity='正式党员', party_status='在册',
             employment_status='在职', member_mobility='流动'),
        # 在职 + 在家 → 在家
        dict(name='组合丙', member_identity='预备党员', party_status='在册',
             employment_status='在职', member_mobility='在家'),
        # 退休 + 在家 → 退休
        dict(name='组合丁', member_identity='正式党员', party_status='在册',
             employment_status='退休', member_mobility='在家'),
        # 已转出 → 默认口径不计入
        dict(name='组合戊', member_identity='正式党员', party_status='已转出',
             employment_status='退休', member_mobility='流动'),
        # 两列 NULL → 在家（兜底口径）
        dict(name='组合己', member_identity='正式党员', party_status='在册',
             employment_status=None, member_mobility=None),
    ]
    with app.app_context():
        for row in rows:
            db.session.add(PartyMember(**row))
        db.session.commit()

    client = _admin_client(app)
    resp, body = _get_json(client, metric_key='party_members', dims='party_category')
    assert resp.status_code == 200
    data = body['data']
    assert data['dims'] == ['party_category']
    assert data['total'] == 5, '已转出党员不计入组合口径'
    counts = _bucket_counts(data)
    assert counts == {'退休': 2, '流动': 1, '在家': 2}, '互斥三分类：退休>流动>在家'
    assert counts.get('已转出') is None
    assert data['masked'] is False, '纯计数结果不应标记为已脱敏'

    # 默认维度向后兼容：不传 dims 仍按 member_identity 分组
    resp, body = _get_json(client, metric_key='party_members')
    assert resp.status_code == 200
    assert body['data']['dims'] == ['member_identity']
    default_counts = _bucket_counts(body['data'])
    assert default_counts == {'正式党员': 4, '预备党员': 1}


@pytest.mark.parametrize('employment,mobility,party_status,expected', [
    ('退休', '流动', '在册', '退休'),   # 退休 > 流动
    ('在职', '流动', '在册', '流动'),
    ('在职', '在家', '在册', '在家'),
    ('退休', '在家', '在册', '退休'),
    (None, None, '在册', '在家'),       # 两列 NULL 兜底
    ('退休', '流动', '已转出', None),   # 默认排除已转出
])
def test_c1_party_category_priority_matrix(app, employment, mobility,
                                           party_status, expected):
    """逐例钉住 party_category 优先级矩阵（红→绿反例）。"""
    from models import db, PartyMember

    with app.app_context():
        db.session.add(PartyMember(
            name='矩阵党员', member_identity='正式党员', party_status=party_status,
            employment_status=employment, member_mobility=mobility))
        db.session.commit()

    resp, body = _get_json(_admin_client(app), metric_key='party_members',
                           dims='party_category')
    assert resp.status_code == 200
    counts = _bucket_counts(body['data'])
    if expected is None:
        assert counts == {}, '已转出党员不得进入组合口径'
    else:
        assert counts == {expected: 1}


def test_contains_identity_keys_for_table_payload():
    """M4：identity 键检测用于如实标记 masked（未来表格路径可用）。"""
    from utils.metrics import contains_identity_keys, mask_metric_payload

    payload = {'buckets': [], 'data': {'rows': [{'name': '张三',
                                                 'id_card': '340104199001011234',
                                                 'phone': '13812345678'}]}}
    assert contains_identity_keys(payload) is True
    masked = mask_metric_payload(payload)
    assert masked['data']['rows'][0]['id_card'] == '340104********1234'
    assert masked['data']['rows'][0]['phone'] == '138****5678'
    assert contains_identity_keys({'total': 3, 'buckets': []}) is False


# ============ C-2 治理台账 ============

def test_c2_governance_ledgers_and_key_personnel(app):
    from models import db, GovernanceLedger

    seed = [('stability-risk', 2), ('mental-health', 1), ('criminal', 2),
            ('road-hazard', 1)]
    with app.app_context():
        for category, count in seed:
            for index in range(count):
                db.session.add(GovernanceLedger(
                    category=category, name=f'{category}-{index}',
                    payload='{}', record_date=date(2026, 11, 1)))
        db.session.commit()

    client = _admin_client(app)

    resp, body = _get_json(client, metric_key='governance_ledgers')
    assert resp.status_code == 200
    data = body['data']
    assert data['total'] == 6
    counts = _bucket_counts(data)
    assert counts['stability-risk'] == 2
    assert counts['mental-health'] == 1
    assert counts['criminal'] == 2
    assert counts['road-hazard'] == 1
    # 2026-11-16：社区矫正 / 社区戒毒康复 已下线，不再出现在全类别统计
    for retired in ('community-correction', 'drug-rehab', 'resettlement'):
        assert retired not in counts, '已下线类别仍在统计内：%s' % retired
    # 旧四类仍在全类别统计内（无数据的两类计 0，不丢类别）
    for old in ('special-visit', 'stability-risk', 'mental-health', 'security-committee'):
        assert old in counts
    assert counts['special-visit'] == 0
    assert counts['security-committee'] == 0
    labels = {bucket['key']: bucket['label'] for bucket in data['buckets']}
    assert labels['stability-risk'] == '社会稳定风险排查化解记录', 'label 应取 CATEGORY_CONFIG 标题'
    assert labels['criminal'] == '刑事人员台账', '刑事人员标题应随改名生效'

    resp, body = _get_json(client, metric_key='governance_key_personnel')
    assert resp.status_code == 200
    kp = body['data']
    assert kp['total'] == 2, '重点人员仅统计刑事人员台账'
    kp_counts = _bucket_counts(kp)
    assert set(kp_counts) == {'criminal'}
    assert kp_counts['criminal'] == 2


# ============ C-3 就业/公益岗 ============

def test_c3_welfare_jobs(app):
    from models import db, WelfareJob

    with app.app_context():
        for name, job_type, village_group in (
            ('岗位甲', '保洁员', '一组'),
            ('岗位乙', '保洁员', '一组'),
            ('岗位丙', '护林员', '二组'),
            ('岗位丁', '', ''),
        ):
            db.session.add(WelfareJob(name=name, job_type=job_type,
                                      village_group=village_group))
        db.session.commit()

    client = _admin_client(app)

    resp, body = _get_json(client, metric_key='welfare_jobs')
    assert resp.status_code == 200
    data = body['data']
    assert data['dims'] == ['job_type'], '默认维度应为 job_type'
    assert data['total'] == 4
    counts = _bucket_counts(data)
    assert counts['保洁员'] == 2
    assert counts['护林员'] == 1
    assert counts['未填写'] == 1

    resp, body = _get_json(client, metric_key='welfare_jobs', dims='village_group')
    assert resp.status_code == 200
    group_counts = _bucket_counts(body['data'])
    assert group_counts['一组'] == 2
    assert group_counts['二组'] == 1
    assert group_counts['未填写'] == 1


# ============ C-4 退役军人 ============

def test_c4_veterans(app):
    from models import db, Veteran

    with app.app_context():
        for index, category in enumerate(('参战参试', '参战参试', '烈士遗属', '')):
            db.session.add(Veteran(name=f'退役-{index}', key_group_category=category))
        db.session.commit()

    client = _admin_client(app)
    resp, body = _get_json(client, metric_key='veterans')
    assert resp.status_code == 200
    data = body['data']
    assert data['total'] == 4
    counts = _bucket_counts(data)
    assert counts['参战参试'] == 2
    assert counts['烈士遗属'] == 1
    assert counts['未填写'] == 1


# ============ C-5 党费 ============

def test_c5_party_dues(app):
    from models import db, PartyFee, PartyMember

    with app.app_context():
        member = PartyMember(name='党费党员', member_identity='正式党员',
                             party_status='在册', branch='一支部')
        db.session.add(member)
        db.session.commit()
        for month in range(1, 13):
            db.session.add(PartyFee(
                member_id=member.id, fee_year=2025, fee_month=month,
                amount=10, paid_amount=10 if month <= 6 else 5))
        db.session.commit()

    client = _admin_client(app)
    resp, body = _get_json(client, metric_key='party_dues', year='2025')
    assert resp.status_code == 200
    metric = body['data']
    dues = metric['data']
    assert dues['h1']['amount'] == 60
    assert dues['h2']['amount'] == 60
    assert dues['annual']['amount'] == 120
    assert dues['annual']['paid'] == 90
    assert dues['h1']['amount'] + dues['h2']['amount'] == dues['annual']['amount']
    assert len(dues['months']) == 12
    assert sum(m['amount'] for m in dues['months']) == dues['annual']['amount']
    assert sum(m['paid'] for m in dues['months']) == dues['annual']['paid']
    assert dues['year'] == 2025
    assert metric['total'] == 90


# ============ TemplateAdapter ============

def test_notice_records_template_adapter(app):
    from models import db, NoticeRecord, NoticeTemplate
    from models.notice import NOTICE_TYPE_NAMES

    with app.app_context():
        template = NoticeTemplate(name='低保公示模板', notice_type='dibao', title='低保公示')
        db.session.add(template)
        db.session.commit()
        for index in range(2):
            db.session.add(NoticeRecord(template_id=template.id, notice_type='dibao',
                                        period='2025-10', title=f'低保公示{index}'))
        db.session.commit()

    client = _admin_client(app)
    resp, body = _get_json(client, metric_key='notice_records')
    assert resp.status_code == 200
    data = body['data']
    assert data['total'] == 2
    counts = _bucket_counts(data)
    assert counts['dibao'] == 2
    labels = {bucket['key']: bucket['label'] for bucket in data['buckets']}
    assert labels['dibao'] == NOTICE_TYPE_NAMES['dibao']


# ============ 缓存 ============

def test_cache_hit_and_invalidate(app):
    from models import db, PartyMember

    with app.app_context():
        db.session.add(PartyMember(name='缓存党员甲', member_identity='正式党员',
                                   party_status='在册', branch='一支部'))
        db.session.commit()

    client = _admin_client(app)
    resp, first = _get_json(client, metric_key='party_members', dims='member_identity')
    assert resp.status_code == 200
    assert first['data']['total'] == 1
    assert 'v1.0-081' in first['data']['cache_version']
    assert re.search(r'v1\.0-081:[0-9a-f]{12}', first['data']['cache_version']), \
        'cache_version 应含固定口径版本 + 12 位字典签名'

    with app.app_context():
        db.session.add(PartyMember(name='缓存党员乙', member_identity='预备党员',
                                   party_status='在册', branch='二支部'))
        db.session.commit()

    resp, second = _get_json(client, metric_key='party_members', dims='member_identity')
    assert resp.status_code == 200
    assert second['data']['total'] == 1, 'TTL 内同参数应命中旧缓存'

    from utils.agg_cache import invalidate
    invalidate()
    resp, third = _get_json(client, metric_key='party_members', dims='member_identity')
    assert resp.status_code == 200
    assert third['data']['total'] == 2, '缓存失效后应重新聚合'


def test_dict_signature_refresh(app):
    from models import db, DictCategory, DictItem
    from utils import metrics

    with app.app_context():
        before = metrics._dict_signature()
        assert re.fullmatch(r'[0-9a-f]{12}', before), '字典签名应为 12 位 sha256 片段'
        category = DictCategory(name='081签名测试分类', description='081')
        db.session.add(category)
        db.session.commit()
        db.session.add(DictItem(category_id=category.id, label='测试项',
                                value='测试项', sort_order=0))
        db.session.commit()
        after = metrics._dict_signature()
    assert after != before, '插入 DictItem 后字典签名必须变化'


# ============ 脱敏 ============

def test_masking_and_no_plaintext(app):
    from utils.metrics import mask_metric_payload

    masked = mask_metric_payload({'id_card': '340104199001011234',
                                  'phone': '13812345678'})
    assert masked['id_card'] == '340104********1234'
    assert masked['phone'] == '138****5678'

    client = _admin_client(app)
    for metric_key in ('party_members', 'party_summary', 'governance_ledgers',
                       'governance_key_personnel', 'welfare_jobs', 'veterans',
                       'party_dues', 'notice_records'):
        resp, body = _get_json(client, metric_key=metric_key)
        assert resp.status_code == 200
        text = json.dumps(body, ensure_ascii=False)
        assert not ID_CARD_PLAIN.search(text), f'{metric_key} 响应含身份证明文'
        assert not PHONE_PLAIN.search(text), f'{metric_key} 响应含手机号明文'


# ============ 三调用方同源同值 ============

def test_three_callers_single_source(app):
    from models import db, Resident, ResidentTag, PartyMember
    from utils.agg_cache import get_stats_table

    with app.app_context():
        resident = Resident(name='困境儿童甲', birth_date=date(2017, 5, 1),
                            status='normal', flow_status='常住', gender='女',
                            village_group='一组')
        db.session.add(resident)
        db.session.commit()
        db.session.add(ResidentTag(resident_id=resident.id, tag_code='困境儿童',
                                   status='有效', source='手动'))
        db.session.add(PartyMember(name='汇总党员', member_identity='正式党员',
                                   party_status='在册', branch='一支部'))
        db.session.commit()

    client = _admin_client(app)

    dash_resp = client.get('/dashboard/api/dashboard/stats')
    assert dash_resp.status_code == 200
    dash_data = dash_resp.get_json()['data']
    with app.app_context():
        engine_dashboard = MetricsEngine.query('dashboard_counts')['data']
    assert dash_data['distressed_child_count'] == engine_dashboard['distressed_child_count'] == 1
    for key in ('total_population', 'total_households', 'party_count',
                'left_behind_count', 'hard_elderly_count', 'dibao_count',
                'disabled_count', 'veteran_count'):
        assert dash_data[key] == engine_dashboard[key], f'看板 {key} 与引擎不一致'

    with app.app_context():
        table = get_stats_table()
        engine_table = MetricsEngine.query('stats_table')['data']
    assert set(table) == set(engine_table)
    for key in table:
        assert table[key] == engine_table[key], f'统计表 {key} 与引擎不一致'

    party_data = client.get('/party/api/summary').get_json()['data']
    with app.app_context():
        engine_party = MetricsEngine.query('party_summary')['data']
    assert party_data == engine_party
