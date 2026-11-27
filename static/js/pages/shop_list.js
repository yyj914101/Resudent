/* shop_list.js — 店铺信息台账（居民管理组）
 * 固定字段 CRUD + Excel 批量导入/导出；IIFE 化，事件经 #tableBody 委托绑定。
 * 101 年度缴费融合：当年状态列 + 详情历年记录/登记（feeModal）+ 按年度批量登记（feeBatchModal）。
 */
(function () {
    'use strict';
    var currentPage = 1;
    var pageSize = 20;
    var baseUrl = '/shop';
    // 101：缴费件上下文（模板仅就绪态注入 #feeCtx；未就绪 = 列/按钮/弹窗全部缺席，行为同旧版）
    var FEE_CTX = null;
    try {
        var raw = document.getElementById('feeCtx');
        FEE_CTX = raw ? JSON.parse(raw.textContent || '{}') : null;
    } catch (e) { FEE_CTX = null; }
    var FEE_READY = !!(FEE_CTX && FEE_CTX.year);
    var COL_COUNT = FEE_READY ? 9 : 8;

    function feeCell(item) {
        if (!FEE_READY || !item.fee) return '<td>-</td>';
        if (item.fee.status === '已缴') {
            return '<td><span class="sc-badge sc-badge--success">已缴</span>' +
                (item.fee.amount != null ? ' <span style="color:var(--c-ink-2)">¥' +
                  Number(item.fee.amount).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) + '</span>' : '') +
                '</td>';
        }
        return '<td><span class="sc-badge sc-badge--muted">未缴</span></td>';
    }

    function loadData(page) {
        currentPage = Math.max(1, page || 1);
        var query = $('#searchForm').serialize();
        $('#exportLink').attr('href', baseUrl + '/api/export?' + query);
        $.getJSON(baseUrl + '/api/list?' + query + '&page=' + currentPage + '&limit=' + pageSize, function (resp) {
            if (!resp.success) { showToast(resp.msg || '查询失败', 'danger'); return; }
            var html = '';
            $.each(resp.data || [], function (index, item) {
                // 062 P2 v4.0：行内操作换纯文本按钮（组件-handoff §2.12），绑定属性 data-edit/data-delete 不变
                html += '<tr data-id="' + item.id + '" style="cursor:pointer"><td>' + ((currentPage - 1) * pageSize + index + 1) + '</td>' +
                    '<td class="sc-ellipsis" style="max-width:180px" title="' + escAttr(item.name) + '">' + esc(item.name) + '</td>' +
                    '<td class="sc-ellipsis" style="max-width:180px">' + esc(item.street || '-') + '</td>' +
                    '<td>' + esc(item.house_number || '-') + '</td>' +
                    '<td>' + esc(item.owner_name || '-') + '</td>' +
                    '<td>' + esc(item.id_card || '-') + '</td>' +
                    '<td>' + esc(item.phone || '-') + '</td>' +
                    feeCell(item) +
                    '<td><div class="sc-row-actions">' +
                    (FEE_READY ? '<button class="sc-btn sc-btn--text" type="button" data-fee="' + escAttr(item.name) + '" data-fee-id="' + item.id + '">缴费</button>' : '') +
                    '<button class="sc-btn sc-btn--text is-danger" type="button" data-delete="' + item.id + '">删除</button>' +
                    '</div></td></tr>';
            });
            $('#tableBody').html(html || '<tr><td colspan="' + COL_COUNT + '" class="text-center text-muted py-4">暂无店铺记录</td></tr>');
            $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 ' + pageSize + ' 条');
            var pages = Math.max(1, Math.ceil((resp.total || 0) / pageSize));
            renderPagination('#pagination', pages, currentPage, loadData);
        });
    }

    function openAdd() {
        $('#saveForm')[0].reset();
        $('#recordId').val('');
        $('#formTitle').text('新增店铺');
        getModal('formModal').show();
    }

    function editRecord(id) {
        $.getJSON(baseUrl + '/api/detail/' + id, function (resp) {
            if (!resp.success) { showToast(resp.msg || '读取失败', 'danger'); return; }
            $('#saveForm')[0].reset();
            $('#recordId').val(id);
            $('#formTitle').text('编辑店铺');
            $.each(['name', 'street', 'house_number', 'owner_name', 'id_card', 'phone'], function (_, key) {
                $('#saveForm [name="' + key + '"]').val(resp.data[key] || '');
            });
            getModal('formModal').show();
        });
    }

    // 2026-11-17 Master–Detail：行点击 → 只读详情弹窗（复用 /api/detail）
    function openDetail(id) {
        $.getJSON(baseUrl + '/api/detail/' + id, function (resp) {
            if (!resp.success) { showToast(resp.msg || '读取失败', 'danger'); return; }
            var d = resp.data;
            var rows = [
                ['店铺名称', d.name], ['所在街道', d.street], ['门牌号', d.house_number],
                ['经营者姓名', d.owner_name], ['身份证号', d.id_card], ['联系电话', d.phone]
            ];
            var html = window.SC.renderDetailCards(rows);
            $('#shopDetailBody').html(html);
            $('#shopDetailTitle').text((d.name || '店铺') + ' · 店铺详情');
            $('#shopDetailBtns').html(
                '<button type="button" class="sc-btn sc-btn--secondary" data-bs-dismiss="modal">关闭</button>' +
                '<button type="button" class="sc-btn sc-btn--primary" onclick="editRecord(' + id + ')"><i class="bi bi-pencil"></i> 编辑</button>');
            getModal('shopDetailModal').show();
        });
    }

    function deleteRecord(id) {
        if (!confirm('确定删除该条店铺记录吗？删除后不可恢复。')) { return; }
        $.post(baseUrl + '/api/delete/' + id, function (resp) {
            showToast(resp.msg || '操作完成', resp.success ? 'success' : 'danger');
            if (resp.success) { loadData(currentPage); }
        });
    }

    // ── 101 年度缴费：详情（历年记录 + 登记） ──────────────────────────
    var feeDetail = null;   // 最近一次 /shop/annual-fee/detail 响应（含字段 codes）

    function openFeeModal(name) {
        $.getJSON(baseUrl + '/annual-fee/detail', { name: name }, function (resp) {
            if (!resp.success) { showToast(resp.msg || '读取失败', 'danger'); return; }
            feeDetail = resp.data;
            $('#feeModalTitle').text('年度缴费 · ' + name);
            $('#feeShopName').val(name);
            $('#feeYear').val(feeDetail.year || '');
            $('#feeAmount').val('');
            renderFeeHistory(feeDetail);
            getModal('feeModal').show();
        });
    }

    function renderFeeHistory(data) {
        var optional = (data.codes && data.codes.optional) || {};
        var optNames = Object.keys(optional);
        var head = '<th class="sc-table__th" style="width:90px">缴费年度</th>' +
            '<th class="sc-table__th" style="width:120px">实缴金额（元）</th>';
        optNames.forEach(function (n) { head += '<th class="sc-table__th">' + esc(n) + '</th>'; });
        head += '<th class="sc-table__th" style="width:130px">登记时间</th>';
        $('#feeHistoryHead').html(head);
        var rows = '';
        (data.records || []).forEach(function (r) {
            var row = '<td>' + esc(r.year) + '</td>' +
                '<td style="text-align:right">' + (r.amount != null && r.amount !== '' ?
                  '¥' + Number(r.amount).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '-') + '</td>';
            optNames.forEach(function (n) { row += '<td>' + esc(r[n] || '-') + '</td>'; });
            row += '<td>' + esc(r.created_at || '-') + '</td>';
            rows += '<tr>' + row + '</tr>';
        });
        $('#feeHistoryBody').html(rows ||
            '<tr><td colspan="' + (2 + optNames.length + 1) + '" class="text-center text-muted py-3">暂无缴费记录，可在下方登记</td></tr>');
    }

    function submitFeeRegister(e) {
        e.preventDefault();
        if (!feeDetail) return;
        var name = $('#feeShopName').val();
        var year = $('#feeYear').val();
        var amount = $('#feeAmount').val();
        if (!name || !year || amount === '') { showToast('请填写缴费年度与实缴金额', 'danger'); return; }
        var c = feeDetail.codes || {};
        var values = {};
        values[c.venue] = name;
        values[c.item] = feeDetail.item_value;
        values[c.year] = year;
        values[c.amount] = amount;
        $.ajax({
            url: baseUrl + '/roster/shop_annual_fee/api/record',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ values: values }),
            success: function (resp) {
                showToast(resp.msg || '登记完成', resp.success ? 'success' : 'danger');
                if (resp.success) {
                    $('#feeAmount').val('');
                    openFeeModal(name);   // 历年记录与列表状态同步刷新
                    loadData(currentPage);
                }
            },
            error: function () { showToast('登记请求失败', 'danger'); }
        });
    }

    // ── 101 年度缴费：按年度批量登记 ─────────────────────────────────
    function openBatchModal() {
        $('#feeBatchYear').val(FEE_CTX.year);
        $('#feeBatchBody').html('<tr><td colspan="4" class="text-center text-muted py-3">加载中…</td></tr>');
        getModal('feeBatchModal').show();
        $.getJSON(baseUrl + '/api/list', { page: 1, limit: 100 }, function (resp) {
            if (!resp.success) { $('#feeBatchBody').html('<tr><td colspan="4" class="text-center text-muted py-3">读取失败</td></tr>'); return; }
            var rows = '';
            $.each(resp.data || [], function (i, item) {
                var badge = (item.fee && item.fee.status === '已缴')
                    ? '<span class="sc-badge sc-badge--success">已缴</span>'
                    : '<span class="sc-badge sc-badge--muted">未缴</span>';
                rows += '<tr>' +
                    '<td>' + (i + 1) + '</td>' +
                    '<td>' + esc(item.name) + '</td>' +
                    '<td>' + badge + '</td>' +
                    '<td><input type="number" class="sc-input fee-batch-amount" min="0" step="0.01" style="width:140px" ' +
                    'data-name="' + escAttr(item.name) + '" placeholder="留空跳过"></td></tr>';
            });
            $('#feeBatchBody').html(rows ||
                '<tr><td colspan="4" class="text-center text-muted py-3">暂无店铺记录</td></tr>');
        });
    }

    function submitFeeBatch(e) {
        e.preventDefault();
        var year = $('#feeBatchYear').val();
        if (!year) { showToast('请填写缴费年度', 'danger'); return; }
        var rows = [];
        $('.fee-batch-amount').each(function () {
            var amount = $(this).val();
            if (amount !== '' && amount != null) {
                rows.push({ name: $(this).data('name'), amount: amount });
            }
        });
        if (!rows.length) { showToast('请至少填写一行金额', 'danger'); return; }
        var btn = $(this).find('button[type="submit"]');
        btn.prop('disabled', true);
        $.ajax({
            url: baseUrl + '/annual-fee/batch-add',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ year: year, rows: rows }),
            success: function (resp) {
                showToast(resp.msg || '批量登记完成', resp.success ? 'success' : 'danger');
                if (resp.success) {
                    getModal('feeBatchModal').hide();
                    loadData(currentPage);
                }
            },
            error: function () { showToast('批量登记请求失败', 'danger'); }
        }).always(function () { btn.prop('disabled', false); });
    }

    // 详情底部编辑按钮走全局（onclick 内联需要）
    window.editRecord = editRecord;

    $(function () {
        loadData(1);
        $('#addButton').on('click', openAdd);
        $('#importButton').on('click', function () { getModal('importModal').show(); });
        $('#searchForm').on('submit', function (e) { e.preventDefault(); loadData(1); });
        $('#resetButton').on('click', function () { $('#searchForm')[0].reset(); loadData(1); });
        $('#tableBody').on('click', '[data-delete]', function () { deleteRecord($(this).data('delete')); });
        $('#tableBody').on('click', '[data-fee]', function () { openFeeModal($(this).data('fee')); });
        // 2026-11-17 Master–Detail：行点击 → 只读详情弹窗（缴费/删除按钮点击已在其委托内消化）
        $('#tableBody').on('click', 'tr[data-id]', function (e) {
            if ($(e.target).closest('button, a').length) return;
            openDetail($(this).data('id'));
        });
        if (FEE_READY) {
            $('#feeBatchButton').on('click', openBatchModal);
            $('#feeRegisterForm').on('submit', submitFeeRegister);
            $('#feeBatchForm').on('submit', submitFeeBatch);
        }
        $('#saveForm').on('submit', function (e) {
            e.preventDefault();
            $.post(baseUrl + '/api/save', $(this).serialize(), function (resp) {
                showToast(resp.msg || '保存完成', resp.success ? 'success' : 'danger');
                if (resp.success) { getModal('formModal').hide(); loadData(currentPage); }
            });
        });
        $('#importForm').on('submit', function (e) {
            e.preventDefault();
            var fd = new FormData(this);
            $.ajax({
                url: baseUrl + '/api/import',
                type: 'POST',
                data: fd,
                processData: false,
                contentType: false,
                success: function (resp) {
                    showToast(resp.msg || '导入完成', resp.success ? 'success' : 'danger');
                    if (resp.success) { getModal('importModal').hide(); loadData(1); }
                }
            });
        });
    });
})();


// 2026-11-19 换壳配套：导入弹窗投放区（app.js wireImportDropzone 全局单源）。
// #f_file 仍是真实 file input（name="file"），#importForm FormData 提交链路不变。
$(function () {
    wireImportDropzone({
        fileSel: '#f_file', dropSel: '#shopImpDrop', stagedSel: '#shopImpStaged',
        stagedNameSel: '#shopImpStagedName', stagedMetaSel: '#shopImpStagedMeta',
        submitBtnSel: '#shopImportSubmit', reselectSel: '#shopImpReselect', removeSel: '#shopImpRemove',
        modalSel: '#importModal'
    });
});
