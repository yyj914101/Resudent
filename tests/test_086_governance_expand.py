# -*- coding: utf-8 -*-
"""tests/test_086_governance_expand.py — 086 平安扩展（道交三类 + 信访维稳）验收。

被测实现（读码确认，本文件只测不改）：
- routes/governance.py：CATEGORY_CONFIG 新增 road-hazard / key-vehicle / guide-station
  （现 10 类，旧 7 类配置不得漂移）；信访块 /governance/petition/ 与
  /petition/api/{options,list,detail,save,delete,transition,handle-save,export}
- models/governance_petition.py（主表 + GovernancePetitionHandle 子表）、m033
- utils/metrics.py 注册 governance_petition

覆盖 9 组验收：
  1) 三类页面 GET 200（标题 + 弹窗字段渲染）
  2) 旧 7 类守门：fields 数量 7/10/8/6/15/15/16 与字段键集合逐类冻结，且不含新增类别字段
  3) 三类各一条 save→list→export；非法枚举值保存失败且不写库
  4) 信访：必填三缺一失败 → 新增 → 列表 → 详情（含 handles）→ 办理记录新增 → 导出
  5) 编号唯一：重复 petition_no 失败且库内 1 行
  6) 状态机：待受理→办理中 true；待受理→已办结 409；办理中→已办结（close_date+reply）true；
     缺 reply 失败
  7) 日期校验：close_date < accept_date 失败
  8) 权限：未登录 302/401；无 mediation 位角色 403
  9) 指标：MetricsEngine.query('governance_petition') total 与库内一致

反例判据：
- 旧 7 类守门：若旧类被顺手改动（加/删字段、改顺序）→ 键列表比对红；这是 086 只增不改的红线。
- 非法枚举：若 _payload_from_form 的 option_category 校验被移除 → 200/true 且写库 → 红。
- 编号唯一：若 unique 索引与预检双保险任一被移除 → 第二次 200 且 COUNT==2 → 红。
- 非法状态迁移：若 PETITION_TRANSITIONS 判定被移除 → 待受理→已办结 返回 200 → 红。

数据全部为假值（张三 / 330101199001011234 / 13812345678），不含真实居民信息。
"""
import io
import json
import os
import sqlite3
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ID_CARD = '330101199001011234'
PHONE = '13812345678'
NAME = '张三'
MASKED_ID_CARD = '330101********1234'
MASKED_PHONE = '138****5678'

NEW_CATEGORIES = ('road-hazard', 'key-vehicle', 'guide-station')
NEW_TITLES = {
    'road-hazard': '道路交通安全隐患台账',
    'key-vehicle': '重点车辆驾驶人名册',
    'guide-station': '交通安全劝导站',
}

# ── 086 落地前已存在的台账类别：字段键顺序与数量逐类冻结（先读 routes/governance.py 核对后写死）──
# 2026-11-16：社区矫正（community-correction）、社区戒毒康复（drug-rehab）整类下线；
# 刑满释放帮教（resettlement）改名刑事人员（criminal），新增「人员状态」字段（两类人员筛选口径），
# 故此处按现行真源冻结（原 7 类 → 5 类）。
LEGACY_CATEGORY_KEYS = {
    'special-visit': [
        'record_date', 'name', 'id_card', 'village_group', 'phone',
        'responsible_person', 'visit_situation',                                  # 7
    ],
    'stability-risk': [
        'record_date', 'name', 'id_card', 'village_group', 'work_unit', 'phone',
        'risk_category', 'basic_situation', 'work_measures', 'remark',             # 10
    ],
    'mental-health': [
        'record_date', 'name', 'id_card', 'village_group', 'phone',
        'responsible_person', 'visit_situation', 'remark',                         # 8
    ],
    'security-committee': [
        'name', 'position', 'id_card', 'phone', 'address', 'remark',               # 6
    ],
    'criminal': [
        'personnel_status', 'name', 'id_card', 'village_group', 'phone', 'address',
        'record_date', 'responsible_person', 'visit_situation', 'help_measures',
        'release_date', 'help_type', 'original_sentence', 'control_status',
        'control_measures', 'remark',                                             # 16
    ],
}
LEGACY_FIELD_COUNTS = {
    'special-visit': 7, 'stability-risk': 10, 'mental-health': 8,
    'security-committee': 6, 'criminal': 16,
}

