# -*- coding: utf-8 -*-
"""036 P3 导入方案复用与治理（F1~F5）专项测试。

覆盖：
- F1 import_profiles 表/迁移幂等 + standard_v1 种子与 FIELD_CATALOG 一致（24 项）
- F2 方案保存/加载（映射逐列一致、仍返回确认页、catalog_version 不一致提示复核）
- F3 批次列表页/API（筛选/分页；admin 200、无 special 403）
- F4 模板版本标记（批次 profile_key 版本快照 + 模板文件版本页）
- F5 字段注册审批页（页面 resident.view 200、审批动作 admin 403；审批复用既有函数、审批后入统计口径）
"""
import json
import os
import sqlite3
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "shucun.db")
    import config
    for key, val in {
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_LOG_DIR": str(tmp_path / "logs"),
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_LICENSE_FILE": str(tmp_path / "license.key"),
        "SHUCUN_DISABLE_PASSWORD_CHANGE": "1",
        "SHUCUN_DISABLE_PERIODIC": "1",
        "SHUCUN_DISABLE_CSRF": "1",
    }.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "TEMP_FOLDER", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "IMPORTS_DIR", str(tmp_path / "temp" / "imports"))
    for d in ("uploads", "backups", "logs", "temp", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    from utils.crypto import key_holder
    with application.app_context():
        if not key_holder.is_initialized:
            key_holder.initialize("test-secret-key-0123456789abcdef")
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


def _mk_user(app, username, perms):
    from models import db, Role, User
    with app.app_context():
        role = Role(name='role_' + username)
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


def _admin_id(app):
    from models import User
    with app.app_context():
        return User.query.filter_by(username="admin").first().id


def _perms(edit=True, special=False):
    return {"__schema_version__": 3, "resident.view": True,
            "resident.edit": edit,
            "special": {"view_sensitive": False, "death_confirm": False,
                        "import_batch": special, "backup_restore": False,
                        "mask_config": False}}


def _write_xlsx(path, headers, rows=()):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return str(path)


def _make_batch(app, xlsx_path, mode='custom'):
    from models import db, ImportBatch
    with app.app_context():
        b = ImportBatch(batch_id=uuid.uuid4().hex[:12], source_file='t.xlsx',
                        source_path=xlsx_path, source_hash='h_' + uuid.uuid4().hex[:8],
                        status='pending', mode=mode)
        db.session.add(b)
        db.session.commit()
        return b.batch_id


def _seed_standard(app):
    from utils.import_engine import save_profile, standard_profile_mapping
    with app.app_context():
        return save_profile('标准模板 v1', standard_profile_mapping(),
                            created_by='system', profile_key='standard_v1',
                            is_builtin=True, required_permission='resident.edit')


# ---------------- F1 ----------------

def test_catalog_version_and_standard_mapping():
    from utils.import_engine import (CATALOG_VERSION, FIELD_CATALOG,
                                     field_catalog_version, standard_profile_mapping)
    mapping = standard_profile_mapping()
    assert CATALOG_VERSION == field_catalog_version()
    assert len(mapping) == len(FIELD_CATALOG) == 24
    assert {m['field'] for m in mapping} == {f[0] for f in FIELD_CATALOG}
    assert all(m['transform'] == 'map' and m['header'] for m in mapping)


def test_migration_m014_idempotent_and_seed_matches_catalog(tmp_path):
    from utils.import_engine import CATALOG_VERSION, standard_profile_mapping
    migration = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'scripts', 'migrations', 'm014_import_profiles.sql')
    db_path = str(tmp_path / 'm014.db')
    conn = sqlite3.connect(db_path)
    with open(migration, encoding='utf-8') as f:
        sql = f.read()
    conn.executescript(sql)
    conn.executescript(sql)  # 重复执行不重复插种子
    rows = conn.execute('SELECT profile_key, is_builtin, catalog_version, mapping_json '
                        'FROM import_profiles').fetchall()
    conn.close()
    assert len(rows) == 1
    key, builtin, version, mapping_json = rows[0]
    assert key == 'standard_v1' and builtin == 1
    assert version == CATALOG_VERSION
    assert json.loads(mapping_json) == standard_profile_mapping()


def test_save_profile_unique_by_key(app):
    from models import ImportProfile
    from utils.import_engine import save_profile, profile_key_for_mapping
    mapping = [{'header': '姓名', 'field': 'name', 'transform': 'map'}]
    with app.app_context():
        a = save_profile('方案A', mapping, created_by='u1')
        key = profile_key_for_mapping(mapping)
        # 同一映射被第二人重复保存：key 复用、名称更新，但归属不得被夺取（R-036-04）
        b = save_profile('方案A改', mapping, created_by='u2')
        rec = ImportProfile.query.filter_by(profile_key=key).first()
        assert a.profile_key == b.profile_key == key
        assert ImportProfile.query.filter_by(profile_key=key).count() == 1
        assert b.name == '方案A改'
        assert rec.created_by == 'u1', 'created_by 不得被后保存者覆盖'


