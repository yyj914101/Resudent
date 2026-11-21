(function () {
var cp = 1, ps = 20, tp = 0;
var currentDetailData = null;
var listReqId = 0;

function loadList(p) {
    cp = p; var pms = {page:p, limit:ps};
    var n=$('#searchName').val(), ic=$('#searchIdCard').val(), t=$('#searchType').val(), ss=$('#searchSupport').val();
    if(n) pms.name=n; if(ic) pms.id_card=ic; if(t) pms.dibao_type=t; if(ss) pms.support_status=ss;
    var myId = ++listReqId;
    $('#dibaoBody').html('<tr><td colspan="18" class="text-center py-4"><div class="spinner-border spinner-border-sm text-primary"></div> 加载中...</td></tr>');
    $.get('/dibao/api/list', pms, function(r) {
        if(myId !== listReqId) return;
        if(!r.success) return; var d=r.data, items=d.data||[]; tp=d.pages||0;
        $('#pageStat').text('共 ' + d.total + ' 条 · 每页 ' + ps + ' 条');
        var h='';
        if(!items.length) h='<tr><td colspan="18" class="text-center text-muted py-4">暂无数据</td></tr>';
        else $.each(items, function(i, r){
            // 067 ADR-024②：保障状态徽章（真实字段 support_status 渲染）
            var ssBadge = r.support_status === '复审中' ? 'sc-badge--warning'
                : (r.support_status === '待核实' ? 'sc-badge--muted' : 'sc-badge--success');
            h+='<tr data-id="'+r.id+'" style="cursor:pointer">'+
            '<td>'+esc(r.region)+'</td><td><strong>'+esc(r.household_name)+'</strong></td><td>'+esc(r.household_id_card)+'</td><td>'+esc(r.name)+'</td>'+
            '<td>'+esc(r.id_card)+'</td><td class="sc-table__td--center">'+esc(r.is_head)+'</td><td>'+esc(r.relationship)+'</td><td>'+r.start_date+'</td>'+
            '<td class="sc-table__td--center">'+esc(r.dibao_type)+'</td><td>'+esc(r.sub_category)+'</td><td class="sc-table__td--center">'+r.beneficiary_count+'</td>'+
            '<td class="sc-table__td--center">'+Number(r.household_amount).toFixed(2)+'</td><td>'+esc(r.payment_month)+'</td>'+
            '<td>'+esc(r.account_holder)+'</td><td>'+esc(r.bank_card)+'</td>'+
            '<td>'+esc(r.is_deceased)+'</td>'+
            '<td><span class="sc-badge '+ssBadge+'">'+esc(r.support_status||'已认定')+'</span></td>'+
            '<td><span class="sc-row-actions"><button class="sc-btn sc-btn--text is-danger" onclick="event.stopPropagation();del('+r.id+')"><i class="bi bi-trash"></i></button></span></td></tr>';
        });
        $('#dibaoBody').html(h);
        $('#dibaoBody tr[data-id]').on('click', function(){openDetail($(this).data('id'));});
        renderPagination('#pagination', d.pages, d.page, loadList);
    });
}

function openDetail(id) {
    $.get('/dibao/api/detail/'+id, function(r) {
        if(!r.success) return; var d=r.data;
        currentDetailData = d;
        // 2026-11-11 详情 v2：ui1.md 只读规范（四层结构/四列栅格/风险横幅/隐私脱敏），原型 code_artifact1.html。
        // 权限口径沿用：身份证类字段（户主/开户人）仅在启动口令解锁（d.unlocked）后渲染，
        // 锁定态该格显示「—」且不提供复制/脱敏；成员身份证号与银行账号始终显示。
        var emeraldDot = '<span class="d2-dot d2-dot--emerald"></span>';
        var ok = d.support_status === '已认定';
        var deceased = d.is_deceased === '是';
        var months = d2MonthsBetween(d.start_date, d.payment_month);
        var sameAddr = !!(d.address && d.address === d.registered_address);
        // T1 身份标签组（首枚=档案存档编号：入库分配的主键 id，唯一且不随列表排序变化）
        var tags = [];
        if (d.id) tags.push({t: '序号: #' + d.id, tone: 'slate'});
        if (d.is_head === '是') tags.push({t: d.relationship || '户主本人', tone: 'amber'});
        if (d.dibao_type) tags.push({t: '低保' + d.dibao_type, tone: 'blue'});
        if (d.health_status) tags.push({t: d.health_status, tone: 'orange'});
        if (deceased) tags.push({t: '死亡成员', tone: 'red', ping: true});
        // T2 业务核查预警（死亡成员 × 已认定 × 存在发放记录）
        var risk = null;
        if (deceased && ok && d.payment_month) {
            risk = { html: '<strong>业务核查预警：</strong>当前人员标记为 <span class="d2-risk__u">死亡成员</span>，但保障状态仍为 '
                + '<span class="d2-risk__u">已认定</span> 且存在 <span class="d2-mono">' + esc(d.payment_month) + '</span> 发放记录（¥'
                + Number(d.household_amount || 0).toFixed(2) + '），请及时核实并办理停发或自然减员手续。',
                action: { id: 'd2AuditBtn', label: '发起减员核查',
                    onClick: function () { showToast('请及时办理停发或自然减员手续，可从「待办提醒」跟进', 'success'); } } };
        }
        // 敏感格（锁定态显示「—」）
        var idMainCell = (idUnlocked() && d.id_card)
            ? '<span class="d2-mono d2-val--medium" id="d2s-id-main">' + esc(d.id_card) + '</span>' + d2CopyBtn(d.id_card, '身份证号')
            : '<span class="d2-mute">—</span>';
        var accIdCell = (idUnlocked() && d.account_holder_id_card)
            ? '<span class="d2-mono d2-val--medium" id="d2s-id-bank">' + esc(d.account_holder_id_card) + '</span>'
              + (d.account_holder_id_card === d.id_card ? '<span class="d2-chip d2-chip--slate">同本人</span>' : '')
            : '<span class="d2-mute">—</span>';
        var bank = d2BankDisplay(d.bank_card);
        var bankCell = d.bank_card
            ? '<span class="d2-mono d2-val--strong" id="d2s-bank">' + esc(bank.display) + '</span>' + d2CopyBtn(bank.copy, '银行账号')
            : '<span class="d2-mute">—</span>';
        var sensitive = [];
        if (idUnlocked() && d.id_card) sensitive.push({id: 'd2s-id-main', raw: d.id_card, masked: d2MaskIdCard(d.id_card)});
        if (idUnlocked() && d.account_holder_id_card) sensitive.push({id: 'd2s-id-bank', raw: d.account_holder_id_card, masked: d2MaskIdCard(d.account_holder_id_card)});
        if (d.bank_card) sensitive.push({id: 'd2s-bank', raw: bank.display, masked: bank.masked});
        // T3 三段式
        var supVal = ok ? emeraldDot + '<span class="d2-val--emerald">' + esc(d.support_status) + '</span>'
            : '<span class="d2-val--medium">' + esc(d.support_status || '—') + '</span>';
        var kpis = [
            {label: '保障状态', html: supVal},
            {label: '户救助金额 (月)', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d.household_amount || 0).toFixed(2)) + '</span>'},
            {label: '最新发放月份', html: d.payment_month ? '<span class="d2-mono">' + esc(d.payment_month) + '</span>' : '<span class="d2-mute">—</span>'}
        ];
        var sections = [
            {title: '个人与户籍信息', note: '（已合并原户主与成员重复项）', rows: [
                [{label: '成员/户主姓名', html: d2ValStrong(d.name) + (d.relationship ? '<span class="d2-chip d2-chip--slate">与户主关系：' + esc(d.relationship) + '</span>' : '')},
                 {label: '成员/户主身份证号', html: idMainCell},
                 {label: '健康状况 / 残疾类型', html: (d.health_status ? '<span class="d2-val--orange">' + esc(d.health_status) + '</span>' : '<span class="d2-mute">—</span>') + '<span class="d2-mute">/</span><span class="d2-mute">残疾类型：' + esc(d.disability_type || '— (无)') + '</span>'},
                 {label: '建档立卡贫困对象', html: d2ValMedium(d.poverty_registered || '否')}],
                [{label: '是否死亡成员', html: deceased ? '<span class="d2-chip d2-chip--red">是（需核减）</span>' : '<span class="d2-val--medium">否</span>'},
                 {label: '是否户主 / 所属村组', html: d2ValMedium((d.is_head || '—') + (d.region ? ' · ' + esc(d.region) : ''))},
                 {label: '家庭地址 / 户籍地址', span2: true, stamp: sameAddr ? '<span class="d2-stamp">两址一致</span>' : '',
                  html: '<div class="d2-chipbox"><span class="d2-chipbox__text">' + esc(d.address || '—') + '</span>' + d2CopyBtnText(d.address, '地址') + '</div>'}]
            ]},
            {title: '保障与认定信息', rows: [
                [{label: '救助细类 / 救助类别', html: d2ValMedium(d.sub_category || '—') + (d.dibao_type ? '<span class="d2-chip d2-chip--blue">' + esc(d.dibao_type) + '</span>' : '')},
                 {label: '享受人口数', html: d2ValStrong((d.beneficiary_count || 1) + ' 人')},
                 {label: '户救助金额（月）', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d.household_amount || 0).toFixed(2)) + '</span>'},
                 {label: '保障状态', html: supVal}],
                [{label: '开始享受时间', html: d2ValMono(d.start_date)},
                 {label: '最近变更时间', html: d2ValMono(d.change_time)},
                 {label: '最新发放月份', html: d2ValMono(d.payment_month)},
                 {label: '连续保障时长 (系统核算)', html: months ? '<span class="d2-chip d2-chip--slate">已连续享受 ' + months + ' 个月</span>' : '<span class="d2-mute">—</span>'}]
            ]},
            {title: '发放账户信息', card: true, cardNote: '一卡通代发账户 · 账号已做四位分段排版', rows: [
                [{label: '开户人 / 开户人身份', html: d2ValStrong(d.account_holder) + (d.account_holder_identity ? '<span class="d2-chip d2-chip--white">' + esc(d.account_holder_identity) + '</span>' : '')},
                 {label: '开户人身份证号', html: accIdCell},
                 {label: '开户银行种类', html: (d.bank_type ? '<span class="d2-dot d2-dot--blue"></span>' : '') + '<span class="d2-val--medium">' + esc(d.bank_type || '—') + '</span>'},
                 {label: '银行账号', html: bankCell}]
            ]}
        ];
        d2Render({
            modal: '#dibaoDetailModal', body: '#dibaoDetailBody', footerSel: '#dibaoDetailModal',
            name: d.name, tags: tags,
            subtitle: '<span>' + esc(d.sub_category || '低保') + ' · 低保详情</span><span class="d2-subtitle__sep">|</span><span>所属所在组：<strong>' + esc(d.region || '—') + '</strong></span>',
            kpis: kpis, risk: risk, sections: sections, sensitive: sensitive
        });
        $('#dibaoDetailBtns').html(
            '<button type="button" class="d2-btn d2-btn--danger" onclick="del(' + d.id + ')"><i class="bi bi-trash"></i> 删除</button>'
            + '<button type="button" class="d2-btn d2-btn--ghost" data-bs-dismiss="modal">关闭</button>'
            + '<button type="button" class="d2-btn d2-btn--primary" onclick="openEdit()"><i class="bi bi-pencil"></i> 编辑资料</button>');
        $('#dibaoDetailModal').modal('show');
    });
}

