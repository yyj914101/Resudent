# -*- coding: utf-8 -*-
"""tests/test_import_dual_mode.py — 034 P1 居民导入双通道统一

覆盖（034 验收）：
- 验收 1：同一文件 模板模式（/resident/api/import 薄门面）vs 自定义模式（/import/* 四步）
  四计数一致（success/updated/skip/fail 同口径）
- 验收 2：模板模式缺必需列阻断并列出缺失列；模板外列提示忽略
- 验收 3：覆盖语义三用例（空状态/空户编号/空非敏感列不破坏既有记录）；户编号分配与冲突
- 验收 5：幂等（同文件重复上传返回既有批次；done 再 execute 拒绝；run_import 重入不重复写）
- 验收 8：50MB 口径（zip magic + 大小拒收）、行数护栏（monkeypatch 常量）
- 验收 9：权限矩阵（resident.edit 可走模板；import_batch 可走自定义；无权限 403）
"""
import io
import json
import time

import pytest


def _make_app(tmp_path, monkeypatch):
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
        "SHUCUN_DISABLE_PASSWORD_CHANGE": "1",
        "SHUCUN_DISABLE_PERIODIC": "1",
    }.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "TEMP_FOLDER", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "IMPORTS_DIR", str(tmp_path / "temp" / "imports"))
    for d in ("uploads", "backups", "logs", "temp", "data"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    # 密钥就绪：导入管线要求（测试库口令自动解锁）
    from utils.crypto import key_holder
    with application.app_context():
        if not key_holder.is_initialized:
            key_holder.initialize("test-secret-key-0123456789abcdef")
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


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


def _login_as(client, user_id):
    with client.session_transaction() as s:
        s["user_id"] = user_id


def _admin_id(app):
    from models import User
    with app.app_context():
        return User.query.filter_by(username="admin").first().id


def _open_perm():
    return {"__schema_version__": 3,
            "resident.view": True, "resident.edit": True,
            "special": {"view_sensitive": False, "death_confirm": False,
                        "import_batch": False, "backup_restore": False,
                        "mask_config": False}}


_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CODES = '10X98765432'


def _gen_id(seq: int) -> str:
    """GLB-42 后生成合法校验位的 18 位测试身份证（唯一且可过导入校验）。"""
    prefix = f"11010119900101{seq:03d}"
    return prefix + _ID_CODES[sum(int(d) * w for d, w in zip(prefix, _ID_WEIGHTS)) % 11]


def _write_xlsx(path, headers, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _wait_done(app, batch_id, timeout=30):
    from models import ImportBatch
    deadline = time.time() + timeout
    while time.time() < deadline:
        with app.app_context():
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            if b and b.status == 'done':
                return b
            if b and b.status in ('blocked', 'aborted'):
                return b
        time.sleep(0.3)
    raise AssertionError(f'批次 {batch_id} 未在 {timeout}s 内完成')


def _tmpl_rows(n=100):
    """标准模板行：四个必需列 + 民族/备注；身份证唯一。"""
    return [
        [f'演示民{seq:03d}', _gen_id(seq), '配偶' if seq % 2 else '本人',
         f'户主{seq % 7:02d}', '汉族', f'备注{seq:03d}']
        for seq in range(1, n + 1)
    ]


TPL_HEADERS = ['姓名', '身份证号', '与户主关系', '户主名', '民族', '备注']


class TestTemplateModeHardRules:
    def test_preset_mapping_ok_and_extra(self, app):
        import os
        xlsx = app.config.get('TEMP_FOLDER') or '/tmp'
        path = os.path.join(app.config.get('IMPORTS_DIR') or os.path.join(xlsx, 'imports'), 'tpl.xlsx')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write_xlsx(path, TPL_HEADERS + ['上级临时列'],
                    [['张三', _gen_id(1), '本人', '李四', '汉族', 'r1', 'x']])
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/import/upload', data={'mode': 'template',
                                               'file': (io.BytesIO(f.read()), 'tpl.xlsx')},
                       content_type='multipart/form-data')
        d = r.get_json()['data']
        assert d.get('mapped') is True
        assert set(d['matched']) >= {'name', 'id_card', 'relationship', 'household_name'}
        assert '上级临时列' in d['extra']

    def test_missing_required_blocked(self, app):
        import os
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'bad.xlsx')
        _write_xlsx(path, ['姓名', '民族'], [['张三', '汉族']])
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/import/upload', data={'mode': 'template',
                                               'file': (io.BytesIO(f.read()), 'bad.xlsx')},
                       content_type='multipart/form-data')
        d = r.get_json()['data']
        assert d.get('blocked') is True
        assert {'id_card', 'relationship', 'household_name'} <= set(d['missing'])

    def test_blocked_template_handoff_to_custom(self, app):
        """验收 2 / 目标9 转自定义：模板通道阻断的批次经自定义映射端点救回——
        保存映射时 mode 切 custom、校验按自定义口径（姓名/身份证）、可直接执行（免重传）。"""
        import os
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'handoff.xlsx')
        # 缺 与户主关系/户主名 → 模板预设映射阻断；含 姓名/身份证 满足自定义口径
        _write_xlsx(path, ['姓名', '身份证号', '民族'],
                    [['张三', _gen_id(1), '汉族'], ['李四', _gen_id(2), '汉族']])
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/resident/api/import', data={'mode': 'append',
                                                     'file': (io.BytesIO(f.read()), 'handoff.xlsx')},
                       content_type='multipart/form-data')
        d = r.get_json()
        assert d['success'] is False and d['data']['blocked'] is True
        bid = d['data']['batch_id']

        # 自定义映射端点：读映射 → 保存映射（此时批次应切 custom 语义）
        r2 = c.get(f'/import/{bid}/mapping')
        assert r2.get_json()['success']
        cols = [{'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
                {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
                {'source': '民族', 'action': 'map', 'target': 'nation', 'matched_by': 'exact'}]
        r3 = c.post(f'/import/{bid}/mapping', json={'columns': cols})
        assert r3.get_json()['success'] and not r3.get_json()['data'].get('blocked')

        from models import ImportBatch
        with app.app_context():
            b = ImportBatch.query.filter_by(batch_id=bid).first()
            assert b.mode == 'custom' and b.status == 'mapped'

        r4 = c.get(f'/import/{bid}/validate')
        assert r4.get_json()['data']['mode'] == 'custom'

        r5 = c.post(f'/import/{bid}/execute', json={'policy': 'skip'})
        assert r5.status_code == 202
        b = _wait_done(app, bid)
        assert b.status == 'done' and b.success_rows == 2


class TestDualModeConsistency:
    """验收 1：同一份模板文件，模板模式 vs 自定义模式（自动映射确认）四计数一致。"""

    def _import_via_template(self, app):
        import os
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'dual.xlsx')
        _write_xlsx(path, TPL_HEADERS, _tmpl_rows(40))
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/resident/api/import', data={'mode': 'append',
                                                     'file': (io.BytesIO(f.read()), 'dual.xlsx')},
                       content_type='multipart/form-data')
        assert r.status_code == 200, r.get_json()
        data = r.get_json()['data']
        if data.get('async') or not data.get('imported'):
            # 异步兜底：等待批次完成（与自定义模式同口径比较）
            from models import ImportBatch, AsyncTask, db
            import time as _t
            bid = data.get('batch_id')
            for _ in range(100):
                with app.app_context():
                    b = ImportBatch.query.filter_by(batch_id=bid).first()
                    if b and b.status in ('done', 'blocked', 'aborted'):
                        data = {'imported': (b.success_rows or 0) - (b.updated_rows or 0),
                                'updated': b.updated_rows or 0, 'skipped': b.skip_rows or 0}
                        return data
                    t = AsyncTask.query.order_by(AsyncTask.id.desc()).first()
                    st = t.status if t else 'no-task'
                _t.sleep(0.3)
            raise AssertionError(f'模板批次 {bid} 未在 30s 内完成（最后任务状态 {st}）')
        return data

    def _import_via_custom(self, app):
        import os
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'dual2.xlsx')
        _write_xlsx(path, TPL_HEADERS, _tmpl_rows(40))
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/import/upload', data={'mode': 'custom',
                                               'file': (io.BytesIO(f.read()), 'dual2.xlsx')},
                       content_type='multipart/form-data')
        bid = r.get_json()['data']['batch_id']
        cols = [{'source': h, 'action': 'map', 'target': 'name' if h == '姓名' else
                 'id_card' if h == '身份证号' else
                 'relationship' if h == '与户主关系' else
                 'household_name' if h == '户主名' else
                 'nation' if h == '民族' else 'remark', 'matched_by': 'exact'}
                for h in TPL_HEADERS]
        r2 = c.post(f'/import/{bid}/mapping', json={'columns': cols})
        assert r2.get_json()['success']
        r3 = c.post(f'/import/{bid}/execute', json={'policy': 'skip'})
        assert r3.status_code == 202
        b = _wait_done(app, bid)
        return {'success': b.success_rows, 'updated': b.updated_rows,
                'skip': b.skip_rows, 'fail': b.fail_rows}

    def test_counts_identical(self, app, tmp_path, monkeypatch):
        """双模式一致性：同一文件在两个独立库各跑一次，四计数一致（同库二跑必全 skip）。"""
        a = self._import_via_template(app)
        import shutil
        d2 = tmp_path / 'second'
        d2.mkdir(parents=True, exist_ok=True)
        app2 = _make_app(d2, monkeypatch)
        b = self._import_via_custom(app2)
        
        # 注意：模板薄门面响应 imported=success-updated；自定义走批次计数。同口径断言：
        # 实际在独立 app 上各跑一次：模板 40 新增；自定义 40 成功。
        assert a['imported'] == 40
        assert b['success'] == 40 and b['updated'] == 0 and b['skip'] == 0 and b['fail'] == 0
        assert a['updated'] == 0 and a['skipped'] == 0


