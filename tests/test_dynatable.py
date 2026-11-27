# -*- coding: utf-8 -*-
"""tests/test_dynatable.py — 078 软表引擎（动态台账）

覆盖（对应 docs/tasks/078-softtable-engine.md 验收标准）：
1. 零物理 DDL：引擎与路由源码不出现 CREATE/ALTER/DROP TABLE（断言扫描）
2. 类型推断 5 条启发式（4 条 Spike 坑 + 缴费期号误判 amount 的敏感识别修正）
3. 建表强制确认：normalize_fields 拒绝 enum 无字典分类 / 非法标识
4. 敏感分级：identity 密文无明文、HMAC 指纹等值命中、跨字段 domain separation、打码
5. 内存筛选排序：枚举等值 / 金额区间 / 排序
6. 权限归类：ADR-016 三选一（未登录 302、漏归 403、admin 200）
7. 结构断言：确认页 / 列表页 tab 与嵌套 form（HTMLParser）
"""
import json

import pytest

from utils import softtable_engine as eng


# ------------------------------------------------------------------ 2. 类型推断

@pytest.mark.parametrize('header,values,expect', [
    # 坑 1：18 位证件号曾误判 number
    ('证件号码', ['110101199001011234', '110101199001011235'], 'text'),
    # 坑 2：姓名去重少曾误判 enum
    ('姓　名', ['张三', '李四', '王五'], 'text'),
    # 坑 3：字母+数字编号曾误判 enum
    ('人员编号', ['RY001', 'RY002', 'RY003'], 'text'),
    # 坑 4：6 位年月期号曾误判 number
    ('最大做账期号', ['202610', '202609'], 'text'),
    # 正常金额列仍应识别为数字
    ('当期缴费基数', ['8000', '6500.5'], 'number'),
    # 低基数文本列 → 枚举建议
    ('参保状态', ['正常参保', '暂停参保', '正常参保'], 'enum'),
    # 日期列
    ('本次参保时间', ['2024-01-05', '2023/12/31'], 'date'),
])
def test_infer_column_heuristics(header, values, expect):
    """验收 2：4 条启发式修正 + 正常识别不被误伤。"""
    dtype, hint = eng.infer_column(header, values)
    assert dtype == expect, '字段 %s 推断为 %s（期望 %s）' % (header, dtype, expect)
    if header in ('证件号码', '姓　名', '人员编号', '最大做账期号'):
        assert hint, '修正类字段必须给出可见提示（%s）' % header


def test_infer_sensitivity_excludes_period_no():
    """期号/编号类字段不得被判为 amount（养老样本复跑实测坑）。"""
    assert eng.infer_sensitivity('证件号码') == 'identity'
    assert eng.infer_sensitivity('身份证号') == 'identity'
    assert eng.infer_sensitivity('当期缴费基数') == 'amount'
    assert eng.infer_sensitivity('缴费金额') == 'amount'
    assert eng.infer_sensitivity('缴费期号') == 'none'
    assert eng.infer_sensitivity('人员编号') == 'none'
    assert eng.infer_sensitivity('姓名') == 'none'


def test_suggest_fields_codes_and_hints():
    headers = ['姓名', '证件号码', '参保状态']
    rows = [['张三', '110101199001011234', '正常参保'],
            ['李四', '110101199001011235', '暂停参保']]
    out = eng.suggest_fields(headers, rows)
    assert [f['code'] for f in out] == ['f00', 'f01', 'f02']
    assert [f['sort_order'] for f in out] == [0, 1, 2]
    assert out[0]['display_name'] == '姓名'
    assert out[1]['sensitivity'] == 'identity'
    assert out[2]['data_type'] == 'enum'
    assert out[2]['hint'], '枚举建议必须提示需选择字典分类'


# ------------------------------------------------------------------ 3. 字段规格

def test_normalize_fields_rejects_enum_without_category():
    """约束：enum 存 dict_category_code 引用，不收自由文本 → 无分类直接拒。"""
    with pytest.raises(eng.SoftTableError, match='字典分类'):
        eng.normalize_fields([{'code': 'f00', 'display_name': '参保状态',
                               'data_type': 'enum', 'dict_category_code': None}])


