# -*- coding: utf-8 -*-
"""064 顶栏同步状态：provider 契约 + 降级 + 模板渲染 + 裁撤守卫。"""
import pytest

import utils.sync_status as sync_mod


def test_local_status_contract():
    """单机 provider 返回事实状态，不含假时间。"""
    st = sync_mod.current_sync_status()
    assert set(st) == {'mode', 'state', 'tone', 'text', 'title'}
    assert st['mode'] == 'local'
    assert st['state'] == 'local'
    assert st['tone'] == 'muted'
    assert st['text'] == '本机数据'
    assert '单机' in st['title']


def test_provider_failure_degrades_to_unknown(monkeypatch):
    """provider 异常不得拖垮页面，统一降级 unknown。"""
    def _boom():
        raise RuntimeError('sync unavailable')

    monkeypatch.setattr(sync_mod, '_provider', _boom)
    st = sync_mod.current_sync_status()
    assert st['state'] == 'unknown'
    assert st['tone'] == 'muted'
    assert st['text'] == '同步状态未知'


@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def test_topbar_renders_sync_status(app):
    """壳层真实渲染：同步状态控件 + 灰点 + 本机数据文案。"""
    html = _admin_client(app).get('/dashboard/').get_data(as_text=True)
    assert 'id="syncStatus"' in html
    assert '本机数据' in html
    assert 'sc-dot--muted' in html
    assert '单机模式' in html


def test_cut_controls_not_rendered(app):
    """064 裁撤项不得在壳层出现任何残留控件。"""
    html = _admin_client(app).get('/dashboard/').get_data(as_text=True)
    for dead in ('sc-topbar__village', 'sc-topbar__notify', 'sc-topbar__theme'):
        assert dead not in html
