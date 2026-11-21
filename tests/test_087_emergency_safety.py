# -*- coding: utf-8 -*-
"""tests/test_087_emergency_safety.py — 应急管理 + 综合安全检查结构化闭环（087，B-10）。

覆盖任务书验收 1/2/4/6 的可执行子集（轨 A 迁移幂等、闭环状态机、ADR-016 默认拒绝、
枚举种子、档案归档位）；验收 3（软表名册）095 已接入，契约面随 095 升级
（占位断言 → 真数据层，名册细节在 test_095_087_roster_closeout.py）；
验收 5（全量零回归）由验收环境全量 pytest 承担。

鉴权口径：新端点挂 safety_bp（ADR-016 BLUEPRINT_MODULES['safety']='safety' 已归类），
本文件同时锁「漏归反例」：无 safety.* 权限的角色访问新端点必须 403。
"""
import json
import re
import uuid

import pytest
from werkzeug.security import generate_password_hash

ADMIN_USER, ADMIN_PASS = 'admin', 'admin123'


def _login(app):
    client = app.test_client()
    r = client.get('/auth/login')
    m = re.search(r'name="csrf_token"[^>]*value="([^"]*)"', r.get_data(as_text=True))
    token = m.group(1) if m else ''
    client.post('/auth/login', data={'csrf_token': token, 'username': ADMIN_USER, 'password': ADMIN_PASS})
    return client


def _no_safety_perm_client(app):
    """漏归反例：显式 safety.view/edit = False 的操作员（v3 权限格式，默认拒绝）。"""
    from models import db, Role, User
    perms = {'schema_version': 3, 'safety.view': False, 'safety.edit': False}
    role_name = f'无安全权限员087-{uuid.uuid4().hex[:8]}'
    with app.app_context():
        role = Role(name=role_name, permissions=json.dumps(perms, ensure_ascii=False), is_system=False)
        db.session.add(role)
        db.session.commit()
        u = User(username=f'op_087_{uuid.uuid4().hex[:6]}', password_hash=generate_password_hash('x'),
                 role_id=role.id, is_active=True)
        db.session.add(u)
        db.session.commit()
        uid = u.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid
    return client


# ============ 页面结构（验收 1/2 的 SSR 面） ============

def test_emergency_page_structure(isolated_app_factory):
    """应急管理页：页头 + 三边界四卡（方案档案/检查闭环/队伍·物资名册 095 接入真数据层）。"""
    app = isolated_app_factory()
    client = _login(app)
    html = client.get('/safety/emergency').get_data(as_text=True)

    assert 'class="sc-page-head"' in html
    assert '应急管理' in html
    # 三边界：方案→档案入口；检查→闭环台账入口；队伍/物资→软表名册（095 接入，无表空态）
    assert '工作方案（档案归档）' in html and '/safety/' in html
    assert '综合安全检查（闭环台账）' in html and '/safety/checks' in html
    assert '工作队伍（名册）' in html and '储备物资（名册）' in html
    assert '/dynatable/upload' in html and '尚未建表' in html   # 095：空态引导（去建表）
    assert '待软表引擎' not in html                             # 095：占位徽章退役
    # 空库不出现名册表格（零伪造契约不变；有表态由 test_095_087_roster_closeout 锁定）
    assert 'sc-table' not in html


def test_checks_page_structure(isolated_app_factory):
    """检查台账页：spec-02 六件套 + 三弹窗（录入/闭环操作台/流转）+ 枚举服务端渲染。"""
    app = isolated_app_factory()
    client = _login(app)
    html = client.get('/safety/checks').get_data(as_text=True)

    assert 'class="sc-page-head"' in html
    assert 'class="sc-card"' in html and 'class="sc-toolbar"' in html
    assert 'class="sc-table"' in html and 'class="sc-pagination"' in html
    for modal_id in ('checkModal', 'workModal', 'flowModal'):
        assert f'id="{modal_id}"' in html
    # 枚举来自 dict_items（087 约束：模板不硬编码口径）
    assert '常规检查' in html and '上级督查' in html
    assert '重大' in html


# ============ 闭环状态机（验收 2：检查→隐患→整改可追踪） ============

def _create_check(client, title='五一节前消防安全检查', date='2026-04-29'):
    resp = client.post('/safety/api/checks', json={
        'title': title, 'check_date': date, 'check_type': '节假日检查',
        'location': '村部及辖区商铺', 'checker': '村两委',
    }).get_json()
    assert resp['success'], resp.get('msg')
    return resp['data']


def _add_hazard(client, check_id, desc, level='一般', **kw):
    payload = {'description': desc, 'level': level}
    payload.update(kw)
    resp = client.post(f'/safety/api/checks/{check_id}/hazards', json=payload).get_json()
    assert resp['success'], resp.get('msg')
    return resp['data']


