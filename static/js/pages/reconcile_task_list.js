(function () {
var currentTaskId = null;
var currentType = '';

function hashTabKey() {
    var m = /^#tab=([^&]+)/.exec(location.hash || '');
    return m ? decodeURIComponent(m[1]) : 'all';
}

function activateHashTab() {
    var key = hashTabKey();
    var $a = $('#typeTabs a[data-key="' + key + '"]');
    if (!$a.length) $a = $('#typeTabs a[data-key="all"]');
    $('#typeTabs a').removeClass('active');
    $a.addClass('active');
    loadResults($a.data('type') || '');
}

function refreshQuarters() {
    $.get('/reconcile/api/tasks', function (res) {
        if (!res.success) return;
        var qs = {};
        (res.data || []).forEach(function (t) { if (t.quarter) qs[t.quarter] = 1; });
        var $sel = $('#quarterFilter');
        var cur = $sel.val();
        $sel.find('option:not(:first)').remove();
        Object.keys(qs).sort().reverse().forEach(function (q) {
            $sel.append('<option value="' + q + '">' + q + '</option>');
        });
        $sel.val(cur);
    });
}

function loadTasks() {
    var q = $('#quarterFilter').val() || '';
    $.get('/reconcile/api/tasks', {quarter: q}, function (res) {
        if (!res.success) { showToast ? showToast(res.msg, 'danger') : alert(res.msg); return; }
        var rows = (res.data || []);
        $('#taskStat').text('共 ' + rows.length + ' 个任务');
        if (!rows.length) {
            $('#taskBody').html('<tr><td colspan="10" class="text-center text-muted py-4">暂无对账任务（季度首启自动生成提醒）</td></tr>');
            return;
        }
        // 067：任务状态为真实字段（pending/done/failed），展示文案对齐 v8-09 样图
        var STATUS_LABEL = {pending: '比对中', done: '已核对', failed: '比对失败'};
        var STATUS_BADGE = {pending: 'sc-badge--warning', done: 'sc-badge--success', failed: 'sc-badge--danger'};
        var html = '';
        rows.forEach(function (t) {
            var src = t.source_deleted ? '<span class="text-success"><i class="bi bi-check-circle"></i> 已删除</span>'
                : (t.has_source ? '<span class="text-muted">待删除（≤30 天）</span>' : '—');
            // 2026-11-17 Master–Detail：行点击开任务详情，操作列撤「详情」按钮
            html += '<tr data-tid="' + t.id + '" style="cursor:pointer">' +
                '<td>#' + t.id + '</td>' +
                '<td>' + t.source_label + '</td>' +
                '<td>' + (t.quarter || '—') + '</td>' +
                '<td><span class="sc-badge ' + (STATUS_BADGE[t.status] || 'sc-badge--muted') + '">' + (STATUS_LABEL[t.status] || t.status) + '</span></td>' +
                '<td>' + (t.summary['已登记'] || 0) + '</td>' +
                '<td>' + (t.summary['疑似未登记'] || 0) + '</td>' +
                '<td>' + (t.summary['无档案'] || 0) + '</td>' +
                '<td>' + (t.summary['名单外'] || 0) + '</td>' +
                '<td>' + src + '</td>' +
                '<td></td>' +
                '</tr>';
        });
        $('#taskBody').html(html);
        // 2026-11-17 Master–Detail：行点击 → 任务详情（详情为页内结果卡展开）
        $('#taskBody tr[data-tid]').on('click', function () { openDetail($(this).data('tid')); });
    });
}

function doImport() {
    var file = $('#fileInput')[0].files[0];
    if (!file) { showToast ? showToast('请选择 .xlsx / .xls / .xlsb / .csv / .xlsm 名单文件', 'warning') : alert('请选择文件'); return; }
    var fd = new FormData();
    fd.append('file', file);
    fd.append('source', $('#sourceSel').val());
    $('#importProgress').removeClass('d-none');
    $('#importMsg').text('上传并提交比对…');
    $.ajax({
        url: '/reconcile/import', type: 'POST', data: fd,
        processData: false, contentType: false,
        success: function (res) {
            if (!res.success) {
                $('#importProgress').addClass('d-none');
                showToast ? showToast(res.msg, 'danger') : alert(res.msg);
                return;
            }
            $('#importMsg').text('已提交，比对进行中（A1 任务 #' + res.data.a1_task_id + '）…');
            pollStatus(res.data.a1_task_id);
            refreshQuarters();
        },
        error: function (xhr) {
            $('#importProgress').addClass('d-none');
            showToast ? showToast('导入失败：' + (xhr.responseJSON && xhr.responseJSON.msg || xhr.status), 'danger') : alert('导入失败');
        }
    });
}

function pollStatus(a1Id) {
    var timer = setInterval(function () {
        $.get('/reconcile/api/import-status/' + a1Id, function (res) {
            if (!res.success) { clearInterval(timer); $('#importProgress').addClass('d-none'); return; }
            var s = res.data.status;
            if (s === 'done') {
                clearInterval(timer);
                $('#importProgress').addClass('d-none');
                $('#importMsg').text('');
                showToast ? showToast('对账比对完成', 'success') : alert('对账比对完成');
                loadTasks();
            } else if (s === 'failed') {
                clearInterval(timer);
                $('#importProgress').addClass('d-none');
                showToast ? showToast('对账失败：' + (res.data.error || ''), 'danger') : alert('对账失败');
                loadTasks();
            }
        });
    }, 1500);
}

function openDetail(tid) {
    currentTaskId = tid;
    $.get('/reconcile/api/task/' + tid, function (res) {
        if (!res.success) { showToast ? showToast(res.msg, 'danger') : alert(res.msg); return; }
        var t = res.data;
        $('#detailTitle').text('任务 #' + t.id + '（' + t.source_label + ' · ' + (t.quarter || '—') + '）');
        $('#detailCard').removeClass('d-none');
        activateHashTab();
    });
}

function loadResults(type) {
    currentType = type || '';
    var url = '/reconcile/api/task/' + currentTaskId + '/results' + (currentType ? '?match_type=' + encodeURIComponent(currentType) : '');
    $.get(url, function (res) {
        if (!res.success) { return; }
        var rows = res.data || [];
        $('#resultStat').text('共 ' + rows.length + ' 条明细');
        if (!rows.length) {
            $('#resultBody').html('<tr><td colspan="6" class="text-center text-muted py-3">该分类暂无明细</td></tr>');
            return;
        }
        // 067 §2.4 徽章映射：match_type 四分类为真实字段（模型注释口径）
        var MATCH_BADGE = {'已登记': 'sc-badge--success', '疑似未登记': 'sc-badge--warning', '无档案': 'sc-badge--muted', '名单外': 'sc-badge--info'};
        var html = '';
        rows.forEach(function (r) {
            var canHandle = r.match_type !== '已登记';
            var btns = '';
            if (canHandle) {
                btns = '<span class="sc-row-actions">' +
                       '<button type="button" class="sc-btn sc-btn--text" onclick="handleResult(' + r.id + ', \'登记\')"><i class="bi bi-pencil"></i> 登记</button>' +
                       '<button type="button" class="sc-btn sc-btn--text" onclick="handleResult(' + r.id + ', \'排除\')">排除</button>' +
                       '</span>';
            } else {
                btns = '<span class="text-muted small">—</span>';
            }
            html += '<tr>' +
                '<td>' + esc(r.name || '—') + '</td>' +
                '<td>' + esc(r.id_card || '—') + '</td>' +
                '<td>' + esc(r.death_date || '—') + '</td>' +
                '<td><span class="sc-badge ' + (MATCH_BADGE[r.match_type] || 'sc-badge--muted') + '">' + r.match_type + '</span></td>' +
                '<td><span class="sc-badge ' + (r.handle_status === '已处置' ? 'sc-badge--success' : 'sc-badge--warning') + '">' + esc(r.handle_status) + '</span></td>' +
                '<td>' + btns + '</td></tr>';
        });
        $('#resultBody').html(html);
    });
}

function handleResult(rid, action) {
    $.post('/reconcile/api/result/' + rid + '/handle-save', {action: action}, function (res) {
        if (!res.success) { showToast ? showToast(res.msg, 'warning') : alert(res.msg); return; }
        showToast ? showToast(res.msg, 'success') : alert(res.msg);
        if (action === '登记' && res.data && res.data.redirect && !res.data.need_resident) {
            window.open(res.data.redirect, '_blank');
        } else if (action === '登记' && res.data && res.data.need_resident) {
            showToast ? showToast('请先在居民档案中补录该居民', 'warning') : alert('请先补录居民档案');
        }
        loadResults(currentType);
    });
}

function exportTask() {
    if (!currentTaskId) return;
    $.post('/reconcile/api/task/' + currentTaskId + '/export', function (res) {
        if (res.success && res.data && res.data.download_url) {
            window.location.href = res.data.download_url;
        } else if (res.msg) {
            showToast ? showToast(res.msg, 'warning') : alert(res.msg);
        }
    });
}

function purgeSource() {
    if (!currentTaskId) return;
    if (!confirm('确定删除该任务的源名单文件？删除后不可恢复（系统仅保留比对结果）。')) return;
    $.post('/reconcile/task/' + currentTaskId + '/purge-source', function (res) {
        showToast ? showToast(res.msg, res.success ? 'success' : 'warning') : alert(res.msg);
        loadTasks();
        openDetail(currentTaskId);
    });
}

function closeDetail() {
    $('#detailCard').addClass('d-none');
    currentTaskId = null;
}

$(function () {
    $('#typeTabs a').on('click', function (e) {
        e.preventDefault();
        $('#typeTabs a').removeClass('active');
        $(this).addClass('active');
        var key = $(this).data('key') || 'all';
        // D-18：页签状态用 hash #tab=<key> 承载
        if (history.replaceState) history.replaceState(null, '', '#tab=' + key);
        else location.hash = 'tab=' + key;
        loadResults($(this).data('type') || '');
    });
    refreshQuarters();
    loadTasks();
});

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.closeDetail = closeDetail;
window.doImport = doImport;
window.exportTask = exportTask;
window.handleResult = handleResult;
window.loadTasks = loadTasks;
window.openDetail = openDetail;
window.purgeSource = purgeSource;
})();
