/* dynatable_v2_upload.js — 软表 V2.5 补丁 C-1 懒人 1 屏智能传表控制器 */
(function () {
    'use strict';

    var BASE = '/dynatable';
    var state = {
        token: '',
        workbookGroup: '',
        sheets: [],
        smartRoute: null,
        safetyFoldersLoaded: false
    };

    function _esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function setMsg(txt, isErr) {
        var el = document.getElementById('v2Msg');
        if (!el) return;
        el.textContent = txt || '';
        el.style.color = isErr ? 'var(--c-danger, #B91C1C)' : 'var(--c-muted-2)';
    }
    function slugCode(prefix, idx) {
        var base = String(prefix || 'tbl').toLowerCase().replace(/[^a-z0-9_]/g, '');
        if (!/^[a-z]/.test(base)) base = 't_' + base;
        var stamp = String(Date.now()).slice(-5);
        return (base.slice(0, 22) || 'tbl') + '_' + stamp + '_' + idx;
    }

    function unwrapCustomSelect(sel) {
        if (!sel) return;
        // 兼容全站 app.js 自定义下拉：若已包装则移除包装退回原生，确保动态 options 100% 同步可见
        var wrap = sel.closest('.custom-select');
        if (wrap && wrap.parentNode) {
            sel.classList.remove('cs-native');
            sel.style.display = '';
            wrap.parentNode.insertBefore(sel, wrap);
            wrap.parentNode.removeChild(wrap);
        }
    }

    function maybeLoadSafetyFolders() {
        var modSel = document.getElementById('v2ModuleSelect');
        var wrap = document.getElementById('v2SafetyFolderWrap');
        if (!modSel || !wrap) return;
        var isSafety = modSel.value === 'safety';
        wrap.hidden = !isSafety;
        if (!isSafety || state.safetyFoldersLoaded) return;
        $.getJSON('/safety/api/folders').done(function (res) {
            state.safetyFoldersLoaded = true;
            var list = (res && res.data) || [];
            var sel = document.getElementById('v2SafetyFolderSelect');
            if (!sel) return;
            unwrapCustomSelect(sel);
            sel.innerHTML = '<option value="">（暂不挂入指定安全文件夹）</option>' + list.map(function (f) {
                return '<option value="' + _esc(f.id) + '">' + _esc(f.name || f.title) + '</option>';
            }).join('');
        });
    }

    function renderSmartRoute() {
        var sr = state.smartRoute || {};
        var banner = document.getElementById('v2SmartRouteBanner');
        var titleEl = document.getElementById('v2SmartRouteTitle');
        var descEl = document.getElementById('v2SmartRouteDesc');
        var linkEl = document.getElementById('v2SmartRouteLink');
        var modSel = document.getElementById('v2ModuleSelect');
        var modHint = document.getElementById('v2ModuleHint');

        if (modSel && sr.recommended_module) {
            unwrapCustomSelect(modSel);
            modSel.value = sr.recommended_module;
            maybeLoadSafetyFolders();
        }
        if (modHint && sr.reason && sr.kind === 'softtable_v2') {
            modHint.textContent = sr.reason + '挂入「机构管理」的台账在建表和保存时会自动静默同步至「组织架构」。';
        }

        var altBtn = document.getElementById('v2SmartRouteAltBtn');
        if (altBtn) { altBtn.hidden = true; altBtn.onclick = null; }
        if (banner && (sr.kind === 'builtin_softtable' || sr.kind === 'physical_table')) {
            banner.hidden = false;
            titleEl.textContent = sr.kind === 'builtin_softtable'
                ? ('已自动识别为系统内置台账「' + sr.target_name + '」')
                : ('已自动识别为强物理底账「' + sr.target_name + '」');
            descEl.textContent = sr.reason || '';
            linkEl.href = sr.target_url || BASE + '/';
            linkEl.textContent = sr.kind === 'builtin_softtable' ? '前往该表一键追加导入 →' : '前往对应业务模块导入 →';
        } else if (banner) {
            banner.hidden = true;
        }
    }

    function renderQuickDiagAndSheets() {
        var diagEl = document.getElementById('v2QuickDiag');
        var box = document.getElementById('v2SheetsBox');
        var totalTables = 0, totalRows = 0;
        var modeLabels = [];
        state.sheets.forEach(function (sh) {
            if (sh.mode_label && modeLabels.indexOf(sh.mode_label) < 0) modeLabels.push(sh.mode_label);
            (sh.tables || []).forEach(function (t) {
                totalTables += 1;
                totalRows += (t.row_count || 0);
            });
        });
        if (diagEl) {
            diagEl.innerHTML =
                modeLabels.map(function (m) { return '<span class="dtv2-tag dtv2-tag--ok">' + _esc(m) + '</span>'; }).join('') +
                '<span class="dtv2-tag dtv2-tag--info">共 ' + totalTables + ' 张子表 · ' + totalRows + ' 行数据</span>' +
                '<span class="dtv2-tag">智能分流已校验完毕</span>';
        }

        if (!box) return;
        var tblSeq = 0;
        box.innerHTML = state.sheets.map(function (sh, si) {
            var overrideBar = '<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0">' +
                '<span class="dtv2-field__hint" style="margin:0">字段指定表头行（可选）：</span>' +
                '<input type="number" class="sc-input dtv2-fixed-ctrl" min="1" max="20" placeholder="起始行" data-ov-start="' + si + '" style="width:76px;height:30px;padding:0 6px">' +
                '<input type="number" class="sc-input dtv2-fixed-ctrl" min="1" max="20" placeholder="结束行" data-ov-end="' + si + '" style="width:76px;height:30px;padding:0 6px">' +
                '<button type="button" class="sc-btn sc-btn--secondary" data-reparse-sh="' + si + '" style="padding:3px 9px;font-size:12px">重建解析</button>' +
                '</div>';

            var tablesHtml = (sh.tables || []).map(function (t, ti) {
                var idx = tblSeq++;
                t._code = t._code || slugCode('v2', idx);
                var fields = t.fields || [];
                // 优先取后端为每张子表独立下发的 sample_rows，彻底避免并排双表/三段表错列
                var sampleRows = (t.sample_rows && t.sample_rows.length) ? t.sample_rows : (t.rows || []).slice(0, 3);
                var schemaRows = fields.map(function (f, fi) {
                    var smps = sampleRows.map(function (r) { return r[fi]; }).filter(function (v) { return v != null && String(v).trim() !== ''; }).slice(0, 3);
                    var smpHtml = smps.length ? smps.map(function (v) { return '<span class="dtv2-sample-code">' + _esc(v) + '</span>'; }).join('') : '<span class="dtv2-field__hint">（空样）</span>';
                    return '<tr>' +
                        '<td>' + (fi + 1) + '</td>' +
                        '<td><strong>' + _esc(f.display_name) + '</strong></td>' +
                        '<td>' + smpHtml + '</td>' +
                        '<td><select class="sc-select dtv2-fixed-ctrl" data-col-type="' + si + ':' + ti + ':' + fi + '" style="width:130px;height:28px;padding:0 6px;font-size:12px">' +
                            '<option value="text"' + (f.data_type === 'text' ? ' selected' : '') + '>文本</option>' +
                            '<option value="number"' + (f.data_type === 'number' ? ' selected' : '') + '>数值</option>' +
                            '<option value="date"' + (f.data_type === 'date' ? ' selected' : '') + '>日期</option>' +
                        '</select></td>' +
                        '<td><select class="sc-select dtv2-fixed-ctrl" data-col-sens="' + si + ':' + ti + ':' + fi + '" style="width:152px;height:28px;padding:0 6px;font-size:12px">' +
                            '<option value="none"' + (f.sensitivity !== 'identity' ? ' selected' : '') + '>普通字段</option>' +
                            '<option value="identity"' + (f.sensitivity === 'identity' ? ' selected' : '') + '>身份敏感（加密）</option>' +
                        '</select></td></tr>';
                }).join('');

                return '<div style="border:1px solid var(--c-line-2);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--c-surface)">' +
                    '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:8px">' +
                        '<label style="display:inline-flex;align-items:center;gap:4px;font-weight:600;cursor:pointer"><input type="checkbox" data-v2-chk="' + si + ':' + ti + '" checked> 导入</label>' +
                        '<input type="text" class="sc-input dtv2-fixed-ctrl" data-v2-name="' + si + ':' + ti + '" value="' + _esc(t.suggested_name) + '" style="width:220px;height:28px;padding:0 8px">' +
                        '<input type="text" class="sc-input dtv2-fixed-ctrl" data-v2-code="' + si + ':' + ti + '" value="' + _esc(t._code) + '" style="width:170px;height:28px;padding:0 8px;font-family:monospace">' +
                        '<span class="dtv2-tag">' + fields.length + ' 列 · ' + (t.row_count || 0) + ' 行</span>' +
                    '</div>' +
                    '<div style="overflow-x:auto"><table class="dtv2-sub-table"><thead><tr><th style="width:40px">#</th><th>列名</th><th>本表真实样本值</th><th style="width:140px">数据类型可改</th><th style="width:162px">脱敏级别可改</th></tr></thead><tbody>' + schemaRows + '</tbody></table></div></div>';
            }).join('');

            return '<div style="margin-bottom:12px"><strong>Sheet：' + _esc(sh.sheet_name) + '</strong>' + overrideBar + tablesHtml + '</div>';
        }).join('');
    }

    function showSmartBanner(title, desc, href, linkText, altText, altHandler) {
        var banner = document.getElementById('v2SmartRouteBanner');
        var titleEl = document.getElementById('v2SmartRouteTitle');
        var descEl = document.getElementById('v2SmartRouteDesc');
        var linkEl = document.getElementById('v2SmartRouteLink');
        var altBtn = document.getElementById('v2SmartRouteAltBtn');
        if (!banner) return;
        banner.hidden = false;
        if (titleEl) titleEl.textContent = title || '';
        if (descEl) descEl.textContent = desc || '';
        if (linkEl) { linkEl.href = href || '#'; linkEl.textContent = linkText || '立即前往 →'; }
        if (altBtn) {
            if (altText && typeof altHandler === 'function') {
                altBtn.textContent = altText;
                altBtn.hidden = false;
                altBtn.onclick = altHandler;
            } else {
                altBtn.hidden = true;
                altBtn.onclick = null;
            }
        }
    }

    function uploadDocxToWordEngine(file) {
        var pickBtn = document.getElementById('v2PickBtn');
        var fd = new FormData();
        fd.append('file', file);
        setMsg('正在调用 Word 通用报表引擎解析 .docx...');
        if (pickBtn) pickBtn.disabled = true;
        $.ajax({ url: '/word-form/api/template/upload', method: 'POST', data: fd, processData: false, contentType: false })
            .done(function (res) {
                if (!res || !res.success || !res.data) {
                    setMsg((res && res.msg) || 'Word 表单解析失败', true);
                    return;
                }
                var tpl = res.data;
                var blockCount = (tpl.blocks && tpl.blocks.length) ? tpl.blocks.length : 0;
                showSmartBanner(
                    'Word 通用表单已解析入库：' + (tpl.title || file.name),
                    '已自动识别出 ' + blockCount + ' 个内容块（段落/表格）并完成居民回填变量绑定，支持 A4 排版微调与一键导出原位回写 .docx。',
                    '/word-form/?tpl_id=' + encodeURIComponent(tpl.id),
                    '进入 Word 报表工作台 →'
                );
                setMsg('Word 表单已解析入库，可点击上方按钮直达工作台套打。');
            })
            .fail(function (xhr) {
                if (xhr && xhr.status === 403) {
                    setMsg('当前账号未开通报表审批 / Word 通用权限，无法直接导入 .docx', true);
                } else {
                    setMsg((xhr && xhr.responseJSON && xhr.responseJSON.msg) || 'Word 表单上传失败', true);
                }
            })
            .always(function () { if (pickBtn) pickBtn.disabled = false; });
    }

    function handleDocxFile(file) {
        state.token = ''; state.sheets = []; state.smartRoute = null;
        document.getElementById('v2QuickConfirm').hidden = true;
        var cleanTitle = file.name.replace(/\.docx$/i, '').trim();
        setMsg('正在检查 Word 报表库是否已有同名模板...');
        $.getJSON('/word-form/api/templates')
            .done(function (res) {
                var list = (res && res.data) || [];
                var dup = null;
                for (var i = 0; i < list.length; i++) {
                    if ((list[i].title || '').trim() === cleanTitle) { dup = list[i]; break; }
                }
                if (dup) {
                    showSmartBanner(
                        '识别到 Word 报表库中已有同名模板：' + dup.title,
                        '为避免重复建模板，已直接打开既有模板套打（如需用新版本，也可点右侧按钮重新作为模板导入）。',
                        '/word-form/?tpl_id=' + encodeURIComponent(dup.id),
                        '打开既有 Word 模板 →',
                        '重新作为模板导入',
                        function () { uploadDocxToWordEngine(file); }
                    );
                    setMsg('检测到同名 Word 报表模板，可选择直接打开既有模板。');
                } else {
                    uploadDocxToWordEngine(file);
                }
            })
            .fail(function (xhr) {
                if (xhr && xhr.status === 403) {
                    setMsg('当前账号未开通报表审批 / Word 通用权限，无法直接导入 .docx', true);
                } else {
                    uploadDocxToWordEngine(file);
                }
            });
    }

    function parseFile(file) {
        if (!file) { setMsg('请先选择要解析的表格 / Word (.docx) 文件', true); return; }
        var fn = document.getElementById('v2FileName');
        if (fn) { fn.textContent = '已选择：' + file.name; fn.hidden = false; }
        var lower = (file.name || '').toLowerCase();
        if (lower.endsWith('.doc')) {
            document.getElementById('v2QuickConfirm').hidden = true;
            document.getElementById('v2SmartRouteBanner').hidden = true;
            setMsg('暂不支持旧版 .doc 格式，请先用 Word / WPS 另存为 .docx 后再导入', true);
            return;
        }
        if (lower.endsWith('.docx')) {
            handleDocxFile(file);
            return;
        }
        var fd = new FormData();
        fd.append('file', file);
        setMsg('正在智能识别全量样本并结构感知解析...');
        var pickBtn = document.getElementById('v2PickBtn');
        if (pickBtn) pickBtn.disabled = true;
        $.ajax({ url: BASE + '/api/upload-v2', method: 'POST', data: fd, processData: false, contentType: false })
            .done(function (res) {
                if (!res || !res.success) { setMsg((res && res.msg) || '解析失败', true); return; }
                state.token = res.data.token;
                state.workbookGroup = res.data.workbook_group || '';
                state.sheets = res.data.sheets || [];
                state.smartRoute = res.data.smart_route || null;
                document.getElementById('v2GroupInput').value = state.workbookGroup;
                document.getElementById('v2QuickConfirm').hidden = false;
                setMsg('解析完成，确认分流推荐后点「一键建表」，或打开高级设置微调。');
                renderSmartRoute();
                renderQuickDiagAndSheets();
            })
            .fail(function (xhr) { setMsg((xhr.responseJSON && xhr.responseJSON.msg) || '解析请求失败', true); })
            .always(function () { if (pickBtn) pickBtn.disabled = false; });
    }

    var fileInput = document.getElementById('v2FileInput');
    var pickBtn = document.getElementById('v2PickBtn');
    var dropZone = document.getElementById('v2DropZone');
    var createBtn = document.getElementById('v2CreateBatchBtn');
    var resetBtn = document.getElementById('v2ResetBtn');
    var boxEl = document.getElementById('v2SheetsBox');
    if (!fileInput || !pickBtn || !createBtn) return;

    unwrapCustomSelect(document.getElementById('v2ModuleSelect'));
    unwrapCustomSelect(document.getElementById('v2SafetyFolderSelect'));

    pickBtn.addEventListener('click', function () { fileInput.click(); });
    fileInput.addEventListener('change', function () {
        if (fileInput.files && fileInput.files[0]) parseFile(fileInput.files[0]);
    });
    if (dropZone) {
        dropZone.addEventListener('dragover', function (e) { e.preventDefault(); dropZone.classList.add('is-dragover'); });
        dropZone.addEventListener('dragleave', function () { dropZone.classList.remove('is-dragover'); });
        dropZone.addEventListener('drop', function (e) {
            e.preventDefault(); dropZone.classList.remove('is-dragover');
            var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
            if (f) parseFile(f);
        });
    }

    if (resetBtn) {
        resetBtn.addEventListener('click', function () {
            state.token = ''; state.sheets = []; state.smartRoute = null;
            fileInput.value = '';
            document.getElementById('v2FileName').hidden = true;
            document.getElementById('v2SmartRouteBanner').hidden = true;
            document.getElementById('v2QuickConfirm').hidden = true;
            setMsg('');
        });
    }
    document.getElementById('v2ModuleSelect').addEventListener('change', maybeLoadSafetyFolders);

    if (boxEl) {
        boxEl.addEventListener('change', function (e) {
            var ct = e.target.getAttribute('data-col-type');
            if (ct) { var p = ct.split(':').map(Number); state.sheets[p[0]].tables[p[1]].fields[p[2]].data_type = e.target.value; return; }
            var cs = e.target.getAttribute('data-col-sens');
            if (cs) { var q = cs.split(':').map(Number); state.sheets[q[0]].tables[q[1]].fields[q[2]].sensitivity = e.target.value; }
        });
        boxEl.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-reparse-sh]');
            if (!btn || !state.token) return;
            var si = parseInt(btn.getAttribute('data-reparse-sh'), 10);
            var sh = state.sheets[si];
            if (!sh) return;
            var sIn = boxEl.querySelector('[data-ov-start="' + si + '"]');
            var eIn = boxEl.querySelector('[data-ov-end="' + si + '"]');
            var hStart = parseInt(sIn && sIn.value, 10);
            if (!hStart) { setMsg('请填写表头起始行号', true); return; }
            var ov = {}; ov[sh.sheet_name] = { header_start: hStart, header_end: parseInt(eIn && eIn.value, 10) || hStart };
            $.ajax({ url: BASE + '/api/reparse-v2', method: 'POST', contentType: 'application/json', data: JSON.stringify({ token: state.token, header_overrides: ov }) })
                .done(function (res) { if (res && res.success) { state.sheets = res.data.sheets || []; renderQuickDiagAndSheets(); } });
        });
    }

    createBtn.addEventListener('click', function () {
        if (!state.token) { setMsg('请先上传并解析文件', true); return; }
        var moduleKey = (document.getElementById('v2ModuleSelect').value || 'dynatable').trim();
        var groupName = (document.getElementById('v2GroupInput').value || state.workbookGroup || '').trim();
        var periodTag = (document.getElementById('v2PeriodInput').value || '').trim();
        var sfSel = document.getElementById('v2SafetyFolderSelect');
        var safetyFolderId = (moduleKey === 'safety' && sfSel && sfSel.value) ? sfSel.value : null;
        var combineChk = document.getElementById('v2CombinePage');
        var combineOnSinglePage = combineChk ? combineChk.checked : true;

        var items = [];
        state.sheets.forEach(function (sh, si) {
            (sh.tables || []).forEach(function (t, ti) {
                var chk = boxEl && boxEl.querySelector('[data-v2-chk="' + si + ':' + ti + '"]');
                if (chk && !chk.checked) return;
                var nEl = boxEl && boxEl.querySelector('[data-v2-name="' + si + ':' + ti + '"]');
                var cEl = boxEl && boxEl.querySelector('[data-v2-code="' + si + ':' + ti + '"]');
                items.push({
                    sheet_name: sh.sheet_name,
                    table_idx: ti,
                    name: (nEl && nEl.value.trim()) || t.suggested_name,
                    code: (cEl && cEl.value.trim()) || t._code || slugCode('v2', items.length),
                    fields: t.fields
                });
            });
        });
        if (!items.length) { setMsg('请至少勾选 1 张要创建的子表', true); return; }

        createBtn.disabled = true;
        $.ajax({
            url: BASE + '/api/create-batch',
            method: 'POST', contentType: 'application/json',
            data: JSON.stringify({
                token: state.token,
                module_key: moduleKey,
                group_name: groupName,
                period_tag: periodTag,
                safety_folder_id: safetyFolderId,
                combine_on_single_page: combineOnSinglePage,
                skip_error_rows: true,
                items: items
            })
        }).done(function (res) {
            if (!res || !res.success) { setMsg((res && res.msg) || '建表失败', true); return; }
            var created = (res.data && res.data.created) || [];
            if (typeof showToast === 'function') showToast(res.msg || '建表成功', 'success');
            if (created.length && created[0].code) {
                window.location.href = BASE + '/t/' + encodeURIComponent(created[0].code);
            }
        }).fail(function (xhr) {
            setMsg((xhr.responseJSON && xhr.responseJSON.msg) || '批量建表请求失败', true);
        }).always(function () { createBtn.disabled = false; });
    });
})();
