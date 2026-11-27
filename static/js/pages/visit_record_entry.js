(function () {
var currentPlanId = null;
var candidateRows = [];
var recordedSet = new Set();
var entryRows = [];   // 每项: {resident_id, name, visit_date, visit_type, content, has_need, need_desc, photos}

// ---------- 计划选择与清单加载 ----------
function loadPlans() {
    $.getJSON('/visit/api/list?limit=1000', function(resp) {
        if (!resp.success) return;
        let html = '<option value="">请选择计划</option>';
        resp.data.forEach(function(p) {
            let tag = p.plan_type === '按标签' ? '(按标签)' : (p.plan_type === '按区域' ? '(按区域)' : '(按对象类型)');
            html += '<option value="' + p.id + '">' + esc(p.plan_name) + ' ' + tag + '</option>';
        });
        $('#planSelect').html(html);
        let pre = getUrlParam('plan_id');
        if (pre) { $('#planSelect').val(pre); loadPlan(pre); }
    });
}

function loadPlan(pid) {
    currentPlanId = pid;
    entryRows = [];
    renderEntryRows();
    $('#batchErrors').html('');
    if (!pid) return;
    // 完成度
    $.getJSON('/visit/api/records?plan_id=' + pid, function(resp) {
        if (!resp.success) return;
        let d = resp.data;
        recordedSet = new Set(d.recorded || []);
        let rate = d.rate || 0;
        $('#progressBar').css('width', rate + '%').text(rate + '%');
        $('#progressBar').removeClass('bg-success bg-warning');
        $('#progressBar').addClass(rate >= 90 ? 'bg-success' : 'bg-warning');
        $('#progressText').text('已访 ' + d.completed_count + ' / 应访 ' + d.target_count);
        $('#progressBadge').text('完成率 ' + rate + '%').removeClass('bg-secondary').addClass(rate >= 90 ? 'bg-success' : 'bg-warning');
        loadCandidates();
    });
}

function loadCandidates() {
    if (!currentPlanId) return;
    let kw = $('#keyword').val().trim();
    $.getJSON('/visit/api/candidates?plan_id=' + currentPlanId + '&limit=1000&keyword=' + encodeURIComponent(kw), function(resp) {
        if (!resp.success) return;
        candidateRows = resp.data || [];
        renderCandidates();
    });
}

function renderCandidates() {
    let html = '';
    candidateRows.forEach(function(c) {
        let done = recordedSet.has(c.resident_id);
        html += '<tr>' +
            '<td>' + esc(c.name) + '</td>' +
            '<td>' + esc(c.gender || '—') + '</td>' +
            '<td>' + (c.age != null ? c.age : '—') + '</td>' +
            '<td>' + esc(c.village_group || (c.grid || '—')) + '</td>' +
            '<td>' + esc((c.tags || []).join('、')) + '</td>' +
            '<td>' + (done ? '<span class="sc-badge sc-badge--success">已补录</span>' : '<span class="sc-badge sc-badge--muted">未访</span>') + '</td>' +
            '<td><button type="button" class="sc-btn sc-btn--text" onclick="addEntry(' + c.resident_id + ')">补录</button></td>' +
            '</tr>';
    });
    if (!html) html = '<tr><td colspan="7" class="text-center text-muted py-3">暂无应访对象</td></tr>';
    $('#candidateBody').html(html);
}

function addEntry(rid) {
    let c = candidateRows.find(function(x) { return x.resident_id === rid; });
    if (!c) return;
    if (entryRows.some(function(e) { return e.resident_id === rid; })) {
        showToast('该对象已在补录队列中', 'warning');
        return;
    }
    entryRows.push({
        resident_id: rid, name: c.name,
        visit_date: todayStr(), visit_type: '入户',
        content: '', has_need: 0, need_desc: '', photos: []
    });
    renderEntryRows();
}

function removeEntry(idx) { entryRows.splice(idx, 1); renderEntryRows(); }

function renderEntryRows() {
    let html = '';
    entryRows.forEach(function(e, i) {
        html += '<div class="border rounded p-2 mb-2 entry-row" data-idx="' + i + '">' +
            '<div class="d-flex justify-content-between align-items-start">' +
                '<div><strong>' + esc(e.name) + '</strong>' +
                    '<span class="text-muted small ms-2">' + esc(e.tagsText || '') + '</span></div>' +
                '<button type="button" class="sc-btn sc-btn--text is-danger" onclick="removeEntry(' + i + ')">删除</button>' +
            '</div>' +
            '<div class="d-flex flex-wrap gap-3 mt-1">' +
                '<div class="sc-field">' +
                    '<label class="sc-field__label">走访方式</label>' +
                    '<select class="sc-select" data-field="visit_type" style="width:150px">' +
                        window.__PAGE_CFG__.visitTypes.map(function(t){ return '<option value="' + esc(t) + '">' + esc(t) + '</option>'; }).join('') +
                    '</select></div>' +
                '<div class="sc-field">' +
                    '<label class="sc-field__label">走访日期</label>' +
                    '<input type="date" class="sc-input" data-field="visit_date" value="' + e.visit_date + '">' +
                '</div>' +
                '<div class="sc-field">' +
                    '<label class="sc-field__label">走访内容（文本）</label>' +
                    '<input type="text" class="sc-input" data-field="content" maxlength="2000" style="width:260px" placeholder="走访情况记录">' +
                '</div>' +
                '<div class="sc-field">' +
                    '<div class="form-check form-check-inline">' +
                        '<input class="form-check-input" type="checkbox" data-field="has_need">' +
                        '<label class="form-check-label small">有需求</label>' +
                    '</div>' +
                    '<input type="text" class="sc-input" data-field="need_desc" style="width:220px" maxlength="500" placeholder="需求描述">' +
                '</div>' +
                '<div class="sc-field">' +
                    '<label class="sc-field__label">照片（先上传，随记录保存）</label>' +
                    '<div class="d-flex gap-1 align-items-center">' +
                        '<input type="file" class="sc-input" accept="image/*" multiple data-role="photo">' +
                        '<button type="button" class="sc-btn sc-btn--text" onclick="uploadPhotos(' + i + ', this)">上传</button>' +
                    '</div>' +
                    '<div class="small text-muted mt-1" data-role="photoList"></div>' +
                '</div>' +
            '</div></div>';
    });
    if (!html) html = '<div class="text-center text-muted py-4">请在左侧清单点击「补录」添加本次走访对象</div>';
    $('#entryRows').html(html);
}

function uploadPhotos(idx, btn) {
    let input = $(btn).closest('.entry-row').find('[data-role="photo"]')[0];
    if (!input || !input.files.length) { showToast('请先选择照片', 'warning'); return; }
    let fd = new FormData();
    for (let f of input.files) fd.append('photos', f);
    $.ajax({
        url: '/visit/api/photo/upload',
        type: 'POST', data: fd, processData: false, contentType: false,
        success: function(resp) {
            if (!resp.success) { showToast(resp.msg, 'danger'); return; }
            let e = entryRows[idx];
            e.photos = e.photos.concat((resp.data.files || []).map(function(f){ return f.filename; }));
            $(btn).closest('.entry-row').find('[data-role="photoList"]').text('已上传 ' + e.photos.length + ' 张');
            showToast('照片上传成功', 'success');
        }
    });
}

function collectEntries() {
    let rows = [];
    entryRows.forEach(function(e, i) {
        let $row = $('.entry-row[data-idx="' + i + '"]');
        rows.push({
            resident_id: e.resident_id,
            visit_date: $row.find('[data-field="visit_date"]').val() || todayStr(),
            visit_type: $row.find('[data-field="visit_type"]').val() || '入户',
            content: $row.find('[data-field="content"]').val() || '',
            has_need: $row.find('[data-field="has_need"]').is(':checked') ? 1 : 0,
            need_desc: $row.find('[data-field="need_desc"]').val() || '',
            photos: e.photos || []
        });
    });
    return rows;
}

function submitBatch() {
    if (!currentPlanId) { showToast('请先选择计划', 'warning'); return; }
    let rows = collectEntries();
    if (!rows.length) { showToast('补录队列为空', 'warning'); return; }
    $.ajax({
        url: '/visit/api/record/batch',
        type: 'POST', contentType: 'application/json',
        data: JSON.stringify({plan_id: currentPlanId, rows: rows}),
        success: function(resp) {
            if (!resp.success) { showToast(resp.msg, 'danger'); return; }
            let d = resp.data;
            let msg = '成功 ' + d.success + ' 条（新增 ' + d.inserted + '，更新 ' + d.updated + '）';
            if (d.errors && d.errors.length) {
                msg += '，失败 ' + d.errors.length + ' 条';
                let ehtml = '<div class="alert alert-danger mt-2"><ul class="mb-0">';
                d.errors.forEach(function(e) { ehtml += '<li>第 ' + e.row + ' 行：' + esc(e.msg) + '</li>'; });
                ehtml += '</ul></div>';
                $('#batchErrors').html(ehtml);
            } else {
                $('#batchErrors').html('');
            }
            showToast(msg, d.errors && d.errors.length ? 'warning' : 'success');
            entryRows = [];
            renderEntryRows();
            loadPlan(currentPlanId);  // 刷新完成度与已补录标记
        }
    });
}

// ---------- 工具 ----------
function todayStr() {
    let d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}
function getUrlParam(name) {
    let m = new RegExp('[?&]' + name + '=([^&]*)').exec(location.search);
    return m ? decodeURIComponent(m[1]) : '';
}
$('#planSelect').change(function() { loadPlan($(this).val()); });
$('#keyword').on('input', function() { loadCandidates(); });

$(function() { loadPlans(); });

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.addEntry = addEntry;
window.removeEntry = removeEntry;
window.submitBatch = submitBatch;
window.uploadPhotos = uploadPhotos;
})();
