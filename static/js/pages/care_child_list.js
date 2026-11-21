(function () {
var cp=1,ps=20,tp=0, currentDelId=null;

function load(p){
    cp=p;
    $('#tb').html('<tr><td colspan="12" class="text-center py-4 text-muted">加载中...</td></tr>');
    $.get('/care/api/child/list',{page:p,limit:ps,name:$('#sName').val(),id_card:$('#sIdCard').val()},function(r){
        if(!r.success||!r.data) return;
        var d=r.data, items=d.data||[];
        tp=d.pages||0;
        $('#pageStat').text('共 ' + d.total + ' 条 · 每页 ' + ps + ' 条');
        var h='';
        if(items.length===0){
            h='<tr><td colspan="12" class="text-center py-4 text-muted">暂无数据</td></tr>';
        }else{
            for(var i=0;i<items.length;i++){
                var x=items[i];
                h+='<tr rid="'+x.id+'">'+
                    '<td>'+esc(x.village_group)+'</td>'+
                    '<td class="text-end">'+Number(x.amount||0).toFixed(2)+'</td>'+
                    '<td><strong>'+esc(x.name)+'</strong></td>'+
                    '<td>'+esc(x.id_card)+'</td>'+
                    '<td>'+esc(x.gender)+'</td>'+
                    '<td>'+esc(x.father_status)+'</td>'+
                    '<td>'+esc(x.mother_status)+'</td>'+
                    '<td>'+esc(x.health_status)+'</td>'+
                    '<td>'+esc(x.cardholder_relation)+'</td>'+
                    '<td>'+esc(x.phone)+'</td>'+
                    '<td>'+esc(x.address)+'</td>'+
                    '<td><span class="sc-row-actions"><button type="button" class="sc-btn sc-btn--text is-danger btn-del" rid="'+x.id+'"><i class="bi bi-trash"></i></button></span></td>'+
                    '</tr>';
            }
        }
        $('#tb').html(h);
        $('#tb tr[rid]').on('click', function(){
            var rid=$(this).attr('rid');
            $.get('/care/api/child/detail/'+rid, function(r2){
                if(!r2.success) return;
                var d2=r2.data;
                // B4（GLB-46）：详情含 birth_date；2026-11-11 详情 v2：ui1.md 只读规范（四层/四列键值上下/隐私脱敏）
                // 身份证号按启动口令解锁态渲染（unlocked），锁定时该格「—」
                currentDelId=rid;
                var idCell = (d2.unlocked && d2.id_card)
                    ? '<span class="d2-mono d2-val--medium" id="d2s-id-main">' + esc(d2.id_card) + '</span>' + d2CopyBtn(d2.id_card, '身份证号')
                    : '<span class="d2-mute">—</span>';
                var sensitive = (d2.unlocked && d2.id_card) ? [{id: 'd2s-id-main', raw: d2.id_card, masked: d2MaskIdCard(d2.id_card)}] : [];
                d2Render({
                    modal: '#dtModal', body: '#dtBody', footerSel: '#dtModal',
                    name: d2.name || '儿童详情',
                    tags: [d2.health_status ? {t: d2.health_status, tone: 'orange'} : null,
                           d2.cardholder_relation ? {t: '持卡人：' + d2.cardholder_relation, tone: 'slate'} : null].filter(Boolean),
                    subtitle: '<span>事实无人抚养儿童 · 儿童详情</span><span class="d2-subtitle__sep">|</span><span>所属所在组：<strong>' + esc(d2.village_group || '—') + '</strong></span>',
                    kpis: [
                        {label: '月发放金额', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d2.amount || 0).toFixed(2)) + '</span>'},
                        {label: '性别', html: d2ValMedium(d2.gender || '—')},
                        {label: '出生日期', html: d2ValMono(d2.birth_date)}
                    ],
                    risk: null,
                    sections: [
                        {title: '个人信息', rows: [
                            [{label: '儿童姓名', html: d2ValStrong(d2.name || '—')},
                             {label: '性别', html: d2ValMedium(d2.gender || '—')},
                             {label: '出生日期', html: d2ValMono(d2.birth_date)},
                             {label: '身份证号码', html: idCell}],
                            [{label: '联系电话', html: d2ValMedium(d2.phone || '—')},
                             {label: '所在组', html: d2ValMedium(d2.village_group || '—')},
                             {label: '儿童身体情况', html: d2ValMedium(d2.health_status || '—')}]
                        ]},
                        {title: '家庭与保障信息', rows: [
                            [{label: '父亲情况', html: d2ValMedium(d2.father_status || '—')},
                             {label: '母亲情况', html: d2ValMedium(d2.mother_status || '—')},
                             {label: '持卡人与本人关系', html: d2ValMedium(d2.cardholder_relation || '—')},
                             {label: '月发放金额', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d2.amount || 0).toFixed(2)) + '</span>'}],
                            [{label: '家庭地址', span2: true,
                              html: '<div class="d2-chipbox"><span class="d2-chipbox__text">' + esc(d2.address || '—') + '</span>' + d2CopyBtnText(d2.address, '地址') + '</div>'}]
                        ]}
                    ],
                    sensitive: sensitive
                });
                $('#dtBtns').html(
                    '<button type="button" class="d2-btn d2-btn--primary" onclick="openEditChild(' + d2.id + ')"><i class="bi bi-pencil"></i> 编辑资料</button>'
                    + '<button type="button" class="d2-btn d2-btn--danger" onclick="delChildCurrent(' + d2.id + ')"><i class="bi bi-trash"></i> 删除</button>'
                    + '<button type="button" class="d2-btn d2-btn--ghost" data-bs-dismiss="modal">关闭</button>');
                $('#dtModal').modal('show');
            });
        });
        $('.btn-del').on('click', function(e){
            e.stopPropagation();
            var rid=$(this).attr('rid');
            if(!confirm('确定删除？')) return;
            $.post('/care/api/child/delete/'+rid, function(r3){
                if(r3.success){showToast('删除成功');load(cp);}
                else showToast(r3.msg,'danger');
            });
        });
        renderPagination('#pg', tp, cp, load);
    });
}

