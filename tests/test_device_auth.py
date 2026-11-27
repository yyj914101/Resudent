# -*- coding: utf-8 -*-
"""tests/test_device_auth.py — 049 局域网设备准入回归。

覆盖（对齐 docs/tasks/049-device-admission.md 验收标准）：
- ADR-016 归类：device_handshake.* 入 PUBLIC_ENDPOINTS；device_mgmt 入 ADMIN_BLUEPRINTS
- 单机版不启用：回环正常 / 非回环被「局域网授权」先拦（设备层未介入）
- 局域网版拦截：无 token 403、回环豁免、豁免端点（/auth/login）放行
- 握手状态机：首次 pending + 回传凭证 → 审批 approved + token → revoked 立即失效
- 凭证防伪：篡改 / 机器码不匹配 / reissue 后旧凭证失效
- 持久化：白名单跨 create_app 存活，会话 token 不存活（重启需重握手）
"""
import json

import os

import pytest

EXT = {'REMOTE_ADDR': '192.168.1.50'}      # 模拟局域网内的另一台机器
MACHINE = 'A1B2C3D4E5F60718'


def _make_app(tmp_path, monkeypatch, admission=True, lan_authorized=True):
    db_path = str(tmp_path / "shucun.db")
    import config
    monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SHUCUN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("SHUCUN_LICENSE_FILE", str(tmp_path / "license.key"))
    if lan_authorized:
        monkeypatch.setenv("SHUCUN_LAN_AUTHORIZED", "1")   # 模拟「局域网版已授权」
    else:
        monkeypatch.delenv("SHUCUN_LAN_AUTHORIZED", raising=False)
    if admission:
        monkeypatch.setenv("SHUCUN_DEVICE_ADMISSION", "1")
    else:
        monkeypatch.delenv("SHUCUN_DEVICE_ADMISSION", raising=False)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    # DATA_DIR 同步覆写：config 在首次 import 时固化该模块变量，只 setenv 不覆写会让
    # 后续测试共享同一份 device_key.pem.enc（密钥损坏反例会污染别的用例，对齐
    # test_template_smoke._setup_env 的隔离基线）。
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    for d in ("uploads", "backups", "logs", "temp", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    from utils import device_auth
    device_auth.reset_cache()
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch, admission=True)


@pytest.fixture()
def app_single(tmp_path, monkeypatch):
    """单机版：不启用准入，也没有局域网授权。"""
    return _make_app(tmp_path, monkeypatch, admission=False, lan_authorized=False)


def _admin_id(app):
    from models import User
    with app.app_context():
        return User.query.filter_by(username='admin').first().id


def _admin_client(app):
    c = app.test_client()
    uid = _admin_id(app)
    with c.session_transaction() as s:
        s['user_id'] = uid
        s['username'] = 'admin'
    return c


def _handshake(client, machine=MACHINE, credential=None, label='测试机'):
    body = {'machine_code': machine, 'label': label}
    if credential is not None:
        body['credential'] = credential
    return client.post('/device/handshake', json=body, environ_base=EXT)


class TestClassification:
    def test_public_endpoints_listed(self):
        from utils.permissions import PUBLIC_ENDPOINTS
        assert 'device_handshake.handshake' in PUBLIC_ENDPOINTS
        assert 'device_handshake.status' in PUBLIC_ENDPOINTS

    def test_admin_blueprint_listed(self):
        from utils.permissions import ADMIN_BLUEPRINTS
        assert 'device_mgmt' in ADMIN_BLUEPRINTS

    def test_non_admin_denied(self, app):
        from models import db, Role, User
        with app.app_context():
            role = Role(name='普通角色X')
            db.session.add(role)
            db.session.commit()
            u = User(username='plainx', role_id=role.id, is_active=True)
            u.set_password('TestPass123')
            db.session.add(u)
            db.session.commit()
            uid = u.id
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = uid
            s['username'] = 'plainx'
        assert c.get('/device-mgmt/').status_code == 403
        assert c.get('/device-mgmt/api/list').status_code == 403


class TestSingleEdition:
    def test_loopback_not_blocked(self, app_single):
        """单机版：回环访问走原流程（302 去登录），设备层不介入。"""
        c = app_single.test_client()
        r = c.get('/dashboard/')
        assert r.status_code in (302, 401)

    def test_external_blocked_by_license_first(self, app_single):
        """单机版：非回环先被「局域网授权」拦，文案不是设备准入页。"""
        c = app_single.test_client()
        r = c.get('/dashboard/', environ_base=EXT)
        assert r.status_code == 403
        body = r.get_data(as_text=True)
        assert '设备' not in body and '局域网' in body


