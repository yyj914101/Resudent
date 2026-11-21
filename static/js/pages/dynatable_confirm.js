/* dynatable_confirm.js — 建表确认页（078）
 * 强制人工确认：推断结果只作为表单初值，用户可逐列改；enum 必须选字典分类。
 */
(function () {
    'use strict';
    var baseUrl = '/dynatable';
    var token = (location.search.match(/[?&]token=([^&]+)/) || [])[1] || '';
    var meta = null;

    function msg(text, type) {
        $('#createMsg').text(text || '');
        if (type) { showToast(text, type); }
    }

    function typeOptions(cur) {
        return ['text', 'number', 'date', 'enum'].map(function (t) {
            return '<option value="' + t + '"' + (t === cur ? ' selected' : '') + '>' + t + '</option>';
        }).join('');
    }

    function sensOptions(cur) {
        return ['none', 'identity', 'amount'].map(function (s) {
            return '<option value="' + s + '"' + (s === cur ? ' selected' : '') + '>' + s + '</option>';
        }).join('');
    }

    function catOptions(cats, cur) {
        var html = '<option value="">（非枚举）</option>';
        $.each(cats || [], function (i, c) {
            html += '<option value="' + escAttr(c.code) + '"' + (c.code === cur ? ' selected' : '') + '>' + esc(c.name) + '</option>';
        });
        return html;
    }

    function renderFields() {
        var html = '';
        $.each(meta.suggestions || [], function (i, f) {
            html += '<tr data-idx="' + i + '">' +
                '<td>' + (i + 1) + '</td>' +
                '<td><input type="text" class="sc-input" data-role="name" value="' + escAttr(f.display_name) + '"></td>' +
                '<td><code>' + esc(f.code) + '</code></td>' +
                '<td><select class="sc-select" data-role="type">' + typeOptions(f.data_type) + '</select></td>' +
                '<td><select class="sc-select" data-role="sens">' + sensOptions(f.sensitivity) + '</select></td>' +
                '<td><select class="sc-select" data-role="cat">' + catOptions(meta.dict_categories, f.dict_category_code) + '</select></td>' +
                '<td><input type="checkbox" data-role="required"' + (f.is_required ? ' checked' : '') + '></td>' +
                '<td class="text-muted">' + esc(f.hint || '') + '</td>' +
                '</tr>';
        });
        $('#fieldBody').html(html);
        toggleCats();
    }

    function toggleCats() {
        $('#fieldBody tr').each(function () {
            var isEnum = $(this).find('[data-role="type"]').val() === 'enum';
            $(this).find('[data-role="cat"]').prop('disabled', !isEnum);
        });
    }

    function renderPreview() {
        var head = '<th class="sc-table__th" style="width:56px">#</th>';
        $.each(meta.headers || [], function (i, h) { head += '<th class="sc-table__th">' + esc(h) + '</th>'; });
        $('#previewHead').html(head);
        var body = '';
        $.each(meta.sample || [], function (i, row) {
            body += '<tr><td>' + (i + 1) + '</td>';
            $.each(row, function (j, cell) { body += '<td>' + esc(cell || '') + '</td>'; });
            body += '</tr>';
        });
        $('#previewBody').html(body || '<tr><td colspan="3" class="text-muted">无数据行</td></tr>');
    }

    function collectFields() {
        var out = [];
        $('#fieldBody tr').each(function (i) {
            var $tr = $(this);
            out.push({
                code: (meta.suggestions[i] || {}).code,
                display_name: $.trim($tr.find('[data-role="name"]').val()),
                data_type: $tr.find('[data-role="type"]').val(),
                sensitivity: $tr.find('[data-role="sens"]').val(),
                dict_category_code: $tr.find('[data-role="cat"]').val() || null,
                is_required: $tr.find('[data-role="required"]').is(':checked'),
                sort_order: i
            });
        });
        return out;
    }

    var ASYNC_POLL_MS = 1500;      // 107：进度轮询间隔
    var pollTimer = null;
    var lastTaskId = null;
    var lastBatchId = null;

    function create() {
        var stored = {};
        try { stored = JSON.parse(sessionStorage.getItem('st_new_table') || '{}'); } catch (e) { stored = {}; }
        if (!stored.code) {
            msg('缺少建表信息（可能刷新过页面），请重新上传', 'danger');
            return;
        }
        var fields = collectFields();
        var bad = $.grep(fields, function (f) {
            return !f.display_name || (f.data_type === 'enum' && !f.dict_category_code);
        });
        if (bad.length) { msg('请补全列名；枚举列必须选择字典分类', 'danger'); return; }
        var payload = {
            token: token, code: stored.code, name: stored.name,
            module_key: stored.module_key, group_name: stored.group_name,
            description: stored.description, fields: fields
        };
        $(this).prop('disabled', true);
        // 107：大表（> async_min_rows，api_meta 单一真源下发）路由到后台分批导入；
        // 小表仍走 api_create 同步路径（模式 A 的性能分支，两路径语义见 107-RESULT.md）
        if (meta && meta.row_count > (meta.async_min_rows || 2000)) {
            createAsync(payload);
            return;
        }
        msg('正在建表…');
        $.ajax({
            url: baseUrl + '/api/create', type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(Object.assign({}, payload, {
                skip_error_rows: $('#skipErrorRows').is(':checked')
            }))
        }).done(function (resp) {
            if (!resp.success) {
                msg(resp.msg || '建表失败', 'danger');
                var lines = '';
                $.each(resp.errors || [], function (i, e) {
                    lines += '<p class="sc-field__hint">第 ' + e.row + ' 行「' + esc(e.field || '') + '」：' + esc(e.msg) + '</p>';
                });
                $('#errorList').html(lines);
                return;
            }
            msg('建表完成，已导入 ' + resp.data.imported + ' 行');
            showToast('建表完成', 'success');
            setTimeout(function () { location.href = baseUrl + '/t/' + resp.data.code; }, 600);
        }).fail(function (xhr) {
            var resp = xhr.responseJSON || {};
            msg(resp.msg || ('建表失败（' + xhr.status + '）'), 'danger');
        }).always(function () {
            $('#createBtn').prop('disabled', false);
        });
    }

    // ---------------- 107：后台分批导入（提交 → 轮询 → 报告 → 失败行导出） ----------------

    function createAsync(payload) {
        msg('大表（' + meta.row_count + ' 行）走后台分批导入…');
        $.ajax({
            url: baseUrl + '/api/import-execute', type: 'POST',
            contentType: 'application/json', data: JSON.stringify(payload)
        }).done(function (resp) {
            if (!resp.success) {
                msg(resp.msg || '提交失败', 'danger');
                $('#createBtn').prop('disabled', false);
                return;
            }
            lastTaskId = resp.data.task_id;
            lastBatchId = resp.data.batch_id;
            $('#asyncCard').removeClass('d-none');
            if (resp.data.duplicate) { msg(resp.msg || '该文件已建过导入批次，返回既有批次', 'warning'); }
            poll();
        }).fail(function (xhr) {
            var resp = xhr.responseJSON || {};
            msg(resp.msg || ('提交失败（' + xhr.status + '）'), 'danger');
            $('#createBtn').prop('disabled', false);
        });
    }

    function poll() {
        if (!lastTaskId) { showReport(); return; }
        $.getJSON(baseUrl + '/api/import-status/' + encodeURIComponent(lastTaskId), function (resp) {
            if (!resp.success) { $('#asyncMsg').text(resp.msg || '状态查询失败，稍后重试'); }
            else {
                var d = resp.data;
                var pct = d.progress || 0;
                $('#asyncBar').css('width', pct + '%').text(pct + '%').attr('aria-valuenow', pct);
                if (d.result_summary) { $('#asyncMsg').text(d.result_summary); }
                if (d.status === 'done' || d.status === 'failed') { showReport(); return; }
            }
            pollTimer = setTimeout(poll, ASYNC_POLL_MS);
        }).fail(function () {
            pollTimer = setTimeout(poll, ASYNC_POLL_MS);
        });
    }

    function showReport() {
        if (!lastBatchId) { $('#createBtn').prop('disabled', false); return; }
        $.getJSON(baseUrl + '/api/import-report/' + encodeURIComponent(lastBatchId), function (resp) {
            if (!resp.success) {
                msg(resp.msg || '报告读取失败', 'danger');
                $('#createBtn').prop('disabled', false);
                return;
            }
            var d = resp.data;
            var ok = d.status === 'done';
            var html = '<div class="alert ' + (ok ? 'alert-success' : 'alert-danger') + ' py-2 small">'
                + '对账：共 ' + d.total_rows + ' 行 · 成功 ' + d.success_rows + ' · 失败 ' + d.fail_rows
                + '（' + d.success_rows + ' + ' + d.fail_rows + ' = ' + d.total_rows + '）</div>';
            if ((d.errors || []).length) {
                html += '<div class="sc-table-wrap"><table class="sc-table"><thead class="sc-table__head">'
                    + '<tr><th class="sc-table__th" style="width:100px">数据行号</th>'
                    + '<th class="sc-table__th" style="width:180px">出错列</th>'
                    + '<th class="sc-table__th">失败原因</th></tr></thead><tbody>';
                $.each(d.errors, function (i, e) {
                    html += '<tr><td>' + esc(e.row_no) + '</td><td>' + esc(e.field_name || '') + '</td>'
                         + '<td>' + esc(e.reason || '') + '</td></tr>';
                });
                html += '</tbody></table></div>';
                if (d.fail_rows > d.errors.length) {
                    html += '<p class="sc-field__hint">失败明细仅显示前 ' + d.errors.length + ' 行，全部失败行请导出查看</p>';
                }
            }
            html += '<div class="sc-row-actions" style="margin-top:8px">'
                + ((d.errors || []).length ? '<button type="button" class="sc-btn sc-btn--secondary" id="exportFailedBtn"><i class="bi bi-download"></i> 导出失败行</button> ' : '')
                + (ok ? '<a class="sc-btn sc-btn--primary" href="' + baseUrl + '/t/' + escAttr(d.table_code) + '">前往台账 ' + esc(d.table_code) + '</a>' : '')
                + '</div>';
            $('#asyncReport').html(html);
            if (ok) {
                showToast('导入完成：成功 ' + d.success_rows + ' 行，失败 ' + d.fail_rows + ' 行',
                          d.fail_rows ? 'warning' : 'success');
            } else {
                showToast('导入失败，请查看失败明细（0 行入库的失败可直接重试）', 'danger');
            }
            $('#createBtn').prop('disabled', false);
            if ($('#exportFailedBtn').length) { $('#exportFailedBtn').on('click', exportFailed); }
        }).fail(function () {
            msg('报告读取失败', 'danger');
            $('#createBtn').prop('disabled', false);
        });
    }

    function exportFailed() {
        if (!lastBatchId) { return; }
        $.getJSON(baseUrl + '/api/import-export-failed/' + encodeURIComponent(lastBatchId), function (resp) {
            if (!resp.success) { showToast(resp.msg || '导出失败', 'danger'); return; }
            location.href = resp.data.download_url;
        }).fail(function () { showToast('导出失败', 'danger'); });
    }

    $(function () {
        if (!token) { msg('缺少上传凭证，请重新上传文件', 'danger'); return; }
        $.getJSON(baseUrl + '/api/meta/' + encodeURIComponent(token), function (resp) {
            if (!resp.success) { msg(resp.msg || '读取失败', 'danger'); return; }
            meta = resp.data;
            $('#sourceLine').text('文件：' + meta.filename + ' · 共 ' + meta.row_count + ' 行 · ' + (meta.headers || []).length + ' 列');
            renderFields();
            renderPreview();
        });
        $('#fieldBody').on('change', '[data-role="type"]', toggleCats);
        $('#createBtn').on('click', create);
    });
})();
