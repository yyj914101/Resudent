/* roster_append_import.js — 已有软表二次批量导入弹窗控制器（第一批软表闭环）
 * 供 roster_page.html 与 dynatable/list.html 共用：
 *   window.initSofttableAppendImport(urls, onSuccessRefresh)
 * urls 契约：{ appendUpload, appendExecute, importStatus, importReport, importExportFailed }
 */
(function () {
    'use strict';

    window.initSofttableAppendImport = function (urls, onSuccessRefresh) {
        if (!urls || !urls.appendUpload || !$('#appendImportModal').length) {
            return;
        }
        var state = { token: '', fields: [], headers: [], pollTimer: null };

        function clearPoll() {
            if (state.pollTimer) {
                clearTimeout(state.pollTimer);
                state.pollTimer = null;
            }
        }

        function resetModal() {
            clearPoll();
            state.token = '';
            state.fields = [];
            state.headers = [];
            $('#appendFileInput').val('');
            $('#appendUploadStatus').text('');
            $('#appendStepUpload').prop('hidden', false);
            $('#appendStepMap').prop('hidden', true);
            $('#appendStepProgress').prop('hidden', true);
            $('#appendExecuteBtn').prop('hidden', true).prop('disabled', false);
            $('#appendDoneBtn').prop('hidden', true);
            $('#appendCancelBtn').prop('hidden', false);
            $('#appendErrorActions').prop('hidden', true);
            $('#appendMissingNotice').prop('hidden', true).text('');
            $('#appendAsyncNotice').prop('hidden', true);
            $('#appendProgressBar').css('width', '0%');
        }

        function updateValidationState() {
            var missing = [];
            $('#appendMapBody tr').each(function () {
                var $tr = $(this);
                var req = $tr.data('required') === 1 || $tr.data('required') === '1';
                var val = $tr.find('.js-col-select').val();
                if (req && (val === '' || val === null || val === undefined)) {
                    missing.push($tr.data('label'));
                }
            });
            if (missing.length) {
                $('#appendMissingNotice').prop('hidden', false)
                    .text('以下必填字段尚未指定对应列：' + missing.join('、'));
                $('#appendExecuteBtn').prop('disabled', true);
            } else {
                $('#appendMissingNotice').prop('hidden', true).text('');
                $('#appendExecuteBtn').prop('disabled', false);
            }
        }

        function renderMapTable(data) {
            state.token = data.token || '';
            state.fields = data.fields || [];
            state.headers = data.headers || [];

            var matchedCount = 0;
            var html = '';
            $.each(state.fields, function (_, f) {
                if (f.matched_index !== null && f.matched_index !== undefined) {
                    matchedCount += 1;
                }
                var opts = '<option value="">（不导入此列）</option>';
                $.each(state.headers, function (idx, h) {
                    var sel = (f.matched_index === idx) ? ' selected' : '';
                    opts += '<option value="' + idx + '"' + sel + '>' + esc(h || ('第 ' + (idx + 1) + ' 列')) + '</option>';
                });
                html += '<tr data-code="' + escAttr(f.code) + '" data-required="' + (f.is_required ? '1' : '0') +
                    '" data-label="' + escAttr(f.display_name) + '">' +
                    '<td>' + esc(f.display_name) +
                    (f.is_required ? ' <span class="sc-field__required">*</span>' : '') +
                    (f.sensitivity === 'identity' ? ' <span class="sc-badge">加密</span>' : '') + '</td>' +
                    '<td><select class="sc-select js-col-select">' + opts + '</select></td>' +
                    '</tr>';
            });
            $('#appendMapBody').html(html);
            $('#appendFileSummary').text(
                (data.filename || '上传文件') + ' · 共 ' + (data.row_count || 0) +
                ' 行 · 已自动匹配 ' + matchedCount + '/' + state.fields.length + ' 列'
            );
            $('#appendAsyncNotice').prop('hidden', !data.will_async);
            $('#appendStepUpload').prop('hidden', true);
            $('#appendStepMap').prop('hidden', false);
            $('#appendExecuteBtn').prop('hidden', false);
            updateValidationState();
        }

        function uploadFile(file) {
            if (!file) { return; }
            var fd = new FormData();
            fd.append('file', file);
            $('#appendUploadStatus').text('正在解析文件，请稍候…');
            $.ajax({
                url: urls.appendUpload,
                type: 'POST',
                data: fd,
                processData: false,
                contentType: false
            }).done(function (resp) {
                if (!resp.success) {
                    $('#appendUploadStatus').text(resp.msg || '解析失败');
                    showToast(resp.msg || '解析失败', 'danger');
                    return;
                }
                renderMapTable(resp.data || {});
            }).fail(function (xhr) {
                var resp = xhr.responseJSON || {};
                var msg = resp.msg || ('上传失败（' + xhr.status + '）');
                $('#appendUploadStatus').text(msg);
                showToast(msg, 'danger');
            });
        }

        function finishAsync(batchId, isFailed, fallbackSummary) {
            $('#appendCancelBtn').prop('hidden', true);
            $('#appendDoneBtn').prop('hidden', false);
            if (!urls.importReport || !batchId) {
                $('#appendProgressText').text(isFailed ? '导入未完成' : '导入完成');
                $('#appendProgressDetail').text(fallbackSummary || '');
                if (!isFailed && typeof onSuccessRefresh === 'function') { onSuccessRefresh(); }
                return;
            }
            $.getJSON(urls.importReport + '/' + encodeURIComponent(batchId), function (resp) {
                var d = (resp && resp.data) || {};
                var okRows = d.success_rows || 0;
                var failRows = d.fail_rows || 0;
                $('#appendProgressBar').css('width', '100%');
                $('#appendProgressText').text(isFailed ? '导入中止' : '导入完成');
                $('#appendProgressDetail').text(
                    '共 ' + (d.total_rows || 0) + ' 行 · 成功 ' + okRows + ' 行 · 失败 ' + failRows + ' 行'
                );
                if (failRows > 0 && urls.importExportFailed) {
                    $('#appendDownloadFailed').attr('href', urls.importExportFailed + '/' + encodeURIComponent(batchId));
                    $('#appendErrorActions').prop('hidden', false);
                }
                if (okRows > 0 && typeof onSuccessRefresh === 'function') {
                    onSuccessRefresh();
                }
            });
        }

        function pollTask(taskId, batchId) {
            clearPoll();
            $.getJSON(urls.importStatus + '/' + encodeURIComponent(taskId), function (resp) {
                if (!resp || !resp.success) {
                    finishAsync(batchId, true, (resp && resp.msg) || '查询进度失败');
                    return;
                }
                var d = resp.data || {};
                var pct = Math.max(0, Math.min(100, d.progress || 0));
                $('#appendProgressBar').css('width', pct + '%');
                $('#appendProgressText').text('后台分批导入中（' + pct + '%）');
                $('#appendProgressDetail').text(d.result_summary || '');
                if (d.status === 'done') {
                    finishAsync(batchId, false, d.result_summary);
                } else if (d.status === 'failed') {
                    finishAsync(batchId, true, d.result_summary);
                } else {
                    state.pollTimer = setTimeout(function () { pollTask(taskId, batchId); }, 800);
                }
            }).fail(function () {
                state.pollTimer = setTimeout(function () { pollTask(taskId, batchId); }, 1500);
            });
        }

        function executeAppend() {
            var columnMap = {};
            $('#appendMapBody tr').each(function () {
                var $tr = $(this);
                var val = $tr.find('.js-col-select').val();
                columnMap[$tr.data('code')] = (val === '' || val === null) ? null : parseInt(val, 10);
            });
            $('#appendExecuteBtn').prop('disabled', true);
            $.ajax({
                url: urls.appendExecute,
                type: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    token: state.token,
                    column_map: columnMap,
                    skip_error_rows: $('#appendSkipErrors').is(':checked')
                })
            }).done(function (resp) {
                if (!resp.success) {
                    showToast(resp.msg || '导入失败', 'danger');
                    $('#appendExecuteBtn').prop('disabled', false);
                    return;
                }
                var d = resp.data || {};
                if (d.mode === 'async' && d.task_id) {
                    $('#appendStepMap').prop('hidden', true);
                    $('#appendExecuteBtn').prop('hidden', true);
                    $('#appendStepProgress').prop('hidden', false);
                    $('#appendProgressText').text('已提交后台分批导入…');
                    $('#appendProgressBar').css('width', '5%');
                    pollTask(d.task_id, d.batch_id);
                    return;
                }
                showToast(resp.msg || ('已追加 ' + (d.imported || 0) + ' 行'), 'success');
                getModal('appendImportModal').hide();
                if (typeof onSuccessRefresh === 'function') { onSuccessRefresh(); }
            }).fail(function (xhr) {
                var resp = xhr.responseJSON || {};
                showToast(resp.msg || ('导入失败（' + xhr.status + '）'), 'danger');
                $('#appendExecuteBtn').prop('disabled', false);
            });
        }

        $('#appendImportButton').on('click', function () {
            resetModal();
            getModal('appendImportModal').show();
        });
        $('#appendReselectBtn').on('click', resetModal);
        $('#appendFileInput').on('change', function () {
            if (this.files && this.files[0]) { uploadFile(this.files[0]); }
        });
        $('#appendDropZone')
            .on('dragover', function (e) { e.preventDefault(); $(this).addClass('is-dragover'); })
            .on('dragleave drop', function (e) { e.preventDefault(); $(this).removeClass('is-dragover'); })
            .on('drop', function (e) {
                var files = e.originalEvent && e.originalEvent.dataTransfer && e.originalEvent.dataTransfer.files;
                if (files && files[0]) { uploadFile(files[0]); }
            });
        $('#appendMapBody').on('change', '.js-col-select', updateValidationState);
        $('#appendExecuteBtn').on('click', executeAppend);
        $('#appendImportModal').on('hidden.bs.modal', clearPoll);
    };
})();
