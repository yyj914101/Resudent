# -*- coding: utf-8 -*-
"""tests/test_082_b_required_fields.py — 082 B 必补字段回归（B-1/B-2/B-3）。

覆盖：
- B-1 医保待遇认证状态：目录/字典/录入/列表/筛选/导出/导入/与核对状态隔离
- B-2 救助动态状态：正常/停发/新增录入、非法值拒绝、列表筛选、冲正不变
- B-3 退役重点群体：录入/列表/详情/筛选/导出/模板/导入/空值
- 迁移 m026 幂等 + 保守回填；字典三源一致；SQL 级聚合就绪
"""
import io
import os
import re
import sqlite3
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DICT_EXPECTED = {
    '医保待遇认证状态': ['已认证', '未认证', '待认证'],
    '救助动态状态': ['正常', '停发', '新增'],
    '退役重点群体': ['参战参试', '带病回乡', '伤残军人', '烈士遗属',
                     '因公牺牲军人遗属', '病故军人遗属', '在乡老复员军人', '其他'],
}
ID_A = '110101199001011234'
ID_B = '110101199202022345'
ID_C = '110101198803033456'


def _unlock_keyholder():
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令082', salt=generate_salt())


@pytest.fixture()
def app(isolated_app_factory):
    application = isolated_app_factory()
    _unlock_keyholder()
    return application


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def _xlsx(headers, row):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _headers(resp):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    return [c.value for c in wb.active[1]]


def _rows(resp):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(resp.data))
    return [list(r) for r in wb.active.iter_rows(min_row=2, values_only=True)]


