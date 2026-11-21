# -*- coding: utf-8 -*-
"""T21-2 花名册 PDF 导出单测（R10-E1~E4，reportlab + 思源黑体）。

覆盖：
- TestPdfRoster：PDF 生成（%PDF 魔数）/ 分页（/Type /Page ≥2）/ 统计行与盖章位
  文本（pdftotext 可用时）/ 脱敏两态（R10-E3）/ log_operation 留痕（R10-E2）/
  字体缺失可读报错 / 1000 行 ≤30s（RUN_PERF=1）

文本断言依赖 poppler-utils（pdftotext）；缺失时降级为结构断言（魔数/页数），
保证无 poppler 环境也能跑通核心链路。
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import date, timedelta

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


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # admin
    return client


def _years_ago(n):
    return date.today().replace(year=date.today().year - n)


def _make_resident(app, name, birth_date, grid='第1组', status='normal', flow_status='常住'):
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=birth_date, status=status, flow_status=flow_status,
                     village_group=grid)
        db.session.add(r)
        db.session.commit()
        return r.id


@pytest.fixture()
def app(tmp_path, monkeypatch):
    _unlock_keyholder()
    return _make_app(tmp_path, monkeypatch)


def _pdf_text(data: bytes):
    """提取 PDF 文本（pdftotext 可用时）；不可用返回 None。"""
    if not shutil.which('pdftotext'):
        return None
    p = subprocess.run(['pdftotext', '-', '-'], input=data,
                       capture_output=True, timeout=60)
    return p.stdout.decode('utf-8', errors='replace') if p.returncode == 0 else None


class TestPdfRoster:
    """T21-2 花名册 PDF（reportlab + 思源黑体）。"""

    def _export_pdf(self, client, category='高龄', grid=''):
        r = client.post('/report/export-pdf',
                        json={'type': 'roster', 'category': category, 'grid': grid})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        url = r.get_json()['data']['download_url']
        return client.get(url)

    def test_export_pdf_ok_and_magic(self, app):
        """PDF 生成成功：200 + %PDF 魔数 + 表头文本可提取（pdftotext）。"""
        _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        dr = self._export_pdf(client, '高龄')
        assert dr.status_code == 200
        assert dr.data.startswith(b'%PDF')
        text = _pdf_text(dr.data)
        if text is not None:
            assert '姓名' in text and '高龄甲' in text

    def test_pdf_multi_page(self, app):
        """分页：200 行 → PDF /Type /Page ≥2（R10 分页页码）。"""
        for i in range(200):
            _make_resident(app, f'高{i}', _years_ago(81), grid=f'第{i % 5 + 1}组')
        client = _admin_client(app)
        dr = self._export_pdf(client, '高龄')
        assert dr.data.startswith(b'%PDF')
        assert dr.data.count(b'/Type /Page') >= 2, '200 行应跨多页'

    def test_pdf_summary_and_stamp(self, app):
        """统计行 + 盖章位：文本含「高龄 共 N 人」与「盖章/签字位」（R10-E2/E4）。"""
        for i in range(3):
            _make_resident(app, f'高{i}', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        dr = self._export_pdf(client, '高龄')
        text = _pdf_text(dr.data)
        if text is None:
            pytest.skip('无 pdftotext，跳过文本断言（结构断言已覆盖）')
        assert '高龄 共 3 人' in text
        assert '盖章/签字位' in text
        assert '页/共' in text.replace(' ', '')    # 分页页码（pdftotext 压缩空白）

    def test_pdf_masked_vs_plain(self, app):
        """脱敏两态（R10-E3）：admin 明文文件名含「含明文_」；无 view_sensitive → 脱敏版。"""
        from models import db, Resident, User, Role
        from utils.crypto import key_holder
        rid = _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        with app.app_context():
            r = db.session.get(Resident, rid)
            r.id_card = None
            r.id_card_enc = key_holder.encrypt('110101194501011234')
            db.session.commit()
        # admin → 明文（文件名前缀 + PDF 文本含完整身份证）
        client = _admin_client(app)
        dr = self._export_pdf(client, '高龄')
        cd = urllib.parse.unquote(dr.headers.get('Content-Disposition', ''))
        assert '含明文_' in cd
        text = _pdf_text(dr.data)
        if text is not None:
            assert '110101194501011234' in text
        # 有 report.export 无 view_sensitive → 脱敏版（身份证前6后4）
        import json
        with app.app_context():
            role = Role(name='导出员', permissions=json.dumps(
                {'report.view': True, 'report.export': True}))
            db.session.add(role); db.session.commit()
            u = User(username='导出员p', password_hash='x', role_id=role.id, is_active=True)
            db.session.add(u); db.session.commit()
            uid = u.id
        client2 = app.test_client()
        with client2.session_transaction() as sess:
            sess['user_id'] = uid
        dr2 = self._export_pdf(client2, '高龄')
        cd2 = urllib.parse.unquote(dr2.headers.get('Content-Disposition', ''))
        assert '含明文_' not in cd2
        text2 = _pdf_text(dr2.data)
        if text2 is not None:
            assert '110101194501011234' not in text2
            assert '110101********1234' in text2    # 18 位身份证：前6后4，中间 8 位掩码

    def test_pdf_log_operation(self, app):
        """留痕（R10-E2）：log_operation 落库，detail 含「含明文=是/否」。"""
        from models import OperationLog
        _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        self._export_pdf(client, '高龄')
        with app.app_context():
            logs = OperationLog.query.filter_by(module='report', action='export').all()
            assert logs, '应存在 report.export 留痕'
            assert any('含明文=是' in (l.detail or '') for l in logs), \
                [l.detail for l in logs]

    def test_pdf_font_missing_error(self, app, monkeypatch):
        """字体缺失：500 + 可读提示（M6.0 C2 随包预置保障）。"""
        import utils.pdf_export as pdf_mod
        import config
        monkeypatch.setattr(pdf_mod, '_REGISTERED', False)          # 重置幂等缓存
        monkeypatch.setattr(config.Config, 'FONT_REGULAR_PATH', '/nonexist/NotoSansSC-Regular.ttf')
        _make_resident(app, '高龄甲', _years_ago(81), grid='第1组')
        client = _admin_client(app)
        r = client.post('/report/export-pdf', json={'type': 'roster', 'category': '高龄'})
        assert r.status_code == 500
        assert '字体缺失' in (r.get_json() or {}).get('msg', '')
        # 恢复后可用（_REGISTERED 已重置，字体路径恢复默认）
        monkeypatch.undo()
        dr = self._export_pdf(client, '高龄')
        assert dr.data.startswith(b'%PDF')

    def test_pdf_perf_1000_rows(self, app):
        """1000 行 PDF ≤30s（RUN_PERF=1 启用；详细设计 L1154 性能口径）。"""
        if os.environ.get('RUN_PERF') != '1':
            pytest.skip('性能压测默认跳过（RUN_PERF=1 启用）')
        for i in range(1000):
            _make_resident(app, f'高{i}', _years_ago(81), grid=f'第{i % 8 + 1}组')
        client = _admin_client(app)
        t0 = time.perf_counter()
        dr = self._export_pdf(client, '高龄')
        elapsed = time.perf_counter() - t0
        assert dr.data.startswith(b'%PDF')
        assert elapsed < 30, f'PDF 导出耗时 {elapsed:.1f}s 超 30s'
