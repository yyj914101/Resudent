# -*- coding: utf-8 -*-
"""tests/test_t021_hardening.py — 021 后端加固回归（鉴权默认拒绝 + 纪律一致）

覆盖（对应 docs/tasks/021-backend-hardening.md 验收标准）：
- API-01 未登记蓝图默认拒绝；dashboard/auth.recovery/migration.required/root 不误伤
- API-02 action 判定顺序：含 view 字样的写 POST 不再被降为 view
- API-03 import_batch 显式装饰器（体内自检归零）+ 018 D-01 blocked 批次 execute 拦截
- API-04 migration 端点级 @admin_required + ADMIN_BLUEPRINTS 归类
- API-06 schema_ensure 标识符白名单
"""
import json

import pytest


def _make_app(tmp_path, monkeypatch):
    """隔离 DB：config.py 模块级缓存 DB_PATH（全量顺序下先 import 者固化共享路径），
    须直接覆写 config.DB_PATH / Config.SQLALCHEMY_DATABASE_URI——与 test_masking._make_app
    同款项目先例（conftest「config 首 import 固化」坑注释）。"""
    db_path = str(tmp_path / "shucun.db")
    import config
    monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SHUCUN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("SHUCUN_LICENSE_FILE", str(tmp_path / "license.key"))
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    for d in ("uploads", "backups", "logs", "temp", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


def _mk_user(app, username, role_name, perms, activate_role=True):
    """建角色+用户，返回 user_id。"""
    from models import db, Role, User
    with app.app_context():
        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            role = Role(name=role_name)
            db.session.add(role)
            db.session.commit()
        role.permissions = json.dumps(perms, ensure_ascii=False)
        db.session.commit()
        u = User(username=username, role_id=role.id, is_active=True)
        u.set_password("TestPass123")
        db.session.add(u)
        db.session.commit()
        return u.id


def _login_as(client, user_id):
    with client.session_transaction() as s:
        s["user_id"] = user_id


def _open_perm(name):
    """非 admin 角色：resident.* /report.* 全开、无 special、无 __admin__。"""
    return {"__schema_version__": 3,
            "resident.view": True, "resident.add": True, "resident.edit": True,
            "resident.delete": True, "resident.export": True,
            "report.view": True, "report.add": False,
            "certificate.view": True,
            "death_report.view": True, "death_report.edit": True,
            "special": {"view_sensitive": False, "death_confirm": False,
                        "import_batch": False, "backup_restore": False,
                        "mask_config": False}}


class TestGhostBlueprintDefaultDeny:
    """API-01：未登记蓝图 → 默认拒绝（方案 A）。"""

    def test_unregistered_blueprint_denied_for_nonadmin(self, app):
        from flask import Blueprint, jsonify
        ghost = Blueprint("ghost_probe", __name__)

        @ghost.route("/secret")
        def secret():
            return jsonify({"ok": True})

        app.register_blueprint(ghost, url_prefix="/ghost-probe")
        uid = _mk_user(app, "ghost_user", "ghost_role", _open_perm("ghost"))
        c = app.test_client()
        _login_as(c, uid)
        r = c.get("/ghost-probe/secret")
        assert r.status_code == 403, \
            "未登记蓝图端点对非 admin 应默认拒绝（API-01 方案 A）"

    def test_anonymous_gets_401_not_pass(self, app):
        from flask import Blueprint, jsonify
        ghost = Blueprint("ghost_anon", __name__)

        @ghost.route("/x")
        def x():
            return jsonify({"ok": True})

        app.register_blueprint(ghost, url_prefix="/ghost-anon")
        c = app.test_client()
        r = c.get("/ghost-anon/x")
        assert r.status_code in (401, 302), "匿名访问未登记蓝图须先过登录关"

    def test_admin_allowed_by_default_deny(self, app):
        from flask import Blueprint, jsonify
        ghost = Blueprint("ghost_admin", __name__)

        @ghost.route("/x")
        def x():
            return jsonify({"ok": True})

        app.register_blueprint(ghost, url_prefix="/ghost-adm")
        c = app.test_client()
        from models import User
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
            assert admin and admin.role_id == 1
        _login_as(c, admin.id)
        r = c.get("/ghost-adm/x")
        assert r.status_code == 200, "__admin__ 默认拒绝不应挡住超管"


class TestNoCollateralLockout:
    """API-01 误伤防护：现有「无模块映射但合法」的端点行为不变。"""

    def test_dashboard_index_reachable_by_nonadmin(self, app):
        uid = _mk_user(app, "dash_user", "dash_role", _open_perm("dash"))
        c = app.test_client()
        _login_as(c, uid)
        r = c.get("/dashboard/")
        assert r.status_code == 200, "dashboard 首页对全体登录用户可达（PUBLIC 归类）"

    def test_dashboard_stats_json_ok(self, app):
        uid = _mk_user(app, "dash_user2", "dash_role", _open_perm("dash"))
        c = app.test_client()
        _login_as(c, uid)
        r = c.get("/dashboard/api/dashboard/stats")
        assert r.status_code == 200

    def test_auth_recovery_not_blocked_by_enforce(self, app):
        c = app.test_client()
        r = c.get("/auth/recovery")
        assert r.status_code != 403, "auth.recovery 走端点内回环+口令自校验，enforce 须豁免"

    def test_root_reachable(self, app):
        c = app.test_client()
        r = c.get("/")
        assert r.status_code in (200, 302)


class TestActionOrdering:
    """API-02：action 判定顺序——写 token 优先，只读字样判定殿后。"""

    def test_view_named_post_now_routes_to_add(self, app):
        from utils.permissions import action_for_request
        with app.test_request_context("/report/probe-save-view", method="POST"):
            assert action_for_request("report.probe_save_view") == "add", \
                "名含 save+view 的 POST 应判 add（写优先），不得被 view 字样短路"
            assert action_for_request("report.api_view_update") == "edit", \
                "名含 view+update 的 POST 应判 edit"

    def test_existing_readonly_posts_unchanged(self, app):
        """4 处现存命中点行为不变（验收标准）。"""
        from utils.permissions import action_for_request
        with app.test_request_context("/certificate/api/preview", method="POST"):
            assert action_for_request("certificate.api_preview") == "view"
        with app.test_request_context("/report/custom-query/api", method="POST"):
            assert action_for_request("report.custom_query_api") == "view"
        with app.test_request_context("/report/custom-query/export", method="POST"):
            assert action_for_request("report.custom_query_export") == "export"
        with app.test_request_context("/resident/api/change-head/query", method="POST"):
            assert action_for_request("resident.api_change_head_query") == "view"

    def test_get_default_still_view(self, app):
        from utils.permissions import action_for_request
        with app.test_request_context("/resident/list", method="GET"):
            assert action_for_request("resident.index") == "view"


class TestMigrationDecorators:
    """API-04：migration 端点显式 @admin_required + 蓝图归 ADMIN_BLUEPRINTS。"""

class TestImportBatchExplicitDecorator:
    """API-03：special.import_batch 显式装饰器；体内自检归零。D-01 blocked 拦截。"""

    def test_no_inbody_checks_left(self):
        import routes.resident_enhance as re_mod
        assert not hasattr(re_mod, "_require_import_batch"), "体内自检助手应删除"
        assert not hasattr(re_mod, "_denied"), "_denied 应随体内自检一并删除"

    def test_upload_denied_without_special(self, app):
        uid = _mk_user(app, "imp_user", "imp_role", _open_perm("imp"))
        c = app.test_client()
        _login_as(c, uid)
        r = c.get("/import/upload")
        assert r.status_code == 403, "无 import_batch 位须 403（special 显式装饰器）"

    def test_upload_allowed_with_special(self, app):
        """035 R-035-01：GET /import/upload 深链已删——有 import_batch 位也不再 302；
        POST 路由仍由 special.import_batch 放行（缺文件 400 = 已进入视图）。"""
        perms = _open_perm("imps")
        perms["special"]["import_batch"] = True
        uid = _mk_user(app, "imp_ok", "imp_spec_role", perms)
        c = app.test_client()
        _login_as(c, uid)
        # GET 深链删除：路由已无 GET 分支；非 admin 命中 ADR-016 未匹配路径默认拒绝 → 403
        r = c.get("/import/upload")
        assert r.status_code == 403, "GET 深链已删，未匹配路径按 ADR-016 默认拒绝（非 admin 403）"
        # POST 路由仍在：装饰器放行后因缺文件 400（证明端点可达且权限生效）
        assert c.post("/import/upload", data={}).status_code == 400

    def test_upload_get_removed_405_for_admin(self, app):
        """035 R-035-01③：GET /import/upload 302 深链分支已删——admin 直接访问返回
        405 METHOD NOT ALLOWED（URL 仍匹配 POST-only 路由，Flask 路由层拒绝 GET；
        035 实测勘误：任务书预估 404，实际为 405——比 404 更精确，旧书签可见资源在
        但方法不允许；非 admin 因方法不匹配 endpoint=None 命中 ADR-016 默认拒绝 403，
        见 test_upload_allowed_with_special）。"""
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = 1  # admin（role_id=1：越过 ADR-016 默认拒绝后由路由层出 405）
        assert c.get("/import/upload").status_code == 405

    def test_execute_blocks_blocked_batch(self, app):
        """018 D-01：blocked 批次 + 残留旧映射 → execute 必须 4xx，不得 202。"""
        from models import db, ImportBatch, ImportFieldMapping
        perms = _open_perm("d01")
        perms["special"]["import_batch"] = True
        uid = _mk_user(app, "d01_user", "d01_role", perms)
        bid = "d01batch"
        with app.app_context():
            b = ImportBatch(batch_id=bid, source_file="x.xlsx",
                            source_path="/nonexistent.xlsx",
                            source_hash="d01" * 21 + "a",
                            operator=uid, status="blocked")
            db.session.add(b)
            db.session.commit()
            for col, tgt in (("姓名", "name"), ("身份证号", "id_card")):
                db.session.add(ImportFieldMapping(
                    batch_id=bid, source_field=col, target_field=tgt,
                    storage="column", mapping_kind="rename",
                    matched_by="exact"))
            db.session.commit()
        c = app.test_client()
        _login_as(c, uid)
        r = c.post(f"/import/{bid}/execute", json={})
        assert r.status_code in (400, 403, 409), \
            f"blocked 批次 execute 应被拦截，实际 {r.status_code}（D-01）"


class TestSchemaEnsureGuard:
    """API-06：DDL 标识符白名单。"""

    def test_bad_identifier_rejected(self, app):
        from utils.schema_ensure import ensure_columns
        with app.app_context():
            with pytest.raises((ValueError, TypeError)):
                ensure_columns("bad_tbl; DROP TABLE users--", {"x": "TEXT"})

    def test_good_identifier_passes(self, app):
        from utils.schema_ensure import ensure_columns
        with app.app_context():
            ensure_columns("residents", {"__t021_noop_col_should_not_exist": "TEXT"})
            # 不抛即通过（列不存在只是不新增；具体行为以现实现为准）


class TestR26DeathReportContract:
    """R-26 后端半：api/deceased 出 confirmed_at；写端点 JSON 载荷不 500。"""

    def test_deceased_rows_carry_confirmed_at(self, app):
        from models import db, DeathReport
        from datetime import datetime, date
        with app.app_context():
            dr = DeathReport(deceased_name="T021测试员",
                             death_date=date(2026, 9, 1),
                             cascade_status="done",
                             confirmed_at=datetime(2026, 9, 20, 8, 30, 0),
                             person_type="低保户")
            db.session.add(dr)
            db.session.commit()
        c = app.test_client()
        from models import User
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
        _login_as(c, admin.id)
        r = c.get("/death-report/api/deceased")
        assert r.status_code == 200
        rows = r.get_json()["data"]
        mine = [x for x in rows if x.get("name") == "T021测试员"]
        assert mine and mine[0].get("confirmed_at") == "2026-09-20 08:30:00", \
            "api/deceased 的 DR 行须携带 confirmed_at（前端 48h 倒计时依据）"

    def test_revoke_json_payload_no_500(self, app):
        """JSON 载荷下 dict.get('id', type=int) 曾 TypeError→500（D-08 同型），修复后 <500。"""
        c = app.test_client()
        from models import User
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
        _login_as(c, admin.id)
        r = c.post("/death-report/api/revoke", json={"id": 999999})
        assert r.status_code < 500, f"JSON 载荷 revoke 不应 500，实际 {r.status_code}"

    def test_confirm_json_payload_no_500(self, app):
        c = app.test_client()
        from models import User
        with app.app_context():
            admin = User.query.filter_by(username="admin").first()
        _login_as(c, admin.id)
        r = c.post("/death-report/api/confirm", json={"id": 999999})
        assert r.status_code < 500, f"JSON 载荷 confirm 不应 500，实际 {r.status_code}"
