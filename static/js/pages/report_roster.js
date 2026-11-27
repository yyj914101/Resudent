(function () {
$(function() {
    var CFG = window.__PAGE_CFG__;
    function doExport(url, btn) {
        btn.prop('disabled', true);
        $.ajax({
            url: url,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                type: 'roster',
                category: CFG.category,
                grid: CFG.grid
            }),
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
    }
    $('#btnExportRoster').on('click', function() {
        doExport(CFG.exportExcel, $(this));
    });
    $('#btnExportPdf').on('click', function() {
        doExport(CFG.exportPdf, $(this));
    });
});
})();
