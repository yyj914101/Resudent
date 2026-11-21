# -*- coding: utf-8 -*-
"""tests/test_092_attachments.py — 092 模板附件区（通用附件表 + 三模块接入）

覆盖（docs/tasks/092-template-attachments.md 验收标准中可自动化部分）：
A. 全链路：上传 → 列表 → 下载 → 删除（disabled 模块），文件名/MIME/大小正确，
   删除后落盘文件清除
B. 三模块可达入口：disabled 页 / care 页 / subsidy projects 页均含「办事流程模板」
   section 与 initAttachments 挂载；projects 页补「补贴登记」反向可达链接；
   care/subsidy 轻量全链路
C. 跨模块 403 反例：disabled 用户读 care 附件（① 走 care 端点被模块位拦；
   ② 走 disabled 端点被 service 归属校验拦，两道防线各自 403）；跨模块删除同拒
D. 白名单反例：.exe 拒绝；扩展名白名单内但 content_type 不符拒绝；均不落盘不落库；
   MIME 兼容表内（octet-stream / x-pdf）放行
E. 超限反例：ATTACHMENT_MAX_SIZE 调小后超限拒绝且不落盘；默认上限 = 10MB
F. 穿越反例：module_key '..' 拒绝；伪造 stored_name '../evil.txt' 拒绝；
   原始文件名清洗去路径分隔符；落盘名恒为 uuid hex + 白名单扩展名
G. 未登录 401 / 无模块位 403（JSON 反例）
H. m036 幂等：空库首跑建表建索引、二跑零变更；对 create_all 库重放数据无损
I. 收口断言（验收 4 的自动化比对）：三蓝图均调用同一 attachment_service
   （save/resolve_path/delete），无本地复制实现；三模板均挂 module_attachments.js
"""
import importlib.util
import io
import json
import os
import sqlite3

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_M036_PATH = os.path.join(_ROOT, 'scripts', 'migrations', 'm036_module_attachment.py')

PDF_BYTES = b'%PDF-1.4\n% 092 template attachment test\n'


# ------------------------------------------------------------------ 夹具/工具

