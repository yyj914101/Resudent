(function () {
var FIELD_OPTIONS = [
    ['resident.name', '居民·姓名', ['eq', 'contains']],
    ['resident.id_card', '居民·身份证', ['eq']],
    ['resident.gender', '居民·性别', ['eq']],
    ['resident.age', '居民·年龄', ['gt', 'lt', 'between']],
    ['resident.status', '居民·状态', ['eq']],
    ['resident.grid', '居民·所在组', ['eq', 'contains']],
    ['resident.tag', '居民·标签', ['in']],
    ['dibao.status', '低保·状态', ['eq']],
    ['dibao.type', '低保·类型', ['eq']],
    ['disabled.status', '残疾·状态', ['eq']],
    ['care_target.type', '一老一小·类别', ['eq']],
    ['care_target.child_type', '一老一小·儿童类型', ['eq']],
    ['special_support.mode', '特困·供养方式', ['eq']],
    ['subsidy.project', '补贴·项目ID', ['eq', 'in']],
    ['subsidy.period', '补贴·发放期', ['eq']],
    ['subsidy.amount', '补贴·金额', ['gt', 'lt', 'between']],
    ['visit.date', '走访·日期', ['gt', 'lt', 'between']],
    ['visit.type', '走访·方式', ['eq']],
    ['visit.status', '走访·状态', ['eq']],
];
var OP_LABELS = {eq: '等于', contains: '包含', gt: '大于', lt: '小于', between: '介于', in: '属于'};

function addConditionRow(container, logic, field, op, value) {
    var row = $('<div class="cond-row d-flex align-items-center gap-2 mb-2"></div>');
    if (container.children().length > 0 || container.attr('id') === 'condRoot') {
        // 非首行前加逻辑连接符
        if (container.attr('id') !== 'condRoot' || container.children().length > 0) {
            var lv = logic || 'AND';
            row.append('<select class="sc-select logic-sel" style="width:80px;">' +
                '<option value="AND"' + (lv === 'AND' ? ' selected' : '') + '>并且</option>' +
                '<option value="OR"' + (lv === 'OR' ? ' selected' : '') + '>或者</option></select>');
        }
    }
    row.append('<select class="sc-select field-sel" style="width:190px;"></select>');
    row.append('<select class="sc-select op-sel" style="width:90px;"></select>');
    row.append('<input type="text" class="sc-input val-input" style="width:180px;" placeholder="值（between 用 从,到）">');
    row.append('<button type="button" class="sc-btn sc-btn--text is-danger" aria-label="删除条件" title="删除条件" onclick="$(this).closest(\'.cond-row\').remove()"><i class="bi bi-x"></i></button>');
    // 填充字段选项
    var $fs = row.find('.field-sel');
    FIELD_OPTIONS.forEach(function (f) {
        $fs.append('<option value="' + f[0] + '">' + f[1] + '</option>');
    });
    if (field) $fs.val(field);
    $fs.trigger('change');
    if (op) row.find('.op-sel').val(op);
    if (value) row.find('.val-input').val(value);
    container.append(row);
}

function refreshOps() {
    var $fs = $(this);
    var field = $fs.val();
    var ops = [];
    FIELD_OPTIONS.forEach(function (f) { if (f[0] === field) ops = f[2]; });
    var $op = $fs.closest('.cond-row').find('.op-sel');
    $op.empty();
    ops.forEach(function (o) { $op.append('<option value="' + o + '">' + (OP_LABELS[o] || o) + '</option>'); });
}

$(function () {
    $('#condRoot').on('change', '.field-sel', refreshOps);
    // 初始一行：居民姓名 包含（示例友好）
    addConditionRow($('#condRoot'), 'AND', 'resident.name', 'contains', '');
});

function buildTree($container) {
    var conds = [];
    $container.children('.cond-row').each(function () {
        var $r = $(this);
        var field = $r.find('.field-sel').val();
        var op = $r.find('.op-sel').val();
        var raw = $r.find('.val-input').val().trim();
        if (!field || !raw) return;
        var value = raw;
        if (op === 'between') {
            var parts = raw.split(',');
            value = {from: parts[0] ? parts[0].trim() : null, to: parts[1] ? parts[1].trim() : null};
        } else if (op === 'in') {
            value = raw.split(',').map(function (s) { return s.trim(); });
        }
        conds.push({field: field, op: op, value: value});
    });
    return conds.length ? {logic: 'AND', conditions: conds} : null;
}

function doQuery() {
    var tree = buildTree($('#condRoot'));
    if (!tree) { $('#queryMsg').text('请至少填写一个完整条件'); return; }
    $('#queryMsg').text('');
    $.post('/report/api/custom-query', JSON.stringify(tree), function (res) {
        if (!res.success) { $('#queryMsg').text(res.msg); return; }
        var rows = res.data || [];
        $('#resultCount').text(res.total + ' 行');
        if (!rows.length) {
            $('#resultBody').html('<tr><td colspan="6" class="text-center text-muted py-4">无匹配结果</td></tr>');
            return;
        }
        var html = '';
        rows.forEach(function (r) {
            html += '<tr><td>' + esc(r.name || '') + '</td><td>' + esc(r.id_card || '') + '</td>' +
                '<td>' + esc(r.gender || '') + '</td><td>' + esc(r.age || '') + '</td>' +
                '<td>' + esc(r.status || '') + '</td><td>' + esc(r.grid || '') + '</td></tr>';
        });
        $('#resultBody').html(html);
    });
}

function doExport() {
    var tree = buildTree($('#condRoot'));
    if (!tree) { $('#queryMsg').text('请至少填写一个完整条件'); return; }
    $('#queryMsg').text('');
    $.post('/report/api/custom-query/export', JSON.stringify(tree), function (res) {
        if (!res.success) { $('#queryMsg').text(res.msg); return; }
        if (res.data && res.data.download_url) window.location.href = res.data.download_url;
    });
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.addConditionRow = addConditionRow;
window.doExport = doExport;
window.doQuery = doQuery;
})();
