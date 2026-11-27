/* youshu_list.js — 优抚对象台账（2026-11-16，民政对象第 6 类）。
 * 结构对齐 dibao_list.js（同一套 civil 页面壳）。
 * 2026-11-17 Master–Detail 全站统一：行点击 → d2 只读详情弹窗（detail_v2.js 渲染器，
 * 行数据来自最近一次列表响应，敏感字段已由接口解密），编辑/删除入口在详情底部。
 * 救助类别走字典分类「优抚对象救助类别」（字典管理可改选项）。
 */
(function () {
var cp = 1, ps = 20;
var rowMap = {};   // id → 最近一次列表响应的行数据（详情/编辑回填用）
var listReqId = 0;

function loadList(p) {
    cp = p; var pms = {page: p, limit: ps};
    var n = $('#searchName').val(), ic = $('#searchIdCard').val(),
        t = $('#searchType').val(), ss = $('#searchSupport').val(), dc = $('#searchDeceased').val();
    if (n) pms.name = n;
    if (ic) pms.id_card = ic;
    if (t) pms.care_category = t;
    if (ss) pms.support_status = ss;
    if (dc) pms.is_deceased = dc;
    var myId = ++listReqId;
    $('#youshuBody').html('<tr><td colspan="8" class="text-center py-4"><div class="spinner-border spinner-border-sm text-primary"></div> 加载中...</td></tr>');
    $.get('/youshu/api/list', pms, function (r) {
        if (myId !== listReqId) return;
        if (!r.success) return;
        var d = r.data, items = d.data || [];
        $('#pageStat').text('共 ' + d.total + ' 条 · 每页 ' + ps + ' 条');
        rowMap = {};
        var h = '';
        if (!items.length) {
            h = '<tr><td colspan="8" class="text-center text-muted py-4">暂无数据</td></tr>';
        } else {
            $.each(items, function (i, r) {
                rowMap[r.id] = r;
                var ssBadge = r.support_status === '复审中' ? 'sc-badge--warning'
                    : (r.support_status === '待核实' ? 'sc-badge--muted' : 'sc-badge--success');
                var deceased = r.is_deceased === '是'
                    ? '<span class="sc-badge sc-badge--danger">是</span>' : '<span class="sc-badge sc-badge--muted">否</span>';
                h += '<tr data-id="' + r.id + '" style="cursor:pointer">' +
                    '<td><strong>' + esc(r.name) + '</strong></td>' +
                    '<td>' + esc(r.id_card) + '</td>' +
                    '<td>' + esc(r.phone) + '</td>' +
                    '<td class="sc-table__td--center">' + esc(r.care_category) + '</td>' +
                    '<td class="sc-table__td--center">' + Number(r.amount || 0).toFixed(2) + '</td>' +
                    '<td class="sc-table__td--center">' + deceased + '</td>' +
                    '<td class="sc-table__td--center"><span class="sc-badge ' + ssBadge + '">' + esc(r.support_status || '已认定') + '</span></td>' +
                    '<td><span class="sc-row-actions">' +
                    '<button class="sc-btn sc-btn--text is-danger" onclick="event.stopPropagation();del(' + r.id + ')" title="删除"><i class="bi bi-trash"></i></button>' +
                    '</span></td></tr>';
            });
        }
        $('#youshuBody').html(h);
        $('#youshuBody tr[data-id]').on('click', function () { openDetail($(this).data('id')); });
        renderPagination('#pagination', d.pages, d.page, loadList);
    });
}

function openAdd() {
    $('#youshuForm')[0].reset();
    $('#yFormId').val('');
    $('#yFormTitle').text('新增优抚对象');
    $('#yFormMsg').text('');
    loadYoushuDictOptions(function () { $('#yCategory').val(''); });
    $('#youshuFormModal').modal('show');
}

// 2026-11-17 Master–Detail：行点击 → 只读详情弹窗（d2 渲染器，数据取 rowMap 行快照）
function openDetail(id) {
    var d = rowMap[id];
    if (!d) { showToast('数据已刷新，请重试', 'danger'); return; }
    var idCell = d.id_card
        ? '<span class="d2-mono d2-val--medium" id="d2s-id-main">' + esc(d.id_card) + '</span>' + d2CopyBtn(d.id_card, '身份证号')
        : '<span class="d2-mute">—</span>';
    var ssBadge = d.support_status === '复审中' ? '复审中'
        : (d.support_status === '待核实' ? '待核实' : '已认定');
    d2Render({
        modal: '#youshuDetailModal', body: '#youshuDetailBody', footerSel: '#youshuDetailModal',
        name: d.name || '优抚对象详情',
        tags: [d.care_category ? {t: d.care_category, tone: 'blue'} : null,
               d.is_deceased === '是' ? {t: '死亡', tone: 'red', ping: true} : null].filter(Boolean),
        subtitle: '<span>优抚对象详情</span><span class="d2-subtitle__sep">|</span><span>因伤病残涉核退役军人优待补贴</span>',
        kpis: [
            {label: '优抚金额', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d.amount || 0).toFixed(2)) + '</span>'},
            {label: '保障状态', html: '<span class="d2-chip d2-chip--slate">' + esc(d.support_status || '已认定') + '</span>'},
            {label: '联系电话', html: d2ValMedium(d.phone || '—')}
        ],
        risk: null,
        sections: [
            {title: '个人与救助信息', rows: [
                [{label: '姓名', html: d2ValStrong(d.name || '—')},
                 {label: '身份证号', html: idCell},
                 {label: '联系电话', html: d2ValMedium(d.phone || '—')},
                 {label: '救助类别', html: d2ValMedium(d.care_category || '—')}],
                [{label: '优抚金额（元）', html: '<span class="d2-mono d2-val--strong">' + esc(Number(d.amount || 0).toFixed(2)) + '</span>'},
                 {label: '是否死亡', html: d.is_deceased === '是' ? '<span class="d2-chip d2-chip--red">是</span>' : '<span class="d2-chip d2-chip--slate">否</span>'},
                 {label: '保障状态', html: '<span class="d2-chip d2-chip--slate">' + esc(ssBadge) + '</span>'},
                 {label: '备注', html: d2ValMedium(d.remark || '—')}]
            ]}
        ],
        sensitive: d.id_card ? [{id: 'd2s-id-main', raw: d.id_card, masked: d2MaskIdCard(d.id_card)}] : []
    });
    $('#youshuDetailBtns').html(
        '<button type="button" class="d2-btn d2-btn--primary" onclick="openEdit(' + d.id + ')"><i class="bi bi-pencil"></i> 编辑资料</button>'
        + '<button type="button" class="d2-btn d2-btn--danger" onclick="del(' + d.id + ')"><i class="bi bi-trash"></i> 删除</button>'
        + '<button type="button" class="d2-btn d2-btn--ghost" data-bs-dismiss="modal">关闭</button>');
    $('#youshuDetailModal').modal('show');
}

