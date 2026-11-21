# -*- coding: utf-8 -*-
"""tests/test_db_admin.py — 025+026 数据库管理面板回归

覆盖（对齐 025/026 任务书验收标准）：
- 权限：全部端点归 ADMIN_BLUEPRINTS（url_map 审计零未归类）；非 admin 403 / 未登录 401
- 🟢只读：总览/表清单/单表详情/完整性检查/Schema 对账/审计台账
- 🟡操作：建表/加字段/建索引/删索引/复制表/表重命名/ANALYZE/VACUUM 全链演练（非应用表）
- 应用表只读：加字段/建删索引/重命名对应用表一律拒绝并引导轨 A
- 🔴红级（026）：删字段/改字段(重建)/截断/删表全链 + 应用表硬拒 + 三重闸 + 重建等价/回滚 + 实测回滚
- 安全：标识符注入拒绝 / 前置备份失败中止 / 审计落 ddl_audit_log + OperationLog
"""
import json
import os

import pytest


def _make_app(tmp_path, monkeypatch):
    """隔离 DB（与 test_t021_hardening._make_app 同款先例：覆写 config 模块缓存路径）。"""
    db_path = str(tmp_path / "shucun.db")
    import config
    monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SHUCUN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("SHUCUN_LICENSE_FILE", str(tmp_path / "license.key"))
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    for d in ("uploads", "backups", "logs", "temp", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


def _unlock():
    """密钥就绪（前置备份 .wbak 加密需要；照 test_t20_backup._unlock 先例）。"""
    from utils.crypto import key_holder, generate_salt
    key_holder.lock()
    key_holder.initialize('测试口令123', salt=generate_salt())


def _mk_user(app, username, role_name, perms):
    from models import db, Role, User
    with app.app_context():
        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            role = Role(name=role_name)
            db.session.add(role)
            db.session.commit()
        role.permissions = json.dumps(perms, ensure_ascii=False)
        db.session.commit()
        u = User(username=username, role_id=role.id, is_active=True)
        u.set_password("TestPass123")
        db.session.add(u)
        db.session.commit()
        return u.id


def _login_as(client, user_id):
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["username"] = "admin"


def _admin_id(app):
    from models import User
    with app.app_context():
        return User.query.filter_by(username='admin').first().id


def _open_perm():
    return {"__schema_version__": 3,
            "resident.view": True,
            "special": {"view_sensitive": False, "death_confirm": False,
                        "import_batch": False, "backup_restore": False,
                        "mask_config": False}}


class TestEndpointClassification:
    """验收：面板全部端点归 ADMIN_BLUEPRINTS（漏归=403）；url_map 审计零未归类。"""

    def test_all_endpoints_admin_classified(self, app):
        from utils.permissions import ADMIN_BLUEPRINTS, PUBLIC_ENDPOINTS
        assert 'db_admin' in ADMIN_BLUEPRINTS
        rules = [r for r in app.url_map.iter_rules()
                 if r.endpoint.startswith('db_admin.')]
        assert rules, 'db-admin 蓝图应注册端点'
        for r in rules:
            full = r.endpoint
            assert full not in PUBLIC_ENDPOINTS, f'{full} 不应进入 PUBLIC_ENDPOINTS'
            bp = full.rsplit('.', 1)[0]
            assert bp in ADMIN_BLUEPRINTS, f'{full} 未归 ADMIN_BLUEPRINTS'

    def test_nonadmin_denied(self, app):
        uid = _mk_user(app, "clerk01", "clerk_role", _open_perm())
        c = app.test_client()
        _login_as(c, uid)
        for path in ('/db-admin/', '/db-admin/api/overview',
                     '/db-admin/api/tables', '/db-admin/api/check',
                     '/db-admin/api/reconcile', '/db-admin/api/audit'):
            r = c.get(path)
            assert r.status_code == 403, f'非 admin 应 403：{path}'
        r = c.post('/db-admin/api/execute', json={'action': 'analyze'})
        assert r.status_code == 403

    def test_anonymous_401_json(self, app):
        c = app.test_client()
        r = c.get('/db-admin/api/tables')
        assert r.status_code == 401

    def test_admin_ok(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        assert c.get('/db-admin/').status_code == 200
        assert c.get('/db-admin/api/tables').status_code == 200


class TestReadOnlyGreen:
    """🟢 只读：总览/表清单/单表详情/完整性检查/Schema 对账/审计台账。"""

    def test_overview_and_tables(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.get('/db-admin/api/overview').get_json()
        assert r['success'] and r['data']['table_count'] >= 1
        r = c.get('/db-admin/api/tables').get_json()
        assert r['success']
        names = [t['name'] for t in r['data']]
        assert 'residents' in names
        res = [t for t in r['data'] if t['name'] == 'residents'][0]
        assert res['is_app'] is True, 'residents 应识别为应用表'

    def test_table_detail(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.get('/db-admin/api/table/residents').get_json()
        assert r['success']
        d = r['data']
        assert d['is_app'] is True
        assert d['columns'] and d['create_sql']

    def test_integrity_check_ok(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.get('/db-admin/api/check').get_json()
        assert r['success']
        assert r['data']['ok'] is True
        assert 'integrity_check' in r['data']['details']

    def test_reconcile_shape(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.get('/db-admin/api/reconcile').get_json()
        assert r['success']
        assert r['data']['checked_tables'] >= 1
        assert r['data']['missing_tables'] == []

    def test_audit_list_bootstraps_table(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.get('/db-admin/api/audit').get_json()
        assert r['success']
        assert isinstance(r['data'], list)


def _post(c, action, payload):
    return c.post('/db-admin/api/execute', json={'action': action,
                                                 'payload': payload})


class TestYellowOperations:
    """🟡 全链演练（非应用表）+ 应用表拒绝 + 审计留痕。"""

    def test_full_flow_on_panel_table(self, app):
        _unlock()
        c = app.test_client()
        _login_as(c, _admin_id(app))

        # ① 建表
        r = _post(c, 'create_table', {
            'name': 'demo_roster',
            'columns': [
                {'name': 'id', 'type': 'INTEGER', 'pk': True},
                {'name': 'name', 'type': 'TEXT'},
                {'name': 'qty', 'type': 'INTEGER', 'notnull': True,
                 'default': '0'},
            ]})
        assert r.status_code == 200 and r.get_json()['success'], r.get_json()
        # ② 加字段
        r = _post(c, 'add_column', {'table': 'demo_roster',
                                    'column': {'name': 'remark',
                                               'type': 'TEXT', 'default': '-'}})
        assert r.status_code == 200 and r.get_json()['success']
        # ③ 建索引
        r = _post(c, 'create_index', {'table': 'demo_roster',
                                      'name': 'idx_demo_name',
                                      'columns': ['name']})
        assert r.status_code == 200 and r.get_json()['success']
        # ④ 复制表（结构+数据）
        r = _post(c, 'copy_table', {'src': 'demo_roster',
                                    'dst': 'demo_roster_copy', 'with_data': True})
        assert r.status_code == 200 and r.get_json()['success']
        # ⑤ 表重命名
        r = _post(c, 'rename_table', {'old': 'demo_roster_copy',
                                      'new': 'demo_roster_v2'})
        assert r.status_code == 200 and r.get_json()['success']
        # ⑥ 删索引（非应用表允许）
        r = _post(c, 'drop_index', {'name': 'idx_demo_name'})
        assert r.status_code == 200 and r.get_json()['success']
        # ⑦ ANALYZE
        r = _post(c, 'analyze', {})
        assert r.status_code == 200 and r.get_json()['success']
        # ⑧ VACUUM
        r = _post(c, 'vacuum', {})
        assert r.status_code == 200 and r.get_json()['success']
        assert r.get_json()['data']['backup_file'].endswith('.wbak')

        # 结构核验：表确实建成/改名，索引已删
        with app.app_context():
            from utils.ddl_guard import table_exists, index_exists
            assert table_exists('demo_roster')
            assert table_exists('demo_roster_v2')
            assert not table_exists('demo_roster_copy')
            assert not index_exists('idx_demo_name')

        # 审计留痕
        with app.app_context():
            from models import db, OperationLog
            from sqlalchemy import text as _text
            rows = db.session.execute(_text(
                'SELECT COUNT(*) FROM ddl_audit_log')).scalar()
            assert rows >= 8, '每个黄级操作都应落 ddl_audit_log'
            oplogs = OperationLog.query.filter_by(module='数据库管理').count()
            assert oplogs >= 8, '每个黄级操作都应写 OperationLog'

    def test_app_table_refused(self, app):
        _unlock()
        c = app.test_client()
        _login_as(c, _admin_id(app))
        # 加字段
        r = _post(c, 'add_column', {'table': 'residents',
                                    'column': {'name': 'zzz', 'type': 'TEXT'}})
        assert r.status_code == 400
        assert '轨 A' in r.get_json()['msg']
        # 建索引
        r = _post(c, 'create_index', {'table': 'residents',
                                      'name': 'idx_zzz', 'columns': ['name']})
        assert r.status_code == 400
        # 重命名
        r = _post(c, 'rename_table', {'old': 'residents', 'new': 'residents_v9'})
        assert r.status_code == 400
        # 删索引（对应用表上的自定义索引同样拒绝；先造一个）
        with app.app_context():
            from models import db
            from sqlalchemy import text as _text
            db.session.execute(_text(
                'CREATE INDEX IF NOT EXISTS idx_res_test ON residents (name)'))
            db.session.commit()
        r = _post(c, 'drop_index', {'name': 'idx_res_test'})
        assert r.status_code == 400
        assert '轨 A' in r.get_json()['msg']
        with app.app_context():
            from utils.ddl_guard import index_exists
            assert index_exists('idx_res_test'), '拒绝后索引应保留'

    def test_automatic_index_refused(self, app):
        _unlock()
        c = app.test_client()
        _login_as(c, _admin_id(app))
        # 找真实存在的自动索引（residents 主键 sqlite_autoindex_*）
        with app.app_context():
            from models import db
            from sqlalchemy import text as _text
            rows = db.session.execute(_text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name LIKE 'sqlite_autoindex%' LIMIT 1")).fetchall()
            auto_name = rows[0][0] if rows else None
        assert auto_name, '测试库应存在主键自动索引'
        r = _post(c, 'drop_index', {'name': auto_name})
        assert r.status_code == 400
        assert '自动索引' in r.get_json()['msg']


class TestSecurity:
    """标识符注入拒绝 / 预览不执行 / 备份失败中止。"""

    def test_identifier_injection_refused(self, app):
        _unlock()
        c = app.test_client()
        _login_as(c, _admin_id(app))
        bad = 'evil_drop_table'
        for action, payload in (
            ('create_table', {'name': 'x;DROP TABLE residents',
                              'columns': [{'name': 'a', 'type': 'TEXT'}]}),
            ('create_table', {'name': 'x"--',
                              'columns': [{'name': 'a', 'type': 'TEXT'}]}),
            ('create_table', {'name': 'SELECT',
                              'columns': [{'name': 'a', 'type': 'TEXT'}]}),
            ('add_column', {'table': 'residents";--',
                            'column': {'name': 'a', 'type': 'TEXT'}}),
            ('rename_table', {'old': 'residents', 'new': 'select'}),
        ):
            r = _post(c, action, payload)
            assert r.status_code == 400, (action, r.get_json())
        r = c.post('/db-admin/api/preview', json={
            'action': 'create_table',
            'payload': {'name': bad + ';bad',
                        'columns': [{'name': 'a;', 'type': 'TEXT'}]}})
        assert r.status_code == 400

    def test_preview_does_not_execute(self, app):
        _unlock()
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.post('/db-admin/api/preview', json={
            'action': 'create_table',
            'payload': {'name': 'only_preview',
                        'columns': [{'name': 'id', 'type': 'INTEGER', 'pk': True}]}})
        assert r.status_code == 200 and r.get_json()['success']
        assert 'CREATE TABLE' in r.get_json()['data']['sql']
        with app.app_context():
            from utils.ddl_guard import table_exists
            assert not table_exists('only_preview'), '预览不得执行'

    def test_backup_failure_aborts(self, app, monkeypatch):
        _unlock()
        from utils import ddl_guard

        def _boom(trigger):
            raise ddl_guard.GuardError('前置 .wbak 备份失败，操作已中止：模拟磁盘满')

        monkeypatch.setattr(ddl_guard, 'preflight_backup', _boom)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = _post(c, 'create_table', {
            'name': 'should_not_exist',
            'columns': [{'name': 'id', 'type': 'INTEGER', 'pk': True}]})
        assert r.status_code == 400
        assert '备份失败' in r.get_json()['msg']
        with app.app_context():
            from utils.ddl_guard import table_exists
            assert not table_exists('should_not_exist'), '备份失败必须中止操作'

    def test_notnull_without_default_refused(self, app):
        _unlock()
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = _post(c, 'create_table', {
            'name': 'bad_notnull',
            'columns': [{'name': 'id', 'type': 'INTEGER', 'pk': True},
                        {'name': 'col', 'type': 'TEXT', 'notnull': True}]})
        assert r.status_code == 400
        assert '默认值' in r.get_json()['msg']

    def test_unknown_action_refused(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.post('/db-admin/api/execute',
                   json={'action': 'drop_table', 'payload': {}})
        assert r.status_code == 400
        r = c.post('/db-admin/api/red/execute',
                   json={'action': 'xyz_drop', 'payload': {}})
        assert r.status_code == 400


def _make_red_tbl(app, rows=3):
    """面板自建非应用表 demo_red（id 主键 + code + note）。"""
    from models import db
    from sqlalchemy import text as _text
    with app.app_context():
        db.session.execute(_text(
            'DROP TABLE IF EXISTS demo_red'))
        db.session.commit()
        db.session.execute(_text(
            'CREATE TABLE demo_red (id INTEGER PRIMARY KEY, code TEXT, note TEXT)'))
        for i in range(1, rows + 1):
            db.session.execute(_text(
                'INSERT INTO demo_red (code, note) VALUES (:c, :n)'),
                {'c': f'c{i}', 'n': f'n{i}'})
        db.session.commit()


def _red_post(c, action, payload, confirm=None):
    body = {'action': action, 'payload': payload}
    if confirm is not None:
        body['confirm_name'] = confirm
    return c.post('/db-admin/api/red/execute', json=body)


class TestRedOperations:
    """🔴 红级（026）：全流程演练 + 应用表硬拒 + 三重闸 + 重建等价/回滚 + 可恢复性。"""

    def test_full_red_flow(self, app):
        _unlock()
        _make_red_tbl(app)
        c = app.test_client()
        _login_as(c, _admin_id(app))

        # ① 改字段（重建引擎）：note TEXT → VARCHAR(30)
        r = _red_post(c, 'alter_column',
                      {'table': 'demo_red',
                       'column': {'name': 'note', 'type': 'VARCHAR(30)'}},
                      confirm='demo_red')
        assert r.status_code == 200 and r.get_json()['success'], r.get_json()
        d = r.get_json()['data']
        assert d['rows'] == 3 and d['backup_file'].endswith('.wbak')
        assert d['audit_id'] > 0
        with app.app_context():
            from models import db
            from sqlalchemy import text as _text
            rows = db.session.execute(_text(
                'SELECT code, note FROM demo_red ORDER BY id')).fetchall()
            assert [(r[0], r[1]) for r in rows] == [('c1', 'n1'), ('c2', 'n2'), ('c3', 'n3')], \
                '重建后数据须逐条等价'
            cols = db.session.execute(_text(
                'PRAGMA table_info(demo_red)')).fetchall()
            note_col = [c for c in cols if c[1] == 'note'][0]
            assert note_col[2].upper().startswith('VARCHAR'), '类型应已变更'
        # ② 删字段：note（无索引/外键引用）
        r = _red_post(c, 'drop_column',
                      {'table': 'demo_red', 'column': 'note'}, confirm='demo_red')
        assert r.status_code == 200 and r.get_json()['success']
        with app.app_context():
            from models import db
            from sqlalchemy import text as _text
            cols = db.session.execute(_text(
                'PRAGMA table_info(demo_red)')).fetchall()
            assert 'note' not in [c[1] for c in cols]
        # ③ 截断
        r = _red_post(c, 'truncate_table', {'table': 'demo_red'},
                      confirm='demo_red')
        assert r.status_code == 200 and r.get_json()['success']
        with app.app_context():
            from models import db
            from sqlalchemy import text as _text
            assert db.session.execute(_text(
                'SELECT COUNT(*) FROM demo_red')).scalar() == 0
        # ④ 删表
        r = _red_post(c, 'drop_table', {'table': 'demo_red'}, confirm='demo_red')
        assert r.status_code == 200 and r.get_json()['success']
        with app.app_context():
            from utils.ddl_guard import table_exists
            assert not table_exists('demo_red')
        # 审计：红级 4 条 + 每条 OperationLog
        with app.app_context():
            from models import db, OperationLog
            from sqlalchemy import text as _text
            cnt = db.session.execute(_text(
                "SELECT COUNT(*) FROM ddl_audit_log WHERE action LIKE 'red_%'"
            )).scalar()
            assert cnt >= 4
            assert OperationLog.query.filter_by(
                module='数据库管理', action='red_drop_table').count() >= 1

    def test_app_table_red_all_refused(self, app):
        _unlock()
        c = app.test_client()
        _login_as(c, _admin_id(app))
        for action, payload in (
            ('drop_column', {'table': 'residents', 'column': 'name'}),
            ('alter_column', {'table': 'residents',
                              'column': {'name': 'name', 'type': 'TEXT'}}),
            ('truncate_table', {'table': 'residents'}),
            ('drop_table', {'table': 'residents'}),
        ):
            r = _red_post(c, action, payload, confirm='residents')
            assert r.status_code == 400, action
            assert '轨 A' in r.get_json()['msg'], action
        # preview 同样拒绝
        r = c.post('/db-admin/api/red/preview', json={
            'action': 'drop_table', 'payload': {'table': 'residents'}})
        assert r.status_code == 400

    def test_confirm_mismatch_refused(self, app):
        _unlock()
        _make_red_tbl(app)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = _red_post(c, 'truncate_table', {'table': 'demo_red'},
                      confirm='demo_red_typo')
        assert r.status_code == 400
        assert '闸 2' in r.get_json()['msg']
        with app.app_context():
            from models import db
            from sqlalchemy import text as _text
            assert db.session.execute(_text(
                'SELECT COUNT(*) FROM demo_red')).scalar() == 3, '闸 2 拒绝后数据不动'

    def test_preview_shows_impact(self, app):
        _unlock()
        _make_red_tbl(app)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.post('/db-admin/api/red/preview', json={
            'action': 'truncate_table', 'payload': {'table': 'demo_red'}})
        assert r.status_code == 200 and r.get_json()['success']
        assert r.get_json()['data']['rows'] == 3

    def test_backup_failure_aborts_red(self, app, monkeypatch):
        _unlock()
        _make_red_tbl(app)
        from utils import ddl_guard

        def _boom(trigger):
            raise ddl_guard.GuardError('前置 .wbak 备份失败，操作已中止：模拟磁盘满')

        monkeypatch.setattr(ddl_guard, 'preflight_backup', _boom)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = _red_post(c, 'drop_table', {'table': 'demo_red'}, confirm='demo_red')
        assert r.status_code == 400
        assert '备份失败' in r.get_json()['msg']
        with app.app_context():
            from utils.ddl_guard import table_exists
            assert table_exists('demo_red'), '备份失败必须中止（闸 1）'

    def test_rebuild_failure_rolls_back(self, app, monkeypatch):
        """重建任一步失败 → 整体回滚、原表数据完好、无 _rebuild 残留。"""
        _unlock()
        _make_red_tbl(app)
        from utils import ddl_guard

        def _boom(table, changed_column):
            raise sqlite3_or_runtime()

        def sqlite3_or_runtime():
            raise RuntimeError('simulated rebuild crash')

        monkeypatch.setattr(ddl_guard, '_rebuild_table', _boom)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = _red_post(c, 'alter_column',
                      {'table': 'demo_red',
                       'column': {'name': 'note', 'type': 'VARCHAR(30)'}},
                      confirm='demo_red')
        assert r.status_code == 400
        assert '已回滚' in r.get_json()['msg']
        with app.app_context():
            from models import db
            from sqlalchemy import text as _text
            rows = db.session.execute(_text(
                'SELECT code, note FROM demo_red ORDER BY id')).fetchall()
            assert [(r0[0], r0[1]) for r0 in rows] == [('c1', 'n1'), ('c2', 'n2'), ('c3', 'n3')]
            assert db.session.execute(_text(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='demo_red__rebuild'"
            )).scalar() == 0

    def test_rebuild_with_fk_child(self, app):
        """父表被外键子表引用时重建：数据等价、子表引用保持、外键校验通过、无残留。"""
        _unlock()
        from models import db
        from sqlalchemy import text as _text
        with app.app_context():
            db.session.execute(_text('CREATE TABLE fk_parent (id INTEGER PRIMARY KEY, name TEXT)'))
            db.session.execute(_text(
                'CREATE TABLE fk_child (id INTEGER PRIMARY KEY, '
                'pid INTEGER REFERENCES fk_parent(id), note TEXT)'))
            db.session.execute(_text("INSERT INTO fk_parent (name) VALUES ('p1'),('p2')"))
            db.session.execute(_text("INSERT INTO fk_child (pid, note) VALUES (1,'c1'),(2,'c2')"))
            db.session.commit()
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = _red_post(c, 'alter_column',
                      {'table': 'fk_parent',
                       'column': {'name': 'name', 'type': 'VARCHAR(50)'}},
                      confirm='fk_parent')
        assert r.status_code == 200 and r.get_json()['success'], r.get_json()
        with app.app_context():
            from sqlalchemy import text as _text
            assert db.session.execute(_text(
                'SELECT id, name FROM fk_parent ORDER BY id')).fetchall() == [(1, 'p1'), (2, 'p2')]
            assert db.session.execute(_text(
                'SELECT id, pid, note FROM fk_child ORDER BY id')).fetchall() == [
                    (1, 1, 'c1'), (2, 2, 'c2')], '子表数据与引用关系须保持'
            assert db.session.execute(_text('PRAGMA foreign_key_check')).fetchall() == []
            assert db.session.execute(_text(
                "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%__rebuild'"
            )).scalar() == 0

    def test_restore_after_truncate(self, app, tmp_path):
        """可恢复性实测：截断后从闸 1 备份整库回滚，数据恢复。"""
        _unlock()
        _make_red_tbl(app)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = _red_post(c, 'truncate_table', {'table': 'demo_red'}, confirm='demo_red')
        assert r.status_code == 200 and r.get_json()['success']
        backup_name = r.get_json()['data']['backup_file']
        import config as _config
        wbak_path = os.path.join(_config.BACKUP_DIR, backup_name)
        assert os.path.isfile(wbak_path)
        # 恢复纪律：先断会话/引擎，再 restore，再验证
        from models import db
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
        from utils.backup_crypto import restore_from_wbak
        meta = restore_from_wbak(wbak_path, _config.DB_PATH,
                                 _config.UPLOAD_DIR, _config.BACKUP_DIR)
        assert meta is not None
        with app.app_context():
            from sqlalchemy import text as _text
            cnt = db.session.execute(_text(
                'SELECT COUNT(*) FROM demo_red')).scalar()
        assert cnt == 3, '恢复后数据应回来（可恢复性实测）'