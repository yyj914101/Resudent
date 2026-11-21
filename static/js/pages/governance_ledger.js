/* governance_ledger.js — 综治管理台账（配置驱动，4 类台账共用一页）
 *
 * 配置经 window.__GOV_CFG__ 注入（template 内联 script），spa.js 重放本脚本时
 * 会重新读取注入值；本脚本 IIFE 化，页面事件经 #tableBody 委托绑定。
 * 2026-11-17 Master–Detail 全站统一：行点击 → 只读详情弹窗（行数据缓存 rowsCache），
 * 编辑/删除入口在详情底部，操作列只保留危险动作。
 */
(function () {
    'use strict';
    var cfg = window.__GOV_CFG__ || {};
    var ledgerConfig = cfg.config || {};
    var ledgerCategory = cfg.category || '';
    if (!ledgerCategory) { return; }

    var currentPage = 1;
    var pageSize = 20;
    var baseUrl = '/governance/' + ledgerCategory;
    var columns = ledgerConfig.columns || [];
    var fields = ledgerConfig.fields || [];
    var rowsCache = {};   // id → 最近一次列表响应的行数据（详情直建，避免二次请求）

    // 状态语义字段 = 配置里首个 select 字段（criminal.personnel_status / road-hazard.fix_status /
    // key-vehicle.license_status 等，逐台账不同）；无 select 字段的台账无徽章。
    // 补丁原取值 person_status/risk_level/status 均为臆造字段名（配置里不存在），徽章恒隐藏，此处配置驱动修正。
    var statusField = null;
    for (var si = 0; si < fields.length; si++) {
        if (fields[si].kind === 'select') { statusField = fields[si]; break; }
    }
    var isStatusKey = function (key) { return !!statusField && key === statusField.key; };
    var isNumKey = function (key, f) { return f.kind === 'date' || f.kind === 'number' || /phone|id_card|count|date/.test(key); };

    function loadData(page) {
        currentPage = Math.max(1, page || 1);
        var query = $('#searchForm').serialize();
        $('#exportLink').attr('href', baseUrl + '/api/export?' + query);
        $.getJSON(baseUrl + '/api/list?' + query + '&page=' + currentPage + '&limit=' + pageSize, function (resp) {
            if (!resp.success) { showToast(resp.msg || '查询失败', 'danger'); return; }
            rowsCache = {};
            var html = '';
            $.each(resp.data || [], function (index, item) {
                rowsCache[item.id] = item;
                html += '<tr data-id="' + item.id + '" style="cursor:pointer"><td class="gs-num">' + ((currentPage - 1) * pageSize + index + 1) + '</td>';
                $.each(columns, function (_, key) {
                    var value = item[key];
                    if (isStatusKey(key)) {
                        // 原型口径：状态列统一语义徽章（info 档）
                        html += '<td><span class="sc-badge sc-badge--info">' + esc(value || '—') + '</span></td>';
                    } else {
                        var fdef = null;
                        for (var fi = 0; fi < fields.length; fi++) { if (fields[fi].key === key) { fdef = fields[fi]; break; } }
                        var empty = value === null || value === undefined || value === '';
                        if (fdef && fdef.kind === 'textarea') {
                            html += '<td class="sc-ellipsis" style="max-width:220px" title="' + escAttr(empty ? '—' : value) + '">' + esc(empty ? '—' : value) + '</td>';
                        } else {
                            var cls = empty ? '' : (isNumKey(key, fdef || {}) ? ' class="gs-num"' : '');
                            html += '<td' + (cls ? ' class="' + cls.trim() + '"' : ' class="sc-ellipsis" style="max-width:200px"') +
                                (empty ? '' : ' title="' + escAttr(value) + '"') + '>' + esc(empty ? '—' : value) + '</td>';
                        }
                    }
                });
                html += '<td><div class="sc-row-actions">' +
                    '<button class="sc-btn sc-btn--text" type="button" data-detail="' + item.id + '">详情</button>' +
                    '<button class="sc-btn sc-btn--text" type="button" data-edit="' + item.id + '">编辑</button>' +
                    '<button class="sc-btn sc-btn--text is-danger" type="button" data-delete="' + item.id + '">删除</button>' +
                    '</div></td></tr>';
            });
            var colCount = columns.length + 2;
            $('#tableBody').html(html || '<tr><td colspan="' + colCount + '" class="text-center text-muted py-4">暂无台账记录</td></tr>');
            $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 ' + pageSize + ' 条');
            var pages = Math.max(1, Math.ceil((resp.total || 0) / pageSize));
            renderPagination('#pagination', pages, currentPage, loadData);
        });
    }

    function openAdd() {
        $('#saveForm')[0].reset();
        $('#recordId').val('');
        $('#formTitle').text('新增' + (ledgerConfig.title || ''));
        $('#saveForm [name="record_date"]').val(new Date().toISOString().slice(0, 10));
        getModal('formModal').show();
    }

    function editRecord(id) {
        $.getJSON(baseUrl + '/api/detail/' + id, function (resp) {
            if (!resp.success) { showToast(resp.msg || '读取失败', 'danger'); return; }
            $('#saveForm')[0].reset();
            $('#recordId').val(id);
            $('#formTitle').text('编辑' + (ledgerConfig.title || ''));
            $.each(fields, function (_, field) {
                $('#saveForm [name="' + field.key + '"]').val(resp.data[field.key] || '');
            });
            getModal('formModal').show();
        });
    }

    // ADR-022 统一单屏详情弹窗（自动拆分短字段至 4 列栅格、长文本/备注至通栏卡槽）
    function openDetail(id) {
        var item = rowsCache[id];
        if (!item) { showToast('数据已刷新，请重试', 'danger'); return; }

        var shortFields = [];
        var longFields = [];
        $.each(fields, function (_, f) {
            if (f.kind === 'textarea' || f.wide || f.full) longFields.push(f);
            else shortFields.push(f);
        });

        var titleText = item.name || item.station_name || item.plate_no || item.location || (ledgerConfig.title + '详情');
        $('#govDetailTitle').text(titleText);
        // 配置驱动：首个 select 字段即状态语义（criminal.personnel_status / road-hazard.fix_status /
        // key-vehicle.license_status …），无 select 字段的台账徽章隐藏；补丁原 person_status/risk_level/status 为臆造字段名
        var badgeVal = statusField ? (item[statusField.key] || '') : '';
        if (badgeVal) {
            $('#govDetailBadge').text(badgeVal).prop('hidden', false);
        } else {
            $('#govDetailBadge').prop('hidden', true);
        }
        $('#govDetailMeta').text(ledgerConfig.title + (item.record_date ? ' · 登记日期 ' + item.record_date : ''));

        var gridHtml = '<div class="gs-grid4">';
        $.each(shortFields, function (_, f) {
            var v = item[f.key];
            var empty = v === null || v === undefined || v === '';
            var isNum = f.kind === 'date' || f.kind === 'number' || /phone|id_card|count|date/.test(f.key);
            gridHtml += '<div class="gs-field"><span class="gs-label">' + esc(f.label || f.key) + '</span>' +
                '<span class="gs-value' + (empty ? ' gs-value--empty' : '') + (isNum ? ' gs-num' : '') + '">' +
                esc(empty ? '未填写' : v) + '</span></div>';
        });
        gridHtml += '</div>';

        var bodyHtml = '<section class="gs-card">' +
            '<div class="gs-card__head"><div class="gs-card__title">' +
            '<span class="gs-card__bar gs-card__bar--1"></span>' +
            '<span class="gs-card__index gs-card__index--1">01</span>' +
            '<h4 class="gs-card__name">基本信息</h4></div>' +
            '<span class="gs-card__count">共 ' + shortFields.length + ' 项字段</span></div>' +
            gridHtml + '</section>';

        if (longFields.length) {
            bodyHtml += '<section class="gs-card">' +
                '<div class="gs-card__head"><div class="gs-card__title">' +
                '<span class="gs-card__bar gs-card__bar--2"></span>' +
                '<span class="gs-card__index gs-card__index--2">02</span>' +
                '<h4 class="gs-card__name">详细内容与备注</h4></div></div>';
            $.each(longFields, function (_, f) {
                var v = item[f.key];
                bodyHtml += '<div class="gs-slot"><span class="gs-slot__tag">' + esc(f.label || f.key) + '</span>' +
                    esc(v === null || v === undefined || v === '' ? '未填写' : v) + '</div>';
            });
            bodyHtml += '</section>';
        }

        $('#govDetailBody').html(bodyHtml);
        $('#govDetailBtns').html(
            '<button type="button" class="sc-btn sc-btn--primary" data-gov-edit="' + id + '"><i class="bi bi-pencil"></i> 编辑</button>');
        getModal('govDetailModal').show();
    }

    function deleteRecord(id) {
        if (!confirm('确定删除该条记录吗？删除后不可恢复。')) { return; }
        $.post(baseUrl + '/api/delete/' + id, function (resp) {
            showToast(resp.msg || '操作完成', resp.success ? 'success' : 'danger');
            if (resp.success) { loadData(currentPage); }
        });
    }

    $(function () {
        loadData(1);
        $('#addButton').on('click', openAdd);
        $('#searchForm').on('submit', function (e) { e.preventDefault(); loadData(1); });
        // 2026-11-16：工具栏筛选下拉（如「人员状态」）改选即生效，无需再点查询
        $('#searchForm').on('change', 'select', function () { loadData(1); });
        $('#resetButton').on('click', function () { $('#searchForm')[0].reset(); loadData(1); });
        $('#tableBody').on('click', '[data-delete]', function () { deleteRecord($(this).data('delete')); });
        $('#tableBody').on('click', '[data-detail]', function () { openDetail($(this).data('detail')); });
        $('#tableBody').on('click', '[data-edit]', function () { editRecord($(this).data('edit')); });
        $('#govDetailBtns').on('click', '[data-gov-edit]', function () {
            var eid = $(this).data('gov-edit');
            getModal('govDetailModal').hide();
            editRecord(eid);
        });
        $('#tableBody').on('click', 'tr[data-id]', function (e) {
            if ($(e.target).closest('[data-delete], [data-detail], [data-edit]').length) return;
            openDetail($(this).data('id'));
        });
        $('#saveForm').on('submit', function (e) {
            e.preventDefault();
            $.post(baseUrl + '/api/save', $(this).serialize(), function (resp) {
                showToast(resp.msg || '保存完成', resp.success ? 'success' : 'danger');
                if (resp.success) { getModal('formModal').hide(); loadData(currentPage); }
            });
        });
    });

    // 详情底部编辑按钮走全局（onclick 内联需要）
    window.editRecord = editRecord;
})();
