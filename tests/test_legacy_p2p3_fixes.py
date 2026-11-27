# -*- coding: utf-8 -*-
"""066：P2/P3 遗留修复回归（GLB-44/46/47/49 + AUD-04/12/16/19 + GLB-42 校验位出口）。

覆盖口径见 docs/tasks/066-legacy-p2p3-cleanup.md 与 遗留问题核查报告-20261029.md。
"""
import json
import os
from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def app(isolated_app_factory):
    return isolated_app_factory()


def _admin(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def _clerk(app, username='clerk066'):
    from models import db, Role, User
    with app.app_context():
        role = Role(name='066只读角色', permissions=json.dumps({
            'dict.view': True, 'resident.view': True,
        }))
        db.session.add(role)
        db.session.commit()
        user = User(username=username, password_hash='x', role_id=role.id, is_active=True)
        db.session.add(user)
        db.session.commit()
        uid = user.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid
    return client


# ---------------- GLB-44：特困供养字典种子 ----------------

def test_care_support_dict_seeded_and_idempotent(app):
    from models import DictCategory, DictItem
    from utils.dict_seed import CARE_SUPPORT_CATEGORIES, ensure_care_support_dict

    with app.app_context():
        for name, labels in CARE_SUPPORT_CATEGORIES.items():
            cat = DictCategory.query.filter_by(name=name).first()
            assert cat is not None, '启动守卫应已补录分类：%s' % name
            got = {i.label for i in DictItem.query.filter_by(category_id=cat.id).all()}
            assert set(labels) <= got, '分类 %s 缺项：%s' % (name, set(labels) - got)
        # 幂等：再跑一次不得新增
        assert ensure_care_support_dict() == (0, 0)


# ---------------- GLB-46：儿童详情出生日期 ----------------

def test_child_serialize_includes_birth_date(app):
    from models import db, UnsupportedChild
    from routes.care import _serialize_child

    with app.app_context():
        row = UnsupportedChild(name='测试儿童066', birth_date=date(2015, 3, 2))
        db.session.add(row)
        db.session.commit()
        data = _serialize_child(row)
        assert data['birth_date'] == '2015-03-02'


# ---------------- GLB-47：公益岗导出按身份证过滤 ----------------

def _add_welfare_job(app, name, id_card_upper):
    from models import db, WelfareJob
    from utils.crypto import sha256_hash

    with app.app_context():
        job = WelfareJob(name=name, job_type='保洁员',
                         id_card_hash=sha256_hash(id_card_upper))
        db.session.add(job)
        db.session.commit()


def test_welfare_export_filters_by_id_card(app):
    target = '110101199001011237'
    other = '110101199002022341'
    _add_welfare_job(app, '目标人', target)
    _add_welfare_job(app, '其他人', other)

    client = _admin(app)
    resp = client.get('/welfare/api/export?id_card=' + target)
    assert resp.status_code == 200
    wb = load_workbook(BytesIO(resp.data), read_only=True)
    rows = list(wb.active.iter_rows(values_only=True))
    assert len(rows) == 2, '导出应只含 1 条匹配记录（表头 + 1 行）'
    assert rows[1][1] == '目标人'


# ---------------- GLB-49：缺列/缺表映射迁移 ----------------

@pytest.mark.parametrize('message,expected', [
    ('no such column: import_batch.mode', 'scripts/migrations/m013_import_batch_mode.sql'),
    ('no such table: import_profiles', 'scripts/migrations/m014_import_profiles.sql'),
    ('no such column: certificate_templates.field_schema', 'scripts/migrations/m015_certificate_full.py'),
    ('no such column: medical_insurance.insurance_type', 'scripts/migrations/m021_medical_insurance_type.py'),
    ('no such column: users.last_active', 'scripts/migrations/m022_user_last_active.py'),
    ('no such table: weird_table', None),
])
def test_migration_hint_mapping(message, expected):
    from app import _migration_script_for_error

    assert _migration_script_for_error(message) == expected


# ---------------- AUD-04：首页退役军人计数 ----------------

def test_dashboard_api_exposes_veteran_count(app):
    resp = _admin(app).get('/dashboard/api/dashboard/stats')
    assert resp.status_code == 200
    data = resp.get_json()['data']
    assert 'veteran_count' in data, '首页第 5 格依赖 veteran_count 聚合键'


# ---------------- AUD-12 / AUD-06：样式裸值收敛 ----------------

def test_badge_and_quick_entry_literals_moved_to_tokens():
    comp = open(os.path.join(BASE, 'static', 'css', 'components.css'), encoding='utf-8').read()
    for literal in ('#FDECEC', '#DBEAFE', '#2563EB', '#E0F2FE', '#0891B2'):
        assert literal not in comp
    app_css = open(os.path.join(BASE, 'static', 'css', 'app.css'), encoding='utf-8').read()
    for literal in ('#1f66c1', '#eef5ff', '#edf1f5'):
        assert literal not in app_css


# ---------------- AUD-16：非 admin 不渲染写操作按钮 ----------------

def test_dict_page_hides_write_buttons_for_clerk(app):
    admin_html = _admin(app).get('/dict/').get_data(as_text=True)
    assert 'showCategoryAdd()' in admin_html
    assert 'showItemAdd()' in admin_html

    clerk_html = _clerk(app).get('/dict/').get_data(as_text=True)
    assert 'showCategoryAdd()' not in clerk_html
    assert 'showItemAdd()' not in clerk_html


# ---------------- AUD-19：审批页显式登录兜底 ----------------

def test_registry_page_login_decorator_declared():
    src = open(os.path.join(BASE, 'routes', 'resident_enhance.py'), encoding='utf-8').read()
    assert "@resident_enhance_bp.route('/registry')\n@login_required" in src


# ---------------- GLB-42：身份证校验位错误出口 ----------------

def test_id_card_checksum_error_contract():
    from utils.import_engine import id_card_checksum_error

    assert id_card_checksum_error('110101199001011237') is None
    assert '校验位' in id_card_checksum_error('110101199001011234')
    assert '15 位' in id_card_checksum_error('110101900101123')
