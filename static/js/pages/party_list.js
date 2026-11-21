(function () {
var cp=1,ps=20,tp=0;
function load(p){
    cp=p;
    $('#tb').html('<tr><td colspan="17" class="text-center py-4 text-muted">加载中...</td></tr>');
    $.get('/party/api/list',{page:p,limit:ps,name:$('#sName').val(),id_card:$('#sIdCard').val(),member_identity:$('#sType').val(),branch:$('#sBranch').val()},function(r){
        if(!r.success||!r.data)return;
        var d=r.data,items=d.data||[]; tp=d.pages||0; $('#fi').text('共'+d.total+'条');
        var h='';
        if(!items.length) h='<tr><td colspan="17" class="text-center py-4 text-muted">暂无数据</td></tr>';
        else for(var i=0;i<items.length;i++){
            var x=items[i];
            h+='<tr rid="'+x.id+'" style="cursor:pointer"><td class="col-index">'+( (cp-1)*ps+i+1)+'</td>'+
                '<td class="col-name"><span class="party-name">'+esc(x.name)+'</span></td><td class="col-id-card">'+esc(x.id_card)+'</td><td class="col-gender">'+esc(x.gender)+'</td>'+
                '<td class="col-date">'+x.birth_date+'</td><td class="col-age">'+x.age+'</td><td class="col-party-age">'+x.party_age+'</td>'+
                '<td class="col-nation">'+esc(x.nation)+'</td><td class="col-education">'+esc(x.education)+'</td>'+
                '<td class="col-date">'+x.join_date+'</td><td class="col-date">'+x.confirm_date+'</td>'+
                '<td class="col-type">'+(x.source==='auto'?'<span class="sc-badge sc-badge--success">自动识别</span>':esc(x.member_type_display))+'</td><td class="col-phone">'+esc(x.phone)+'</td>'+
                '<td class="col-branch" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+esc(x.branch)+'">'+esc(x.branch)+'</td>'+
                '<td class="col-address" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+esc(x.current_address)+'">'+esc(x.current_address)+'</td>'+
                '<td class="col-fee"><button class="sc-btn sc-btn--text btn-fee" rid="'+x.id+'">查缴</button></td>'+
                '<td class="col-action">'+(x.source==='auto'?'<button class="sc-btn sc-btn--text btn-auto" rid="'+x.id+'" resid="'+x.resident_id+'">转正</button>':'<button class="sc-btn sc-btn--text is-danger btn-del" rid="'+x.id+'"><i class="bi bi-trash"></i></button>')+'</td></tr>';
        }
        $('#tb').html(h);
        // Click row -> detail（详情 v2：读写分离 + 分栏页签，ADR-022 令牌驱动）
        $('#tb tr[rid]').on('click',function(){
            var rid=$(this).attr('rid');
            if(rid==='0'){
                var resid=$(this).find('.btn-auto').attr('resid');
                if(resid&&confirm('该居民来自自动识别，是否转为正式党员？')){
                    $.post('/party/api/auto-create',{resident_id:resid},function(r){if(r.success){showToast('已转为正式党员');load(cp);}else showToast(r.msg,'danger');});
                }
                return;
            }
            openDetail(rid);
        });
        // btn-fee 由 document 委托 click.partyFee 统一处理（R-21 合一，删元素级重复绑定）
        $('.btn-auto').on('click',function(e){e.stopPropagation();
            var resid=$(this).attr('resid');
            if(!confirm('确认转为正式党员？将自动补全基本信息。'))return;
            $.post('/party/api/auto-create',{resident_id:resid},function(r){if(r.success){showToast('已转为正式党员');load(cp);}else showToast(r.msg,'danger');});
        });
        // Delete
        $('.btn-del').on('click',function(e){e.stopPropagation();
            var rid=$(this).attr('rid');
            if(!confirm('确定删除？'))return;
            $.post('/party/api/delete/'+rid,function(r3){if(r3.success){showToast('删除成功');load(cp);}else showToast(r3.msg,'danger');});
        });
        // Pagination
        var pg='<li class="page-item '+(cp<=1?'disabled':'')+'"><a class="page-link" href="javascript:load('+(cp-1)+')">上一页</a></li>';
        if(tp > 7){
            pg+='<li class="page-item"><select class="page-select" id="partyPageJump" title="跳转页码">';
            for(var pi=1;pi<=tp;pi++) pg+='<option value="'+pi+'" '+(pi===cp?'selected':'')+'>'+pi+'</option>';
            pg+='</select></li>';
        }else{
            pg+='<li class="page-item"><span class="page-link current-page">'+cp+'</span></li>';
        }
        pg+='<li class="page-item '+(cp>=tp?'disabled':'')+'"><a class="page-link" href="javascript:load('+(cp+1)+')">下一页</a></li>';
        $('#pg').html(pg);
        $('#partyPageJump').on('change',function(){load(parseInt($(this).val()));});
        // 2026-11-19：重渲染后按开关当前态重放脱敏（默认开）
        if (document.getElementById('pwPrivacySwitch') &&
            document.getElementById('pwPrivacySwitch').getAttribute('aria-checked') === 'true') {
            applyPartyMasking(true);
        }
    });
}

// ══════════ 党员详情弹窗 v2（ADR-022 视觉基线 v4.0 · 令牌驱动） ══════════
// 设计要点：读写分离（主视图只读，编辑走行内抽屉）+ 分栏页签/全量平铺双视图 +
// 敏感号段脱敏（data-raw/data-masked 节点遍历替换）。全部交互为委托绑定，
// 弹窗内容重渲染后仍生效；不做任何 SSR 影响（详情为纯 JS 渲染）。
var pdState={id:0,masked:false,allView:false,drawerSave:null};

function pdMaskId(v){
    v=String(v||'');
    return v.length>=10 ? v.slice(0,6)+'********'+v.slice(-4) : v;
}
function pdMaskPhone(v){
    v=String(v||'');
    return v.length>=7 ? v.slice(0,3)+'****'+v.slice(-4) : v;
}
function pdFamHead(d){
    var fam=d.family||[];
    if(!fam.length) return null;
    for(var i=0;i<fam.length;i++){ if(fam[i].relationship==='户主本人') return fam[i]; }
    return fam[0];
}
function pdSelfRelation(d){
    var fam=d.family||[];
    for(var i=0;i<fam.length;i++){ if(fam[i].id_card&&fam[i].id_card===d.id_card) return fam[i].relationship; }
    return '';
}
function pdField(label,valueHtml,span2){
    return '<div class="pd-field'+(span2?' pd-field--span2':'')+'"><span class="pd-label">'+label+'</span><div class="pd-value">'+valueHtml+'</div></div>';
}
function pdCopyBtn(text,label){
    if(!text) return '';
    return '<button type="button" class="pd-copy" data-copy="'+esc(text)+'" data-copy-label="'+label+'" title="复制'+label+'"><i class="bi bi-clipboard"></i></button>';
}

function openDetail(id){
    $.get('/party/api/detail/'+id,function(r2){
        if(!r2.success)return;
        pdState.id=id; pdState.masked=false; pdState.allView=false; pdState.drawerSave=null;
        renderDetail(r2.data);
        $('#dtModal').modal('show');
    });
}
function refreshDetail(id){openDetail(id);}

function renderDetail(d){
    var fam=d.family||[], trs=d.transfers||[], pos=d.positions||[], opos=d.organization_positions||[];
    var linked=!!fam.length;
    var head=pdFamHead(d);
    // —— 头部：姓名 + 党籍徽章组 + 支部 ——
    var badges='';
    if(d.member_identity) badges+='<span class="pd-badge pd-badge--success"><span class="pd-dot"></span>'+esc(d.member_identity)+'</span>';
    if(d.party_age!==''&&d.party_age!==null&&d.party_age!==undefined) badges+='<span class="pd-badge pd-badge--info pd-badge--num">党龄 '+d.party_age+' 年</span>';
    if(d.party_status||d.member_mobility) badges+='<span class="pd-badge pd-badge--primary">'+esc(d.party_status||'—')+' · '+esc(d.member_mobility||'—')+'</span>';
    var selfRel=pdSelfRelation(d);
    if(selfRel) badges+='<span class="pd-badge pd-badge--neutral">'+esc(selfRel)+'</span>';
    $('#dtTitle').text(d.name);
    $('#pdBadges').html(badges);
    $('#pdBranch').html('所属党支部：'+(d.branch?'<strong>'+esc(d.branch)+'</strong>':'<strong>未分配党支部</strong>'));
    // 头部工具态复位
    $('#pdPrivacyLabel').text('隐藏敏感号段');
    $('#pdLayoutLabel').text('切换全量平铺');
    $('#dtBody').removeClass('pd-all');
    // —— 01 基础与户籍信息（分类色 1 绿） ——
    var html='<section class="pd-card"><header class="pd-card__head">'+
        '<div class="pd-card__titlewrap"><span class="pd-catbar pd-catbar--1"></span><span class="pd-catindex pd-catindex--1">01</span>'+
        '<h2 class="pd-card__title">基础与户籍信息</h2><span class="pd-card__hint">（'+(linked?'已关联村民人口基础库':'未关联村民人口基础库')+'）</span></div>'+
        (d.household_type?'<span class="pd-card__meta">户籍性质：<strong>'+esc(d.household_type)+'</strong></span>':'')+
        '</header><div class="pd-grid">'+
        pdField('身份证号','<span class="pd-num pd-strong" data-raw="'+esc(d.id_card)+'" data-masked="'+pdMaskId(d.id_card)+'">'+esc(d.id_card||'—')+'</span>'+pdCopyBtn(d.id_card,'身份证号'))+
        pdField('性别 / 出生日期 / 年龄','<span class="pd-strong">'+esc(d.gender||'—')+'</span><span class="pd-sep">·</span><span class="pd-num">'+(d.birth_date||'—')+'</span>'+(d.age?'<span class="pd-badge pd-badge--neutral pd-badge--num">'+d.age+' 岁</span>':''))+
        pdField('民族 / 文化程度','<span class="pd-strong">'+esc(d.nation||'—')+'</span><span class="pd-sep">·</span><span class="pd-strong">'+esc(d.education||'—')+'</span>')+
        pdField('手机号码','<span class="pd-num pd-strong" data-raw="'+esc(d.phone)+'" data-masked="'+pdMaskPhone(d.phone)+'">'+esc(d.phone||'—')+'</span>'+pdCopyBtn(d.phone,'手机号码'));
    // Row 2：现居地址（跨 2 列）+ 家庭概况摘要（跨 2 列）
    html+='<div class="pd-field pd-field--span2"><span class="pd-label">现居地址</span><div class="pd-slot">'+
        '<span class="pd-slot__text">'+esc(d.current_address||'—')+'</span>'+
        (d.current_address?'<button type="button" class="pd-slot__act" data-copy="'+esc(d.current_address)+'" data-copy-label="现居地址">复制</button>':'')+
        '</div></div>';
    if(linked){
        var note=head.family_situation||'';
        html+='<div class="pd-field pd-field--span2"><span class="pd-label">家庭概况摘要（读取居民信息）</span><div class="pd-slot">'+
            '<span class="pd-slot__text"><span>'+esc(head.name)+'（'+fam.length+'人户）</span><span class="pd-sep">|</span>'+
            '<span class="pd-slot__note">家庭情况备注：<strong class="pd-fam-note" data-rid="'+head.resident_id+'">'+(note?esc(note):'未填写（点击右侧登记）')+'</strong></span></span>'+
            '<button type="button" class="pd-slot__act" data-drawer="family-note" data-resident-id="'+head.resident_id+'" data-resident-name="'+esc(head.name)+'" data-resident-idcard="'+esc(head.id_card)+'" data-resident-note="'+esc(note)+'">登记家庭情况</button>'+
            '</div></div>';
    }else{
        html+='<div class="pd-field pd-field--span2"><span class="pd-label">家庭概况摘要（读取居民信息）</span><div class="pd-slot">'+
            '<span class="pd-slot__text pd-muted">未关联到村民人口基础库，暂无家庭概况</span></div></div>';
    }
    html+='</div></section>';
    // —— 02 党员党籍信息（分类色 2 青） ——
    html+='<section class="pd-card"><header class="pd-card__head">'+
        '<div class="pd-card__titlewrap"><span class="pd-catbar pd-catbar--2"></span><span class="pd-catindex pd-catindex--2">02</span>'+
        '<h2 class="pd-card__title">党员党籍信息</h2></div>'+
        '<button type="button" class="pd-ghost-act" data-pd-edit="'+d.id+'"><i class="bi bi-pencil"></i> 修改党籍属性</button>'+
        '</header><div class="pd-grid">'+
        pdField('入党时间 / 转正时间','<span class="pd-num pd-strong">'+(d.join_date||'—')+'</span><span class="pd-arrow">→</span><span class="pd-num">'+(d.confirm_date||'—')+'</span>')+
        pdField('党员身份 / 党龄核算','<span class="pd-strong">'+esc(d.member_identity||'—')+'</span>'+(d.party_age?'<span class="pd-badge pd-badge--neutral pd-badge--num">'+d.party_age+' 年</span>':''))+
        pdField('党员流动状态 / 在册状态',(d.member_mobility?'<span class="pd-badge pd-badge--success">'+esc(d.member_mobility)+'</span>':'<span class="pd-muted">—</span>')+(d.party_status?'<span class="pd-badge pd-badge--primary">'+esc(d.party_status)+'</span>':''))+
        pdField('在职状态','<span class="'+(d.employment_status?'pd-strong':'pd-muted')+'">'+esc(d.employment_status||'未填写')+'</span>')+
        '</div></section>';
    // —— 03~06 关联记录：分栏页签（读写分离，新增走抽屉） ——
    html+='<section class="pd-card"><header class="pd-tabs-head">'+
        '<div class="pd-rail" role="tablist">'+
        '<button type="button" class="pd-tab is-active" data-panel="family"><span class="pd-catbar pd-catbar--4"></span>03 家庭概况（读取居民）<span class="pd-tab__count">'+fam.length+'</span></button>'+
        '<button type="button" class="pd-tab" data-panel="flow"><span class="pd-catbar pd-catbar--3"></span>04 党员流动/转入转出<span class="pd-tab__count">'+trs.length+'</span></button>'+
        '<button type="button" class="pd-tab" data-panel="post"><span class="pd-catbar pd-catbar--1"></span>05 党内任职信息<span class="pd-tab__count">'+pos.length+'</span></button>'+
        '<button type="button" class="pd-tab" data-panel="org"><span class="pd-catbar pd-catbar--2"></span>06 组织架构任职（身份证关联）<span class="pd-tab__count">'+opos.length+'</span></button>'+
        '</div><div class="pd-tabs-actions">'+
        '<button type="button" class="pd-btn pd-btn--secondary pd-btn--sm" data-drawer="flow-add"><span class="pd-plus">+</span> 新增流动记录</button>'+
        '<button type="button" class="pd-btn pd-btn--secondary pd-btn--sm" data-drawer="post-add"><span class="pd-plus">+</span> 新增任职</button>'+
        '</div></header>';
    html+=pdFamilyPanel(fam)+pdFlowPanel(trs)+pdPostPanel(pos)+pdOrgPanel(opos,d.id_card);
    html+='</section>';
    $('#dtBody').html(html);
    // —— 底栏：数据核验状态 + 真实最近更新时间 ——
    $('#pdFootStatus').html(
        '<span class="pd-foot__ok"><span class="pd-foot__dot'+(linked?'':' pd-foot__dot--muted')+'"></span>'+
        (linked?'数据核验正常 · 已与人口库实时同步':'未关联村民人口基础库 · 基础信息为党员建档数据')+'</span>'+
        '<span class="pd-foot__sep">|</span><span>最近更新：<span class="pd-foot__num">'+esc(d.updated_at||'—')+'</span></span>');
}

// —— 面板（含全量平铺模式下的分区标题） ——
function pdPanelHead(idx,catCls,idxCls,title,isFirst){
    return '<div class="pd-panel-title'+(isFirst?' is-first':'')+'"><span class="pd-catbar '+catCls+'"></span>'+
        '<span class="pd-catindex '+idxCls+'">'+idx+'</span><span class="pd-panel-title__text">'+title+'</span></div>';
}
function pdFamilyPanel(fam){
    var h='<div class="pd-panel is-active" data-panel="family">'+
        pdPanelHead('03','pd-catbar--4','pd-catindex--4','家庭概况（读取居民信息）',true)+
        '<div class="pd-tablewrap"><table class="pd-table"><thead><tr>'+
        '<th class="pd-num">#</th><th>姓名</th><th>民族</th><th>性别</th><th class="pd-num">年龄</th><th>与户主关系</th><th class="pd-num">身份证号</th><th class="pd-num">联系电话</th><th>家庭情况备注</th><th style="text-align:right">操作</th>'+
        '</tr></thead><tbody>';
    if(!fam.length){
        h+='<tr><td colspan="10" class="pd-empty"><span class="pd-empty__text">未关联到居民信息或暂无家庭成员</span></td></tr>';
    }else{
        for(var i=0;i<fam.length;i++){
            var m=fam[i];
            h+='<tr><td class="pd-num pd-cell-muted">'+(i+1)+'</td><td class="pd-strong">'+esc(m.name)+'</td><td>'+esc(m.nation||'—')+'</td><td>'+esc(m.gender||'—')+'</td><td class="pd-num">'+m.age+'</td>'+
                '<td><span class="pd-badge pd-badge--neutral">'+esc(m.relationship||'—')+'</span></td>'+
                '<td class="pd-num" data-raw="'+esc(m.id_card)+'" data-masked="'+pdMaskId(m.id_card)+'">'+esc(m.id_card||'—')+'</td>'+
                '<td class="pd-num" data-raw="'+esc(m.phone)+'" data-masked="'+pdMaskPhone(m.phone)+'">'+esc(m.phone||'—')+'</td>'+
                '<td>'+(m.family_situation?esc(m.family_situation):'<span class="pd-faint pd-fam-note" data-rid="'+m.resident_id+'">暂无特殊家庭情况说明</span>')+'</td>'+
                '<td class="pd-cell-right"><button type="button" class="pd-text-act" data-drawer="family-note" data-resident-id="'+m.resident_id+'" data-resident-name="'+esc(m.name)+'" data-resident-idcard="'+esc(m.id_card)+'" data-resident-note="'+esc(m.family_situation||'')+'">编辑家庭情况</button></td></tr>';
        }
    }
    return h+'</tbody></table></div></div>';
}
function pdFlowPanel(trs){
    var h='<div class="pd-panel" data-panel="flow">'+
        pdPanelHead('04','pd-catbar--3','pd-catindex--3','党员流动 / 转入转出记录')+
        '<div class="pd-tablewrap"><table class="pd-table"><thead><tr>'+
        '<th class="pd-num">#</th><th>流转类型</th><th class="pd-num">日期</th><th>原党支部</th><th>目标党支部</th><th>原因</th><th>备注</th><th style="text-align:right">操作</th>'+
        '</tr></thead><tbody>';
    if(!trs.length){
        h+='<tr><td colspan="8" class="pd-empty"><span class="pd-empty__text">暂无党员流动或组织关系转接记录</span>'+
            '<button type="button" class="pd-text-act" data-drawer="flow-add">+ 立即登记第一条流动记录</button></td></tr>';
    }else{
        for(var i=0;i<trs.length;i++){
            var t=trs[i];
            h+='<tr><td class="pd-num pd-cell-muted">'+t.seq_no+'</td><td><span class="pd-badge pd-badge--info">'+esc(t.transfer_type||'—')+'</span></td>'+
                '<td class="pd-num">'+(t.transfer_date||'—')+'</td><td>'+esc(t.from_branch||'—')+'</td><td>'+esc(t.to_branch||'—')+'</td>'+
                '<td>'+esc(t.reason||'—')+'</td><td class="pd-cell-muted">'+esc(t.remark||'—')+'</td>'+
                '<td class="pd-cell-right"><button type="button" class="pd-text-act pd-text-act--danger" data-del-transfer="'+t.id+'">删除</button></td></tr>';
        }
    }
    return h+'</tbody></table></div></div>';
}
function pdPostPanel(pos){
    var h='<div class="pd-panel" data-panel="post">'+
        pdPanelHead('05','pd-catbar--1','pd-catindex--1','党内任职信息')+
        '<div class="pd-tablewrap"><table class="pd-table"><thead><tr>'+
        '<th>职务名称</th><th class="pd-num">开始时间</th><th class="pd-num">结束时间</th><th>备注说明</th><th style="text-align:right">操作</th>'+
        '</tr></thead><tbody>';
    if(!pos.length){
        h+='<tr><td colspan="5" class="pd-empty"><span class="pd-empty__text">暂无党内职务任职履历</span>'+
            '<button type="button" class="pd-text-act" data-drawer="post-add">+ 新增党内任职</button></td></tr>';
    }else{
        for(var i=0;i<pos.length;i++){
            var p=pos[i];
            h+='<tr><td class="pd-strong">'+esc(p.position_name)+'</td><td class="pd-num">'+(p.start_date||'—')+'</td><td class="pd-num">'+(p.end_date||'至今')+'</td>'+
                '<td class="pd-cell-muted">'+esc(p.remark||'—')+'</td>'+
                '<td class="pd-cell-right"><button type="button" class="pd-text-act pd-text-act--danger" data-del-position="'+p.id+'">删除</button></td></tr>';
        }
    }
    return h+'</tbody></table></div></div>';
}
function pdOrgPanel(opos,idCard){
    var h='<div class="pd-panel" data-panel="org">'+
        pdPanelHead('06','pd-catbar--2','pd-catindex--2','组织架构任职（按身份证自动关联）')+
        '<div class="pd-tablewrap"><table class="pd-table"><thead><tr>'+
        '<th>关联机构</th><th>姓名</th><th>担任职务</th><th class="pd-num">开始时间</th><th class="pd-num">结束时间</th><th>状态</th>'+
        '</tr></thead><tbody>';
    if(!opos.length){
        h+='<tr><td colspan="6" class="pd-empty"><span class="pd-empty__text">系统按身份证号（'+esc(idCard||'—')+'）自动检索：未关联到村两委或合作社组织架构任职</span></td></tr>';
    }else{
        for(var i=0;i<opos.length;i++){
            var op=opos[i];
            h+='<tr><td>'+esc(op.org_type)+'</td><td class="pd-strong">'+esc(op.name)+'</td><td>'+esc(op.position)+'</td><td class="pd-num">'+(op.start_date||'—')+'</td>'+
                '<td class="pd-num">'+(op.end_date||'—')+'</td><td>'+(op.is_current?'<span class="pd-badge pd-badge--success">现任</span>':'<span class="pd-badge pd-badge--neutral">离任</span>')+'</td></tr>';
        }
    }
    return h+'</tbody></table></div></div>';
}

// —— 行内抽屉（读写分离：家庭情况 / 流动记录 / 任职履历） ——
function openDrawer(title,contentHtml,onSave){
    $('#pdDrawerTitle').text(title);
    $('#pdDrawerContent').html(contentHtml);
    pdState.drawerSave=onSave||null;
    $('#pdDrawerBackdrop').removeAttr('hidden');
}
function closeDrawer(){
    $('#pdDrawerBackdrop').attr('hidden','');
    $('#pdDrawerContent').html('');
    pdState.drawerSave=null;
}
function openFamilyDrawer($btn){
    var rid=$btn.attr('data-resident-id');
    var name=$btn.attr('data-resident-name')||'';
    var idCard=$btn.attr('data-resident-idcard')||'';
    var note=$btn.attr('data-resident-note')||'';
    var html='<div class="pd-field"><label class="pd-label">成员姓名 / 身份证号</label>'+
        '<input type="text" class="pd-input pd-num" disabled value="'+esc(name)+(idCard?' ('+esc(idCard)+')':'')+'"></div>'+
        '<div class="pd-field"><label class="pd-label">家庭情况补充说明</label>'+
        '<textarea class="pd-input" id="pdFamNoteInput" rows="3" placeholder="请输入家庭成员就业、就学或生活困难情况...">'+esc(note)+'</textarea></div>';
    openDrawer('编辑家庭情况说明'+(name?'（'+name+'）':''),html,function(){
        var val=($('#pdFamNoteInput').val()||'').trim();
        $.post('/party/api/family-situation/save',{resident_id:rid,family_situation:val},function(resp){
            if(resp.success){
                // 就近回填：摘要 + 表格备注单元格（不整页刷新，保持只读视图结构）
                $('#dtBody').find('.pd-fam-note[data-rid="'+rid+'"]').each(function(){
                    $(this).text(val||($(this).hasClass('pd-faint')?'暂无特殊家庭情况说明':'未填写（点击右侧登记）'));
                    if(val) $(this).removeClass('pd-faint');
                });
                $('#dtBody').find('[data-drawer="family-note"][data-resident-id="'+rid+'"]').attr('data-resident-note',val);
                closeDrawer();
                showToast('家庭情况备注已更新');
            }else{
                showToast(resp.msg||'保存失败','danger');
            }
        });
    });
}
function openFlowDrawer(){
    var yr=new Date().getFullYear(),mo=('0'+(new Date().getMonth()+1)).slice(-2);
    var html='<div class="pd-form-grid">'+
        '<div class="pd-field"><label class="pd-label">流转类型</label><select class="pd-input" id="pdFlowType"><option value="转入">转入</option><option value="转出">转出</option></select></div>'+
        '<div class="pd-field"><label class="pd-label">变动日期</label><input type="date" class="pd-input pd-num" id="pdFlowDate" value="'+yr+'-'+mo+'-01"></div></div>'+
        '<div class="pd-field"><label class="pd-label">原党支部（须从党支部字典选择）</label><select class="pd-input pd-branch-sel" id="pdFlowFrom"><option value="">请选择</option></select></div>'+
        '<div class="pd-field"><label class="pd-label">目标党支部（须从党支部字典选择）</label><select class="pd-input pd-branch-sel" id="pdFlowTo"><option value="">请选择</option></select></div>'+
        '<div class="pd-field"><label class="pd-label">流动原因</label><input type="text" class="pd-input" id="pdFlowReason" placeholder="如：随子女居住 / 外出务工"></div>'+
        '<div class="pd-field"><label class="pd-label">备注</label><input type="text" class="pd-input" id="pdFlowRemark" placeholder="选填"></div>';
    openDrawer('新增党员流动 / 转入转出记录',html,function(){
        $.post('/party/api/transfer/save',{
            member_id:pdState.id, seq_no:1,
            transfer_type:$('#pdFlowType').val(),
            transfer_date:$('#pdFlowDate').val(),
            from_branch:$('#pdFlowFrom').val(),
            to_branch:$('#pdFlowTo').val(),
            reason:($('#pdFlowReason').val()||'').trim(),
            remark:($('#pdFlowRemark').val()||'').trim()
        },function(r){
            if(r.success){closeDrawer();showToast('已新增党员流动记录');refreshDetail(pdState.id);}
            else showToast(r.msg||'保存失败','danger');
        });
    });
    // 党支部为字典约束字段（054-A#6 服务端校验），下拉与编辑表单同源
    dictSelect('党支部','.pd-branch-sel','请选择');
}
function openPostDrawer(){
    var html='<div class="pd-field"><label class="pd-label">职务名称</label>'+
        '<input type="text" class="pd-input" id="pdPostName" placeholder="如：支部宣传委员"></div>'+
        '<div class="pd-form-grid">'+
        '<div class="pd-field"><label class="pd-label">开始时间</label><input type="date" class="pd-input pd-num" id="pdPostStart"></div>'+
        '<div class="pd-field"><label class="pd-label">结束时间（在任留空）</label><input type="date" class="pd-input pd-num" id="pdPostEnd"></div></div>'+
        '<div class="pd-field"><label class="pd-label">任职备注</label><input type="text" class="pd-input" id="pdPostRemark" placeholder="如：换届选举当选"></div>';
    openDrawer('新增党内任职履历',html,function(){
        $.post('/party/api/position/save',{
            member_id:pdState.id,
            position_name:($('#pdPostName').val()||'').trim(),
            start_date:$('#pdPostStart').val(),
            end_date:$('#pdPostEnd').val(),
            remark:($('#pdPostRemark').val()||'').trim()
        },function(r){
            if(r.success){closeDrawer();showToast('已新增党内任职信息');refreshDetail(pdState.id);}
            else showToast(r.msg||'保存失败','danger');
        });
    });
}

// —— 详情弹窗 v2 事件（委托绑定：内容重渲染后仍生效） ——
// 脱敏切换：遍历带 data-raw/data-masked 的节点整体替换（§5.3）
$('#dtModal').on('click','#pdPrivacyBtn',function(){
    pdState.masked=!pdState.masked;
    $('#dtModal').find('[data-raw]').each(function(){
        var raw=$(this).attr('data-raw')||'', msk=$(this).attr('data-masked')||raw;
        $(this).text(pdState.masked?msk:raw);
    });
    $('#pdPrivacyLabel').text(pdState.masked?'显示完整号段':'隐藏敏感号段');
    showToast(pdState.masked?'已开启敏感号段脱敏保护':'已显示完整身份证与手机号');
});
// 双视图切换：一屏分栏 vs 全量平铺（CSS 按 .pd-all 控制显隐）
$('#dtModal').on('click','#pdLayoutBtn',function(){
    pdState.allView=!pdState.allView;
    $('#dtBody').toggleClass('pd-all',pdState.allView);
    $('#pdLayoutLabel').text(pdState.allView?'切换分栏一屏':'切换全量平铺');
    showToast(pdState.allView?'已切换为「全量平铺视图」':'已恢复「一屏分栏视图」');
});
// 编辑党员信息（头部主按钮）：复用既有编辑表单（写入 #dtBody，保存后关弹窗并刷新列表）
$('#dtModal').on('click','#pdEditBtn',function(){ if(pdState.id) editPartyBase(pdState.id); });
$('#dtModal').on('click','[data-pd-edit]',function(){ editPartyBase(parseInt($(this).attr('data-pd-edit'),10)); });
// 分栏页签
$('#dtBody').on('click','.pd-tab',function(){
    if(pdState.allView) return;
    var key=$(this).attr('data-panel');
    $('#dtBody').find('.pd-tab').each(function(){ $(this).toggleClass('is-active',$(this).attr('data-panel')===key); });
    $('#dtBody').find('.pd-panel').each(function(){ $(this).toggleClass('is-active',$(this).attr('data-panel')===key); });
});
// 复制到剪贴板（身份证 / 手机号 / 地址）
$('#dtModal').on('click','[data-copy]',function(){
    var text=$(this).attr('data-copy')||'', label=$(this).attr('data-copy-label')||'内容';
    if(!text) return;
    function done(){ showToast('已复制'+label); }
    if(navigator.clipboard&&navigator.clipboard.writeText){ navigator.clipboard.writeText(text).then(done); }
    else{
        var $t=$('<textarea>').val(text).css({position:'fixed',opacity:'0'}).appendTo('body');
        $t[0].select(); document.execCommand('copy'); $t.remove(); done();
    }
});
// 抽屉路由：家庭情况 / 新增流动 / 新增任职
$('#dtModal').on('click','[data-drawer]',function(){
    var t=$(this).attr('data-drawer');
    if(t==='family-note') openFamilyDrawer($(this));
    else if(t==='flow-add') openFlowDrawer();
    else if(t==='post-add') openPostDrawer();
});
// 抽屉关闭 / 保存
$('#dtModal').on('click','#pdDrawerClose, #pdDrawerCancel',closeDrawer);
$('#dtModal').on('click','#pdDrawerSave',function(){ if(pdState.drawerSave) pdState.drawerSave(); });
// 流动 / 任职记录删除（与旧版同口径：confirm → POST → 刷新详情）
$('#dtModal').on('click','[data-del-transfer]',function(){
    var tid=parseInt($(this).attr('data-del-transfer'),10);
    if(!confirm('确定删除该条流动记录？'))return;
    $.post('/party/api/transfer/delete/'+tid,function(r){ if(r.success){showToast('已删除');refreshDetail(pdState.id);} else showToast(r.msg||'删除失败','danger'); });
});
$('#dtModal').on('click','[data-del-position]',function(){
    var pid=parseInt($(this).attr('data-del-position'),10);
    if(!confirm('确定删除该条任职记录？'))return;
    $.post('/party/api/position/delete/'+pid,function(r){ if(r.success){showToast('已删除');refreshDetail(pdState.id);} else showToast(r.msg||'删除失败','danger'); });
});
// 底栏导出（复用既有名册导出端点）
$('#dtModal').on('click','#pdExportBtn',function(){ window.location='/party/api/export'; });

// 055：党员情况汇总面板（页头 7 格 + 党支部 chip）
function loadSummary(){
    $.get('/party/api/summary',function(r){
        if(!r.success||!r.data)return;
        var d=r.data;
        $('#summaryTotal').text(d.total_archived || 0);
        $('#summaryFormal').text(d.formal || 0);
        $('#summaryProbationary').text(d.probation || 0);
        $('#summaryFloating').text(d.floating || 0);
        $('#summaryRetired').text(d.retired || 0);
        $('#summaryAutoLinked').text(d.auto || 0);
        $('#summaryManual').text(d.on_roll || 0);
        $('#summaryTransferred').text(d.transferred || 0);
        var chips='';
        $.each(d.branch_stats || [],function(_,item){
            // 2026-11-19：chip 改可点药丸 = 支部快捷筛选（写回 #sBranch 并触发查询；再次点击取消）
        chips+='<button type="button" class="party-branch-chip" data-branch="'+esc(item.branch || '')+'">'+esc(item.branch || '')+
                '<strong>'+esc(String(item.count || 0))+'</strong></button>';
        });
        $('#partyBranchSummary').html(chips || '<span class="text-muted small">暂无支部数据</span>');
        syncBranchPillActive($('#sBranch').val() || '');
    }).fail(function(){
        $('#partyBranchSummary').html('<span class="text-muted small">汇总加载失败</span>');
    });
}

// 支部药丸快捷筛选：写入 #sBranch 后查询；点同一个药丸取消筛选。
// loadSummary 会在同步后重渲染 chips → 激活态放在点击处同步，重渲染后按 #sBranch 回放。
$('#partyBranchSummary').on('click', '.party-branch-chip', function () {
    var b = $(this).attr('data-branch') || '';
    if ($('#sBranch').val() === b && b !== '') { b = ''; }
    $('#sBranch').val(b);
    syncBranchPillActive(b);
    load(1);
});

function syncBranchPillActive(b) {
    $('#partyBranchSummary .party-branch-chip').each(function () {
        $(this).toggleClass('is-active', b !== '' && $(this).attr('data-branch') === b);
    });
}

// 054-A#3：手动同步居民政治面貌
$('#btnSyncResidents').on('click',function(){
    if(!confirm('将按党员身份/流动/在册状态批量同步居民政治面貌（预备党员→中共预备党员、已转出→群众），确定继续？'))return;
    var $b=$(this).prop('disabled',true).html('<i class="bi bi-arrow-repeat"></i> 同步中…');
    $.post('/party/api/sync-residents',function(r){
        $b.prop('disabled',false).html('<i class="bi bi-arrow-repeat"></i> 同步居民政治面貌');
        if(r.success){showToast(r.msg||'同步完成');loadSummary();}
        else showToast(r.msg||'同步失败','danger');
    });
});

function editPartyBase(id){
    // Open the edit form modal filled with existing data
    $.get('/party/api/detail/'+id,function(r){
        if(!r.success)return;var d=r.data;
        // Just redirect to the edit approach - open modal
        // For simplicity, we'll just show a form inside the detail modal
        var html='<form id="editPartyForm"><input type="hidden" name="id" value="'+d.id+'"><div class="row g-2">'+
            '<div class="col-md-4"><label>姓名</label><input type="text" class="form-control form-control-sm" name="name" value="'+esc(d.name)+'"></div>'+
            '<div class="col-md-4"><label>身份证号</label><input type="text" class="form-control form-control-sm" name="id_card" value="'+esc(d.id_card)+'"></div>'+
            '<div class="col-md-4"><label>性别</label><select class="form-select form-select-sm" name="gender"><option value="男"'+(d.gender==='男'?' selected':'')+'>男</option><option value="女"'+(d.gender==='女'?' selected':'')+'>女</option></select></div>'+
            '<div class="col-md-4"><label>出生日期</label><input type="date" class="form-control form-control-sm" name="birth_date" value="'+d.birth_date+'"></div>'+
            '<div class="col-md-4"><label>民族</label><input type="text" class="form-control form-control-sm" name="nation" value="'+esc(d.nation)+'"></div>'+
            '<div class="col-md-4"><label>学历</label><input type="text" class="form-control form-control-sm" name="education" value="'+esc(d.education)+'"></div>'+
            '<div class="col-md-4"><label>手机号码</label><input type="text" class="form-control form-control-sm" name="phone" value="'+esc(d.phone)+'"></div>'+
            '<div class="col-md-4"><label>现居住址</label><input type="text" class="form-control form-control-sm" name="current_address" value="'+esc(d.current_address)+'"></div>'+
            '<div class="col-md-4"><label>入党时间</label><input type="date" class="form-control form-control-sm" name="join_date" value="'+d.join_date+'"></div>'+
            '<div class="col-md-4"><label>转正时间</label><input type="date" class="form-control form-control-sm" name="confirm_date" value="'+d.confirm_date+'"></div>'+
            // 080 A-1：人员类别拆四维——空壳 + dictSelect 异步填充（同 branch-edit 先例），选中值经回调回填
            '<div class="col-md-4"><label>党员身份</label><select class="form-select form-select-sm member-identity-edit" name="member_identity"><option value="">请选择</option></select></div>'+
            '<div class="col-md-4"><label>党员流动状态</label><select class="form-select form-select-sm member-mobility-edit" name="member_mobility"><option value="">请选择</option></select></div>'+
            '<div class="col-md-4"><label>在职状态</label><select class="form-select form-select-sm employment-status-edit" name="employment_status"><option value="">请选择</option></select></div>'+
            '<div class="col-md-4"><label>在册状态</label><select class="form-select form-select-sm party-status-edit" name="party_status"><option value="">请选择</option></select></div>'+
            '<div class="col-md-8"><label>党支部</label><select class="form-select form-select-sm branch-edit" name="branch"><option value="">请选择</option></select></div>'+
            '</div><div class="mt-3 text-end"><button type="submit" class="btn btn-primary btn-sm"><i class="bi bi-check-lg"></i> 保存</button>'+
            '<button type="button" class="btn btn-secondary btn-sm ms-2" onclick="refreshDetail('+d.id+')">取消</button></div></form>';
        $('#dtBody').html(html);
        // Reload dropdowns from dict and set values（080：四维与党支部同模式）
        dictSelect('党员身份', '.member-identity-edit', '请选择', function() { $('.member-identity-edit').val(d.member_identity); });
        dictSelect('党员流动状态', '.member-mobility-edit', '请选择', function() { $('.member-mobility-edit').val(d.member_mobility); });
        dictSelect('党员在职状态', '.employment-status-edit', '请选择', function() { $('.employment-status-edit').val(d.employment_status); });
        dictSelect('党员在册状态', '.party-status-edit', '请选择', function() { $('.party-status-edit').val(d.party_status); });
        // Reload branch dropdown and set value
        dictSelect('党支部', '.branch-edit', '请选择', function() { $('.branch-edit').val(d.branch); });
        $('#editPartyForm').submit(function(e){e.preventDefault();
            $.post('/party/api/save',$(this).serialize(),function(r2){
                if(r2.success){
                    showToast('保存成功');
                    $('#dtModal').modal('hide');
                    load(cp);
                }else showToast(r2.msg,'danger');
            });
        });
    });
}

function addTransfer(mid){
    // 详情 v2 起改由行内抽屉承接（data-drawer="flow-add"），此处保留空实现避免外部残留调用
    openFlowDrawer();
}
function addPosition(mid){
    openPostDrawer();
}

$('#btnSearch').on('click',function(){load(1);});
$('#btnReset').on('click',function(){$('#sName').val('');$('#sIdCard').val('');$('#sType').val('');$('#sBranch').val('');load(1);});
$('#btnAdd').on('click',function(){$('#fm')[0].reset();$('#fid').val('');$('#fmTitle').text('新增党员');$('#fmModal').modal('show');});

// 姓名自动读取居民信息
$('#fmName').on('input', function() {
    var name = $(this).val().trim();
    if (name.length >= 2) {
        $.get('/resident/api/lookup', {name: name}, function(r) {
            if (r.success && r.data && r.data.length > 0) {
                var d = r.data[0];
                // Only fill if the field is empty or matches what we'd fill
                if (!$('#fmIdCard').val()) $('#fmIdCard').val(d.id_card || '');
                if (!$('#fmGender').val()) $('#fmGender').val(d.gender || '');
                if (!$('#fmBirth').val() && d.birth_date) $('#fmBirth').val(d.birth_date);
                if (!$('#fmNation').val()) $('#fmNation').val(d.nation || '');
                var phoneInput = $('input[name="phone"]');
                if (!phoneInput.val() && d.phone) phoneInput.val(d.phone);
                var addrInput = $('input[name="current_address"]');
                if (!addrInput.val() && d.address) addrInput.val(d.address);
            }
        });
    }
});

// 身份证号自动解析性别和出生日期
$('#fmIdCard').on('input', function() {
    var idCard = $(this).val().trim();
    if (idCard.length === 18) {
        // 出生日期: positions 6-13
        var birthStr = idCard.substring(6, 14);
        var y = birthStr.substring(0, 4), m = birthStr.substring(4, 6), d = birthStr.substring(6, 8);
        if (y > '1900' && y < '2100' && m > '00' && m < '13' && d > '00' && d < '32') {
            if (!$('#fmBirth').val()) $('#fmBirth').val(y + '-' + m + '-' + d);
        }
        // 性别: 第17位, 奇数为男, 偶数为女
        var genderCode = parseInt(idCard.charAt(16));
        if (!isNaN(genderCode) && !$('#fmGender').val()) {
            $('#fmGender').val(genderCode % 2 === 1 ? '男' : '女');
        }
    }
});

// 手动查居民
$('#fmLookup').on('click', function() {
    $('#fmName').trigger('input');
});

// Enter 键触发查询
$('#fmName').on('keydown', function(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        $('#fmLookup').click();
    }
});
$('#fm').submit(function(e){e.preventDefault();$.post('/party/api/save',$(this).serialize(),function(r){if(r.success){showToast('保存成功');$('#fmModal').modal('hide');load(1);}else showToast(r.msg,'danger');});});

function doImport(){
    importWizard({fileSel:'#impFile', resultSel:'#impRes', endpoint:'/party/api/import',
        extraData:{mode:$('input[name="impMode"]:checked').val() || 'append'},
        onDone:function(){ setTimeout(function(){$('#impModal').modal('hide');load(1);},1500); }});
}

// 2026-11-19 UI 换壳配套：导入弹窗投放区（点击/拖拽/暂存）+ 隐私脱敏开关 + 支部药丸筛选。
// 只动「提交前选文件」的 UI 形态——#impFile 仍是真实 file input，doImport 链路不变。
$(function(){
    var $impInput = $('#impFile');
    var $impDrop = $('#impDrop');
    var $impStaged = $('#impStaged');
    var $impSubmit = $('#impSubmit');
    if ($impDrop.length) {
        var fmtSize = function (b) {
            if (b < 1024) return b + ' B';
            if (b < 1024 * 1024) return (b / 1024).toFixed(1) + ' KB';
            return (b / (1024 * 1024)).toFixed(2) + ' MB';
        };
        var stage = function (f) {
            $('#impStagedName').text(f.name);
            $('#impStagedMeta').text(fmtSize(f.size) + ' ·');
            $impDrop.addClass('d-none');
            $impStaged.removeClass('d-none');
            $impSubmit.prop('disabled', false);
        };
        var clearStaged = function () {
            $impInput.val('');
            $impStaged.addClass('d-none');
            $impDrop.removeClass('d-none');
            $impSubmit.prop('disabled', true);
            $('#impRes').html('');
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
        $('#impReselect').on('click', function () { $impInput.trigger('click'); });
        $('#impRemove').on('click', clearStaged);
        // 弹窗每次打开回到初始态（关闭不残留旧文件）
        $('#impModal').on('show.bs.modal', clearStaged);
    }

    // 隐私脱敏开关：只改表格里身份证/手机号的展示文本，不改 API 与数据
    var $switch = $('#pwPrivacySwitch');
    if ($switch.length) {
        $switch.on('click', function () {
            var on = $switch.attr('aria-checked') !== 'true';
            $switch.attr('aria-checked', String(on));
            $('#tb .col-id-card').each(function () { $(this).attr('data-masked', String(on)); });
            applyPartyMasking(on);
        });
        applyPartyMasking(true);
    }
});

function applyPartyMasking(maskOn) {
    var reId = /\b(\d{6})\d{8}(\d{4})\b/g;
    var rePh = /\b(\d{3})\d{4}(\d{4})\b/g;
    $('#tb').find('.col-id-card, .col-phone').each(function () {
        var $td = $(this);
        var raw = $td.data('plainText');
        if (raw === undefined) raw = $td.text();
        $td.data('plainText', raw);
        if (maskOn) {
            $td.text(raw.replace(reId, function (_, a, b) { return a + '********' + b; })
                        .replace(rePh, function (_, a, b) { return a + '****' + b; }));
        } else {
            $td.text(raw);
        }
    });
}

// Fee modal
var curFeeId=0;
// Fee - jump to annual dues page（R-21：删元素级重复绑定后，委托补 stopPropagation 防行点击冒泡）
$(document).off('click.partyFee').on('click.partyFee','.btn-fee',function(e){
    e.stopPropagation();
    curFeeId=$(this).attr('rid');
    window.location.href='/party/dues?highlight='+curFeeId;
});
$(function(){
    // 055：page_title 已置空，SPA 从其他页进入时顶栏会残留上一页标题 → 进页主动清空
    $('#pageTitle').text('');
    dictSelect('党员身份', '#sType', '党员身份');
    dictSelect('党员身份', '#fmMemberIdentity', '请选择');
    dictSelect('党员流动状态', '#fmMemberMobility', '请选择');
    dictSelect('党员在职状态', '#fmEmploymentStatus', '请选择');
    dictSelect('党员在册状态', '#fmPartyStatus');
    dictSelect('党支部', '#sBranch', '党支部', function() {
        var h = $('#sBranch').html();
        $('.branch-select').html(h);
        $('.branch-edit').html(h);
    });
    load(1);
    loadSummary();
});

// R-20：内联事件依赖函数挂 window（IIFE 隔离顶层作用域；内联 onclick/onblur 契约保留）
window.doImport = doImport;
window.applyPartyMasking = applyPartyMasking;
window.addPosition = addPosition;
window.addTransfer = addTransfer;
window.editPartyBase = editPartyBase;
window.refreshDetail = refreshDetail;
})();
