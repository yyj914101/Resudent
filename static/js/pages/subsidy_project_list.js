(function () {
function ageRange(item) {
    if (item.age_min == null && item.age_max == null) return '—';
    let lo = item.age_min != null ? item.age_min : '';
    let hi = item.age_max != null ? item.age_max : '';
    return lo + ' ~ ' + hi;
}

var list = crudTable({
    endpoint: '/subsidy/api/project/list',
    columns: [
        {field: '__index'},
        {field: 'name', esc: true},
        {field: 'project_type', esc: true},
        {field: 'cycle', esc: true},
        {render: function(item) { return item.default_amount != null ? item.default_amount : '—'; }},
        {render: function(item) { return ageRange(item); }},
        {render: function(item) { return esc((item.target_tags || []).join(', ')); }},
        {render: function(item) {
            return item.status === '启用'
                ? '<span class="sc-badge sc-badge--success">启用</span>'
                : '<span class="sc-badge sc-badge--muted">停用</span>';
        }},
        {field: 'remark', esc: true},
        {render: function(item) {
            // 2026-11-17 Master–Detail：行点击开详情后操作列只保留危险动作
            return '<span class="sc-row-actions">' +
                   '<button type="button" class="sc-btn sc-btn--text is-danger" onclick="delItem(' + item.id + ')">删除</button>' +
                   '</span>';
        }}
    ],
    // 2026-11-17：行点击 → 只读详情弹窗（Master–Detail 全站统一）
    onRowClick: function(item) { openDetail(item.id); },
    // 077 回灌：分页统计（sc-pagination__stat）：crudTable 不产共 N 条，由 onLoaded 回填
    onLoaded: function(resp) {
        $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条');
    }
});

$('#searchForm').submit(function(e) { e.preventDefault(); list.reload(1); });

function openAddModal() {
    $('#editId').val(0);
    $('#formModalTitle').text('新增补贴项目');
    $('#saveForm')[0].reset();
    $('#formModal').modal('show');
}

function openEdit(id) {
    $('#editId').val(id);
    $('#formModalTitle').text('编辑补贴项目');
    $.getJSON('/subsidy/api/project/detail/' + id, function(resp) {
        if (!resp.success) return;
        let item = resp.data;
        if (!item) return;
        $('[name="name"]').val(item.name);
        $('[name="project_type"]').val(item.project_type);
        $('[name="cycle"]').val(item.cycle);
        $('[name="default_amount"]').val(item.default_amount);
        $('[name="age_min"]').val(item.age_min);
        $('[name="age_max"]').val(item.age_max);
        $('#targetTagsInput').val((item.target_tags || []).join(', '));
        $('[name="status"]').val(item.status);
        $('[name="remark"]').val(item.remark);
        $('#formModal').modal('show');
    });
}

$('#saveForm').submit(function(e) {
    e.preventDefault();
    let tagsStr = $('#targetTagsInput').val().trim();
    let tagsArr = tagsStr ? tagsStr.split(',').map(function(s) { return s.trim(); }).filter(Boolean) : [];
    let data = $(this).serialize();
    data += '&target_tags=' + encodeURIComponent(JSON.stringify(tagsArr));
    $.ajax({
        url: '/subsidy/api/project/save',
        type: 'POST',
        data: data,
        success: function(resp) {
            if (resp.success) { $('#formModal').modal('hide'); showToast(resp.msg, 'success'); list.reload(); }
            else { showToast(resp.msg, 'danger'); }
        }
    });
});

function delItem(id) {
    if (!confirm('确定要删除该补贴项目吗？')) return;
    $.ajax({
        url: '/subsidy/api/project/delete/999'.replace('999', id),
        type: 'POST',
        success: function(resp) {
            if (resp.success) { showToast(resp.msg, 'success'); list.reload(); }
            else { showToast(resp.msg, 'danger'); }
        }
    });
}

// 2026-11-17 Master–Detail：行点击 → 只读详情弹窗（复用项目详情接口）
function openDetail(id) {
    $.getJSON('/subsidy/api/project/detail/' + id, function(resp) {
        if (!resp.success || !resp.data) { showToast(resp.msg || '读取失败', 'danger'); return; }
        let item = resp.data;
        let rows = [
            ['项目名称', item.name], ['项目类型', item.project_type], ['发放周期', item.cycle],
            ['默认金额（元）', item.default_amount], ['适用年龄', ageRange(item)],
            ['目标人群标签', (item.target_tags || []).join('、')],
            ['状态', item.status]
        ];
        let html = window.SC.renderDetailCards(rows, [['备注', item.remark]]);
        $('#projDetailBody').html(html);
        $('#projDetailTitle').text((item.name || '补贴项目') + ' · 项目详情');
        $('#projDetailBtns').html(
            '<button type="button" class="sc-btn sc-btn--secondary" data-bs-dismiss="modal">关闭</button>' +
            '<button type="button" class="sc-btn sc-btn--primary" onclick="openEdit(' + item.id + ')"><i class="bi bi-pencil"></i> 编辑</button>');
        $('#projDetailModal').modal('show');
    });
}

$(function() { list.reload(1); });

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ delItem: delItem, openAddModal: openAddModal, openEdit: openEdit, openDetail: openDetail }); }
})();