// 详情 v2 底部删除入口（2026-11-11：footer 由 JS 组装）
function delChildCurrent(did){
    did = did || currentDelId;
    if(!did||!confirm('确定删除？')) return;
    $.post('/care/api/child/delete/'+did, function(r){
        if(r.success){showToast('删除成功');$('#dtModal').modal('hide');load(cp);}
        else showToast(r.msg,'danger');
    });
}
$('#btnSearch').on('click', function(){load(1);});
$('#sName,#sIdCard').on('keydown', function(e){if(e.key==='Enter')load(1);});

$('#btnAdd').on('click', function(){
    var html=
        '<div class="sc-fgroups">'
        + '<div class="sc-fgroups__col">'
        +   '<section class="sc-fgroup"><h4 class="sc-fgroup__title">基本信息</h4><div class="sc-fgroup__fields">'
        +     '<div class="sc-field"><label class="sc-field__label">儿童姓名<span class="sc-field__required">*</span></label><input type="text" class="sc-input" name="name" required></div>'
        +     '<div class="sc-field"><label class="sc-field__label">性别</label><select class="sc-select" name="gender"><option>请选择</option><option>男</option><option>女</option></select></div>'
        +     '<div class="sc-field"><label class="sc-field__label">出生日期</label><input type="date" class="sc-input" name="birth_date"></div>'
        +     '<div class="sc-field"><label class="sc-field__label">所在组</label><input type="text" class="sc-input" name="village_group"></div>'
        +     '<div class="sc-field"><label class="sc-field__label">身份证号码</label><input type="text" class="sc-input" name="id_card" onblur="autoFillChild(this)"></div>'
        +   '</div></section>'
        +   '<section class="sc-fgroup"><h4 class="sc-fgroup__title">保障信息</h4><div class="sc-fgroup__fields">'
        +     '<div class="sc-field"><label class="sc-field__label">发放金额（元）</label><input type="number" step="0.01" class="sc-input" name="amount" value="0"></div>'
        +   '</div></section>'
        + '</div>'
        + '<div class="sc-fgroups__col">'
        +   '<section class="sc-fgroup"><h4 class="sc-fgroup__title">健康与家庭</h4><div class="sc-fgroup__fields">'
        +     '<div class="sc-field"><label class="sc-field__label">儿童身体情况</label><input type="text" class="sc-input" name="health_status"></div>'
        +     '<div class="sc-field"><label class="sc-field__label">父亲情况</label><input type="text" class="sc-input" name="father_status"></div>'
        +     '<div class="sc-field"><label class="sc-field__label">母亲情况</label><input type="text" class="sc-input" name="mother_status"></div>'
        +   '</div></section>'
        +   '<section class="sc-fgroup"><h4 class="sc-fgroup__title">联系与地址</h4><div class="sc-fgroup__fields">'
        +     '<div class="sc-field"><label class="sc-field__label">持卡人与本人关系</label><input type="text" class="sc-input" name="cardholder_relation"></div>'
        +     '<div class="sc-field"><label class="sc-field__label">联系电话</label><input type="text" class="sc-input" name="phone"></div>'
        +     '<div class="sc-field sc-fgroup__field--full"><label class="sc-field__label">家庭地址</label><input type="text" class="sc-input" name="address"></div>'
        +   '</div></section>'
        + '</div></div>';
    $('#fmBody').html(html);$('#fmTitle').text('新增儿童');$('#fmMeta').text('新增儿童记录');$('#fid').val('');$('#fmModal').modal('show');
});

