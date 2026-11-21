/* spa.js — 壳层运行时（011：自 base.html 内联 <script> 外移）
 *
 * 组成（严格保持原内联顺序，执行时序等价）：
 *   ① CSRF 前端自动携带（原第 1 个内联 script）
 *   ② 侧栏权限菜单 + 标签栏 + 伪 SPA 导航（原第 2 个；权限表改读 window.__SPA_CFG__）
 *   ③ showProfile（原第 3 个）
 *
 * 约束：
 *   - 全局函数名与签名保持不变（各页内联事件直接依赖这些全局符号）
 *   - 值等价搬运，零行为变更
 *   - 引入位置：base.html 的 {% block extra_js %} 之后（与原内联块位置一致，避免覆盖顺序反转）
 */

/* P2-1 整改：CSRF 前端自动携带。
   1) jQuery AJAX → 统一注入 X-CSRF-Token 头（ajaxSend 全局钩子，不被单次 beforeSend 覆盖）
   2) 原生 fetch → 包装统一注入 X-CSRF-Token 头（import_wizard.js 等原生 fetch 场景）
   3) 传统 form submit → 提交前自动补 hidden input[name=csrf_token]（事件委托，兼容 SPA 动态表单）
*/
(function () {
    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }
    // 1) jQuery AJAX
    if (window.jQuery) {
        jQuery(document).ajaxSend(function (event, xhr, settings) {
            var method = (settings.type || 'GET').toUpperCase();
            if (['POST', 'PUT', 'PATCH', 'DELETE'].indexOf(method) >= 0) {
                xhr.setRequestHeader('X-CSRF-Token', csrfToken());
            }
        });
    }
    // 2) 原生 fetch 包装（仅补状态变更方法的 token 头）
    var _origFetch = window.fetch;
    if (_origFetch) {
        window.fetch = function (url, opts) {
            opts = opts || {};
            var method = (opts.method || 'GET').toUpperCase();
            if (['POST', 'PUT', 'PATCH', 'DELETE'].indexOf(method) >= 0) {
                var headers = new Headers(opts.headers || {});
                if (!headers.has('X-CSRF-Token')) {
                    headers.set('X-CSRF-Token', csrfToken());
                }
                opts.headers = headers;
            }
            return _origFetch.call(this, url, opts);
        };
    }
    // 3) 传统 form submit 自动注入（事件委托，兼容 SPA 动态加载的表单）
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || form.tagName !== 'FORM') return;
        if (form.querySelector('input[name="csrf_token"]')) return;
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'csrf_token';
        input.value = csrfToken();
        form.appendChild(input);
    }, true);
})();

function applyRoleMenuPermissions() {
    var cfg = window.__SPA_CFG__ || {};
    if (cfg.isAdmin) return;
    var rules = cfg.menuRules || [];
    rules.forEach(function(rule) {
        if (!rule[1]) {
            $('#menu').find(rule[0]).closest('li').remove();
        }
    });
    $('#menu .collapse').each(function() {
        var box = $(this);
        if (box.find('li').length === 0) {
            $('a[href="#' + box.attr('id') + '"]').closest('li').remove();
        }
    });
    /* 073 扁平组收空：某组条目被上方规则删空后整组隐藏——组标题 .sc-sidebar__label
       是 h4 不是 li，级联删命中不到它，不收空会留「孤儿组标题 + 空 ul」。
       含 .collapse 的组是 D-22 回退态（两层）结构，由上一段存量级联语义处理，此处跳过。
       评估见 docs/design/073-侧栏扁平化-spa.js依赖评估-2026-11-02.md。 */
    $('#menu .sc-sidebar__group').each(function() {
        var group = $(this);
        if (group.find('.collapse').length) return;
        group.toggleClass('d-none', group.find('li').length === 0);
    });
}

