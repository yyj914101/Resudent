# -*- coding: utf-8 -*-
"""tests/test_083b_party_development.py — 083B 党员发展回归（五阶段 + 状态机）。

覆盖：
- 五阶段名册 CRUD（新增固定「申请人」，编辑基本信息，阶段不在 save 修改）
- 状态机：仅前滚一格；跳级/回退/同态 409 且不落库；每次合法迁移留日志
- 目标阶段节点日期必填（缺则 400）；to_stage 非法值 400
- 转正回写：仅动 party_members 的 party_status/member_identity 两列（diff 断言其余
  维度不变）；无复核确认不回写；无在册档案跳过回写
- 对账：名册总行数 = 发展池（未转正阶段）计数 + 正式党员阶段计数；正式党员计数 = 回写日志计数
- 导出一人一行含五阶段日期（未到阶段留空）+ 敏感列打码
- 迁移 m041 双跑幂等；字典三源一致；m041.CATEGORIES 与 DICT_SEEDS 一致性
- 权限：未登录 401/302、无 party 模块位 403
"""
import importlib.util
import io
import json
import os
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STAGES = ['申请人', '积极分子', '发展对象', '预备党员', '正式党员']
ID_A = '110101199001011234'
ID_B = '110101199202022345'


# ══════════════════════════ 夹具 ══════════════════════════

