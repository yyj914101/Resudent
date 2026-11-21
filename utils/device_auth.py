# -*- coding: utf-8 -*-
"""设备准入（049）—— 局域网白名单设备才可访问服务机。

三层信任模型（方案 §三，与软件授权红线不冲突）：
- ① 软件授权（`utils/license.py`，作者离线签名）控制「单机版 / 局域网版」——**本模块不触碰**。
- ② 设备准入（本模块）：**独立** Ed25519 密钥对，服务机自助签发凭证，控制「哪些设备能连」。
- ③ 会话（本模块）：随机会话 token（进程内存），控制「每次访问」。

接口（深模块，调用方只需知道这几件事）：
    admission_enabled()                     准入是否启用（单机版恒 False）
    ensure_keypair()                        首次调用生成设备密钥对（幂等）
    issue_credential(machine, label, expire) -> dict      签发凭证（含 sign）
    verify_credential(cred, current_machine) -> (ok, msg)  验签 + 机器码比对
    DeviceKeyCorruptedError                               密钥文件损坏（HTTP 入口须捕获转 JSON）
    issue_session(machine) -> token                         签发会话 token
    check_session(token) -> machine | None                  校验会话 token
    drop_sessions_for(machine) -> int                       撤销设备时清 token
    guard() -> Response | None                               before_request 拦截
    apply_cookie(response) -> response                       after_request 种 cookie

实现细节（密钥加密、内存表、豁免清单）全部留在本模块内部，调用方不感知。

私钥落盘（方案 §4.1 方案 B）：
- `DATA_DIR/device_key.pem.enc` = Fernet(私钥 PEM)，Fernet key 由 `SECRET_KEY` 固定派生。
- **偏离说明**：方案原写「PBKDF2 启动口令」，但握手/自助签发发生在**无用户会话**时，
  拿不到启动口令。改用本机 `secret.key`（`Config.SECRET_KEY`）派生——仍非明文落盘，
  且备份（只含 db/uploads）泄露时无法解密私钥。详见 049-RESULT。
"""
import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from datetime import date, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import current_app, g, jsonify, render_template, request

TOKEN_COOKIE = 'device_token'
TOKEN_TTL_SECONDS = 12 * 3600
CREDENTIAL_FILE = 'device_key.pem.enc'

# 050 R-050-03：密钥文件损坏（半截密文 / SECRET_KEY 变更）时的统一可读文案。
# 握手与签发入口捕获本异常转为 JSON 返回，不得落通用 500。
KEY_CORRUPT_MSG = '设备密钥损坏，请恢复 device_key.pem.enc 或重新签发全部设备'


class DeviceKeyCorruptedError(Exception):
    """`device_key.pem.enc` 无法解密/解析（R-050-03）。"""

    def __init__(self, msg=KEY_CORRUPT_MSG):
        super().__init__(msg)

# 豁免蓝图（方案 §6 拍板：登录/握手/静态/大屏 token + 本机回环）
EXEMPT_BLUEPRINTS = {
    'auth',              # 登录 / 登出 / 改密 / 口令通道（登录流程本身）
    'static',            # 静态资源
    'device_handshake',  # 握手端点（客户端未准入时的唯一入口）
    'screen',            # 大屏公开投屏（048：自带 token 自校验）
}
EXEMPT_ENDPOINTS = set()  # 维护模式已拆除，保留空集合供后续扩展

_sessions = {}                           # {token: {'machine': str, 'expire': float}}
_sessions_lock = threading.Lock()
_keypair_lock = threading.Lock()         # N-050-01：串行化首次签发（单进程 waitress 够用）
_priv_cache = {}                         # {pem_path: Ed25519PrivateKey}
_pub_cache = {}
_lan_cache = {}                          # {'ok': bool} 软件授权缓存（进程内一次）


# ---------------------------------------------------------------- 开关

def admission_enabled():
    """准入是否启用：环境变量 SHUCUN_DEVICE_ADMISSION 覆盖 > 局域网授权状态。

    单机版（未授权局域网）= False，零额外开销（方案 §9 拍板 5）。
    """
    override = (os.environ.get('SHUCUN_DEVICE_ADMISSION') or '').strip()
    if override in ('0', '1'):
        return override == '1'
    if os.environ.get('SHUCUN_LAN_AUTHORIZED') == '1':
        return True
    return _lan_licensed()


