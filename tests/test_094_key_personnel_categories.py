# -*- coding: utf-8 -*-
"""tests/test_094_key_personnel_categories.py — 重点人员台账（2026-11-16 收缩后）。

历史沿革：本文件原覆盖 094 B-9「社区矫正 / 刑满释放安置帮教 / 社区戒毒康复」三类配置驱动台账。
2026-11-16 主理人派单把「社区矫正」「社区戒毒康复」整类下线（入口 / 路由 / 数据 / 代码一并移除），
并把「刑满释放帮教」改名「刑事人员」（category 键 resettlement → criminal）：
服刑人员与刑满释放人员同表存放，由「人员状态」字段（字典「刑事人员状态」）区分，
页面工具栏用同名字段筛选。故本文件改为覆盖：

  1) 配置形状：criminal 字段 16 个、personnel_status 必填且走字典、filters 声明同源；
  2) 页面：标题 / 录入弹窗人员状态下拉 / 工具栏人员状态筛选；
  3) 流程：两类人员各存一条 → 列表 2 条 → 筛选只回 1 条 → 导出同口径过滤；
  4) 反例：非法筛选值返回空集；人员状态缺失 / 非法值保存失败且不写库；
  5) 下线守门：社区矫正、社区戒毒康复、旧 resettlement 三个入口 404 且配置不存在；
  6) 字典单一真源：刑事人员状态 = 服刑人员 / 刑满释放人员，旧三类字典不再存在；
  7) 未登录 302 / API 401。

数据全部为假值（张三 / 李四），不含真实居民信息。
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CATEGORY = 'criminal'
TITLE = '刑事人员台账'
RETIRED_CATEGORIES = ('community-correction', 'drug-rehab', 'resettlement')
STATUS_DICT = '刑事人员状态'
IN_PRISON = '服刑人员'
RELEASED = '刑满释放人员'

ID_CARD = '110101199001011234'

# 三要素字段键（094 Spec「三要素完整」在本类别继续成立）
THREE_ELEMENT_KEYS = [
    'name', 'id_card', 'village_group', 'phone', 'address',
    'record_date', 'responsible_person', 'visit_situation', 'help_measures',
    'control_status', 'control_measures', 'remark',
]
# 刑事人员专属字段
CRIMINAL_KEYS = ['personnel_status', 'release_date', 'help_type', 'original_sentence']

# 下拉字段 → 字典分类（option_category）
OPTION_FIELDS = {'personnel_status': STATUS_DICT, 'help_type': '帮教类型',
                 'control_status': '帮教管控状态'}

DICT_EXPECT = {
    '重点人员类别': [IN_PRISON, RELEASED],
    STATUS_DICT: [IN_PRISON, RELEASED],
    '帮教类型': ['刑满释放', '假释', '暂予监外执行', '其他'],
    '帮教管控状态': ['正常', '重点帮教', '失联', '已解除'],
}
RETIRED_DICTS = ('矫正类别', '戒毒阶段', '矫正管控状态', '戒毒管控状态')


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / "shucun.db")
    for k, v in {
        "SHUCUN_DB_PATH": db_path,
        "SHUCUN_TEMP_DIR": str(tmp_path / "temp"),
        "SHUCUN_UPLOAD_DIR": str(tmp_path / "uploads"),
        "SHUCUN_BACKUP_DIR": str(tmp_path / "backups"),
        "SHUCUN_DATA_DIR": str(tmp_path / "data"),
        "SHUCUN_LAN_AUTHORIZED": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path / "temp"))
    monkeypatch.setattr(config.Config, "SQLALCHEMY_DATABASE_URI",
                        f"sqlite:///{db_path}?check_same_thread=False")
    monkeypatch.setattr(config.Config, "IMPORTS_DIR", str(tmp_path / "temp" / "imports"))
    from app import create_app
    application = create_app()
    application.config["TESTING"] = True
    application.config["TEST_DB_PATH"] = db_path
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    return _make_app(tmp_path, monkeypatch)


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令123', salt=generate_salt())


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1  # admin 全权限
    return client


def _payload(status=IN_PRISON, name='张三', **overrides):
    """刑事人员完整载荷（枚举值与 dict_seed 对齐）。"""
    data = {
        'personnel_status': status,
        'name': name,
        'id_card': ID_CARD,
        'village_group': '一组',
        'phone': '13812345678',
        'address': '三合村一组12号',
        'record_date': '2026-11-01',
        'responsible_person': '李四',
        'visit_situation': '走访情况正常',
        'help_measures': '申请临时救助',
        'control_measures': '每月见面核查',
        'remark': '测试备注',
        'release_date': '2026-10-01' if status == RELEASED else '',
        'help_type': '刑满释放' if status == RELEASED else '',
        'original_sentence': '三年',
        'control_status': '正常',
    }
    data.update(overrides)
    return data


ROSTER = '/governance/roster/gov_criminal'


def _list(client, **params):
    """V2 列表：字段筛选参数带 f_ 前缀，关键字走单行通用搜索 q。"""
    query = {'limit': 100}
    for k, v in params.items():
        if k == 'keyword':
            query['q'] = v
        else:
            query['f_' + k] = v
    return client.get(ROSTER + '/api/list', query_string=query).get_json()


def _save(client, payload):
    """V2 新增一行（整行保存口径）。"""
    return client.post(ROSTER + '/api/record', json={'values': payload}).get_json()


# ══════════ 1) 配置形状 ══════════

def test_criminal_config_shape():
    """字段 16 个、人员状态必填且走字典、filters 与字段共用同一字典分类。"""
    from routes.governance import CATEGORY_CONFIG

    cfg = CATEGORY_CONFIG[CATEGORY]
    assert cfg['title'] == TITLE
    keys = [field['key'] for field in cfg['fields']]
    assert len(keys) == 16, f'字段数应为 16，实际 {len(keys)}：{keys}'
    for key in THREE_ELEMENT_KEYS + CRIMINAL_KEYS:
        assert key in keys, f'缺字段 {key}'
    assert 'personnel_status' in cfg['columns'], '人员状态应进列表列'

    status_field = next(f for f in cfg['fields'] if f['key'] == 'personnel_status')
    assert status_field['required'] is True, '人员状态必须必填（两类人员靠它区分）'
    assert status_field['option_category'] == STATUS_DICT
    assert status_field['options'] == [], '配置层不得硬编码枚举'

    assert cfg['filters'] == [{'key': 'personnel_status', 'label': '人员状态',
                               'option_category': STATUS_DICT}]

    # 已下线类别不得残留在白名单
    from routes.governance import CATEGORY_CONFIG as all_cfg
    for retired in RETIRED_CATEGORIES:
        assert retired not in all_cfg, f'已下线类别仍在白名单：{retired}'


def test_criminal_option_fields_use_dict():
    """下拉字段全部走字典单一真源（不写死 options）。"""
    from routes.governance import CATEGORY_CONFIG

    got = {field['key']: field.get('option_category')
           for field in CATEGORY_CONFIG[CATEGORY]['fields']
           if field.get('option_category')}
    assert got == OPTION_FIELDS, f'枚举下拉声明不符：{got}'


# ══════════ 2) 页面（141 起为软表 V2 统一工作台） ══════════

def test_page_renders_status_field_and_filter(app):
    """V2 工作台 200：标题、人员状态列头与筛选控件、帮教专属列齐全（旧入口 302 跳转）。"""
    client = _admin_client(app)
    r = client.get('/governance/criminal/')
    assert r.status_code == 302 and (r.headers.get('Location') or '').endswith(ROSTER + '/')
    resp = client.get('/governance/criminal/', follow_redirects=True)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert TITLE in html
    assert 'id="dtV2A4Wrap"' in html, '未进入统一双视图工作台'

    thead = html[html.index('<table class="sc-table" id="recordTable">'):]
    thead = thead[:thead.index('</thead>')]
    for label in ('人员状态', '释放日期', '帮教类型'):
        assert label in thead, f'列表表头缺「{label}」'
    # 高级筛选区：人员状态为枚举下拉筛选，选项走字典
    assert 'name="f_personnel_status"' in html, '高级筛选缺人员状态'
    sel = html[html.index('name="f_personnel_status"'):]
    sel = sel[:sel.index('</select>')]
    assert IN_PRISON in sel and RELEASED in sel, '人员状态筛选缺字典选项'
    # 录入行表单：人员状态为必填下拉（required 属性位于开标签内）
    fi = html.index('id="f_personnel_status"')
    tag = html[html.rfind('<select', 0, fi):html.index('>', fi)]
    assert 'required' in tag, '人员状态录入控件应 required'
    fsel = html[fi:]
    fsel = fsel[:fsel.index('</select>')]
    assert IN_PRISON in fsel and RELEASED in fsel


# ══════════ 3) 保存 / 列表 / 筛选 / 导出 ══════════

def test_two_statuses_filter_and_export(app):
    """两类人员同表：全部 2 条；筛选服刑人员 1 条；导出按同一筛选口径。"""
    import openpyxl

    _unlock_keyholder()
    client = _admin_client(app)

    assert _save(client, _payload())['success'] is True
    assert _save(client, _payload(status=RELEASED, name='李四',
                                  id_card='110101199002021234'))['success'] is True

    all_rows = _list(client)
    assert all_rows['total'] == 2, '全部人员状态应回 2 条'
    statuses = {row['name']: row['personnel_status'] for row in all_rows['data']}
    assert statuses == {'张三': IN_PRISON, '李四': RELEASED}, statuses

    only_prison = _list(client, personnel_status=IN_PRISON)
    assert only_prison['total'] == 1
    assert only_prison['data'][0]['name'] == '张三'
    assert only_prison['data'][0]['personnel_status'] == IN_PRISON

    only_released = _list(client, personnel_status=RELEASED)
    assert only_released['total'] == 1
    assert only_released['data'][0]['name'] == '李四'

    # 筛选 + 单行通用搜索可叠加（同一次请求）：刑满释放 + 姓名张三 → 0 条
    combined = _list(client, personnel_status=RELEASED, keyword='张三')
    assert combined['total'] == 0, '筛选与关键字应同时生效'
    combined = _list(client, personnel_status=RELEASED, keyword='李四')
    assert combined['total'] == 1, '刑满释放 + 李四应命中 1 条'

    export = client.get(ROSTER + '/api/export',
                        query_string={'f_personnel_status': RELEASED})
    assert export.status_code == 200
    assert 'spreadsheetml' in (export.content_type or '')
    wb = openpyxl.load_workbook(io.BytesIO(export.data))
    values = [str(cell.value) for row in wb.active.iter_rows() for cell in row if cell.value]
    assert '李四' in values, '导出未带筛选行'
    assert '张三' not in values, '导出忽略了人员状态筛选'


def test_invalid_filter_value_returns_empty(app):
    """非法筛选值 → 空集，且不报错、不落到全表（防止筛选被绕过）。"""
    _unlock_keyholder()
    client = _admin_client(app)
    assert _save(client, _payload())['success'] is True

    bogus = _list(client, personnel_status='不属于刑事人员状态的值')
    assert bogus['success'] is True
    assert bogus['total'] == 0, '非法筛选值不得回落到全表'
    assert bogus['data'] == []


# ══════════ 4) 反例：必填 / 非法枚举 ══════════

def test_personnel_status_required(app):
    """人员状态缺失 → 保存失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    payload = _payload()
    payload.pop('personnel_status')
    resp = _save(client, payload)
    assert resp['success'] is False
    assert '人员状态' in resp['msg'] and '必填' in resp['msg'], resp['msg']
    assert _list(client)['total'] == 0


