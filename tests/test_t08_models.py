"""T08 数据底座单测：18 新表模型注册 + m001 幂等 + 6 扩展表 m002 列。"""
import os

os.environ.setdefault("SHUCUN_DATA_DIR", "/tmp/shucun_t08/data")
os.environ.setdefault("SHUCUN_DB_PATH", "/tmp/shucun_t08/shucun.db")
os.environ.setdefault("SHUCUN_UPLOAD_DIR", "/tmp/shucun_t08/uploads")
os.environ.setdefault("SHUCUN_BACKUP_DIR", "/tmp/shucun_t08/backups")
os.environ.setdefault("SHUCUN_LOG_DIR", "/tmp/shucun_t08/logs")
os.environ.setdefault("SHUCUN_TEMP_DIR", "/tmp/shucun_t08/temp")
os.environ.setdefault("SHUCUN_LICENSE_FILE", "/tmp/shucun_t08/license.key")
os.environ.setdefault("SHUCUN_PORT", "5000")
os.environ.setdefault("SHUCUN_SECRET_KEY", "smoke")

import sqlite3
import pytest

NEW_MODELS = [
    "SubsidyProject", "SubsidyRecord", "ResidentTag",
    "VisitPlan", "VisitRecord", "WarnRecord",
    "ReconcileTask", "ReconcileResult", "CascadeChecklist", "CascadeNotice",
    "ImportBatch", "ImportFieldMapping", "ImportRowError", "ImportFieldRegistry",
    "SchemaChangeRequest", "DataModelVersion",
    "MigrationRecord", "MigrationLog", "MigrationOrphanLog",
]

NEW_TABLES = [
    "subsidy_project", "subsidy_record", "resident_tag",
    "visit_plan", "visit_record", "warn_record",
    "reconcile_task", "reconcile_result", "cascade_checklist", "cascade_notice",
    "import_batch", "import_field_mapping", "import_row_error", "import_field_registry",
    "schema_change_request", "data_model_version",
    "migration_record", "migration_log", "migration_orphan_log",
]


class TestModelRegistration:
    def test_all_new_models_importable(self):
        """18 新表模型全部注册（models/__init__.py）。"""
        import models
        missing = [m for m in NEW_MODELS if not hasattr(models, m)]
        assert not missing, f"未注册模型: {missing}"


class TestM001Idempotent:
    def test_m001_runs_twice(self, tmp_path):
        """m001 可重复执行（IF NOT EXISTS），两遍不报错。"""
        sql_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts", "migrations", "m001_new_tables.sql",
        )
        sql = open(sql_path, encoding="utf-8").read()
        db_path = str(tmp_path / "m001.db")
        con = sqlite3.connect(db_path)
        # 依赖表 residents/death_reports 必须先存在（FK 引用）
        con.executescript(
            "CREATE TABLE residents(id INTEGER PRIMARY KEY);"
            "CREATE TABLE death_reports(id INTEGER PRIMARY KEY);"
        )
        con.executescript(sql)  # 第一遍
        con.executescript(sql)  # 第二遍（幂等）
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in NEW_TABLES if t not in tables]
        assert not missing, f"m001 建表缺失: {missing}"


class TestExtendedColumns:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("SHUCUN_DB_PATH", str(tmp_path / "shucun.db"))
        monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
        monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
        monkeypatch.setenv("SHUCUN_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
        monkeypatch.setenv("SHUCUN_LICENSE_FILE", str(tmp_path / "license.key"))
        for d in ("uploads", "backups", "logs", "temp", "data"):
            (tmp_path / d).mkdir(parents=True, exist_ok=True)
        from app import create_app
        application = create_app()
        application.config["TESTING"] = True
        return application

    def test_m002_columns_present(self, app):
        """6 扩展表的 m002 列已由模型落库。"""
        from models import db
        checks = {
            "residents": ["flow_status", "ext_data"],
            "care_targets": ["target_type", "child_type", "guardian_consent", "status"],
            "death_reports": ["cascade_status", "confirm_by", "confirmed_at"],
            "dibao": ["status", "invalid_reason"],
            "disabled_persons": ["status", "invalid_reason"],
            "special_support": ["status", "invalid_reason"],
        }
        for table, cols in checks.items():
            have = {c.name for c in db.metadata.tables[table].columns}
            miss = [c for c in cols if c not in have]
            assert not miss, f"{table} 缺列: {miss}"