# ---------------- F2 ----------------

def test_profile_save_from_batch_and_list_visibility(app):
    from utils.import_engine import save_mapping
    xlsx = _write_xlsx('/tmp/t036_profile.xlsx',
                       ['姓名', '身份证号', '备注列'])
    bid = _make_batch(app, xlsx)
    with app.app_context():
        from models import db
        save_mapping(bid, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'manual'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'manual'},
            {'source': '备注列', 'action': 'ext', 'matched_by': 'manual'},
        ])
        db.session.commit()
    uid = _mk_user(app, 'editor1', _perms(edit=True))
    c = app.test_client()
    _login_as(c, uid)
    r = c.post('/import/profiles', json={'name': '镇级季度名单', 'batch_id': bid})
    assert r.status_code == 200 and r.get_json()['success']
    key = r.get_json()['data']['profile_key']
    assert key.startswith('custom_')
    assert r.get_json()['data']['mapping'] == [
        {'header': '姓名', 'field': 'name', 'transform': 'map'},
        {'header': '身份证号', 'field': 'id_card', 'transform': 'map'},
        {'header': '备注列', 'field': '备注列', 'transform': 'ext'},
    ]
    # 重复保存同映射 → 不产生第二行
    c.post('/import/profiles', json={'name': '同名改', 'batch_id': bid})
    r = c.get('/import/profiles')
    keys = [p['profile_key'] for p in r.get_json()['data']]
    assert keys.count(key) == 1


def test_profile_apply_keeps_confirmation_and_flags_version_mismatch(app):
    xlsx = _write_xlsx('/tmp/t036.xlsx', ['姓名', '身份证号', '陌生列'])
    bid = _make_batch(app, xlsx)
    _seed_standard(app)
    c = app.test_client()
    _login_as(c, _admin_id(app))

    r = c.get(f'/import/{bid}/mapping?profile_key=standard_v1')
    data = r.get_json()['data']
    assert r.status_code == 200
    cols = {col['source']: col for col in data['columns']}
    assert cols['姓名']['target'] == 'name' and cols['姓名']['matched_by'] == 'preset'
    assert cols['身份证号']['target'] == 'id_card'
    assert data['profile']['version_mismatch'] is False
    assert '陌生列' in data['profile']['unmatched']
    assert data['threshold']['blocked'] is False
    # 仍返回确认所需的全量列与阈值（未跳过人工确认）
    assert len(data['columns']) == 3 and 'thresholds' in data['threshold']

    from models import db, ImportProfile
    with app.app_context():
        rec = ImportProfile.query.filter_by(profile_key='standard_v1').first()
        rec.catalog_version = 'v0-old'
        db.session.commit()
    r = c.get(f'/import/{bid}/mapping?profile_key=standard_v1')
    assert r.get_json()['data']['profile']['version_mismatch'] is True


def test_profiles_require_resident_edit(app):
    uid = _mk_user(app, 'viewer1', _perms(edit=False, special=True))
    c = app.test_client()
    _login_as(c, uid)
    assert c.get('/import/profiles').status_code == 403
    assert c.post('/import/profiles', json={'name': 'x', 'batch_id': 'nope'}).status_code == 403


# ---------------- F3 ----------------

def test_batches_permissions_and_filters(app):
    of = _write_xlsx('/tmp/t036b.xlsx', ['姓名'])
    t1 = _make_batch(app, of, mode='template')
    t2 = _make_batch(app, of, mode='custom')

    c = app.test_client()
    _login_as(c, _admin_id(app))
    assert c.get('/import/batches').status_code == 200
    r = c.get('/import/batches/data')
    body = r.get_json()
    assert r.status_code == 200 and body['total'] == 2
    assert {row['batch_id'] for row in body['data']} == {t1, t2}

    r = c.get('/import/batches/data?mode=template')
    assert [row['batch_id'] for row in r.get_json()['data']] == [t1]
    r = c.get('/import/batches/data?status=done')
    assert r.get_json()['total'] == 0
    r = c.get('/import/batches/data?limit=1&page=2')
    assert r.get_json()['pages'] == 2 and len(r.get_json()['data']) == 1

    clerk = _mk_user(app, 'clerk036', _perms(edit=True, special=False))
    c2 = app.test_client()
    _login_as(c2, clerk)
    assert c2.get('/import/batches').status_code == 403
    assert c2.get('/import/batches/data').status_code == 403