class TestInterception:
    def test_external_without_token_denied(self, app):
        c = app.test_client()
        r = c.get('/dashboard/', environ_base=EXT)
        assert r.status_code == 403
        assert '本设备尚未通过准入' in r.get_data(as_text=True)
        assert '192.168.1.50' in r.get_data(as_text=True)

    def test_external_json_endpoint_denied(self, app):
        c = app.test_client()
        r = c.get('/dict/api/bundle', environ_base=EXT)
        assert r.status_code == 403
        assert r.get_json()['success'] is False

    def test_loopback_exempt(self, app):
        c = app.test_client()
        r = c.get('/dashboard/')
        assert r.status_code in (302, 401)          # 不是设备 403

    def test_exempt_login_page(self, app):
        c = app.test_client()
        assert c.get('/auth/login', environ_base=EXT).status_code == 200

    def test_exempt_screen_endpoint(self, app):
        """大屏端点豁免设备准入（自带 token 自校验 → 无 token 走它自己的 403）。"""
        r = app.test_client().get('/screen/', environ_base=EXT)
        assert r.status_code == 403
        assert '本设备尚未通过准入' not in r.get_data(as_text=True)


class TestHandshakeFlow:
    def test_first_handshake_pending_returns_credential(self, app):
        r = _handshake(app.test_client())
        assert r.status_code == 200
        data = r.get_json()['data']
        assert data['status'] == 'pending'
        assert data['credential']['machine'] == MACHINE
        assert data['credential']['sign']
        assert 'token' not in data

    def test_pending_cannot_get_token(self, app):
        c = app.test_client()
        _handshake(c)
        r = _handshake(c)
        assert r.get_json()['data']['status'] == 'pending'
        assert 'token' not in r.get_json()['data']

    def test_approved_needs_credential(self, app):
        c = app.test_client()
        cred = _handshake(c).get_json()['data']['credential']
        _admin_client(app).post('/device-mgmt/api/1/approve')
        r = _handshake(c)                       # 带机器码但不带凭证
        assert r.status_code == 200
        assert r.get_json()['success'] is False
        assert '缺少设备凭证' in r.get_json()['msg']
        # 带凭证则放行
        r2 = _handshake(c, credential=cred)
        assert r2.get_json()['data']['status'] == 'approved'
        assert r2.get_json()['data']['token']

    def test_token_grants_access(self, app):
        c = app.test_client()
        cred = _handshake(c).get_json()['data']['credential']
        _admin_client(app).post('/device-mgmt/api/1/approve')
        token = _handshake(c, credential=cred).get_json()['data']['token']
        r = c.get('/dashboard/', environ_base=EXT, headers={'X-Device-Token': token})
        assert r.status_code in (302, 401)      # 过设备层，落到登录重定向

    def test_revoke_kills_token_immediately(self, app):
        c = app.test_client()
        cred = _handshake(c).get_json()['data']['credential']
        admin = _admin_client(app)
        admin.post('/device-mgmt/api/1/approve')
        token = _handshake(c, credential=cred).get_json()['data']['token']
        assert c.get('/dashboard/', environ_base=EXT,
                     headers={'X-Device-Token': token}).status_code in (302, 401)
        admin.post('/device-mgmt/api/1/revoke')
        r = c.get('/dashboard/', environ_base=EXT, headers={'X-Device-Token': token})
        assert r.status_code == 403
        assert '本设备尚未通过准入' in r.get_data(as_text=True)


class TestCredentialSecurity:
    def _approved(self, app):
        c = app.test_client()
        cred = _handshake(c).get_json()['data']['credential']
        _admin_client(app).post('/device-mgmt/api/1/approve')
        return c, cred

    def test_tampered_credential_rejected(self, app):
        c, cred = self._approved(app)
        bad = dict(cred, label='被篡改')
        r = _handshake(c, credential=bad)
        assert r.get_json()['success'] is False
        assert '签名' in r.get_json()['msg']

    def test_credential_from_other_machine_rejected(self, app):
        """把 A 的凭证拿到已登记的 B 机器上用 → 验签通过但机器码不匹配。"""
        c, cred = self._approved(app)
        other = 'BBBBBBBBBBBBBBBB'
        _handshake(c, machine=other)
        admin = _admin_client(app)
        from models import DeviceRegistry
        with app.app_context():
            other_id = DeviceRegistry.query.filter_by(machine_code=other).first().id
        admin.post(f'/device-mgmt/api/{other_id}/approve')
        r = _handshake(c, machine=other, credential=cred)
        assert r.get_json()['success'] is False
        assert '机器码' in r.get_json()['msg']

    def test_reissue_invalidates_old_credential(self, app):
        c, cred = self._approved(app)
        r = _admin_client(app).post('/device-mgmt/api/1/reissue')
        new_cred = r.get_json()['data']['credential']
        old = _handshake(c, credential=cred)
        assert old.get_json()['success'] is False
        assert '已失效' in old.get_json()['msg']
        new = _handshake(c, credential=new_cred)
        assert new.get_json()['data']['status'] == 'approved'

    def test_bad_machine_code_rejected(self, app):
        r = _handshake(app.test_client(), machine='ZZZ')
        assert r.get_json()['success'] is False
        assert '机器码格式' in r.get_json()['msg']


