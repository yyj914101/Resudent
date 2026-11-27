/* dynatable_list.js — 软表数据列表（078）
 * 内存筛选/排序由服务端完成（不碰 json_extract）；单元格 Enter 提交、Esc 取消。
 */
(function () {
    'use strict';
    var cfg = window.__ST_LIST__ || { code: '', fields: [] };
    var baseUrl = '/dynatable';
    var fields = cfg.fields || [];
    var page = 1, pageSize = 20, sortField = '', sortDir = 'asc';

    function fieldOf(code) {
        for (var i = 0; i < fields.length; i++) { if (fields[i].code === code) return fields[i]; }
        return null;
    }

    function query() {
        var q = $('#filterForm').serialize();
        if (sortField) { q += '&sort=' + encodeURIComponent(sortField) + '&dir=' + sortDir; }
        return q;
    }

    function enumLabel(f, value) {
        if (f.data_type !== 'enum') { return value; }
        var options = f.options || [];
        for (var i = 0; i < options.length; i++) {
            if (options[i] && options[i].value === value) { return options[i].label; }
        }
        return value;
    }

    function cellHtml(f, row) {
        var val = row[f.code];
        if (val === undefined || val === null || val === '') { return '<span class="text-muted">—</span>'; }
        return esc(enumLabel(f, val));
    }

    function render(resp) {
        var html = '';
        $.each(resp.data || [], function (i, row) {
            html += '<tr data-id="' + row.id + '">' +
                '<td>' + ((page - 1) * pageSize + i + 1) + '</td>';
            $.each(fields, function (j, f) {
                html += '<td class="st-cell" data-code="' + escAttr(f.code) + '" data-type="' + escAttr(f.data_type) +
                    '" data-sens="' + escAttr(f.sensitivity) + '" title="点击编辑">' + cellHtml(f, row) + '</td>';
            });
            html += '<td><div class="sc-row-actions">' +
                '<button type="button" class="sc-btn sc-btn--text" data-act="edit">编辑</button>' +
                (hasIdentity() ? '<button type="button" class="sc-btn sc-btn--text" data-act="reveal">解锁</button>' : '') +
                '</div></td></tr>';
        });
        $('#recordBody').html(html || '<tr><td colspan="' + (fields.length + 2) + '" class="text-center text-muted py-4">暂无数据</td></tr>');
        // 107（078 遗留 P2）：超列表上限截断时显式提示，total 不再静默失真
        var stat = '共 ' + (resp.total || 0) + ' 条 · 每页 ' + pageSize + ' 条';
        if (resp.truncated) {
            stat += ' · 数据超 ' + (resp.total_records || 20000) + ' 行，仅展示前 20000 行（筛选不含截断部分）';
        }
        $('#pageStat').text(stat);
        var pages = Math.max(1, Math.ceil((resp.total || 0) / pageSize));
        renderPagination('#pagination', pages, page, function (p) { page = p; load(); });
    }

    function hasIdentity() {
        return fields.some(function (f) { return f.sensitivity === 'identity'; });
    }

    function load() {
        $.getJSON(baseUrl + '/api/t/' + cfg.code + '/records?' + query() +
            '&page=' + page + '&limit=' + pageSize, function (resp) {
            if (!resp.success) { showToast(resp.msg || '加载失败', 'danger'); return; }
            render(resp);
        });
    }

    function beginEdit($cell) {
        if ($cell.find('input').length) { return; }
        var f = fieldOf($cell.data('code'));
        var raw = $cell.text() === '—' ? '' : $cell.text();
        var input = $('<input type="text" class="sc-input" style="width:100%" />').val(raw);
        $cell.data('old', raw).empty().append(input);
        input.focus().select();
        input.on('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); commit($cell, f, input.val()); }
            if (e.key === 'Escape') { $cell.text($cell.data('old') || '—'); }
        });
        input.on('blur', function () {
            if ($cell.find('input').length) { $cell.text($cell.data('old') || '—'); }
        });
    }

    function commit($cell, f, value) {
        var $tr = $cell.closest('tr');
        $.ajax({
            url: baseUrl + '/api/t/' + cfg.code + '/record/' + $tr.data('id'),
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ code: f.code, value: value })
        }).done(function (resp) {
            if (!resp.success) { showToast(resp.msg || '保存失败', 'danger'); load(); return; }
            var shown = enumLabel(f, resp.data.value);
            $cell.empty().html(shown === '' || shown === null
                ? '<span class="text-muted">—</span>' : esc(shown));
            showToast('已保存', 'success');
        }).fail(function (xhr) {
            var resp = xhr.responseJSON || {};
            showToast(resp.msg || ('保存失败（' + xhr.status + '）'), 'danger');
            load();
        });
    }

    function reveal(id) {
        $.ajax({
            url: baseUrl + '/api/t/' + cfg.code + '/record/' + id + '/reveal',
            type: 'POST'
        }).done(function (resp) {
            if (!resp.success) { showToast(resp.msg || '解锁失败', 'danger'); return; }
            var html = '';
            $.each(resp.data || {}, function (code, value) {
                var f = fieldOf(code) || { display_name: code };
                html += '<p><strong>' + esc(f.display_name) + '：</strong><code>' + esc(value) + '</code></p>';
            });
            $('#revealBody').html(html || '<p class="text-muted">无身份标识字段</p>');
            getModal('revealModal').show();
        }).fail(function (xhr) {
            var resp = xhr.responseJSON || {};
            showToast(resp.msg || '无权限查看明文数据标识字段', 'danger');
        });
    }

    $(function () {
        $('#filterForm').on('submit', function (e) { e.preventDefault(); page = 1; load(); });
        $('#resetFilter').on('click', function () {
            $('#filterForm')[0].reset(); sortField = ''; sortDir = 'asc'; page = 1; load();
        });
        $('#recordBody').on('click', '.st-cell', function () { beginEdit($(this)); });
        $('#recordBody').on('click', '[data-act="edit"]', function () {
            var $first = $(this).closest('tr').find('.st-cell').first();
            if ($first.length) { beginEdit($first); }
        });
        $('#recordBody').on('click', '[data-act="reveal"]', function () {
            reveal($(this).closest('tr').data('id'));
        });
        if (window.initSofttableAppendImport && cfg.urls) {
            window.initSofttableAppendImport(cfg.urls, function () { page = 1; load(); });
        }
        $('#recordTable').on('click', 'th[data-sort]', function () {
            var code = $(this).data('sort');
            if (sortField === code) { sortDir = sortDir === 'asc' ? 'desc' : 'asc'; }
            else { sortField = code; sortDir = 'asc'; }
            page = 1; load();
        });
        load();
    });
})();
