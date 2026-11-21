# -*- coding: utf-8 -*-
"""tests/test_084_employment_service.py — 084 就业服务（就业状态登记 + 灵活就业补贴申报）验收。

被测实现（读码确认，本文件只测不改）：
- 模型：models/welfare_employment.py、models/welfare_flex_subsidy.py
- 迁移：scripts/migrations/m037_employment_service.py
- 路由：routes/welfare_job.py（welfare_bp，/welfare/employment/ 与 /api/{options,list,
  detail,save,delete,transition,export}，tab=employment|subsidy 双台账同页）
- 指标：utils/metrics.py 注册 welfare_employment / welfare_flex_subsidy

覆盖 11 组验收（对应用户故事 AC）：
  1) 页面 200 + 双 tab 文案；未登录 302 / API 401
  2) employment 新增 → 列表 → 状态筛选 → 详情 → 删除
  3) 列表打码：身份证 前6后4、电话 前3后4
  4) 唯一约束：同身份证重复登记失败且库内仍 1 行
  5) 唯一约束：补贴申报 (身份证, 年度, 类型) 重复失败且库内仍 1 行
  6) 状态机：合法迁移 true / 非法迁移 HTTP 409 / 已发放需 issue_date / 未通过需 reject_reason
  7) 非法枚举值保存失败且不写库
  8) 日期校验：issue_date < apply_date 失败；apply_year 越界失败
  9) 导出 xlsx（content-type + 服务端打码落表）
 10) 权限：无 welfare 位的角色 403、未登录 401/302（断言不是「200 且 success=true」）
 11) 指标：MetricsEngine.query total 与库内行数一致

反例判据（半修复态防护，均以注释写明「移除校验即红」）：
- 非法状态迁移：断言 HTTP 409 且状态未变；若 SUBSIDY_TRANSITIONS 判定被移除 → 200 且 true → 红。
- 唯一约束：断言第二次保存 success=False 且库内 COUNT==1；若 unique 索引/IntegrityError
  分支被移除 → 200 且 COUNT==2 → 红。
- 打码：断言列表值为 mask 形态且明文串不出现在响应体；若列表改回 mask=False → 明文出现 → 红。

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

# 假值常量（与真实居民无关）
ID_CARD = '330101199001011234'
ID_CARD_2 = '330101199202021234'
PHONE = '13812345678'
NAME = '张三'
MASKED_ID_CARD = '330101********1234'
MASKED_PHONE = '138****5678'


# ══════════════════════════ 夹具 ══════════════════════════

@pytest.fixture()
def app(isolated_app_factory):
    """隔离 DB 的 app（conftest.isolated_app_factory 统一覆写 SHUCUN_* + Config）。"""
    application = isolated_app_factory()
    import config as cfg
    application.config['TEST_DB_PATH'] = str(cfg.DB_PATH)
    return application


@pytest.fixture(autouse=True)
def _clean_agg_cache():
    """081 指标缓存是进程级字典，跨 app 共用；每用例前后清空避免 total 串扰。"""
    from utils.agg_cache import invalidate
    invalidate()
    yield
    invalidate()


def _unlock_keyholder():
    """R12 字段级加密需先解锁密钥（align test_094 先例）。"""
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())


def _client_for(app, user_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
    return client


def _admin_client(app):
    """admin（role_id=1）全权限；create_app 首启已种子 admin。"""
    return _client_for(app, 1)


def _role_client(app, perms):
    """建无 welfare 位的角色 + 用户，返回已登录 client（权限反例用）。"""
    from models import db, Role, User
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        role = Role(name=f'084角色-{suffix}',
                    permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.commit()
        user = User(username=f'084用户-{suffix}', password_hash='x', role_id=role.id)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    return _client_for(app, uid)


# ══════════════════════════ 载荷与请求 ══════════════════════════

def _emp_payload(**overrides):
    data = {
        'tab': 'employment',
        'name': NAME,
        'id_card': ID_CARD,
        'gender': '男',
        'village_group': '一组',
        'phone': PHONE,
        'employment_status': '已就业',
        'employment_type': '单位就业',
        'employer': '三合村种植合作社',
        'industry': '农林牧渔',
        'work_location': '本地',
        'start_date': '2026-01-01',
        'update_date': '2026-03-01',
        'remark': '假值测试备注',
    }
    data.update(overrides)
    return data


def _sub_payload(**overrides):
    data = {
        'tab': 'subsidy',
        'name': NAME,
        'id_card': ID_CARD,
        'apply_year': '2026',
        'batch_no': 'PC2026-01',
        'subsidy_type': '社保补贴',
        'amount': '1200.50',
        'apply_date': '2026-01-05',
        'remark': '假值测试备注',
    }
    data.update(overrides)
    return data


def _save_emp(client, **overrides):
    return client.post('/welfare/employment/api/save', data=_emp_payload(**overrides))


def _save_sub(client, **overrides):
    return client.post('/welfare/employment/api/save', data=_sub_payload(**overrides))


def _list(client, tab='employment', **params):
    query = {'tab': tab, 'limit': 100}
    query.update(params)
    return client.get('/welfare/employment/api/list', query_string=query).get_json()


def _transition(client, item_id, to_status, tab='subsidy'):
    return client.post('/welfare/employment/api/transition',
                       data={'tab': tab, 'id': item_id, 'to_status': to_status})


def _rows(app, table):
    """直读隔离库行数与关键列（绕开 ORM 会话，验证「库内仍 N 行」）。"""
    conn = sqlite3.connect(app.config['TEST_DB_PATH'])
    try:
        return conn.execute(f'SELECT * FROM {table}').fetchall()
    finally:
        conn.close()


def _status_of(app, table, item_id):
    conn = sqlite3.connect(app.config['TEST_DB_PATH'])
    try:
        cur = conn.execute(f'SELECT apply_status FROM {table} WHERE id = ?', (item_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ══════════ 1) 页面 / 登录闸门 ══════════

def test_page_renders_two_tabs_and_requires_login(app):
    """页面 200 且含双 tab 文案；未登录 302 跳登录、API 401。"""
    anonymous = app.test_client()
    resp = anonymous.get('/welfare/employment/')
    assert resp.status_code == 302
    assert 'login' in (resp.headers.get('Location') or '').lower()

    # API 未登录：XHR/JSON 通道返回 401（不是 200 成功）
    resp = anonymous.get('/welfare/employment/api/list',
                         headers={'X-Requested-With': 'XMLHttpRequest'})
    assert resp.status_code == 401
    assert resp.get_json()['success'] is False

    _unlock_keyholder()
    html = _admin_client(app).get('/welfare/employment/').get_data(as_text=True)
    assert '就业状态登记' in html, '缺少就业登记 tab 文案'
    assert '灵活就业补贴申报' in html, '缺少补贴申报 tab 文案'
    assert 'id="employmentTabs"' in html


# ══════════ 2) employment CRUD + 筛选 ══════════

def test_employment_crud_and_status_filter(app):
    """新增 → 列表回显 → 按 employment_status 筛选 → 详情 → 删除。"""
    _unlock_keyholder()
    client = _admin_client(app)

    assert _save_emp(client).get_json()['success'] is True

    listing = _list(client)
    assert listing['success'] is True
    assert listing.get('total') == 1
    row = listing['data'][0]
    assert row['name'] == NAME
    assert row['employment_status'] == '已就业'
    assert row['employment_type'] == '单位就业'
    assert row['village_group'] == '一组'
    assert row['start_date'] == '2026-01-01'
    assert row['update_date'] == '2026-03-01'
    item_id = row['id']

    # 命中筛选
    assert _list(client, employment_status='已就业').get('total') == 1
    # 未命中筛选（筛选条件必须真生效，不能恒返回全量）
    assert _list(client, employment_status='未就业').get('total', 0) == 0
    assert _list(client, village_group='二组').get('total', 0) == 0

    # 详情：admin 有 view_sensitive → 解密回填明文（同时验证 R12 加解密闭环）
    detail = client.get(f'/welfare/employment/api/detail/{item_id}?tab=employment').get_json()
    assert detail['success'] is True
    assert detail['data']['id_card'] == ID_CARD
    assert detail['data']['phone'] == PHONE

    assert client.post(f'/welfare/employment/api/delete/{item_id}',
                       data={'tab': 'employment'}).get_json()['success'] is True
    assert _list(client).get('total', 0) == 0
    assert _rows(app, 'welfare_employment') == []


# ══════════ 3) 列表打码 ══════════

def test_list_masks_id_card_and_phone(app):
    """列表身份证前 6 后 4、电话前 3 后 4；明文串不得出现在响应体。

    反例判据：若 _employment_to_dict(item) 被改成 mask=False（或路由层裸返回明文），
    响应体将出现 330101199001011234 / 13812345678 → 本条红。
    """
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_emp(client).get_json()['success'] is True

    resp = client.get('/welfare/employment/api/list', query_string={'tab': 'employment'})
    body = resp.get_data(as_text=True)
    row = resp.get_json()['data'][0]
    assert row['id_card'] == MASKED_ID_CARD, f'身份证打码形态错误：{row["id_card"]}'
    assert row['phone'] == MASKED_PHONE, f'电话打码形态错误：{row["phone"]}'
    assert ID_CARD not in body, '列表响应泄露身份证明文'
    assert PHONE not in body, '列表响应泄露电话明文'

    # 补贴申报 tab 同样只回打码值
    assert _save_sub(client).get_json()['success'] is True
    sub_body = client.get('/welfare/employment/api/list',
                          query_string={'tab': 'subsidy'}).get_data(as_text=True)
    assert ID_CARD not in sub_body, '补贴列表响应泄露身份证明文'
    assert MASKED_ID_CARD in sub_body


# ══════════ 4) 身份证唯一约束（反例） ══════════

def test_duplicate_employment_id_card_rejected(app):
    """同身份证重复登记：success=False + 提示含「已存在」+ 库内仍 1 行。

    反例判据：若 welfare_employment.id_card_hash 的 unique 索引或 IntegrityError
    分支被移除 → 第二次保存 200/true 且 COUNT==2 → 本条红（半修复态即失败）。
    """
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_emp(client).get_json()['success'] is True

    dup = _save_emp(client, name='李四').get_json()
    assert dup['success'] is False, '重复身份证不得保存成功'
    assert '已存在' in dup['msg'], f'重复提示文案不符：{dup["msg"]}'
    assert len(_rows(app, 'welfare_employment')) == 1, '重复登记写入了第二行'


# ══════════ 5) 补贴申报 (人, 年度, 类型) 唯一约束（反例） ══════════

def test_duplicate_subsidy_triplet_rejected(app):
    """同 (身份证, 年度, 补贴类型) 重复失败且库内 1 行；换类型应可保存（约束不过宽）。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_sub(client).get_json()['success'] is True

    dup = _save_sub(client, name='李四').get_json()
    assert dup['success'] is False, '同人同年度同类型重复申报不得保存成功'
    assert len(_rows(app, 'welfare_flex_subsidy')) == 1, '重复申报写入了第二行'

    # 换补贴类型 → 复合唯一键允许（证明约束是三元组而非单列）
    assert _save_sub(client, subsidy_type='岗位补贴').get_json()['success'] is True
    assert len(_rows(app, 'welfare_flex_subsidy')) == 2


