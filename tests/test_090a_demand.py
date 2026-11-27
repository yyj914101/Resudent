# -*- coding: utf-8 -*-
"""tests/test_090a_demand.py — 090A 民生诉求（demand 蓝图 / 两跳状态机 / 三指标）

覆盖（docs/tasks/090A-livelihood-demand.md 验收标准 + 一页纸 v1.1 校验口径）：
A. 页面与登录闸门：GET /demand/ 200；未登录页面 302 / API 401
B. 字典 options：三分类 value/label 对（「诉求来源」value 编码：12345→「12345 热线」）
C. 登记闭环：新增固定「待受理」；必填校验；V-02 非字典值 400 语义失败；
   掩码回显不覆盖密文（R12 防护）
D. 状态机（验收 1）：跳级/终态/非法目标 409 且不落库；办结缺 close_date 拒绝；
   close_date < accept_date 拒绝（原子性：状态/日期零改动）；不予受理缺依据拒绝
E. save 侧共用前置（R-086-02 同款防绕行）：status=已办结 无 close_date 失败、
   close_date<accept_date 失败、不予受理缺依据失败；合法迁移经 save 放行
F. 处理记录：挂靠已存在的诉求单；V-06 单不存在失败
G. 导出（验收 4）：xlsx 列序按一页纸 §5；来源出 label；PII 恒打码、身份证不外显
H. 三指标三处同值（验收 3）：引擎直调 == /demand/api/stats == /api/stats/query；
   办结率口径 closed/total；平均办结天数 Decimal 应用层（3+5 天 → 4.00）；
   accept_year 过滤；空表 fail-soft
I. 权限（验收 5）：demand.view 授权红→绿；无权限 403（页面/list/save 越权不写库）；
   删除仅管理员；统一入口按 demand.view 二次校验
J. m034 幂等（验收 6）：重放建表/字典全跳过；缺字典表 RuntimeError
"""
import importlib.util
import io
import json
import os
import sqlite3

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_M034_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm034_livelihood_demand.py')

VALID_SOURCE = '12345'          # 「诉求来源」value 编码（一页纸 D-01）
VALID_CATEGORY = '邻里纠纷'


# ------------------------------------------------------------------ 夹具/工具

def _load_m034():
    spec = importlib.util.spec_from_file_location('m034_under_test', _M034_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def app(isolated_app_factory):
    """隔离应用（088 同款：密钥解锁；demand 字典由 ensure_enum_dicts 启动守卫自动补种）。"""
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())
    return application


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    from utils import agg_cache
    agg_cache.invalidate()
    yield
    agg_cache.invalidate()


def _login(client, user_id, username='admin'):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _admin_id(app):
    with app.app_context():
        from models import User
        return User.query.filter_by(username='admin').first().id


def _make_role_user(app, username, permissions):
    with app.app_context():
        from models import Role, User, db
        role = Role(name='role_%s' % username, description='090A 用例',
                    permissions=json.dumps(permissions))
        db.session.add(role)
        db.session.commit()
        user = User(username=username, role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        return user.id


def _demand_payload(**overrides):
    """一页纸 §1 必填 + 常用可选字段（来源 value 编码、类别 label=value）。"""
    payload = {
        'source': VALID_SOURCE,
        'category': VALID_CATEGORY,
        'petitioner_name': '张三',
        'contact': '13812345678',
        'id_card': '',
        'address': '幸福村一组',
        'summary': '村道路灯长期不亮，夜间出行不便',
        'accept_date': '2026-03-01',
        'case_officer': '李四',
        'remark': '',
        'id': '0',
    }
    payload.update(overrides)
    return payload


def _save_demand(client, **overrides):
    return client.post('/demand/api/save', data=_demand_payload(**overrides))


def _transition(client, demand_id, to_status, **extra):
    data = {'id': str(demand_id), 'to_status': to_status}
    data.update(extra)
    return client.post('/demand/api/transition', data=data)


def _demand_status(app, demand_id):
    with app.app_context():
        from models import Demand
        item = Demand.query.get(demand_id)
        return item.status if item else None


def _demand_field(app, demand_id, field):
    with app.app_context():
        from models import Demand
        item = Demand.query.get(demand_id)
        return getattr(item, field)


def _demand_decrypt(item, field):
    from utils.sensitive_fields import decrypt_field
    from utils.crypto import key_holder
    field_map = {
        'contact': ('contact_enc', 'contact_hash', None),
        'id_card': ('id_card_enc', 'id_card_hash', None),
    }
    return decrypt_field(item, field, field_map, key_holder)


def _count(app, sql):
    with app.app_context():
        import config as cfg
        conn = sqlite3.connect(app.config.get('TEST_DB_PATH', cfg.DB_PATH))
        try:
            return conn.execute(sql).fetchone()[0]
        finally:
            conn.close()


def _sheet_values(resp):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.data), read_only=True, data_only=True)
    values = []
    for row in wb.active.iter_rows(values_only=True):
        values.extend(str(c) for c in row if c is not None)
    wb.close()
    return values


