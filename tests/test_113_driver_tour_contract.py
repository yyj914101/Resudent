# -*- coding: utf-8 -*-
"""tests/test_113_driver_tour_contract.py — driver.js 七场景操作指引集成静态契约守卫。"""
from pathlib import Path
import re

BASE = Path(__file__).resolve().parents[1]


def test_tour_registry_covers_seven_core_workflows():
    src = (BASE / 'static/js/tour_registry.js').read_text(encoding='utf-8')
    for tour_id in (
        'shell_overview',
        'dynatable_guide',
        'safety_archive',
        'batch_import_ops',
        'certificate_issue',
        'dict_mgmt_guide',
        'db_admin_guide',
    ):
        assert f"id: '{tour_id}'" in src, f'缺少引导场景：{tour_id}'
    for sel in ('#issueTemplateSelect', '#issueDynamicFields', '#issuePurpose',
                '#issuePaper', '#btnIssueCertificate',
                '#categoryList', '#currentCategoryName',
                '.db-stat-grid', '#btnShowNotice', '.db-tabs', '#tableSearch'):
        assert sel in src, f'指引缺少关键锚点：{sel}'


def test_tour_css_uses_adr022_tokens_without_hardcoded_hex():
    css = (BASE / 'static/css/tour.css').read_text(encoding='utf-8')
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', css), 'tour.css 禁止出现硬编码十六进制色值'
    assert 'var(--c-surface)' in css and 'var(--c-ink)' in css


def test_base_and_spa_wire_tour_lifecycle():
    base_html = (BASE / 'templates/base.html').read_text(encoding='utf-8')
    assert 'id="btnPageTour"' in base_html
    assert "filename='lib/driver.css'" in base_html
    assert "filename='css/tour.css'" in base_html
    assert "filename='lib/driver.js.iife.js'" in base_html
    assert "filename='js/tour_registry.js'" in base_html

    spa_js = (BASE / 'static/js/spa.js').read_text(encoding='utf-8')
    assert 'driver.js.iife.js' in spa_js and 'tour_registry.js' in spa_js
    assert 'driver.css' in spa_js and 'tour.css' in spa_js
    assert 'ResudentTour.destroy()' in spa_js
    assert 'ResudentTour.onRouteChange()' in spa_js
