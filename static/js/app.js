// ===== P0-5 SPA 页级全局导出注册与自动清理（防跨页同名 window.* 覆盖）=====
(function () {
    'use strict';
    window.SC = window.SC || {};
    var _pageGlobalKeys = [];
    window.SC.exportPageGlobals = function (exportsMap) {
        if (!exportsMap || typeof exportsMap !== 'object') return;
        Object.keys(exportsMap).forEach(function (k) {
            window[k] = exportsMap[k];
            if (_pageGlobalKeys.indexOf(k) === -1) _pageGlobalKeys.push(k);
        });
    };

    window.SC.renderDetailCards = function (rows, longRows) {
        var shortList = rows || [];
        var longList = longRows || [];
        var numRe = /(日期|时间|电话|手机|身份证|金额|收入|年度|人数|号|周期|年龄)/;
        var grid = '<div class="sc-d3-grid4">';
        for (var i = 0; i < shortList.length; i++) {
            var lbl = shortList[i][0];
            var val = shortList[i][1];
            var empty = val === null || val === undefined || val === '';
            var cls = 'sc-d3-val' + (empty ? ' sc-d3-val--empty' : '') + (numRe.test(String(lbl)) ? ' sc-d3-val--num' : '');
            grid += '<div class="sc-d3-field"><span class="sc-d3-label">' + esc(lbl) + '</span>' +
                '<span class="' + cls + '">' + esc(empty ? '未填写' : val) + '</span></div>';
        }
        grid += '</div>';
        var html = '<div class="sc-d3-wrap"><section class="sc-d3-card">' +
            '<div class="sc-d3-card__head"><div class="sc-d3-card__title">' +
            '<span class="sc-d3-bar sc-d3-bar--1"></span><span class="sc-d3-idx sc-d3-idx--1">01</span>' +
            '<h4 class="sc-d3-card__name">基本信息</h4></div>' +
            '<span class="sc-d3-card__count">共 ' + shortList.length + ' 项字段</span></div>' +
            grid + '</section>';
        if (longList.length) {
            html += '<section class="sc-d3-card"><div class="sc-d3-card__head"><div class="sc-d3-card__title">' +
                '<span class="sc-d3-bar sc-d3-bar--2"></span><span class="sc-d3-idx sc-d3-idx--2">02</span>' +
                '<h4 class="sc-d3-card__name">备注与补充说明</h4></div></div>';
            for (var j = 0; j < longList.length; j++) {
                var lv = longList[j][1];
                html += '<div class="sc-d3-slot"><span class="sc-d3-slot__tag">' + esc(longList[j][0]) + '</span>' +
                    esc(lv === null || lv === undefined || lv === '' ? '未填写' : lv) + '</div>';
            }
            html += '</section>';
        }
        html += '</div>';
        return html;
    };

    window.SC.clearPageGlobals = function () {
        while (_pageGlobalKeys.length) {
            var k = _pageGlobalKeys.pop();
            try { delete window[k]; } catch (e) { window[k] = undefined; }
        }
    };
})();

// ===== 通用JS工具 =====
$(function() {
    // Auto close flash messages
    setTimeout(function() {
        $('.alert-dismissible').fadeOut(300);
    }, 4000);
});