def test_check_closed_loop_full_journey(isolated_app_factory):
    """闭环全旅程：登记→隐患→上报→驳回→重报→复核通过；检查单派生状态随之收敛。"""
    app = isolated_app_factory()
    client = _login(app)

    check = _create_check(client)
    cid = check['id']
    assert check['status'] == '已闭环' and check['hazard_total'] == 0  # 无隐患即闭环口径

    h1 = _add_hazard(client, cid, '灭火器过期未检', level='重大', rectify_owner='张三',
                     rectify_deadline='2026-05-10')
    _add_hazard(client, cid, '疏散通道堆物', level='一般')
    detail = client.get(f'/safety/api/checks/{cid}').get_json()['data']
    assert detail['status'] == '整改中' and detail['hazard_open'] == 2

    # 上报整改：待整改 → 待复核
    r = client.post(f'/safety/api/hazards/{h1["id"]}/rectify', json={'rectify_note': '已换新灭火器'}).get_json()
    assert r['success'] and r['data']['status'] == '待复核'
    # 复核驳回：待复核 → 待整改（意见留痕）
    r = client.post(f'/safety/api/hazards/{h1["id"]}/review', json={'action': 'reject', 'review_note': '未附照片'}).get_json()
    assert r['success'] and r['data']['status'] == '待整改' and r['data']['review_note'] == '未附照片'
    # 重报 + 通过：→ 已闭环
    client.post(f'/safety/api/hazards/{h1["id"]}/rectify', json={'rectify_note': '已换新并附照片'})
    r = client.post(f'/safety/api/hazards/{h1["id"]}/review', json={'action': 'pass', 'review_note': '核验属实'}).get_json()
    assert r['success'] and r['data']['status'] == '已闭环'

    detail = client.get(f'/safety/api/checks/{cid}').get_json()['data']
    assert detail['hazard_open'] == 1 and detail['status'] == '整改中'  # 另一条仍待整改
    # 闭环最后一条 → 检查单派生闭环
    h2 = detail['hazards'][1]
    client.post(f'/safety/api/hazards/{h2["id"]}/rectify', json={'rectify_note': '已清理'})
    client.post(f'/safety/api/hazards/{h2["id"]}/review', json={'action': 'pass', 'review_note': '通过'})
    detail = client.get(f'/safety/api/checks/{cid}').get_json()['data']
    assert detail['status'] == '已闭环' and detail['hazard_open'] == 0


def test_checks_list_endpoint(isolated_app_factory):
    """列表端点（补 087 回归缺口）：分页元组契约 + title 筛选 + 派生状态在场。"""
    app = isolated_app_factory()
    client = _login(app)
    _create_check(client, title='防火检查甲')
    _create_check(client, title='防汛检查乙')

    resp = client.get('/safety/api/checks?page=1&per_page=20').get_json()
    assert resp['success'] and resp['total'] == 2
    titles = {c['title'] for c in resp['data']}
    assert titles == {'防火检查甲', '防汛检查乙'}
    assert all('status' in c and 'hazard_open' in c for c in resp['data'])

    resp = client.get('/safety/api/checks?title=防汛').get_json()
    assert resp['success'] and resp['total'] == 1 and resp['data'][0]['title'] == '防汛检查乙'


def test_state_machine_guards(isolated_app_factory):
    """状态机守卫：非法迁移 / 缺必填 / 枚举外取值 / 日期格式 全部被拒。"""
    app = isolated_app_factory()
    client = _login(app)
    check = _create_check(client)
    h = _add_hazard(client, check['id'], '电线私拉乱接')

    # 非法迁移：待整改状态下不可直接复核
    r = client.post(f'/safety/api/hazards/{h["id"]}/review', json={'action': 'pass', 'review_note': 'x'}).get_json()
    assert not r['success'] and '不可复核' in r['msg']
    # 上报整改缺整改情况
    r = client.post(f'/safety/api/hazards/{h["id"]}/rectify', json={'rectify_note': ''}).get_json()
    assert not r['success']
    # 复核动作非法
    client.post(f'/safety/api/hazards/{h["id"]}/rectify', json={'rectify_note': '已整改'})
    r = client.post(f'/safety/api/hazards/{h["id"]}/review', json={'action': 'unknown', 'review_note': 'x'}).get_json()
    assert not r['success'] and 'pass 或 reject' in r['msg']
    # 隐患等级不在枚举内
    r = client.post(f"/safety/api/checks/{check['id']}/hazards", json={'description': 'x', 'level': '特级'}).get_json()
    assert not r['success'] and '枚举' in r['msg']
    # 检查单校验：缺名称 / 缺日期 / 日期格式错
    assert not client.post('/safety/api/checks', json={'title': '', 'check_date': '2026-01-01'}).get_json()['success']
    assert not client.post('/safety/api/checks', json={'title': 'x', 'check_date': ''}).get_json()['success']
    assert not client.post('/safety/api/checks', json={'title': 'x', 'check_date': '2026/01/01'}).get_json()['success']
    # R-087-06 收口：检查类型枚举外取值拒绝
    r = client.post('/safety/api/checks', json={'title': 'x', 'check_date': '2026-01-01', 'check_type': '突击检查'}).get_json()
    assert not r['success'] and '枚举' in r['msg']
    # 删除检查单级联隐患
    cid = check['id']
    assert client.delete(f'/safety/api/checks/{cid}').get_json()['success']
    assert not client.get(f'/safety/api/checks/{cid}').get_json()['success']


