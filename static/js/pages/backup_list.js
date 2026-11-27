(function () {
$(function() {
    loadBackups();
    loadSettings();

    $('#settingsForm').on('submit', function(e) {
        e.preventDefault();
        var data = $(this).serialize();
        if (!$('#inputAutoBackup').is(':checked')) {
            data = data.replace('enable_auto=1', 'enable_auto=0');
            if (data.indexOf('enable_auto') === -1) data += '&enable_auto=0';
        }
        $.post('/backup/api/settings', data, function(resp) {
            if (resp.success) showToast(resp.msg, 'success');
            else showToast(resp.msg, 'danger');
        });
    });
});

function loadBackups() {
    $.getJSON('/backup/api/list', function(resp) {
        if (!resp.success) return;
        var html = '';
        var bytes = 0;
        $.each(resp.data, function(i, item) {
            bytes += (item.size || 0);
            html += '<tr>' +
                '<td>' + item.filename + '</td>' +
                '<td>' + item.size_str + '</td>' +
                '<td>' + item.created_at + '</td>' +
                '<td>' +
                '<div class="sc-row-actions">' +
                '<a class="sc-btn sc-btn--text" href="/backup/api/download/' + item.filename + '">下载</a>' +
                '<button type="button" class="sc-btn sc-btn--text" onclick="restoreBackup(\'' + item.filename + '\')">还原</button>' +
                '<button type="button" class="sc-btn sc-btn--text is-danger" onclick="deleteBackup(\'' + item.filename + '\')">删除</button>' +
                '</div>' +
                '</td></tr>';
        });
        $('#backupBody').html(html || '<tr><td colspan="4" class="text-center text-muted">暂无备份文件</td></tr>');
        var count = resp.data ? resp.data.length : 0;
        $('#backupMeta').text('共 ' + count + ' 个 · 占用 ' + (bytes / 1048576).toFixed(1) + ' MB');
    });
}

function loadSettings() {
    $.getJSON('/settings/api/notification/data', function(resp) {
        if (resp.success) return;
    });
    // Try to load backup-specific settings from dict
    $.getJSON('/dict/api/categories', function(resp) {
        if (resp.success) {
            // Settings are stored in DictCategory '系统设置'
        }
    });
    // For now, set defaults
    $('#inputBackupTime').val('02:00');
    $('#inputRetentionDays').val('30');
}

function createBackup() {
    $.post('/backup/api/backup', function(resp) {
        if (resp.success) { loadBackups(); showToast(resp.msg, 'success'); }
        else { showToast(resp.msg, 'danger'); }
    });
}

function uploadAndRestore() {
    var file = $('#restoreFile')[0].files[0];
    if (!file) { showToast('请选择 .db 备份文件', 'warning'); return; }
    if (!confirm('确定要从上传的文件恢复数据库吗？\n当前数据将被覆盖，此操作不可撤销！')) return;
    var fd = new FormData();
    fd.append('file', file);
    $.ajax({
        url: '/backup/api/restore-upload',
        type: 'POST',
        data: fd,
        processData: false,
        contentType: false,
        success: function(resp) {
            if (resp.success) {
                showToast(resp.msg, 'success');
                setTimeout(function() { location.href = '/auth/logout'; }, 2000);
            } else {
                showToast(resp.msg, 'danger');
            }
        },
        error: function() {
            showToast('上传恢复请求失败', 'danger');
        }
    });
}

function restoreBackup(filename) {
    if (!confirm('确定要从 ' + filename + ' 恢复数据库吗？\n当前数据将被覆盖，此操作不可撤销！')) return;
    $.post('/backup/api/restore/' + filename, function(resp) {
        if (resp.success) {
            showToast(resp.msg, 'success');
            setTimeout(function() { location.href = '/auth/logout'; }, 2000);
        } else {
            showToast(resp.msg, 'danger');
        }
    });
}

function deleteBackup(filename) {
    if (!confirm('确定要删除备份文件 ' + filename + ' 吗？')) return;
    $.post('/backup/api/delete/' + filename, function(resp) {
        if (resp.success) { loadBackups(); showToast(resp.msg, 'success'); }
        else { showToast(resp.msg, 'danger'); }
    });
}

function openClearDbModal() {
    $('#clearDbPassword').val('');
    $('#clearDbModal').modal('show');
}
$('#btnClearDbConfirm').on('click', function() {
    var pwd = $('#clearDbPassword').val();
    if (!pwd) { showToast('请输入登录密码确认', 'warning'); return; }
    if (!confirm('确认要清空所有业务数据吗？此操作不可恢复！')) return;
    $('#btnClearDbConfirm').prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span> 处理中...');
    $.post('/backup/api/clear-db', {password: pwd}, function(resp) {
        $('#clearDbModal').modal('hide');
        if (resp.success) {
            showToast(resp.msg, 'success');
        } else {
            showToast(resp.msg, 'danger');
        }
    }).always(function() {
        $('#btnClearDbConfirm').prop('disabled', false).html('<i class="bi bi-check-lg"></i> 确认清空');
        $('#clearDbPassword').val('');
    });
});
$('#clearDbModal').on('hidden.bs.modal', function() {
    $('#clearDbPassword').val('');
});

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.createBackup = createBackup;
window.deleteBackup = deleteBackup;
window.openClearDbModal = openClearDbModal;
window.restoreBackup = restoreBackup;
window.uploadAndRestore = uploadAndRestore;
})();
