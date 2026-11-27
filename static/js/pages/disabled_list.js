(function () {
var cp=1,ps=20,tp=0, disabledLookupTimer=null, listReqId=0, dtCurrent=null, svcCache={};
function load(p){
    p=parseInt(p,10);
    if(!p||p<1||(tp>0&&p>tp))return;
    cp=p; var q={page:p,name:$('#sName').val(),id_card:$('#sIdCard').val(),village:$('#sVillage').val(),dtype:$('#sType').val(),
        service_item:$('#sServiceItem').val(),service_status:$('#sServiceStatus').val()};
    var myId = ++listReqId;
    $('#tb').html('<tr><td colspan="20" class="text-center py-4"><div class="spinner-border spinner-border-sm text-primary"></div> 加载中...</td></tr>');
    $.get('/disabled/api/list',q,function(r){
        if(myId !== listReqId) return;
        if(!r.success)return; var d=r.data,items=d.data||[]; tp=Number(d.pages)||0;cp=Number(d.page)||1;
        $('#pageStat').text('共 ' + d.total + ' 条 · 每页 ' + ps + ' 条');
        var h='';
        if(!items.length) h='<tr><td colspan="20" class="text-center py-4 text-muted">暂无数据</td></tr>';
        else $.each(items,function(i,r){
            h+='<tr data-id="'+r.id+'" style="cursor:pointer">'+
            '<td>'+esc(r.village_group)+'</td><td class="text-nowrap" style="min-width:4.5em;">'+esc(r.name)+'</td><td>'+esc(r.id_card)+'</td>'+
            '<td>'+esc(r.gender)+'</td><td>'+esc(r.nation)+'</td><td>'+esc(r.education)+'</td><td>'+esc(r.phone)+'</td>'+
            '<td>'+esc(r.registered_address)+'</td><td>'+esc(r.guardian_name)+'</td><td>'+esc(r.guardian_relation)+'</td><td>'+esc(r.guardian_phone)+'</td>'+
            '<td>'+esc(r.cert_no)+'</td><td>'+esc(r.disability_type)+'</td><td>'+esc(r.disability_level)+'</td><td>'+esc(r.disability_detail)+'</td>'+
            '<td>'+Number(r.subsidy_living).toFixed(2)+'</td><td>'+Number(r.subsidy_nursing).toFixed(2)+'</td>'+
            '<td>'+esc(r.service_item||'')+((Number(r.service_count)||0)>1?' <span class="sc-badge sc-badge--muted">'+Number(r.service_count)+' 项</span>':'')+'</td>'+
            '<td>'+esc(r.service_status||'')+'</td>'+
            '<td><span class="sc-row-actions">'+
            '<button type="button" class="sc-btn sc-btn--text is-danger" title="删除" aria-label="删除" onclick="event.stopPropagation();del('+r.id+')"><i class="bi bi-trash"></i></button>'+
            '</span></td></tr>';
        });
        $('#tb').html(h);
        $('#tb tr[data-id]').on('click',function(){var id=$(this).data('id');$.get('/disabled/api/detail/'+id,function(r2){if(r2.success)openDt(r2.data);});});
        renderPagination('#pg', tp, cp, load);
    });
}

function openDt(d){
    // 2026-11-11 详情 v2：ui1.md 只读规范（四层/四列键值上下/隐私脱敏），渲染器 detail_v2.js；
    // 身份证号按启动口令解锁态渲染（unlocked），锁定时该格「—」；093 服务事项区块保留（extraHtml）
    dtCurrent = d.id;
    var idCell = (d.unlocked && d.id_card)
        ? '<span class="d2-mono d2-val--medium" id="d2s-id-main">' + esc(d.id_card) + '</span>' + d2CopyBtn(d.id_card, '身份证号')
        : '<span class="d2-mute">—</span>';
    var sensitive = (d.unlocked && d.id_card) ? [{id: 'd2s-id-main', raw: d.id_card, masked: d2MaskIdCard(d.id_card)}] : [];
    var levelChip = (d.disability_level ? '<span class="d2-chip d2-chip--slate">' + esc(d.disability_level) + '</span>' : '');
    var dcfg = {
        modal: '#dtModal', body: '#dtBody', footerSel: '#dtModal',
        name: d.name || '残疾人详情',
        tags: [d.disability_type ? {t: d.disability_type, tone: 'blue'} : null, levelChip ? {t: d.disability_level, tone: 'slate'} : null].filter(Boolean),
        subtitle: '<span>残疾人详情</span><span class="d2-subtitle__sep">|</span><span>所属所在组：<strong>' + esc(d.village_group || '—') + '</strong></span>',
        kpis: [
            {label: '残疾等级', html: d2ValMedium((d.disability_type || '—') + ' ' + (d.disability_level || ''))},
            {label: '困难生活补贴', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d.subsidy_living || 0).toFixed(2)) + '</span>'},
            {label: '重度护理补贴', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d.subsidy_nursing || 0).toFixed(2)) + '</span>'}
        ],
        risk: null,
        sections: [
            {title: '个人与基本信息', rows: [
                [{label: '姓名', html: d2ValStrong(d.name || '—') + (d.gender ? '<span class="d2-chip d2-chip--slate">' + esc(d.gender) + '</span>' : '')},
                 {label: '身份证号', html: idCell},
                 {label: '民族', html: d2ValMedium(d.nation || '—')},
                 {label: '文化程度', html: d2ValMedium(d.education || '—')}],
                [{label: '联系电话', html: d2ValMedium(d.phone || '—')},
                 {label: '所在组', html: d2ValMedium(d.village_group || '—')},
                 {label: '监护人 / 关系', html: d2ValMedium((d.guardian_name || '—') + (d.guardian_relation ? ' · ' + esc(d.guardian_relation) : ''))},
                 {label: '监护人手机', html: d2ValMedium(d.guardian_phone || '—')}]
            ]},
            {title: '残疾与补贴信息', rows: [
                [{label: '残疾类别', html: d2ValMedium(d.disability_type || '—')},
                 {label: '残疾等级', html: d2ValMedium(d.disability_level || '—') + levelChip},
                 {label: '残疾证号', html: d2ValMono(d.cert_no)},
                 {label: '困难生活补贴 (月)', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d.subsidy_living || 0).toFixed(2)) + '</span>'}],
                [{label: '残疾详情', span2: true, html: d2ValMedium(d.disability_detail || '—')},
                 {label: '重度护理补贴 (月)', html: '<span class="d2-mono d2-val--strong">' + esc('¥' + Number(d.subsidy_nursing || 0).toFixed(2)) + '</span>'}]
            ]},
            {title: '户籍与联系地址', rows: [
                [{label: '户籍地地址', span2: true,
                  html: '<div class="d2-chipbox"><span class="d2-chipbox__text">' + esc(d.registered_address || '—') + '</span>' + d2CopyBtnText(d.registered_address, '地址') + '</div>'}]
            ]}
        ],
        sensitive: sensitive
    };
    // 服务事项容器位（renderSvcList 填充），置于正文之后
    dcfg.extraHtml = '<div id="dtSvcWrap"></div>';
    d2Render(dcfg);
    renderSvcList(d.services || []);
    $('#dtBtns').html(
        '<button type="button" class="d2-btn d2-btn--primary" onclick="openEdit(' + d.id + ')"><i class="bi bi-pencil"></i> 编辑资料</button>'
        + '<button type="button" class="d2-btn d2-btn--danger" onclick="del(' + d.id + ')"><i class="bi bi-trash"></i> 删除</button>'
        + '<button type="button" class="d2-btn d2-btn--ghost" data-bs-dismiss="modal">关闭</button>');
    $('#dtModal').modal('show');
}

