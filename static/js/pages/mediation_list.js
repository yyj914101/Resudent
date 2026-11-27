/* mediation_list.js — 综治调解列表（crudTable + 4 弹窗）
 * 062 P3 v4.0：仅换渲染层 class（sc-badge/sc-row-actions/sc-btn--text/sc-table），
 * crudTable 配置、端点、弹窗函数名（openAddModal/openEdit/openDetail/openClose/openReturn）零改动。
 */
(function () {
var T={add:'新增调解案件',edit:'编辑调解案件',none:'无',pic:'图片',noPic:'暂无图片附件',detail:'详情',editBtn:'编辑',close:'办结',ret:'退回',readFail:'读取失败',saveFail:'保存失败',linked:'已关联居民信息',empty:'暂无数据'};
var statusMap={mediating:'<span class="sc-badge sc-badge--warning">调解中</span>',closed:'<span class="sc-badge sc-badge--success">已办结</span>',returned:'<span class="sc-badge sc-badge--danger">已退回</span>'};

function attachmentCount(item){return item.attachment_count||(item.attachments?item.attachments.length:0)||0;}
function buildAttachmentBadge(item){let c=attachmentCount(item);return c?'<span class="sc-badge sc-badge--primary">'+c+' 张</span>':'<span class="text-muted">'+T.none+'</span>';}
function renderAttachments(list){
  if(!list||!list.length)return '<div class="text-muted small">'+T.noPic+'</div>';
  let h='<div style="display:flex;flex-wrap:wrap;gap:8px">';
  $.each(list,function(_,a){
    h+='<a href="'+esc(a.url)+'" target="_blank" style="border:1px solid var(--c-line-3);border-radius:var(--r-ctrl);padding:4px;text-decoration:none">'
      +'<img src="'+esc(a.url)+'" style="width:96px;height:72px;object-fit:cover;display:block;">'
      +'<div class="small text-muted text-truncate" style="width:96px;">'+esc(a.original_name||T.pic)+'</div></a>';
  });
  return h+'</div>';
}
function setCurrentAttachments(list){$('#currentAttachments').html(renderAttachments(list));}

var list = crudTable({
    endpoint: '/mediation/api/list',
    emptyColspan: 14,
    emptyText: T.empty,
    columnToggle: true, // 028 新增推广页：列显隐（保留既有能力）
    columns: [
        {field: '__index', title: '序号'},
        {field: 'case_no', esc: true, title: '案件编号'},
        {title: '纠纷类型', render: function(item) { return '<div class="sc-ellipsis" style="max-width:150px" title="' + esc(item.dispute_type) + '">' + esc(item.dispute_type) + '</div>'; }},
        {field: 'applicant', esc: true, title: '申请人'},
        {field: 'applicant_id_card', esc: true, title: '申请人身份证'},
        {field: 'applicant_village_group', esc: true, title: '所在组'},
        {field: 'respondent', esc: true, title: '被申请人'},
        {field: 'respondent_id_card', esc: true, title: '被申请人身份证'},
        {field: 'people_count', esc: true, title: '涉及人数'},
        {field: 'mediator', esc: true, title: '调解人'},
        {field: 'start_date', esc: true, title: '开始日期'},
        {title: '状态', render: function(item) { return statusMap[item.status] || esc(item.status); }},
        {title: '附件', render: function(item) { return buildAttachmentBadge(item); }},
        {title: '操作', render: function(item) { return '<div class="sc-row-actions">' + buildActions(item) + '</div>'; }}
    ],
    onLoaded: function(resp) { $('#pageStat').text('共 ' + (resp.total || 0) + ' 条 · 每页 20 条'); },
    onRowClick: function(item) { openDetail(item.id); }   // ADR-022：单击行打开详情（crud.js 统一委托，操作列按钮不受影响）
});

function buildActions(item){
  let h='<button type="button" class="sc-btn sc-btn--text" onclick="openDetail('+item.id+')">'+T.detail+'</button>'
       +'<button type="button" class="sc-btn sc-btn--text" onclick="openEdit('+item.id+')">'+T.editBtn+'</button>';
  // 验收 4：办结/退回仅「调解中」显示
  if(item.status==='mediating') h+='<button type="button" class="sc-btn sc-btn--text" onclick="openClose('+item.id+')">'+T.close+'</button>'
       +'<button type="button" class="sc-btn sc-btn--text is-danger" onclick="openReturn('+item.id+')">'+T.ret+'</button>';
  return h;
}
$('#searchForm').submit(function(e){e.preventDefault();list.reload(1);});

function lookupApplicant(){let name=$('#applicantInput').val().trim();if(!name)return;$.getJSON('/resident/api/lookup',{name:name},function(resp){if(resp.success&&resp.data&&resp.data.length){let r=resp.data[0];$('#applicantIdCard').val(r.id_card||'');$('#applicantVillageGroup').val(r.village_group||'');showToast(T.linked,'success');}});}
$('#applicantInput').on('blur',lookupApplicant).on('keydown',function(e){if(e.key==='Enter'){e.preventDefault();lookupApplicant();}});

function openAddModal(){
  $('#editId').val(0);$('#formModalTitle').text(T.add);$('#saveForm')[0].reset();$('#currentAttachments').empty();$('#attachmentsInput').val('');getModal('formModal').show();
}
function openEdit(id){
  $('#editId').val(id);$('#formModalTitle').text(T.edit);
  $.getJSON('/mediation/api/detail/'+id,function(resp){
    if(!resp.success){showToast(resp.msg||T.readFail,'danger');return;}
    let item=resp.data;
    $('#saveForm [name="dispute_type"]').val(item.dispute_type);
    $('#saveForm [name="location"]').val(item.location);
    $('#saveForm [name="applicant"]').val(item.applicant);
    $('#saveForm [name="applicant_id_card"]').val(item.applicant_id_card);
    $('#saveForm [name="applicant_village_group"]').val(item.applicant_village_group);
    $('#saveForm [name="respondent"]').val(item.respondent);
    $('#saveForm [name="respondent_id_card"]').val(item.respondent_id_card);
    $('#saveForm [name="people_count"]').val(item.people_count||1);
    $('#saveForm [name="mediator"]').val(item.mediator);
    $('#saveForm [name="start_date"]').val(item.start_date);
    $('#saveForm [name="mediation_date"]').val(item.mediation_date);
    $('#saveForm [name="content"]').val(item.content);
    setCurrentAttachments(item.attachments);$('#attachmentsInput').val('');getModal('formModal').show();
  });
}
$('#saveForm').submit(function(e){e.preventDefault();$.ajax({url:'/mediation/api/save',type:'POST',data:new FormData(this),processData:false,contentType:false,success:function(resp){if(resp.success){getModal('formModal').hide();showToast(resp.msg,'success');list.reload();}else showToast(resp.msg,'danger');},error:function(){showToast(T.saveFail,'danger');}});});

function openDetail(id){
  $.getJSON('/mediation/api/detail/'+id,function(resp){
    if(!resp.success){showToast(resp.msg||T.readFail,'danger');return;}
    let item=resp.data;
    let status=item.status==='mediating'?'调解中':item.status==='closed'?'已办结':'已退回';
    let bCls=item.status==='closed'?'sc-badge--success':(item.status==='returned'?'sc-badge--danger':'sc-badge--warning');
    $('#medDetailTitle').text((item.applicant||'案件')+' · '+(item.dispute_type||'纠纷详情'));
    $('#medDetailBadge').attr('class','sc-badge '+bCls).text(status).prop('hidden',false);
    $('#medDetailMeta').text((item.case_no||'—')+' · 调解人：'+(item.mediator||'—')+' · 开始：'+(item.start_date||'—'));
    $('#medDetailEditBtn').prop('hidden',false).off('click').on('click',function(){
      getModal('detailModal').hide();
      openEdit(id);
    });
    function gf(lbl,val,num){
      let empty=val===null||val===undefined||val==='';
      return '<div class="gs-field"><span class="gs-label">'+lbl+'</span><span class="gs-value'+(empty?' gs-value--empty':'')+(num?' gs-num':'')+'">'+esc(empty?'未填写':val)+'</span></div>';
    }
    let h='<section class="gs-card"><div class="gs-card__head"><div class="gs-card__title">'
      +'<span class="gs-card__bar gs-card__bar--1"></span><span class="gs-card__index gs-card__index--1">01</span>'
      +'<h4 class="gs-card__name">基本与当事人信息</h4></div><span class="gs-card__count">共 12 项字段</span></div>'
      +'<div class="gs-grid4">'
      +gf('案件编号',item.case_no,true)+gf('纠纷类型',item.dispute_type)+gf('申请人',item.applicant)+gf('申请人身份证',item.applicant_id_card,true)
      +gf('所在组',item.applicant_village_group)+gf('被申请人',item.respondent)+gf('被申请人身份证',item.respondent_id_card,true)+gf('涉及人数',item.people_count,true)
      +gf('事发地点',item.location)+gf('调解人',item.mediator)+gf('调解日期',item.mediation_date,true)+gf('办结日期',item.end_date,true)
      +'</div></section>'
      +'<section class="gs-card"><div class="gs-card__head"><div class="gs-card__title">'
      +'<span class="gs-card__bar gs-card__bar--2"></span><span class="gs-card__index gs-card__index--2">02</span>'
      +'<h4 class="gs-card__name">纠纷内容与办理结果</h4></div></div>'
      +'<div class="gs-slot"><span class="gs-slot__tag">纠纷内容</span>'+esc(item.content||'未填写')+'</div>'
      +(item.status==='closed'?'<div class="gs-slot"><span class="gs-slot__tag">办结结果</span>'+esc(item.result||'未填写')+'</div>':'')
      +(item.status==='returned'?'<div class="gs-slot"><span class="gs-slot__tag">退回原因</span>'+esc(item.return_reason||'未填写')+'</div>':'')
      +'</section>'
      +'<section class="gs-card"><div class="gs-card__head"><div class="gs-card__title">'
      +'<span class="gs-card__bar gs-card__bar--3"></span><span class="gs-card__index gs-card__index--3">03</span>'
      +'<h4 class="gs-card__name">图片附件</h4></div></div>'
      +'<div>'+renderAttachments(item.attachments)+'</div></section>';
    $('#detailBody').html(h);getModal('detailModal').show();
  });
}
function openClose(id){$('#closeForm')[0].reset();$('#closeId').val(id);$('#closeForm [name="end_date"]').val(new Date().toISOString().slice(0,10));getModal('closeModal').show();}
$('#closeForm').submit(function(e){e.preventDefault();let id=$('#closeId').val();$.post('/mediation/api/close/'+id,$(this).serialize(),function(resp){if(resp.success){getModal('closeModal').hide();showToast(resp.msg,'success');list.reload();}else showToast(resp.msg,'danger');});});
function openReturn(id){$('#returnForm')[0].reset();$('#returnId').val(id);getModal('returnModal').show();}
$('#returnForm').submit(function(e){e.preventDefault();let id=$('#returnId').val();$.post('/mediation/api/return/'+id,$(this).serialize(),function(resp){if(resp.success){getModal('returnModal').hide();showToast(resp.msg,'success');list.reload();}else showToast(resp.msg,'danger');});});
if (window.SC && window.SC.exportPageGlobals) { window.SC.exportPageGlobals({ openAddModal: openAddModal, openEdit: openEdit, openDetail: openDetail, openClose: openClose, openReturn: openReturn }); }
$(function(){list.reload(1);});
})();
