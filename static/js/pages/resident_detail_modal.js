(function () {
var currentDetailId = null;

// 062 P4：详情描述项（标签 + 值），标签复用 sc-pagination__stat 字号，值走令牌
function fieldHtml(label, value, span) {
    return '<div' + (span ? ' style="grid-column:span ' + span + '"' : '') + '>' +
        '<div class="sc-pagination__stat">' + label + '</div>' +
        '<div style="color:var(--c-ink-2);font-size:14px;word-break:break-all">' + esc(value || '-') + '</div></div>';
}

function isHouseholdHeadRelation(relationship) {
    relationship = (relationship || '').trim();
    return ['户主本人', '户主', '本人'].indexOf(relationship) !== -1;
}

function findHouseholdHead(members, detail) {
    members = members || [];
    return members.find(function(m) { return isHouseholdHeadRelation(m.relationship); }) || detail || members[0] || null;
}

function fillAddMemberIdentityFields() {
    var idCard = ($('#addMemberForm input[name="id_card"]').val() || '').trim();
    if (idCard.length !== 18) return;
    var birth = idCard.substring(6, 14);
    var y = birth.substring(0, 4), m = birth.substring(4, 6), d = birth.substring(6, 8);
    if (y > '1900' && y < '2100' && m > '00' && m < '13' && d > '00' && d < '32') {
        $('#addMemberForm input[name="birth_date"]').val(y + '-' + m + '-' + d);
    }
    var genderCode = parseInt(idCard.charAt(16), 10);
    if (!isNaN(genderCode)) {
        $('#addMemberForm select[name="gender"]').val(genderCode % 2 === 1 ? '男' : '女')
            .trigger('refresh.cs');   // 054-R2 P2-2：程序化赋值须同步自定义下拉触发框
    }
}

function getAddMemberModalInstance() {
    var modalEl = document.getElementById('addMemberModal');
    return bootstrap.Modal.getOrCreateInstance(modalEl, {backdrop: 'static'});
}

function openAddMemberModal(hhCode) {
    $('#addMemberForm')[0].reset();
    $('#addMemberForm').removeClass('was-validated');
    $('#addMemberForm input[name="household_code"]').val(hhCode);
    loadDictSm();

    var modalEl = document.getElementById('addMemberModal');
    if (modalEl && modalEl.parentNode !== document.body) {
        document.body.appendChild(modalEl);
    }
    getAddMemberModalInstance().show();
}
window.openAddMemberModal = openAddMemberModal;

function renderDetailModal(detail, members) {
    currentDetailId = detail.id;
    members = members || [];

    // Find household head
    var head = findHouseholdHead(members, detail);

    var hhCode = head.household_code || detail.household_code || '-';
    var headName = head.name || detail.household_name || '-';
    var headIdCard = head.id_card || '-';
    var headPhone = head.phone || '-';
    var headNation = head.nation || '-';
    var headProp = head.household_property || detail.household_property || '-';
    var headGroup = head.village_group || '-';
    var headAddr = head.address || detail.address || '-';
    var memberCount = members.length;

    // Build household info card（062 P4：sc-card + 令牌描述项）
    var html = '<div class="sc-card" style="margin-bottom:var(--sp-4)">' +
        '<div class="sc-card__header" style="display:flex;justify-content:space-between;align-items:center;gap:var(--sp-3);border-bottom:1px solid var(--c-line-2);padding:12px 16px">' +
        '<h3 class="sc-card__title" style="font-size:14px"><i class="bi bi-house-door me-1"></i> 户信息</h3>' +
        '<div class="sc-row-actions"><small class="sc-pagination__stat">户编号: ' + esc(hhCode) + '</small>' +
        '<button type="button" class="sc-btn sc-btn--text" onclick="editHousehold()" title="编辑"><i class="bi bi-pencil"></i></button>' +
        '<button type="button" class="sc-btn sc-btn--text is-danger btn-delete-household" data-code="' + esc(hhCode) + '" data-name="' + esc(headName) + '" data-count="' + memberCount + '" title="删除整户"><i class="bi bi-trash"></i></button></div></div>' +
        '<div class="sc-card__body" style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px 16px">' +
        fieldHtml('户主姓名', headName) + fieldHtml('身份证号码', headIdCard) +
        fieldHtml('电话号码', headPhone) + fieldHtml('民族', headNation) +
        fieldHtml('户属性', headProp) + fieldHtml('所在组', headGroup) +
        fieldHtml('家庭住址', headAddr, 2) +
        '</div></div>';

    // Build member table
    html += '<div class="sc-card">' +
        '<div class="sc-card__header" style="display:flex;justify-content:space-between;align-items:center;gap:var(--sp-3);border-bottom:1px solid var(--c-line-2);padding:12px 16px">' +
        '<h3 class="sc-card__title" style="font-size:14px"><i class="bi bi-people me-1"></i> 家庭成员信息（共 ' + memberCount + ' 人）</h3>' +
        '<div class="sc-row-actions">' +
        '<button type="button" class="sc-btn sc-btn--secondary" id="btnAddMember" data-household-code="' + esc(hhCode) + '" onclick="openAddMemberModal(this.getAttribute(\'data-household-code\'));return false;"><i class="bi bi-plus-lg"></i> 新增成员</button>' +
        '<button type="button" class="sc-btn sc-btn--text" onclick="showChangeHeadForm()" title="更换户主"><i class="bi bi-arrow-repeat"></i> 更换户主</button></div></div>' +
        '<div class="sc-card__body" style="padding:0"><div class="sc-table-wrap">' +
        '<table class="sc-table"><thead><tr>' +
        '<th class="sc-table__th">姓名</th><th class="sc-table__th">与户主关系</th><th class="sc-table__th">身份证号码</th>' +
        '<th class="sc-table__th" style="width:60px">性别</th><th class="sc-table__th" style="width:60px">年龄</th>' +
        '<th class="sc-table__th" style="width:130px">电话号码</th><th class="sc-table__th" style="width:70px">操作</th>' +
        '</tr></thead><tbody>';

    if (memberCount === 0) {
        html += '<tr><td colspan="7" class="text-center text-muted py-4"><i class="bi bi-person-plus"></i> 暂无成员</td></tr>';
    } else {
        $.each(members, function(i, m) {
            var rowCls = (m.status === 'deceased' || m.status === 'moved_out') ? ' class="opacity-75"' : '';
            var mark = m.status === 'deceased' ? ' ☦' : m.status === 'moved_out' ? ' ↪' : '';
            html += '<tr' + rowCls + ' data-id="' + m.id + '" style="cursor:pointer">' +
                '<td>' + esc(m.name) + mark + '</td>' +
                '<td>' + esc(m.relationship) + '</td>' +
                '<td>' + esc(m.id_card) + '</td>' +
                '<td>' + esc(m.gender) + '</td>' +
                '<td>' + (m.age || '-') + '</td>' +
                '<td>' + esc(m.phone) + '</td>' +
                '<td><div class="sc-row-actions"><button type="button" class="sc-btn sc-btn--text btn-view" data-id="' + m.id + '" title="详情"><i class="bi bi-eye"></i></button></div></td></tr>';
        });
    }

    html += '</tbody></table></div></div></div>';

    $('#detailModalBody').html(html);
    $('#detailModalTitle').text(headName + ' 户 - 共' + memberCount + '人');

    // Bind: view member detail (only inside detailModalBody)
    $('#detailModalBody .btn-view').on('click', function() {
        var mid = $(this).data('id');
        showMemberDetail(mid);
    });

    // Bind: click row also opens detail (only inside member table)
    $('#detailModalBody table tbody tr[data-id]').on('click', function(e) {
        if ($(e.target).closest('button').length) return;
        showMemberDetail($(this).data('id'));
    });

    // Bind: add member
    $('#detailModalBody').off('click.addMember', '#btnAddMember').on('click.addMember', '#btnAddMember', function(e) {
        e.preventDefault();
        e.stopPropagation();
        openAddMemberModal($(this).data('household-code') || hhCode);
    });
    $('#btnAddMemberSubmit').off('click').on('click', function() {
        var form = $('#addMemberForm')[0];
        if (!form.checkValidity()) {
            form.classList.add('was-validated');
            // 062 P4：自定义下拉隐藏原生 select，浏览器不再弹原生气泡，这里给出可见提示并聚焦
            var bad = $(form).find('input:invalid, select:invalid, textarea:invalid').first();
            var label = bad.closest('.sc-field').find('.sc-field__label').first().text().replace(/\*/g, '').trim();
            showToast((label || '必填项') + '未填写', 'warning');
            bad.closest('.custom-select').find('.custom-select__trigger').addClass('is-invalid').trigger('focus');
            return;
        }
        $.post('/resident/api/household/add', $('#addMemberForm').serialize(), function(r) {
            if (r.success) { showToast('新增成功', 'success'); getAddMemberModalInstance().hide(); refreshDetailSafe(); }
            else { showToast(r.msg, 'danger'); }
        });
    });
    $('#addMemberForm input[name="id_card"]').off('input.identity').on('input.identity', fillAddMemberIdentityFields);

    // Bind: delete entire household
    $('.btn-delete-household').off('click').on('click', function() {
        var code = $(this).data('code');
        var name = $(this).data('name');
        var count = $(this).data('count');
        deleteHousehold(code, name, count);
    });
}

function showMemberDetail(rid) {
    $.get('/resident/api/detail/' + rid, function(r) {
        if (!r.success) { showToast('加载失败', 'danger'); return; }
        var d = r.data.detail;
        var statusLabel = d.status === 'normal' ? '正常' : d.status === 'deceased' ? '死亡' : '迁出';

        // All fields in read-only format（062 P4：三列描述栅格）
        var html = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px 16px">';
        var fields = [
            ['姓名', d.name], ['身份证号', d.id_card], ['性别', d.gender],
            ['出生日期', d.birth_date], ['年龄', d.age], ['民族', d.nation],
            ['与户主关系', d.relationship], ['电话号码', d.phone],
            ['所在组', d.village_group], ['家庭地址', d.address],
            ['户口类型', d.household_type], ['政治面貌', d.politics_status],
            ['文化程度', d.education], ['职业', d.occupation],
            ['健康状态', d.health_status], ['婚姻状况', d.marital_status],
            ['兵役状况', d.military_status], ['户属性', d.household_property],
            ['开户行', d.bank_name], ['银行帐号', d.bank_card],
            ['人员状态', statusLabel], ['流动状态', d.flow_status || '常住'],
            ['建档来源', d.source || '手工'], ['备注', d.remark || '-'],
        ];
        $.each(fields, function(i, f) {
            html += fieldHtml(f[0], f[1]);
        });
        // T11-3：标签胶囊
        if (d.tags && d.tags.length) {
            html += '<div style="grid-column:1/-1"><div class="sc-pagination__stat">标签</div>';
            $.each(d.tags, function(i, t) {
                html += '<span class="sc-badge sc-badge--info me-1">' + esc(t.tag_code) + '</span>';
            });
            html += '</div>';
        }
        html += '</div>';

        // T14-4：个人补贴总览区（明细 + 累计总额 + 最近发放期间，Q1 取 subsidy_record）
        html += '<div class="sc-card" style="margin-top:var(--sp-4)">' +
            '<div class="sc-card__header" style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--c-line-2);padding:12px 16px">' +
            '<h3 class="sc-card__title" style="font-size:14px"><i class="bi bi-wallet2 me-1"></i>个人补贴总览</h3>' +
            '<button type="button" class="sc-btn sc-btn--text" onclick="refreshSubsidyOverview(' + d.id + ')">' +
            '<i class="bi bi-arrow-clockwise"></i> 刷新</button></div>' +
            '<div class="sc-card__body" style="padding:12px 16px" id="subsidyOverviewArea">' +
            '<div class="sc-pagination__stat">加载中…</div></div></div>';

        // Action buttons
        html += '<div class="sc-row-actions" style="margin-top:var(--sp-4);flex-wrap:wrap">' +
            '<button type="button" class="sc-btn sc-btn--secondary" onclick="editMemberDetail(' + d.id + ')"><i class="bi bi-pencil"></i> 编辑</button>';

        // Status change buttons (only if not already in that state)
        if (d.status !== 'deceased') {
            html += '<button type="button" class="sc-btn sc-btn--secondary" onclick="markDeceased(' + d.id + ')"><i class="bi bi-heartbreak"></i> 标记死亡</button>';
        }
        if (d.status !== 'moved_out') {
            html += '<button type="button" class="sc-btn sc-btn--secondary" onclick="markMovedOut(' + d.id + ')"><i class="bi bi-box-arrow-right"></i> 标记迁出</button>';
        }
        if (d.status !== 'normal') {
            html += '<button type="button" class="sc-btn sc-btn--secondary" onclick="markNormal(' + d.id + ')"><i class="bi bi-arrow-counterclockwise"></i> 恢复正常</button>';
        }
        html += '<button type="button" class="sc-btn sc-btn--text is-danger" style="margin-left:auto" onclick="deleteMember(' + d.id + ')"><i class="bi bi-trash"></i> 删除</button>' +
            '</div>';

        $('#memberDetailBody').html(html);
        $('#memberDetailTitle').text(d.name + ' - 详细信息');
        $('#memberDetailModal').modal('show');
        refreshSubsidyOverview(d.id);
    });
}

function refreshSubsidyOverview(rid) {
    var area = $('#subsidyOverviewArea');
    if (!area.length) return;
    $.getJSON('/subsidy/api/resident/' + rid + '/overview', function(resp) {
        if (!resp.success) {
            area.html('<div class="sc-pagination__stat">总览加载失败</div>');
            return;
        }
        var d = resp.data;
        var html = '';
        if (!d.detail.length) {
            html = '<div class="sc-pagination__stat">暂无补贴发放记录</div>';
        } else {
            html += '<div class="sc-table-wrap"><table class="sc-table sc-table--dense">' +
                '<thead><tr><th class="sc-table__th">项目</th><th class="sc-table__th">期间</th><th class="sc-table__th">金额</th><th class="sc-table__th">来源</th><th class="sc-table__th">日期</th></tr></thead><tbody>';
            $.each(d.detail, function(i, r) {
                var srcBadge = r.source === 'reverse' ? '<span class="sc-badge sc-badge--warning">冲正</span>'
                    : (r.source === 'legacy' ? '<span class="sc-badge sc-badge--muted">存量</span>' : '<span class="sc-badge sc-badge--info">正常</span>');
                html += '<tr><td>' + esc(r.project_name) + '</td><td>' + esc(r.period) + '</td><td>' + r.amount +
                    '</td><td>' + srcBadge + '</td><td>' + esc(r.issue_date) + '</td></tr>';
            });
            html += '</tbody></table></div>';
        }
        html += '<div class="sc-row-actions" style="margin-top:var(--sp-3)">' +
            '<span class="sc-pagination__stat">累计总额：<strong>' + d.total_amount + '</strong> 元</span>' +
            '<span class="sc-pagination__stat">最近发放期间：<strong>' + esc(d.last_period || '—') + '</strong></span>' +
            '<span class="sc-pagination__stat">记录数：' + d.count + '</span></div>';
        area.html(html);
    });
}

function markDeceased(rid) {
    var reason = prompt('请输入死亡日期 (YYYY-MM-DD)');
    if (!reason) return;
    $.post('/resident/api/status', {id: rid, status: 'deceased', status_date: reason, status_reason: '死亡登记'}, function(r) {
        if (r.success) { showToast('已标记死亡', 'success'); $('#memberDetailModal').modal('hide'); refreshDetailSafe(); if(typeof loadResidentList === 'function') loadResidentList(); }
        else { showToast(r.msg, 'danger'); }
    });
}

function markMovedOut(rid) {
    var reason = prompt('请输入迁出日期 (YYYY-MM-DD)');
    if (!reason) return;
    $.post('/resident/api/status', {id: rid, status: 'moved_out', status_date: reason, status_reason: '迁出'}, function(r) {
        if (r.success) { showToast('已标记迁出', 'success'); $('#memberDetailModal').modal('hide'); refreshDetailSafe(); if(typeof loadResidentList === 'function') loadResidentList(); }
        else { showToast(r.msg, 'danger'); }
    });
}

function markNormal(rid) {
    $.post('/resident/api/status', {id: rid, status: 'normal', status_date: '', status_reason: ''}, function(r) {
        if (r.success) { showToast('已恢复正常', 'success'); $('#memberDetailModal').modal('hide'); refreshDetailSafe(); if(typeof loadResidentList === 'function') loadResidentList(); }
        else { showToast(r.msg, 'danger'); }
    });
}

function deleteMember(rid) {
    if (!confirm('确定删除该居民？不可恢复。')) return;
    $.post('/resident/api/delete/' + rid, function(r) {
        if (r.success) { 
            showToast('删除成功', 'success'); 
            $('#memberDetailModal').modal('hide'); 
            // Remove the deleted row from the table, or re-fetch the whole household
            refreshDetailSafe(); 
            if (typeof loadResidentList === 'function') loadResidentList(); 
        }
        else { showToast(r.msg, 'danger'); }
    });
}

function editMemberDetail(rid) {
    $.get('/resident/api/detail/' + rid, function(r) {
        if (!r.success) return;
        var d = r.data.detail;
        var html = '<form id="memberEditForm"><input type="hidden" name="id" value="' + d.id + '"><input type="hidden" name="household_code" value="' + esc(d.household_code) + '"><input type="hidden" name="household_name" value="' + esc(d.household_name) + '"><div style="display:grid;grid-template-columns:1fr 1fr;gap:12px 16px">';
        var editFields = [
            ['name','姓名','text'], ['id_card','身份证号','text'],
            ['gender','性别','select',['男','女']], ['birth_date','出生日期','date'],
            ['nation','民族','dict','民族'], ['relationship','与户主关系','dict','与户主关系'],
            ['phone','电话号码','text'], ['village_group','所在组','dict','所在组'],
            ['address','家庭地址','text'], ['household_type','户口类型','dict','户口类型'],
            ['politics_status','政治面貌','dict','政治面貌'], ['education','文化程度','dict','学历'],
            ['occupation','职业','text'], ['health_status','健康状态','dict','健康状态'],
            ['marital_status','婚姻状况','dict','婚姻状况'], ['military_status','兵役状况','dict','兵役状况'],
            ['household_property','户属性','dict','户属性'], ['bank_name','开户行','dict','开户行'],
            ['bank_card','银行帐号','text'], ['remark','备注','textarea'],
        ];
        $.each(editFields, function(i, f) {
            var val = d[f[0]] || '';
            html += '<div class="sc-field"><label class="sc-field__label">' + f[1] + '</label>';
            if (f[2] === 'text') {
                html += '<input type="text" class="sc-input" name="' + f[0] + '" value="' + esc(val) + '">';
            } else if (f[2] === 'date') {
                html += '<input type="date" class="sc-input" name="' + f[0] + '" value="' + esc(val) + '">';
            } else if (f[2] === 'select') {
                html += '<select class="sc-select" name="' + f[0] + '"><option value="">请选择</option>';
                $.each(f[3], function(j, opt) { html += '<option value="' + esc(opt) + '"' + (val === opt ? ' selected' : '') + '>' + esc(opt) + '</option>'; });
                html += '</select>';
            } else if (f[2] === 'textarea') {
                html += '<textarea class="sc-textarea" name="' + f[0] + '" rows="2">' + esc(val) + '</textarea>';
            } else if (f[2] === 'dict') {
                html += '<select class="sc-select dict-load" name="' + f[0] + '" data-dict="' + f[3] + '" data-val="' + esc(val) + '"><option value="">请选择</option></select>';
            }
            html += '</div>';
        });
        html += '</div></form><div class="sc-row-actions" style="justify-content:flex-end;margin-top:var(--sp-4)">' +
            '<button type="button" class="sc-btn sc-btn--primary" onclick="submitMemberEdit()"><i class="bi bi-check-lg"></i> 保存</button>' +
            '<button type="button" class="sc-btn sc-btn--secondary" onclick="showMemberDetail(' + d.id + ')">取消</button></div>';
        $('#memberDetailBody').html(html);
        $('#memberDetailTitle').text('编辑 - ' + d.name);
        // Load dict dropdowns
        $('.dict-load').each(function() {
            dictSelect($(this).data('dict'), this);
        });
    });
}

function submitMemberEdit() {
    $.post('/resident/api/save', $('#memberEditForm').serialize(), function(r) {
        if (r.success) { showToast('保存成功', 'success'); refreshDetail(); showMemberDetail(r.data.id); }
        else { showToast(r.msg, 'danger'); }
    });
}

function refreshDetail() {
    if (currentDetailId) {
        $.get('/resident/api/detail/' + currentDetailId, function(r) {
            if (r.success) renderDetailModal(r.data.detail, r.data.members);
        });
    }
}

function refreshDetailSafe() {
    // Try currentDetailId first; if deleted, try by household_code
    if (!currentDetailId) { location.reload(); return; }
    $.get('/resident/api/detail/' + currentDetailId, function(r) {
        if (r.success) {
            renderDetailModal(r.data.detail, r.data.members);
        } else {
            // The current person was deleted. Try to find any member of same household
            // from the rendered page's household_code
            var codeMatch = $('#detailModalBody').text().match(/户编号:\s*(\S+)/);
            if (!codeMatch) { location.reload(); return; }
            var hhCode = codeMatch[1];
            // Look up a resident in this household by searching the list
            $.get('/resident/api/list', {limit: 50}, function(listResp) {
                if (listResp.success && listResp.data && listResp.data.data) {
                    var found = listResp.data.data.find(function(item) {
                        return item.household_code === hhCode;
                    });
                    if (found) {
                        currentDetailId = found.id;
                        $.get('/resident/api/detail/' + found.id, function(r2) {
                            if (r2.success) renderDetailModal(r2.data.detail, r2.data.members);
                            else location.reload();
                        });
                    } else {
                        // Household is empty, close modal
                        $('#detailModal').modal('hide');
                        if (typeof loadResidentList === 'function') loadResidentList();
                    }
                } else {
                    location.reload();
                }
            });
        }
    });
}

function loadDictSm() {
    var dicts = ['民族','与户主关系','所在组','政治面貌','学历','健康状态','婚姻状况','兵役状况','开户行'];
    dicts.forEach(function(dict) {
        var sel = $('#addMemberModal select[data-dict="' + dict + '"]');
        if (!sel.length || sel.find('option').length > 1) return;
        dictSelect(dict, sel);
    });
}

function editHousehold(){
    var rid=currentDetailId;
    if(!rid)return;
    $.get('/resident/api/detail/'+rid,function(r){
        if(!r.success)return;
        var d=r.data.detail;
        var html='<form id="editHouseholdForm"><input type="hidden" name="id" value="'+d.id+'"><input type="hidden" name="household_code" value="'+esc(d.household_code)+'"><input type="hidden" name="name" value="'+esc(d.name)+'"><input type="hidden" name="relationship" value="'+esc(d.relationship)+'"><div style="display:grid;grid-template-columns:1fr 1fr;gap:12px 16px">'+
            '<div class="sc-field"><label class="sc-field__label">户主姓名</label><input type="text" class="sc-input" name="household_name" value="'+esc(d.household_name)+'"></div>'+
            '<div class="sc-field"><label class="sc-field__label">身份证号</label><input type="text" class="sc-input" name="id_card" value="'+esc(d.id_card)+'"></div>'+
            '<div class="sc-field"><label class="sc-field__label">电话号码</label><input type="text" class="sc-input" name="phone" value="'+esc(d.phone)+'"></div>'+
            '<div class="sc-field"><label class="sc-field__label">民族</label><input type="text" class="sc-input" name="nation" value="'+esc(d.nation)+'"></div>'+
            '<div class="sc-field"><label class="sc-field__label">户属性</label><select class="sc-select" name="household_property"><option value="">请选择</option><option value="一般农户"'+(d.household_property==='一般农户'?' selected':'')+'>一般农户</option><option value="脱贫户"'+(d.household_property==='脱贫户'?' selected':'')+'>脱贫户</option><option value="监测户"'+(d.household_property==='监测户'?' selected':'')+'>监测户</option><option value="五保户"'+(d.household_property==='五保户'?' selected':'')+'>五保户</option></select></div>'+
            '<div class="sc-field"><label class="sc-field__label">所在组</label><input type="text" class="sc-input" name="village_group" value="'+esc(d.village_group)+'"></div>'+
            '<div class="sc-field" style="grid-column:1/-1"><label class="sc-field__label">家庭住址</label><input type="text" class="sc-input" name="address" value="'+esc(d.address)+'"></div>'+
            '</div></form>';
        $('#detailModalBody').html('<div><h3 class="sc-card__title" style="font-size:14px;margin-bottom:var(--sp-3)"><i class="bi bi-pencil"></i> 编辑户信息</h3>'+html+'<div class="sc-row-actions" style="justify-content:flex-end;margin-top:var(--sp-4)"><button type="button" class="sc-btn sc-btn--secondary" onclick="refreshDetail()">取消</button><button type="button" class="sc-btn sc-btn--primary" id="btnSaveHousehold">保存</button></div></div>');
        $('#btnSaveHousehold').on('click',function(){
            $.post('/resident/api/save',$('#editHouseholdForm').serialize(),function(r2){
                if(r2.success){showToast('保存成功','success');refreshDetail();}
                else showToast(r2.msg,'danger');
            });
        });
    });
}

// ═══════════════════════════════════════════════════════════════
// 更换户主（内嵌在户详情页）
// ═══════════════════════════════════════════════════════════════

function showChangeHeadForm() {
    if (!currentDetailId) return;
    $.get('/resident/api/detail/' + currentDetailId, function(r) {
        if (!r.success) { showToast('加载失败', 'danger'); return; }
        var detail = r.data.detail;
        var members = r.data.members;
        if (!members || members.length < 2) {
            showToast('家庭成员不足2人，无法更换户主', 'warning');
            return;
        }
        var hhCode = detail.household_code;
        var head = findHouseholdHead(members, detail);
        
        var html = '<div>';
        html += '<div class="sc-pagination__stat" style="margin-bottom:12px">当前户主：<span style="color:var(--c-ink-2);font-weight:500">' + esc(head.name) + '</span>（户编号：' + esc(hhCode) + '）</div>';
        html += '<div class="sc-table-wrap"><table class="sc-table">';
        html += '<thead><tr><th class="sc-table__th" style="width:48px">选择</th><th class="sc-table__th">姓名</th><th class="sc-table__th" style="width:60px">性别</th><th class="sc-table__th" style="width:60px">年龄</th><th class="sc-table__th" style="width:120px">与户主关系</th><th class="sc-table__th" style="width:170px">身份证号</th><th class="sc-table__th" style="width:120px">原户主新关系</th></tr></thead><tbody>';
        
        var candidateIds = [];
        var headId = head.id;
        var headName = head.name;
        var selectableStatuses = ['normal'];
        
        members.forEach(function(m) {
            var isHead = m.id === headId;
            var isDeceased = m.status === 'deceased' || m.status === 'moved_out';
            var canSelect = !isHead && !isDeceased;
            if (canSelect) candidateIds.push(m.id);
            var rowAttr = isHead ? ' style="background:var(--c-warning-bg)"' : (isDeceased ? ' class="opacity-75"' : '');
            html += '<tr' + rowAttr + ' data-id="' + m.id + '" data-name="' + esc(m.name) + '" data-selectable="' + canSelect + '">';
            html += '<td>';
            if (canSelect) {
                html += '<input type="radio" name="chHeadSelect" value="' + m.id + '">';
            }
            html += '</td>';
            html += '<td>' + esc(m.name) + (isDeceased ? ' <span class="sc-badge sc-badge--muted">已故</span>' : '') + '</td>';
            html += '<td>' + esc(m.gender) + '</td>';
            html += '<td>' + (m.age || '-') + '</td>';
            html += '<td>' + (isHead ? '<span class="sc-badge sc-badge--warning">当前户主</span>' : esc(m.relationship)) + '</td>';
            html += '<td>' + esc(m.id_card) + '</td>';
            html += '<td>';
            if (isHead) {
                html += '<select class="sc-select" id="chOldRelation">';
                var rels = ['其他亲属','配偶','子女','父母','儿媳','女婿','兄弟姐妹'];
                rels.forEach(function(rv) { html += '<option value="' + rv + '">' + rv + '</option>'; });
                html += '</select>';
            }
            html += '</td></tr>';
        });
        
        html += '</tbody></table></div>';
        html += '<div class="sc-row-actions" style="justify-content:space-between;margin-top:var(--sp-4)">';
        html += '<div class="sc-pagination__stat">新户主：<span id="chSelectedName" style="color:var(--c-ink-2);font-weight:500">未选择</span></div>';
        html += '<div class="sc-row-actions">';
        html += '<button type="button" class="sc-btn sc-btn--secondary" onclick="refreshDetail()">取消</button>';
        html += '<button type="button" class="sc-btn sc-btn--primary" id="btnChSubmit" disabled><i class="bi bi-arrow-repeat"></i> 确认更换</button>';
        html += '</div></div></div>';
        
        $('#detailModalBody').html(html);
        $('#detailModalTitle').text('更换户主 - ' + headName + ' 户');
        
        // 单选事件
        $('input[name="chHeadSelect"]').on('change', function() {
            var tid = parseInt($(this).val());
            var tr = $(this).closest('tr');
            $('#chSelectedName').text(tr.data('name'));
            $('#btnChSubmit').prop('disabled', false).data('new-id', tid).data('old-id', headId);
            $('#detailModalBody table tbody tr').removeClass('selected');
            tr.addClass('selected');
        });
        
        // 提交
        $('#btnChSubmit').on('click', function() {
            var newId = $(this).data('new-id');
            var oldId = $(this).data('old-id');
            if (!newId || !oldId) return;
            var newName = $('#chSelectedName').text();
            var relation = $('#chOldRelation').val() || '其他亲属';
            if (!confirm('确认将户主从【' + esc(headName) + '】变更为【' + esc(newName) + '】？\n原户主新关系：' + relation)) return;
            $.post('/resident/api/change-head/execute', {
                household_code: hhCode,
                old_head_id: oldId,
                new_head_id: newId,
                old_new_relation: relation
            }, function(resp) {
                if (resp.success) {
                    showToast(resp.msg, 'success');
                    refreshDetail();
                } else {
                    showToast(resp.msg, 'danger');
                }
            });
        });
    });
}
// 整户删除 - 三步防呆确认
// ═══════════════════════════════════════════════════════════════

function deleteHousehold(hhCode, hhName, memberCount) {
    if (!hhCode) { alert('无法获取户编号'); return; }

    // 确认提示：删除整户（不需要输入户主姓名确认）
    if (!confirm('\u26a0\ufe0f 危险操作！\n\n您正在删除整户信息！\n户主：' + hhName + '\n户编号：' + hhCode + '\n家庭成员：' + memberCount + '人\n\n此操作将永久删除该户全部居民信息，不可恢复！\n\n确定要继续吗？')) {
        return;
    }

    // 执行 AJAX 删除
    $.post('/resident/api/household/delete/' + hhCode, function(r) {
        if (r.success) {
            showToast('整户删除成功，共删除' + (r.data ? r.data.count : '') + '条记录', 'success');
            $('#detailModal').modal('hide');
            if (typeof loadResidentList === 'function') loadResidentList();
        } else {
            showToast(r.msg || '\u5220\u9664\u5931\u8d25', 'danger');
        }
    }).fail(function() {
        alert('\u5220\u9664\u8bf7\u6c42\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5\u7f51\u7edc\u540e\u91cd\u8bd5');
    });
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.deleteMember = deleteMember;
window.editHousehold = editHousehold;
window.editMemberDetail = editMemberDetail;
window.markDeceased = markDeceased;
window.markMovedOut = markMovedOut;
window.markNormal = markNormal;
window.refreshDetail = refreshDetail;
window.refreshSubsidyOverview = refreshSubsidyOverview;
window.showChangeHeadForm = showChangeHeadForm;
window.showMemberDetail = showMemberDetail;
window.submitMemberEdit = submitMemberEdit;
// 024-hotfix2：居民列表页加载详情后的渲染入口（跨文件调用，需全局可见）
window.renderDetailModal = renderDetailModal;
})();