// ── 093 服务事项（残疾人服务记录：列表 + 新增/编辑/删除） ──
function renderSvcList(list) {
    list = list || [];
    svcCache = {};
    $.each(list, function (i, s) { svcCache[s.id] = s; });
    var h = '<section class="d2-section" style="margin-top:4px"><div class="d2-sechead">'
        + '<span class="d2-sechead__bar"></span><h4 class="d2-sechead__title">服务事项</h4>'
        + '<span class="d2-sechead__note">（093 残疾人服务记录）</span><span class="d2-sechead__line"></span></div>';
    if (!list.length) {
        h += '<div class="d2-mute" style="padding:6px 2px;font-size:12px">暂无服务记录</div>';
    } else {
        h += '<div class="sc-table-wrap"><table class="sc-table sc-table--dense"><thead class="sc-table__head"><tr>' +
            '<th class="sc-table__th">事项</th><th class="sc-table__th">办理状态</th><th class="sc-table__th">进度</th>' +
            '<th class="sc-table__th">责任人</th><th class="sc-table__th">服务日期</th><th class="sc-table__th">备注</th>' +
            '<th class="sc-table__th">操作</th></tr></thead><tbody>';
        $.each(list, function (i, s) {
            h += '<tr><td>' + esc(s.service_item || '—') + '</td><td>' + esc(s.service_status || '—') + '</td>' +
                '<td>' + esc(s.service_progress || '—') + '</td><td>' + esc(s.service_owner || '—') + '</td>' +
                '<td>' + esc(s.service_date || '—') + '</td><td>' + esc(s.remark || '—') + '</td>' +
                '<td><span class="sc-row-actions">' +
                '<button type="button" class="sc-btn sc-btn--text" title="编辑" onclick="openSvcEdit(' + s.id + ')"><i class="bi bi-pencil-square"></i></button>' +
                '<button type="button" class="sc-btn sc-btn--text is-danger" title="删除" onclick="delSvc(' + s.id + ')"><i class="bi bi-trash"></i></button>' +
                '</span></td></tr>';
        });
        h += '</tbody></table></div>';
    }
    h += '<button type="button" class="sc-btn sc-btn--secondary" style="margin-top:8px" onclick="openSvcAdd()"><i class="bi bi-plus-lg"></i> 新增服务事项</button></section>';
    $('#dtSvcWrap').html(h);
}

