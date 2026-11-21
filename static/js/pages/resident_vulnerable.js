(function () {
var vPage = 1;
var V_LIMIT = 20;

function vulnLoad() {
    const category = $('#vCategory').val();
    const name = $('#vName').val().trim();
    $.getJSON('/resident/api/vulnerable/list', {category, name, page: vPage, limit: V_LIMIT})
        .done(function (res) {
            if (!res.success) { alert(res.msg); return; }
            const d = res.data;
            // 统计
            $('#stDibao').text(d.stats.dibao.count);
            $('#stDisabled').text(d.stats.disabled.count);
            $('#stTekun').text(d.stats.tekun.count);
            $('#stSpecial').text(d.stats.special.count);
            $('#stTotal').text(d.stats.total.count);
            // 明细
            const catName = {dibao:'低保', disabled:'残疾', tekun:'特困', special:'其他困难'}[d.category] || d.category;
            let html = '';
            (d.data || []).forEach(function (r) {
                html += '<tr>' +
                    '<td>' + esc(r.name) + '</td>' +
                    '<td>' + esc(catName) + '</td>' +
                    '<td>' + esc(r.id_card) + '</td>' +
                    '<td>' + esc(r.gender) + '</td>' +
                    '<td>' + (r.age !== '' && r.age !== null ? r.age : '-') + '</td>' +
                    '<td>' + esc(r.grid) + '</td>' +
                    '<td>' + esc(r.detail) + '</td>' +
                    '<td>' + (r.resident_id ? '#' + r.resident_id : '<span class="text-muted">未关联</span>') + '</td>' +
                    '</tr>';
            });
            $('#vb').html(html || '<tr><td colspan="8" class="text-center text-muted py-4">暂无数据 —— 请先导入镇级名单，或到对应模块新增低保/残疾/特困记录</td></tr>');
            $('#pageStat').text('共 ' + d.total + ' 条 · 每页 ' + V_LIMIT + ' 条');
            // 分页（R-09：原全量渲染所有页码 → 全局 renderPagination）
            renderPagination('#vpg', d.pages, d.page, function (page) { vPage = page; vulnLoad(); });
        });
}

$(function () {
    vulnLoad();
    $('#btnVSearch').on('click', function () { vPage = 1; vulnLoad(); });
    $('#vCategory').on('change', function () { vPage = 1; vulnLoad(); });
    $('#vName').on('keydown', function (e) { if (e.key === 'Enter') { vPage = 1; vulnLoad(); } });
});
})();