function idUnlocked() { return !!currentDetailData.unlocked; }

function openEdit() {
    if (!currentDetailData) return;
    var d = currentDetailData;
    $('#dibaoFormId').val(d.id);
    $('#dibaoFormTitle').text('编辑低保信息');
    $('#dibaoFormMeta').text((d.name || '') + ' · ' + (d.sub_category || '低保') + ' · 修改后保存生效');
    // 填充所有表单字段
    ['region','household_name','household_id_card','name','id_card','is_head','relationship',
     'dibao_type','sub_category','payment_month','account_holder','account_holder_identity',
     'account_holder_id_card','bank_card','bank_type','address','registered_address',
     'health_status','disability_type','poverty_registered','is_deceased','support_status','remark'].forEach(function(f) {
        $('[name="'+f+'"]').val(d[f] || '');
    });
    $('[name="beneficiary_count"]').val(d.beneficiary_count || 1);
    $('[name="household_amount"]').val(d.household_amount || 0);
    $('[name="start_date"]').val(d.start_date || '');
    $('[name="change_time"]').val(d.change_time || '');
    $('#dibaoDetailModal').one('hidden.bs.modal', function () { $('#dibaoFormModal').modal('show'); });
    $('#dibaoDetailModal').modal('hide');
    loadDibaoDictOptions(function() {
        $('[name="dibao_type"]').val(d.dibao_type || '');
        $('[name="sub_category"]').val(d.sub_category || '');
    });
}

