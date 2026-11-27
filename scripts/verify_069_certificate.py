#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""069 复验脚本：存量库补跑迁移后 /certificate/ 环境闭环取证。

前置：验证服务器已起（SHUCUN_DB_PATH 指向存量库，见 069-RESULT）。
产物：gui-test-screenshots/069-verify/certificate-1440.png / certificate-preview-1440.png
"""
import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get('VERIFY_BASE', 'http://127.0.0.1:5001')
OUT = os.environ.get(
    'VERIFY_OUT',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 '..', '..', 'gui-test-screenshots', '069-verify'))
os.makedirs(OUT, exist_ok=True)

checks = {}
with sync_playwright() as p:
    channel = os.environ.get('VERIFY_BROWSER_CHANNEL')
    browser = p.chromium.launch(headless=True, **({'channel': channel} if channel else {}))
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.set_default_timeout(15000)

    # ① 登录
    page.goto(BASE + '/auth/login')
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    page.wait_for_load_state('domcontentloaded')
    checks['login'] = '/dashboard' in page.url or '/auth' not in page.url

    # ② 打开证明开具页
    page.goto(BASE + '/certificate/')
    page.wait_for_load_state('domcontentloaded')
    page.wait_for_function(
        "() => document.querySelectorAll('#issueTemplateSelect option').length > 0",
        timeout=15000)
    options = page.eval_on_selector_all(
        '#issueTemplateSelect option',
        'els => els.map(e => e.textContent.trim()).filter(t => t)')
    checks['template_options'] = options
    checks['no_error_toast'] = page.locator('.toast').count() == 0 or (
        '请求失败' not in page.locator('body').inner_text())
    page.screenshot(path=os.path.join(OUT, 'certificate-1440.png'), full_page=True)

    # ③ 选择模板 → A4 预览渲染（下拉为自绘控件，原生 select 隐藏，经 JS 派发 change）
    page.eval_on_selector(
        '#issueTemplateSelect',
        'el => { el.selectedIndex = 1; '
        'el.dispatchEvent(new Event("change", {bubbles: true})); }')
    page.wait_for_function(
        "() => !document.querySelector('#issuePaper')"
        ".innerText.includes('正在加载证明模板')", timeout=15000)
    paper_text = page.locator('#issuePaper').inner_text()
    checks['preview_rendered'] = len(paper_text.strip()) > 20
    checks['preview_head'] = paper_text.strip().splitlines()[:3]
    page.screenshot(path=os.path.join(OUT, 'certificate-preview-1440.png'),
                    full_page=True)
    browser.close()

print(json.dumps(checks, ensure_ascii=False, indent=2))
ok = (checks['login'] and len(checks['template_options']) >= 3
      and checks['no_error_toast'] and checks['preview_rendered'])
print('RESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