@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    _unlock_keyholder()
    import config as cfg
    application.config['TEST_DB_PATH'] = str(cfg.DB_PATH)
    return application


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令083b', salt=generate_salt())


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
        role = Role(name=f'083B角色-{suffix}',
                    permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.commit()
        user = User(username=f'083B用户-{suffix}', password_hash='x', role_id=role.id)
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


def _make_member(app, name, id_card, **dims):
    """在册党员档案（转正回写的目标；080 四维显式赋值以验证 diff 断言）。"""
    from models import db, PartyMember
    from utils.crypto import sha256_hash
    with app.app_context():
        member = PartyMember(name=name, party_status='在册', branch='第一党支部',
                             member_identity='预备党员', member_mobility='在家',
                             employment_status='在职', member_type='历史值', **dims)
        member.id_card = id_card
        member.id_card_enc = 'enc-' + id_card
        member.id_card_hash = sha256_hash(id_card)
        db.session.add(member)
        db.session.commit()
        mid = member.id
    return mid


def _dev_payload(**overrides):
    data = {
        'name': '王小明',
        'id_card': ID_A,
        'phone': '13800000001',
        'gender': '男',
        'branch': '第一党支部',
        'apply_date': '2026-01-10',
        'introducer': '张书记',
        'remark': '',
    }
    data.update(overrides)
    return data


def _save_dev(client, **overrides):
    return client.post('/party/development/api/save', data=_dev_payload(**overrides))


def _transition(client, dev_id, to_stage, stage_date='2026-06-01', **extra):
    data = {'id': str(dev_id), 'to_stage': to_stage, 'stage_date': stage_date}
    data.update(extra)
    return client.post('/party/development/api/transition', data=data)


def _load_m041():
    path = os.path.join(BASE, 'scripts', 'migrations', 'm041_party_development.py')
    spec = importlib.util.spec_from_file_location('m041_party_development', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════ 字典 + 迁移 ══════════════════════════

class TestDictAndMigration:
    def test_seed_constant_and_closed_set(self):
        from utils.dict_seed import DICT_SEEDS
        assert DICT_SEEDS['党员发展阶段'] == STAGES
        # R-083A-P2-1 教训：闭集契约同步（test_080 EXPECTED_SEEDS 由该文件自证，此处双保险）
        import tests.test_080_a_enum_alignment as t080
        assert t080.EXPECTED_SEEDS['党员发展阶段'] == STAGES

    def test_m041_categories_match_dict_seeds(self):
        from utils.dict_seed import DICT_SEEDS
        mod = _load_m041()
        assert mod.CATEGORIES == {k: DICT_SEEDS[k] for k in mod.CATEGORIES}, \
            'm041.CATEGORIES 与 DICT_SEEDS 漂移'

    def test_app_db_has_stage_dict(self, app):
        from models import DictCategory, DictItem
        with app.app_context():
            cat = DictCategory.query.filter_by(name='党员发展阶段').first()
            assert cat is not None
            got = [i.value for i in DictItem.query.filter_by(
                category_id=cat.id).order_by(DictItem.sort_order).all()]
            assert got == STAGES
            from utils.dict_seed import ensure_enum_dicts
            assert ensure_enum_dicts() == (0, 0)

    def test_m041_idempotent_double_run(self, tmp_path):
        db_path = str(tmp_path / 'legacy.db')
        conn = sqlite3.connect(db_path)
        conn.execute(
            'CREATE TABLE dict_categories (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'name VARCHAR(50), description VARCHAR(200), is_active INTEGER, '
            'sort_order INTEGER, created_at DATETIME)')
        conn.execute(
            'CREATE TABLE dict_items (id INTEGER PRIMARY KEY AUTOINCREMENT, '
            'category_id INTEGER, label VARCHAR(100), value VARCHAR(100), '
            'sort_order INTEGER, is_active INTEGER, created_at DATETIME)')
        conn.commit()
        conn.close()

        mod = _load_m041()
        first = mod.run(db_path)
        assert first['new_categories'] == ['党员发展阶段']
        assert first['new_items'] == 5

        second = mod.run(db_path)
        assert second['new_categories'] == []
        assert second['new_items'] == 0
        assert second['skipped_categories'] == ['党员发展阶段']

        conn = sqlite3.connect(db_path)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {'party_development', 'party_development_log'} <= tables
            indexes = {r[1] for r in conn.execute('PRAGMA index_list(party_development)')}
            assert 'uq_party_development_id_card_hash' in indexes
        finally:
            conn.close()


# ══════════════════════════ 名册 CRUD ══════════════════════════

class TestCrud:
    def test_create_defaults_to_first_stage_and_edit(self, app):
        client = _admin_client(app)
        resp = _save_dev(client).get_json()
        assert resp['success'] is True, resp.get('msg')
        dev_id = resp['data']['id']

        detail = client.get(f'/party/development/api/detail/{dev_id}').get_json()
        assert detail['data']['stage'] == '申请人', '新增必须固定「申请人」'
        assert detail['data']['apply_date'] == '2026-01-10'

        resp = _save_dev(client, id=str(dev_id), name='王小明（改）',
                         remark='更新备注').get_json()
        assert resp['success'] is True
        detail = client.get(f'/party/development/api/detail/{dev_id}').get_json()
        assert detail['data']['name'] == '王小明（改）'
        assert detail['data']['stage'] == '申请人', 'save 不得修改阶段'

    def test_duplicate_id_card_rejected(self, app):
        client = _admin_client(app)
        assert _save_dev(client).get_json()['success'] is True
        resp = _save_dev(client, name='李四').get_json()
        assert resp['success'] is False
        assert '已存在发展记录' in resp['msg']
        assert _count(app, 'SELECT COUNT(*) FROM party_development') == 1

    def test_required_and_format_validation(self, app):
        client = _admin_client(app)
        resp = _save_dev(client, name='').get_json()
        assert resp['success'] is False
        resp = _save_dev(client, id_card='abc').get_json()
        assert resp['success'] is False and '格式' in resp['msg']
        resp = _save_dev(client, apply_date='2026/01/01').get_json()
        assert resp['success'] is False
        assert _count(app, 'SELECT COUNT(*) FROM party_development') == 0

    def test_list_filters_stage_branch_keyword(self, app):
        client = _admin_client(app)
        _save_dev(client, name='甲', branch='第一党支部')
        resp = _save_dev(client, name='乙', id_card=ID_B, branch='第二党支部').get_json()
        _transition(client, resp['data']['id'], '积极分子', '2026-03-01')

        cases = [
            ({'stage': '积极分子'}, {'乙'}),
            ({'stage': '申请人'}, {'甲'}),
            ({'branch': '第二党支部'}, {'乙'}),
            ({'keyword': '甲'}, {'甲'}),
        ]
        for params, expected in cases:
            got = {r['name'] for r in client.get(
                '/party/development/api/list', query_string=params).get_json()['data']}
            assert got == expected, f'{params} → {got}'


# ══════════════════════════ 状态机 ══════════════════════════

class TestStateMachine:
    def _walk_to(self, client, dev_id, upto_index):
        """从申请人一路合法前滚到 STAGES[upto_index]，返回最后响应。"""
        dates = ['2026-01-01', '2026-02-01', '2026-03-01', '2026-04-01', '2026-05-01']
        resp = None
        for i in range(1, upto_index + 1):
            resp = _transition(client, dev_id, STAGES[i], dates[i])
            assert resp.status_code == 200 and resp.get_json()['success'], \
                f'{STAGES[i - 1]}→{STAGES[i]} 应合法：{resp.get_data(as_text=True)}'
        return resp

    def test_forward_only_one_step_with_logs(self, app):
        """验收 2：合法迁移留日志；链路 申请人→…→正式党员。"""
        client = _admin_client(app)
        dev_id = _save_dev(client).get_json()['data']['id']
        self._walk_to(client, dev_id, 4)

        detail = client.get(f'/party/development/api/detail/{dev_id}').get_json()['data']
        assert detail['stage'] == '正式党员'
        assert detail['full_member_date'] == '2026-05-01'
        logs = detail['logs']
        assert [(l['from_stage'], l['to_stage']) for l in logs] == [
            ('申请人', '积极分子'), ('积极分子', '发展对象'),
            ('发展对象', '预备党员'), ('预备党员', '正式党员')]
        assert all(l['operator'] for l in logs), '每次迁移须留操作人'
        assert _count(app, 'SELECT COUNT(*) FROM party_development_log') == 4

    def test_illegal_moves_409_no_write(self, app):
        """跳级 / 回退 / 同态重复 → HTTP 409 且不落库（日志也不加行）。"""
        client = _admin_client(app)
        dev_id = _save_dev(client).get_json()['data']['id']

        # 跳级：申请人 → 发展对象
        resp = _transition(client, dev_id, '发展对象')
        assert resp.status_code == 409
        assert '非法阶段迁移' in resp.get_json()['msg']
        # 同态重复：申请人 → 申请人
        resp = _transition(client, dev_id, '申请人')
        assert resp.status_code == 409
        # 回退（前滚一格后）
        assert _transition(client, dev_id, '积极分子').get_json()['success']
        resp = _transition(client, dev_id, '申请人')
        assert resp.status_code == 409

        assert _count(app, 'SELECT COUNT(*) FROM party_development_log') == 1
        stage = client.get(f'/party/development/api/detail/{dev_id}').get_json()['data']['stage']
        assert stage == '积极分子', '非法迁移不得改变库内状态'

    def test_stage_date_required_and_dict_values_only(self, app):
        """验收 1：to_stage 只认字典；节点日期缺则 400 语义失败；均不落库。"""
        client = _admin_client(app)
        dev_id = _save_dev(client).get_json()['data']['id']

        resp = _transition(client, dev_id, '考察对象')  # 非字典值（400 语义失败，084/086 口径）
        assert resp.status_code == 200 and resp.get_json()['success'] is False
        assert '不合法' in resp.get_json()['msg']
        resp = _transition(client, dev_id, '积极分子', stage_date='')  # 缺日期
        assert resp.get_json()['success'] is False and '节点日期' in resp.get_json()['msg']
        resp = _transition(client, dev_id, '积极分子', stage_date='2026/02/01')  # 日期格式
        assert resp.get_json()['success'] is False

        assert _count(app, 'SELECT COUNT(*) FROM party_development_log') == 0
        detail = client.get(f'/party/development/api/detail/{dev_id}').get_json()['data']
        assert detail['stage'] == '申请人'


# ══════════════════════════ 转正回写 ══════════════════════════

class TestWriteback:
    def _make_development_at_probation(self, app, client, id_card):
        dev_id = _save_dev(client, id_card=id_card).get_json()['data']['id']
        for i, ds in enumerate(['2026-02-01', '2026-03-01', '2026-04-01'], start=1):
            assert _transition(client, dev_id, STAGES[i], ds).get_json()['success']
        return dev_id

    def test_writeback_only_two_columns_with_snapshot(self, app):
        """验收 5：仅动 party_status/member_identity 两列；其余维度 diff 不变；留快照。"""
        client = _admin_client(app)
        member_id = _make_member(app, '王小明', ID_A, gender='男')
        dev_id = self._make_development_at_probation(app, client, ID_A)

        resp = _transition(client, dev_id, '正式党员', '2026-05-01',
                           confirm_writeback='1').get_json()
        assert resp['success'] is True
        assert resp['data']['writeback'] == 'done'

        from models import PartyMember
        with app.app_context():
            m = PartyMember.query.get(member_id)
            # 两列已改
            assert m.party_status == '在册' and m.member_identity == '正式党员'
            # 其余维度 diff 断言（改前显式赋值，改后逐位相等）
            assert m.member_mobility == '在家' and m.employment_status == '在职'
            assert m.member_type == '历史值' and m.branch == '第一党支部' and m.gender == '男'

        log = client.get(f'/party/development/api/detail/{dev_id}').get_json()['data']['logs'][-1]
        assert 'party_status=' in log['writeback_before'], '改前快照必须留痕'

    def test_no_confirm_no_writeback(self, app):
        """无复核确认不回写（阶段照常推进）。"""
        client = _admin_client(app)
        member_id = _make_member(app, '王小明', ID_A)
        dev_id = self._make_development_at_probation(app, client, ID_A)

        resp = _transition(client, dev_id, '正式党员', '2026-05-01').get_json()
        assert resp['success'] is True
        assert resp['data']['writeback'] == 'skipped'

        from models import PartyMember
        with app.app_context():
            m = PartyMember.query.get(member_id)
            assert m.member_identity == '预备党员', '未确认不得回写（原值不变）'
            assert m.party_status == '在册'  # 原值未变

    def test_writeback_skipped_without_member_record(self, app):
        """无在册档案（身份证匹配不到）：跳过回写，不报错。"""
        client = _admin_client(app)
        dev_id = self._make_development_at_probation(app, client, ID_B)
        resp = _transition(client, dev_id, '正式党员', '2026-05-01',
                           confirm_writeback='1').get_json()
        assert resp['success'] is True
        assert resp['data']['writeback'] == 'skipped'
        assert _count(app, "SELECT COUNT(*) FROM party_development WHERE stage='正式党员'") == 1

    def test_writeback_log_count_reconciles(self, app):
        """验收 3 对账：名册总行数 = 发展池 + 正式党员阶段；正式党员计数 = 回写留痕计数。"""
        client = _admin_client(app)
        _make_member(app, '王小明', ID_A)
        dev_a = _save_dev(client, name='王小明').get_json()['data']['id']
        dev_b = _save_dev(client, name='李小红', id_card=ID_B).get_json()['data']['id']
        for i, ds in enumerate(['2026-02-01', '2026-03-01', '2026-04-01', '2026-05-01'], start=1):
            _transition(client, dev_a, STAGES[i], ds, **(
                {'confirm_writeback': '1'} if STAGES[i] == '正式党员' else {}))
        _transition(client, dev_b, '积极分子', '2026-02-01')

        total = _count(app, 'SELECT COUNT(*) FROM party_development')
        pool = _count(app, "SELECT COUNT(*) FROM party_development WHERE stage != '正式党员'")
        formal = _count(app, "SELECT COUNT(*) FROM party_development WHERE stage = '正式党员'")
        writeback_logs = _count(app, "SELECT COUNT(*) FROM party_development_log "
                                     "WHERE to_stage='正式党员' AND writeback_before IS NOT NULL")
        assert total == pool + formal, '阶段人数合计 = 发展池 + 正式党员（对账）'
        assert formal == writeback_logs == 1, '正式党员计数 = 回写留痕计数'
        assert _count(app, "SELECT COUNT(*) FROM party_members WHERE member_identity='正式党员'") == 1


# ══════════════════════════ 导出 ══════════════════════════

class TestExport:
    def test_export_one_row_per_person_with_stage_dates(self, app):
        client = _admin_client(app)
        _save_dev(client, name='王小明', id_card=ID_A)  # 申请人：其余阶段留空
        import openpyxl
        export = client.get('/party/development/api/export')
        assert 'spreadsheetml' in (export.content_type or '')
        wb = openpyxl.load_workbook(io.BytesIO(export.data))
        rows = [list(r) for r in wb.active.iter_rows(min_row=2, values_only=True)]
        assert len(rows) == 1
        row = rows[0]
        assert row[0] == '王小明'
        assert row[5] == '申请人'
        assert row[6] == '2026-01-10', '申请日期落第 1 节点列'
        for idx in (7, 8, 9, 10):
            assert row[idx] in (None, ''), f'未到阶段留空（列 {idx}）'
        header = [c.value for c in wb.active[1]]
        assert header[6:11] == ['申请入党日期', '积极分子确定日期', '发展对象确定日期',
                                '预备党员日期', '转正日期']

    def test_export_masks_id_card_for_plain_user(self, app):
        client = _role_client(app, {'party.view': True, 'party.export': True})
        _save_dev(_admin_client(app), name='王小明', id_card=ID_A)
        import openpyxl
        export = client.get('/party/development/api/export')
        wb = openpyxl.load_workbook(io.BytesIO(export.data))
        values = [str(cell.value) for r in wb.active.iter_rows() for cell in r if cell.value]
        assert not any(ID_A == v for v in values), '无 view_sensitive 导出必须打码'
        assert any('*' in v for v in values)


class TestReworkR3R4R5:
    """返工 R1 必修项：R3 模型唯一约束 / R4 末位 X / R5 打码回显不覆盖敏感列。"""

    def test_r3_model_has_unique_constraint(self):
        from models.party_development import PartyDevelopment
        names = {c.name for c in PartyDevelopment.__table__.constraints
                 if c.__class__.__name__ == 'UniqueConstraint'}
        assert 'uq_party_development_id_card_hash' in names, \
            '新库 create_all 路径必须有 DB 级一人一条约束（R3）'

    def test_r4_id_card_trailing_x_accepted(self, app):
        client = _admin_client(app)
        ok = _save_dev(client, name='赵六', id_card='11010119900101123X').get_json()
        assert ok['success'] is True, ok.get('msg')
        bad = _save_dev(client, name='钱七',
                        id_card='11010119900101123Y').get_json()
        assert bad['success'] is False, '非 X 的非法末位仍应拒绝'

    def test_r5_masked_edit_keeps_sensitive_columns(self, app):
        admin = _admin_client(app)
        dev_id = _save_dev(admin).get_json()['data']['id']
        plain = _role_client(app, {'party.view': True, 'party.add': True})
        masked = plain.get(f'/party/development/api/detail/{dev_id}').get_json()['data']
        assert '*' in masked['id_card'] and '*' in masked['phone'], '无权用户详情应打码'

        resp = _save_dev(plain, id=str(dev_id), name='王小明二世',
                         id_card=masked['id_card'], phone=masked['phone']).get_json()
        assert resp['success'] is True, resp.get('msg')

        after = admin.get(f'/party/development/api/detail/{dev_id}').get_json()['data']
        assert after['name'] == '王小明二世', '非敏感字段正常更新'
        assert after['id_card'] == ID_A, '打码身份证不得覆盖原值（修复前必 400）'
        assert after['phone'] == '13800000001', '打码电话不得覆盖密文'


# ══════════════════════════ 权限 ══════════════════════════

class TestPermissions:
    def test_unauthenticated(self, app):
        client = app.test_client()
        assert client.get('/party/development').status_code == 302
        for path in ('/party/development/api/list', '/party/development/api/options',
                     '/party/development/api/export'):
            resp = client.get(path, headers={'X-Requested-With': 'XMLHttpRequest'})
            assert resp.status_code == 401, f'{path} 未登录应 401'
        assert client.post('/party/development/api/save',
                           data=_dev_payload()).status_code == 401
        assert _count(app, 'SELECT COUNT(*) FROM party_development') == 0

    def test_role_without_party_module_denied(self, app):
        client = _role_client(app, {'resident.view': True})
        assert client.get('/party/development').status_code == 403
        for path in ('/party/development/api/list', '/party/development/api/options',
                     '/party/development/api/export'):
            resp = client.get(path, headers={'X-Requested-With': 'XMLHttpRequest'})
            assert resp.status_code == 403
        save = client.post('/party/development/api/save', data=_dev_payload())
        assert save.status_code == 403
        assert _count(app, 'SELECT COUNT(*) FROM party_development') == 0

    def test_page_renders_for_admin(self, app):
        client = _admin_client(app)
        html = client.get('/party/development').get_data(as_text=True)
        assert '党员发展' in html
        assert 'party-development' in html, 'org 组新胶囊应渲染'