// Keep sidebar menu open based on current URL
$(function(){
    applyRoleMenuPermissions();
    var path=window.location.pathname;
    function normalizeMenuPath(v) {
        var a = document.createElement('a');
        a.href = v;
        var p = a.pathname || '/';
        return p.length > 1 ? p.replace(/\/+$/, '') : p;
    }
    $('#menu .collapse').each(function(){
        var current = normalizeMenuPath(path);
        var hasActive=$(this).find('a[href]').filter(function(){return normalizeMenuPath($(this).attr('href')) === current;}).length>0;
        if(hasActive)$(this).addClass('show');
    });
    /* 016：原「Prevent parent collapse when clicking child links」直绑 stopPropagation 已删——
       它掐断冒泡致 document 上的 click.spaNav / click.tabRecord 永不触发（子链接整页跳转根因）。
       折叠触发器仅 Bootstrap document 级委托 [data-bs-toggle="collapse"]，子链接不匹配该选择器
       且不嵌于父链接内部，点子链接本就不会折叠父菜单，守卫无的放矢。 */
});

// ═══ 顶部标签栏 ═══
var TAB_STORAGE_KEY = 'app_tabs_v2';
var TAB_PATH_TITLES = {
    '/medical-insurance': '\u533b\u4fdd\u7f34\u8d39',
    '/medical-insurance/': '\u533b\u4fdd\u7f34\u8d39'
};
var TAB_PATH_ICONS = {
    '/medical-insurance': 'bi-clipboard2-pulse',
    '/medical-insurance/': 'bi-clipboard2-pulse'
};
var TAB_ICONS = {
    '首页': 'bi-house-door',
    '居民查询': 'bi-search', '新增居民': 'bi-person-plus', '务工信息': 'bi-briefcase', '风险排查': 'bi-exclamation-triangle',
    '低保人员查询': 'bi-heart-pulse', '残疾人查询': 'bi-person-wheelchair', '关爱对象': 'bi-heart',
    '组织架构': 'bi-diagram-3', '党员管理': 'bi-flag',
    '待办提醒': 'bi-bell',
    '地力补贴': 'bi-cash-coin', '公益岗查询': 'bi-briefcase',
    '综治调解': 'bi-chat-dots',
    '证明开具': 'bi-file-text', '死亡上报': 'bi-heartbreak',
    '死亡名单对账': 'bi-clipboard-check',
    '用户管理': 'bi-people-gear', '角色管理': 'bi-shield', '界面设置': 'bi-palette',
    '字典管理': 'bi-book', '通知配置': 'bi-bell', '数据库安全': 'bi-database-gear',
    '操作日志': 'bi-journal-text',
};

function normalizeTabPath(key) {
    if (!key) return '/';
    return key.length > 1 ? key.replace(/\/+$/, '') : key;
}

function normalizeTabTitle(key, title) {
    var normalized = normalizeTabPath(key);
    return TAB_PATH_TITLES[normalized] || TAB_PATH_TITLES[normalized + '/'] || title || '\u9875\u9762';
}

function normalizeStoredTabs(tabs) {
    return (tabs || []).map(function(t) {
        t.title = normalizeTabTitle(t.key, t.title);
        return t;
    });
}

/* 032：标签按权限黑名单过滤（window.__SPA_CFG__.menuRules 唯一真源）。
   仅删除 bool=false 规则命中的标签；不匹配任何规则的路径一律放行
   （menuRules 仅显式规则，dashboard/详情页等不在其中，不得误删）。 */
function filterTabsByPermission(tabs) {
    var cfg = window.__SPA_CFG__ || {};
    var rules = cfg.menuRules || [];
    var denied = [];
    var hrefRe = /a\[href="([^"]+)"\]/;
    rules.forEach(function(rule) {
        if (rule[1]) return;  // 仅取显式 false 规则
        var m = hrefRe.exec(rule[0]);
        if (!m) return;
        var a = document.createElement('a');
        a.href = m[1];
        var p = a.pathname || '/';
        p = p.length > 1 ? p.replace(/\/+$/, '') : p;
        denied.push(p);
    });
    if (!denied.length) return tabs;
    return (tabs || []).filter(function(t) {
        return denied.indexOf(normalizeTabPath(t.key)) === -1;
    });
}

function getTabIcon(t) {
    var normalized = normalizeTabPath(t.key);
    return TAB_PATH_ICONS[normalized] || TAB_PATH_ICONS[normalized + '/'] || TAB_ICONS[t.title] || 'bi-file-earmark';
}

function getTabId(href) {
    var a = document.createElement('a'); a.href = href;
    return a.pathname || '/';
}

function getCurrentTabKey() {
    return window.location.pathname;
}

