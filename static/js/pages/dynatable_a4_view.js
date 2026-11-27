/* dynatable_a4_view.js — 软表引擎 V2.5 补丁 B-2 统一双视图工作台控制器
 * 核心升级：
 *   1. 同时兼容 __ST_LIST__ 与 __ST_ROSTER__，挂载点与台账建表 100% 同一工作台；
 *   2. 真正消费 layout.combine_on_single_page 与 siblings：
 *      - side_by_side（如高龄津贴增减变动表）：合并渲染左侧+右侧双层表头大表，失焦按子表 code 分别落库；
 *      - vertical_sections（如 1.1 人员名单三段异构小表）：同页纵向拼装，按区块子表 code 分别落库；
 *   3. 列表视图顶部「独立软表分区」+ 容错「增行/减行/增列/减列」；
 *   4. 全面替换浏览器原生 prompt/confirm 为 ADR-022 页内弹窗，支持整组结转 clone_group。 */
(function () {
    'use strict';

    var root = document.getElementById('dtV2A4Wrap');
    var switchBar = document.getElementById('dtV2SwitchBar');
    var BOOT = window.__ST_LIST__ || window.__ST_ROSTER__;
    if (!root || !switchBar || !BOOT) return;

    var CODE = BOOT.code;
    var BASE = '/dynatable';
    var IS_ROSTER = !window.__ST_LIST__ && !!window.__ST_ROSTER__;
    var state = {
        mode: 'list',          // 'list' | 'a4' | 'format'
        loaded: false,
        table: null,
        fields: BOOT.fields || [],
        layout: null,
        records: [],
        siblings: [],
        truncated: false,
        unlockedCells: {},     // { 'code:rid:fcode': plaintext }
        selectedFmtKey: null,
        colModalMode: 'add'
    };

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function toast(msg, type) {
        if (typeof showToast === 'function') showToast(msg, type || 'success');
    }
    function fail(xhr) {
        var msg = '请求失败';
        try { msg = xhr.responseJSON ? xhr.responseJSON.msg : msg; } catch (e) {}
        toast(msg || '请求失败', 'danger');
    }
    function unwrapCustomSelect(sel) {
        if (!sel) return;
        var wrap = sel.closest('.custom-select');
        if (wrap && wrap.parentNode) {
            sel.classList.remove('cs-native');
            sel.style.display = '';
            wrap.parentNode.insertBefore(sel, wrap);
            wrap.parentNode.removeChild(wrap);
        }
    }
    function siblingHref(sibCode) {
        if (IS_ROSTER && BOOT.urls && BOOT.urls.page) {
            return BOOT.urls.page.replace(CODE, sibCode);
        }
        return BASE + '/t/' + encodeURIComponent(sibCode);
    }
    function isCombinedSideBySide() {
        return !!(state.layout && state.layout.combine_on_single_page &&
            state.layout.layout_mode === 'side_by_side' &&
            state.siblings && state.siblings.length >= 2 &&
            state.siblings[0].fields && state.siblings[1].fields);
    }
    function isCombinedVertical() {
        return !!(state.layout && state.layout.combine_on_single_page &&
            state.layout.layout_mode === 'vertical_sections' &&
            state.siblings && state.siblings.length >= 2 &&
            state.siblings[0].fields);
    }

    function renderSiblingsUI() {
        var sibs = state.siblings || [];
        var hasMulti = sibs.length > 1;
        var partLbl = document.getElementById('dtV2ListPartLabel');
        var partList = document.getElementById('dtV2ListPartitions');
        if (partLbl) partLbl.hidden = !hasMulti;
        if (partList) {
            partList.innerHTML = hasMulti ? sibs.map(function (s) {
                var active = s.code === CODE;
                return '<a class="dtv2-pill' + (active ? ' active' : '') + '" href="' + siblingHref(s.code) + '">' + esc(s.name) + '</a>';
            }).join('') : '';
        }

        // 更新模式徽章
        var badgeEl = document.getElementById('dtV2ModeBadges');
        if (badgeEl && state.layout) {
            var mMap = {
                side_by_side: '横向并排双表 + 双层表头',
                vertical_sections: '单页纵向多区块合页',
                standard_titled: '带合并大标题标准表',
                standard: '标准二维表'
            };
            var lbl = mMap[state.layout.layout_mode] || '软表 V2.5';
            badgeEl.innerHTML = '<span class="dtv2-tag dtv2-tag--ok">' + esc(lbl) + '</span>';
        }
    }

    function applyPaperStyle(el) {
        if (!el || !state.layout) return;
        var orient = state.layout.paper_orient || 'landscape';
        var margin = state.layout.paper_margin || 'normal';
        el.classList.toggle('is-landscape', orient === 'landscape');
        el.classList.remove('margin-narrow', 'margin-normal', 'margin-wide');
        el.classList.add('margin-' + margin);
        var mMap = { narrow: '10mm', normal: '15mm', wide: '20mm' };
        var dyn = document.getElementById('dtV2PageStyle');
        if (dyn) dyn.textContent = '@page { size: A4 ' + orient + '; margin: ' + (mMap[margin] || '15mm') + '; }';
        var oSel = document.getElementById('dtV2PaperOrient');
        var mSel = document.getElementById('dtV2PaperMargin');
        if (oSel) oSel.value = orient;
        if (mSel) mSel.value = margin;
        var hint = document.getElementById('dtV2PaperHint');
        if (hint) {
            hint.textContent = (orient === 'landscape' ? 'A4 横向 297mm' : 'A4 纵向 210mm') +
                ' · 跨页自动重复表头' + (state.layout.period_tag ? (' · 期次：' + state.layout.period_tag) : '');
        }
    }

    function cellStyleStr(fmtKey, defaultAlign) {
        var st = (state.layout && state.layout.cell_styles && state.layout.cell_styles[fmtKey]) || {};
        var parts = [];
        if (st.font_family) parts.push('font-family:' + st.font_family);
        parts.push('font-size:' + (st.font_px || 12) + 'px');
        if (st.bold) parts.push('font-weight:700');
        parts.push('text-align:' + (st.align || defaultAlign || 'center'));
        parts.push('vertical-align:' + (st.valign || 'middle'));
        return parts.join(';');
    }

    function renderCellInner(tblCode, rec, f, forPrint, isFmt) {
        var rid = rec ? rec.id : 0;
        var uKey = tblCode + ':' + rid + ':' + f.code;
        var isUnlocked = Object.prototype.hasOwnProperty.call(state.unlockedCells, uKey);
        var dispVal = isUnlocked ? state.unlockedCells[uKey] : (rec && rec[f.code] != null ? String(rec[f.code]) : '');
        var isSensLocked = (f.sensitivity === 'identity' && !isUnlocked && dispVal !== '');
        if (forPrint || isFmt) return esc(dispVal);
        if (!rid) return '<span style="color:var(--c-muted-2)"></span>';
        if (isSensLocked) {
            return '<span class="dtv2-locked" data-unlock-code-tbl="' + esc(tblCode) + '" data-unlock-rid="' + rid +
                '" data-unlock-fcode="' + esc(f.code) + '" title="敏感加密列：点击解密后才可编辑，防止误覆写密文">🔒 ' + esc(dispVal) + '</span>';
        }
        return '<div class="dtv2-cell-ed" contenteditable="true" spellcheck="false" data-tbl-code="' + esc(tblCode) +
            '" data-rid="' + rid + '" data-fcode="' + esc(f.code) + '">' + esc(dispVal) + '</div>';
    }

    function normalizePcts(rawPcts, count) {
        var arr = (rawPcts && rawPcts.length === count) ? rawPcts.slice() : null;
        if (!arr) {
            var eq = Math.round((100 / Math.max(1, count)) * 100) / 100;
            arr = [];
            for (var i = 0; i < count; i++) arr.push(eq);
            if (arr.length) arr[arr.length - 1] = Math.round((100 - eq * (arr.length - 1)) * 100) / 100;
        }
        return arr;
    }

    function renderSideBySideA4(forPrint, isFmt) {
        var sL = state.siblings[0];
        var sR = state.siblings[1];
        var fL = sL.fields || [], fR = sR.fields || [];
        var rL = sL.records || [], rR = sR.records || [];
        var allCount = fL.length + fR.length;
        var pL = normalizePcts((sL.layout && sL.layout.col_pcts), fL.length);
        var pR = normalizePcts((sR.layout && sR.layout.col_pcts), fR.length);
        var scaleL = fL.length / Math.max(1, allCount);
        var scaleR = fR.length / Math.max(1, allCount);
        var combinedPcts = pL.map(function (x) { return Math.round(x * scaleL * 100) / 100; })
            .concat(pR.map(function (x) { return Math.round(x * scaleR * 100) / 100; }));

        var titles = (sL.layout && sL.layout.title_rows && sL.layout.title_rows.length)
            ? sL.layout.title_rows : (state.layout.title_rows || []);
        var html = '';
        titles.forEach(function (tr, ti) {
            var cls = tr.is_meta ? 'dtv2-doc-meta' : 'dtv2-doc-title';
            var ed = (!forPrint && !isFmt) ? ' contenteditable="true" spellcheck="false" data-edit-title="' + ti + '"' : '';
            html += '<div class="' + cls + '"' + ed + '>' + esc(tr.text || '') + '</div>';
        });

        var titleL = (sL.layout && sL.layout.panel_title) || sL.panel_title || sL.name;
        var titleR = (sR.layout && sR.layout.panel_title) || sR.panel_title || sR.name;
        html += '<table class="dtv2-table"><colgroup>' +
            combinedPcts.map(function (p) { return '<col style="width:' + p + '%">'; }).join('') +
            '</colgroup><thead>' +
            '<tr><th colspan="' + fL.length + '" style="text-align:center;font-size:14px">' + esc(titleL) + '</th>' +
            '<th colspan="' + fR.length + '" style="text-align:center;font-size:14px">' + esc(titleR) + '</th></tr><tr>';

        fL.concat(fR).forEach(function (f, ci) {
            var fk = 'h:' + ci;
            html += '<th data-fmt-key="' + fk + '" style="' + cellStyleStr(fk, 'center') + '">' + esc(f.display_name) + '</th>';
        });
        html += '</tr></thead><tbody>';

        var maxRows = Math.max(rL.length, rR.length, 4);
        for (var ri = 0; ri < maxRows; ri++) {
            var recL = rL[ri] || null;
            var recR = rR[ri] || null;
            html += '<tr data-ri="' + ri + '">';
            fL.forEach(function (f, ci) {
                var fk = ri + ':' + ci;
                html += '<td data-fmt-key="' + fk + '" style="' + cellStyleStr(fk, 'center') + '">' +
                    renderCellInner(sL.code, recL, f, forPrint, isFmt) + '</td>';
            });
            fR.forEach(function (f, ci) {
                var fk = ri + ':' + (fL.length + ci);
                html += '<td data-fmt-key="' + fk + '" style="' + cellStyleStr(fk, 'center') + '">' +
                    renderCellInner(sR.code, recR, f, forPrint, isFmt) + '</td>';
            });
            html += '</tr>';
        }
        html += '</tbody></table>';
        if (!forPrint && !isFmt) {
            html += '<div class="dtv2-sec-actions">' +
                '<button type="button" class="sc-btn sc-btn--secondary" data-add-for-code="' + esc(sL.code) + '" style="padding:3px 10px;font-size:12px">＋ 左侧【' + esc(titleL.replace(/\s+/g, '')) + '】加 1 行</button>' +
                '<button type="button" class="sc-btn sc-btn--secondary" data-add-for-code="' + esc(sR.code) + '" style="padding:3px 10px;font-size:12px">＋ 右侧【' + esc(titleR.replace(/\s+/g, '')) + '】加 1 行</button>' +
            '</div>';
        }
        return html;
    }

    function renderVerticalSectionsA4(forPrint, isFmt) {
        var html = '';
        state.siblings.forEach(function (sec, si) {
            var fields = sec.fields || [];
            var recs = sec.records || [];
            var pcts = normalizePcts((sec.layout && sec.layout.col_pcts), fields.length);
            var secTitle = (sec.layout && sec.layout.title_rows && sec.layout.title_rows[0] && sec.layout.title_rows[0].text) || sec.name;
            html += '<div class="dtv2-doc-title" style="font-size:16px;margin-top:' + (si ? '10px' : '0') + '">' + esc(secTitle) + '</div>';
            html += '<table class="dtv2-table"><colgroup>' +
                pcts.map(function (p) { return '<col style="width:' + p + '%">'; }).join('') +
                '</colgroup><thead><tr>' +
                fields.map(function (f) { return '<th style="text-align:center">' + esc(f.display_name) + '</th>'; }).join('') +
                '</tr></thead><tbody>';
            if (!recs.length) {
                html += '<tr><td colspan="' + fields.length + '" style="text-align:center;color:var(--c-muted-2);padding:12px">暂无数据</td></tr>';
            } else {
                recs.forEach(function (rec) {
                    html += '<tr>' + fields.map(function (f) {
                        return '<td style="text-align:center">' + renderCellInner(sec.code, rec, f, forPrint, isFmt) + '</td>';
                    }).join('') + '</tr>';
                });
            }
            html += '</tbody></table>';
            if (!forPrint && !isFmt) {
                html += '<div class="dtv2-sec-actions"><button type="button" class="sc-btn sc-btn--secondary" data-add-for-code="' + esc(sec.code) + '" style="padding:3px 10px;font-size:12px">＋ 本区块增行</button></div>';
            }
        });
        return html;
    }

    function renderA4(targetEl, forPrint) {
        if (!targetEl || !state.layout) return;
        applyPaperStyle(targetEl);
        var isFmt = !forPrint && state.mode === 'format';
        targetEl.classList.toggle('is-fmt-mode', isFmt);

        if (isCombinedSideBySide()) {
            targetEl.innerHTML = renderSideBySideA4(forPrint, isFmt);
            if (!forPrint) {
                var infoS = document.getElementById('dtV2CountHint');
                if (infoS) infoS.textContent = '合页并排双表 · 左 ' + (state.siblings[0].records || []).length + ' 条 / 右 ' + (state.siblings[1].records || []).length + ' 条';
                var tSel = document.getElementById('dtV2BackfillTarget');
                if (tSel) {
                    unwrapCustomSelect(tSel);
                    tSel.hidden = false;
                    tSel.innerHTML = '<option value="' + esc(state.siblings[0].code) + '">回填左侧 ' + esc(state.siblings[0].name) + '</option>' +
                        '<option value="' + esc(state.siblings[1].code) + '">回填右侧 ' + esc(state.siblings[1].name) + '</option>';
                }
            }
            return;
        }
        if (isCombinedVertical()) {
            targetEl.innerHTML = renderVerticalSectionsA4(forPrint, isFmt);
            if (!forPrint) {
                var infoV = document.getElementById('dtV2CountHint');
                if (infoV) infoV.textContent = '合页纵向多区块 · 共 ' + state.siblings.length + ' 段异构小表';
            }
            return;
        }

        var fields = state.fields || [];
        var pcts = normalizePcts(state.layout.col_pcts, fields.length);
        state.layout.col_pcts = pcts;

        var html = '';
        (state.layout.title_rows || []).forEach(function (tr, ti) {
            var cls = tr.is_meta ? 'dtv2-doc-meta' : 'dtv2-doc-title';
            var ed = (!forPrint && !isFmt) ? ' contenteditable="true" spellcheck="false" data-edit-title="' + ti + '"' : '';
            html += '<div class="' + cls + '"' + ed + '>' + esc(tr.text || '') + '</div>';
        });

        html += '<table class="dtv2-table"><colgroup>' +
            pcts.map(function (p) { return '<col style="width:' + p + '%">'; }).join('') +
            '</colgroup><thead><tr>';
        fields.forEach(function (f, ci) {
            var fk = 'h:' + ci;
            var selCls = (isFmt && state.selectedFmtKey === fk) ? ' is-selected-fmt' : '';
            var resC = (!forPrint && ci < fields.length - 1) ? '<span class="dtv2-col-resizer" data-col="' + ci + '"></span>' : '';
            html += '<th class="' + selCls + '" data-fmt-key="' + fk + '" style="' + cellStyleStr(fk, 'center') + '">' +
                esc(f.display_name) + resC + '</th>';
        });
        html += '</tr></thead><tbody>';

        var rowHeights = state.layout.row_heights || {};
        if (!state.records.length) {
            html += '<tr><td colspan="' + fields.length + '" style="text-align:center;color:var(--c-muted-2);padding:20px">暂无数据，点击上方「＋ 末行增行」或「一键回填新增行」</td></tr>';
        } else {
            state.records.forEach(function (rec, ri) {
                var rh = rowHeights[String(ri)] || rowHeights[ri];
                html += '<tr data-ri="' + ri + '"' + (rh ? ' style="height:' + rh + 'px"' : '') + '>';
                fields.forEach(function (f, ci) {
                    var fk = ri + ':' + ci;
                    var selCls = (isFmt && state.selectedFmtKey === fk) ? ' is-selected-fmt' : '';
                    var resC = (!forPrint && ci < fields.length - 1) ? '<span class="dtv2-col-resizer" data-col="' + ci + '"></span>' : '';
                    var resR = !forPrint ? '<span class="dtv2-row-resizer" data-ri="' + ri + '"></span>' : '';
                    html += '<td class="' + selCls + '" data-fmt-key="' + fk + '" style="' + cellStyleStr(fk, 'center') + '">' +
                        renderCellInner(CODE, rec, f, forPrint, isFmt) + resC + resR + '</td>';
                });
                html += '</tr>';
            });
        }
        html += '</tbody></table>';
        (state.layout.footer_rows || []).forEach(function (fr, fi) {
            var ed = (!forPrint && !isFmt) ? ' contenteditable="true" spellcheck="false" data-edit-footer="' + fi + '"' : '';
            html += '<div class="dtv2-doc-meta"' + ed + '>' + esc(fr.text || '') + '</div>';
        });

        targetEl.innerHTML = html;
        if (!forPrint) {
            var info = document.getElementById('dtV2CountHint');
            if (info) info.textContent = '已加载 ' + state.records.length + ' 条' + (state.truncated ? '（A4 视图单次上限 300 行）' : '');
        }
    }

    function loadA4Data(cb) {
        $.getJSON(BASE + '/api/t/' + encodeURIComponent(CODE) + '/a4-data').done(function (res) {
            if (!res || !res.success) { toast((res && res.msg) || '加载 A4 视图失败', 'warning'); return; }
            state.loaded = true;
            state.table = res.data.table;
            state.fields = res.data.fields || state.fields;
            state.layout = res.data.layout;
            state.records = res.data.records || [];
            state.siblings = res.data.siblings || [];
            state.truncated = !!res.data.truncated;
            renderSiblingsUI();
            if (state.mode !== 'list') renderA4(document.getElementById('dtV2Canvas'), false);
            if (typeof cb === 'function') cb();
        }).fail(fail);
    }

    function searchResidents(q) {
        $.getJSON(BASE + '/api/residents/search', { q: q || '' }).done(function (res) {
            var items = (res && res.data) || [];
            var sel = document.getElementById('dtV2ResidentSelect');
            if (!sel) return;
            unwrapCustomSelect(sel);
            sel.innerHTML = items.length
                ? items.map(function (r) { return '<option value="' + r.id + '">' + esc(r.label) + '</option>'; }).join('')
                : '<option value="">（无匹配居民）</option>';
        });
    }

    function setMode(nextMode) {
        state.mode = nextMode;
        var listCard = document.getElementById('dtV2ListCard') ||
            (document.querySelector('#filterForm') && document.querySelector('#filterForm').closest('section.sc-card'));
        var listBar = document.getElementById('dtV2ListBar');
        var listOps = document.getElementById('dtV2ListOps');
        switchBar.querySelectorAll('[data-dtv2-mode]').forEach(function (b) {
            b.classList.toggle('active', b.getAttribute('data-dtv2-mode') === nextMode);
        });
        if (nextMode === 'list') {
            if (listCard) listCard.hidden = false;
            if (listBar) listBar.hidden = false;
            if (listOps) listOps.hidden = false;
            root.hidden = true;
            return;
        }
        if (listCard) listCard.hidden = true;
        // C-1：多 Sheet 标准表 A4 视图顶部保留子表切换条（合页并排/纵向时不保留）
        var keepPartitionNav = (state.siblings && state.siblings.length > 1 && !isCombinedSideBySide() && !isCombinedVertical());
        if (listBar) listBar.hidden = !keepPartitionNav;
        if (listOps) listOps.hidden = true;
        root.hidden = false;
        document.getElementById('dtV2FillBar').hidden = (nextMode === 'format');
        document.getElementById('dtV2FmtBar').hidden = (nextMode !== 'format');
        if (!state.loaded) {
            searchResidents('');
            loadA4Data();
        } else {
            renderA4(document.getElementById('dtV2Canvas'), false);
        }
    }

    function saveLayout() {
        if (!state.layout) return;
        var payload = $.extend({}, state.layout, { expected_version_ts: state.layout.version_ts || '' });
        $.ajax({
            url: BASE + '/api/t/' + encodeURIComponent(CODE) + '/layout',
            method: 'POST', contentType: 'application/json', data: JSON.stringify(payload)
        }).done(function (res) {
            if (!res || !res.success) { toast((res && res.msg) || '保存版式失败', 'warning'); return; }
            state.layout = res.data;
            toast('版式配置已保存');
        }).fail(function (xhr) {
            if (xhr.status === 409) { toast('版式已被其他终端更新，正在刷新...', 'warning'); loadA4Data(); }
            else fail(xhr);
        });
    }

    function createRowFor(targetCode, residentId) {
        var body = residentId ? { resident_id: residentId } : { values: {} };
        $.ajax({
            url: BASE + '/api/t/' + encodeURIComponent(targetCode) + '/record/create',
            method: 'POST', contentType: 'application/json', data: JSON.stringify(body)
        }).done(function (res) {
            if (!res || !res.success) { toast((res && res.msg) || '新增行失败', 'warning'); return; }
            toast(residentId ? '已从居民主档一键回填并加密入库' : '已追加 1 行空白记录');
            if (state.mode === 'list') { window.location.reload(); }
            else { loadA4Data(); }
        }).fail(fail);
    }

    // 视图切换 & 弹窗入口
    switchBar.addEventListener('click', function (e) {
        var modeBtn = e.target.closest('[data-dtv2-mode]');
        if (modeBtn) { setMode(modeBtn.getAttribute('data-dtv2-mode')); return; }
        if (e.target.closest('#dtV2BtnClonePeriod')) {
            var now = new Date();
            document.getElementById('dtV2ClonePeriodIn').value = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
            var hasMulti = state.siblings && state.siblings.length > 1;
            var gChk = document.getElementById('dtV2CloneGroupChk');
            if (gChk) { gChk.checked = hasMulti; gChk.disabled = !hasMulti; }
            document.getElementById('dtV2CloneOverlay').hidden = false;
            return;
        }
        if (e.target.closest('#dtV2BtnDeleteTable')) {
            document.getElementById('dtV2DelOverlay').hidden = false;
        }
    });

    // 列表视图容错操作：增行 / 减行 / 增列 / 减列
    var btnLAddR = document.getElementById('dtV2ListAddRow');
    var btnLDelR = document.getElementById('dtV2ListDelRow');
    var btnLAddC = document.getElementById('dtV2ListAddCol');
    var btnLDelC = document.getElementById('dtV2ListDelCol');
    if (btnLAddR) btnLAddR.addEventListener('click', function () { createRowFor(CODE, null); });
    if (btnLDelR) btnLDelR.addEventListener('click', function () {
        $.getJSON(BASE + '/api/t/' + encodeURIComponent(CODE) + '/a4-data', { limit: 300 }).done(function (res) {
            var recs = (res && res.data && res.data.records) || [];
            if (!recs.length) { toast('当前分区暂无可删除的记录行', 'warning'); return; }
            var last = recs[recs.length - 1];
            $.ajax({ url: BASE + '/api/t/' + encodeURIComponent(CODE) + '/record/' + last.id + '/delete', method: 'POST' })
                .done(function (r) { if (r && r.success) { toast('已移除末尾 1 行'); window.location.reload(); } else toast((r && r.msg) || '删行失败', 'danger'); })
                .fail(fail);
        }).fail(fail);
    });
    if (btnLAddC) btnLAddC.addEventListener('click', function () {
        state.colModalMode = 'add';
        document.getElementById('dtV2ColTitle').textContent = '新增字段列';
        document.getElementById('dtV2ColAddBox').hidden = false;
        document.getElementById('dtV2ColDelBox').hidden = true;
        document.getElementById('dtV2NewColName').value = '';
        unwrapCustomSelect(document.getElementById('dtV2NewColType'));
        unwrapCustomSelect(document.getElementById('dtV2NewColSens'));
        document.getElementById('dtV2ColOverlay').hidden = false;
    });
    if (btnLDelC) btnLDelC.addEventListener('click', function () {
        if (!state.fields || state.fields.length <= 1) { toast('至少保留 1 列字段', 'warning'); return; }
        state.colModalMode = 'del';
        document.getElementById('dtV2ColTitle').textContent = '移除字段列';
        document.getElementById('dtV2ColAddBox').hidden = true;
        document.getElementById('dtV2ColDelBox').hidden = false;
        var sel = document.getElementById('dtV2DelColSelect');
        unwrapCustomSelect(sel);
        sel.innerHTML = state.fields.map(function (f, i) {
            return '<option value="' + esc(f.id) + '">' + (i + 1) + '. ' + esc(f.display_name) + '</option>';
        }).join('');
        document.getElementById('dtV2ColOverlay').hidden = false;
    });

    // 统一关闭页内弹窗
    document.addEventListener('click', function (e) {
        var c = e.target.closest('[data-dtv2-close]');
        if (c) { var el = document.getElementById(c.getAttribute('data-dtv2-close')); if (el) el.hidden = true; }
    });

    document.getElementById('dtV2BtnConfirmClone').addEventListener('click', function () {
        var pTag = (document.getElementById('dtV2ClonePeriodIn').value || '').trim();
        if (!pTag) { toast('请输入新期次标签', 'warning'); return; }
        var btn = this; btn.disabled = true;
        $.ajax({
            url: BASE + '/api/t/' + encodeURIComponent(CODE) + '/clone-period',
            method: 'POST', contentType: 'application/json',
            data: JSON.stringify({
                period_tag: pTag,
                copy_records: document.getElementById('dtV2CloneCopyChk').checked,
                clone_group: document.getElementById('dtV2CloneGroupChk').checked
            })
        }).done(function (res) {
            if (!res || !res.success) { toast((res && res.msg) || '结转失败', 'warning'); return; }
            toast(res.msg || '新期次已创建');
            if (res.data && res.data.code) window.location.href = siblingHref(res.data.code);
        }).fail(fail).always(function () { btn.disabled = false; });
    });

    document.getElementById('dtV2BtnConfirmDel').addEventListener('click', function () {
        var btn = this; btn.disabled = true;
        $.ajax({ url: BASE + '/api/t/' + encodeURIComponent(CODE) + '/delete', method: 'POST', contentType: 'application/json', data: '{}' })
            .done(function (res) {
                if (!res || !res.success) { toast((res && res.msg) || '删除失败', 'danger'); btn.disabled = false; return; }
                toast(res.msg || '已安全删除台账');
                window.location.href = BASE + '/';
            }).fail(function (xhr) { fail(xhr); btn.disabled = false; });
    });

    document.getElementById('dtV2BtnConfirmCol').addEventListener('click', function () {
        var btn = this; btn.disabled = true;
        if (state.colModalMode === 'add') {
            var name = (document.getElementById('dtV2NewColName').value || '').trim();
            if (!name) { toast('请输入列名', 'warning'); btn.disabled = false; return; }
            $.ajax({
                url: BASE + '/api/t/' + encodeURIComponent(CODE) + '/field/add',
                method: 'POST', contentType: 'application/json',
                data: JSON.stringify({
                    display_name: name,
                    data_type: document.getElementById('dtV2NewColType').value || 'text',
                    sensitivity: document.getElementById('dtV2NewColSens').value || 'none'
                })
            }).done(function (res) {
                if (!res || !res.success) { toast((res && res.msg) || '新增列失败', 'danger'); btn.disabled = false; return; }
                toast('已新增列「' + name + '」');
                window.location.reload();
            }).fail(function (xhr) { fail(xhr); btn.disabled = false; });
        } else {
            var fid = document.getElementById('dtV2DelColSelect').value;
            if (!fid) { btn.disabled = false; return; }
            $.ajax({
                url: BASE + '/api/t/' + encodeURIComponent(CODE) + '/field/' + encodeURIComponent(fid) + '/delete',
                method: 'POST', contentType: 'application/json', data: '{}'
            }).done(function (res) {
                if (!res || !res.success) { toast((res && res.msg) || '移除列失败', 'danger'); btn.disabled = false; return; }
                toast('已移除所选字段列');
                window.location.reload();
            }).fail(function (xhr) { fail(xhr); btn.disabled = false; });
        }
    });

    // A4 工具条交互
    var rTimer = null;
    document.getElementById('dtV2ResidentQuery').addEventListener('input', function () {
        var q = this.value.trim();
        window.clearTimeout(rTimer);
        rTimer = window.setTimeout(function () { searchResidents(q); }, 250);
    });
    document.getElementById('dtV2BtnAddRow').addEventListener('click', function () { createRowFor(CODE, null); });
    document.getElementById('dtV2BtnBackfill').addEventListener('click', function () {
        var rid = parseInt(document.getElementById('dtV2ResidentSelect').value, 10);
        if (!rid) { toast('请先选择要回填的居民', 'warning'); return; }
        // C-1：并排双表时可选择回填左/右区
        var tSel = document.getElementById('dtV2BackfillTarget');
        var targetCode = (tSel && !tSel.hidden && tSel.value) ? tSel.value : CODE;
        createRowFor(targetCode, rid);
    });

    // C-1：A4 一键明文切换（当前页 ≤50 条逐格 reveal；再点恢复脱敏显示）
    var revBtn = document.getElementById('dtV2BtnToggleReveal');
    if (revBtn) {
        revBtn.addEventListener('click', function () {
            var hasUnlocked = Object.keys(state.unlockedCells).length > 0;
            if (hasUnlocked) {
                state.unlockedCells = {};
                revBtn.textContent = '查看明文（当前页数据）';
                renderA4(document.getElementById('dtV2Canvas'), false);
                toast('已恢复默认脱敏显示');
                return;
            }
            var pairs = [];
            if (isCombinedSideBySide() || isCombinedVertical()) {
                state.siblings.forEach(function (s) {
                    (s.records || []).slice(0, 50).forEach(function (r) { pairs.push([s.code, r.id]); });
                });
            } else {
                (state.records || []).slice(0, 50).forEach(function (r) { pairs.push([CODE, r.id]); });
            }
            if (!pairs.length) { toast('当前表无可解密的记录', 'warning'); return; }
            revBtn.disabled = true;
            var done = 0, forbidden = false;
            pairs.forEach(function (p) {
                $.ajax({ url: BASE + '/api/t/' + encodeURIComponent(p[0]) + '/record/' + p[1] + '/reveal', method: 'POST' })
                    .done(function (res) {
                        if (res && res.success && res.data) {
                            Object.keys(res.data).forEach(function (k) { state.unlockedCells[p[0] + ':' + p[1] + ':' + k] = res.data[k]; });
                        }
                    })
                    .fail(function (xhr) { if (xhr.status === 403) forbidden = true; })
                    .always(function () {
                        done += 1;
                        if (done === pairs.length) {
                            revBtn.disabled = false;
                            if (forbidden && !Object.keys(state.unlockedCells).length) {
                                toast('无权限查看明文数据标识字段', 'danger');
                            } else {
                                revBtn.textContent = '恢复脱敏（当前页数据）';
                                renderA4(document.getElementById('dtV2Canvas'), false);
                                toast('已解开当前页明文显示');
                            }
                        }
                    });
            });
        });
    }

    // 画布行内编辑、合页按子表增行、敏感格解密、版式选中
    var canvasEl = document.getElementById('dtV2Canvas');
    canvasEl.addEventListener('focusout', function (e) {
        var cellEl = e.target.closest('.dtv2-cell-ed');
        if (cellEl) {
            var tCode = cellEl.getAttribute('data-tbl-code') || CODE;
            var rid = parseInt(cellEl.getAttribute('data-rid'), 10);
            var fcode = cellEl.getAttribute('data-fcode');
            var val = (cellEl.innerText || '').replace(/\r\n/g, '\n').replace(/\n$/, '');
            if (!rid) return;
            $.ajax({
                url: BASE + '/api/t/' + encodeURIComponent(tCode) + '/record/' + rid,
                method: 'POST', contentType: 'application/json',
                data: JSON.stringify({ values: (function () { var o = {}; o[fcode] = val; return o; })() })
            }).done(function (res) {
                if (!res || !res.success) { toast((res && res.msg) || '保存失败', 'danger'); return; }
                var uKey = tCode + ':' + rid + ':' + fcode;
                if (Object.prototype.hasOwnProperty.call(state.unlockedCells, uKey)) state.unlockedCells[uKey] = val;
            }).fail(fail);
            return;
        }
        var tIdx = e.target.getAttribute('data-edit-title');
        if (tIdx !== null && state.layout && state.layout.title_rows) {
            state.layout.title_rows[parseInt(tIdx, 10)].text = e.target.innerText;
        }
    });

    canvasEl.addEventListener('click', function (e) {
        var addFor = e.target.closest('[data-add-for-code]');
        if (addFor) { createRowFor(addFor.getAttribute('data-add-for-code'), null); return; }

        var unlock = e.target.closest('.dtv2-locked');
        if (unlock) {
            var tCode = unlock.getAttribute('data-unlock-code-tbl') || CODE;
            var rid = parseInt(unlock.getAttribute('data-unlock-rid'), 10);
            var fcode = unlock.getAttribute('data-unlock-fcode');
            $.ajax({ url: BASE + '/api/t/' + encodeURIComponent(tCode) + '/record/' + rid + '/reveal', method: 'POST' })
                .done(function (res) {
                    if (!res || !res.success) { toast((res && res.msg) || '无权解密', 'danger'); return; }
                    var data = res.data || {};
                    Object.keys(data).forEach(function (k) { state.unlockedCells[tCode + ':' + rid + ':' + k] = data[k]; });
                    renderA4(canvasEl, false);
                    toast('已解锁明文，可直接编辑');
                }).fail(fail);
            return;
        }

        if (state.mode === 'format') {
            if (e.target.closest('.dtv2-col-resizer') || e.target.closest('.dtv2-row-resizer')) return;
            var cell = e.target.closest('[data-fmt-key]');
            if (!cell || !state.layout) return;
            state.selectedFmtKey = cell.getAttribute('data-fmt-key');
            document.getElementById('dtV2FmtLabel').textContent = '已选格：' + state.selectedFmtKey;
            renderA4(canvasEl, false);
        }
    });

    // 版式微调控件
    function ensureStyle() {
        if (!state.selectedFmtKey) { toast('请先点击表格中任意单元格', 'warning'); return null; }
        if (!state.layout.cell_styles) state.layout.cell_styles = {};
        if (!state.layout.cell_styles[state.selectedFmtKey]) state.layout.cell_styles[state.selectedFmtKey] = {};
        return state.layout.cell_styles[state.selectedFmtKey];
    }
    document.getElementById('dtV2FmtFont').addEventListener('change', function () { var o = ensureStyle(); if (o) { o.font_family = this.value; renderA4(canvasEl, false); } });
    document.getElementById('dtV2FmtSize').addEventListener('input', function () { var o = ensureStyle(); if (o) { o.font_px = Math.max(10, Math.min(28, parseInt(this.value, 10) || 12)); renderA4(canvasEl, false); } });
    document.getElementById('dtV2FmtBold').addEventListener('change', function () { var o = ensureStyle(); if (o) { o.bold = this.checked; renderA4(canvasEl, false); } });
    document.getElementById('dtV2FmtHAlign').addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (!b) return;
        var o = ensureStyle(); if (!o) return;
        o.align = b.getAttribute('data-val');
        document.querySelectorAll('#dtV2FmtHAlign button').forEach(function (x) { x.classList.toggle('active', x === b); });
        renderA4(canvasEl, false);
    });
    document.getElementById('dtV2FmtVAlign').addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (!b) return;
        var o = ensureStyle(); if (!o) return;
        o.valign = b.getAttribute('data-val');
        document.querySelectorAll('#dtV2FmtVAlign button').forEach(function (x) { x.classList.toggle('active', x === b); });
        renderA4(canvasEl, false);
    });
    document.getElementById('dtV2BtnRowAll').addEventListener('click', function () {
        if (!state.layout) return;
        var h = Math.max(22, Math.min(180, parseInt(document.getElementById('dtV2FmtRowH').value, 10) || 32));
        state.layout.row_heights = {};
        state.records.forEach(function (_, ri) { state.layout.row_heights[String(ri)] = h; });
        renderA4(canvasEl, false);
        toast('全表行高已统一为 ' + h + 'px');
    });
    document.getElementById('dtV2BtnSaveLayout').addEventListener('click', saveLayout);
    document.getElementById('dtV2PaperOrient').addEventListener('change', function () { if (state.layout) { state.layout.paper_orient = this.value; renderA4(canvasEl, false); } });
    document.getElementById('dtV2PaperMargin').addEventListener('change', function () { if (state.layout) { state.layout.paper_margin = this.value; renderA4(canvasEl, false); } });
    document.getElementById('dtV2BtnFitA4').addEventListener('click', function () {
        if (!state.layout || !state.layout.col_pcts) return;
        var arr = state.layout.col_pcts;
        var sum = arr.reduce(function (a, b) { return a + Number(b || 0); }, 0) || 1;
        for (var i = 0; i < arr.length; i++) arr[i] = Math.max(3, Math.round((arr[i] / sum) * 10000) / 100);
        var diff = Math.round((100 - arr.reduce(function (a, b) { return a + b; }, 0)) * 100) / 100;
        arr[arr.length - 1] = Math.max(3, Math.round((arr[arr.length - 1] + diff) * 100) / 100);
        renderA4(canvasEl, false);
        toast('已按 100% 比例严格适配 A4 单页宽');
    });

    // 高级筛选折叠切换
    var advBtn = document.getElementById('dtV2ToggleAdvFilter');
    var advPanel = document.getElementById('dtV2AdvFilterPanel');
    if (advBtn && advPanel) {
        advBtn.addEventListener('click', function () {
            advPanel.hidden = !advPanel.hidden;
            advBtn.textContent = advPanel.hidden ? '高级筛选 ▾' : '收起筛选 ▴';
        });
    }

    // 套打预览
    document.getElementById('dtV2BtnPrint').addEventListener('click', function () {
        if (!state.layout) return;
        renderA4(document.getElementById('dtV2PrintPaper'), true);
        document.getElementById('dtV2PrintOverlay').hidden = false;
    });
    document.getElementById('dtV2BtnDoPrint').addEventListener('click', function () { window.print(); });

    // 首屏拉取同工作簿兄弟表与版式元数据
    $.getJSON(BASE + '/api/t/' + encodeURIComponent(CODE) + '/a4-data', { limit: 1 }).done(function (res) {
        if (res && res.success && res.data) {
            state.layout = res.data.layout;
            state.siblings = res.data.siblings || [];
            renderSiblingsUI();
        }
    });
})();
