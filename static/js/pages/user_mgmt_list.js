(function () {
var list = crudTable({
        endpoint: '/user-mgmt/api/list',
        columns: [
            {field: 'username'},
            {field: 'real_name'},
            {field: 'role_name'},
            {render: function(item) {
                return item.is_active
                    ? '<span class="sc-badge sc-badge--success">启用</span>'
                    : '<span class="sc-badge sc-badge--muted">停用</span>';
            }},
            {field: 'last_login'},
            {field: 'created_at'},
            {render: function(item) {
                var on = item.is_active;
                return '<div class="sc-row-actions">' +
                    '<button class="sc-btn sc-btn--text" onclick="showEdit(' + item.id + ')">编辑</button>' +
                    '<button class="sc-btn sc-btn--text" onclick="toggleUser(' + item.id + ')">' + (on ? '停用' : '启用') + '</button>' +
                    '<button class="sc-btn sc-btn--text" onclick="resetPwd(' + item.id + ')">重置口令</button>' +
                    '<button class="sc-btn sc-btn--text is-danger" onclick="deleteUser(' + item.id + ')">删除</button>' +
                    '</div>';
            }}
        ],
        pagesOf: function(resp) { return Math.ceil(resp.total / 20) || 1; },
        onLoaded: function(resp) { $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条'); }
});

$(function() {
    list.reload();
    loadRoles();
    $('#searchForm').on('submit', function(e) { e.preventDefault(); list.reload(1); });
    $('#saveForm').on('submit', function(e) { e.preventDefault(); saveData(); });
});

function loadRoles() {
    $.getJSON('/role-mgmt/api/list?page=1&limit=100', function(resp) {
        if (!resp.success) return;
        var opts = '';
        $.each(resp.data, function(i, item) {
            opts += '<option value="' + item.id + '">' + item.name + '</option>';
        });
        $('#roleSelect').html(opts);
    });
}

function showAdd() {
    $('#editId').val('');
    $('#saveForm')[0].reset();
    $('#formTitle').text('新增用户');
    $('#formModal').modal('show');
}

function showEdit(id) {
    $.getJSON('/user-mgmt/api/detail/' + id, function(resp) {
        if (!resp.success) return;
        var item = resp.data;
        if (!item) return;
        $('[name="id"]').val(item.id);
        $('[name="username"]').val(item.username);
        $('[name="real_name"]').val(item.real_name);
        $('[name="role_id"]').val(item.role_id);
        $('[name="password"]').val('');
        $('#formTitle').text('编辑用户');
        $('#formModal').modal('show');
    });
}

function saveData() {
    $.post('/user-mgmt/api/save', $('#saveForm').serialize(), function(resp) {
        if (resp.success) {
            $('#formModal').modal('hide');
            list.reload();
            showToast(resp.msg, 'success');
        } else {
            showToast(resp.msg, 'danger');
        }
    });
}

function toggleUser(id) {
    if (!confirm('确定要切换用户状态吗？')) return;
    $.post('/user-mgmt/api/toggle/' + id, function(resp) {
        if (resp.success) { list.reload(); showToast(resp.msg, 'success'); }
        else { showToast(resp.msg, 'danger'); }
    });
}

function resetPwd(id) {
    if (!confirm('确定要重置该用户密码为123456吗？')) return;
    $.post('/user-mgmt/api/reset-password/' + id, function(resp) {
        if (resp.success) { showToast(resp.msg, 'success'); }
        else { showToast(resp.msg, 'danger'); }
    });
}

function deleteUser(id) {
    if (!confirm('确定要删除此用户吗？')) return;
    $.post('/user-mgmt/api/delete/' + id, function(resp) {
        if (resp.success) { list.reload(); showToast(resp.msg, 'success'); }
        else { showToast(resp.msg, 'danger'); }
    });
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ deleteUser: deleteUser, resetPwd: resetPwd, showAdd: showAdd, showEdit: showEdit, toggleUser: toggleUser }); }
})();
