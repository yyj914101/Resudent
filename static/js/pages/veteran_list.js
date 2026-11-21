(function () {
var cp = 1, ps = 20, tp = 0;

// 062 P5：详情描述项（标签 + 值），三列栅格复用
function fieldHtml(label, value, span) {
    return '<div' + (span ? ' style="grid-column:span ' + span + '"' : '') + '>' +
        '<div class="sc-pagination__stat">' + label + '</div>' +
        '<div style="color:var(--c-ink-2);font-size:14px;word-break:break-all">' + esc(value || '-') + '</div></div>';
}

// ─── Load List ────────────────────────────────────────────────────────
function load(p) {
    cp = p;
    $('#tableBody').html('<tr><td colspan="26" class="text-center py-4" style="color:var(--c-muted)">加载中...</td></tr>');
    var q = $('#searchForm').serializeArray();
    q.push({name:'page',value:p},{name:'limit',value:ps});
    $.get('/veteran/api/list', q, function(r) {
        if (!r.success || !r.data) return;
        var d = r.data, items = d.data || [];
        tp = d.pages || 0;
        var h = '';
        if (!items.length) {
            h = '<tr><td colspan="26" class="text-center py-4" style="color:var(--c-muted)">暂无数据</td></tr>';
        } else {
            for (var i = 0; i < items.length; i++) {
                var x = items[i];
                h += '<tr data-id="' + x.id + '" style="cursor:pointer">' +
                    '<td>' + (x.serial_no || '') + '</td>' +
                    '<td>' + esc(x.name) + '</td>' +
                    '<td>' + esc(x.id_card) + '</td>' +
                    '<td>' + esc(x.gender) + '</td>' +
                    '<td>' + esc(x.nation) + '</td>' +
                    '<td>' + esc(x.politics_status) + '</td>' +
                    '<td>' + esc(x.education) + '</td>' +
                    '<td>' + esc(x.registered_residence) + '</td>' +
                    '<td>' + esc(x.grid) + '</td>' +
                    '<td>' + esc(x.home_address) + '</td>' +
                    '<td>' + esc(x.phone) + '</td>' +
                    '<td>' + esc(x.relative_name) + '</td>' +
                    '<td>' + esc(x.relative_phone) + '</td>' +
                    '<td>' + esc(x.enlist_date) + '</td>' +
                    '<td>' + esc(x.discharge_date) + '</td>' +
                    '<td>' + (x.service_years != null ? x.service_years : '') + '</td>' +
                    '<td>' + esc(x.service_unit) + '</td>' +
                    '<td>' + esc(x.service_branch) + '</td>' +
                    '<td>' + esc(x.service_rank) + '</td>' +
                    '<td>' + esc(x.discharge_cert_no) + '</td>' +
                    '<td>' + esc(x.household_reg_type) + '</td>' +
                    '<td>' + esc(x.placement_status) + '</td>' +
                    '<td>' + esc(x.retire_category || '') + '</td>' +
                    '<td>' + (x.key_group_category ? esc(x.key_group_category) : '') + '</td>' +
                    '<td>' + esc(x.remark) + '</td>' +
                    '<td><div class="sc-row-actions">' +
                    '<button type="button" class="sc-btn sc-btn--text is-danger btn-del" data-id="' + x.id + '" data-name="' + esc(x.name) + '" title="删除"><i class="bi bi-trash"></i></button>' +
                    '</div></td></tr>';
            }
        }
        $('#tableBody').html(h);

        // 2026-11-17 Master–Detail：行点击 → 详情（姓名链接与「查看」按钮入口合并）
        $('#tableBody tr[data-id]').on('click', function () { showDetail($(this).data('id')); });

        // Bind delete
        $('.btn-del').on('click', function() {
            var id = $(this).data('id'), name = $(this).data('name');
            if (!confirm('确定要删除退伍军人【' + name + '】的档案吗？\n此操作不可恢复！')) return;
            $.post('/veteran/api/delete/'+id, function(r2) {
                if (r2.success) { showToast(r2.msg, 'success'); load(cp); }
                else showToast(r2.msg, 'danger');
            });
        });

        // Pagination（R-09：手写分页 → 全局 renderPagination）
        renderPagination('#pagination', tp, cp, load);
        $('#pageStat').text('共 ' + (d.total || 0) + ' 条 · 每页 ' + ps + ' 条');
    });
}

// ─── Resident Auto-fill ──────────────────────────────────────────────
var nameTimer = null;
$('#fName').on('input', function() {
    clearTimeout(nameTimer);
    var val = $(this).val().trim();
    if (val.length < 1) { $('#nameSuggest').hide(); return; }
    nameTimer = setTimeout(function() {
        $.get('/veteran/api/lookup', {name: val}, function(r) {
            if (!r.success || !r.data || !r.data.length) { $('#nameSuggest').hide(); return; }
            var html = '';
            $.each(r.data, function(i, item) {
                html += '<button type="button" class="list-group-item list-group-item-action py-1 small" ' +
                    'data-name="' + escAttr(item.name) + '" data-id-card="' + escAttr(item.id_card) + '" ' +
                    'data-gender="' + escAttr(item.gender) + '" data-nation="' + escAttr(item.nation) + '" ' +
                    'data-phone="' + escAttr(item.phone) + '" data-address="' + escAttr(item.address) + '">' +
                    esc(item.name) + ' ' + esc(item.id_card) + ' ' + esc(item.gender) + ' ' + esc(item.nation) +
                    '</button>';
            });
            $('#nameSuggest').html(html).show();
            $('#nameSuggest button').on('click', function() {
                $('#fName').val($(this).data('name'));
                fillFromResident($(this).data());
                $('#nameSuggest').hide();
            });
        });
    }, 300);
});
$(document).off('click.vetDoc').on('click.vetDoc', function(e) {
    if (!$(e.target).closest('#fName, #nameSuggest').length) $('#nameSuggest').hide();
});

function fillFromResident(data) {
    if (data.idCard) {
        $('#fIdCard').val(data.idCard);
        autoFillIdCard(data.idCard);
    }
    if (data.gender) $('#fGender').val(data.gender);
    if (data.nation && data.nation !== '少数民族') $('#fNation').val(data.nation);
    if (data.phone) $('#fPhone').val(data.phone);
    if (data.address) {
        $('#fRegisteredResidence').val(data.address);
        $('#fHomeAddress').val(data.address);
    }
}

// ─── ID Card Auto-fill ───────────────────────────────────────────────
$('#fIdCard').on('input', function() {
    autoFillIdCard($(this).val());
});

function autoFillIdCard(val) {
    val = val.trim();
    if (val.length === 18) {
        var genderDigit = parseInt(val.charAt(16));
        $('#fGender').val(genderDigit % 2 === 1 ? '男' : '女');
    }
}

// ─── Auto-calculate Service Years ────────────────────────────────────
function calcServiceYears() {
    var enlist = $('#fEnlistDate').val();
    var disch = $('#fDischargeDate').val();
    if (enlist && disch) {
        var e = new Date(enlist + '-01');
        var d = new Date(disch + '-01');
        var years = d.getFullYear() - e.getFullYear();
        if (d.getMonth() < e.getMonth()) years--;
        if (years < 0) years = 0;
        $('#fServiceYears').val(years).prop('readonly', true);
    } else {
        $('#fServiceYears').val('');
    }
}
$('#fEnlistDate, #fDischargeDate').on('change', calcServiceYears);

// ─── Show Add ────────────────────────────────────────────────────────
function showAdd() {
    $('#saveForm')[0].reset();
    $('#editId').val('');
    $('#formModalTitle').html('<i class="bi bi-person-plus"></i> 新增退伍军人');
    $('#btnSave').html('<i class="bi bi-check-lg"></i> 保存');
    $('#fNation').val('汉族');
    $('#fServiceYears').prop('readonly', true);
    // reset() + 程序化赋值都不派发 change，自定义下拉的触发框会停在旧文本（054 复核 P1-2）
    $('#saveForm select.cs-native').trigger('refresh.cs');
    $('#formModal').modal('show');
}

// ─── Show Edit ───────────────────────────────────────────────────────
function showEdit(id) {
    $.get('/veteran/api/detail/'+id, function(r) {
        if (!r.success) { showToast('加载失败', 'danger'); return; }
        var d = r.data;
        $('#saveForm')[0].reset();
        $('#editId').val(d.id);
        $('#formModalTitle').html('<i class="bi bi-pencil"></i> 编辑退伍军人');
        $('#btnSave').html('<i class="bi bi-check-lg"></i> 更新');
        $('#fName').val(d.name);
        $('#fIdCard').val(d.id_card);
        $('#fGender').val(d.gender);
        $('#fNation').val(d.nation);
        $('#fPolitics').val(d.politics_status);
        $('#fEducation').val(d.education);
        $('#fRegisteredResidence').val(d.registered_residence);
        $('#fHomeAddress').val(d.home_address);
        $('#fPhone').val(d.phone);
        $('#fRelativeName').val(d.relative_name);
        $('#fRelativePhone').val(d.relative_phone);
        $('#fEnlistDate').val(d.enlist_date);
        $('#fDischargeDate').val(d.discharge_date);
        $('#fServiceYears').val(d.service_years).prop('readonly', true);
        $('#fServiceUnit').val(d.service_unit);
        $('#fServiceBranch').val(d.service_branch);
        $('#fServiceRank').val(d.service_rank);
        $('#fDischargeCertNo').val(d.discharge_cert_no);
        $('#fHouseholdReg').val(d.household_reg_type);
        $('select[name="placement_status"]').val(d.placement_status);
        $('#fRetireCategory').val(d.retire_category || '');
        $('#fKeyGroup').val(d.key_group_category);
        $('textarea[name="remark"]').val(d.remark);
        // 同上：批量 .val() 后统一刷新一次触发框（054 复核 P1-2）
        $('#saveForm select.cs-native').trigger('refresh.cs');
        $('#formModal').modal('show');
    });
}

// ─── Save ─────────────────────────────────────────────────────────────
$('#saveForm').submit(function(e) {
    e.preventDefault();
    var form = this;
    if (!form.checkValidity()) {
        form.classList.add('was-validated');
        return;
    }
    // Id card validation
    var idCard = $('#fIdCard').val().trim();
    if (idCard.length !== 18) { showToast('身份证号码必须为18位', 'danger'); return; }
    // Phone validation
    var phone = $('#fPhone').val().trim();
    if (!/^\d{7,15}$/.test(phone)) { showToast('请输入有效的手机号码', 'danger'); return; }
    var relPhone = $('#fRelativePhone').val().trim();
    if (!/^\d{7,15}$/.test(relPhone)) { showToast('请输入有效的亲属手机号码', 'danger'); return; }

    var btn = $('#btnSave');
    btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span> 保存中...');
    $.post('/veteran/api/save', $(this).serialize(), function(r) {
        if (r.success) {
            showToast('保存成功', 'success');
            $('#formModal').modal('hide');
            load(cp);
        } else {
            showToast(r.msg, 'danger');
        }
    }).always(function() {
        btn.prop('disabled', false).html('<i class="bi bi-check-lg"></i> 保存');
    });
});

// ─── Detail ──────────────────────────────────────────────────────────
function showDetail(id) {
    $('#detailModalBody').html('<div class="text-center py-5"><div class="spinner-border text-primary"></div></div>');
    $('#detailModal').modal('show');
    $.get('/veteran/api/detail/'+id, function(r) {
        if (!r.success) { showToast('加载失败', 'danger'); return; }
        var d = r.data;
        $('#detailModalTitle').text(d.name + ' - 退伍军人档案');
        var fields = [
            ['姓名', d.name], ['身份证号码', d.id_card],
            ['性别', d.gender], ['民族', d.nation],
            ['政治面貌', d.politics_status], ['文化程度', d.education],
            ['户口所在地', d.registered_residence],
            ['所在组', d.grid],
            ['家庭详细地址', d.home_address],
            ['本人联系方式', d.phone],
            ['主要亲属姓名', d.relative_name],
            ['主要亲属联系方式', d.relative_phone],
            ['入伍年月', d.enlist_date],
            ['退役年月', d.discharge_date],
            ['服役年限', d.service_years != null ? d.service_years + '年' : ''],
            ['原服役部队', d.service_unit],
            ['原服役兵种', d.service_branch],
            ['原服役职务/军衔', d.service_rank],
            ['退伍证件编号', d.discharge_cert_no],
            ['户籍性质', d.household_reg_type],
            ['安置情况', d.placement_status],
            ['退役类别', d.retire_category],
            ['重点群体类别', d.key_group_category],
            ['备注', d.remark || '-'],
        ];
        var html = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px 16px">';
        $.each(fields, function(i, f) {
            html += fieldHtml(f[0], f[1]);
        });
        html += '</div>';
        html += '<div class="sc-row-actions" style="margin-top:var(--sp-4)"><button type="button" class="sc-btn sc-btn--secondary" onclick="$(\'#detailModal\').modal(\'hide\');showEdit(' + d.id + ')"><i class="bi bi-pencil"></i> 编辑</button>' +
            '<button type="button" class="sc-btn sc-btn--text is-danger" style="margin-left:auto" onclick="if(confirm(\'确定删除【' + esc(d.name) + '】的档案吗？\\n此操作不可恢复！\')){$.post(\'/veteran/api/delete/0\'.replace(\'/0\',\'/\'+' + d.id + '),function(r2){if(r2.success){showToast(r2.msg,\'success\');$(\'#detailModal\').modal(\'hide\');load(' + cp + ');}else showToast(r2.msg,\'danger\');});}"><i class="bi bi-trash"></i> 删除</button></div>';
        $('#detailModalBody').html(html);
    });
}

// ─── Import ───────────────────────────────────────────────────────────
function showImport() { $('#importModal').modal('show'); }

$('#btnImport').on('click', function() {
    var file = $('#importFile')[0].files[0];
    if (!file) { showToast('请选择文件', 'warning'); return; }
    var btn = $(this);
    btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span> 导入中...');
    var fd = new FormData();
    fd.append('file', file);
    fd.append('mode', $('input[name="importMode"]:checked').val() || 'append');
    $.ajax({
        url: '/veteran/api/import',
        type: 'POST',
        data: fd,
        processData: false,
        contentType: false,
        success: function(r) {
            showToast(r.msg, r.success ? 'success' : 'danger');
            $('#importModal').modal('hide');
            load(1);
        },
        error: function() { showToast('导入请求失败', 'danger'); }
    }).always(function() {
        btn.prop('disabled', false).html('<i class="bi bi-upload"></i> 开始导入');
    });
});

// ─── Export with Search Params ────────────────────────────────────────
$('#searchForm').on('submit', function(e) {
    e.preventDefault();
    updateExportLink();
    load(1);
});

// 075 RC-18：紧凑工具栏单搜索框（姓名/身份证/政治面貌/兵种组合检索），300ms 防抖
var qTimer = null;
$('#q').on('input', function() {
    clearTimeout(qTimer);
    qTimer = setTimeout(function() {
        updateExportLink();
        load(1);
    }, 300);
});

$('#filterKeyGroup').on('change', function() {
    updateExportLink();
    load(1);
});

$('#vRetireCategory').on('change', function() {
    updateExportLink();
    load(1);
});

function updateExportLink() {
    var params = $('#searchForm').serialize();
    $('#exportBtn').attr('href', '/veteran/api/export?' + params);
}

function resetSearch() {
    $('#searchForm')[0].reset();
    updateExportLink();
    load(1);
}

// ─── Helper ──────────────────────────────────────────────────────────
// ─── Init ─────────────────────────────────────────────────────────────
$(function() {
    // Load edit form dropdowns（075 RC-18：筛选区改单搜索框 q，筛选下拉的字典加载移除）
    loadDict('原服役兵种', 'select[name="service_branch"]', false);
    loadDict('安置情况', 'select[name="placement_status"]', false);
    loadDict('退役重点群体', '#fKeyGroup', true);
    loadDict('退役重点群体', '#filterKeyGroup', true);
    loadDict('户籍性质', 'select[name="household_reg_type"]', false);
    loadDict('所在组', '#fGrid', false);
    loadDict('原服役部队', '#serviceUnitOptions', false);
    loadDict('原服役职务/军衔', '#serviceRankOptions', false);
    loadVeteranServiceDicts();
    dictSelect('退役类别', '#fRetireCategory', '请选择');
    dictSelect('退役类别', '#vRetireCategory', '退役类别');
    updateExportLink();
    // 062 P5：24 列宽表列显隐（序号/操作固定，其余 22 列可隐藏）
    // 075 RC-18：默认隐藏 6 列（样图 v7-14「已选 17 / 共 25 列」）+ 计数行 + 「列选择」文案
    scColToggle.init('#dataTable', {
        menuHost: '#veteranColHost',
        storageKey: 'veteran_col_hidden',
        fixed: [0, 25],
        toggleLabel: '列选择',
        countHost: '#veteranColCount',
        countSuffix: ' · 横向滚动查看全部',
        defaultHidden: [11, 16, 17, 18, 21, 24]
    });
    load(1);
});

function loadVeteranServiceDicts() {
    loadDict('\u539f\u670d\u5f79\u90e8\u961f', '#fServiceUnit', false);
    loadDict('\u539f\u670d\u5f79\u804c\u52a1/\u519b\u8854', '#fServiceRank', false);
}

function loadDict(dictName, selector, keepFirst) {
    dictStore.ready(function() {
        var sel = $(selector);
        // GLB-24：两个分支都先清空——keepFirst=false（表单内 select/datalist）此前
        // 不清空，SPA 复进页面会重复 append 同一批选项
        if (keepFirst) {
            var firstVal = sel.find('option:first').val();
            var firstText = sel.find('option:first').text();
            sel.empty().append('<option value="' + firstVal + '">' + firstText + '</option>');
        } else {
            sel.empty();
        }
        $.each(dictStore.items(dictName), function(i, item) {
            sel.append('<option value="' + escAttr(item.value) + '">' + esc(item.label) + '</option>');
        });
    });
}

// R-20：内联事件依赖函数挂 window（IIFE 隔离顶层作用域；内联 onclick/onblur 契约保留）
// 024-hotfix2：拼接事件在点击时于全局求值，本文件分页加载函数需全局可见
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ showImport: showImport, showAdd: showAdd, resetSearch: resetSearch, showDetail: showDetail, showEdit: showEdit, load: load }); }
})();


// 2026-11-19 换壳配套：导入弹窗投放区（app.js wireImportDropzone 全局单源）。
// #importFile 仍是真实 file input；导入模式读单选卡 name="importMode"（契约取值不变）。
$(function () {
    wireImportDropzone({
        fileSel: '#importFile', dropSel: '#veteranImpDrop', stagedSel: '#veteranImpStaged',
        stagedNameSel: '#veteranImpStagedName', stagedMetaSel: '#veteranImpStagedMeta',
        submitBtnSel: '#btnImport', reselectSel: '#veteranImpReselect', removeSel: '#veteranImpRemove',
        modalSel: '#importModal'
    });
});
