# -*- coding: utf-8 -*-
"""042 证明开具模块迁移（exe V2.45.2 → 仓库）专项测试。

覆盖验收：
1 模板闭环（新增/编辑/停用/删除；系统模板不可删）
2 开具闭环（居民检索 → ZM 编号 → 记录）
3 记录闭环（筛选/详情/作废）
4 Word 导出（可打开、占位符全替换）
5 旧端点去留（generate 302；save/preview/delete/list 404）
6 排除项（无电子公章表/端点/UI 字段）
7 权限（缺 certificate.edit/export 403）
"""
import io
import json
import os
import sqlite3
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'shucun.db')
    import config
    for key, val in {
        'SHUCUN_DATA_DIR': str(tmp_path / 'data'),
        'SHUCUN_DB_PATH': db_path,
        'SHUCUN_UPLOAD_DIR': str(tmp_path / 'uploads'),
        'SHUCUN_BACKUP_DIR': str(tmp_path / 'backups'),
        'SHUCUN_LOG_DIR': str(tmp_path / 'logs'),
        'SHUCUN_TEMP_DIR': str(tmp_path / 'temp'),
        'SHUCUN_LICENSE_FILE': str(tmp_path / 'license.key'),
        'SHUCUN_DISABLE_PASSWORD_CHANGE': '1',
        'SHUCUN_DISABLE_PERIODIC': '1',
        'SHUCUN_DISABLE_CSRF': '1',
    }.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config.Config, 'SQLALCHEMY_DATABASE_URI',
                        f'sqlite:///{db_path}?check_same_thread=False')
    monkeypatch.setattr(config, 'TEMP_DIR', str(tmp_path / 'temp'))
    monkeypatch.setattr(config.Config, 'TEMP_FOLDER', str(tmp_path / 'temp'))
    for d in ('uploads', 'backups', 'logs', 'temp', 'data'):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    from utils.crypto import key_holder
    with application.app_context():
        if not key_holder.is_initialized:
            key_holder.initialize('test-secret-key-0123456789abcdef')
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


def _login_as(client, user_id):
    with client.session_transaction() as s:
        s['user_id'] = user_id


def _admin_id(app):
    from models import User
    with app.app_context():
        return User.query.filter_by(username='admin').first().id


def _perms(**over):
    p = {'__schema_version__': 3,
         'certificate.view': True, 'certificate.add': True,
         'certificate.edit': True, 'certificate.delete': True,
         'certificate.export': True,
         'special': {'view_sensitive': False, 'death_confirm': False,
                     'import_batch': False, 'backup_restore': False,
                     'mask_config': False}}
    p.update(over)
    return p


def _mk_user(app, username, perms):
    from models import db, Role, User
    with app.app_context():
        role = Role(name='role_' + username, permissions=json.dumps(perms, ensure_ascii=False))
        db.session.add(role)
        db.session.commit()
        u = User(username=username, role_id=role.id, is_active=True)
        u.set_password('TestPass123')
        db.session.add(u)
        db.session.commit()
        return u.id


def _mk_resident(app, name='张三', status='normal'):
    from models import db, Resident
    from utils.crypto import sha256_hash
    with app.app_context():
        r = Resident(name=name, gender='男', id_card='11010519491231002X',
                     id_card_hash=sha256_hash('11010519491231002X'),
                     address='思榜村1组', village_group='思榜', grid='1',
                     status=status)
        db.session.add(r)
        db.session.commit()
        return r.id


def _template_payload(name='测试模板', cert_type='测试证明'):
    return {
        'name': name, 'cert_type': cert_type,
        'template_content': '<p style="text-indent:2em;">兹证明 {{姓名}}，身份证号 {{身份证号}}。</p>',
        'field_schema': [
            {'key': '姓名', 'type': 'resident', 'required': True},
            {'key': '身份证号', 'type': 'resident', 'required': True}],
        'page_settings': {'orientation': 'portrait', 'font_size': 16},
        'is_active': True,
    }


# ---------------- 迁移与种子 ----------------