class TestOverwriteSemantics:
    """验收 3：覆盖语义三用例（空状态/空户编号/空非敏感列不破坏既有记录）。"""

    def _seed_existing(self, app):
        from models import db, Resident
        from utils.crypto import key_holder, sha256_hash, sha256_prefix_hash
        with app.app_context():
            idc = _gen_id(900)
            r = Resident(name='既有户', id_card=None,
                         id_card_enc=key_holder.encrypt(idc),
                         id_card_hash=sha256_hash(idc),
                         id_card_prefix_hash=sha256_prefix_hash(idc, 6),
                         status='deceased', flow_status='常住',
                         household_name='既户主', household_code='H7777',
                         village_group='旧寨', address='旧地址', remark='旧备注',
                         source='迁移')
            db.session.add(r)
            db.session.commit()
        return idc

    def test_empty_cells_do_not_destroy(self, app, tmp_path):
        existing_idc = self._seed_existing(app)
        path = str(tmp_path / 'ov.xlsx')
        # 状态/户编号/备注留空；地址显式更新
        _write_xlsx(path, TPL_HEADERS + ['家庭地址'],
                    [[f'既有户', existing_idc, '本人', '既户主', '', '', '新地址']])
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/resident/api/import', data={'mode': 'overwrite',
                                                     'file': (io.BytesIO(f.read()), 'ov.xlsx')},
                       content_type='multipart/form-data')
        assert r.status_code == 200, r.get_json()
        data = r.get_json()['data']
        assert data['updated'] == 1 and data['imported'] == 0
        from models import db, Resident
        with app.app_context():
            row = Resident.query.filter_by(household_code='H7777').first()
            assert row.status == 'deceased', '空状态不得重置既有状态（P4-②）'
            assert row.household_code == 'H7777', '空户编号不得重新生成（P4-③）'
            assert row.remark == '旧备注', '空非敏感列不得清空既有值（P4-①）'
            assert row.address == '新地址', '显式提供值应覆盖'


