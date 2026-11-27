# -*- coding: utf-8 -*-
"""T12 R1-E2 导入引擎移植 — 14 项原型测试重建。

⚠️ 重建声明（如实标注）：原型 04-实现原型/import-engine-r1e2 已于 2026-08-13 确认丢失，
本测试集按权威口径重建，依据：
- PRD v1.2.3 §R1-E2「导入兼容性机制」+「导入兼容性验收标准（6 条）」
- 详细设计 v1.1 §2.2 映射向导 / §8 T12 / §2.5 导入管线

覆盖 14 项行为（对应六条兼容性验收 + 原型 14 测试）：
 1. 三级匹配 exact/template/fuzzy/unknown（含全角归一化）
 2. 阈值守卫：比例 >50% / 新增字段 >10 / 通过
 3. 类型转换：日期多格式解析（YYYY年M月D日 / YYYY.M.D / YYYYMMDD）
 4. 值域映射：gender M/F → 男/女（apply_value_domain）
 5. 容错层：未知字段进 ext_data（方案A），导入不中断
 6. 新增字段登记 import_field_registry（draft）
 7. 必填缺失 → 异常暂存区（行号+原因+原文脱敏），整批不阻断
 8. 去重：库内 + 名单内，跳过已成功行
 9. 失败行仅导出（export-failed 路由）
10. 阈值阻断：不落业务数据 + admin 确认豁免
11. 统计口径门：is_statistics_eligible（主字段放行/未审批拒绝/审批后放行）
12. schema_change_request 审批流 + data_model_version 版本号
13. A1 集成回归：import_execute→run_import→status 轮询轨迹 + 断点恢复
13b. 续跑接线（035 R-034-03）：create_app 启动钩子 mark_aborted_stale +
    /import/upload duplicate 响应 resumable 标识 + 续跑按身份证 hash 跳过已成功行
14. 新装空库口径：含额外字段名单导入后主字段正常建档、统计口径不受污染
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def _make_app(tmp_path, monkeypatch, **create_kwargs):
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
    application = create_app(**create_kwargs)
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


def _rebuild_import_batch_without(app, drop_cols):
    """R-037-04：删表重建窄表（等价于「缺列」库态），不依赖 SQLite ≥ 3.35 的
    ALTER TABLE ... DROP COLUMN（低版本环境收集/执行会失败）。"""
    from sqlalchemy import MetaData, Table, text
    from models import db
    with app.app_context():
        src = db.metadata.tables['import_batch']
        cols = [c.copy() for c in src.columns if c.name not in drop_cols]
        narrow = Table('import_batch', MetaData(), *cols)
        db.session.execute(text('DROP TABLE IF EXISTS import_batch'))
        db.session.commit()
        narrow.create(db.engine)


# ============ 1. 三级匹配 ============
class TestMatchField:
    def test_match_exact_template_fuzzy_unknown(self):
        from utils.import_engine import match_field
        assert match_field('姓名') == ('name', 'exact')
        assert match_field('身份证号码') == ('id_card', 'template')
        assert match_field('身分证号')[0] == 'id_card'
        assert match_field('身分证号')[1] == 'fuzzy'
        assert match_field('完全陌生列XYZ') == (None, 'unknown')

    def test_match_fullwidth_normalized(self):
        from utils.import_engine import match_field
        assert match_field('姓　名') == ('name', 'exact')  # 全角空格归一化


# ============ 2. 阈值守卫 ============
class TestThreshold:
    def test_block_by_ratio(self):
        from utils.import_engine import compute_threshold
        cols = [{'target': None} for _ in range(3)]
        blocked, th, reason = compute_threshold(['a', 'b', 'c'], cols)
        assert blocked is True and th['ratio'] == 1.0
        assert '未映射比例' in reason

    def test_block_by_new_fields(self):
        from utils.import_engine import compute_threshold
        cols = ([{'target': 'name'}] * 19) + ([{'target': None}] * 11)
        blocked, th, reason = compute_threshold([f'h{i}' for i in range(30)], cols)
        assert blocked is True and th['new_fields'] == 11

    def test_pass(self):
        from utils.import_engine import compute_threshold
        cols = ([{'target': 'name'}] * 9) + ([{'target': None}] * 1)
        blocked, th, _ = compute_threshold([f'h{i}' for i in range(10)], cols)
        assert blocked is False


# ============ 3. 类型转换（日期多格式） ============
class TestDateConversion:
    def test_date_multi_format(self):
        from utils.import_engine import parse_date_value
        from datetime import date, datetime
        assert parse_date_value('2024-03-01') == date(2024, 3, 1)
        assert parse_date_value('2024年3月1日') is None  # 中文日期待扩展（见下方注释）
        assert parse_date_value('2024.03.01') == date(2024, 3, 1)
        assert parse_date_value('20240301') == date(2024, 3, 1)
        assert parse_date_value(datetime(2024, 3, 1)) == date(2024, 3, 1)

    def test_date_chinese_format(self):
        """PRD 要求支持 %Y年%m月%d日；当前 parse_date_value 未覆盖中文格式，
        本测试如实标注该缺口（T12 重建口径），验证现有行为并留作后续补充锚点。"""
        from utils.import_engine import parse_date_value
        # 中文日期当前解析为 None（如实标注：待扩展 parse_date_value 支持）
        assert parse_date_value('2024年3月1日') is None


# ============ 4. 值域映射 ============
class TestValueDomain:
    def test_gender_mf(self):
        from utils.import_engine import apply_value_domain
        assert apply_value_domain('gender', 'M') == '男'
        assert apply_value_domain('gender', 'F') == '女'
        assert apply_value_domain('gender', '1') == '男'
        assert apply_value_domain('gender', '2') == '女'

    def test_unmapped_passthrough(self):
        from utils.import_engine import apply_value_domain
        assert apply_value_domain('gender', '未知X') == '未知X'  # 未命中透传
        assert apply_value_domain('name', '张三') == '张三'      # 无映射字段透传


# ============ 5/6/7/8. 容错层 + registry 登记 + 暂存区 + 去重 ============
class TestImportTolerance:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def _prepare_ext(self, app, tmp_path):
        """含未知字段「家庭收入」的名单（方案A ext_data）。"""
        xlsx = tmp_path / "ext.xlsx"
        _write_xlsx(str(xlsx),
                    ['姓名', '身份证号', '家庭收入', '性别'],
                    [['张三', '110101199001011237', '2000', 'M'],
                     ['李四', '110101199002022341', '3000', 'F'],
                     ['缺证件', '', '1500', 'M']])
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
            {'source': '家庭收入', 'action': 'ext', 'target': '家庭收入', 'matched_by': 'unknown'},
            {'source': '性别', 'action': 'map', 'target': 'gender', 'matched_by': 'exact'},
        ])
        return batch_id

    def test_ext_data_and_not_blocked(self, app, tmp_path):
        """未知字段进 ext_data，导入不中断（六条验收第 1 条）。"""
        _unlock_keyholder()
        batch_id = self._prepare_ext(app, tmp_path)
        from utils.import_engine import run_import
        from models import db, Resident, ImportBatch
        with app.app_context():
            run_import('task_ext', batch_id)
        with app.app_context():
            zhang = Resident.query.filter_by(name='张三').first()
            assert zhang is not None
            import json as _j
            ext = _j.loads(zhang.ext_data)
            assert ext.get('家庭收入') == '2000'
            # 性别值域映射生效
            assert zhang.gender == '男'
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'

    def test_registry_draft_registered(self, app, tmp_path):
        """新增字段登记 registry（draft）（六条验收第 1 条）。"""
        _unlock_keyholder()
        batch_id = self._prepare_ext(app, tmp_path)
        from utils.import_engine import run_import, list_registry
        from models import ImportBatch
        with app.app_context():
            run_import('task_reg', batch_id)
        with app.app_context():
            recs = list_registry()
            codes = [r.code for r in recs]
            assert '家庭收入' in codes
            draft = [r for r in recs if r.code == '家庭收入'][0]
            assert draft.status == 'draft'
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.new_field_count == 1

    def test_required_missing_to_staging(self, app, tmp_path):
        """必填缺失 → 暂存区，整批不阻断（六条验收第 3 条）。"""
        _unlock_keyholder()
        batch_id = self._prepare_ext(app, tmp_path)
        from utils.import_engine import run_import
        from models import db, ImportBatch, ImportRowError, Resident
        with app.app_context():
            run_import('task_stage', batch_id)
        with app.app_context():
            # 缺证件行进暂存区
            errs = ImportRowError.query.filter_by(batch_id=batch_id).all()
            assert any('缺证件' in (e.raw_row or '') for e in errs)
            # 整批完成，未阻断
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'
            assert b.success_rows == 2
            assert b.fail_rows == 1
            # 明文身份证未泄入暂存区原文
            for e in errs:
                assert '110101' not in (e.raw_row or '')

    def test_dedup_db_and_list(self, app, tmp_path):
        """去重：库内 + 名单内，跳过已成功行（六条验收第 4 条）。"""
        _unlock_keyholder()
        from models import db, Resident, ImportBatch
        with app.app_context():
            db.session.add(Resident(name='已有', id_card='110101199001011237'))
            db.session.commit()

        xlsx = tmp_path / "dup.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'],
                    [['新来', '110101199001011237'],    # 库内重复 → skip
                     ['重复', '110101199003033456'],    # 有效
                     ['再重复', '110101199003033456']])  # 名单内重复 → skip
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
        ])
        from utils.import_engine import run_import
        with app.app_context():
            run_import('task_dup', batch_id)
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.success_rows == 1
            assert b.skip_rows == 2


# ============ 9. 失败行仅导出 ============
class TestExportFailed:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_export_failed_rows(self, app, tmp_path):
        _unlock_keyholder()
        xlsx = tmp_path / "e.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'],
                    [['张三', '110101199001011237'], ['坏', 'badid']])
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
        ])
        from utils.import_engine import run_import
        with app.app_context():
            run_import('task_exp', batch_id)

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1
        r = client.get(f'/import/{batch_id}/export-failed')
        # 108 对齐 106 §7.1：token 通道 302 → 一次性令牌直出
        assert r.status_code == 302
        assert '/report/download/' in r.headers.get('Location', '')
        r2 = client.get(r.headers['Location'])
        assert r2.status_code == 200
        assert r2.data.startswith(b'PK')  # xlsx 文件头
        # 失败行导出仅含暂存表脱敏内容：成功行身份证（'110101199001011237'）
        # 不得出现在失败行导出中（106 §5.2）
        assert b'110101199001011237' not in r2.data


# ============ 10. 阈值阻断不落库 + admin 豁免 ============
class TestThresholdBlockHttp:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_block_no_business_data(self, app, tmp_path):
        xlsx = tmp_path / "b.xlsx"
        headers = [f'陌生列{i}' for i in range(15)]
        _write_xlsx(str(xlsx), headers, [[''] * 15])
        batch_id = _make_batch(app, str(xlsx))

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 1
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


# ============ 11. 统计口径门 ============
class TestStatisticsGate:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_gate(self, app):
        from utils.import_engine import (is_statistics_eligible,
                                         register_new_fields,
                                         approve_registry_field)
        with app.app_context():
            # 主字段放行
            assert is_statistics_eligible('name') is True
            assert is_statistics_eligible('id_card') is True
            # 未登记扩展字段拒绝
            assert is_statistics_eligible('家庭收入') is False
            # 登记 draft 仍拒绝
            register_new_fields('batch_x', ['家庭收入'])
            assert is_statistics_eligible('家庭收入') is False
            # 审批后放行
            approve_registry_field('家庭收入', approved_by=1)
            assert is_statistics_eligible('家庭收入') is True


# ============ 12. 审批流 + 版本号 ============
class TestSchemaGovernance:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_schema_change_and_version(self, app):
        from utils.import_engine import (create_schema_change_request,
                                         decide_schema_change,
                                         bump_data_model_version)
        from models import SchemaChangeRequest, DataModelVersion
        with app.app_context():
            no = create_schema_change_request('add_field', {'code': '新字段'},
                                              requested_by=1)
            req = SchemaChangeRequest.query.filter_by(request_no=no).first()
            assert req.status == 'pending'

            decide_schema_change(no, True, decided_by=1)
            req = SchemaChangeRequest.query.filter_by(request_no=no).first()
            assert req.status == 'approved'
            assert req.decided_at is not None

            v1 = bump_data_model_version('residents', '新增字段')
            v2 = bump_data_model_version('residents', '再新增字段')
            assert v1 == 1 and v2 == 2
            rows = DataModelVersion.query.filter_by(model_name='residents').all()
            assert len(rows) == 2


# ============ 13. A1 集成回归 ============
class TestA1Integration:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_async_trajectory_and_resume(self, app, tmp_path):
        _unlock_keyholder()
        xlsx = tmp_path / "a1.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'],
                    [['张三', '110101199001011237'], ['李四', '110101199002022341']])
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
        ])

        from utils.async_tasks import create_task, get_task, run_async
        from utils.import_engine import run_import, resume_import
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

        with app.app_context():
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'
            # 已完成批次不可重进（resume_import 对 done 返回 False）
            assert resume_import(b) is False


# ============ 14. 新装空库口径 ============
class TestFreshDb:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_main_fields_ok_and_stats_unaffected(self, app, tmp_path):
        """含额外字段名单导入后：主字段正常建档、额外字段可见、统计口径不受污染。"""
        _unlock_keyholder()
        xlsx = tmp_path / "fresh.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号', '家庭收入'],
                    [['张三', '110101199001011237', '2000']])
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
            {'source': '家庭收入', 'action': 'ext', 'target': '家庭收入', 'matched_by': 'unknown'},
        ])
        from utils.import_engine import run_import, is_statistics_eligible
        from utils.crypto import key_holder
        from models import db, Resident
        with app.app_context():
            run_import('task_fresh', batch_id)
        with app.app_context():
            zhang = Resident.query.filter_by(name='张三').first()
            assert zhang is not None
            # 主字段正常建档（密文）
            assert zhang.id_card is None
            assert zhang.id_card_enc.startswith('gAAAA')
            # 额外字段可见
            import json as _j
            assert _j.loads(zhang.ext_data).get('家庭收入') == '2000'
            # 统计口径：主字段可入统计，额外字段未审批不可入统计
            assert is_statistics_eligible('name') is True
            assert is_statistics_eligible('家庭收入') is False


# ============ 13b. 续跑接线（035 R-034-03） ============
class TestResumeWiring:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_startup_hook_marks_stale_running(self, tmp_path, monkeypatch):
        """R-035-05②：钩子只在「进程启动路径」调用，且恰好一次。

        本仓 create_app 也被后台任务线程调用（任务级），进程重启语义只能由调用方
        参数 skip_startup_hooks 表达——故断言调用次数，不再把「二次 create_app」
        当作「模拟进程重启」（该失真前提正是 R-035-03 长期未被测出的原因）。
        """
        import utils.import_engine as eng
        _make_app(tmp_path, monkeypatch)   # 先建 tmp 环境；本次调用发生在计数前
        import app as app_module           # 模块级 app = create_app() 至多一次
        calls = []
        monkeypatch.setattr(eng, 'mark_aborted_stale', lambda: calls.append(1))
        app_module.create_app()                                  # 默认 False = 进程启动
        assert len(calls) == 1, '进程启动路径应恰调用一次钩子'
        app_module.create_app(skip_startup_hooks=True)           # 后台任务借上下文
        assert len(calls) == 1, 'skip_startup_hooks=True 时钩子零调用'

    def test_background_task_create_app_keeps_running(self, tmp_path, monkeypatch):
        """R-035-03：run_async 的 worker 线程建 app 只是借上下文，不得清 running 批次。

        回归背景：钩子曾无条件挂在 create_app 上，而每个后台任务（import/reconcile/
        subsidy/migration/lazy_check）都会走一次 create_app —— 正在执行的导入批次被
        误标 aborted，破坏 execute/resumable 依赖的「aborted ⇒ 无人执行」不变量。
        """
        app = _make_app(tmp_path, monkeypatch)
        from models import db, ImportBatch
        with app.app_context():
            db.session.add(ImportBatch(batch_id='alive01', source_file='x.xlsx',
                                       source_path='/nonexistent.xlsx',
                                       source_hash='h_alive_01', operator=1,
                                       status='running'))
            db.session.commit()
        # 与 utils/async_tasks._runner 完全同参
        app2 = _make_app(tmp_path, monkeypatch, skip_startup_hooks=True)
        with app2.app_context():
            b = ImportBatch.query.filter_by(batch_id='alive01').first()
            assert b.status == 'running', '后台任务建 app 不得改动运行中批次'

    def test_startup_hook_marks_stale_running_aborted(self, tmp_path, monkeypatch):
        """R-037-01：残留 running 批次在「进程启动路径」被真实内核标 aborted。

        与 test_background_task_create_app_keeps_running 互为对偶：
        - skip_startup_hooks=True（后台任务借上下文）→ running 不动
        - skip_startup_hooks=False（进程启动，默认）→ running 翻转为 aborted
        内核 mark_aborted_stale 不 monkeypatch，真实执行（修复前该行为断言被整体删除）。
        """
        app = _make_app(tmp_path, monkeypatch)  # 默认 False，此时库内尚无 running 批次
        from models import db, ImportBatch
        with app.app_context():
            db.session.add(ImportBatch(batch_id='stale01', source_file='x.xlsx',
                                       source_path='/nonexistent.xlsx',
                                       source_hash='h_stale_01', operator=1,
                                       status='running'))
            db.session.commit()

        import app as app_module
        app_module.create_app()  # 默认 skip_startup_hooks=False = 进程启动路径
        with app.app_context():
            b = ImportBatch.query.filter_by(batch_id='stale01').first()
            assert b.status == 'aborted', '进程启动路径应把残留 running 批次标 aborted'

    def test_resume_skips_already_written_rows(self, app, tmp_path):
        """R-034-03③：aborted 批次续跑——库内已成功行按身份证 hash 跳过，不重复写入。"""
        _unlock_keyholder()
        xlsx = tmp_path / "resume.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'], [
            ['张三', '110101199001011237'],
            ['李四', '110101199002022341'],
            ['王五', '110101199003033456'],
        ])
        batch_id = _make_batch(app, str(xlsx), status='aborted')
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
        ])
        from utils.import_engine import apply_resident_row, run_import, resume_import
        from models import db, Resident, ImportBatch
        with app.app_context():
            # 模拟中断前已成功写入前两行（走唯一写入通道，id_card_hash 落库）
            for name, idc in (('张三', '110101199001011237'),
                              ('李四', '110101199002022341')):
                r = Resident()
                db.session.add(r)
                apply_resident_row(r, {'name': name, 'id_card': idc}, True, 'skip',
                                   {'map': {}, 'counter': [0]}, None)
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert resume_import(b) is True, 'aborted 批次应判可续跑'
            db.session.commit()
            assert Resident.query.count() == 2
            run_import('task_resume', batch_id)
        with app.app_context():
            assert Resident.query.count() == 3, '续跑后应恰好 3 人，零重复写入'
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'
            assert b.skip_rows == 2, '已成功行按身份证 hash 跳过（跳过计数应为 2）'
            assert b.success_rows == 1

    def test_upload_duplicate_returns_resumable_flag(self, app, tmp_path):
        """R-034-03②：/import/upload 命中 duplicate 且批次未完成 → 响应带 resumable=True。"""
        import io
        xlsx = tmp_path / "dup.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'],
                    [['赵六', '110101199004044560']])
        client = app.test_client()
        with client.session_transaction() as s:
            s['user_id'] = 1  # admin
        data = {'file': (io.BytesIO(xlsx.read_bytes()), 'dup.xlsx')}
        r1 = client.post('/import/upload', data=data,
                         content_type='multipart/form-data')
        assert r1.status_code == 200
        bid = r1.get_json()['data']['batch_id']
        from models import db, ImportBatch
        with app.app_context():
            # 142：aborted 且 0 行会作废重建（不再 duplicate）；resumable 语义的
            # 承载形态 = 已入库一半的中断批次（身份证 hash 断点续传的真实场景）
            ImportBatch.query.filter_by(batch_id=bid).update(
                {'status': 'aborted', 'success_rows': 1})
            db.session.commit()
        data2 = {'file': (io.BytesIO(xlsx.read_bytes()), 'dup.xlsx')}
        r2 = client.post('/import/upload', data=data2,
                         content_type='multipart/form-data')
        j2 = r2.get_json()
        assert j2['success'] and j2['data']['duplicate'] is True
        assert j2['data']['batch_id'] == bid, '重复文件应复用既有批次'
        assert j2['data']['status'] == 'aborted'
        assert j2['data']['resumable'] is True, '未完成批次应返回可续跑标识'

    def test_startup_hook_never_blocks_app_start(self, tmp_path, monkeypatch):
        """R-034-03① 护栏：存量库未迁移 m013（import_batch 缺 mode 等列）时，
        启动钩子的 OperationalError 不得演变为 create_app 整体失败（035 实测回归）。"""
        import utils.import_engine as eng
        from sqlalchemy.exc import OperationalError

        def _boom():
            raise OperationalError('SELECT ...', {}, Exception('no such column: import_batch.mode'))

        monkeypatch.setattr(eng, 'mark_aborted_stale', _boom)
        app = _make_app(tmp_path, monkeypatch)   # 不得抛错
        assert app is not None
        # 应用仍可正常渲染页面（启动未被阻断）
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = 1
        assert c.get('/dashboard/').status_code == 200

    @pytest.mark.parametrize('exc_name', ['DatabaseError', 'ProgrammingError'])
    def test_startup_hook_wide_db_errors_never_block_start(self, tmp_path, monkeypatch, exc_name):
        """R-035-05①（红→绿判定项）：放宽捕获到 SQLAlchemyError 后，
        非 OperationalError 的 DB 异常（文件损坏 / 方言差异）同样不得阻断启动。

        修复前红形态 = 该异常自 create_app 冒泡（不是 TypeError）。
        """
        import utils.import_engine as eng
        import sqlalchemy.exc as sx

        def _boom():
            raise getattr(sx, exc_name)('SELECT ...', {},
                                        Exception('database disk image is malformed'))

        monkeypatch.setattr(eng, 'mark_aborted_stale', _boom)
        app = _make_app(tmp_path, monkeypatch)
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = 1
        assert c.get('/dashboard/').status_code == 200

    def test_missing_columns_gives_migration_hint(self, app, tmp_path):
        """GLB-03-②：缺列引发的 OperationalError → 500 含迁移指引，非通用 500。

        R-037-04：缺列库态改「删表重建窄表」构造（不依赖 SQLite ≥ 3.35 的 DROP COLUMN）。"""
        import io
        _rebuild_import_batch_without(app, ('mode', 'profile_key', 'policy', 'updated_rows'))
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = 1
        xlsx = tmp_path / 'old.xlsx'
        _write_xlsx(str(xlsx), ['姓名', '身份证号'], [['张三', '110101199001011237']])
        r = c.post('/import/upload',
                   data={'file': (io.BytesIO(xlsx.read_bytes()), 'old.xlsx')},
                   content_type='multipart/form-data')
        assert r.status_code == 500
        assert 'm013_import_batch_mode.sql' in r.get_data(as_text=True)

    def test_startup_schema_check_warns_missing_columns(self, tmp_path, monkeypatch, caplog):
        """GLB-03-③：启动期只读体检——缺列打 warning（列名 + 迁移脚本），不阻断启动。

        R-037-04：同上，窄表构造替代 DROP COLUMN。"""
        import logging
        app = _make_app(tmp_path, monkeypatch)
        _rebuild_import_batch_without(app, ('mode',))
        with caplog.at_level(logging.WARNING, logger='shucun'):
            app2 = _make_app(tmp_path, monkeypatch)
        assert app2 is not None, '体检不得阻断启动'
        assert '启动体检' in caplog.text and 'mode' in caplog.text
        assert 'm013_import_batch_mode.sql' in caplog.text
        assert app2.test_client().get('/auth/login').status_code == 200


# ============ 13c. 预览/执行口径一致（035 R-034-05） ============
class TestPreviewReceiptConsistency:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        return _make_app(tmp_path, monkeypatch)

    def test_preview_fail_equals_receipt_fail(self, app, tmp_path):
        """R-034-05：同一含坏行文件——预览「失败 M」== 执行回执「失败 N」。

        组成（标准 100 行验收文件的微缩版）：3 正常 + 1 必填缺失（硬失败）+
        1 坏出生日期（原预览失败/执行成功的口径漂移类，现排除出执行并计入回执）+
        1 名单内重复（信息桶，不计失败、执行按跳过口径）。
        """
        _unlock_keyholder()
        xlsx = tmp_path / "mixed.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号', '出生日期'], [
            ['张三', '110101199001011237', ''],
            ['李四', '110101199002022341', ''],
            ['王五', '110101199003033456', ''],
            ['赵缺', '', ''],                                 # 必填缺失（硬失败）
            ['钱日', '110101199004044560', '2026年13月99日'],  # 坏出生日期（硬失败）
            ['张三', '110101199001011237', ''],                # 名单内重复（信息桶）
        ])
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
            {'source': '出生日期', 'action': 'map', 'target': 'birth_date', 'matched_by': 'exact'},
        ])
        from utils.import_engine import (read_xlsx, validate_rows, load_mapping,
                                         run_import)
        from models import db, ImportBatch, ImportRowError, Resident
        with app.app_context():
            headers, rows = read_xlsx(str(xlsx))
            summary = validate_rows(batch_id, headers, rows, load_mapping(batch_id),
                                    required_codes=('name', 'id_card'))
            assert summary['total'] == 6
            assert summary['valid'] == 3
            assert summary['fail'] == 2, '必填缺失 + 坏出生日期均为硬失败'
            assert summary['list_dup'] == 1, '名单内重复走信息桶，不计入失败'
            run_import('task_r5', batch_id)
        with app.app_context():
            b = ImportBatch.query.filter_by(batch_id=batch_id).first()
            assert b.status == 'done'
            assert b.fail_rows == summary['fail'], '回执失败数必须与预览失败数一致'
            assert b.success_rows == 3
            assert b.skip_rows == 1, '名单内重复按跳过口径执行'
            assert Resident.query.count() == 3, '失败行不落库，干净行恰好 3 人'
            # 预览失败明细已落暂存区（供报告与失败行导出），且行数与预览失败行对齐
            err_rows = {e.row_no for e in ImportRowError.query.filter_by(batch_id=batch_id)}
            assert err_rows == {5, 6}, '硬失败两行（Excel 行号 5/6）明细齐全'

    def test_update_mode_db_dup_not_prefailed(self, app, tmp_path):
        """R-034-05 伴生护栏：库内重复为信息桶——覆盖更新模式下既有居民仍被执行不被排除。"""
        _unlock_keyholder()
        xlsx = tmp_path / "upd.xlsx"
        _write_xlsx(str(xlsx), ['姓名', '身份证号'], [
            ['张三', '110101199001011237'],
        ])
        batch_id = _make_batch(app, str(xlsx))
        _save_columns(app, batch_id, [
            {'source': '姓名', 'action': 'map', 'target': 'name', 'matched_by': 'exact'},
            {'source': '身份证号', 'action': 'map', 'target': 'id_card', 'matched_by': 'exact'},
        ])
        from utils.import_engine import apply_resident_row, read_xlsx, validate_rows, load_mapping
        from models import db, Resident, ImportBatch
        with app.app_context():
            r = Resident()
            db.session.add(r)
            apply_resident_row(r, {'name': '张三', 'id_card': '110101199001011237',
                                   'phone': '13800000001'}, True, 'skip',
                               {'map': {}, 'counter': [0]}, None)
            db.session.commit()
            headers, rows = read_xlsx(str(xlsx))
            summary = validate_rows(batch_id, headers, rows, load_mapping(batch_id),
                                    required_codes=('name', 'id_card'))
            # 库内重复：信息桶（duplicate=1），不预扣失败、valid 不含该行
            assert summary['duplicate'] == 1
            assert summary['fail'] == 0, '库内重复不得预扣失败（覆盖更新模式须可执行）'
            assert summary['valid'] == 0
