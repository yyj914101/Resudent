# -*- coding: utf-8 -*-
"""tests/test_device_screen_isolation.py — 050 设备管理 × 大屏凭证隔离（R-050-01/02/03）。

背景（review-049 §四 N-049-05/06/07）：049-P2 把大屏记录复用进 device_registry 后，
设备侧操作未按 device_type 分流：对大屏行点「重新签发」会用 Ed25519 凭证覆盖
credential 里的 token → 大屏地址 403 而列表仍显示「有效」。

覆盖（全部反向用例，对齐 050 任务书验收 1-5）：
- R-050-01 后端：reissue 遇 screen 行拒绝，credential/machine_code 逐字节不变
- R-050-01 前端：list.html 的「批准/重新签发」受 device_type 分支控制（静态断言）
- R-050-01 状态：token 被清空的 screen 行 status 不再是 approved（凭证缺失）
- R-050-02 计数：counts 只统计客户机（1 客户机 + 2 大屏 → approved == 1）
- R-050-03 反例：密钥文件损坏 → 握手/签发返回可读 JSON（非 500），恢复后全链路通过
"""
import json
import os
import re
from datetime import datetime

import pytest

from test_device_auth import _admin_client, _handshake, _make_app

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'templates', 'device', 'list.html')


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch, admission=True)


def _add_screen(app, name='测试大屏', token=None):
    """直接造一块大屏行（同 routes.screen._create_screen，但不走 HTTP）。"""
    import secrets as _secrets
    from models import db, DeviceRegistry
    from routes.screen import _store_screen
    token = token or ('S' + _secrets.token_urlsafe(24))
    with app.app_context():
        row = DeviceRegistry(label=name, first_seen=datetime.now())
        _store_screen(row, token)
        db.session.add(row)
        db.session.commit()
        return row.id, row.credential, row.machine_code


def _screen_item(app, client, screen_id):
    r = client.get('/screen-admin/api/screens')
    items = r.get_json()['data']['items']
    return [i for i in items if i['id'] == screen_id][0]


# ---------------- R-050-01 后端：reissue 不得破坏大屏凭证 ----------------

class TestReissueScreenGuard:
    def test_reissue_rejected_for_screen_row(self, app):
        sid, cred, machine = _add_screen(app, name='二楼会议室')
        r = _admin_client(app).post(f'/device-mgmt/api/{sid}/reissue')
        body = r.get_json()
        assert body['success'] is False
        assert '大屏管理' in body['msg']

        from models import db, DeviceRegistry
        with app.app_context():
            row = db.session.get(DeviceRegistry, sid)
            assert row.credential == cred               # 逐字节不变
            assert row.machine_code == machine
            assert row.status == 'approved'
            assert json.loads(row.credential)['token']

    def test_reissue_still_works_for_client(self, app):
        """守卫不得误伤客户机正常重签（既有链路的正向对照）。"""
        c = app.test_client()
        _handshake(c)
        r = _admin_client(app).post('/device-mgmt/api/1/reissue')
        assert r.get_json()['success'] is True
        assert r.get_json()['data']['credential']['sign']


# ---------------- R-050-01 前端：大屏行不渲染「批准 / 重新签发」 ----------------

class TestListTemplateGuard:
    def test_approve_reissue_guarded_by_device_type(self):
        with open(TEMPLATE, encoding='utf-8') as fh:
            src = fh.read()
        m = re.search(r"if\s*\(\s*d\.device_type\s*!==\s*'screen'\s*\)\s*\{(.*?)\}", src, re.S)
        assert m, 'list.html 未按 device_type 分流操作按钮'
        branch = m.group(1)
        assert 'data-act="approve"' in branch and 'data-act="reissue"' in branch
        assert 'data-act="revoke"' not in branch and 'data-act="delete"' not in branch


# ---------------- R-050-01 状态诚实：凭证缺失不再谎报「有效」 ----------------

class TestScreenStatusHonesty:
    def test_empty_credential_reports_missing(self, app):
        sid, _, _ = _add_screen(app)
        from models import db, DeviceRegistry
        with app.app_context():
            row = db.session.get(DeviceRegistry, sid)
            row.credential = '{}'                       # 模拟被历史误操作清空 token
            db.session.commit()
        item = _screen_item(app, _admin_client(app), sid)
        assert item['status'] != 'approved'
        assert item['status'] == 'missing'              # 前端映射「凭证缺失」
        assert not item['token']

    def test_intact_screen_still_approved(self, app):
        sid, _, _ = _add_screen(app)
        item = _screen_item(app, _admin_client(app), sid)
        assert item['status'] == 'approved' and item['token']


# ---------------- R-050-02 计数只统计客户机 ----------------

class TestCountsClientOnly:
    def test_counts_exclude_screen_rows(self, app):
        c = app.test_client()
        _handshake(c)                                    # 1 台客户机 pending → approved
        _admin_client(app).post('/device-mgmt/api/1/approve')
        _add_screen(app, name='A屏')
        _add_screen(app, name='B屏')
        data = _admin_client(app).get('/device-mgmt/api/list').get_json()['data']
        assert data['counts']['approved'] == 1
        assert data['counts']['pending'] == 0
        assert data['counts']['revoked'] == 0
        assert len(data['items']) == 3                   # 大屏行仍在列表展示


# ---------------- R-050-03 密钥损坏反例：可读 JSON，不落 500 ----------------

class TestCorruptDeviceKey:
    def _corrupt(self, app):
        from utils import device_auth
        with app.app_context():
            path = device_auth.ensure_keypair()          # 本用例未握手时先确保密钥已生成
        with open(path, 'rb') as fh:
            raw = fh.read()
        with open(path, 'wb') as fh:                     # 半截密文
            fh.write(raw[: max(1, len(raw) // 2)])
        device_auth.reset_cache()
        return raw

    def _restore(self, app, raw):
        from utils import device_auth
        with open(device_auth._key_path(), 'wb') as fh:
            fh.write(raw)
        device_auth.reset_cache()

    def test_approved_handshake_corrupt_key_readable(self, app):
        c = app.test_client()
        cred = _handshake(c).get_json()['data']['credential']
        _admin_client(app).post('/device-mgmt/api/1/approve')
        raw = self._corrupt(app)
        r = _handshake(c, credential=cred)
        assert r.status_code != 500
        body = r.get_json()
        assert body['success'] is False
        assert '密钥损坏' in body['msg']
        self._restore(app, raw)                          # 恢复后全链路通过
        assert _handshake(c, credential=cred).get_json()['data']['status'] == 'approved'

    def test_first_handshake_corrupt_key_readable(self, app):
        raw = self._corrupt(app)
        r = _handshake(app.test_client(), machine='BBBBBBBBBBBBBBBB')
        assert r.status_code != 500
        body = r.get_json()
        assert body['success'] is False and '密钥损坏' in body['msg']

    def test_reissue_corrupt_key_readable(self, app):
        c = app.test_client()
        _handshake(c)
        raw = self._corrupt(app)
        r = _admin_client(app).post('/device-mgmt/api/1/reissue')
        assert r.status_code != 500
        body = r.get_json()
        assert body['success'] is False and '密钥损坏' in body['msg']