class TestHouseholdCode:
    def test_alloc_and_conflict(self, app):
        import os
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'hh.xlsx')
        # 同户主两行（空编号）→ 复用同一分配号；第三行显式编号冲突（不同户主同号）
        _write_xlsx(path, TPL_HEADERS + ['户编号'],
                    [['甲一', _gen_id(1), '本人', '王户主', '', ''],
                     ['甲二', _gen_id(2), '配偶', '王户主', '', ''],
                     ['乙一', _gen_id(3), '本人', '陈户主', '', '', 'H9998']])
        with app.app_context():
            from models import db, Resident
            db.session.add(Resident(name='冲突占位', household_name='第三人',
                                    household_code='H9998', source='迁移'))
            db.session.commit()
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/resident/api/import', data={'mode': 'append',
                                                     'file': (io.BytesIO(f.read()), 'hh.xlsx')},
                       content_type='multipart/form-data')
        data = r.get_json()['data']
        assert data['imported'] == 2
        from models import db, Resident
        with app.app_context():
            rows = Resident.query.filter(Resident.name.in_(['甲一', '甲二'])).all()
            codes = {r.household_code for r in rows}
            assert len(codes) == 1 and codes != {None}, '同户主应复用同一分配编号'
            assert Resident.query.filter_by(name='冲突占位').first() is not None
        assert any('户编号冲突' in e for e in data.get('errors', []))


