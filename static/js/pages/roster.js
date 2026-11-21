/* roster.js — 台账门面前端（099）。
 * 三模板共用，按 window.__ST_ROSTER__.mode 分流：
 *   page      列表页（roster_page.html）：筛选 / 分页 / 新增·编辑弹窗 / 导出
 *   bootstrap 未建表引导页（roster_bootstrap.html）：上传 + 字段确认
 *   upload    独立上传页（roster_upload.html）：上传 + 字段确认（与 bootstrap 共用流程）
 * urls 契约：{ page, list, record, upload, create, export }（/shop/roster/<code>/api/...；
 * record 由 JS 拼 /<rid>，rid=0 表示新增）。
 * CSRF 由 spa.js ajaxSend 全局注入（X-CSRF-Token），此处不另造通道（078 同口径）。
 */
(function () {
    'use strict';
    var cfg = window.__ST_ROSTER__ || { mode: 'page', code: '', fields: [], urls: {} };
    var urls = cfg.urls || {};

    /* ============ 列表页（mode=page） ============ */
    function initPage() {
        var fields = cfg.fields || [];
        var page = 1, pageSize = 20;

        function filterQuery() { return $('#filterForm').serialize(); }

        function load() {
            $.getJSON(urls.list + '?' + filterQuery() + '&page=' + page + '&limit=' + pageSize,
                function (resp) {
                    if (!resp.success) { showToast(resp.msg || '加载失败', 'danger'); return; }
                    // 路由返回扁平体 {success,msg,data:[行],total,truncated}，render 直接吃整个 resp
                    // （R-103-01：原 render(resp.data || {}) 传入的是行数组，render 内 data.data 取空 → 列表恒「暂无数据」）
                    render(resp);
                });
        }

        // 列表每行为 {f_code: 显示值}：枚举 label 由后端 decode+映射后回传，前端直展。
        function render(data) {
            var rows = data.data || [];
            var html = '';
            $.each(rows, function (i, row) {
                html += '<tr data-id="' + escAttr(row.id) + '"><td>' + ((page - 1) * pageSize + i + 1) + '</td>';
                $.each(fields, function (_, f) {
                    var v = row[f.code];
                    var raw = (v === undefined || v === null) ? '' : String(v);
                    html += '<td data-code="' + escAttr(f.code) + '" data-raw="' + escAttr(raw) + '">' +
                        (raw === '' ? '<span class="text-muted">—</span>' : esc(raw)) + '</td>';
                });
                html += '<td><div class="sc-row-actions">' +
                    '<button type="button" class="sc-btn sc-btn--text" data-act="edit">编辑</button>' +
                    '</div></td></tr>';
            });
            $('#recordBody').html(html ||
                '<tr><td colspan="' + (fields.length + 2) + '" class="text-center text-muted py-4">暂无数据</td></tr>');
            $('#pageStat').text('共 ' + (data.total || 0) + ' 条 · 每页 ' + pageSize + ' 条' +
                (data.truncated ? ' · 结果过多已截断' : ''));
            var pages = Math.max(1, Math.ceil((data.total || 0) / pageSize));
            renderPagination('#pagination', pages, page, function (p) { page = p; load(); });
        }

        function openCreate() {
            $('#saveForm')[0].reset();
            $('#recordId').val('0');
            $('#formTitle').text('新增记录');
            getModal('formModal').show();
        }

        // 编辑回填：行值是展示值——枚举需按 label 反查 option value（提交 value）；identity 为掩码，
        // 原样回填（后端按「值未变更即保持原文」处理，勿以掩码覆盖密文）。
        function openEdit($tr) {
            $('#saveForm')[0].reset();
            $('#recordId').val($tr.data('id'));
            $('#formTitle').text('编辑记录');
            $.each(fields, function (_, f) {
                var shown = $tr.find('td[data-code="' + f.code + '"]').attr('data-raw') || '';
                var $input = $('#saveForm [name="f_' + f.code + '"]');
                if (f.data_type === 'enum') {
                    var value = '';
                    $.each(f.options || [], function (_, o) {
                        if (!value && o.label === shown) { value = o.value; }
                    });
                    $input.val(value);
                } else {
                    $input.val(shown);
                }
            });
            getModal('formModal').show();
        }

        function save() {
            var rid = $('#recordId').val() || '0';
            var values = {};
            $.each(fields, function (_, f) {
                var v = $('#saveForm [name="f_' + f.code + '"]').val();
                values[f.code] = (v === undefined || v === null) ? '' : v;
            });
            var $btn = $('#saveForm button[type="submit"]').prop('disabled', true);
            $.ajax({
                url: urls.record + '/' + encodeURIComponent(rid),
                type: 'POST', contentType: 'application/json',
                data: JSON.stringify({ values: values })
            }).done(function (resp) {
                showToast(resp.msg || '保存完成', resp.success ? 'success' : 'danger');
                if (resp.success) { getModal('formModal').hide(); load(); }
            }).fail(function (xhr) {
                var resp = xhr.responseJSON || {};
                showToast(resp.msg || ('保存失败（' + xhr.status + '）'), 'danger');
            }).always(function () { $btn.prop('disabled', false); });
        }

        $(function () {
            $('#filterForm').on('submit', function (e) { e.preventDefault(); page = 1; load(); });
            $('#resetFilter').on('click', function () { $('#filterForm')[0].reset(); page = 1; load(); });
            $('#addButton').on('click', openCreate);
            // 导出带当前筛选参数（不带分页），后端直接回文件流。
            $('#exportButton').on('click', function () { window.location = urls.export + '?' + filterQuery(); });
            // 2026-11-17 Master–Detail：行点击 → 编辑弹窗（软表字段动态，编辑表单即全量信息视图，
            // 单元格点击编辑契约不变；操作列编辑钮保留为唯一显式入口）
            $('#recordBody').on('click', 'tr[data-id]', function (e) {
                if ($(e.target).closest('button, a').length) return;
                openEdit($(this));
            });
            $('#recordBody').on('click', '[data-act="edit"]', function () { openEdit($(this).closest('tr')); });
            $('#saveForm').on('submit', function (e) { e.preventDefault(); save(); });
            load();
        });
    }

    /* ============ 上传 + 字段确认（mode=bootstrap / upload 共用） ============ */
    function initUploadFlow() {
        var state = { token: '', dictCategories: [] };
        var TYPE_OPTIONS = [['text', '文本'], ['number', '数字'], ['date', '日期'], ['enum', '枚举']];
        var SENS_OPTIONS = [['none', '不敏感'], ['identity', '身份标识（加密）'], ['amount', '金额']];

        function selectHtml(cls, options, current) {
            var html = '<select class="sc-select ' + cls + '">';
            $.each(options, function (_, o) {
                html += '<option value="' + escAttr(o[0]) + '"' +
                    (String(o[0]) === String(current) ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
            });
            return html + '</select>';
        }

        // dict_categories：[{code, name}]，code 即分类名（dict_categories 无 code 列，引擎以 name 兼作 code）。
        function dictSelectHtml(current) {
            var cats = state.dictCategories || [];
            var html = '<select class="sc-select js-dict-cat"' + (cats.length ? '' : ' disabled') + '>';
            html += '<option value=""' + (current ? '' : ' selected') + '>' +
                (cats.length ? '请选择字典分类' : '（无可用字典分类）') + '</option>';
            $.each(cats, function (_, c) {
                html += '<option value="' + escAttr(c.code) + '"' +
                    (c.code === current ? ' selected' : '') + '>' + esc(c.name) + '</option>';
            });
            return html + '</select>';
        }

        // suggestions 每项（softtable_engine.suggest_fields）：
        // { code:'f00', display_name, data_type, dict_category_code, sensitivity, is_required, sort_order, hint }
        function renderFieldRows(suggestions) {
            var html = '';
            $.each(suggestions || [], function (_, s) {
                html += '<tr>' +
                    '<td class="text-center"><input type="checkbox" class="form-check-input js-skip" aria-label="跳过此列"></td>' +
                    '<td><input type="text" class="sc-input js-display" value="' + escAttr(s.display_name || '') +
                    '" maxlength="100">' +
                    (s.hint ? '<p class="sc-field__hint">' + esc(s.hint) + '</p>' : '') + '</td>' +
                    '<td><code class="js-code">' + esc(s.code) + '</code></td>' +
                    '<td>' + selectHtml('js-dtype', TYPE_OPTIONS, s.data_type || 'text') + '</td>' +
                    '<td class="text-center"><input type="checkbox" class="form-check-input js-required"' +
                    (s.is_required ? ' checked' : '') + ' aria-label="必填"></td>' +
                    '<td>' + selectHtml('js-sens', SENS_OPTIONS, s.sensitivity || 'none') + '</td>' +
                    '<td class="js-dict-cat-cell"' + (s.data_type === 'enum' ? '' : ' hidden') + '>' +
                    dictSelectHtml(s.dict_category_code) + '</td>' +
                    '</tr>';
            });
            $('#fieldRows').html(html ||
                '<tr><td colspan="7" class="text-center text-muted py-4">未解析到字段</td></tr>');
        }

        function renderSample(headers, rows, rowCount) {
            var head = headers || [], body = rows || [];
            $('#sampleStat').text('共解析 ' + rowCount + ' 行数据，前 ' + Math.min(body.length, 5) + ' 行预览：');
            if (!head.length || !body.length) {
                $('#sampleWrap').html('<p class="sc-field__hint">无预览数据</p>');
                return;
            }
            var html = '<div class="sc-table-wrap"><table class="sc-table"><thead class="sc-table__head"><tr>';
            $.each(head, function (_, h) { html += '<th class="sc-table__th">' + esc(h) + '</th>'; });
            html += '</tr></thead><tbody>';
            $.each(body.slice(0, 5), function (_, row) {
                html += '<tr>';
                $.each(head, function (i) {
                    var v = Array.isArray(row) ? row[i] : row[head[i]];
                    html += '<td>' + (v === undefined || v === null || v === ''
                        ? '<span class="text-muted">—</span>' : esc(v)) + '</td>';
                });
                html += '</tr>';
            });
            $('#sampleWrap').html(html + '</tbody></table></div>');
        }

        // 汇总确认字段（勾「跳过此列」的剔除）；枚举必须有字典分类（口径同引擎 normalize_fields）。
        function collectFields() {
            var out = [], ok = true;
            $('#fieldRows tr').each(function () {
                var $tr = $(this);
                if (!$tr.find('.js-code').length || $tr.find('.js-skip').is(':checked')) { return; }
                var dtype = $tr.find('.js-dtype').val();
                var cat = $tr.find('.js-dict-cat').val() || '';
                if (dtype === 'enum' && !cat) {
                    ok = false;
                    $tr.find('.js-dict-cat').addClass('is-invalid');
                }
                out.push({
                    code: $tr.find('.js-code').text(),
                    display_name: $.trim($tr.find('.js-display').val()),
                    data_type: dtype,
                    dict_category_code: dtype === 'enum' ? cat : null,
                    sensitivity: $tr.find('.js-sens').val(),
                    is_required: $tr.find('.js-required').is(':checked')
                });
            });
            if (!ok) { showToast('枚举字段必须选择字典分类，或改为文本类型', 'danger'); return null; }
            return out;
        }

        function doUpload() {
            if (!$('#rosterFile').val()) { showToast('请先选择 Excel 文件', 'warning'); return; }
            var fd = new FormData();
            fd.append('file', $('#rosterFile')[0].files[0]);
            var $btn = $('#uploadBtn').prop('disabled', true);
            $.ajax({ url: urls.upload, type: 'POST', data: fd, processData: false, contentType: false })
                .done(function (resp) {
                    if (!resp.success) { showToast(resp.msg || '解析失败', 'danger'); return; }
                    state.token = (resp.data && resp.data.token) || '';
                    state.dictCategories = (resp.data && resp.data.dict_categories) || [];
                    $('#uploadCard').prop('hidden', true);
                    $('#confirmCard').prop('hidden', false);
                    if (!$('#tableName').val() && cfg.meta && cfg.meta.name) { $('#tableName').val(cfg.meta.name); }
                    renderFieldRows(resp.data.suggestions || []);
                    renderSample(resp.data.headers || [], resp.data.sample || [], resp.data.row_count || 0);
                    showToast('解析完成，请确认字段后建表', 'success');
                })
                .fail(function (xhr) {
                    var resp = xhr.responseJSON || {};
                    showToast(resp.msg || ('上传失败（' + xhr.status + '）'), 'danger');
                })
                .always(function () { $btn.prop('disabled', false); });
        }

        function doCreate() {
            var payloadFields = collectFields();
            if (!payloadFields) { return; }
            if (!payloadFields.length) { showToast('至少保留一个字段，其余可勾选「跳过此列」', 'warning'); return; }
            var name = $.trim($('#tableName').val());
            if (!name) { showToast('请填写台账名称', 'warning'); return; }
            var $btn = $('#createBtn').prop('disabled', true);
            $.ajax({
                url: urls.create, type: 'POST', contentType: 'application/json',
                data: JSON.stringify({
                    token: state.token,
                    code: cfg.code,
                    name: name,
                    skip_error_rows: $('#skipErrorRows').is(':checked'),
                    fields: payloadFields
                })
            }).done(function (resp) {
                if (!resp.success) { showToast(resp.msg || '建表失败', 'danger'); return; }
                var imported = resp.data && resp.data.imported;
                showToast('台账已建立' + (imported ? '，已导入 ' + imported + ' 条' : ''), 'success');
                // 成功后跳转门面列表页（code 以后端回传为准，兜底用页面传入的 code）；
                // 目标取模板下发的 urls.page（__ST_ROSTER__ 契约），不写死业务前缀
                // （089：civilization/pension 复用本 JS，写死 /shop 会跳错模块）。
                window.location = cfg.urls.page;
            }).fail(function (xhr) {
                var resp = xhr.responseJSON || {};
                showToast(resp.msg || ('建表失败（' + xhr.status + '）'), 'danger');
            }).always(function () { $btn.prop('disabled', false); });
        }

        $(function () {
            $('#uploadBtn').on('click', doUpload);
            $('#createBtn').on('click', doCreate);
            // 类型切到「枚举」才显示字典分类列选择。
            $('#fieldRows').on('change', '.js-dtype', function () {
                $(this).closest('tr').find('.js-dict-cat-cell').prop('hidden', $(this).val() !== 'enum');
            });
        });
    }

    $(function () {
        if (cfg.mode === 'bootstrap' || cfg.mode === 'upload') { initUploadFlow(); }
        else { initPage(); }
    });
})();