function openAdd() {
    $('#dibaoForm')[0].reset(); $('#dibaoFormId').val(''); loadDibaoDictOptions(); $('#dibaoFormModal').modal('show');
    $('#dibaoFormTitle').text('新增低保信息'); $('#dibaoFormMeta').text('新增低保记录');
}
$('#dibaoForm').submit(function(e) {
    e.preventDefault();
    $.post('/dibao/api/save', $(this).serialize(), function(r) {
        if(r.success){showToast('保存成功','success');$('#dibaoFormModal').modal('hide');loadList(1);}
        else showToast(r.msg,'danger');
    });
});
function del(id) {
    if(!confirm('确定删除？')) return;
    $.post('/dibao/api/delete/'+id, function(r){
        if(r.success){showToast('删除成功','success');$('#dibaoDetailModal').modal('hide');loadList(1);}
        else showToast(r.msg,'danger');
    });
}
function exportData() {
    var pms = {};
    var n=$('#searchName').val(), ic=$('#searchIdCard').val(), t=$('#searchType').val();
    if(n) pms.name=n; if(ic) pms.id_card=ic; if(t) pms.dibao_type=t;
    var qs = $.param(pms);
    window.location.href='/dibao/api/export' + (qs ? '?' + qs : '');
}

function loadDibaoDictOptions(done) {
    var pending = 2;
    function next() {
        pending -= 1;
        if (pending === 0 && done) done();
    }
    dictSelect('救助类别', 'select[name="dibao_type"]', '请选择', next);
    dictSelect('救助细类', 'select[name="sub_category"]', '请选择', next);
}