def _lan_licensed():
    """软件授权是否为局域网版（进程内缓存一次，避免每请求验签）。"""
    if 'ok' not in _lan_cache:
        from utils.license import check_license_on_startup
        try:
            _lan_cache['ok'] = bool(check_license_on_startup())
        except Exception:
            _lan_cache['ok'] = False
    return _lan_cache['ok']


def reset_cache():
    """测试用：清密钥/授权/会话缓存（切换 DATA_DIR / SECRET_KEY 后必须调用）。"""
    _priv_cache.clear()
    _pub_cache.clear()
    _lan_cache.clear()
    with _sessions_lock:
        _sessions.clear()


# ---------------------------------------------------------------- 密钥

def _key_path():
    from config import DATA_DIR
    return os.path.join(DATA_DIR, CREDENTIAL_FILE)


def _secret_key():
    try:
        value = current_app.config.get('SECRET_KEY')
    except RuntimeError:                  # 无 app context（脚本/单测直调）
        from config import Config
        value = Config.SECRET_KEY
    return value or ''


def _fernet():
    digest = hashlib.sha256((_secret_key() + '|device-admission-key').encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def ensure_keypair():
    """首次调用生成设备准入密钥对并加密落盘；已存在则不动（幂等）。

    原子写（R-050-03）：先写临时文件再 `os.replace`——写一半崩溃或并发
    首签时，最终路径上要么是完整旧文件要么是完整新文件，不会留半截密文
    （半截密文会让 `_private_key` 解密失败，全部设备凭证随之验签失败）。

    并发（N-050-01）：加进程内锁 + 唯一临时名。原先临时名固定为
    `path + '.tmp'`，并发首签时一方持有写句柄 → 另一方 `open` 或 `os.replace`
    抛 PermissionError（未被任何层捕获 → 500）。
    ponytail: 进程内锁覆盖单进程 waitress 部署（本项目的唯一形态）；
    若将来改多进程（gunicorn 多 worker），需换成文件锁（msvcrt/fcntl）。
    """
    path = _key_path()
    with _keypair_lock:
        if os.path.exists(path):
            return path
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        priv = Ed25519PrivateKey.generate()
        pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        blob = _fernet().encrypt(pem)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.',
                                   prefix='.device_key-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(blob)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return path


def _private_key():
    path = ensure_keypair()
    key = _priv_cache.get(path)
    if key is None:
        try:
            with open(path, 'rb') as fh:
                pem = _fernet().decrypt(fh.read())
            key = serialization.load_pem_private_key(pem, password=None)
        except InvalidToken as exc:                 # 半截密文 / 非本机 SECRET_KEY 加密
            raise DeviceKeyCorruptedError() from exc
        except (ValueError, TypeError) as exc:      # 解密成功但不是 PEM（密钥被换）
            raise DeviceKeyCorruptedError() from exc
        _priv_cache[path] = key
    return key


def _public_key():
    path = _key_path()
    key = _pub_cache.get(path)
    if key is None:
        key = _private_key().public_key()
        _pub_cache[path] = key
    return key


def public_key_pem():
    """公钥 PEM（可随客户端分发；仅用于本地预校验，服务端始终以自身公钥为准）。"""
    return _public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode('ascii')


# ---------------------------------------------------------------- 凭证

def _payload_bytes(cred):
    """凭证载荷规范化：剔除 sign 后 JSON 稳定序列化（与 license._payload_bytes 同款）。"""
    return json.dumps({k: v for k, v in cred.items() if k != 'sign'},
                      sort_keys=True, ensure_ascii=False).encode('utf-8')


def issue_credential(machine_code, label='', expire='forever', issued=None):
    """服务机签发设备凭证（绑定 machine_code）。

    `serial` 是随机序号：Ed25519 签名是确定性的，同一载荷会得到同一签名——
    没有 serial 时「重新签发」会产出与旧凭证一模一样的 sign，无法让旧凭证失效。
    """
    cred = {
        'machine': (machine_code or '').strip().upper(),
        'label': label or '',
        'expire': expire or 'forever',
        'issued': issued or date.today().isoformat(),
        'serial': secrets.token_hex(4),
    }
    cred['sign'] = base64.b64encode(_private_key().sign(_payload_bytes(cred))).decode('ascii')
    return cred


def slim_credential(cred) -> dict:
    """凭证落库形态（GLB-41）：只存 sign + 审计元数据。

    库内一致性校验只比对 sign（handshake），验签走公钥——label 等展示字段无需落库；
    回传给管理员/客户端的仍是全量凭证。读取侧对全量旧形态天然兼容（都含 sign）。
    """
    return {k: cred[k] for k in ('sign', 'serial', 'issued', 'expire') if cred.get(k)}


def verify_credential(cred, current_machine=None):
    """验签 + 机器码比对。返回 (ok, msg)。"""
    if not isinstance(cred, dict) or not cred.get('sign'):
        return False, '凭证签名缺失'
    try:
        _public_key().verify(base64.b64decode(cred['sign']), _payload_bytes(cred))
    except (InvalidSignature, ValueError, TypeError):
        return False, '凭证签名无效（可能被篡改）'
    if current_machine and cred.get('machine') != (current_machine or '').strip().upper():
        return False, '凭证与设备机器码不匹配'
    if not _expire_ok(cred.get('expire')):
        return False, f"凭证已过期（{cred.get('expire')}）"
    return True, 'ok'


def _expire_ok(expire):
    from datetime import date, datetime
    if not expire or expire == 'forever':
        return True
    try:
        return datetime.strptime(expire, '%Y-%m-%d').date() >= date.today()
    except ValueError:
        return False


# ---------------------------------------------------------------- 会话

def issue_session(machine_code, ttl=TOKEN_TTL_SECONDS):
    token = secrets.token_urlsafe(24)
    with _sessions_lock:
        _sessions[token] = {'machine': (machine_code or '').strip().upper(),
                            'expire': time.time() + ttl}
    return token


def check_session(token):
    if not token:
        return None
    with _sessions_lock:
        row = _sessions.get(token)
        if not row:
            return None
        if row['expire'] < time.time():
            _sessions.pop(token, None)
            return None
        return row['machine']


def drop_sessions_for(machine_code):
    machine = (machine_code or '').strip().upper()
    with _sessions_lock:
        victims = [t for t, row in _sessions.items() if row['machine'] == machine]
        for t in victims:
            _sessions.pop(t, None)
    return len(victims)


# ---------------------------------------------------------------- 拦截

def _request_token():
    return (request.headers.get('X-Device-Token')
            or request.args.get('device_token')
            or request.cookies.get(TOKEN_COOKIE)
            or '').strip()


def _is_local():
    remote = request.remote_addr or ''
    return remote in ('127.0.0.1', '::1', 'localhost') or remote.startswith('127.')


def _is_exempt(endpoint):
    if not endpoint:
        return False
    if endpoint in EXEMPT_ENDPOINTS:
        return True
    blueprint = endpoint.rsplit('.', 1)[0] if '.' in endpoint else endpoint
    return blueprint in EXEMPT_BLUEPRINTS


def guard():
    """before_request：设备未准入则拒绝。返回 Response 表示拦截，None 表示放行。"""
    if not admission_enabled():
        return None

    token = _request_token()
    machine = check_session(token) if token else None

    if _is_local():                       # 本机回环豁免（方案 §6）
        return None

    exempt = _is_exempt(request.endpoint)
    if exempt:
        # 豁免端点也消费 query token：客户端首次打开 /auth/login?device_token=… 时种 cookie
        if machine and request.args.get('device_token'):
            g.device_cookie = token
        return None

    if machine:
        return None

    from utils.permissions import response_wants_json
    if response_wants_json():
        return jsonify({'success': False, 'msg': '设备未准入，请联系管理员审批'}), 403
    return render_template('device/denied.html', remote=request.remote_addr or ''), 403


def apply_cookie(response):
    """after_request：把 URL 带来的会话 token 落成 cookie（浏览器后续请求自动携带）。"""
    token = g.pop('device_cookie', None)
    if token:
        response.set_cookie(TOKEN_COOKIE, token, max_age=TOKEN_TTL_SECONDS,
                            httponly=True, samesite='Lax')
    return response
