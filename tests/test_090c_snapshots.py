# -*- coding: utf-8 -*-
"""tests/test_090c_snapshots.py — 090C 环卫工作新增页 SSR 渲染快照（test_088/099 同款）。

/sanitation/ 由 test_template_smoke 的无参 GET 扫描自动收录（sanitation/index.html）；
/sanitation/roster/<code>/ 带参被跳过，本文件把三态四页纳入快照门禁，
复用 test_template_smoke 的归一化实现：
  首跑 → 生成 tests/snapshots/sanitation_*.html（须提交入库）；
  次跑 → 与基线逐字节对比，任一差异即 fail。

归一化覆盖：CSRF token、日期/时间、tmp 绝对路径（同 test_template_smoke._normalize）。
快照取样：队伍册（identity 列 + 未建引导 + 上传页）与整治册（enum/date/number 列），
覆盖两类字段形态；点位册为纯 text/number 子集，不另设快照。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

# 归一化与快照目录复用既有实现，保证四页与全站其它页同一口径
from test_template_smoke import SNAPSHOT_DIR, _normalize

TEAM_CODE = 'sanitation_team'
CLEANUP_CODE = 'sanitation_cleanup'
ROUTE_TEAM = '/sanitation/roster/%s/' % TEAM_CODE
ROUTE_TEAM_UPLOAD = '/sanitation/roster/%s/upload' % TEAM_CODE
ROUTE_CLEANUP = '/sanitation/roster/%s/' % CLEANUP_CODE

# 台账页固定字段规格（快照需确定性结构：顺序/类型/必填/敏感级全部显式，一页纸 v1.2 §1/§3）
TEAM_FIELDS = [
    {'code': 'f00', 'display_name': '队伍名称', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f01', 'display_name': '负责人', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f02', 'display_name': '联系电话', 'data_type': 'text',
     'sensitivity': 'identity', 'is_required': False},
    {'code': 'f03', 'display_name': '在岗人数', 'data_type': 'number',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f04', 'display_name': '负责片区', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f05', 'display_name': '备注', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
]
CLEANUP_FIELDS = [
    {'code': 'f00', 'display_name': '整治日期', 'data_type': 'date',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f01', 'display_name': '整治地点/片区', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f02', 'display_name': '整治类型', 'data_type': 'enum',
     'dict_category_code': '整治记录类型', 'sensitivity': 'none', 'is_required': True},
    {'code': 'f03', 'display_name': '参与人数', 'data_type': 'number',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f04', 'display_name': '整治内容', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': True},
    {'code': 'f05', 'display_name': '照片', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
    {'code': 'f06', 'display_name': '记录人', 'data_type': 'text',
     'sensitivity': 'none', 'is_required': False},
]


def _prepare(app):
    """补种快照所需的确定性数据：密钥 + m031 预置行
    （「整治记录类型」字典已由 create_app 的 ensure_enum_dicts 守卫种齐）。"""
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('sanitation-snapshot-test', salt=generate_salt())

    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'scripts', 'migrations', 'm031_roster_metadata.py')
    spec = importlib.util.spec_from_file_location('m031_for_090c_snapshots', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert len(mod.run(os.environ['SHUCUN_DB_PATH'])['inserted']) == 7


def _login(app):
    client = app.test_client()
    with app.app_context():
        from models import User
        admin = User.query.filter_by(username='admin').first()
    with client.session_transaction() as sess:
        sess['user_id'] = admin.id
        sess['username'] = admin.username
    return client


def _check_snapshot(client, route, name, tmp):
    """GET route → 归一化 → 与 tests/snapshots/<name> 对比（缺则生成）。"""
    resp = client.get(route)
    assert resp.status_code == 200, '%s 渲染失败：%s' % (route, resp.status_code)
    assert 'text/html' in (resp.content_type or ''), '%s 非 HTML' % route
    norm = _normalize(resp.get_data(as_text=True), tmp)
    snap = os.path.join(SNAPSHOT_DIR, name)
    if not os.path.exists(snap):
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        with open(snap, 'w', encoding='utf-8', newline='\n') as f:
            f.write(norm)
        pytest.skip('首跑生成基线 %s，提交入库后重跑进入对比模式' % name)
    with open(snap, encoding='utf-8') as f:
        baseline = f.read()
    # 换行符不参与 diff：仓库 checkout 可能按平台转为 CRLF，比较前统一归一为 LF
    assert baseline.replace('\r\n', '\n') == norm.replace('\r\n', '\n'), (
        '%s 渲染与基线 %s 不一致。若为有意改动：删除快照后重跑重建基线；'
        '若为无意回归：先还原模板。' % (route, name)
    )


@pytest.fixture()
def app_preset(isolated_app_factory):
    """未建表态（m031 预置空行）→ 引导页 / 上传页快照。"""
    application = isolated_app_factory()
    _prepare(application)
    return application


@pytest.fixture()
def app_built_team(isolated_app_factory):
    """已建表态（队伍册字段行落库）→ 台账页快照。"""
    application = isolated_app_factory()
    _prepare(application)
    with application.app_context():
        from utils import roster_facade as facade
        table = facade.get_table('sanitation', TEAM_CODE)
        facade.create_table('sanitation', TEAM_CODE, table.name, table.group_name,
                            TEAM_FIELDS, created_by='snapshot')
    return application


@pytest.fixture()
def app_built_cleanup(isolated_app_factory):
    """已建表态（整治册字段行落库）→ 台账页快照（enum/date/number 形态）。"""
    application = isolated_app_factory()
    _prepare(application)
    with application.app_context():
        from utils import roster_facade as facade
        table = facade.get_table('sanitation', CLEANUP_CODE)
        facade.create_table('sanitation', CLEANUP_CODE, table.name, table.group_name,
                            CLEANUP_FIELDS, created_by='snapshot')
    return application


def test_sanitation_team_bootstrap_snapshot(tmp_path, app_preset):
    """未建表引导页：200 + 流程说明 + 上传入口（roster_prefix=/sanitation）。"""
    _check_snapshot(_login(app_preset), ROUTE_TEAM,
                    'sanitation_team_bootstrap.html', str(tmp_path))


def test_sanitation_team_upload_snapshot(tmp_path, app_preset):
    """上传与字段确认页（独立入口，POST 走 sanitation.add、GET 走 sanitation.view）。"""
    _check_snapshot(_login(app_preset), ROUTE_TEAM_UPLOAD,
                    'sanitation_team_upload.html', str(tmp_path))


def test_sanitation_team_page_snapshot(tmp_path, app_built_team):
    """已建表台账页：表头按一页纸 §1 渲染（identity 加密徽章 / number 区间）。"""
    _check_snapshot(_login(app_built_team), ROUTE_TEAM,
                    'sanitation_team_page.html', str(tmp_path))


def test_sanitation_cleanup_page_snapshot(tmp_path, app_built_cleanup):
    """已建表台账页：表头按一页纸 §3 渲染（enum 下拉 label / date / number 区间）。"""
    _check_snapshot(_login(app_built_cleanup), ROUTE_CLEANUP,
                    'sanitation_cleanup_page.html', str(tmp_path))