def test_migration_idempotent_and_seed(app, tmp_path):
    from utils.certificate_catalog import ensure_certificate_seed
    from models import CertificateTemplate
    with app.app_context():
        assert CertificateTemplate.query.count() == 3
        assert ensure_certificate_seed() is True
        assert CertificateTemplate.query.count() == 3  # 二次补种不重复

    # 旧 5 列库跑两次 m015：第一次加列+建表+种子，第二次全部跳过
    old_db = str(tmp_path / 'old.db')
    conn = sqlite3.connect(old_db)
    conn.execute('CREATE TABLE certificate_templates (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                 'name VARCHAR(100) NOT NULL, cert_type VARCHAR(50), template_content TEXT, '
                 'is_custom BOOLEAN, created_at DATETIME, updated_at DATETIME)')
    conn.commit()
    conn.close()
    from scripts.migrations.m015_certificate_full import run
    first = run(old_db)
    second = run(old_db)
    assert len(first['added_columns']) == 6 and len(first['seeded']) == 3
    assert second['added_columns'] == [] and second['seeded'] == []
    conn = sqlite3.connect(old_db)
    cols = {row[1] for row in conn.execute('PRAGMA table_info(certificate_templates)')}
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    count = conn.execute('SELECT COUNT(*) FROM certificate_templates').fetchone()[0]
    conn.close()
    assert {'field_schema', 'page_settings', 'is_active', 'version',
            'created_by', 'updated_by'} <= cols
    assert 'certificate_issues' in tables and 'certificate_seals' not in tables
    assert count == 3


# ---------------- 页面与排除项 ----------------

