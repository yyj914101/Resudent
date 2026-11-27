(function () {
var crud = crudTable({
    endpoint: '/organization/api/list',
    emptyColspan: 11,
    limit: 20,
    columns: [
        {field: '__index'},
        {field: 'org_type', esc: true},
        {field: 'name', esc: true},
        {field: 'position', esc: true},
        {field: 'id_card', esc: true},
        {field: 'phone', esc: true},
        {field: 'politics_status', esc: true},
        {field: 'start_date'},
        {field: 'end_date'},
        {render: function(item) {
            // 062 徽章映射（组件-handoff §2.4）：bg-success→sc-badge--success、bg-secondary→sc-badge--muted
            return item.is_current
                ? '<span class="sc-badge sc-badge--success">现任</span>'
                : '<span class="sc-badge sc-badge--muted">离任</span>';
        }},
        {render: function(item) {
            // 2026-11-17 Master–Detail：行点击开详情后操作列只保留危险动作
            return '<div class="sc-row-actions">'
                 + '<button type="button" class="sc-btn sc-btn--text is-danger" onclick="deleteData(' + item.id + ')">删除</button>'
                 + '</div>';
        }}
    ],
    // 2026-11-17：行点击 → 只读详情弹窗（Master–Detail 全站统一）
    onRowClick: function(item) { openDetail(item.id); },
    onLoaded: function(resp) {
        $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条');
    }
});

function searchData(page) { crud.reload(page); }

function openAddModal() {
    $('#modalTitle').text('新增组织成员');
    $('#editId').val('');
    $('#dataForm')[0].reset();
    $('#f_is_current').prop('checked', true);
    $('#formModal').modal('show');
}

function editData(id) {
    $.getJSON('/organization/api/list', {page: 1, limit: 10000}, function(res) {
        const item = res.data.find(function(d) { return d.id === id; });
        if (!item) return;
        $('#modalTitle').text('编辑组织成员');
        $('#editId').val(item.id);
        $('#f_org_type').val(item.org_type);
        $('#f_name').val(item.name);
        $('#f_position').val(item.position);
        $('#f_id_card').val(item.id_card);
        $('#f_phone').val(item.phone);
        $('#f_politics_status').val(item.politics_status);
        $('#f_start_date').val(item.start_date);
        $('#f_end_date').val(item.end_date);
        $('#f_is_current').prop('checked', item.is_current);
        $('#formModal').modal('show');
    });
}

// 2026-11-17 Master–Detail：行点击 → 只读详情弹窗（列表响应内含行数据，无需二次请求）
function openDetail(id) {
    $.getJSON('/organization/api/list', {page: 1, limit: 10000}, function(res) {
        const item = res.data.find(function(d) { return d.id === id; });
        if (!item) { showToast('记录不存在', 'danger'); return; }
        const rows = [
            ['组织类型', item.org_type], ['姓名', item.name], ['职务', item.position],
            ['身份证号', item.id_card], ['联系电话', item.phone], ['政治面貌', item.politics_status],
            ['任职开始', item.start_date], ['任职结束', item.end_date],
            ['是否现任', item.is_current ? '现任' : '离任']
        ];
        let html = window.SC.renderDetailCards(rows);
        $('#orgDetailBody').html(html);
        $('#orgDetailTitle').text((item.name || '组织成员') + ' · 详情');
        $('#orgDetailBtns').html(
            '<button type="button" class="sc-btn sc-btn--secondary" data-bs-dismiss="modal">关闭</button>' +
            '<button type="button" class="sc-btn sc-btn--primary" onclick="editData(' + item.id + ')"><i class="bi bi-pencil"></i> 编辑</button>');
        $('#orgDetailModal').modal('show');
    });
}

function deleteData(id) {
    if (!confirm('确认删除该记录吗？')) return;
    $.post('/organization/api/delete/' + id, function(res) {
        if (res.success) {
            showToast('删除成功', 'success');
            searchData();
        } else {
            showToast(res.msg, 'danger');
        }
    });
}

$('#dataForm').submit(function(e) {
    e.preventDefault();
    $.post('/organization/api/save', $(this).serialize(), function(res) {
        if (res.success) {
            $('#formModal').modal('hide');
            showToast('保存成功', 'success');
            searchData();
        } else {
            showToast(res.msg, 'danger');
        }
    });
});

function exportData() {
    const params = $.param({
        org_type: $('[name="org_type"]').val(),
        is_current: $('[name="is_current"]').val(),
    });
    window.location.href = '/organization/api/export?' + params;
}

// ─── Load dictionary options into a select element ───
$(function() {
    // Load dictionary options for dropdowns
    dictSelect('机构类型', '#search_org_type', '全部机构');
    dictSelect('机构类型', '#f_org_type', '请选择');
    dictSelect('组织职务', '#f_position', '请选择');
    dictSelect('\u653f\u6cbb\u9762\u8c8c', '#f_politics_status', '\u8bf7\u9009\u62e9');
    searchData();
});

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.deleteData = deleteData;
window.editData = editData;
window.exportData = exportData;
window.openAddModal = openAddModal;
window.searchData = searchData;
})();
