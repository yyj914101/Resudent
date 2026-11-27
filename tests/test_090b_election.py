# -*- coding: utf-8 -*-
"""tests/test_090b_election.py — 090B 公共事务·选举（届次状态机 + 选民名册 + 结果，m035）

覆盖（docs/tasks/090B-public-election.md 验收标准 + 一页纸 v1.2 校验 V-01~V-06 中可自动化部分）：
A. 权限三处同步（验收 4）：BLUEPRINT_MODULES / role_mgmt 面板 / nav_spec；非超管授权
   election 红→绿；无权限 403；未登录 302（页面）/401（JSON API）
B. m035 幂等（验收 5）：空库首跑三表 + 唯一索引 + 字典、二跑零变更；create_all 库重放数据无损
C. 届次状态机（验收 1，V-01）：非法迁移 409 且不落库；S-01~S-04 前置守卫逐边验证；
   状态不经 edit 接口变更（防绕过）
D. 登记期守卫（V-02）+ 选民校验（V-03/V-04）：非「选民登记」状态增删改选民 409；
   同届次同人唯一 409；委托投票须受托人 400；身份证 18 位/状态取字典 400
E. 对账（验收 3，M-01/M-02/M-03）：四值互斥计数、登记数、得票合计
F. 导出（一页纸 §8）：token 通道；身份证打码（前 6 后 4）；委托受托人可导出（V-04）
G. 附件复用 092（module_key='election'）：上传→列表→下载→删除全链路；跨模块 403
H. 页面：届次列表页含附件区/查询；名册页含状态推进/对账条/名册/结果区
"""
import importlib.util
import io
import json
import os
from datetime import date, timedelta

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_M035_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm035_election.py')

ID_A = '110101199001011234'
ID_B = '110101199001011235'


# ------------------------------------------------------------------ 夹具/工具