def test_page_and_no_seal(app):
    c = app.test_client()
    _login_as(c, _admin_id(app))
    r = c.get('/certificate/')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert '证明开具' in html and '模板管理' in html and '开具记录' in html
    assert 'cert-seal' not in html and '电子公章' not in html

    from models import db
    with app.app_context():
        from sqlalchemy import text
        tables = {row[0] for row in db.session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert 'certificate_seals' not in tables
    assert c.get('/certificate/api/seals').status_code in (404, 403)


# ---------------- 模板闭环 ----------------

def test_template_crud_and_system_guard(app):
    c = app.test_client()
    _login_as(c, _admin_id(app))

    r = c.post('/certificate/api/template/create', json=_template_payload())
    assert r.status_code == 200 and r.get_json()['success']
    item = r.get_json()['data']
    tid = item['id']
    assert item['version'] == 1 and item['is_custom'] is True

    r = c.post(f'/certificate/api/template/update/{tid}',
               json={**_template_payload('测试模板V2'), 'is_active': True})
    assert r.get_json()['data']['version'] == 2
    assert r.get_json()['data']['name'] == '测试模板V2'

    r = c.post(f'/certificate/api/template/status/{tid}')
    assert r.get_json()['data']['is_active'] is False
    r = c.post(f'/certificate/api/template/status/{tid}')
    assert r.get_json()['data']['is_active'] is True

    r = c.get('/certificate/api/templates?include_inactive=1')
    assert any(t['id'] == tid for t in r.get_json()['data'])
    # 3 个系统模板不可删
    sys_tpl = [t for t in r.get_json()['data'] if t['is_custom'] is False][0]
    assert c.post(f"/certificate/api/template/delete/{sys_tpl['id']}").get_json()['success'] is False

    assert c.post(f'/certificate/api/template/delete/{tid}').get_json()['success'] is True
    assert c.get(f'/certificate/api/template/{tid}').get_json()['success'] is False


# ---------------- 开具 / 记录 / 导出 ----------------

def test_issue_record_and_word(app):
    rid = _mk_resident(app)
    c = app.test_client()
    _login_as(c, _admin_id(app))

    tpl = [t for t in c.get('/certificate/api/templates').get_json()['data']
           if t['cert_type'] == '居住证明'][0]
    r = c.get('/certificate/api/residents?keyword=张三')
    assert r.get_json()['data'][0]['address'] == '思榜村1组'

    r = c.post('/certificate/api/issue/create', json={
        'template_id': tpl['id'],
        'values': {'姓名': '张三', '性别': '男', '身份证号': '11010519491231002X',
                   '家庭住址': '思榜村1组', '联系电话': '13800000000'},
        'resident_ids': {'primary': rid},
        'issuer_name': '经办员', 'issue_unit': '思榜村委会',
        'issue_date': '2026-10-21', 'purpose': '入学', 'copies': 2,
    })
    data = r.get_json()['data']
    assert r.get_json()['success'] and data['serial_no'].startswith('ZM')
    assert len(data['serial_no']) == 14 and data['serial_no'][-4:].isdigit()
    assert '{{' not in data['rendered_content']
    assert '2026年10月21日' in data['rendered_content']
    issue_id = data['id']

    r = c.get('/certificate/api/issues?keyword=' + data['serial_no'])
    body = r.get_json()['data']
    assert body['total'] == 1 and body['items'][0]['status'] == 'issued'

    r = c.post(f'/certificate/api/issue/void/{issue_id}', json={'reason': '信息有误'})
    assert r.get_json()['data']['status'] == 'voided'
    assert c.post(f'/certificate/api/issue/void/{issue_id}',
                  json={'reason': '重复'}).get_json()['success'] is False

    r = c.get(f'/certificate/api/issue/word/{issue_id}')
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    xml = zf.read('word/document.xml').decode('utf-8')
    assert '{{' not in xml and '______' not in xml
    assert '张三' in xml and '2026年10月21日' in xml

    # 有记录的模板不可删除
    assert c.post(f"/certificate/api/template/delete/{tpl['id']}").get_json()['success'] is False

    # 旧端点去留
    assert c.post('/certificate/api/generate', data={'issue_id': issue_id}).status_code == 302
    for path in ('/certificate/api/save', '/certificate/api/preview',
                 '/certificate/api/list'):
        assert c.get(path).status_code in (404, 405)
    assert c.post('/certificate/api/delete/1').status_code in (404, 405)


def test_residents_filter_dead_and_moved(app):
    _mk_resident(app, '在世', 'normal')
    _mk_resident(app, '已故', 'deceased')
    _mk_resident(app, '迁出', 'moved_out')
    c = app.test_client()
    _login_as(c, _admin_id(app))
    names = {r['name'] for r in c.get('/certificate/api/residents?keyword=').get_json().get('data', [])}
    assert names == set()  # 空关键字不返回
    for kw, expect in (('在世', True), ('已故', False), ('迁出', False)):
        data = c.get(f'/certificate/api/residents?keyword={kw}').get_json()['data']
        assert any(x['name'] == kw for x in data) is expect


# ---------------- 权限 ----------------

def test_permissions_edit_export(app):
    viewer = _mk_user(app, 'viewer042', _perms(
        **{'certificate.add': False, 'certificate.edit': False,
           'certificate.delete': False, 'certificate.export': False}))
    c = app.test_client()
    _login_as(c, viewer)
    assert c.get('/certificate/').status_code == 200
    assert c.post('/certificate/api/template/create', json=_template_payload()).status_code == 403
    assert c.post('/certificate/api/template/status/1').status_code == 403
    assert c.post('/certificate/api/issue/void/1', json={'reason': 'x'}).status_code == 403
    assert c.get('/certificate/api/issue/word/999').status_code == 403

    no_export = _mk_user(app, 'editor042', _perms(**{'certificate.export': False}))
    c2 = app.test_client()
    _login_as(c2, no_export)
    assert c2.get('/certificate/api/issue/word/999').status_code == 403


# ---------------- 043 返工：脱敏 / 启动种子 / 手工开具 ----------------

VALID_ID = '11010519491231002X'
BAD_CHECKSUM_ID = '110105194912310021'


def _issue_payload_for(template_id, name='张三', id_card=VALID_ID):
    return {
        'template_id': template_id,
        'values': {'姓名': name, '性别': '男', '身份证号': id_card,
                   '家庭住址': '思榜村1组', '联系电话': '13800000000'},
        'issuer_name': '经办员', 'issue_unit': '思榜村委会',
        'issue_date': '2026-10-21',
    }


def _first_template(client, cert_type='居住证明'):
    return [t for t in client.get('/certificate/api/templates').get_json()['data']
            if t['cert_type'] == cert_type][0]


def test_detail_and_create_mask_without_sensitive(app):
    _mk_resident(app)
    admin = app.test_client()
    _login_as(admin, _admin_id(app))
    tpl = _first_template(admin)
    r = admin.post('/certificate/api/issue/create', json=_issue_payload_for(tpl['id']))
    assert r.get_json()['success']
    issue_id = r.get_json()['data']['id']
    # ① admin 基线：明文
    assert VALID_ID in r.get_json()['data']['rendered_content']
    assert r.get_json()['data']['form_data']['身份证号'] == VALID_ID

    viewer = _mk_user(app, 'viewer_mask043', _perms())
    v = app.test_client()
    _login_as(v, viewer)
    # ② 无 view_sensitive：详情全链路掩码
    d = v.get(f'/certificate/api/issue/{issue_id}').get_json()['data']
    assert VALID_ID not in d['form_data']['身份证号']
    assert VALID_ID not in d['rendered_content']
    assert VALID_ID not in d['subject_id_card']
    # ③ 开具响应同样掩码
    r2 = v.post('/certificate/api/issue/create', json=_issue_payload_for(tpl['id']))
    assert r2.get_json()['success']
    d2 = r2.get_json()['data']
    assert VALID_ID not in d2['form_data']['身份证号']
    assert VALID_ID not in d2['rendered_content']
    assert VALID_ID not in d2['subject_id_card']


def test_seed_only_on_process_start(tmp_path, monkeypatch):
    import utils.certificate_catalog as catalog
    calls = []
    monkeypatch.setattr(catalog, 'ensure_certificate_seed',
                        lambda: calls.append(1) or True)
    _make_app(tmp_path, monkeypatch)
    assert len(calls) == 1  # 进程启动跑一次
    from app import create_app
    create_app(skip_startup_hooks=True)
    assert len(calls) == 1  # 后台任务级 create_app 不再写库


def test_manual_issue_without_resident(app):
    from models import CertificateIssue
    c = app.test_client()
    _login_as(c, _admin_id(app))
    tpl = _first_template(c)

    r = c.post('/certificate/api/issue/create',
               json=_issue_payload_for(tpl['id'], name='手工李四'))
    data = r.get_json()['data']
    assert r.get_json()['success']
    assert data['resident_id'] is None
    assert '手工李四' in data['rendered_content']
    assert VALID_ID in data['subject_id_card']

    bad = c.post('/certificate/api/issue/create',
                 json=_issue_payload_for(tpl['id'], name='校验位错', id_card=BAD_CHECKSUM_ID))
    assert bad.status_code == 400
    assert bad.get_json()['success'] is False
    with app.app_context():
        assert CertificateIssue.query.count() == 1


def test_binding_released_on_manual_edit(app):
    rid = _mk_resident(app, '张三')
    c = app.test_client()
    _login_as(c, _admin_id(app))
    tpl = _first_template(c)

    payload = _issue_payload_for(tpl['id'], name='李四')
    payload['resident_ids'] = {'primary': rid}
    r = c.post('/certificate/api/issue/create', json=payload)
    assert r.get_json()['success']
    assert r.get_json()['data']['resident_id'] is None  # 改动姓名即解绑

    payload = _issue_payload_for(tpl['id'], name='张三')
    payload['resident_ids'] = {'primary': rid}
    r = c.post('/certificate/api/issue/create', json=payload)
    assert r.get_json()['data']['resident_id'] == rid  # 一致则保留关联


# ---------------- 044 返工：编号重试 / 解绑双比对 ----------------

OTHER_VALID_ID = '110105194912310038'


def test_serial_retry_on_collision(app, monkeypatch):
    from datetime import datetime
    from models import CertificateIssue, OperationLog, db
    import routes.certificate as cert_routes
    c = app.test_client()
    _login_as(c, _admin_id(app))
    tpl = _first_template(c)

    occupied = 'ZM' + datetime.now().strftime('%Y%m%d') + '0001'
    with app.app_context():
        db.session.add(CertificateIssue(
            serial_no=occupied, template_name='占位', subject_name='占位',
            issued_at=datetime.now()))
        db.session.commit()

    # 模拟并发：第一次取号返回已被抢占的编号，重试时恢复正常取号
    original = cert_routes._next_serial_no
    calls = {'n': 0}

    def pick_serial(now):
        calls['n'] += 1
        return occupied if calls['n'] == 1 else original(now)

    monkeypatch.setattr(cert_routes, '_next_serial_no', pick_serial)

    r = c.post('/certificate/api/issue/create', json=_issue_payload_for(tpl['id']))
    assert r.status_code == 200 and r.get_json()['success']
    serial_no = r.get_json()['data']['serial_no']
    assert serial_no != occupied and serial_no.endswith('0002')
    with app.app_context():
        logs = OperationLog.query.filter_by(module='证明开具', action='add').all()
        assert len(logs) == 1
        assert serial_no in (logs[0].detail or '')


def test_binding_released_when_id_differs(app):
    rid = _mk_resident(app, '张三')
    c = app.test_client()
    _login_as(c, _admin_id(app))
    tpl = _first_template(c)

    payload = _issue_payload_for(tpl['id'], name='张三', id_card=OTHER_VALID_ID)
    payload['resident_ids'] = {'primary': rid}
    r = c.post('/certificate/api/issue/create', json=payload)
    assert r.get_json()['success']
    assert r.get_json()['data']['resident_id'] is None  # 身份证不一致即解绑

    payload = _issue_payload_for(tpl['id'], name='张三')
    payload['resident_ids'] = {'primary': rid}
    r = c.post('/certificate/api/issue/create', json=payload)
    assert r.get_json()['data']['resident_id'] == rid  # 姓名 + 身份证一致才保留
