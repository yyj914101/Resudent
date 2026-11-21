(function () {
var permissionModules = {
    'resident': '居民管理', 'medical_insurance': '医保缴费',
    'dibao': '低保管理', 'disabled': '残疾管理',
    'care': '关爱对象', 'organization': '组织管理', 'party': '党员管理',
    'veteran': '退伍军人管理', 'subsidy': '补贴发放',
    'welfare': '公益岗', 'mediation': '综治管理',
    'todo': '待办提醒', 'certificate': '证明材料',
    'notice': '补贴公示', 'shop': '店铺信息',
    'death_report': '死亡上报', 'dict': '字典管理', 'dynatable': '软表管理',
    'report': '报表统计', 'reconcile': '名单对账', 'safety': '安全工作',
    'visit': '走访管理', 'warn': '到期预警',
    'pension': '养老保险',
    'election': '公共事务·选举',
    'demand': '民生诉求',
    'sanitation': '环卫工作',
    'civilization': '文明实践'
};
var permissionActions = {'view': '查看', 'add': '新增', 'edit': '编辑', 'delete': '删除', 'export': '导出'};

var list = crudTable({
    endpoint: '/role-mgmt/api/list',
    columns: [
        {render: function(item) { return '<strong>' + item.name + '</strong>'; }},
        {field: 'description'},
        {field: 'user_count'},
        {render: function(item) {
            return item.is_system ? '<span class="sc-badge sc-badge--info">是</span>' : '<span class="sc-badge sc-badge--muted">否</span>';
        }},
        {field: 'created_at'},
        {render: function(item) {
            return '<div class="sc-row-actions">' +
                '<button class="sc-btn sc-btn--text" onclick="showEdit(' + item.id + ')">配置权限</button>' +
                '<button class="sc-btn sc-btn--text" onclick="showCopy(' + item.id + ')">复制</button>' +
                '<button class="sc-btn sc-btn--text is-danger" onclick="deleteRole(' + item.id + ')">删除</button>' +
                '</div>';
        }}
    ],
    pagesOf: function(resp) { return Math.ceil(resp.total / 20) || 1; },
    onLoaded: function(resp) { $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条'); }
});

$(function() {
    list.reload();
    $('#searchForm').on('submit', function(e) { e.preventDefault(); list.reload(1); });
    $('#saveForm').on('submit', function(e) { e.preventDefault(); saveData(); });
    $('#copyForm').on('submit', function(e) { e.preventDefault(); copyRole(); });
    $(document).off('click.rolePermBatch').on('click.rolePermBatch', '[data-perm-batch]', handlePermissionBatch);
    $(document).off('click.rolePermAction').on('click.rolePermAction', '[data-perm-action]', handlePermissionAction);
    $(document).off('click.roleModuleBatch').on('click.roleModuleBatch', '[data-module-batch]', handleModuleBatch);
});

function showAdd() {
    $('#editId').val('');
    $('#saveForm')[0].reset();
    $('#formTitle').text('新增角色');
    buildPermissions({});
    $('#formModal').modal('show');
}

function showEdit(id) {
    $.getJSON('/role-mgmt/api/list?page=1&limit=100', function(resp) {
        if (!resp.success) return;
        var item = resp.data.find(function(v) { return v.id === id; });
        if (!item) return;
        $('[name="id"]').val(item.id);
        $('[name="name"]').val(item.name);
        $('[name="description"]').val(item.description);
        buildPermissions(item.permissions || {});
        $('#formTitle').text('编辑角色');
        $('#formModal').modal('show');
    });
}

function buildPermissions(perms) {
    // 068 v9-02：矩阵表格（模块 × 5 动作 + 本组全选/清空），替换原分组按钮块。
    var html = '<table class="sc-table sc-table--dense sc-perm-table"><thead><tr>' +
        '<th class="sc-table__th">模块</th>';
    for (var actKey in permissionActions) {
        html += '<th class="sc-table__th" style="width:56px">' + permissionActions[actKey] + '</th>';
    }
    html += '<th class="sc-table__th" style="width:120px">本组</th></tr></thead><tbody>';
    for (var modKey in permissionModules) {
        html += '<tr><td><strong>' + permissionModules[modKey] + '</strong></td>';
        for (var a in permissionActions) {
            html += '<td><input type="checkbox" class="sc-checkbox perm-check" data-mod="' + modKey +
                '" data-act="' + a + '"' + (perms[modKey + '.' + a] ? ' checked' : '') + '></td>';
        }
        html += '<td><div class="sc-perm-tools">' +
            '<button type="button" class="sc-btn sc-btn--text" data-module-batch="all" data-mod="' + modKey + '">全选</button>' +
            '<button type="button" class="sc-btn sc-btn--text" data-module-batch="clear" data-mod="' + modKey + '">清空</button>' +
            '</div></td></tr>';
    }
    html += '</tbody></table>';
    $('#permissionsContainer').html(html);
    updatePermissionsInput();
    $('.perm-check').on('change', updatePermissionsInput);
}

function handlePermissionBatch() {
    var type = $(this).data('perm-batch');
    if (type === 'all') {
        $('.perm-check').prop('checked', true);
    } else if (type === 'clear') {
        $('.perm-check').prop('checked', false);
    } else if (type === 'view-only') {
        $('.perm-check').prop('checked', false);
        $('.perm-check[data-act="view"]').prop('checked', true);
    }
    updatePermissionsInput();
}

function handlePermissionAction() {
    var act = $(this).data('perm-action');
    $('.perm-check[data-act="' + act + '"]').prop('checked', true);
    updatePermissionsInput();
}

function handleModuleBatch() {
    var type = $(this).data('module-batch');
    var mod = $(this).data('mod');
    $('.perm-check[data-mod="' + mod + '"]').prop('checked', type === 'all');
    updatePermissionsInput();
}

function updatePermissionsInput() {
    var perms = {};
    $('.perm-check:checked').each(function() {
        perms[$(this).data('mod') + '.' + $(this).data('act')] = true;
    });
    $('#permissionsInput').val(JSON.stringify(perms));
}

function saveData() {
    updatePermissionsInput();
    $.post('/role-mgmt/api/save', $('#saveForm').serialize(), function(resp) {
        if (resp.success) {
            $('#formModal').modal('hide');
            list.reload();
            showToast(resp.msg, 'success');
        } else {
            showToast(resp.msg, 'danger');
        }
    });
}

function showCopy(id) {
    $('#copySourceId').val(id);
    $('#copyForm')[0].reset();
    $('#copyModal').modal('show');
}

function copyRole() {
    var id = $('#copySourceId').val();
    $.post('/role-mgmt/api/copy/' + id, $('#copyForm').serialize(), function(resp) {
        if (resp.success) {
            $('#copyModal').modal('hide');
            list.reload();
            showToast(resp.msg, 'success');
        } else {
            showToast(resp.msg, 'danger');
        }
    });
}

function deleteRole(id) {
    if (!confirm('确定要删除此角色吗？')) return;
    $.post('/role-mgmt/api/delete/' + id, function(resp) {
        if (resp.success) { list.reload(); showToast(resp.msg, 'success'); }
        else { showToast(resp.msg, 'danger'); }
    });
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ deleteRole: deleteRole, showAdd: showAdd, showCopy: showCopy, showEdit: showEdit }); }
})();
