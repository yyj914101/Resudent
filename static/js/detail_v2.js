// detail_v2.js — 民政对象详情弹窗 v2 共享渲染器（ui1.md「全宽四列统一栅格只读规范 + 业务风险阻断」，
// 原型 code_artifact1.html；低保/残疾/特困/儿童四模块共用）。
// 纯渲染函数 + 一次性委托绑定，SPA 脚本重放幂等。样式见 components.css d2-* 块。
//
// cfg 契约：
//   modal      弹窗选择器（内含 .d2-shell 四层结构，元素 id 由各模块模板持有）
//   name/tags/subtitle/kpis   T1 头部
//   risk       null 或 { html, action: { id, label, onClick } }
//   body       正文容器选择器
//   sections   [{ title, note(html), card(bool 灰底卡槽+右上 note), cardNote, rows: [[{label, html, span2, stamp(html)}, ...]] }]
//   extraHtml  附加区块（如残疾·服务事项表）
//   footerSel  底部操作栏选择器
//   sensitive  [{ id, raw, masked }] —— 隐私脱敏开关的目标元素（checkbox#d2Privacy 由壳模板静态提供）
window.d2Copy = function (text, label) {
    var ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) { /* 剪贴板不可用静默 */ }
    document.body.removeChild(ta);
    if (window.showToast) showToast('已复制' + (label || '') + '：' + text, 'success');
};

window.d2MaskIdCard = function (raw) {
    var v = String(raw || '');
    if (v.length < 11) return v;
    return v.slice(0, 6) + '********' + v.slice(-4);
};

window.d2BankDisplay = function (raw) {
    var v = String(raw || '').replace(/\s+/g, '');
    if (!v) return { display: '', masked: '', copy: '' };
    var parts = v.replace(/(.{4})/g, '$1 ').trim();
    var seg = parts.split(' ');
    var masked = seg.map(function (s, i) {
        return (i > 0 && i < seg.length - 1) ? '****' : s;
    }).join(' ');
    return { display: parts, masked: masked, copy: v };
};

window.d2MonthsBetween = function (startDate, endMonth) {
    // 含首尾月数：2024-06-01 → 2026-08 = 27
    if (!startDate || !endMonth) return null;
    var m = String(endMonth).match(/^(\d{4})-(\d{1,2})/);
    var d = String(startDate).match(/^(\d{4})-(\d{1,2})/);
    if (!m || !d) return null;
    var n = (parseInt(m[1], 10) - parseInt(d[1], 10)) * 12 + (parseInt(m[2], 10) - parseInt(d[2], 10)) + 1;
    return n > 0 ? n : null;
};

