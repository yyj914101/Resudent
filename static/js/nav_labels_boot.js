/* ==========================================================================
 * nav_labels_boot.js — 菜单自定义名「首帧前」引导（2026-11-16 闪烁修复）
 *
 * 问题：自定义菜单名只存在浏览器 localStorage，服务端 SSR 每次只能输出原始名。
 *   shell.js 在 body 末尾加载时 readyState 仍是 'loading'，旧实现等
 *   DOMContentLoaded 才 apply()；功能胶囊是普通 <a>（spa.js 只拦截侧栏链接），
 *   每次切换都整页导航 → 原名先被绘制、随后被 JS 改成自定义名，肉眼可见闪烁。
 *   实测量：修复前 ready:interactive=「民政对象管理」→ complete=「社会救助」。
 *
 * 方案：本脚本在 <head> 同步加载，注册 MutationObserver 监听文档解析；
 *   菜单 <a> 一插入 DOM 就在同一微任务里按 localStorage 覆盖文字并固化
 *   data-sc-nav-default（服务端原始名），早于任何首次绘制（FCP）。
 *   shell.js 的 apply() 保持幂等，作为兜底与后续重渲染/面板/storage 的唯一入口。
 *
 * 契约（由 tests/test_sidebar_custom_guard.py 钉住，勿单边修改）：
 *   KEY = 'sc.nav.labels'；选择器 '#menu a.nav-link[href]'；
 *   校验 1~16 字符、拒绝 < > 与控制字符；textContent 写入；
 *   原值写 data-sc-nav-default 供「全部恢复默认」使用。
 * ========================================================================== */
(function () {
    'use strict';
    var KEY = 'sc.nav.labels';
    var raw;
    try { raw = window.localStorage.getItem(KEY); } catch (e) { return; }
    if (!raw) return;
    var ov;
    try { ov = JSON.parse(raw); } catch (e) { return; }
    if (!ov || typeof ov !== 'object' || Array.isArray(ov)) return;
    if (!window.MutationObserver) return;      // 老浏览器由 shell.js 兜底

    function inMenu(a) {
        var p = a.parentNode;
        while (p && p !== document) {
            if (p.id === 'menu') return true;
            p = p.parentNode;
        }
        return false;
    }

    function applyOne(a) {
        var span = null, i;
        for (i = 0; i < a.children.length; i++) {
            if (a.children[i].tagName === 'SPAN') { span = a.children[i]; break; }
        }
        if (!span) return;
        // 先固化服务端原始名，避免 shell.js 把已覆盖的自定义名当默认值缓存
        if (!a.getAttribute('data-sc-nav-default')) {
            a.setAttribute('data-sc-nav-default', span.textContent.trim());
        }
        var key = (a.getAttribute('href') || '').split('#')[0].split('?')[0];
        var v = ov[key];
        if (typeof v !== 'string') return;
        v = v.trim();
        if (!v || v.length > 16 || /[<>\x00-\x1f]/.test(v)) return;
        if (span.textContent !== v) span.textContent = v;
    }

    function visit(node) {
        if (!node || node.nodeType !== 1) return;
        if (node.tagName === 'A' && node.classList.contains('nav-link') && inMenu(node)) {
            applyOne(node);
        }
        if (!node.querySelectorAll) return;
        var list = node.querySelectorAll('a.nav-link[href]');
        for (var i = 0; i < list.length; i++) {
            if (inMenu(list[i])) applyOne(list[i]);
        }
    }

    var mo;
    try {
        mo = new MutationObserver(function (recs) {
            for (var i = 0; i < recs.length; i++) {
                var added = recs[i].addedNodes;
                for (var j = 0; j < added.length; j++) visit(added[j]);
            }
        });
        mo.observe(document, { childList: true, subtree: true });
    } catch (e) { return; }

    // 解析结束即停止观察，运行期重渲染由 shell.js 自己的 #menu 观察器接管
    document.addEventListener('DOMContentLoaded', function () { mo.disconnect(); });
})();