# ══════════ 6) 状态机 ══════════

def test_subsidy_transition_state_machine(app):
    """待申报→已申报→已审核→已发放；非法迁移 HTTP 409；已发放需 issue_date。

    反例判据：若 SUBSIDY_TRANSITIONS 校验被移除（任意迁移放行）→ 待申报→已发放
    返回 200 而不是 409 → 本条红。
    """
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_sub(client).get_json()['success'] is True
    item_id = _list(client, tab='subsidy')['data'][0]['id']
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '待申报'

    # 非法：跨级前进 → 409 且状态不变
    resp = _transition(client, item_id, '已发放')
    assert resp.status_code == 409, f'非法迁移应 409，实际 {resp.status_code}'
    assert resp.get_json()['success'] is False
    assert '非法状态迁移' in resp.get_json()['msg']
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '待申报', '409 后状态被改动'

    # 合法链：待申报 → 已申报 → 已审核
    assert _transition(client, item_id, '已申报').get_json()['success'] is True
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '已申报'
    assert _transition(client, item_id, '已审核').get_json()['success'] is True
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '已审核'

    # 已发放缺 issue_date → 业务失败（非 409，不改状态）
    no_date = _transition(client, item_id, '已发放').get_json()
    assert no_date['success'] is False
    assert '发放日期' in no_date['msg'], f'缺发放日期提示不符：{no_date["msg"]}'
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '已审核'

    # 补 issue_date（保存不改状态）→ 再迁移 true
    assert _save_sub(client, id=item_id, apply_date='2026-01-05',
                     review_date='2026-01-20', issue_date='2026-02-10',
                     subsidy_type='社保补贴', apply_year='2026').get_json()['success'] is True
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '已审核', '保存不应改动状态'
    assert _transition(client, item_id, '已发放').get_json()['success'] is True
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '已发放'

    # 回退非法：已发放 → 已申报 → 409
    back = _transition(client, item_id, '已申报')
    assert back.status_code == 409
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '已发放'


