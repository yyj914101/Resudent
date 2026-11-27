"""T06 脱敏四通道单测：粒度 / 12 变体 / 行级 / 导出 / 日志 / JSON 层 / 明文端点 / 扫描。"""
import os

os.environ.setdefault("SHUCUN_DATA_DIR", "/tmp/shucun_mask/data")
os.environ.setdefault("SHUCUN_DB_PATH", "/tmp/shucun_mask/shucun.db")
os.environ.setdefault("SHUCUN_UPLOAD_DIR", "/tmp/shucun_mask/uploads")
os.environ.setdefault("SHUCUN_BACKUP_DIR", "/tmp/shucun_mask/backups")
os.environ.setdefault("SHUCUN_LOG_DIR", "/tmp/shucun_mask/logs")
os.environ.setdefault("SHUCUN_TEMP_DIR", "/tmp/shucun_mask/temp")
os.environ.setdefault("SHUCUN_LICENSE_FILE", "/tmp/shucun_mask/license.key")
os.environ.setdefault("SHUCUN_PORT", "5000")
os.environ.setdefault("SHUCUN_SECRET_KEY", "smoke")

import pytest

from utils.masking import (
    mask_id_card, mask_phone, mask_bank_card, mask_field, MaskFilter,
    field_kind, mask_text, mask_export_rows, static_scan_text,
    filter_child_rows, can_see_children, get_mask_config, update_mask_config,
    DEFAULT_MASK_RULES,
)


def _make_app(tmp_path, monkeypatch):
    """隔离 DB：config.py 模块级缓存 DB_PATH，需直接覆写 Config 类属性。"""
    db_path = str(tmp_path / "shucun.db")
    import config
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


# ============ 粒度（前 6 后 4 / 前 3 后 4 / 前 6 后 4） ============
class TestGranularity:
    def test_id_card(self):
        assert mask_id_card("340104199001011234") == "340104********1234"
        assert mask_id_card("34010419900101123X") == "340104********123X"

    def test_phone(self):
        assert mask_phone("13812345678") == "138****5678"

    def test_bank_card(self):
        assert mask_bank_card("6221881234567890") == "622188******7890"

    def test_short_value_unchanged(self):
        assert mask_id_card("123") == "123"
        assert mask_phone("") == ""
        assert mask_bank_card(None) is None


class TestFieldKind:
    def test_variants(self):
        assert field_kind("id_card") == "id_card"
        assert field_kind("household_id_card") == "id_card"
        assert field_kind("account_holder_id_card") == "id_card"
        assert field_kind("guardian_phone") == "phone"
        assert field_kind("relative_phone") == "phone"
        assert field_kind("bank_card") == "bank_card"
        assert field_kind("bank_info") == "bank_card"
        assert field_kind("name") is None


class TestMaskFilter:
    def test_admin_plaintext(self):
        data = {"id_card": "340104199001011234", "name": "张三"}
        assert MaskFilter().apply(data, can_view_sensitive=True)["id_card"] == "340104199001011234"

    def test_normal_masked_12_variants(self):
        data = {
            "id_card": "340104199001011234",
            "household_id_card": "340104199001011234",
            "account_holder_id_card": "340104199001011234",
            "applicant_id_card": "340104199001011234",
            "respondent_id_card": "340104199001011234",
            "phone": "13812345678",
            "guardian_phone": "13812345678",
            "relative_phone": "13812345678",
            "contact_phone": "13812345678",
            "mobile_phone": "13812345678",
            "bank_card": "6221881234567890",
            "bank_info": "6221881234567890",
            "name": "张三",
        }
        out = MaskFilter().apply(data, can_view_sensitive=False)
        assert out["id_card"] == "340104********1234"
        assert out["household_id_card"] == "340104********1234"
        assert out["phone"] == "138****5678"
        assert out["bank_card"] == "622188******7890"
        assert out["bank_info"] == "622188******7890"
        assert out["name"] == "张三"  # 非敏感不动

    def test_recursive_nested(self):
        data = {"list": [{"id_card": "340104199001011234"}], "nested": {"phone": "13812345678"}}
        out = MaskFilter().apply(data, can_view_sensitive=False)
        assert out["list"][0]["id_card"] == "340104********1234"
        assert out["nested"]["phone"] == "138****5678"


# ============ 儿童行级可见性 ============
class TestChildRows:
    def test_filter(self):
        rows = [
            {"name": "老人A", "target_type": "老人"},
            {"name": "儿童留守", "target_type": "儿童", "child_type": "留守"},
            {"name": "儿童普通", "target_type": "儿童", "child_type": "一般"},
        ]
        filtered = filter_child_rows(rows, can_see=False)
        assert [r["name"] for r in filtered] == ["老人A", "儿童普通"]

    def test_can_see_children_admin(self):
        class U:
            role_id = 1
        assert can_see_children(U()) is True


