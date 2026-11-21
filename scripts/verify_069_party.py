#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""069 复验脚本：/party/ 换壳后实拍取证（1440 + 1024 + 弹窗开启态）。

产物：gui-test-screenshots/069-verify/party-{1440,1024,modal}-*.png
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

    def login(page):
        page.goto(BASE + '/auth/login')
        page.fill('input[name="username"]', 'admin')
        page.fill('input[name="password"]', 'admin123')
        page.click('button[type="submit"]')
        page.wait_for_load_state('domcontentloaded')

    # ── 1440 主视口 ──
    page = browser.new_page(viewport={'width': 1440, 'height': 900})
    page.set_default_timeout(15000)
    login(page)
    page.goto(BASE + '/party/')
    page.wait_for_load_state('domcontentloaded')
    page.wait_for_selector('#tb tr[rid]', timeout=15000)
    checks['rows_from_db'] = page.locator('#tb tr[rid]').count()
    checks['sc_page_head'] = page.locator('header.sc-page-head').count() == 1
    checks['sc_table'] = page.locator('.sc-table.sc-table--dense').count() == 1
    checks['sc_pagination'] = page.locator('.sc-pagination').count() == 1
    checks['footer_stat'] = page.locator('#fi').inner_text()
    checks['summary_total'] = page.locator('#summaryTotal').inner_text()
    checks['no_legacy_table'] = page.locator(
        '.party-list-card table.table-bordered').count() == 0
    page.screenshot(path=os.path.join(OUT, 'party-1440.png'), full_page=True)

    # ── 新增党员弹窗（sc-modal 开启态） ──
    page.click('#btnAdd')
    page.wait_for_selector('#fmModal .sc-modal', state='visible')
    page.wait_for_function(
        "() => getComputedStyle(document.querySelector('#fmModal'))"
        ".opacity === '1'")  # 等 BS fade 过渡结束，避免拍到半透明中间态
    checks['modal_sc'] = page.locator('#fmModal .sc-modal.sc-modal--lg').count() == 1
    checks['modal_fields'] = page.locator('#fmModal .sc-field').count()
    page.screenshot(path=os.path.join(OUT, 'party-modal-1440.png'))
    # 069-FIX P1-01：鼠标点击弹窗内部必须保持打开，取消/关闭必须可点
    input_box = page.locator('#fmName').bounding_box()
    page.mouse.click(input_box['x'] + 10, input_box['y'] + input_box['height'] / 2)
    page.wait_for_timeout(400)
    checks['modal_click_inside_keeps_open'] = page.locator(
        '#fmModal .sc-modal').is_visible()
    page.click('#fmModal .sc-modal__footer .sc-btn--secondary')
    page.wait_for_timeout(500)
    checks['modal_cancel_closes'] = page.locator('#fmModal .sc-modal').is_visible() is False
    page.keyboard.press('Escape')
    page.close()

    # ── 1024 视口（响应式） ──
    page2 = browser.new_page(viewport={'width': 1024, 'height': 768})
    page2.set_default_timeout(15000)
    login(page2)
    page2.goto(BASE + '/party/')
    page2.wait_for_load_state('domcontentloaded')
    page2.wait_for_selector('#tb tr[rid]', timeout=15000)
    page2.screenshot(path=os.path.join(OUT, 'party-1024.png'), full_page=True)
    browser.close()

print(json.dumps(checks, ensure_ascii=False, indent=2))
ok = (checks['rows_from_db'] > 0 and checks['sc_page_head']
      and checks['sc_table'] and checks['sc_pagination']
      and checks['no_legacy_table'] and checks['modal_sc']
      and checks['modal_fields'] == 12
      and checks['modal_click_inside_keeps_open']
      and checks['modal_cancel_closes'])
print('RESULT:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