function ensureSvcDicts(done) {
    var pending = 2;
    function next() { pending -= 1; if (pending === 0 && done) done(); }
    dictSelect('残疾人服务事项', '#svcItem', '请选择', next);
    dictSelect('服务办理状态', '#svcStatus', '请选择', next);
}

function openSvcAdd() {
    if (!dtCurrent) return;
    $('#svcForm')[0].reset();
    $('#svcId').val('');
    $('#svcTitle').html('<i class="bi bi-clipboard-check"></i> 新增服务事项');
    ensureSvcDicts(function () {
        $('#svcModal').modal('show');
    });
}

function openSvcEdit(sid) {
    var s = svcCache[sid];
    if (!s || !dtCurrent) return;
    $('#svcForm')[0].reset();
    $('#svcId').val(s.id);
    $('#svcTitle').html('<i class="bi bi-pencil-square"></i> 编辑服务事项');
    ensureSvcDicts(function () {
        $('#svcItem').val(s.service_item || '');
        $('#svcStatus').val(s.service_status || '');
        $('#svcForm [name="service_progress"]').val(s.service_progress || '');
        $('#svcForm [name="service_owner"]').val(s.service_owner || '');
        $('#svcForm [name="service_date"]').val(s.service_date || '');
        $('#svcForm [name="remark"]').val(s.remark || '');
        $('#svcModal').modal('show');
    });
}

function refreshDtAndList() {
    load(cp);
    if (!dtCurrent) return;
    $.get('/disabled/api/detail/' + dtCurrent, function (r) {
        if (r.success) openDt(r.data);
    });
}

function delSvc(sid) {
    if (!dtCurrent) return;
    if (!confirm('确定删除该服务记录？')) return;
    $.post('/disabled/api/services/' + dtCurrent + '/delete/' + sid, function (r) {
        if (!r.success) { showToast(r.msg || '删除失败', 'danger'); return; }
        showToast('删除成功', 'success');
        refreshDtAndList();
    });
}

