(function () {
'use strict';
// 090B 选举管理：届次列表页（一页纸 §8 届次列表；状态机推进按钮 §6）
var ROUND_STATUS_BADGE = {
    '筹备': 'sc-badge sc-badge--muted',
    '选民登记': 'sc-badge sc-badge--primary',
    '投票': 'sc-badge sc-badge--warning',
    '结果公示': 'sc-badge sc-badge--info',
    '已完成': 'sc-badge sc-badge--success'
};
// §6 动作名 → 下一状态（voters 页同步使用同一映射）
var ROUND_NEXT = {
    '筹备': {'label': '进入登记', 'to': '选民登记'},
    '选民登记': {'label': '结束登记', 'to': '投票'},
    '投票': {'label': '完成投票', 'to': '结果公示'},
    '结果公示': {'label': '归档', 'to': '已完成'}
};

function roundBadge(status) {
    return '<span class="' + (ROUND_STATUS_BADGE[status] || 'sc-badge') + '">' + esc(status) + '</span>';
}

function loadRounds() {
    $.get('/election/api/rounds', { status: $('#sStatus').val() || '' }, function (r) {
        if (!r.success) { showToast(r.msg || '加载失败', 'danger'); return; }
        var rows = r.data.data || [];
        if (!rows.length) {
            $('#tb').html('<tr><td class="sc-table__td sc-table__td--center" colspan="7">暂无选举届次</td></tr>');
            return;
        }
        $('#tb').html(rows.map(function (d) {
            var next = ROUND_NEXT[d.status];
            return '<tr>'
                + '<td class="sc-table__td">' + esc(d.round_name) + '</td>'
                + '<td class="sc-table__td">' + esc(d.start_date) + (d.end_date ? ' ~ ' + esc(d.end_date) : '') + '</td>'
                + '<td class="sc-table__td">' + esc(d.register_start) + ' ~ ' + esc(d.register_end) + '</td>'
                + '<td class="sc-table__td">' + esc(d.vote_date) + '</td>'
                + '<td class="sc-table__td">' + roundBadge(d.status) + '</td>'
                + '<td class="sc-table__td sc-table__td--center">' + d.registered_count + ' / ' + d.voters_count + '</td>'
                + '<td class="sc-table__td">'
                + '<a class="sc-btn sc-btn--text" href="/election/rounds/' + d.id + '/voters">进入名册</a> '
                + (next ? '<button type="button" class="sc-btn sc-btn--text" onclick="transitionRound(' + d.id + ',\'' + next.to + '\')">' + next.label + '</button> ' : '')
                + '<button type="button" class="sc-btn sc-btn--text" onclick="openRoundModal(' + d.id + ')">编辑</button>'
                + '</td></tr>';
        }).join(''));
    });
}

function transitionRound(id, to) {
    if (!confirm('确定执行状态推进到「' + to + '」？非法迁移将被拒绝且不落库。')) return;
    apiPost('/election/api/rounds/' + id + '/transition', { to_status: to }, function () {
        showToast('状态已更新为' + to, 'success');
        loadRounds();
    });
}

function openRoundModal(id) {
    $('#rmForm')[0].reset();
    if (id) {
        // 编辑：从当前列表数据回填（rounds 列表已全量在内存，按 id 查找）
        $.get('/election/api/rounds', {}, function (r) {
            var d = (r.data.data || []).filter(function (x) { return x.id === id; })[0];
            if (!d) { showToast('届次不存在', 'danger'); return; }
            $('#rmTitle').text('编辑选举届次');
            $('#rmId').val(d.id);
            var f = $('#rmForm')[0];
            f.round_name.value = d.round_name;
            f.start_date.value = d.start_date;
            f.end_date.value = d.end_date || '';
            f.register_start.value = d.register_start;
            f.register_end.value = d.register_end;
            f.vote_date.value = d.vote_date;
            f.remark.value = d.remark || '';
            $('#fmModal').modal('show');
        });
    } else {
        $('#rmTitle').text('新增选举届次');
        $('#rmId').val(0);
        $('#fmModal').modal('show');
    }
}

$(function () {
    $('#rmForm').on('submit', function (e) {
        e.preventDefault();
        var data = $(this).serializeObject();
        var url = Number(data.id) > 0 ? '/election/api/rounds/edit' : '/election/api/rounds/add';
        apiPost(url, data, function () {
            showToast('保存成功', 'success');
            $('#fmModal').modal('hide');
            loadRounds();
        });
    });
    loadRounds();
});

// P2-2 IIFE 收口：局部隔离顶层变量，仅导出模板/内联回调所需入口
var _exports = { roundBadge: roundBadge, loadRounds: loadRounds, transitionRound: transitionRound, openRoundModal: openRoundModal };
if (window.SC && window.SC.exportPageGlobals) {
    window.SC.exportPageGlobals(_exports);
} else {
    Object.keys(_exports).forEach(function (k) { window[k] = _exports[k]; });
}
})();
