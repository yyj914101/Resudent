/* dynatable_index.js — 动态台账清单（078）
 * IIFE；数据由 /dynatable/api/list 拉取，行内操作为跳转链接。
 */
(function () {
    'use strict';
    var baseUrl = '/dynatable';

    function load() {
        $.getJSON(baseUrl + '/api/list', function (resp) {
            if (!resp.success) { showToast(resp.msg || '加载失败', 'danger'); return; }
            var html = '';
            $.each(resp.data || [], function (i, t) {
                html += '<tr>' +
                    '<td>' + (i + 1) + '</td>' +
                    '<td class="sc-ellipsis" style="max-width:260px" title="' + escAttr(t.name) + '">' + esc(t.name) + '</td>' +
                    '<td><code>' + esc(t.code) + '</code></td>' +
                    '<td>' + esc(t.module_label || t.module_key) + '</td>' +
                    '<td>' + (t.field_count || 0) + '</td>' +
                    '<td>' + (t.record_count || 0) + '</td>' +
                    '<td>' + esc(t.updated_at || '-') + '</td>' +
                    '<td><div class="sc-row-actions">' +
                    '<a class="sc-btn sc-btn--text" href="' + baseUrl + '/t/' + escAttr(t.code) + '">数据</a>' +
                    '<a class="sc-btn sc-btn--text" href="' + baseUrl + '/t/' + escAttr(t.code) + '/structure">结构</a>' +
                    '</div></td>' +
                    '</tr>';
            });
            $('#tableBody').html(html || '<tr><td colspan="8" class="text-center text-muted py-4">暂无台账，请先上传 Excel 建表</td></tr>');
            $('#pageStat').text('共 ' + ((resp.data || []).length) + ' 张台账');
        });
    }

    $(function () { load(); });
})();