function openAdd(){
    $('#fm')[0].reset();$('#fid').val('');
    $('#fmTitle').text('新增残疾人员');$('#fmMeta').text('新增残疾人员记录');
    loadDisabledDicts();$('#fmModal').modal('show');
}
function openEdit(id){
    $.get('/disabled/api/detail/'+id,function(r){
        if(!r.success){showToast(r.msg||'读取数据失败','danger');return;}
        var d=r.data;
        $('#fm')[0].reset();$('#fid').val(d.id);
        $('#fmTitle').text('编辑残疾人员');
        $('#fmMeta').text((d.name||'') + ' · ' + (d.disability_type||'') + (d.disability_level||'') + ' · 修改后保存生效');
        var fields=['village_group','name','id_card','gender','nation','education','phone','registered_address',
            'guardian_name','guardian_relation','guardian_phone','cert_no','disability_type','disability_level',
            'disability_detail','subsidy_living','subsidy_nursing'];
        loadDisabledDicts(function(){
            $.each(fields,function(_,field){$('#fm [name="'+field+'"]').val(d[field] == null ? '' : d[field]);});
            $('#dtModal').one('hidden.bs.modal', function () { $('#fmModal').modal('show'); });
            $('#dtModal').modal('hide');
        });
    });
}
$('#fm').submit(function(e){e.preventDefault();
    $.post('/disabled/api/save',$(this).serialize(),function(r){if(r.success){showToast('保存成功','success');$('#fmModal').modal('hide');load(cp);}else showToast(r.msg,'danger');});
});
$('#svcForm').submit(function(e){e.preventDefault();
    if(!dtCurrent)return;
    $.post('/disabled/api/services/'+dtCurrent+'/save',$(this).serialize(),function(r){
        if(!r.success){showToast(r.msg||'保存失败','danger');return;}
        showToast('保存成功','success');$('#svcModal').modal('hide');refreshDtAndList();
    });
});
function del(id){
    if(!confirm('确定删除？'))return;
    $.post('/disabled/api/delete/'+id,function(r){if(r.success){showToast('删除成功','success');$('#dtModal').modal('hide');load(cp);}else showToast(r.msg,'danger');});
}
function exportDisabledData() {
    var q = {name:$('#sName').val(),id_card:$('#sIdCard').val(),village:$('#sVillage').val(),dtype:$('#sType').val(),
        service_item:$('#sServiceItem').val(),service_status:$('#sServiceStatus').val()};
    Object.keys(q).forEach(function(k){ if(!q[k]) delete q[k]; });
    var qs = $.param(q);
    window.location.href = '/disabled/api/export' + (qs ? '?' + qs : '');
}
function doImport(){
    importWizard({fileSel:'#impFile', resultSel:'#impRes', endpoint:'/disabled/api/import',
        extraData:{mode:$('input[name="impMode"]:checked').val() || 'append'},
        onDone:function(){ load(1); }});
}
function loadDisabledDicts(done) {
    var pending=3;
    function next(){pending-=1;if(pending===0&&done)done();}
    dictSelect('所在组', 'select[name="village_group"]', '请选择', next);
    // B1（GLB-43）：字典种子名是「残疾类型」，原「残疾类别」查不到 → 下拉恒空
    dictSelect('残疾类型', 'select[name="disability_type"]', '请选择', next);
    dictSelect('残疾等级', 'select[name="disability_level"]', '请选择', next);
}
function loadDisabledSearchDicts() {
    dictSelect('残疾类型', '#sType', '全部残疾类别');
    dictSelect('残疾人服务事项', '#sServiceItem', '全部服务事项');
    dictSelect('服务办理状态', '#sServiceStatus', '全部办理状态');
}
function fillDisabledFromResident(d) {
    if (!d) return;
    var map = {
        village_group: d.village_group || '',
        id_card: d.id_card || '',
        gender: d.gender || '',
        nation: d.nation || '',
        education: d.education || '',
        phone: d.phone || '',
        registered_address: d.registered_address || d.address || ''
    };
    $.each(map, function(field, value) {
        if (value) $('#fm [name="' + field + '"]').val(value);
    });
}

function lookupDisabledResidentByName() {
    var name = ($('#fm [name="name"]').val() || '').trim();
    if (!name) return;
    $.get('/resident/api/lookup', {name: name}, function(r) {
        if (r.success && r.data && r.data.length > 0) {
            var d = r.data[0];
            $('#fm [name="name"]').val(d.name || name);
            fillDisabledFromResident(d);
            showToast('已读取居民信息', 'success');
        }
    });
}

$(function(){
    loadDisabledSearchDicts();loadDisabledDicts();load(1);
    $('#sName,#sIdCard,#sVillage').on('keydown',function(e){if(e.key==='Enter'){e.preventDefault();load(1);}});
    $('#fm [name="name"]').on('input', function() {
        clearTimeout(disabledLookupTimer);
        var name = ($(this).val() || '').trim();
        if (!name) return;
        disabledLookupTimer = setTimeout(lookupDisabledResidentByName, 400);
    }).on('blur', lookupDisabledResidentByName);
});

// R-20：内联事件依赖函数挂 window（IIFE 隔离顶层作用域；内联 onclick/onblur 契约保留）
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ load: load, openAdd: openAdd, exportDisabledData: exportDisabledData, doImport: doImport, del: del, openEdit: openEdit, openSvcAdd: openSvcAdd, openSvcEdit: openSvcEdit, delSvc: delSvc }); }
})();

// 2026-11-19 换壳配套：导入弹窗投放区（app.js wireImportDropzone 全局单源）。
// #impFile 仍是真实 file input，doImport 链路不变；弹窗每次打开经 show.bs.modal 复位。
$(function () {
    wireImportDropzone({
        fileSel: '#impFile', dropSel: '#disabledImpDrop', stagedSel: '#disabledImpStaged',
        stagedNameSel: '#disabledImpStagedName', stagedMetaSel: '#disabledImpStagedMeta',
        submitBtnSel: '#impSubmit', reselectSel: '#disabledImpReselect', removeSel: '#disabledImpRemove',
        modalSel: '#impModal'
    });
});
