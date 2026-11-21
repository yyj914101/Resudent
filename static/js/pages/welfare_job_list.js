(function () {
var list = crudTable({
    endpoint: '/welfare/api/list',
    columns: [
        {field: '__index'},
        {field: 'name', esc: true},
        {field: 'village_group', esc: true},
        {field: 'id_card', esc: true},
        {field: 'phone', esc: true},
        {field: 'job_type', esc: true},
        {field: 'start_date'},
        {field: 'end_date'},
        {field: 'monthly_amount'},
        {field: 'bank_info', esc: true},
        {render: function(item) {
            // 2026-11-17 Master–Detail：行点击开详情后操作列只保留危险动作
            return '<span class="sc-row-actions">' +
                   '<button type="button" class="sc-btn sc-btn--text is-danger" onclick="delItem(' + item.id + ')">删除</button>' +
                   '</span>';
        }}
    ],
    // 2026-11-17：行点击 → 只读详情弹窗（Master–Detail 全站统一）
    onRowClick: function(item) { openDetail(item.id); },
    // 067 分页统计（sc-pagination__stat）：crudTable 不产共 N 条，由 onLoaded 回填
    onLoaded: function(resp) {
        $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条');
    }
});

$('#searchForm').submit(function(e) { e.preventDefault(); list.reload(1); });

// B5（GLB-47）：导出必须带当前筛选（原静态链接只读渲染时 URL，不读表单）
$('#btnExport').on('click', function() {
    var base = $(this).data('export-url');
    var qs = $('#searchForm').serialize();
    window.location.href = base + (qs ? '?' + qs : '');
});

function openAddModal() {
    $('#editId').val(0);
    $('#formModalTitle').text('新增公益岗');
    $('#formModalMeta').text('新增公益岗记录');
    $('#saveForm')[0].reset();
    $('#formModal').modal('show');
}

function openEdit(id) {
    $('#editId').val(id);
    $('#formModalTitle').text('编辑公益岗');
    $.getJSON('/welfare/api/detail/' + id, function(resp) {
        if (!resp.success) return;
        let item = resp.data;
        if (!item) return;
        // 详情→编辑切换：先收详情再开编辑（对齐 dibao/disabled 范式；修双 backdrop 叠加与焦点滞留）
        const detailInst = bootstrap.Modal.getInstance(document.getElementById('detailModal'));
        if (detailInst && document.getElementById('detailModal').classList.contains('show')) {
            document.getElementById('detailModal').addEventListener('hidden.bs.modal', () => $('#formModal').modal('show'), { once: true });
            detailInst.hide();
            return;
        }
        $('#formModalMeta').text((item.name || '') + ' · 修改后保存生效');
        $('[name="name"]').val(item.name);
        $('[name="village_group"]').val(item.village_group);
        $('[name="id_card"]').val(item.id_card);
        $('[name="phone"]').val(item.phone);
        $('[name="job_type"]').val(item.job_type);
        $('[name="start_date"]').val(item.start_date);
        $('[name="end_date"]').val(item.end_date);
        $('[name="monthly_amount"]').val(item.monthly_amount);
        $('[name="bank_info"]').val(item.bank_info);
        $('#formModal').modal('show');
    });
}

// 2026-11-17 Master–Detail：行点击 → 只读详情弹窗（sc-detail 键值栅格，复用 openEdit 取数链路）
function openDetail(id) {
    $.getJSON('/welfare/api/detail/' + id, function(resp) {
        if (!resp.success) { showToast(resp.msg || '读取失败', 'danger'); return; }
        let item = resp.data;
        if (!item) return;
        let rows = [
            ['姓名', item.name], ['所在组', item.village_group], ['身份证号', item.id_card],
            ['联系电话', item.phone], ['岗位类型', item.job_type], ['开始日期', item.start_date],
            ['结束日期', item.end_date], ['月补贴金额（元）', item.monthly_amount], ['银行账户', item.bank_info]
        ];
        let html = '<div class="sc-detail__grid"><div class="sc-detail__col">';
        $.each(rows, function(_, r) {
            html += '<div class="sc-detail__field">' +
                '<span class="sc-detail__label">' + r[0] + '</span>' +
                '<span class="sc-detail__value">' + esc(r[1] === null || r[1] === undefined || r[1] === '' ? '—' : r[1]) + '</span></div>';
        });
        html += '</div></div>';
        $('#detailBody').html(html);
        $('#detailModalTitle').text((item.name || '公益岗') + ' · 公益岗详情');
        $('#detailBtns').html(
            '<button type="button" class="sc-btn sc-btn--secondary" data-bs-dismiss="modal">关闭</button>' +
            '<button type="button" class="sc-btn sc-btn--primary" onclick="openEdit(' + item.id + ')"><i class="bi bi-pencil"></i> 编辑</button>');
        getModal('detailModal').show();
    });
}

$('#saveForm').submit(function(e) {
    e.preventDefault();
    $.ajax({
        url: '/welfare/api/save',
        type: 'POST',
        data: $(this).serialize(),
        success: function(resp) {
            if (resp.success) { $('#formModal').modal('hide'); showToast(resp.msg, 'success'); list.reload(); }
            else { showToast(resp.msg, 'danger'); }
        }
    });
});

function delItem(id) {
    if (!confirm('确定要删除该条记录吗？')) return;
    $.ajax({
        url: '/welfare/api/delete/999'.replace('999', id),
        type: 'POST',
        success: function(resp) {
            if (resp.success) { showToast(resp.msg, 'success'); list.reload(); }
            else { showToast(resp.msg, 'danger'); }
        }
    });
}

// 导入弹窗
function openImportModal() {
    $('#importFile').val('');
    $('#importResult').addClass('d-none').html('');
    $('#importProgress').addClass('d-none');
    $('#importModal').modal('show');
}
$('#btnImportSubmit').on('click', function() {
    importWizard({fileSel:'#importFile', resultSel:'#importResult', endpoint:'/welfare/api/import',
        extraData:{mode:$('input[name="importMode"]:checked').val()},
        progressSel:'#importProgress', submitBtnSel:'#btnImportSubmit',
        onDone:function(){ list.reload(1); }});
});

function fillDictSelect(category, selectors, blankText) {
    dictStore.ready(function() {
        var html = '<option value="">' + blankText + '</option>';
        $.each(dictStore.items(category), function(i, item) {
            var val = esc(item.value || item.label);
            html += '<option value="' + val + '">' + esc(item.label) + '</option>';
        });
        selectors.forEach(function(selector) {
            $(selector).html(html);
        });
    });
}

function loadDicts() {
    fillDictSelect('所在组', ['#searchVillageGroup', '#formVillageGroup'], '请选择所在组');
    fillDictSelect('岗位类型（公益岗）', ['#searchJobType', '#formJobType'], '请选择岗位类型');
}

$(function() { loadDicts(); list.reload(1); });

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ delItem: delItem, openAddModal: openAddModal, openEdit: openEdit, openImportModal: openImportModal }); }
})();

// 2026-11-19 换壳配套：导入弹窗投放区（app.js wireImportDropzone 全局单源）。
// #importFile 仍是真实 file input，btnImportSubmit→importWizard 链路不变。
$(function () {
    wireImportDropzone({
        fileSel: '#importFile', dropSel: '#welfareImpDrop', stagedSel: '#welfareImpStaged',
        stagedNameSel: '#welfareImpStagedName', stagedMetaSel: '#welfareImpStagedMeta',
        submitBtnSel: '#btnImportSubmit', reselectSel: '#welfareImpReselect', removeSel: '#welfareImpRemove',
        modalSel: '#importModal'
    });
});