# 086 新增类别（道交三类）专属字段（旧类不得出现任何一个）
NEW_ONLY_KEYS = {
    'road_name', 'location', 'hazard_type', 'hazard_level', 'found_date', 'measure',
    'fixed_date', 'fix_status', 'owner', 'owner_phone',
    'record_type', 'plate_no', 'vehicle_type', 'license_status', 'register_date',
    'record_status', 'station_name', 'staff_name', 'staff_phone', 'setup_date',
    'run_status',
}


# ══════════════════════════ 夹具 ══════════════════════════

@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    import config as cfg
    application.config['TEST_DB_PATH'] = str(cfg.DB_PATH)
    return application


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    from utils.agg_cache import invalidate
    invalidate()
    yield
    invalidate()


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())


def _client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
    return client


def _admin_client(app):
    return _client_for(app, 1)


def _role_client(app, perms):
    from models import db, Role, User
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        role = Role(name=f'086角色-{suffix}',
                    permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.commit()
        user = User(username=f'086用户-{suffix}', password_hash='x', role_id=role.id)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    return _client_for(app, uid)


def _count(app, sql, params=()):
    conn = sqlite3.connect(app.config['TEST_DB_PATH'])
    try:
        return conn.execute(sql, params).fetchone()[0]
    finally:
        conn.close()


def _ledger_count(app, category):
    return _count(app, 'SELECT COUNT(*) FROM governance_ledgers WHERE category = ?',
                  (category,))


# ══════════════════════════ 载荷 ══════════════════════════

def _category_payload(category, **overrides):
    """三类道交台账完整载荷（枚举值与 dict_seed 对齐）。"""
    data = {'name': NAME, 'record_date': '2026-03-01'}
    if category == 'road-hazard':
        data.update({
            'road_name': '村口至三合桥路段',
            'location': 'K0+200 弯道',
            'hazard_type': '路面破损',
            'hazard_level': '一般',
            'found_date': '2026-02-20',
            'measure': '补铺沥青并加装警示桩',
            'fixed_date': '2026-03-05',
            'fix_status': '已整改',
            'owner': '王五',
            'owner_phone': '13812345670',
            'remark': '假值测试备注',
        })
    elif category == 'key-vehicle':
        data.update({
            'record_type': '车辆',
            'plate_no': '浙A12345',
            'vehicle_type': '面包车',
            'phone': PHONE,
            'license_status': '证照齐全',
            'register_date': '2026-01-15',
            'record_status': '在册',
            'remark': '假值测试备注',
        })
    else:
        data.update({
            'station_name': '三合村口劝导站',
            'location': '村口与县道交叉口',
            'staff_name': '赵六',
            'staff_phone': '13812345671',
            'setup_date': '2025-06-01',
            'run_status': '正常',
            'remark': '假值测试备注',
        })
    data.update(overrides)
    return data


def _petition_payload(**overrides):
    data = {
        'petition_no': 'XF2026-0001',
        'source': '来访',
        'petitioner_name': NAME,
        'contact': PHONE,
        'id_card': ID_CARD,
        'address': '三合村一组12号',
        'category': '邻里纠纷',
        'summary': '邻里宅基地边界纠纷，请求调解',
        'accept_date': '2026-03-01',
        'due_date': '2026-04-01',
        'case_officer': '李四',
        'remark': '假值测试备注',
    }
    data.update(overrides)
    return data


def _save_petition(client, **overrides):
    return client.post('/governance/petition/api/save', data=_petition_payload(**overrides))


def _petition_list(client, **params):
    query = {'limit': 100}
    query.update(params)
    return client.get('/governance/petition/api/list', query_string=query).get_json()


def _petition_transition(client, pid, to_status):
    return client.post('/governance/petition/api/transition',
                       data={'id': pid, 'to_status': to_status})


def _petition_status(app, pid):
    conn = sqlite3.connect(app.config['TEST_DB_PATH'])
    try:
        row = conn.execute('SELECT status FROM governance_petition WHERE id = ?',
                           (pid,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ══════════ 1) 三类页面 ══════════

@pytest.mark.parametrize('category', NEW_CATEGORIES)
def test_new_category_page_renders(app, category):
    """三类页面 GET 200，标题与本类必填字段的弹窗控件均渲染。"""
    client = _admin_client(app)
    resp = client.get(f'/governance/{category}/')
    assert resp.status_code == 200, f'{category} 页面 {resp.status_code}'
    html = resp.get_data(as_text=True)
    assert NEW_TITLES[category] in html, f'{category} 缺标题 {NEW_TITLES[category]}'
    modal = html[html.index('id="formModal"'):]
    for key in _category_payload(category):
        if key == 'name':
            continue
        assert f'name="{key}"' in modal, f'{category} 弹窗缺字段 {key}'


def test_unknown_category_still_404(app):
    """动态段不吞未登记类别：/governance/not-exist/ → 404（新增类别走白名单而非通配）。"""
    client = _admin_client(app)
    assert client.get('/governance/not-exist/').status_code == 404


# ══════════ 2) 旧 7 类守门 ══════════

def test_legacy_categories_frozen(app):
    """既有台账类别（special-visit / stability-risk / mental-health / security-committee /
    criminal）的字段键顺序与数量逐类冻结，且不含 086 新增类别专属字段。"""
    from routes.governance import CATEGORY_CONFIG

    assert set(LEGACY_CATEGORY_KEYS) <= set(CATEGORY_CONFIG), '既有类别被删除或改名'
    for category, expected_keys in LEGACY_CATEGORY_KEYS.items():
        cfg = CATEGORY_CONFIG[category]
        keys = [field['key'] for field in cfg['fields']]
        assert len(keys) == LEGACY_FIELD_COUNTS[category], \
            f'{category} 字段数漂移：应为 {LEGACY_FIELD_COUNTS[category]}，实际 {len(keys)}'
        assert keys == expected_keys, f'{category} 字段键/顺序漂移'
        leaked = NEW_ONLY_KEYS & set(keys)
        assert not leaked, f'{category} 混入新增类别字段：{leaked}'
        # R-086-08：删除自比较恒真断言；字段键/顺序冻结（上方 keys == expected_keys）
        # 已足以守护「旧 7 类零变化」，再序列化同一对象无判别力。


def test_category_total_and_new_ones_declared(app):
    """CATEGORY_CONFIG 共 8 类（既有 5 + 086 新增 3）；新 3 类字段数与必填声明符合规格。"""
    from routes.governance import CATEGORY_CONFIG

    assert len(CATEGORY_CONFIG) == 8, \
        f'类别数应为 8，实际 {len(CATEGORY_CONFIG)}：{sorted(CATEGORY_CONFIG)}'
    # 2026-11-16：社区矫正 / 社区戒毒康复 整类下线，白名单不得残留
    for retired in ('community-correction', 'drug-rehab', 'resettlement'):
        assert retired not in CATEGORY_CONFIG, f'已下线类别仍在白名单：{retired}'
    assert set(NEW_CATEGORIES) <= set(CATEGORY_CONFIG)
    for category in NEW_CATEGORIES:
        cfg = CATEGORY_CONFIG[category]
        assert cfg['title'] == NEW_TITLES[category]
        required = {field['key'] for field in cfg['fields'] if field.get('required')}
        assert required, f'{category} 未声明任何必填字段'

    assert len(CATEGORY_CONFIG['road-hazard']['fields']) == 12
    assert len(CATEGORY_CONFIG['key-vehicle']['fields']) == 10
    assert len(CATEGORY_CONFIG['guide-station']['fields']) == 8
    # 三类必须声明的枚举下拉（字典单一真源，配置层不得写死 options）
    option_map = {
        'road-hazard': {'hazard_type': '道路隐患类型', 'hazard_level': '隐患等级',
                        'fix_status': '整改状态'},
        'key-vehicle': {'record_type': '道交登记类型', 'vehicle_type': '车辆类型',
                        'license_status': '证照情况', 'record_status': '在册状态'},
        'guide-station': {'run_status': '运行状态'},
    }
    for category, expected in option_map.items():
        for field in CATEGORY_CONFIG[category]['fields']:
            assert field['options'] == [], f'{category}.{field["key"]} 硬编码 options'
        got = {field['key']: field.get('option_category')
               for field in CATEGORY_CONFIG[category]['fields']
               if field.get('option_category')}
        assert got == expected, f'{category} 枚举下拉声明不符：{got}'


# ══════════ 3) 三类 save → list → export + 非法枚举 ══════════

@pytest.mark.parametrize('category', NEW_CATEGORIES)
def test_category_save_list_export(app, category):
    """三类各一条：保存 true → 列表回显 → 导出 xlsx；且不串类别。"""
    _unlock_keyholder()
    client = _admin_client(app)
    payload = _category_payload(category)

    resp = client.post(f'/governance/{category}/api/save', data=payload).get_json()
    assert resp['success'] is True, f'{category} 保存失败：{resp}'
    assert _ledger_count(app, category) == 1

    listing = client.get(f'/governance/{category}/api/list').get_json()
    assert listing['success'] is True
    assert listing.get('total') == 1
    row = listing['data'][0]
    assert row['category'] == category
    for key, value in payload.items():
        assert row.get(key) == value, f'{category}.{key} 未回显（实际 {row.get(key)!r}）'

    export = client.get(f'/governance/{category}/api/export')
    assert export.status_code == 200
    assert 'spreadsheetml' in (export.content_type or '')

    # 类别隔离：其余两个新类别不得看到本类数据
    for other in NEW_CATEGORIES:
        if other == category:
            continue
        assert _ledger_count(app, other) == 0, f'{other} 串入 {category} 数据'
        assert client.get(f'/governance/{other}/api/list').get_json().get('total', 0) == 0


@pytest.mark.parametrize('category,field,bad_value', [
    ('road-hazard', 'hazard_level', '乱填等级'),
    ('road-hazard', 'fix_status', '乱填状态'),
    ('road-hazard', 'hazard_type', '乱填类型'),
    ('key-vehicle', 'record_status', '乱填在册状态'),
    ('key-vehicle', 'vehicle_type', '乱填车型'),
    ('guide-station', 'run_status', '乱填运行状态'),
])
def test_invalid_category_enum_rejected(app, category, field, bad_value):
    """非法字典值 → success=False 且不写库（option_category 校验反例）。"""
    _unlock_keyholder()
    client = _admin_client(app)
    payload = _category_payload(category)
    payload[field] = bad_value
    resp = client.post(f'/governance/{category}/api/save', data=payload).get_json()
    assert resp['success'] is False, f'{category}.{field} 非法值被接受'
    assert '不合法' in resp['msg'], f'提示文案不符：{resp["msg"]}'
    assert _ledger_count(app, category) == 0, '非法枚举值写入了库'


@pytest.mark.parametrize('category,missing_field', [
    ('road-hazard', 'road_name'),
    ('road-hazard', 'record_date'),
    ('key-vehicle', 'record_type'),
    ('guide-station', 'station_name'),
])
def test_category_required_fields_enforced(app, category, missing_field):
    """必填字段缺失 → 失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    payload = _category_payload(category)
    payload.pop(missing_field, None)
    resp = client.post(f'/governance/{category}/api/save', data=payload).get_json()
    assert resp['success'] is False
    assert '不能为空' in resp['msg'], f'必填提示不符：{resp["msg"]}'
    assert _ledger_count(app, category) == 0


# ══════════ 4) 信访全流程 ══════════

def test_petition_page_renders(app):
    client = _admin_client(app)
    resp = client.get('/governance/petition/')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '信访维稳' in html
    assert 'id="formModal"' in html


@pytest.mark.parametrize('missing_field,expect_msg', [
    ('petition_no', '事项编号不能为空'),
    ('summary', '诉求摘要不能为空'),
    ('accept_date', '受理日期不能为空'),
])
def test_petition_required_fields(app, missing_field, expect_msg):
    """信访新增三缺一：编号 / 摘要 / 受理日期各一例失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    payload = _petition_payload()
    payload.pop(missing_field)
    resp = client.post('/governance/petition/api/save', data=payload).get_json()
    assert resp['success'] is False
    assert expect_msg in resp['msg'], f'必填提示不符：{resp["msg"]}'
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition') == 0


def test_petition_full_flow_with_handles(app):
    """新增 → 列表 → 详情（含 handles）→ 追加办理记录 → 详情含 1 条 → 导出。"""
    _unlock_keyholder()
    client = _admin_client(app)

    assert _save_petition(client).get_json()['success'] is True
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition') == 1

    listing = _petition_list(client)
    assert listing['success'] is True
    assert listing.get('total') == 1
    row = listing['data'][0]
    assert row['petition_no'] == 'XF2026-0001'
    assert row['petitioner_name'] == NAME
    assert row['category'] == '邻里纠纷'
    assert row['source'] == '来访'
    assert row['status'] == '待受理', f'新增应固定「待受理」，实际 {row["status"]}'
    # 列表打码（信访人联系方式/身份证）
    assert row['contact'] == MASKED_PHONE, f'contact 打码形态错误：{row["contact"]}'
    assert row['id_card'] == MASKED_ID_CARD, f'id_card 打码形态错误：{row["id_card"]}'
    pid = row['id']

    detail = client.get(f'/governance/petition/api/detail/{pid}').get_json()
    assert detail['success'] is True
    assert detail['data']['handles'] == [], '新建信访件不应有办理记录'
    # admin 有 view_sensitive → 详情回填明文（编辑保存需要原值）
    assert detail['data']['contact'] == PHONE
    assert detail['data']['id_card'] == ID_CARD

    handle = client.post('/governance/petition/api/handle-save', data={
        'petition_id': pid, 'handle_date': '2026-03-05', 'handler': '李四',
        'action': '约谈双方当事人', 'result': '双方同意现场丈量',
        'next_plan': '一周内出具调解意见',
    }).get_json()
    assert handle['success'] is True, f'办理记录保存失败：{handle}'
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition_handle') == 1

    detail = client.get(f'/governance/petition/api/detail/{pid}').get_json()
    handles = detail['data']['handles']
    assert len(handles) == 1
    assert handles[0]['handle_date'] == '2026-03-05'
    assert handles[0]['action'] == '约谈双方当事人'
    assert handles[0]['handler'] == '李四'

    export = client.get('/governance/petition/api/export')
    assert export.status_code == 200
    assert 'spreadsheetml' in (export.content_type or '')
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(export.data))
    values = [str(cell.value) for r in wb.active.iter_rows() for cell in r if cell.value]
    assert 'XF2026-0001' in values
    assert ID_CARD not in values, '信访导出泄露身份证明文'
    assert MASKED_ID_CARD in values, '信访导出未打码身份证'


def test_petition_handle_required_fields(app):
    """办理记录：办理日期与办理措施必填。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_petition(client).get_json()['success'] is True
    pid = _petition_list(client)['data'][0]['id']

    no_date = client.post('/governance/petition/api/handle-save', data={
        'petition_id': pid, 'action': '约谈'}).get_json()
    assert no_date['success'] is False and '办理日期不能为空' in no_date['msg']

    no_action = client.post('/governance/petition/api/handle-save', data={
        'petition_id': pid, 'handle_date': '2026-03-05'}).get_json()
    assert no_action['success'] is False and '办理措施不能为空' in no_action['msg']
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition_handle') == 0


# ══════════ 5) 编号唯一（反例） ══════════

def test_duplicate_petition_no_rejected(app):
    """重复 petition_no → 失败且库内 1 行（预检 + 唯一索引双保险）。

    反例判据：若 save 的重复预检与 petition_no 的 unique 索引同时被移除 →
    第二次返回 200 且 COUNT==2 → 本条红。
    """
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_petition(client).get_json()['success'] is True

    dup = _save_petition(client, summary='重复编号的第二条').get_json()
    assert dup['success'] is False, '重复编号不得保存成功'
    assert '已存在' in dup['msg'], f'重复提示文案不符：{dup["msg"]}'
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition') == 1

    # 换编号可保存（约束不过宽）
    assert _save_petition(client, petition_no='XF2026-0002').get_json()['success'] is True
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition') == 2


# ══════════ 6) 状态机 ══════════

def test_petition_transition_state_machine(app):
    """待受理→办理中 true；待受理→已办结 409；办理中→已办结需 close_date+reply；缺 reply 失败。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_petition(client).get_json()['success'] is True
    pid = _petition_list(client)['data'][0]['id']
    assert _petition_status(app, pid) == '待受理'

    # 非法：跳级到已办结 → 409 且状态不变
    skip = _petition_transition(client, pid, '已办结')
    assert skip.status_code == 409, f'非法迁移应 409，实际 {skip.status_code}'
    assert skip.get_json()['success'] is False
    assert '非法状态迁移' in skip.get_json()['msg']
    assert _petition_status(app, pid) == '待受理'

    # 合法：待受理 → 办理中
    assert _petition_transition(client, pid, '办理中').get_json()['success'] is True
    assert _petition_status(app, pid) == '办理中'

    # 缺 close_date + reply → 失败且状态不变
    lacking = _petition_transition(client, pid, '已办结').get_json()
    assert lacking['success'] is False
    assert '办结日期' in lacking['msg'] and '答复' in lacking['msg'], \
        f'办结缺项提示不符：{lacking["msg"]}'
    assert _petition_status(app, pid) == '办理中'

    # 只补 close_date、缺 reply → 仍失败（reply 必填是独立项）
    assert client.post('/governance/petition/api/save', data=_petition_payload(
        id=pid, close_date='2026-03-20')).get_json()['success'] is True
    only_date = _petition_transition(client, pid, '已办结').get_json()
    assert only_date['success'] is False, '缺 reply 时不得办结'
    assert _petition_status(app, pid) == '办理中'

    # 补 reply → 办结 true
    assert client.post('/governance/petition/api/save', data=_petition_payload(
        id=pid, close_date='2026-03-20', reply='已达成调解协议')).get_json()['success'] is True
    assert _petition_transition(client, pid, '已办结').get_json()['success'] is True
    assert _petition_status(app, pid) == '已办结'

    # 终态再迁 → 409
    assert _petition_transition(client, pid, '办理中').status_code == 409


def test_petition_reject_and_invalid_target(app):
    """待受理→不予受理 true；非法目标状态值失败。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_petition(client).get_json()['success'] is True
    pid = _petition_list(client)['data'][0]['id']

    bad_target = _petition_transition(client, pid, '乱填状态').get_json()
    assert bad_target['success'] is False
    assert '不合法' in bad_target['msg']
    assert _petition_status(app, pid) == '待受理'

    assert _petition_transition(client, pid, '不予受理').get_json()['success'] is True
    assert _petition_status(app, pid) == '不予受理'


# ══════════ 6b) R-086-02/03 状态机唯一入口（反例） ══════════

def test_close_via_transition_payload_atomically(app):
    """迁「已办结」可随迁 close_date+reply（R-086-03）；日期非法时整体失败不落库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_petition(client).get_json()['success'] is True
    pid = _petition_list(client)['data'][0]['id']
    assert _petition_transition(client, pid, '办理中').get_json()['success'] is True

    # 随迁办结日期早于受理日期 → 失败；状态/close_date/reply 三处均不得变化（原子性）
    early = client.post('/governance/petition/api/transition', data={
        'id': pid, 'to_status': '已办结',
        'close_date': '2026-02-01', 'reply': '已答复'}).get_json()
    assert early['success'] is False
    assert '办结日期不得早于受理日期' in early['msg'], f'提示文案不符：{early["msg"]}'
    assert _petition_status(app, pid) == '办理中'
    detail = client.get(f'/governance/petition/api/detail/{pid}').get_json()['data']
    assert detail['close_date'] == '' and detail['reply'] == '', '失败迁移落入了随迁字段'

    # 合法随迁 → 一次提交完成状态 + 办结日期 + 答复
    ok = client.post('/governance/petition/api/transition', data={
        'id': pid, 'to_status': '已办结',
        'close_date': '2026-03-20', 'reply': '已达成调解协议'}).get_json()
    assert ok['success'] is True, f'随迁办结迁移失败：{ok}'
    detail = client.get(f'/governance/petition/api/detail/{pid}').get_json()['data']
    assert detail['status'] == '已办结'
    assert detail['close_date'] == '2026-03-20'
    assert detail['reply'] == '已达成调解协议'


def test_save_cannot_bypass_close_guard(app):
    """save 与 transition 共用「办结必须有日期+答复」前置条件（R-086-02）。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_petition(client).get_json()['success'] is True
    pid = _petition_list(client)['data'][0]['id']
    assert _petition_transition(client, pid, '办理中').get_json()['success'] is True

    blocked = client.post('/governance/petition/api/save', data=_petition_payload(
        id=pid, status='已办结')).get_json()
    assert blocked['success'] is False, '保存接口绕过了办结前置条件'
    assert '办结前请填写办结日期与答复情况' in blocked['msg']
    assert _petition_status(app, pid) == '办理中'

    # 补齐后经 save 合法迁移同样放行（save 与 transition 同一真源）
    allowed = client.post('/governance/petition/api/save', data=_petition_payload(
        id=pid, status='已办结', close_date='2026-03-20', reply='已答复来信人')).get_json()
    assert allowed['success'] is True, f'补齐后保存失败：{allowed}'
    assert _petition_status(app, pid) == '已办结'


# ══════════ 7) 日期校验 ══════════

def test_petition_close_date_before_accept_date_rejected(app):
    """close_date < accept_date → 失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    resp = _save_petition(client, accept_date='2026-03-01',
                          close_date='2026-02-01').get_json()
    assert resp['success'] is False
    assert '办结日期不得早于受理日期' in resp['msg'], f'日期先后提示不符：{resp["msg"]}'
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition') == 0


def test_petition_invalid_enum_rejected(app):
    """诉求类别 / 来源 非字典值 → 失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    for field, value in (('category', '乱填类别'), ('source', '乱填来源')):
        payload = _petition_payload()
        payload[field] = value
        resp = client.post('/governance/petition/api/save', data=payload).get_json()
        assert resp['success'] is False, f'{field} 非法值被接受'
        assert '不合法' in resp['msg']
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition') == 0


# ══════════ 8) 权限 ══════════

def test_petition_requires_login(app):
    """未登录：页面 302 跳登录、API 401（不是 200 成功）。"""
    anonymous = app.test_client()
    page = anonymous.get('/governance/petition/')
    assert page.status_code == 302
    assert 'login' in (page.headers.get('Location') or '').lower()

    api = anonymous.get('/governance/petition/api/list',
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert api.status_code == 401
    assert api.get_json()['success'] is False


def test_petition_denied_without_mediation_permission(app):
    """无 mediation 位角色：页面 403、API 403、写接口 403 且不写库。"""
    _unlock_keyholder()
    client = _role_client(app, {'resident': {'view': True}})
    page = client.get('/governance/petition/')
    assert page.status_code == 403, f'无 mediation 位页面应 403，实际 {page.status_code}'

    for path in ('/governance/petition/api/list', '/governance/petition/api/options',
                 '/governance/petition/api/export'):
        resp = client.get(path, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 403, f'{path} 无 mediation 位应 403，实际 {resp.status_code}'
        assert (resp.get_json() or {}).get('success') is not True

    save = client.post('/governance/petition/api/save', data=_petition_payload(),
                       headers={'X-Requested-With': 'XMLHttpRequest'})
    assert save.status_code == 403
    assert _count(app, 'SELECT COUNT(*) FROM governance_petition') == 0, '越权请求写入了数据'

    # 三类新增台账同属 governance 蓝图 → mediation 位同样守护
    resp = client.get('/governance/road-hazard/api/list',
                      headers={'X-Requested-With': 'XMLHttpRequest'})
    assert resp.status_code == 403


# ══════════ 9) 指标 ══════════

def test_metrics_petition_total_matches_db(app):
    """governance_petition 指标 total 与库内行数一致，且按 status 分组。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_petition(client).get_json()['success'] is True
    assert _save_petition(client, petition_no='XF2026-0002',
                          category='土地宅基地', summary='承包地界争议').get_json()['success'] is True
    pid = _petition_list(client)['data'][-1]['id']
    assert _petition_transition(client, pid, '办理中').get_json()['success'] is True

    db_total = _count(app, 'SELECT COUNT(*) FROM governance_petition')
    assert db_total == 2

    from utils.metrics import MetricsEngine
    with app.app_context():
        result = MetricsEngine.query('governance_petition')

    assert result['total'] == db_total, \
        f'governance_petition.total={result["total"]} 与库内 {db_total} 行不一致'
    buckets = {bucket['key']: bucket['count'] for bucket in result['buckets']}
    assert buckets.get('待受理') == 1 and buckets.get('办理中') == 1, buckets


# ══════════ 9b) R-086-06 办结率指标 ══════════

def test_metrics_petition_rate_decimal_and_year_filter(app):
    """R-086-06：办结率 = closed/total（Decimal 两位）；空表 0、跨年过滤不串。"""
    _unlock_keyholder()
    client = _admin_client(app)
    from utils.metrics import MetricsEngine
    from utils.agg_cache import invalidate

    with app.app_context():
        empty = MetricsEngine.query('governance_petition_rate')
    assert empty['total'] == 0
    assert empty['data']['closed'] == 0
    assert empty['data']['rate'] == '0.00', empty['data']

    assert _save_petition(client).get_json()['success'] is True
    assert _save_petition(client, petition_no='XF2026-0002',
                          category='土地宅基地').get_json()['success'] is True
    assert _save_petition(client, petition_no='XF2025-0001',
                          accept_date='2025-05-01',
                          due_date='2025-06-01').get_json()['success'] is True

    rows = _petition_list(client)['data']
    pid = next(row['id'] for row in rows if row['petition_no'] == 'XF2026-0001')
    assert _petition_transition(client, pid, '办理中').get_json()['success'] is True
    close = client.post('/governance/petition/api/transition', data={
        'id': pid, 'to_status': '已办结',
        'close_date': '2026-03-20', 'reply': '已完成答复',
    }).get_json()
    assert close['success'] is True, close

    invalidate()
    with app.app_context():
        year_2026 = MetricsEngine.query('governance_petition_rate',
                                        filters={'accept_year': '2026'})
        year_2025 = MetricsEngine.query('governance_petition_rate',
                                        filters={'accept_year': '2025'})

    assert year_2026['total'] == 2, year_2026
    assert year_2026['data']['closed'] == 1, year_2026['data']
    assert year_2026['data']['rate'] == '0.50', year_2026['data']
    assert year_2025['total'] == 1 and year_2025['data']['closed'] == 0, year_2025
    assert year_2025['data']['rate'] == '0.00', year_2025['data']


# ══════════ 9c) R-086-11/12 字段歧义与道交电话打码 ══════════

def test_key_vehicle_register_date_label_disambiguated():
    """R-086-11：record_date / register_date 不得同日同名。"""
    from routes.governance import CATEGORY_CONFIG
    labels = {field['key']: field['label'] for field in CATEGORY_CONFIG['key-vehicle']['fields']}
    assert labels['record_date'] == '登记日期'
    assert labels['register_date'] == '初次登记日期', labels


def test_road_vehicle_phone_masked_for_non_sensitive_role(app):
    """R-086-12：道交 owner_phone/staff_phone 列表与导出对无 view_sensitive 角色打码。"""
    _unlock_keyholder()
    admin = _admin_client(app)
    assert admin.post('/governance/road-hazard/api/save',
                      data=_category_payload('road-hazard')).get_json()['success'] is True
    assert admin.post('/governance/guide-station/api/save',
                      data=_category_payload('guide-station')).get_json()['success'] is True

    client = _role_client(app, {
        '__schema_version__': 3,
        'special': {'view_sensitive': False, 'death_confirm': False,
                    'import_batch': False, 'backup_restore': False,
                    'mask_config': False},
        'mediation.view': True,
        'mediation.export': True,
    })
    checks = {
        'road-hazard': ('owner_phone', '13812345670', '138****5670'),
        'guide-station': ('staff_phone', '13812345671', '138****5671'),
    }
    for category, (key, plain, masked) in checks.items():
        resp = client.get(f'/governance/{category}/api/list')
        assert resp.status_code == 200, (category, resp.status_code,
                                         resp.get_data(as_text=True)[:300])
        body = resp.get_data(as_text=True)
        row = resp.get_json()['data'][0]
        assert row[key] == masked, f'{category}.{key} 未打码：{row[key]}'
        assert plain not in body, f'{category} 列表泄露明文电话'

    export = client.get('/governance/road-hazard/api/export')
    assert export.status_code == 200
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(export.data))
    values = [str(cell.value) for row in wb.active.iter_rows() for cell in row if cell.value]
    assert '138****5670' in values
    assert '13812345670' not in values, '道交导出泄露明文电话'
