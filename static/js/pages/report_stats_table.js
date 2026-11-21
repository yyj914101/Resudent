(function () {
$(function() {
    $('#btnExportStats').on('click', function() {
        var btn = $(this).prop('disabled', true);
        var grid = $('select[name="grid"]').val() || '';
        $.ajax({
            url: '/report/export-excel',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ type: 'stats', grid: grid }),
            success: function(resp) {
                if (resp.success) {
                    window.location.href = resp.data.download_url;
                } else {
                    alert(resp.msg || '导出失败');
                }
            },
            error: function(xhr) {
                var msg = '导出失败';
                try { msg = JSON.parse(xhr.responseText).msg || msg; } catch(e) {} // responseText 非 JSON（如 500 HTML）时兜底默认 msg
                alert(msg);
            },
            complete: function() { btn.prop('disabled', false); }
        });
    });
});
})();