# ---------------- R-036-01 菜单入口 ----------------

def test_sidebar_menu_entries(app):
    """R-036-01（方案A）：导入批次 / 字段注册审批 两个入口按权限渲染。

    - admin：侧栏「数据管理」组见导入批次；证明开具页不再承载胶囊；设置中心见「字段注册审批」
    - 有 special.import_batch 的非 admin：侧栏可见（不再依赖 certificate.view）
    - 无 special.import_batch：侧栏不可见

    057a 修订：系统管理 12 项从侧栏下沉到设置中心 hub（A 方案 §2.3 / D-16），
    「字段注册审批」改为在 /settings/center 的入口卡里断言；侧栏只断言「设置」入口存在。
    091 修订：侧栏六组后「导入批次」曾回到侧栏「系统」组。
    102 修订：撤「系统」组，导入批次改由 /certificate/ 页 module_tabs 胶囊（tools 分组）承载。
    2026-11-16 修订（主理人指示）：tools 胶囊组整组撤除，导入批次回到侧栏「数据管理」组
    （与「台账建表」并列），项级守卫 `special.import_batch` 语义不变。
    """

    def _page(user_id, url):
        c = app.test_client()
        _login_as(c, user_id)
        resp = c.get(url)
        return resp.get_data(as_text=True) if resp.status_code == 200 else ''

    def _sidebar(user_id):
        return _page(user_id, '/dashboard/')

    def _cert(user_id):
        return _page(user_id, '/certificate/')

    def _center(user_id):
        return _page(user_id, '/settings/center')

    html = _sidebar(_admin_id(app))
    # 2026-11-16：导入批次在侧栏「数据管理」组；设置入口保留
    assert '导入批次' in html and '/import/batches' in html
    assert '实用工具' not in html
    assert '/settings/center' in html
    cert = _cert(_admin_id(app))
    # 侧栏在每页都渲染（含 /import/batches 链接），故此处按胶囊锚点判定「页内入口已撤」
    assert '#tab=import-batch' not in cert, '证明开具页不再承载导入批次胶囊入口'
    center = _center(_admin_id(app))
    assert '字段注册审批' in center and '/import/registry' in center

    uid = _mk_user(app, 'special036m',
                   {**_perms(edit=True, special=True), 'certificate.view': True})
    assert '导入批次' in _sidebar(uid), '有 special.import_batch 即应在侧栏看到入口'

    uid = _mk_user(app, 'plain036m',
                   {**_perms(edit=True, special=False), 'certificate.view': True})
    assert '导入批次' not in _sidebar(uid)
    assert '导入批次' not in _cert(uid)
    assert '字段注册审批' not in _sidebar(uid)
    # 2026-11-16 语义边界：导入批次不再依赖证明开具页 —— 只有 special.import_batch 的用户
    # 也能从侧栏直接进入；/certificate/ 仍按其自身权限 403（两侧解耦）
    uid = _mk_user(app, 'nocert036m', _perms(edit=True, special=True))
    assert '导入批次' in _sidebar(uid), '侧栏入口不应再依赖 certificate.view'
    assert _cert(uid) == ''


# ---------------- F4 ----------------

def test_template_batch_records_version_and_template_file(app, tmp_path):
    """R-036-03：F4 走真实上传路径，验证 start_batch 落 profile_key 纯键 + 独立版本快照列。"""
    import io
    xlsx = _write_xlsx(str(tmp_path / 't036c.xlsx'), ['姓名', '身份证号'])
    # 反例用不同内容（不同表头），避免 source_hash 命中幂等去重分支、污染断言
    xlsx2 = _write_xlsx(str(tmp_path / 't036d.xlsx'), ['姓名', '身份证号', '备注列'])
    c = app.test_client()
    _login_as(c, _admin_id(app))

    from models import ImportBatch
    from utils.import_engine import (CATALOG_VERSION, STANDARD_PROFILE_KEY,
                                     batch_catalog_version)

    def _upload(path, name, mode):
        with open(path, 'rb') as fh:
            data = fh.read()
        r = c.post('/import/upload',
                   data={'file': (io.BytesIO(data), name), 'mode': mode},
                   content_type='multipart/form-data')
        assert r.status_code == 200 and r.get_json()['success'], r.get_json()
        return r.get_json()['data']['batch_id']

    bid = _upload(xlsx, 't036c.xlsx', 'template')
    with app.app_context():
        batch = ImportBatch.query.filter_by(batch_id=bid).first()
        assert batch.mode == 'template'
        assert batch.profile_key == STANDARD_PROFILE_KEY, 'profile_key 应为纯键，不含版本'
        assert batch.catalog_version == CATALOG_VERSION, '版本快照应落独立列'
        assert batch_catalog_version(batch) == CATALOG_VERSION

    # 自定义模式反例：不落 profile_key / catalog_version
    bid2 = _upload(xlsx2, 't036d.xlsx', 'custom')
    with app.app_context():
        b2 = ImportBatch.query.filter_by(batch_id=bid2).first()
        assert b2.profile_key is None
        assert b2.catalog_version is None

    # 模板文件版本页（含「模板版本」工作表，值与目录版本一致）
    r = c.get('/resident/api/template')
    assert r.status_code == 200
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    assert '模板版本' in wb.sheetnames
    assert wb['模板版本']['B1'].value == CATALOG_VERSION

    # R-036-09（选项②）：模板列 = standard_v1 = 24 项，含「所在网格」
    from utils.import_engine import standard_profile_mapping
    headers = [cell.value for cell in wb.worksheets[0][1]]
    assert len(headers) == 24
    assert '所在网格' in headers
    assert headers == [m['header'] for m in standard_profile_mapping()]


