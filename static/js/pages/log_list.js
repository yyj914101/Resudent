(function () {
$(function() {
    var list = crudTable({
        endpoint: '/log/api/list',
        columnToggle: true, // 028 列显隐（F9）
        columns: [
            {field: 'user_name', title: '操作人'},
            {field: 'module', title: '模块'},
            {field: 'action', title: '操作类型', dict: {add: '新增', edit: '编辑', delete: '删除', export: '导出',
                import: '导入', done: '完成', batch_done: '批量完成',
                batch_delete: '批量删除', toggle: '切换', reset_password: '重置密码',
                copy: '复制', backup: '备份', restore: '还原',
                settings: '设置', generate: '生成'}},
            {field: 'summary', title: '摘要'},
            {title: '结果', render: function(item) {
                return item.result === 'success'
                    ? '<span class="sc-badge sc-badge--success">成功</span>'
                    : '<span class="sc-badge sc-badge--danger">失败</span>';
            }},
            {field: 'created_at', datetime: true, title: '时间'}
        ],
        pagesOf: function(resp) { return Math.ceil(resp.total / 20) || 1; },
        onLoaded: function(resp) { $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条'); }
    });
    list.reload();
    $('#searchForm').on('submit', function(e) { e.preventDefault(); list.reload(1); });
    // Update export link when search changes
    $('#searchForm input, #searchForm select').on('change', function() {
        var params = $('#searchForm').serialize();
        $('#exportBtn').attr('href', '/log/api/export?' + params);
    });
});
})();