def test_subsidy_reject_requires_reason(app):
    """任意态 → 未通过；缺 reject_reason 时失败，补原因后成功。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_sub(client).get_json()['success'] is True
    item_id = _list(client, tab='subsidy')['data'][0]['id']

    no_reason = _transition(client, item_id, '未通过').get_json()
    assert no_reason['success'] is False
    assert '原因' in no_reason['msg'], f'缺未通过原因提示不符：{no_reason["msg"]}'
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '待申报'

    assert _save_sub(client, id=item_id, reject_reason='材料不齐').get_json()['success'] is True
    assert _transition(client, item_id, '未通过').get_json()['success'] is True
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '未通过'


def test_employment_tab_has_no_transition(app):
    """状态迁移仅补贴申报支持：employment tab 调 transition 必须失败。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_emp(client).get_json()['success'] is True
    item_id = _list(client)['data'][0]['id']
    resp = _transition(client, item_id, '已申报', tab='employment').get_json()
    assert resp['success'] is False
    assert '补贴' in resp['msg']


# ══════════ 7) 非法枚举值（不写库） ══════════

@pytest.mark.parametrize('overrides,msg_fragment', [
    ({'employment_status': '乱填'}, '就业状态'),
    ({'employment_type': '乱填'}, '就业类型'),
    ({'industry': '乱填'}, '所属行业'),
])
def test_invalid_employment_enum_rejected(app, overrides, msg_fragment):
    """就业侧字段必须是字典取值；非法值失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    resp = _save_emp(client, **overrides).get_json()
    assert resp['success'] is False
    assert '不合法' in resp['msg'], f'非法枚举提示不符：{resp["msg"]}'
    assert msg_fragment in resp['msg']
    assert _rows(app, 'welfare_employment') == [], '非法枚举值不得写库'


def test_invalid_subsidy_type_rejected(app):
    """补贴类型必须是字典取值；非法值失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    resp = _save_sub(client, subsidy_type='乱填').get_json()
    assert resp['success'] is False
    assert '不合法' in resp['msg'], f'非法补贴类型提示不符：{resp["msg"]}'
    assert _rows(app, 'welfare_flex_subsidy') == []