$('#fm').submit(function(e){
    e.preventDefault();
    $.post('/care/api/child/save', $(this).serialize(), function(r){
        if(r.success){showToast('保存成功');$('#fmModal').modal('hide');load(1);}
        else showToast(r.msg,'danger');
    });
});

// 导出（修复死链：模板 onclick="exportChildData()" 原无定义；对齐 target_list 的 exportTarget 写法）
function exportChildData(){
    var q='name='+encodeURIComponent($('#sName').val()||'')+'&id_card='+encodeURIComponent($('#sIdCard').val()||'');
    window.location='/care/api/child/export?'+q;
}

function startImport(){
    importWizard({fileSel:'#impFile', resultSel:'#impRes', endpoint:'/care/api/child/import',
        extraData:{mode:$('input[name="impMode"]:checked').val() || 'append'},
        onDone:function(){ setTimeout(function(){$('#impModal').modal('hide');load(1);},1500); }});
}


function autoFillChild(el){
    var card = $(el).val().trim();
    if(card.length<18) return;
    var yr=card.substr(6,4), mo=card.substr(10,2), dy=card.substr(12,2);
    if(yr&&mo&&dy) $(el).closest(".modal").find('input[name="birth_date"]').val(yr+"-"+mo+"-"+dy);
}

$(function(){load(1);});

// R-20：内联事件依赖函数挂 window（IIFE 隔离顶层作用域；内联 onclick/onblur 契约保留）
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ delChildCurrent: delChildCurrent, startImport: startImport, autoFillChild: autoFillChild, exportChildData: exportChildData, openAddChild: openAddChild, openEditChild: openEditChild }); }
})();

