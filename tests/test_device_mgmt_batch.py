# -*- coding: utf-8 -*-
"""tests/test_device_mgmt_batch.py — 052 簇 E 设备管理批（GLB-40/41）。

- GLB-40：设备管理页 7 列在 1093px 视口横向滚动 → 窄屏隐藏低价值时间列
- GLB-41：device_registry.credential 存明文全量凭证 → 瘦身为 sign+元数据
  （handshake 验签用公钥、一致性只比对 sign，新旧形态天然兼容）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from test_device_auth import _admin_client, _handshake, _make_app

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'templates', 'device', 'list.html')


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch, admission=True)


class TestDeviceListResponsive:
    def test_time_columns_hidden_below_xl(self):
        """GLB-40：首次登记/最近握手两列 ≥1200px 才显示（1093px 视口隐藏，消滚动条）。"""
        src = open(TEMPLATE, encoding='utf-8').read()
        assert src.count('d-none d-xl-table-cell') >= 4      # thead 2 列 + JS 2 个 td


class TestSlimCredential:
    def test_reissue_stores_sign_only(self, app):
        """GLB-41：重签发后库内 credential 只存 sign+元数据（不含 label 等全量字段）。"""
        c = app.test_client()
        _handshake(c)
        r = _admin_client(app).post('/device-mgmt/api/1/reissue')
        new_cred = r.get_json()['data']['credential']        # 返回给管理员的全量凭证
        assert new_cred['label'] is not None or 'label' in new_cred
        from models import db, DeviceRegistry
        with app.app_context():
            stored = json.loads(db.session.get(DeviceRegistry, 1).credential)
        assert set(stored) <= {'sign', 'serial', 'issued', 'expire'}
        assert stored['sign'] == new_cred['sign']

    def test_first_handshake_stores_slim(self, app):
        c = app.test_client()
        full = _handshake(c).get_json()['data']['credential']
        from models import db, DeviceRegistry
        with app.app_context():
            stored = json.loads(db.session.get(DeviceRegistry, 1).credential)
        assert set(stored) <= {'sign', 'serial', 'issued', 'expire'}

    def test_slim_still_verifies_end_to_end(self, app):
        """瘦身形态与全量回传凭证的握手链路等价（sign 一致性比对不变）。"""
        c = app.test_client()
        cred = _handshake(c).get_json()['data']['credential']
        _admin_client(app).post('/device-mgmt/api/1/approve')
        r = _handshake(c, credential=cred)
        assert r.get_json()['data']['status'] == 'approved'