@pytest.mark.parametrize('bad', [
    {'code': 'F00', 'display_name': 'x'},
    {'code': 'f 00', 'display_name': 'x'},
    {'code': 'f00', 'display_name': ''},
    {'code': 'f00', 'display_name': 'x', 'data_type': 'blob'},
    {'code': 'f00', 'display_name': 'x', 'sensitivity': 'secret'},
])
def test_normalize_fields_rejects_bad_spec(bad):
    with pytest.raises(eng.SoftTableError):
        eng.normalize_fields([bad])


def test_normalize_fields_assigns_sort_order():
    out = eng.normalize_fields([
        {'code': 'f01', 'display_name': 'B', 'data_type': 'text', 'sort_order': 9},
        {'code': 'f00', 'display_name': 'A', 'data_type': 'text'},
    ])
    assert [f['sort_order'] for f in out] == [0, 1]


def test_validate_table_code():
    assert eng.validate_table_code('pension_roster') == 'pension_roster'
    for bad in ('Pension', '1abc', 'a-b', '', 'x' * 61):
        with pytest.raises(eng.SoftTableError):
            eng.validate_table_code(bad)


# ------------------------------------------------------------------ 4. 加密/指纹

def _specs():
    return eng.normalize_fields([
        {'code': 'f00', 'display_name': '姓名', 'data_type': 'text'},
        {'code': 'f01', 'display_name': '证件号码', 'data_type': 'text',
         'sensitivity': 'identity'},
        {'code': 'f02', 'display_name': '当期缴费基数', 'data_type': 'number',
         'sensitivity': 'amount'},
    ])


_TEST_HMAC = eng.derive_hmac_key(b'test-key-bytes-32-for-hmac-0001')


def _fake_encrypt(token='enc::'):
    """可逆假加密：仅用于断言「写入的是密文、且不含明文」。"""
    return lambda s: token + s[::-1]


def test_build_records_encrypts_identity_only():
    specs = _specs()
    rows = [['张三', '110101199001011234', 8000]]
    records, errors = eng.build_records(specs, rows, encrypt=_fake_encrypt(), hmac_key=_TEST_HMAC)
    assert not errors
    data = json.loads(records[0]['data'])
    digest = json.loads(records[0]['digest'])
    assert '110101199001011234' not in records[0]['data'], '明文不得出现在 data'
    assert data['f01'].startswith('enc::'), 'identity 字段必须是密文'
    assert data['f00'] == '张三' and data['f02'] == 8000, '非 identity 字段保持明文'
    assert set(digest) == {'f01'}, '只有 identity 字段写指纹'
    assert len(digest['f01']) == 64, 'HMAC-SHA256 十六进制 64 位'


def test_identity_digest_domain_separation():
    """同一证件号在两个字段下的指纹必须不同（防跨字段频率关联）。"""
    key = eng.derive_hmac_key(b'test-key-bytes-32-for-hmac-0001')
    a = eng.identity_digest(key, 'f01', '110101199001011234')
    b = eng.identity_digest(key, 'f09', '110101199001011234')
    assert a != b
    # 归一化：空格/大小写不影响指纹
    assert a == eng.identity_digest(key, 'f01', ' 110101199001011234 ')


def test_build_records_collects_row_errors():
    specs = _specs()
    rows = [['张三', '110101199001011234', '不是数字']]
    records, errors = eng.build_records(specs, rows, encrypt=_fake_encrypt(), hmac_key=_TEST_HMAC)
    assert records == []
    assert errors and errors[0]['row'] == 1 and '数字' in errors[0]['msg']


def test_required_field_blocks_row():
    specs = eng.normalize_fields([{'code': 'f00', 'display_name': '姓名',
                                   'data_type': 'text', 'is_required': True}])
    records, errors = eng.build_records(specs, [['']], encrypt=_fake_encrypt(), hmac_key=_TEST_HMAC)
    assert records == [] and '必填' in errors[0]['msg']


