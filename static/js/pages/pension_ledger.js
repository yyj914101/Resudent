/* pension_ledger.js — 养老保险（农保）大表台账前端控制器
 * 与 medical_insurance.js 同构：crudTable（脱敏/多选/批量已缴/单击行看详情/双击编辑）+
 * mi2 四层规范编辑弹窗（三段四列栅格 + 联动锁定 + 逻辑摘要 + 居民库自动反查）+
 * ADR-022 pw-modal 拖拽投放区 + A1 异步分批导入。
 */
(function () {
    'use strict';

    var rpOptions = {};
    var rpList = null;
    var rpRestore = null;
    var rpDetailId = 0;

    var RP2_SECTIONS = [
        {title: '个人与户籍信息', rows: [
            ['name', 'id_card', 'gender', 'mobile_phone'],
            ['household_property', 'household_name', 'village_group', 'contact_phone'],
            [{k: 'address', span: 2}, {k: 'remark', span: 2}]
        ]},
        {title: '参保与核对状态', rows: [
            ['payment_year', 'insurance_status', 'link_status', 'verify_status'],
            ['benefit_cert_status']
        ]},
        {title: '缴费与资助核减', note: '修改“国家代缴”或“可核减”将自动开启/锁定关联原因', rows: [
            ['is_paid', 'is_relocated', 'is_state_paid', 'state_paid_identity'],
            ['is_deductible', 'deduction_reason', {summary: true, span: 2}]
        ]}
    ];

    var RP2_META = [
        {key: 'data_source', label: '数据来源', dot: 'slate', dict: '医保数据来源'},
        {key: 'import_batch', label: '导入批次', dot: 'slate', mono: true},
        {key: 'exception_note', label: '异常说明', dot: 'auto'}
    ];
    var RP2_META_KEYS = ['seq_no', 'data_source', 'import_batch', 'exception_note'];
    var RP2_COND = {
        state_paid_identity: {trigger: 'is_state_paid', badge: 'rp2BadgeGov', def: '低保对象', off: '无需选择（非国家代缴）'},
        deduction_reason: {trigger: 'is_deductible', badge: 'rp2BadgeDed', def: '死亡', off: '无需选择（不可核减）'}
    };
    var RP2_LABELS = { address: '家庭住址', remark: '备注说明', contact_phone: '联系电话 (备选)' };
    var RP2_REQUIRED = {
        name: 1, id_card: 1, payment_year: 1, insurance_status: 1,
        is_paid: 1, is_state_paid: 1, is_deductible: 1
    };
    var RP2_PLACEHOLDER = {
        id_card: '请输入18位身份证号',
        mobile_phone: '请输入11位手机号',
        contact_phone: '座机或备用电话',
        remark: '可填写补充说明（选填）'
    };

    function fillOptSelect(sel, arr, blank) {
        var h = blank ? '<option value="">' + esc(blank) + '</option>' : '';
        $.each(arr || [], function (_, v) {
            h += '<option value="' + escAttr(v) + '">' + esc(v) + '</option>';
        });
        $(sel).html(h);
    }

    function rpCatalogFields() {
        var cat = rpOptions.field_catalog || {};
        return (cat.fields && cat.fields.rural_pension) || [];
    }

    function rpLoadOptions(cb) {
        $.getJSON('/pension/api/options', function (r) {
            if (!r || !r.success) return;
            rpOptions = r.data || {};
            fillOptSelect('#rpStatsYear', rpOptions.years, '');
            fillOptSelect('#rpSearchGroup', rpOptions.groups, '所在组');
            fillOptSelect('#rpSearchSource', rpOptions.data_sources, '数据来源');
            fillOptSelect('#rpSearchLink', rpOptions.link_statuses, '关联状态');
            fillOptSelect('#rpSearchVerify', rpOptions.verify_statuses, '核对状态');
            fillOptSelect('#rpSearchCert', rpOptions.benefit_cert_statuses, '待遇认证状态');
            var curY = new Date().getFullYear();
            var yrs = rpOptions.years || [];
            var initY = yrs.indexOf(curY) >= 0 ? curY : (yrs[0] || curY);
            $('#rpStatsYear').val(initY);
            $('#rpSearchYear').val(initY);
            if (typeof cb === 'function') cb();
        });
    }

    function rpLoadStats() {
        var y = $('#rpStatsYear').val() || $('#rpSearchYear').val() || '';
        $.getJSON('/pension/api/ledger-stats', { payment_year: y }, function (r) {
            if (!r || !r.success) return;
            var d = r.data || {};
            var html =
                '<div class="col-md-3"><div class="mini-stat"><div class="num text-primary">' + (d.total || 0) + '</div><div>农保参保总数</div></div></div>' +
                '<div class="col-md-3"><div class="mini-stat"><div class="num text-success">' + (d.paid || 0) + '（' + (d.paid_rate || 0) + '%）</div><div>已缴费人数 / 缴费率</div></div></div>' +
                '<div class="col-md-3"><div class="mini-stat"><div class="num text-info">' + (d.certified || 0) + '（' + (d.cert_rate || 0) + '%）</div><div>待遇已认证 / 认证率</div></div></div>' +
                '<div class="col-md-3"><div class="mini-stat"><div class="num text-warning">' + (d.state_paid || 0) + '</div><div>国家代缴人数</div></div></div>';
            $('#rpStatsBar').html(html);
        });
    }

    function rpListColumns() {
        var cols = [{field: '__index', title: '#'}];
        var fields = rpCatalogFields();
        function decorate(c, key) {
            var f = null;
            $.each(fields, function (_, x) { if (x.key === key) f = x; });
            if (key === 'id_card') { c.mask = true; return c; }
            if (f && f.options && f.options.length) {
                var m = {};
                $.each(f.options, function (__, o) { m[o] = o; });
                c.dict = m;
                if (key === 'is_paid') { c.tag = true; c.tagMap = {'是': 'success', '否': 'danger'}; }
            } else if (f && f.dict) {
                c.dict = f.dict;
            } else {
                c.esc = true;
            }
            return c;
        }
        [
            ['name', '姓名'], ['id_card', '身份证号'], ['gender', '性别'], ['village_group', '所在组'],
            ['insurance_status', '参保情况'], ['benefit_cert_status', '待遇认证状态'],
            ['link_status', '关联状态'], ['is_paid', '是否已缴'], ['verify_status', '核对状态']
        ].forEach(function (p) {
            var col = decorate({field: p[0], title: p[1]}, p[0]);
            if (col.field === 'link_status') col.colormap = { '已关联居民': '#0F7B3F', '已关联': '#0F7B3F', '未关联居民': '#B26A00', '未关联': '#B26A00' };
            if (col.field === 'verify_status') col.colormap = { '已核对': '#0F7B3F', '未核对': '#B26A00', '待核对': '#B26A00' };
            cols.push(col);
        });
        return cols;
    }

    function rpBuildList() {
        $('#batchHost').empty();
        var cols = rpListColumns();
        var th = '';
        $.each(cols, function (_, c) { if (c.title) th += '<th class="sc-table__th">' + esc(c.title) + '</th>'; });
        th += '<th class="sc-table__th">操作</th>';
        $('#rpTableHead').html(th);

        return crudTable({
            endpoint: '/pension/api/list',
            searchForm: '#rpSearchForm',
            tableBody: '#rpTableBody',
            pagination: '#rpPagination',
            batchBar: '#batchHost',
            emptyColspan: cols.length + 2,
            emptyText: '暂无农保参保记录，可使用上方「批量导入」或「居民库补充」',
            selection: true,
            dblclickEdit: true,
            columnToggle: true,
            onRowClick: function (item) {
                if (item && item.id) rpOpenDetail(item.id);
            },
            batchActions: [
                {
                    label: '批量标记已缴',
                    url: '/pension/api/batch-paid',
                    confirm: '确定将勾选记录批量设置为已缴费吗？',
                    cls: 'btn-dark'
                }
            ],
            pagesOf: function (r) { return r.pages || 1; },
            onLoaded: function (r) {
                rpLoadStats();
                $('#rpPageStat').text('共 ' + (r.total || 0).toLocaleString('zh-CN') + ' 人 · 每页 20 条');
            },
            columns: cols,
            rowActions: [
                {name: 'view', label: '详情', onClick: function (x) { rpOpenDetail(x.id); }},
                {name: 'edit', onClick: function (x) { rpOpenEdit(x.id); }},
                {name: 'delete', confirm: '确定删除这条养老保险参保记录吗？', url: function (x) { return '/pension/api/delete/' + x.id; }}
            ]
        });
    }

    function rpReload(p) {
        if (rpList) rpList.reload(p);
    }

    // ── 只读详情弹窗（单击数据行打开） ──
    function rpRenderHeader(pillsSel, metaSel, d) {
        var pills = [];
        if (d && d.name) pills.push('<span class="mi2-pill mi2-pill--primary">' + esc(d.name) + '</span>');
        pills.push('<span class="mi2-pill">城乡居民养老</span>');
        var y = (d && d.payment_year) ? d.payment_year : ($('#rpSearchYear').val() || new Date().getFullYear());
        pills.push('<span class="mi2-pill">' + esc(String(y)) + ' 年度</span>');
        $(pillsSel).html(pills.join(''));

        var items = $.map(RP2_META, function (m) {
            var v = d ? (d[m.key] || '') : '';
            var dot = 'mi2-metadot--slate';
            if (m.dot === 'auto') dot = (v && v !== '无') ? 'mi2-metadot--amber' : 'mi2-metadot--emerald';
            var valHtml = '<strong class="mi2-meta__val' + (m.mono ? ' mi2-meta__val--mono' : '') + '">' + esc(v || '—') + '</strong>';
            return '<div class="mi2-meta__item"><span class="mi2-metadot ' + dot + '"></span><span>' + esc(m.label) + '：</span>' + valHtml + '</div>';
        });
        $(metaSel).html('<div class="mi2-meta__list">' + items.join('') + '</div>' +
            (metaSel === '#rpFormMetaBar' ? '<div class="mi2-meta__legend">带 <span class="mi2-star">*</span> 号为必填业务字段</div>' : ''));
    }

    function rpLogicSummaryHtml(d) {
        var paid = (d.is_paid === '是');
        var gov = (d.is_state_paid === '是');
        var ded = (d.is_deductible === '是');
        var govId = d.state_paid_identity || '未填写';
        var dedR = d.deduction_reason || '未填写';
        var dotCls = ded ? 'mi2-dot--rose' : (paid || gov) ? 'mi2-dot--emerald' : 'mi2-dot--amber';
        var text = '当前逻辑：' + (paid ? '已缴费' : '未缴费') + ' · ' +
            (gov ? '国家代缴（' + govId + '）' : '个人自缴') + ' · ' +
            (ded ? '申请核减（原因：' + dedR + '）' : '正常参保不核减');
        return '<div class="mi2-span2"><div class="mi2-summary"><div class="mi2-summary__left">' +
            '<span class="mi2-dot ' + dotCls + '"></span><span class="mi2-summary__text">' + esc(text) + '</span>' +
            '</div><span class="mi2-summary__tag">逻辑正常</span></div></div>';
    }

    function rpOpenDetail(id) {
        $.getJSON('/pension/api/detail/' + id, function (r) {
            if (!r || !r.success) {
                showToast((r && r.msg) || '读取详情失败', 'danger');
                return;
            }
            var d = r.data || {};
            rpDetailId = d.id || 0;
            $('#rpDetailTitle').text('养老保险参保详情');
            rpRenderHeader('#rpDetailPills', '#rpDetailMetaBar', d);

            var byKey = {};
            $.each(rpCatalogFields(), function (_, f) { byKey[f.key] = f; });
            var html = '';
            $.each(RP2_SECTIONS, function (_, s) {
                var cells = '';
                $.each(s.rows, function (_, row) {
                    $.each(row, function (_, cell) {
                        if (cell.summary) {
                            cells += rpLogicSummaryHtml(d);
                            return;
                        }
                        var key = cell.k || cell;
                        var span = cell.span || 0;
                        var f = byKey[key];
                        if (!f) return;
                        var val = d[key];
                        var display = (val === null || val === undefined || val === '') ? '—' : String(val);
                        cells += '<div class="sc-field mi2-field' + (span ? ' mi2-span' + span : '') + '">' +
                            '<label class="sc-field__label"><span>' + esc(RP2_LABELS[key] || f.label) + '</span></label>' +
                            '<div class="sc-input" style="display:flex;align-items:center;background:var(--c-surface-2,#f8fafc);color:var(--c-ink-1);cursor:default">' +
                            esc(display) + '</div></div>';
                    });
                });
                html += '<section class="mi2-section"><div class="mi2-sechead">' +
                    '<span class="mi2-sechead__bar"></span><h4 class="mi2-sechead__title">' + esc(s.title) + '</h4>' +
                    '<span class="mi2-sechead__line"></span></div><div class="mi2-grid">' + cells + '</div></section>';
            });
            $('#rpDetailBody').html(html);
            getModal('rpDetailModal').show();
        });
    }

    // ── 四列三段式编辑弹窗（对齐医保 mi2 规范） ──
    function rp2Field(f, span) {
        var attrs = 'name="' + escAttr(f.key) + '"';
        var req = (f.required || RP2_REQUIRED[f.key]) ? '<span class="sc-field__required">*</span>' : '';
        if (RP2_REQUIRED[f.key]) attrs += ' required';
        if (RP2_PLACEHOLDER[f.key]) attrs += ' placeholder="' + escAttr(RP2_PLACEHOLDER[f.key]) + '"';
        var badge = RP2_COND[f.key] ? '<span class="mi2-badge" id="' + RP2_COND[f.key].badge + '">已锁定</span>' : '';
        var control;
        if (f.options && f.options.length) {
            control = '<select class="sc-select" ' + attrs + '>';
            $.each(f.options, function (__, o) {
                control += '<option value="' + escAttr(o) + '"' + (o === f['default'] ? ' selected' : '') + '>' + esc(o) + '</option>';
            });
            control += '</select>';
        } else if (f.key === 'payment_year' && (rpOptions.years || []).length) {
            control = '<select class="sc-select" ' + attrs + '>';
            $.each(rpOptions.years.slice().sort(function (a, b) { return b - a; }), function (__, y) {
                control += '<option value="' + escAttr(y) + '">' + esc(y) + '</option>';
            });
            control += '</select>';
        } else if (f.dict) {
            control = '<select class="sc-select form-dict" ' + attrs + ' data-dict="' + escAttr(f.dict) + '"><option value=""></option></select>';
        } else if (f.type === 'text' && f.key !== 'remark') {
            control = '<textarea class="sc-textarea" ' + attrs + ' rows="2"></textarea>';
        } else {
            var num = (f.type === 'int' || f.type === 'float');
            control = '<input class="sc-input" ' + attrs + ' type="' + (num ? 'number' : 'text') + '"' +
                (f.type === 'float' ? ' step="0.01"' : '') + '>';
        }
        return '<div class="sc-field mi2-field' + (span ? ' mi2-span' + span : '') + '">' +
            '<label class="sc-field__label"><span>' + esc(RP2_LABELS[f.key] || f.label) + req + '</span>' + badge + '</label>' +
            control + '</div>';
    }

    function rpRenderFormV2(after) {
        var byKey = {};
        $.each(rpCatalogFields(), function (_, f) { byKey[f.key] = f; });
        var html = '';
        $.each(RP2_SECTIONS, function (_, s) {
            var cells = '';
            $.each(s.rows, function (_, row) {
                $.each(row, function (_, cell) {
                    if (cell.summary) {
                        cells += '<div class="mi2-span2"><div class="mi2-summary" id="rp2Summary">' +
                            '<div class="mi2-summary__left"><span class="mi2-dot mi2-dot--amber" id="rp2StatusDot"></span>' +
                            '<span class="mi2-summary__text" id="rp2StatusText">当前逻辑：未缴费 · 个人自缴 · 正常参保不核减</span></div>' +
                            '<span class="mi2-summary__tag">逻辑正常</span></div></div>';
                        return;
                    }
                    var key = cell.k || cell;
                    var span = cell.span || 0;
                    var f = byKey[key];
                    if (!f) return;
                    cells += rp2Field(f, span);
                });
            });
            html += '<section class="mi2-section"><div class="mi2-sechead">' +
                '<span class="mi2-sechead__bar"></span><h4 class="mi2-sechead__title">' + esc(s.title) + '</h4>' +
                (s.note ? '<span class="mi2-sechead__note">' + esc(s.note) + '</span>' : '<span class="mi2-sechead__line"></span>') +
                '</div><div class="mi2-grid' + (s.note ? ' mi2-grid--end' : '') + '">' + cells + '</div></section>';
        });
        $.each(RP2_META_KEYS, function (_, k) {
            html += '<input type="hidden" name="' + escAttr(k) + '">';
        });
        $('#rpFormFields').addClass('mi2-body').html(html);

        var pending = 0, fired = false;
        function finish() { if (!fired && pending === 0) { fired = true; if (after) after(); } }
        $('#rpFormFields .form-dict').each(function () {
            pending++;
            dictSelect($(this).data('dict'), $(this), undefined, function () { pending--; finish(); });
        });
        finish();
    }

    function rp2Summary() {
        var paid = $('#rpFormFields [name="is_paid"]').val() === '是';
        var gov = $('#rpFormFields [name="is_state_paid"]').val() === '是';
        var ded = $('#rpFormFields [name="is_deductible"]').val() === '是';
        var govId = $('#rpFormFields [name="state_paid_identity"]').val() || '待选身份';
        var dedR = $('#rpFormFields [name="deduction_reason"]').val() || '待选';
        $('#rp2StatusText').text('当前逻辑：' + (paid ? '已缴费' : '未缴费') + ' · ' +
            (gov ? '国家代缴（' + govId + '）' : '个人自缴') + ' · ' +
            (ded ? '申请核减（原因：' + dedR + '）' : '正常参保不核减'));
        $('#rp2StatusDot').attr('class', 'mi2-dot ' + (ded ? 'mi2-dot--rose' : (paid || gov) ? 'mi2-dot--emerald' : 'mi2-dot--amber'));
    }

    function rp2Sync() {
        $.each(RP2_COND, function (key, c) {
            var $sel = $('#rpFormFields [name="' + key + '"]');
            var locked = $('#rpFormFields [name="' + c.trigger + '"]').val() !== '是';
            $sel.prop('disabled', locked);
            $sel.closest('.custom-select').find('.custom-select__trigger').prop('disabled', locked);
            var $first = $sel.find('option:first');
            if (locked) {
                if ($sel.val()) $sel.val('').trigger('change');
                $first.text(c.off);
            } else {
                $first.text('请选择');
                if (!$sel.val()) $sel.val(c.def).trigger('change');
            }
            if ($sel.hasClass('cs-native')) $sel.trigger('refresh.cs');
            $('#' + c.badge).text(locked ? '已锁定' : '必填项').toggleClass('mi2-badge--on', !locked);
            $sel.closest('.sc-field').toggleClass('mi-req', !locked).find('.sc-input,.sc-select').prop('required', !locked);
        });
        rp2Summary();
    }

    function rpSetVal(key, v) {
        var $f = $('#rpSaveForm [name="' + key + '"]');
        if (!$f.length) return;
        $f.val(v == null ? '' : v);
        if ($f.hasClass('cs-native')) $f.trigger('refresh.cs');
    }

    function rpLookupResident() {
        var name = $.trim($('#rpSaveForm [name="name"]').val() || '');
        var id = $.trim($('#rpSaveForm [name="id_card"]').val() || '');
        if (!name && !id) return;
        $.getJSON('/resident/api/lookup', { name: name, id_card: id }, function (r) {
            if (!r || !r.success || !r.data || !r.data.length) return;
            var d = r.data[0];
            $.each({
                name: 'name', id_card: 'id_card', gender: 'gender', village_group: 'village_group',
                address: 'address', contact_phone: 'phone', mobile_phone: 'phone',
                household_name: 'household_name', household_property: 'household_property'
            }, function (k, src) {
                if (d[src]) rpSetVal(k, d[src]);
            });
            showToast('已自动关联居民信息', 'success');
        });
    }

    function rpInitNewForm() {
        $('#rpSaveForm')[0].reset();
        rpSetVal('payment_year', $('#rpSearchYear').val() || new Date().getFullYear());
        rpSetVal('data_source', '手工新增');
        rpSetVal('insurance_status', '正常参保');
        rpSetVal('benefit_cert_status', '未认证');
        rpRenderHeader('#rpFormPills', '#rpFormMetaBar', null);
        rp2Sync();
    }

    function rpOpenAdd() {
        $('#rpEditId').val(0);
        $('#rpFormTitle').text('新增参保人');
        rpRenderFormV2(function () {
            rpInitNewForm();
            rpRestore = rpInitNewForm;
        });
        getModal('rpFormModal').show();
    }

    function rpOpenEdit(id) {
        $.getJSON('/pension/api/detail/' + id, function (r) {
            if (!r || !r.success) {
                showToast((r && r.msg) || '读取失败', 'danger');
                return;
            }
            var d = r.data || {};
            $('#rpEditId').val(d.id || 0);
            $('#rpFormTitle').text('编辑养老保险');
            function fill() {
                $('#rpSaveForm')[0].reset();
                Object.keys(d).forEach(function (k) { rpSetVal(k, d[k]); });
                rpRenderHeader('#rpFormPills', '#rpFormMetaBar', d);
                rp2Sync();
            }
            rpRenderFormV2(function () {
                fill();
                rpRestore = fill;
            });
            getModal('rpFormModal').show();
        });
    }

    function rpValidateRequired() {
        $('#rpFormFields .mi-error').removeClass('mi-error').find('.mi-err-text').remove();
        var bad = [];
        $('#rpFormFields [required]').each(function () {
            if (!$(this).prop('disabled') && !String($(this).val() || '').trim()) bad.push(this);
        });
        $.each(bad, function (_, el) {
            var $f = $(el).closest('.sc-field');
            $f.addClass('mi-error');
            $f.append('<span class="mi-err-text">该项为必填项</span>');
        });
        if (bad.length) { bad[0].focus(); return false; }
        return true;
    }

    // ── 导入弹窗拖拽与暂存区（对齐医保 pw-modal） ──
    function initImportDropZone() {
        var $inp = $('#rpImportFile');
        var $drop = $('#rpImpDrop');
        var $staged = $('#rpImpStaged');
        var $submit = $('#btnRpDoImport');
        if (!$drop.length) return;
        function fmtSize(b) {
            if (b < 1024) return b + ' B';
            if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
            return (b / (1024 * 1024)).toFixed(2) + ' MB';
        }
        function stage(f) {
            $('#rpImpStagedName').text(f.name);
            $('#rpImpStagedMeta').text(fmtSize(f.size) + ' ·');
            $drop.addClass('d-none');
            $staged.removeClass('d-none');
            $submit.prop('disabled', false);
        }
        function resetStaged() {
            $inp.val('');
            $staged.addClass('d-none');
            $drop.removeClass('d-none');
            $submit.prop('disabled', true).text('开始导入');
        }
        $drop.on('click', function () { $inp.trigger('click'); })
            .on('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $inp.trigger('click'); }
            })
            .on('dragover', function (e) { e.preventDefault(); $(this).addClass('is-drag'); })
            .on('dragleave drop', function () { $(this).removeClass('is-drag'); })
            .on('drop', function (e) {
                e.preventDefault();
                var dt = e.originalEvent && e.originalEvent.dataTransfer;
                if (dt && dt.files && dt.files.length) {
                    $inp[0].files = dt.files;
                    stage(dt.files[0]);
                }
            });
        $inp.on('change', function () { if (this.files && this.files[0]) stage(this.files[0]); });
        $('#rpImpReselect').on('click', function () { $inp.trigger('click'); });
        $('#rpImpRemove').on('click', resetStaged);
        window.__rpImpReset = resetStaged;
    }

    $(function () {
        initImportDropZone();

        rpLoadOptions(function () {
            rpList = rpBuildList();
            rpList.reload(1);
        });

        $('#rpStatsYear').on('change', function () {
            $('#rpSearchYear').val($(this).val());
            rpReload(1);
        });

        $('#rpSearchForm').on('submit', function (e) {
            e.preventDefault();
            rpReload(1);
        });

        $('#btnRpReset').on('click', function () {
            $('#rpKeyword').val('');
            $('#rpSearchForm select').val('');
            $('#rpSearchYear').val($('#rpStatsYear').val() || new Date().getFullYear());
            rpReload(1);
        });

        $('#btnRpStatsToggle').on('click', function () {
            var $bar = $('#rpStatsBar');
            $bar.toggle();
            $(this).html($bar.is(':visible')
                ? '<i class="bi bi-bar-chart"></i> 收起年度看板'
                : '<i class="bi bi-bar-chart"></i> 查看年度看板');
        });

        $('#btnRpAdd').on('click', rpOpenAdd);
        $('#btnRpDetailEdit').on('click', function () {
            getModal('rpDetailModal').hide();
            if (rpDetailId) rpOpenEdit(rpDetailId);
        });

        $('#btnRpExport').on('click', function () {
            $('#rpSearchYear').val($('#rpStatsYear').val() || '');
            window.location.href = '/pension/api/export?' + $('#rpSearchForm').serialize();
        });

        $('#btnRpGenerate').on('click', function () {
            var y = $('#rpStatsYear').val() || new Date().getFullYear();
            if (!confirm('确定从居民库补充生成 ' + y + ' 年度未建档的农保参保记录吗？')) return;
            $.post('/pension/api/generate-from-residents', {
                payment_year: y,
                village_group: $('#rpSearchGroup').val() || ''
            }, function (r) {
                if (r && r.success) {
                    showToast(r.msg || '补充完成', 'success');
                    rpLoadOptions(function () { rpReload(1); });
                } else {
                    showToast((r && r.msg) || '补充失败', 'danger');
                }
            });
        });

        $('#btnRpImport').on('click', function () {
            var y = $('#rpStatsYear').val() || new Date().getFullYear();
            $('#rpImportBatch').val(y + '年农保部门下发名单');
            if (window.__rpImpReset) window.__rpImpReset();
            getModal('rpImportModal').show();
        });

        $('#rpImportForm').on('submit', function (e) {
            e.preventDefault();
            var fileEl = document.getElementById('rpImportFile');
            var file = fileEl && fileEl.files && fileEl.files[0];
            if (!file) { showToast('请先选择要导入的表格文件', 'warning'); return; }
            var batchName = ($('#rpImportBatch').val() || '').trim();
            var $btn = $('#btnRpDoImport').prop('disabled', true);
            if (window.runDomainBatchImport) {
                window.runDomainBatchImport({
                    prefix: '/pension',
                    file: file,
                    uploadFormFields: {},
                    extraPayload: { import_batch: batchName },
                    onProgress: function (pct) {
                        $btn.text('导入中 ' + pct + '%');
                    },
                    onDone: function (rep) {
                        $btn.prop('disabled', false).text('开始导入');
                        getModal('rpImportModal').hide();
                        showToast('农保导入完成：新增 ' + (rep.inserted_rows || 0) + '，更新 ' + (rep.updated_rows || 0) +
                            '，跳过 ' + (rep.skip_rows || 0) + '，失败 ' + (rep.fail_rows || 0), 'success');
                        rpLoadOptions(function () { rpReload(1); });
                    },
                    onError: function (msg) {
                        $btn.prop('disabled', false).text('开始导入');
                        showToast(msg || '导入失败', 'danger');
                    }
                });
            }
        });

        $(document).on('change', '#rpFormFields [name="is_state_paid"], #rpFormFields [name="is_deductible"]', rp2Sync);
        $('#rpFormFields').on('change', '[name="is_paid"], [name="state_paid_identity"], [name="deduction_reason"]', rp2Summary);
        $('#rpFormFields').on('input change', '.sc-input, .sc-select, .sc-textarea', function () {
            $(this).closest('.sc-field').removeClass('mi-error').find('.mi-err-text').remove();
        });
        $('#rpSaveForm').on('blur', '[name="name"],[name="id_card"]', rpLookupResident)
            .on('keydown', '[name="name"],[name="id_card"]', function (e) {
                if (e.key === 'Enter') { e.preventDefault(); rpLookupResident(); }
            });

        $('#rpResetBtn').on('click', function () {
            if (typeof rpRestore === 'function') {
                rpRestore();
                showToast('已恢复初始状态', 'success');
            }
        });

        $('#rpSaveForm').on('submit', function (e) {
            e.preventDefault();
            if (!rpValidateRequired()) return;
            $.post('/pension/api/save', $(this).serialize(), function (r) {
                if (r && r.success) {
                    getModal('rpFormModal').hide();
                    showToast(r.msg || '保存成功', 'success');
                    rpReload();
                } else {
                    showToast((r && r.msg) || '保存失败', 'danger');
                }
            });
        });
    });
})();