def _load_m035():
    """importlib 加载迁移脚本（scripts/ 非包；test_099 同款方式）。"""
    spec = importlib.util.spec_from_file_location('m035_election_under_test', _M035_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('election-test', salt=generate_salt())
    return application


def _login(client, user_id, username):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _make_role_user(app, username, permissions):
    """建角色 + 用户。返回 user.id。"""
    with app.app_context():
        from models import Role, User, db
        role = Role(name=('role_%s' % username), description='090B 用例',
                    permissions=json.dumps(permissions))
        db.session.add(role)
        db.session.commit()
        user = User(username=username, role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        return user.id


PERM_ALL = {'election.view': True, 'election.add': True, 'election.export': True,
            'election.edit': True, 'election.delete': True}
PERM_DISABLED_ONLY = {'disabled.view': True, 'disabled.add': True, 'disabled.delete': True}


def _date_str(d):
    return d.strftime('%Y-%m-%d')


def _round_form(name='第十二届换届选举', register_end=None, register_start=None):
    today = date.today()
    return {
        'round_name': name,
        'start_date': _date_str(today + timedelta(days=30)),
        'end_date': _date_str(today + timedelta(days=365 * 3)),
        'register_start': _date_str(register_start or (today - timedelta(days=7))),
        'register_end': _date_str(register_end or (today + timedelta(days=7))),
        'vote_date': _date_str(today + timedelta(days=14)),
        'remark': '',
    }


def _create_round(client, name='第十二届换届选举', register_end=None):
    form = _round_form(name=name, register_end=register_end)
    resp = client.post('/election/api/rounds/add', data=form)
    body = resp.get_json()
    assert body['success'] is True, body
    return body['data']['id']


def _add_voter(client, rid, name='张三', id_card=ID_A, register_status='已登记',
               proxy_name='', phone='', group_name='一组', is_out=False):
    return client.post('/election/api/rounds/%d/voters/add' % rid, data={
        'name': name, 'id_card': id_card, 'group_name': group_name,
        'register_status': register_status, 'proxy_name': proxy_name,
        'phone': phone, 'is_out': '1' if is_out else '0', 'mobile_box': '0', 'remark': '',
    })


def _add_result(client, rid, position='村委会主任', winner='李四', votes=100,
                is_elected=True, notice_date=None):
    return client.post('/election/api/rounds/%d/results/add' % rid, data={
        'position': position, 'winner_name': winner, 'votes': str(votes),
        'is_elected': '1' if is_elected else '0',
        'notice_date': _date_str(notice_date) if notice_date else '',
        'winner_id_card': '', 'remark': '',
    })


def _transition(client, rid, to_status):
    return client.post('/election/api/rounds/%d/transition' % rid, data={'to_status': to_status})


# ------------------------------------------------------------------ A. 权限三处同步

def test_a_permission_three_places_and_grant_flow(app):
    # ① BLUEPRINT_MODULES 模块位
    from utils.permissions import BLUEPRINT_MODULES
    assert BLUEPRINT_MODULES.get('election') == 'election'

    # ② role_mgmt 权限面板（前端中文名登记）
    js = open(os.path.join(_ROOT, 'static', 'js', 'pages', 'role_mgmt_list.js'),
              encoding='utf-8').read()
    assert "'election'" in js and '选举' in js

    # ③ nav_spec 侧栏真源（102：党建工作组，一页纸 §9 机构管理下）
    nav = open(os.path.join(_ROOT, 'utils', 'nav_spec.py'), encoding='utf-8').read()
    assert "'endpoint': 'election.index'" in nav
    assert nav.index('organization.list') < nav.index('election.index')

    # 红→绿：无权限 403 → 授权 election.view 后 200
    uid = _make_role_user(app, 'ops_elec', {})
    client = app.test_client()
    _login(client, uid, 'ops_elec')
    assert client.get('/election/').status_code == 403
    assert client.get('/election/api/rounds').status_code == 403

    uid2 = _make_role_user(app, 'ops_elec2', PERM_ALL)
    client = app.test_client()
    _login(client, uid2, 'ops_elec2')
    assert client.get('/election/').status_code == 200
    assert client.get('/election/api/rounds').get_json()['success'] is True

    # 未登录：页面 302 去登录；JSON API 401
    anon = app.test_client()
    resp = anon.get('/election/')
    assert resp.status_code == 302
    resp = anon.get('/election/api/rounds')
    assert resp.status_code == 401


# ------------------------------------------------------------------ B. m035 幂等

def test_b_m035_idempotent_on_fresh_db(tmp_path):
    mod = _load_m035()
    db_path = str(tmp_path / 'fresh.db')

    # 模拟存量库：手工建最小字典骨架（迁移守卫要求 dict_categories/dict_items 在位）
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            "CREATE TABLE dict_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(50),"
            " description VARCHAR(200), is_active INTEGER, sort_order INTEGER, created_at DATETIME);"
            "CREATE TABLE dict_items (id INTEGER PRIMARY KEY AUTOINCREMENT, category_id INTEGER,"
            " label VARCHAR(50), value VARCHAR(50), sort_order INTEGER, is_active INTEGER, created_at DATETIME);")
        conn.commit()
    finally:
        conn.close()

    stats1 = mod.run(db_path)
    assert stats1['tables_created'] == ['election_result', 'election_round', 'election_voter']
    assert stats1['uq_election_voter_round_hash'] == 'created:uq_election_voter_round_hash'
    assert stats1['new_categories'] == ['选举届次状态', '选民登记状态', '当选职务']

    stats2 = mod.run(db_path)   # 重放：零变更
    assert stats2['tables_created'] == []
    assert stats2['new_categories'] == [] and stats2['new_items'] == 0
    assert stats2['uq_election_voter_round_hash'].startswith('reuse:')

    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE '%election%'")}
        assert 'uq_election_voter_round_hash' in idx
        assert {'idx_election_voter_round', 'idx_election_voter_hash',
                'idx_election_result_round'} <= idx
        cats = {r[0] for r in conn.execute(
            "SELECT name FROM dict_categories WHERE name IN ('选举届次状态','选民登记状态','当选职务')")}
        assert cats == {'选举届次状态', '选民登记状态', '当选职务'}
    finally:
        conn.close()


def test_b_m035_replay_on_create_all_db_preserves_data(app):
    from models import db, ElectionRound
    with app.app_context():
        db.session.add(ElectionRound(round_name='数据无损验证届', start_date=date(2026, 1, 1),
                                     register_start=date(2026, 1, 1), register_end=date(2026, 1, 31),
                                     vote_date=date(2026, 2, 1), status='筹备'))
        db.session.commit()
    before = None
    with app.app_context():
        from models import ElectionRound as R
        before = R.query.count()
    assert before == 1

    stats = _load_m035().run(os.environ['SHUCUN_DB_PATH'])
    assert stats['tables_created'] == []                    # create_all 已建，不重建
    assert stats['uq_election_voter_round_hash'].startswith('reuse:')
    with app.app_context():
        from models import ElectionRound as R
        assert R.query.count() == before                    # 数据无损


# ------------------------------------------------------------------ C. 届次状态机

def test_c_state_machine_guards_full_path(app):
    uid = _make_role_user(app, 'ops_sm', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_sm')

    # 新届次固定从「筹备」起步（§6 S-01 起点）
    rid = _create_round(client)
    with app.app_context():
        from models import ElectionRound
        assert ElectionRound.query.get(rid).status == '筹备'

    # V-01：非法迁移 409 且不落库（跨两步：筹备→投票）
    resp = _transition(client, rid, '投票')
    assert resp.status_code == 409
    body = resp.get_json()
    assert body['success'] is False and '非法状态迁移' in body['msg']
    with app.app_context():
        from models import ElectionRound
        assert ElectionRound.query.get(rid).status == '筹备'

    # S-02 前置：登记期未结束 → 409（当前筹备态先 S-01，再验证 S-02 守卫）
    assert _transition(client, rid, '选民登记').get_json()['success'] is True
    resp = _transition(client, rid, '投票')     # register_end = today+7，登记期未结束
    assert resp.status_code == 409 and '登记期尚未结束' in resp.get_json()['msg']

    # S-03 前置：未录入结果 → 409。先造一个登记期已结束的届次走完 S-02
    rid2 = _create_round(client, name='登记期已结束届', register_end=date.today() - timedelta(days=1))
    assert _transition(client, rid2, '选民登记').get_json()['success'] is True
    assert _transition(client, rid2, '投票').get_json()['success'] is True
    resp = _transition(client, rid2, '结果公示')
    assert resp.status_code == 409 and '尚未录入' in resp.get_json()['msg']

    # S-03：录入结果后可完成投票；S-04 前置：公示日期未齐 → 409；补齐后归档
    assert _add_result(client, rid2, notice_date=None).get_json()['success'] is True
    assert _transition(client, rid2, '结果公示').get_json()['success'] is True
    resp = _transition(client, rid2, '已完成')
    assert resp.status_code == 409 and '公示日期' in resp.get_json()['msg']
    with app.app_context():
        from models import ElectionResult, db
        e = ElectionResult.query.filter_by(round_id=rid2).first()
        e.notice_date = date.today()
        db.session.commit()
    resp = _transition(client, rid2, '已完成')
    assert resp.get_json()['success'] is True
    with app.app_context():
        from models import ElectionRound
        assert ElectionRound.query.get(rid2).status == '已完成'


def test_c_round_edit_does_not_touch_status(app):
    """防绕过：edit 接口不改状态（状态只经 /transition，governance 同款）。"""
    uid = _make_role_user(app, 'ops_edit', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_edit')
    rid = _create_round(client)
    form = _round_form(name='改名后的届次')
    form['id'] = rid
    assert client.post('/election/api/rounds/edit', data=form).get_json()['success'] is True
    with app.app_context():
        from models import ElectionRound
        r = ElectionRound.query.get(rid)
        assert r.round_name == '改名后的届次'
        assert r.status == '筹备'   # 状态未被 edit 触碰


def test_c_round_form_validation(app):
    uid = _make_role_user(app, 'ops_form', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_sm' if False else 'ops_form')

    # 必填缺失 / 日期先后约束（§2 R-003/R-005）
    bad = _round_form()
    bad.pop('vote_date')
    assert client.post('/election/api/rounds/add', data=bad).status_code == 400
    bad2 = _round_form()
    bad2['register_end'] = bad2['register_start']          # 登记期结束<开始 的边界：等于合法
    bad2['end_date'] = '2020-01-01'                        # 任期截止早于起始 → 400
    assert client.post('/election/api/rounds/add', data=bad2).status_code == 400
    bad3 = _round_form()
    bad3['register_end'] = '2020-01-01'                    # 登记期结束早于开始 → 400
    assert client.post('/election/api/rounds/add', data=bad3).status_code == 400


# ------------------------------------------------------------------ D. 登记期守卫 + 选民

def test_d_voter_guards_and_validations(app):
    uid = _make_role_user(app, 'ops_voter', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_voter')

    # 筹备态：不可登记选民（V-02 → 409）
    rid = _create_round(client)
    resp = _add_voter(client, rid)
    assert resp.status_code == 409 and '仅「选民登记」' in resp.get_json()['msg']

    # S-01 进入选民登记 → 可登记；委托投票须受托人（V-04 → 400）
    assert _transition(client, rid, '选民登记').get_json()['success'] is True
    resp = _add_voter(client, rid, register_status='委托投票', proxy_name='')
    assert resp.status_code == 400 and '受托人' in resp.get_json()['msg']
    resp = _add_voter(client, rid, register_status='不存在的状态')
    assert resp.status_code == 400 and '字典' in resp.get_json()['msg']
    resp = _add_voter(client, rid, id_card='11010119900101123')   # 17 位 → 400
    assert resp.status_code == 400 and '18 位' in resp.get_json()['msg']

    assert _add_voter(client, rid).get_json()['success'] is True
    assert _add_voter(client, rid, name='李四', id_card=ID_B,
                      register_status='委托投票', proxy_name='张三').get_json()['success'] is True

    # V-03：同届次同人唯一 → 409；同人不同届次 → 可登记
    resp = _add_voter(client, rid, id_card=ID_A)
    assert resp.status_code == 409 and '唯一' in resp.get_json()['msg']
    rid_other = _create_round(client, name='另一届')
    assert _transition(client, rid_other, '选民登记').get_json()['success'] is True
    assert _add_voter(client, rid_other, id_card=ID_A).get_json()['success'] is True

    # 加密管线落地：enc/hash 双列入库、resident_hash 派生（V-002 无明文列）
    with app.app_context():
        from models import ElectionVoter
        v = ElectionVoter.query.filter_by(round_id=rid, name='张三').first()
        assert v.id_card_enc and v.id_card_hash            # V-002 加密双列
        assert v.id_card_hash == v.resident_hash           # V-011：resident_hash 由 V-002 派生

    # S-02 结束登记后：增/删选民全部 409（登记期外名册冻结）
    rid_done = _create_round(client, name='登记期已结束届2', register_end=date.today() - timedelta(days=1))
    assert _transition(client, rid_done, '选民登记').get_json()['success'] is True
    assert _add_voter(client, rid_done, name='王五', id_card=ID_B).get_json()['success'] is True
    assert _transition(client, rid_done, '投票').get_json()['success'] is True
    assert _add_voter(client, rid_done, name='赵六', id_card=ID_A).status_code == 409
    with app.app_context():
        from models import ElectionVoter
        vid = ElectionVoter.query.filter_by(round_id=rid_done).first().id
    assert client.post('/election/api/voters/%d/delete' % vid).status_code == 409


def test_d_voter_edit_and_delete_in_window(app):
    uid = _make_role_user(app, 'ops_voter2', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_voter2')
    rid = _create_round(client)
    assert _transition(client, rid, '选民登记').get_json()['success'] is True
    assert _add_voter(client, rid, name='张三', id_card=ID_A,
                      register_status='未登记').get_json()['success'] is True

    # 编辑：改登记状态 + 补受托人；V-03 排除自身
    with app.app_context():
        from models import ElectionVoter
        vid = ElectionVoter.query.filter_by(round_id=rid).first().id
    resp = client.post('/election/api/voters/%d/edit' % vid, data={
        'name': '张三', 'id_card': ID_A, 'group_name': '二组',
        'register_status': '委托投票', 'proxy_name': '李四', 'phone': '13800001111',
        'is_out': '0', 'mobile_box': '1', 'remark': '改'})
    assert resp.get_json()['success'] is True, (resp.status_code, resp.get_data(as_text=True))
    with app.app_context():
        from models import ElectionVoter
        v = ElectionVoter.query.get(vid)
        assert v.register_status == '委托投票' and v.group_name == '二组' and v.mobile_box
    # 删除成功
    assert client.post('/election/api/voters/%d/delete' % vid).get_json()['success'] is True


# ------------------------------------------------------------------ E. 对账

def test_e_reconcile_counts_and_votes_sum(app):
    uid = _make_role_user(app, 'ops_rec', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_rec')
    rid = _create_round(client)
    assert _transition(client, rid, '选民登记').get_json()['success'] is True

    # 名册 5 人：已登记×2 + 未登记×1 + 委托投票×1 + 外出×1（四值互斥，M-03）
    assert _add_voter(client, rid, name='张三', id_card=ID_A, register_status='已登记').get_json()['success']
    assert _add_voter(client, rid, name='李四', id_card=ID_B, register_status='已登记').get_json()['success']
    assert _add_voter(client, rid, name='王五', id_card='110101199001011236', register_status='未登记').get_json()['success']
    assert _add_voter(client, rid, name='赵六', id_card='110101199001011237', register_status='委托投票', proxy_name='张三').get_json()['success']
    assert _add_voter(client, rid, name='孙七', id_card='110101199001011238', register_status='外出', is_out=True).get_json()['success']

    rc = client.get('/election/api/rounds/%d/reconcile' % rid).get_json()['data']
    assert rc['total'] == 5
    assert rc['registered'] == 2                     # M-01：登记数 = 已登记行数
    assert rc['by_status'] == {'已登记': 2, '未登记': 1, '委托投票': 1, '外出': 1}
    assert rc['four_way_sum'] == rc['total'] == 5    # M-03：四值互斥对账

    # M-02：得票合计 = Σ 结果行（先录 100 + 50）
    rid2 = _create_round(client, name='结果对账届')
    with app.app_context():
        from models import db, ElectionRound
        r = ElectionRound.query.get(rid2)
        r.status = '投票'    # 直接置状态仅为对账单测，结果录入窗口守卫由 D/C 组覆盖
        db.session.commit()
    assert _add_result(client, rid2, votes=100).get_json()['success']
    assert _add_result(client, rid2, position='村委会委员', winner='王五', votes=50).get_json()['success']
    rc2 = client.get('/election/api/rounds/%d/reconcile' % rid2).get_json()['data']
    assert rc2['votes_total'] == 150 and rc2['results_count'] == 2

    # 列表接口随数据下发对账块
    data = client.get('/election/api/rounds/%d/voters' % rid).get_json()['data']
    assert data['reconcile']['total'] == 5


# ------------------------------------------------------------------ F. 导出

def test_f_export_masked_with_proxy_column(app):
    # 导出授权口径（T17-4 既有机制）：发起导出需 election.export（action_for_request
    # 对 export 端点独立动作位）；/report/download/<token> 的 enforce 层需 report.export，
    # 路由纵深校验需 election.export（签发时绑定的模块）。
    uid = _make_role_user(app, 'ops_exp', dict(PERM_ALL, **{'report.export': True}))
    client = app.test_client()
    _login(client, uid, 'ops_exp')
    rid = _create_round(client, register_end=date.today() - timedelta(days=1))
    assert _transition(client, rid, '选民登记').get_json()['success'] is True
    assert _add_voter(client, rid, name='张三', id_card=ID_A,
                      register_status='委托投票', proxy_name='李四').get_json()['success']

    resp = client.get('/election/api/rounds/%d/voters/export' % rid)
    body = resp.get_json()
    assert body['success'] is True, (body, resp.status_code)
    assert body['data']['download_url'].startswith('/report/download/')

    # 拉取导出文件核对：身份证打码（前 6 后 4）、受托人在列（V-04）
    xlsx = client.get(body['data']['download_url'])
    assert xlsx.status_code == 200
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx.data))
    ws = wb.active
    rows = [[c.value for c in row] for row in ws.iter_rows()]
    assert rows[0] == ['姓名', '身份证（打码）', '组别', '登记状态', '是否外出', '受托人', '流动票箱']
    data_row = rows[1]
    assert ID_A not in [str(c) for c in data_row]              # 全文无明文身份证
    assert data_row[1] == ID_A[:6] + '*' * 8 + ID_A[-4:]       # 前 6 后 4 打码
    assert data_row[5] == '李四'                                # 委托受托人可导出


# ------------------------------------------------------------------ G. 附件复用 092

def test_g_election_attachments_full_chain_and_cross_module(app):
    uid_all = _make_role_user(app, 'ops_att_all', dict(PERM_ALL, **{
        'disabled.view': True, 'disabled.add': True, 'disabled.delete': True}))
    client = app.test_client()
    _login(client, uid_all, 'ops_att_all')

    # election 模块全链路（module_key='election'，service 收口）
    resp = client.post('/election/api/attachments/upload', data={
        'file': (io.BytesIO(b'%PDF-1.4 election'), '选举办法.pdf', 'application/pdf')},
        content_type='multipart/form-data')
    assert resp.get_json()['success'] is True
    att_id = resp.get_json()['data']['id']

    rows = client.get('/election/api/attachments').get_json()['data']['data']
    assert len(rows) == 1 and rows[0]['module_key'] == 'election' and rows[0]['original_name'] == '选举办法.pdf'

    dl = client.get('/election/api/attachments/%d/download' % att_id)
    assert dl.status_code == 200 and dl.data == b'%PDF-1.4 election'
    dl.close()

    # 跨模块：走 disabled 端点读 election 附件 → service 归属校验 403
    resp = client.get('/disabled/api/attachments/%d/download' % att_id)
    assert resp.status_code == 403
    resp = client.post('/disabled/api/attachments/%d/delete' % att_id)
    assert resp.status_code == 403

    assert client.post('/election/api/attachments/%d/delete' % att_id).get_json()['success'] is True


def test_g_election_module_bit_blocks_cross_module_user(app):
    """disabled 用户访问 election 端点：模块位 403（enforce 兜底）。"""
    uid = _make_role_user(app, 'ops_att_dis', PERM_DISABLED_ONLY)
    client = app.test_client()
    _login(client, uid, 'ops_att_dis')
    assert client.get('/election/api/attachments').status_code == 403


# ------------------------------------------------------------------ H. 页面

def test_h_pages_render_sections(app):
    uid = _make_role_user(app, 'ops_page', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_page')

    html = client.get('/election/').get_data(as_text=True)
    assert '届次列表' in html and '选举材料' in html and 'initAttachments(' in html and 'attFile' in html

    rid = _create_round(client)
    html = client.get('/election/rounds/%d/voters' % rid).get_data(as_text=True)
    assert '选民名册' in html and '当选结果' in html
    assert '名册总数' in html and '得票合计' in html           # §7 对账条
    assert '登记期守卫' in html
    assert 'roundStatus' in html and 'btnTransition' in html

    # 不存在的届次 → 404
    assert client.get('/election/rounds/99999/voters').status_code == 404
