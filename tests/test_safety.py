# -*- coding: utf-8 -*-
"""tests/test_safety.py — 030 安全工作模块·一期核心闭环回归

覆盖（对齐 030 任务书验收标准）：
- 三触点归类：safety 进 BLUEPRINT_MODULES（url_map 审计零未归类）；非 admin 按模块位授权
- 种子：20 类一级节点按方案 §一顺序；m012 幂等可重跑
- 树懒加载：children 分块（50 限流 + 加载更多）；子计数 / has_children；path 父链
- 列表：20/页分页、目录优先；folder 新建（同名拒绝/空名/超长）、删除（仅空目录）
- 上传：白名单三类、单文件上限、同名提示不阻断、写 OperationLog
- 下载/预览：attachment 原名、行内仅图片/PDF、防路径穿越（伪造 stored_name 拒绝）
- 搜索：全树匹配带完整路径、空关键词拒绝
- 红线：uploads/safety 不加入 UPLOAD_SUBDIR_PERM_MAP（直链 403）
"""
import io
import json

import pytest


def _make_app(tmp_path, monkeypatch):
    """隔离 DB（与 test_db_admin._make_app 同款先例：覆写 config 缓存路径 + 类属性）。"""
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
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "backups"))
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


def _seed(app):
    # 087 返工 B4/B5 覆盖缺口收口：真实运行态含 m012 档案种子 + m028 种子
    # （档案根目录「应急工作方案」+ 检查枚举 dict_items），seed 须与之一致
    from routes.safety import _apply_m012
    from routes.safety_emergency import _apply_m028
    with app.app_context():
        _apply_m012()
        _apply_m028()


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


def _login_as(client, user_id, username="clerk01"):
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["username"] = username


def _open_perm():
    return {"__schema_version__": 3,
            "resident.view": True,
            "special": {"view_sensitive": False, "death_confirm": False,
                        "import_batch": False, "backup_restore": False,
                        "mask_config": False}}


def _full_safety_perm():
    p = _open_perm()
    for a in ("view", "add", "edit", "delete", "export"):
        p[f"safety.{a}"] = True
    return p


def _root_categories(app):
    from models import db
    from models.safety import SafetyNode
    with app.app_context():
        return [n.name for n in SafetyNode.query
                .filter(SafetyNode.parent_id.is_(None))
                .order_by(SafetyNode.sort_order).all()]


class TestClassification:
    """验收：safety 全部端点三选一归类（漏归=默认拒绝）。"""

    def test_blueprint_module_mapping(self, app):
        from utils.permissions import BLUEPRINT_MODULES, PUBLIC_ENDPOINTS, ADMIN_BLUEPRINTS
        assert BLUEPRINT_MODULES.get('safety') == 'safety'
        rules = [r for r in app.url_map.iter_rules() if r.endpoint.startswith('safety.')]
        assert rules, 'safety 蓝图应注册端点'
        for r in rules:
            full = r.endpoint
            assert full not in PUBLIC_ENDPOINTS, f'{full} 不应进入 PUBLIC_ENDPOINTS'
            bp = full.rsplit('.', 1)[0]
            assert bp not in ADMIN_BLUEPRINTS, f'{full} 不应归 ADMIN_BLUEPRINTS'
            assert bp in BLUEPRINT_MODULES, f'{full} 未归 BLUEPRINT_MODULES'

    def test_get_view_post_edit_mapping(self, app):
        from utils.permissions import required_permission_for_request
        with app.test_request_context('/safety/'):
            assert required_permission_for_request() == 'safety.view'
        with app.test_request_context('/safety/api/folder', method='POST'):
            assert required_permission_for_request() == 'safety.edit'
        with app.test_request_context('/safety/api/file/upload', method='POST'):
            assert required_permission_for_request() == 'safety.add'
        with app.test_request_context('/safety/file/1/download'):
            assert required_permission_for_request() == 'safety.view'

    def test_031_mapping(self, app):
        """031 新端点归类：batch_delete→delete（命名含 delete 自动升级）、batch_move→edit、flat/zip GET→view。"""
        from utils.permissions import required_permission_for_request
        with app.test_request_context('/safety/api/file/batch_delete', method='POST'):
            assert required_permission_for_request() == 'safety.delete'
        with app.test_request_context('/safety/api/file/batch_move', method='POST'):
            assert required_permission_for_request() == 'safety.edit'
        with app.test_request_context('/safety/api/flat'):
            assert required_permission_for_request() == 'safety.view'
        with app.test_request_context('/safety/api/zip'):
            assert required_permission_for_request() == 'safety.view'


