"""T10 权限 Schema v3 单测 + 063 端点级归类（ADR-023）。"""
import os
import sys

import pytest

from utils.permissions import (
    PERMISSION_SCHEMA_KEY,
    PERMISSION_SCHEMA_VERSION,
    SPECIAL_KEYS,
    normalize_role_permissions,
    user_has_permission,
    user_has_special,
)


class FakeUser:
    def __init__(self, role_id):
        self.role_id = role_id


class TestNormalizeV3:
    def test_existing_module_actions_preserved(self):
        """v2→v3 继承（D8）：既有 module.action 键逐位不变。"""
        perms = {"resident.view": True, "resident.edit": False, "care.add": True}
        norm = normalize_role_permissions(perms)
        assert norm["resident.view"] is True
        assert norm["resident.edit"] is False
        assert norm["care.add"] is True

    def test_special_defaults_false(self):
        """special 键缺省全 false。"""
        norm = normalize_role_permissions({"resident.view": True})
        special = norm.get("special", {})
        for key in SPECIAL_KEYS:
            assert special[key] is False

    def test_special_exception_preserved(self):
        """民政主任例外：已置 true 的 special 位保留，缺省补 false。"""
        norm = normalize_role_permissions({"special": {"death_confirm": True}})
        assert norm["special"]["death_confirm"] is True
        assert norm["special"]["view_sensitive"] is False

    def test_schema_version_stamped(self):
        norm = normalize_role_permissions({})
        assert norm[PERMISSION_SCHEMA_KEY] == PERMISSION_SCHEMA_VERSION == 3


class TestSpecialCheck:
    def test_admin_full_pass(self):
        """admin（role_id=1）天然全通，special 不例外。"""
        admin = FakeUser(1)
        assert user_has_permission(admin, "special.death_confirm")
        assert user_has_special(admin, "view_sensitive")

    def test_civil_director_special(self, monkeypatch):
        user = FakeUser(2)
        perms = {"special": {"view_sensitive": True, "death_confirm": True}}
        monkeypatch.setattr("utils.permissions._load_permissions", lambda u: perms)
        assert user_has_permission(user, "special.death_confirm")
        assert user_has_special(user, "view_sensitive")
        assert not user_has_permission(user, "special.mask_config")

    def test_plain_role_no_special(self, monkeypatch):
        user = FakeUser(3)
        perms = {"resident.view": True, "special": {"death_confirm": False}}
        monkeypatch.setattr("utils.permissions._load_permissions", lambda u: perms)
        assert not user_has_permission(user, "special.death_confirm")
        assert not user_has_special(user, "death_confirm")


class TestCivilDirectorSeed:
    """首启建库须写入「民政主任」角色（special: view_sensitive + death_confirm）。"""

    @pytest.fixture()
    def app(self, isolated_app_factory):
        """GLB-05/17：隔离环境由 conftest 工厂统一建立（含 Config 类属性覆写）。"""
        return isolated_app_factory()

    def test_seed_civil_director_role(self, app):
        from models import Role
        import json
        with app.app_context():
            role = Role.query.filter_by(name="民政主任").first()
            assert role is not None
            perms = json.loads(role.permissions or "{}")
            special = perms.get("special", {})
            assert special.get("view_sensitive") is True
            assert special.get("death_confirm") is True
            # 民政模块权限已授予（resident/care/dibao 逐位）
            assert perms.get("resident.view") is True
            assert perms.get("care.edit") is True
            assert perms.get("dibao.export") is True
            # 非民政模块未授予
            assert perms.get("party.view") is not True


# ─── 063 / ADR-023：端点级权限归类（ENDPOINT_MODULES） ──────────────────

def _mk_clerk(app, username, role_name, perms):
    from models import db, Role, User
    import json
    with app.app_context():
        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            role = Role(name=role_name)
            db.session.add(role)
            db.session.commit()
        role.permissions = json.dumps(perms, ensure_ascii=False)
        db.session.commit()
        user = User(username=username, role_id=role.id, is_active=True)
        user.set_password("TestPass123")
        db.session.add(user)
        db.session.commit()
        return user.id


def _login_as(client, user_id):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["username"] = "clerk063"


def _url(app, endpoint):
    with app.test_request_context("/"):
        from flask import url_for
        return url_for(endpoint)


