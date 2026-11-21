/* sanitation.js — 090C 环卫工作门面页指标卡（M-01 队伍在岗人数合计 / M-02 点位数量）。
 * 取数唯一入口：GET /sanitation/api/stats（后端代理 MetricsEngine.query），
 * 与统一统计入口 /api/stats/query、引擎直调三处同值，页面不做二次计算。
 * 依赖：jQuery（全局）；卡片容器：#sanitationStats（templates/sanitation/index.html）。
 */
(function () {
    'use strict';

    function loadStats() {
        $.getJSON('/sanitation/api/stats', function (r) {
            if (!r || !r.success) return;
            var d = r.data || {};
            var head = d.headcount || {};
            var site = d.site_count || {};
            $('#sanitationHeadcount').text(head.data && head.data.sum != null ? head.data.sum : '-');
            $('#sanitationSiteCount').text(site.data && site.data.total != null ? site.data.total : '-');
        });
    }

    $(function () { loadStats(); });
})();
