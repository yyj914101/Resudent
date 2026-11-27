/* ==========================================================================
 * shell.js — 壳层交互（057a + 057b 心跳接线）
 * 职责：① 侧栏折叠 + localStorage 记忆 ② Ctrl/Cmd+K 聚焦搜索 ③ 提示条关闭
 *       ④ 在线状态：每 30s POST /api/heartbeat 刷新本用户 last_active，
 *          并拉 /api/online-count（60s 窗口）更新顶栏计数（057b 后端）
 * 边界：禁止并入 spa.js（ADR-022 §一-4）
 * 依赖：无（原生 DOM / fetch；Bootstrap collapse 由 bundle 自理）
 * ========================================================================== */
(function () {
    'use strict';

    var COLLAPSE_KEY = 'sc.sidebar.collapsed';
    var BANNER_KEY = 'sc.banner.closed';
    var HEARTBEAT_MS = 30000;   // G-02：上报 30s
    var ONLINE_WINDOW = 60;     // G-02：判定窗口 60s（服务端口径，此处仅注释）

    function safeGet(store, key) {
        try { return window[store].getItem(key); } catch (e) { return null; }
    }
    function safeSet(store, key, value) {
        try { window[store].setItem(key, value); } catch (e) { /* 隐私模式静默降级 */ }
    }

    /** 应用折叠态：body class + 按钮 aria + 图标切换 */
    function applyCollapsed(collapsed) {
        document.body.classList.toggle('sc-sidebar-collapsed', collapsed);
        var btn = document.getElementById('sidebarCollapseBtn');
        if (!btn) return;
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        btn.setAttribute('aria-label', collapsed ? '展开侧栏' : '折叠侧栏');
        var use = btn.querySelector('use');
        if (!use) return;
        var href = use.getAttribute('href') || use.getAttribute('xlink:href') || '';
        var base = href.split('#')[0];
        if (base) use.setAttribute('href', base + (collapsed ? '#panel-left' : '#panel-left-close'));
    }

    function initCollapse() {
        applyCollapsed(safeGet('localStorage', COLLAPSE_KEY) === '1');
        var btn = document.getElementById('sidebarCollapseBtn');
        if (!btn) return;
        btn.addEventListener('click', function () {
            var next = !document.body.classList.contains('sc-sidebar-collapsed');
            applyCollapsed(next);
            safeSet('localStorage', COLLAPSE_KEY, next ? '1' : '0');
        });
        // bug1 补充（2026-11-09）：窄视口 Esc 关闭移动抽屉，提供不依赖导航的关闭手段
        document.addEventListener('keydown', function (e) {
            if (e.key !== 'Escape') return;
            if (window.innerWidth >= 992) return;
            if (document.body.classList.contains('sidebar-open')) {
                document.body.classList.remove('sidebar-open');
            }
        });
    }

    function initBanner() {
        var banner = document.getElementById('scBanner');
        if (!banner) return;
        // 按文案指纹记：同一条提示本会话内只关一次，文案变了重新出现
        var text = (banner.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60);
        var key = BANNER_KEY + '::' + text;
        if (safeGet('sessionStorage', key) === '1') {
            banner.parentNode.removeChild(banner);
            return;
        }
        var close = document.getElementById('scBannerClose');
        if (!close) return;
        close.addEventListener('click', function () {
            banner.parentNode.removeChild(banner);
            safeSet('sessionStorage', key, '1');
        });
    }

    function initHotkey() {
        document.addEventListener('keydown', function (e) {
            if (!(e.ctrlKey || e.metaKey)) return;
            if (e.key !== 'k' && e.key !== 'K') return;
            var input = document.getElementById('globalSearch');
            if (!input) return;
            e.preventDefault();
            input.focus();
            if (input.select) input.select();
        });
    }

    /** 在线状态：心跳 + 在线人数（057b 后端；端点缺失时静默降级，不影响业务） */
    function initPresence() {
        var countEl = document.getElementById('onlineCount');
        var inFlight = false;

        function pullCount() {
            return fetch('/api/online-count', {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin'
            }).then(function (r) {
                return r.ok ? r.json() : null;
            }).then(function (data) {
                if (data && typeof data.online === 'number' && countEl) {
                    countEl.textContent = data.online;
                }
            });
        }

        function beat() {
            if (inFlight) return;
            inFlight = true;
            fetch('/api/heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}',
                credentials: 'same-origin'
            }).then(function (r) {
                return r.ok ? pullCount() : null;
            }).catch(function () {
                /* 网络异常 / 端点未部署（404）→ 静默，不打扰业务操作 */
            }).then(function () {
                inFlight = false;
            });
        }

        // 首屏立即对齐一次（不用等 30s），随后固定节拍
        beat();
        window.setInterval(beat, HEARTBEAT_MS);
    }

    initCollapse();
    initBanner();
    initHotkey();
    initPresence();
})();