function loadDibaoSearchDict() {
    dictSelect('救助类别', '#searchType', '全部救助类别');
}

function doImport() {
    importWizard({fileSel:'#importFile', resultSel:'#importResult', endpoint:'/dibao/api/import',
        extraData:{mode:$('input[name="importMode"]:checked').val() || 'append'},
        onDone:function(){  loadList(1); }});
}

$(function(){loadDibaoSearchDict(); loadDibaoDictOptions();  loadList(1);});

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ del: del, doImport: doImport, exportData: exportData, loadList: loadList, openAdd: openAdd, openDetail: openDetail, openEdit: openEdit }); }
})();

// 2026-11-19 换壳配套：导入弹窗投放区（app.js wireImportDropzone 全局单源）。
// #importFile 仍是真实 file input，doImport 链路不变；弹窗每次打开经 show.bs.modal 复位。
$(function () {
    wireImportDropzone({
        fileSel: '#importFile', dropSel: '#dibaoImpDrop', stagedSel: '#dibaoImpStaged',
        stagedNameSel: '#dibaoImpStagedName', stagedMetaSel: '#dibaoImpStagedMeta',
        submitBtnSel: '#impSubmit', reselectSel: '#dibaoImpReselect', removeSel: '#dibaoImpRemove',
        modalSel: '#importModal'
    });
});
