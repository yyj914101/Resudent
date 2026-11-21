(function () {
var currentRows = [];
var defaultVillageTitle = 'XX乡镇XX村（社区）';

$(function() {
    // 参数变化时更新表头
    $('#reportYear, #reportMonth').on('change keyup', function() {
        var y = $('#reportYear').val() || '____';
        var m = $('#reportMonth').val() || '__';
        $('#titleYear').text(y);
        $('#titleMonth').text(m);
    });

    loadHistoryOptions('death_report_unit_history', '#reportUnitHistory');

    $('#reportUnit').on('input', function() {
        $('#headerUnit').text($(this).val() || '');
    }).on('blur', function() {
        saveHistoryOption('death_report_unit_history', $(this).val(), '#reportUnitHistory');
    });

    // 乡镇/村历史记录
    loadVillageHistory();

    $('#villageName').on('input', function() {
        var val = $(this).val() || '';
        $('#titleVillage').text(val || defaultVillageTitle);
    }).on('blur', function() {
        saveVillageHistory($(this).val());
    });

    $('#reporter').on('input', function() {
        $('#headerReporter').text($(this).val() || '');
    });

    $('#responsiblePerson').on('input', function() {
        $('#headerResponsible').text($(this).val() || '');
    });

    $('#reportDate').on('change', function() {
        var d = $(this).val();
        if (d) {
            var parts = d.split('-');
            $('#headerDate').text(parts[0] + '年' + parseInt(parts[1]) + '月' + parseInt(parts[2]) + '日');
        }
    });

    // Enter键触发查询
    $('#reportYear, #reportMonth').keydown(function(e) {
        if (e.key === 'Enter') queryDeceased();
    });
});

// ─── 输入历史记录（localStorage） ───
// ─── 乡镇/村历史记录（localStorage） ───
function loadVillageHistory() {
    loadHistoryOptions('death_report_village_history', '#villageNameHistory');
}

function saveVillageHistory(val) {
    saveHistoryOption('death_report_village_history', val, '#villageNameHistory');
}

// ─── 查询已死亡居民 ───
function queryDeceased() {
    var year = $('#reportYear').val();
    var month = $('#reportMonth').val();

    $.get('/death-report/api/deceased', {year: year, month: month}, function(resp) {
        if (!resp.success) { showToast(resp.msg, 'danger'); return; }

        currentRows = resp.data || [];
        renderTable();
        $('#rowCount').text(currentRows.length);
        showToast('已加载 ' + currentRows.length + ' 条死亡记录', 'success');
    });
}

// ─── 渲染表格 ───
function renderTable() {
    var html = '';
    if (currentRows.length === 0) {
        html = '<tr id="noDataRow"><td colspan="10" class="text-center text-muted py-4"><i class="bi bi-inbox" style="font-size:1.5rem;"></i><br>暂无死亡记录</td></tr>';
    } else {
        $.each(currentRows, function(idx, row) {
            html += buildRowHtml(idx, row);
        });
    }
    $('#templateBody').html(html);
}

// 列索引：0编号 1姓名 2户籍地址 3所在组 4性别 5身份证 6死亡时间 7对象类型 8备注 9操作
function buildRowHtml(idx, row) {
    var actions = '';
    if (row.id && row.cascade_status === 'done') {
        // R-26：确认后 48h 内可撤回（超窗/无 confirmed_at 不展示撤回入口）
        var revokeBtn = '';
        var remain = revokeRemainText(row.confirmed_at);
        if (remain) {
            revokeBtn = '<button type="button" class="sc-btn sc-btn--text is-danger" title="确认后 48 小时内可撤回" ' +
                'onclick="revokeDeath(' + row.id + ')"><i class="bi bi-arrow-counterclockwise"></i> 撤回</button>';
        }
        actions = '<div class="sc-row-actions">' +
            '<button type="button" class="sc-btn sc-btn--text" title="防误发清单（可打印/导出）" ' +
            'onclick="openChecklist(' + row.id + ')"><i class="bi bi-list-check"></i> 清单</button>' +
            '<button type="button" class="sc-btn sc-btn--text" title="停发告知单（可打印/导出）" ' +
            'onclick="openNotice(' + row.id + ')"><i class="bi bi-file-earmark-text"></i> 告知单</button>' +
            revokeBtn +
            '</div>';
    } else if (row.id) {
        actions = '<span class="text-muted small">' + (row.cascade_status === 'failed' ? '级联失败' : '待确认') + '</span>';
    }
    return '<tr>' +
        '<td class="text-center">' + (idx + 1) + '</td>' +
        '<td contenteditable="true" class="editable" onblur="updateRow(' + idx + ')">' + esc(row.name) + '</td>' +
        '<td contenteditable="true" class="editable" onblur="updateRow(' + idx + ')">' + esc(row.address) + '</td>' +
        '<td contenteditable="true" class="editable" onblur="updateRow(' + idx + ')">' + esc(row.village_group) + '</td>' +
        '<td contenteditable="true" class="editable" onblur="updateRow(' + idx + ')">' + esc(row.gender) + '</td>' +
        '<td contenteditable="true" class="editable" onblur="updateRow(' + idx + ')">' + esc(row.id_card) + '</td>' +
        '<td contenteditable="true" class="editable" onblur="updateRow(' + idx + ')">' + esc(row.death_date) + '</td>' +
        '<td contenteditable="true" class="editable" onblur="updateRow(' + idx + ')">' + esc(row.person_type) + '</td>' +
        '<td contenteditable="true" class="editable" style="position:relative;" onblur="updateRow(' + idx + ')">' +
        esc(row.remark) +
        ' <span class="sc-text-danger" style="cursor:pointer;float:right;" onclick="deleteRow(' + idx + ')"><i class="bi bi-x-circle"></i></span>' +
        '</td>' +
        '<td class="text-center">' + actions + '</td></tr>';
}

// T15-3：清单/告知单入口（新窗口打开）
function openChecklist(rid) {
    window.open('/death-report/' + rid + '/checklist_view', '_blank');
}
function openNotice(rid) {
    window.open('/death-report/' + rid + '/notice-print', '_blank');
}

// R-26：确认后 48h 撤回（走 apiPost 表单编码，规避后端 dict.get(type=) 500）
function revokeRemainText(confirmedAt) {
    if (!confirmedAt) return '';
    var t = new Date(confirmedAt.replace(' ', 'T'));
    if (isNaN(t.getTime())) return '';
    var remainMs = t.getTime() + 48 * 3600 * 1000 - Date.now();
    if (remainMs <= 0) return '';
    var h = Math.floor(remainMs / 3600000);
    var m = Math.floor((remainMs % 3600000) / 60000);
    return '剩余可撤回 ' + h + ' 小时 ' + m + ' 分钟';
}

function revokeDeath(rid) {
    var row = null;
    for (var i = 0; i < currentRows.length; i++) { if (currentRows[i].id === rid) { row = currentRows[i]; break; } }
    if (!row) return;
    var remain = revokeRemainText(row.confirmed_at);
    var tip = '确定撤回该死亡登记吗？将回滚 6 表级联。';
    if (remain) tip += '\n' + remain + '。';
    if (!confirm(tip)) return;
    apiPost('/death-report/api/revoke', {id: rid}, function(resp) {
        showToast(resp.msg || '撤回成功', 'success');
        queryDeceased();
    });
}

// ─── 更新行数据 ───
function updateRow(idx) {
    var tr = $('#templateBody tr').eq(idx);
    if (!tr.length) return;
    var cells = tr.find('td');
    if (cells.length < 9) return;
    currentRows[idx].name = cells.eq(1).text().trim();
    currentRows[idx].address = cells.eq(2).text().trim();
    currentRows[idx].village_group = cells.eq(3).text().trim();
    currentRows[idx].gender = cells.eq(4).text().trim();
    currentRows[idx].id_card = cells.eq(5).text().trim();
    currentRows[idx].death_date = cells.eq(6).text().trim();
    currentRows[idx].person_type = cells.eq(7).text().trim();
    currentRows[idx].remark = cells.eq(8).text().trim();
}

// ─── 添加行 ───
function addRow() {
    var newRow = {
        seq: currentRows.length + 1,
        name: '',
        address: '',
        village_group: '',
        gender: '',
        id_card: '',
        death_date: '',
        person_type: '',
        remark: ''
    };
    currentRows.push(newRow);
    if ($('#noDataRow').length) {
        $('#templateBody').html('');
    }
    $('#templateBody').append(buildRowHtml(currentRows.length - 1, newRow));
    $('#rowCount').text(currentRows.length);
    var lastRow = $('#templateBody tr:last td:eq(1)');
    lastRow.focus();
}

// ─── 删除行 ───
function deleteRow(idx) {
    if (!confirm('确定要删除第 ' + (idx + 1) + ' 行吗？')) return;
    currentRows.splice(idx, 1);
    renderTable();
    $('#rowCount').text(currentRows.length);
}

// ─── 导出Excel ───
function exportExcel() {
    if (currentRows.length === 0) {
        showToast('没有数据可导出，请先查询', 'warning');
        return;
    }
    saveHistoryOption('death_report_unit_history', $('#reportUnit').val(), '#reportUnitHistory');
    saveVillageHistory($('#villageName').val());

    // 先同步所有编辑中的内容
    $('#templateBody tr').each(function(idx) {
        if (idx < currentRows.length) {
            updateRow(idx);
        }
    });

    var data = {
        year: $('#reportYear').val(),
        month: $('#reportMonth').val(),
        report_unit: $('#reportUnit').val(),
        village_name: $('#villageName').val() || defaultVillageTitle,
        reporter: $('#reporter').val(),
        responsible_person: $('#responsiblePerson').val(),
        report_date: $('#reportDate').val(),
        rows: currentRows
    };

    $.ajax({
        url: '/death-report/api/export',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(data),
        xhrFields: { responseType: 'blob' },
        success: function(blob) {
            var link = document.createElement('a');
            link.href = window.URL.createObjectURL(blob);
            var yearStr = $('#reportYear').val() || '____';
            var monthStr = $('#reportMonth').val() || '__';
            var nameForFile = ($('#villageName').val() || defaultVillageTitle).replace(/\s+/g, '_');
            link.download = nameForFile + '_' + yearStr + '年' + monthStr + '月份死亡名单.xlsx';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            showToast('导出成功', 'success');
        },
        error: function() {
            showToast('导出失败', 'danger');
        }
    });
}

// ─── 打印 ───
function printTable() {
    if (currentRows.length === 0) {
        showToast('没有数据可打印，请先查询', 'warning');
        return;
    }
    saveHistoryOption('death_report_unit_history', $('#reportUnit').val(), '#reportUnitHistory');
    saveVillageHistory($('#villageName').val());
    // 先同步编辑中的内容
    $('#templateBody tr').each(function(idx) {
        if (idx < currentRows.length) updateRow(idx);
    });

    // 构建纯打印内容（去掉页面多余元素）
    var year = $('#reportYear').val() || '____';
    var month = $('#reportMonth').val() || '__';
    var unit = $('#reportUnit').val() || '';
    var reporter = $('#reporter').val() || '';
    var responsible = $('#responsiblePerson').val() || '';
    var dateVal = $('#reportDate').val();
    var dateStr = '';
    if (dateVal) {
        var parts = dateVal.split('-');
        dateStr = parts[0] + '年' + parseInt(parts[1]) + '月' + parseInt(parts[2]) + '日';
    }

    // 构建数据行 HTML
    var rowHtml = '';
    $.each(currentRows, function(idx, row) {
        rowHtml += '<tr>' +
            '<td style="text-align:center;padding:4px 3px;border:1px solid #000;font-size:10pt;">' + (idx + 1) + '</td>' +
            '<td style="padding:4px 3px;border:1px solid #000;font-size:10pt;">' + esc(row.name) + '</td>' +
            '<td style="padding:4px 3px;border:1px solid #000;font-size:10pt;">' + esc(row.address) + '</td>' +
            '<td style="padding:4px 3px;border:1px solid #000;font-size:10pt;">' + esc(row.village_group) + '</td>' +
            '<td style="padding:4px 3px;border:1px solid #000;font-size:10pt;text-align:center;">' + esc(row.gender) + '</td>' +
            '<td style="padding:4px 3px;border:1px solid #000;font-size:10pt;">' + esc(row.id_card) + '</td>' +
            '<td style="padding:4px 3px;border:1px solid #000;font-size:10pt;text-align:center;">' + esc(row.death_date) + '</td>' +
            '<td style="padding:4px 3px;border:1px solid #000;font-size:10pt;text-align:center;">' + esc(row.person_type) + '</td>' +
            '<td style="padding:4px 3px;border:1px solid #000;font-size:10pt;">' + esc(row.remark) + '</td></tr>';
    });

    var villageTitle = $('#villageName').val() || defaultVillageTitle;
    var printHtml = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>死亡名单打印</title></head><body>\
' +
        '<div class="print-title">' + esc(villageTitle) + ' ' + esc(year) + '年' + esc(month) + '月份死亡名单</div>\
' +
        '<table class="print-header" style="width:100%;border:none;"><tr>\
            <td style="border:none;width:40%;font-size:10pt;">填报单位：' + esc(unit) + '（盖章）</td>\
            <td style="border:none;width:20%;font-size:10pt;text-align:center;">填报人：' + esc(reporter) + '</td>\
            <td style="border:none;width:20%;font-size:10pt;text-align:center;">负责人：' + esc(responsible) + '</td>\
            <td style="border:none;width:20%;font-size:10pt;text-align:right;">填报日期：' + dateStr + '</td>\
        </tr></table>\
' +
        '<table>\
' +
        '<thead><tr>\
' +
            '<th style="width:5%;">编号</th>\
' +
            '<th style="width:10%;">姓名</th>\
' +
            '<th style="width:18%;">户籍地址</th>\
' +
            '<th style="width:8%;">所在组</th>\
' +
            '<th style="width:6%;">性别</th>\
' +
            '<th style="width:18%;">身份证号码</th>\
' +
            '<th style="width:12%;">死亡时间</th>\
' +
            '<th style="width:10%;">对象类型</th>\
' +
            '<th style="width:13%;">备注</th>\
' +
        '</tr></thead><tbody>' + rowHtml + '</tbody></table>\
' +
    '</body></html>';

    var w = window.open('', '_blank');
    w.document.write(printHtml);
    w.document.close();
    w.focus();
    setTimeout(function() { w.print(); }, 500);
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.addRow = addRow;
window.deleteRow = deleteRow;
window.exportExcel = exportExcel;
window.openChecklist = openChecklist;
window.openNotice = openNotice;
window.printTable = printTable;
window.queryDeceased = queryDeceased;
window.revokeDeath = revokeDeath;
window.updateRow = updateRow;
})();
