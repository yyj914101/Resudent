(function () {
function printNotice() {
    var nid = window.__PAGE_CFG__.noticeId;
    $.post('/death-report/api/notice/print', {id: nid}, function (res) {
        if (res && res.success) window.print();
        else if (res && res.msg) showToast ? showToast(res.msg, 'warning') : alert(res.msg);
    });
}
function exportNotice() {
    var rid = window.__PAGE_CFG__.drId;
    $.post('/death-report/api/notice/export', {id: rid}, function (res) {
        if (res && res.success && res.data && res.data.download_url) {
            window.location.href = res.data.download_url;
        } else if (res && res.msg) {
            showToast ? showToast(res.msg, 'warning') : alert(res.msg);
        }
    });
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.exportNotice = exportNotice;
window.printNotice = printNotice;
})();