def test_mask_identity_keeps_edges():
    masked = eng.mask_identity('110101199001011234')
    assert masked.startswith('110') and masked.endswith('1234')
    assert '*' in masked and '11010119900101' not in masked
    assert eng.mask_identity('1234567') == '*' * 7


def test_decode_records_masks_by_default():
    class Rec(object):
        id = 1
        data = json.dumps({'f00': '张三', 'f01': '4321101099110101 1'[::-1]})
        digest = json.dumps({'f01': 'x'})
        created_by = 'admin'
        created_at = None
        updated_at = None

    specs = _specs()
    dec = lambda s: '110101199001011234'
    masked = eng.decode_records(specs, [Rec()], decrypt=dec)[0]
    assert masked['f00'] == '张三'
    assert '*' in masked['f01'] and '110101199001011234' not in masked['f01']
    revealed = eng.decode_records(specs, [Rec()], reveal=True, decrypt=dec)[0]
    assert revealed['f01'] == '110101199001011234'


# ------------------------------------------------------------------ 5. 内存筛选/排序

def _rows():
    return [
        {'id': 1, 'f00': '张三', 'f02': 8000, '__digest__': {'f01': 'AAA'}},
        {'id': 2, 'f00': '李四', 'f02': 6500, '__digest__': {'f01': 'BBB'}},
        {'id': 3, 'f00': '张三', 'f02': 12000, '__digest__': {'f01': 'AAA'}},
    ]


def test_select_rows_text_contains_and_sort():
    specs = _specs()
    hit = eng.select_rows(_rows(), specs, {'f00': '张三'})
    assert [r['id'] for r in hit] == [1, 3]
    desc = eng.select_rows(_rows(), specs, sort_field='f02', sort_dir='desc')
    assert [r['id'] for r in desc] == [3, 1, 2]


def test_select_rows_amount_range():
    specs = _specs()
    out = eng.select_rows(_rows(), specs, {'f02': {'min': 7000, 'max': 9000}})
    assert [r['id'] for r in out] == [1]


def test_select_rows_blank_range_is_not_a_constraint():
    """137 修复：前后界都为空（列表页每次请求都带 f_*_min=&f_*_max=）不构成约束。

    此前空 dict 绕过 `cond in (None, '')` 短路进入 _in_range，把「该数值字段为空」的行
    一律判 False —— 表现为有数据却「共 0/1 条」。单边区间语义不变：空值行不匹配。
    """
    specs = _specs()
    rows = _rows() + [{'id': 4, 'f00': '王五', 'f02': '', '__digest__': {}}]
    blank = eng.select_rows(rows, specs, {'f02': {'min': '', 'max': ''}})
    assert [r['id'] for r in blank] == [1, 2, 3, 4], blank
    lo_only = eng.select_rows(rows, specs, {'f02': {'min': 7000}})
    assert [r['id'] for r in lo_only] == [1, 3], lo_only


def test_select_rows_identity_uses_digest():
    specs = _specs()
    out = eng.select_rows(_rows(), specs, {'f01': '110101199001011234'},
                          digest_fn=lambda code, v: 'AAA' if v else '')
    assert [r['id'] for r in hit_ids(out)] == [1, 3]


def hit_ids(rows):
    return rows


def test_select_rows_identity_cannot_sort():
    specs = _specs()
    out = eng.select_rows(_rows(), specs, sort_field='f01', sort_dir='desc')
    assert [r['id'] for r in out] == [1, 2, 3], 'identity 字段不得参与排序（密码学约束）'


# ------------------------------------------------------------------ 1. 零物理 DDL

def test_no_physical_ddl_in_engine_and_routes():
    """验收 1：业务台账零物理 DDL —— 引擎与蓝图源码不得出现建表/改表语句。"""
    import os
    import tokenize
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = [os.path.join(root, 'utils', 'softtable_engine.py'),
               os.path.join(root, 'routes', 'dynatable.py')]
    banned = ('CREATE TABLE', 'ALTER TABLE', 'DROP TABLE')
    for path in targets:
        if not os.path.exists(path):
            continue
        with open(path, 'rb') as fh:
            tokens = list(tokenize.tokenize(fh.readline))
        # 剔除注释/字符串（docstring 会提到「禁止 CREATE TABLE」），只留真实代码
        code = ' '.join(
            t.string for t in tokens
            if t.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.NL) and t.string
        ).upper()
        code = ' '.join(code.split())
        for word in banned:
            assert word not in code, '%s 真实代码出现 %s（违反零物理 DDL）' % (path, word)