# ---------------- 结构断言（SSR 文本 diff 对非法嵌套零感知，034 教训） ----------------

def _form_depth_stats(html):
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.max_depth = 0
            self.forms = 0
            self.actions = []

        def handle_starttag(self, tag, attrs):
            if tag == 'form':
                d = dict(attrs)
                self.depth += 1
                self.max_depth = max(self.max_depth, self.depth)
                self.forms += 1
                if (d.get('method') or '').lower() == 'post':
                    self.actions.append(d.get('action', ''))

        def handle_endtag(self, tag):
            if tag == 'form' and self.depth:
                self.depth -= 1

    p = P()
    p.feed(html)
    return p


def test_new_pages_structure(app):
    _registry_draft(app, '结构字段')
    c = app.test_client()
    _login_as(c, _admin_id(app))

    reg = _form_depth_stats(c.get('/import/registry?status=draft').get_data(as_text=True))
    assert reg.max_depth == 1 and reg.forms >= 2  # 每个审批动作独立 form，无嵌套
    assert all('/import/registry/' in a for a in reg.actions)

    html = c.get('/import/batches').get_data(as_text=True)
    assert 'id="searchForm"' in html and 'id="tableBody"' in html and 'id="pagination"' in html
    assert _form_depth_stats(html).max_depth == 1


def _registry_draft(app, code='家庭收入'):
    from models import db, ImportFieldRegistry
    with app.app_context():
        rec = ImportFieldRegistry(code=code, display_name=code, data_type='string',
                                  source_batch='b1', definition='{"口径说明":"年收入"}',
                                  status='draft')
        db.session.add(rec)
        db.session.commit()
        return rec.id


def test_registry_page_open_approval_admin_only(app):
    """063-FIX：页面按 resident.view 开放；approve/reject 仍 admin 专属（036 F5 意图保留）。"""
    _registry_draft(app)
    c = app.test_client()
    _login_as(c, _admin_id(app))
    r = c.get('/import/registry?status=draft')
    assert r.status_code == 200
    assert '家庭收入' in r.get_data(as_text=True)

    clerk = _mk_user(app, 'clerk036r', _perms(edit=True, special=True))
    c2 = app.test_client()
    _login_as(c2, clerk)
    r2 = c2.get('/import/registry')
    assert r2.status_code == 200
    assert '家庭收入' in r2.get_data(as_text=True)
    assert c2.post('/import/registry/家庭收入/approve').status_code == 403
    assert c2.post('/import/registry/家庭收入/reject').status_code == 403


def test_registry_approve_and_reject_wire_existing_functions(app):
    _registry_draft(app, '家庭收入')
    _registry_draft(app, '帮扶类型')
    c = app.test_client()
    _login_as(c, _admin_id(app))
    assert c.post('/import/registry/家庭收入/approve').status_code in (302, 200)
    assert c.post('/import/registry/帮扶类型/reject').status_code in (302, 200)

    from utils.import_engine import is_statistics_eligible
    from models import ImportFieldRegistry
    with app.app_context():
        assert ImportFieldRegistry.query.filter_by(code='家庭收入').first().status == 'approved'
        assert ImportFieldRegistry.query.filter_by(code='帮扶类型').first().status == 'rejected'
        assert ImportFieldRegistry.query.filter_by(code='家庭收入').first().approved_by
        assert is_statistics_eligible('家庭收入') is True
        assert is_statistics_eligible('帮扶类型') is False
