/* 083B 党员发展页面脚本（五阶段名册 + 阶段迁移状态机）。
 *
 * API 契约（routes/party.py 党员发展块）：
 *   GET  /party/development/api/options        {dicts, branches}
 *   GET  /party/development/api/list?page&limit&stage&branch&keyword
 *   GET  /party/development/api/detail/<id>    （含 logs 阶段迁移日志）
 *   POST /party/development/api/save           form: id/name/id_card/phone/gender/branch/
 *                                              introducer/apply_date/remark
 *   POST /party/development/api/transition     form: id/to_stage/stage_date/confirm_writeback
 *                                              非法迁移 HTTP 409（跳级/回退/同态）
 *   GET  /party/development/api/export?stage&branch
 *
 * 纪律（R-083A-P1-1 教训）：本文件内不得定义与全局助手（renderPagination 等）同名的函数。
 * CSRF：POST 经 spa.js fetch 包装自动注入 X-CSRF-Token 头。
 */
(function () {
    'use strict';

    var API = {
        options: '/party/development/api/options',
        list: '/party/development/api/list',
        detail: '/party/development/api/detail/',
        save: '/party/development/api/save',
        transition: '/party/development/api/transition',
        export: '/party/development/api/export'
    };
    var PAGE_SIZE = 20;

    var byId = function (id) { return document.getElementById(id); };
    var state = { page: 1, pages: 1, total: 0 };
    var stages = [];            // 党员发展阶段（字典序 = 状态机顺序）
    var rowsById = {};

    function d(v) { return v === null || v === undefined || v === '' ? '—' : v; }

    function request(url, opts) {
        return fetch(url, opts || {}).then(function (resp) {
            if (resp.status === 401) { window.location.href = '/auth/login'; return null; }
            return resp.text().then(function (text) {
                var body = null;
                try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
                if (!body || typeof body !== 'object') {
                    body = { success: false, msg: '返回内容解析失败（HTTP ' + resp.status + '）' };
                }
                body.__status = resp.status;
                return body;
            });
        }, function () {
            return { success: false, msg: '网络异常，请检查连接后重试', __status: 0 };
        });
    }

    function report(body, okText) {
        if (!body) return;
        if (body.success) { showToast(okText || body.msg || '操作成功', 'success'); return; }
        var tone = (body.__status === 400 || body.__status === 409) ? 'warning' : 'danger';
        showToast(body.msg || ('操作失败（HTTP ' + (body.__status || 0) + '）'), tone);
    }

    function nextStageOf(current) {
        var i = stages.indexOf(current);
        return (i >= 0 && i + 1 < stages.length) ? stages[i + 1] : '';
    }

    function fillOptions() {
        stages = options_stage_list();
        fillSelect(byId('filterStage'), stages.map(function (s) { return { value: s, label: s }; }), '全部阶段');
        fillSelect(byId('f_branch'), (optionsData.branches || []).map(function (b) {
            return { value: b, label: b };
        }), '请选择');
    }

    var optionsData = { dicts: {}, branches: [] };
    function options_stage_list() {
        return optionsData.dicts['党员发展阶段'] || [];
    }

    function fillSelect(el, items, placeholder) {
        if (!el) return;
        var html = '<option value="">' + esc(placeholder || '') + '</option>';
        for (var i = 0; i < items.length; i++) {
            html += '<option value="' + escAttr(items[i].value) + '">' + esc(items[i].label) + '</option>';
        }
        el.innerHTML = html;
    }

    function loadOptions() {
        request(API.options).then(function (body) {
            if (!body || !body.success) return;
            optionsData = body.data || { dicts: {}, branches: [] };
            fillOptions();
        });
    }

    // ────────────────────────── 列表 ──────────────────────────
    function showState(kind, text) {
        var cls = (kind === 'error') ? 'text-danger' : 'text-muted';
        var icon = kind === 'loading'
            ? '<span class="spinner-border spinner-border-sm text-primary me-2" role="status"></span>' : '';
        byId('tableBody').innerHTML = '<tr><td colspan="8" class="text-center ' + cls +
            ' py-4">' + icon + text + '</td></tr>';
    }

    function renderRows(list) {
        var html = '';
        var start = (state.page - 1) * PAGE_SIZE;
        rowsById = {};
        for (var i = 0; i < list.length; i++) {
            var item = list[i];
            rowsById[String(item.id)] = item;
            var id = escAttr(item.id);
            html += '<tr data-id="' + id + '" style="cursor:pointer">';
            html += '<td>' + (start + i + 1) + '</td>';
            html += '<td>' + esc(d(item.name)) + '</td>';
            html += '<td>' + esc(d(item.id_card)) + '</td>';
            html += '<td><span class="sc-badge sc-badge--info">' + esc(d(item.stage)) + '</span></td>';
            html += '<td>' + esc(d(item.apply_date)) + '</td>';
            html += '<td>' + esc(d(item.branch)) + '</td>';
            html += '<td>' + esc(d(item.introducer)) + '</td>';
            var next = nextStageOf(item.stage);
            html += '<td><span class="sc-row-actions">' +
                (next ? '<button type="button" class="sc-btn sc-btn--text" data-act="transition" data-id="' + id + '">推进</button>' : '') +
                '</span></td>';
            html += '</tr>';
        }
        byId('tableBody').innerHTML = html;
    }

    function refreshPager() {
        // 全局 app.js renderPagination（jQuery 选择器容器，086 同法）；局部命名避开全局同名
        renderPagination('#pagination', state.pages, state.page, function (np) { loadList(np); });
        byId('pageStat').textContent = '共 ' + state.total + ' 条，第 ' + state.page + '/' + state.pages + ' 页';
    }

    function loadList(page) {
        var p = Math.max(1, page || 1);
        state.page = p;
        showState('loading', '加载中…');
        var qs = new URLSearchParams(new FormData(byId('searchForm'))).toString();
        request(API.list + '?page=' + p + '&limit=' + PAGE_SIZE + (qs ? '&' + qs : ''))
            .then(function (body) {
                if (!body) return;
                if (!body.success) { showState('error', body.msg || '查询失败'); return; }
                var data = body.data || {};
                var rows = Array.isArray(data) ? data : (data.data || []);
                state.total = body.total || 0;
                state.pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
                if (!rows.length) { showState('empty', '暂无记录'); }
                else { renderRows(rows); }
                refreshPager();
            });
    }

    // ────────────────────────── 新增 / 编辑 ──────────────────────────
    function openCreate() {
        byId('recordId').value = '0';
        byId('formTitle').textContent = '新增发展对象';
        byId('saveForm').reset();
        byId('f_stage_ro').value = stages[0] || '申请人';
        getModal('formModal').show();
    }

    function openEdit(id) {
        request(API.detail + id).then(function (body) {
            if (!body || !body.success) { report(body); return; }
            var item = body.data || {};
            byId('recordId').value = item.id;
            byId('formTitle').textContent = '编辑发展对象';
            byId('f_name').value = item.name || '';
            byId('f_gender').value = item.gender || '';
            byId('f_id_card').value = item.id_card || '';
            byId('f_phone').value = item.phone || '';
            byId('f_branch').value = item.branch || '';
            byId('f_apply_date').value = item.apply_date || '';
            byId('f_introducer').value = item.introducer || '';
            byId('f_remark').value = item.remark || '';
            byId('f_stage_ro').value = item.stage || '';
            getModal('formModal').show();
        });
    }

    function saveRow() {
        var form = byId('saveForm');
        if (!form.reportValidity()) return;
        var fd = new FormData(form);
        request(API.save, { method: 'POST', body: new URLSearchParams(fd) }).then(function (resp) {
            report(resp, '保存成功');
            if (resp && resp.success) {
                getModal('formModal').hide();
                loadList(state.page);
            }
        });
    }

    // ────────────────────────── 推进阶段 ──────────────────────────
    // 2026-11-19 详情改版：行内「推进」与详情头部「推进下一阶段」共用同一业务面
    function openTransitionFor(item) {
        if (!item) return;
        var next = nextStageOf(item.stage);
        if (!next) { showToast('已在最终阶段（正式党员）', 'warning'); return; }
        byId('transitionId').value = item.id;
        byId('transitionToStage').value = next;
        byId('t_name').value = item.name || '';
        byId('t_current').value = item.stage || '';
        byId('t_next').value = next;
        byId('t_stage_date').value = '';
        byId('t_writebackWrap').hidden = (next !== '正式党员');
        byId('t_writeback').value = '';
        clearTransitionError();
        getModal('transitionModal').show();
    }

    function openTransition(id) {
        openTransitionFor(rowsById[String(id)]);
    }

    // R-103-03：409/失败原因渲染在弹窗内（toast 会被打开中的 modal backdrop 遮住）
    function showTransitionError(msg) {
        var el = byId('transitionError');
        if (!el) return;
        el.textContent = msg || '阶段推进不合法，请核对后重试';
        el.hidden = false;
    }

    function clearTransitionError() {
        var el = byId('transitionError');
        if (el) { el.textContent = ''; el.hidden = true; }
    }

    function submitTransition() {
        var form = byId('transitionForm');
        if (!form.reportValidity()) return;
        var fd = new FormData(form);
        request(API.transition, { method: 'POST', body: new URLSearchParams(fd) }).then(function (resp) {
            if (resp && !resp.success) showTransitionError(resp.msg);
            report(resp, '阶段已推进');
            if (resp && resp.success) {
                getModal('transitionModal').hide();
                loadList(state.page);
            }
        });
    }

    // ────────────────────────── 详情 / 导出 ──────────────────────────
    // 2026-11-19 详情弹窗按设计稿重做：头部单行档案组 + 三段卡片（01 四列网格 /
    // 02 五阶段进度条 + 备注槽 / 03 迁移日志）+ 底部周期状态栏。
    // 编辑档案/修改备注/推进下一阶段仍走 formModal/transitionModal 既有业务面。
    var STAGE_DATE_KEYS = ['apply_date', 'activist_date', 'develop_date', 'probate_date', 'full_member_date'];

    function unsetPill() {
        return '<span class="pdv-unset">未登记</span>';
    }

    function valueOrPill(v) {
        return v ? '<span class="pdv-value pdv-num">' + esc(v) + '</span>' : unsetPill();
    }

    function renderStepper(item) {
        var activeIdx = stages.indexOf(item.stage);
        var node = [];
        for (var i = 0; i < stages.length && i < STAGE_DATE_KEYS.length; i++) {
            var dateV = item[STAGE_DATE_KEYS[i]] || '';
            var isFinal = (i === stages.length - 1);
            // 状态：有日期=完成；当前阶段（且非最终档）=active；最终档有日期仍按设计呈完成勾
            var status = dateV ? 'done' : 'todo';
            if (dateV && i === activeIdx && !isFinal) status = 'active';
            var circle, dateHtml;
            if (status === 'done') {
                circle = '<div class="pdv-step__circle"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg></div>';
                dateHtml = '<span class="pdv-step__date pdv-num">' + esc(dateV) + '</span>';
            } else if (status === 'active') {
                circle = '<div class="pdv-step__circle pdv-num">' + (i + 1) + '</div>';
                dateHtml = '<span class="pdv-step__date pdv-num">' + esc(dateV) + ' · 当前</span>';
            } else {
                circle = '<div class="pdv-step__circle pdv-num">' + (i + 1) + '</div>';
                dateHtml = '<span class="pdv-step__date">待推进</span>';
            }
            node.push('<div class="pdv-step pdv-step--' + status + '">' + circle +
                '<span class="pdv-step__name">' + esc(stages[i]) + '</span>' + dateHtml + '</div>');
            if (i < stages.length - 1) {
                node.push('<div class="pdv-step-line pdv-step-line--' +
                    (status === 'done' ? 'done' : 'todo') + '"></div>');
            }
        }
        var currentStepNum = activeIdx >= 0 ? activeIdx + 1 : Math.min(stages.length, 5);
        byId('pdvProgress').textContent = '当前进度：第 ' + currentStepNum + ' / ' + stages.length + ' 阶段';
        byId('pdvStepper').innerHTML = '<div class="pdv-step-row">' + node.join('') + '</div>';
    }

    function cycleText(item) {
        if (!item.apply_date) return '培养周期：申请日期未登记 · 档案状态正常';
        var days = Math.max(0, Math.floor((Date.now() - new Date(item.apply_date + 'T00:00:00').getTime()) / 86400000));
        return '培养周期：已历时 ' + days + ' 天 · 档案状态正常';
    }

    function openDetail(id) {
        request(API.detail + id).then(function (body) {
            if (!body || !body.success) { report(body); return; }
            var item = body.data || {};
            byId('d_name').textContent = item.name || '—';
            byId('d_stage').textContent = item.stage || '—';
            byId('d_meta').textContent = (d(item.gender)) + ' · 党支部：' + d(item.branch);
            byId('pd_name').textContent = item.name || '—';
            byId('pd_gender').textContent = d(item.gender);
            byId('pd_idcard').innerHTML = valueOrPill(item.id_card);
            byId('pd_phone').innerHTML = valueOrPill(item.phone);
            byId('pd_branch').textContent = d(item.branch);
            byId('pd_branch').title = item.branch || '';
            byId('pd_introducer').textContent = d(item.introducer);
            byId('pd_remark').textContent = item.remark || '暂无考察备注';
            renderStepper(item);
            byId('pdvCycle').textContent = cycleText(item);
            var logs = item.logs || [];
            var html = '';
            for (var j = 0; j < logs.length; j++) {
                var g = logs[j];
                html += '<tr><td>' + esc(g.from_stage + ' → ' + g.to_stage) + '</td>' +
                    '<td class="pdv-num">' + esc(d(g.stage_date)) + '</td>' +
                    '<td class="pdv-num">' + esc(d(g.operator)) + '</td>' +
                    '<td title="' + escAttr(g.writeback_before) + '">' +
                    esc(d(g.writeback_before)) + '</td></tr>';
            }
            if (!logs.length) {
                html = '<tr><td colspan="4" class="text-center text-muted py-3">暂无迁移记录</td></tr>';
            }
            byId('logBody').innerHTML = html;
            byId('logCount').textContent = '共 ' + logs.length + ' 次';
            // 编辑档案 / 修改备注 / 推进下一阶段：统一接既有业务弹窗（与行内入口同源）
            var editBtn = byId('detailEditBtn');
            if (editBtn) {
                editBtn.hidden = false;
                editBtn.onclick = function () { getModal('detailModal').hide(); openEdit(id); };
            }
            var remarkBtn = byId('detailRemarkBtn');
            if (remarkBtn) {
                remarkBtn.onclick = function () { getModal('detailModal').hide(); openEdit(id); };
            }
            var advBtn = byId('detailAdvanceBtn');
            if (advBtn) {
                var next = nextStageOf(item.stage);
                if (next) {
                    advBtn.disabled = false;
                    advBtn.textContent = '+ 推进下一阶段';
                    advBtn.onclick = function () { getModal('detailModal').hide(); openTransitionFor(item); };
                } else {
                    advBtn.disabled = true;
                    advBtn.textContent = '已完成全部发展流程';
                    advBtn.onclick = null;
                }
            }
            getModal('detailModal').show();
        });
    }

    function exportLedger() {
        var qs = new URLSearchParams(new FormData(byId('searchForm'))).toString();
        window.location.href = API.export + (qs ? '?' + qs : '');
    }

    // ────────────────────────── 事件绑定 ──────────────────────────
    function bind() {
        byId('searchForm').addEventListener('submit', function () { loadList(1); });
        byId('resetButton').addEventListener('click', function () {
            byId('searchForm').reset();
            loadList(1);
        });
        byId('addButton').addEventListener('click', openCreate);
        byId('saveForm').addEventListener('submit', function (e) { e.preventDefault(); saveRow(); });
        byId('transitionForm').addEventListener('submit', function (e) { e.preventDefault(); submitTransition(); });
        byId('exportLink').addEventListener('click', function (e) { e.preventDefault(); exportLedger(); });
        byId('tableBody').addEventListener('click', function (e) {
            // 2026-11-17 Master–Detail：按钮命中走按钮语义，否则整行点击开详情
            var btn = e.target.closest('button[data-act]');
            if (btn) {
                var id = btn.getAttribute('data-id');
                var act = btn.getAttribute('data-act');
                if (act === 'transition') openTransition(id);
                return;
            }
            var tr = e.target.closest('tr[data-id]');
            if (tr) openDetail(tr.getAttribute('data-id'));
        });
        byId('pagination').addEventListener('click', function (e) {
            var a = e.target.closest('a[data-page]');
            if (!a) return;
            var page = parseInt(a.getAttribute('data-page'), 10);
            if (page) loadList(page);
        });
    }

    function boot() {
        bind();
        loadOptions();
        loadList(1);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