// ── 2026-11-11 儿童信息编辑功能补齐（ui.md 四列栅格三段式，ed2-* 样式；name 契约与 /care/api/child/save 对齐） ──
function childFormHtml(){
    function f(label, name, type, span2){
        type = type || 'text';
        return '<div class="sc-field ed2-field' + (span2 ? ' ed2-span2' : '') + '"><label class="sc-field__label"><span>' + label + '</span></label>'
            + '<input type="' + type + '" class="sc-input" name="' + name + '"' + (type === 'number' && name === 'amount' ? ' step="0.01"' : '') + '></div>';
    }
    function s(label, name, opts){
        var o = '<option value="">请选择</option>' + (opts || ['男', '女']).map(function (x) { return '<option>' + x + '</option>'; }).join('');
        return '<div class="sc-field ed2-field"><label class="sc-field__label"><span>' + label + '</span></label><select class="sc-select" name="' + name + '">' + o + '</select></div>';
    }
    return ''
        + '<section class="ed2-section"><div class="ed2-sechead"><span class="ed2-sechead__bar"></span><h4 class="ed2-sechead__title">个人信息</h4><span class="ed2-sechead__line"></span></div><div class="ed2-grid">'
        +   '<div class="sc-field ed2-field"><label class="sc-field__label"><span>儿童姓名<span class="sc-field__required">*</span></span></label><input type="text" class="sc-input" name="name" required></div>'
        +   s('性别', 'gender')
        +   f('出生日期', 'birth_date', 'date')
        +   '<div class="sc-field ed2-field"><label class="sc-field__label"><span>身份证号码</span></label><input type="text" class="sc-input" name="id_card"></div>'
        +   f('联系电话', 'phone')
        +   f('所在组', 'village_group')
        +   f('儿童身体情况', 'health_status')
        + '</div></section>'
        + '<section class="ed2-section"><div class="ed2-sechead"><span class="ed2-sechead__bar"></span><h4 class="ed2-sechead__title">家庭与保障信息</h4><span class="ed2-sechead__line"></span></div><div class="ed2-grid">'
        +   f('父亲情况', 'father_status')
        +   f('母亲情况', 'mother_status')
        +   f('持卡人与本人关系', 'cardholder_relation')
        +   f('月发放金额（元）', 'amount', 'number')
        +   f('家庭地址', 'address', 'text', true)
        + '</div></section>';
}

function openAddChild(){
    $('#childFm')[0].reset();
    $('#childFid').val('');
    $('#childFmTitle').text('新增儿童');
    $('#childFmMeta').text('新增事实无人抚养儿童记录');
    $('#childFmBody').html(childFormHtml());
    $('#childFmModal').modal('show');
}
function openEditChild(rid){
    $.get('/care/api/child/detail/'+rid, function(r2){
        if(!r2.success) { showToast(r2.msg || '读取数据失败', 'danger'); return; }
        var d = r2.data;
        $('#childFmBody').html(childFormHtml());
        $('#childFid').val(d.id);
        $('#childFmTitle').text('编辑儿童');
        $('#childFmMeta').text((d.name || '') + ' · 修改后保存生效');
        $.each(d, function (k, v) {
            var $f = $('#childFm [name="' + k + '"]');
            if ($f.length) $f.val(v == null ? '' : v);
        });
        $('#dtModal').one('hidden.bs.modal', function () { $('#childFmModal').modal('show'); });
        $('#dtModal').modal('hide');
    });
}
$('#childFm').submit(function(e){
    e.preventDefault();
    if (!$.trim($('#childFm [name="name"]').val())) { showToast('儿童姓名为必填项', 'danger'); return; }
    $.post('/care/api/child/save', $('#childFm').serialize(), function(r){
        if(r.success){ showToast(r.msg || '保存成功', 'success'); $('#childFmModal').modal('hide'); load(cp); }
        else showToast(r.msg, 'danger');
    });
});

// 2026-11-19 换壳配套：导入弹窗投放区（app.js wireImportDropzone 全局单源）。
// #impFile 仍是真实 file input，startImport 链路不变；弹窗每次打开经 show.bs.modal 复位。
$(function () {
    wireImportDropzone({
        fileSel: '#impFile', dropSel: '#careChildImpDrop', stagedSel: '#careChildImpStaged',
        stagedNameSel: '#careChildImpStagedName', stagedMetaSel: '#careChildImpStagedMeta',
        submitBtnSel: '#impSubmit', reselectSel: '#careChildImpReselect', removeSel: '#careChildImpRemove',
        modalSel: '#impModal'
    });
});
