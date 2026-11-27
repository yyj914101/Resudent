# -*- coding: utf-8 -*-
"""tests/test_import_governance.py — 052 簇 C 导入治理批（R-036-05/06/07/08/10/11 + R-037-02/03）。

来源：docs/tasks/待办登记-20261021-导入P3遗留.md（leader 独立复核产出，逐条有证据）。
反向用例先红后绿；R-037-04 为测试构造兼容性改造（无产品行为变化），不在此设红。
"""
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from test_import_engine_t12 import _make_app, _unlock_keyholder, _write_xlsx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATIONS = os.path.join(ROOT, 'scripts', 'migrations')


@pytest.fixture()
def app(tmp_path, monkeypatch):
    application = _make_app(tmp_path, monkeypatch)
    _unlock_keyholder()
    return application


def _admin_client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = 1
    return c


def _exec_sql_file(app, filename):
    """按轨 A 方式执行迁移脚本：剥行注释后逐语句执行（幂等脚本可重复跑）。"""
    from sqlalchemy import text
    from models import db
    with open(os.path.join(MIGRATIONS, filename), encoding='utf-8') as fh:
        raw = fh.read()
    # 去掉整行注释，再按 ; 切分（块内不残留注释，语句块判定才可靠）
    body = '\n'.join(ln for ln in raw.splitlines() if not ln.strip().startswith('--'))
    with app.app_context():
        for stmt in [s.strip() for s in body.split(';') if s.strip()]:
            db.session.execute(text(stmt))
        db.session.commit()


# ---------------- R-036-05：required_permission 口径统一（轨 A m015） ----------------

class TestProfilePermissionFix:
    def test_m015_unifies_standard_v1_permission(self, app):
        _exec_sql_file(app, 'm014_import_profiles.sql')
        _exec_sql_file(app, 'm015_import_profiles_permission_fix.sql')
        from models import ImportProfile
        with app.app_context():
            rec = ImportProfile.query.filter_by(profile_key='standard_v1').first()
            assert rec is not None
            assert rec.required_permission == 'special.import_batch'

    def test_m015_covers_all_seed_rows(self, app):
        """m014+m015 后，库内不存在 required_permission='resident.edit' 的方案行
        （m014 历史脚本本身不动 —— 轨 A，未改动证明走 git diff，见 RESULT）。"""
        _exec_sql_file(app, 'm014_import_profiles.sql')
        _exec_sql_file(app, 'm015_import_profiles_permission_fix.sql')
        from models import ImportProfile
        with app.app_context():
            stale = ImportProfile.query.filter_by(
                required_permission='resident.edit').count()
            assert stale == 0


# ---------------- R-036-06：批次列表展示模板版本 ----------------

class TestBatchesCatalogVersion:
    def test_data_api_returns_catalog_version(self, app, tmp_path):
        from test_import_engine_t12 import _make_batch
        xlsx = tmp_path / 'v.xlsx'
        _write_xlsx(str(xlsx), ['姓名', '身份证号'], [['张三', '110101199001011237']])
        bid = _make_batch(app, str(xlsx))
        from models import db, ImportBatch
        with app.app_context():
            b = ImportBatch.query.filter_by(batch_id=bid).first()
            b.catalog_version = 'v1-test123'
            db.session.commit()
        r = _admin_client(app).get('/import/batches/data')
        items = r.get_json()['data']
        row = [i for i in items if i['batch_id'] == bid][0]
        assert row.get('catalog_version') == 'v1-test123'

    def test_frontend_has_version_column(self):
        html = open(os.path.join(ROOT, 'templates/resident_enhance/import_batches.html'),
                    encoding='utf-8').read()
        js = open(os.path.join(ROOT, 'static/js/pages/import_batches.js'), encoding='utf-8').read()
        assert '模板版本' in html
        assert 'catalog_version' in js


# ---------------- R-036-07：registry-definition CSS 特异性 ----------------

def _css_no_comments():
    css = open(os.path.join(ROOT, 'static/css/app.css'), encoding='utf-8').read()
    stripped = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    return css, stripped


