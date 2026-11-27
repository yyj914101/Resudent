(function () {
function printChecklist() {
    var cid = window.__PAGE_CFG__.checklistId;
    if (cid) {
        $.post('/death-report/api/checklist/print', {id: cid}, function (res) {
            if (res && res.success) window.print();
            else if (res && res.msg) showToast ? showToast(res.msg, 'warning') : alert(res.msg);
        });
    } else {
        window.print();
    }
}
function exportChecklist() {
    var rid = window.__PAGE_CFG__.drId;
    $.post('/death-report/api/checklist/export', {id: rid}, function (res) {
        if (res && res.success && res.data && res.data.download_url) {
            window.location.href = res.data.download_url;
        } else if (res && res.msg) {
            showToast ? showToast(res.msg, 'warning') : alert(res.msg);
        }
    });
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.exportChecklist = exportChecklist;
window.printChecklist = printChecklist;
})();
