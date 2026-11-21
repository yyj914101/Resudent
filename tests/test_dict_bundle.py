# -*- coding: utf-8 -*-
"""tests/test_dict_bundle.py — 027 字典 Bundle + g 请求级缓存回归

覆盖（对应 docs/tasks/027-crudtable-render.md 验收标准）：
- Bundle 形状 {分类名: {value: label}} + 版本号协商（unchanged）+ 内容哈希 bump
- ADR-016 归类：dict_mgmt 从 ADMIN_BLUEPRINTS 迁 BLUEPRINT_MODULES['dict']（dict.view）
- 管理端点仍 admin 锁定（非 admin 即便持 dict.add 也 403）
- get_dict_items 请求级缓存（同请求同分类单次查询）
"""
import json

import pytest

from models import DictCategory, DictItem, db


def _make_app(tmp_path, monkeypatch):
    """隔离 DB（test_t021_hardening._make_app 同款先例）。"""
    db_path = str(tmp_path / "shucun.db")
    import config
    for key, val in {
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_LOG_DIR": str(tmp_path / "logs"),
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_LICENSE_FILE": str(tmp_path / "license.key"),
    }.items():
        monkeypatch.setenv(key, val)
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


def _mk_user(app, username, role_name, perms):
    from models import Role, User
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


def _seed_dict(app):
    """造一个分类两个条目，返回 (category_id)。"""
    with app.app_context():
        cat = DictCategory(name="\u6d4b\u8bd5\u5206\u7c7b", is_active=True, sort_order=0)
        db.session.add(cat)
        db.session.commit()
        for label, value, order in (("\u5728\u804c", "1", 0), ("\u79bb\u804c", "2", 1)):
            db.session.add(DictItem(category_id=cat.id, label=label, value=value,
                                    sort_order=order, is_active=True))
        db.session.commit()
        return cat.id


def _admin_id(app):
    from models import User
    with app.app_context():
        return User.query.filter_by(username="admin").first().id


class TestBundleShapeAndVersion:
    def test_bundle_shape_and_unchanged(self, app):
        _seed_dict(app)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.get("/dict/api/bundle")
        assert r.status_code == 200
        d = r.get_json()["data"]
        assert d["unchanged"] is False and d["v"]
        assert d["bundle"]["\u6d4b\u8bd5\u5206\u7c7b"] == {"1": "\u5728\u804c", "2": "\u79bb\u804c"}
        r2 = c.get("/dict/api/bundle?v=%s" % d["v"])
        assert r2.status_code == 200
        d2 = r2.get_json()["data"]
        assert d2["unchanged"] is True and "bundle" not in d2

    def test_version_bumps_on_label_edit(self, app):
        cat_id = _seed_dict(app)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        v1 = c.get("/dict/api/bundle").get_json()["data"]["v"]
        with app.app_context():
            it = DictItem.query.filter_by(category_id=cat_id, value="1").first()
            it.label = "\u5728\u804c\u6539"
            db.session.commit()
        r = c.get("/dict/api/bundle?v=%s" % v1)
        d = r.get_json()["data"]
        assert d["unchanged"] is False and d["v"] != v1, \
            "保存字典后内容哈希版本必须 bump（R4 版本失效）"


class TestAdr016Classification:
    def test_dict_mgmt_moved_to_module(self):
        from utils.permissions import ADMIN_BLUEPRINTS, BLUEPRINT_MODULES
        assert "dict_mgmt" not in ADMIN_BLUEPRINTS
        assert BLUEPRINT_MODULES.get("dict_mgmt") == "dict"

    def test_anonymous_bundle_401(self, app):
        c = app.test_client()
        r = c.get("/dict/api/bundle")
        assert r.status_code == 401

    def test_nonadmin_without_dict_view_403(self, app):
        perms = {"__schema_version__": 3, "resident.view": True,
                 "special": {"view_sensitive": False, "death_confirm": False,
                             "import_batch": False, "backup_restore": False,
                             "mask_config": False}}
        uid = _mk_user(app, "nodict", "nodict_role", perms)
        c = app.test_client()
        _login_as(c, uid)
        assert c.get("/dict/api/bundle").status_code == 403

    def test_nonadmin_with_dict_view_200(self, app):
        perms = {"__schema_version__": 3, "dict.view": True,
                 "special": {"view_sensitive": False, "death_confirm": False,
                             "import_batch": False, "backup_restore": False,
                             "mask_config": False}}
        uid = _mk_user(app, "dictview", "dictview_role", perms)
        c = app.test_client()
        _login_as(c, uid)
        assert c.get("/dict/api/bundle").status_code == 200

    def test_management_endpoints_still_admin(self, app):
        """非 admin 持 dict.add 仍须 403（端点级 @admin_required 锁定，分类迁移不放行）。"""
        perms = {"__schema_version__": 3, "dict.view": True, "dict.add": True,
                 "dict.edit": True, "dict.delete": True,
                 "special": {"view_sensitive": False, "death_confirm": False,
                             "import_batch": False, "backup_restore": False,
                             "mask_config": False}}
        uid = _mk_user(app, "dictwrite", "dictwrite_role", perms)
        c = app.test_client()
        _login_as(c, uid)
        r = c.post("/dict/api/item/save", json={"category_id": 1, "label": "x"})
        assert r.status_code == 403


