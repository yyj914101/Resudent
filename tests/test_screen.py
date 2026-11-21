# -*- coding: utf-8 -*-
"""tests/test_screen.py — 048 大屏管理回归（双蓝图 + 公开 token 投屏 + ADR-016 归类）

覆盖（对齐 docs/tasks/048-screen-console.md 验收标准）：
- 权限归类：screen.page / screen.api_overview ∈ PUBLIC_ENDPOINTS（免登录）；screen_admin ∈ ADMIN_BLUEPRINTS
- 管理端：未登录 302/401；非 admin 403；admin 200（页面 + 配置读写 + token 轮换）
- 展示端：无/错 token 403；正确 token + 启用 200；未启用 403；api/overview 同步口径
- 安全：token 常量时间校验；身份证号脱敏；配置 KV 持久化
"""
import json
from html.parser import HTMLParser

import pytest


@pytest.fixture()
def app(isolated_app_factory):
    """GLB-17：统一隔离夹具工厂（替代本文件原手抄的 env + Config 覆写）。"""
    return isolated_app_factory()


def _mk_user(app, username, role_name, perms):
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


def _login_as(client, user_id, username="admin"):
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["username"] = username


def _admin_id(app):
    from models import User
    with app.app_context():
        return User.query.filter_by(username='admin').first().id


def _open_perm():
    return {"__schema_version__": 3,
            "resident.view": True,
            "special": {"view_sensitive": False, "death_confirm": False,
                        "import_batch": False, "backup_restore": False,
                        "mask_config": False}}


class TestPermissionClassification:
    """ADR-016：三选一归类；漏归=403。"""

    def test_public_endpoints_listed(self):
        from utils.permissions import PUBLIC_ENDPOINTS
        assert 'screen.page' in PUBLIC_ENDPOINTS
        assert 'screen.api_overview' in PUBLIC_ENDPOINTS

    def test_admin_blueprint_listed(self):
        from utils.permissions import ADMIN_BLUEPRINTS
        assert 'screen_admin' in ADMIN_BLUEPRINTS

    def test_all_screen_admin_endpoints_classified(self, app):
        """screen_admin 蓝图全部端点归 ADMIN_BLUEPRINTS（漏归=403 硬门禁）。"""
        from utils.permissions import ADMIN_BLUEPRINTS
        assert 'screen_admin' in ADMIN_BLUEPRINTS
        rules = [r for r in app.url_map.iter_rules()
                 if r.endpoint.startswith('screen_admin.')]
        assert rules, 'screen_admin 蓝图应注册端点'
        assert all(r.endpoint.startswith('screen_admin.') for r in rules)


