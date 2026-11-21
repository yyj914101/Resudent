# -*- coding: utf-8 -*-
"""tests/test_093_special_veteran_fields.py — 093 B-6/B-7/B-8 特殊群体服务与退役类别。

覆盖（红→绿）：
  1) B-6 残疾人服务事项：1:N 服务记录顺序（service_date 倒序、同日 id 倒序、空日期最后）、
     非法事项/状态/日期零写库、列表 service_item/service_status 筛选与 service_count、
     导出末尾 5 列且行数与筛选一致、删残疾人级联清子表。
  2) B-7 老年人服务事项：仅老人可登记、儿童拒绝零写库、列表筛选/导出（儿童行空值）、
     软失效后服务记录保留、高龄补贴申报不写 subsidy_record。
  3) B-8 退役类别：save/detail/list 筛选/导出、非法值零写库、空值允许、
     不影响 service_branch/placement_status、模板字段清单、导入非法行不落库。
  4) m027 迁移/字典：create_all 新库两表 + retire_category 列；连续执行两次幂等，
     四分类项数 3/3/4/7 且无重复。
  5) 鉴权：未登录 GET/POST 服务端点按 login_required 302 重定向。

隔离：临时 SHUCUN_DB_PATH + temp/upload/backup/data 目录（沿用 094 夹具风格），
测试数据均为虚构（姓名「测试甲」等，身份证为合成号码）。
"""
import importlib.util
import io
import os
import sqlite3
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# 虚构测试数据（非真实个人信息）
ID_A = '110101199001011234'
ID_B = '110101199202022345'
ID_C = '110101199303033456'
ID_D = '110101199404044567'

DISABLED_ITEMS = ('残疾证代办', '康复服务对接', '两项补贴申报')
ELDER_ITEMS = ('老年证代办', '高龄补贴申报', '居家养老对接')
SERVICE_STATUS = ('待受理', '办理中', '已完成', '已终止')
RETIRE_CATEGORIES = ('义务兵退役', '士官退役', '军官退役', '转业', '复员', '离退休', '其他')

DICT_EXPECT = {
    '残疾人服务事项': list(DISABLED_ITEMS),
    '老年人服务事项': list(ELDER_ITEMS),
    '服务办理状态': list(SERVICE_STATUS),
    '退役类别': list(RETIRE_CATEGORIES),
}


# ───────────────────── 夹具 ─────────────────────

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
    os.makedirs(tmp_path / "temp", exist_ok=True)
    return _make_app(tmp_path, monkeypatch)


def _unlock_keyholder():
    from utils.crypto import key_holder, generate_salt
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令093', salt=generate_salt())


@pytest.fixture()
def client(app):
    _unlock_keyholder()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = 1  # admin 全权限
    return c


# ───────────────────── 通用工具 ─────────────────────