def test_invalid_personnel_status_rejected(app):
    """人员状态非字典值 → 保存失败且不写库。"""
    _unlock_keyholder()
    client = _admin_client(app)
    resp = _save(client, _payload(status='乱填状态'))
    assert resp['success'] is False
    assert '人员状态' in resp['msg'], resp['msg']
    assert _list(client)['total'] == 0


# ══════════ 5) 下线守门 ══════════

@pytest.mark.parametrize('retired', RETIRED_CATEGORIES)
def test_retired_category_pages_404(app, retired):
    """三类下线入口（社区矫正 / 社区戒毒康复 / 旧 resettlement）页面 404、旧 API 不可达。"""
    client = _admin_client(app)
    assert client.get(f'/governance/{retired}/').status_code == 404, \
        f'{retired} 页面仍可访问'
    # 141（C-2）：旧 CRUD 路由已整体移除，下线类别的旧 API 不得可达（更不得导出任何数据）
    for legacy_api in (f'/governance/{retired}/api/list', f'/governance/{retired}/api/export',
                       f'/governance/{retired}/api/save'):
        resp = client.open(legacy_api, method='POST' if 'save' in legacy_api else 'GET')
        assert resp.status_code in (404, 405), f'{legacy_api} 旧路径仍可达'


def test_retired_dashboard_entries_absent(app):
    """侧栏不再出现社区矫正 / 社区戒毒康复 / 刑满释放帮教，且新增「走访关爱」一级入口。"""
    client = _admin_client(app)
    page = client.get('/dashboard/').get_data(as_text=True)
    for text in ('社区矫正', '社区戒毒康复', '刑满释放帮教'):
        assert text not in page, f'侧栏仍渲染已下线入口：{text}'
    assert '走访关爱' in page, '新一级入口「走访关爱」未渲染'
    assert '综治管理' in page, '综治管理一级入口不得丢失'


