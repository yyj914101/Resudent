# -*- coding: utf-8 -*-
"""054 代码复核回归：自定义下拉的必填拦截/值同步（源码级守卫）+ 迁移幂等 + 字段零写死。

为什么用源码守卫：P1-1/P1-2 是纯浏览器行为（HTML5 校验中止时序、form.reset 不派发 change），
pytest 无法驱动；但「拦截点挂在哪、有没有写死字段清单」是结构问题，源码断言能可靠守住回归。
"""
import importlib.util
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
        return fh.read()


class TestRequiredGuard:
    """P1-1：required 的原生 select 被 display:none 后，浏览器在 submit 之前静默中止
    且**不派发 submit 事件**（实测 console 报 "invalid form control is not focusable"），
    所以拦截点必须是 click / keydown，不能是 submit。"""

    def test_guard_exists(self):
        src = _read('static/js/app.js')
        assert 'function csGuardRequired' in src
        assert 'checkValidity' in src
        assert 'is-invalid' in src, '必须给触发框可见错误态'

    def test_hooks_click_and_enter(self):
        src = _read('static/js/app.js')
        assert "addEventListener('click'" in src, '未拦截提交按钮点击'
        assert "addEventListener('keydown'" in src, '未拦截输入框回车提交'

    def test_does_not_rely_on_submit_event(self):
        src = _read('static/js/app.js')
        assert "addEventListener('submit'" not in src, (
            '原生校验会在 submit 之前中止且不派发事件，挂 submit 拦不住（054 复核 P1-1）')


class TestValueSync:
    """P1-2：form.reset() 与程序化 .val() 都不派发 change，触发框文本会与提交值分叉。"""

    def test_refresh_and_reset_channels(self):
        src = _read('static/js/app.js')
        assert "'change.cs refresh.cs'" in src, '缺少 refresh.cs 显式同步通道'
        assert 'csRefresh' in src, '未暴露统一刷新入口'
        assert "reset.cs" in src, '未监听表单 reset'

    def test_open_refreshes_as_fallback(self):
        src = _read('static/js/app.js')
        open_body = src.split('function open()', 1)[1].split('}', 1)[0]
        assert 'refreshTrigger' in open_body, '展开浮层时应先按真值刷新触发框'

    def test_callsite_explicit_sync(self):
        """点名修复的两处程序化赋值必须显式同步。"""
        dues = _read('static/js/pages/party_dues.js')
        assert "$('#batchStartMonth').val('1').trigger('refresh.cs')" in dues
        veteran = _read('static/js/pages/veteran_list.js')
        assert veteran.count("select.cs-native').trigger('refresh.cs')") >= 2

    def test_identity_lookup_and_medical_setfield_sync(self):
        """054-R2 P2-2：身份证联动/医保回填的 .val() 后必须刷新触发框。"""
        resident = _read('static/js/pages/resident_detail_modal.js')
        assert "select[name=\"gender\"]').val(genderCode % 2 === 1 ? '男' : '女')" in resident
        assert ".trigger('refresh.cs')" in resident
        medical = _read('static/js/pages/medical_insurance.js')
        assert "$f.hasClass('cs-native')" in medical
        assert "$f.trigger('refresh.cs')" in medical


class TestMigrationIdempotent:
    """P1-5：m021 原为裸 ALTER，二次执行报 duplicate column；现为 PRAGMA 检查式 .py。"""

    def test_sql_replaced_by_py(self):
        assert not os.path.exists(os.path.join(ROOT, 'scripts/migrations',
                                               'm021_medical_insurance_type.sql'))
        assert os.path.exists(os.path.join(ROOT, 'scripts/migrations',
                                           'm021_medical_insurance_type.py'))

    def test_run_twice_no_error(self, tmp_path):
        spec = importlib.util.spec_from_file_location(
            'm021', os.path.join(ROOT, 'scripts/migrations/m021_medical_insurance_type.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        db = str(tmp_path / 'm021.db')
        conn = sqlite3.connect(db)
        conn.execute('CREATE TABLE medical_insurance_payments '
                     '(id INTEGER PRIMARY KEY, name VARCHAR(50))')
        conn.commit()
        conn.close()

        first = mod.run(db)
        second = mod.run(db)
        third = mod.run(db)

        assert first['added_columns'] == ['insurance_type', 'ext_data']
        assert first['skipped_columns'] == []
        for again in (second, third):
            assert again['added_columns'] == []
            assert set(again['skipped_columns']) == {'insurance_type', 'ext_data'}

        conn = sqlite3.connect(db)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(medical_insurance_payments)')]
        assert 'insurance_type' in cols and 'ext_data' in cols
        idx = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='medical_insurance_payments'")]
        assert 'idx_medical_insurance_type' in idx
        conn.close()


class TestNoHardcodedFields:
    """P1-3：表单/表头/列定义不得再出现字段 key 清单，必须由目录派生。"""

    def test_template_has_containers_only(self):
        html = _read('templates/medical_insurance/list.html')
        assert 'id="formFields"' in html
        assert 'id="formInsuranceType"' in html, 'saveForm 缺 insurance_type 隐藏域（新增会落成居民）'
        assert 'id="typeFields"' not in html, '旧的专属字段容器应已移除'
        assert re.search(r'<thead[^>]*><tr id="tableHead"></tr></thead>', html), '表头应改为动态容器'
        # 仅约束 saveForm 内不得硬编码字段（搜索表单的筛选条件是另一回事）
        # 2026-11-16：表单开标签带 novalidate，正则补属性通配（原口径对现状恒不匹配）
        form = re.search(r'<form id="saveForm"[^>]*>(.*?)</form>', html, re.S)
        assert form, 'saveForm 未找到'
        inner = form.group(1)
        for key in ('village_group', 'household_property', 'deduction_reason',
                    'contact_phone', 'is_paid'):
            assert f'name="{key}"' not in inner, f'saveForm 仍硬编码字段 {key}'

    def test_js_drives_from_catalog(self):
        src = _read('static/js/pages/medical_insurance.js')
        assert 'catalogFields' in src and 'renderTableHead' in src
        # 2026-11-16：三分类合并为单表后列集合固定（险种 + 居民列序），原「职工/其他按
        # 目录 in_list 派生」分支随之移除，故不再断言 in_list；标签/字典仍由 catalogFields 派生
        assert "field: 'insurance_type'" in src, '缺「险种」列（合并展示的类别标识）'
        for key in ('village_group', 'insurance_status', 'is_state_paid', 'work_unit'):
            assert f"field: '{key}'" not in src, f'列表列仍写死 {key}'

    def test_export_uses_catalog(self):
        py = _read('routes/medical_insurance.py')
        export_body = py.split('def api_export', 1)[1].split('@medical_bp.route', 1)[0]
        assert 'mfc.fields_for' in export_body, '导出表头未从目录派生'
        assert "'\u5e8f\u53f7'" not in export_body, '导出表头仍硬编码'
