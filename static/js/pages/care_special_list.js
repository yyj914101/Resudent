(function () {
var cp=1,ps=20,tp=0, currentDelId=null;

function load(p){
    cp=p;
    $('#tb').html('<tr><td colspan="20" class="text-center py-4 text-muted">加载中...</td></tr>');
    $.get('/care/api/special/list',{page:p,limit:ps,name:$('#sName').val(),id_card:$('#sIdCard').val()},function(r){
        if(!r.success||!r.data) return;
        var d=r.data, items=d.data||[];
        tp=d.pages||0;
        $('#pageStat').text('共 ' + d.total + ' 条 · 每页 ' + ps + ' 条');
        var h='';
        if(items.length===0){
            h='<tr><td colspan="20" class="text-center py-4 text-muted">暂无数据</td></tr>';
        }else{
            for(var i=0;i<items.length;i++){
                var x=items[i];
                h+='<tr rid="'+x.id+'">'+
                    '<td>'+esc(x.village_group)+'</td>'+
                    '<td><strong>'+esc(x.household_name)+'</strong></td>'+
                    '<td>'+esc(x.household_id_card)+'</td>'+
                    '<td class="text-center">'+(x.beneficiary_count||'')+'</td>'+
                    '<td class="text-end">'+Number(x.guarantee_amount||0).toFixed(2)+'</td>'+
                    '<td>'+esc(x.support_mode)+'</td>'+
                    '<td>'+esc(x.support_institution)+'</td>'+
                    '<td>'+(x.start_date||'')+'</td>'+
                    '<td>'+(x.change_time||'')+'</td>'+
                    '<td>'+esc(x.bank_card)+'</td>'+
                    '<td>'+esc(x.payment_month)+'</td>'+
                    '<td>'+esc(x.account_holder)+'</td>'+
                    '<td>'+esc(x.account_holder_identity)+'</td>'+
                    '<td>'+esc(x.account_holder_id_card)+'</td>'+
                    '<td>'+esc(x.bank_type)+'</td>'+
                    '<td>'+esc(x.address)+'</td>'+
                    '<td>'+esc(x.registered_address)+'</td>'+
                    '<td>'+esc(x.phone)+'</td>'+
                    '<td>'+esc(x.self_care_ability)+'</td>'+
                    '<td><span class="sc-row-actions"><button type="button" class="sc-btn sc-btn--text is-danger btn-del" rid="'+x.id+'"><i class="bi bi-trash"></i></button></span></td>'+
                    '</tr>';
            }
        }
        $('#tb').html(h);
        // Bind row click -> detail
        $('#tb tr[rid]').on('click', function(){
            var rid=$(this).attr('rid');
            $.get('/care/api/special/detail/'+rid, function(r2){
                if(!r2.success) return;
                var d2=r2.data;
                // 2026-11-11 详情 v2：ui1.md 只读规范（四层/四列键值上下/账户灰底卡槽/隐私脱敏），渲染器 detail_v2.js
                currentDelId=rid;
                var idCell = (d2.unlocked && d2.household_id_card)
                    ? '<span class="d2-mono d2-val--medium" id="d2s-id-house">' + esc(d2.household_id_card) + '</span>' + d2CopyBtn(d2.household_id_card, '户主身份证号')
                    : '<span class="d2-mute">—</span>';
                var accIdCell = (d2.unlocked && d2.account_holder_id_card)
                    ? '<span class="d2-mono d2-val--medium" id="d2s-id-acc">' + esc(d2.account_holder_id_card) + '</span>'
                      + (d2.account_holder_id_card === d2.household_id_card ? '<span class="d2-chip d2-chip--slate">同户主</span>' : '')
                    : '<span class="d2-mute">—</span>';
                var bank = d2BankDisplay(d2.bank_card);
                var bankCell = d2.bank_card
                    ? '<span class="d2-mono d2-val--strong" id="d2s-bank">' + esc(bank.display) + '</span>' + d2CopyBtn(bank.copy, '银行账号')
                    : '<span class="d2-mute">—</span>';
                var sensitive = [];
                if (d2.unlocked && d2.household_id_card) sensitive.push({id: 'd2s-id-house', raw: d2.household_id_card, masked: d2MaskIdCard(d2.household_id_card)});
                if (d2.unlocked && d2.account_holder_id_card) sensitive.push({id: 'd2s-id-acc', raw: d2.account_holder_id_card, masked: d2MaskIdCard(d2.account_holder_id_card)});
                if (d2.bank_card) sensitive.push({id: 'd2s-bank', raw: bank.display, masked: bank.masked});
                var sameAddr = !!(d2.address && d2.address === d2.registered_address);
                d2Render({
                    modal: '#dtModal', body: '#dtBody', footerSel: '#dtModal',
                    name: d2.household_name || '特困供养详情',
                    tags: [d2.support_mode ? {t: d2.support_mode, tone: 'blue'} : null,
                           d2.self_care_ability ? {t: '自理能力：' + d2.self_care_ability, tone: 'orange'} : null].filter(Boolean),
                    subtitle: '<span>特困供养详情</span><span class="d2-subtitle__sep">|</span><span>所属所在组：<strong>' + esc(d2.village_group || '—') + '</strong></span>',
                    kpis: [
                        {label: '保障金额 (月)', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d2.guarantee_amount || 0).toFixed(2)) + '</span>'},
                        {label: '供养方式', html: d2ValMedium(d2.support_mode || '—')},
                        {label: '最新发放月份', html: d2.payment_month ? '<span class="d2-mono">' + esc(d2.payment_month) + '</span>' : '<span class="d2-mute">—</span>'}
                    ],
                    risk: null,
                    sections: [
                        {title: '个人与户籍信息', rows: [
                            [{label: '户主姓名', html: d2ValStrong(d2.household_name || '—')},
                             {label: '户主身份证号', html: idCell},
                             {label: '享受人口数', html: d2ValStrong((d2.beneficiary_count || 1) + ' 人')},
                             {label: '联系电话', html: d2ValMedium(d2.phone || '—')}],
                            [{label: '所在组', html: d2ValMedium(d2.village_group || '—')},
                             {label: '家庭地址 / 户籍地址', span2: true, stamp: sameAddr ? '<span class="d2-stamp">两址一致</span>' : '',
                              html: '<div class="d2-chipbox"><span class="d2-chipbox__text">' + esc(d2.address || '—') + '</span>' + d2CopyBtnText(d2.address, '地址') + '</div>'}]
                        ]},
                        {title: '保障与供养信息', rows: [
                            [{label: '保障金额 (月)', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d2.guarantee_amount || 0).toFixed(2)) + '</span>'},
                             {label: '供养方式', html: d2ValMedium(d2.support_mode || '—')},
                             {label: '供养机构', html: d2ValMedium(d2.support_institution || '—')},
                             {label: '自理能力', html: d2ValMedium(d2.self_care_ability || '—')}],
                            [{label: '开始享受时间', html: d2ValMono(d2.start_date)},
                             {label: '最近变更时间', html: d2ValMono(d2.change_time)},
                             {label: '最新发放月份', html: d2ValMono(d2.payment_month)}]
                        ]},
                        {title: '发放账户信息', card: true, cardNote: '一卡通代发账户 · 账号已做四位分段排版', rows: [
                            [{label: '开户人 / 开户人身份', html: d2ValStrong(d2.account_holder) + (d2.account_holder_identity ? '<span class="d2-chip d2-chip--white">' + esc(d2.account_holder_identity) + '</span>' : '')},
                             {label: '开户人身份证号', html: accIdCell},
                             {label: '开户银行种类', html: (d2.bank_type ? '<span class="d2-dot d2-dot--blue"></span>' : '') + '<span class="d2-val--medium">' + esc(d2.bank_type || '—') + '</span>'},
                             {label: '银行账号', html: bankCell}]
                        ]}
                    ],
                    sensitive: sensitive
                });
                $('#dtBtns').html(
                    '<button type="button" class="d2-btn d2-btn--primary" onclick="openEditFromDetail()"><i class="bi bi-pencil"></i> 编辑资料</button>'
                    + '<button type="button" class="d2-btn d2-btn--danger" onclick="delSpecialCurrent()"><i class="bi bi-trash"></i> 删除</button>'
                    + '<button type="button" class="d2-btn d2-btn--ghost" data-bs-dismiss="modal">关闭</button>');
                $('#dtModal').modal('show');
            });
        });
        // Bind delete button
        $('.btn-del').on('click', function(e){
            e.stopPropagation();
            var rid=$(this).attr('rid');
            if(!confirm('确定删除？')) return;
            $.post('/care/api/special/delete/'+rid, function(r3){
                if(r3.success){showToast('删除成功');load(cp);}
                else showToast(r3.msg,'danger');
            });
        });
        // Pagination（R-09：手写分页 → 全局 renderPagination）
        renderPagination('#pg', tp, cp, load);
    });
}

