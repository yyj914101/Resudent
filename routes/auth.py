from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models import db, User
from datetime import datetime
import time

from utils.crypto import (
    key_holder, bootstrap_token, retry_delay,
    load_key_meta, save_key_meta,
    CryptoUninitialized,
)

auth_bp = Blueprint('auth', __name__)

# B13 端点级退避状态（进程内）
_bootstrap_attempts = {}

# P1-2 整改：登录失败限流（进程内，按 username@IP 计数 + 锁定窗口）
_LOGIN_ATTEMPTS = {}
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_SECONDS = 300
LOGIN_RETRY_BASE_DELAY = 0.5


def _login_key(username):
    ip = request.remote_addr or 'local'
    return f'{username}@{ip}'


def _login_locked(username):
    entry = _LOGIN_ATTEMPTS.get(_login_key(username))
    if not entry:
        return False
    count, first = entry
    if count >= LOGIN_MAX_ATTEMPTS and (time.time() - first) < LOGIN_LOCK_SECONDS:
        return True
    if (time.time() - first) >= LOGIN_LOCK_SECONDS:
        _LOGIN_ATTEMPTS.pop(_login_key(username), None)
    return False


def _password_strong(password):
    """密码强度校验：长度 ≥8 且同时含字母与数字（与 PBKDF2 高迭代匹配）。"""
    if not password or len(password) < 8:
        return False
    return any(c.isalpha() for c in password) and any(c.isdigit() for c in password)


def _is_local_request():
    """回环判定：仅 127.0.0.1 / ::1 / localhost 视为本机（B13 降级路径）。"""
    remote = request.remote_addr or ""
    return remote in ("127.0.0.1", "::1", "localhost")


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        # P1-2 整改：登录失败限流（锁定窗口内直接拒绝，防爆破）
        if _login_locked(username):
            flash('登录失败次数过多，请 5 分钟后再试', 'danger')
            return render_template('auth/login.html')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            if not user.is_active:
                flash('账号已被停用', 'danger')
                return render_template('auth/login.html')
            _LOGIN_ATTEMPTS.pop(_login_key(username), None)  # 成功清零
            session['user_id'] = user.id
            session['username'] = user.username
            session.permanent = True
            user.last_login = datetime.now()
            db.session.commit()
            # P1-2 整改：首次登录/重置后强制改密
            if user.must_change_password:
                flash('首次登录或密码已重置，请先修改密码', 'warning')
                return redirect(url_for('auth.change_password'))
            return redirect(url_for('dashboard.index'))
        # 失败计数（进程内）
        key = _login_key(username)
        count, first = _LOGIN_ATTEMPTS.get(key, (0, time.time()))
        _LOGIN_ATTEMPTS[key] = (count + 1, first)
        flash('用户名或密码错误', 'danger')
    return render_template('auth/login.html')


