(function () {
var currentPage = 1;
var pageSize = 20;
var totalPages = 0;
var currentMode = 'detail'; // 'detail' or 'household'
var listReqId = 0; // R-07：丢弃过期列表响应

$(function() {
    loadDictOptions();
    bindJumpPage();
    $('#btnSearch').on('click', function() { currentPage = 1; loadData(); });
    $('#btnReset').on('click', function() {
        $('#searchName').val(''); $('#searchIdCard').val('');
        $('#searchAgeMin').val(''); $('#searchAgeMax').val(''); $('#searchGender').val('');
        $('#searchVillageGroup').val(''); $('#searchPoliticsStatus').val(''); $('#searchHouseholdProp').val('');
        $('#searchStatus').val(''); $('#searchFlowStatus').val(''); $('#searchPhone').val(''); $('#searchAddress').val('');
        currentPage = 1; loadData();
    });

    // 高级筛选折叠
    $('#btnToggleAdv').on('click', function() {
        var adv = $('#advancedSearch');
        if (adv.is(':visible')) {
            adv.slideUp(200);
            $(this).html('<i class="bi bi-sliders"></i> 高级');
        } else {
            adv.slideDown(200);
            $(this).html('<i class="bi bi-sliders"></i> 收起高级');
        }
    });
    $('.sc-toolbar input, .sc-toolbar select').on('keydown', function(e) { if (e.key === 'Enter') $('#btnSearch').click(); });

    // 模式切换（062 P4：复用 sc-chip 激活态，不再写内联裸色值）
    $('.mode-btn').on('click', function() {
        $('.mode-btn').removeClass('is-active');
        $(this).addClass('is-active');
        currentMode = $(this).data('mode');
        currentPage = 1;
        loadData();
    });

    // 导出（按模式区分）
    $('#btnExport').on('click', function() {
        if (currentMode === 'household') {
            // 按户汇总导出：household API
            var qs = $.param(getSearchParams());
            window.location.href = '/resident/api/export?mode=household&' + qs;
        } else {
            var qs = $.param(getSearchParams());
            window.location.href = '/resident/api/export?' + qs;
        }
    });

    // 062 P4 验收 6：服务端注入 ?name= → 回填搜索框并首查一次；
    // 无 name 保持空态（不自动全表查询，同改造前口径）。
    var init = window.__RESIDENT_INIT__ || {};
    if (init.name) { $('#searchName').val(init.name); loadData(); }
});

function loadData() {
    if (currentMode === 'household') {
        loadHouseholdList();
    } else {
        loadResidentList();
    }
}

function getSearchParams() {
    return { name: $('#searchName').val(), id_card: $('#searchIdCard').val(), age_min: $('#searchAgeMin').val(), age_max: $('#searchAgeMax').val(), gender: $('#searchGender').val(), village_group: $('#searchVillageGroup').val(), politics_status: $('#searchPoliticsStatus').val(), household_property: $('#searchHouseholdProp').val(), status: $('#searchStatus').val(), flow_status: $('#searchFlowStatus').val(), phone: $('#searchPhone').val(), address: $('#searchAddress').val() };
}

function loadResidentList() {
    var params = getSearchParams();
    params.page = currentPage; params.limit = pageSize;
    var myId = ++listReqId;
    $('#detailHead').show(); $('#householdHead').hide();
    $('#residentTbody').html('<tr><td colspan="11" class="text-center py-4" style="color:var(--c-muted)"><div class="spinner-border text-primary me-2"></div> 加载中...</td></tr>');
    $.get('/resident/api/list', params, function(resp) {
        if (myId !== listReqId) return;
        if (!resp.success) { $('#residentTbody').html('<tr><td colspan="11" class="text-center sc-text-danger py-4">加载失败</td></tr>'); return; }
        var d = resp.data, items = d.data || [], total = d.total || 0;
        totalPages = d.pages || 0;
        $('#statsText').html('查询结果：<strong>' + total + '</strong> 人');
        var html = '';
        if (items.length === 0) {
            html = '<tr><td colspan="11" class="text-center text-muted py-4"><i class="bi bi-inbox" style="font-size:2rem;display:block;margin-bottom:8px;"></i>暂无数据</td></tr>';
        } else {
            var startIdx = (d.page - 1) * d.limit + 1;
            $.each(items, function(i, item) {
                var rowClass = (item.status === 'deceased' || item.status === 'moved_out') ? 'opacity-75' : '';
                var statusBadge = item.status === 'deceased' ? '<span class="sc-badge sc-badge--danger">死亡</span>' : item.status === 'moved_out' ? '<span class="sc-badge sc-badge--warning">迁出</span>' : '<span class="sc-badge sc-badge--success">正常</span>';
                var flowBadge = '<span class="sc-badge ' + (item.flow_status === '流出' ? 'sc-badge--warning' : item.flow_status === '流入' ? 'sc-badge--info' : 'sc-badge--muted') + '">' + esc(item.flow_status || '常住') + '</span>';
                html += '<tr class="' + rowClass + '" data-id="' + item.id + '">' +
                    '<td class="col-index">' + (startIdx + i) + '</td>' +
                    '<td class="col-name" title="' + escAttr(item.name) + '">' + esc(item.name) + '</td>' +
                    '<td class="col-id-card" title="' + escAttr(item.id_card) + '">' + esc(item.id_card) + '</td>' +
                    '<td class="col-nation">' + esc(item.nation) + '</td>' +
                    '<td class="col-gender">' + esc(item.gender) + '</td>' +
                    '<td class="col-age">' + item.age + '</td>' +
                    '<td class="col-group" title="' + escAttr(item.village_group) + '">' + esc(item.village_group) + '</td>' +
                    '<td class="col-politics" title="' + escAttr(item.politics_status) + '">' + esc(item.politics_status) + '</td>' +
                    '<td class="col-relation" title="' + escAttr(item.relationship) + '">' + esc(item.relationship) + '</td>' +
                    '<td class="col-status">' + statusBadge + '</td>' +
                    '<td class="col-flow">' + flowBadge + '</td></tr>';
            });
        }
        // 关键：先清除所有旧的行级 click 委托
        $('#residentTbody').off('click.household').off('click.detail');
        $('#residentTbody').html(html);
        // 居民明细行点击
        $('#residentTbody').on('click.detail', 'tr[data-id]', function() { openDetailModal($(this).data('id')); });
        renderPagination('#pagination', totalPages, d.page, function(p) { currentPage = p; loadData(); });
        $('#totalCount').text(total);
        $('#pageInfo').text(totalPages ? ' · 第 ' + d.page + '/' + totalPages + ' 页' : '');
    });
}

// ═══ 按户汇总（全新重写）═══
function loadHouseholdList() {
    var params = getSearchParams();
    params.page = currentPage; params.limit = pageSize;
    var myId = ++listReqId;
    $('#detailHead').hide(); $('#householdHead').show();
    $('#residentTbody').html('<tr><td colspan="10" class="text-center py-4" style="color:var(--c-muted)"><div class="spinner-border text-primary me-2"></div> 加载中...</td></tr>');
    $.get('/resident/api/household-summary', params, function(resp) {
        if (myId !== listReqId) return;
        if (!resp.success) { $('#residentTbody').html('<tr><td colspan="10" class="text-center sc-text-danger py-4">加载失败</td></tr>'); return; }
        var d = resp.data, items = d.data || [];
        totalPages = d.pages || 0;
        var totalHH = d.total_households || 0;
        var totalP = d.total_persons || 0;
        $('#statsText').html('查询结果：<strong>' + totalHH + '</strong> 户 <strong>' + totalP + '</strong> 人');

        // 构建表格 HTML
        var html = '';
        if (items.length === 0) {
            html = '<tr><td colspan="10" class="text-center text-muted py-4"><i class="bi bi-inbox" style="font-size:2rem;display:block;margin-bottom:8px;"></i>暂无数据</td></tr>';
        } else {
            var startIdx = (d.page - 1) * d.limit + 1;
            $.each(items, function(i, hh) {
                var rowCls = (hh.has_deceased || hh.has_moved) ? 'opacity-75' : '';
                // 每行内联 onclick，head_id 硬编码在 HTML 中，不依赖任何 JS 变量
                html += '<tr class="' + rowCls + '" onclick="openDetailModal(' + hh.head_id + ')">' +
                    '<td class="col-index">' + (startIdx + i) + '</td>' +
                    '<td class="col-name" title="' + escAttr(hh.head_name) + '"><a href="javascript:void(0)" style="color:var(--c-ink-2);text-decoration:none;font-weight:500" onclick="event.stopPropagation();openDetailModal(' + hh.head_id + ');return false;">' + esc(hh.head_name) + '</a></td>' +
                    '<td class="col-gender">' + esc(hh.head_gender || '') + '</td>' +
                    '<td class="col-age">' + (hh.head_age !== undefined && hh.head_age !== '' ? hh.head_age : '-') + '</td>' +
                    '<td class="col-group" title="' + escAttr(hh.village_group) + '">' + esc(hh.village_group) + '</td>' +
                    '<td class="col-address" title="' + escAttr(hh.address) + '">' + esc(hh.address) + '</td>' +
                    '<td class="col-id-card" title="' + escAttr(hh.head_id_card) + '">' + esc(hh.head_id_card) + '</td>' +
                    '<td class="col-household-count"><span class="sc-badge sc-badge--primary">' + hh.member_count + '</span></td>' +
                    '<td class="col-property" title="' + escAttr(hh.household_property || '') + '">' + esc(hh.household_property || '') + '</td>' +
                    '<td class="col-phone" title="' + escAttr(hh.head_phone) + '">' + esc(hh.head_phone) + '</td></tr>';
            });
        }

        // 直接设置 HTML，不附加任何 jQuery click 委托
        $('#residentTbody').html(html);
        renderPagination('#pagination', totalPages, d.page, function(p) { currentPage = p; loadData(); });
        $('#totalCount').text(totalHH);
        $('#pageInfo').text(totalPages ? ' · 第 ' + d.page + '/' + totalPages + ' 页' : '');
    });
}

// ponytail: 分页渲染已收敛到 app.js 统一 renderPagination；此处仅保留本页「跳转指定页」绑定
function bindJumpPage() {
    $('#btnJumpPage').off('click').on('click', function() {
        var p = parseInt($('#jumpPage').val());
        if (isNaN(p) || p < 1) { showToast('请输入有效页数', 'warning'); return; }
        if (p > totalPages) { showToast('最大页数为 ' + totalPages, 'warning'); return; }
        if (p === currentPage) return;
        currentPage = p;
        loadData();
    });
    $('#jumpPage').off('keydown').on('keydown', function(e) {
        if (e.key === 'Enter') $('#btnJumpPage').click();
    });
}

function loadDictOptions() {
    dictStore.ready(function() {
        $.each(dictStore.items('所在组'), function(i, item) { $('#searchVillageGroup').append('<option value="' + escAttr(item.value || item.label) + '">' + esc(item.label) + '</option>'); });
        $.each(dictStore.items('政治面貌'), function(i, item) { $('#searchPoliticsStatus').append('<option value="' + escAttr(item.value || item.label) + '">' + esc(item.label) + '</option>'); });
        $.each(dictStore.items('户属性'), function(i, item) { $('#searchHouseholdProp').append('<option value="' + escAttr(item.value || item.label) + '">' + esc(item.label) + '</option>'); });
    });
}

function openDetailModal(residentId) {
    $('#detailModal').modal('show');
    $('#detailModalBody').html('<div class="text-center py-5"><div class="spinner-border text-primary"></div><div class="mt-2" style="color:var(--c-muted)">加载中...</div></div>');
    $('#detailModal').data('resident-id', residentId);
    // 直接用拼接构造 URL，避免 .replace() 替换错误
    $.get('/resident/api/detail/' + residentId, function(resp) {
        if (!resp.success) { $('#detailModalBody').html('<div class="m-3 sc-text-danger">加载失败: ' + esc(resp.msg) + '</div>'); return; }
        renderDetailModal(resp.data.detail, resp.data.members);
    });
}

function openDetailByCode(code) {
    if (!code) { showToast('无户编号', 'warning'); return; }
    // Search for the first member of this household to open detail modal
    $.get('/resident/api/list', {household_code: code, limit: 1}, function(resp) {
        if (resp.success && resp.data && resp.data.data && resp.data.data.length > 0) {
            openDetailModal(resp.data.data[0].id);
        } else {
            showToast('未找到该户成员', 'warning');
        }
    });
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.openDetailModal = openDetailModal;
// 024-hotfix2：详情弹窗标记死亡/迁出/恢复/编辑后回刷列表（跨文件调用，需全局可见）
window.loadResidentList = loadResidentList;
})();