# ══════════ 8) 日期 / 年度校验 ══════════

def test_subsidy_issue_date_before_apply_date_rejected(app):
    """发放日期早于申报日期 → 失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    resp = _save_sub(client, apply_date='2026-05-01', issue_date='2026-04-01').get_json()
    assert resp['success'] is False
    assert '发放日期不得早于申报日期' in resp['msg'], f'日期先后提示不符：{resp["msg"]}'
    assert _rows(app, 'welfare_flex_subsidy') == []


@pytest.mark.parametrize('year', ['1800', '2200', 'abc'])
def test_subsidy_apply_year_out_of_range_rejected(app, year):
    """申报年度越界/非整数 → 失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    resp = _save_sub(client, apply_year=year).get_json()
    assert resp['success'] is False
    assert _rows(app, 'welfare_flex_subsidy') == []


def test_employment_bad_date_format_rejected(app):
    """非 YYYY-MM-DD 日期 → 失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    resp = _save_emp(client, start_date='2026/01/01').get_json()
    assert resp['success'] is False
    assert 'YYYY-MM-DD' in resp['msg'], f'日期格式提示不符：{resp["msg"]}'
    assert _rows(app, 'welfare_employment') == []


# ══════════ 8b) R-084-01 身份证必填（唯一键前置条件） ══════════

def test_id_card_required_for_both_ledgers(app):
    """身份证为空/格式非法 → 失败且不写库（R-084-01）。

    背景：唯一键是 id_card_hash，SQLite 唯一索引对 NULL 不去重 → 空证件会让防重形同虚设。
    反例判据：若 _normalize_id_card 的必填分支被移除 → 空证件保存成功且 hash 为 NULL
    → 本条红（且后续同证件可无限重复登记）。
    """
    _unlock_keyholder()
    client = _admin_client(app)

    for saver, table in ((_save_emp, 'welfare_employment'),
                         (_save_sub, 'welfare_flex_subsidy')):
        empty = saver(client, id_card='').get_json()
        assert empty['success'] is False, f'{table} 允许空身份证保存'
        assert '身份证号不能为空' in empty['msg'], f'提示文案不符：{empty["msg"]}'

        malformed = saver(client, id_card='1234').get_json()
        assert malformed['success'] is False
        assert '身份证号格式不正确' in malformed['msg'], f'提示文案不符：{malformed["msg"]}'
        assert _rows(app, table) == [], f'{table} 非法证件号写入了库'


# ══════════ 8c) R-084-02/03 状态机唯一入口（反例） ══════════

def test_save_cannot_bypass_state_machine(app):
    """save 接口对 apply_status 只读校验、不写状态位（R-084-02）。

    反例判据：若 _save_subsidy 恢复 `item.apply_status = apply_status`，
    本条会看到状态直接变 已发放（且无 issue_date）→ 红。
    """
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_sub(client).get_json()['success'] is True
    item_id = _list(client, tab='subsidy')['data'][0]['id']

    # 合法取值允许保存（不报错），但状态位不得改变
    ok = _save_sub(client, id=item_id, apply_status='已发放').get_json()
    assert ok['success'] is True, f'合法状态值不应报错：{ok}'
    row = _list(client, tab='subsidy')['data'][0]
    assert row['apply_status'] == '待申报', f'保存接口绕过了状态机：{row["apply_status"]}'
    assert row['issue_date'] == '', '保存接口写入了发放日期'

    # 非法状态值仍失败
    bad = _save_sub(client, id=item_id, apply_status='乱填').get_json()
    assert bad['success'] is False
    assert '不合法' in bad['msg']
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '待申报'


def test_transition_carries_issue_date_atomically(app):
    """transition 可随迁 issue_date（R-084-03）；日期先后仍校验，失败不落库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_sub(client).get_json()['success'] is True
    item_id = _list(client, tab='subsidy')['data'][0]['id']
    assert _save_sub(client, id=item_id, review_date='2026-01-20').get_json()['success'] is True
    assert _transition(client, item_id, '已申报').get_json()['success'] is True
    assert _transition(client, item_id, '已审核').get_json()['success'] is True

    # 随迁日期早于审核日期 → 失败，状态与 issue_date 均不变（原子性）
    early = client.post('/welfare/employment/api/transition', data={
        'tab': 'subsidy', 'id': item_id, 'to_status': '已发放',
        'issue_date': '2026-01-01'}).get_json()
    assert early['success'] is False
    assert '发放日期不得早于审核日期' in early['msg'], f'提示文案不符：{early["msg"]}'
    assert _status_of(app, 'welfare_flex_subsidy', item_id) == '已审核'
    assert _list(client, tab='subsidy')['data'][0]['issue_date'] == '', '失败迁移落入了日期'

    # 随迁日期合法 → 一次提交完成状态 + 日期落库
    ok = client.post('/welfare/employment/api/transition', data={
        'tab': 'subsidy', 'id': item_id, 'to_status': '已发放',
        'issue_date': '2026-02-10'}).get_json()
    assert ok['success'] is True, f'随迁发放日期迁移失败：{ok}'
    row = _list(client, tab='subsidy')['data'][0]
    assert row['apply_status'] == '已发放'
    assert row['issue_date'] == '2026-02-10'


