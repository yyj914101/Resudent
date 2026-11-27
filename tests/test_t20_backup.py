# -*- coding: utf-8 -*-
"""T20-1/2/3 .wbak 加密备份 + 恢复 + 惰性提醒单测（Sprint6）。

覆盖（对应 PRD R9-E1~E4 + R12-E5/E7 + 计划稿 §4.2 验收 1-4 条）：
- .wbak 生成：文件存在 + 魔数 + strings 扫描 0 明文（R9-E1）
- 元信息校验：db/uploads 双 sha256 一致（R9-E1）
- 目标目录不可写 → 中止无半成品（R9-E3，临时文件清理）
- 损坏包拒绝恢复 + 提示含备份位置（R12-E7）
- 恢复完整：打包→改库→恢复→一致（R9-E1/R12-E5）
- 滚动保留 N 份（决策 2）
- 备份惰性提醒：超期生成待办 + 幂等（R9-E4）
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

from test_t15_cascade import _make_app, _admin_client, ID_CARD


def _make_app_t20(tmp_path, monkeypatch):
    """T20 专用 app：备份目录 + uploads 目录全部隔离到本测试 tmp（config 模块级固化，须补 patch）。"""
    app = _make_app(tmp_path, monkeypatch)
    import config as _config
    import routes.backup as _rb
    bk = str(tmp_path / 'backups')
    up = str(tmp_path / 'uploads')
    monkeypatch.setattr(_config, 'BACKUP_DIR', bk)
    monkeypatch.setattr(_config.Config, 'BACKUP_FOLDER', bk)
    monkeypatch.setattr(_rb, 'BACKUP_DIR', bk)  # backup 蓝图模块常量
    # 2026-09-07 修复（roundtrip 全量顺序偶发失败）：备份/恢复端点经 Config.UPLOAD_FOLDER
    # 定位 uploads（routes/backup.py 函数内读固化值）；config 首 import 固化在全量顺序下
    # 可能指向他处（如 async_tasks 的 /tmp/shucun_a1/uploads 共享目录）→ uploads_sha256 校验失败。
    # 补 patch uploads 到本测试 tmp，保证上传/备份/恢复三端一致。
    monkeypatch.setattr(_config, 'UPLOAD_DIR', up)
    monkeypatch.setattr(_config.Config, 'UPLOAD_FOLDER', up)
    app.config['UPLOAD_FOLDER'] = up
    return app


def _unlock(app):
    from utils.crypto import key_holder, generate_salt
    # 2026-09-07：强制重置全局 key_holder —— 先前测试若已 initialize 会残留其密钥，
    # 导致 backup roundtrip 校验失败（顺序耦合，单跑绿/全量挂）。lock + initialize 保证自洽。
    key_holder.lock()
    key_holder.initialize('测试口令123', salt=generate_salt())


def _backup_dir(app):
    from config import Config
    return getattr(Config, 'BACKUP_FOLDER', None) or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backups')


def _uploads_dir(app):
    from config import Config
    return getattr(Config, 'UPLOAD_FOLDER', None) or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')


def _make_upload(app, rel, content):
    base = _uploads_dir(app)
    full = os.path.join(base, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'wb') as f:
        f.write(content if isinstance(content, bytes) else content.encode('utf-8'))
    return full


# ============ .wbak 生成与加密 ============
class TestBackupCreate:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app_t20(tmp_path, monkeypatch)

    def test_backup_creates_wbak_no_plaintext(self, app):
        """备份生成 .wbak：魔数正确 + strings 扫描 0 明文（R9-E1）。"""
        _unlock(app)
        _make_upload(app, 'test.txt', '附件内容')
        client = _admin_client(app)
        resp = client.post('/backup/api/backup')
        body = resp.get_json()
        assert body['success'] is True, body
        fname = body['data']['filename']
        assert fname.endswith('.wbak')

        wbak_path = os.path.join(_backup_dir(app), fname)
        with open(wbak_path, 'rb') as f:
            data = f.read()
        assert data[:8] == b'SCWNBK01'
        assert b'SQLite format' not in data
        assert '附件内容'.encode('utf-8') not in data

        # strings 扫描（strings 为 Linux 工具，Windows 无此命令时降级跳过该加固段；
        # 对齐 test_t21_pdf._pdf_text 对 pdftotext 的处理先例）
        import shutil
        import subprocess
        if shutil.which('strings'):
            out = subprocess.run(['strings', wbak_path], capture_output=True, text=True).stdout
            assert 'SQLite' not in out
            assert '附件内容' not in out
        # meta 摘要
        assert body['data']['meta'].get('db_sha256')
        assert body['data']['meta'].get('file_count') >= 1

    def test_backup_restore_roundtrip(self, app):
        """打包→改库→恢复→一致（R9-E1/R12-E5）。"""
        _unlock(app)
        _make_upload(app, 'keep.txt', '待恢复附件')
        client = _admin_client(app)
        r1 = client.post('/backup/api/backup').get_json()
        assert r1['success'] is True
        fname = r1['data']['filename']

        # 改库：新增居民
        from models import db, Resident
        with app.app_context():
            db.session.add(Resident(name='改库居民', birth_date=None))
            db.session.commit()

        # 恢复
        r2 = client.post(f'/backup/api/restore/{fname}')
        body2 = r2.get_json()
        assert body2['success'] is True, body2

        # 校验：改库居民已消失（恢复到备份时点）
        with app.app_context():
            from models import Resident as R
            gone = R.query.filter_by(name='改库居民').first()
        assert gone is None
        # uploads 附件恢复
        assert os.path.isfile(os.path.join(_uploads_dir(app), 'keep.txt'))

    def test_readonly_dir_aborts_no_partial(self, app, monkeypatch):
        """目标目录不可写/空间不足 → 中止且无半成品（R9-E3）。

        注：沙盒以 root 运行，chmod 只读对 root 无效 → 注入 preflight_dir
        抛「空间不足」验证中止路径 + 无 .tmp/.wbak 残留。
        """
        _unlock(app)
        client = _admin_client(app)
        import utils.backup_crypto as _bc
        bk = _backup_dir(app)

        def _fake_preflight(dest, estimated):
            raise ValueError(f'目标目录空间不足（{dest}），已中止，无半成品产生')

        monkeypatch.setattr(_bc, 'preflight_dir', _fake_preflight)
        resp = client.post('/backup/api/backup')
        body = resp.get_json()
        assert body['success'] is False
        assert '中止' in body['msg'] or '不足' in body['msg']
        # 无 .tmp 残留、无 .wbak 半成品
        leftovers = [f for f in os.listdir(bk) if f.endswith('.wbak') or f.endswith('.tmp')]
        assert leftovers == []

    def test_corrupted_package_rejected_with_location(self, app):
        """损坏包拒绝恢复 + 提示含备份位置（R12-E7）。"""
        _unlock(app)
        client = _admin_client(app)
        r1 = client.post('/backup/api/backup').get_json()
        fname = r1['data']['filename']
        wbak_path = os.path.join(_backup_dir(app), fname)

        # 破坏文件中间字节
        with open(wbak_path, 'r+b') as f:
            f.seek(20)
            f.write(b'\x00\x00\x00\x00')

        resp = client.post(f'/backup/api/restore/{fname}')
        body = resp.get_json()
        assert body['success'] is False
        assert '备份位置' in body['msg']

    def test_retention_keeps_n(self, app):
        """滚动保留：保留最近 N 份（默认 10），超量删最旧（决策 2）。"""
        _unlock(app)
        client = _admin_client(app)
        # 造 12 份（mtime 递增）
        from utils.backup_crypto import create_wbak, latest_wbak
        db_path = _backup_dir(app).replace('backups', 'data/shucun.db')
        from routes.backup import get_db_path
        db_path = get_db_path()
        import time as _t
        os.makedirs(_backup_dir(app), exist_ok=True)
        base = _t.time()
        # enforce_retention 仅按文件名/mtime 排序删除，直接造 12 个不同 mtime 的 wbak
        for i in range(12):
            f = os.path.join(_backup_dir(app), f'shucun_backup_{i:03d}.wbak')
            with open(f, 'wb') as _fh:
                _fh.write(b'x')
            os.utime(f, (base + i, base + i))
        from utils.backup_crypto import enforce_retention
        removed = enforce_retention(_backup_dir(app), 10)
        wbaks = [f for f in os.listdir(_backup_dir(app)) if f.endswith('.wbak')]
        assert len(wbaks) == 10
        assert removed == 2


# ============ 惰性提醒（R9-E4） ============
class TestBackupReminder:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app_t20(tmp_path, monkeypatch)

    def test_reminder_creates_todo_and_idempotent(self, app):
        """从未备份 → 生成待办；重复检查不重复建（幂等）。"""
        _unlock(app)
        from utils.async_tasks import backup_reminder_check
        from models.todo import TodoItem
        with app.app_context():
            r1 = backup_reminder_check()
            assert r1['overdue'] is True
            r2 = backup_reminder_check()
            assert r2.get('existing') is True
            cnt = TodoItem.query.filter_by(todo_type='backup', status='todo').count()
        assert cnt == 1

    def test_reminder_not_overdue_after_backup(self, app):
        """备份后不超期 → 无待办。"""
        _unlock(app)
        client = _admin_client(app)
        client.post('/backup/api/backup')
        from utils.async_tasks import backup_reminder_check
        from models.todo import TodoItem
        with app.app_context():
            r = backup_reminder_check()
        assert r['overdue'] is False
        with app.app_context():
            cnt = TodoItem.query.filter_by(todo_type='backup', status='todo').count()
        assert cnt == 0

    def test_reminder_overdue_after_aging(self, app):
        """备份超 7 天（回拨 mtime）→ 生成待办。"""
        _unlock(app)
        client = _admin_client(app)
        client.post('/backup/api/backup')
        # 回拨最新 .wbak mtime 到 8 天前
        from utils.backup_crypto import latest_wbak
        from utils.async_tasks import backup_reminder_check
        from models.todo import TodoItem
        p, _ = latest_wbak(_backup_dir(app))
        old = time.time() - 8 * 86400
        os.utime(p, (old, old))
        with app.app_context():
            r = backup_reminder_check()
        assert r['overdue'] is True
        with app.app_context():
            todo = TodoItem.query.filter_by(todo_type='backup', status='todo').first()
        assert todo is not None
        assert '超' in todo.title