# ------------------------------------------------------------------ A. 页面与登录闸门

def test_page_requires_admin_login(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    resp = client.get('/demand/')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert '民生诉求' in resp.get_data(as_text=True)


def test_anonymous_gates(app):
    client = app.test_client()
    resp = client.get('/demand/')
    assert resp.status_code == 302, resp.get_data(as_text=True)
    assert '/auth/login' in resp.headers.get('Location', '')
    api = client.get('/demand/api/list', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert api.status_code == 401
    assert api.get_json()['success'] is False


# ------------------------------------------------------------------ B. 字典 options

def test_options_value_label_pairs(app):
    """三分类下发 value/label 对；「诉求来源」value 编码（12345 → 12345 热线）。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    data = client.get('/demand/api/options').get_json()['data']
    sources = {p['value']: p['label'] for p in data['诉求来源']}
    assert sources['12345'] == '12345 热线', '一页纸 D-01：value 编码 + label 展示'
    assert sources['visit'] == '走访'
    categories = {p['value'] for p in data['诉求类别']}
    assert categories == {'邻里纠纷', '土地宅基地', '其他'}, 'D-02 删惠农补贴/涉法涉诉/干部作风'
    statuses = [p['value'] for p in data['诉求办理状态']]
    assert statuses == ['待受理', '已办结', '不予受理'], 'D-03 两跳状态机取值'


# ------------------------------------------------------------------ C. 登记闭环

def test_create_defaults_pending_and_stores_enum_value(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    resp = _save_demand(client)
    assert resp.get_json()['success'], resp.get_data(as_text=True)
    did = resp.get_json()['data']['id']
    assert _demand_status(app, did) == '待受理', '新增固定「待受理」'
    assert _demand_field(app, did, 'source') == VALID_SOURCE, '枚举 value 落库'
    with app.app_context():
        from models import Demand
        item = Demand.query.get(did)
        assert item.contact_enc and item.contact is None, 'R12 双列：密文落库明文置 NULL'
        assert item.accept_date is not None


def test_create_required_and_enum_validation(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    missing = _save_demand(client, summary='')
    assert missing.get_json()['success'] is False
    assert _count(app, 'SELECT COUNT(*) FROM demand') == 0, '失败不得落库'
    bad_enum = _save_demand(client, source='不属于字典的来源')
    body = bad_enum.get_json()
    assert body['success'] is False and '不在字典' in body['msg'], 'V-02 语义失败'
    assert _count(app, 'SELECT COUNT(*) FROM demand') == 0


def test_mask_backfill_keeps_ciphertext(app):
    """编辑时提交打码 contact（含 '*'）→ 该字段跳过写入，解密明文不变（Fernet 随机 IV，
    密文逐次不同，只能以明文比对）。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    did = _save_demand(client).get_json()['data']['id']
    masked = '138****5678'
    resp = _save_demand(client, id=str(did), contact=masked, summary='内容更新')
    assert resp.get_json()['success'], resp.get_data(as_text=True)
    with app.app_context():
        from models import Demand
        item = Demand.query.get(did)
        assert item.contact_enc, '密文仍在库'
        assert _demand_decrypt(item, 'contact') == '13812345678', \
            '掩码回填不得覆盖原明文（跳过写入）'
        assert item.summary == '内容更新', '非敏感字段正常更新'


def test_no_view_user_edit_keeps_plain_name_and_address(app):
    """P1-1 红→绿：无权用户编辑提交打码姓名/住址 → 跳过写入，库内明文不变（防静默损坏）。"""
    admin = app.test_client()
    _login(admin, _admin_id(app))
    did = _save_demand(admin, petitioner_name='张长丰',
                       address='幸福村一组村道旁').get_json()['data']['id']

    uid = _make_role_user(app, 'd090a_maskedit',
                          {'demand.view': True, 'demand.add': True})
    client = app.test_client()
    _login(client, uid, 'd090a_maskedit')
    detail = client.get('/demand/api/detail/%d' % did).get_json()['data']
    assert detail['petitioner_name'] == '张**', '无权用户详情打码'
    assert detail['address'] == '幸福村一组村**', '无权用户详情住址打码'
    assert '*' in detail['contact'], '联系方式打码回显'

    payload = _demand_payload(id=str(did), petitioner_name=detail['petitioner_name'],
                              contact=detail['contact'], address=detail['address'],
                              summary='内容更新')
    resp = client.post('/demand/api/save', data=payload)
    assert resp.get_json()['success'], resp.get_data(as_text=True)

    with app.app_context():
        from models import Demand
        item = Demand.query.get(did)
        assert item.petitioner_name == '张长丰', '打码姓名不得覆盖明文'
        assert item.address == '幸福村一组村道旁', '打码住址不得覆盖明文'
        assert _demand_decrypt(item, 'contact') == '13812345678', '打码联系方式不得覆盖明文'
        assert item.summary == '内容更新', '非 PII 字段正常更新'


# ------------------------------------------------------------------ D. 状态机（transition）

def test_transition_two_hop_state_machine(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    did = _save_demand(client).get_json()['data']['id']

    # 待受理→已办结是合法迁移，但缺 close_date 前置 → 语义失败（非 409）
    no_date = _transition(client, did, '已办结')
    assert no_date.get_json()['success'] is False
    assert '办结日期' in no_date.get_json()['msg']
    assert _demand_status(app, did) == '待受理'

    first = _transition(client, did, '已办结', close_date='2026-03-05')
    assert first.status_code == 200 and first.get_json()['success'], first.get_data(as_text=True)
    assert _demand_status(app, did) == '已办结'
    assert str(_demand_field(app, did, 'close_date')) == '2026-03-05'

    terminal = _transition(client, did, '不予受理')
    assert terminal.status_code == 409, '终态再迁移必须 409'
    assert terminal.get_json()['success'] is False
    assert '非法状态迁移' in terminal.get_json()['msg']
    assert _demand_status(app, did) == '已办结', '409 后状态零改动'


def test_transition_illegal_target_409(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    did = _save_demand(client).get_json()['data']['id']
    resp = _transition(client, did, '乱填状态')
    body = resp.get_json()
    assert body['success'] is False and '不合法' in body['msg']
    assert _demand_status(app, did) == '待受理'


def test_transition_close_date_validation(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    did = _save_demand(client, accept_date='2026-03-01').get_json()['data']['id']
    missing = _transition(client, did, '已办结')
    assert missing.get_json()['success'] is False
    assert '办结日期' in missing.get_json()['msg']
    early = _transition(client, did, '已办结', close_date='2026-02-01')
    assert early.get_json()['success'] is False
    assert '不得早于受理日期' in early.get_json()['msg'], 'V-03'
    assert _demand_status(app, did) == '待受理', '拒绝后零落库'
    assert _demand_field(app, did, 'close_date') is None, '随迁字段不写入'
    ok = _transition(client, did, '已办结', close_date='2026-03-05')
    assert ok.get_json()['success'] and _demand_status(app, did) == '已办结'


def test_transition_reject_requires_remark(app):
    """S-02：不予受理须写明依据，随迁 remark。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    did = _save_demand(client).get_json()['data']['id']
    missing = _transition(client, did, '不予受理')
    assert missing.get_json()['success'] is False
    assert '依据' in missing.get_json()['msg']
    assert _demand_status(app, did) == '待受理'
    ok = _transition(client, did, '不予受理', remark='不属于村级受理范围')
    assert ok.get_json()['success'] and _demand_status(app, did) == '不予受理'
    assert _demand_field(app, did, 'remark') == '不属于村级受理范围', '随迁字段仅终态迁移写入'


# ------------------------------------------------------------------ E. save 侧共用前置（防绕行）

def test_save_side_transition_guard(app):
    """R-086-02 同款：save 与 transition 共用状态机前置，防绕行。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    did = _save_demand(client, accept_date='2026-03-01').get_json()['data']['id']

    blocked = _save_demand(client, id=str(did), status='已办结')
    body = blocked.get_json()
    assert body['success'] is False and '办结日期' in body['msg']
    assert _demand_status(app, did) == '待受理'

    early = _save_demand(client, id=str(did), status='已办结', close_date='2026-02-01')
    assert early.get_json()['success'] is False
    assert '不得早于受理日期' in early.get_json()['msg']

    no_remark = _save_demand(client, id=str(did), status='不予受理')
    assert no_remark.get_json()['success'] is False
    assert '依据' in no_remark.get_json()['msg']

    legal = _save_demand(client, id=str(did), status='已办结', close_date='2026-03-06')
    assert legal.get_json()['success'], legal.get_data(as_text=True)
    assert _demand_status(app, did) == '已办结'

    another = _save_demand(client)  # 新单
    other_id = another.get_json()['data']['id']
    illegal = _save_demand(client, id=str(other_id), status='不予受理')
    # 新单（待受理）→不予受理是合法迁移，但缺依据 → 前置拦截
    assert illegal.get_json()['success'] is False
    assert '依据' in illegal.get_json()['msg']
    assert _demand_status(app, other_id) == '待受理'


def test_save_new_ignores_posted_status(app):
    """新增固定「待受理」：提交 status=已办结 也被忽略（一页纸 F-012 系统口径）。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    resp = _save_demand(client, status='已办结', close_date='2026-03-05')
    assert resp.get_json()['success']
    did = resp.get_json()['data']['id']
    assert _demand_status(app, did) == '待受理'


# ------------------------------------------------------------------ F. 处理记录

def test_handle_record_flow(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    did = _save_demand(client).get_json()['data']['id']
    ok = client.post('/demand/api/handle-save', data={
        'demand_id': str(did), 'handle_date': '2026-03-02',
        'handler': '王五', 'action': '现场核实并约谈相关住户'})
    body = ok.get_json()
    assert body['success'], ok.get_data(as_text=True)
    detail = client.get('/demand/api/detail/%d' % did).get_json()['data']
    assert len(detail['handles']) == 1
    assert detail['handles'][0]['handler'] == '王五'

    missing = client.post('/demand/api/handle-save', data={
        'demand_id': '99999', 'handle_date': '2026-03-02',
        'handler': 'x', 'action': 'y'})
    assert missing.get_json()['success'] is False, 'V-06 单不存在失败'
    empty = client.post('/demand/api/handle-save', data={
        'demand_id': str(did), 'handle_date': '2026-03-02', 'handler': '王五', 'action': ''})
    assert empty.get_json()['success'] is False


# ------------------------------------------------------------------ G. 导出

def test_export_columns_labels_and_masking(app):
    """验收 4：列序按一页纸 §5；来源出 label；PII 恒打码、身份证不外显。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    _save_demand(client, petitioner_name='张长丰', contact='13812345678',
                 address='幸福村一组村道旁')
    resp = client.get('/demand/api/export')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert 'spreadsheetml' in (resp.content_type or '')

    values = _sheet_values(resp)
    assert '受理日期' in values and '办结日期' in values, '导出表头对齐一页纸 §5'
    assert '12345 热线' in values, '来源列出 label（value 编码映射）'
    assert '张长丰' not in values, '诉求人恒打码'
    assert '张**' in values, '姓名保首字打码'
    assert '13812345678' not in values and '138****5678' in values
    assert '幸福村一组村道旁' not in values, '住址恒打码'


def test_export_respects_date_range(app):
    """P2-1 红→绿：导出必须与列表同口径，受理日期区间筛选生效。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    _save_demand(client, accept_date='2026-03-01')
    _save_demand(client, accept_date='2026-03-02')
    _save_demand(client, accept_date='2026-03-03')

    resp = client.get('/demand/api/export?accept_from=2026-03-02&accept_to=2026-03-02')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    values = _sheet_values(resp)
    assert '2026-03-02' in values, '区间内记录应导出'
    assert '2026-03-01' not in values and '2026-03-03' not in values, \
        '区间外记录不得导出（修复前导出忽略日期筛选）'


def test_list_masks_pii_even_for_view_sensitive(app):
    """P1-2 红→绿：列表恒打码（admin 与 view_sensitive 用户一致）；明文仅 detail。"""
    admin = app.test_client()
    _login(admin, _admin_id(app))
    did = _save_demand(admin, petitioner_name='张长丰',
                       address='幸福村一组村道旁').get_json()['data']['id']
    row = admin.get('/demand/api/list').get_json()['data']['items'][0]
    assert row['petitioner_name'] == '张**', 'admin 列表姓名仍打码'
    assert row['address'] == '幸福村一组村**', 'admin 列表住址仍打码'
    assert row['contact'] == '138****5678', 'admin 列表联系方式打码'
    detail = admin.get('/demand/api/detail/%d' % did).get_json()['data']
    assert detail['petitioner_name'] == '张长丰', '明文仅 detail + view_sensitive'

    uid = _make_role_user(app, 'd090a_vs',
                          {'demand.view': True,
                           'special': {'view_sensitive': True}})
    other = app.test_client()
    _login(other, uid, 'd090a_vs')
    vs_row = other.get('/demand/api/list').get_json()['data']['items'][0]
    assert vs_row['petitioner_name'] == '张**', 'view_sensitive 用户列表同样打码'
    vs_detail = other.get('/demand/api/detail/%d' % did).get_json()['data']
    assert vs_detail['petitioner_name'] == '张长丰', 'view_sensitive 详情回明文'


def test_list_limit_alias_and_clamp(app):
    """P2-4 红→绿：后端认前端 `limit` 且夹取 1~100（防整表倾卸）。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    for i in range(3):
        _save_demand(client, accept_date='2026-03-0%d' % (i + 1))

    data = client.get('/demand/api/list?limit=2').get_json()['data']
    assert len(data['items']) == 2 and data['total'] == 3, 'limit 生效'
    data0 = client.get('/demand/api/list?limit=0').get_json()['data']
    assert len(data0['items']) == 1, 'limit=0 夹取到下限 1'
    big = client.get('/demand/api/list?limit=100000').get_json()['data']
    assert len(big['items']) <= 100, 'limit 不得超过上限 100'


# ------------------------------------------------------------------ H. 三指标三处同值

def test_metrics_three_callers_same_value(app):
    """验收 3：引擎直调 == /demand/api/stats == /api/stats/query。"""
    client = app.test_client()
    _login(client, _admin_id(app))
    _save_demand(client, accept_date='2026-03-01', status='')  # 待受理
    did2 = _save_demand(client, accept_date='2026-03-02').get_json()['data']['id']
    did3 = _save_demand(client, accept_date='2026-03-03').get_json()['data']['id']
    _transition(client, did2, '已办结', close_date='2026-03-05')   # 3 天
    _transition(client, did3, '已办结', close_date='2026-03-12')   # 9 天
    _save_demand(client, accept_date='2026-03-04')  # 待受理

    from utils.metrics import MetricsEngine
    with app.app_context():
        stats = MetricsEngine.query('demand_stats')
        rate = MetricsEngine.query('demand_close_rate')
        days = MetricsEngine.query('demand_avg_close_days')
    assert stats['total'] == 4
    assert rate['data'] == {'total': 4, 'closed': 2, 'rate': '0.50'}, \
        '办结率 = 已办结 /（已办结+不予受理+待受理）'
    assert days['data']['dated'] == 2 and days['data']['avg_days'] == '6.00', \
        '平均办结天数 = (3+9)/2 = 6.00（Decimal 应用层）'

    proxy = client.get('/demand/api/stats').get_json()
    assert proxy['success'] and proxy['data']['close_rate'] == rate['data']
    assert proxy['data']['avg_close_days'] == days['data']
    unified = client.get('/api/stats/query?metric_key=demand_close_rate').get_json()
    assert unified['data']['data'] == rate['data'], '统一入口与引擎同值'
    unified_days = client.get('/api/stats/query?metric_key=demand_avg_close_days').get_json()
    assert unified_days['data']['data'] == days['data']


def test_metrics_accept_year_filter_and_empty(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    from utils.metrics import MetricsEngine
    with app.app_context():
        empty = MetricsEngine.query('demand_close_rate')['data']
    assert empty == {'total': 0, 'closed': 0, 'rate': '0.00'}, '空表 fail-soft'
    _save_demand(client, accept_date='2025-06-01')
    did = _save_demand(client, accept_date='2026-06-01').get_json()['data']['id']
    _transition(client, did, '不予受理', remark='非村级事项')
    from utils import agg_cache
    agg_cache.invalidate()
    with app.app_context():
        y2025 = MetricsEngine.query('demand_close_rate', filters={'accept_year': '2025'})['data']
        y2026 = MetricsEngine.query('demand_close_rate', filters={'accept_year': '2026'})['data']
    assert y2025['total'] == 1 and y2026['total'] == 1, 'accept_year 跨年不串'
    assert y2026['rate'] == '0.00' and y2026['closed'] == 0


def test_metrics_stats_buckets_and_write_invalidation(app):
    client = app.test_client()
    _login(client, _admin_id(app))
    _save_demand(client, category='邻里纠纷')
    from utils.metrics import MetricsEngine
    with app.app_context():
        buckets = MetricsEngine.query('demand_stats')['buckets']
    assert any(b['key'] == '待受理' and b['count'] == 1 for b in buckets)
    did = _save_demand(client, category='土地宅基地').get_json()['data']['id']
    _transition(client, did, '已办结', close_date='2026-03-08')
    from utils import agg_cache
    agg_cache.invalidate()
    with app.app_context():
        buckets = MetricsEngine.query('demand_stats')['buckets']
    assert any(b['key'] == '已办结' and b['count'] == 1 for b in buckets), '写后失效再取'


# ------------------------------------------------------------------ I. 权限

def test_role_with_demand_view_granted(app):
    """红→绿：授予 demand.view 的非超管可访问页面/列表/指标代理。"""
    uid = _make_role_user(app, 'd090a_clerk', {'demand.view': True})
    client = app.test_client()
    _login(client, uid, 'd090a_clerk')
    assert client.get('/demand/').status_code == 200
    assert client.get('/demand/api/list').get_json()['success'] is True
    assert client.get('/demand/api/stats').get_json()['success'] is True


def test_role_without_demand_forbidden(app):
    uid = _make_role_user(app, 'd090a_none', {})
    client = app.test_client()
    _login(client, uid, 'd090a_none')
    assert client.get('/demand/').status_code == 403
    assert client.get('/demand/api/list').status_code == 403
    save = client.post('/demand/api/save', data=_demand_payload())
    assert save.status_code == 403, '越权 save 403'
    assert _count(app, 'SELECT COUNT(*) FROM demand') == 0, '越权请求不写库'
    unified = client.get('/api/stats/query?metric_key=demand_close_rate')
    assert unified.status_code == 403, '统一入口按 demand.view 二次校验'


def test_delete_admin_only(app):
    """一页纸 §6：删除仅管理员（admin_required），子表级联删除。"""
    admin = app.test_client()
    _login(admin, _admin_id(app))
    did = _save_demand(admin).get_json()['data']['id']
    admin.post('/demand/api/handle-save', data={
        'demand_id': str(did), 'handle_date': '2026-03-02',
        'handler': '王五', 'action': '核实'})

    uid = _make_role_user(app, 'd090a_deleter',
                          {'demand.view': True, 'demand.delete': True})
    other = app.test_client()
    _login(other, uid, 'd090a_deleter')
    resp = other.post('/demand/api/delete/%d' % did)
    assert resp.status_code == 403, '非超管即使有 demand.delete 位也不可删除'
    assert _count(app, 'SELECT COUNT(*) FROM demand') == 1

    gone = admin.post('/demand/api/delete/%d' % did)
    assert gone.get_json()['success']
    assert _count(app, 'SELECT COUNT(*) FROM demand') == 0
    assert _count(app, 'SELECT COUNT(*) FROM demand_handle') == 0, '子表级联删除'


# ------------------------------------------------------------------ J. m034 幂等

class TestM034Migration:
    """验收 6：m034 重放幂等；缺字典表拒绝。"""

    def test_idempotent_replay(self, isolated_app_factory):
        _app = isolated_app_factory()
        # 启动守卫 ensure_enum_dicts 已按 DICT_SEEDS 种齐 3 分类（与部署序一致），
        # 先删该 3 分类再跑迁移，验证「首跑补齐 + 二跑全跳过」的幂等闭环。
        with _app.app_context():
            from models import DictCategory, DictItem, db
            for name in ('诉求来源', '诉求类别', '诉求办理状态'):
                cat = DictCategory.query.filter_by(name=name).first()
                if cat:
                    DictItem.query.filter_by(category_id=cat.id).delete()
                    db.session.delete(cat)
            db.session.commit()
        m034 = _load_m034()
        db_path = os.environ['SHUCUN_DB_PATH']
        first = m034.run(db_path)
        assert sorted(first['tables']) == ['demand', 'demand_handle']
        assert first['categories'] == ['诉求来源', '诉求类别', '诉求办理状态']
        n_items = len(first['items'])
        assert n_items == 12, '6 来源 + 3 类别 + 3 状态 = 12 条目：%s' % first
        second = m034.run(db_path)
        assert second['categories'] == [] and second['items'] == [], '二跑零重复插入'
        assert second['skipped_items'] == n_items
        conn = sqlite3.connect(db_path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                             "AND name IN ('demand','demand_handle')").fetchone()[0]
        finally:
            conn.close()
        assert n == 2

    def test_encoded_source_values_in_dict(self, isolated_app_factory):
        """D-01：诉求来源 value 编码（12345/visit/grid/phone/onsite/other）。"""
        _app = isolated_app_factory()
        db_path = os.environ['SHUCUN_DB_PATH']
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT i.label, i.value FROM dict_items i "
                "JOIN dict_categories c ON c.id = i.category_id "
                "WHERE c.name = '诉求来源' ORDER BY i.sort_order").fetchall()
        finally:
            conn.close()
        assert rows == [('12345 热线', '12345'), ('走访', 'visit'), ('网格', 'grid'),
                        ('电话', 'phone'), ('来访', 'onsite'), ('其他', 'other')]

    def test_missing_dict_tables_raise(self, tmp_path):
        empty_db = str(tmp_path / 'no_dict.db')
        with pytest.raises(RuntimeError):
            _load_m034().run(empty_db)