# ============ 鉴权（验收 4：ADR-016 三选一归类，漏归反例 403） ============

def test_adr016_default_deny_new_endpoints(isolated_app_factory):
    """无 safety.* 权限角色：新页面与全部新 API 一律 403（默认拒绝，漏归即拒绝）。"""
    app = isolated_app_factory()
    no_perm = _no_safety_perm_client(app)
    # 页面（GET → safety.view）
    assert no_perm.get('/safety/emergency').status_code == 403
    assert no_perm.get('/safety/checks').status_code == 403
    # API 读（GET → safety.view）
    assert no_perm.get('/safety/api/checks').status_code == 403
    assert no_perm.get('/safety/api/emergency/summary').status_code == 403
    # API 写（POST/DELETE → safety.edit）
    assert no_perm.post('/safety/api/checks', json={'title': 'x', 'check_date': '2026-01-01'}).status_code == 403
    assert no_perm.post('/safety/api/checks/1/hazards', json={'description': 'x'}).status_code == 403
    assert no_perm.post('/safety/api/hazards/1/rectify', json={'rectify_note': 'x'}).status_code == 403
    assert no_perm.post('/safety/api/hazards/1/review', json={'action': 'pass', 'review_note': 'x'}).status_code == 403
    assert no_perm.delete('/safety/api/checks/1').status_code == 403


# ============ 种子与幂等（轨 A m028 + 验收 1 方案归档位） ============

def test_m028_seeds_and_idempotent(isolated_app_factory):
    """枚举分类/条目与档案目录按守卫种子落位；重复执行 m028 零重复。"""
    app = isolated_app_factory()
    client = _login(app)
    client.get('/safety/emergency')  # 触发蓝图级幂等种子

    from models import db
    from models.dict_model import DictCategory, DictItem
    from models.safety import SafetyNode
    with app.app_context():
        cat1 = DictCategory.query.filter_by(name='综合安全检查类型').one()
        cat2 = DictCategory.query.filter_by(name='安全隐患等级').one()
        assert DictItem.query.filter_by(category_id=cat1.id).count() == 4
        assert DictItem.query.filter_by(category_id=cat2.id).count() == 3
        folders = SafetyNode.query.filter_by(parent_id=None, name='应急工作方案').all()
        assert len(folders) == 1

        # 幂等：直接重跑 m028 → 计数不变
        from routes.safety_emergency import _apply_m028
        _apply_m028()
        _apply_m028()
        assert DictItem.query.filter_by(category_id=cat1.id).count() == 4
        assert DictItem.query.filter_by(category_id=cat2.id).count() == 3
        assert len(SafetyNode.query.filter_by(parent_id=None, name='应急工作方案').all()) == 1

        # 方案归档位可达（验收 1：方案走档案）
        node = SafetyNode.query.filter_by(parent_id=None, name='应急工作方案').first()
        assert node.node_type == 'folder'


def test_emergency_summary_counts(isolated_app_factory):
    """应急管理页概览接口：检查/隐患计数与派生闭环口径一致。"""
    app = isolated_app_factory()
    client = _login(app)
    check = _create_check(client, title='汛前检查', date='2026-04-01')
    _add_hazard(client, check['id'], '排水沟淤堵')

    d = client.get('/safety/api/emergency/summary').get_json()['data']
    assert d['total_checks'] == 1
    assert d['open_hazards'] == 1 and d['closed_hazards'] == 0
    assert d['archive_folder_id']  # 方案归档目录就位

    # 闭环后计数迁移
    h = client.get(f"/safety/api/checks/{check['id']}").get_json()['data']['hazards'][0]
    client.post(f"/safety/api/hazards/{h['id']}/rectify", json={'rectify_note': '已清淤'})
    client.post(f"/safety/api/hazards/{h['id']}/review", json={'action': 'pass', 'review_note': '通过'})
    d = client.get('/safety/api/emergency/summary').get_json()['data']
    assert d['open_hazards'] == 0 and d['closed_hazards'] == 1