class TestManageAccess:
    def test_manage_unauthenticated(self, app):
        c = app.test_client()
        r = c.get('/screen-admin/')
        assert r.status_code in (302, 401)

    def test_manage_nonadmin_denied(self, app):
        c = app.test_client()
        uid = _mk_user(app, 'plain', '普通角色', _open_perm())
        _login_as(c, uid, username='plain')
        assert c.get('/screen-admin/').status_code == 403
        assert c.get('/screen-admin/api/config').status_code == 403

    def test_manage_admin_ok(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        assert c.get('/screen-admin/').status_code == 200
        r = c.get('/screen-admin/api/config')
        assert r.status_code == 200
        assert r.get_json()['success'] is True


class TestPublicScreen:
    def _token(self, app):
        from routes.screen import _ensure_access_token
        with app.app_context():
            return _ensure_access_token()

    def test_screen_no_token_403(self, app):
        assert app.test_client().get('/screen/').status_code == 403

    def test_screen_bad_token_403(self, app):
        assert app.test_client().get('/screen/?token=wrong').status_code == 403

    def test_screen_valid_token_ok(self, app):
        token = self._token(app)
        r = app.test_client().get(f'/screen/?token={token}')
        assert r.status_code == 200
        assert '实时数据驾驶舱' in r.get_data(as_text=True)

    def test_screen_disabled_403(self, app):
        from routes.screen import _save_settings
        token = self._token(app)
        with app.app_context():
            _save_settings({'screen_enabled': '0'})
        assert app.test_client().get(f'/screen/?token={token}').status_code == 403

    def test_overview_valid_token(self, app):
        token = self._token(app)
        r = app.test_client().get(f'/screen/api/overview?token={token}')
        assert r.status_code == 200
        body = r.get_json()
        assert body['success'] is True
        assert 'total_population' in body['data']

    def test_overview_no_token_403(self, app):
        assert app.test_client().get('/screen/api/overview').status_code == 403


class TestConfigAndToken:
    def test_config_defaults(self, app):
        from routes.screen import _config
        with app.app_context():
            cfg = _config()
        assert cfg['enabled'] is True
        assert cfg['refresh_seconds'] == 60
        assert cfg['show_death_count'] is True

    def test_token_rotate_invalidates_old(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        old = self._old_token(app)
        r = c.post('/screen-admin/api/token/rotate')
        assert r.status_code == 200
        new = r.get_json()['data']['token']
        assert new != old
        # 旧 token 立即失效
        assert app.test_client().get(f'/screen/?token={old}').status_code == 403
        assert app.test_client().get(f'/screen/?token={new}').status_code == 200

    def _old_token(self, app):
        from routes.screen import _ensure_access_token
        with app.app_context():
            return _ensure_access_token()

    def test_config_save_persists(self, app):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.post('/screen-admin/api/config/save', data={
            'enabled': '1', 'village_name': '测试村', 'title': '测试大屏',
            'refresh_seconds': '120', 'work_start': '09:00', 'work_end': '17:00',
            'show_death_count': '0',
        })
        assert r.status_code == 200
        assert r.get_json()['success'] is True
        from routes.screen import _config
        with app.app_context():
            cfg = _config()
        assert cfg['village_name'] == '测试村'
        assert cfg['refresh_seconds'] == 120
        assert cfg['show_death_count'] is False


class TestMasking:
    def test_id_card_stripped(self):
        from routes.screen import _screen_safe_label
        out = _screen_safe_label('张三 110101199001011234 提醒')
        assert '110101' not in out and '19900101' not in out
        assert '张三' in out

    def test_label_truncated(self):
        from routes.screen import _screen_safe_label
        out = _screen_safe_label('很' * 60)
        assert len(out) <= 24

    def test_phone_and_address_stripped(self):
        from routes.screen import _screen_safe_label
        out = _screen_safe_label('三合村1组 13800138000 生日提醒 11010519491231002X')
        assert '13800138000' not in out
        assert '三合村1组' not in out
        assert '110105' not in out


# ---------------- 048-FIX：R-048-01 ~ 07 ----------------

def _token(app):
    from routes.screen import _ensure_access_token
    with app.app_context():
        return _ensure_access_token()


class TestFixPrivacy:
    def test_token_absent_from_dict_bundle(self, app):
        admin = app.test_client()
        _login_as(admin, _admin_id(app))
        token = admin.get('/screen-admin/api/config').get_json()['data']['token']

        c = app.test_client()
        uid = _mk_user(app, 'dictreader', '字典只读', dict(_open_perm(), **{'dict.view': True}))
        _login_as(c, uid, username='dictreader')
        r = c.get('/dict/api/bundle')
        assert r.status_code == 200
        body = r.get_json()['data']
        assert '系统设置' not in (body.get('bundle') or {})
        assert '系统设置' not in (body.get('order') or {})
        assert token not in r.get_data(as_text=True)
        assert c.get('/screen-admin/api/config').status_code == 403

    def test_public_overview_has_no_personal_data(self, app):
        from models import db, Resident, TodoItem
        name, phone, id_card, address = '隐私测试甲', '13800001234', '11010519491231002X', '隐私路88号'
        with app.app_context():
            resident = Resident(name=name, gender='男', id_card=id_card,
                                address=address, village_group='思榜', status='normal')
            db.session.add(resident)
            db.session.commit()
            db.session.add(TodoItem(
                title=f'{name} {phone} {id_card} {address} 提醒',
                todo_type='custom', status='todo', related_id=resident.id))
            db.session.commit()

        token = _token(app)
        r = app.test_client().get(f'/screen/api/overview?token={token}')
        assert r.status_code == 200
        text = r.get_data(as_text=True)
        for value in (name, phone, id_card, address):
            assert value not in text
        todos = r.get_json()['data']['screen_todos']
        assert todos and all('name' not in item for item in todos)


class TestFixDeathCount:
    def _count(self, app, resident, report):
        from datetime import date
        from models import db, DeathReport, Resident
        from routes.screen import _current_month_death_count
        with app.app_context():
            DeathReport.query.delete()
            Resident.query.filter_by(status='deceased').delete()
            db.session.commit()
            r = Resident(name=resident[0], status='deceased', status_date=date.today(),
                         id_card_hash=resident[1])
            db.session.add(r)
            db.session.add(DeathReport(deceased_name=report[0], id_card_hash=report[1],
                                       death_date=date.today()))
            db.session.commit()
            return _current_month_death_count()

    def test_hash_first_and_name_fallback(self, app):
        assert self._count(app, ('甲', 'h1'), ('甲', 'h1')) == 1      # 双 hash 同人
        assert self._count(app, ('甲', 'h1'), ('甲', '')) == 1        # 半迁移态同人
        assert self._count(app, ('甲', 'h1'), ('甲', 'h2')) == 2      # 同名不同 hash

    def test_death_count_switch_off(self, app):
        from routes.screen import _save_settings
        with app.app_context():
            _save_settings({'screen_show_death_count': '0'})
        token = _token(app)
        body = app.test_client().get(f'/screen/api/overview?token={token}').get_json()
        assert body['data']['current_month_death_count'] is None


# ---------------- 053：GLB-31/32/33/35/37 ----------------

class TestTodoPresentation:
    def test_glb31_glb32_labels_and_ops_filtered(self, app):
        """GLB-31：英文类型不上屏；GLB-32：对账/备份类运维待办不上公开大屏。"""
        from models import db, TodoItem
        with app.app_context():
            db.session.add_all([
                TodoItem(title='补贴到期', todo_type='warn', status='todo'),
                TodoItem(title='自定义事项', todo_type='custom', status='todo'),
                TodoItem(title='未映射类型', todo_type='unknown_en', status='todo'),
                TodoItem(title='对账任务', todo_type='reconcile', status='todo'),
                TodoItem(title='备份提醒', todo_type='backup', status='todo'),
            ])
            db.session.commit()
        token = _token(app)
        data = app.test_client().get(f'/screen/api/overview?token={token}').get_json()['data']
        # 运维类 2 条被过滤，计数与列表同口径
        assert data['todo_count'] == 3
        categories = [item['category'] for item in data['screen_todos']]
        assert '补贴预警' in categories
        assert '自定义' in categories
        assert '待办事项' in categories          # 未知类型兜底
        assert all('reconcile' not in c and 'backup' not in c for c in categories)


class TestScreenStructure:
    def test_glb33_structural_guard(self, app):
        """GLB-33：大屏独立页不做全文快照，改用 KPI/关键 id/无外链结构断言。"""
        token = _token(app)
        html = app.test_client().get(f'/screen/?token={token}').get_data(as_text=True)
        assert html.count('<article class="kpi"') == 8
        for anchor in ('id="population"', 'id="households"', 'id="party"', 'id="dibao"',
                       'id="disabled"', 'id="elderly"', 'id="specialSupport"', 'id="deaths"',
                       'id="groupChart"', 'id="todoTrack"'):
            assert anchor in html, anchor
        assert 'http://' not in html and 'https://' not in html


class TestMenuAndPaths:
    def test_glb35_nonadmin_sidebar_hides_screen_admin(self, app):
        """GLB-35：非 admin 侧栏不渲染「大屏管理」（整组 role_id==1 门控）。"""
        c = app.test_client()
        uid = _mk_user(app, 'menucheck', '菜单检查', _open_perm())
        _login_as(c, uid, username='menucheck')
        html = c.get('/dashboard/').get_data(as_text=True)
        sidebar = html.split('id="sidebar"', 1)[1].split('</aside>', 1)[0]
        assert '大屏管理' not in sidebar

    def test_glb35_admin_sidebar_has_screen_admin(self, app):
        """GLB-35（057a 修订）：系统管理入口沉到设置中心后，admin 的「大屏管理」在
        /settings/center 的入口卡里；侧栏改断言「设置入口可见、业务区不含系统管理」。"""
        c = app.test_client()
        _login_as(c, _admin_id(app))
        html = c.get('/dashboard/').get_data(as_text=True)
        sidebar = html.split('id="sidebar"', 1)[1].split('</aside>', 1)[0]
        assert '大屏管理' not in sidebar
        assert '/settings/center' in sidebar
        center = c.get('/settings/center').get_data(as_text=True)
        assert '大屏管理' in center and '/screen-admin' in center

    def test_glb37_manage_ajax_uses_script_root(self):
        """GLB-37：大屏管理页 AJAX 路径挂 script_root，不得写死绝对前缀。"""
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'templates', 'screen', 'manage.html')
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        assert 'request.script_root' in text
        assert "$.get('/screen-admin" not in text
        assert "$.post('/screen-admin" not in text
        assert "url='/screen-admin" not in text


class TestFixWorkTime:
    def _save(self, app, start, end):
        c = app.test_client()
        _login_as(c, _admin_id(app))
        return c.post('/screen-admin/api/config/save', data={
            'enabled': '1', 'refresh_seconds': '60',
            'work_start': start, 'work_end': end, 'show_death_count': '1',
        })

    def test_invalid_work_time_rejected_and_unchanged(self, app):
        from routes.screen import _config
        for start, end in (('99:99', 'zz'), ('abc', '')):
            r = self._save(app, start, end)
            assert r.get_json()['success'] is False
            assert 'HH:MM' in r.get_json()['msg']
            with app.app_context():
                assert _config()['work_start'] == '08:00'

    def test_valid_work_time_saved(self, app):
        from routes.screen import _config
        assert self._save(app, '00:00', '23:59').get_json()['success'] is True
        with app.app_context():
            cfg = _config()
        assert (cfg['work_start'], cfg['work_end']) == ('00:00', '23:59')
        assert self._save(app, '08:00', '08:00').get_json()['success'] is True


class TestFixScreenStructure:
    class _Balance(HTMLParser):
        VOID = {'meta', 'link', 'br', 'img', 'input', 'hr', 'source', 'area',
                'base', 'col', 'embed', 'param', 'track', 'wbr'}

        def __init__(self):
            super().__init__()
            self.stack, self.errors = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in self.VOID:
                return
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
            else:
                self.errors.append(tag)

    def test_screen_page_structure(self, app):
        token = _token(app)
        html = app.test_client().get(f'/screen/?token={token}').get_data(as_text=True)
        assert '<form' not in html.lower()
        assert html.count('<canvas') == 1
        assert html.count('class="kpi"') == 8
        parser = self._Balance()
        parser.feed(html)
        assert parser.stack == [] and parser.errors == []


class TestMultiScreen:
    """049-P2：每块大屏一个 token（device_registry device_type='screen'），可轮换/撤销/删除。"""

    def _admin(self, app):
        from models import User
        with app.app_context():
            uid = User.query.filter_by(username='admin').first().id
        c = app.test_client()
        _login_as(c, uid)
        return c

    def _kv_token(self, app):
        from routes.screen import _ensure_access_token
        with app.app_context():
            return _ensure_access_token()

    def _screens(self, app):
        from models import DeviceRegistry
        with app.app_context():
            return [{'id': r.id, 'name': r.label, 'status': r.status,
                     'token': __import__('json').loads(r.credential or '{}').get('token')}
                    for r in DeviceRegistry.query.filter_by(device_type='screen')
                    .order_by(DeviceRegistry.id.asc()).all()]

    def test_kv_token_valid_before_migration(self, app):
        """升级兼容：表内还没有大屏记录时，048 的 KV token 仍可投屏。"""
        token = self._kv_token(app)
        assert app.test_client().get(f'/screen/?token={token}').status_code == 200
        assert self._screens(app) == []          # 只读校验不建行

    def test_default_screen_migrates_kv_token(self, app):
        """管理员首次打开管理页：KV token 迁成「默认大屏」，旧地址不断。"""
        admin = self._admin(app)
        kv = self._kv_token(app)
        admin.get('/screen-admin/')
        rows = self._screens(app)
        assert len(rows) == 1
        assert rows[0]['name'] == '默认大屏'
        assert rows[0]['token'] == kv            # 复用旧 token
        assert app.test_client().get(f'/screen/?token={kv}').status_code == 200

    def test_create_second_screen(self, app):
        admin = self._admin(app)
        admin.get('/screen-admin/')
        r = admin.post('/screen-admin/api/screens', data={'name': '二楼会议室'})
        assert r.get_json()['success'] is True
        rows = self._screens(app)
        assert len(rows) == 2
        new_token = r.get_json()['data']['item']['token']
        assert app.test_client().get(f'/screen/?token={new_token}').status_code == 200
        # 旧屏不受影响
        assert app.test_client().get(f'/screen/?token={rows[0]["token"]}').status_code == 200

    def test_rotate_one_screen_isolated(self, app):
        admin = self._admin(app)
        admin.get('/screen-admin/')
        created = admin.post('/screen-admin/api/screens', data={'name': '二楼'}).get_json()['data']['item']
        first = self._screens(app)[0]
        r = admin.post(f'/screen-admin/api/screens/{created["id"]}/rotate')
        new_token = r.get_json()['data']['item']['token']
        assert new_token != created['token']
        assert app.test_client().get(f'/screen/?token={created["token"]}').status_code == 403
        assert app.test_client().get(f'/screen/?token={new_token}').status_code == 200
        # 另一块不受影响
        assert app.test_client().get(f'/screen/?token={first["token"]}').status_code == 200

    def test_revoke_one_screen_isolated(self, app):
        admin = self._admin(app)
        admin.get('/screen-admin/')
        created = admin.post('/screen-admin/api/screens', data={'name': '二楼'}).get_json()['data']['item']
        first = self._screens(app)[0]
        admin.post(f'/screen-admin/api/screens/{created["id"]}/revoke')
        assert app.test_client().get(f'/screen/?token={created["token"]}').status_code == 403
        assert app.test_client().get(f'/screen/?token={first["token"]}').status_code == 200
        assert self._screens(app)[1]['status'] == 'revoked'

    def test_delete_screen(self, app):
        admin = self._admin(app)
        admin.get('/screen-admin/')
        created = admin.post('/screen-admin/api/screens', data={'name': '临时'}).get_json()['data']['item']
        r = admin.delete(f'/screen-admin/api/screens/{created["id"]}')
        assert r.get_json()['success'] is True
        assert len(self._screens(app)) == 1
        assert app.test_client().get(f'/screen/?token={created["token"]}').status_code == 403

    def test_last_seen_recorded_after_visit(self, app):
        admin = self._admin(app)
        admin.get('/screen-admin/')
        token = self._screens(app)[0]['token']
        app.test_client().get(f'/screen/?token={token}')
        from models import DeviceRegistry
        with app.app_context():
            row = DeviceRegistry.query.filter_by(device_type='screen').first()
            assert row.last_seen is not None

    def test_screens_endpoint_requires_admin(self, app):
        assert app.test_client().get('/screen-admin/api/screens').status_code in (302, 401)