// 旧 #btnDelDt 绑定已由详情 v2 底部 delSpecialCurrent() 取代（2026-11-11）

// 2026-11 分组表单式改版：新增/编辑共用同一构建器；编辑由「详情就地换表单」改为打开独立二级表单弹窗
function specialFormHtml(){
    // 2026-11-11 编辑 v2：ui.md 四列栅格三段式（ed2-* 样式）；字段 name/data-dict 契约零改动
    function f(label, name, type, span2){
        type = type || 'text';
        var cls = ' class="sc-input"';
        if (type === 'select') cls = ' class="sc-select"';
        var ph = (type === 'date') ? '' : '';
        return '<div class="sc-field ed2-field' + (span2 ? ' ed2-span2' : '') + '"><label class="sc-field__label"><span>' + label + '</span></label>'
            + '<input type="' + type + '"' + cls + ' name="' + name + '"' + (type === 'number' && name === 'guarantee_amount' ? ' step="0.01"' : '') + '></div>';
    }
    function s(label, name){
        return '<div class="sc-field ed2-field"><label class="sc-field__label"><span>' + label + '</span></label><select class="sc-select" name="' + name + '"><option value="">请选择</option></select></div>';
    }
    return ''
        + '<section class="ed2-section"><div class="ed2-sechead"><span class="ed2-sechead__bar"></span><h4 class="ed2-sechead__title">个人与户籍信息</h4><span class="ed2-sechead__line"></span></div><div class="ed2-grid">'
        +   f('所在组','village_group')
        +   '<div class="sc-field ed2-field"><label class="sc-field__label"><span>户主姓名<span class="sc-field__required">*</span></span></label><input type="text" class="sc-input" name="household_name"></div>'
        +   f('户主身份证号','household_id_card') + f('联系电话','phone')
        +   f('家庭地址','address','text',true)
        + '</div></section>'
        + '<section class="ed2-section"><div class="ed2-sechead"><span class="ed2-sechead__bar"></span><h4 class="ed2-sechead__title">保障与供养信息</h4><span class="ed2-sechead__line"></span></div><div class="ed2-grid">'
        +   f('发放人数','beneficiary_count','number') + f('保障金额（元）','guarantee_amount','number') + s('供养方式','support_mode') + s('供养机构','support_institution')
        +   f('开始享受时间','start_date','date') + f('变更时间','change_time','date') + f('自理能力','self_care_ability')
        + '</div></section>'
        + '<section class="ed2-section"><div class="ed2-sechead"><span class="ed2-sechead__bar"></span><h4 class="ed2-sechead__title">发放账户信息</h4><span class="ed2-sechead__line"></span></div><div class="ed2-grid">'
        +   f('开户人','account_holder') + f('开户人身份','account_holder_identity') + f('开户人身份证号','account_holder_id_card')
        +   f('开户银行种类','bank_type') + f('银行账号','bank_card') + f('发放月份','payment_month')
        +   f('户籍地址','registered_address','text',true)
        + '</div></section>';
}
var SPECIAL_TEXT_FIELDS = ['village_group','household_name','household_id_card','phone','beneficiary_count','guarantee_amount','start_date','change_time','self_care_ability','account_holder','account_holder_identity','account_holder_id_card','bank_type','bank_card','payment_month','address','registered_address'];