class TestPersistence:
    def test_whitelist_survives_restart_token_does_not(self, tmp_path, monkeypatch):
        app1 = _make_app(tmp_path, monkeypatch, admission=True)
        c = app1.test_client()
        cred = _handshake(c).get_json()['data']['credential']
        _admin_client(app1).post('/device-mgmt/api/1/approve')
        token = _handshake(c, credential=cred).get_json()['data']['token']

        app2 = _make_app(tmp_path, monkeypatch, admission=True)     # 同库新进程
        from models import DeviceRegistry
        with app2.app_context():
            row = DeviceRegistry.query.filter_by(machine_code=MACHINE).first()
            assert row is not None and row.status == 'approved'
        c2 = app2.test_client()
        # 白名单还在 → 带凭证可重新握手；旧 token 属旧进程内存 → 失效
        assert c2.get('/dashboard/', environ_base=EXT,
                      headers={'X-Device-Token': token}).status_code == 403
        again = _handshake(c2, credential=cred)
        assert again.get_json()['data']['status'] == 'approved'


class TestKeyRobustness:
    """050 R-050-03：密钥落盘原子性 + 解密失败可控（HTTP 层反例见 test_device_screen_isolation.py）。"""

    def test_corrupt_key_raises_readable_error(self, tmp_path, monkeypatch):
        app = _make_app(tmp_path, monkeypatch, admission=True)
        from utils import device_auth
        with app.app_context():
            path = device_auth.ensure_keypair()
            with open(path, 'rb') as fh:
                raw = fh.read()
            with open(path, 'wb') as fh:                # 半截密文
                fh.write(raw[: max(1, len(raw) // 2)])
            device_auth.reset_cache()
            with pytest.raises(device_auth.DeviceKeyCorruptedError) as ei:
                device_auth._private_key()
        assert '设备密钥损坏' in str(ei.value)

    def test_ensure_keypair_atomic_write(self):
        """源级守卫：禁止直写最终路径，必须 tmp + os.replace（对齐验收 6 的 grep 判定）。"""
        import utils.device_auth as mod
        src = open(mod.__file__, encoding='utf-8').read()
        assert 'os.replace' in src
        assert "open(path, 'wb')" not in src

    def test_concurrent_first_issue_no_error_and_single_key(self, tmp_path, monkeypatch):
        """N-050-01：并发首次签发不得抛异常，且只落一套密钥、无临时文件残留。

        原缺陷：临时文件用固定名 `path + '.tmp'`，一方持有写句柄时另一方
        `open(tmp,'wb')` 抛 PermissionError，且未被任何一层捕获 → 500。
        """
        import threading

        app = _make_app(tmp_path, monkeypatch, admission=True)
        from utils import device_auth
        with app.app_context():
            path = device_auth._key_path()
            key_dir = os.path.dirname(path)
            errors = []
            for _ in range(10):                      # 多轮提高并发竞争命中率
                if os.path.exists(path):
                    os.remove(path)
                device_auth.reset_cache()
                barrier = threading.Barrier(8)

                def run():
                    try:
                        barrier.wait(timeout=5)
                        device_auth.ensure_keypair()
                    except Exception as exc:         # noqa: BLE001
                        errors.append(f'{type(exc).__name__}: {exc}')

                threads = [threading.Thread(target=run) for _ in range(8)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=10)
                if errors:
                    break
            assert not errors, f'并发首签异常（{len(errors)} 次）：{errors[:3]}'
            leftovers = sorted(f for f in os.listdir(key_dir) if f.startswith('device_key'))
            assert leftovers == ['device_key.pem.enc'], f'临时文件残留：{leftovers}'