# ============ 日志/导出/扫描 ============
class TestLogExportScan:
    def test_mask_text_chinese_adjacent(self):
        assert mask_text("证件340104199001011234电话13812345678") == "证件340104********1234电话138****5678"

    def test_mask_export_rows(self):
        headers = ["姓名", "身份证号", "联系电话", "银行卡号"]
        rows = [["张三", "340104199001011234", "13812345678", "6221881234567890"]]
        out = mask_export_rows(headers, rows, can_view_sensitive=False)
        assert out[0][1] == "340104********1234"
        assert out[0][2] == "138****5678"
        assert out[0][3] == "622188******7890"

    def test_mask_export_rows_admin_plaintext(self):
        headers = ["姓名", "身份证号"]
        rows = [["张三", "340104199001011234"]]
        out = mask_export_rows(headers, rows, can_view_sensitive=True)
        assert out[0][1] == "340104199001011234"

    def test_static_scan_text(self):
        hits = static_scan_text("340104199001011234 13812345678")
        assert hits["id_card"] == ["340104199001011234"]
        assert hits["phone"] == ["13812345678"]

    def test_scan_file(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "checks"))
        import scan_mask
        hits = scan_mask.scan_file("fake.html")
        assert hits == []


# ============ JSON 层 after_request 脱敏（集成） ============
class TestJsonLayerMasking:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _make_non_admin(self, app):
        from models import db, Role, User
        with app.app_context():
            role = Role(name="普通角色", permissions="{}")
            db.session.add(role)
            db.session.flush()
            user = User(username="普通用户", password_hash="x", role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            return user.id

    def test_non_admin_masked(self, app, monkeypatch):
        uid = self._make_non_admin(app)
        from flask import jsonify

        # 本用例只测 after_request 脱敏层。裸 app 级测试路由（endpoint='_t'）无模块
        # 映射，021 API-01 默认拒绝下会被判 __admin__——显式豁免鉴权层（生产 url_map
        # 无此类端点，审计见 021-RESULT §二）。
        import utils.permissions as perms_mod
        monkeypatch.setattr(perms_mod, "PUBLIC_ENDPOINTS",
                            set(perms_mod.PUBLIC_ENDPOINTS) | {"_t"})

        @app.route("/_test_sensitive")
        def _t():
            return jsonify({"success": True, "data": {"id_card": "340104199001011234", "phone": "13812345678"}})

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        data = client.get("/_test_sensitive").get_json()
        assert data["data"]["id_card"] == "340104********1234"
        assert data["data"]["phone"] == "138****5678"

    def test_admin_plaintext(self, app):
        from flask import jsonify

        @app.route("/_test_sensitive_admin")
        def _t2():
            return jsonify({"success": True, "data": {"id_card": "340104199001011234"}})

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1  # admin
        data = client.get("/_test_sensitive_admin").get_json()
        assert data["data"]["id_card"] == "340104199001011234"


# ============ 明文查看端点（resident_sensitive） ============
class TestResidentSensitive:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_admin_plaintext_and_logged(self, app):
        from models import db, Resident, OperationLog
        from utils.crypto import key_holder
        with app.app_context():
            # P2-2 整改后数据走字段级加密（对齐 R12 口径），明文兜底已移除
            key_holder.initialize("测试口令12345")
            r = Resident(name="张三")
            r.id_card_enc = key_holder.encrypt("340104199001011234")
            r.phone_enc = key_holder.encrypt("13812345678")
            db.session.add(r)
            db.session.commit()
            rid = r.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = 1  # admin
        resp = client.get(f"/resident/{rid}/sensitive?field=id_card")
        assert resp.status_code == 200
        assert resp.get_json()["data"]["id_card"] == "340104199001011234"
        with app.app_context():
            log = OperationLog.query.filter_by(action="view_sensitive").first()
            assert log is not None
            assert "340104199001011234" not in (log.detail or "")  # 只记字段名不记明文

    def test_non_admin_403(self, app):
        from models import db, Resident, Role, User
        from utils.crypto import key_holder
        with app.app_context():
            key_holder.initialize("测试口令12345")
            r = Resident(name="张三")
            r.id_card_enc = key_holder.encrypt("340104199001011234")
            db.session.add(r)
            role = Role(name="普通角色2", permissions="{}")
            db.session.add(role)
            db.session.flush()
            user = User(username="普通用户2", password_hash="x", role_id=role.id, is_active=True)
            db.session.add(user)
            db.session.commit()
            rid, uid = r.id, user.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        resp = client.get(f"/resident/{rid}/sensitive?field=id_card")
        assert resp.status_code == 403


# ============ mask_config 规则配置 ============
class TestMaskConfig:
    def test_default(self):
        assert get_mask_config() == DEFAULT_MASK_RULES

    def test_update_invalid_negative(self):
        with pytest.raises(ValueError):
            update_mask_config({"id_card": {"prefix": -1, "suffix": 4}})