function initTabs() {
    // 032：normalizeStoredTabs 之后、saveTabs 之前过滤，被删标签回写后不再复活
    var tabs = filterTabsByPermission(normalizeStoredTabs(loadTabs()));
    var currentKey = getCurrentTabKey();
    var currentTitle = normalizeTabTitle(currentKey, $('#pageTitle').text().trim() || document.title || '\u9996\u9875');
    
    var exists = tabs.some(function(t) { return normalizeTabPath(t.key) === normalizeTabPath(currentKey); });
    if (!exists && currentKey !== '/auth/login') {
        tabs.push({ key: currentKey, title: currentTitle, time: Date.now() });
    }
    
    var seen = {};
    tabs = tabs.filter(function(t) {
        var k = normalizeTabPath(t.key);
        if (seen[k]) return false;
        seen[k] = true;
        return true;
    }).slice(-12);
    
    saveTabs(tabs);
    renderTabs(currentKey);
}

function loadTabs() {
    try {
        return normalizeStoredTabs(JSON.parse(sessionStorage.getItem(TAB_STORAGE_KEY)) || []);
    } catch(e) { return []; }
}

function saveTabs(tabs) {
    try { sessionStorage.setItem(TAB_STORAGE_KEY, JSON.stringify(tabs)); } catch(e) {} // 隐私模式/配额满写失败，静默降级（标签仅会话内便利，非关键）
}

