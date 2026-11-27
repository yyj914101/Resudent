# -*- coding: utf-8 -*-
"""tests/test_youshu_module.py — 优抚对象台账（2026-11-16，民政对象第 6 类）验收。

覆盖：
A. 胶囊接线：民政对象管理 civil 组第 6 项「优抚对象」，宿主 /youshu/ 页 200 且
   页内胶囊 8 枚、youshu 激活；
B. 权限归属：BLUEPRINT_MODULES['youshu'] = 'dibao'（复用低保模块位，既有角色零补授权）；
C. CRUD：新增（校验必填）→ 列表 → 编辑回填与保存 → 删除；
D. 救助类别走字典：分类「优抚对象救助类别」三项种子（ensure_enum_dicts 幂等），
   表单/筛选下拉经 dictSelect 消费（源码断言），选项可改属字典管理职责；
E. 导出：/api/export 200 且走 T06 脱敏管线（身份证列含敏感词表头）；
F. 迁移 m043 幂等（建表 + 字典补种）。
"""
import importlib.util
import io
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.dynatable_modules import MODULE_KEYS  # noqa: E402  （胶囊真源引用见 D）

ID_CARD = '110101199001011234'


def _login(client):
    r = client.get('/auth/login')
    import re
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', r.get_data(as_text=True))
    client.post('/auth/login', data={
        'csrf_token': m.group(1) if m else '', 'username': 'admin', 'password': 'admin123'})
    return client


# ------------------------------------------------------------------ A. 胶囊接线

def test_civil_chip_has_youshu(isolated_app_factory):
    from tests.test_module_tabs import _render
    html = _render(isolated_app_factory(), 'civil', 'youshu')
    assert '>优抚对象<' in html and '#tab=youshu' in html
    assert 'class="sc-chip is-active"' in html


def test_youshu_page_renders(isolated_app_factory):
    app = isolated_app_factory()
    client = app.test_client()
    _login(client)
    resp = client.get('/youshu/')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '优抚对象' in html
    assert 'civil_tabs' not in html  # 胶囊经 module_tabs('civil') 宏渲染
    for frag in ('id="youshuBody"', 'id="searchType"', 'id="youshuFormModal"',
                 'css/pages/civil_pages.css', 'js/pages/youshu_list.js'):
        assert frag in html, '页面缺 %s' % frag


# ------------------------------------------------------------------ B. 权限归属

def test_module_key_reuses_dibao():
    """权限复用低保模块位：既有角色零补授权（主理人口径「权限设置不变」）。"""
    from utils.permissions import BLUEPRINT_MODULES
    assert BLUEPRINT_MODULES.get('youshu') == 'dibao'


# ------------------------------------------------------------------ C. CRUD

def _crypto_ready():
    """身份证/电话走加密管线，测试前须初始化密钥（test_088 同款）。"""
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('youshu-test', salt=generate_salt())


def test_save_list_edit_delete(isolated_app_factory):
    app = isolated_app_factory()
    _crypto_ready()
    client = app.test_client()
    _login(client)

    # 新增：必填校验
    bad = client.post('/youshu/api/save', data={'name': '', 'id_card': ''})
    assert bad.get_json()['success'] is False

    # 新增成功
    resp = client.post('/youshu/api/save', data={
        'name': '张优抚', 'id_card': ID_CARD, 'phone': '13800001111',
        'care_category': '因残退役', 'amount': '600', 'is_deceased': '否',
        'support_status': '已认定', 'remark': '验收行',
    })
    body = resp.get_json()
    assert body['success'], body
    rid = body['data']['id']

    # 列表可查且敏感字段解密下发
    listed = client.get('/youshu/api/list').get_json()
    row = next(x for x in listed['data']['data'] if x['id'] == rid)
    assert row['name'] == '张优抚'
    assert row['id_card'] == ID_CARD, '列表必须下发解密后的身份证号'
    assert row['care_category'] == '因残退役'

    # 编辑（改类别与金额）
    resp = client.post('/youshu/api/save', data={
        'id': rid, 'name': '张优抚', 'id_card': ID_CARD, 'phone': '13800001111',
        'care_category': '涉核工程', 'amount': '800', 'is_deceased': '否',
        'support_status': '复审中',
    })
    assert resp.get_json()['success']
    row = next(x for x in client.get('/youshu/api/list').get_json()['data']['data']
               if x['id'] == rid)
    assert row['care_category'] == '涉核工程' and row['amount'] == 800

    # 非法保障状态回落已认定（白名单）
    resp = client.post('/youshu/api/save', data={
        'id': rid, 'name': '张优抚', 'id_card': ID_CARD,
        'care_category': '涉核工程', 'amount': '800', 'support_status': '随意',
    })
    row = next(x for x in client.get('/youshu/api/list').get_json()['data']['data']
               if x['id'] == rid)
    assert row['support_status'] == '已认定'

    # 删除
    resp = client.post('/youshu/api/delete/%d' % rid)
    assert resp.get_json()['success']
    assert client.get('/youshu/api/list').get_json()['data']['total'] == 0


