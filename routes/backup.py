import os, shutil, glob, time
from datetime import datetime
from flask import Blueprint, render_template, request, session, send_file
from models import db, User
from models.resident import Resident
from models.dibao import Dibao
from models.welfare_job import WelfareJob
from models.disabled import Disabled
from models.care_target import CareTarget, SpecialSupport, UnsupportedChild
from models.todo import TodoItem
from models.mediation import Mediation
from models.party import PartyMember, PartyFee
from models.certificate import CertificateTemplate
from models.death_report import DeathReport
from models.governance import GovernanceLedger
from models.notice import NoticeRecord, NoticeTemplate
from models.shop import Shop
from models.log import OperationLog
from utils.decorators import login_required, admin_required
from utils.helpers import json_response, log_operation
from config import Config

backup_bp = Blueprint('backup', __name__)

BACKUP_DIR = Config.BACKUP_FOLDER


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def get_db_path():
    """Get the SQLite database file path from config."""
    db_uri = Config.SQLALCHEMY_DATABASE_URI
    # Extract path from sqlite:///path
    if db_uri.startswith('sqlite:///'):
        path = db_uri[len('sqlite:///'):]
        # Remove ?check_same_thread=False etc.
        if '?' in path:
            path = path.split('?')[0]
        return path
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shucun.db')


def get_backup_files():
    """Get sorted list of backup files with metadata.

    T20-1：主扫 *.wbak（解密读 meta）；旧明文 .db 标 format='legacy'
    （仅下载/删除，还原走新 .wbak 链路，R1 兼容）。
    """
    ensure_backup_dir()
    files = []
    # .wbak 加密备份
    for f in sorted(glob.glob(os.path.join(BACKUP_DIR, '*.wbak')), key=os.path.getmtime, reverse=True):
        fname = os.path.basename(f)
        size = os.path.getsize(f)
        mtime = datetime.fromtimestamp(os.path.getmtime(f))
        meta = {}
        try:
            from utils.backup_crypto import decrypt_from_wbak
            with open(f, 'rb') as fh:
                import io, zipfile, json as _json
                raw = decrypt_from_wbak(fh.read())
                with zipfile.ZipFile(io.BytesIO(raw), 'r') as zf:
                    if 'meta.json' in zf.namelist():
                        meta = _json.loads(zf.read('meta.json').decode('utf-8'))
        except Exception:
            meta = {}
        files.append({
            'filename': fname,
            'size': size,
            'size_str': format_size(size),
            'created_at': mtime.strftime('%Y-%m-%d %H:%M:%S'),
            'format': 'wbak',
            'meta': meta,
        })
    # 旧明文 .db（legacy，R1）
    for f in sorted(glob.glob(os.path.join(BACKUP_DIR, '*.db')), key=os.path.getmtime, reverse=True):
        fname = os.path.basename(f)
        size = os.path.getsize(f)
        mtime = datetime.fromtimestamp(os.path.getmtime(f))
        files.append({
            'filename': fname,
            'size': size,
            'size_str': format_size(size),
            'created_at': mtime.strftime('%Y-%m-%d %H:%M:%S'),
            'format': 'legacy',
            'meta': {},
        })
    return files