class TestEndpointModules063:
    """任务书 063 四例：端点级优先 / None 不绕过登录 / 漏归仍拒 / 蓝图级零变化。"""

    @pytest.fixture()
    def app(self, isolated_app_factory):
        return isolated_app_factory()

    def test_endpoint_level_overrides_blueprint_level(self, app):
        """验收 1+4：同一 settings 蓝图，center 200（仅 2 卡）、branding 仍 403。"""
        uid = _mk_clerk(app, "clerk063a", "clerk_role_063a",
                        {"dict.view": True, "resident.view": True})
        client = app.test_client()
        _login_as(client, uid)
        hub_path = _url(app, "settings.center")
        branding_path = _url(app, "settings.branding")

        hub = client.get(hub_path)
        assert hub.status_code == 200, "ENDPOINT_MODULES 应优先于 ADMIN_BLUEPRINTS"
        body = hub.get_data(as_text=True)
        assert body.count('class="sc-set-card"') == 2
        assert body.count("sc-set-group__label") == 1
        assert "字典管理" in body and "字段注册审批" in body
        assert "账号与权限" not in body and "界面与展示" not in body and "系统与审计" not in body

        assert client.get(branding_path).status_code == 403, "同蓝图其余端点仍 admin 专属"

    def test_none_entry_does_not_bypass_login(self, app):
        """验收 2：None 条目不绕过 @login_required——未登录仍 302 到登录页。"""
        resp = app.test_client().get(_url(app, "settings.center"))
        assert resp.status_code == 302
        assert "login" in resp.headers["Location"].lower()

    def test_unregistered_endpoint_falls_back_to_deny(self, app):
        """验收 3（红→绿）：删掉登记 → 回落 ADMIN_BLUEPRINTS 403；补回 → 200。"""
        from utils.permissions import ENDPOINT_MODULES

        uid = _mk_clerk(app, "clerk063b", "clerk_role_063b",
                        {"dict.view": True, "resident.view": True})
        client = app.test_client()
        _login_as(client, uid)
        hub_path = _url(app, "settings.center")

        saved = ENDPOINT_MODULES.pop("settings.center")
        try:
            assert client.get(hub_path).status_code == 403, "漏归端点必须默认拒绝"
        finally:
            ENDPOINT_MODULES["settings.center"] = saved

        assert client.get(hub_path).status_code == 200, "补回登记后端点级归类生效"

    def test_blueprint_level_behaviour_unchanged(self, app):
        """验收 4：ADMIN_BLUEPRINTS / BLUEPRINT_MODULES 既有行为零变化。"""
        uid_ok = _mk_clerk(app, "clerk063c", "clerk_role_063c",
                           {"dict.view": True, "resident.view": True})
        client_ok = app.test_client()
        _login_as(client_ok, uid_ok)

        assert client_ok.get(_url(app, "backup.index")).status_code == 403, "ADMIN_BLUEPRINTS 不变"
        assert client_ok.get(_url(app, "resident.list")).status_code == 200, "BLUEPRINT_MODULES 不变"

        uid_no = _mk_clerk(app, "clerk063d", "clerk_role_063d", {"dict.view": True})
        client_no = app.test_client()
        _login_as(client_no, uid_no)
        assert client_no.get(_url(app, "resident.list")).status_code == 403, "无模块位仍 403"


class TestHubCardTargets063Fix:
    """063-FIX：设置中心两卡目标页对模块位开放，写操作/审批仍 admin（review-063 F-1）。"""

    @pytest.fixture()
    def app(self, isolated_app_factory):
        return isolated_app_factory()

    def _clerk(self, app, username):
        """模块位全开：确保 403 只可能来自动作级 @admin_required，而非模块位缺失。"""
        return _mk_clerk(app, username, f"role_{username}", {
            "dict.view": True, "dict.add": True, "dict.edit": True, "dict.delete": True,
            "resident.view": True, "resident.edit": True,
        })

    def test_dict_page_open_to_dict_view_writes_still_admin(self, app):
        client = app.test_client()
        _login_as(client, self._clerk(app, "clerk063f1"))

        page = client.get(_url(app, "dict_mgmt.list"))
        assert page.status_code == 200, "字典管理页应对 dict.view 开放"
        assert "字典管理" in page.get_data(as_text=True)

        assert client.get(_url(app, "dict_mgmt.api_categories")).status_code == 403
        assert client.post("/dict/api/item/save",
                           data={"category_id": 1, "label": "x"}).status_code == 403

    def test_registry_page_open_to_resident_view_approval_still_admin(self, app):
        client = app.test_client()
        _login_as(client, self._clerk(app, "clerk063f2"))

        page = client.get(_url(app, "resident_enhance.import_registry"))
        assert page.status_code == 200, "字段注册审批页应对 resident.view 开放"
        assert "字段注册审批" in page.get_data(as_text=True)

        assert client.post("/import/registry/家庭收入/approve").status_code == 403
        assert client.post("/import/registry/家庭收入/reject").status_code == 403

    def test_admin_behaviour_unchanged(self, app):
        from models import User
        with app.app_context():
            admin_id = User.query.filter_by(username="admin").first().id
        client = app.test_client()
        _login_as(client, admin_id)

        assert client.get(_url(app, "dict_mgmt.list")).status_code == 200
        assert client.get(_url(app, "resident_enhance.import_registry")).status_code == 200
