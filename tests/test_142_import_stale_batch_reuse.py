# -*- coding: utf-8 -*-
"""tests.test_142_import_stale_batch_reuse — 居民批量导入 0 行残留批次死锁修复回归。

复现形态（主理人报）：首次导入被取消（未入库，批次停在 mapped）→ 同文件二次导入
无论仅新增/覆盖更新都被「该文件已导入过（返回既有批次）」拦死。

修法：start_batch 判重按状态分流（142）——
  0 行 pending/mapped/aborted/blocked → 作废旧批次，放行重建新批次；
  done / running / 已入库（>0 行）→ 维持幂等（034 幂等一）。
辅助：/import/<batch_id>/void 手动作废端点（批次中心清理残留）。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _make_app(tmp_path, monkeypatch):
    """078/134 先例：env + config 双覆写（config.py 首次 import 即固化路径，GLB-17）。"""
    import config
    db_path = str(tmp_path / 'shucun.db')
    for key, value in {
        'SHUCUN_DB_PATH': db_path,
        'SHUCUN_TEMP_DIR': str(tmp_path / 'temp'),
        'SHUCUN_UPLOAD_DIR': str(tmp_path / 'uploads'),
        'SHUCUN_BACKUP_DIR': str(tmp_path / 'backups'),
        'SHUCUN_DATA_DIR': str(tmp_path / 'data'),
        'SHUCUN_LOG_DIR': str(tmp_path / 'logs'),
        'SHUCUN_LICENSE_FILE': str(tmp_path / 'license.key'),
        'SHUCUN_LAN_AUTHORIZED': '1',
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(config, 'TEMP_DIR', str(tmp_path / 'temp'))
    monkeypatch.setattr(config, 'UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(config.Config, 'SQLALCHEMY_DATABASE_URI',
                        'sqlite:///%s?check_same_thread=False' % db_path)
    for sub in ('temp', 'uploads', 'backups', 'data', 'logs'):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    from models import db, User, Role
    from app import create_app
    application = create_app()
    with application.app_context():
        db.create_all()
        if not Role.query.first():
            db.session.add(Role(id=1, name='admin'))
            db.session.add(Role(id=2, name='user'))
        if not User.query.first():
            db.session.add(User(id=1, username='admin', password_hash='x', role_id=1))
        db.session.commit()
    application.config['TESTING'] = True
    return application


@pytest.fixture()
def client(tmp_path, monkeypatch):
    application = _make_app(tmp_path, monkeypatch)
    from utils.crypto import generate_salt, key_holder
    key_holder.initialize('stale-batch-pass', salt=generate_salt())
    c = application.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
    c.application = application
    return c




def _xlsx_bytes(tag):
    """最小可解析 xlsx 载体：start_batch 落盘后要过 count_spreadsheet_rows 真解析，
    纯 zip 假载体会被 openpyxl 拒（zip 魔数只骗得过魔数校验，骗不过解析）。
    内容全部是自造测试值，无真实数据。tag 仅用于制造不同字节流（不同 hash）。"""
    import io as _io
    import openpyxl as _opx
    wb = _opx.Workbook()
    ws = wb.active
    ws.title = 'S'
    ws.append(['姓名', '身份证号码'])
    ws.append(['测试-' + tag, '110101199001011234'])
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _mk_batch(client, code='stale_a', source_file='旧名单.xlsx', status='mapped',
              success_rows=0, updated_rows=0):
    """直接落一枚可控状态的批次行（不经上传，隔离状态机）。"""
    from models import db, ImportBatch
    from utils.import_engine import STANDARD_PROFILE_KEY, CATALOG_VERSION
    with client.application.app_context():
        b = ImportBatch(
            batch_id=code,
            source_file=source_file,
            source_path='whatever.xlsx',
            source_hash=code + '-hash',
            operator=1,
            status=status,
            mode='template',
            profile_key=STANDARD_PROFILE_KEY,
            catalog_version=CATALOG_VERSION,
            policy='skip',
            success_rows=success_rows,
            updated_rows=updated_rows,
        )
        db.session.add(b)
        db.session.commit()
        return code, code + '-hash'


def test_stale_mapped_batch_not_resumable_before_fix(client):
    """修复前形态存证：mapped 不在 resume_import 名单（这是死锁的半个根因）。"""
    from utils.import_engine import resume_import
    code, _h = _mk_batch(client, code='stale_mapped', status='mapped')
    with client.application.app_context():
        from models import ImportBatch
        b = ImportBatch.query.filter_by(batch_id=code).first()
        assert resume_import(b) is False


def test_start_batch_voids_zero_row_stale_and_creates_new(client):
    """142 核心：0 行 mapped 残留 + 同 hash 重传 → 旧批次 aborted、新批次 pending。"""
    from models import ImportBatch, db
    from utils.import_engine import start_batch, STANDARD_PROFILE_KEY
    code, _h = _mk_batch(client, code='stale_v1', status='mapped')

    # 用同一 source_hash 构造一个全新上传（文件字节 = hash 对应内容）：
    # start_batch 以上传字节算 hash，为让它命中既有 hash，直接改既有批次的 hash
    # 为目标字节流的 hash（等价模拟「同文件」）。
    payload = _xlsx_bytes('stale-v1')
    import hashlib
    target_hash = hashlib.sha256(payload).hexdigest()
    with client.application.app_context():
        b = ImportBatch.query.filter_by(batch_id=code).first()
        b.source_hash = target_hash
        db.session.commit()

    class _F:
        filename = '重传名单.xlsx'

        def read(self):
            return payload

    with client.application.app_context():
        batch, err, was_dup = start_batch(_F(), 1, 'template')
        new_id, new_status = batch.batch_id, batch.status
    assert err is None
    # 142 新语义：不再 duplicate 短路，而是作废旧批次 + 建新批次
    assert was_dup is False and err is None
    assert new_id != code
    assert new_status == 'pending'
    with client.application.app_context():
        old = ImportBatch.query.filter_by(batch_id=code).first()
        assert old.status == 'aborted'
        assert '142' in (old.block_reason or '')


def test_done_batch_still_idempotent(client):
    """done 批次（已入库）重复上传维持幂等返回（034 幂等一不破坏）。"""
    from models import ImportBatch, db
    from utils.import_engine import start_batch
    import hashlib
    payload = _xlsx_bytes('done-v1')
    target_hash = hashlib.sha256(payload).hexdigest()
    _mk_batch(client, code='done_v1', status='done', success_rows=30, updated_rows=5)
    with client.application.app_context():
        b = ImportBatch.query.filter_by(batch_id='done_v1').first()
        b.source_hash = target_hash
        db.session.commit()

    class _F:
        filename = '已完成名单.xlsb'

        def read(self):
            return payload

    with client.application.app_context():
        batch, err, was_dup = start_batch(_F(), 1, 'template')
        dup_id = batch.batch_id
    assert was_dup is True and err is None
    assert dup_id == 'done_v1'


def test_imported_aborted_batch_not_voided(client):
    """>0 行的 aborted 批次（执行过一半）重传不误作废 → 维持幂等（报告保留）。"""
    from models import ImportBatch, db
    from utils.import_engine import start_batch
    import hashlib
    payload = _xlsx_bytes('half-v1')
    target_hash = hashlib.sha256(payload).hexdigest()
    _mk_batch(client, code='half_v1', status='aborted', success_rows=120)
    with client.application.app_context():
        b = ImportBatch.query.filter_by(batch_id='half_v1').first()
        b.source_hash = target_hash
        db.session.commit()

    class _F:
        filename = '中断名单.xlsb'

        def read(self):
            return payload

    with client.application.app_context():
        batch, err, was_dup = start_batch(_F(), 1, 'template')
        dup_id = batch.batch_id
    assert was_dup is True and dup_id == 'half_v1'
    with client.application.app_context():
        b = ImportBatch.query.filter_by(batch_id='half_v1').first()
        assert b.status == 'aborted'  # 未被改写
        assert (b.block_reason or '') == ''  # 未打 142 标记


def test_void_endpoint_only_for_zero_row(client):
    """/import/<id>/void：0 行残留可作废；>0 行 / running 拒绝。"""
    _mk_batch(client, code='void_a', status='mapped')
    _mk_batch(client, code='void_b', status='aborted', success_rows=10)
    resp = client.post('/import/void_a/void')
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    resp2 = client.post('/import/void_b/void')
    assert resp2.status_code == 400
    assert '行入库' in resp2.get_json()['msg']


def test_resident_api_import_recovers_after_stale(client):
    """端到端：mapped 残留批次在场时，模板通道二次上传不再被 duplicate 短路。

    走真实 /resident/api/import：上传用同 hash 字节流的 .csv（走 template 预设映射
    会缺列 blocked —— 但只要返回的 JSON 不是 duplicate=True 即证明死锁解除）。
    """
    import hashlib
    from models import ImportBatch, db
    from utils.import_engine import STANDARD_PROFILE_KEY
    code, _h = _mk_batch(client, code='e2e_v1', status='mapped')
    payload = ('姓名,身份证号码\n测试,110101199001011234\n').encode('utf-8')
    target_hash = hashlib.sha256(payload).hexdigest()
    with client.application.app_context():
        b = ImportBatch.query.filter_by(batch_id=code).first()
        b.source_hash = target_hash
        db.session.commit()

    data = {'mode': 'append'}
    resp = client.post('/resident/api/import', data={**data, 'file': (
        __import__('io').BytesIO(payload), '重传名单.csv')},
        content_type='multipart/form-data')
    body = resp.get_json()
    assert body is not None
    data_part = body.get('data') or {}
    # 关键断言：不再命中 duplicate 短路（允许 blocked：缺其余必需列，属另一契约）
    assert data_part.get('duplicate') is not True, body
    assert data_part.get('batch_id') != code