// 详情 v2 底部按钮入口（2026-11-11：v2 footer 由 JS 组装，编辑/删除走以下函数）
function openEditFromDetail(){
    var id=currentDelId;if(!id)return;
    $.get('/care/api/special/detail/'+id,function(r2){
        if(!r2.success)return;var d=r2.data;
        $('#fmBody').html(specialFormHtml());
        $('#fid').val(d.id);
        $('#fmTitle').text('编辑特困供养');
        $('#fmMeta').text((d.household_name||'') + ' · 修改后保存生效');
        $('#fm [name="support_mode"]').attr('data-val', d.support_mode||'');
        $('#fm [name="support_institution"]').attr('data-val', d.support_institution||'');
        loadSpecialSupportDicts('#fm');
        $.each(SPECIAL_TEXT_FIELDS,function(_,k){ $('#fm [name="'+k+'"]').val(d[k]==null?'':d[k]); });
        $('#dtModal').one('hidden.bs.modal', function () { $('#fmModal').modal('show'); });
        $('#dtModal').modal('hide');
    });
}
function delSpecialCurrent(){
    if(!currentDelId||!confirm('确定删除？')) return;
    $.post('/care/api/special/delete/'+currentDelId, function(r){
        if(r.success){showToast('删除成功');$('#dtModal').modal('hide');load(cp);}
        else showToast(r.msg,'danger');
    });
}
$('#btnSearch').on('click', function(){load(1);});
$('#sName,#sIdCard').on('keydown', function(e){if(e.key==='Enter')load(1);});