# ══════════ 9) 导出 xlsx ══════════

def test_export_returns_masked_xlsx(app):
    """导出 200 + spreadsheetml；工作表内身份证/电话为打码值（服务端通道同口径）。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_emp(client).get_json()['success'] is True
    assert _save_sub(client).get_json()['success'] is True

    for tab in ('employment', 'subsidy'):
        resp = client.get('/welfare/employment/api/export', query_string={'tab': tab})
        assert resp.status_code == 200, f'{tab} 导出状态 {resp.status_code}'
        assert 'spreadsheetml' in (resp.content_type or ''), \
            f'{tab} 导出 content-type 非 xlsx：{resp.content_type}'

        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(resp.data))
        ws = wb.active
        values = [str(cell.value) for row in ws.iter_rows() for cell in row if cell.value]
        assert any(MASKED_ID_CARD == v for v in values), f'{tab} 导出未打码身份证：{values[:20]}'
        assert ID_CARD not in values, f'{tab} 导出泄露身份证明文'
    # 补贴导出也带打码身份证
    resp = client.get('/welfare/employment/api/export', query_string={'tab': 'subsidy'})
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    sub_values = [str(cell.value) for row in wb.active.iter_rows() for cell in row if cell.value]
    assert ID_CARD not in sub_values


def test_export_invalid_tab_falls_back_to_employment(app):
    """导出 tab 归一：非法 tab 回落 employment（读接口容错），仍返回 200 xlsx。"""
    _unlock_keyholder()
    client = _admin_client(app)
    resp = client.get('/welfare/employment/api/export', query_string={'tab': '乱填'})
    assert resp.status_code == 200
    assert 'spreadsheetml' in (resp.content_type or '')


# ══════════ 10) 权限 ══════════

def test_api_denied_for_role_without_welfare_permission(app):
    """无 welfare 位的角色：页面 403、API 403（断言不是「200 且 success=true」）。"""
    _unlock_keyholder()
    client = _role_client(app, {'resident': {'view': True}})  # 有登录态但无 welfare

    page = client.get('/welfare/employment/')
    assert page.status_code == 403, f'无 welfare 位页面应 403，实际 {page.status_code}'

    for path in ('/welfare/employment/api/list', '/welfare/employment/api/options',
                 '/welfare/employment/api/export'):
        resp = client.get(path, headers={'X-Requested-With': 'XMLHttpRequest'})
        assert resp.status_code == 403, f'{path} 无 welfare 位应 403，实际 {resp.status_code}'
        body = resp.get_json() or {}
        assert body.get('success') is not True, f'{path} 未拦截：{body}'

    # 写接口同样被拦（POST save 归 welfare.add）
    save = client.post('/welfare/employment/api/save', data=_emp_payload(),
                       headers={'X-Requested-With': 'XMLHttpRequest'})
    assert save.status_code == 403, f'save 无 welfare.add 应 403，实际 {save.status_code}'
    assert (save.get_json() or {}).get('success') is not True
    assert _rows(app, 'welfare_employment') == [], '越权请求写入了数据'


def test_employment_data_isolated_between_tabs(app):
    """双台账物理隔离：employment 行不进 subsidy 列表，反之亦然。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_emp(client).get_json()['success'] is True
    assert _save_sub(client).get_json()['success'] is True
    assert _list(client, tab='employment').get('total') == 1
    assert _list(client, tab='subsidy').get('total') == 1
    emp_row = _list(client, tab='employment')['data'][0]
    assert 'employment_status' in emp_row and 'subsidy_type' not in emp_row
    sub_row = _list(client, tab='subsidy')['data'][0]
    assert 'subsidy_type' in sub_row and 'employment_status' not in sub_row


