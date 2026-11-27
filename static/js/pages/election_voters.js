(function () {
'use strict';
// 090B 选民名册页（一页纸 §8 名册 + 结果；§6 状态推进；§7 对账条）
// 仅「选民登记」状态可增删改选民；结果录入窗口「投票/结果公示」——前端按状态隐藏入口，
// 后端 utils/election_service 守卫为唯一真源（409 不落库）。
var ROUND;
var NEXT_MAP;
var REG_STATUS_BADGE = {
    '已登记': 'sc-badge sc-badge--success',
    '未登记': 'sc-badge sc-badge--muted',
    '委托投票': 'sc-badge sc-badge--warning',
    '外出': 'sc-badge sc-badge--info'
};

function initVotersPage(cfg) {
    ROUND = cfg.round;
    NEXT_MAP = cfg.transitions || {};
    $('#btnTransition').on('click', function () {
        var next = NEXT_MAP[ROUND.status];
        if (!next) return;
        if (!confirm('确定「' + next + '」？非法迁移与前置不满足将被拒绝且不落库。')) return;
        apiPost('/election/api/rounds/' + ROUND.id + '/transition', { to_status: next }, function () {
            showToast('状态已更新为' + next, 'success');
            window.location.reload();   // 状态推进后整页刷新（守卫/按钮/对账全部随之更新）
        });
    });
    refreshTransitionBtn();
    $('#vtForm').on('submit', function (e) { e.preventDefault(); saveVoter(); });
    $('#rsForm').on('submit', function (e) { e.preventDefault(); saveResult(); });
    // 105 批量导入（异步批次：上传 → 执行 → 轮询 → 报告）
    // 2026-11-19 换壳配套：导入弹窗投放区（点击/拖拽/暂存），与党员管理 impModal 同款交互
    // （共享组件 css/pages/import_modal_v2.css）。只动「提交前选文件」的 UI 形态——
    // #voterImportFile 仍是真实 file input，submitVoterImport 上传→执行→轮询链路不变。
    var $impInput = $('#voterImportFile');
    var $impDrop = $('#voterImpDrop');
    var $impStaged = $('#voterImpStaged');
    var $impSubmit = $('#btnVoterImportSubmit');
    var fmtImpSize = function (b) {
        if (b < 1024) return b + ' B';
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
        return (b / (1024 * 1024)).toFixed(2) + ' MB';
    };
    var stageVoterImp = function (f) {
        $('#voterImpStagedName').text(f.name);
        $('#voterImpStagedMeta').text(fmtImpSize(f.size) + ' ·');
        $impDrop.addClass('d-none');
        $impStaged.removeClass('d-none');
        $impSubmit.prop('disabled', false);
    };
    var resetVoterImpStaged = function () {
        $impInput.val('');
        $impStaged.addClass('d-none');
        $impDrop.removeClass('d-none');
        $impSubmit.prop('disabled', true);
    };
    $impDrop.on('click', function () { $impInput.trigger('click'); })
        .on('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $impInput.trigger('click'); }
        })
        .on('dragover', function (e) { e.preventDefault(); $(this).addClass('is-drag'); })
        .on('dragleave drop', function () { $(this).removeClass('is-drag'); })
        .on('drop', function (e) {
            e.preventDefault();
            var dt = e.originalEvent && e.originalEvent.dataTransfer;
            if (dt && dt.files && dt.files.length) {
                $impInput[0].files = dt.files;
                stageVoterImp(dt.files[0]);
            }
        });
    $impInput.on('change', function () { if (this.files[0]) stageVoterImp(this.files[0]); });
    $('#voterImpReselect').on('click', function () { $impInput.trigger('click'); });
    $('#voterImpRemove').on('click', resetVoterImpStaged);
    // 弹窗每次打开回到初始态（关闭不残留旧文件/进度/报告）
    $('#btnVoterImport').on('click', function () {
        $('#voterImportProgress').addClass('d-none');
        $('#voterImportReport').addClass('d-none');
        resetVoterImpStaged();
        $('#voterImportModal').modal('show');
    });
    $('#btnVoterImportSubmit').on('click', submitVoterImport);
    loadVoters();
    loadResults();
}

function refreshTransitionBtn() {
    var next = NEXT_MAP[ROUND.status];
    var $btn = $('#btnTransition');
    if (next) { $btn.text(next).removeAttr('hidden'); } else { $btn.attr('hidden', true); }
    // 登记期守卫（V-02）：仅「选民登记」状态可增删改选民；结果录入窗口：投票/结果公示
    if (ROUND.status === '选民登记') $('#btnAddVoter').removeAttr('hidden'); else $('#btnAddVoter').attr('hidden', true);
    // 105：批量导入与单条登记同窗口（仅「选民登记」状态后端可写，前端跟随）
    if (ROUND.status === '选民登记') $('#btnVoterImport').removeAttr('hidden'); else $('#btnVoterImport').attr('hidden', true);
    if (ROUND.status === '投票' || ROUND.status === '结果公示') $('#btnAddResult').removeAttr('hidden');
    else $('#btnAddResult').attr('hidden', true);
}

function renderReconcile(rc) {
    $('#rcTotal').text(rc.total);
    $('#rcRegistered').text(rc.registered);
    $('#rcProxy').text(rc.by_status['委托投票'] || 0);
    $('#rcOut').text(rc.by_status['外出'] || 0);
    $('#rcVotes').text(rc.votes_total);
    // M-03 四值互斥对账：四类计数之和应等于名册总数
    if (rc.four_way_sum !== rc.total) {
        showToast('对账异常：四类状态计数之和(' + rc.four_way_sum + ')≠名册总数(' + rc.total + ')', 'warning');
    }
}

function loadVoters() {
    $.get('/election/api/rounds/' + ROUND.id + '/voters', {
        group_name: $('#sGroup').val() || '',
        register_status: $('#sRegisterStatus').val() || '',
        is_out: $('#sIsOut').val() || ''
    }, function (r) {
        if (!r.success) { showToast(r.msg || '加载失败', 'danger'); return; }
        var rows = r.data.data || [];
        renderReconcile(r.data.reconcile || {});
        if (!rows.length) {
            $('#tb').html('<tr><td class="sc-table__td sc-table__td--center" colspan="10">名册暂无选民</td></tr>');
            return;
        }
        $('#tb').html(rows.map(function (v) {
            return '<tr data-id="' + v.id + '" style="cursor:pointer">'
                + '<td class="sc-table__td">' + esc(v.name) + '</td>'
                + '<td class="sc-table__td">' + esc(v.id_card) + '</td>'
                + '<td class="sc-table__td">' + esc(v.group_name || '-') + '</td>'
                + '<td class="sc-table__td"><span class="' + (REG_STATUS_BADGE[v.register_status] || 'sc-badge') + '">' + esc(v.register_status) + '</span></td>'
                + '<td class="sc-table__td">' + (v.is_out ? '是' : '否') + '</td>'
                + '<td class="sc-table__td">' + esc(v.proxy_name || '-') + '</td>'
                + '<td class="sc-table__td">' + esc(v.phone || '-') + '</td>'
                + '<td class="sc-table__td">' + (v.mobile_box ? '是' : '否') + '</td>'
                + '<td class="sc-table__td">' + esc(v.remark || '') + '</td>'
                + '<td class="sc-table__td">'
                + '<button type="button" class="sc-btn sc-btn--text sc-text-danger" onclick="delVoter(' + v.id + ')">删除</button>'
                + '</td></tr>';
        }).join(''));
        // 2026-11-17 Master–Detail：行点击 → 只读详情（删除按钮已 stopPropagation 兜底排除）
        $('#tb tr[data-id]').on('click', function (e) {
            if ($(e.target).closest('button, a').length) return;
            openVoterDetail($(this).data('id'));
        });
    });
}

function _voterById(id, cb) {
    $.get('/election/api/rounds/' + ROUND.id + '/voters', {}, function (r) {
        var v = (r.data.data || []).filter(function (x) { return x.id === id; })[0];
        cb(v);
    });
}

function openVoterModal(id) {
    $('#vtForm')[0].reset();
    if (id) {
        _voterById(id, function (v) {
            if (!v) { showToast('选民不存在', 'danger'); return; }
            $('#vtTitle').text('编辑选民');
            $('#vtId').val(v.id);
            var f = $('#vtForm')[0];
            f.name.value = v.name;
            f.id_card.value = v.id_card || '';
            f.group_name.value = v.group_name || '';
            f.register_status.value = v.register_status;
            f.proxy_name.value = v.proxy_name || '';
            f.phone.value = v.phone || '';
            f.is_out.checked = v.is_out;
            f.mobile_box.checked = v.mobile_box;
            f.remark.value = v.remark || '';
            $('#vtModal').modal('show');
        });
    } else {
        $('#vtTitle').text('登记选民');
        $('#vtId').val(0);
        $('#vtModal').modal('show');
    }
}

// 2026-11-17 Master–Detail：行点击 → 只读详情弹窗（行数据来自 _voterById，编辑入口在详情底部）
function openVoterDetail(id) {
    _voterById(id, function (v) {
        if (!v) { showToast('选民不存在', 'danger'); return; }
        var rows = [
            ['姓名', v.name], ['身份证号', v.id_card], ['所在组', v.group_name],
            ['登记状态', v.register_status], ['是否外出', v.is_out ? '是' : '否'],
            ['委托投票人', v.proxy_name], ['联系电话', v.phone],
            ['流动票箱', v.mobile_box ? '是' : '否']
        ];
        var html = window.SC.renderDetailCards(rows, [['备注', v.remark]]);
        $('#vtDetailBody').html(html);
        $('#vtDetailTitle').text((v.name || '选民') + ' · 选民详情');
        $('#vtDetailBtns').html(
            '<button type="button" class="sc-btn sc-btn--secondary" data-bs-dismiss="modal">关闭</button>' +
            (ROUND.status === '选民登记'
                ? '<button type="button" class="sc-btn sc-btn--primary" id="vtDetailEditBtn"><i class="bi bi-pencil"></i> 编辑</button>' : ''));
        $('#vtDetailModal').modal('show');
        $('#vtDetailEditBtn').off('click').on('click', function () {
            $('#vtDetailModal').modal('hide');
            openVoterModal(id);
        });
    });
}

function saveVoter() {
    var data = $('#vtForm').serializeObject();
    data.is_out = $('#vtForm')[0].is_out.checked ? '1' : '0';
    data.mobile_box = $('#vtForm')[0].mobile_box.checked ? '1' : '0';
    var id = Number(data.id);
    data.round_id = ROUND.id;
    var url = id > 0 ? '/election/api/voters/edit' : '/election/api/rounds/' + ROUND.id + '/voters/add';
    apiPost(url, data, function () {
        showToast('保存成功', 'success');
        $('#vtModal').modal('hide');
        loadVoters();
    });
}

function delVoter(id) {
    if (!confirm('确定删除该选民？')) return;
    apiPost('/election/api/voters/' + id + '/delete', {}, loadVoters);
}
window.delVoter = delVoter;   // 行内 onclick 消费（2026-11-17 删除按钮保留）

function exportVoters() {
    $.get('/election/api/rounds/' + ROUND.id + '/voters/export', {
        group_name: $('#sGroup').val() || '',
        register_status: $('#sRegisterStatus').val() || '',
        is_out: $('#sIsOut').val() || ''
    }, function (r) {
        if (r.success && r.data.download_url) { window.location.href = r.data.download_url; }
        else showToast(r.msg || '导出失败', 'danger');
    });
}

function loadResults() {
    $.get('/election/api/rounds/' + ROUND.id + '/results', {}, function (r) {
        if (!r.success) { showToast(r.msg || '加载失败', 'danger'); return; }
        var rows = r.data.data || [];
        if (r.data.reconcile) renderReconcile(r.data.reconcile);
        if (!rows.length) {
            $('#rtb').html('<tr><td class="sc-table__td sc-table__td--center" colspan="7">暂无结果记录</td></tr>');
            return;
        }
        $('#rtb').html(rows.map(function (e) {
            return '<tr>'
                + '<td class="sc-table__td">' + esc(e.position) + '</td>'
                + '<td class="sc-table__td">' + esc(e.winner_name) + '</td>'
                + '<td class="sc-table__td sc-table__td--num">' + e.votes + '</td>'
                + '<td class="sc-table__td">' + (e.is_elected ? '是' : '否') + '</td>'
                + '<td class="sc-table__td">' + esc(e.notice_date || '-') + '</td>'
                + '<td class="sc-table__td">' + esc(e.remark || '') + '</td>'
                + '<td class="sc-table__td">'
                + '<button type="button" class="sc-btn sc-btn--text" onclick="openResultModal(' + e.id + ')">编辑</button> '
                + '<button type="button" class="sc-btn sc-btn--text sc-text-danger" onclick="delResult(' + e.id + ')">删除</button>'
                + '</td></tr>';
        }).join(''));
    });
}

function _resultById(id, cb) {
    $.get('/election/api/rounds/' + ROUND.id + '/results', {}, function (r) {
        cb((r.data.data || []).filter(function (x) { return x.id === id; })[0]);
    });
}

function openResultModal(id) {
    $('#rsForm')[0].reset();
    if (id) {
        _resultById(id, function (e) {
            if (!e) { showToast('结果记录不存在', 'danger'); return; }
            $('#rsTitle').text('编辑当选结果');
            $('#rsId').val(e.id);
            var f = $('#rsForm')[0];
            f.position.value = e.position;
            f.winner_name.value = e.winner_name;
            f.votes.value = e.votes;
            f.notice_date.value = e.notice_date || '';
            f.is_elected.checked = e.is_elected;
            f.remark.value = e.remark || '';
            $('#rsModal').modal('show');
        });
    } else {
        $('#rsTitle').text('录入当选结果');
        $('#rsId').val(0);
        $('#rsModal').modal('show');
    }
}

function saveResult() {
    var data = $('#rsForm').serializeObject();
    data.is_elected = $('#rsForm')[0].is_elected.checked ? '1' : '0';
    var id = Number(data.id);
    var url = id > 0 ? '/election/api/results/edit' : '/election/api/rounds/' + ROUND.id + '/results/add';
    apiPost(url, data, function () {
        showToast('保存成功', 'success');
        $('#rsModal').modal('hide');
        loadResults();
    });
}

function delResult(id) {
    if (!confirm('确定删除该结果记录？')) return;
    apiPost('/election/api/results/' + id + '/delete', {}, loadResults);
}

// ===== 105 选民批量导入（上传 → 执行 → 轮询 → 报告；失败行导出） =====
var voterImportTimer = null;
var voterImportBatchId = null;
window.openVoterModal = openVoterModal;   // 详情底部「编辑」按钮消费
window.openVoterDetail = openVoterDetail;
window.__SPA_TEARDOWN__ = function () {
    if (voterImportTimer) { clearTimeout(voterImportTimer); voterImportTimer = null; }
};

function submitVoterImport() {
    var f = $('#voterImportFile')[0];
    if (!f || !f.files || !f.files.length) { showToast('请选择 .xlsx / .xls / .xlsb / .csv / .xlsm 名单文件', 'warning'); return; }
    var fd = new FormData();
    fd.append('file', f.files[0]);
    $('#btnVoterImportSubmit').prop('disabled', true);
    $.ajax({
        url: '/election/api/rounds/' + ROUND.id + '/voters/import-upload',
        type: 'POST', data: fd, processData: false, contentType: false,
        success: function (resp) {
            $('#btnVoterImportSubmit').prop('disabled', false);
            if (!resp.success) { showToast(resp.msg || '上传失败', 'danger'); return; }
            var batchId = resp.data.batch_id;
            if (resp.data.duplicate) {
                showToast('该文件已导入过，加载既有批次', 'warning');
                voterImportBatchId = batchId;
                loadVoterImportReport(batchId);
                return;
            }
            executeVoterImport(batchId);
        },
        error: function (xhr) {
            $('#btnVoterImportSubmit').prop('disabled', false);
            var data = xhr.responseJSON || {};
            showToast(data.msg || '上传失败', 'danger');
        }
    });
}

function executeVoterImport(batchId) {
    voterImportBatchId = batchId;
    apiPost('/election/api/rounds/' + ROUND.id + '/voters/import-execute',
        { batch_id: batchId }, function (resp) {
            $('#voterImportProgress').removeClass('d-none');
            $('#voterImportReport').addClass('d-none');
            pollVoterImport(resp.data.task_id);
        });
}

function pollVoterImport(taskId) {
    $.getJSON('/election/api/rounds/' + ROUND.id + '/voters/import-status/' + taskId, function (resp) {
        if (!resp.success) { showToast(resp.msg || '查询失败', 'danger'); return; }
        var d = resp.data;
        $('#voterImportBar').css('width', d.progress + '%').text(d.progress + '%');
        $('#voterImportText').text(d.result_summary || '');
        if (d.status === 'done') {
            clearTimeout(voterImportTimer);
            loadVoterImportReport(voterImportBatchId);
            loadVoters();
        } else if (d.status === 'failed') {
            clearTimeout(voterImportTimer);
            showToast('批量导入失败：' + (d.result_summary || ''), 'danger');
        } else {
            voterImportTimer = setTimeout(function () { pollVoterImport(taskId); }, 1000);
        }
    });
}

function loadVoterImportReport(batchId) {
    $.getJSON('/election/api/rounds/' + ROUND.id + '/voters/import-report/' + batchId, function (resp) {
        if (!resp.success) { showToast(resp.msg || '加载失败', 'danger'); return; }
        var d = resp.data;
        $('#voterImportReport').removeClass('d-none');
        $('#voterImportProgress').addClass('d-none');
        $('#voterImportSummary').html('总行数：<strong>' + d.total_rows + '</strong>，成功：<strong>' +
            d.success_rows + '</strong>，失败：<strong>' + d.fail_rows + '</strong>');
        var html = '';
        $.each(d.errors || [], function (i, e) {
            html += '<tr><td>' + e.row_no + '</td><td>' + esc(e.id_card_masked || '') +
                '</td><td>' + esc(e.reason || '') + '</td></tr>';
        });
        if (!html) html = '<tr><td colspan="3" class="text-center text-muted">无失败行</td></tr>';
        $('#voterImportErrors').html(html);
        var failed = (d.fail_rows || 0) > 0;
        $('#btnVoterImportExportFailed').prop('disabled', !failed).off('click').on('click', function () {
            if (!failed) return;
            $.get('/election/api/rounds/' + ROUND.id + '/voters/import-export-failed/' + batchId,
                function (r2) {
                    if (r2.success && r2.data.download_url) { window.location.href = r2.data.download_url; }
                    else { showToast(r2.msg || '导出失败', 'danger'); }
                });
        });
    });
}

// P2-2 IIFE 收口：局部隔离顶层变量，仅导出模板/内联回调所需入口
var _exports = { initVotersPage: initVotersPage, loadVoters: loadVoters, openVoterModal: openVoterModal, openVoterDetail: openVoterDetail, saveVoter: saveVoter, delVoter: delVoter, exportVoters: exportVoters, loadResults: loadResults, openResultModal: openResultModal, saveResult: saveResult, delResult: delResult, submitVoterImport: submitVoterImport, executeVoterImport: executeVoterImport };
if (window.SC && window.SC.exportPageGlobals) {
    window.SC.exportPageGlobals(_exports);
} else {
    Object.keys(_exports).forEach(function (k) { window[k] = _exports[k]; });
}
})();