// ===== 侧边栏切换 =====
// 窄屏 off-canvas 靠 body.sidebar-open 展开（adminlte.min.css 提供 margin-left:0；未引入其 JS）。
// 2026-11-11 修复：展开后 .app-sidebar(z-index:1038) 覆盖顶栏(.app-header:1034)最左侧，
// 同一汉堡按钮点不到 → 抽屉无法再次收起。补齐遮罩 + 侧栏内关闭按钮，并统一状态入口；
// MutationObserver 兜住 shell.js 的 Esc 与 spa.js 换页（二者直接删 class）造成的状态漂移。
(function () {
    var OVERLAY_CLS = 'sc-sidebar-overlay';
    var CLOSE_ID = 'scSidebarClose';
    var MOBILE_MQ = '(max-width: 991.98px)';

    function isMobile() {
        return window.matchMedia ? window.matchMedia(MOBILE_MQ).matches : window.innerWidth < 992;
    }

    function spriteBase() {
        var sample = document.querySelector('.sc-topbar__burger--mobile use');
        var href = sample && (sample.getAttribute('href') || sample.getAttribute('xlink:href')) || '';
        return href.split('#')[0] || '/static/lib/lucide/sprite.svg';
    }

    function ensureNodes() {
        var overlay = document.querySelector('.' + OVERLAY_CLS);
        if (!overlay) {
            overlay = document.createElement('div');
            overlay.className = OVERLAY_CLS;
            overlay.setAttribute('aria-hidden', 'true');
            overlay.addEventListener('click', function () { window.setSidebarOpen(false); });
            document.body.appendChild(overlay);
        }
        var sidebar = document.getElementById('sidebar');
        var close = document.getElementById(CLOSE_ID);
        if (!close && sidebar) {
            close = document.createElement('button');
            close.type = 'button';
            close.id = CLOSE_ID;
            close.className = 'sc-sidebar-close';
            close.setAttribute('aria-label', '收起菜单');
            var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            // 与 icon.html 宏同规格：lucide symbol 的线条由外层 svg 的 stroke 承载
            svg.setAttribute('class', 'sc-icon sc-icon--18');
            svg.setAttribute('viewBox', '0 0 24 24');
            svg.setAttribute('fill', 'none');
            svg.setAttribute('stroke', 'currentColor');
            svg.setAttribute('stroke-width', '1.7');
            svg.setAttribute('stroke-linecap', 'round');
            svg.setAttribute('stroke-linejoin', 'round');
            svg.setAttribute('aria-hidden', 'true');
            var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
            use.setAttribute('href', spriteBase() + '#x');
            svg.appendChild(use);
            close.appendChild(svg);
            close.addEventListener('click', function () { window.setSidebarOpen(false); });
            sidebar.appendChild(close);
        }
    }

    function sync() {
        var open = document.body.classList.contains('sidebar-open');
        ensureNodes();
        var burger = document.querySelector('.sc-topbar__burger--mobile');
        if (burger) {
            burger.setAttribute('aria-expanded', open ? 'true' : 'false');
            burger.setAttribute('aria-label', open ? '收起菜单' : '展开菜单');
        }
    }

    /** 唯一写入口：改 class 后立即同步遮罩/按钮语义；外部（Esc/换页）改 class 由观察器接住。 */
    window.setSidebarOpen = function (open) {
        document.body.classList.toggle('sidebar-open', !!open);
        sync();
    };

    // 保留全局名：base.html 的 inline onclick="toggleSidebar()" 依赖它（不改模板→SSR 快照零 diff）
    window.toggleSidebar = function () {
        window.setSidebarOpen(!document.body.classList.contains('sidebar-open'));
    };

    function init() {
        sync();
        if (window.MutationObserver) {
            new MutationObserver(sync).observe(document.body, {
                attributes: true, attributeFilter: ['class']
            });
        }
        window.addEventListener('resize', function () {
            // 回到桌面尺寸时清掉抽屉态，避免 sidebar-open 残留影响 >=992px 布局
            if (!isMobile() && document.body.classList.contains('sidebar-open')) {
                window.setSidebarOpen(false);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

// ===== 通用AJAX封装 =====
function apiPost(url, data, successCallback, errorCallback) {
    $.ajax({
        url: url,
        type: 'POST',
        data: data,
        contentType: 'application/x-www-form-urlencoded; charset=UTF-8',
        success: function(resp) {
            if (resp.success) {
                if (successCallback) successCallback(resp);
            } else {
                showToast(resp.msg || '操作失败', 'danger');
                if (errorCallback) errorCallback(resp);
            }
        },
        error: function(xhr) {
            xhr.__handled = true;
            var msg = '请求失败';
            if (xhr.status === 401) { window.location.href = '/auth/login'; return; }
            if (xhr.status === 403) msg = '权限不足';
            if (xhr.responseJSON && xhr.responseJSON.msg) msg = xhr.responseJSON.msg;
            showToast(msg, 'danger');
            if (errorCallback) errorCallback(xhr);
        }
    });
}

// ===== R-08：全局 AJAX 网络错误兜底 =====
// 21 个页面文件的主体请求裸 $.get/$.post/$.ajax 无 error/fail（网络失败静默，用户以为卡死）。
// 与其逐个补 .fail（约 85 处），用全局 ajaxError 钩子统一兜底；已由调用方 error 处理的请求会设 xhr.__handled 跳过。
$(document).ajaxError(function (event, xhr, settings) {
    if (xhr.__handled) return;
    if (xhr.status === 401) { window.location.href = '/auth/login'; return; }
    if (xhr.status === 403) { showToast('权限不足', 'danger'); return; }
    showToast('请求失败，请检查网络后重试', 'danger');
});
function showToast(message, type) {
    type = type || 'success';
    var icon = type === 'success' ? 'bi-check-circle' :
               type === 'danger' ? 'bi-exclamation-circle' :
               type === 'warning' ? 'bi-exclamation-triangle' : 'bi-info-circle';
    var bg = type === 'success' ? 'bg-success' :
             type === 'danger' ? 'bg-danger' :
             type === 'warning' ? 'bg-warning' : 'bg-info';
    var html = '<div class="toast align-items-center text-white ' + bg + ' border-0 show" role="alert">' +
        '<div class="d-flex"><div class="toast-body"><i class="bi ' + icon + ' me-2"></i>' + esc(message) + '</div>' +
        '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="关闭"></button></div></div>';
    var $container = $('.toast-container');
    if (!$container.length) {
        $container = $('<div class="toast-container position-fixed top-0 end-0 p-3"></div>').appendTo('body');
    }
    var $toast = $(html).appendTo($container);
    setTimeout(function() { $toast.fadeOut(300, function() { $(this).remove(); }); }, 3000);
}

// ===== HTML 转义（013 收敛：原 24 处页面本地定义 → 全局单源） =====
function esc(s) { return $('<span>').text(s == null ? '' : s).html(); }
// 属性上下文转义：在 esc 基础上再转义引号，防双引号 HTML 属性注入（R-02）
function escAttr(s) { return esc(String(s == null ? '' : s)).replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

// ===== 表单序列化为JSON =====
$.fn.serializeObject = function() {
    var o = {};
    var a = this.serializeArray();
    $.each(a, function() {
        if (o[this.name] !== undefined) {
            if (!o[this.name].push) { o[this.name] = [o[this.name]]; }
            o[this.name].push(this.value || '');
        } else {
            o[this.name] = this.value || '';
        }
    });
    return o;
};

// ===== 确认弹窗 =====
function confirmAction(msg, callback) {
    if (confirm(msg)) {
        callback();
    }
}

// ===== 格式化日期 =====
function formatDate(dateStr) {
    if (!dateStr) return '-';
    var d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
}

// ===== Modal 实例（013 收敛：mediation/medical 两页同构复制 → 全局单源）=====
function getModal(id) { return bootstrap.Modal.getOrCreateInstance(document.getElementById(id)); }

// ===== 统一分页模块（009：renderPagination 14→1）=====
// 接口：renderPagination(container, pages, current, onPage)
//   container : 分页容器 selector（如 '#pagination'）
//   pages     : 总页数
//   current   : 当前页（1-based）
//   onPage    : 翻页回调 function(page)
// 深实现：滑动窗口 ±2 + 首尾页 + 省略号 + «/» + active/disabled + 幂等绑定（.pg 命名空间防泄漏）
function renderPagination(container, pages, current, onPage) {
    var $c = $(container);
    if (pages <= 1) {
        // 074 RC-11：单页也渲染方形页码签（样图 v9-01 用户管理 / DoD §一④-6）。
        // 全站口径统一：只有一页时不显示「« »」翻页，仅一个激活态「1」。
        $c.html('<li class="page-item active"><a class="page-link" href="#" aria-current="page">1</a></li>');
        return;
    }
    var html = '';
    html += '<li class="page-item' + (current <= 1 ? ' disabled' : '') + '"><a class="page-link" href="#" data-page="' + (current - 1) + '">«</a></li>';
    var start = Math.max(1, current - 2), end = Math.min(pages, current + 2);
    if (start > 1) { html += pgBtn(1, current); if (start > 2) html += '<li class="page-item disabled"><span class="page-link">…</span></li>'; }
    for (var i = start; i <= end; i++) html += pgBtn(i, current);
    if (end < pages) { if (end < pages - 1) html += '<li class="page-item disabled"><span class="page-link">…</span></li>'; html += pgBtn(pages, current); }
    html += '<li class="page-item' + (current >= pages ? ' disabled' : '') + '"><a class="page-link" href="#" data-page="' + (current + 1) + '">»</a></li>';
    $c.html(html);
    $c.find('[data-page]').off('click.pg').on('click.pg', function (e) {
        e.preventDefault();
        var p = parseInt($(this).data('page'), 10);
        if (!isNaN(p) && p >= 1 && p <= pages && p !== current) onPage(p);
    });
}
function pgBtn(n, current) {
    return '<li class="page-item' + (n === current ? ' active' : '') + '"><a class="page-link" href="#" data-page="' + n + '">' + n + '</a></li>';
}

// ===== 宽表列显隐（062 P5：手写表复用；crud.js 的 columnToggle 绑定 crudTable 内部状态，不可直取）=====
// 接口：scColToggle.init(tableSel, {menuHost, storageKey, fixed, toggleLabel, countHost, countSuffix, defaultHidden})
//   tableSel   : 目标表选择器（须有 id，列头取 thead 最后一行 th）
//   menuHost   : 菜单挂载点（缺省插到表格所在 sc-card 的工具栏之后）
//   storageKey : localStorage 键（隐藏列下标 JSON 数组）
//   fixed      : 不可隐藏的列下标（0-based）
//   toggleLabel: 触发按钮文案（缺省「列设置」；075 RC-18 veteran 传「列选择」对齐样图）
//   countHost  : 「已选 X / 共 Y 列」计数行选择器（缺省不渲染；075 RC-18 对齐 v7-14）
//   countSuffix: 计数后缀文案（如「 · 横向滚动查看全部」）
//   defaultHidden: localStorage 为空时的默认隐藏列下标（075 RC-18：veteran 默认 16/24 列）
// X 计口径 = 可见列中除 fixed 外的可勾选列数；Y = 表头总列数（对齐样图「已选 16 / 共 24 列」）
// 深实现：幂等（重复 init 先删旧菜单/旧规则）+ 持久化 + 零侵入（<style> nth-child 规则，
//         行全量重绘后无需回调；colspan 空态行天然不命中 nth-child）
var scColToggle = (function () {
    function init(tableSel, opts) {
        opts = opts || {};
        var $table = $(tableSel);
        var tid = $table.attr('id');
        if (!$table.length || !tid) return;
        var storageKey = opts.storageKey || ('sc_col_hidden_' + tid);
        var fixed = opts.fixed || [];
        var hidden = [];
        try { hidden = JSON.parse(localStorage.getItem(storageKey) || '[]') || []; } catch (e) { hidden = []; }
        if (!hidden.length && opts.defaultHidden && opts.defaultHidden.length) {
            hidden = opts.defaultHidden.slice();
        }

        // 幂等：SPA 复进页会重放调用方 IIFE
        $('div[data-sc-col-toggle="' + tid + '"]').remove();
        $('style[data-sc-col-toggle="' + tid + '"]').remove();

        var $style = $('<style data-sc-col-toggle="' + tid + '"></style>').appendTo('head');

        var $count = opts.countHost ? $(opts.countHost) : $();

        function updateCount(totalCols) {
            if (!$count.length) return;
            // X = 可见列中除 fixed 外的可勾选列数；Y = 表头总列数
            var visibleSel = 0;
            for (var i = 0; i < totalCols; i++) {
                if (fixed.indexOf(i) !== -1) continue;
                if (hidden.indexOf(i) === -1) visibleSel++;
            }
            $count.text('已选 ' + visibleSel + ' / 共 ' + totalCols + ' 列' + (opts.countSuffix || ''));
        }

        var totalCols = $table.find('thead tr').last().find('th').length;

        function apply() {
            var sel = hidden.filter(function (i) { return fixed.indexOf(i) === -1; }).map(function (i) {
                var n = i + 1;
                return '#' + tid + ' th:nth-child(' + n + '),#' + tid + ' td:nth-child(' + n + ')';
            }).join(',');
            $style.text(sel ? sel + '{display:none}' : '');
            updateCount(totalCols);
        }

        var items = '';
        $table.find('thead tr').last().find('th').each(function (i) {
            if (fixed.indexOf(i) !== -1) return;
            var label = $(this).text().trim();
            if (!label) return;
            items += '<li><label class="dropdown-item"><input type="checkbox" class="form-check-input me-1 sc-col-check" data-col="' + i + '"' +
                (hidden.indexOf(i) === -1 ? ' checked' : '') + '> ' + esc(label) + '</label></li>';
        });

        var $menu = $('<div class="text-end" data-sc-col-toggle="' + tid + '"></div>').append(
            '<div class="dropdown d-inline-block">' +
            '<button type="button" class="sc-btn sc-btn--secondary dropdown-toggle" data-bs-toggle="dropdown">' +
            '<i class="bi bi-layout-three-columns"></i> ' + (opts.toggleLabel || '列设置') + '</button>' +
            '<ul class="dropdown-menu">' + items + '</ul></div>');
        var $host = $(opts.menuHost || '');
        if ($host.length) $host.append($menu);
        else $table.closest('.sc-card').find('.sc-toolbar').first().after($menu);

        $menu.on('change', '.sc-col-check', function () {
            var idx = parseInt($(this).attr('data-col'), 10);
            if (this.checked) hidden = hidden.filter(function (i) { return i !== idx; });
            else if (hidden.indexOf(idx) === -1) hidden.push(idx);
            try { localStorage.setItem(storageKey, JSON.stringify(hidden)); } catch (e) {}
            apply();
        });

        apply();
    }
    return { init: init };
})();

// ===== 字典下拉（R-10 收敛 + 029 端点收敛：数据源统一 dictStore，不再逐模块调各自的字典端点）=====
// 接口：dictSelect(category, selector, placeholder, cb)
//   category   : 字典分类名
//   selector   : 目标 select 选择器（可传 jQuery 对象）
//   placeholder: 首项占位文案（可选；省略且原首项为空值占位项时自动保留原占位项，勿依赖“不加占位项”副作用）
//   cb         : 完成回调（可选，如串行加载下一个字典）
// 内置转义：value 属性上下文 escAttr、label 文本上下文 esc（R-30 一并修复）
// ponytail: 原 endpoint 参数废弃（029 收敛到 dictStore）；保留该参数会误导调用方，故一并删除
function dictSelect(category, selector, placeholder, cb) {
    var $sel = $(selector);
    var oldVal = $sel.data('val') || $sel.val();
    // 029 复验修复：未显式传 placeholder 时，若原首项为空值占位项（如“请选择/留空默认户主所在组”），
    // 保留该占位项。否则 $sel.html() 整体重写会让空值字段静默选中首个字典值，
    // 保存时把空字段改写为首个字典值（静默数据损坏）。
    if (placeholder === undefined) {
        var _firstOpt = $sel.find('option:first');
        if (_firstOpt.length && _firstOpt.val() === '') {
            placeholder = _firstOpt.text();
        }
    }
    dictStore.ready(function () {
        var html = '';
        if (placeholder) html += '<option value="">' + esc(placeholder) + '</option>';
        $.each(dictStore.items(category), function (i, item) {
            html += '<option value="' + escAttr(item.value) + '">' + esc(item.label) + '</option>';
        });
        $sel.html(html);
        if (oldVal) $sel.val(oldVal).trigger('refresh.cs');   // R-4（2026-11-09）：程序化赋值后同步触发框
        if (cb) cb();
    });
}

// ===== 字典 Bundle 前端缓存（027：D3/D4）=====
// 接口：dictStore.ready(cb)   bundle 就绪后回调（失败/缓存降级也回调，store 为空对象）
//       dictStore.label(category, value)  值→标签；未命中返回 null（调用方原值兜底）
// 缓存：sessionStorage 一次全量 + 版本号协商（?v=<cached>），保存字典服务端内容哈希变化即失效。
// 静默失败：xhr.__handled = true 防全局 ajaxError toast；降级为原值渲染（与改造前行为等价）。
var dictStore = (function () {
    var KEY = 'shucun_dict_bundle';
    var version = '';
    // 033：新形状 {bundle: {分类名: {value: label}}, order: {分类名: [{value,label}]}}；
    //      旧缓存为纯映射 {分类名: {value: label}}（无 order），回退路径继续兼容。
    var store = null;
    var loading = false;
    var readyCbs = [];

    function cached() {
        try {
            var raw = sessionStorage.getItem(KEY);
            var o = raw ? JSON.parse(raw) : null;
            return (o && o.v && o.data) ? o : null;
        } catch (e) { return null; }
    }

    // 033：取值映射统一入口——新形状取 .bundle；旧缓存本身即映射
    function bundleOf(s) {
        return (s && s.bundle && typeof s.bundle === 'object') ? s.bundle : (s || {});
    }

    function fire() {
        var cbs = readyCbs;
        readyCbs = [];
        $.each(cbs, function (i, cb) { try { cb(); } catch (e) {} });
    }

    function load() {
        if (loading) return;
        loading = true;
        var c = cached();
        version = c ? c.v : '';
        $.getJSON('/dict/api/bundle', { v: version }, function (resp) {
            var c2 = cached();
            if (resp && resp.success && resp.data && resp.data.unchanged && c2) {
                store = c2.data;
            } else if (resp && resp.success && resp.data && resp.data.bundle) {
                // 033：同时缓存 bundle 映射与 order 有序结构（version 哈希已含两者，形状变化即失效）
                store = { bundle: resp.data.bundle, order: resp.data.order || {} };
                version = resp.data.v || '';
                try { sessionStorage.setItem(KEY, JSON.stringify({ v: version, data: store })); }
                catch (e) {}
            } else {
                store = c2 ? c2.data : {};
            }
            fire();
        }).fail(function (xhr) {
            xhr.__handled = true; // 静默：403/网络失败均不打扰页面，原值兜底
            store = (cached() || {}).data || {};
            fire();
        });
    }

    function ready(cb) {
        if (store !== null) { try { cb(); } catch (e) {} return; }
        readyCbs.push(cb);
        load();
    }

    function label(category, value) {
        if (!store) return null;
        var cat = bundleOf(store)[category];
        if (!cat) return null;
        var key = String(value == null ? '' : value);
        return Object.prototype.hasOwnProperty.call(cat, key) ? cat[key] : null;
    }

    // 033：优先 order 有序数组（sort_order 唯一真源）；旧缓存/断网降级无 order 时回退映射枚举
    //（2026-10-19 修正注释：旧文「插入序即 sort_order」系 029 错误假设——
    //   jsonify sort_keys 下对象 key 为 Unicode 序，且数字串 value 会被 JS 整形键重排。）
    function items(category) {
        if (!store) return [];
        if (store.order && store.order[category]) return store.order[category].slice();
        var cat = bundleOf(store)[category];
        if (!cat) return [];
        var out = [];
        for (var k in cat) if (Object.prototype.hasOwnProperty.call(cat, k)) out.push({ value: k, label: cat[k] });
        return out;
    }

    return { ready: ready, label: label, items: items, load: load };
})();

// ===== 导入块（R-11 收敛：7 页 startImport/doImport 同构复制 → 全局单源）=====
// 接口：importWizard({fileSel, resultSel, endpoint, onDone, extraData, progressSel, submitBtnSel})
//   fileSel     : 文件 input 选择器
//   resultSel   : 结果容器选择器
//   endpoint    : 导入端点
//   onDone      : 成功且有导入数后的回调（各页不同：load(1) / loadStats+loadList / list.reload 等）
//   extraData   : 额外表单字段对象（如 {mode: ...}），可选
//   progressSel : 上传进度条容器（复杂变体 resident/welfare），可选
//   submitBtnSel: 提交按钮（上传期间禁用），可选
// 统一：showToast 选文件提示（弃 alert，R-20）、esc(r.msg) 转义（R-05）、错误兜底
function importWizard(opts) {
    var f = $(opts.fileSel)[0];
    if (!f || !f.files || !f.files.length) { showToast('请选择文件', 'warning'); return; }
    var fd = new FormData();
    fd.append('file', f.files[0]);
    if (opts.extraData) { for (var k in opts.extraData) fd.append(k, opts.extraData[k]); }
    if (opts.progressSel) $(opts.progressSel).removeClass('d-none');
    if (opts.submitBtnSel) $(opts.submitBtnSel).prop('disabled', true);
    $(opts.resultSel).html('<div class="spinner-border spinner-border-sm text-primary"></div> 导入中...');
    $.ajax({
        url: opts.endpoint, type: 'POST', data: fd, processData: false, contentType: false,
        success: function(r) {
            if (opts.progressSel) $(opts.progressSel).addClass('d-none');
            if (opts.submitBtnSel) $(opts.submitBtnSel).prop('disabled', false);
            var cls = r.success ? 'success' : 'warning';
            $(opts.resultSel).removeClass('d-none').html('<div class="alert alert-' + cls + ' py-1 small mb-0">' + esc(r.msg) + '</div>');
            if (r.data && r.data.imported > 0 && opts.onDone) opts.onDone(r);
        },
        error: function() {
            if (opts.progressSel) $(opts.progressSel).addClass('d-none');
            if (opts.submitBtnSel) $(opts.submitBtnSel).prop('disabled', false);
            $(opts.resultSel).removeClass('d-none').html('<div class="alert alert-danger py-1 small">导入失败</div>');
        }
    });
}

// ===== 导入弹窗 File Drop Zone（2026-11-19 ADR-022 v4.0 换壳收敛：party/election/medical
// 三页内联同构 → 全局单源；社会救助五页（低保/残疾/关爱儿童/关爱特殊/公益岗）接入）=====
// 接口：wireImportDropzone({fileSel, dropSel, stagedSel, stagedNameSel, stagedMetaSel,
//   submitBtnSel, reselectSel, removeSel, modalSel})
//   fileSel     : 真实 file input（保留原 name/id 提交契约，仍是 importWizard/FormData 的数据源）
//   dropSel     : .pw-drop 投放区（点击/键盘/拖拽投放，四态样式见 import_modal_v2.css）
//   stagedSel   : .pw-staged 暂存卡容器
//   stagedNameSel/stagedMetaSel : 暂存卡文件名/大小槽位
//   submitBtnSel: 提交按钮（未选文件禁用、暂存后解禁；可省略）
//   reselectSel/removeSel : 暂存卡「重新选择/移除」（可省略）
//   modalSel    : 弹窗根（每次打开回到初始态；可省略）
// 返回 { stage, reset }；只动「提交前选文件」的 UI 形态，各页提交链路不变。
function wireImportDropzone(opts) {
    var $input = $(opts.fileSel), $drop = $(opts.dropSel), $staged = $(opts.stagedSel);
    if (!$drop.length || !$input.length) return null;
    var fmtSize = function (b) {
        if (b < 1024) return b + ' B';
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
        return (b / (1024 * 1024)).toFixed(2) + ' MB';
    };
    var stage = function (f) {
        if (opts.stagedNameSel) $(opts.stagedNameSel).text(f.name);
        if (opts.stagedMetaSel) $(opts.stagedMetaSel).text(fmtSize(f.size) + ' ·');
        $drop.addClass('d-none');
        $staged.removeClass('d-none');
        if (opts.submitBtnSel) $(opts.submitBtnSel).prop('disabled', false);
    };
    var reset = function () {
        $input.val('');
        $staged.addClass('d-none');
        $drop.removeClass('d-none');
        if (opts.submitBtnSel) $(opts.submitBtnSel).prop('disabled', true);
    };
    $drop.on('click', function () { $input.trigger('click'); })
        .on('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $input.trigger('click'); }
        })
        .on('dragover', function (e) { e.preventDefault(); $(this).addClass('is-drag'); })
        .on('dragleave drop', function () { $(this).removeClass('is-drag'); })
        .on('drop', function (e) {
            e.preventDefault();
            var dt = e.originalEvent && e.originalEvent.dataTransfer;
            if (dt && dt.files && dt.files.length) {
                $input[0].files = dt.files;
                stage(dt.files[0]);
            }
        });
    $input.on('change', function () { if (this.files[0]) stage(this.files[0]); });
    if (opts.reselectSel) $(opts.reselectSel).on('click', function () { $input.trigger('click'); });
    if (opts.removeSel) $(opts.removeSel).on('click', reset);
    if (opts.modalSel) $(opts.modalSel).on('show.bs.modal', reset);
    return { stage: stage, reset: reset };
}

// ===== 历史选项（localStorage datalist，013 收敛：certificate/death 两页同构复制 → 全局单源）=====
// R-31：外部写坏 localStorage 时安全返回 []（对齐 spa.js saveTabs 范式）
function readHistory(storageKey) {
    try { return JSON.parse(localStorage.getItem(storageKey) || '[]'); }
    catch (e) { return []; }
}
function loadHistoryOptions(storageKey, datalistSelector) {
    var history = readHistory(storageKey);
    var $datalist = $(datalistSelector);
    $datalist.empty();
    $.each(history, function(i, v) {
        $datalist.append('<option value="' + escAttr(v) + '">');
    });
}
function saveHistoryOption(storageKey, val, datalistSelector) {
    if (!val || !val.trim()) return;
    var history = readHistory(storageKey);
    var idx = $.inArray(val.trim(), history);
    if (idx > -1) history.splice(idx, 1);
    history.unshift(val.trim());
    if (history.length > 10) history = history.slice(0, 10);
    localStorage.setItem(storageKey, JSON.stringify(history));
    loadHistoryOptions(storageKey, datalistSelector);
}

// ===== 纯 DOM 自定义下拉（054-C）：根治原生 select 浮层在内嵌浏览器漂移 =====
// 根因：原生 <select> 浮层由内核绘制，iframe/webview 缩放下坐标错位，CSS 不可控。
// 方案：隐藏原生 select（保留 name/value → request.form + $().val() + on('change') 契约不变），
//       浮层改纯 DOM，position:absolute 相对 wrapper，同一文档流坐标系，跨环境稳定。
// 接口：customSelect($sel) 包裹单个；autoCustomSelect() 全自动扫描 + 观察新增节点。
function customSelect($sel) {
    if (!$sel.length || $sel.data('cs')) return;
    if ($sel.prop('multiple') || ($sel.prop('size') || 1) > 1) return;  // 多选/多行不接管
    $sel.data('cs', true).addClass('cs-native');

    var inlineStyle = $sel.attr('style') || '';
    var $wrap = $('<span class="custom-select"></span>').insertBefore($sel);
    if (/width/.test(inlineStyle)) {
        $wrap.css('display', 'inline-block');          // 筛选栏内联宽度：trigger 继承原宽
    } else {
        $wrap.css({ display: 'block', width: '100%' }); // 表单列：占满父容器
    }
    var $trg = $('<button type="button" class="form-select custom-select__trigger" aria-haspopup="listbox" aria-expanded="false"></button>');
    var $menu = $('<div class="custom-select__menu" role="listbox"></div>');
    if (/width/.test(inlineStyle)) $trg.attr('style', inlineStyle);
    $wrap.append($sel).append($trg).append($menu);

    var placeholder = (function () {
        var f = $sel.find('option:first');
        return (f.length && f.val() === '') ? f.text() : '';
    })();
    // 104 P2-6：占位文案作触发器可读名称（否则占位态按钮对读屏是纯空按钮）
    if (placeholder) $trg.attr('aria-label', placeholder);

    function currentOption() {
        var v = String($sel.val());
        var hit = null;
        $sel.find('option').each(function () {
            if (String(this.value) === v) { hit = this; return false; }
        });
        return hit;
    }

    function refreshTrigger() {
        var o = currentOption();
        var label = o ? o.textContent : '';
        $trg.text(label || placeholder || '\u00a0');
        $trg.toggleClass('is-placeholder', !label);
    }

    function renderMenu() {
        $menu.empty();
        var cur = String($sel.val());
        $sel.find('option').each(function () {
            var val = String(this.value);
            var $item = $('<div class="custom-select__item" role="option"></div>')
                .text(this.textContent)
                .data('value', val)
                .toggleClass('is-selected', val === cur);
            if (this.disabled) $item.addClass('is-disabled');
            $menu.append($item);
        });
    }

    function open() {
        renderMenu();
        refreshTrigger();   // 兜底：未显式同步的路径，展开时也以真值为准（P1-2）
        $menu.removeClass('is-open-up');
        $menu.addClass('is-open');
        $trg.attr('aria-expanded', 'true');
        // 2026-11-21 自适应展开方向：菜单默认向下；若底部溢出最近的滚动裁剪容器（overflow-y
        // auto/scroll）或视口、且 trigger 上方空间足够容纳整棵菜单，则向上展开。
        // 之前固定向下：抽屉/弹窗底部字段（petition 变更抽屉、医保 mi-fmodal 底部下拉）菜单被裁出
        // 可视区=人工"点击无反应"（elementFromPoint 命中 null/下层字段实证）。
        var rect = $menu[0].getBoundingClientRect();
        if (rect.height > 0 && rect.bottom > window.innerHeight || hasScrollClipOverflow()) {
            var topLimit = scrollClipTop();
            if (rect.top - topLimit > rect.height) $menu.addClass('is-open-up');
        }
    }

    // 最近一个 overflow-y 为 auto/scroll 的祖先（菜单会被它裁剪）；无则视口为界
    function scrollClipOverflow() {
        var el = $menu[0].parentElement;
        while (el && el !== document.body) {
            var ov = getComputedStyle(el).overflowY;
            if (ov === 'auto' || ov === 'scroll') return el;
            el = el.parentElement;
        }
        return null;
    }
    function hasScrollClipOverflow() {
        var el = scrollClipOverflow();
        if (!el) return false;
        return $menu[0].getBoundingClientRect().bottom > el.getBoundingClientRect().bottom;
    }
    function scrollClipTop() {
        var el = scrollClipOverflow();
        return el ? el.getBoundingClientRect().top : 0;
    }
    function close() {
        $menu.removeClass('is-open');
        $menu.removeClass('is-open-up');
        $trg.attr('aria-expanded', 'false');
    }
    function toggle() { $menu.hasClass('is-open') ? close() : open(); }

    $trg.on('click', function (e) { e.preventDefault(); toggle(); });
    $menu.on('click', '.custom-select__item', function () {
        if ($(this).hasClass('is-disabled')) return;
        $sel.val($(this).data('value')).trigger('change');   // 保留既有 change 契约
        close();
    });
    $trg.on('keydown', function (e) {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!$menu.hasClass('is-open')) open();
            var items = $menu.find('.custom-select__item:not(.is-disabled)');
            var idx = items.index($menu.find('.custom-select__item.is-highlight'));
            var n = (e.key === 'ArrowDown') ? idx + 1 : idx - 1;
            if (n < 0) n = 0; if (n >= items.length) n = items.length - 1;
            items.removeClass('is-highlight');
            if (items.length) items.eq(n).addClass('is-highlight');
        } else if (e.key === 'Enter' || e.key === ' ') {
            if ($menu.hasClass('is-open')) {
                var hl = $menu.find('.custom-select__item.is-highlight');
                if (hl.length) { hl.trigger('click'); e.preventDefault(); }
                else e.preventDefault();
            } else { e.preventDefault(); open(); }
        } else if (e.key === 'Escape') {
            close();
        }
    });

    // 值同步（054 复核 P1-2）：除 change 外，`form.reset()` 与程序化 `.val()` 都不触发 change，
    // 会让触发框显示值与实际提交值分叉。reset 由全局 reset.cs 监听兜底（见文件末尾）；
    // 程序化赋值后如需立刻同步，调用方 `.trigger('refresh.cs')` 即可，无需另设接口。
    $sel.on('change.cs refresh.cs', function () {
        refreshTrigger();
        $trg.removeClass('is-invalid');
    });
    $sel.data('csRefresh', refreshTrigger);
    if (window.MutationObserver) {
        new MutationObserver(function () { renderMenu(); refreshTrigger(); })
            .observe($sel[0], { childList: true });
    }

    refreshTrigger();
}

// R-4（2026-11-09）：页面回填普遍用裸 .val()（不触发 change/refresh.cs），触发框
// 会停留在回填前的显示值，与隐藏 select 的提交值分叉（是否户主 hidden=是/trigger=否）。
// 模态框 shown 时对账一次：所有 cs 接管的 select 重刷触发框。
$(document).on('shown.bs.modal', function (e) {
    $(e.target).find('select.cs-native').each(function () {
        var r = $(this).data('csRefresh');
        if (r) r();
    });
});

function autoCustomSelect() {
    function wrapAll(scope) {
        $(scope).find('select').addBack('select').each(function () { customSelect($(this)); });
    }
    wrapAll(document.body);
    if (window.MutationObserver) {
        new MutationObserver(function (muts) {
            muts.forEach(function (m) {
                $(m.addedNodes).each(function () {
                    if (this.nodeType === 1) wrapAll(this);
                });
            });
        }).observe(document.body, { childList: true, subtree: true });
    }
}

// 全局点击关闭浮层（点 wrap 外任意处）
// 2026-11-21 修复：原判断 closest('.custom-select') 不区分实例——点 B 下拉时 A 菜单不关闭，
// 双菜单同开后 toggle 语义让点击表现为"开关混乱/无反应"（信访维稳编辑弹窗人工验收实证）。
// 改为逐实例判断：点击目标不在该菜单自己的 wrapper 内才关闭。
$(document).on('click', function (e) {
    $('.custom-select__menu.is-open').each(function () {
        var $wrap = $(this).closest('.custom-select');
        if (!$wrap.length || !$(e.target).closest($wrap).length) $(this).removeClass('is-open')
            .siblings('.custom-select__trigger').attr('aria-expanded', 'false');
    });
});

autoCustomSelect();

// ===== 必填校验 + reset 同步（054 复核 P1-1 / P1-2）=====
// P1-1：原生 select 被 display:none 后仍参与 HTML5 校验（willValidate=true），浏览器会报
//   "An invalid form control with name='x' is not focusable" 然后**直接中止提交、且不派发
//   submit 事件** —— 所以监听 submit 拦不住（实测 console 三条同类报错）。
//   拦截点前移到「点击提交按钮」与「输入框回车」的 capture 阶段：preventDefault 掉默认提交
//   动作，原生校验根本不会启动，改由这里给出可见提示并聚焦触发框。
// P1-2：form.reset() 是同步改值、不派发 change，触发框文本会停在上一次的值
//       → 全局 reset.cs 监听，下一 tick 统一刷新（一次性覆盖所有 form，不必约束调用时机）。
function csGuardRequired(form, e) {
    var firstBad = null;
    $(form).find('select.cs-native').each(function () {
        if (this.willValidate && !this.checkValidity()) { firstBad = this; return false; }
    });
    if (!firstBad) return false;
    e.preventDefault();
    e.stopImmediatePropagation();
    $(firstBad).closest('.custom-select').find('.custom-select__trigger')
        .addClass('is-invalid').trigger('focus');
    var label = $(firstBad).closest('[class*="col-"]').find('.form-label').first()
        .text().replace(/\*/g, '').trim();
    showToast((label || '必填项') + '未选择', 'warning');
    return true;
}

document.addEventListener('click', function (e) {
    var btn = e.target && e.target.closest &&
        e.target.closest('button[type="submit"], button:not([type]), input[type="submit"]');
    if (btn && btn.form) csGuardRequired(btn.form, e);
}, true);

document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    var t = e.target;
    if (!t || !t.form || t.tagName === 'TEXTAREA' || t.tagName === 'BUTTON') return;
    csGuardRequired(t.form, e);
}, true);

$(document).on('reset.cs', 'form', function () {
    var form = this;
    setTimeout(function () {
        $(form).find('select.cs-native').each(function () {
            var refresh = $(this).data('csRefresh');
            if (typeof refresh === 'function') refresh();
        });
    }, 0);
});


// ===== Batch 2 通用 Click-or-Drop 文件投放区（自动增强带 data-sc-dropzone 的原生 file input）=====
(function () {
    'use strict';
    function fmtBytes(b) {
        if (b < 1024) return b + ' B';
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
        return (b / (1024 * 1024)).toFixed(2) + ' MB';
    }
    function enhanceOne(input) {
        if (!input || input.getAttribute('data-dz-ready') === '1') return;
        input.setAttribute('data-dz-ready', '1');
        var hint = input.getAttribute('data-dz-hint') || ('支持 ' + (input.getAttribute('accept') || '所有文件') + ' · 点击或拖拽文件至此');
        var wrap = document.createElement('div');
        wrap.className = 'sc-dz-wrap' + (input.hasAttribute('data-dz-compact') ? ' sc-dz-wrap--compact' : '');
        if (input.style && input.style.marginBottom) wrap.style.marginBottom = input.style.marginBottom;
        if (input.style && input.style.width && input.style.width !== 'auto') wrap.style.width = input.style.width;
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);
        input.classList.add('sc-dz-input-hidden');

        var box = document.createElement('div');
        box.className = 'sc-dz-box';
        box.setAttribute('role', 'button');
        box.setAttribute('tabindex', '0');
        box.innerHTML =
            '<div class="sc-dz-box__left">' +
                '<span class="sc-dz-box__icon"><i class="bi bi-cloud-arrow-up"></i></span>' +
                '<div class="sc-dz-box__text">' +
                    '<span class="sc-dz-box__title">点击选择文件，或拖拽至此</span>' +
                    '<span class="sc-dz-box__hint">' + esc(hint) + '</span>' +
                '</div>' +
            '</div>' +
            '<button type="button" class="sc-dz-box__clear" hidden>清除</button>';
        var errEl = document.createElement('div');
        errEl.className = 'sc-dz-error';
        errEl.setAttribute('role', 'alert');
        errEl.setAttribute('aria-live', 'polite');
        errEl.hidden = true;
        wrap.appendChild(box);
        wrap.appendChild(errEl);

        var titleEl = box.querySelector('.sc-dz-box__title');
        var hintEl = box.querySelector('.sc-dz-box__hint');
        var clearBtn = box.querySelector('.sc-dz-box__clear');

        function clearErr() {
            box.classList.remove('is-invalid');
            errEl.hidden = true;
            errEl.textContent = '';
        }

        function sync() {
            clearErr();
            var files = input.files;
            if (files && files.length) {
                var total = 0;
                for (var i = 0; i < files.length; i++) total += files[i].size;
                titleEl.textContent = files.length === 1 ? files[0].name : ('已选择 ' + files.length + ' 个文件');
                hintEl.textContent = fmtBytes(total) + ' · 已就绪';
                clearBtn.hidden = false;
            } else {
                titleEl.textContent = '点击选择文件，或拖拽至此';
                hintEl.textContent = hint;
                clearBtn.hidden = true;
            }
        }
        box.addEventListener('click', function (e) {
            if (e.target === clearBtn) {
                e.stopPropagation();
                input.value = '';
                input.dispatchEvent(new Event('change', { bubbles: true }));
                sync();
                return;
            }
            input.click();
        });
        box.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
        });
        box.addEventListener('dragover', function (e) { e.preventDefault(); box.classList.add('is-dragover'); });
        box.addEventListener('dragleave', function () { box.classList.remove('is-dragover'); });
        box.addEventListener('drop', function (e) {
            e.preventDefault();
            box.classList.remove('is-dragover');
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
                try {
                    input.files = e.dataTransfer.files;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    sync();
                } catch (err) {}
            }
        });
        input.addEventListener('invalid', function (e) {
            e.preventDefault();
            box.classList.add('is-invalid');
            errEl.innerHTML = '<i class="bi bi-exclamation-circle" aria-hidden="true"></i> ' + esc(input.validationMessage || '请先选择需要上传的文件');
            errEl.hidden = false;
            box.focus();
        });
        input.addEventListener('change', sync);
        if (input.form) input.form.addEventListener('reset', function () { setTimeout(sync, 0); });
    }
    window.SC = window.SC || {};
    window.SC.initDropzones = function (root) {
        var nodes = (root || document).querySelectorAll('input[type="file"][data-sc-dropzone]');
        for (var i = 0; i < nodes.length; i++) enhanceOne(nodes[i]);
    };
    $(function () { window.SC.initDropzones(document); });
    // SPA 切页后自动增强新注入的文件投放区
    if (window.MutationObserver) {
        var obs = new MutationObserver(function () { window.SC.initDropzones(document); });
        $(function () {
            var ca = document.getElementById('contentArea');
            if (ca) obs.observe(ca, { childList: true });
        });
    }
})();