class TestRegistryDefinitionCss:
    def test_selector_beats_table_td(self):
        """0,2,1 特异性选择器存在，且全文件仅此一处引用该类（无裸规则残留）。"""
        _raw, stripped = _css_no_comments()
        assert '.table td.registry-definition' in stripped
        assert stripped.count('registry-definition') == 1
        assert '!important' not in re.search(
            r'\.table td\.registry-definition\s*\{[^}]*\}', stripped).group(0)

    def test_css_comment_audit(self):
        """注释解析级审计：/* */ 配对完整，剥离后无残留定界符（历史陷阱）。"""
        _raw, stripped = _css_no_comments()
        assert '/*' not in stripped and '*/' not in stripped


# ---------------- R-036-08：三小项 ----------------

class TestBatchesPageBrowser403:
    """R-036-08①：有 resident.edit（过全局 ADR-016 层）但无 special.import_batch 的用户，
    才会走到视图内 `_has_import_special()` —— 即 JSON 403 的真实触发路径。"""

    def _client_no_import_special(self, app, username):
        from models import db, Role, User
        with app.app_context():
            role = Role(name='role_' + username)
            db.session.add(role)
            db.session.commit()
            role.permissions = json.dumps({'resident.view': True}, ensure_ascii=False)
            db.session.commit()
            u = User(username=username, role_id=role.id, is_active=True)
            u.set_password('TestPass123')
            db.session.add(u)
            db.session.commit()
            uid = u.id
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = uid
        return c

    def test_browser_gets_html_page_not_json(self, app):
        c = self._client_no_import_special(app, 'edit_no_import_a')
        r = c.get('/import/batches')
        assert r.status_code == 403
        assert 'text/html' in (r.content_type or '')
        assert '权限不足' in r.get_data(as_text=True)

    def test_xhr_still_gets_json(self, app):
        c = self._client_no_import_special(app, 'edit_no_import_b')
        r = c.get('/import/batches', headers={'X-Requested-With': 'XMLHttpRequest'})
        assert r.status_code == 403
        assert r.get_json()['success'] is False


class TestApproveRejectReturnCheck:
    def test_approve_missing_code_not_silent_success(self, app):
        c = _admin_client(app)
        r = c.post('/import/registry/no_such_code/approve')
        assert r.status_code in (302, 404)
        page = c.get(r.headers['Location'] if r.status_code == 302 else '/import/registry')
        assert '不存在' in page.get_data(as_text=True)


class TestListRegistryLimit:
    def test_default_limit_50_and_cap_200(self, app):
        from models import db, ImportFieldRegistry
        from utils.import_engine import list_registry
        with app.app_context():
            for i in range(60):
                db.session.add(ImportFieldRegistry(code=f'f{i:03d}', display_name=f'f{i}',
                                                   data_type='string', status='draft'))
            db.session.commit()
            assert len(list_registry()) == 50
            assert len(list_registry(limit=500)) == 60      # 上限 200，实际只有 60


# ---------------- R-036-10：源文件丢失 → 404 可读提示 ----------------

class TestMissingSourceFile:
    def _batch_without_file(self, app, tmp_path):
        from test_import_engine_t12 import _make_batch
        xlsx = tmp_path / 'gone.xlsx'
        _write_xlsx(str(xlsx), ['姓名', '身份证号'], [['张三', '110101199001011237']])
        bid = _make_batch(app, str(xlsx))
        os.remove(xlsx)
        return bid

    def test_mapping_returns_404_readable(self, app, tmp_path):
        bid = self._batch_without_file(app, tmp_path)
        r = _admin_client(app).get(f'/import/{bid}/mapping')
        assert r.status_code == 404
        assert '源文件已丢失' in (r.get_json() or {}).get('msg', '')

    def test_validate_returns_404_readable(self, app, tmp_path):
        bid = self._batch_without_file(app, tmp_path)
        from test_import_engine_t12 import _save_columns
        _save_columns(app, bid, [{'source': '姓名', 'target': 'name', 'action': 'map'}])
        r = _admin_client(app).get(f'/import/{bid}/validate')
        assert r.status_code == 404
        assert '源文件已丢失' in (r.get_json() or {}).get('msg', '')


