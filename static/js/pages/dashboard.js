(function () {
var charts = {};
var lastStats = null;
var structureTab = 'age';

$(function() {
    loadDashboardStats();
    loadDashboardTodos();
    $('#structureTabs').on('click', '.sc-dashboard-tab', function() {
        structureTab = $(this).data('structure-tab');
        $('#structureTabs .sc-dashboard-tab').removeClass('is-active');
        $(this).addClass('is-active');
        renderStructure();
    });
    // R6-E3：停留首页按默认 60s 轮询刷新速览数字（后端 agg_cache TTL=45s）
    if (!window._dashboardPollTimer) {
        window._dashboardPollTimer = setInterval(function() {
            loadDashboardStats();
            loadDashboardTodos();
        }, 60000);
    }
    // R-06：SPA 切页清轮询
    window.__SPA_TEARDOWN__ = function() {
        if (window._dashboardPollTimer) {
            clearInterval(window._dashboardPollTimer);
            window._dashboardPollTimer = null;
        }
        Object.keys(charts).forEach(function(k) {
            if (charts[k]) charts[k].destroy();
            charts[k] = null;
        });
    };
});

// 058：大数值带千分位（对齐 v1 样板「1,286」显示）
function fmtNum(n) {
    return String(n == null ? 0 : n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
}

function palette() {
    return {
        green: cssVar('--c-cat-1', '#16A34A'),
        amber: cssVar('--c-cat-4', '#D97706'),
        teal: cssVar('--c-cat-2', '#0891B2'),
        muted: cssVar('--c-muted', '#737373'),
        rail: cssVar('--c-rail', '#F5F5F5'),
        ink: cssVar('--c-ink-2', '#171717')
    };
}

function destroyChart(key) {
    if (charts[key]) {
        charts[key].destroy();
        charts[key] = null;
    }
}

function numCell(v, cls) {
    return '<td class="' + (cls || 'sc-table__td--num') + '">' + fmtNum(v) + '</td>';
}

// T20-3：首页待办提醒（复用 /todo/api/list，只取 5 项未办结）
function loadDashboardTodos() {
    if (!$('#dashboardTodoList').length) return;
    $.get('/todo/api/list', {limit: 5, status: 'todo'}, function(resp) {
        if (!resp.success) return;
        var payload = resp.data || {};
        var items = payload.data || [];
        if (!items.length) {
            $('#dashboardTodoList').html('<tr><td colspan="4" class="sc-empty-row"><i class="bi bi-check2-circle"></i> 暂无待办事项</td></tr>');
            return;
        }
        var html = '';
        $.each(items, function(i, t) {
            var badge = todoBadge(t.todo_type);
            var icon = todoIcon(t.todo_type);
            // 076 RC-03（验收标准 3）：时间列「—」数据缺陷修复——remind_time 为空的待办
            // 回退展示相对创建时间（v1-01 样图「3 天前」口径），不再整列空杠
            var due = todoDueText(t.remind_time, t.overdue, t.created_at);
            html += '<tr>' +
                '<td><span class="sc-todo-title"><i class="bi ' + icon + '"></i> ' + esc(t.title) + '</span></td>' +
                '<td><span class="sc-badge ' + badge + '">' + esc(t.todo_type_label || '待办') + '</span></td>' +
                '<td class="sc-todo-due' + (t.overdue ? ' is-overdue' : '') + '">' + esc(due) + '</td>' +
                '<td class="sc-todo-action"><a class="sc-btn sc-btn--text" href="/todo/" aria-label="查看待办">→</a></td>' +
                '</tr>';
        });
        $('#dashboardTodoList').html(html);
    });
}

function todoBadge(type) {
    return type === 'warn' ? 'sc-badge--danger'
        : type === 'reconcile' ? 'sc-badge--info'
        : type === 'backup' ? 'sc-badge--warning'
        : type === 'birthday' ? 'sc-badge--primary'
        : type === 'age_reminder' ? 'sc-badge--warning'
        : 'sc-badge--muted';
}

function todoIcon(type) {
    return type === 'warn' ? 'bi-exclamation-triangle'
        : type === 'reconcile' ? 'bi-clipboard-check'
        : type === 'backup' ? 'bi-archive'
        : type === 'birthday' ? 'bi-cake2'
        : type === 'age_reminder' ? 'bi-clock'
        : 'bi-check2-circle';
}

function todoDueText(remindTime, overdue, createdAt) {
    if (remindTime) {
        if (overdue) return '已逾期';
        var dt = new Date(remindTime.replace(/-/g, '/'));
        if (isNaN(dt.getTime())) return remindTime;
        var now = new Date();
        var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        var day = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
        var diff = Math.round((day - today) / 86400000);
        var hm = ('0' + dt.getHours()).slice(-2) + ':' + ('0' + dt.getMinutes()).slice(-2);
        if (diff === 0) return '今天 ' + hm + ' 截止';
        if (diff === 1) return '明天 ' + hm + ' 截止';
        if (diff > 1 && diff <= 7) return diff + ' 天后 ' + hm + ' 截止';
        return ('0' + (dt.getMonth() + 1)).slice(-2) + '-' + ('0' + dt.getDate()).slice(-2);
    }
    // 076 RC-03：无提醒时间 → 相对创建时间（今天/昨天/N 天前，>30 天回退月-日）
    if (createdAt) {
        var cd = new Date(String(createdAt).replace(/-/g, '/'));
        if (!isNaN(cd.getTime())) {
            var n = new Date();
            var today0 = new Date(n.getFullYear(), n.getMonth(), n.getDate());
            var cday = new Date(cd.getFullYear(), cd.getMonth(), cd.getDate());
            var cdiff = Math.round((today0 - cday) / 86400000);
            if (cdiff <= 0) return '今天';
            if (cdiff === 1) return '昨天';
            if (cdiff <= 30) return cdiff + ' 天前';
            return ('0' + (cd.getMonth() + 1)).slice(-2) + '-' + ('0' + cd.getDate()).slice(-2);
        }
        return createdAt;
    }
    return '—';
}

function loadDashboardStats() {
    $.get('/dashboard/api/dashboard/stats', function(resp) {
        if (!resp.success) return;
        $('#dashboardLoadError').addClass('d-none');
        var d = resp.data;
        lastStats = d;

        // T17-5 空态引导（D3）：空库（无人口）时展示导入引导与快捷入口
        if (Number(d.total_population) === 0) {
            $('#emptyGuide').removeClass('d-none');
            $('#statsCards').addClass('d-none');
        } else {
            $('#emptyGuide').addClass('d-none');
            $('#statsCards').removeClass('d-none');
        }

        $('#pageMetaCounts').text('在册 ' + fmtNum(d.total_population) + ' 人 / ' + fmtNum(d.total_households) + ' 户');
        $('#statPopulation').text(fmtNum(d.total_population));
        $('#statHouseholds').text(fmtNum(d.total_households));
        $('#statParty').text(d.party_count);
        $('#statDistressed').text(d.distressed_child_count);
        $('#statDibao').text(d.dibao_count);
        $('#statWelfare').text(d.welfare_count);
        $('#statHardElderly').text(d.hard_elderly_count);
        $('#statDisabled').text(d.disabled_count);
        $('#statVeteran').text(d.veteran_count);
        $('#statElderly').text(d.elderly_count);

        $('#statMale').text(fmtNum(d.male_count));
        $('#statFemale').text(fmtNum(d.female_count));
        var genderTotal = (Number(d.male_count) || 0) + (Number(d.female_count) || 0) || 1;
        $('#maleBar').css('width', Math.round((Number(d.male_count) || 0) / genderTotal * 100) + '%');
        $('#femaleBar').css('width', Math.round((Number(d.female_count) || 0) / genderTotal * 100) + '%');

        renderGroupTable(d.group_stats || [], d);
        renderComposition(d.population_composition || {});
        renderReview(d.review_progress || []);
        renderStructure();
        renderHouseProp(d);
        renderMedical(d);
    }).fail(function() {
        $('#dashboardLoadError').removeClass('d-none');
    });
}

function renderGroupTable(rows, d) {
    var html = '';
    rows.forEach(function(g) {
        html += '<tr><td>' + esc(g.name) + '</td>' +
            numCell(g.population) + numCell(g.households) + numCell(g.male) + numCell(g.female) + '</tr>';
    });
    $('#groupStatsBody').html(html || '<tr><td colspan="5" class="sc-empty-row">暂无数据</td></tr>');
    // 合计行取 KPI 权威总数（分组合计会因未分组/跨组户而偏差）
    $('#groupStatsFoot').html(rows.length
        ? '<tr class="sc-table__foot"><td>合计</td>' +
          numCell(Number(d.total_population) || 0) + numCell(Number(d.total_households) || 0) +
          numCell(Number(d.male_count) || 0) + numCell(Number(d.female_count) || 0) + '</tr>'
        : '');
}

function renderComposition(data) {
    var p = palette();
    var items = data.items || [];
    var colorMap = {
        dibao_disabled: p.green,
        elderly: p.amber,
        child: p.teal,
        other: p.muted
    };
    $('#compositionMeta').text('重点对象 ' + fmtNum(data.total_people || 0) + ' 人 / ' + fmtNum(data.total_households || 0) + ' 户');
    $('#compositionCenter').text(fmtNum(data.total_people || 0));
    var legend = '';
    items.forEach(function(it) {
        legend += '<li><span class="sc-legend__dot" style="background:' + (colorMap[it.key] || p.muted) + '"></span>' +
            '<span class="sc-legend__label">' + esc(it.label) + '</span>' +
            '<span class="sc-legend__pct">' + (Number(it.pct) || 0).toFixed(1) + '%</span></li>';
    });
    $('#compositionLegend').html(legend || '<li class="sc-legend__empty">暂无重点对象</li>');

    destroyChart('composition');
    var el = document.getElementById('compositionChart');
    if (!el) return;
    charts.composition = new Chart(el.getContext('2d'), {
        type: 'doughnut',
        data: {
            labels: items.map(function(it) { return it.label; }),
            datasets: [{
                data: items.map(function(it) { return it.count; }),
                backgroundColor: items.map(function(it) { return colorMap[it.key] || p.muted; }),
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '62%',
            plugins: {legend: {display: false}, datalabels: {display: false}}
        }
    });
}

function renderReview(rows) {
    var html = '';
    rows.forEach(function(r) {
        var total = Number(r.total) || 0;
        var done = (r.done === null || r.done === undefined) ? null : Number(r.done);
        var pct = (r.available && total > 0 && done !== null) ? Math.round(done / total * 100) : 0;
        var text = (r.available && done !== null) ? (done + '/' + total) : '—';
        html += '<div class="sc-review__row">' +
            '<div class="sc-review__head"><span>' + esc(r.label) + '</span><span>' + esc(text) + '</span></div>' +
            '<div class="sc-review__track"><span class="sc-review__bar" style="width:' + (r.available ? pct : 0) + '%"></span></div>' +
            (!r.available && r.note ? '<small class="sc-review__note">' + esc(r.note) + '</small>' : '') +
            '</div>';
    });
    $('#reviewProgress').html(html || '<div class="sc-empty-row">暂无复核口径</div>');
}

function renderStructure() {
    var d = lastStats;
    if (!d) return;
    var p = palette();
    var labels = [];
    var counts = [];
    var colors = [];
    var legend = '';

    if (structureTab === 'age') {
        var age = d.age_structure || {labels: [], counts: [], labor: 0, nonlabor: 0};
        labels = age.labels || [];
        counts = age.counts || [];
        colors = counts.map(function(_, i) { return (i === 1 || i === 2) ? p.green : p.muted; });
        legend = '<span><i class="sc-legend__dot" style="background:' + p.green + '"></i>劳动年龄 ' + fmtNum(age.labor) + ' 人</span>' +
                 '<span><i class="sc-legend__dot" style="background:' + p.muted + '"></i>非劳动年龄 ' + fmtNum(age.nonlabor) + ' 人</span>' +
                 (Number(age.unknown) > 0 ? '<span>未登记出生日期 ' + fmtNum(age.unknown) + ' 人</span>' : '');
    } else if (structureTab === 'household') {
        var ht = d.household_type_stats || {};
        Object.keys(ht).forEach(function(k) {
            labels.push(k);
            counts.push(Number(ht[k].people_count != null ? ht[k].people_count : ht[k].count) || 0);
            colors.push(p.green);
        });
        legend = '<span>共 ' + fmtNum(counts.reduce(function(a, b) { return a + b; }, 0)) + ' 人</span>';
    } else {
        var ns = d.nation_stats || [];
        labels = ns.map(function(n) { return n.name; });
        counts = ns.map(function(n) { return Number(n.count) || 0; });
        colors = counts.map(function() { return p.green; });
        legend = '<span>共 ' + fmtNum(counts.reduce(function(a, b) { return a + b; }, 0)) + ' 人</span>';
    }
    $('#structureLegend').html(legend);

    destroyChart('structure');
    var el = document.getElementById('structureChart');
    if (!el) return;
    charts.structure = new Chart(el.getContext('2d'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                data: counts,
                backgroundColor: colors,
                borderRadius: 3,
                maxBarThickness: 42
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {display: false},
                datalabels: {
                    anchor: 'end', align: 'top', color: p.ink,
                    font: {size: 11, weight: '600'},
                    formatter: function(v) { return v ? fmtNum(v) : ''; }
                }
            },
            scales: {
                y: {beginAtZero: true, ticks: {precision: 0}, grid: {color: cssVar('--c-line-2', '#E5E5E5')}},
                x: {grid: {display: false}}
            }
        }
    });
}

function renderHouseProp(d) {
    var p = palette();
    var stats = d.household_prop_stats || {};
    var labels = Object.keys(stats);
    var counts = labels.map(function(k) { return Number(stats[k].count) || 0; });
    $('#housePropTotal').text(fmtNum(d.total_households || 0));

    destroyChart('houseProp');
    var el = document.getElementById('housePropChart');
    if (!el) return;
    charts.houseProp = new Chart(el.getContext('2d'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                data: counts,
                backgroundColor: p.green,
                borderRadius: 3,
                maxBarThickness: 70
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {display: false},
                datalabels: {
                    anchor: 'end', align: 'top', color: p.ink,
                    font: {size: 11, weight: '600'},
                    formatter: function(v) { return v ? fmtNum(v) : ''; }
                }
            },
            scales: {
                y: {beginAtZero: true, ticks: {precision: 0}, grid: {color: cssVar('--c-line-2', '#E5E5E5')}},
                x: {grid: {display: false}}
            }
        }
    });
}

function renderMedical(d) {
    var p = palette();
    var total = Number(d.medical_total) || 0;
    var paid = Number(d.medical_paid) || 0;
    var rate = Number(d.medical_paid_rate) || 0;
    var color = rate >= 90 ? p.green : p.amber;
    $('#medicalYear').text(d.medical_year || '—');
    $('#medicalRateText').text(rate + '%');
    $('#medicalRateMeta').text('已缴 ' + fmtNum(paid) + ' / 应缴 ' + fmtNum(total) + ' 人');

    destroyChart('medical');
    var el = document.getElementById('medicalRateChart');
    if (!el) return;
    charts.medical = new Chart(el.getContext('2d'), {
        type: 'doughnut',
        data: {
            labels: ['已缴', '未缴'],
            datasets: [{
                data: total > 0 ? [paid, Math.max(total - paid, 0)] : [0, 0],
                backgroundColor: [color, p.rail],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '72%',
            plugins: {legend: {display: false}, datalabels: {display: false}}
        }
    });
}
})();