# ================================================================== 路由层

import io as _io
import os as _os


def _make_app(tmp_path, monkeypatch):
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
    # config.py 首 import 即固化模块常量：必须同时改写模块属性，否则后续用例
    # 仍会用首个用例的临时目录（GLB-05 同源坑，test_template_smoke 已先例）。
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config, 'BACKUP_DIR', str(tmp_path / 'backups'))
    monkeypatch.setattr(config, 'TEMP_DIR', str(tmp_path / 'temp'))
    monkeypatch.setattr(config, 'UPLOAD_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setattr(config, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(config.Config, 'SQLALCHEMY_DATABASE_URI',
                        'sqlite:///%s?check_same_thread=False' % db_path)
    for sub in ('temp', 'uploads', 'backups', 'data', 'logs'):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / 'temp' / 'exports').mkdir(exist_ok=True)
    (tmp_path / 'temp' / 'imports').mkdir(exist_ok=True)
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    return application


@pytest.fixture()
def app(tmp_path, monkeypatch):
    application = _make_app(tmp_path, monkeypatch)
    from utils.crypto import generate_salt, key_holder
    key_holder.initialize('softtable-test-pass', salt=generate_salt())
    with application.app_context():
        from models import DictCategory, DictItem, db
        cat = DictCategory(name='参保状态', description='测试用')
        db.session.add(cat)
        db.session.flush()
        for i, label in enumerate(['正常参保', '暂停参保']):
            db.session.add(DictItem(category_id=cat.id, label=label, value=label,
                                    sort_order=i))
        db.session.commit()
    return application