class TestIdempotencyAndGuards:
    def test_duplicate_upload_returns_existing(self, app):
        """034 幂等一（142 口径修订）：同文件重复上传的幂等保护对象是
        「已入库 / done」批次；0 行 mapped 残留按 142 作废重建（不再 duplicate）。"""
        import os
        from models import db, ImportBatch
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'dup.xlsx')
        _write_xlsx(path, TPL_HEADERS, [_tmpl_rows(2)[0]])
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            content = f.read()
        r1 = c.post('/import/upload', data={'mode': 'custom',
                                            'file': (io.BytesIO(content), 'dup.xlsx')},
                    content_type='multipart/form-data')
        bid = r1.get_json()['data']['batch_id']
        # 142：先把批次推进到 done（已入库）——这才是幂等要保护的形态
        with app.app_context():
            ImportBatch.query.filter_by(batch_id=bid).update(
                {'status': 'done', 'success_rows': 1})
            db.session.commit()
        r2 = c.post('/import/upload', data={'mode': 'custom',
                                            'file': (io.BytesIO(content), 'dup.xlsx')},
                    content_type='multipart/form-data')
        d2 = r2.get_json()['data']
        assert d2.get('duplicate') is True
        assert d2['batch_id'] == bid

    def test_done_batch_execute_rejected(self, app):
        import os
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'done.xlsx')
        _write_xlsx(path, TPL_HEADERS, _tmpl_rows(2))
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/import/upload', data={'mode': 'custom',
                                               'file': (io.BytesIO(f.read()), 'done.xlsx')},
                       content_type='multipart/form-data')
        bid = r.get_json()['data']['batch_id']
        cols = [{'source': h, 'action': 'map',
                 'target': {'姓名': 'name', '身份证号': 'id_card',
                            '与户主关系': 'relationship', '户主名': 'household_name',
                            '民族': 'nation', '备注': 'remark'}[h], 'matched_by': 'exact'}
                for h in TPL_HEADERS]
        m = c.post(f'/import/{bid}/mapping', json={'columns': cols})
        assert m.get_json()['success']
        assert c.post(f'/import/{bid}/execute', json={'policy': 'skip'}).status_code == 202
        _wait_done(app, bid)
        r2 = c.post(f'/import/{bid}/execute', json={'policy': 'skip'})
        assert r2.status_code == 409, 'done 批次 execute 应拒绝（幂等）'

    def test_bad_magic_and_size_rejected(self, app, monkeypatch):
        import utils.import_engine as eng
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.post('/import/upload', data={'mode': 'custom',
                                           'file': (io.BytesIO(b'not-a-zip'), 'evil.xlsx')},
                   content_type='multipart/form-data')
        assert r.status_code == 400 and 'zip' in r.get_json()['msg']
        monkeypatch.setattr(eng, 'MAX_IMPORT_SIZE', 10)
        r2 = c.post('/import/upload', data={'mode': 'custom',
                                            'file': (io.BytesIO(b'PK\x03\x04' + b'x' * 64),
                                                     'big.xlsx')},
                    content_type='multipart/form-data')
        assert r2.status_code == 400
        assert '50MB' in r2.get_json()['msg'] or '上限' in r2.get_json()['msg']

    def test_413_returns_friendly_json(self, app):
        """035 R-034-04：请求体超 MAX_CONTENT_LENGTH → 413 + 与内核一致的 JSON 文案（非白页）。"""
        app.config['MAX_CONTENT_LENGTH'] = 64  # 压小上限触发 413（与 >50MiB 同一处理路径）
        c = app.test_client()
        _login_as(c, _admin_id(app))
        r = c.post('/import/upload', data={'mode': 'custom',
                                           'file': (io.BytesIO(b'PK\x03\x04' + b'x' * 512),
                                                    'big.xlsx')},
                   content_type='multipart/form-data')
        assert r.status_code == 413
        j = r.get_json()
        assert j['success'] is False
        assert j['msg'] == '文件超过 50MB 上限，请分批导入', '文案须与内核 start_batch 一致'

    def test_row_limit_guard(self, app, monkeypatch):
        import os
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'rows.xlsx')
        _write_xlsx(path, TPL_HEADERS, _tmpl_rows(4))
        import utils.import_engine as eng
        monkeypatch.setattr(eng, 'MAX_IMPORT_ROWS', 3)
        c = app.test_client()
        _login_as(c, _admin_id(app))
        with open(path, 'rb') as f:
            r = c.post('/import/upload', data={'mode': 'custom',
                                               'file': (io.BytesIO(f.read()), 'rows.xlsx')},
                       content_type='multipart/form-data')
        assert r.status_code == 400 and ('行上限' in r.get_json()['msg'] or '万行' in r.get_json()['msg'])