@auth_bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    """P1-2 整改：首次登录/重置后强制改密（需登录，端点内自校验）。"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user = User.query.get(session.get('user_id'))
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        old_password = request.form.get('old_password', '')
        new_password = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not user.check_password(old_password):
            flash('原密码错误', 'danger')
        elif not _password_strong(new_password):
            flash('新密码至少 8 位，且须同时包含字母和数字', 'danger')
        elif new_password != confirm:
            flash('两次输入的新密码不一致', 'danger')
        elif new_password == old_password:
            flash('新密码不能与原密码相同', 'danger')
        else:
            user.set_password(new_password)
            user.must_change_password = False
            db.session.commit()
            flash('密码已修改，请重新登录', 'success')
            session.clear()
            return redirect(url_for('auth.login'))
    return render_template('auth/change_password.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


# ---------------------------------------------------------------
# B13 口令通道（T05）：bootstrap-key 主通道 + /auth/unlock 降级页
# ---------------------------------------------------------------

@auth_bp.route('/bootstrap-token')
def bootstrap_token_issue():
    """B13 主通道令牌下发（P1-3 整改：launcher 启动后取回一次性令牌，修复恒 401）。

    - 仅本机回环可访问；非回环 503
    - 返回一次性令牌（TTL≤10min），由 /auth/bootstrap-key 消费
    """
    if not _is_local_request():
        return jsonify({"success": False, "msg": "仅本机可访问"}), 503
    token = bootstrap_token.issue()
    return jsonify({"success": True, "token": token})


@auth_bp.route('/bootstrap-key', methods=['GET'])
def bootstrap_key():
    """B13 主通道：launcher 携带一次性令牌提交启动口令。

    - 仅本机回环可访问（非回环 503 不渲染）
    - 令牌 TTL≤10min、一次性；校验失败指数退避（延迟非锁定，态③兼容）
    - 成功派生密钥（KeyHolder 内存持有，不落盘），返回 ok
    """
    if not _is_local_request():
        return jsonify({"success": False, "msg": "口令设置须在服务器本机浏览器完成"}), 503

    token = request.args.get('token', '')
    password = request.args.get('password', '')
    if not token or not password:
        return jsonify({"success": False, "msg": "缺少令牌或口令"}), 400

    # 端点级指数退避：同 IP 连续失败延迟递增（非锁定）
    ip = request.remote_addr or "local"
    attempt = _bootstrap_attempts.get(ip, 0)
    if attempt > 0:
        import time as _t
        _t.sleep(retry_delay(attempt - 1))

    if not bootstrap_token.check(token):
        _bootstrap_attempts[ip] = attempt + 1
        return jsonify({"success": False, "msg": "令牌无效或已过期，请重新启动程序"}), 401

    meta = load_key_meta()
    if meta is None:
        # 首启：校验口令强度后生成盐并派生（P3-4 整改，与 unlock 降级页口径一致）
        if not _password_strong(password):
            return jsonify({"success": False,
                            "msg": "启动口令至少 8 位，且须同时包含字母和数字"}), 400
        key_meta = key_holder.initialize(password)
        save_key_meta(key_meta)
    else:
        salt = bytes.fromhex(meta["salt_hex"])
        iterations = int(meta.get("iterations", 310_000))
        check_token = meta.get("check_token", "")
        if not key_holder.unlock(password, salt, iterations, check_token=check_token):
            _bootstrap_attempts[ip] = attempt + 1
            return jsonify({"success": False, "msg": "口令错误，请重试（不锁定）。"
                                                      "如确认口令无误仍无法解锁，可能密钥数据损坏，"
                                                      "可在本机访问 /auth/recovery 从最近 .wbak 备份恢复"}), 401
    _bootstrap_attempts[ip] = 0
    return jsonify({"success": True, "msg": "密钥已就绪"})


@auth_bp.route('/unlock', methods=['GET', 'POST'])
def unlock():
    """B13 降级路径：首启/解锁页面（仅回环可渲染表单）。

    - 非回环请求 503 不渲染表单
    - 无 key_meta → 首启设置口令；有 key_meta → 输入口令解锁（态③原地重试不锁定）
    """
    if not _is_local_request():
        return "口令设置须在服务器本机浏览器完成", 503

    meta = load_key_meta()
    is_first_run = meta is None

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if is_first_run:
            # P3-4 整改：提高启动口令强度（长度+字符集，与 PBKDF2 高迭代匹配）
            if not _password_strong(password):
                flash('启动口令至少 8 位，且须同时包含字母和数字', 'danger')
            elif password != confirm:
                flash('两次输入不一致', 'danger')
            else:
                key_meta = key_holder.initialize(password)
                save_key_meta(key_meta)
                flash('启动口令已设置，密钥已就绪', 'success')
                return redirect(url_for('dashboard.index'))
        else:
            salt = bytes.fromhex(meta["salt_hex"])
            iterations = int(meta.get("iterations", 310_000))
            check_token = meta.get("check_token", "")
            if key_holder.unlock(password, salt, iterations, check_token=check_token):
                flash('解锁成功', 'success')
                return redirect(url_for('dashboard.index'))
            flash('口令错误，请重试（不锁定）', 'danger')

    return render_template('auth/unlock.html', is_first_run=is_first_run)


def _wbak_within_backup_dir(wbak_path, backup_dir):
    """P3-3 整改：校验 wbak_path 位于 backup_dir 内（realpath + 前缀比对，防路径穿越）。"""
    import os
    try:
        root = os.path.realpath(backup_dir)
        target = os.path.realpath(wbak_path)
        return target == root or target.startswith(root + os.sep)
    except Exception:
        return False


@auth_bp.route('/recovery', methods=['GET', 'POST'])
def recovery():
    """R12-E7 态④ 口令遗忘/密钥损坏恢复引导（仅本机回环）。

    口令遗忘=数据不可恢复（A3 拍板不采纳 U 盘密钥兜底）：
    本页仅提供从最近 .wbak 加密备份恢复路径（提示语含备份位置），无自助重置。
    """
    import os
    if not _is_local_request():
        return "恢复引导须在服务器本机浏览器完成", 503

    from config import Config
    backup_dir = getattr(Config, 'BACKUP_FOLDER', None) or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backups')
    from utils.backup_crypto import latest_wbak
    latest = latest_wbak(backup_dir)
    backups = []
    if latest:
        backups.append({'path': latest[0],
                        'mtime': datetime.fromtimestamp(latest[1]).strftime('%Y-%m-%d %H:%M:%S')})

    if request.method == 'POST':
        password = request.form.get('password', '')
        wbak_path = request.form.get('wbak_path', '')
        if not password or not wbak_path:
            flash('请输入启动口令并选择备份包', 'danger')
        else:
            try:
                # 用口令解锁密钥后从 .wbak 恢复
                meta_info = load_key_meta()
                if meta_info:
                    salt = bytes.fromhex(meta_info['salt_hex'])
                    iterations = int(meta_info.get('iterations', 310_000))
                    ok = key_holder.unlock(password, salt, iterations,
                                           check_token=meta_info.get('check_token', ''))
                else:
                    # 无 key_meta（数据丢失场景）：口令用于派生新密钥解密备份
                    key_meta = key_holder.initialize(password)
                    save_key_meta(key_meta)
                    ok = True
                if not ok:
                    flash('口令错误，无法解密备份（不锁定，可重试）', 'danger')
                elif not _wbak_within_backup_dir(wbak_path, backup_dir):
                    # P3-3 整改：wbak_path 白名单限定在 backup_dir 内，防任意 .wbak 路径
                    flash('所选备份包路径非法（须位于备份目录内）', 'danger')
                elif not os.path.isfile(wbak_path):
                    flash('所选备份包不存在', 'danger')
                else:
                    from utils.backup_crypto import restore_from_wbak
                    from routes.backup import get_db_path
                    from config import Config as _C
                    uploads_dir = getattr(_C, 'UPLOAD_FOLDER', None) or os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
                    db.session.remove()
                    try:
                        db.engine.dispose()
                    except Exception:
                        pass
                    try:
                        restore_from_wbak(wbak_path, get_db_path(), uploads_dir, backup_dir)
                        flash('已从备份恢复（含数据库与附件）。请重启系统生效。', 'success')
                    except Exception as e:
                        flash(f'恢复失败：{e}（备份位置：{os.path.abspath(backup_dir)}）', 'danger')
            except Exception as e:
                flash(f'恢复流程异常：{e}', 'danger')

    return render_template('auth/recovery.html', backups=backups,
                           backup_dir=os.path.abspath(backup_dir))


@auth_bp.route('/key-status')
def key_status():
    """供前端判断是否需要口令通道（无需登录，仅回环）。"""
    if not _is_local_request():
        return jsonify({"success": False, "msg": "仅本机可查"}), 503
    return jsonify({
        "success": True,
        "initialized": key_holder.is_initialized,
        "has_meta": load_key_meta() is not None,
    })
