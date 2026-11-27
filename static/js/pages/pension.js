/* pension.js — 088 养老保险门面页指标卡。
 * 取数唯一入口：GET /pension/api/stats（后端代理 MetricsEngine.query），
 * 与统一统计入口 /api/stats/query、看板/报表消费方三处同值，页面不做二次计算。
 * 依赖：jQuery（全局）；卡片容器：#pensionStats（templates/pension/index.html）。
 */
(function () {
    'use strict';

    function loadStats() {
        $.getJSON('/pension/api/stats', function (r) {
            if (!r || !r.success) return;
            var d = r.data || {};
            var coverage = d.coverage || {};
            var cert = d.cert_rate || {};
            $('#pensionCoverageRate').text((coverage.data && coverage.data.rate != null ? coverage.data.rate : '-') );
            $('#pensionInsured').text(coverage.data && coverage.data.insured != null ? coverage.data.insured : '-');
            $('#pensionCertRate').text(cert.data && cert.data.rate != null ? cert.data.rate : '-');
            $('#pensionRosterTotal').text(cert.data && cert.data.total != null ? cert.data.total : '-');
        });
    }

    $(function () { loadStats(); });
})();
