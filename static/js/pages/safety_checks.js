/* safety_checks.js — 综合安全检查列表 + 闭环操作台（087，B-10）
 * 契约：API 形状见 routes/safety_emergency.py；状态机唯一真源 models/safety_check.HAZARD_STATUSES。
 * 命名空间解绑：SPA 每次进页重放本作用域，document 持久绑定须 off 后再 on（GLB 惯例）。
 */
(function () {
    var currentPage = 1;
    var currentCheckId = 0;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    /* ── 列表 ── */
    function loadChecks(page) {
        currentPage = page || 1;
        var params = { page: currentPage, per_page: 20 };
        var kw = $('#searchTitle').val().trim();
        if (kw) params.title = kw;
        $.getJSON('/safety/api/checks', params, function (resp) {
            if (!resp.success) { showToast(resp.msg || '加载失败', 'danger'); return; }
            var rows = resp.data || [];
            var html = '';
            $.each(rows, function (i, c) {
                var st = c.status_display || c.status;
                var statusBadge = st === '已闭环'
                    ? '<span class="sc-badge sc-badge--success">已闭环</span>'
                    : (st === '无隐患'
                        ? '<span class="sc-badge sc-badge--muted">无隐患</span>'
                        : '<span class="sc-badge sc-badge--warning">整改中</span>');
                html += '<tr data-check="' + c.id + '" style="cursor:pointer">' +
                    '<td>' + ((currentPage - 1) * 20 + i + 1) + '</td>' +
                    '<td>' + esc(c.title) + '</td>' +
                    '<td>' + esc(c.check_date) + '</td>' +
                    '<td>' + esc(c.check_type || '—') + '</td>' +
                    '<td>' + esc(c.location || '—') + '</td>' +
                    '<td>' + esc(c.checker || '—') + '</td>' +
                    '<td>' + c.hazard_total + ' 条' + (c.hazard_open > 0 ? '（未闭环 ' + c.hazard_open + '）' : '') + '</td>' +
                    '<td>' + statusBadge + '</td>' +
                    '<td><span class="sc-row-actions">' +
                    '<button type="button" class="sc-btn sc-btn--text is-danger" data-del="' + c.id + '">删除</button>' +
                    '</span></td></tr>';
            });
            if (!rows.length) html = '<tr><td colspan="9" class="text-center text-muted py-4">暂无检查记录，点击右上角「检查登记」开始</td></tr>';
            $('#tableBody').html(html);
            var total = resp.total || 0;   // R-103-04：后端已恒含 total（含 0），此处再兜底防 undefined
            $('#pageStat').text('共 ' + total + ' 条');
            $('#pageStat2').text('共 ' + total + ' 条');
            renderPagination('#pagination', Math.max(1, Math.ceil(total / 20)), currentPage, loadChecks);
        });
    }
    window.loadChecks = loadChecks;

    /* ── 检查单录入/编辑 ── */
    window.openCheckModal = function (id) {
        $('#checkForm')[0].reset();
        $('#checkId').val(0);
        $('#checkModalTitle').text('检查登记');
        if (id) {
            $.getJSON('/safety/api/checks/' + id, function (resp) {
                if (!resp.success) { showToast(resp.msg, 'danger'); return; }
                var c = resp.data;
                $('#checkModalTitle').text('编辑检查单');
                $('#checkId').val(c.id);
                $('#fTitle').val(c.title);
                $('#fDate').val(c.check_date);
                $('#fType').val(c.check_type);
                $('#fLocation').val(c.location);
                $('#fChecker').val(c.checker);
                $('#fRemark').val(c.remark);
                $('#checkModal').modal('show');
            });
        } else {
            $('#checkModal').modal('show');
        }
    };

    $('#checkForm').off('submit.sc').on('submit.sc', function (e) {
        e.preventDefault();
        $.post('/safety/api/checks', $(this).serialize(), null, 'json')
            .done(function (resp) {
                if (!resp.success) { showToast(resp.msg || '保存失败', 'danger'); return; }
                $('#checkModal').modal('hide');
                showToast('保存成功', 'success');
                loadChecks(currentPage);
            });
    });

    /* ── 闭环操作台 ── */
    function renderHazards(hazards) {
        if (!hazards.length) {
            $('#workHazardList').html('<p class="text-muted small">本检查单暂无隐患行，可在下方登记。</p>');
            return;
        }
        var html = '';
        $.each(hazards, function (i, h) {
            var badge = h.status === '已闭环' ? 'sc-badge--success'
                : (h.status === '待复核' ? 'sc-badge--primary' : 'sc-badge--warning');
            html += '<div class="border rounded p-2 mb-2">' +
                '<div class="d-flex justify-content-between align-items-start">' +
                '<div><strong>' + esc(h.description) + '</strong>' +
                ' <span class="sc-badge ' + badge + '">' + esc(h.status) + '</span>' +
                '<div class="text-muted small mt-1">等级 ' + esc(h.level) +
                (h.location ? ' · ' + esc(h.location) : '') +
                (h.rectify_owner ? ' · 责任人 ' + esc(h.rectify_owner) : '') +
                (h.rectify_deadline ? ' · 期限 ' + esc(h.rectify_deadline) : '') + '</div>' +
                (h.rectify_note ? '<div class="small mt-1">整改情况：' + esc(h.rectify_note) + '</div>' : '') +
                (h.review_note ? '<div class="small mt-1">复核意见：' + esc(h.review_note) + '</div>' : '') +
                '</div>' +
                '<div class="text-nowrap">' + flowButtons(h) + '</div>' +
                '</div></div>';
        });
        $('#workHazardList').html(html);
    }

    function flowButtons(h) {
        if (h.status === '待整改') {
            return '<button type="button" class="sc-btn sc-btn--text" data-rectify="' + h.id + '" data-desc="' + esc(h.description) + '">上报整改</button>';
        }
        if (h.status === '待复核') {
            return '<button type="button" class="sc-btn sc-btn--text is-success" data-review="pass" data-hazard="' + h.id + '" data-desc="' + esc(h.description) + '">复核通过</button>' +
                   '<button type="button" class="sc-btn sc-btn--text is-danger" data-review="reject" data-hazard="' + h.id + '" data-desc="' + esc(h.description) + '">驳回</button>';
        }
        return '';
    }

    function openWorkbench(checkId) {
        currentCheckId = checkId;
        $.getJSON('/safety/api/checks/' + checkId, function (resp) {
            if (!resp.success) { showToast(resp.msg, 'danger'); return; }
            $('#workCheckTitle').text(resp.data.title);
            // 2026-11-17 Master–Detail：操作列编辑钮撤除后，编辑入口收敛到操作台头部
            $('#workCheckEditBtn').attr('hidden', null).data('edit', checkId);
            renderHazards(resp.data.hazards || []);
            $('#workModal').modal('show');
        });
    }
    window.openWorkbench = openWorkbench;

    /* 隐患登记 */
    $('#hazardForm').off('submit.sc').on('submit.sc', function (e) {
        e.preventDefault();
        if (!currentCheckId) return;
        $.post('/safety/api/checks/' + currentCheckId + '/hazards', $(this).serialize(), null, 'json')
            .done(function (resp) {
                if (!resp.success) { showToast(resp.msg || '登记失败', 'danger'); return; }
                showToast('隐患已登记', 'success');
                $('#hazardForm').trigger('reset');
                openWorkbench(currentCheckId);
            });
    });

    /* 整改上报弹窗 */
    $(document).off('click.scRectify').on('click.scRectify', '[data-rectify]', function () {
        $('#flowHazardId').val($(this).data('rectify'));
        $('#flowAction').val('rectify');
        $('#flowTitle').text('整改上报');
        $('#flowNoteLabel').text('整改情况');
        $('#flowHint').text('提交后状态转为「待复核」，由复核人确认闭环。');
        $('#flowHazardDesc').text($(this).data('desc'));
        $('#flowNote').val('');
        $('#flowModal').modal('show');
    });

    /* 复核（通过/驳回）弹窗 */
    $(document).off('click.scReview').on('click.scReview', '[data-review]', function () {
        var action = $(this).data('review');
        $('#flowHazardId').val($(this).data('hazard'));
        $('#flowAction').val('review:' + action);
        $('#flowTitle').text(action === 'pass' ? '复核通过' : '驳回整改');
        $('#flowNoteLabel').text('复核意见');
        $('#flowHint').text(action === 'pass' ? '通过后隐患闭环，检查单随之收敛。' : '驳回后隐患退回「待整改」，整改人重新上报。');
        $('#flowHazardDesc').text($(this).data('desc'));
        $('#flowNote').val('');
        $('#flowModal').modal('show');
    });

    $('#flowForm').off('submit.sc').on('submit.sc', function (e) {
        e.preventDefault();
        var action = $('#flowAction').val();
        var hid = $('#flowHazardId').val();
        var url, payload;
        if (action === 'rectify') {
            url = '/safety/api/hazards/' + hid + '/rectify';
            payload = { rectify_note: $('#flowNote').val() };
        } else {
            url = '/safety/api/hazards/' + hid + '/review';
            payload = { action: action.split(':')[1], review_note: $('#flowNote').val() };
        }
        $.post(url, payload, null, 'json').done(function (resp) {
            if (!resp.success) { showToast(resp.msg || '操作失败', 'danger'); return; }
            $('#flowModal').modal('hide');
            showToast(resp.msg, 'success');
            openWorkbench(currentCheckId);
            loadChecks(currentPage);
        });
    });

    /* ── 行内动作委托 ── */
    // 2026-11-17 Master–Detail：行点击 → 闭环操作台（检查单详情即工作台，隐患闭环入口合一）
    $(document).off('click.scCheckRow').on('click.scCheckRow', 'tr[data-check]', function (e) {
        if ($(e.target).closest('button, a').length) return;
        openWorkbench($(this).data('check'));
    });
    $(document).off('click.scCheckEdit').on('click.scCheckEdit', '[data-edit]', function () { openCheckModal($(this).data('edit')); });
    $(document).off('click.scCheckDel').on('click.scCheckDel', '[data-del]', function () {
        var id = $(this).data('del');
        if (!window.confirm('删除检查单将连同其隐患行一并删除，确定？')) return;
        $.ajax({ url: '/safety/api/checks/' + id, method: 'DELETE' }).done(function (resp) {
            if (!resp.success) { showToast(resp.msg || '删除失败', 'danger'); return; }
            showToast('删除成功', 'success');
            loadChecks(currentPage);
        });
    });

    $('#searchTitle').off('keydown.sc').on('keydown.sc', function (e) { if (e.key === 'Enter') loadChecks(1); });

    loadChecks(1);
})();
