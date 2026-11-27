/* domain_batch_import.js — 通用域 A1 异步批量导入前端辅助（医保/民政共用）
 * window.runDomainBatchImport({
 *   prefix, file, extraPayload, onProgress, onDone, onError
 * })
 */
(function () {
    'use strict';

    window.runDomainBatchImport = function (opts) {
        if (!opts || !opts.prefix || !opts.file) { return; }
        var prefix = opts.prefix.replace(/\/+$/, '');
        var fd = new FormData();
        fd.append('file', opts.file);
        if (opts.uploadFormFields) {
            $.each(opts.uploadFormFields, function (k, v) { fd.append(k, v); });
        }
        if (typeof opts.onProgress === 'function') {
            opts.onProgress(5, '正在上传并解析文件…');
        }
        $.ajax({
            url: prefix + '/api/import-upload',
            type: 'POST',
            data: fd,
            processData: false,
            contentType: false
        }).done(function (up) {
            if (!up || !up.success) {
                if (typeof opts.onError === 'function') { opts.onError((up && up.msg) || '解析失败'); }
                return;
            }
            var ud = up.data || {};
            var payload = Object.assign({ token: ud.token }, opts.extraPayload || {});
            if (typeof opts.onProgress === 'function') {
                opts.onProgress(10, '已解析 ' + (ud.row_count || 0) + ' 行，正在提交后台导入…');
            }
            $.ajax({
                url: prefix + '/api/import-execute',
                type: 'POST',
                contentType: 'application/json',
                data: JSON.stringify(payload)
            }).done(function (ex) {
                if (!ex || !ex.success) {
                    if (typeof opts.onError === 'function') { opts.onError((ex && ex.msg) || '提交导入失败'); }
                    return;
                }
                var ed = ex.data || {};
                var taskId = ed.task_id;
                var batchId = ed.batch_id;
                if (!taskId) {
                    if (typeof opts.onDone === 'function') { opts.onDone(ed, null); }
                    return;
                }
                (function poll() {
                    $.getJSON(prefix + '/api/import-status/' + encodeURIComponent(taskId), function (st) {
                        var sd = (st && st.data) || {};
                        var pct = Math.max(10, Math.min(100, sd.progress || 10));
                        if (typeof opts.onProgress === 'function') {
                            opts.onProgress(pct, sd.result_summary || ('后台导入中（' + pct + '%）'));
                        }
                        if (sd.status === 'done' || sd.status === 'failed') {
                            $.getJSON(prefix + '/api/import-report/' + encodeURIComponent(batchId), function (rep) {
                                var rd = (rep && rep.data) || {};
                                var failedUrl = rd.fail_rows > 0
                                    ? (prefix + '/api/import-export-failed/' + encodeURIComponent(batchId))
                                    : '';
                                if (sd.status === 'failed' && typeof opts.onError === 'function') {
                                    opts.onError(sd.result_summary || '导入失败', rd, failedUrl);
                                } else if (typeof opts.onDone === 'function') {
                                    opts.onDone(rd, failedUrl);
                                }
                            });
                            return;
                        }
                        setTimeout(poll, 800);
                    }).fail(function () { setTimeout(poll, 1500); });
                })();
            }).fail(function (xhr) {
                var msg = (xhr.responseJSON && xhr.responseJSON.msg) || ('提交失败（' + xhr.status + '）');
                if (typeof opts.onError === 'function') { opts.onError(msg); }
            });
        }).fail(function (xhr) {
            var msg = (xhr.responseJSON && xhr.responseJSON.msg) || ('上传失败（' + xhr.status + '）');
            if (typeof opts.onError === 'function') { opts.onError(msg); }
        });
    };
})();
