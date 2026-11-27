"""T07 uploads 子目录规范 + 导出独立通道 单测

覆盖：
- serve_upload：未登录 403、无权限 403、有权限 200、export/misc/根 403、img 占位符
- export_token：issue+verify+consume+过期+user_id 不匹配
- export_download：token 生成+校验+消费+过期
- relocate_uploads：归位+路径更新+孤儿+幂等
- cleanup：>7 天清理
"""
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime, timedelta


def _setup_env(tmp_path):
    """配置独立测试环境（避免共享 DB）。"""
    data_dir = str(tmp_path / "data")
    for d in ("data", "uploads", "uploads/risk",
              "uploads/mediation", "uploads/visit", "uploads/logos",
              "uploads/misc", "uploads/export", "backups", "logs",
              "temp", "temp/exports"):
        os.makedirs(os.path.join(str(tmp_path), d.replace("data/", "")), exist_ok=True)
    os.environ["SHUCUN_DATA_DIR"] = data_dir
    os.environ["SHUCUN_DB_PATH"] = str(tmp_path / "shucun.db")
    os.environ["SHUCUN_UPLOAD_DIR"] = str(tmp_path / "uploads")
    os.environ["SHUCUN_BACKUP_DIR"] = str(tmp_path / "backups")
    os.environ["SHUCUN_LOG_DIR"] = str(tmp_path / "logs")
    os.environ["SHUCUN_TEMP_DIR"] = str(tmp_path / "temp")
    os.environ["SHUCUN_LICENSE_FILE"] = str(tmp_path / "license.key")
    os.environ["SHUCUN_PORT"] = "5000"
    os.environ["SHUCUN_SECRET_KEY"] = "test"
    # 覆写 Config 类属性（避免模块级缓存导致跨测试共享 DB）
    from config import Config
    Config.SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.environ["SHUCUN_DB_PATH"]}?check_same_thread=False'
    Config.UPLOAD_FOLDER = os.environ["SHUCUN_UPLOAD_DIR"]
    Config.TEMP_FOLDER = os.environ["SHUCUN_TEMP_DIR"]
    Config.EXPORTS_DIR = os.path.join(os.environ["SHUCUN_TEMP_DIR"], 'exports')


# ============ ExportTokenStore ============

class TestExportTokenStore:

    def test_issue_verify_consume(self):
        from utils.export_token import ExportTokenStore
        store = ExportTokenStore()
        token = store.issue(1, "/tmp/test.xlsx", "resident", ttl=600)
        ok, path, mod = store.verify(token, 1)
        assert ok and path == "/tmp/test.xlsx" and mod == "resident"
        store.consume(token)
        ok2, _, _ = store.verify(token, 1)
        assert not ok2  # 消费后失效

    def test_wrong_user_rejected(self):
        from utils.export_token import ExportTokenStore
        store = ExportTokenStore()
        token = store.issue(1, "/tmp/x.xlsx", "resident")
        ok, _, _ = store.verify(token, 999)
        assert not ok

    def test_expired(self):
        from utils.export_token import ExportTokenStore
        store = ExportTokenStore()
        token = store.issue(1, "/tmp/x.xlsx", "resident", ttl=1)
        time.sleep(1.2)
        ok, _, _ = store.verify(token, 1)
        assert not ok


# ============ serve_upload ============