@pytest.fixture()
def client(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = 1   # 首装种子 admin（role_id=1 全权）
        sess['username'] = 'admin'
    return c


SAMPLE_HEADERS = ['姓　名', '证件号码', '参保状态', '缴费期号', '当期缴费基数']
SAMPLE_ROWS = [
    ['张三', '110101199001011234', '正常参保', '202610', 8000],
    ['李四', '110101199001011235', '暂停参保', '202609', 6500],
    ['张三', '110101199001011234', '正常参保', '202608', 12000],
]


def _xlsx_bytes(headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _upload(client):
    data = {'file': (_xlsx_bytes(SAMPLE_HEADERS, SAMPLE_ROWS), 'sample.xlsx')}
    return client.post('/dynatable/api/upload', data=data,
                       content_type='multipart/form-data')


def _default_fields():
    return [
        {'code': 'f00', 'display_name': '姓名', 'data_type': 'text',
         'sensitivity': 'none', 'is_required': True},
        {'code': 'f01', 'display_name': '证件号码', 'data_type': 'text',
         'sensitivity': 'identity'},
        {'code': 'f02', 'display_name': '参保状态', 'data_type': 'enum',
         'dict_category_code': '参保状态'},
        {'code': 'f03', 'display_name': '缴费期号', 'data_type': 'text',
         'sensitivity': 'none'},
        {'code': 'f04', 'display_name': '当期缴费基数', 'data_type': 'number',
         'sensitivity': 'amount'},
    ]


def _create_table(client):
    resp = _upload(client)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['success'], body
    token = body['data']['token']
    payload = {'token': token, 'code': 'pension_roster', 'name': '养老保险参保台账',
               'module_key': 'pension', 'group_name': '社会保障',
               'description': '测试台账', 'fields': _default_fields()}
    created = client.post('/dynatable/api/create', json=payload)
    assert created.status_code == 200, created.get_data(as_text=True)
    return body['data'], created.get_json()


def test_upload_returns_suggestions(client):
    body = _upload(client).get_json()
    assert body['success'] and body['data']['row_count'] == 3
    sugg = body['data']['suggestions']
    assert [s['code'] for s in sugg] == ['f00', 'f01', 'f02', 'f03', 'f04']
    by_name = {s['display_name']: s for s in sugg}
    assert by_name['证件号码']['sensitivity'] == 'identity'
    assert by_name['当期缴费基数']['sensitivity'] == 'amount'
    assert by_name['缴费期号']['sensitivity'] == 'none', '期号不得判为金额（实测坑）'
    assert by_name['缴费期号']['data_type'] == 'text', '6 位年月期号默认文本'
    assert body['data']['sample'][0][0] == '张三'


def test_upload_rejects_bad_extension(client):
    """139：文案「只支持」已随 127/132 统一为「仅支持 …」，断言改绑单一真源常量。"""
    from utils.spreadsheet_reader import SPREADSHEET_ERR_MSG
    resp = client.post('/dynatable/api/upload',
                       data={'file': (_io.BytesIO(b'a,b'), 'x.txt')},
                       content_type='multipart/form-data')
    assert resp.status_code == 400
    assert resp.get_json()['msg'] == SPREADSHEET_ERR_MSG


def test_create_rejects_empty_fields(client):
    """建表必须带完整字段规格（强制人工确认，无字段 → 400，不落库）。"""
    body = _upload(client).get_json()
    resp = client.post('/dynatable/api/create',
                       json={'token': body['data']['token'], 'code': 'x_table',
                             'name': 'x', 'fields': []})
    assert resp.status_code == 400


def test_create_table_and_zero_physical_ddl(app, client):
    _data, created = _create_table(client)
    assert created['success'] and created['data']['imported'] == 3
    import sqlite3
    db_path = _os.environ['SHUCUN_DB_PATH']
    with app.app_context():
        from models import DynamicField, DynamicRecord, DynamicTable
        table = DynamicTable.query.filter_by(code='pension_roster').first()
        assert table and table.module_key == 'pension'
        assert DynamicField.query.filter_by(table_id=table.id).count() == 5
        assert DynamicRecord.query.filter_by(table_id=table.id).count() == 3
    con = sqlite3.connect(db_path)
    physical = {r[0] for r in con.execute(
        'select name from sqlite_master where type="table"')}
    con.close()
    assert 'dynamic_table' in physical and 'dynamic_field' in physical
    assert 'pension_roster' not in physical, '业务台账不得生成物理表（零物理 DDL）'


def test_records_list_masks_identity_and_filters(app, client):
    _create_table(client)
    body = client.get('/dynatable/api/t/pension_roster/records').get_json()
    assert body['success'] and body['total'] == 3
    first = body['data'][0]
    assert '*' in first['f01'] and '110101199001011234' not in str(first['f01'])
    assert '__digest__' not in first, '指纹不得回传前端'

    enumed = client.get('/dynatable/api/t/pension_roster/records?f_f02=正常参保').get_json()
    assert enumed['total'] == 2, '枚举等值筛选失效'

    ranged = client.get('/dynatable/api/t/pension_roster/records?f_f04_min=7000'
                        '&f_f04_max=9000').get_json()
    assert ranged['total'] == 1, '金额区间筛选失效'

    sorted_body = client.get('/dynatable/api/t/pension_roster/records?sort=f04&dir=desc').get_json()
    assert sorted_body['data'][0]['f04'] == 12000, '金额降序失效'

    dup = client.get('/dynatable/api/t/pension_roster/records?f_f01=110101199001011234').get_json()
    assert dup['total'] == 2, '同一证件号应命中 2 行（HMAC 指纹查重）'


def test_reveal_admin_only(app, client):
    _create_table(client)
    rid = client.get('/dynatable/api/t/pension_roster/records').get_json()['data'][0]['id']
    ok = client.post('/dynatable/api/t/pension_roster/record/%d/reveal' % rid)
    assert ok.status_code == 200
    assert '110101199001011234' in str(ok.get_json()['data'])

    anon = app.test_client()
    denied = anon.post('/dynatable/api/t/pension_roster/record/%d/reveal' % rid)
    assert denied.status_code in (302, 401), '未登录不得解锁明文'


def test_update_record_cell_and_required_guard(client):
    _create_table(client)
    rid = client.get('/dynatable/api/t/pension_roster/records').get_json()['data'][0]['id']
    ok = client.post('/dynatable/api/t/pension_roster/record/%d' % rid,
                     json={'code': 'f00', 'value': '张三改'})
    assert ok.get_json()['success']
    refreshed = client.get('/dynatable/api/t/pension_roster/records').get_json()['data'][0]
    assert refreshed['f00'] == '张三改'

    bad = client.post('/dynatable/api/t/pension_roster/record/%d' % rid,
                      json={'code': 'f00', 'value': ''})
    assert bad.status_code == 400 and '必填' in bad.get_json()['msg']


def test_structure_add_rename_retype_drop(client):
    _create_table(client)
    fields = client.get('/dynatable/api/t/pension_roster/fields').get_json()['data']
    fname = [f for f in fields if f['code'] == 'f00'][0]
    fnum = [f for f in fields if f['code'] == 'f04'][0]

    added = client.post('/dynatable/api/t/pension_roster/field/add',
                        json={'display_name': '备注', 'data_type': 'text'})
    assert added.get_json()['data']['code'] == 'f05'

    renamed = client.post('/dynatable/api/t/pension_roster/field/%d/update' % fname['id'],
                          json={'display_name': '参保人姓名'})
    assert renamed.get_json()['success']

    guard = client.post('/dynatable/api/t/pension_roster/field/%d/update' % fnum['id'],
                        json={'data_type': 'text'})
    assert guard.status_code == 409 and guard.get_json()['need_confirm']
    done = client.post('/dynatable/api/t/pension_roster/field/%d/update' % fnum['id'],
                       json={'data_type': 'text', 'confirm': True})
    assert done.get_json()['success']
    backups = _os.listdir(_os.path.join(_os.environ['SHUCUN_BACKUP_DIR'], 'softtable'))
    assert any(b.startswith('pension_roster-') for b in backups), '改类型必须留备份'
    assert any(b.endswith('.wbak') for b in backups), '密钥可用时应同时写 .wbak'

    dguard = client.post('/dynatable/api/t/pension_roster/field/%d/delete' % fnum['id'],
                         json={})
    assert dguard.status_code == 409 and dguard.get_json()['affected'] == 3
    deleted = client.post('/dynatable/api/t/pension_roster/field/%d/delete' % fnum['id'],
                          json={'confirm': True})
    assert deleted.get_json()['success']
    remaining = client.get('/dynatable/api/t/pension_roster/fields').get_json()['data']
    assert fnum['code'] not in [f['code'] for f in remaining]


def test_dynatable_permission_classified():
    """验收 6：蓝图必须按 ADR-016 归类，漏归 = 全端点 403。"""
    from utils.permissions import (ADMIN_BLUEPRINTS, BLUEPRINT_MODULES,
                                   PUBLIC_ENDPOINTS, module_for_endpoint)
    assert 'dynatable' not in ADMIN_BLUEPRINTS
    assert BLUEPRINT_MODULES.get('dynatable') == 'dynatable'
    assert module_for_endpoint('dynatable.index') == 'dynatable'
    assert not any(e.startswith('dynatable.') for e in PUBLIC_ENDPOINTS)


def test_pages_render_without_nested_form(client):
    """验收 8：页面不得嵌套 form（SSR 快照对标签非法嵌套零感知）。"""
    from html.parser import HTMLParser

    class Balance(HTMLParser):
        def __init__(self):
            HTMLParser.__init__(self)
            self.depth = 0
            self.max_form = 0
            self.errors = []

        def handle_starttag(self, tag, attrs):
            if tag == 'form':
                self.depth += 1
                self.max_form = max(self.max_form, self.depth)

        def handle_endtag(self, tag):
            if tag == 'form':
                self.depth -= 1
                if self.depth < 0:
                    self.errors.append('extra close form')

    for url in ('/dynatable/', '/dynatable/upload'):
        resp = client.get(url)
        assert resp.status_code == 200, url
        parser = Balance()
        parser.feed(resp.get_data(as_text=True))
        assert parser.errors == [], '%s 结构错误 %s' % (url, parser.errors)
        assert parser.depth == 0, '%s form 未闭合' % url
        assert parser.max_form <= 1, '%s 出现嵌套 form（深度 %d）' % (url, parser.max_form)
    # C-1：V1 confirm 死页退役，转 302 回统一智能传表入口
    resp = client.get('/dynatable/confirm?token=abc')
    assert resp.status_code == 302, 'confirm 应重定向到 upload'
    assert '/dynatable/upload' in resp.headers.get('Location', '')


# ================================================================== 返工闭环（Q-2 / Q-3）

def _row_of(client, code, rid):
    """按 id 取一条原始记录（直读 DB，绕过展示层打码）。"""
    with client.application.app_context():
        from models import DynamicRecord
        return DynamicRecord.query.filter_by(id=rid).first()


def _records_payload(client, code):
    return client.get('/dynatable/api/t/%s/records' % code).get_json()


def _create_with_rows(client, rows, code='rw_table'):
    """自定义行数据建表（默认字段规格 + 指定 code）。"""
    data = {'file': (_xlsx_bytes(SAMPLE_HEADERS, rows), 'sample.xlsx')}
    resp = client.post('/dynatable/api/upload', data=data,
                       content_type='multipart/form-data')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    token = resp.get_json()['data']['token']
    payload = {'token': token, 'code': code, 'name': '返工用例台账',
               'module_key': 'pension', 'fields': _default_fields()}
    return client.post('/dynatable/api/create', json=payload)


def test_q2_none_to_identity_reencrypts_existing(client):
    """Q-2①：none → identity 必须把存量转密文 + 补指纹，否则假打码、查重恒 0。"""
    rows = [['张三', '110101199001011234', '正常参保', '202610', 8000],
            ['李四', '110101199001011235', '暂停参保', '202610', 6500],
            ['王五', '110101199001011236', '正常参保', '202609', 7000]]
    created = _create_with_rows(client, rows)
    assert created.get_json()['success'], created.get_data(as_text=True)

    fields = client.get('/dynatable/api/t/rw_table/fields').get_json()['data']
    f03 = [f for f in fields if f['code'] == 'f03'][0]
    rid = _records_payload(client, 'rw_table')['data'][0]['id']

    before = _row_of(client, 'rw_table', rid)
    assert '"f03": "202610"' in before.data.replace('\n', ' ')
    assert 'f03' not in json.loads(before.digest or '{}'), \
        '改敏感前不应有指纹（结构化判键，避免 HMAC 哈希子串假阳性）'

    resp = client.post('/dynatable/api/t/rw_table/field/%d/update' % f03['id'],
                       json={'sensitivity': 'identity', 'confirm': True})
    assert resp.get_json()['success'], resp.get_data(as_text=True)

    after = _row_of(client, 'rw_table', rid)
    import json as _json
    data = _json.loads(after.data)
    digest = _json.loads(after.digest or '{}')
    assert data['f03'] != '202610', '改 identity 后存量必须变密文'
    assert '202610' not in after.data, '明文不得残留'
    assert len(digest.get('f03', '')) == 64, 'identity 必须补 64 位 HMAC 指纹'
    shown = _records_payload(client, 'rw_table')['data'][0]['f03']
    assert '*' in shown, '列表必须打码'
    # 查重：明文等值经指纹命中 2 行（修复前为 0）
    dup = client.get('/dynatable/api/t/rw_table/records?f_f03=202610').get_json()
    assert dup['total'] == 2, 'identity 明文查重必须命中 2 行'


def test_q2_identity_to_none_restores_plaintext(client):
    """Q-2②：identity → none 必须解密回明文并清除该键指纹。"""
    _create_table(client)
    fields = client.get('/dynatable/api/t/pension_roster/fields').get_json()['data']
    f01 = [f for f in fields if f['code'] == 'f01'][0]
    rid = _records_payload(client, 'pension_roster')['data'][0]['id']
    assert 'f01' in (_row_of(client, 'pension_roster', rid).digest or '')

    resp = client.post('/dynatable/api/t/pension_roster/field/%d/update' % f01['id'],
                       json={'sensitivity': 'none', 'confirm': True})
    assert resp.get_json()['success'], resp.get_data(as_text=True)

    import json as _json
    after = _row_of(client, 'pension_roster', rid)
    assert _json.loads(after.data)['f01'] == '110101199001011234', '必须还原明文'
    assert 'f01' not in _json.loads(after.digest or '{}'), '指纹必须清除'
    assert _records_payload(client, 'pension_roster')['data'][0]['f01'] == '110101199001011234'


def test_q2_failed_retype_rolls_back_and_keeps_backup(client):
    """Q-2③：identity → number 遇非数字存量 → 400，数据零改动，备份仍在。"""
    rows = [['张三', '110101199001011234', '正常参保', '202610', 8000],
            ['李四', 'ABC-不能转数字', '暂停参保', '202609', 6500]]
    created = _create_with_rows(client, rows, code='rw_fail')
    assert created.get_json()['success']

    fields = client.get('/dynatable/api/t/rw_fail/fields').get_json()['data']
    f01 = [f for f in fields if f['code'] == 'f01'][0]
    rid = _records_payload(client, 'rw_fail')['data'][0]['id']
    snapshot = _row_of(client, 'rw_fail', rid).data

    resp = client.post('/dynatable/api/t/rw_fail/field/%d/update' % f01['id'],
                       json={'data_type': 'number', 'confirm': True})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert '数据未改动' in resp.get_json()['msg']
    assert _row_of(client, 'rw_fail', rid).data == snapshot, '失败路径必须整笔回退'
    backups = _os.listdir(_os.path.join(_os.environ['SHUCUN_BACKUP_DIR'], 'softtable'))
    assert any(b.startswith('rw_fail-') for b in backups), '中止也必须留备份'


def test_q3_ghost_dict_category_rejected(app, client):
    """Q-3：分类不存在必须 400 且不落库（get_enum 的种子回退不得放行幽灵分类）。"""
    from models import DynamicField
    body = _upload(client).get_json()
    with app.app_context():
        fields_before = DynamicField.query.count()
    fields = _default_fields()
    fields[2]['dict_category_code'] = '不存在的分类'
    resp = client.post('/dynatable/api/create',
                       json={'token': body['data']['token'], 'code': 'ghost_table',
                             'name': '幽灵分类', 'fields': fields})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert '不存在' in resp.get_json()['msg']
    with app.app_context():
        from models import DynamicTable
        assert DynamicTable.query.filter_by(code='ghost_table').first() is None
        # 141（C-2）：启动即初始化 8 张综治固定表，「全新库零字段」前提不再成立 → 断言请求前后字段数不变
        assert DynamicField.query.count() == fields_before


def test_q3_enum_value_must_be_in_dict(app):
    """Q-3 兜底：枚举取值不在字典内必须报错，不得静默收自由文本。"""
    from utils import softtable_engine as eng
    spec = {'code': 'f00', 'display_name': '参保状态', 'data_type': 'enum',
            'dict_category_code': '参保状态'}
    with app.app_context():   # 分类存在性校验必须在应用上下文内（fail-closed 设计）
        out = eng.normalize_fields([spec])[0]
        with pytest.raises(eng.SoftTableError, match='不在字典分类'):
            eng.coerce_value(out, '自由文本乱填')
        # 分类不存在时 normalize_fields 直接拒
        with pytest.raises(eng.SoftTableError, match='不存在'):
            eng.normalize_fields([dict(spec, dict_category_code='幽灵分类')])

def test_a6_attribute_contexts_use_escattr():
    """A-6：属性上下文（title/value/href/data-*）必须用 escAttr，esc 只用于文本节点。"""
    import os
    import re
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'static', 'js', 'pages')
    names = ['dynatable_index.js', 'dynatable_list.js',
             'dynatable_structure.js', 'dynatable_v2_upload.js']
    pattern = re.compile(r'(title|value|href|placeholder|data-[a-z-]+)="\'\s*\+\s*esc\(')
    bad = []
    for name in names:
        src = open(os.path.join(base, name), encoding='utf-8').read()
        bad.extend((name, m.group(0)) for m in pattern.finditer(src))
    assert not bad, '属性上下文使用了 esc（应改 escAttr）：%s' % bad
