(function () {
var list = crudTable({
    endpoint: '/visit/api/list',
    columns: [
        {field: '__index'},
        {field: 'plan_name', esc: true},
        {field: 'plan_type', esc: true},
        {render: function(item) { return item.target_count != null ? item.target_count : '—'; }},
        {render: function(item) { return rateCell(item); }},
        {render: function(item) { return statusBadge(item.status); }},
        {field: 'created_at', esc: true},
        {render: function(item) {
            // 067 §2.12 行内操作组：≤2 动作 12px；危险动作恒末位
            return '<span class="sc-row-actions sc-row-actions--tight">' +
                   '<button type="button" class="sc-btn sc-btn--text" onclick="goEntry(' + item.id + ')">补录</button>' +
                   '<button type="button" class="sc-btn sc-btn--text" onclick="goEntry(' + item.id + ')">清单</button>' +
                   '<button type="button" class="sc-btn sc-btn--text" onclick="openEdit(' + item.id + ')">编辑</button>' +
                   '<button type="button" class="sc-btn sc-btn--text is-danger" onclick="delItem(' + item.id + ')">删除</button>' +
                   '</span>';
        }}
    ],
    // 067 分页统计（sc-pagination__stat）：crudTable 不产共 N 条，由 onLoaded 回填
    onLoaded: function(resp) {
        $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条');
    }
});

function statusBadge(s) {
    // 067 §2.4 映射：bg-primary→primary、bg-success→success、bg-secondary→muted（真实字段 status）
    if (s === '进行中') return '<span class="sc-badge sc-badge--primary">进行中</span>';
    if (s === '已完成') return '<span class="sc-badge sc-badge--success">已完成</span>';
    return '<span class="sc-badge sc-badge--muted">草稿</span>';
}

function rateCell(item) {
    let c = item.completed_count != null ? item.completed_count : 0;
    let t = item.target_count != null ? item.target_count : 0;
    let rate = item.rate != null ? item.rate : 0;
    let color = rate >= 90 ? 'sc-badge--success' : (rate > 0 ? 'sc-badge--warning' : 'sc-badge--muted');
    return c + ' / ' + t + ' <span class="sc-badge ' + color + '">' + rate + '%</span>';
}

function goEntry(id) { location.href = '/visit/record_entry?plan_id=' + id; }

$('#searchForm').submit(function(e) { e.preventDefault(); list.reload(1); });

function syncScopeFields() {
    let t = $('[name="plan_type"]').val();
    $('.scope-group').hide();
    if (t) $('#scope-' + t).show();
}

function collectScopeParams() {
    let t = $('[name="plan_type"]').val();
    if (t === '按区域') {
        let af = $('[name="area_field"]').val() || 'village_group';
        let values = $('[name="area_values"]').val().split(',').map(function(s){ return s.trim(); }).filter(Boolean);
        return JSON.stringify({area_field: af, values: values});
    }
    if (t === '按对象类型') {
        let types = [];
        $('input[name="obj_type"]:checked').each(function(){ types.push($(this).val()); });
        return JSON.stringify({types: types});
    }
    if (t === '按标签') {
        let tags = [];
        $('input[name="tag_item"]:checked').each(function(){ tags.push($(this).val()); });
        return JSON.stringify({tags: tags});
    }
    return '';
}

function openAddModal() {
    $('#editId').val(0);
    $('#formModalTitle').text('新增走访计划');
    $('#saveForm')[0].reset();
    $('.scope-group').hide();
    $('#targetCountPreview').val('');
    $('#formModal').modal('show');
}

function openEdit(id) {
    $('#editId').val(id);
    $('#formModalTitle').text('编辑走访计划');
    $.getJSON('/visit/api/detail/' + id, function(resp) {
        if (!resp.success) return;
        let item = resp.data;
        if (!item) return;
        $('[name="plan_name"]').val(item.plan_name);
        $('[name="plan_type"]').val(item.plan_type);
        $('[name="status"]').val(item.status);
        $('#targetCountPreview').val(item.target_count != null ? item.target_count : '');
        $('.scope-group').hide();
        $('input[name="obj_type"]').prop('checked', false);
        $('input[name="tag_item"]').prop('checked', false);
        let sp = {};
        try { sp = JSON.parse(item.scope_params || '{}'); } catch (e) { sp = {}; }
        if (item.plan_type === '按区域') {
            $('[name="area_field"]').val(sp.area_field || 'village_group');
            $('[name="area_values"]').val((sp.values || []).join(','));
            $('#scope-按区域').show();
        } else if (item.plan_type === '按对象类型') {
            (sp.types || []).forEach(function(v){ $('input[name="obj_type"][value="' + v + '"]').prop('checked', true); });
            $('#scope-按对象类型').show();
        } else if (item.plan_type === '按标签') {
            (sp.tags || []).forEach(function(v){ $('input[name="tag_item"][value="' + v + '"]').prop('checked', true); });
            $('#scope-按标签').show();
        }
        $('#formModal').modal('show');
    });
}

$('#saveForm').submit(function(e) {
    e.preventDefault();
    let data = $(this).serialize();
    data += '&scope_params=' + encodeURIComponent(collectScopeParams());
    $.ajax({
        url: '/visit/api/save',
        type: 'POST',
        data: data,
        success: function(resp) {
            if (resp.success) {
                $('#formModal').modal('hide');
                showToast(resp.msg, 'success');
                list.reload();
            } else { showToast(resp.msg, 'danger'); }
        }
    });
});

function delItem(id) {
    if (!confirm('确定要删除该走访计划吗？')) return;
    $.ajax({
        url: '/visit/api/delete/999'.replace('999', id),
        type: 'POST',
        success: function(resp) {
            if (resp.success) { showToast(resp.msg, 'success'); list.reload(); }
            else { showToast(resp.msg, 'danger'); }
        }
    });
}

$(function() { list.reload(1); });

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ delItem: delItem, goEntry: goEntry, openAddModal: openAddModal, openEdit: openEdit, syncScopeFields: syncScopeFields }); }
})();
