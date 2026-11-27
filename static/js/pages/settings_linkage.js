/* settings_linkage.js — 数据联动与补贴口径可视化配置前端控制器 */
(function () {
    'use strict';

    var state = {
        activeTab: 'policy',
        data: null,
        softtableSubsidies: []
    };

    function colOptions(cols, current, allowEmpty) {
        var html = allowEmpty ? '<option value="">（无）</option>' : '';
        $.each(cols || [], function (_, c) {
            var sel = c.name === current ? ' selected' : '';
            html += '<option value="' + escAttr(c.name) + '"' + sel + '>' + esc(c.name) +
                (c.type === 'number' ? ' [数值]' : '') + '</option>';
        });
        return html;
    }

    function unitOptions(units, current) {
        var html = '';
        $.each(units || [], function (_, u) {
            html += '<option value="' + escAttr(u) + '"' + (u === current ? ' selected' : '') + '>' + esc(u) + '</option>';
        });
        return html;
    }

    function renderPolicy() {
        var d = state.data || {};
        $('#policyStatusBadge').text(d.has_custom_policy ? '已自定义' : '出厂默认');
        var rows = d.policy_items || [];
        var html = '';
        $.each(rows, function (_, it) {
            var cols = (d.model_columns || {})[it.model] || [];
            html += '<tr data-key="' + escAttr(it.item_key) + '">' +
                '<td class="text-center"><input type="checkbox" class="form-check-input js-pol-en"' + (it.enabled ? ' checked' : '') + '></td>' +
                '<td><strong>' + esc(it.module_label) + '</strong><div class="small text-muted">' + esc(it.module) + '</div></td>' +
                '<td><input type="text" class="sc-input js-pol-name" value="' + escAttr(it.name) + '" maxlength="60"></td>' +
                '<td><select class="sc-select js-pol-amt">' + colOptions(cols, it.amount_field, false) + '</select></td>' +
                '<td><select class="sc-select js-pol-period">' + colOptions(cols, it.period_field, true) + '</select></td>' +
                '<td><select class="sc-select js-pol-unit">' + unitOptions(d.unit_options, it.unit) + '</select></td>' +
                '</tr>';
        });
        $('#policyBody').html(html || '<tr><td colspan="6" class="text-center text-muted py-4">暂无配置项</td></tr>');

        var stOpts = '<option value="">请选择已建软表</option>';
        $.each(d.softtables || [], function (_, t) {
            stOpts += '<option value="' + escAttr(t.code) + '">' + esc(t.name) + '（' + esc(t.code) + '）</option>';
        });
        $('#sfPickTable').html(stOpts);
        renderSoftSubsidies();
    }

    function renderSoftSubsidies() {
        var list = state.softtableSubsidies || [];
        if (!list.length) {
            $('#softSubsidyBody').html('<tr><td colspan="6" class="text-center text-muted py-3">暂未接入扩展软表补贴</td></tr>');
            return;
        }
        var html = '';
        $.each(list, function (i, it) {
            html += '<tr>' +
                '<td><code>' + esc(it.table_code) + '</code></td>' +
                '<td>' + esc(it.name) + '</td>' +
                '<td><code>' + esc(it.identity_field) + '</code></td>' +
                '<td><code>' + esc(it.amount_field) + '</code></td>' +
                '<td>' + esc(it.unit || '元/月') + '</td>' +
                '<td><button type="button" class="sc-btn sc-btn--text is-danger js-rm-soft" data-idx="' + i + '">移除</button></td>' +
                '</tr>';
        });
        $('#softSubsidyBody').html(html);
    }

    function renderSync() {
        var d = state.data || {};
        $('#syncStatusBadge').text(d.has_custom_sync ? '已自定义' : '出厂默认');
        var labels = d.sync_field_labels || {};
        var html = '';
        $.each(d.sync_tables || [], function (_, row) {
            var locLabel = row.locate === 'resident_id' ? '主档ID + 身份证兜底'
                : (row.locate === 'medical' ? '已关联主档ID' : '身份证哈希');
            var chips = '<div class="sl-sync-chips">';
            $.each(row.default_fields || {}, function (srcKey, defDst) {
                var checked = row.fields && row.fields[srcKey] ? ' checked' : '';
                var dstVal = (row.fields && row.fields[srcKey]) || defDst;
                var dstText = Array.isArray(dstVal) ? dstVal.join('/') : dstVal;
                chips += '<label class="sl-sync-chip">' +
                    '<input type="checkbox" class="form-check-input js-sync-f" data-src="' + escAttr(srcKey) +
                    '" data-dst="' + escAttr(JSON.stringify(dstVal)) + '"' + checked + '>' +
                    '<span>' + esc(labels[srcKey] || srcKey) + ' → <code>' + esc(dstText) + '</code></span></label>';
            });
            chips += '</div>';
            html += '<tr data-table="' + escAttr(row.table) + '">' +
                '<td class="text-center"><input type="checkbox" class="form-check-input js-sync-en"' + (row.enabled ? ' checked' : '') + '></td>' +
                '<td><strong>' + esc(row.table_label) + '</strong><div class="small text-muted">' + esc(row.table) + '</div></td>' +
                '<td><span class="sc-badge sc-badge--muted">' + esc(locLabel) + '</span></td>' +
                '<td>' + chips + '</td></tr>';
        });
        $('#syncBody').html(html || '<tr><td colspan="4" class="text-center text-muted py-4">暂无联动模块</td></tr>');
    }

    function loadData() {
        $.getJSON('/settings/api/linkage/data', function (resp) {
            if (!resp || !resp.success) {
                showToast((resp && resp.msg) || '加载失败', 'danger');
                return;
            }
            state.data = resp.data || {};
            state.softtableSubsidies = (state.data.softtable_subsidies || []).slice();
            renderPolicy();
            renderSync();
        });
    }

    function switchTab(tab) {
        state.activeTab = tab;
        var isPolicy = tab === 'policy';
        $('#tabPolicy').toggleClass('is-active', isPolicy).attr('aria-selected', String(isPolicy));
        $('#tabSync').toggleClass('is-active', !isPolicy).attr('aria-selected', String(!isPolicy));
        $('#panePolicy').prop('hidden', !isPolicy);
        $('#paneSync').prop('hidden', isPolicy);
    }

    function saveCurrent() {
        if (state.activeTab === 'policy') {
            var items = [];
            $('#policyBody tr[data-key]').each(function () {
                var $tr = $(this);
                items.push({
                    item_key: $tr.data('key'),
                    enabled: $tr.find('.js-pol-en').is(':checked'),
                    name: $.trim($tr.find('.js-pol-name').val()),
                    amount_field: $tr.find('.js-pol-amt').val(),
                    period_field: $tr.find('.js-pol-period').val(),
                    unit: $tr.find('.js-pol-unit').val()
                });
            });
            $.ajax({
                url: '/settings/api/linkage/policy', type: 'POST', contentType: 'application/json',
                data: JSON.stringify({ policy_items: items, softtable_subsidies: state.softtableSubsidies })
            }).done(function (r) {
                showToast(r.msg || '政策性补贴口径已保存', r.success ? 'success' : 'danger');
                if (r.success) loadData();
            });
            return;
        }
        var tables = [];
        $('#syncBody tr[data-table]').each(function () {
            var $tr = $(this);
            var fields = {};
            $tr.find('.js-sync-f:checked').each(function () {
                var src = $(this).data('src');
                var dst = $(this).data('dst');
                try { dst = typeof dst === 'string' ? JSON.parse(dst) : dst; } catch (e) {}
                fields[src] = dst;
            });
            tables.push({
                table: $tr.data('table'),
                enabled: $tr.find('.js-sync-en').is(':checked'),
                fields: fields
            });
        });
        $.ajax({
            url: '/settings/api/linkage/sync', type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ sync_tables: tables })
        }).done(function (r) {
            showToast(r.msg || '主档联动配置已保存', r.success ? 'success' : 'danger');
            if (r.success) loadData();
        });
    }

    $(function () {
        $('#tabPolicy').on('click', function () { switchTab('policy'); });
        $('#tabSync').on('click', function () { switchTab('sync'); });
        $('#btnSaveLinkage').on('click', saveCurrent);
        $('#btnResetLinkage').on('click', function () {
            if (!window.confirm('确定将当前分区的配置恢复为系统出厂默认吗？')) return;
            $.ajax({
                url: '/settings/api/linkage/reset', type: 'POST', contentType: 'application/json',
                data: JSON.stringify({ scope: state.activeTab })
            }).done(function (r) {
                showToast(r.msg || '已恢复默认', r.success ? 'success' : 'danger');
                if (r.success) loadData();
            });
        });

        $('#sfPickTable').on('change', function () {
            var code = $(this).val();
            var tbl = ((state.data && state.data.softtables) || []).filter(function (t) { return t.code === code; })[0];
            if (!tbl) {
                $('#sfPickIdentity').html('<option value="">先选软表</option>');
                $('#sfPickAmount').html('<option value="">先选软表</option>');
                return;
            }
            var idOpts = '', amtOpts = '';
            $.each(tbl.fields || [], function (_, f) {
                var opt = '<option value="' + escAttr(f.code) + '">' + esc(f.display_name) + ' (' + esc(f.code) + ')</option>';
                idOpts += opt;
                amtOpts += opt;
            });
            $('#sfPickIdentity').html(idOpts);
            $('#sfPickAmount').html(amtOpts);
            if (!$('#sfPickName').val()) $('#sfPickName').val(tbl.name);
        });

        $('#btnAddSoftSubsidy').on('click', function () {
            var code = $('#sfPickTable').val();
            var idCol = $('#sfPickIdentity').val();
            var amtCol = $('#sfPickAmount').val();
            var name = $.trim($('#sfPickName').val());
            var unit = $('#sfPickUnit').val() || '元/月';
            if (!code || !idCol || !amtCol) {
                showToast('请选择软表、身份证列与金额列', 'warning');
                return;
            }
            state.softtableSubsidies.push({
                table_code: code, name: name || code,
                identity_field: idCol, amount_field: amtCol,
                unit: unit, enabled: true
            });
            $('#sfPickName').val('');
            renderSoftSubsidies();
            showToast('已加入列表，请点击右上角「保存当前配置」生效', 'info');
        });

        $('#softSubsidyBody').on('click', '.js-rm-soft', function () {
            var idx = parseInt($(this).data('idx'), 10);
            state.softtableSubsidies.splice(idx, 1);
            renderSoftSubsidies();
        });

        loadData();
    });
})();
