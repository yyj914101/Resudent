(function () {
function loadPlans() {
    $.getJSON('/visit/api/list?limit=1000', function(resp) {
        if (!resp.success) return;
        let html = '<option value="">全部计划</option>';
        resp.data.forEach(function(p) {
            html += '<option value="' + p.id + '">' + esc(p.plan_name) + '</option>';
        });
        $('#planSelect').html(html);
        let pre = getUrlParam('plan_id');
        if (pre) { $('#planSelect').val(pre); }
        list.reload(1);
    });
}

var list = crudTable({
    endpoint: '/visit/api/history',
    emptyText: '暂无走访记录',
    // 067 分页统计（sc-pagination__stat）+ 原有统计卡回填
    onLoaded: function(resp) {
        fillStats(resp.stats);
        $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条');
    },
    columns: [
        {field: '__index'},
        {field: 'visit_date', esc: true},
        {field: 'resident_name', esc: true},
        {render: function(item) { return esc(item.plan_name || '—'); }},
        {field: 'visit_type', esc: true},
        {render: function(item) { return truncate(item.content, 30); }},
        {render: function(item) {
            // 067 §2.4：has_need 为真实字段
            return item.has_need ? '<span class="sc-badge sc-badge--warning">有需求</span>' : '<span class="text-muted">无</span>';
        }},
        {render: function(item) { return photoThumbs(item.photos); }},
        {render: function(item) { return esc(item.visitor_name || '—'); }},
        {render: function(item) {
            return '<span class="sc-row-actions"><button type="button" class="sc-btn sc-btn--text" onclick="showTimeline(' + item.resident_id + ')">时间线</button></span>';
        }}
    ]
});

function fillStats(s) {
    if (!s) return;
    $('#statTotal').text(s.total);
    $('#statVisited').text(s.visited);
    $('#statCoverage').text(s.coverage_rate + '%');
    $('#statCompletion').text(s.completion_rate + '%');
    $('#statScope').text(s.scope === 'plan' ? '按计划' : '全局');
    $('#statCoverage').parent().find('.label').toggleClass('text-danger', s.coverage_rate < 90);
    $('#statCompletion').parent().find('.label').toggleClass('text-danger', s.completion_rate < 90);
}

function photoThumbs(photos) {
    if (!photos || !photos.length) return '<span class="text-muted">—</span>';
    let html = '';
    photos.forEach(function(p) {
        html += '<a href="' + p.url + '" target="_blank" rel="noopener"><img src="' + p.url + '" style="width:40px;height:40px;object-fit:cover;border-radius:4px" class="me-1" alt="照片"></a>';
    });
    return html;
}

function showTimeline(rid) {
    let name = '';
    $.getJSON('/visit/api/history?resident_id=' + rid + '&limit=1000', function(resp) {
        if (!resp.success || !resp.data.length) { showToast('无走访记录', 'warning'); return; }
        name = resp.data[0].resident_name;
        $('#timelineTitle').text('走访历史：' + name);
        let html = '';
        let curDate = null;
        resp.data.forEach(function(r) {
            if (r.visit_date !== curDate) {
                curDate = r.visit_date;
                html += '<div class="border-start border-2 border-primary ps-3 mb-3">' +
                        '<h6 class="text-primary">' + esc(curDate) + '</h6>';
            }
            html += '<div class="mb-2">' +
                '<span class="badge bg-secondary">' + esc(r.visit_type) + '</span> ' +
                '<span class="text-muted small">走访人：' + esc(r.visitor_name || '—') + '</span>' +
                '<div class="mt-1">' + esc(r.content || '') + '</div>' +
                (r.has_need ? '<div class="text-warning">需求：' + esc(r.need_desc || '') + '</div>' : '') +
                (r.photos.length ? '<div class="mt-1">' + r.photos.map(function(p){
                    return '<a href="' + p.url + '" target="_blank" rel="noopener"><img src="' + p.url + '" style="width:90px;height:90px;object-fit:cover;border-radius:4px" class="me-1" alt="照片"></a>';
                }).join('') + '</div>' : '') +
                '</div>';
        });
        $('#timelineBody').html(html + '</div>');
        $('#timelineModal').modal('show');
    });
}

function truncate(s, n) { s = s || ''; return s.length > n ? esc(s.slice(0, n)) + '…' : esc(s); }
function getUrlParam(name) {
    let m = new RegExp('[?&]' + name + '=([^&]*)').exec(location.search);
    return m ? decodeURIComponent(m[1]) : '';
}

$('#searchForm').submit(function(e) { e.preventDefault(); list.reload(1); });
$('#planSelect').change(function() { list.reload(1); });

$(function() { loadPlans(); });

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.showTimeline = showTimeline;
})();
