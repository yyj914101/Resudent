/* import_batches.js — 036 F3 导入批次列表（crudTable 复用 S2 统一分页组件）
 * IIFE；事件委托由 crudTable.bindEvent 统一处理，不阻断冒泡（016 教训）。 */
(function () {
$(function () {
    var STATUS = {
        pending: ['待映射', 'muted'],
        mapped: ['已映射', 'info'],
        running: ['导入中', 'primary'],
        done: ['完成', 'success'],
        failed: ['失败', 'danger'],
        blocked: ['已阻断', 'danger'],
        aborted: ['已中止', 'warning']
    };

    var list = crudTable({
        endpoint: '/import/batches/data',
        columns: [
            {title: '批次号', render: function (item) {
                return '<span class="batch-id">' + esc(item.batch_id) + '</span>';
            }},
            {field: 'source_file', title: '源文件', esc: true},
            {title: '模式', render: function (item) {
                if (item.mode_label) { return esc(item.mode_label); }
                return item.mode === 'template' ? '模板' : '自定义';
            }},
            {field: 'catalog_version', title: '模板版本', esc: true},   // R-036-06
            {title: '状态', render: function (item) {
                var s = STATUS[item.status] || [item.status || '', 'muted'];
                return '<span class="sc-badge sc-badge--' + s[1] + '">' + esc(s[0]) + '</span>';
            }},
            {field: 'inserted_rows', title: '新增'},
            {field: 'updated_rows', title: '更新'},
            {field: 'skip_rows', title: '跳过'},
            {title: '失败', render: function (item) {
                return item.fail_rows ? '<span class="sc-text-danger">' + item.fail_rows + '</span>' : '0';
            }},
            {field: 'operator', title: '操作人', esc: true},
            {field: 'created_at', title: '创建时间'},
            {title: '操作', render: function (item) {
                var id = encodeURIComponent(item.batch_id);
                var repUrl = item.report_url || ('/import/' + id + '/report');
                var failUrl = item.export_failed_url || ('/import/' + id + '/export-failed');
                // 142：0 行残留批次（pending/mapped/blocked/aborted 且未入库）提供「作废」，
                // 解除同文件重传死锁的另一种出口（手动清理历史残留）。
                var imported = (item.inserted_rows || 0) + (item.updated_rows || 0);
                var canVoid = ['pending', 'mapped', 'blocked', 'aborted'].indexOf(item.status) >= 0
                    && !imported && item.status !== 'aborted_voided';
                var voidBtn = canVoid
                    ? '<button type="button" class="sc-btn sc-btn--text sc-text-danger"'
                    + ' data-void-batch="' + escAttr(item.batch_id) + '">作废</button>'
                    : '';
                return '<div class="sc-row-actions">'
                    + '<a class="sc-btn sc-btn--text" target="_blank" rel="noopener"'
                    + ' href="' + escAttr(repUrl) + '">报告</a>'
                    + '<a class="sc-btn sc-btn--text" target="_blank" rel="noopener"'
                    + ' href="' + escAttr(failUrl) + '">失败行</a>'
                    + voidBtn
                    + '</div>';
            }}
        ],
        pagesOf: function (resp) { return resp.pages || 1; },
        onLoaded: function (resp) {
            $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条');
        }
    });
    list.reload();

    $('#searchForm').on('submit', function (e) { e.preventDefault(); list.reload(1); });
    $('#searchForm select, #searchForm input').on('change', function () { list.reload(1); });

    // 142：作废按钮（事件委托；confirm 走 ADR-022 页内确认前先原生确认兜底——
    // 本页未引 sc-modal 体系，保持轻量）
    $('#tableBody').on('click', '[data-void-batch]', function () {
        var bid = $(this).attr('data-void-batch');
        if (!window.confirm('确认作废批次 ' + bid + '？（0 行残留清理，之后可重新上传同一文件）')) { return; }
        $.ajax({
            url: '/import/' + encodeURIComponent(bid) + '/void',
            type: 'POST',
            success: function (resp) {
                if (resp && resp.success) { list.reload(); }
                else { window.alert((resp && resp.msg) || '作废失败'); }
            },
            error: function (xhr) {
                window.alert((xhr.responseJSON && xhr.responseJSON.msg) || '作废请求失败');
            }
        });
    });
});
})();