window.d2Render = function (cfg) {
    var $m = $(cfg.modal);
    // T1 头部
    $m.find('.d2-name').text(cfg.name || '');
    $m.find('.d2-tags').html((cfg.tags || []).map(function (t) {
        return '<span class="d2-tag d2-tag--' + (t.tone || 'slate') + '">'
            + (t.ping ? '<span class="d2-pingdot"></span>' : '') + esc(t.t) + '</span>';
    }).join(''));
    $m.find('.d2-subtitle').html(cfg.subtitle || '');
    $m.find('.d2-kpis').html((cfg.kpis || []).length
        ? '<div class="d2-kpi__box">' + $.map(cfg.kpis, function (k) {
            return '<div class="d2-kpi"><span class="d2-kpi__label">' + esc(k.label) + '</span>'
                + '<div class="d2-kpi__val">' + (k.html || '<span class="d2-mute">—</span>') + '</div></div>';
        }).join('<span class="d2-kpi__sep"></span>') + '</div>'
        : '');
    // T2 风险横幅
    var $risk = $m.find('.d2-risk');
    if (cfg.risk) {
        $risk.html('<div class="d2-risk__text"><i class="bi bi-exclamation-triangle-fill d2-risk__icon"></i>'
            + '<span>' + cfg.risk.html + '</span></div>'
            + (cfg.risk.action ? '<button type="button" class="d2-risk__btn" id="' + escAttr(cfg.risk.action.id) + '">'
                + esc(cfg.risk.action.label) + '</button>' : ''));
        $risk.prop('hidden', false);
        if (cfg.risk.action && cfg.risk.action.onClick) {
            $('#' + cfg.risk.action.id).off('click.d2risk').on('click.d2risk', cfg.risk.action.onClick);
        }
    } else {
        $risk.prop('hidden', true).empty();
    }
    // T3 三段式四列栅格
    var h = '';
    $.each(cfg.sections || [], function (_, s) {
        h += '<section class="d2-section"><div class="d2-sechead">'
            + '<span class="d2-sechead__bar"></span><h4 class="d2-sechead__title">' + esc(s.title) + '</h4>'
            + (s.note ? '<span class="d2-sechead__note">' + s.note + '</span>' : '')
            + '<span class="d2-sechead__line"></span>'
            + (s.cardNote ? '<span class="d2-sechead__note">' + s.cardNote + '</span>' : '')
            + '</div>';
        if (s.card) h += '<div class="d2-accountcard">';
        h += '<div class="d2-grid">';
        $.each(s.rows, function (_, row) {
            $.each(row, function (_, c) {
                h += '<div class="d2-field' + (c.span2 ? ' d2-span2' : '') + '">'
                    + '<div class="d2-field__labelrow"><span class="d2-field__label">' + c.label + '</span>' + (c.stamp || '') + '</div>'
                    + '<div class="d2-field__valrow">' + (c.html || '<span class="d2-mute">—</span>') + '</div></div>';
            });
        });
        h += '</div>';
        if (s.card) h += '</div>';
        h += '</section>';
    });
    if (cfg.extraHtml) h += cfg.extraHtml;
    $(cfg.body).html(h);
    // T4 隐私脱敏（checkbox 由壳模板静态提供）
    var $p = $(cfg.footerSel).find('#d2Privacy');
    var items = cfg.sensitive || [];
    $p.off('change.d2').prop('checked', false).on('change.d2', function () {
        var masked = this.checked;
        $.each(items, function (_, it) {
            var $el = $('#' + it.id);
            if ($el.length) $el.text(masked ? it.masked : it.raw);
        });
        if (window.showToast) showToast(masked ? '已开启隐私脱敏显示' : '已恢复完整明文显示', 'success');
    });
    // 无敏感目标（如全部锁定态未渲染）时开关隐藏
    $(cfg.footerSel).find('.d2-privacy').prop('hidden', items.length === 0);
};

// 值单元构建辅助（等宽证件号/金额/日期 + 复制钮），供各模块 config 组装复用
window.d2ValMono = function (v) { return v ? '<span class="d2-mono d2-val--medium">' + esc(v) + '</span>' : ''; };
window.d2ValStrong = function (v) { return v ? '<span class="d2-val--strong">' + esc(v) + '</span>' : ''; };
window.d2ValMedium = function (v) { return v ? '<span class="d2-val--medium">' + esc(v) + '</span>' : ''; };
window.d2CopyBtn = function (raw, label) {
    return raw ? '<button type="button" class="d2-copybtn" title="复制' + esc(label || '') + '" onclick="d2Copy(\'' + String(raw).replace(/'/g, '') + '\', \'' + esc(label || '') + '\')"><i class="bi bi-clipboard"></i></button>' : '';
};
window.d2CopyBtnText = function (raw, label) {
    return raw ? '<button type="button" class="d2-copybtn d2-copybtn--text" onclick="d2Copy(\'' + String(raw).replace(/'/g, '') + '\', \'' + esc(label || '') + '\')">复制</button>' : '';
};

// ── 编辑弹窗 v2 表单构建辅助（ed2-*：低保/残疾/特困/儿童编辑弹窗，样式 components.css） ──
window.ed2Field = function (label, control, opts) {
    opts = opts || {};
    return '<div class="sc-field ed2-field' + (opts.span2 ? ' ed2-span2' : '') + '">'
        + '<label class="sc-field__label"><span>' + label + (opts.req ? '<span class="sc-field__required">*</span>' : '') + '</span>'
        + (opts.badge || '') + '</label>' + control + '</div>';
};
window.ed2Section = function (title, cellsHtml) {
    return '<section class="ed2-section"><div class="ed2-sechead">'
        + '<span class="ed2-sechead__bar"></span><h4 class="ed2-sechead__title">' + title + '</h4>'
        + '<span class="ed2-sechead__line"></span></div><div class="ed2-grid">' + cellsHtml + '</div></section>';
};
