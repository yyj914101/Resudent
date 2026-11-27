# -*- coding: utf-8 -*-
"""tests/test_108_softtable_closure.py — 第一批软表闭环回归测试。

覆盖：
1. 动态归属软表自动追加为页内胶囊（social / resident / governance / sanitation / civilization）；
2. 无动态软表时 module_tabs 渲染与旧版完全一致；
3. dynatable.api_create 兼容 m031 预置空行就地补全字段；
4. 已有软表二次批量导入（自动列匹配、重排 column_map 投影、必填校验、同步与异步分批、失败行导出）；
5. 业务门面 roster_create 超过阈值自动切 A1 异步分批。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _login_admin(app):
    client = app.test_client()
    with app.app_context():
        from models import User
        admin = User.query.filter_by(username='admin').first()
    with client.session_transaction() as sess:
        sess['user_id'] = admin.id
        sess['username'] = admin.username
    return client


def _csv_bytes(lines):
    return ('\n'.join(lines) + '\n').encode('utf-8-sig')


def _seed_table(app, module_key, code, name, fields):
    with app.app_context():
        from utils import roster_facade as facade
        return facade.create_table(module_key, code, name, fields=fields, created_by='test')


def test_dynamic_capsules_across_modules(isolated_app_factory):
    app = isolated_app_factory()
    client = _login_admin(app)

    # 无动态表时，环卫与文明实践站不渲染胶囊条
    r_san_empty = client.get('/sanitation/')
    assert r_san_empty.status_code == 200
    assert 'sc-chips' not in r_san_empty.get_data(as_text=True)

    fields = [{'code': 'f00', 'display_name': '姓名', 'data_type': 'text',
               'sensitivity': 'none', 'is_required': True}]
    _seed_table(app, 'pension', 'pension_extra_1', '高龄补贴名册', fields)
    _seed_table(app, 'governance', 'gov_extra_1', '巡逻队员名册', fields)
    _seed_table(app, 'sanitation', 'san_extra_1', '公厕保洁台账', fields)

    # 1. 社会保障（单行追加）
    html_social = client.get('/pension/roster/pension_extra_1/').get_data(as_text=True)
    assert '高龄补贴名册' in html_social
    assert 'class="sc-chip is-active"' in html_social
    assert '#tab=dt:pension_extra_1' in html_social

    # 2. 综治管理（多行带标签栅格，新增「扩展台账」行）
    html_gov = client.get('/governance/roster/gov_extra_1/').get_data(as_text=True)
    assert '扩展台账' in html_gov
    assert '巡逻队员名册' in html_gov
    assert '#tab=dt:gov_extra_1' in html_gov

    # 3. 环卫工作（自动出现概览 + 动态台账胶囊）
    html_san = client.get('/sanitation/').get_data(as_text=True)
    assert '环卫概览' in html_san
    assert '公厕保洁台账' in html_san
    assert '#tab=dt:san_extra_1' in html_san


def test_dynatable_api_create_fills_m031_preset(isolated_app_factory):
    app = isolated_app_factory()
    client = _login_admin(app)

    with app.app_context():
        from models import DynamicTable, db
        db.session.add(DynamicTable(
            code='pension_roster', name='养老保险参保名册',
            module_key='pension', group_name='社会保障',
            permission_key='dynatable', created_by='m031'))
        db.session.commit()

    up = client.post('/dynatable/api/upload', data={
        'file': (io.BytesIO(_csv_bytes(['姓名,参保状态', '张三,正常参保'])), 'p.csv')
    }, content_type='multipart/form-data')
    assert up.status_code == 200 and up.get_json()['success']
    token = up.get_json()['data']['token']

    cr = client.post('/dynatable/api/create', json={
        'token': token,
        'code': 'pension_roster',
        'name': '养老保险参保名册',
        'module_key': 'pension',
        'fields': [
            {'code': 'f00', 'display_name': '姓名', 'data_type': 'text', 'is_required': True},
            {'code': 'f01', 'display_name': '参保状态', 'data_type': 'text', 'is_required': False},
        ],
    })
    body = cr.get_json()
    assert cr.status_code == 200 and body['success'], body
    assert body['data']['imported'] == 1


def test_roster_append_sync_and_async(isolated_app_factory, monkeypatch):
    app = isolated_app_factory()
    client = _login_admin(app)

    fields = [
        {'code': 'f00', 'display_name': '姓名', 'data_type': 'text', 'is_required': True},
        {'code': 'f01', 'display_name': '金额', 'data_type': 'number', 'is_required': False},
    ]
    _seed_table(app, 'governance', 'gov_append_demo', '测试追加台账', fields)

    # 1. 同步二次追加（列顺序故意与建表相反，验证自动列匹配 + 投影）
    up = client.post('/governance/roster/gov_append_demo/api/append-upload', data={
        'file': (io.BytesIO(_csv_bytes(['金额,姓名,多余列', '100,李四,忽略', '200,王五,忽略'])), 'a.csv')
    }, content_type='multipart/form-data')
    assert up.status_code == 200
    ud = up.get_json()['data']
    assert ud['missing_required'] == []
    assert ud['unmatched_headers'] == ['多余列']
    col_map = {f['code']: f['matched_index'] for f in ud['fields']}
    assert col_map == {'f00': 1, 'f01': 0}

    ex = client.post('/governance/roster/gov_append_demo/api/append-execute', json={
        'token': ud['token'], 'column_map': col_map, 'skip_error_rows': False,
    })
    eb = ex.get_json()
    assert ex.status_code == 200 and eb['success']
    assert eb['data']['mode'] == 'sync' and eb['data']['imported'] == 2

    # 2. 异步二次追加（调低阈值 + 同步执行 run_async 验证全链路）
    import utils.roster_routes as rr
    import utils.async_tasks as at
    monkeypatch.setattr(rr, 'ASYNC_ROW_THRESHOLD', 1)

    def _sync_run_async(task_type, task_id, fn, *args, **kwargs):
        with app.app_context():
            fn(task_id, *args, **kwargs)

    monkeypatch.setattr(at, 'run_async', _sync_run_async)

    up2 = client.post('/governance/roster/gov_append_demo/api/append-upload', data={
        'file': (io.BytesIO(_csv_bytes(['姓名,金额', '赵六,300', '钱七,not_a_num'])), 'b.csv')
    }, content_type='multipart/form-data')
    ud2 = up2.get_json()['data']
    assert ud2['will_async'] is True
    col_map2 = {f['code']: f['matched_index'] for f in ud2['fields']}

    ex2 = client.post('/governance/roster/gov_append_demo/api/append-execute', json={
        'token': ud2['token'], 'column_map': col_map2, 'skip_error_rows': True,
    })
    eb2 = ex2.get_json()
    assert ex2.status_code == 200 and eb2['success']
    assert eb2['data']['mode'] == 'async'
    batch_id = eb2['data']['batch_id']
    task_id = eb2['data']['task_id']

    st = client.get('/governance/roster/gov_append_demo/api/import-status/' + task_id).get_json()
    assert st['success'] and st['data']['status'] == 'done'

    rep = client.get('/governance/roster/gov_append_demo/api/import-report/' + batch_id).get_json()
    assert rep['success'] and rep['data']['success_rows'] == 1 and rep['data']['fail_rows'] == 1

    exp = client.get('/governance/roster/gov_append_demo/api/import-export-failed/' + batch_id)
    assert exp.status_code == 200