/* ==========================================================================
 * 侧栏菜单名自定义（2026-11-11）
 * 可修改范围：仅 #menu 内可点击菜单项 <a class="nav-link"> 的显示文字。
 *   不含组标题、品牌名、「设置」项、用户卡；不改 href / class / li / endpoint，
 *   故导航、menuRules（按 href 匹配）、角色过滤与 SSR 快照全部不受影响。
 * 生效方式：保存后立即改写侧栏文字并写 localStorage；刷新、SPA 换页、权限过滤
 *   重渲染后经 MutationObserver 重放；多标签页经 storage 事件同步。
 * 持久化：localStorage `sc.nav.labels`（浏览器级，当前用户/当前浏览器）；
 *   不可用时退化为内存，仅当前会话有效。需要跨设备全局配置请另走服务端 KV。
 * 校验：去首尾空白后 1~16 字符；禁止 < > 与控制字符；留空 = 恢复默认名。
 *   一律用 textContent 写入，天然防 XSS。
 * ========================================================================== */
(function () {
    'use strict';
    var KEY = 'sc.nav.labels';
    var MAX = 16;
    var memoryFallback = null;
    var defaults = {};          // href -> 服务端原始名（会话内基线，DOM 重建后仍可恢复默认）
    var panel = null;

    function getRaw() {
        try { return window.localStorage.getItem(KEY); } catch (e) { return memoryFallback; }
    }
    function setRaw(s) {
        try { window.localStorage.setItem(KEY, s); } catch (e) { memoryFallback = s; }
    }
    function read() {
        var raw = getRaw();
        if (!raw) return {};
        try {
            var o = JSON.parse(raw);
            return (o && typeof o === 'object' && !Array.isArray(o)) ? o : {};
        } catch (e) { return {}; }
    }
    /** '' = 恢复默认；null = 非法；其余为合法新名 */
    function normalize(v) {
        v = String(v == null ? '' : v).trim();
        if (!v) return '';
        if (v.length > MAX) return null;
        if (/[<>\x00-\x1f]/.test(v)) return null;
        return v;
    }
    function keyOf(a) {
        return (a.getAttribute('href') || '').split('#')[0].split('?')[0];
    }
    function spanOf(a) {
        for (var i = 0; i < a.children.length; i++) {
            if (a.children[i].tagName === 'SPAN') return a.children[i];
        }
        return null;
    }
    function links() {
        var menu = document.getElementById('menu');
        return menu ? [].slice.call(menu.querySelectorAll('li > a.nav-link[href]')) : [];
    }

    var applying = false;
    function apply() {
        if (applying) return;
        applying = true;
        var ov = read();
        links().forEach(function (a) {
            var span = spanOf(a);
            if (!span) return;
            var key = keyOf(a);
            if (!defaults[key]) {
                defaults[key] = a.getAttribute('data-sc-nav-default') || span.textContent.trim();
                a.setAttribute('data-sc-nav-default', defaults[key]);
            }
            var def = defaults[key];
            var v = normalize(ov[key]);
            var next = (v === null || v === '') ? def : v;
            if (span.textContent !== next) span.textContent = next;
        });
        applying = false;
    }

    function make(tag, cls, text) {
        var el = document.createElement(tag);
        if (cls) el.className = cls;
        if (text != null) el.textContent = text;
        return el;
    }

    function closePanel() {
        if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
        panel = null;
    }

    function openPanel(focusKey) {
        closePanel();
        var ov = read();
        panel = make('div', 'sc-nav-edit');
        var box = make('div', 'sc-nav-edit__box');
        box.appendChild(make('h3', 'sc-nav-edit__title', '自定义侧栏菜单名'));
        box.appendChild(make('p', 'sc-nav-edit__hint',
            '仅修改菜单项的显示名称（1–' + MAX + ' 字，留空恢复默认），不影响菜单结构、顺序与跳转。'
            + '名称保存在当前浏览器，刷新与页面切换后仍生效。'));
        var err = make('div', 'sc-nav-edit__err');
        err.setAttribute('role', 'alert');
        box.appendChild(err);

        var list = make('div', 'sc-nav-edit__list');
        var inputs = [];
        links().forEach(function (a) {
            var span = spanOf(a);
            if (!span) return;
            var key = keyOf(a);
            var def = defaults[key] || a.getAttribute('data-sc-nav-default') || span.textContent.trim();
            var row = make('label', 'sc-nav-edit__row');
            row.appendChild(make('span', 'sc-nav-edit__label', def));
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'sc-nav-edit__input';
            input.maxLength = MAX + 16;   // 允许输入后再校验，给出明确错误而非静默截断
            input.placeholder = def;
            input.value = (normalize(ov[key]) === null) ? def : (ov[key] || def);
            input.setAttribute('data-key', key);
            row.appendChild(input);
            list.appendChild(row);
            inputs.push(input);
        });
        box.appendChild(list);

        var actions = make('div', 'sc-nav-edit__actions');
        var btnSave = make('button', 'sc-nav-edit__btn sc-nav-edit__btn--primary', '保存');
        var btnReset = make('button', 'sc-nav-edit__btn', '全部恢复默认');
        var btnCancel = make('button', 'sc-nav-edit__btn', '取消');
        [btnSave, btnReset, btnCancel].forEach(function (b) { b.type = 'button'; actions.appendChild(b); });
        box.appendChild(actions);
        panel.appendChild(box);
        document.body.appendChild(panel);

        function showErr(msg, input) {
            err.textContent = msg;
            inputs.forEach(function (i) { i.classList.remove('is-invalid'); });
            if (input) { input.classList.add('is-invalid'); input.focus(); }
        }
        btnCancel.addEventListener('click', closePanel);
        panel.addEventListener('click', function (e) { if (e.target === panel) closePanel(); });
        btnReset.addEventListener('click', function () {
            setRaw('{}');
            apply();
            inputs.forEach(function (i) { i.value = i.placeholder; });
            err.textContent = '';
            inputs.forEach(function (i) { i.classList.remove('is-invalid'); });
        });
        btnSave.addEventListener('click', function () {
            var next = {};
            for (var i = 0; i < inputs.length; i++) {
                var raw = inputs[i].value;
                var v = normalize(raw);
                if (v === null) {
                    showErr('「' + inputs[i].placeholder + '」' +
                        (String(raw).trim().length > MAX ? '超过 ' + MAX + ' 个字符' : '含非法字符（< > 或控制字符）'),
                        inputs[i]);
                    return;
                }
                // 只存与默认名不同的项：服务端 nav_spec 调整默认名时可自然生效，不被旧值覆盖
                if (v && v !== inputs[i].placeholder) {
                    next[inputs[i].getAttribute('data-key')] = v;
                }
            }
            setRaw(JSON.stringify(next));
            apply();
            closePanel();
        });
        panel.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { e.stopPropagation(); closePanel(); }
        });
        var target = focusKey && inputs.filter(function (i) { return i.getAttribute('data-key') === focusKey; })[0];
        (target || inputs[0] || btnCancel).focus();
    }

    function initNavLabels() {
        var menu = document.getElementById('menu');
        if (!menu) return;                 // 登录页/无侧栏页面不注入任何 UI
        apply();

        var btn = make('button', 'sc-nav-edit-entry', '✎ 自定义菜单名');
        btn.type = 'button';
        btn.addEventListener('click', function () { openPanel(null); });
        menu.appendChild(btn);
        // 双击某个菜单项 → 打开面板并聚焦对应输入框（不改变单击导航）
        menu.addEventListener('dblclick', function (e) {
            var a = e.target.closest ? e.target.closest('a.nav-link[href]') : null;
            if (a) openPanel(keyOf(a));
        });

        var timer = null;
        function schedule() {
            if (timer) return;
            timer = window.setTimeout(function () { timer = null; apply(); }, 0);
        }
        if (window.MutationObserver) {
            new MutationObserver(schedule).observe(menu, { childList: true, subtree: true });
        }
        window.addEventListener('storage', function (e) {
            if (e.key === KEY || e.key === null) apply();
        });
    }

    /* 2026-11-16 闪烁修复：菜单覆盖必须在「首次绘制前」同步完成。
     * shell.js 在 base.html 的 body 末尾同步加载，此时侧栏 #menu 及其全部
     * <li><a> 已解析完毕（include 在所有 <script> 之前），但 document.readyState
     * 仍是 'loading' —— 旧实现因此挂到 DOMContentLoaded 才 apply()，期间浏览器
     * 已把服务端渲染的原始菜单名画出来，随后 JS 改成自定义名，形成整页导航
     * （功能胶囊是普通 <a>，spa.js 只拦截侧栏链接）时每次必现的「原名↔新名」闪烁。
     * 改为：菜单 DOM 就绪即立即初始化（同步，早于首帧绘制）；无菜单或菜单尚未
     * 解析出链接时仍回退 DOMContentLoaded，保证登录页与未来模板调整不漏覆盖。 */
    var bootMenu = document.getElementById('menu');
    var bootLinks = bootMenu ? bootMenu.querySelectorAll('li > a.nav-link[href]') : null;
    if (bootLinks && bootLinks.length) {
        initNavLabels();
    } else if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initNavLabels);
    } else {
        initNavLabels();
    }
})();
