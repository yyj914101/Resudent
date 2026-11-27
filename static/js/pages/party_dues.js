(function () {
var currentPage = 1, pageSize = 20, totalPages = 0;
var MONTHS = [1,2,3,4,5,6,7,8,9,10,11,12];
var highlightId = 0;

function calcDues(income) {
    if (!income || income <= 0) return 0;
    income = parseFloat(income);
    if (income <= 3000) return Math.round(income * 0.005 * 100) / 100;
    if (income <= 5000) return Math.round(income * 0.01 * 100) / 100;
    if (income <= 10000) return Math.round(income * 0.015 * 100) / 100;
    return Math.min(Math.round(income * 0.02 * 100) / 100, 1000);
}

$(function() {
    var up = new URLSearchParams(window.location.search);
    highlightId = parseInt(up.get('highlight')) || 0;

    $('#btnSearch').on('click', function() { currentPage = 1; loadDuesList(); });
    $('#btnReset').on('click', function() {
        $('#searchName').val(''); $('#searchIdCard').val(''); $('#searchYear').val(new Date().getFullYear()); $('#searchStatus').val('');
        currentPage = 1; loadDuesList();
    });
    $('#btnExport').on('click', function() {
        var qs = $.param({ name: $('#searchName').val(), id_card: $('#searchIdCard').val(), year: $('#searchYear').val(), status: $('#searchStatus').val() });
        window.location.href = '/party/api/dues/export?' + qs;
    });

    // 062 P5：18 列宽表列显隐（12 月台账列固定，仅党支部/平均月收入/年欠缴可隐藏）
    scColToggle.init('#duesTable', {
        menuHost: '#duesColHost',
        storageKey: 'dues_col_hidden',
        fixed: [0, 1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17]
    });

    // 实时计算
    $('#editIncome').on('input', function() {
        var income = parseFloat($(this).val()) || 0;
        var calc = calcDues(income);
        $('#editCalculated').text(calc.toFixed(2) + ' 元');
        updateArrears();
    });
    $('#editPaid').on('input', function() { updateArrears(); });

    function updateArrears() {
        var calc = parseFloat($('#editCalculated').text()) || 0;
        var paid = parseFloat($('#editPaid').val()) || 0;
        var arrears = Math.max(0, Math.round((calc - paid) * 100) / 100);
        $('#editArrears').text(arrears.toFixed(2) + ' 元');
        if (arrears > 0) {
            $('#editArrears').css({'background':'var(--c-warning-bg)','color':'var(--c-warning)'});
        } else {
            $('#editArrears').css({'background':'var(--c-cat-1-weak)','color':'var(--c-success)'});
        }
    }

    // 保存
    $('#btnSaveDues').on('click', function() {
        var memberId = $('#editMemberId').val();
        var month = parseInt($('#editMonth').val());
        var income = parseFloat($('#editIncome').val()) || 0;
        var paid = parseFloat($('#editPaid').val()) || 0;
        if (!memberId || !month) { showToast('数据异常', 'danger'); return; }

        $.post('/party/api/dues/save', {
            member_id: memberId,
            fee_year: $('#searchYear').val(),
            fee_month: month,
            monthly_income: income,
            paid_amount: paid
        }, function(r) {
            if (r.success) {
                showToast('保存成功', 'success');
                $('#editModal').modal('hide');
                loadDuesList();
            } else {
                showToast(r.msg, 'danger');
            }
        });
    });

    // 054-A#7：批量设置收入标准提交
    $('#btnBatchIncomeSave').on('click', function() {
        var memberId = $('#batchMemberId').val();
        var income = parseFloat($('#batchIncome').val());
        var startMonth = parseInt($('#batchStartMonth').val());
        if (!memberId || isNaN(income) || income < 0) { showToast('请填写有效的月收入', 'danger'); return; }
        $.post('/party/api/dues/income-standard', {
            member_id: memberId,
            fee_year: $('#searchYear').val(),
            start_month: startMonth,
            monthly_income: income
        }, function(r) {
            if (r.success) {
                showToast(r.msg || '批量设置成功', 'success');
                $('#batchIncomeModal').modal('hide');
                loadDuesList();
            } else {
                showToast(r.msg || '设置失败', 'danger');
            }
        });
    });

    $('#searchName, #searchIdCard, #searchYear').on('keydown', function(e) {
        if (e.key === 'Enter') $('#btnSearch').click();
    });
    loadDuesList();
});

function loadDuesList() {
    var params = {
        page: currentPage, limit: pageSize,
        name: $('#searchName').val(),
        id_card: $('#searchIdCard').val(),
        year: $('#searchYear').val(),
        status: $('#searchStatus').val()
    };
    $('#duesTbody').html('<tr><td colspan="18" class="text-center py-4" style="color:var(--c-muted)"><div class="spinner-border spinner-border-sm text-primary me-2"></div>加载中...</td></tr>');
    $.get('/party/api/dues/list', params, function(resp) {
        if (!resp.success) { $('#duesTbody').html('<tr><td colspan="18" class="text-center sc-text-danger py-4">加载失败</td></tr>'); return; }
        var d = resp.data, items = d.data || [], total = d.total || 0;
        $('#titleYear').text(d.year || $('#searchYear').val());
        totalPages = d.pages || 0;
        var html = '';
        if (items.length === 0) {
            html = '<tr><td colspan="18" class="text-center text-muted py-4"><i class="bi bi-inbox" style="font-size:2rem;display:block;margin-bottom:8px;"></i>暂无数据</td></tr>';
        } else {
            $.each(items, function(i, row) {
                var nameHtml = '<a href="javascript:void(0)" class="name-link" data-id="' + row.id + '" style="color:var(--c-ink-2);text-decoration:none;font-weight:500;">' + esc(row.name) + '</a>';
                // 054-A#8：收入分段（连续同额月合并）作 tooltip
                var segTitle = (row.income_segments || []).map(function(s) {
                    return (s.from_month === s.to_month ? s.from_month + '月' : s.from_month + '-' + s.to_month + '月') + ': ' + s.income + '元';
                }).join(' / ');
                html += '<tr>' +
                    '<td class="text-center">' + ((d.page - 1) * d.limit + i + 1) + '</td>' +
                    '<td>' + nameHtml + '</td>' +
                    '<td>' + esc(row.branch || '') + '</td>' +
                    '<td class="text-end" title="' + esc(segTitle) + '" style="cursor:help;">' + (row.monthly_income > 0 ? row.monthly_income.toFixed(2) : '<span class="text-muted">-</span>') + '</td>';
                // 12 months
                $.each(row.months, function(mi, m) {
                    var cls = 'sc-month-cell month-cell';
                    if (m.status === 'paid') cls += ' sc-month-cell--paid';
                    else if (m.status === 'unpaid') cls += ' sc-month-cell--unpaid';
                    else cls += ' sc-month-cell--none';
                    var tooltip = m.calc.toFixed(2) + '元';
                    html += '<td class="' + cls + '" data-member-id="' + row.id + '" data-month="' + (mi+1) + '" data-name="' + escAttr(row.name) + '" data-income="' + (m.monthly_income || 0) + '" data-calc="' + m.calc + '" data-paid="' + m.paid + '" title="' + tooltip + '">';
                    if (m.status === 'paid') html += '✓';
                    else if (m.status === 'unpaid') html += '<span style="color:var(--c-warning);">✗</span>';
                    else html += '-';
                    html += '</td>';
                });
                // Year arrears + 批量设置操作
                html += '<td class="text-center" style="color:var(--c-' + (row.year_arrears > 0 ? 'danger' : 'success') + ');font-weight:' + (row.year_arrears > 0 ? '600' : '400') + ';">' + row.year_arrears.toFixed(2) + '</td>';
                html += '<td class="text-center"><button type="button" class="sc-btn sc-btn--text btn-batch" data-id="' + row.id + '" data-name="' + escAttr(row.name) + '">批量设置</button></td></tr>';
            });
        }
        $('#duesTbody').html(html);

        // Name click -> party detail
        $('.name-link').on('click', function() {
            window.location.href = '/party/?highlight=' + $(this).data('id');
        });

        // 054-A#7：批量设置收入标准
        $('.btn-batch').on('click', function() {
            $('#batchMemberId').val($(this).data('id'));
            $('#batchMemberName').val($(this).data('name'));
            $('#batchStartMonth').val('1').trigger('refresh.cs');   // 程序化赋值须同步触发框（054 复核 P1-2）
            $('#batchIncome').val('');
            $('#batchIncomeModal').modal('show');
        });

        // Month cell click -> edit modal
        $('.month-cell').on('click', function() {
            var memberId = $(this).data('member-id');
            var month = $(this).data('month');
            var name = $(this).data('name');
            var income = parseFloat($(this).data('income')) || 0;
            var calcVal = parseFloat($(this).data('calc')) || 0;
            var paidVal = parseFloat($(this).data('paid')) || 0;

            $('#editMemberId').val(memberId);
            $('#editMonth').val(month);
            $('#editName').val(name);
            $('#editMonthLabel').text(month + '月');
            $('#editIncome').val(income > 0 ? income : '');
            $('#editPaid').val(paidVal > 0 ? paidVal : '');
            $('#editCalculated').text(calcVal.toFixed(2) + ' 元');
            updateArrears();
            $('#editModalTitle').text('编辑党费 - ' + name + ' ' + month + '月');
            $('#editModal').modal('show');
        });

        // Stats
        if (d.stats) {
            $('#statAnnualCalc').text(d.stats.annual_calculated.toFixed(2));
            $('#statAnnualPaid').text(d.stats.annual_paid.toFixed(2));
            $('#statAnnualArrears').text(d.stats.annual_arrears.toFixed(2));
        }
        $('#footerInfo').text('共 ' + total + ' 名党员');
        renderPagination('#pagination', totalPages, d.page, function(p) { currentPage = p; loadDuesList(); });
        // Scroll to highlight
        if (highlightId && items.length) {
            var idx = -1;
            $.each(items, function(i, r) { if (r.id === highlightId) { idx = i; return false; } });
            if (idx >= 0) {
                var tr = $('#duesTbody tr').eq(idx);
                if (tr.length) {
                    tr.css('background', 'var(--c-warning-bg)');
                    tr[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
                }
            }
        }
    });
}

function updateArrears() {
    var calc = parseFloat($('#editCalculated').text()) || 0;
    var paid = parseFloat($('#editPaid').val()) || 0;
    var arrears = Math.max(0, Math.round((calc - paid) * 100) / 100);
    $('#editArrears').text(arrears.toFixed(2) + ' 元');
    if (arrears > 0) {
        $('#editArrears').css({'background':'var(--c-warning-bg)','color':'var(--c-warning)'});
    } else {
        $('#editArrears').css({'background':'var(--c-cat-1-weak)','color':'var(--c-success)'});
    }
}
})();
