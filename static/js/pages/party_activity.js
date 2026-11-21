/* 083A 党务活动台账页面脚本。
 *
 * API 契约（routes/party.py 党务活动块）：
 *   GET  /party/activity/api/options           {dicts, branches, members}
 *   GET  /party/activity/api/list?page&limit&year&activity_type&branch&keyword
 *   GET  /party/activity/api/detail/<id>
 *   POST /party/activity/api/save              form: id/activity_type/title/activity_date/host/
 *                                              expected_count/actual_count/branch/content/remark
 *   POST /party/activity/api/delete/<id>
 *   GET  /party/activity/api/export?year&activity_type&branch&keyword
 *   GET  /party/activity/api/reviews?year
 *   POST /party/activity/api/review-save       form: year + member_ids[], grades[] 平行数组 + review_date
 *   GET  /api/stats/query?metric_key=party_activity  （MetricsEngine 单一取数入口）
 *
 * CSRF：POST 经 spa.js fetch 包装自动注入 X-CSRF-Token 头（base.html 注入）。
 * 评议等次分布 = 前端对逐人记录就地计数（与 MetricsEngine party_review_grade 同源同值）。
 */
(function () {
    'use strict';

    var API = {
        options: '/party/activity/api/options',
        list: '/party/activity/api/list',
        detail: '/party/activity/api/detail/',
        save: '/party/activity/api/save',
        remove: '/party/activity/api/delete/',
        export: '/party/activity/api/export',
        reviews: '/party/activity/api/reviews',
        reviewSave: '/party/activity/api/review-save',
        metrics: '/api/stats/query?metric_key=party_activity'
    };
    var PAGE_SIZE = 20;

    var byId = function (id) { return document.getElementById(id); };
    var state = { page: 1, pages: 1, total: 0 };
    var options = { dicts: {}, branches: [], members: [] };
    var rowsById = {};        // id → 最近一次列表响应的行数据（详情直建）
    var staged = [];          // 待保存评议清单：{party_member_id, member_name, grade, review_date}
    var existing = [];        // 已入库评议记录（当前年度）

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

    function fillSelect(el, items, placeholder) {
        if (!el) return;
        var html = '<option value="">' + esc(placeholder || '') + '</option>';
        for (var i = 0; i < items.length; i++) {
            html += '<option value="' + escAttr(items[i].value) + '">' + esc(items[i].label) + '</option>';
        }
        el.innerHTML = html;
    }

    function fillOptions() {
        var typeItems = (options.dicts['党务活动类型'] || []).map(function (v) {
            return { value: v, label: v };
        });
        fillSelect(byId('f_activity_type'), typeItems, '请选择');
        fillSelect(byId('filterType'), typeItems, '全部活动类型');
        var branchItems = (options.branches || []).map(function (b) {
            return { value: b, label: b };
        });
        fillSelect(byId('filterBranch'), branchItems, '全部支部');
        fillSelect(byId('f_branch'), branchItems, '请选择');
        var gradeItems = (options.dicts['民主评议等次'] || []).map(function (v) {
            return { value: v, label: v };
        });
        fillSelect(byId('reviewGrade'), gradeItems, '评议等次');
        var memberItems = (options.members || []).map(function (m) {
            return { value: m.id, label: m.name + (m.branch ? '（' + m.branch + '）' : '') };
        });
        fillSelect(byId('reviewMember'), memberItems, '选择党员（在册名单）');
    }

    function loadOptions() {
        request(API.options).then(function (body) {
            if (!body || !body.success) return;
            options = body.data || { dicts: {}, branches: [], members: [] };
            fillOptions();
        });
    }

    // ────────────────────────── 活动列表 ──────────────────────────
    function showState(kind, text) {
        var cls = (kind === 'error') ? 'text-danger' : 'text-muted';
        var icon = kind === 'loading'
            ? '<span class="spinner-border spinner-border-sm text-primary me-2" role="status"></span>' : '';
        byId('tableBody').innerHTML = '<tr><td colspan="9" class="text-center ' + cls +
            ' py-4">' + icon + text + '</td></tr>';
    }

    function typeBadgeClass(t) {
        if (t === '党员教育') return 'sc-badge--info';
        if (t === '组织生活') return 'sc-badge--success';
        if (t === '党风廉政') return 'sc-badge--warning';
        return 'sc-badge--muted';
    }

    function renderRows(list) {
        var html = '';
        var start = (state.page - 1) * PAGE_SIZE;
        rowsById = {};
        for (var i = 0; i < list.length; i++) {
            var item = list[i];
            if (item.id !== undefined && item.id !== null) rowsById[String(item.id)] = item;
            var id = escAttr(item.id);
            html += '<tr data-id="' + id + '" style="cursor:pointer">';
            html += '<td class="pa-num">' + (start + i + 1) + '</td>';
            html += '<td><span class="sc-badge ' + typeBadgeClass(item.activity_type) + '">' + esc(d(item.activity_type)) + '</span></td>';
            html += '<td class="sc-ellipsis" style="max-width:240px;font-weight:500" title="' + escAttr(item.title) + '">' + esc(d(item.title)) + '</td>';
            html += '<td class="pa-num">' + esc(d(item.activity_date)) + '</td>';
            html += '<td>' + esc(d(item.host)) + '</td>';
            html += '<td class="pa-num">' + esc(item.expected_count) + '</td>';
            html += '<td class="pa-num">' + esc(item.actual_count) + '</td>';
            html += '<td class="sc-ellipsis pa-branch-cell" title="' + escAttr(item.branch || '') + '">' + esc(d(item.branch)) + '</td>';
            html += '<td><span class="sc-row-actions">' +
                '<button type="button" class="sc-btn sc-btn--text" data-act="detail" data-id="' + id + '">详情</button>' +
                '<button type="button" class="sc-btn sc-btn--text" data-act="edit" data-id="' + id + '">编辑</button>' +
                '<button type="button" class="sc-btn sc-btn--text is-danger" data-act="delete" data-id="' + id + '">删除</button>' +
                '</span></td>';
            html += '</tr>';
        }
        byId('tableBody').innerHTML = html;
    }

    function renderPager() {
        // R-083A-P1-1：局部包装不得与全局 app.js renderPagination 同名（同名遮蔽→自递归
        // RangeError，分页与统计行双失效）；此处直接调用全局（container 为 jQuery 选择器，与 086 同法）
        renderPagination('#pagination', state.pages, state.page, function (np) { loadList(np); });
        byId('pageStat').textContent = '共 ' + state.total + ' 条，第 ' + state.page + '/' + state.pages + ' 页';
    }

    function loadMetrics() {
        request(API.metrics).then(function (body) {
            if (!body || !body.success) return;
            var data = (body.data && body.data.data) || {};
            var sc = data.session_count || 0;
            var es = data.expected_sum || 0;
            var ac = data.actual_sum || 0;
            byId('statLine').textContent = '本库活动 ' + sc + ' 场 · 应到合计 ' + es + ' · 实到合计 ' + ac;
            if (byId('kpiSession')) byId('kpiSession').textContent = sc;
            if (byId('kpiExpected')) byId('kpiExpected').textContent = es;
            if (byId('kpiActual')) byId('kpiActual').textContent = ac;
        });
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
                renderPager();
                loadMetrics();
            });
    }



    function openCreate() {
        byId('recordId').value = '0';
        byId('formTitle').textContent = '新增党务活动';
        byId('saveForm').reset();
        getModal('formModal').show();
    }

    function openEdit(id) {
        request(API.detail + id).then(function (body) {
            if (!body || !body.success) { report(body); return; }
            var item = body.data || {};
            byId('recordId').value = item.id;
            byId('formTitle').textContent = '编辑党务活动';
            byId('f_activity_type').value = item.activity_type || '';
            byId('f_activity_date').value = item.activity_date || '';
            byId('f_title').value = item.title || '';
            byId('f_host').value = item.host || '';
            byId('f_branch').value = item.branch || '';
            byId('f_expected_count').value = item.expected_count || 0;
            byId('f_actual_count').value = item.actual_count || 0;
            byId('f_content').value = item.content || '';
            byId('f_remark').value = item.remark || '';
            getModal('formModal').show();
        });
    }

    // 2026-11-20 ADR-022 紧凑单屏详情弹窗（4 列统一栅格 + 头部操作区）
    function openDetail(id) {
        var item = rowsById[String(id)];
        if (!item) { showToast('数据已刷新，请重试', 'warning'); return; }

        var exp = Number(item.expected_count || 0);
        var act = Number(item.actual_count || 0);
        var rate = exp > 0 ? Math.round((act / exp) * 100) + '%' : '—';
        var attendText = act + ' / ' + exp + (exp > 0 ? '（出勤率 ' + rate + '）' : '');

        byId('actDetailTitle').textContent = item.title || '党务活动详情';
        byId('actDetailTitle').title = item.title || '';
        var badgeEl = byId('actDetailBadge');
        if (badgeEl) {
            badgeEl.className = 'sc-badge ' + typeBadgeClass(item.activity_type);
            badgeEl.textContent = item.activity_type || '未分类';
            badgeEl.hidden = false;
        }
        if (byId('actDetailVline')) byId('actDetailVline').hidden = false;
        if (byId('actDetailMeta')) {
            byId('actDetailMeta').textContent = d(item.activity_date) + ' · 主持：' + d(item.host) + ' · 实到 ' + act + '/' + exp;
        }

        function valHtml(val, strong) {
            if (val === null || val === undefined || val === '') {
                return '<span class="pa-unset">未填写</span>';
            }
            return '<span class="pa-value' + (strong ? ' pa-value--strong' : '') + '">' + esc(val) + '</span>';
        }

        var html = '' +
            '<section class="pa-card">' +
                '<div class="pa-card__head">' +
                    '<div class="pa-card__title">' +
                        '<span class="pa-catbar pa-catbar--1"></span>' +
                        '<span class="pa-catindex pa-catindex--1">01</span>' +
                        '<h4 class="pa-card__name">基本与考勤信息</h4>' +
                    '</div>' +
                '</div>' +
                '<div class="pa-grid">' +
                    '<div class="pa-field"><span class="pa-label">活动类型</span>' + valHtml(item.activity_type, true) + '</div>' +
                    '<div class="pa-field"><span class="pa-label">活动日期</span><span class="pa-value pa-num">' + esc(d(item.activity_date)) + '</span></div>' +
                    '<div class="pa-field"><span class="pa-label">主持/承办</span>' + valHtml(item.host, false) + '</div>' +
                    '<div class="pa-field"><span class="pa-label">出勤人数</span><span class="pa-value pa-num">' + esc(attendText) + '</span></div>' +
                    '<div class="pa-field pa-field--half"><span class="pa-label">所属党支部</span>' + valHtml(item.branch, false) + '</div>' +
                    '<div class="pa-field pa-field--half"><span class="pa-label">备注</span>' + valHtml(item.remark, false) + '</div>' +
                '</div>' +
            '</section>' +
            '<section class="pa-card">' +
                '<div class="pa-card__head">' +
                    '<div class="pa-card__title">' +
                        '<span class="pa-catbar pa-catbar--2"></span>' +
                        '<span class="pa-catindex pa-catindex--2">02</span>' +
                        '<h4 class="pa-card__name">活动内容摘要</h4>' +
                    '</div>' +
                '</div>' +
                '<div class="pa-content-slot">' +
                    (item.content ? esc(item.content) : '<span class="pa-unset">暂无活动内容记录</span>') +
                '</div>' +
            '</section>';

        byId('actDetailBody').innerHTML = html;
        byId('actDetailBtns').innerHTML =
            '<button type="button" class="sc-btn sc-btn--primary pa-btn-sm" data-act="edit" data-id="' + escAttr(id) + '"><i class="bi bi-pencil"></i> 编辑</button>';
        getModal('actDetailModal').show();
    }

    function saveRow() {
        var form = byId('saveForm');
        if (!form.reportValidity()) return;
        var fd = new FormData(form);
        var body = request(API.save, { method: 'POST', body: new URLSearchParams(fd) });
        body.then(function (resp) {
            report(resp, '保存成功');
            if (resp && resp.success) {
                getModal('formModal').hide();
                loadList(state.page);
                loadMetrics();   // R-103-05：统计行随新增立即刷新（配合后端写后失效）
            }
        });
    }

    function deleteRow(id) {
        confirmAction('确定删除该活动记录？', function () {
            request(API.remove + id, { method: 'POST' }).then(function (resp) {
                report(resp, '已删除');
                if (resp && resp.success) {
                    loadList(state.page);
                    loadMetrics();   // R-103-05：统计行随删除立即刷新
                }
            });
        });
    }

    function exportLedger() {
        var qs = new URLSearchParams(new FormData(byId('searchForm'))).toString();
        window.location.href = API.export + (qs ? '?' + qs : '');
    }

    // ────────────────────────── 民主评议区 ──────────────────────────
    function renderReviewBody() {
        var html = '';
        var rows = existing.concat(staged.map(function (s) {
            return { party_member_id: s.party_member_id, member_name: s.member_name,
                     grade: s.grade, review_date: s.review_date, staged: true };
        }));
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            var mid = escAttr(r.party_member_id);
            html += '<tr>';
            html += '<td>' + esc(d(r.member_name)) + (r.staged ? ' <span class="sc-field__hint">（待保存）</span>' : '') + '</td>';
            html += '<td>' + esc(d(r.grade)) + '</td>';
            html += '<td>' + esc(d(r.review_date)) + '</td>';
            html += '<td><span class="sc-row-actions">' +
                '<button type="button" class="sc-btn sc-btn--text is-danger" data-act="review-remove" data-id="' + mid + '"' +
                (r.staged ? '' : ' disabled title="已入库记录走覆盖保存更新"') + '>移除</button>' +
                '</span></td>';
            html += '</tr>';
        }
        if (!rows.length) {
            html = '<tr><td colspan="4" class="text-center text-muted py-3">选择年度后「载入年度记录」，或在上方加入待保存清单</td></tr>';
        }
        byId('reviewBody').innerHTML = html;
        renderDistribution();
    }

    function renderDistribution() {
        // 等次分布 = 逐人记录求和（已入库 + 待保存清单合并计数）
        var tally = {};
        (existing.concat(staged)).forEach(function (r) {
            tally[r.grade] = (tally[r.grade] || 0) + 1;
        });
        var grades = options.dicts['民主评议等次'] || [];
        var parts = [];
        grades.forEach(function (g) {
            if (tally[g]) parts.push(g + ' ' + tally[g] + ' 人');
        });
        Object.keys(tally).forEach(function (g) {
            if (grades.indexOf(g) < 0) parts.push(g + ' ' + tally[g] + ' 人');
        });
        var total = existing.length + staged.length;
        byId('reviewDistribution').textContent = total
            ? '等次分布：' + parts.join(' · ') + '（合计 ' + total + ' 人）' : '';
    }

    function loadReviews() {
        var year = (byId('reviewYear').value || '').trim();
        if (!year) { showToast('请先填写评议年度', 'warning'); return; }
        request(API.reviews + '?year=' + encodeURIComponent(year)).then(function (body) {
            if (!body || !body.success) { report(body); return; }
            existing = body.data || [];
            renderReviewBody();
        });
    }

    function stageReview() {
        var year = (byId('reviewYear').value || '').trim();
        if (!year) { showToast('请先填写评议年度', 'warning'); return; }
        var memberEl = byId('reviewMember');
        var gradeEl = byId('reviewGrade');
        var mid = memberEl.value;
        var grade = gradeEl.value;
        if (!mid) { showToast('请选择党员', 'warning'); return; }
        if (!grade) { showToast('请选择评议等次', 'warning'); return; }
        if (existing.some(function (r) { return String(r.party_member_id) === String(mid); }) ||
            staged.some(function (r) { return String(r.party_member_id) === String(mid); })) {
            showToast('该党员本年度已有记录，保存清单将覆盖更新等次', 'warning');
        }
        staged = staged.filter(function (r) { return String(r.party_member_id) !== String(mid); });
        staged.push({
            party_member_id: mid,
            member_name: memberEl.options[memberEl.selectedIndex] ?
                memberEl.options[memberEl.selectedIndex].text : '',
            grade: grade,
            review_date: byId('reviewDate').value || ''
        });
        renderReviewBody();
    }

    function removeStaged(mid) {
        staged = staged.filter(function (r) { return String(r.party_member_id) !== String(mid); });
        renderReviewBody();
    }

    function saveReviews() {
        var year = (byId('reviewYear').value || '').trim();
        if (!year) { showToast('请先填写评议年度', 'warning'); return; }
        if (!staged.length) { showToast('清单为空，请先加入评议记录', 'warning'); return; }
        var fd = new URLSearchParams();
        fd.set('year', year);
        if (byId('reviewDate').value) fd.set('review_date', byId('reviewDate').value);
        staged.forEach(function (r) {
            fd.append('member_ids', r.party_member_id);
            fd.append('grades', r.grade);
        });
        request(API.reviewSave, { method: 'POST', body: fd }).then(function (resp) {
            report(resp, '评议记录已保存');
            if (resp && resp.success) {
                staged = [];
                loadReviews();
            }
        });
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
        byId('exportLink').addEventListener('click', function (e) { e.preventDefault(); exportLedger(); });
        byId('tableBody').addEventListener('click', function (e) {
            // 按钮命中走行内操作（详情/编辑/删除），否则整行点击开详情
            var btn = e.target.closest('button[data-act]');
            if (btn) {
                var act = btn.getAttribute('data-act');
                var rid = btn.getAttribute('data-id');
                if (act === 'delete') deleteRow(rid);
                else if (act === 'edit') openEdit(rid);
                else if (act === 'detail') openDetail(rid);
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
        byId('reviewLoadButton').addEventListener('click', loadReviews);
        byId('reviewStageButton').addEventListener('click', stageReview);
        byId('reviewSaveButton').addEventListener('click', saveReviews);
        byId('reviewBody').addEventListener('click', function (e) {
            var btn = e.target.closest('button[data-act="review-remove"]');
            if (btn) removeStaged(btn.getAttribute('data-id'));
        });
        // 2026-11-17：详情弹窗底部「编辑」按钮（事件委托，内容重渲染仍生效）
        byId('actDetailBtns').addEventListener('click', function (e) {
            var btn = e.target.closest('button[data-act="edit"]');
            if (btn) { getModal('actDetailModal').hide(); openEdit(btn.getAttribute('data-id')); }
        });
    }

    function boot() {
        bind();
        loadOptions();
        loadList(1);
        var y = byId('reviewYear');
        if (y && !y.value) y.value = new Date().getFullYear();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