$('#btnAdd').on('click', function(){
    $('#fmBody').html(specialFormHtml());
    $('#fmTitle').text('新增特困供养');
    $('#fmMeta').text('新增特困供养记录');
    $('#fid').val('');loadSpecialSupportDicts('#fm');$('#fmModal').modal('show');
});

$('#fm').submit(function(e){
    e.preventDefault();
    $.post('/care/api/special/save', $(this).serialize(), function(r){
        if(r.success){showToast('保存成功');$('#fmModal').modal('hide');load(1);}
        else showToast(r.msg,'danger');
    });
});

// 导出（修复死链：模板 onclick="exportSpecialData()" 原无定义；对齐 target_list 的 exportTarget 写法）
function exportSpecialData(){
    var q='name='+encodeURIComponent($('#sName').val()||'')+'&id_card='+encodeURIComponent($('#sIdCard').val()||'');
    window.location='/care/api/special/export?'+q;
}

function startImport(){
    importWizard({fileSel:'#impFile', resultSel:'#impRes', endpoint:'/care/api/special/import',
        extraData:{mode:$('input[name="impMode"]:checked').val() || 'append'},
        onDone:function(){ setTimeout(function(){$('#impModal').modal('hide');load(1);},1500); }});
}

function loadSpecialSupportDicts(scope) {
    scope = scope || document;
    dictSelect('供养方式', $(scope).find('select[name="support_mode"]'), '请选择');
    dictSelect('供养机构', $(scope).find('select[name="support_institution"]'), '请选择');
}

$(function(){load(1);});

// R-20：内联事件依赖函数挂 window（IIFE 隔离顶层作用域；内联 onclick/onblur 契约保留）
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ openEditFromDetail: openEditFromDetail, delSpecialCurrent: delSpecialCurrent, startImport: startImport, exportSpecialData: exportSpecialData }); }
})();

// 2026-11-19 换壳配套：导入弹窗投放区（app.js wireImportDropzone 全局单源）。
// #impFile 仍是真实 file input，startImport 链路不变；弹窗每次打开经 show.bs.modal 复位。
$(function () {
    wireImportDropzone({
        fileSel: '#impFile', dropSel: '#careSpecialImpDrop', stagedSel: '#careSpecialImpStaged',
        stagedNameSel: '#careSpecialImpStagedName', stagedMetaSel: '#careSpecialImpStagedMeta',
        submitBtnSel: '#impSubmit', reselectSel: '#careSpecialImpReselect', removeSel: '#careSpecialImpRemove',
        modalSel: '#impModal'
    });
});