def _load_m026():
    import importlib.util
    path = os.path.join(BASE, 'scripts', 'migrations', 'm026_b_required_fields.py')
    spec = importlib.util.spec_from_file_location('m026_b_required_fields', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ═══════════════ 字典三源一致 + 迁移 ═══════════════

class TestDictAndMigration:
    def test_seed_constant_matches_closed_values(self):
        # 082 B 三分类已收敛进 DICT_SEEDS（唯一真源），不再有独立 B_REQUIRED_DICT_SEEDS
        from utils.dict_seed import DICT_SEEDS
        for category, values in DICT_EXPECTED.items():
            assert DICT_SEEDS[category] == values, f'{category} 种子不一致'

    def test_three_seed_sources_same_literal(self):
        # 收敛后：三分类字面量唯一真源为 DICT_SEEDS，init_db.py / app.py 首装不再重复字面量
        from utils.dict_seed import DICT_SEEDS
        for category, values in DICT_EXPECTED.items():
            assert DICT_SEEDS[category] == values, f'{category} 种子不一致'
        for name in ('init_db.py', 'app.py'):
            text = open(os.path.join(BASE, name), encoding='utf-8').read()
            for category, values in DICT_EXPECTED.items():
                literal = "'%s': [%s]," % (
                    category, ','.join("'%s'" % v for v in values))
                assert literal not in text, f'{name} 仍含 {category} 首装字面量（应唯一真源 DICT_SEEDS）'

    def test_app_db_has_three_categories(self, app):
        from models import DictCategory, DictItem, db
        with app.app_context():
            for category, values in DICT_EXPECTED.items():
                cat = DictCategory.query.filter_by(name=category).first()
                assert cat is not None, f'缺字典分类 {category}'
                got = [i.value for i in DictItem.query.filter_by(
                    category_id=cat.id).order_by(DictItem.sort_order).all()]
                assert got == values, f'{category} 取值不一致：{got}'
            # 幂等补录二次执行不重复（三分类已并入 DICT_SEEDS，统一走 ensure_enum_dicts）
            from utils.dict_seed import ensure_enum_dicts
            assert ensure_enum_dicts() == (0, 0)

    def test_m026_idempotent_and_conservative_backfill(self, tmp_path):
        db_path = str(tmp_path / 'legacy.db')
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE medical_insurance_payments (id INTEGER PRIMARY KEY, name TEXT)')
        conn.execute('CREATE TABLE veteran (id INTEGER PRIMARY KEY, name TEXT)')
        conn.execute("INSERT INTO medical_insurance_payments (name) VALUES ('旧医保')")
        conn.execute("INSERT INTO veteran (name) VALUES ('旧退役')")
        conn.commit()
        conn.close()

        mod = _load_m026()
        first = mod.run(db_path)
        assert sorted(first['added_columns']) == ['benefit_cert_status', 'key_group_category']
        second = mod.run(db_path)
        assert second['added_columns'] == []
        assert sorted(second['skipped_columns']) == ['benefit_cert_status', 'key_group_category']

        conn = sqlite3.connect(db_path)
        b1 = conn.execute('SELECT benefit_cert_status FROM medical_insurance_payments').fetchone()[0]
        b3 = conn.execute('SELECT key_group_category FROM veteran').fetchone()[0]
        conn.close()
        assert b1 == '未认证', 'B-1 存量默认回填应为未认证'
        assert b3 is None, 'B-3 存量不得猜类别（应保持 NULL）'


# ═══════════════ B-1 医保待遇认证状态 ═══════════════

class TestB1BenefitCert:
    def test_catalog_contains_field_and_semantics(self):
        from utils import medical_field_catalog as mfc
        f = mfc.field_map('resident')['benefit_cert_status']
        assert f['label'] == '待遇认证状态'
        assert f['dict'] == '医保待遇认证状态'
        assert f['in_list'] is True
        # 与核对/参保字段并存，不互相替代
        m = mfc.field_map('resident')
        assert m['verify_status']['label'] == '核对状态'
        assert m['insurance_status']['label'] == '参保情况'

    def test_save_list_filter_and_export(self, app):
        client = _admin_client(app)
        resp = client.post('/medical-insurance/api/save', data={
            'name': '甲', 'id_card': ID_A, 'insurance_type': 'resident',
            'payment_year': '2026', 'benefit_cert_status': '已认证',
            'insurance_status': '正常参保', 'verify_status': '未核对',
        })
        body = resp.get_json()
        assert body['success'], body

        listed = client.get('/medical-insurance/api/list?benefit_cert_status=已认证').get_json()
        assert listed['success']
        row = next(x for x in listed['data'] if x['name'] == '甲')
        assert row['benefit_cert_status'] == '已认证'
        assert row['verify_status'] == '未核对'
        assert row['insurance_status'] == '正常参保'

        other = client.get('/medical-insurance/api/list?benefit_cert_status=待认证').get_json()
        assert all(x['name'] != '甲' for x in other['data'])

        export = client.get('/medical-insurance/api/export?benefit_cert_status=已认证')
        assert export.status_code == 200
        assert '待遇认证状态' in _headers(export)

    def test_template_and_import(self, app):
        client = _admin_client(app)
        tpl = client.get('/medical-insurance/api/template?insurance_type=resident')
        assert '待遇认证状态' in _headers(tpl)

        resp = client.post('/medical-insurance/api/import', data={
            'insurance_type': 'resident',
            'file': (_xlsx(['姓名', '身份证号码', '缴费年度', '待遇认证状态'],
                          ['乙', ID_B, '2026', '待认证']), 'b1.xlsx'),
        }, content_type='multipart/form-data')
        body = resp.get_json()
        assert body['success'], body
        from models import MedicalInsurance, db
        with app.app_context():
            item = MedicalInsurance.query.filter_by(name='乙').first()
            assert item is not None and item.benefit_cert_status == '待认证'


# ═══════════════ B-2 救助动态状态 ═══════════════

class TestB2SubsidyStatus:
    @staticmethod
    def _seed(app):
        from models import Resident, SubsidyProject, db
        with app.app_context():
            r = Resident(name='丙', id_card=ID_C, status='normal')
            p = SubsidyProject(name='临时救助', project_type='其他', cycle='月',
                               status='启用', target_tags='[]')
            db.session.add_all([r, p])
            db.session.commit()
            return r.id, p.id

    def test_add_new_status_filter_and_invalid(self, app):
        rid, pid = self._seed(app)
        client = _admin_client(app)
        ok = client.post('/subsidy/api/record/add', data={
            'resident_id': rid, 'project_id': pid, 'period': '2026-11',
            'amount': '100', 'status': '新增',
        }).get_json()
        assert ok['success'], ok

        bad = client.post('/subsidy/api/record/add', data={
            'resident_id': rid, 'project_id': pid, 'period': '2026-11',
            'amount': '100', 'status': '在保',
        }).get_json()
        assert not bad['success']
        from models import SubsidyRecord, db
        with app.app_context():
            assert SubsidyRecord.query.filter_by(status='在保').count() == 0

        listed = client.get('/subsidy/api/record/list?status=新增').get_json()
        assert listed['success'] and any(x['status'] == '新增' for x in listed['data'])
        normal = client.get('/subsidy/api/record/list?status=正常').get_json()
        assert all(x['status'] != '新增' for x in normal['data'])

    def test_reverse_keeps_normal(self, app):
        rid, pid = self._seed(app)
        client = _admin_client(app)
        ok = client.post('/subsidy/api/record/add', data={
            'resident_id': rid, 'project_id': pid, 'period': '2026-11',
            'amount': '100', 'status': '新增',
        }).get_json()
        assert ok['success']
        rev = client.post('/subsidy/api/record/reverse-save',
                          data={'id': ok['data']['id']}).get_json()
        assert rev['success'], rev
        from models import SubsidyRecord, db
        with app.app_context():
            rec = SubsidyRecord.query.get(rev['data']['id'])
            assert rec.status == '正常' and rec.source == 'reverse'


# ═══════════════ B-3 退役重点群体 ═══════════════

VET_FORM = {
    'name': '丁', 'id_card': '110101199505055678', 'gender': '男', 'nation': '汉族',
    'politics_status': '群众', 'education': '初中', 'registered_residence': '思榜村',
    'grid': '思榜', 'home_address': '思榜村一组', 'phone': '13800138000',
    'relative_name': '戊', 'relative_phone': '13900139000',
    'enlist_date': '2012-01', 'discharge_date': '2015-01', 'service_years': '3',
    'service_unit': '某部', 'service_branch': '陆军', 'service_rank': '班长',
    'discharge_cert_no': 'X001', 'household_reg_type': '农业户口',
    'placement_status': '自主就业', 'key_group_category': '参战参试', 'remark': '',
}


class TestB3VeteranKeyGroup:
    def test_save_list_detail_filter_export_template(self, app):
        client = _admin_client(app)
        ok = client.post('/veteran/api/save', data=VET_FORM).get_json()
        assert ok['success'], ok

        listed = client.get('/veteran/api/list?key_group_category=参战参试').get_json()
        assert listed['success']
        assert any(x['key_group_category'] == '参战参试' for x in listed['data']['data'])
        other = client.get('/veteran/api/list?key_group_category=烈士遗属').get_json()
        assert all(x['key_group_category'] != '参战参试' for x in other['data']['data'])

        d = client.get('/veteran/api/detail/%d' % ok['data']['id']).get_json()
        assert d['data']['key_group_category'] == '参战参试'

        export = client.get('/veteran/api/export?key_group_category=参战参试')
        headers = _headers(export)
        assert '重点群体类别' in headers
        assert headers.index('重点群体类别') == headers.index('安置情况') + 1
        # P1-1 守卫：表头列数必须等于每行值数，且新字段值不得错位
        rows = _rows(export)
        assert rows, '导出无数据行'
        assert all(len(r) == len(headers) for r in rows), (
            '导出列错位：表头 %d 列 / 行值 %s' % (len(headers), [len(r) for r in rows]))
        row = next(r for r in rows if r[headers.index('姓名')] == '丁')
        assert row[headers.index('所在网格')] == '思榜'
        assert row[headers.index('安置情况')] == '自主就业'
        assert row[headers.index('重点群体类别')] == '参战参试'

        tpl = client.get('/veteran/api/template')
        assert '重点群体类别' in _headers(tpl)

    def test_import_and_empty_value(self, app):
        client = _admin_client(app)
        resp = client.post('/veteran/api/import', data={
            'mode': 'append',
            'file': (_xlsx(['姓名', '身份证号码', '重点群体类别'],
                          ['己', '110101199606066789', '带病回乡']), 'b3.xlsx'),
        }, content_type='multipart/form-data')
        body = resp.get_json()
        assert body['success'], body
        from models import Veteran, db
        with app.app_context():
            v = Veteran.query.filter_by(name='己').first()
            assert v is not None and v.key_group_category == '带病回乡'
            # 空值：非重点群体，不报错
            v2 = Veteran(name='庚', key_group_category=None)
            db.session.add(v2)
            db.session.commit()
        listed = client.get('/veteran/api/list?name=庚').get_json()
        assert listed['success'] and listed['data']['data'][0]['key_group_category'] == ''

    def test_sql_aggregation_ready(self, app):
        client = _admin_client(app)
        assert client.post('/veteran/api/save', data=VET_FORM).get_json()['success']
        from models import Veteran, db
        from sqlalchemy import func
        with app.app_context():
            rows = db.session.query(Veteran.key_group_category, func.count(Veteran.id)).all()
            counts = {k or '': n for k, n in rows}
            assert counts.get('参战参试', 0) >= 1
            total = sum(counts.values())
            assert total == Veteran.query.count()