class TestSeeds:
    def test_21_root_categories_in_order(self, app):
        _seed(app)
        names = _root_categories(app)
        assert len(names) == 21
        assert names == ['安全隐患', '防汛减灾', '工作简报', '急难愁盼', '建筑安全',
                         '交通资料', '矛盾纠纷', '溺水安全', '其他资料', '燃气资料',
                         '人社资料', '人员信息', '森林防火', '社区图片', '食品安全',
                         '水利资料', '通知模板', '消防资料', '信访维稳', '征兵资料',
                         '应急工作方案']  # 087 m028：方案归档位（尾位 sort_order 21）

    def test_seed_idempotent(self, app):
        _seed(app)
        _seed(app)
        assert len(_root_categories(app)) == 21


class TestTreeLazy:
    def _client(self, app):
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        c = app.test_client()
        _login_as(c, uid, "clerk01")
        return c

    def test_children_root(self, app):
        _seed(app)
        c = self._client(app)
        r = c.get('/safety/api/children')
        assert r.status_code == 200
        d = r.get_json()['data']
        assert d['total'] == 21 and len(d['items']) == 21 and d['has_more'] is False
        first = d['items'][0]
        assert first['node_type'] == 'folder' and 'child_count' in first \
            and 'has_children' in first

    def test_children_limit_and_load_more(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            parent = SafetyNode.query.filter_by(name='防汛减灾').first()
            for i in range(55):
                db.session.add(SafetyNode(parent_id=parent.id, name=f'子目录{i:03d}',
                                          node_type='folder', sort_order=i + 1))
            db.session.commit()
            pid = parent.id
        c = self._client(app)
        r = c.get('/safety/api/children', query_string={'parent_id': pid})
        d = r.get_json()['data']
        assert len(d['items']) == 50 and d['total'] == 55 and d['has_more'] is True
        r2 = c.get('/safety/api/children',
                   query_string={'parent_id': pid, 'offset': 50})
        d2 = r2.get_json()['data']
        assert len(d2['items']) == 5 and d2['has_more'] is False

    def test_children_missing_node(self, app):
        _seed(app)
        c = self._client(app)
        assert c.get('/safety/api/children',
                     query_string={'parent_id': 99999}).get_json()['success'] is False

    def test_path_chain(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            root = SafetyNode.query.filter_by(name='消防资料').first()
            child = SafetyNode(parent_id=root.id, name='2026年度', node_type='folder',
                               sort_order=1)
            db.session.add(child)
            db.session.commit()
            leaf = child.id
        c = self._client(app)
        r = c.get('/safety/api/path', query_string={'id': leaf})
        chain = r.get_json()['data']['items']
        assert [x['name'] for x in chain] == ['消防资料', '2026年度']


class TestList:
    def _client(self, app):
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        c = app.test_client()
        _login_as(c, uid, "clerk01")
        return c

    def test_list_pagination_and_folder_first(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            root = SafetyNode.query.filter_by(name='通知模板').first()
            db.session.add(SafetyNode(parent_id=root.id, name='a文件.txt',
                                      node_type='file', ext='txt', sort_order=1))
            db.session.add(SafetyNode(parent_id=root.id, name='z目录',
                                      node_type='folder', sort_order=99))
            db.session.add(SafetyNode(parent_id=root.id, name='b文件.txt',
                                      node_type='file', ext='txt', sort_order=2))
            for i in range(25):
                db.session.add(SafetyNode(parent_id=root.id, name=f'批量文件{i:02d}.txt',
                                          node_type='file', ext='txt', sort_order=100 + i))
            db.session.commit()
            nid = root.id
        c = self._client(app)
        r = c.get('/safety/api/list', query_string={'node_id': nid})
        d = r.get_json()['data']
        assert d['total'] == 28 and d['pages'] == 2 and d['page'] == 1
        assert len(d['items']) == 20
        assert d['items'][0]['node_type'] == 'folder'  # 目录优先
        assert d['items'][1]['name'] == 'a文件.txt'
        r2 = c.get('/safety/api/list', query_string={'node_id': nid, 'page': 2})
        assert len(r2.get_json()['data']['items']) == 8

    def test_list_root(self, app):
        _seed(app)
        c = self._client(app)
        r = c.get('/safety/api/list', query_string={'node_id': 0})
        d = r.get_json()['data']
        assert d['current']['name'] == '安全工作' and d['total'] == 21


class TestFolder:
    def _client(self, app):
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        c = app.test_client()
        _login_as(c, uid, "clerk01")
        return c

    def test_create_ok_and_logged(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        c = self._client(app)
        r = c.post('/safety/api/folder', data={'action': 'new', 'parent_id': 0, 'name': '新增分类'})
        assert r.get_json()['success'] is True
        with app.app_context():
            node = SafetyNode.query.filter_by(name='新增分类').first()
            assert node is not None and node.parent_id is None
            from models import OperationLog
            assert OperationLog.query.filter_by(module='安全工作',
                                                action='folder_new').count() == 1

    def test_duplicate_sibling_rejected(self, app):
        _seed(app)
        c = self._client(app)
        r = c.post('/safety/api/folder', data={'action': 'new', 'parent_id': 0, 'name': '隐患子目录'})
        assert r.get_json()['success'] is True
        r2 = c.post('/safety/api/folder', data={'action': 'new', 'parent_id': 0, 'name': '隐患子目录'})
        assert r2.get_json()['success'] is False and '同名' in r2.get_json()['msg']

    def test_same_name_in_different_parent_allowed(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            for cat in ('安全隐患', '防汛减灾'):
                p = SafetyNode.query.filter_by(name=cat).first()
                db.session.add(SafetyNode(parent_id=p.id, name='同名目录',
                                          node_type='folder', sort_order=1))
            db.session.commit()
        # 两个父级下同名互不影响；在同一父级新建才被拒
        c = self._client(app)
        p = None
        with app.app_context():
            p = SafetyNode.query.filter_by(name='安全隐患').first().id
        r = c.post('/safety/api/folder', data={'action': 'new', 'parent_id': p, 'name': '同名目录'})
        assert r.get_json()['success'] is False

    def test_name_validation(self, app):
        _seed(app)
        c = self._client(app)
        assert c.post('/safety/api/folder', data={'action': 'new', 'parent_id': 0,
                                                  'name': '  '}).get_json()['success'] is False
        assert c.post('/safety/api/folder', data={'action': 'new', 'parent_id': 99999,
                                                  'name': 'x'}).get_json()['success'] is False
        assert c.post('/safety/api/folder', data={'action': 'new', 'parent_id': 0,
                                                  'name': '长' * 121}).get_json()['success'] is False

    def test_delete_empty_folder_ok(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            root = SafetyNode.query.filter_by(name='其他资料').first()
            node = SafetyNode(parent_id=root.id, name='临时目录', node_type='folder', sort_order=1)
            db.session.add(node)
            db.session.commit()
            nid = node.id
        c = self._client(app)
        r = c.post('/safety/api/folder', data={'action': 'delete', 'id': nid})
        assert r.get_json()['success'] is True
        with app.app_context():
            assert SafetyNode.query.get(nid) is None

    def test_delete_nonempty_rejected(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            root = SafetyNode.query.filter_by(name='其他资料').first()
            node = SafetyNode(parent_id=root.id, name='非空目录', node_type='folder', sort_order=1)
            db.session.add(node)
            db.session.flush()
            db.session.add(SafetyNode(parent_id=node.id, name='子文件.txt',
                                      node_type='file', ext='txt', sort_order=1))
            db.session.commit()
            nid = node.id
        c = self._client(app)
        r = c.post('/safety/api/folder', data={'action': 'delete', 'id': nid})
        assert r.get_json()['success'] is False and '非空' in r.get_json()['msg']


class TestUpload:
    def _client(self, app):
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        c = app.test_client()
        _login_as(c, uid, "clerk01")
        return c

    def test_upload_allowed_ext(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            nid = SafetyNode.query.filter_by(name='水利资料').first().id
        c = self._client(app)
        r = c.post('/safety/api/file/upload',
                   data={'node_id': str(nid),
                         'files': (io.BytesIO(b'hello world'), '预案说明.png')},
                   content_type='multipart/form-data')
        assert r.status_code == 200
        d = r.get_json()['data']
        assert d['results'][0]['ok'] is True and d['results'][0]['duplicate'] is False
        with app.app_context():
            node = SafetyNode.query.filter_by(name='预案说明.png').first()
            assert node is not None and node.node_type == 'file'
            assert node.ext == 'png' and node.size == 11
            import os
            from config import Config
            assert os.path.exists(os.path.join(Config.UPLOAD_FOLDER, 'safety', node.stored_name))
            from models import OperationLog
            assert OperationLog.query.filter_by(module='安全工作', action='upload').count() == 1

    def test_upload_duplicate_name_warns_but_ok(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            nid = SafetyNode.query.filter_by(name='水利资料').first().id
        c = self._client(app)
        for _ in range(2):
            r = c.post('/safety/api/file/upload',
                       data={'node_id': str(nid),
                             'files': (io.BytesIO(b'doc'), '重复名.txt')},
                       content_type='multipart/form-data')
            assert r.get_json()['data']['results'][0]['ok'] is True
        assert r.get_json()['data']['results'][0]['duplicate'] is True

    def test_upload_reject_bad_ext(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            nid = SafetyNode.query.filter_by(name='水利资料').first().id
        c = self._client(app)
        r = c.post('/safety/api/file/upload',
                   data={'node_id': str(nid),
                         'files': (io.BytesIO(b'MZ\x00'), 'evil.exe')},
                   content_type='multipart/form-data')
        res = r.get_json()['data']['results'][0]
        assert res['ok'] is False and '不支持' in res['msg']
        with app.app_context():
            assert SafetyNode.query.filter_by(name='evil.exe').first() is None

    def test_upload_oversize_rejected(self, app, monkeypatch):
        _seed(app)
        import routes.safety as safety_mod
        monkeypatch.setattr(safety_mod, 'MAX_FILE_SIZE', 10)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            nid = SafetyNode.query.filter_by(name='水利资料').first().id
        c = self._client(app)
        r = c.post('/safety/api/file/upload',
                   data={'node_id': str(nid),
                         'files': (io.BytesIO(b'x' * 64), '大文件.txt')},
                   content_type='multipart/form-data')
        res = r.get_json()['data']['results'][0]
        assert res['ok'] is False and '50MB' in res['msg']
        with app.app_context():
            assert SafetyNode.query.filter_by(name='大文件.txt').first() is None

    def test_upload_to_missing_dir_rejected(self, app):
        _seed(app)
        c = self._client(app)
        r = c.post('/safety/api/file/upload',
                   data={'node_id': '99999',
                         'files': (io.BytesIO(b'x'), 'a.txt')},
                   content_type='multipart/form-data')
        assert r.get_json()['success'] is False


class TestFileOps:
    def _setup_file(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        import os
        from config import Config
        with app.app_context():
            nid = SafetyNode.query.filter_by(name='消防资料').first().id
        c = app.test_client()
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        _login_as(c, uid, "clerk01")
        r = c.post('/safety/api/file/upload',
                   data={'node_id': str(nid),
                         'files': [(io.BytesIO(b'PNGDATA'), '照片.PNG'),
                                   (io.BytesIO(b'%PDF-1.4 fake'), '档案.pdf'),
                                   (io.BytesIO(b'word data'), '台账.docx')]},
                   content_type='multipart/form-data')
        ids = [x['id'] for x in r.get_json()['data']['results']]
        with app.app_context():
            nodes = {n.id: n for n in SafetyNode.query.filter(
                SafetyNode.id.in_(ids)).all()}
            paths = {i: os.path.join(Config.UPLOAD_FOLDER, 'safety',
                                     nodes[i].stored_name) for i in ids}
        return c, ids, nodes, paths

    def test_download_attachment_original_name(self, app):
        from urllib.parse import quote
        c, ids, nodes, _ = self._setup_file(app)
        n = nodes[ids[0]]
        r = c.get(f'/safety/file/{ids[0]}/download')
        assert r.status_code == 200
        assert r.data == b'PNGDATA'
        cd = r.headers.get('Content-Disposition', '')
        assert 'attachment' in cd
        # 中文原名经 RFC 5987 filename* 下发（ASCII 兜底名必然被剥离，不影响浏览器解析）
        assert "filename*=UTF-8''" + quote(n.original_name) in cd

    def test_preview_inline_only_image_pdf(self, app):
        c, ids, nodes, _ = self._setup_file(app)
        r_png = c.get(f'/safety/file/{ids[0]}/preview')
        assert r_png.headers.get('Content-Type') == 'image/png'
        assert 'inline' in r_png.headers.get('Content-Disposition', '')
        r_pdf = c.get(f'/safety/file/{ids[1]}/preview')
        assert 'inline' in r_pdf.headers.get('Content-Disposition', '')
        r_docx = c.get(f'/safety/file/{ids[2]}/preview')
        assert 'attachment' in r_docx.headers.get('Content-Disposition', '')

    def test_download_missing_and_forged_traversal(self, app):
        c, ids, _, _ = self._setup_file(app)
        assert c.get('/safety/file/99999/download').status_code == 404
        # 伪造落盘名为路径穿越串 → 正则护栏拒绝（即使磁盘真的可拼出路径也不放行）
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            node = SafetyNode.query.get(ids[0])
            node.stored_name = '../../config.ini'
            db.session.commit()
        assert c.get(f'/safety/file/{ids[0]}/download').status_code == 404

    def test_delete_file_removes_db_and_disk(self, app):
        c, ids, _, paths = self._setup_file(app)
        import os
        from models import db
        from models.safety import SafetyNode
        assert os.path.exists(paths[ids[0]])
        r = c.post('/safety/api/file', data={'action': 'delete', 'id': ids[0]})
        assert r.get_json()['success'] is True
        with app.app_context():
            assert SafetyNode.query.get(ids[0]) is None
        assert not os.path.exists(paths[ids[0]])
        with app.app_context():
            from models import OperationLog
            assert OperationLog.query.filter_by(module='安全工作',
                                                action='file_delete').count() == 1

    def test_delete_folder_target_rejected(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            nid = SafetyNode.query.filter_by(name='安全隐患').first().id
        c = app.test_client()
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        _login_as(c, uid, "clerk01")
        r = c.post('/safety/api/file', data={'action': 'delete', 'id': nid})
        assert r.get_json()['success'] is False


class TestSearch:
    def test_full_tree_with_path(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            root = SafetyNode.query.filter_by(name='消防资料').first()
            child = SafetyNode(parent_id=root.id, name='2026年预案', node_type='folder',
                               sort_order=1)
            db.session.add(child)
            db.session.flush()
            db.session.add(SafetyNode(parent_id=child.id, name='疏散演练方案.docx',
                                      node_type='file', ext='docx', sort_order=1))
            db.session.commit()
        c = app.test_client()
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        _login_as(c, uid, "clerk01")
        r = c.get('/safety/api/search', query_string={'q': '疏散演练'})
        d = r.get_json()['data']
        assert len(d['items']) == 1
        assert d['items'][0]['path'] == '安全工作 › 消防资料 › 2026年预案 › 疏散演练方案.docx'

    def test_search_empty_q_rejected(self, app):
        _seed(app)
        c = app.test_client()
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        _login_as(c, uid, "clerk01")
        assert c.get('/safety/api/search', query_string={'q': '  '}).get_json()['success'] is False


class TestPermission:
    def _no_perm_client(self, app):
        uid = _mk_user(app, "nobody", "nobody_role", _open_perm())
        c = app.test_client()
        _login_as(c, uid, "nobody")
        return c

    def test_no_view_denied(self, app):
        _seed(app)
        c = self._no_perm_client(app)
        assert c.get('/safety/').status_code == 403
        for path in ('/safety/api/children', '/safety/api/list',
                     '/safety/api/search?q=a', '/safety/file/1/download'):
            assert c.get(path).status_code == 403, path
        assert c.post('/safety/api/folder',
                      data={'action': 'new', 'parent_id': 0, 'name': 'x'}).status_code == 403

    def test_anonymous_json_401(self, app):
        _seed(app)
        c = app.test_client()
        r = c.get('/safety/api/children')
        assert r.status_code == 401

    def test_view_only_cannot_write(self, app):
        _seed(app)
        p = _open_perm()
        p['safety.view'] = True
        uid = _mk_user(app, "viewer", "viewer_role", p)
        c = app.test_client()
        _login_as(c, uid, "viewer")
        assert c.get('/safety/api/children').status_code == 200
        assert c.post('/safety/api/folder',
                      data={'action': 'new', 'parent_id': 0, 'name': 'x'}).status_code == 403
        assert c.post('/safety/api/file/upload',
                      data={'node_id': '0', 'files': (io.BytesIO(b'x'), 'a.txt')},
                      content_type='multipart/form-data').status_code == 403

    def test_uploads_direct_link_red_line(self, app):
        """红线：uploads/safety 不得进入 UPLOAD_SUBDIR_PERM_MAP；直链一律 403。"""
        import os

        from routes.safety import _upload_dir
        _seed(app)
        # UPLOAD_SUBDIR_PERM_MAP 是 create_app 内局部变量：从源码块静态断言未登记 safety 键
        import app as app_module
        src = open(app_module.__file__, encoding='utf-8').read()
        marker = 'UPLOAD_SUBDIR_PERM_MAP = {'
        block = src[src.index(marker):src.index(marker) + src[src.index(marker):].index('\n    }')]
        assert "'safety'" not in block, 'uploads/safety 不得加入 UPLOAD_SUBDIR_PERM_MAP'
        with open(os.path.join(_upload_dir(), 'deadbeef.txt'), 'w') as f:
            f.write('x')
        c = app.test_client()
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        _login_as(c, uid, "clerk01")
        r = c.get('/uploads/safety/deadbeef.txt')
        assert r.status_code == 403
        # 不存在的子目录同样拒绝（未映射默认 403）
        assert c.get('/uploads/nonexist_xxx/a.txt').status_code == 403

class _FullClient:
    """已登录全权限 clerk 的 test_client 便捷构造（031 测试基类）。"""

    def _client(self, app):
        uid = _mk_user(app, "clerk01", "clerk_role", _full_safety_perm())
        c = app.test_client()
        _login_as(c, uid, "clerk01")
        return c


class TestRenameMove(_FullClient):
    def _mk_dir_and_file(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            p1 = SafetyNode.query.filter_by(name='安全隐患').first()
            p2 = SafetyNode.query.filter_by(name='防汛减灾').first()
            sub = SafetyNode(parent_id=p1.id, name='子目录A', node_type='folder', sort_order=1)
            sib = SafetyNode(parent_id=p1.id, name='子目录B', node_type='folder', sort_order=2)
            db.session.add_all([sub, sib])
            db.session.flush()
            f = SafetyNode(parent_id=p1.id, name='报告.docx', node_type='file',
                           ext='docx', stored_name='afile', sort_order=3)
            db.session.add(f)
            db.session.commit()
            return p1.id, p2.id, sub.id, f.id

    def test_folder_rename_ok_and_dup_rejected(self, app):
        _, _, sub_id, _ = self._mk_dir_and_file(app)
        c = self._client(app)
        r = c.post('/safety/api/folder', data={'action': 'rename', 'id': sub_id,
                                               'name': '子目录A2'})
        assert r.get_json()['success'] is True
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            assert SafetyNode.query.get(sub_id).name == '子目录A2'
        # 与同级姐妹目录「子目录B」重名 → 拒绝
        r3 = c.post('/safety/api/folder', data={'action': 'rename', 'id': sub_id,
                                                'name': '子目录B'})
        assert r3.get_json()['success'] is False and '同名' in r3.get_json()['msg']

    def test_file_rename_ok(self, app):
        p1, _, _, f_id = self._mk_dir_and_file(app)
        c = self._client(app)
        r = c.post('/safety/api/file', data={'action': 'rename', 'id': f_id,
                                             'name': '新报告.docx'})
        assert r.get_json()['success'] is True
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            n = SafetyNode.query.get(f_id)
            assert n.name == '新报告.docx' and n.original_name == '新报告.docx'
            assert n.ext == 'docx'  # 扩展名/mime 不变

    def test_file_move_and_unreachable_old_path(self, app):
        p1, p2, _, f_id = self._mk_dir_and_file(app)
        c = self._client(app)
        r = c.post('/safety/api/file', data={'action': 'move', 'id': f_id,
                                             'target_id': p2})
        assert r.get_json()['success'] is True
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            assert SafetyNode.query.get(f_id).parent_id == p2
        # 原路径（p1 子级）不可达、新路径可达
        r1 = c.get('/safety/api/list', query_string={'node_id': p1})
        assert all(x['name'] != '报告.docx' for x in r1.get_json()['data']['items'])
        r2 = c.get('/safety/api/list', query_string={'node_id': p2})
        assert any(x['name'] == '报告.docx' for x in r2.get_json()['data']['items'])

    def test_folder_move_cycle_rejected(self, app):
        p1, _, sub_id, _ = self._mk_dir_and_file(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            inner = SafetyNode(parent_id=sub_id, name='孙目录', node_type='folder', sort_order=1)
            db.session.add(inner)
            db.session.commit()
            inner_id = inner.id
        c = self._client(app)
        # 移动到自己的子孙（inner）→ 环 → 拒绝
        r = c.post('/safety/api/folder', data={'action': 'move', 'id': sub_id,
                                               'target_id': inner_id})
        assert r.get_json()['success'] is False and '子目录' in r.get_json()['msg']
        # 移动到自身 → 拒绝
        r2 = c.post('/safety/api/folder', data={'action': 'move', 'id': sub_id,
                                                'target_id': sub_id})
        assert r2.get_json()['success'] is False
        with app.app_context():
            assert SafetyNode.query.get(sub_id).parent_id == p1  # 未发生变化


class TestBatch(_FullClient):
    def _setup(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        import os
        from config import Config
        with app.app_context():
            p1 = SafetyNode.query.filter_by(name='安全隐患').first()
            p2 = SafetyNode.query.filter_by(name='防汛减灾').first()
            empty = SafetyNode(parent_id=p1.id, name='空目录', node_type='folder', sort_order=1)
            busy = SafetyNode(parent_id=p1.id, name='非空目录', node_type='folder', sort_order=2)
            db.session.add(empty)
            db.session.add(busy)
            db.session.flush()
            f1 = SafetyNode(parent_id=p1.id, name='a.txt', node_type='file', ext='txt',
                            stored_name='afile', sort_order=3)
            f2 = SafetyNode(parent_id=p1.id, name='b.txt', node_type='file', ext='txt',
                            stored_name='afile2', sort_order=4)
            db.session.add_all([f1, f2])
            db.session.add(SafetyNode(parent_id=busy.id, name='child.txt', node_type='file',
                                      ext='txt', stored_name='afile3', sort_order=1))
            db.session.commit()
            return p1.id, p2.id, empty.id, busy.id, f1.id, f2.id

    def test_batch_move_ok_and_log(self, app):
        p1, p2, _, _, f1, f2 = self._setup(app)
        c = self._client(app)
        r = c.post('/safety/api/file/batch_move',
                   data={'ids': [f1, f2], 'target_id': p2})
        d = r.get_json()
        assert d['success'] is True and d['data']['success'] == 2
        from models import db
        from models.safety import SafetyNode
        from models import OperationLog
        with app.app_context():
            assert SafetyNode.query.get(f1).parent_id == p2
            assert SafetyNode.query.get(f2).parent_id == p2
            log = OperationLog.query.filter_by(module='安全工作', action='batch_done').first()
            assert log is not None and '成功 2' in log.summary

    def test_batch_delete_mixed_result(self, app):
        p1, _, empty_id, busy_id, f1, f2 = self._setup(app)
        c = self._client(app)
        r = c.post('/safety/api/file/batch_delete',
                   data={'ids': [f1, busy_id, empty_id]})
        d = r.get_json()['data']
        assert d['success'] == 2  # f1 + 空目录成功
        assert len(d['failed']) == 1 and d['failed'][0]['id'] == busy_id
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            assert SafetyNode.query.get(f1) is None
            assert SafetyNode.query.get(empty_id) is None
            assert SafetyNode.query.get(busy_id) is not None  # 非空目录保留

    def test_batch_move_cycle_item_failed(self, app):
        p1, _, empty_id, busy_id, f1, _ = self._setup(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            sub = SafetyNode(parent_id=busy_id.id if hasattr(busy_id, 'id') else busy_id,
                             name='深孙', node_type='folder', sort_order=9)
            db.session.add(sub)
            db.session.commit()
            sub_id = sub.id
        c = self._client(app)
        r = c.post('/safety/api/file/batch_move',
                   data={'ids': [f1, sub_id], 'target_id': sub_id})
        d = r.get_json()['data']
        assert d['success'] == 1
        assert any(x['id'] == sub_id for x in d['failed'])


class TestFlat(_FullClient):
    def test_flat_subtree_with_path(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            root = SafetyNode.query.filter_by(name='消防资料').first()
            sub = SafetyNode(parent_id=root.id, name='2026年预案', node_type='folder',
                             sort_order=1)
            db.session.add(sub)
            db.session.flush()
            db.session.add(SafetyNode(parent_id=sub.id, name='演练方案.docx',
                                      node_type='file', ext='docx', sort_order=1))
            db.session.commit()
            rid = root.id
            sid = sub.id
        c = self._client(app)
        r = c.get('/safety/api/flat', query_string={'node_id': rid})
        d = r.get_json()['data']
        assert d['total'] == 1
        assert d['items'][0]['path'] == '2026年预案/演练方案.docx'
        assert d['items'][0]['parent_id'] == sid
        # q 过滤
        r2 = c.get('/safety/api/flat', query_string={'node_id': rid, 'q': '不存在'})
        assert r2.get_json()['data']['total'] == 0


class TestZip(_FullClient):
    def _mk_files(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        import os
        from config import Config
        with app.app_context():
            root = SafetyNode.query.filter_by(name='消防资料').first()
            sub = SafetyNode(parent_id=root.id, name='2026年预案', node_type='folder',
                             sort_order=1)
            db.session.add(sub)
            db.session.flush()
            f = SafetyNode(parent_id=sub.id, name='演练方案.docx', node_type='file',
                           ext='docx', sort_order=1)
            db.session.add(f)
            db.session.flush()
            dirpath = os.path.join(Config.UPLOAD_FOLDER, 'safety')
            os.makedirs(dirpath, exist_ok=True)
            f.stored_name = '0' * 32 + '.docx'
            with open(os.path.join(dirpath, f.stored_name), 'wb') as fh:
                fh.write(b'ZIPCONTENT')
            db.session.commit()
            return root.id, f.id, dirpath

    def test_zip_node_structure_and_cleanup(self, app, monkeypatch):
        import zipfile
        import io as _io
        import os
        import tempfile

        recorded = {}
        orig_mkstemp = tempfile.mkstemp

        def _spy_mkstemp(suffix=None):
            fd, path = orig_mkstemp(suffix)
            recorded['path'] = path
            return fd, path

        monkeypatch.setattr(tempfile, 'mkstemp', _spy_mkstemp)
        rid, _, _ = self._mk_files(app)
        c = self._client(app)
        r = c.get('/safety/api/zip', query_string={'node_id': rid})
        assert r.status_code == 200
        zf = zipfile.ZipFile(_io.BytesIO(r.data))
        assert zf.namelist() == ['消防资料/2026年预案/演练方案.docx']
        assert zf.read('消防资料/2026年预案/演练方案.docx') == b'ZIPCONTENT'
        # P2 修复后清理挂在文件句柄 close 上（服务器发送完毕/测试端缓冲完毕必触发，
        # Windows 语义一致）；显式 close 后再断言磁盘无残留
        r.close()
        assert not os.path.exists(recorded['path']), '临时 zip 文件应已清理'

    def test_zip_ids_selection(self, app):
        import zipfile
        import io as _io
        rid, fid, _ = self._mk_files(app)
        c = self._client(app)
        r = c.get('/safety/api/zip', query_string={'ids': fid})
        assert r.status_code == 200
        zf = zipfile.ZipFile(_io.BytesIO(r.data))
        assert zf.namelist() == ['演练方案.docx']
        r.close()  # P2：触发文件句柄 close 清理临时 zip

    def test_zip_no_files(self, app):
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            rid = SafetyNode.query.filter_by(name='通知模板').first().id
        c = self._client(app)
        r = c.get('/safety/api/zip', query_string={'node_id': rid})
        assert r.get_json()['success'] is False


class TestP3Fallback(_FullClient):
    def test_download_ascii_fallback_name(self, app):
        """P3-2：中文名 ASCII 回退名兜底 download.<ext>，不再为空名。"""
        import io as _io
        _seed(app)
        from models import db
        from models.safety import SafetyNode
        with app.app_context():
            nid = SafetyNode.query.filter_by(name='水利资料').first().id
        c = self._client(app)
        r = c.post('/safety/api/file/upload',
                   data={'node_id': str(nid), 'files': (_io.BytesIO(b'x'), '巡河记录.txt')},
                   content_type='multipart/form-data')
        fid = r.get_json()['data']['results'][0]['id']
        r2 = c.get(f'/safety/file/{fid}/download')
        cd = r2.headers.get('Content-Disposition', '')
        assert 'filename=download.txt' in cd
        assert "filename*=UTF-8''" in cd


class TestArchivePageRender(_FullClient):
    """P1 回归：首屏目录视图必须默认可见（031 验收缺陷：tableWrap 初始隐藏）。"""

    def test_table_wrap_visible_by_default(self, app):
        """SSR 结构断言：#tableWrap 不得带 d-none；网格容器仍初始隐藏。"""
        c = self._client(app)
        r = c.get('/safety/')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert '<div class="table-responsive" id="tableWrap">' in html, \
            'P1 回归：首屏 #tableWrap 必须默认可见（不得含 d-none）'
        assert 'id="gridWrap" class="d-none position-relative"' in html