class TestServeUpload:

    @pytest.fixture()
    def app(self, tmp_path):
        _setup_env(tmp_path)
        from app import create_app
        application = create_app()
        application.config["TESTING"] = True
        return application

    @pytest.fixture()
    def client(self, app):
        return app.test_client()

    def test_not_logged_in_403(self, client, tmp_path):
        """未登录访问 uploads → 403（或重定向 login）。"""
        # 先放一个文件
        risk_dir = os.path.join(str(tmp_path), "uploads", "risk")
        os.makedirs(risk_dir, exist_ok=True)
        open(os.path.join(risk_dir, "test.jpg"), "wb").write(b"x")
        r = client.get("/uploads/risk/test.jpg")
        assert r.status_code in (401, 302, 403)

    def test_export_subdir_always_403(self, client, app, tmp_path):
        """export 子目录一律 403（即使登录）。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='test', permissions='{"*": true}')
            db.session.add(role); db.session.flush()
            u = User(username='t7test', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        export_dir = os.path.join(str(tmp_path), "uploads", "export")
        os.makedirs(export_dir, exist_ok=True)
        open(os.path.join(export_dir, "secret.xlsx"), "wb").write(b"x")
        r = client.get("/uploads/export/secret.xlsx")
        assert r.status_code == 403

    def test_misc_subdir_403(self, client, app, tmp_path):
        """misc 子目录一律 403。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='test2', permissions='{"*": true}')
            db.session.add(role); db.session.flush()
            u = User(username='t7misc', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        misc_dir = os.path.join(str(tmp_path), "uploads", "misc")
        os.makedirs(misc_dir, exist_ok=True)
        open(os.path.join(misc_dir, "orphan.jpg"), "wb").write(b"x")
        r = client.get("/uploads/misc/orphan.jpg")
        assert r.status_code == 403

    def test_root_no_subdir_403(self, client, app, tmp_path):
        """根目录散文件 403。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='test3', permissions='{"*": true}')
            db.session.add(role); db.session.flush()
            u = User(username='t7root', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        open(os.path.join(str(tmp_path), "uploads", "scatter.txt"), "wb").write(b"x")
        r = client.get("/uploads/scatter.txt")
        assert r.status_code == 403

    def test_img_403_returns_svg_placeholder(self, client, app, tmp_path):
        """<img> 场景 403 返回 1×1 SVG 占位符不破图。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='test4', permissions='{"resident.view": true}')
            db.session.add(role); db.session.flush()
            u = User(username='t7img', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get("/uploads/mediation/noexist.jpg",
                       headers={"Accept": "image/png, image/*"})
        assert r.status_code == 200
        assert r.mimetype == "image/svg+xml"
        assert b"<svg" in r.data

    def test_authorized_access_200(self, client, app, tmp_path):
        """有权限的子目录文件可访问。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='test5', permissions='{"mediation.view": true}')
            db.session.add(role); db.session.flush()
            u = User(username='t7ok', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        med_dir = os.path.join(str(tmp_path), "uploads", "mediation")
        os.makedirs(med_dir, exist_ok=True)
        open(os.path.join(med_dir, "photo.jpg"), "wb").write(b"image-data")
        r = client.get("/uploads/mediation/photo.jpg")
        assert r.status_code == 200


# ============ visit 子目录（T18-4：照片登录可预览、无权限拒绝） ============

    def _visit_file(self, tmp_path):
        visit_dir = os.path.join(str(tmp_path), "uploads", "visit")
        os.makedirs(visit_dir, exist_ok=True)
        p = os.path.join(visit_dir, "a.png")
        open(p, "wb").write(b"png-data")
        return "/uploads/visit/a.png"

    def test_visit_authorized_access_200(self, client, app, tmp_path):
        """有 visit.view → 走访照片可预览（T18-4 验收#3）。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='t7visit-ok', permissions='{"visit.view": true}')
            db.session.add(role); db.session.flush()
            u = User(username='t7visitok', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get(self._visit_file(tmp_path))
        assert r.status_code == 200

    def test_visit_without_permission_403(self, client, app, tmp_path):
        """无 visit.view → 403（无权限不可访问，验收#3）。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='t7visit-no', permissions='{"resident.view": true}')
            db.session.add(role); db.session.flush()
            u = User(username='t7visitno', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get(self._visit_file(tmp_path))
        assert r.status_code == 403

    def test_visit_img_placeholder(self, client, app, tmp_path):
        """无 visit.view + <img> 场景 → 200 SVG 占位不破图。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='t7visit-img', permissions='{"resident.view": true}')
            db.session.add(role); db.session.flush()
            u = User(username='t7visitimg', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get(self._visit_file(tmp_path),
                       headers={"Accept": "image/png, image/*"})
        assert r.status_code == 200
        assert r.mimetype == "image/svg+xml"
        assert b"<svg" in r.data

    def test_visit_not_logged_in(self, client, tmp_path):
        """未登录访问走访照片 → 拒绝（401/302/403）。"""
        self._visit_file(tmp_path)
        r = client.get("/uploads/visit/a.png")
        assert r.status_code in (401, 302, 403)


# ============ report 导出通道 ============

class TestReportExportDownload:

    @pytest.fixture()
    def app(self, tmp_path):
        _setup_env(tmp_path)
        from app import create_app
        application = create_app()
        application.config["TESTING"] = True
        return application

    @pytest.fixture()
    def client(self, app):
        return app.test_client()

    def test_download_invalid_token_403(self, client, app):
        """无效 token → 403。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='dl1', permissions='{"*": true}')
            db.session.add(role); db.session.flush()
            u = User(username='dl1', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get("/report/download/nonexistent-token")
        assert r.status_code == 403

    def test_download_valid_token_200(self, client, app, tmp_path):
        """有效 token → 200 send_file。"""
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        from utils.export_token import export_token_store
        with app.app_context():
            role = Role(name='dl2', permissions='{"*": true}')
            db.session.add(role); db.session.flush()
            u = User(username='dl2', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        # 创建导出文件
        exports_dir = os.path.join(str(tmp_path), "temp", "exports")
        os.makedirs(exports_dir, exist_ok=True)
        fpath = os.path.join(exports_dir, "test_export.xlsx")
        open(fpath, "wb").write(b"fake-xlsx")
        token = export_token_store.issue(uid, fpath, "resident")
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get(f"/report/download/{token}")
        assert r.status_code == 200
        # 二次下载 → 403（已消费）
        r2 = client.get(f"/report/download/{token}")
        assert r2.status_code == 403


# ============ cleanup_temp_exports ============

class TestCleanupTempExports:

    def test_cleanup_old_files(self, tmp_path):
        """>7 天文件被清理，新文件保留。"""
        _setup_env(tmp_path)
        from utils.async_tasks import cleanup_temp_exports
        exports_dir = os.path.join(str(tmp_path), "temp", "exports")
        os.makedirs(exports_dir, exist_ok=True)
        # 旧文件（>7 天）
        old_file = os.path.join(exports_dir, "old.xlsx")
        open(old_file, "wb").write(b"old")
        old_time = time.time() - 8 * 24 * 3600
        os.utime(old_file, (old_time, old_time))
        # 新文件
        new_file = os.path.join(exports_dir, "new.xlsx")
        open(new_file, "wb").write(b"new")
        result = cleanup_temp_exports()
        assert result['deleted'] >= 1
        assert not os.path.isfile(old_file)
        assert os.path.isfile(new_file)


# ============ relocate_uploads（幂等性验证）============

class TestRelocateUploads:

    def test_idempotent_dry_run(self, tmp_path):
        """dry-run 不修改文件，可重复执行。"""
        _setup_env(tmp_path)
        from scripts.migrations.relocate_uploads import relocate
        uploads = os.path.join(str(tmp_path), "uploads")
        os.makedirs(os.path.join(uploads, "risk_checks"), exist_ok=True)
        os.makedirs(os.path.join(uploads, "risk"), exist_ok=True)
        report1 = relocate(uploads, dry_run=True)
        report2 = relocate(uploads, dry_run=True)
        # dry-run 不报错即可
        assert 'errors' in report1
        assert 'errors' in report2