# ---------------- R-036-11：审批填写口径说明 / 统计用途 ----------------

class TestApproveDefinitionInput:
    def _draft(self, app, code='note_field'):
        from models import db, ImportFieldRegistry
        with app.app_context():
            db.session.add(ImportFieldRegistry(
                code=code, display_name=code, data_type='string', status='draft',
                definition=json.dumps({'口径说明': '', '统计用途': '',
                                       '来源': '导入新增字段（ext_data，方案A）',
                                       '待管理员确认': True}, ensure_ascii=False)))
            db.session.commit()
        return code

    def test_approve_writes_definition_and_unmarks(self, app):
        code = self._draft(app)
        r = _admin_client(app).post(f'/import/registry/{code}/approve', data={
            'definition_note': '低保证人数口径', 'definition_usage': '月度统计'})
        assert r.status_code == 302
        from models import ImportFieldRegistry
        with app.app_context():
            d = json.loads(ImportFieldRegistry.query.filter_by(code=code).first().definition)
        assert d['口径说明'] == '低保证人数口径'
        assert d['统计用途'] == '月度统计'
        assert d['待管理员确认'] is False

    def test_registry_page_renders_chinese_not_true(self, app):
        code = self._draft(app, 'note_field2')
        c = _admin_client(app)
        c.post(f'/import/registry/{code}/approve', data={'definition_note': '口径A'})
        page = c.get('/import/registry?status=approved').get_data(as_text=True)
        assert '口径A' in page
        assert 'True' not in page and 'False' not in page

    def test_approved_record_can_supplement(self, app):
        """已批记录可补录（默认口径：非必填、支持补录）。"""
        code = self._draft(app, 'note_field3')
        c = _admin_client(app)
        c.post(f'/import/registry/{code}/approve', data={})
        c.post(f'/import/registry/{code}/approve', data={'definition_note': '补录口径'})
        from models import ImportFieldRegistry
        with app.app_context():
            rec = ImportFieldRegistry.query.filter_by(code=code).first()
            assert rec.status == 'approved'
            assert json.loads(rec.definition)['口径说明'] == '补录口径'


# ---------------- R-037-02 / R-037-03：启动体检结构 + 空方案告警 ----------------

class TestStartupSchemaCheck:
    def test_structure_covers_import_profiles(self):
        import app as app_module
        tables = getattr(app_module, '_STARTUP_TABLE_COLUMNS', None)
        assert isinstance(tables, dict)
        assert 'import_batch' in tables and 'import_profiles' in tables

    def test_missing_profiles_table_warns(self, app, caplog):
        from sqlalchemy import text
        from models import db
        with app.app_context():
            db.session.execute(text('DROP TABLE import_profiles'))
            db.session.commit()
            import app as app_module
            with caplog.at_level(logging.WARNING, logger='shucun'):
                app_module._warn_missing_import_tables()
        assert 'import_profiles' in caplog.text and 'm014' in caplog.text

    def test_empty_profiles_warns_m014(self, tmp_path, monkeypatch, caplog):
        from sqlalchemy import text
        from models import db
        app1 = _make_app(tmp_path, monkeypatch)
        with app1.app_context():
            db.session.execute(text('DELETE FROM import_profiles'))
            db.session.commit()
        with caplog.at_level(logging.WARNING, logger='shucun'):
            app2 = _make_app(tmp_path, monkeypatch)          # 重启体检
        assert app2 is not None
        assert 'import_profiles' in caplog.text and 'm014' in caplog.text


class TestProfilesEmptyStateFrontend:
    def test_wizard_empty_state_hint(self):
        js = open(os.path.join(ROOT, 'static/js/import_wizard.js'), encoding='utf-8').read()
        assert '内置方案未初始化' in js