def _load_m036():
    """importlib 加载迁移脚本（scripts/ 非包；test_099 同款方式）。"""
    spec = importlib.util.spec_from_file_location('m036_module_attachment_under_test', _M036_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('attachments-test', salt=generate_salt())
    return application


def _login(client, user_id, username):
    with client.session_transaction() as sess:
        sess['user_id'] = user_id
        sess['username'] = username


def _make_role_user(app, username, permissions):
    """建角色 + 用户（默认无任何模块权限，除非 permissions 给出）。返回 user.id。"""
    with app.app_context():
        from models import Role, User, db
        role = Role(name=('role_%s' % username), description='092 用例',
                    permissions=json.dumps(permissions))
        db.session.add(role)
        db.session.commit()
        user = User(username=username, role_id=role.id, is_active=True)
        user.set_password('TestPass123')
        db.session.add(user)
        db.session.commit()
        return user.id


PERM_ALL = {
    'disabled.view': True, 'disabled.add': True, 'disabled.delete': True,
    'care.view': True, 'care.add': True, 'care.delete': True,
    'subsidy.view': True, 'subsidy.add': True, 'subsidy.delete': True,
}
PERM_DISABLED = {'disabled.view': True, 'disabled.add': True, 'disabled.delete': True}


def _upload(client, module, name='办事流程.pdf', content=PDF_BYTES,
            content_type='application/pdf'):
    return client.post('/%s/api/attachments/upload' % module,
                       data={'file': (io.BytesIO(content), name, content_type)},
                       content_type='multipart/form-data')


def _att_files_root(app):
    """uploads/attachments 根目录（隔离副本内）。落盘规范：attachments/<module>/<yyyyMM>/"""
    import config as cfg
    return os.path.join(cfg.Config.UPLOAD_FOLDER, 'attachments')


def _disk_files(app):
    root = _att_files_root(app)
    out = []
    for dirpath, _dirs, files in os.walk(root):
        out.extend(os.path.join(dirpath, f) for f in files)
    return out


def _row_count(app):
    with app.app_context():
        from models import ModuleAttachment, db
        return db.session.query(ModuleAttachment).count()


# ------------------------------------------------------------------ A. 全链路

def test_a_upload_list_download_delete_full_chain(app):
    uid = _make_role_user(app, 'ops_all', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_all')

    # 上传（中文原始文件名）
    resp = _upload(client, 'disabled', name='残疾补贴办事流程.pdf')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['success'] is True, body
    att_id = body['data']['id']
    assert body['data']['original_name'] == '残疾补贴办事流程.pdf'

    # 落盘规范：attachments/disabled/<yyyyMM>/<32hex>.pdf；原始名不落盘
    files = _disk_files(app)
    assert len(files) == 1
    stored_base = os.path.basename(files[0])
    assert stored_base.endswith('.pdf') and len(stored_base.split('.')[0]) == 32
    assert '残疾补贴办事流程' not in files[0]

    # 列表：文件名/大小/MIME/上传人正确
    resp = client.get('/disabled/api/attachments')
    rows = resp.get_json()['data']['data']
    assert len(rows) == 1
    row = rows[0]
    assert row['id'] == att_id
    assert row['original_name'] == '残疾补贴办事流程.pdf'
    assert row['ext'] == 'pdf'
    assert row['mime'] == 'application/pdf'
    assert row['size'] == len(PDF_BYTES)
    assert row['uploaded_by'] == 'ops_all'

    # 下载：字节一致 + 附件形响应 + MIME 正确
    resp = client.get('/disabled/api/attachments/%d/download' % att_id)
    assert resp.status_code == 200
    assert resp.data == PDF_BYTES
    assert resp.headers['Content-Type'].startswith('application/pdf')
    assert 'attachment' in resp.headers.get('Content-Disposition', '')
    resp.close()   # Windows：send_file 句柄随响应关闭，避免删除时 WinError 32

    # 删除：记录 + 落盘文件均清除
    resp = client.post('/disabled/api/attachments/%d/delete' % att_id)
    assert resp.get_json()['success'] is True, resp.get_data(as_text=True)
    assert _row_count(app) == 0
    assert _disk_files(app) == []


# ------------------------------------------------------------------ B. 三模块可达

def test_b_three_modules_page_sections_and_link(app):
    uid = _make_role_user(app, 'ops_page', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_page')

    resp = client.get('/disabled/')
    html = resp.get_data(as_text=True)
    assert '办事流程模板' in html and 'initAttachments(' in html and 'attFile' in html

    resp = client.get('/care/')
    html = resp.get_data(as_text=True)
    assert '办事流程模板' in html and 'initAttachments(' in html and 'attFile' in html

    # subsidy 落 projects 页（subsidy 不在侧栏，唯一入口是首页按钮）
    resp = client.get('/subsidy/projects')
    html = resp.get_data(as_text=True)
    assert '办事流程模板' in html and 'initAttachments(' in html and 'attFile' in html
    # 工作项 3：补「补贴登记」入口旁的可达性（projects 页反向链接）
    assert '补贴登记' in html


def test_b_care_and_subsidy_light_full_chain(app):
    uid = _make_role_user(app, 'ops_light', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_light')

    for module, name, mime in (
            ('care', '关爱服务流程.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('subsidy', '补贴发放流程表.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')):
        resp = _upload(client, module, name=name, content=b'PK\x03\x04 xlsx bytes',
                       content_type=mime)
        assert resp.get_json()['success'] is True, (module, resp.get_json())
        att_id = resp.get_json()['data']['id']

        resp = client.get('/%s/api/attachments' % module)
        rows = resp.get_json()['data']['data']
        assert len(rows) == 1 and rows[0]['original_name'] == name and rows[0]['module_key'] == module

        resp = client.get('/%s/api/attachments/%d/download' % (module, att_id))
        assert resp.status_code == 200 and resp.data == b'PK\x03\x04 xlsx bytes'
        resp.close()   # Windows：同上，避免下载句柄占用导致删除失败

        assert client.post('/%s/api/attachments/%d/delete' % (module, att_id)).get_json()['success']
    assert _row_count(app) == 0


# ------------------------------------------------------------------ C. 跨模块 403

def test_c_cross_module_read_and_delete_403(app):
    all_uid = _make_role_user(app, 'ops_cross_all', PERM_ALL)
    dis_uid = _make_role_user(app, 'ops_cross_dis', PERM_DISABLED)

    admin_client = app.test_client()
    _login(admin_client, all_uid, 'ops_cross_all')
    resp = _upload(admin_client, 'care', name='care 模块附件.pdf')
    care_att_id = resp.get_json()['data']['id']

    dis_client = app.test_client()
    _login(dis_client, dis_uid, 'ops_cross_dis')

    # ① 走 care 端点：模块位拦截（enforce_role_permissions，无 care.view → 403）
    resp = dis_client.get('/care/api/attachments')
    assert resp.status_code == 403
    resp = dis_client.get('/care/api/attachments/%d/download' % care_att_id)
    assert resp.status_code == 403

    # ② 走 disabled 端点读 care 附件：模块位已过，service 归属校验拦截（403 JSON）
    resp = dis_client.get('/disabled/api/attachments/%d/download' % care_att_id)
    assert resp.status_code == 403
    body = resp.get_json()
    assert body['success'] is False and '模块' in body['msg']

    # 跨模块删除同拒
    resp = dis_client.post('/disabled/api/attachments/%d/delete' % care_att_id)
    assert resp.status_code == 403
    assert _row_count(app) == 1  # 附件未被误删

    # 文件本体未被读到
    assert len(_disk_files(app)) == 1


# ------------------------------------------------------------------ D. 白名单

def test_d_whitelist_and_mime_double_check(app):
    uid = _make_role_user(app, 'ops_wl', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_wl')

    # 白名单外扩展名拒绝
    resp = _upload(client, 'disabled', name='tool.exe', content=b'MZ...',
                   content_type='application/octet-stream')
    assert resp.status_code == 400
    body = resp.get_json()
    assert body['success'] is False and 'pdf' in body['msg']

    # 白名单内但 content_type 不符（MIME 双检）拒绝
    resp = _upload(client, 'disabled', name='伪装.pdf', content=PDF_BYTES,
                   content_type='application/x-msdownload')
    assert resp.status_code == 400
    assert resp.get_json()['success'] is False

    # 无扩展名拒绝
    resp = _upload(client, 'disabled', name='README', content=b'x', content_type='text/plain')
    assert resp.status_code == 400

    # 拒绝即不落盘、不落库（验收 3）
    assert _disk_files(app) == []
    assert _row_count(app) == 0

    # MIME 兼容表放行：octet-stream（浏览器 office 兜底）/ x-pdf（历史别名）
    resp = _upload(client, 'disabled', name='流程2.pdf', content=PDF_BYTES,
                   content_type='application/x-pdf')
    assert resp.get_json()['success'] is True
    resp = _upload(client, 'care', name='流程3.docx',
                   content=b'PK\x03\x04 docx', content_type='application/octet-stream')
    assert resp.get_json()['success'] is True


# ------------------------------------------------------------------ E. 超限

def test_e_oversize_rejected_not_saved(app, monkeypatch):
    from config import Config
    assert Config.ATTACHMENT_MAX_SIZE == 10 * 1024 * 1024  # 默认 10MB（可配）
    monkeypatch.setattr(Config, 'ATTACHMENT_MAX_SIZE', 100)

    uid = _make_role_user(app, 'ops_big', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_big')

    resp = _upload(client, 'disabled', name='大文件.pdf', content=b'%PDF' + b'x' * 200)
    assert resp.status_code == 400
    body = resp.get_json()
    assert body['success'] is False and '上限' in body['msg']
    assert _disk_files(app) == []   # 超限拒绝且不落盘（验收 3）
    assert _row_count(app) == 0

    # 边界内正常
    resp = _upload(client, 'disabled', name='小文件.pdf', content=b'%PDF' + b'x' * 50)
    assert resp.get_json()['success'] is True


# ------------------------------------------------------------------ F. 穿越

def test_f_path_traversal_defences(app):
    uid = _make_role_user(app, 'ops_trav', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_trav')

    from utils import attachment_service as svc

    # module_key 进存储路径：'..' / 带分隔符一律 ValueError（不触盘）
    with app.app_context():
        with pytest.raises(ValueError):
            svc.save('..', None)
        for bad_key in ('..', 'disabled/../care', 'a/b'):
            f = io.BytesIO(PDF_BYTES)
            f.filename = 'x.pdf'
            with pytest.raises(ValueError):
                svc.save(bad_key, f)

    # 伪造 stored_name（直插库）：resolve_path 拒绝
    with app.app_context():
        from models import db, ModuleAttachment
        row = ModuleAttachment(module_key='disabled', original_name='evil.txt',
                               stored_name='../evil.txt', ext='txt', size=1)
        db.session.add(row)
        db.session.commit()
        evil_id = row.id
    resp = client.get('/disabled/api/attachments/%d/download' % evil_id)
    assert resp.status_code == 400   # ValueError → 400，穿越路径被拒（验收 3）
    assert resp.get_json()['success'] is False

    # 原始文件名清洗：去路径分隔符（原始名只入库不落盘，展示层另有 esc 单源转义）
    assert svc._clean_original_name('..\\..\\evil.txt') == 'evil.txt'
    assert svc._clean_original_name('../../evil.txt') == 'evil.txt'
    assert svc._clean_original_name('正常 流程模板.pdf') == '正常 流程模板.pdf'

    # 落盘名恒为本模块生成（uuid hex + 白名单扩展名）
    resp = _upload(client, 'disabled', name='../../流程模板.pdf')
    assert resp.get_json()['success'] is True
    files = _disk_files(app)
    assert len(files) == 1
    base = os.path.basename(files[0])
    assert base.endswith('.pdf') and len(base.split('.')[0]) == 32
    assert '..' not in files[0]


# ------------------------------------------------------------------ G. 未登录 / 无权限

def test_g_unauthenticated_and_no_permission(app):
    dis_uid = _make_role_user(app, 'ops_perm_dis', PERM_DISABLED)
    none_uid = _make_role_user(app, 'ops_perm_none', {})

    # 未登录：JSON 请求 401
    client = app.test_client()
    resp = client.get('/disabled/api/attachments')
    assert resp.status_code == 401
    resp = client.post('/disabled/api/attachments/upload',
                       data={'file': (io.BytesIO(PDF_BYTES), 'x.pdf')},
                       content_type='multipart/form-data')
    assert resp.status_code == 401

    # 无任何模块位：403
    client = app.test_client()
    _login(client, none_uid, 'ops_perm_none')
    resp = client.get('/disabled/api/attachments')
    assert resp.status_code == 403

    # 有 disabled 位无 subsidy 位：subsidy 端点 403
    client = app.test_client()
    _login(client, dis_uid, 'ops_perm_dis')
    resp = client.get('/subsidy/api/attachments')
    assert resp.status_code == 403


# ------------------------------------------------------------------ H. m036 幂等

def test_h_m036_idempotent_on_fresh_db(tmp_path):
    mod = _load_m036()
    db_path = str(tmp_path / 'fresh.db')

    stats1 = mod.run(db_path)
    assert stats1['table_created'] is True
    assert stats1['indexes_created'] == ['idx_module_attachments_module',
                                         'idx_module_attachments_target']

    stats2 = mod.run(db_path)   # 重放：零变更
    assert stats2['table_created'] is False
    assert stats2['indexes_created'] == [] and stats2['indexes_kept'] == 2

    conn = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_module_attachments%'")}
        assert names == {'idx_module_attachments_module', 'idx_module_attachments_target'}
    finally:
        conn.close()


def test_h_m036_replay_on_create_all_db_preserves_data(app):
    uid = _make_role_user(app, 'ops_replay', PERM_ALL)
    client = app.test_client()
    _login(client, uid, 'ops_replay')
    assert _upload(client, 'disabled').get_json()['success'] is True
    before = _row_count(app)
    assert before == 1

    mod = _load_m036()
    stats = mod.run(os.environ['SHUCUN_DB_PATH'])   # 对 create_all 库重放
    assert stats['table_created'] is False          # create_all 已建，不重建
    assert _row_count(app) == before                # 数据无损


# ------------------------------------------------------------------ I. 收口断言（验收 4）

def test_i_three_blueprints_delegate_to_single_service():
    """reviewer 比对项自动化：三蓝图均委托同一 attachment_service，无本地复制实现。

    代码评审整改（P1）后接线形态升级为 register_attachment_routes 工厂注册，
    四个端点处理体收口进 utils/attachment_service.py，路由层零本地拷贝。
    """
    for name in ('disabled', 'care', 'subsidy'):
        src = open(os.path.join(_ROOT, 'routes', '%s.py' % name), encoding='utf-8').read()
        assert 'from utils.attachment_service import' in src, name
        assert 'register_attachment_routes(' in src, name
        # 附件业务不得复制进路由：白名单/落盘/模型操作只许在 service
        assert 'ALLOWED_EXTS' not in src, name
        assert 'ModuleAttachment' not in src, name

    svc = open(os.path.join(_ROOT, 'utils', 'attachment_service.py'), encoding='utf-8').read()
    assert 'ALLOWED_EXTS' in svc and 'uuid.uuid4' in svc

    # 三模板均挂共用 JS（前端同禁三份复制）
    shared_js = os.path.join(_ROOT, 'static', 'js', 'pages', 'module_attachments.js')
    assert os.path.isfile(shared_js)
    for tpl in (os.path.join(_ROOT, 'templates', 'disabled', 'list.html'),
                os.path.join(_ROOT, 'templates', 'care', 'special_list.html'),
                os.path.join(_ROOT, 'templates', 'subsidy', 'project_list.html')):
        src = open(tpl, encoding='utf-8').read()
        assert 'module_attachments.js' in src and '办事流程模板' in src, tpl
