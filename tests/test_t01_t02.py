# -*- coding: utf-8 -*-
"""T01/T02 前端映射向导 + 导入管线 A1 单测。

覆盖：
- 三级匹配（exact/template/fuzzy/unknown）
- 阈值守卫（>50% / >10 阻断 + admin 豁免）
- 上传防重（source_hash）
- 校验预览脱敏（无明文身份证）
- 导入端到端（加密写入 + 原列 NULL + hash 正确 + 去重 + 失败暂存 + 派生标签）
- 断点恢复游标 + A1 进度轨迹
- 3000 行 <60s 性能冒烟（P4）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    monkeypatch.setenv("SHUCUN_DB_PATH", db_path)
    monkeypatch.setenv("SHUCUN_TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setenv("SHUCUN_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("SHUCUN_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SHUCUN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SHUCUN_LAN_AUTHORIZED", "1")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    monkeypatch.setattr(config.Config, "IMPORTS_DIR", str(tmp_path / "temp" / "imports"))
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    return application


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())


def _write_xlsx(path, headers, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def _make_batch(app, xlsx_path, status='pending'):
    import uuid
    from models import db, ImportBatch
    with app.app_context():
        b = ImportBatch(batch_id=uuid.uuid4().hex[:12], source_file='t.xlsx',
                        source_path=xlsx_path, source_hash='h_' + uuid.uuid4().hex[:8],
                        status=status)
        db.session.add(b)
        db.session.commit()
        return b.batch_id


def _save_columns(app, batch_id, columns):
    from models import db
    from utils.import_engine import save_mapping
    with app.app_context():
        save_mapping(batch_id, columns)
        db.session.commit()


# ============ 三级匹配 ============
class TestMatchField:
    def test_exact(self):
        from utils.import_engine import match_field
        assert match_field('姓名') == ('name', 'exact')
        assert match_field('身份证号') == ('id_card', 'exact')
        assert match_field('联系电话') == ('phone', 'exact')
        assert match_field('户主姓名') == ('household_name', 'exact')

    def test_template(self):
        from utils.import_engine import match_field
        # '身份证号码' 在模板规则层（非字段目录 alias）
        assert match_field('身份证号码') == ('id_card', 'template')
        assert match_field('出生年月') == ('birth_date', 'template')

    def test_fuzzy(self):
        from utils.import_engine import match_field
        # 轻微拼写差异 → fuzzy
        assert match_field('身分证号')[0] == 'id_card'
        assert match_field('身分证号')[1] == 'fuzzy'

    def test_unknown(self):
        from utils.import_engine import match_field
        assert match_field('完全不认识列XYZ') == (None, 'unknown')

    def test_fullwidth_normalized(self):
        from utils.import_engine import match_field
        assert match_field('姓　名') == ('name', 'exact')  # 全角空格归一化


# ============ 阈值守卫 ============
class TestThreshold:
    def test_block_by_ratio(self):
        from utils.import_engine import compute_threshold
        cols = [{'target': None} for _ in range(3)]  # 3 列全未映射
        blocked, th, reason = compute_threshold(['a', 'b', 'c'], cols)
        assert blocked is True
        assert th['ratio'] == 1.0
        assert '未映射比例' in reason

    def test_block_by_new_fields(self):
        from utils.import_engine import compute_threshold
        # 30 列，11 列未映射（比例 36.7% <50%，但新增字段 >10）
        cols = ([{'target': 'name'}] * 19) + ([{'target': None}] * 11)
        headers = [f'h{i}' for i in range(30)]
        blocked, th, reason = compute_threshold(headers, cols)
        assert blocked is True
        assert th['new_fields'] == 11
        assert '新增字段 11' in reason

    def test_pass(self):
        from utils.import_engine import compute_threshold
        cols = ([{'target': 'name'}] * 9) + ([{'target': None}] * 1)
        blocked, th, _ = compute_threshold([f'h{i}' for i in range(10)], cols)
        assert blocked is False


# ============ 上传 / 映射 / 校验（HTTP） ============
class TestWizardHttp:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_upload_and_dedup(self, app, tmp_path):
        import io
        xlsx = tmp_path / "名单.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'], [['张三', '110101199001011237']])

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1  # admin

        data = {'file': (io.BytesIO(xlsx.read_bytes()), '名单.xlsx')}
        r1 = client.post('/import/upload', data=data, content_type='multipart/form-data')
        assert r1.status_code == 200
        j1 = r1.get_json()
        assert j1['success'] and 'batch_id' in j1['data']

        # 重复上传 → 返回既有批次
        data2 = {'file': (io.BytesIO(xlsx.read_bytes()), '名单.xlsx')}
        r2 = client.post('/import/upload', data=data2, content_type='multipart/form-data')
        j2 = r2.get_json()
        assert j2['data']['duplicate'] is True
        assert j2['data']['batch_id'] == j1['data']['batch_id']

    def test_upload_reject_non_xlsx(self, app):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        import io
        data = {'file': (io.BytesIO(b'xx'), 'a.txt')}
        r = client.post('/import/upload', data=data, content_type='multipart/form-data')
        assert r.status_code == 400

    def test_non_admin_import_batch_403(self, app):
        from models import db, Role, User
        from werkzeug.security import generate_password_hash
        with app.app_context():
            role = Role(name='普通操作员', permissions='{}', is_system=False)
            db.session.add(role)
            db.session.flush()
            u = User(username='op', password_hash=generate_password_hash('x'),
                     role_id=role.id, is_active=True)
            db.session.add(u)
            db.session.commit()
            uid = u.id
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = uid
        r = client.get('/import/upload')
        assert r.status_code == 403

    def test_mapping_save_blocked_needs_admin(self, app, tmp_path):
        xlsx = tmp_path / "b.xlsx"
        # 15 列，全部未映射 → 阈值阻断
        headers = [f'陌生列{i}' for i in range(15)]
        _write_xlsx(str(xlsx), headers, [[''] * 15])
        batch_id = _make_batch(app, str(xlsx))

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        cols = [{'source': h, 'action': 'ignore', 'target': None, 'matched_by': 'unknown'}
                for h in headers]
        # 全 ignore 不应阻断；改为全未处理（无 action）测阻断
        cols_unresolved = [{'source': h, 'action': None, 'target': None,
                            'matched_by': 'unknown'} for h in headers]
        r = client.post(f'/import/{batch_id}/mapping',
                        json={'columns': cols_unresolved, 'admin_confirm': False})
        j = r.get_json()
        assert j['data']['blocked'] is True
        assert j['data']['need_admin'] is True

        # admin 确认豁免 → 通过
        r2 = client.post(f'/import/{batch_id}/mapping',
                         json={'columns': cols_unresolved, 'admin_confirm': True})
        j2 = r2.get_json()
        assert j2['data']['blocked'] is False

    def test_validate_masked_no_plaintext(self, app, tmp_path):
        from models import db, Resident
        # 预置一个库内居民，触发「库内已有」错误 → 身份证脱敏展示
        with app.app_context():
            db.session.add(Resident(name='已有', id_card='110101199001011237'))
            db.session.commit()

        xlsx = tmp_path / "v.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'],
                    [['重复人', '110101199001011237'], ['', 'badid']])
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
        ])
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        r = client.get(f'/import/{batch_id}/validate')
        j = r.get_json()
        assert j['success']
        # 无明文身份证泄入校验结果
        body = str(j)
        assert '110101199001011237' not in body
        assert '110101********1237' in body  # 脱敏形态（GLB-42 后测试号改为合法校验位）


# ============ 导入端到端（加密写入） ============
class TestImportEndToEnd:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _prepare(self, app, tmp_path):
        xlsx = tmp_path / "e2e.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号', '联系电话', '银行卡号', '出生日期', '户属性'],
                    [
                        ['张三', '110101199001011237', '13812345678', '6221881234567890', '1990-01-01', '脱贫户'],
                        ['李四', '110101199002022341', '13912345678', '', '1991-02-02', '一般农户'],
                        ['坏数据', 'badid', '', '', '', ''],
                    ])
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
            {'source': '联系电话', 'action': 'map', 'target': 'phone', 'matched_by': 'exact'},
            {'source': '银行卡号', 'action': 'map', 'target': 'bank_card', 'matched_by': 'exact'},
            {'source': '出生日期', 'action': 'map', 'target': 'birth_date', 'matched_by': 'exact'},
            {'source': '户属性', 'action': 'map', 'target': 'household_property', 'matched_by': 'exact'},
        ])
        return batch_id

    def test_encrypted_write_and_tag(self, app, tmp_path):
        _unlock_keyholder()
        batch_id = self._prepare(app, tmp_path)
        from utils.import_engine import run_import
        from utils.crypto import key_holder, sha256_hash, decrypt_value
        from models import db, Resident, ResidentTag, ImportBatch, ImportRowError

        with app.app_context():
            run_import('task_x', batch_id)

        with app.app_context():
            zhang = Resident.query.filter_by(name='张三').first()
            assert zhang is not None
            # 原列置 NULL + 密文 + hash 正确
            assert zhang.id_card is None
            assert zhang.id_card_enc.startswith('gAAAA')
            assert zhang.id_card_hash == sha256_hash('110101199001011237')
            assert zhang.phone is None
            assert zhang.phone_enc.startswith('gAAAA')
            assert decrypt_value(key_holder._key, zhang.id_card_enc) == '110101199001011237'
            # 银行卡：李四银行卡为空 → 不加密集
            li = Resident.query.filter_by(name='李四').first()
            assert li.bank_card is None and li.bank_card_enc is None
            # 派生标签
            tag = ResidentTag.query.filter_by(resident_id=zhang.id, tag_code='脱贫户').first()
            assert tag is not None
            # 失败行进暂存区（坏数据身份证）
            err = ImportRowError.query.filter_by(batch_id=batch_id).first()
            assert err is not None and err.error_type == 'type'
            # 批次 done + 统计
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'
            assert b.success_rows == 2
            assert b.fail_rows == 1

    def test_dedup_skip(self, app, tmp_path):
        _unlock_keyholder()
        from models import db, Resident, ImportBatch
        from utils.import_engine import run_import
        from utils.crypto import sha256_hash

        # 预置一个已存在居民（明文 id_card，过渡态）
        with app.app_context():
            db.session.add(Resident(name='已有', id_card='110101199001011237'))
            db.session.commit()

        xlsx = tmp_path / "dup.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'],
                    [['新来', '110101199001011237'],   # 库内重复 → skip
                     ['重复', '110101199003033456'],   # 有效
                     ['再重复', '110101199003033456']])  # 名单内重复 → skip
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
        ])
        with app.app_context():
            run_import('task_dup', batch_id)
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'
            assert b.success_rows == 1
            assert b.skip_rows == 2

    def test_idempotent_rerun(self, app, tmp_path):
        """已 done 批次重跑不重复导入。"""
        _unlock_keyholder()
        batch_id = self._prepare(app, tmp_path)
        from utils.import_engine import run_import
        from models import db, Resident, ImportBatch
        with app.app_context():
            run_import('task_a', batch_id)
            cnt1 = Resident.query.count()
            run_import('task_b', batch_id)  # 幂等：status done 直接 return
            cnt2 = Resident.query.count()
            assert cnt1 == cnt2

    def test_async_trajectory(self, app, tmp_path):
        """A1 进度轨迹 pending→running→done + 断点恢复游标。"""
        _unlock_keyholder()
        batch_id = self._prepare(app, tmp_path)
        from utils.async_tasks import create_task, get_task, run_async
        from utils.import_engine import run_import
        from models import db, ImportBatch

        with app.app_context():
            task = create_task('import', '1', '导入')
            db.session.commit()
            run_async('import', task.task_id, run_import, batch_id=batch_id)

        states = []
        for _ in range(200):
            with app.app_context():
                t = get_task(task.task_id)
                states.append(t.status)
                if t.status in ('done', 'failed'):
                    break
            time.sleep(0.02)
        assert states[-1] == 'done'
        assert 'running' in states or 'pending' in states

        with app.app_context():
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'
            assert b.success_rows == 2  # 游标回写

    def test_perf_3000_rows_under_60s(self, app, tmp_path):
        """P4：3000 行导入 ≤60s。"""
        _unlock_keyholder()
        # GLB-42 后导入校验 GB11643 校验位：批量生成必须使用合法校验位
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        codes = '10X98765432'

        def _valid_id(prefix17):
            return prefix17 + codes[sum(int(d) * w for d, w in zip(prefix17, weights)) % 11]

        xlsx = tmp_path / "perf.xlsx"
        rows = [['张三{i}', _valid_id(f'1101011990{i:04d}123'), '', '', '', '一般农户']
                for i in range(3000)]
        _write_xlsx(str(xlsx), ['姓名', '身份证号', '联系电话', '银行卡号', '出生日期', '户属性'], rows)
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
            {'source': '联系电话', 'action': 'map', 'target': 'phone', 'matched_by': 'exact'},
            {'source': '银行卡号', 'action': 'map', 'target': 'bank_card', 'matched_by': 'exact'},
            {'source': '出生日期', 'action': 'map', 'target': 'birth_date', 'matched_by': 'exact'},
            {'source': '户属性', 'action': 'map', 'target': 'household_property', 'matched_by': 'exact'},
        ])
        from utils.import_engine import run_import
        from models import ImportBatch
        t0 = time.perf_counter()
        with app.app_context():
            run_import('task_perf', batch_id)
        elapsed = time.perf_counter() - t0
        with app.app_context():
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.success_rows == 3000
        assert elapsed < 60, f"3000 行导入耗时 {elapsed:.1f}s 超 60s"
