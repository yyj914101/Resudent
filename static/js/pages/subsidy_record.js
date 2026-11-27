(function () {
var currentPage = 1;

function loadProjects() {
    $.getJSON('/subsidy/api/project/list?page=1&limit=1000', function(resp) {
        if (!resp.success) return;
        let opts = '';
        $.each(resp.data, function(i, p) {
            if (p.status === '启用') {
                opts += '<option value="' + p.id + '">' + esc(p.name) + '</option>';
            }
        });
        $('#projectSelect').html('<option value="">请选择项目</option>' + opts);
        $('#batchProjectSelect').html('<option value="">请选择项目</option>' + opts);

        let fopts = '';
        $.each(resp.data, function(i, p) {
            fopts += '<option value="' + p.id + '">' + esc(p.name) + '</option>';
        });
        $('#filterProject').html('<option value="">全部项目</option>' + fopts);
    });
}

function loadRecords(page) {
    page = page || 1;
    currentPage = page;
    let params = $('#filterForm').serialize() + '&page=' + page + '&limit=50';
    $.getJSON('/subsidy/api/record/list?' + params, function(resp) {
        if (!resp.success) return;
        let html = '';
        $.each(resp.data, function(i, r) {
            let srcBadge = r.source === 'reverse'
                ? '<span class="sc-badge sc-badge--warning">冲正</span>'
                : (r.source === 'legacy' ? '<span class="sc-badge sc-badge--muted">存量</span>' : '<span class="sc-badge sc-badge--info">正常</span>');
            // 082 B-2 动态状态：新增/停发/正常 分别着色，未知值原样兜底
            let statusBadge = r.status === '新增'
                ? '<span class="sc-badge sc-badge--info">新增</span>'
                : (r.status === '停发'
                    ? '<span class="sc-badge sc-badge--warning">停发</span>'
                    : (r.status === '正常' ? '<span class="sc-badge sc-badge--muted">正常</span>' : esc(r.status)));
            html += '<tr>' +
                '<td>' + esc(r.project_name) + '</td>' +
                '<td><a href="javascript:void(0)" onclick="showOverview(' + r.resident_id + ')">居民#' + r.resident_id + '</a></td>' +
                '<td>' + esc(r.period) + '</td>' +
                '<td>' + r.amount + '</td>' +
                '<td>' + srcBadge + '</td>' +
                '<td>' + statusBadge + '</td>' +
                '<td>' + esc(r.note) + '</td>' +
                '<td>' + (r.source !== 'reverse'
                    ? '<span class="sc-row-actions"><button type="button" class="sc-btn sc-btn--text is-danger" onclick="reverseItem(' + r.id + ')">冲正</button></span>'
                    : '—') +
                '</td>' +
                '</tr>';
        });
        if (!html) html = '<tr><td colspan="8" class="text-center text-muted py-4">暂无数据</td></tr>';
        $('#recordBody').html(html);
    });
}

function showOverview(residentId) {
    $.getJSON('/subsidy/api/resident/999/overview'.replace('999', residentId), function(resp) {
        if (!resp.success) { showToast(resp.msg, 'danger'); return; }
        let d = resp.data;
        let html = '<div class="sc-table-wrap"><table class="sc-table sc-table--dense">' +
            '<thead><tr><th class="sc-table__th">项目</th><th class="sc-table__th">期间</th><th class="sc-table__th">金额</th><th class="sc-table__th">来源</th><th class="sc-table__th">日期</th></tr></thead><tbody>';
        $.each(d.detail, function(i, r) {
            html += '<tr><td>' + esc(r.project_name) + '</td><td>' + esc(r.period) + '</td><td>' + r.amount +
                '</td><td>' + esc(r.source) + '</td><td>' + esc(r.issue_date) + '</td></tr>';
        });
        html += '</tbody></table></div>';
        html += '<div class="mt-2">累计总额：<strong>' + d.total_amount + '</strong> 元 &nbsp;|&nbsp; 最近发放期间：<strong>' + esc(d.last_period || '—') + '</strong></div>';
        $('#overviewBody').html(html);
        $('#overviewTitle').text('居民「' + esc(d.resident.name) + '」补贴总览');
        $('#overviewModal').modal('show');
    });
}

$('#residentSearch').on('input', function() {
    let kw = $(this).val().trim();
    if (kw.length < 1) { $('#residentResult').html(''); return; }
    $.getJSON('/resident/api/list?name=' + encodeURIComponent(kw) + '&limit=10', function(resp) {
        if (!resp.success) return;
        // 接口是分页信封 {data, page, pages, limit, total}，列表在 resp.data.data
        let rows = (resp.data && resp.data.data) || [];
        if (!rows.length) {
            $('#residentResult').html('<div class="list-group-item py-1 small text-muted">无匹配居民</div>');
            return;
        }
        $('#residentResult').html(rows.map(function(r) {
            return '<a href="javascript:void(0)" class="list-group-item list-group-item-action py-1"' +
                ' data-id="' + escAttr(r.id) + '" data-name="' + escAttr(r.name) + '">' +
                esc(r.name) + '（' + esc(r.village_group || '') + '）</a>';
        }).join(''));
    });
});

// 选中回填；浮层是 absolute，会盖住下方字段，点外部须收起
$('#residentResult').on('click', '.list-group-item-action', function() {
    $('#residentId').val($(this).attr('data-id'));
    $('#residentSearch').val($(this).attr('data-name'));
    $('#residentResult').html('');
});
// 命名空间解绑：SPA 每次进页重放本作用域，document 是持久的，不去重会累积 handler
$(document).off('click.residentPick').on('click.residentPick', function(e) {
    if (!$(e.target).closest('#residentSearch, #residentResult').length) $('#residentResult').html('');
});

$('#addForm').submit(function(e) {
    e.preventDefault();
    $('#flowHint').addClass('d-none');
    $.ajax({
        url: '/subsidy/api/record/add',
        type: 'POST',
        data: $(this).serialize(),
        success: function(resp) {
            if (resp.success) {
                if (resp.data && resp.data.flow_out) {
                    $('#flowHint').removeClass('d-none').text(resp.data.flow_hint);
                }
                showToast(resp.msg, 'success');
                $('#addForm')[0].reset();
                loadRecords(1);
            } else {
                let msg = resp.msg;
                if (resp.data && resp.data.reasons) {
                    msg += '\n' + resp.data.reasons.join('\n');
                }
                showToast(msg, 'danger');
            }
        }
    });
});

$('#filterForm').submit(function(e) { e.preventDefault(); loadRecords(1); });

function reverseItem(id) {
    if (!confirm('确定冲正该笔发放记录吗？（冲正后累计金额将扣减）')) return;
    $.ajax({
        url: '/subsidy/api/record/reverse-save',
        type: 'POST',
        data: { id: id },
        success: function(resp) {
            if (resp.success) { showToast(resp.msg, 'success'); loadRecords(currentPage); }
            else { showToast(resp.msg, 'danger'); }
        }
    });
}

// ===== 批量登记 =====
var currentBatchId = null;
var pollingTimer = null;
// R-06：SPA 切页时清理批量登记轮询，避免闭包继续递归
window.__SPA_TEARDOWN__ = function() {
    if (pollingTimer) { clearTimeout(pollingTimer); pollingTimer = null; }
};

$('#batchForm').submit(function(e) {
    e.preventDefault();
    let formData = new FormData(this);
    if (!formData.get('project_id')) { showToast('请选择补贴项目', 'danger'); return; }
    $.ajax({
        url: '/subsidy/api/record/import-upload',
        type: 'POST',
        data: formData,
        processData: false,
        contentType: false,
        success: function(resp) {
            if (!resp.success) { showToast(resp.msg, 'danger'); return; }
            let batchId = resp.data.batch_id;
            if (resp.data.duplicate) {
                showToast('该文件已导入过，加载既有批次', 'warning');
                loadBatchReport(batchId);
                return;
            }
            executeBatch(batchId);
        }
    });
});

function executeBatch(batchId) {
    $.ajax({
        url: '/subsidy/api/record/import-execute',
        type: 'POST',
        data: { batch_id: batchId },
        success: function(resp) {
            if (!resp.success) { showToast(resp.msg, 'danger'); return; }
            currentBatchId = batchId;
            $('#batchProgress').removeClass('d-none');
            $('#batchReport').addClass('d-none');
            pollBatchStatus(resp.data.task_id);
        }
    });
}

function pollBatchStatus(taskId) {
    $.getJSON('/subsidy/api/record/import-status/TASKID'.replace('TASKID', taskId), function(resp) {
        if (!resp.success) { showToast(resp.msg, 'danger'); return; }
        let d = resp.data;
        $('#batchProgressBar').css('width', d.progress + '%').text(d.progress + '%');
        $('#batchProgressText').text(d.result_summary || '');
        if (d.status === 'done') {
            clearTimeout(pollingTimer);
            loadBatchReport(currentBatchId);
            loadRecords(1);
        } else if (d.status === 'failed') {
            clearTimeout(pollingTimer);
            showToast('批量登记失败：' + (d.result_summary || ''), 'danger');
        } else {
            pollingTimer = setTimeout(function() { pollBatchStatus(taskId); }, 1000);
        }
    });
}

function loadBatchReport(batchId) {
    $.getJSON('/subsidy/api/record/import-report/BATCHID'.replace('BATCHID', batchId), function(resp) {
        if (!resp.success) { showToast(resp.msg, 'danger'); return; }
        let d = resp.data;
        $('#batchReport').removeClass('d-none');
        $('#batchProgress').addClass('d-none');
        $('#batchReportSummary').html('总行数：<strong>' + d.total_rows + '</strong>，成功：<strong>' + d.success_rows +
            '</strong>，失败：<strong>' + d.fail_rows + '</strong>');
        let html = '';
        $.each(d.errors, function(i, e) {
            html += '<tr><td>' + e.row_no + '</td><td>' + esc(e.id_card_masked) + '</td><td>' + esc(e.reason) + '</td></tr>';
        });
        if (!html) html = '<tr><td colspan="3" class="text-center text-muted py-2">无失败行</td></tr>';
        $('#batchErrorBody').html(html);
        $('#batchExportFailedBtn').prop('disabled', d.fail_rows === 0);
        $('#batchExportFailedBtn').off('click').on('click', function() {
            window.location.href = '/subsidy/api/record/import-export-failed/BATCHID'.replace('BATCHID', batchId);
        });
    });
}

$(function() {
    loadProjects();
    loadRecords(1);
    // 082 B-2：动态状态选项以字典「救助动态状态」为准（分类缺失时保留模板静态兜底项）
    dictStore.ready(function() {
        let opts = '';
        $.each(dictStore.items('救助动态状态'), function(i, item) {
            opts += '<option value="' + escAttr(item.value || item.label) + '">' + esc(item.label) + '</option>';
        });
        if (!opts) return;
        $('#addStatus').html(opts);
        $('#filterStatus').html('<option value="">全部状态</option>' + opts);
    });
});

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.reverseItem = reverseItem;
window.showOverview = showOverview;
})();
