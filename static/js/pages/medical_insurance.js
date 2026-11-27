(function () {
var options = {};
// 2026-11-16：三分类页签取消 → 全量单表 + 险种筛选。#insuranceTypeInput 由隐藏域改为工具栏下拉，
// currentType 语义收窄为「新增/导入/模板的默认险种」（跟随筛选值，筛全部时回落居民医保）。
var currentType = 'resident';
var list = null;
function filterType() { return $('#insuranceTypeInput').val() || ''; }
function defaultType() { return filterType() || 'resident'; }
var TEXT = {yes: '是', no: '否', year: '年度', group: '所在组', source: '数据来源', link: '关联状态', verify: '核对状态', benefitCert: '待遇认证状态', paidRate: '缴费比例', paidPeople: '已缴人数', unpaidPeople: '未缴人数', statePaid: '国家代缴', deductible: '核减人数', topGroup: '最高组比例', edit: '编辑', del: '删除', empty: '暂无数据', found: '已自动关联居民信息', addTitle: '新增参保人', editTitle: '编辑医保缴费', confirmDelete: '确定删除这条医保缴费记录吗？', confirmGen: '确定从居民库补充生成当前年度未建档的医保记录吗？', selectOne: '请先勾选需要设置的记录', confirmPaid: '确定将勾选记录批量设置为已缴费吗？', pageInfo: '第 {page} / {pages} 页，共 {total} 条'};

function fillSelect(sel, arr, blank) { let h = blank ? '<option value="">' + esc(blank) + '</option>' : ''; $.each(arr || [], function (_, v) { h += '<option value="' + esc(v) + '">' + esc(v) + '</option>'; }); $(sel).html(h); }

function loadOptions(cb) {
    $.getJSON('/medical-insurance/api/options', function (r) {
        if (!r.success) return;
        options = r.data;
        fillSelect('#searchYear', options.years, TEXT.year);
        fillSelect('#statsYear', options.years, TEXT.year);
        fillSelect('#searchGroup', options.groups, TEXT.group);
        fillSelect('#searchSource', options.data_sources, TEXT.source);
        fillSelect('#searchLink', options.link_statuses, TEXT.link);
        fillSelect('#searchVerify', options.verify_statuses, TEXT.verify);
        fillSelect('#searchBenefitCert', options.benefit_cert_statuses, TEXT.benefitCert);
        let y = new Date().getFullYear();
        $('#statsYear').val(y); $('#searchYear').val(y);
        if (cb) cb();
    });
}

function loadStats() {
    let y = $('#statsYear').val() || $('#searchYear').val();
    $.getJSON('/medical-insurance/api/stats', {payment_year: y}, function (r) {
        if (!r.success) return;
        let d = r.data;
        let group = d.group_stats && d.group_stats.length ? d.group_stats[0] : null;
        let html = '<div class="col-md-2"><div class="mini-stat"><div class="num text-primary">' + d.paid_rate + '%</div><div>' + TEXT.paidRate + '</div></div></div><div class="col-md-2"><div class="mini-stat"><div class="num text-success">' + d.paid + '</div><div>' + TEXT.paidPeople + '</div></div></div><div class="col-md-2"><div class="mini-stat"><div class="num text-danger">' + d.unpaid + '</div><div>' + TEXT.unpaidPeople + '</div></div></div><div class="col-md-2"><div class="mini-stat"><div class="num text-info">' + d.state_paid + '</div><div>' + TEXT.statePaid + '</div></div></div><div class="col-md-2"><div class="mini-stat"><div class="num text-warning">' + d.deductible + '</div><div>' + TEXT.deductible + '</div></div></div><div class="col-md-2"><div class="mini-stat"><div class="num text-secondary">' + (group ? group.rate + '%' : '-') + '</div><div>' + TEXT.topGroup + '</div></div></div>';
        $('#medicalStats').html(html);
    });
}
function refreshStatsByYear() { loadStats(); }

// ── 字段目录驱动（054 复核 P1-3）：表单、列表列、导出统一从 /api/options 的
//    field_catalog.fields[type] 派生（common + 专属），代码里不再出现任何字段 key 清单 ──
function catalogFields(type) {
    var cat = options.field_catalog || {};
    return (cat.fields && cat.fields[type]) || [];
}

// 2026-11-02 编辑弹窗规范：分组卡双栏（基本信息/缴费与核减 ｜ 参保信息/其他信息）；
// 只读字段（序号/导入批次/数据来源）浅灰禁用；是否国家代缴=是→国家代缴身份必填、是否可核减=是→核减原因必填
var MI_FORM_GROUPS = [
    { key: 'basic',  title: '基本信息',   col: 'left',  keys: ['seq_no', 'name', 'id_card', 'gender', 'contact_phone', 'mobile_phone', 'household_property', 'household_name'] },
    { key: 'pay',    title: '缴费与核减', col: 'left',  keys: ['is_paid', 'is_state_paid', 'state_paid_identity', 'is_deductible', 'deduction_reason', 'is_relocated'] },
    { key: 'insure', title: '参保信息',   col: 'right', keys: ['payment_year', 'data_source', 'link_status', 'verify_status', 'benefit_cert_status', 'village_group', 'insurance_status', 'work_unit', 'insured_place', 'payment_base', 'personal_payment', 'employer_payment', 'employee_status'] },
    { key: 'other',  title: '其他信息',   col: 'right', keys: ['import_batch', 'exception_note', 'address', 'remark', 'insurance_desc'] }
];
var MI_READONLY = { seq_no: 1, import_batch: 1, data_source: 1 };
var MI_FULL = { address: 1, remark: 1 };

function renderFormFields(type, after) {
    var byKey = {};
    $.each(catalogFields(type), function (_, f) { byKey[f.key] = f; });
    $('#formFields').removeClass('mi2-body');   // 职工/其他走 2026-11-02 分组卡样式
    var cols = { left: [], right: [] };
    $.each(MI_FORM_GROUPS, function (_, g) {
        var fields = [];
        $.each(g.keys, function (_, key) {
            var f = byKey[key];
            if (!f) return;
            var ro = MI_READONLY[key] ? ' mi-readonly' : '';
            var full = MI_FULL[key] ? ' mi-full' : '';
            var req = f.required ? '<span class="sc-field__required">*</span>' : '';
            var attrs = 'name="' + escAttr(f.key) + '"';
            var control;
            if (f.options && f.options.length) {
                control = '<select class="sc-select" ' + attrs + (ro ? ' disabled' : '') + '>';
                $.each(f.options, function (__, o) {
                    control += '<option value="' + escAttr(o) + '"' + (o === f['default'] ? ' selected' : '') + '>' + esc(o) + '</option>';
                });
                control += '</select>';
            } else if (f.dict) {
                control = '<select class="sc-select form-dict" ' + attrs + (ro ? ' disabled' : '')
                    + ' data-dict="' + escAttr(f.dict) + '"><option value=""></option></select>';
            } else if (f.type === 'text') {
                control = '<textarea class="sc-textarea" ' + attrs + (ro ? ' disabled' : '') + ' rows="2"></textarea>';
            } else {
                var num = (f.type === 'int' || f.type === 'float');
                control = '<input class="sc-input" ' + attrs + ' type="' + (num ? 'number' : 'text') + '"'
                    + (f.type === 'float' ? ' step="0.01"' : '') + (ro ? ' disabled' : '') + '>';
            }
            fields.push('<div class="sc-field' + full + ro + '"><label class="sc-field__label">' + esc(f.label) + req + '</label>' + control + '</div>');
        });
        if (fields.length) {
            cols[g.col].push('<section class="sc-fgroup"><h4 class="sc-fgroup__title">' + esc(g.title) + '</h4>'
                + '<div class="sc-fgroup__fields">' + fields.join('') + '</div></section>');
        }
    });
    $('#formFields').html('<div class="sc-fgroups">'
        + '<div class="sc-fgroups__col">' + cols.left.join('') + '</div>'
        + '<div class="sc-fgroups__col">' + cols.right.join('') + '</div></div>');
    // 字典字段异步填充，全部就绪后再回调（编辑回填须等它完成）
    var pending = 0, fired = false;
    function finish() { if (!fired && pending === 0) { fired = true; if (after) after(); } }
    $('#formFields .form-dict').each(function () {
        pending++;
        dictSelect($(this).data('dict'), $(this), undefined, function () { pending--; finish(); });
    });
    finish();
}

// ── 2026-11-11 居民医保编辑弹窗 v2（ui.md「全宽四列统一栅格 + 三段式水平流」，原型 code_artifact.html）──
// 结构：T1 标题+身份胶囊组 / T2 只读元数据条（数据来源·导入批次·异常说明——不再以伪输入框出现，
//       提交载荷由隐藏 input 保序）/ T3 四列栅格三段式 / T4 重置为初始状态+取消+保存修改。
// 契约零改动：字段 name、#saveForm 序列化、miValidateRequired、/api/save 全部沿用。职工/其他仍走上方分组卡。
var MI2_SECTIONS = [
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
var MI2_META = [
    {key: 'data_source', label: '数据来源', dot: 'slate', dict: '医保数据来源'},
    {key: 'import_batch', label: '导入批次', dot: 'slate', mono: true},
    {key: 'exception_note', label: '异常说明', dot: 'auto'}
];
var MI2_META_KEYS = ['seq_no', 'data_source', 'import_batch', 'exception_note'];  // 元数据化字段的隐藏载荷
var MI2_COND = {
    state_paid_identity: {trigger: 'is_state_paid', badge: 'mi2BadgeGov', def: '低保对象', off: '无需选择（非国家代缴）'},
    deduction_reason: {trigger: 'is_deductible', badge: 'mi2BadgeDed', def: '死亡', off: '无需选择（不可核减）'}
};
var mi2Record = null;   // 打开弹窗时的记录快照（重置用）
var mi2Restore = null;  // 「重置为初始状态」的恢复闭包（openAdd/openEdit 时赋值）
// 规范 §4.3 矩阵的展示层覆盖（仅弹窗内生效；列表列/导出仍用目录原标签）
var MI2_LABELS = { address: '家庭住址', remark: '备注说明', contact_phone: '联系电话 (备选)' };
var MI2_REQUIRED = { name: 1, id_card: 1, mobile_phone: 1, village_group: 1, payment_year: 1,
    insurance_status: 1, is_paid: 1, is_state_paid: 1, is_deductible: 1 };
var MI2_PLACEHOLDER = { id_card: '请输入18位身份证号', mobile_phone: '请输入11位手机号',
    contact_phone: '座机或备用电话', remark: '可填写补充说明（选填）' };

function mi2Field(f, span) {
    var attrs = 'name="' + escAttr(f.key) + '"';
    var req = (f.required || MI2_REQUIRED[f.key]) ? '<span class="sc-field__required">*</span>' : '';
    if (MI2_REQUIRED[f.key]) attrs += ' required';   // 规范矩阵必填：星标与阻断一致
    if (MI2_PLACEHOLDER[f.key]) attrs += ' placeholder="' + escAttr(MI2_PLACEHOLDER[f.key]) + '"';
    var badge = MI2_COND[f.key] ? '<span class="mi2-badge" id="' + MI2_COND[f.key].badge + '">已锁定</span>' : '';
    var control;
    if (f.options && f.options.length) {
        control = '<select class="sc-select" ' + attrs + '>';
        $.each(f.options, function (__, o) {
            control += '<option value="' + escAttr(o) + '"' + (o === f['default'] ? ' selected' : '') + '>' + esc(o) + '</option>';
        });
        control += '</select>';
    } else if (f.key === 'payment_year' && (options.years || []).length) {
        // 缴费年度：规范为下拉；选项取 /api/options 的 years（年度域由后端供给，不硬编码）
        control = '<select class="sc-select" ' + attrs + '>';
        $.each(options.years.slice().sort(function (a, b) { return b - a; }), function (__, y) {
            control += '<option value="' + escAttr(y) + '">' + esc(y) + '</option>';
        });
        control += '</select>';
    } else if (f.dict) {
        control = '<select class="sc-select form-dict" ' + attrs + ' data-dict="' + escAttr(f.dict) + '"><option value=""></option></select>';
    } else if (f.type === 'text' && f.key !== 'remark') {
        // 备注（说明）按规范为单行输入框；其余 text 型仍为多行
        control = '<textarea class="sc-textarea" ' + attrs + ' rows="2"></textarea>';
    } else {
        var num = (f.type === 'int' || f.type === 'float');
        control = '<input class="sc-input" ' + attrs + ' type="' + (num ? 'number' : 'text') + '"'
            + (f.type === 'float' ? ' step="0.01"' : '') + '>';
    }
    return '<div class="sc-field mi2-field' + (span ? ' mi2-span' + span : '') + '">'
        + '<label class="sc-field__label"><span>' + esc(MI2_LABELS[f.key] || f.label) + req + '</span>' + badge + '</label>'
        + control + '</div>';
}

function mi2Header(d) {
    var pills = [];
    if (d && d.name) pills.push('<span class="mi2-pill mi2-pill--primary">' + esc(d.name) + '</span>');
    // 2026-11-16：新增态险种由头部下拉承担，胶囊不重复；编辑态才用胶囊标险种
    if (!$('#formTypeWrap').is(':visible')) pills.push('<span class="mi2-pill">' + esc(MI_TYPE_LABEL[currentType] || '') + '</span>');
    var y = (d && d.payment_year) ? d.payment_year : ($('#searchYear').val() || new Date().getFullYear());
    pills.push('<span class="mi2-pill">' + esc(String(y)) + ' 年度</span>');
    // 居民医保编辑页不再展示序号胶囊（2026-11-11）；seq_no 隐藏载荷保留，提交取值/保存链路不变。
    $('#formPills').html(pills.join(''));
    var items = $.map(MI2_META, function (m) {
        // 职工/其他表单仍含可编辑「异常说明」，元数据条不重复展示
        if (currentType !== 'resident' && m.key === 'exception_note') return null;
        var v = d ? (d[m.key] || '') : '';
        var dot = 'mi2-metadot--slate';
        if (m.dot === 'auto') dot = (v && v !== '无') ? 'mi2-metadot--amber' : 'mi2-metadot--emerald';
        var valHtml = '<strong class="mi2-meta__val' + (m.mono ? ' mi2-meta__val--mono' : '') + '">' + esc(v || '—') + '</strong>';
        if (m.dict && v) valHtml = '<strong class="mi2-meta__val" id="mi2Meta_' + m.key + '">' + esc(v) + '</strong>';
        return '<div class="mi2-meta__item"><span class="mi2-metadot ' + dot + '"></span><span>' + esc(m.label) + '：</span>' + valHtml + '</div>';
    });
    $('#formMetaBar').html('<div class="mi2-meta__list">' + items.join('') + '</div>'
        + '<div class="mi2-meta__legend">带 <span class="mi2-star">*</span> 号为必填业务字段</div>');
    // 数据来源为字典值 → 就绪后替换为标签（未命中原值兜底）
    if (d && d.data_source) dictStore.ready(function () {
        var lbl = dictStore.label('医保数据来源', d.data_source);
        if (lbl) $('#mi2Meta_data_source').text(lbl);
    });
}

function renderFormFieldsV2(after) {
    var byKey = {};
    $.each(catalogFields('resident'), function (_, f) { byKey[f.key] = f; });
    var html = '';
    $.each(MI2_SECTIONS, function (_, s) {
        var cells = '';
        $.each(s.rows, function (_, row) {
            $.each(row, function (_, cell) {
                if (cell.summary) {
                    cells += '<div class="mi2-span2"><div class="mi2-summary" id="mi2Summary">'
                        + '<div class="mi2-summary__left"><span class="mi2-dot mi2-dot--amber" id="mi2StatusDot"></span>'
                        + '<span class="mi2-summary__text" id="mi2StatusText">当前逻辑：未缴费 · 个人自缴 · 正常参保不核减</span></div>'
                        + '<span class="mi2-summary__tag">逻辑正常</span></div></div>';
                    return;
                }
                var key = cell.k || cell, span = cell.span || 0;
                var f = byKey[key];
                if (!f) return;
                cells += mi2Field(f, span);
            });
        });
        html += '<section class="mi2-section"><div class="mi2-sechead">'
            + '<span class="mi2-sechead__bar"></span><h4 class="mi2-sechead__title">' + esc(s.title) + '</h4>'
            + (s.note ? '<span class="mi2-sechead__note">' + esc(s.note) + '</span>' : '<span class="mi2-sechead__line"></span>')
            + '</div><div class="mi2-grid' + (s.note ? ' mi2-grid--end' : '') + '">' + cells + '</div></section>';
    });
    $.each(MI2_META_KEYS, function (_, k) {
        html += '<input type="hidden" name="' + escAttr(k) + '">';
    });
    $('#formFields').addClass('mi2-body').html(html);
    var pending = 0, fired = false;
    function finish() { if (!fired && pending === 0) { fired = true; if (after) after(); } }
    $('#formFields .form-dict').each(function () {
        pending++;
        dictSelect($(this).data('dict'), $(this), undefined, function () { pending--; finish(); });
    });
    finish();
}

// v2 联动状态机（规范 §6）：否→清空+锁定+角标「已锁定」；是→解锁+空值赋默认+角标「必填项」
function mi2Sync() {
    $.each(MI2_COND, function (key, c) {
        var $sel = $('#formFields [name="' + key + '"]');
        var locked = $('#formFields [name="' + c.trigger + '"]').val() !== '是';
        $sel.prop('disabled', locked);
        // customSelect 触发框随原生 disabled 联动（锁定态不可点开浮层）
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
    mi2Summary();
}

// 状态摘要框实时计算（规范 §6.3）：圆点 玫红=核减中 ＞ 翠绿=已缴/代缴 ＞ 琥珀=未缴自缴
function mi2Summary() {
    var paid = $('#formFields [name="is_paid"]').val() === '是';
    var gov = $('#formFields [name="is_state_paid"]').val() === '是';
    var ded = $('#formFields [name="is_deductible"]').val() === '是';
    var govId = $('#formFields [name="state_paid_identity"]').val() || '待选身份';
    var dedR = $('#formFields [name="deduction_reason"]').val() || '待选';
    $('#mi2StatusText').text('当前逻辑：' + (paid ? '已缴费' : '未缴费') + ' · '
        + (gov ? '国家代缴（' + govId + '）' : '个人自缴') + ' · '
        + (ded ? '申请核减（原因：' + dedR + '）' : '正常参保不核减'));
    $('#mi2StatusDot').attr('class', 'mi2-dot ' + (ded ? 'mi2-dot--rose' : (paid || gov) ? 'mi2-dot--emerald' : 'mi2-dot--amber'));
}

function miCondDispatch() {
    if ($('#formFields .mi2-grid').length) mi2Sync(); else miSyncCondReq();
}
$(document).on('change', '#formFields [name="is_state_paid"], #formFields [name="is_deductible"]', miCondDispatch);
$('#formFields').on('change', '[name="is_paid"], [name="state_paid_identity"], [name="deduction_reason"]', function () {
    if ($('#formFields .mi2-grid').length) mi2Summary();
});
$('#miResetBtn').on('click', function () {
    if (typeof mi2Restore !== 'function') return;
    mi2Restore();
    showToast('已恢复初始状态', 'success');
});

// 联动必填（职工/其他 tab 沿用）：是否国家代缴=是 → 国家代缴身份；是否可核减=是 → 核减原因
// （居民医保 v2 走上方 miCondDispatch → mi2Sync 状态机：锁定/清空/角标/摘要框）
function miSyncCondReq() {
    var st = $('#formFields [name="is_state_paid"]').val() === '是';
    var dd = $('#formFields [name="is_deductible"]').val() === '是';
    $('#formFields [name="state_paid_identity"]').closest('.sc-field')
        .toggleClass('mi-req', st).find('.sc-input,.sc-select').prop('required', st);
    $('#formFields [name="deduction_reason"]').closest('.sc-field')
        .toggleClass('mi-req', dd).find('textarea,.sc-input,.sc-select').prop('required', dd);
}
$('#formFields').on('input change', '.sc-input, .sc-select, .sc-textarea', function () {
    $(this).closest('.sc-field').removeClass('mi-error').find('.mi-err-text').remove();
});

function listColumns() {
    // 2026-11-16：三分类合并为单表 → 列 = 「险种」+ 原居民列序（三类共用同一列集合；
    // 职工/其他专属字段仍在编辑弹窗内按险种目录渲染，不挤占列表宽度）
    var cols = [{field: '__index', title: '#'}];
    function decorate(c, key) {
        var f = null;
        $.each(catalogFields('resident'), function (_, x) { if (x.key === key) f = x; });
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
    cols.push({
        field: 'insurance_type', title: '险种',
        dict: {resident: '居民医保', employee: '职工医保', other: '其他人员'},
        tag: true, tagMap: {resident: 'primary', employee: 'info', other: 'muted'}
    });
    [['name', '姓名'], ['id_card', '身份证号'], ['gender', '性别'], ['village_group', '所在组'],
     ['insurance_status', '参保情况'], ['benefit_cert_status', '待遇认证状态'],
     ['link_status', '关联状态'], ['is_paid', '是否已缴'], ['verify_status', '核对状态']
    ].forEach(function (p) { cols.push(decorate({field: p[0], title: p[1]}, p[0])); });
    return cols;
}

function renderTableHead(cols) {
    var h = '';
    $.each(cols, function (_, c) { if (c.title) h += '<th class="sc-table__th">' + esc(c.title) + '</th>'; });
    h += '<th class="sc-table__th">操作</th>';
    $('#tableHead').html(h);
}

function buildList() {
    $('#batchHost').empty();    // 2026-11-02 修复：人群切换重建实例前清掉旧批量条（否则累积常驻）
    var cols = listColumns();
    renderTableHead(cols);      // 表头也由目录派生；勾选列由 crudTable 自行 prepend
    $('#tableHead').closest('table').attr('data-mi-type', 'all');   // 列宽规格选择器锚点（2026-11-16 单表）
    // 2026-11-02 规范：状态三组语义配色（关联/核对 = 琥珀·绿文字；已缴 = 绿/红胶囊由 tag 承担）
    cols = cols.map(function (c) {
        if (c.field === 'link_status') c.colormap = { '已关联': '#0F7B3F', '未关联': '#B26A00', '未关联居民': '#B26A00' };
        if (c.field === 'verify_status') c.colormap = { '已核对': '#0F7B3F', '待核对': '#B26A00' };
        return c;
    });
    return crudTable({
        endpoint: '/medical-insurance/api/list',
        batchBar: '#batchHost',        // 2026-11-02 规范：批量条置于名册卡顶部（31 高）
        emptyColspan: cols.length + 2,
        emptyText: TEXT.empty,
        // 028 交互层：跨页多选 + 批量已缴（既有端点）+ 双击编辑 + 列显隐
        selection: true,
        dblclickEdit: true,
        columnToggle: true,
        batchActions: [
            {label: '批量标记已缴', url: '/medical-insurance/api/batch-paid', confirm: TEXT.confirmPaid, cls: 'btn-dark'}
        ],
        pagesOf: function (r) { return r.pages || 1; },
        onLoaded: function (r) {
            loadStats();
            // 067 分页统计（sc-pagination__stat）；单位「人」对齐样图
            $('#pageStat').text('共 ' + (r.total || 0).toLocaleString('zh-CN') + ' 人 · 每页 20 条');
        },
        columns: cols,
        rowActions: [
            {name: 'edit', onClick: function (x) { openEditMedical(x.id); }},
            {name: 'delete', confirm: TEXT.confirmDelete, url: function (x) { return '/medical-insurance/api/delete/' + x.id; }}
        ]
    });
}

function reloadList(p) { if (list) list.reload(p); }

// 2026-11-16：分类页签 → 工具栏险种筛选。单表实例只建一次，筛选变化只重载数据；
// 「下载模板」跟随所选险种（筛「全部」时回落居民医保，模板字段集由后端目录决定）。
function syncTemplateLink() {
    $('#templateLink').attr('href', '/medical-insurance/api/template?insurance_type=' + encodeURIComponent(defaultType()));
}

$('#insuranceTypeInput').on('change', function () {
    currentType = defaultType();
    syncTemplateLink();
    reloadList(1);
});

$('#searchForm').submit(function (e) { e.preventDefault(); reloadList(1); });
$('#searchYear').on('change', function () { reloadList(1); $('#statsYear').val($(this).val()); });
// 2026-11-02：页头年度下拉联动列表过滤；「查看年度看板」开合统计行；重置
$('#statsYear').on('change', function () {
    $('#searchYear').val($(this).val());
    reloadList(1);
    loadStats();
});
$('#btnResetFilters').on('click', function () {
    $('#searchKeyword').val('');
    $('#searchForm select').val('');
    $('#searchYear').val($('#statsYear').val() || new Date().getFullYear());
    currentType = defaultType();       // 险种同被清空 → 「全部险种」，新增默认回落居民
    syncTemplateLink();
    reloadList(1);
});

function fieldVal(key) { return $('#saveForm [name="' + key + '"]').val() || ''; }
function setFieldVal(key, v) {
    var $f = $('#saveForm [name="' + key + '"]');
    if (!$f.length) return;
    $f.val(v);
    // 054-R2 P2-2：字典/选项下拉可能已被 customSelect 包裹，程序化赋值须同步触发框
    if ($f.hasClass('cs-native')) $f.trigger('refresh.cs');
}

function lookupResident() {
    let name = $.trim(fieldVal('name')), id = $.trim(fieldVal('id_card'));
    if (!name && !id) return;
    $.getJSON('/resident/api/lookup', {name: name, id_card: id}, function (r) {
        if (!r.success || !r.data || !r.data.length) return;
        let d = r.data[0];
        $.each({name: 'name', id_card: 'id_card', gender: 'gender', village_group: 'village_group',
                address: 'address', contact_phone: 'phone', mobile_phone: 'phone',
                household_name: 'household_name', household_property: 'household_property'}, function (k, src) {
            if (d[src]) setFieldVal(k, d[src]);
        });
        showToast(TEXT.found, 'success');
    });
}
$('#saveForm').on('blur', '[name="name"],[name="id_card"]', lookupResident)
    .on('keydown', '[name="name"],[name="id_card"]', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); lookupResident(); }
    });

var MI_TYPE_LABEL = { resident: '居民医保', employee: '职工医保', other: '其他人员' };

// 2026-11-16：险种由页签改为弹窗头部下拉 → 装配统一走 renderForType（字段集合随险种切换）。
// 编辑态险种不可改：隐藏下拉、以头部胶囊显示（改险种会换字段集合，半途改会丢已填值）。
function formType() { return $('#formTypeSel').val() || 'resident'; }

function renderForType(type, fill) {
    currentType = type;
    $('#formInsuranceType').val(type);
    if (type === 'resident') renderFormFieldsV2(fill);
    else renderFormFields(type, fill);
}

function initNewForm() {
    $('#saveForm')[0].reset();
    $('#formInsuranceType').val(currentType);
    setFieldVal('payment_year', $('#searchYear').val() || new Date().getFullYear());
    setFieldVal('data_source', '手工新增');
    mi2Header(null);
    if (currentType === 'resident') mi2Sync(); else miSyncCondReq();
}

function openAddMedical() {
    $('#editId').val(0);
    $('#formTitle').text(TEXT.addTitle);
    mi2Record = null;
    $('#formTypeSel').val(defaultType()).prop('disabled', false);
    $('#formTypeWrap').show();
    renderForType(defaultType(), function () { initNewForm(); mi2Restore = initNewForm; });
    getModal('formModal').show();
}

$('#formTypeSel').on('change', function () {
    if (String($('#editId').val()) !== '0') return;   // 编辑态下拉已隐藏，双保险
    renderForType(formType(), function () { initNewForm(); mi2Restore = initNewForm; });
});

function openEditMedical(id) {
    $.getJSON('/medical-insurance/api/detail/' + id, function (r) {
        if (!r.success) { showToast(r.msg, 'danger'); return; }
        let d = r.data;
        currentType = d.insurance_type || 'resident';
        $('#editId').val(d.id);
        $('#formTitle').text(TEXT.editTitle);
        mi2Record = d;
        $('#formTypeSel').val(currentType).prop('disabled', true);
        $('#formTypeWrap').hide();
        function fill() {
            $('#saveForm')[0].reset();
            $('#formInsuranceType').val(currentType);
            Object.keys(d).forEach(function (k) { setFieldVal(k, d[k]); });
            mi2Header(d);
            if (currentType === 'resident') mi2Sync(); else miSyncCondReq();
        }
        renderForType(currentType, function () { fill(); mi2Restore = fill; });
        getModal('formModal').show();
    });
}

// 提交校验（2026-11-02 规范）：必填为空 → 描边 #E7000B + 12 号错误文案，阻断提交
function miValidateRequired() {
    $('#formFields .mi-error').removeClass('mi-error').find('.mi-err-text').remove();
    var bad = [];
    $('#formFields [required]').each(function () {
        if (!String($(this).val() || '').trim()) bad.push(this);
    });
    $.each(bad, function (_, el) {
        var $f = $(el).closest('.sc-field');
        $f.addClass('mi-error');
        $f.append('<span class="mi-err-text">该项为必填项</span>');
    });
    if (bad.length) { if (bad[0]) bad[0].focus(); return false; }
    return true;
}

$('#saveForm').submit(function (e) {
    e.preventDefault();
    if (!miValidateRequired()) return;
    $.post('/medical-insurance/api/save', $(this).serialize(), function (r) {
        if (r.success) { getModal('formModal').hide(); showToast(r.msg, 'success'); reloadList(); }
        else showToast(r.msg, 'danger');
    });
});

function exportMedical() { window.location.href = '/medical-insurance/api/export?' + $('#searchForm').serialize(); }
function openImportMedical() {
    let y = $('#searchYear').val() || new Date().getFullYear();
    $('#importBatchInput').val(y + '年医保部门下发名单');
    $('#importType').val(defaultType());   // 2026-11-16：导入险种跟随筛选，默认居民医保
    syncImportTemplateLink();              // 2026-11-19：弹窗内模板下载/险种徽章跟随所选险种
    if (window.__miImpReset) window.__miImpReset();   // 弹窗打开回到初始态（不残留旧文件）
    getModal('importModal').show();
}

$('#importForm').submit(function (e) {
    e.preventDefault();
    var fileEl = document.getElementById('miImportFile') || this.querySelector('input[type="file"]');
    var file = fileEl && fileEl.files && fileEl.files[0];
    var itype = $('#importType').val() || 'resident';
    var batchName = ($('#importBatchInput').val() || '').trim();
    if (window.runDomainBatchImport && file) {
        var $submitBtn = $('#importForm button[type="submit"]').prop('disabled', true);
        window.runDomainBatchImport({
            prefix: '/medical-insurance',
            file: file,
            uploadFormFields: { insurance_type: itype },
            extraPayload: { insurance_type: itype, import_batch: batchName },
            onProgress: function (pct, text) {
                $submitBtn.text('导入中 ' + pct + '%');
            },
            onDone: function (rep) {
                $submitBtn.prop('disabled', false).text('开始导入');
                getModal('importModal').hide();
                showToast('医保导入完成：新增 ' + (rep.inserted_rows || 0) + '，更新 ' + (rep.updated_rows || 0) +
                    '，跳过 ' + (rep.skip_rows || 0) + '，失败 ' + (rep.fail_rows || 0), 'success');
                loadOptions(function () { reloadList(1); });
            },
            onError: function (msg) {
                $submitBtn.prop('disabled', false).text('开始导入');
                showToast(msg || '导入失败', 'danger');
            }
        });
        return;
    }
    let fd = new FormData(this);
    fd.append('insurance_type', itype);
    $.ajax({url: '/medical-insurance/api/import', method: 'POST', data: fd, processData: false, contentType: false,
        success: function (r) {
            if (r.success) { getModal('importModal').hide(); showToast(r.msg, 'success'); loadOptions(function () { reloadList(1); }); }
            else showToast(r.msg, 'danger');
        }});
});

// 2026-11-19 换壳配套：弹窗内模板下载/险种徽章跟随 #importType；投放区（点击/拖拽/暂存）
// 与党员管理 impModal 同款交互（共享组件 css/pages/import_modal_v2.css）。
// #miImportFile 仍是真实 file input（name=file），#importForm 提交链路（FormData →
// /medical-insurance/api/import）不变。
var MI_TYPE_LABELS = { resident: '居民医保', employee: '职工医保', other: '其他人员' };
function syncImportTemplateLink() {
    var t = $('#importType').val() || 'resident';
    $('#modalTemplateLink').attr('href', '/medical-insurance/api/template?insurance_type=' + encodeURIComponent(t));
    $('#miImpTplType').text(MI_TYPE_LABELS[t] || '居民医保');
}
$('#importType').on('change', syncImportTemplateLink);

$(function () {
    var $impInput = $('#miImportFile');
    var $impDrop = $('#miImpDrop');
    var $impStaged = $('#miImpStaged');
    var $impSubmit = $('#miImportSubmit');
    if (!$impDrop.length) return;
    var fmtSize = function (b) {
        if (b < 1024) return b + ' B';
        if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
        return (b / (1024 * 1024)).toFixed(2) + ' MB';
    };
    var stage = function (f) {
        $('#miImpStagedName').text(f.name);
        $('#miImpStagedMeta').text(fmtSize(f.size) + ' ·');
        $impDrop.addClass('d-none');
        $impStaged.removeClass('d-none');
        $impSubmit.prop('disabled', false);
    };
    var resetStaged = function () {
        $impInput.val('');
        $impStaged.addClass('d-none');
        $impDrop.removeClass('d-none');
        $impSubmit.prop('disabled', true);
    };
    $impDrop.on('click', function () { $impInput.trigger('click'); })
        .on('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $impInput.trigger('click'); }
        })
        .on('dragover', function (e) { e.preventDefault(); $(this).addClass('is-drag'); })
        .on('dragleave drop', function () { $(this).removeClass('is-drag'); })
        .on('drop', function (e) {
            e.preventDefault();
            var dt = e.originalEvent && e.originalEvent.dataTransfer;
            if (dt && dt.files && dt.files.length) {
                $impInput[0].files = dt.files;
                stage(dt.files[0]);
            }
        });
    $impInput.on('change', function () { if (this.files[0]) stage(this.files[0]); });
    $('#miImpReselect').on('click', function () { $impInput.trigger('click'); });
    $('#miImpRemove').on('click', resetStaged);
    window.__miImpReset = resetStaged;
});

function generateFromResidents() {
    if (!confirm(TEXT.confirmGen)) return;
    $.post('/medical-insurance/api/generate-from-residents', {payment_year: $('#searchYear').val(), village_group: $('#searchGroup').val()}, function (r) {
        if (r.success) { showToast(r.msg, 'success'); loadOptions(function () { reloadList(1); }); }
        else showToast(r.msg, 'danger');
    });
}

window.openAddMedical = openAddMedical; window.openEditMedical = openEditMedical; window.exportMedical = exportMedical;
window.openImportMedical = openImportMedical; window.generateFromResidents = generateFromResidents;
window.refreshStatsByYear = refreshStatsByYear;

$(function () {
    loadOptions(function () {
        list = buildList();
        list.reload(1);
        syncTemplateLink();
    });
});
})();