def format_size(size_bytes):
    """Format bytes to human readable."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f'{size_bytes:.1f} {unit}'
        size_bytes /= 1024
    return f'{size_bytes:.1f} TB'


@backup_bp.route('/')
@login_required
@admin_required
def index():
    return render_template('backup/list.html')


@backup_bp.route('/api/list')
@login_required
@admin_required
def api_list():
    files = get_backup_files()
    return json_response(True, data=files)


@backup_bp.route('/api/backup', methods=['POST'])
@login_required
@admin_required
def api_backup():
    """T20-1：创建 .wbak 加密备份包（库+uploads+元信息，AES）。

    - 目标目录可写/空间不足 → 提示并中止，无半成品（R9-E3）
    - 原子落盘（临时文件 + os.replace）
    - 滚动保留最近 N 份（默认 10，settings 可配）
    """
    try:
        ensure_backup_dir()
        db_path = get_db_path()
        if not os.path.exists(db_path):
            return json_response(False, '数据库文件不存在')

        # 预估备份包大小（db + uploads，+10% 余量）→ 空间预检
        from config import Config
        uploads_dir = getattr(Config, 'UPLOAD_FOLDER', None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
        estimated = os.path.getsize(db_path)
        if os.path.isdir(uploads_dir):
            for root, _dirs, files in os.walk(uploads_dir):
                for fn in files:
                    try:
                        estimated += os.path.getsize(os.path.join(root, fn))
                    except OSError:
                        pass
        estimated = int(estimated * 1.1) + 1024 * 1024

        from utils.backup_crypto import preflight_dir
        preflight_dir(BACKUP_DIR, estimated)  # 不可写/空间不足抛 ValueError

        from utils.backup_crypto import create_wbak, enforce_retention
        wbak_path, meta = create_wbak(db_path, uploads_dir, BACKUP_DIR)

        # 滚动保留（读配置，默认 10）
        retention = _get_settings_value('backup_retention_count', '10')
        try:
            enforce_retention(BACKUP_DIR, int(retention))
        except (TypeError, ValueError):
            enforce_retention(BACKUP_DIR, 10)

        filename = os.path.basename(wbak_path)
        log_operation(session.get('username'), '数据库备份', 'backup',
                      f'创建加密备份: {filename}（.wbak）', result='success')
        return json_response(True, f'加密备份成功: {filename}', data={
            'filename': filename, 'meta': meta,
        })
    except ValueError as e:
        return json_response(False, str(e))
    except Exception as e:
        return json_response(False, f'备份失败: {str(e)}')


@backup_bp.route('/api/restore/<filename>', methods=['POST'])
@login_required
@admin_required
def api_restore(filename):
    # Security: prevent path traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        return json_response(False, '非法文件名')

    try:
        ensure_backup_dir()
        backup_path = os.path.join(BACKUP_DIR, filename)
        if not os.path.exists(backup_path):
            return json_response(False, '备份文件不存在')

        if filename.endswith('.wbak'):
            # T20-2：从 .wbak 完整恢复（解密校验 → 库+uploads 原子替换）
            from utils.backup_crypto import restore_from_wbak
            from config import Config
            uploads_dir = getattr(Config, 'UPLOAD_FOLDER', None) or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
            db_path = get_db_path()

            # 恢复纪律：断开会话 + 释放引擎 + WAL checkpoint
            db.session.remove()
            db.engine.dispose()

            try:
                meta = restore_from_wbak(backup_path, db_path, uploads_dir, BACKUP_DIR)
            except Exception as e:
                # 损坏包/口令错 → 拒绝恢复，提示含备份位置（R12-E7）
                return json_response(False, f'恢复失败：{str(e)}。'
                                            f'备份位置：{os.path.abspath(BACKUP_DIR)}（请检查口令是否正确或包是否完整）')

            log_operation(session.get('username'), '数据库备份', 'restore',
                          f'从 .wbak 加密备份恢复: {filename}', result='success')
            return json_response(True, '恢复成功（数据库与附件已还原），请重启系统生效')
        else:
            # 旧明文 .db：仅允许下载/删除（R1：还原走新 .wbak 链路）
            return json_response(False, '旧版明文 .db 备份仅支持下载/删除；还原请使用 .wbak 加密备份（旧包可联系管理员走迁移转换）')
    except Exception as e:
        return json_response(False, f'恢复失败: {str(e)}')


@backup_bp.route('/api/restore-upload', methods=['POST'])
@login_required
@admin_required
def api_restore_upload():
    """Restore from an uploaded .wbak 加密备份包（T20-2）或旧 .db（R1 仅校验拒绝）。"""
    file = request.files.get('file')
    if not file:
        return json_response(False, '请选择文件')

    fname_lower = file.filename.lower()
    if not (fname_lower.endswith('.wbak') or fname_lower.endswith('.db')):
        return json_response(False, '仅支持 .wbak 加密备份包（旧 .db 已废弃）')

    try:
        ensure_backup_dir()
        db_path = get_db_path()
        from config import Config
        uploads_dir = getattr(Config, 'UPLOAD_FOLDER', None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')

        # Save uploaded file temporarily
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        uploaded_path = os.path.join(BACKUP_DIR, f'uploaded_restore_{timestamp}.{fname_lower.rsplit(".", 1)[-1]}')
        file.save(uploaded_path)

        if uploaded_path.endswith('.db'):
            # 旧明文 .db：废弃链路，拒绝恢复（R1）
            os.remove(uploaded_path)
            return json_response(False, '旧版明文 .db 备份已废弃，请使用 .wbak 加密备份包（旧包可联系管理员走迁移转换）')

        # .wbak：解密校验 → 完整恢复
        from utils.backup_crypto import restore_from_wbak
        db.session.remove()
        db.engine.dispose()
        try:
            meta = restore_from_wbak(uploaded_path, db_path, uploads_dir, BACKUP_DIR)
        except Exception as e:
            os.remove(uploaded_path)
            return json_response(False, f'恢复失败：{str(e)}。备份位置：{os.path.abspath(BACKUP_DIR)}（请检查口令是否正确或包是否完整）')
        os.remove(uploaded_path)

        log_operation(session.get('username'), '数据库备份', 'restore',
                      f'从上传 .wbak 恢复: {file.filename}', result='success')
        return json_response(True, '外来 .wbak 备份恢复成功，请重启系统生效')
    except Exception as e:
        return json_response(False, f'恢复失败: {str(e)}')


@backup_bp.route('/api/delete/<filename>', methods=['POST'])
@login_required
@admin_required
def api_delete(filename):
    if '..' in filename or '/' in filename or '\\' in filename:
        return json_response(False, '非法文件名')

    try:
        ensure_backup_dir()
        backup_path = os.path.join(BACKUP_DIR, filename)
        if not os.path.exists(backup_path):
            return json_response(False, '备份文件不存在')
        os.remove(backup_path)
        log_operation(session.get('username'), '数据库备份', 'delete', f'删除备份: {filename}', result='success')
        return json_response(True, '删除成功')
    except OSError:
        from flask import current_app
        current_app.logger.exception('删除备份文件失败: %s', filename)
        return json_response(False, '删除失败，请检查文件权限后重试')


@backup_bp.route('/api/download/<filename>')
@login_required
@admin_required
def api_download(filename):
    if not filename or '..' in filename or '/' in filename or '\\' in filename:
        return json_response(False, '非法文件名')

    ensure_backup_dir()
    base_dir = os.path.realpath(BACKUP_DIR)
    backup_path = os.path.realpath(os.path.join(base_dir, filename))
    if not backup_path.startswith(base_dir + os.sep):
        return json_response(False, '非法文件名')
    if not os.path.exists(backup_path):
        return json_response(False, '备份文件不存在')
    return send_file(backup_path, as_attachment=True, download_name=os.path.basename(backup_path))


@backup_bp.route('/api/export-schema')
@login_required
@admin_required
def api_export_schema():
    """GLB-14 备份分型导出——「结构型」：全部表/索引 DDL（.sql 下载，不含数据）。

    数据型 = 既有 .wbak 全量备份包；本端点补齐「只要结构」的场景（跨库比对、
    空库初始化评审），只读 sqlite_master，不触碰业务数据。
    """
    from flask import Response
    from sqlalchemy import text
    rows = db.session.execute(text(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
        "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, name"
    )).fetchall()
    lines = [f"-- 结构导出（仅 DDL，不含数据） {datetime.now():%Y-%m-%d %H:%M:%S}"]
    for typ, name, ddl in rows:
        lines.append(f'-- {typ}: {name}')
        lines.append(ddl.rstrip().rstrip(';') + ';')
    body = '\n'.join(lines) + '\n'
    fname = f'schema_export_{datetime.now():%Y%m%d_%H%M%S}.sql'
    log_operation(session.get('username', ''), '备份管理', 'export',
                  '导出数据库结构（DDL，不含数据）')
    return Response(body, mimetype='application/sql',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


def _get_settings_value(key, default=''):
    """读系统设置字典值（委托 utils.settings_store，保持向后兼容）。"""
    from utils.settings_store import get_settings_value
    return get_settings_value(key, default)


@backup_bp.route('/api/settings', methods=['POST'])
@login_required
@admin_required
def api_settings():
    """Configure auto backup settings - stored in DictCategory '系统设置'."""
    from models import DictCategory, DictItem

    enable_auto = request.form.get('enable_auto', '0').strip()
    backup_time = request.form.get('backup_time', '02:00').strip()
    retention_days = request.form.get('retention_days', '30').strip()
    # T20-1：滚动保留份数（默认 10）；T20-3：提醒周期（默认 7 天）
    retention_count = request.form.get('retention_count', '10').strip()
    reminder_days = request.form.get('reminder_days', '7').strip()

    cat = DictCategory.query.filter_by(name='系统设置').first()
    if not cat:
        cat = DictCategory(name='系统设置', description='系统配置参数')
        db.session.add(cat)
        db.session.flush()

    settings_map = {
        'backup_enable_auto': enable_auto,
        'backup_time': backup_time,
        'backup_retention_days': retention_days,
        'backup_retention_count': retention_count,
        'backup_reminder_days': reminder_days,
    }

    for key, value in settings_map.items():
        item = DictItem.query.filter_by(category_id=cat.id, label=key).first()
        if item:
            item.value = str(value)
        else:
            item = DictItem(category_id=cat.id, label=key, value=str(value))
            db.session.add(item)

    db.session.commit()
    log_operation(session.get('username'), '数据库备份', 'settings', '更新自动备份配置', result='success')
    return json_response(True, '保存成功')


@backup_bp.route('/api/clear-db', methods=['POST'])
@login_required
@admin_required
def api_clear_db():
    """清空所有业务数据（保留用户、角色、字典、系统设置）。需要登录密码验证。"""
    password = request.form.get('password', '').strip()
    from werkzeug.security import check_password_hash
    user = User.query.get(session.get('user_id'))
    if not user or not check_password_hash(user.password_hash, password):
        return json_response(False, '密码验证失败')

    try:
        # 清空前自动留存一份 .wbak 加密快照（T20-1 新备份管线，可回退）
        from utils.backup_crypto import create_wbak
        ensure_backup_dir()
        db_path = get_db_path()
        from config import Config
        uploads_dir = getattr(Config, 'UPLOAD_FOLDER', None) or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
        if os.path.exists(db_path):
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            try:
                create_wbak(db_path, uploads_dir, BACKUP_DIR,
                            meta_extra={'reason': 'clear_db_snapshot'})
            except Exception:
                # 快照失败不阻断清空（原逻辑仅提示）
                pass

        # 清空所有业务表（按外键顺序）
        tables_to_truncate = [
            Dibao, WelfareJob, Disabled, CareTarget,
            SpecialSupport, UnsupportedChild,
            TodoItem, Mediation,
            PartyMember, PartyFee, CertificateTemplate,
            DeathReport, OperationLog, Resident,
            # R-047-07：045/047 新增业务表纳入「一键清空」
            NoticeRecord, NoticeTemplate, Shop, GovernanceLedger,
        ]
        total = 0
        for table in tables_to_truncate:
            count = db.session.query(table).delete()
            total += count

        db.session.commit()
        log_operation(session.get('username'), '数据库安全', 'clear_db', f'一键清空所有业务数据 (共清除{total}条记录)')
        return json_response(True, f'所有业务数据已清空，共清除{total}条记录。用户、角色、字典等系统配置保留不变。')
    except Exception as e:
        db.session.rollback()
        return json_response(False, f'清空失败: {str(e)}')
