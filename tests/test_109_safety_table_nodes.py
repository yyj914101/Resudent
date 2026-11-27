# -*- coding: utf-8 -*-
"""tests/test_109_safety_table_nodes.py — 第二批安全工作目录软表节点与应急收口测试。

覆盖：
1. 文件夹内上传 Excel 新建软表并自动挂载为 SafetyNode(node_type='table')；
2. 列表排序（目录 → 台账 → 文件）、统计胶囊（tables + files）与 /safety/roster/<code>/ 门面访问；
3. 从【台账建表】选安全工作 + safety_folder_id 自动挂载，及 /safety/api/table/unlinked + link 关联；
4. 移出台账节点（unlink / batch_delete）仅解绑目录节点，不物理删除 dynamic_table 数据；
5. 目录打包下载（/safety/api/zip）自动将目录内软表导出为 .xlsx 写入 ZIP；
6. 【应急管理】自动读取「应急工作方案」目录下挂载的台账（含非队伍/物资名册）。
"""
import io
import os
import sys
import zipfile

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


def test_safety_folder_create_table_list_zip_and_unlink(isolated_app_factory):
    app = isolated_app_factory()
    client = _login_admin(app)

    # 找到「消防资料」一级目录
    roots = client.get('/safety/api/children?parent_id=0').get_json()['data']['items']
    fire = next(it for it in roots if it['name'] == '消防资料')
    fid = fire['id']

    # 1. 目录内上传 Excel 新建台账并挂载
    up = client.post('/safety/api/table/upload', data={
        'file': (io.BytesIO(_csv_bytes(['场所名称,灭火器数量', '社区卫生站,6', '快递驿站,3'])), '消防台账.csv')
    }, content_type='multipart/form-data')
    assert up.status_code == 200 and up.get_json()['success']
    ud = up.get_json()['data']

    cr = client.post('/safety/api/table/create', json={
        'parent_id': fid,
        'token': ud['token'],
        'name': '消防器材台账',
        'code': 'safety_fire_equip',
        'fields': [
            {'code': 'f00', 'display_name': '场所名称', 'data_type': 'text', 'is_required': True},
            {'code': 'f01', 'display_name': '灭火器数量', 'data_type': 'number', 'is_required': False},
        ],
    })
    cb = cr.get_json()
    assert cr.status_code == 200 and cb['success'], cb
    node_id = cb['data']['id']

    # 2. 列表可见 + 统计胶囊含 tables=1 + 门面路由可达
    lst = client.get(f'/safety/api/list?node_id={fid}').get_json()['data']['items']
    assert len(lst) == 1 and lst[0]['node_type'] == 'table'
    assert lst[0]['table_code'] == 'safety_fire_equip' and lst[0]['record_count'] == 2
    assert f'from_node={fid}' in lst[0]['roster_url']

    st = client.get('/safety/api/stats').get_json()['data']
    assert st['tables'] == 1

    page = client.get(f'/safety/roster/safety_fire_equip/?from_node={fid}')
    assert page.status_code == 200 and '消防器材台账' in page.get_data(as_text=True)

    # 3. 打包下载目录 ZIP 应包含实时导出的 .xlsx
    zresp = client.get(f'/safety/api/zip?node_id={fid}')
    assert zresp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zresp.data)) as zf:
        names = zf.namelist()
        assert any(n.endswith('消防器材台账.xlsx') for n in names), names

    # 4. 移出台账节点：仅解绑目录挂载，DynamicTable 数据仍保留且重新出现在 unlinked
    un = client.post('/safety/api/table', data={'action': 'unlink', 'id': node_id}).get_json()
    assert un['success']
    lst_after = client.get(f'/safety/api/list?node_id={fid}').get_json()['data']['items']
    assert lst_after == []
    unlinked = client.get('/safety/api/table/unlinked').get_json()['data']['items']
    assert any(it['code'] == 'safety_fire_equip' and it['record_count'] == 2 for it in unlinked)

    # 5. 重新关联回目录
    lk = client.post('/safety/api/table/link', data={'parent_id': fid, 'table_code': 'safety_fire_equip'}).get_json()
    assert lk['success']


def test_emergency_reads_mounted_safety_folder_tables(isolated_app_factory):
    app = isolated_app_factory()
    client = _login_admin(app)

    # 触发应急种子并定位「应急工作方案」目录
    client.get('/safety/emergency')
    roots = client.get('/safety/api/children?parent_id=0').get_json()['data']['items']
    em_folder = next(it for it in roots if it['name'] == '应急工作方案')

    # 通过台账建表 + safety_folder_id 建一张不含「队伍/物资」的应急台账（如避难场所）
    up = client.post('/dynatable/api/upload', data={
        'file': (io.BytesIO(_csv_bytes(['场所名称,容量', '社区广场,500'])), 'shelter.csv')
    }, content_type='multipart/form-data')
    token = up.get_json()['data']['token']
    cr = client.post('/dynatable/api/create', json={
        'token': token,
        'code': 'safety_shelter_list',
        'name': '应急避难场所名册',
        'module_key': 'safety',
        'safety_folder_id': em_folder['id'],
        'fields': [
            {'code': 'f00', 'display_name': '场所名称', 'data_type': 'text', 'is_required': True},
            {'code': 'f01', 'display_name': '容量', 'data_type': 'number', 'is_required': False},
        ],
    })
    assert cr.status_code == 200 and cr.get_json()['success']

    html = client.get('/safety/emergency').get_data(as_text=True)
    assert '其他应急台账（目录挂载）' in html
    assert '应急避难场所名册' in html
    assert '/safety/roster/safety_shelter_list/' in html
    assert f'?node={em_folder["id"]}' in html