function openEdit(id) {
    var d = rowMap[id];
    if (!d) { showToast('数据已刷新，请重试', 'danger'); return; }
    $('#youshuForm')[0].reset();
    $('#yFormId').val(d.id);
    $('#yFormTitle').text('编辑优抚对象');
    $('#yFormMsg').text('');
    $('#yName').val(d.name || '');
    $('#yIdCard').val(d.id_card || '');
    $('#yPhone').val(d.phone || '');
    $('#yAmount').val(d.amount || 0);
    $('#yDeceased').val(d.is_deceased === '是' ? '是' : '否');
    $('#ySupport').val(d.support_status || '已认定');
    $('#yRemark').val(d.remark || '');
    loadYoushuDictOptions(function () { $('#yCategory').val(d.care_category || ''); });
    $('#youshuFormModal').modal('show');
}

$('#youshuForm').submit(function (e) {
    e.preventDefault();
    $.post('/youshu/api/save', $(this).serialize(), function (r) {
        if (r.success) {
            showToast('保存成功', 'success');
            $('#youshuFormModal').modal('hide');
            loadList(cp);
        } else { showToast(r.msg, 'danger'); }
    }).fail(function (xhr) {
        showToast((xhr.responseJSON && xhr.responseJSON.msg) || '保存失败', 'danger');
    });
});

function del(id) {
    if (!confirm('确定删除该优抚对象记录？')) return;
    $.post('/youshu/api/delete/' + id, function (r) {
        if (r.success) { showToast('删除成功', 'success'); loadList(cp); }
        else showToast(r.msg, 'danger');
    });
}

function exportData() {
    var pms = {};
    var n = $('#searchName').val(), ic = $('#searchIdCard').val(),
        t = $('#searchType').val(), ss = $('#searchSupport').val(), dc = $('#searchDeceased').val();
    if (n) pms.name = n;
    if (ic) pms.id_card = ic;
    if (t) pms.care_category = t;
    if (ss) pms.support_status = ss;
    if (dc) pms.is_deceased = dc;
    var qs = $.param(pms);
    window.location.href = '/youshu/api/export' + (qs ? '?' + qs : '');
}

/* 字典填充：救助类别（字典管理 → 优抚对象救助类别，可增删改项） */
function loadYoushuDictOptions(done) {
    dictSelect('优抚对象救助类别', 'select[name="care_category"]', '请选择', done);
}
function loadYoushuSearchDict() {
    dictSelect('优抚对象救助类别', '#searchType', '全部救助类别');
}

$(function () { loadYoushuSearchDict(); loadList(1); });

// ── IIFE 导出：SPA 每次进页在本作用域重放，仅内联事件/外部全局需要的符号可见 ──
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ del: del, exportData: exportData, loadList: loadList, openAdd: openAdd, openEdit: openEdit, openDetail: openDetail }); }
})();