# ══════════ 11) 指标 ══════════

def test_metrics_total_matches_db_rows(app):
    """MetricsEngine.query total 与库内行数一致（081 C-3 取数口径）。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save_emp(client).get_json()['success'] is True
    assert _save_emp(client, id_card=ID_CARD_2, phone='13812345679',
                     employment_status='未就业', name='李四').get_json()['success'] is True
    assert _save_sub(client).get_json()['success'] is True

    assert len(_rows(app, 'welfare_employment')) == 2
    assert len(_rows(app, 'welfare_flex_subsidy')) == 1

    from utils.metrics import MetricsEngine
    with app.app_context():
        emp = MetricsEngine.query('welfare_employment')
        sub = MetricsEngine.query('welfare_flex_subsidy')

    assert emp['total'] == 2, f'welfare_employment.total={emp["total"]} 与库内 2 行不一致'
    assert sub['total'] == 1, f'welfare_flex_subsidy.total={sub["total"]} 与库内 1 行不一致'
    # 分组口径：默认维度 employment_status
    buckets = {b['key']: b['count'] for b in emp['buckets']}
    assert buckets.get('已就业') == 1 and buckets.get('未就业') == 1, buckets


# ══════════ 11b) R-084-05 金额合计指标 ══════════

def test_metrics_subsidy_amount_decimal_and_filters(app):
    """R-084-05：金额合计走 Decimal 应用层累加；空表 0.00、多类型求和、年度/状态筛选。"""
    _unlock_keyholder()
    client = _admin_client(app)

    from utils.metrics import MetricsEngine
    with app.app_context():
        empty = MetricsEngine.query('welfare_flex_subsidy_amount')
    assert empty['total'] == 0
    assert empty['data']['total_amount'] == '0.00', empty['data']

    assert _save_sub(client, amount='1200.50').get_json()['success'] is True
    assert _save_sub(client, id_card=ID_CARD_2, subsidy_type='岗位补贴',
                     amount='800.25').get_json()['success'] is True
    assert _save_sub(client, apply_year='2025', subsidy_type='一次性补贴',
                     amount='300.00').get_json()['success'] is True

    from utils.agg_cache import invalidate
    invalidate()
    with app.app_context():
        total = MetricsEngine.query('welfare_flex_subsidy_amount',
                                    filters={'apply_year': '2026'})
        by_type = MetricsEngine.query('welfare_flex_subsidy_amount',
                                      filters={'apply_year': '2026',
                                               'subsidy_type': '社保补贴'})
        year_2025 = MetricsEngine.query('welfare_flex_subsidy_amount',
                                        filters={'apply_year': '2025'})

    assert total['total'] == 2, total
    assert total['data']['total_amount'] == '2000.75', total['data']
    assert by_type['total'] == 1 and by_type['data']['total_amount'] == '1200.50', by_type
    assert year_2025['total'] == 1 and year_2025['data']['total_amount'] == '300.00', year_2025
    amounts = {b['key']: b['amount'] for b in total['buckets']}
    assert amounts.get('2026') == '2000.75', amounts

    # 状态筛选：转一条到「已申报」后，待申报金额只剩 800.25
    pid = [row['id'] for row in _list(client, tab='subsidy')['data']
           if row['subsidy_type'] == '社保补贴'][0]
    assert _transition(client, pid, '已申报').get_json()['success'] is True
    invalidate()
    with app.app_context():
        pending = MetricsEngine.query('welfare_flex_subsidy_amount',
                                      filters={'apply_year': '2026',
                                               'apply_status': '待申报'})
    assert pending['total'] == 1 and pending['data']['total_amount'] == '800.25', pending


# ══════════ 11c) R-084-13 公益岗零变化 + 错误信息安全 ══════════

def test_welfare_jobs_original_ledger_unchanged(app):
    """R-084-13①：公益岗表列集合、行数、原页与原导出逐项不变。"""
    _unlock_keyholder()
    client = _admin_client(app)
    expected_columns = {
        'id', 'name', 'village_group', 'id_card', 'id_card_enc', 'id_card_hash',
        'phone', 'phone_enc', 'phone_hash', 'job_type', 'start_date', 'end_date',
        'monthly_amount', 'bank_info', 'bank_info_enc', 'bank_info_hash',
        'bank_type', 'created_at', 'updated_at',
    }
    conn = sqlite3.connect(app.config['TEST_DB_PATH'])
    try:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(welfare_jobs)')}
        rows = conn.execute('SELECT * FROM welfare_jobs').fetchall()
    finally:
        conn.close()
    assert columns == expected_columns, f'公益岗列集合漂移：{columns ^ expected_columns}'
    assert rows == [], '公益岗原表被写入测试数据'

    assert client.get('/welfare/').status_code == 200
    export = client.get('/welfare/api/export')
    assert export.status_code == 200
    assert 'spreadsheetml' in (export.content_type or '')


def test_save_error_hides_internal_exception(app, monkeypatch):
    """R-084-13②：保存异常只回通用提示，内部异常文本不得回显。"""
    _unlock_keyholder()
    client = _admin_client(app)
    from routes import welfare_job as wj

    def _boom(item_id):
        raise RuntimeError('DB_SECRET_INTERNAL')

    monkeypatch.setattr(wj, '_save_employment', _boom)
    resp = _save_emp(client).get_json()
    assert resp['success'] is False
    assert resp['msg'] == '保存失败，请稍后重试或联系管理员', resp
    assert 'DB_SECRET_INTERNAL' not in resp['msg']
