(function () {
$(function() {
    loadLicenseStatus();
    $('#btnCopyCode').on('click', copyMachineCode);
    $('#btnExport').on('click', function() { window.location.href = '/license/api/export'; });
    $('#btnImport').on('click', function() { $('#licenseFile').val(''); $('#importResult').html(''); $('#importModal').modal('show'); });
    $('#btnImportSubmit').on('click', importLicense);
    $('#btnRefresh').on('click', loadLicenseStatus);
});

function loadLicenseStatus() {
    $.get('/license/api/status', function(resp) {
        if (!resp.success) return;
        var d = resp.data;
        $('#colStatus').html(d.status === '已授权'
            ? '<span class="sc-badge sc-badge--success">已授权</span>'
            : '<span class="sc-badge sc-badge--muted">未授权</span>');
        $('#colType').text(d.type);
        $('#colMachine code').text(d.machine);
        $('#colExpire').text(d.expire);
        $('#colOwner').text(d.owner);
        $('#colDesc').text(d.desc);

        var tip = $('#statusTip');
        if (d.status === '已授权') {
            tip.show().removeClass('bg-light bg-warning-subtle').addClass('bg-success-subtle border border-success-subtle')
               .html('<i class="bi bi-check-circle-fill text-success me-1"></i> ' + d.desc);
        } else {
            tip.show().removeClass('bg-success-subtle').addClass('bg-light border')
               .html('<i class="bi bi-info-circle-fill text-secondary me-1"></i> ' + d.desc);
        }
    });
}

function copyMachineCode() {
    var code = $('#colMachine code').text();
    if (!code || code === '-') { showToast('无可复制的机器码', 'warning'); return; }
    if (navigator.clipboard) {
        navigator.clipboard.writeText(code).then(function() {
            showToast('✅ 机器码已复制到剪贴板', 'success');
        }).catch(function() {
            fallbackCopy(code);
        });
    } else {
        fallbackCopy(code);
    }
}

function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    showToast('✅ 机器码已复制到剪贴板', 'success');
}

function importLicense() {
    var file = $('#licenseFile')[0].files[0];
    if (!file) { showToast('请选择授权文件', 'warning'); return; }

    var fd = new FormData();
    fd.append('file', file);
    $('#btnImportSubmit').prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-1"></span>校验中...');

    $.ajax({
        url: '/license/api/import',
        type: 'POST',
        data: fd,
        processData: false,
        contentType: false,
        success: function(resp) {
            if (resp.success) {
                showToast(resp.msg, 'success');
                $('#importModal').modal('hide');
                loadLicenseStatus();
            } else {
                showToast(resp.msg || '导入失败', 'danger');
                $('#importResult').html('<span class="text-danger"><i class="bi bi-x-circle me-1"></i>' + (resp.msg || '导入失败') + '</span>');
            }
        },
        error: function(jqXHR) {
            var errMsg = '请求失败';
            if (jqXHR.status === 401) {
                errMsg = '登录已过期，请刷新页面重新登录';
            } else if (jqXHR.status === 403) {
                errMsg = '权限不足，仅超级管理员可导入授权';
            } else if (jqXHR.status >= 500) {
                errMsg = '服务器内部错误，请查看服务端控制台日志';
            } else if (jqXHR.status === 0) {
                errMsg = '无法连接到服务器，请检查网络';
            } else {
                errMsg = '请求失败 (HTTP ' + jqXHR.status + ')';
            }
            showToast(errMsg, 'danger');
            $('#importResult').html('<span class="text-danger"><i class="bi bi-x-circle me-1"></i>' + errMsg + '</span>');
        },
        complete: function() {
            $('#btnImportSubmit').prop('disabled', false).html('<i class="bi bi-check-circle me-1"></i>上传并校验');
        }
    });
}
})();