class TestRequestLevelCache:
    def test_same_category_single_query(self, app):
        _seed_dict(app)
        from sqlalchemy import event
        from utils.helpers import get_dict_items
        calls = [0]
        with app.app_context():
            event.listen(db.engine, "before_cursor_execute", lambda *a, **k: calls.__setitem__(0, calls[0] + 1))
            with app.test_request_context("/x"):
                first = get_dict_items("\u6d4b\u8bd5\u5206\u7c7b")
                second = get_dict_items("\u6d4b\u8bd5\u5206\u7c7b")
                assert first is second, "同请求同分类应命中 g 缓存（同一列表对象）"
                third = get_dict_items("\u4e0d\u5b58\u5728\u5206\u7c7b")
                assert third == []
            # 存在分类 2 条 SQL（分类 + 条目）、不存在分类 1 条 SQL（仅分类）；缓存命中零增查
            assert calls[0] == 3, f"预期 3 次查询（1 分类 × 2 + 1 空分类），实际 {calls[0]}"

class TestOrderShapeAndSort:
    """033：bundle 顶层 order 有序结构 = 下拉顺序唯一真源。"""

    def _bundle(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        return c.get("/dict/api/bundle").get_json()["data"]

    def test_order_shape_and_seed_first_items(self, app):
        """种子库三个分类首项恢复 sort_order（029 P3 #2 见证值）。"""
        d = self._bundle(app)
        order = d["order"]
        assert isinstance(order, dict) and "bundle" in d
        # 形状：有序数组 [{value,label},...]，与 bundle 映射同长
        for cat_name, arr in order.items():
            assert isinstance(arr, list)
            assert arr and isinstance(arr[0], dict) and {"value", "label"} <= set(arr[0])
            assert len(arr) == len(d["bundle"][cat_name])
        assert order["与户主关系"][0] == {"value": "户主本人", "label": "户主本人"}
        assert order["婚姻状况"][0]["label"] == "未婚"
        assert order["民族"][0]["label"] == "汉"

    def test_order_matches_get_dict_items(self, app):
        """order 与 get_dict_items() 的 sort_order 逐项一致（顺序唯一真源双源对齐）。"""
        cat_id = _seed_dict(app)  # 在职("1",0) / 离职("2",1)
        d = self._bundle(app)
        arr = d["order"]["测试分类"]
        assert arr == [{"value": "1", "label": "在职"}, {"value": "2", "label": "离职"}]
        from utils.helpers import get_dict_items
        with app.app_context():
            with app.test_request_context("/x"):
                items = get_dict_items("测试分类")
                assert [{"value": str(i.value), "label": i.label} for i in items] == arr

    def test_numeric_value_order_not_reordered(self, app):
        """P3 #3：数字串 value 在 order 数组中保持 sort_order（JS 整形键重排问题被结构规避）。"""
        cat_id = _seed_dict(app)
        with app.app_context():
            # 追加 "10"（首位为数字的 key：映射枚举路径下会被 JS 按整数键提前）
            db.session.add(DictItem(category_id=cat_id, label="十号", value="10",
                                    sort_order=9, is_active=True))
            db.session.commit()
        d = self._bundle(app)
        arr = d["order"]["测试分类"]
        assert [x["value"] for x in arr] == ["1", "2", "10"]
        assert arr[-1] == {"value": "10", "label": "十号"}
        # bundle 映射原样保留（老消费方 label() 零改动）
        assert d["bundle"]["测试分类"]["10"] == "十号"

    def test_version_changed_when_order_shape_added(self, app):
        """版本协商：order 参与哈希（形状变化 → 版本变化 → 旧缓存自动失效）。"""
        _seed_dict(app)
        d = self._bundle(app)
        v1 = d["v"]
        # 模拟 033 上线前旧客户端缓存版本：仅 bundle 哈希
        import hashlib
        import json as _json
        old_v = hashlib.md5(_json.dumps({"bundle": {}}, ensure_ascii=False,
                                        sort_keys=True).encode("utf-8")).hexdigest()
        assert v1 != old_v