# ------------------------------------------------------------------ D. 字典

def test_care_category_dict_seeded(isolated_app_factory):
    app = isolated_app_factory()
    with app.app_context():
        from models import DictCategory, DictItem
        cat = DictCategory.query.filter_by(name='优抚对象救助类别').first()
        assert cat is not None, '字典分类未播种（ensure_enum_dicts）'
        labels = [i.label for i in DictItem.query.filter_by(category_id=cat.id)
                  .order_by(DictItem.sort_order).all()]
        assert labels == ['因病退役', '因残退役', '涉核工程']


def test_js_consumes_dict_category():
    """表单/筛选下拉经 dictSelect 消费字典分类（源码断言；选项可改由字典管理承载）。"""
    js = open(os.path.join(ROOT, 'static', 'js', 'pages', 'youshu_list.js'),
              encoding='utf-8').read()
    assert "dictSelect('优抚对象救助类别'" in js
    assert js.count("dictSelect('优抚对象救助类别'") == 2  # 表单 + 筛选各一处


# ------------------------------------------------------------------ E. 导出

def test_export_masked_pipeline(isolated_app_factory):
    app = isolated_app_factory()
    _crypto_ready()
    client = app.test_client()
    _login(client)
    client.post('/youshu/api/save', data={
        'name': '李优抚', 'id_card': ID_CARD, 'phone': '13800002222',
        'care_category': '因病退役', 'amount': '500',
    })
    resp = client.get('/youshu/api/export')
    assert resp.status_code == 200
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(resp.data), read_only=True, data_only=True)
    ws = wb.active
    headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    assert headers[:2] == ['序号', '姓名'] and '身份证号' in headers
    wb.close()


# ------------------------------------------------------------------ F. 迁移

def test_migration_m043_idempotent(tmp_path):
    """m043 在带 dict 表的库上跑两次：首跑建表 + 3 字典项，二跑零新增。"""
    spec = importlib.util.spec_from_file_location(
        'm043_under_test', os.path.join(ROOT, 'scripts', 'migrations', 'm043_youshu.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    db_path = str(tmp_path / 'm043.db')
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE dict_categories (id INTEGER PRIMARY KEY, name TEXT,"
                 " description TEXT, is_active INTEGER, sort_order INTEGER, created_at DATETIME)")
    conn.execute("CREATE TABLE dict_items (id INTEGER PRIMARY KEY, category_id INTEGER,"
                 " label TEXT, value TEXT, sort_order INTEGER, is_active INTEGER,"
                 " created_at DATETIME)")
    conn.commit()
    conn.close()

    st1 = m.run(db_path)
    assert st1['table_created'] and st1['new_items'] == 3
    st2 = m.run(db_path)
    assert not st2['table_created'] and st2['new_categories'] == [] and st2['new_items'] == 0
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute('PRAGMA table_info(youshu)')]
    conn.close()
    assert 'id_card_enc' in cols and 'phone_hash' in cols