class TestPermissionMatrix:
    """验收 9：模板 resident.edit（/resident/api/import）；自定义 special.import_batch（/import/*）。"""

    def test_edit_only_user_template_ok_custom_403(self, app):
        perms = _open_perm()
        # 034 权限口径说明：/resident/api/import 路径含 'import' → 全局归类 add（023 既定，
        # 任务书定案 resident.edit 与归类引擎冲突；不改 permissions.py〔034 约束〕）
        perms['resident.add'] = True
        uid = _mk_user(app, 'editor1', 'editor_role', perms)
        c = app.test_client()
        _login_as(c, uid)
        import os
        os.makedirs(os.path.join(app.config['TEMP_FOLDER'], 'imports'), exist_ok=True)
        path = os.path.join(app.config['TEMP_FOLDER'], 'imports', 'perm.xlsx')
        _write_xlsx(path, TPL_HEADERS, [_tmpl_rows(1)[0]])
        with open(path, 'rb') as f:
            r = c.post('/resident/api/import', data={'mode': 'append',
                                                     'file': (io.BytesIO(f.read()), 'perm.xlsx')},
                       content_type='multipart/form-data')
        assert r.status_code in (200, 302), '有 resident.add 应可走模板模式（import 归 add）'
        assert c.get('/import/upload').status_code == 403
        assert c.post('/import/upload',
                      data={'file': (io.BytesIO(b'PK\x03\x04'), 'x.xlsx')},
                      content_type='multipart/form-data').status_code == 403

    def test_no_import_batch_user_custom_tab_fallback(self, app):
        """验收 6/9：无 special.import_batch 用户深链 ?tab=custom → 服务端回退 manual，
        页面不渲染自定义 pane；自定义 API 直连仍 403。"""
        perms = _open_perm()
        perms['resident.add'] = True
        uid = _mk_user(app, 'editor2', 'editor_role2', perms)
        c = app.test_client()
        _login_as(c, uid)
        r = c.get('/resident/add?tab=custom')
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert 'custom-tab' not in html
        assert 'tab-pane fade show active" id="manual"' in html
        assert c.post('/import/upload',
                      data={'file': (io.BytesIO(b'PK\x03\x04'), 'x.xlsx')},
                      content_type='multipart/form-data').status_code == 403