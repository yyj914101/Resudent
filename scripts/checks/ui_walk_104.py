#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""104 全站 UI 视觉走查脚本 v3（环境检查用，不属于业务代码）。

对 dev_server 起的测试实例做 Playwright 全站走查：
  - 种子页面 = /dashboard + /settings/center + 程序化提取的 routes/*.py GET 页面路由
  - 列表页先点「查询/搜索」加载数据再度量
  - 逐页 DOM 度量：按钮裁切/出框、按钮两两重叠、表头垂直居中/列漂移、页面横滚、
    小字号、无标签图标按钮
  - 逐弹窗度量（白名单按钮触发只读类动作：详情/查看/编辑/新增等）：
    标题与关闭钮重叠、弹窗内元素出框、弹窗内按钮裁切、弹窗体横向溢出
输出：问题 JSON（gui-test-screenshots/104/walk-metrics-<tag>.json）+ 截图（before/ 或 before-narrow/）

用法：.venv/bin/python scripts/checks/ui_walk_104.py [port] [tag] [viewport WxH]
默认 port=5012 tag=1440 viewport=1440x900；tag=1024 时配 1024x768。
"""
import glob
import json
import os
import re
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, BASE_DIR)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5012
TAG = sys.argv[2] if len(sys.argv) > 2 else '1440'
VIEWPORT = sys.argv[3] if len(sys.argv) > 3 else ('1440x900' if '1440' in TAG else '1024x768')
W, H = (int(x) for x in VIEWPORT.split('x'))

BASE = f'http://127.0.0.1:{PORT}'
SHOT_NAME = {'1440': 'before', '1024': 'before-narrow', 'after1440': 'after', 'after1024': 'after-narrow'}.get(TAG, TAG)
SHOT_DIR = os.path.join(BASE_DIR, 'gui-test-screenshots', '104', SHOT_NAME)
JSON_PATH = os.path.join(BASE_DIR, 'gui-test-screenshots', '104', f'walk-metrics-{TAG}.json')
os.makedirs(SHOT_DIR, exist_ok=True)

MODAL_BTN_WHITELIST = ['详情', '查看', '编辑', '新增', '新建', '添加', '上传', '筛选',
                       '列选择', '成员', '明细', '批次', '预览', '走访记录', '记录', '说明',
                       '批量导入', '高级', '导入']


def extract_page_routes():
    """从 routes/*.py + app.py 提取 GET 页面路由（无参数、非 /api/、非 POST-only）。"""
    routes_dir = os.path.join(BASE_DIR, 'routes')
    prefix_of = {}
    app_src = open(os.path.join(BASE_DIR, 'app.py'), encoding='utf-8').read()
    for m in re.finditer(r"register_blueprint\((\w+),\s*url_prefix='([^']+)'\)", app_src):
        prefix_of[m.group(1)] = m.group(2)
    bp_of = {}
    seeds = {'/dashboard/', '/settings/center'}
    for path in glob.glob(os.path.join(routes_dir, '*.py')):
        src = open(path, encoding='utf-8').read()
        for m in re.finditer(r'^(\w+)\s*=\s*Blueprint\(', src, re.M):
            bp_of[m.group(1)] = os.path.basename(path)
        for m in re.finditer(r"@(\w+)\.route\('([^']*)'\s*(?:,\s*methods=\[([^\]]*)\])?\)", src):
            bp, sub, methods = m.group(1), m.group(2), (m.group(3) or '')
            if bp not in prefix_of or '<' in sub or '/api/' in sub:
                continue
            if methods and 'GET' not in methods:
                continue
            url = prefix_of[bp].rstrip('/') + '/' + sub.lstrip('/')
            url = '/' + url.lstrip('/')
            if sub in ('', '/'):
                url = prefix_of[bp] + '/'
            if url not in seeds:
                seeds.add(url)
    return sorted(seeds)


PAGE_METRICS = r"""
() => {
  const issues = [];
  const de = document.documentElement;
  const vw = de.clientWidth, vh = de.clientHeight;
  const vis = (el) => !!(el.offsetParent || el.getClientRects().length) &&
                     getComputedStyle(el).visibility !== 'hidden' &&
                     getComputedStyle(el).display !== 'none';
  const rectOf = (el) => el.getBoundingClientRect();
  const textRectOf = (el) => {
    const r = document.createRange(); r.selectNodeContents(el);
    const rr = r.getBoundingClientRect();
    return (rr.width || rr.height) ? rr : null;
  };
  const desc = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    const cls = (typeof el.className === 'string') ? el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
    if (cls) s += '.' + cls;
    const t = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 14);
    if (t) s += ' [' + t + ']';
    return s;
  };
  const push = (o) => issues.push(o);

  if (de.scrollWidth > vw + 2) push({ t: 'page-hscroll', scrollW: de.scrollWidth, clientW: vw });

  // 顶部页签条溢出（右缘裁切，P3 佐证）+ 激活页签是否完整可见
  const ts = document.querySelector('.tab-scroll');
  if (ts) {
    if (ts.scrollWidth > ts.clientWidth + 4)
      push({ t: 'tabbar-overflow', scrollW: ts.scrollWidth, clientW: ts.clientWidth });
    const act = ts.querySelector('.tab-item.active');
    if (act) {
      const ra = act.getBoundingClientRect(), rt = ts.getBoundingClientRect();
      if (ra.right > rt.right + 2 || ra.left < rt.left - 2)
        push({ t: 'tab-active-offstrip', tabR: Math.round(ra.right), stripR: Math.round(rt.right) });
    }
  }

  const btnSel = 'button, .btn, .sc-btn, [role="button"], a.nav-link, a.sc-btn--text';
  const btns = [...document.querySelectorAll(btnSel)].filter(el => vis(el) && rectOf(el).width > 0);
  for (const b of btns) {
    const r = rectOf(b), d = desc(b);
    if (b.scrollWidth > b.clientWidth + 2)
      push({ t: 'btn-clip-x', el: d, scrollW: b.scrollWidth, clientW: b.clientWidth, rect: [r.x|0, r.y|0, r.width|0, r.height|0] });
    if (b.scrollHeight > b.clientHeight + 3)
      push({ t: 'btn-clip-y', el: d, scrollH: b.scrollHeight, clientH: b.clientHeight, rect: [r.x|0, r.y|0, r.width|0, r.height|0] });
    if (r.right > vw + 2 && de.scrollWidth <= vw + 2)
      push({ t: 'btn-offscreen-r', el: d, right: r.right | 0, vw });
  }

  const leaves = btns.filter(el => !el.querySelector('button, .btn, .sc-btn, a.sc-btn--text'));
  for (let i = 0; i < leaves.length; i++) {
    for (let j = i + 1; j < leaves.length; j++) {
      const a = leaves[i], b = leaves[j];
      if (a.contains(b) || b.contains(a)) continue;
      // 侧栏固定底区（不透明底）盖住滚动菜单属常规模式，不算重叠
      if (a.closest('.sc-sidebar__bottom') || b.closest('.sc-sidebar__bottom')) continue;
      const ra = rectOf(a), rb = rectOf(b);
      const ox = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const oy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (ox > 2 && oy > 2)
        push({ t: 'btn-overlap', a: desc(a), b: desc(b), ox: ox | 0, oy: oy | 0,
               ax: [ra.x|0, ra.y|0, ra.width|0, ra.height|0], bx: [rb.x|0, rb.y|0, rb.width|0, rb.height|0] });
    }
  }

  const rowVis = (tr) => vis(tr) && rectOf(tr).height > 0;
  for (const tb of document.querySelectorAll('table')) {
    if (!vis(tb)) continue;
    const ths = [...tb.querySelectorAll('thead th')].filter(vis);
    const row1 = [...tb.querySelectorAll('tbody tr')].find(rowVis);
    if (!ths.length || !row1) continue;
    const tds1 = [...row1.children].filter(c => c.tagName === 'TD' && c.colSpan === 1 && vis(c));
    const tag = desc(tb).slice(0, 60);
    for (const th of ths) {
      const va = getComputedStyle(th).verticalAlign;
      const cr = rectOf(th), tr = textRectOf(th);
      if (tr && tr.height > 0) {
        const off = Math.abs((cr.top + cr.height / 2) - (tr.top + tr.height / 2));
        if (off > 3 && va !== 'middle')
          push({ t: 'th-not-centered', table: tag, th: desc(th), va, off: Math.round(off * 10) / 10,
                 cellH: Math.round(cr.height) });
      }
    }
    const n = Math.min(ths.length, tds1.length);
    for (let i = 0; i < n; i++) {
      const rt = rectOf(ths[i]), rd = rectOf(tds1[i]);
      if (Math.abs(rt.left - rd.left) > 2 || Math.abs(rt.right - rd.right) > 2)
        push({ t: 'col-drift', table: tag, col: i, thL: Math.round(rt.left), tdL: Math.round(rd.left),
               thW: Math.round(rt.width), tdW: Math.round(rd.width) });
    }
  }

  const smallSeen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if (!vis(el) || el.children.length > 0) continue;
    const t = (el.textContent || '').trim();
    if (!t) continue;
    if (el.closest('.sc-badge, .badge, .page-link, .sc-pagination__stat, .sc-kbd')) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs < 11.5) {
      const key = fs + '|' + desc(el);
      if (!smallSeen.has(key)) { smallSeen.add(key); push({ t: 'tiny-font', el: desc(el), fs }); }
      if (smallSeen.size > 8) break;
    }
  }

  for (const b of btns) {
    const txt = (b.textContent || '').trim();
    if (!txt && !b.getAttribute('aria-label') && !b.getAttribute('title'))
      push({ t: 'unlabeled-icon-btn', el: desc(b) });
  }
  return { vw, vh, issues };
}
"""

MODAL_METRICS = r"""
(mroot) => {
  const issues = [];
  const vis = (el) => !!(el.offsetParent || el.getClientRects().length) &&
                     getComputedStyle(el).visibility !== 'hidden' &&
                     getComputedStyle(el).display !== 'none';
  const rectOf = (el) => el.getBoundingClientRect();
  const desc = (el) => {
    let s = el.tagName.toLowerCase();
    const cls = (typeof el.className === 'string') ? el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
    if (cls) s += '.' + cls;
    const t = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 14);
    if (t) s += ' [' + t + ']';
    return s;
  };
  const mr = rectOf(mroot);
  const all = [...mroot.querySelectorAll('*')].filter(vis);

  const closes = all.filter(el => el.matches('.btn-close, .sc-dheader__close, [data-bs-dismiss="modal"], .sc-icon-btn'));
  const titles = all.filter(el => el.matches('.modal-title, .sc-modal__title, .sc-dheader__name, .sc-dheader__name-row'));
  for (const c of closes) {
    const rc = rectOf(c);
    for (const o of all) {
      if (o === c || c.contains(o) || o.contains(c)) continue;
      const ro = rectOf(o);
      const ox = Math.min(rc.right, ro.right) - Math.max(rc.left, ro.left);
      const oy = Math.min(rc.bottom, ro.bottom) - Math.max(rc.top, ro.top);
      if (ox > 2 && oy > 2)
        issues.push({ t: 'modal-close-overlap', close: desc(c), other: desc(o), ox: ox | 0, oy: oy | 0 });
    }
    if (titles.length && vis(titles[0])) {
      const rt = rectOf(titles[0]);
      issues.push({ t: 'modal-head-gap', gap: Math.round(rc.left - rt.right), title: desc(titles[0]) });
    }
  }
  for (const el of all) {
    if (el.matches('.modal-dialog, .modal-content, .sc-modal, .modal, .modal-body, .modal-footer, .modal-header, .sc-modal__body, .sc-modal__footer, .sc-modal__header, .sc-fmodal__meta')) continue;
    const r = rectOf(el);
    if (r.width === 0) continue;
    if (r.right > mr.right + 2) issues.push({ t: 'modal-el-outside-r', el: desc(el), over: Math.round(r.right - mr.right) });
    if (r.left < mr.left - 2) issues.push({ t: 'modal-el-outside-l', el: desc(el), over: Math.round(mr.left - r.left) });
  }
  for (const b of all.filter(el => el.matches('button, .btn, .sc-btn'))) {
    if (b.scrollWidth > b.clientWidth + 2)
      issues.push({ t: 'modal-btn-clip-x', el: desc(b), scrollW: b.scrollWidth, clientW: b.clientWidth });
    if (b.scrollHeight > b.clientHeight + 3)
      issues.push({ t: 'modal-btn-clip-y', el: desc(b), scrollH: b.scrollHeight, clientH: b.clientHeight });
  }
  for (const bd of all.filter(el => el.matches('.modal-body, .sc-modal__body, .sc-fgroups, .sc-fgroup')))
    if (bd.scrollWidth > bd.clientWidth + 2)
      issues.push({ t: 'modal-body-hscroll', el: desc(bd), scrollW: bd.scrollWidth, clientW: bd.clientWidth });
  issues.push({ t: 'modal-size', w: Math.round(mr.width), h: Math.round(mr.height), vh: window.innerHeight });
  return issues;
}
"""

VISIBLE_ROOTS_JS = '''() => {
    const vis = (el) => !!(el.offsetParent || el.getClientRects().length) &&
        getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden';
    const roots = [...document.querySelectorAll('.modal, .sc-modal')]
        .filter(el => vis(el) && (el.classList.contains('modal') || !el.closest('.modal')));
    return roots.length ? roots[roots.length - 1] : null;
}'''


def run():
    from playwright.sync_api import sync_playwright

    def slugify(url):
        path = re.sub(r'^https?://[^/]+', '', url) or '/dashboard'
        s = re.sub(r'[^0-9A-Za-z]+', '_', path).strip('_')[:46] or 'root'
        return s

    seeds = extract_page_routes()
    print(f'[walk] 种子路由 {len(seeds)} 个')

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': W, 'height': H}, locale='zh-CN')
        page = ctx.new_page()
        page.set_default_timeout(12000)
        page.on('dialog', lambda d: d.dismiss())

        page.goto(BASE + '/auth/login', wait_until='networkidle')
        page.fill('input[name=username]', 'admin')
        page.fill('input[name=password]', 'admin123')
        page.click('button[type=submit]')
        page.wait_for_load_state('networkidle')
        assert '/auth/login' not in page.url, '登录失败'

        # 校验种子路由存活（HTML 页面才走查；排除 API 与 auth，绝不能走到 logout）
        urls = []
        for u in seeds:
            if u.startswith('/api/') or u.startswith('/auth/'):
                continue
            try:
                r = ctx.request.get(BASE + u, max_redirects=3)
                ct = (r.headers.get('content-type') or '')
                if r.status in (200, 304) and 'text/html' in ct:
                    urls.append(BASE + u if u.startswith('/') else u)
            except Exception:
                pass
        # 设置中心深链补种
        try:
            page.goto(BASE + '/settings/center', wait_until='networkidle')
            for h in page.eval_on_selector_all('a[href]', 'els => els.map(e => e.getAttribute("href"))'):
                if h and h.startswith('/') and h not in seeds and 'logout' not in h:
                    r = ctx.request.get(BASE + h, max_redirects=3)
                    if r.status == 200 and 'text/html' in (r.headers.get('content-type') or ''):
                        urls.append(BASE + h)
        except Exception:
            pass
        seen = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]
        print(f'[walk] 存活页面 {len(urls)} 个')

        results = []
        for idx, url in enumerate(urls):
            slug = f'p{idx:02d}_{slugify(url)}'
            rec = {'url': url, 'slug': slug, 'viewport': VIEWPORT}
            try:
                page.goto(url, wait_until='networkidle', timeout=25000)
                page.wait_for_timeout(400)
                # 列表页预载：点「查询/搜索」
                for t in ('查询', '搜索'):
                    try:
                        b = page.locator(f'button:has-text("{t}")').first
                        if b.count() and b.is_visible():
                            b.click(timeout=2000)
                            page.wait_for_timeout(900)
                            break
                    except Exception:
                        pass
                rec['page'] = page.evaluate(PAGE_METRICS)
                page.screenshot(path=os.path.join(SHOT_DIR, slug + '.png'), full_page=True)

                # ---- 弹窗走查：行点击（详情类）优先 + 白名单按钮 ----
                rec['modals'] = []
                has_rows = page.evaluate(
                    '''() => !!(document.querySelector('table tbody tr[data-id]') ||
                               document.querySelector('table tbody tr td:not([colspan])'))''')
                candidates = []
                if has_rows:
                    candidates.append('__ROW_CLICK__')
                btns = page.eval_on_selector_all(
                    'button, a.btn, .sc-btn, .sc-row-actions a, .sc-row-actions button, [data-crud-act="rowact"]',
                    '''(els, wl) => {
                        const out = [];
                        for (const e of els) {
                            const cs = getComputedStyle(e);
                            const r = e.getBoundingClientRect();
                            if (!(r.width > 0) || cs.visibility === 'hidden' || cs.display === 'none') continue;
                            if (e.closest('.modal')) continue;
                            const txt = (e.textContent || '').trim();
                            if (!txt || txt.length > 8) continue;
                            if (!wl.some(w => txt.includes(w))) continue;
                            out.push(txt);
                        }
                        return [...new Set(out)];
                    }''', MODAL_BTN_WHITELIST)
                candidates += btns[:3]
                for mi, txt in enumerate(candidates[:3]):
                    try:
                        page.goto(url, wait_until='networkidle', timeout=20000)
                        page.wait_for_timeout(350)
                        for t in ('查询', '搜索'):
                            try:
                                b = page.locator(f'button:has-text("{t}")').first
                                if b.count() and b.is_visible():
                                    b.click(timeout=2000)
                                    page.wait_for_timeout(800)
                                    break
                            except Exception:
                                pass
                        if txt == '__ROW_CLICK__':
                            row = page.locator('table tbody tr[data-id], table tbody tr:not(:has(td[colspan]))').first
                            row.click(timeout=4000)
                        else:
                            btn = page.locator(
                                f'button:has-text("{txt}"), a.btn:has-text("{txt}"), .sc-btn:has-text("{txt}"), '
                                f'.sc-row-actions :has-text("{txt}"), [data-crud-act="rowact"]:has-text("{txt}")').first
                            btn.click(timeout=4000)
                        page.wait_for_timeout(700)
                        mroot = page.evaluate_handle(VISIBLE_ROOTS_JS)
                        if not mroot.as_element():
                            continue
                        mslug = f'{slug}__m{mi}_{re.sub(r"[^0-9A-Za-z]+", "_", txt)[:12]}'
                        mroot.as_element().screenshot(path=os.path.join(SHOT_DIR, mslug + '.png'))
                        missues = page.evaluate(
                            '(function(){ const f = ' + MODAL_METRICS.strip() + ';'
                            ' const vis = (el) => (el.offsetParent || el.getClientRects().length) &&'
                            ' getComputedStyle(el).display !== "none" && getComputedStyle(el).visibility !== "hidden";'
                            ' const roots = [...document.querySelectorAll(".modal, .sc-modal")]'
                            '.filter(el => vis(el) && (el.classList.contains("modal") || !el.closest(".modal")));'
                            ' return f(roots[roots.length - 1]); })()')
                        rec['modals'].append({'trigger': txt, 'issues': missues})
                        try:
                            page.locator(
                                '.modal.show .btn-close, .modal [data-bs-dismiss="modal"], .sc-dheader__close'
                            ).first.click(timeout=1500)
                            page.wait_for_timeout(300)
                        except Exception:
                            page.keyboard.press('Escape')
                            page.wait_for_timeout(300)
                        still = page.evaluate('''() => {
                            const vis = (el) => (el.offsetParent || el.getClientRects().length) &&
                                getComputedStyle(el).display !== 'none';
                            return [...document.querySelectorAll('.modal.show, .modal, .sc-modal')]
                                .some(el => vis(el) && !(el.parentElement && el.parentElement.closest('.modal')));
                        }''')
                        if still:
                            page.keyboard.press('Escape')
                            page.wait_for_timeout(300)
                    except Exception as e:
                        rec['modals'].append({'trigger': txt, 'error': str(e)[:120]})
                        try:
                            page.keyboard.press('Escape')
                        except Exception:
                            pass
            except Exception as e:
                rec['error'] = str(e)[:200]
            results.append(rec)
            n = len(rec.get('page', {}).get('issues', []))
            msum = sum(len(m.get('issues', [])) for m in rec.get('modals', []))
            print(f"  [{idx:02d}] {slug} page={n} modal={msum}"
                  + (f' ERR={rec["error"][:60]}' if 'error' in rec else ''))

        browser.close()

    with open(JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    total = sum(len(r.get('page', {}).get('issues', [])) for r in results)
    mtotal = sum(len(m.get('issues', [])) for r in results for m in r.get('modals', []))
    print(f'[walk] done tag={TAG}: {len(results)} pages, page-issues={total}, modal-issues={mtotal} -> {JSON_PATH}')


if __name__ == '__main__':
    run()
