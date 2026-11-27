# -*- coding: utf-8 -*-
"""054 代码复核回归：医保字段目录驱动 + 旧模板导入兼容。

覆盖复核报告（docs/tasks/review-054-20261027.md）：
- P1-3 字段零写死：表单/列表/导出统一由字段目录派生，职工字段不得混入居民
- P1-4 电话字段：目录必须含 contact_phone/mobile_phone 并声明 sensitive；
         旧模板 22 列表头（含「身份证号码」「是否已缴纳」别名）必须全部映射，不得静默丢弃
- P2-7 ext_data 动态字段：目录声明 sensitive 者必须加密落库，不得绕过 R12
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from utils import medical_field_catalog as mfc

ID_CARD = '110101199001011234'

# 054 之前导入模板的 22 列表头（与 routes/medical_insurance.py 原硬编码表头同序）
LEGACY_HEADERS = [
    '序号', '姓名', '身份证号码', '性别', '住址', '所在组', '联系电话', '手机号码',
    '缴费年度', '户主姓名', '户属性', '参保情况', '是否已缴纳', '是否国家代缴',
    '国家代缴身份', '是否可核减', '核减原因', '是否异地搬迁', '导入批次',
    '核对状态', '异常说明', '备注',
]
LEGACY_ROW = [
    '1', '张三', ID_CARD, '男', '思榜村一组', '思榜', '13800138000', '13900139000',
    '2026', '张三', '一般农户', '正常参保', '否', '否',
    '', '否', '', '否', '批次A',
    '未核对', '', '',
]


def _make_app(tmp_path, monkeypatch):
    import config
    db_path = str(tmp_path / 'shucun.db')
    for k, v in {
        'SHUCUN_DB_PATH': db_path,
        'SHUCUN_TEMP_DIR': str(tmp_path / 'temp'),
        'SHUCUN_UPLOAD_DIR': str(tmp_path / 'uploads'),
        'SHUCUN_BACKUP_DIR': str(tmp_path / 'backups'),
        'SHUCUN_DATA_DIR': str(tmp_path / 'data'),
        'SHUCUN_LAN_AUTHORIZED': '1',
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, 'DB_PATH', db_path)
    monkeypatch.setattr(config, 'TEMP_DIR', str(tmp_path / 'temp'))
    monkeypatch.setattr(config.Config, 'SQLALCHEMY_DATABASE_URI',
                        f'sqlite:///{db_path}?check_same_thread=False')
    monkeypatch.setattr(config.Config, 'IMPORTS_DIR', str(tmp_path / 'temp' / 'imports'))
    from app import create_app
    application = create_app()
    application.config['TESTING'] = True
    return application


def _unlock_keyholder():
    from utils.crypto import generate_salt, key_holder
    if not key_holder.is_initialized:
        key_holder.initialize('测试口令054', salt=generate_salt())


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1
    return client


def _legacy_xlsx(headers, row):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ═══════════════ 目录本身（纯函数，无需 app） ═══════════════

class TestFieldCatalog:
    def test_phone_fields_in_common_with_sensitive(self):
        """P1-4：电话字段必须在目录且声明 sensitive，否则新模板丢列、旧模板导入丢值。"""
        m = mfc.field_map('resident')
        for key in ('contact_phone', 'mobile_phone'):
            assert key in m, f'{key} 未纳入字段目录'
            assert m[key]['sensitive'] == 'phone', f'{key} 未声明 sensitive'

    def test_legacy_template_headers_all_mapped(self):
        """P1-4：旧模板 22 列必须全部映射（别名『身份证号码』『是否已缴纳』尤其关键）。"""
        mapping = mfc.label_to_key_map('resident')
        missing = [h for h in LEGACY_HEADERS if not mapping.get(h)]
        assert missing == [], f'旧模板表头未映射：{missing}'

    def test_alias_points_to_right_key(self):
        mapping = mfc.label_to_key_map('resident')
        assert mapping['身份证号码'] == 'id_card'
        assert mapping['是否已缴纳'] == 'is_paid'
        assert mapping['身份证号'] == 'id_card'

    def test_type_isolation_no_cross_type_leak(self):
        """P1-3：职工/其他专属字段不得出现在居民表单，反之亦然（居民 tab 曾渲染两份同名字段）。"""
        res = {f['key'] for f in mfc.fields_for('resident')}
        emp = {f['key'] for f in mfc.fields_for('employee')}
        assert 'village_group' in res and 'village_group' not in emp
        assert 'work_unit' in emp and 'work_unit' not in res
        # 同一分类内不得重复 key（否则表单渲染出两份）
        for t in mfc.INSURANCE_TYPE_VALUES:
            keys = [f['key'] for f in mfc.fields_for(t)]
            assert len(keys) == len(set(keys)), f'{t} 分类字段重复：{keys}'

    def test_required_and_default_come_from_catalog(self):
        """P1-3：必填与默认值必须由目录声明（表单已不再硬编码，前端只能读目录）。"""
        m = mfc.field_map('resident')
        assert m['name']['required'] is True
        assert m['id_card']['required'] is True
        for key in ('is_paid', 'is_state_paid', 'is_deductible', 'is_relocated'):
            assert m[key]['options'] == ['是', '否']
            assert m[key]['default'] == '否', f'{key} 默认值应为「否」（原模板 selected 项）'

    def test_every_type_has_list_columns(self):
        """P1-3：每个分类都要有可展示的列表列，否则切 tab 后表格空白。"""
        for t in mfc.INSURANCE_TYPE_VALUES:
            cols = [f for f in mfc.fields_for(t) if f['in_list']]
            assert cols, f'{t} 无列表列'

    def test_ext_data_sensitive_registry_exists(self):
        """P2-7：ext_data 敏感字段登记表存在（当前无敏感动态字段，故为空）。"""
        assert isinstance(mfc.SENSITIVE_EXT_KEYS, dict)


# ═══════════════ 导入链路（端到端） ═══════════════

class TestLegacyImport:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        application = _make_app(tmp_path, monkeypatch)
        _unlock_keyholder()
        return application

    def test_legacy_template_import_keeps_phones(self, app):
        """P1-4：旧模板导入后电话列必须落库，且未识别表头为空。"""
        client = _admin_client(app)
        resp = client.post(
            '/medical-insurance/api/import',
            data={'insurance_type': 'resident', 'import_batch': '回归批次',
                  'file': (_legacy_xlsx(LEGACY_HEADERS, LEGACY_ROW), 'legacy.xlsx')},
            content_type='multipart/form-data',
        )
        body = resp.get_json()
        assert body['success'], body
        assert body['data']['imported'] == 1, body
        assert body['data']['unknown_headers'] == [], (
            f'旧模板表头被静默丢弃：{body["data"]["unknown_headers"]}')

        from models import MedicalInsurance, db
        from routes.medical_insurance import _decrypt
        with app.app_context():
            item = MedicalInsurance.query.filter_by(name='张三').first()
            assert item is not None
            assert _decrypt(item, 'contact_phone') == '13800138000'
            assert _decrypt(item, 'mobile_phone') == '13900139000'
            assert item.village_group == '思榜'
            assert item.insurance_type == 'resident'

    def test_unknown_header_reported_not_dropped(self, app):
        """P1-4：无法识别的表头必须在返回里告警，不得静默跳过。"""
        client = _admin_client(app)
        headers = LEGACY_HEADERS + ['外星列']
        row = LEGACY_ROW + ['x']
        resp = client.post(
            '/medical-insurance/api/import',
            data={'insurance_type': 'resident',
                  'file': (_legacy_xlsx(headers, row), 'unknown.xlsx')},
            content_type='multipart/form-data',
        )
        body = resp.get_json()
        assert body['success'], body
        assert body['data']['unknown_headers'] == ['外星列'], body['data']

    def test_new_template_headers_have_single_seq_no(self, app):
        """054-R2 P2-1：序号已由目录提供，模板不得再显式加一列，避免双序号覆盖导入值。"""
        import openpyxl
        client = _admin_client(app)
        for itype, expected, forbidden in (
                ('resident', '所在组', '工作单位'),
                ('employee', '工作单位', '所在组')):
            resp = client.get(f'/medical-insurance/api/template?insurance_type={itype}')
            assert resp.status_code == 200, (itype, resp.status_code)
            wb = openpyxl.load_workbook(io.BytesIO(resp.data))
            headers = [c.value for c in wb.active[1]]
            assert headers.count('序号') == 1, f'{itype} 模板序号重复：{headers}'
            assert len(headers) == len(set(headers)), f'{itype} 模板表头重复：{headers}'
            assert headers[-1] == '保险类型', headers[-3:]
            assert expected in headers and forbidden not in headers, (itype, headers)


# ═══════════════ ext_data 敏感字段加密（P2-7） ═══════════════

class TestExtDataEncryption:
    @pytest.fixture()
    def app(self, tmp_path, monkeypatch):
        application = _make_app(tmp_path, monkeypatch)
        _unlock_keyholder()
        return application

    def test_sensitive_ext_key_is_encrypted_at_rest(self, app):
        """P2-7：目录把某动态字段声明为 sensitive 后，落库必须是密文、读出必须还原。"""
        from routes.medical_insurance import _ext_get, _merge_ext
        from utils import medical_field_catalog as catalog

        original = dict(catalog.SENSITIVE_EXT_KEYS)
        catalog.SENSITIVE_EXT_KEYS['work_unit'] = 'phone'
        try:
            with app.app_context():
                ext = _merge_ext({}, lambda k: {'work_unit': '某公司',
                                                'payment_base': '8000'}.get(k, ''))
                assert ext['work_unit'] != '某公司', '敏感动态字段被明文落库（绕过 R12）'
                assert ext['work_unit'].startswith('gAAAAA'), ext['work_unit']
                assert ext['payment_base'] == '8000', '非敏感字段不应被加密'
                assert _ext_get(ext, 'work_unit') == '某公司'
                assert _ext_get(ext, 'payment_base') == '8000'
        finally:
            catalog.SENSITIVE_EXT_KEYS.clear()
            catalog.SENSITIVE_EXT_KEYS.update(original)

    def test_no_sensitive_declaration_keeps_plaintext(self, app):
        """P2-7 反向对照：未声明 sensitive 时保持明文（与改造前行为一致）。"""
        from routes.medical_insurance import _merge_ext
        with app.app_context():
            ext = _merge_ext({}, lambda k: {'work_unit': '某公司'}.get(k, ''))
            assert ext['work_unit'] == '某公司'