def _xlsx_bytes(headers, rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _xlsx_headers(resp):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    return [c.value for c in wb.active[1]]


def _xlsx_rows(resp):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    return [list(r) for r in wb.active.iter_rows(min_row=2, values_only=True)]


def _sqlite_tables(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return sorted(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
    finally:
        conn.close()


def _sqlite_columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return [row[1] for row in conn.execute('PRAGMA table_info(%s)' % table)]
    finally:
        conn.close()


def _sqlite_indexes(db_path, table):
    """返回该表非自动唯一约束的普通索引名列表（PRAGMA index_list 排除 auto 唯一项）。"""
    conn = sqlite3.connect(db_path)
    try:
        return sorted(
            row[1] for row in conn.execute('PRAGMA index_list(%s)' % table)
            if row[2] == 0)  # unique=0，排除 SQLite 自动为主键/UNIQUE 建的 sqlite_autoindex
    finally:
        conn.close()


def _sqlite_count(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
    finally:
        conn.close()


def _dict_counts(db_path):
    """四分类字典项（按 sort_order，供项数/重复断言）。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            'SELECT c.name, i.label FROM dict_categories c '
            'JOIN dict_items i ON i.category_id = c.id WHERE c.name IN (%s) '
            'ORDER BY c.name, i.sort_order'
            % ','.join('?' * len(DICT_EXPECT)), tuple(DICT_EXPECT)).fetchall()
    finally:
        conn.close()
    out = {name: [] for name in DICT_EXPECT}
    for name, label in rows:
        out[name].append(label)
    return out


def _load_m027():
    path = os.path.join(BASE, 'scripts', 'migrations', 'm027_special_veteran_fields.py')
    spec = importlib.util.spec_from_file_location('m027_special_veteran_fields', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _record_count(app, model_name, **filters):
    import models
    model = getattr(models, model_name)
    with app.app_context():
        q = model.query
        if filters:
            q = q.filter_by(**filters)
        return q.count()


def _make_resident(app, name, birth_year, gender='男'):
    from datetime import date
    from models import db, Resident
    with app.app_context():
        r = Resident(name=name, birth_date=date(birth_year, 1, 1),
                     village_group='一组', grid='一组', gender=gender)
        db.session.add(r)
        db.session.commit()
        return r.id


# ───────────────────── B-6 残疾人服务事项 ─────────────────────

def _new_disabled(client, name='测试甲', id_card=ID_A):
    resp = client.post('/disabled/api/save', data={
        'name': name, 'id_card': id_card, 'village_group': '一组', 'gender': '男',
        'nation': '汉', 'education': '初中', 'phone': '13800138000',
        'registered_address': '一组1号', 'guardian_name': '测试监护人',
        'guardian_relation': '父', 'guardian_phone': '13900139000',
        'cert_no': 'CJ-001', 'disability_type': '肢体', 'disability_level': '三级',
        'disability_detail': '测试残疾详情',
    }).get_json()
    assert resp['success'], resp
    return resp['data']['id']


def _add_disabled_service(client, did, item, status='待受理', date_text='',
                          progress='', owner='', remark=''):
    resp = client.post('/disabled/api/services/%d/save' % did, data={
        'service_item': item, 'service_status': status, 'service_date': date_text,
        'service_progress': progress, 'service_owner': owner, 'remark': remark,
    }).get_json()
    return resp


def test_b6_service_order_by_date_and_id(app, client):
    did = _new_disabled(client)
    r1 = _add_disabled_service(client, did, '残疾证代办', '办理中', '2026-01-05',
                               '材料已收', '村干事甲')
    r2 = _add_disabled_service(client, did, '康复服务对接', '待受理', '2026-02-10')
    r3 = _add_disabled_service(client, did, '两项补贴申报', '已完成', '2026-02-10',
                               '已发放', '村干事乙')
    r4 = _add_disabled_service(client, did, '残疾证代办', '待受理', '')
    for r in (r1, r2, r3, r4):
        assert r['success'], r

    listed = client.get('/disabled/api/services/%d' % did).get_json()
    assert listed['success'] is True
    data = listed['data']
    assert len(data) == 4
    assert [d['service_date'] for d in data] == ['2026-02-10', '2026-02-10', '2026-01-05', '']
    # 同日（2026-02-10）按 id 倒序：后写入的「两项补贴申报」在前
    assert data[0]['service_item'] == '两项补贴申报'
    assert data[1]['service_item'] == '康复服务对接'
    # 空日期记录排最后
    assert data[-1]['service_item'] == '残疾证代办' and data[-1]['service_date'] == ''


def test_b6_invalid_service_values_zero_write(app, client):
    did = _new_disabled(client)
    assert _add_disabled_service(client, did, '残疾证代办', '待受理',
                                 '2026-01-05')['success'] is True
    before = _record_count(app, 'DisabledServiceRecord', disabled_id=did)
    assert before == 1

    bad_item = _add_disabled_service(client, did, '不存在事项')
    assert bad_item['success'] is False and '不合法' in bad_item['msg']

    bad_status = _add_disabled_service(client, did, '残疾证代办', '不存在状态')
    assert bad_status['success'] is False and '不合法' in bad_status['msg']

    bad_date = _add_disabled_service(client, did, '残疾证代办', '待受理', '2026-13-45')
    assert bad_date['success'] is False and '日期' in bad_date['msg']

    assert _record_count(app, 'DisabledServiceRecord', disabled_id=did) == before


def test_b6_list_filter_and_service_count(app, client):
    did1 = _new_disabled(client, '测试甲', ID_A)
    did2 = _new_disabled(client, '测试乙', ID_B)
    assert _add_disabled_service(client, did1, '残疾证代办', '办理中')['success']
    assert _add_disabled_service(client, did1, '残疾证代办', '已完成')['success']

    listing = client.get('/disabled/api/list').get_json()['data']['data']
    row1 = next(r for r in listing if r['id'] == did1)
    row2 = next(r for r in listing if r['id'] == did2)
    assert row1['service_count'] == 2
    assert row1['service_item'] == '残疾证代办'
    assert row2['service_item'] == '' and row2['service_status'] == ''
    assert row2['service_count'] == 0

    by_item = client.get('/disabled/api/list?service_item=残疾证代办').get_json()['data']
    assert by_item['total'] == 1
    assert by_item['data'][0]['id'] == did1

    by_status = client.get('/disabled/api/list?service_status=办理中').get_json()['data']
    assert by_status['total'] == 1
    assert by_status['data'][0]['id'] == did1

    miss = client.get('/disabled/api/list?service_item=居家养老对接').get_json()['data']
    assert miss['total'] == 0


def test_b6_export_headers_and_row_count(app, client):
    did1 = _new_disabled(client, '测试甲', ID_A)
    did2 = _new_disabled(client, '测试乙', ID_B)
    assert _add_disabled_service(client, did1, '残疾证代办', '办理中', '2026-01-05',
                                 '材料已收', '村干事甲')['success']
    assert _add_disabled_service(client, did1, '康复服务对接', '待受理', '2026-02-10')['success']

    export = client.get('/disabled/api/export?service_item=残疾证代办')
    assert export.status_code == 200
    headers = _xlsx_headers(export)
    assert headers[-5:] == ['服务事项', '办理状态', '办理进度', '责任人', '服务日期']
    rows = _xlsx_rows(export)
    assert len(rows) == 1, '导出行数应与 service_item 筛选一致'
    assert rows[0][headers.index('姓名')] == '测试甲'
    assert all(len(r) == len(headers) for r in rows)


def test_b6_delete_disabled_cascades_services(app, client):
    did = _new_disabled(client)
    assert _add_disabled_service(client, did, '残疾证代办', '待受理')['success']
    assert _add_disabled_service(client, did, '康复服务对接', '已完成')['success']
    assert _record_count(app, 'DisabledServiceRecord', disabled_id=did) == 2

    resp = client.post('/disabled/api/delete/%d' % did).get_json()
    assert resp['success'] is True
    assert _record_count(app, 'DisabledServiceRecord', disabled_id=did) == 0


# ───────────────────── B-7 老年人服务事项 ─────────────────────

def _make_target(client, resident_id, **data):
    payload = {'resident_id': str(resident_id), 'source': '手工建档'}
    payload.update(data)
    resp = client.post('/care/api/target/save', data=payload).get_json()
    return resp


def _add_elder_service(client, tid, item, status='待受理', date_text='',
                       progress='', owner=''):
    return client.post('/care/api/target/services/%d/save' % tid, data={
        'service_item': item, 'service_status': status, 'service_date': date_text,
        'service_progress': progress, 'service_owner': owner,
    }).get_json()


def test_b7_elder_services_child_rejected(app, client):
    elder_rid = _make_resident(app, '测试老人甲', 1945)
    child_rid = _make_resident(app, '测试儿童乙', 2015, gender='女')

    elder = _make_target(client, elder_rid, target_type='老人', elder_type='困难')
    assert elder['success'], elder
    elder_id = elder['data']['id']
    child = _make_target(client, child_rid, target_type='儿童', child_type='留守')
    assert child['success'], child
    child_id = child['data']['id']

    subsidy_before = _sqlite_count(app.config['TEST_DB_PATH'], 'subsidy_record')

    for item, status, day in [
        ('老年证代办', '已完成', '2026-01-10'),
        ('高龄补贴申报', '办理中', '2026-02-10'),
        ('居家养老对接', '待受理', '2026-03-10'),
    ]:
        resp = _add_elder_service(client, elder_id, item, status, day)
        assert resp['success'], resp

    assert _sqlite_count(app.config['TEST_DB_PATH'], 'subsidy_record') == subsidy_before

    elder_count = _record_count(app, 'ElderServiceRecord', care_target_id=elder_id)
    assert elder_count == 3
    rejected = _add_elder_service(client, child_id, '老年证代办', '待受理')
    assert rejected['success'] is False and '老年人' in rejected['msg']
    assert _record_count(app, 'ElderServiceRecord', care_target_id=child_id) == 0
    assert _record_count(app, 'ElderServiceRecord', care_target_id=elder_id) == elder_count


def test_b7_list_filter_and_export(app, client):
    elder_rid = _make_resident(app, '测试老人甲', 1945)
    child_rid = _make_resident(app, '测试儿童乙', 2015, gender='女')
    elder_id = _make_target(client, elder_rid, target_type='老人',
                            elder_type='困难')['data']['id']
    child_id = _make_target(client, child_rid, target_type='儿童',
                            child_type='留守')['data']['id']
    assert _add_elder_service(client, elder_id, '老年证代办', '已完成', '2026-01-10')['success']
    assert _add_elder_service(client, elder_id, '居家养老对接', '办理中', '2026-03-10',
                              '已预约', '村干事甲')['success']

    by_item = client.get('/care/api/target/list?service_item=老年证代办').get_json()['data']
    assert by_item['total'] == 1
    elder_row = by_item['data'][0]
    assert elder_row['id'] == elder_id
    assert elder_row['service_count'] == 2
    assert elder_row['service_item'] == '居家养老对接'  # 最近一条

    by_status = client.get('/care/api/target/list?service_status=已完成').get_json()['data']
    assert by_status['total'] == 1 and by_status['data'][0]['id'] == elder_id

    export = client.get('/care/api/target/export')
    assert export.status_code == 200
    headers = _xlsx_headers(export)
    assert headers[-5:] == ['服务事项', '办理状态', '办理进度', '责任人', '服务日期']
    rows = _xlsx_rows(export)
    assert len(rows) == 2
    elder = next(r for r in rows if r[headers.index('姓名')] == '测试老人甲')
    child = next(r for r in rows if r[headers.index('姓名')] == '测试儿童乙')
    assert elder[headers.index('服务事项')] == '居家养老对接'
    assert child[headers.index('服务事项')] in (None, '')
    assert child[headers.index('办理状态')] in (None, '')
    assert child[headers.index('服务日期')] in (None, '')


def test_b7_soft_invalidate_keeps_records(app, client):
    rid = _make_resident(app, '测试老人甲', 1945)
    tid = _make_target(client, rid, target_type='老人', elder_type='困难')['data']['id']
    assert _add_elder_service(client, tid, '老年证代办', '已完成', '2026-01-10')['success']
    assert _add_elder_service(client, tid, '居家养老对接', '办理中', '2026-02-10')['success']
    before = _record_count(app, 'ElderServiceRecord', care_target_id=tid)

    resp = client.post('/care/api/target/delete/%d' % tid,
                       data={'reason': '测试软失效'}).get_json()
    assert resp['success'] is True
    assert _record_count(app, 'ElderServiceRecord', care_target_id=tid) == before
    listed = client.get('/care/api/target/services/%d' % tid).get_json()
    assert listed['success'] and len(listed['data']) == before


# ───────────────────── B-8 退役类别 ─────────────────────

def _vet_form(name, id_card, retire_category='', **overrides):
    data = {
        'name': name, 'id_card': id_card, 'gender': '男', 'nation': '汉族',
        'politics_status': '群众', 'education': '初中',
        'registered_residence': '测试村一组', 'grid': '一组',
        'home_address': '测试村一组1号', 'phone': '13800138001',
        'relative_name': '测试亲属', 'relative_phone': '13900139001',
        'enlist_date': '2015-01', 'discharge_date': '2017-01',
        'service_unit': '测试部队', 'service_branch': '陆军', 'service_rank': '班长',
        'discharge_cert_no': 'CJ-2017-001', 'household_reg_type': '农业户口',
        'placement_status': '自主就业', 'retire_category': retire_category,
        'key_group_category': '', 'remark': '',
    }
    data.update(overrides)
    return data


def test_b8_save_detail_list_export(app, client):
    saved = client.post('/veteran/api/save',
                        data=_vet_form('测试退伍甲', ID_A, '义务兵退役')).get_json()
    assert saved['success'], saved
    vid = saved['data']['id']

    detail = client.get('/veteran/api/detail/%d' % vid).get_json()
    assert detail['success'] and detail['data']['retire_category'] == '义务兵退役'

    listed = client.get('/veteran/api/list?retire_category=义务兵退役').get_json()['data']
    assert listed['total'] == 1
    assert listed['data'][0]['retire_category'] == '义务兵退役'
    other = client.get('/veteran/api/list?retire_category=士官退役').get_json()['data']
    assert other['total'] == 0

    export = client.get('/veteran/api/export?retire_category=义务兵退役')
    headers = _xlsx_headers(export)
    assert '退役类别' in headers
    rows = _xlsx_rows(export)
    assert len(rows) == 1
    assert rows[0][headers.index('退役类别')] == '义务兵退役'


def test_b8_invalid_and_empty_and_isolation(app, client):
    saved = client.post('/veteran/api/save',
                        data=_vet_form('测试退伍甲', ID_A, '义务兵退役')).get_json()
    vid = saved['data']['id']

    bad = client.post('/veteran/api/save',
                      data=_vet_form('测试退伍甲', ID_A, '未知退役类别',
                                     id=str(vid))).get_json()
    assert bad['success'] is False and '不合法' in bad['msg']
    detail = client.get('/veteran/api/detail/%d' % vid).get_json()['data']
    assert detail['retire_category'] == '义务兵退役', '非法值不得改写库中取值'
    assert detail['service_branch'] == '陆军'
    assert detail['placement_status'] == '自主就业'

    # 空值允许
    empty = client.post('/veteran/api/save',
                        data=_vet_form('测试退伍丙', ID_C, '')).get_json()
    assert empty['success'] is True
    empty_detail = client.get('/veteran/api/detail/%d' % empty['data']['id']).get_json()
    assert empty_detail['data']['retire_category'] == ''

    # 另存合法值后，兵种/安置情况保持原值
    updated = client.post('/veteran/api/save',
                          data=_vet_form('测试退伍甲', ID_A, '转业',
                                         id=str(vid))).get_json()
    assert updated['success'] is True
    detail2 = client.get('/veteran/api/detail/%d' % vid).get_json()['data']
    assert detail2['retire_category'] == '转业'
    assert detail2['service_branch'] == '陆军'
    assert detail2['placement_status'] == '自主就业'


def test_b8_template_field_list_contains_key(client):
    tpl = client.get('/veteran/api/template')
    headers = _xlsx_headers(tpl)
    assert '退役类别' in headers

    # 导入字段目录的 key 为 retire_category（模板端点 field_map 在路由内局部变量，
    # pytest 无法驱动，沿用既有「源码结构断言」做法：test_custom_select_guards.py 先例）
    with open(os.path.join(BASE, 'routes', 'veteran.py'), encoding='utf-8') as f:
        source = f.read()
    assert "'label': '退役类别', 'key': 'retire_category'" in source


def test_b8_import_rejects_invalid_category(app, client):
    buf = _xlsx_bytes(['姓名', '身份证号码', '退役类别'], [
        ['测试导入甲', ID_D, '非法退役类别'],
        ['测试导入乙', '110101199505055678', '士官退役'],
    ])
    resp = client.post('/veteran/api/import', data={
        'mode': 'append', 'file': (buf, 'b8.xlsx'),
    }, content_type='multipart/form-data').get_json()

    assert resp['success'] is False, '含非法退役类别行时整批应报错'
    assert resp['data']['imported'] == 1
    assert len(resp['data']['errors']) == 1
    assert '退役类别' in resp['data']['errors'][0]

    from models import Veteran
    with app.app_context():
        assert Veteran.query.filter_by(name='测试导入甲').first() is None, '非法行不得落库'
        v = Veteran.query.filter_by(name='测试导入乙').first()
        assert v is not None and v.retire_category == '士官退役'


# ───────────────────── 迁移 / 字典 ─────────────────────

def test_m027_schema_create_all(app):
    db_path = app.config['TEST_DB_PATH']
    tables = _sqlite_tables(db_path)
    assert 'disabled_service_records' in tables
    assert 'elder_service_records' in tables
    assert 'retire_category' in _sqlite_columns(db_path, 'veteran')


def test_m027_idempotent_and_dict_counts(app):
    db_path = app.config['TEST_DB_PATH']
    m027 = _load_m027()

    first = m027.run(db_path)
    assert first['new_items'] >= 0
    second = m027.run(db_path)

    assert second['added_columns'] == [], '二次执行不得重复加列'
    assert second['created_tables'] == [], '二次执行不得重复建表'
    assert second['new_items'] == 0, '二次执行不得新增字典项'

    counts = _dict_counts(db_path)
    assert counts == DICT_EXPECT
    assert {name: len(labels) for name, labels in counts.items()} == {
        '残疾人服务事项': 3, '老年人服务事项': 3, '服务办理状态': 4, '退役类别': 7}
    for name, labels in counts.items():
        assert len(labels) == len(set(labels)), '%s 存在重复字典项' % name


def test_m027_index_single_source(app):
    """R-093-01：模型去掉 index=True 后 create_all 不产生 ix_* 自动索引，
    m027 建 idx_*，新库/存量库索引集合一致（disabled/elder 两表均仅 idx_*）。"""
    db_path = app.config['TEST_DB_PATH']
    m027 = _load_m027()
    m027.run(db_path)
    expected = {
        'disabled_service_records': ['idx_disabled_service_records_disabled'],
        'elder_service_records': ['idx_elder_service_records_care_target'],
    }
    for table, exp in expected.items():
        idx = _sqlite_indexes(db_path, table)
        assert idx == exp, (table, idx)
        # 无 ix_ 自动索引残留（双索引漂移即红）
        assert not any(name.startswith('ix_') for name in idx), (table, idx)


# ───────────────────── 鉴权 ─────────────────────

@pytest.mark.parametrize('url', [
    '/disabled/api/services/1',
    '/care/api/target/services/1',
])
def test_service_endpoints_require_login(app, url):
    """未登录：/api/ 路径按既有 response_wants_json 口径返回 401 JSON，不泄露业务数据。"""
    anon = app.test_client()
    resp = anon.get(url)
    assert resp.status_code == 401
    assert resp.get_json()['success'] is False

    resp_post = anon.post(url, data={'service_item': '残疾证代办'})
    assert resp_post.status_code == 401
    assert resp_post.get_json()['success'] is False
    assert 'data' not in resp_post.get_json()