function renderTabs(activeKey) {
    // 032：渲染前按权限过滤并回写（黑名单式；防止登出后残留标签复活）
    var tabs = filterTabsByPermission(normalizeStoredTabs(loadTabs()));
    saveTabs(tabs);
    // 076 RC-05（Q4 裁定）：≥2 个标签才显示整条——单标签时页签条与页头标题完全重复，
    // 整条隐藏（含容器，避免占位空条）；≥2 时保留导航功能，当前页以激活态在列
    $('#tabBar').toggleClass('d-none', tabs.length < 2);
    if (tabs.length < 2) { $('#tabScroll').html(''); return; }
    var html = '';
    tabs.forEach(function(t) {
        var active = normalizeTabPath(t.key) === normalizeTabPath(activeKey) ? ' active' : '';
        var icon = getTabIcon(t);
        var displayTitle = t.title;
        if (displayTitle.length > 8) displayTitle = displayTitle.slice(0, 8) + '…';
        html += '<div class="tab-item' + active + '" data-key="' + escAttr(t.key) + '">' +
            '<i class="bi ' + icon + ' tab-icon"></i>' +
            '<span>' + esc(displayTitle) + '</span>' +
            '<span class="tab-close" onclick="event.stopPropagation();closeTab(\'' + t.key.replace(/'/g, "\\'") + '\')">&times;</span>' +
            '</div>';
    });
    $('#tabScroll').html(html);

    // 104 P1-3：激活页签滚入条带视野（超宽被截时保证当前页签可见）+ 右缘渐隐态维护
    (function () {
        var el = document.getElementById('tabScroll');
        var bar = document.getElementById('tabBar');
        if (!el) return;
        var act = el.querySelector('.tab-item.active');
        if (act && act.scrollIntoView) act.scrollIntoView({ inline: 'nearest', block: 'nearest' });
        if (!bar) return;
        var updateFade = function () {
            bar.classList.toggle('tab-at-end', el.scrollWidth - el.scrollLeft - el.clientWidth < 4);
        };
        updateFade();
        if (!el.__tabFadeBound) {
            el.addEventListener('scroll', updateFade, { passive: true });
            el.__tabFadeBound = true;
        }
    })();

    $('.tab-item').on('click', function() {
        var key = $(this).data('key');
        if (key !== getCurrentTabKey()) {
            loadPage(key);
        }
    });
}

function closeTab(key) {
    var tabs = loadTabs();
    var idx = tabs.findIndex(function(t) { return t.key === key; });
    if (idx === -1) return;
    tabs.splice(idx, 1);
    saveTabs(tabs);
    renderTabs(getCurrentTabKey());
    
    if (key === getCurrentTabKey() && tabs.length > 0) {
        var target = tabs[Math.min(idx, tabs.length - 1)];
        loadPage(target.key);
    } else if (key === getCurrentTabKey()) {
        loadPage('/');
    }
}

// 菜单点击 → 记录标签页（仅记录实际页面，不记录折叠展开）
$(document).off('click.tabRecord', '.sidebar-menu a.nav-link[href]').on('click.tabRecord', '.sidebar-menu a.nav-link[href]', function(e) {
    var $this = $(this);
    var href = $this.attr('href');
    if (!href || href === '#' || href.indexOf('javascript:') === 0) return;
    if ($this.attr('data-bs-toggle') === 'collapse') return;
    if (href.indexOf('http') === 0) return;
    
    var key = getTabId(href);
    var title = $(this).text().trim() || '页面';
    title = title.replace(/[\n\r]/g, '').trim();
    title = normalizeTabTitle(key, title);
    
    var tabs = loadTabs();
    var exists = tabs.some(function(t) { return normalizeTabPath(t.key) === normalizeTabPath(key); });
    if (!exists) {
        tabs.push({ key: key, title: title, time: Date.now() });
        var seen = {};
        tabs = tabs.filter(function(t) {
            var k = normalizeTabPath(t.key);
            if (seen[k]) return false;
            seen[k] = true;
            return true;
        }).slice(-12);
        saveTabs(tabs);
    }
});

// Init on page load
$(function() {
    initTabs();
});


// ============================================================
//  菜单 SPA 导航 — AJAX 局部加载，无整页刷新
// ============================================================

var SPA_GLOBAL_SCRIPTS = [
    'jquery.min.js', 'bootstrap.bundle.min.js', 'js/app.js', 'js/crud.js', 'js/spa.js', 'js/shell.js',
    'bootstrap.min.css', 'bootstrap-icons'
];

// GLB-02：壳层全局样式白名单。页面级 extra_css 之外的样式才由 SPA 注入；
// 这些链接在 base.html 已随整页加载，重复注入会打乱层叠顺序。
var SPA_GLOBAL_STYLES = [
    'bootstrap.min.css', 'adminlte.min.css', 'bootstrap-icons',
    'css/tokens.css', 'css/app.css', 'css/skeleton.css'
];

function isGlobalScript(src) {
    if (!src) return false;
    return SPA_GLOBAL_SCRIPTS.some(function(s) { return src.indexOf(s) !== -1; });
}

function isGlobalStyle(href) {
    if (!href) return false;
    return SPA_GLOBAL_STYLES.some(function(s) { return href.indexOf(s) !== -1; });
}

function hasPageStyle(url) {
    var found = false;
    $('link[rel="stylesheet"]').each(function() {
        if (this.href === url) { found = true; return false; }
    });
    return found;
}

/** GLB-02：把 AJAX 响应中的页面级 extra_css 去重注入 <head>。
 *  SPA 只替换 #contentArea，而 {% block extra_css %} 在 <head>，不注入则页面样式缺失。
 *  接口面收在此一处：全局样式过滤、绝对 URL 去重、只接 http(s)。 */
function injectPageStyles($parsed) {
    $parsed.find('link[rel="stylesheet"][href]').each(function() {
        var raw = $(this).attr('href') || '';
        if (!raw || isGlobalStyle(raw)) return;
        var a = document.createElement('a');
        a.href = raw;
        if (!/^https?:$/.test(a.protocol)) return;
        if (hasPageStyle(a.href)) return;
        var link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = a.href;
        link.setAttribute('data-spa-page-css', '1');
        document.head.appendChild(link);
    });
}

/** 显示轻量加载占位 */
function showContentLoading() {
    var $area = $('#contentArea');
    // 保存当前滚动位置
    var scrollY = window.scrollY;
    $area.data('spa-scroll', scrollY);
    $area.html(
        '<div class="spa-loading-placeholder">' +
        '<div class="text-center"><div class="spinner-border text-primary mb-2" role="status"></div>' +
        '<div class="small text-muted">加载中...</div></div></div>'
    );
}

/** AJAX 加载页面内容替换到 contentArea */
function loadPage(url, pushState, skipRecord) {
    if (!url || url === '#' || url.indexOf('javascript:') === 0) return;
    if (url.indexOf('http') === 0 && url.indexOf(window.location.host) === -1) {
        window.location.href = url; return;
    }
    var a = document.createElement('a'); a.href = url;
    var fullUrl = a.href;
    var pathname = a.pathname;

    // 登录退出等整页跳转
    if (pathname.indexOf('/auth/') === 0) {
        window.location.href = fullUrl; return;
    }

    showContentLoading();

    $.get(fullUrl, function(html) {
        var $parsed = $('<div>').html(html);
        // 提取主内容区
        var $newContent = $parsed.find('#contentArea').first();
        if (!$newContent.length) {
            $newContent = $parsed.find('.container-fluid.p-3').first();
        }
        if (!$newContent.length) {
            window.location.href = fullUrl; return;
        }

        var contentHtml = $newContent.html();
        var newTitle = $parsed.filter('title').text() || $parsed.find('title').text() || '';

        // 更新导航栏标题（从加载的页面中提取 page_title）
        var newPageTitle = $parsed.find('#pageTitle').text().trim();
        if (newPageTitle) {
            $('#pageTitle').text(newPageTitle);
        }

        // 提取页面专用脚本
        var scripts = [];
        $parsed.find('script').each(function() {
            var $s = $(this);
            var src = $s.attr('src') || '';
            var code = $s.html() || '';
            if (isGlobalScript(src)) return;
            if (!src && !code.trim()) return;
            scripts.push({ src: src, code: code });
        });

        // GLB-02：页面级 extra_css 先注入，避免 SPA 进页丢样式（全局样式已过滤、同 URL 去重）
        injectPageStyles($parsed);

        // R-06：替换内容前触发页级 teardown，清理上一页残留的定时器/轮询
        if (typeof window.__SPA_TEARDOWN__ === 'function') { try { window.__SPA_TEARDOWN__(); } catch (e) {} }
        window.__SPA_TEARDOWN__ = null;
        if (window.SC && typeof window.SC.clearPageGlobals === 'function') { window.SC.clearPageGlobals(); }

        // 替换内容
        $('#contentArea').html(contentHtml);

        // 更新标题
        if (newTitle) document.title = newTitle;
        updateNavbarTitle();

        // 执行页面脚本
        executeScripts(scripts, function() {
            // 标签栏更新
            if (!skipRecord) initTabs();
            // 恢复滚动位置
            var savedScroll = $('#contentArea').data('spa-scroll');
            if (savedScroll !== undefined) window.scrollTo(0, savedScroll);
        });

        // 更新地址栏
        if (pushState !== false) {
            history.pushState({ path: pathname }, '', pathname);
        }

        // 重新高亮当前菜单
        highlightCurrentMenu(pathname);

        // bug1（2026-11-09）：窄视口换页即收起移动抽屉。sidebar-open 由移动版
        // 「展开菜单」加在 body 上，SPA 换页不清导致抽屉跨页残留、盖住内容与按钮。
        if (window.innerWidth < 992 && document.body.classList.contains('sidebar-open')) {
            document.body.classList.remove('sidebar-open');
        }

    }).fail(function() {
        window.location.href = fullUrl;
    });
}

/** 024 IIFE 根治：外部页面脚本源码缓存 + new Function 作用域重放。
 *  原实现每次切页向 body 追加 <script src> 全局执行——页面脚本顶层声明在全局
 *  作用域二次声明即抛 SyntaxError（const/let），且 script 标签随切页无限堆积。
 *  改为按 src 缓存源码文本，每次进页在全新函数作用域重放：
 *  - pages 页面脚本已 IIFE 化并显式导出内联事件所需符号（window.xxx），重放即重置页面状态；
 *  - /lib/ 第三方库（UMD 挂 window，幂等）只重放一次，避免每次进页重跑大体积库；
 *  - 标签零追加，全局作用域零污染。 */
var SPA_SCRIPT_TEXT_CACHE = {};   // src -> 源码文本
var SPA_LIB_PLAYED = {};          // /lib/ 库只重放一次

function replayScriptText(code, url) {
    try { (new Function(code))(); }
    catch (e) { console.warn('SPA script:', url, e); }
}

function loadExternalScript(url, done) {
    if (Object.prototype.hasOwnProperty.call(SPA_SCRIPT_TEXT_CACHE, url)) {
        if (!(url.indexOf('/lib/') >= 0 && SPA_LIB_PLAYED[url])) {
            if (url.indexOf('/lib/') >= 0) SPA_LIB_PLAYED[url] = true;
            replayScriptText(SPA_SCRIPT_TEXT_CACHE[url], url);
        }
        done();
        return;
    }
    $.ajax({
        url: url,
        type: 'GET',
        dataType: 'text',   // 显式 text：防止 jQuery 把 .js 自动当 script 执行破坏作用域隔离
        cache: true,
        success: function (text) {
            SPA_SCRIPT_TEXT_CACHE[url] = text;
            if (url.indexOf('/lib/') >= 0) SPA_LIB_PLAYED[url] = true;
            replayScriptText(text, url);
            done();
        },
        error: function () {
            console.warn('SPA script load fail:', url);
            done();
        }
    });
}

/** 逐条执行页面脚本 */
function executeScripts(scripts, callback) {
    var idx = 0;
    function next() {
        if (idx >= scripts.length) {
            if (callback) callback(); return;
        }
        var s = scripts[idx++];
        if (s.src) {
            var url = s.src.indexOf('http') === 0 ? s.src
                : window.location.origin + (s.src.indexOf('/') === 0 ? '' : '/') + s.src;
            loadExternalScript(url, next);
        } else {
            try { (new Function(s.code))(); } catch(e) { console.warn('SPA script:', e); }
            setTimeout(next, 10);
        }
    }
    next();
}

/** 同步导航栏标题 */
function updateNavbarTitle() {
    var $pt = $('#pageTitle');
    // 尝试从 content 里找 page_title 区域
    var cardHeader = $('#contentArea .card-header').first().clone();
    cardHeader.find('.d-flex, .btn, input, select, button, a').remove();
    var txt = cardHeader.text().trim();
    if (txt && txt.length < 25) {
        $pt.text(txt);
    }
}

/** 高亮当前菜单项 */
function highlightCurrentMenu(pathname) {
    $('#menu .nav-link').removeClass('active');
    $('#menu .collapse').removeClass('show');
    function normalizeMenuPath(v) {
        var a = document.createElement('a');
        a.href = v;
        var p = a.pathname || '/';
        return p.length > 1 ? p.replace(/\/+$/, '') : p;
    }
    var current = normalizeMenuPath(pathname);
    // R-087-13：深链路径 → 侧栏归属入口别名（检查台账不在菜单树 v4，归属应急管理叶）
    var MENU_PATH_ALIAS = {
        '/safety/checks': '/safety/emergency'
    };
    current = MENU_PATH_ALIAS[current] || current;
    $('#menu a[href]').each(function() {
        var href = $(this).attr('href');
        if (href && normalizeMenuPath(href) === current) {
            $(this).addClass('active');
            $(this).closest('.collapse').addClass('show');
        }
    });
}

// 拦截菜单中的页面链接 → AJAX 加载（仅拦截实际页面链接，不拦截折叠展开）
$(document).off('click.spaNav', '.sidebar-menu a.nav-link[href]').on('click.spaNav', '.sidebar-menu a.nav-link[href]', function(e) {
    var $this = $(this);
    var href = $this.attr('href');
    // 跳过：无链接、折叠展开、空链接、javascript 链接
    if (!href || href === '#' || href.indexOf('javascript:') === 0) return;
    // 跳过：Bootstrap collapse 父菜单（data-bs-toggle="collapse"）
    if ($this.attr('data-bs-toggle') === 'collapse') return;
    // 跳过：外部链接、登录退出
    if (href.indexOf('http') === 0 || href.indexOf('/auth/') === 0 || href.indexOf('/logout') >= 0) return;
    e.preventDefault();
    loadPage(href);
});

// 浏览器前进/后退
$(window).off('popstate.spaNav').on('popstate.spaNav', function(e) {
    var state = e.originalEvent.state;
    if (state && state.path) {
        loadPage(state.path, false, true);
    } else {
        loadPage(window.location.pathname, false, true);
    }
});

// 首次加载记录初始状态
$(function() {
    if (window.location.pathname !== '/auth/login') {
        history.replaceState({ path: window.location.pathname }, '', window.location.href);
        highlightCurrentMenu(window.location.pathname);
    }
});

function showProfile(){
    $('#profileModal').modal('show');
}
