(function () {
var tcp=1, tps=20, ttp=0, esvcDid=null, esvcCache={}, esvcName='';

function loadTarget(p){
    tcp=p;
    $('#tb').html('<tr><td colspan="12" class="text-center py-4 text-muted">加载中...</td></tr>');
    $.get('/care/api/target/list',{page:p,limit:tps,target_type:$('#tTargetType').val(),child_type:$('#tChildType').val(),village_group:$('#tVillage').val(),name:$('#tName').val(),
        service_item:$('#tsServiceItem').val(),service_status:$('#tsServiceStatus').val()},function(r){
        if(!r.success||!r.data) return;
        var d=r.data, items=d.data||[];
        ttp=d.pages||0;
        $('#pageStat').text('共 ' + d.total + ' 条 · 每页 ' + tps + ' 条');
        var h='';
        if(items.length===0){
            h='<tr><td colspan="12" class="text-center py-4 text-muted">暂无数据 —— 请先导入镇级名单，或手工建档一老一小对象</td></tr>';
        }else{
            for(var i=0;i<items.length;i++){
                var x=items[i];
                // 067 §2.4 徽章映射：bg-warning→warning、bg-success→success、bg-info→info、bg-secondary→muted（真实字段渲染）
                var consentBadge = x.guardian_consent==='待补录'
                    ? '<span class="sc-badge sc-badge--warning">待补录</span>'
                    : '<span class="sc-badge sc-badge--success">已同意</span>';
                var clsBadge = x.dynamic_class==='高龄' ? '<span class="sc-badge sc-badge--info">高龄</span>'
                    : (x.dynamic_class==='儿童' ? '<span class="sc-badge sc-badge--muted">儿童</span>' : esc(x.dynamic_class));
                h+='<tr rid="'+x.id+'">'+
                    '<td><strong>'+esc(x.name)+'</strong></td>'+
                    '<td>'+esc(x.target_type)+'</td>'+
                    '<td>'+esc(x.child_type||x.elder_type)+'</td>'+
                    '<td>'+clsBadge+'</td>'+
                    '<td class="text-center">'+(x.age!=null?x.age:'')+'</td>'+
                    '<td>'+esc(x.village_group)+'</td>'+
                    '<td>'+esc(x.grid)+'</td>'+
                    '<td>'+esc(x.source)+'</td>'+
                    '<td>'+consentBadge+'</td>'+
                    '<td>'+esc(x.service_item||'')+((Number(x.service_count)||0)>1?' <span class="sc-badge sc-badge--muted">'+Number(x.service_count)+' 项</span>':'')+'</td>'+
                    '<td>'+esc(x.service_status||'')+'</td>'+
                    '<td><span class="sc-row-actions">'+
                    (x.target_type==='老人' ? '<button type="button" class="sc-btn sc-btn--text btn-svc" rid="'+x.id+'" data-name="'+escAttr(x.name)+'" title="服务事项"><i class="bi bi-clipboard-check"></i></button>' : '')+
                    '<button type="button" class="sc-btn sc-btn--text is-danger btn-del" rid="'+x.id+'"><i class="bi bi-trash"></i></button>'+
                    (x.guardian_consent==='待补录' ? '<button type="button" class="sc-btn sc-btn--text btn-consent" rid="'+x.id+'" title="确认监护人同意"><i class="bi bi-check"></i></button>' : '')+'</span></td>'+
                    '</tr>';
            }
        }
        $('#tb').html(h);
        renderPager();
    });
}

function renderPager(){
    // R-09：原全量渲染所有页码（长列表 DOM 爆炸）→ 全局 renderPagination（±2 窗口 + 省略号）
    renderPagination('#pg', ttp, tcp, loadTarget);
}

$('#btnTSearch').on('click',function(){loadTarget(1);});
$('#btnTAdd').on('click',function(){$('#tForm')[0].reset();$('#tFormModal').modal('show');});
$('#btnTImport').on('click',function(){$('#tImpRes').html('');$('#tImpModal').modal('show');});

$('#tForm').on('submit',function(e){
    e.preventDefault();
    $.post('/care/api/target/save',$(this).serialize(),function(r){
        if(!r.success){showToast(r.msg, 'danger');return;}
        if(r.data && r.data.conflict){
            if(confirm('检测到归类冲突：动态计算为「'+r.data.computed+'」，名单声称「'+r.data.claimed+'」（年龄'+r.data.age+'岁）。是否人工确认覆盖？')){
                // 覆盖归类需走 override 端点（需先保存建档）
                showToast('请先保存建档，再到详情/编辑中人工确认覆盖归类。', 'warning');
            }
            return;
        }
        $('#tFormModal').modal('hide');
        loadTarget(tcp);
    });
});

$('#tb').on('click','.btn-del',function(){
    var rid=$(this).attr('rid');
    if(!confirm('确认失效该关爱对象？')) return;
    $.post('/care/api/target/delete/'+rid,{},function(r){
        if(!r.success){showToast(r.msg, 'danger');return;}
        loadTarget(tcp);
    });
});

$('#tb').on('click','.btn-consent',function(){
    var rid=$(this).attr('rid');
    if(!confirm('确认已取得监护人同意？')) return;
    $.post('/care/api/target/consent/'+rid,{},function(r){
        if(!r.success){showToast(r.msg, 'danger');return;}
        loadTarget(tcp);
    });
});

// ── 093 老人服务事项（仅 target_type='老人'；儿童行无入口） ──
$('#tb').on('click','.btn-svc',function(){
    openEsvc($(this).attr('rid'), $(this).attr('data-name'));
});

function ensureEsvcDicts(done){
    var pending=2;
    function next(){pending-=1;if(pending===0&&done)done();}
    dictSelect('老年人服务事项','#esvcItem','请选择',next);
    dictSelect('服务办理状态','#esvcStatus','请选择',next);
}

function renderEsvcList(list){
    list=list||[];esvcCache={};
    $.each(list,function(i,s){esvcCache[s.id]=s;});
    var h='';
    if(!list.length){
        h='<div class="text-muted" style="padding:6px 2px">暂无服务记录</div>';
    }else{
        h='<div class="sc-table-wrap"><table class="sc-table sc-table--dense"><thead class="sc-table__head"><tr>'+
            '<th class="sc-table__th">事项</th><th class="sc-table__th">办理状态</th><th class="sc-table__th">进度</th>'+
            '<th class="sc-table__th">责任人</th><th class="sc-table__th">服务日期</th><th class="sc-table__th">备注</th>'+
            '<th class="sc-table__th">操作</th></tr></thead><tbody>';
        $.each(list,function(i,s){
            h+='<tr><td>'+esc(s.service_item||'—')+'</td><td>'+esc(s.service_status||'—')+'</td>'+
                '<td>'+esc(s.service_progress||'—')+'</td><td>'+esc(s.service_owner||'—')+'</td>'+
                '<td>'+esc(s.service_date||'—')+'</td><td>'+esc(s.remark||'—')+'</td>'+
                '<td><span class="sc-row-actions">'+
                '<button type="button" class="sc-btn sc-btn--text" title="编辑" onclick="openEsvcEdit('+s.id+')"><i class="bi bi-pencil-square"></i></button>'+
                '<button type="button" class="sc-btn sc-btn--text is-danger" title="删除" onclick="delEsvc('+s.id+')"><i class="bi bi-trash"></i></button>'+
                '</span></td></tr>';
        });
        h+='</tbody></table></div>';
    }
    $('#esvcList').html(h);
}

function loadEsvcList(){
    if(!esvcDid) return;
    $.get('/care/api/target/services/'+esvcDid,function(r){
        if(!r.success){$('#esvcList').html('<div class="text-muted">'+esc(r.msg||'读取失败')+'</div>');return;}
        renderEsvcList(r.data||[]);
    });
}

function openEsvc(rid, name){
    if(!rid) return;
    esvcDid=rid;esvcName=name||'';
    $('#esvcForm')[0].reset();$('#esvcId').val('');
    $('#esvcTitle').html('<i class="bi bi-clipboard-check"></i> 服务事项 · '+esc(esvcName));
    $('#esvcList').html('<div class="text-muted" style="padding:6px 2px">加载中...</div>');
    ensureEsvcDicts(function(){
        $('#esvcModal').modal('show');
        loadEsvcList();
    });
}

function openEsvcEdit(sid){
    var s=esvcCache[sid];
    if(!s||!esvcDid) return;
    $('#esvcForm')[0].reset();$('#esvcId').val(s.id);
    ensureEsvcDicts(function(){
        $('#esvcItem').val(s.service_item||'');
        $('#esvcStatus').val(s.service_status||'');
        $('#esvcForm [name="service_progress"]').val(s.service_progress||'');
        $('#esvcForm [name="service_owner"]').val(s.service_owner||'');
        $('#esvcForm [name="service_date"]').val(s.service_date||'');
        $('#esvcForm [name="remark"]').val(s.remark||'');
    });
}

function delEsvc(sid){
    if(!esvcDid) return;
    if(!confirm('确定删除该服务记录？')) return;
    $.post('/care/api/target/services/'+esvcDid+'/delete/'+sid,{},function(r){
        if(!r.success){showToast(r.msg||'删除失败','danger');return;}
        showToast('删除成功','success');
        loadEsvcList();loadTarget(tcp);
    });
}

$('#esvcForm').on('submit',function(e){
    e.preventDefault();
    if(!esvcDid) return;
    $.post('/care/api/target/services/'+esvcDid+'/save',$(this).serialize(),function(r){
        if(!r.success){showToast(r.msg||'保存失败','danger');return;}
        showToast('保存成功','success');
        $('#esvcForm')[0].reset();$('#esvcId').val('');
        loadEsvcList();loadTarget(tcp);
    });
});

function exportTarget(){
    var q='target_type='+encodeURIComponent($('#tTargetType').val())+'&child_type='+encodeURIComponent($('#tChildType').val())+'&village_group='+encodeURIComponent($('#tVillage').val())+'&name='+encodeURIComponent($('#tName').val())+
        '&service_item='+encodeURIComponent($('#tsServiceItem').val())+'&service_status='+encodeURIComponent($('#tsServiceStatus').val());
    window.location='/care/api/target/export?'+q;
}

function startTargetImport(){
    var fd=new FormData();
    fd.append('file',$('#tImpFile')[0].files[0]);
    $.ajax({url:'/care/api/target/import',type:'POST',data:fd,processData:false,contentType:false,success:function(r){
        $('#tImpRes').html(esc(r.msg||''));
        loadTarget(tcp);
    }});
}

$(function(){
    dictSelect('老年人服务事项', '#tsServiceItem', '全部服务事项');
    dictSelect('服务办理状态', '#tsServiceStatus', '全部办理状态');
    loadTarget(1);
});

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.exportTarget = exportTarget;
window.startTargetImport = startTargetImport;
window.openEsvcEdit = openEsvcEdit;
window.delEsvc = delEsvc;
})();

// 2026-11-19 换壳配套：导入弹窗投放区（app.js wireImportDropzone 全局单源）。
// #tImpFile 仍是真实 file input，startTargetImport 链路不变；弹窗每次打开经 show.bs.modal 复位。
$(function () {
    wireImportDropzone({
        fileSel: '#tImpFile', dropSel: '#targetImpDrop', stagedSel: '#targetImpStaged',
        stagedNameSel: '#targetImpStagedName', stagedMetaSel: '#targetImpStagedMeta',
        submitBtnSel: '#tImpSubmit', reselectSel: '#targetImpReselect', removeSel: '#targetImpRemove',
        modalSel: '#tImpModal'
    });
});