# ══════════ 6) 字典单一真源 ══════════
def test_dict_seeds_single_source(app):
    """DICT_SEEDS 与运行期字典一致；旧三类字典不再存在。"""
    from utils.dict_enum import get_enum
    from utils.dict_seed import DICT_SEEDS

    for name, labels in DICT_EXPECT.items():
        assert DICT_SEEDS.get(name) == labels, f'{name} 字面量不符：{DICT_SEEDS.get(name)}'
    for retired in RETIRED_DICTS:
        assert retired not in DICT_SEEDS, f'已下线字典仍被播种：{retired}'

    with app.app_context():
        assert list(get_enum(STATUS_DICT)) == DICT_EXPECT[STATUS_DICT]
        for retired in RETIRED_DICTS:
            assert list(get_enum(retired)) == [], f'字典表仍残留 {retired}'


# ══════════ 7) 存量库迁移 m044 ══════════

def test_m044_retire_migration_idempotent(app):
    """m044：归档 → 删两类 → resettlement 改名 criminal → 回填人员状态 → 字典收敛；二跑零变更。"""
    import importlib.util
    import json as jsonlib
    import sqlite3

    db_path = app.config['TEST_DB_PATH']
    stamp = '2026-11-01 10:00:00'
    conn = sqlite3.connect(db_path)
    # 141（C-2）：旧模型已删，新库不再自动建 governance_ledgers；需要跑 m044 的只会是仍带
    # 旧表的老库（如备份恢复），这里显式按旧结构建表，保持本迁移守卫有效。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS governance_ledgers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category VARCHAR(40) NOT NULL,
            subtype VARCHAR(40) DEFAULT '',
            name VARCHAR(50) DEFAULT '',
            id_card VARCHAR(18), id_card_enc TEXT, id_card_hash VARCHAR(64),
            phone VARCHAR(120) DEFAULT '', record_date DATE,
            payload TEXT DEFAULT '{}', created_by VARCHAR(50) DEFAULT '',
            created_at DATETIME, updated_at DATETIME)
    """)
    conn.executemany(
        "INSERT INTO governance_ledgers (category, name, payload, record_date, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [('community-correction', '矫正甲', '{}', '2026-11-01', 'admin', stamp),
         ('drug-rehab', '戒毒甲', '{}', '2026-11-01', 'admin', stamp),
         ('resettlement', '帮教甲', '{"name": "王五"}', '2026-11-01', 'admin', stamp)],
    )
    conn.commit()
    conn.close()

    spec = importlib.util.spec_from_file_location(
        'm044_governance_retire',
        os.path.join(ROOT, 'scripts', 'migrations', 'm044_governance_retire.py'))
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    first = migration.run(db_path)
    assert first['rows_removed'] == 2, first
    assert first['rows_renamed'] == 1, first
    assert first['rows_backfilled'] == 1, first
    assert first['archived'] and os.path.exists(first['archived']), '下线数据必须先归档'
    archived = jsonlib.load(open(first['archived'], encoding='utf-8'))
    assert archived['row_count'] == 2
    assert {row['category'] for row in archived['rows']} == {'community-correction', 'drug-rehab'}

    conn = sqlite3.connect(db_path)
    categories = [row[0] for row in conn.execute(
        "SELECT category FROM governance_ledgers ORDER BY id")]
    assert categories == ['criminal'], '历史 resettlement 行应改名为 criminal'
    payload = jsonlib.loads(conn.execute(
        "SELECT payload FROM governance_ledgers").fetchone()[0])
    assert payload['personnel_status'] == RELEASED, '历史行应回填为刑满释放人员'
    assert payload['name'] == '王五', '回填不得丢失原有字段'
    assert conn.execute(
        "SELECT COUNT(*) FROM dict_categories WHERE name IN "
        "('矫正类别','戒毒阶段','矫正管控状态','戒毒管控状态')").fetchone()[0] == 0
    conn.close()

    second = migration.run(db_path)
    assert second['rows_removed'] == 0 and second['rows_renamed'] == 0, second
    assert second['rows_backfilled'] == 0, second
    assert second['dict_categories_removed'] == 0, second
    assert second['dict_items_added'] == 0, second
    assert second['archived'] == '', '无待删行时不应再写归档文件'


# ══════════ 8) 登录 ══════════

def test_requires_login(app):
    """未登录：页面 302 跳登录、API 401。"""
    anonymous = app.test_client()
    page = anonymous.get('/governance/criminal/')
    assert page.status_code == 302
    assert 'login' in (page.headers.get('Location') or '').lower()

    api = anonymous.get('/governance/criminal/api/list',
                        headers={'X-Requested-With': 'XMLHttpRequest'})
    assert api.status_code == 401
    assert api.get_json()['success'] is False
