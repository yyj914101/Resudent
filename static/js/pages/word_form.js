/* word_form.js — 报表审批（通用 Word .docx 表单引擎前端工作台）
 * 零构建链（ADR-006/011）、IIFE（024）、事件委托（016）、ADR-022 中性灰。
 * 支持：上传任意 .docx 自动解析、全段落与全单元格文本直接编辑、复选框勾选与周边文字同格编辑、
 * 居民一键回填 + 纯自定义手填、段落/单元格字体字号加粗对齐、列宽/行高实时拖拽、
 * 固定 A4 纸张单页宽适配与原版 .docx 导出。 */
(function () {
    'use strict';

    var BASE = '/word-form';
    var state = {
        templates: [],
        records: [],
        activeTpl: null,
        activeRecId: null,
        mode: 'fill', // 'fill' | 'format' | 'bind'
        values: {},
        selectedCellId: null,
        selectedParaIdx: null,
        selectedResidentId: null,
        fieldLabels: {}
    };

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function toast(msg, type) {
        if (typeof showToast === 'function') { showToast(msg, type || 'success'); }
        else { window.alert(msg); }
    }
    function fail(xhr) {
        var msg = '请求失败';
        try { msg = xhr.responseJSON ? xhr.responseJSON.msg : msg; } catch (e) {}
        toast(msg || '请求失败', 'danger');
    }
    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    /** 从 contenteditable 宿主取纯文本：<br>/块级子节点→\n，复选框 span→□/☑ 原字符。
     *  用于「全文本可编辑」模式下把 DOM 现状回读成 state.values 里的纯文本。 */
    function extractEditableText(el) {
        if (!el) return '';
        var out = [];
        function walk(node, isRoot) {
            if (node.nodeType === 3) {
                out.push(node.nodeValue || '');
                return;
            }
            if (node.nodeType !== 1) return;
            var tag = node.tagName;
            if (tag === 'BR') {
                out.push('\n');
                return;
            }
            if (node.classList && node.classList.contains('wf-cb-btn')) {
                out.push(isCheckedChar(node.textContent.trim()) ? CHECKED : UNCHECKED);
                return;
            }
            var isBlock = !isRoot && (tag === 'DIV' || tag === 'P');
            if (isBlock && out.length && out[out.length - 1] !== '\n') {
                out.push('\n');
            }
            for (var i = 0; i < node.childNodes.length; i++) {
                walk(node.childNodes[i], false);
            }
        }
        walk(el, true);
        return out.join('').replace(/\r\n/g, '\n').replace(/\n$/, '');
    }

    function initFieldLabelMap() {
        var sel = document.getElementById('wfBindFieldSelect');
        if (!sel) return;
        for (var i = 0; i < sel.options.length; i++) {
            state.fieldLabels[sel.options[i].value] = sel.options[i].textContent;
        }
    }

    function applyPaperClasses(el) {
        if (!el || !state.activeTpl) return;
        var orient = state.activeTpl.paper_orient || 'portrait';
        var margin = state.activeTpl.paper_margin || 'normal';
        el.classList.toggle('is-landscape', orient === 'landscape');
        el.classList.remove('margin-narrow', 'margin-normal', 'margin-wide');
        el.classList.add('margin-' + margin);
        var mMap = { narrow: '10mm', normal: '15mm', wide: '20mm' };
        var dyn = document.getElementById('wfDynamicPageStyle');
        if (dyn) {
            dyn.textContent = '@page { size: A4 ' + (orient === 'landscape' ? 'landscape' : 'portrait') +
                '; margin: ' + (mMap[margin] || '15mm') + '; }';
        }
    }

    function updatePaperHint() {
        var hint = document.getElementById('wfPaperHint');
        if (!hint || !state.activeTpl) return;
        var tbl = (state.activeTpl.blocks || []).filter(function (b) { return b.type === 'table'; })[0];
        var sum = tbl && tbl.col_pcts ? tbl.col_pcts.reduce(function (a, b) { return a + Number(b || 0); }, 0) : 100;
        var orient = state.activeTpl.paper_orient === 'landscape' ? 'A4 横向 297mm' : 'A4 纵向 210mm';
        hint.textContent = orient + ' · 表格列宽总和 ' + sum.toFixed(1) + '% · 严格适配单页宽（打印不超页）';
        document.getElementById('wfPaperOrient').value = state.activeTpl.paper_orient || 'portrait';
        document.getElementById('wfPaperMargin').value = state.activeTpl.paper_margin || 'normal';
    }

    function findCellAndTable(cellId) {
        if (!state.activeTpl || !cellId) return null;
        var blocks = state.activeTpl.blocks || [];
        for (var bi = 0; bi < blocks.length; bi++) {
            if (blocks[bi].type !== 'table') continue;
            var rows = blocks[bi].rows || [];
            for (var ri = 0; ri < rows.length; ri++) {
                for (var ci = 0; ci < rows[ri].length; ci++) {
                    if (rows[ri][ci].id === cellId) {
                        return { tbl: blocks[bi], cell: rows[ri][ci] };
                    }
                }
            }
        }
        return null;
    }

    function activeFmtTarget() {
        if (!state.activeTpl) return null;
        if (state.selectedParaIdx !== null) {
            var p = (state.activeTpl.blocks || [])[state.selectedParaIdx];
            return p ? { kind: 'para', obj: p } : null;
        }
        var ctx = findCellAndTable(state.selectedCellId);
        return ctx ? { kind: 'cell', obj: ctx.cell, tbl: ctx.tbl } : null;
    }

    // 勾选框字符族：□☐ 未勾选 / ☑☒ 已勾选。用工厂函数避免 global 正则 lastIndex 串状态
    var UNCHECKED = '□';
    var CHECKED = '☑';
    function cbRe() { return /[□☐☑☒]/g; }
    function isCheckedChar(ch) { return ch === '☑' || ch === '☒'; }

    function renderCheckboxRichText(text, cellId) {
        var idx = 0;
        return esc(text).replace(cbRe(), function (ch) {
            var ci = idx++;
            var checked = isCheckedChar(ch);
            // 用 span（非 button）+ contenteditable=false：让宿主 contenteditable 里
            // 周边文字可显示光标编辑，而勾选框本身仍可点击切换、不会被编辑删掉。
            return '<span role="button" class="wf-cb-btn' + (checked ? ' checked' : '') +
                '" contenteditable="false" data-cb-cell="' + esc(cellId) + '" data-cb-idx="' + ci +
                '" title="点击勾选/取消">' + (checked ? CHECKED : UNCHECKED) + '</span>';
        }).replace(/\n/g, '<br>');
    }

    function renderPaper(targetEl, forPrint) {
        if (!targetEl) return;
        if (!state.activeTpl) {
            targetEl.innerHTML = '<div class="wf-empty">请点击右上角「＋ 上传 .docx 模板」导入任意 Word 审批表或公示表</div>';
            return;
        }
        applyPaperClasses(targetEl);
        var isBind = !forPrint && state.mode === 'bind';
        var isFmt = !forPrint && state.mode === 'format';
        var isFill = !forPrint && state.mode === 'fill';
        targetEl.classList.toggle('is-fmt-mode', isFmt);
        var html = '';
        var editableCount = 0;
        var boundCount = 0;

        (state.activeTpl.blocks || []).forEach(function (blk, bi) {
            if (blk.type === 'paragraph') {
                var pKey = 'p_' + bi;
                var pVal = state.values[pKey] !== undefined ? state.values[pKey] : (blk.text || '');
                var pPx = blk.font_px || Math.max(12, Math.round((blk.font_pt || 12) * 1.25));
                var pFam = blk.font_family ? ('font-family:' + blk.font_family + ';') : '';
                var style = pFam + 'text-align:' + (blk.align || 'left') + ';font-size:' + pPx + 'px;' + (blk.bold ? 'font-weight:700;' : '');
                if (isFmt) {
                    var pCls = 'wf-doc-para is-fmt-selectable' + (state.selectedParaIdx === bi ? ' is-selected-fmt' : '');
                    html += '<div class="' + pCls + '" data-para-idx="' + bi + '" style="' + style + '">' + esc(pVal).replace(/\n/g, '<br>') + '</div>';
                } else if (isFill) {
                    // 全段落可编辑（标题/正文/落款一致）：不再按 blk.editable 白名单收口
                    html += '<div class="wf-doc-para is-inline-editable" contenteditable="true" spellcheck="false" data-edit-para="' + pKey +
                        '" style="' + style + '">' + esc(pVal).replace(/\n/g, '<br>') + '</div>';
                } else {
                    html += '<div class="wf-doc-para" style="' + style + '">' + esc(pVal).replace(/\n/g, '<br>') + '</div>';
                }
                return;
            }

            if (blk.type !== 'table') return;
            var pcts = blk.col_pcts || [];
            if (!blk.row_heights) blk.row_heights = {};
            html += '<table class="wf-doc-table"><colgroup>' +
                pcts.map(function (pct) { return '<col style="width:' + pct + '%">'; }).join('') +
                '</colgroup><tbody>';

            (blk.rows || []).forEach(function (row, ri) {
                var rh = blk.row_heights[String(ri)] || blk.row_heights[ri];
                html += '<tr' + (rh ? ' style="height:' + rh + 'px"' : '') + ' data-tbl="' + blk.tbl_idx + '" data-ri="' + ri + '">';
                row.forEach(function (cell) {
                    if (cell.editable) editableCount++;
                    if (cell.bind) boundCount++;
                    var curVal = state.values[cell.id] !== undefined ? state.values[cell.id] : (cell.text || '');
                    var cls = [];
                    // 填报模式：全表格单元格（含表头标签、合并格）都可直接键入，不再区分是否「可填格」
                    if (isFill) cls.push('is-cell-text-editable');
                    if (isFill && cell.editable) cls.push('is-editable');
                    if (isBind && state.selectedCellId === cell.id) cls.push('is-selected-bind');
                    if (isFmt && state.selectedCellId === cell.id) cls.push('is-selected-fmt');

                    var vAlign = cell.valign || 'middle';
                    var fPx = cell.font_px || 12;
                    var fFam = cell.font_family ? ('font-family:' + cell.font_family + ';') : '';
                    var styleStr = fFam + 'text-align:' + (cell.align || 'center') + ';vertical-align:' + vAlign +
                        ';font-size:' + fPx + 'px;' + (cell.bold ? 'font-weight:700;' : '');
                    var attrs = ' data-cell-id="' + esc(cell.id) + '" data-tbl="' + blk.tbl_idx + '"' +
                        (cell.colspan > 1 ? ' colspan="' + cell.colspan + '"' : '') +
                        (cell.rowspan > 1 ? ' rowspan="' + cell.rowspan + '"' : '') +
                        ' style="' + styleStr + '"' +
                        (cls.length ? ' class="' + cls.join(' ') + '"' : '');

                    var inner = '';
                    if (isBind) {
                        inner = esc(curVal || '（空格）').replace(/\n/g, '<br>');
                        if (cell.bind) {
                            inner += '<span class="wf-bind-chip">' + esc(state.fieldLabels[cell.bind] || cell.bind) + '</span>';
                        }
                    } else if (isFmt) {
                        inner = esc(curVal || '（空格）').replace(/\n/g, '<br>');
                    } else if (forPrint) {
                        inner = esc(curVal).replace(/\n/g, '<br>');
                    } else if (cell.has_checkbox) {
                        // 复选框格：勾选框 + 周边文字同格编辑（span 不可编辑，文字可编辑）
                        inner = '<div class="wf-cell-editable" contenteditable="true" spellcheck="false" data-edit-cell="' + esc(cell.id) + '">' +
                            renderCheckboxRichText(curVal, cell.id) + '</div>';
                    } else {
                        inner = '<div class="wf-cell-editable" contenteditable="true" spellcheck="false" data-edit-cell="' + esc(cell.id) + '">' +
                            esc(curVal).replace(/\n/g, '<br>') + '</div>';
                    }

                    var rightCol = (cell.ci || 0) + (cell.colspan || 1) - 1;
                    var colResizer = (!forPrint && rightCol < pcts.length - 1)
                        ? '<span class="wf-col-resizer" data-tbl="' + blk.tbl_idx + '" data-col="' + rightCol + '" title="左右拖拽调列宽"></span>'
                        : '';
                    var bottomRi = ri + (cell.rowspan || 1) - 1;
                    var rowResizer = !forPrint
                        ? '<span class="wf-row-resizer" data-tbl="' + blk.tbl_idx + '" data-ri="' + bottomRi + '" title="上下拖拽调行高"></span>'
                        : '';
                    html += '<td' + attrs + '>' + inner + colResizer + rowResizer + '</td>';
                });
                html += '</tr>';
            });
            html += '</tbody></table>';
        });

        targetEl.innerHTML = html;
        if (!forPrint) {
            document.getElementById('wfStats').textContent =
                state.activeTpl.title + ' · 全文本可编辑 / 自动识别 ' + editableCount + ' 个填报格 / 已绑定 ' + boundCount + ' 个回填字段';
        }
    }

    function renderSidebar() {
        document.getElementById('wfTplCount').textContent = state.templates.length + ' 个';
        var tHtml = state.templates.length ? state.templates.map(function (t) {
            var active = state.activeTpl && state.activeTpl.id === t.id;
            return '<button type="button" class="wf-item-btn' + (active ? ' active' : '') + '" data-tpl-id="' + esc(t.id) + '">' +
                '<span class="wf-item-btn__name">' + esc(t.title) + '</span>' +
                '<span class="wf-item-btn__meta">' + (t.rows_count || 0) + ' 行 × ' + (t.cols_count || 0) + ' 列</span>' +
                '<span class="wf-item-del" data-del-tpl="' + esc(t.id) + '" title="删除模板">×</span>' +
                '</button>';
        }).join('') : '<div class="text-muted small px-2 py-3">暂无模板，请点击右上角上传任意 .docx</div>';
        document.getElementById('wfTplList').innerHTML = tHtml;

        document.getElementById('wfRecCount').textContent = state.records.length + ' 条';
        var rHtml = state.records.length ? state.records.map(function (r) {
            var active = state.activeRecId === r.id;
            return '<button type="button" class="wf-item-btn' + (active ? ' active' : '') + '" data-rec-id="' + esc(r.id) + '">' +
                '<span class="wf-item-btn__name">' + esc(r.title) + '</span>' +
                '<span class="wf-item-btn__meta">' + esc(r.updated_at || '') + '</span>' +
                '<span class="wf-item-del" data-del-rec="' + esc(r.id) + '" title="删除记录">×</span>' +
                '</button>';
        }).join('') : '<div class="text-muted small px-2 py-2">暂无已保存记录</div>';
        document.getElementById('wfRecList').innerHTML = rHtml;
    }

    function renderAll() {
        renderSidebar();
        var isFmt = state.mode === 'format';
        var isBind = state.mode === 'bind';
        document.querySelectorAll('#wfModeChips .wf-chip').forEach(function (c) {
            c.classList.toggle('active', c.getAttribute('data-mode') === state.mode);
        });
        document.getElementById('wfFillToolbar').classList.toggle('d-none', isFmt || isBind);
        document.getElementById('wfFormatBar').classList.toggle('d-none', !isFmt);
        document.getElementById('wfBindBar').classList.toggle('d-none', !isBind);
        renderPaper(document.getElementById('wfCanvas'), false);
        updatePaperHint();
    }

    function loadTemplates(selectId) {
        return $.getJSON(BASE + '/api/templates').done(function (res) {
            state.templates = (res && res.data) || [];
            var targetId = selectId || (state.activeTpl ? state.activeTpl.id : (state.templates[0] ? state.templates[0].id : null));
            if (targetId) {
                openTemplate(targetId);
            } else {
                state.activeTpl = null;
                state.records = [];
                renderAll();
            }
        }).fail(fail);
    }

    function openTemplate(tplId, keepValues) {
        return $.getJSON(BASE + '/api/template/' + encodeURIComponent(tplId)).done(function (res) {
            if (!res || !res.success) { toast((res && res.msg) || '加载失败', 'warning'); return; }
            state.activeTpl = res.data;
            if (!keepValues) {
                state.activeRecId = null;
                state.values = {};
            }
            state.selectedCellId = null;
            state.selectedParaIdx = null;
            loadRecords(tplId);
            renderAll();
        }).fail(fail);
    }

    function loadRecords(tplId) {
        $.getJSON(BASE + '/api/records', tplId ? { template_id: tplId } : {}).done(function (res) {
            state.records = (res && res.data) || [];
            renderSidebar();
        });
    }

    function searchResidents(q) {
        $.getJSON(BASE + '/api/residents/search', { q: q || '' }).done(function (res) {
            var items = (res && res.data) || [];
            var sel = document.getElementById('wfResidentSelect');
            sel.innerHTML = items.length
                ? items.map(function (r) { return '<option value="' + r.id + '">' + esc(r.label) + '</option>'; }).join('')
                : '<option value="">（未找到匹配居民）</option>';
        });
    }

    function saveTemplateSchema(silentMsg) {
        if (!state.activeTpl) return;
        $.ajax({
            url: BASE + '/api/template/' + encodeURIComponent(state.activeTpl.id) + '/schema',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                title: state.activeTpl.title,
                paper_orient: state.activeTpl.paper_orient || 'portrait',
                paper_margin: state.activeTpl.paper_margin || 'normal',
                blocks: state.activeTpl.blocks || []
            })
        }).done(function (res) {
            if (!res.success) { toast(res.msg || '保存失败', 'warning'); return; }
            toast(silentMsg || res.msg || '模板版式已保存');
        }).fail(fail);
    }

    function exportDocx() {
        if (!state.activeTpl) { toast('请先选择表单模板', 'warning'); return; }
        var exportTitle = state.activeTpl.title || '表单导出';
        window.fetch(BASE + '/api/export-docx', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken()
            },
            body: JSON.stringify({
                template_id: state.activeTpl.id,
                title: exportTitle,
                schema: state.activeTpl,
                values: state.values
            })
        }).then(function (resp) {
            if (!resp.ok) throw new Error('导出失败（HTTP ' + resp.status + '）');
            return resp.blob();
        }).then(function (blob) {
            var url = window.URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = exportTitle + '.docx';
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            toast('已导出原版 .docx 表单');
        }).catch(function (err) {
            toast(err.message || '导出失败', 'danger');
        });
    }

    // ========== 顶部与侧栏事件 ==========
    document.getElementById('wfBtnUploadDocx').addEventListener('click', function () {
        document.getElementById('wfDocxInput').click();
    });
    document.getElementById('wfDocxInput').addEventListener('change', function () {
        var f = this.files[0];
        this.value = '';
        if (!f) return;
        var fd = new FormData();
        fd.append('file', f);
        $.ajax({
            url: BASE + '/api/template/upload',
            method: 'POST',
            data: fd,
            processData: false,
            contentType: false
        }).done(function (res) {
            if (!res.success) { toast(res.msg || '解析失败', 'warning'); return; }
            toast(res.msg || '模板上传成功');
            loadTemplates(res.data.id);
        }).fail(fail);
    });

    document.getElementById('wfTplList').addEventListener('click', function (e) {
        var delBtn = e.target.closest('[data-del-tpl]');
        if (delBtn) {
            e.stopPropagation();
            var tid = delBtn.getAttribute('data-del-tpl');
            if (!window.confirm('确定删除该审批表模板？')) return;
            $.post(BASE + '/api/template/' + encodeURIComponent(tid) + '/delete').done(function (res) {
                toast((res && res.msg) || '已删除');
                if (state.activeTpl && state.activeTpl.id === tid) state.activeTpl = null;
                loadTemplates();
            }).fail(fail);
            return;
        }
        var btn = e.target.closest('[data-tpl-id]');
        if (!btn) return;
        openTemplate(btn.getAttribute('data-tpl-id'));
    });

    document.getElementById('wfRecList').addEventListener('click', function (e) {
        var delBtn = e.target.closest('[data-del-rec]');
        if (delBtn) {
            e.stopPropagation();
            var rid = delBtn.getAttribute('data-del-rec');
            if (!window.confirm('确定删除该填报记录？')) return;
            $.post(BASE + '/api/record/' + encodeURIComponent(rid) + '/delete').done(function () {
                if (state.activeRecId === rid) state.activeRecId = null;
                loadRecords(state.activeTpl ? state.activeTpl.id : null);
                toast('记录已删除');
            }).fail(fail);
            return;
        }
        var btn = e.target.closest('[data-rec-id]');
        if (!btn) return;
        $.getJSON(BASE + '/api/record/' + encodeURIComponent(btn.getAttribute('data-rec-id'))).done(function (res) {
            if (!res || !res.success) return;
            var rec = res.data;
            state.activeRecId = rec.id;
            state.values = rec.values || {};
            state.selectedResidentId = rec.resident_id || null;
            if (rec.schema_snapshot) {
                state.activeTpl = rec.schema_snapshot;
                renderAll();
            } else if (rec.template_id) {
                openTemplate(rec.template_id, true);
            }
            toast('已加载记录：' + rec.title);
        }).fail(fail);
    });

    document.getElementById('wfModeChips').addEventListener('click', function (e) {
        var ch = e.target.closest('.wf-chip');
        if (!ch) return;
        state.mode = ch.getAttribute('data-mode');
        renderAll();
    });

    // ========== 填报模式按钮 ==========
    var searchTimer = null;
    document.getElementById('wfResidentQuery').addEventListener('input', function () {
        var q = this.value.trim();
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(function () { searchResidents(q); }, 260);
    });

    document.getElementById('wfBtnAutoFill').addEventListener('click', function () {
        if (!state.activeTpl) { toast('请先选择表单模板', 'warning'); return; }
        var rid = parseInt(document.getElementById('wfResidentSelect').value, 10);
        if (!rid) { toast('请先选择要回填的居民', 'warning'); return; }
        state.selectedResidentId = rid;
        $.getJSON(BASE + '/api/residents/' + rid + '/autofill').done(function (res) {
            if (!res || !res.success) { toast((res && res.msg) || '读取失败', 'warning'); return; }
            var info = res.data || {};
            var filled = 0;
            (state.activeTpl.blocks || []).forEach(function (blk) {
                if (blk.type !== 'table') return;
                (blk.rows || []).forEach(function (row) {
                    row.forEach(function (cell) {
                        if (cell.bind && info[cell.bind]) {
                            var v = info[cell.bind];
                            if (/^\s*级\s*$/.test(cell.text || '')) v = v + ' 级';
                            else if (/^\s*人\s*$/.test(cell.text || '')) v = v + ' 人';
                            state.values[cell.id] = v;
                            filled++;
                        }
                    });
                });
            });
            renderPaper(document.getElementById('wfCanvas'), false);
            toast('已从「' + (info.name || '居民') + '」自动回填 ' + filled + ' 个字段（所有格仍可手动修改）');
        }).fail(fail);
    });

    document.getElementById('wfBtnClearFill').addEventListener('click', function () {
        state.values = {};
        state.activeRecId = null;
        renderPaper(document.getElementById('wfCanvas'), false);
        toast('已恢复默认文本并清空手填值');
    });

    document.getElementById('wfBtnAddRow').addEventListener('click', function () {
        if (!state.activeTpl) return;
        var tbl = (state.activeTpl.blocks || []).filter(function (b) { return b.type === 'table'; })[0];
        if (!tbl || !tbl.rows || !tbl.rows.length) return;
        var last = tbl.rows[tbl.rows.length - 1];
        var totalCols = (tbl.col_pcts || []).length;
        var newRi = tbl.rows.length;
        // 末行可能是「纵向合并续格」被过滤后的残缺行（可见格数 < 列数）：
        // 导出时 add_row 按 tblGrid 建满列，故新增行必须覆盖全列，
        // 否则缺失列没有 meta、填了值也落不进 Word。按起始列 ci 逐列重建：
        var byCi = {};
        last.forEach(function (c, idx) {
            var ci = (c.ci === undefined || c.ci === null) ? idx : c.ci;
            byCi[ci] = c;
        });
        if (!totalCols) totalCols = last.reduce(function (a, c) { return a + (c.colspan || 1); }, 0);
        var cells = [];
        for (var ci = 0; ci < totalCols;) {
            var src = byCi[ci];
            var span = Math.max(1, (src && src.colspan) || 1);
            if (ci + span > totalCols) span = totalCols - ci;
            cells.push({
                id: 't' + tbl.tbl_idx + '_r' + newRi + '_c' + ci,
                ri: newRi, ci: ci, colspan: span, rowspan: 1,
                text: '', align: (src && src.align) || 'center', valign: (src && src.valign) || 'middle',
                font_px: (src && src.font_px) || 12,
                font_family: (src && src.font_family) || "SimSun, '宋体', serif",
                bold: false, editable: true, has_checkbox: false, bind: ''
            });
            ci += span;
        }
        tbl.rows.push(cells);
        renderPaper(document.getElementById('wfCanvas'), false);
        toast('已追加 1 行空白填报行');
    });

    document.getElementById('wfBtnSaveRecord').addEventListener('click', function () {
        if (!state.activeTpl) { toast('请先选择表单模板', 'warning'); return; }
        var defaultName = state.activeTpl.title + ' - ' + new Date().toISOString().slice(0, 10);
        var recTitle = window.prompt('请输入本条填报记录名称：', defaultName);
        if (recTitle === null) return;
        $.ajax({
            url: BASE + '/api/record/save',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                id: state.activeRecId,
                template_id: state.activeTpl.id,
                title: recTitle.trim() || defaultName,
                resident_id: state.selectedResidentId,
                values: state.values,
                schema_snapshot: state.activeTpl
            })
        }).done(function (res) {
            if (!res.success) { toast(res.msg || '保存失败', 'warning'); return; }
            state.activeRecId = res.data.id;
            loadRecords(state.activeTpl.id);
            toast('填报记录已保存');
        }).fail(fail);
    });

    document.getElementById('wfBtnExportDocx').addEventListener('click', exportDocx);

    // ========== 纸张与 A4 单页宽适配 ==========
    document.getElementById('wfPaperOrient').addEventListener('change', function () {
        if (!state.activeTpl) return;
        state.activeTpl.paper_orient = this.value;
        renderPaper(document.getElementById('wfCanvas'), false);
        updatePaperHint();
    });
    document.getElementById('wfPaperMargin').addEventListener('change', function () {
        if (!state.activeTpl) return;
        state.activeTpl.paper_margin = this.value;
        renderPaper(document.getElementById('wfCanvas'), false);
        updatePaperHint();
    });
    document.getElementById('wfBtnFitA4').addEventListener('click', function () {
        if (!state.activeTpl) return;
        (state.activeTpl.blocks || []).forEach(function (b) {
            if (b.type !== 'table' || !b.col_pcts || !b.col_pcts.length) return;
            var sum = b.col_pcts.reduce(function (a, c) { return a + Number(c || 0); }, 0) || 1;
            b.col_pcts = b.col_pcts.map(function (c) { return Math.max(2.5, Math.round((c / sum) * 10000) / 100); });
            var diff = Math.round((100 - b.col_pcts.reduce(function (a, c) { return a + c; }, 0)) * 100) / 100;
            b.col_pcts[b.col_pcts.length - 1] = Math.max(2.5, Math.round((b.col_pcts[b.col_pcts.length - 1] + diff) * 100) / 100);
        });
        renderPaper(document.getElementById('wfCanvas'), false);
        updatePaperHint();
        toast('已按 100% 比例严格适配 A4 单页宽');
    });

    // ========== 版式微调工具栏 ==========
    function onRowHeightInput() {
        var ctx = findCellAndTable(state.selectedCellId);
        if (!ctx) { toast('请先点击表格中的任意单元格', 'warning'); return; }
        var h = Math.max(20, Math.min(240, parseInt(document.getElementById('wfFmtRowHeight').value, 10) || 34));
        if (!ctx.tbl.row_heights) ctx.tbl.row_heights = {};
        ctx.tbl.row_heights[String(ctx.cell.ri)] = h;
        renderPaper(document.getElementById('wfCanvas'), false);
    }
    document.getElementById('wfFmtRowHeight').addEventListener('input', onRowHeightInput);
    document.getElementById('wfFmtRowHeight').addEventListener('change', onRowHeightInput);

    document.getElementById('wfBtnRowAll').addEventListener('click', function () {
        if (!state.activeTpl) return;
        var h = Math.max(20, Math.min(240, parseInt(document.getElementById('wfFmtRowHeight').value, 10) || 34));
        (state.activeTpl.blocks || []).forEach(function (b) {
            if (b.type !== 'table') return;
            b.row_heights = {};
            for (var r = 0; r < (b.rows || []).length; r++) b.row_heights[String(r)] = h;
        });
        renderPaper(document.getElementById('wfCanvas'), false);
        toast('全表行高已统一调整为 ' + h + 'px');
    });

    document.getElementById('wfFmtFontFamily').addEventListener('change', function () {
        var t = activeFmtTarget();
        if (!t) { toast('请先点击一个段落或单元格', 'warning'); return; }
        t.obj.font_family = this.value;
        renderPaper(document.getElementById('wfCanvas'), false);
    });

    function onFontPxInput() {
        var t = activeFmtTarget();
        if (!t) { toast('请先点击一个段落或单元格', 'warning'); return; }
        var v = parseInt(document.getElementById('wfFmtFontPx').value, 10);
        if (!v || v < 10) return;
        t.obj.font_px = Math.max(10, Math.min(32, v));
        renderPaper(document.getElementById('wfCanvas'), false);
    }
    document.getElementById('wfFmtFontPx').addEventListener('input', onFontPxInput);
    document.getElementById('wfFmtFontPx').addEventListener('change', onFontPxInput);

    document.getElementById('wfFmtBoldChk').addEventListener('change', function () {
        var t = activeFmtTarget();
        if (!t) { toast('请先点击一个段落或单元格', 'warning'); return; }
        t.obj.bold = this.checked;
        renderPaper(document.getElementById('wfCanvas'), false);
    });

    document.getElementById('wfFmtHAlign').addEventListener('click', function (e) {
        var b = e.target.closest('.wf-seg-btn');
        if (!b) return;
        var t = activeFmtTarget();
        if (!t) { toast('请先点击一个段落或单元格', 'warning'); return; }
        t.obj.align = b.getAttribute('data-val');
        document.querySelectorAll('#wfFmtHAlign .wf-seg-btn').forEach(function (x) { x.classList.toggle('active', x === b); });
        renderPaper(document.getElementById('wfCanvas'), false);
    });

    document.getElementById('wfFmtVAlign').addEventListener('click', function (e) {
        var b = e.target.closest('.wf-seg-btn');
        if (!b) return;
        var ctx = findCellAndTable(state.selectedCellId);
        if (!ctx) { toast('请先点击表格中的任意单元格', 'warning'); return; }
        ctx.cell.valign = b.getAttribute('data-val');
        document.querySelectorAll('#wfFmtVAlign .wf-seg-btn').forEach(function (x) { x.classList.toggle('active', x === b); });
        renderPaper(document.getElementById('wfCanvas'), false);
    });

    document.getElementById('wfBtnSaveTplSchema').addEventListener('click', function () {
        saveTemplateSchema('模板默认版式与格式已保存');
    });

    // ========== 模板绑定保存 ==========
    document.getElementById('wfBtnSaveBind').addEventListener('click', function () {
        var ctx = findCellAndTable(state.selectedCellId);
        if (!ctx) { toast('请先点击表格中的任意单元格', 'warning'); return; }
        ctx.cell.editable = document.getElementById('wfBindEditableChk').checked;
        ctx.cell.bind = document.getElementById('wfBindFieldSelect').value;
        if (ctx.cell.bind) ctx.cell.editable = true;
        renderPaper(document.getElementById('wfCanvas'), false);
        saveTemplateSchema('格位回填绑定已保存到模板');
    });

    // ========== 画布全文本实时编辑、纯文本粘贴、复选框勾选、选中与拖拽 ==========
    var canvasEl = document.getElementById('wfCanvas');
    canvasEl.addEventListener('input', function (e) {
        var cellEl = e.target.closest ? e.target.closest('[data-edit-cell]') : null;
        if (cellEl) {
            state.values[cellEl.getAttribute('data-edit-cell')] = extractEditableText(cellEl);
            return;
        }
        var paraEl = e.target.closest ? e.target.closest('[data-edit-para]') : null;
        if (paraEl) {
            state.values[paraEl.getAttribute('data-edit-para')] = extractEditableText(paraEl);
        }
    });

    // 粘贴只取纯文本：避免 Word/网页富文本带进 <span style> 污染 A4 版面
    canvasEl.addEventListener('paste', function (e) {
        var editable = e.target.closest ? e.target.closest('[contenteditable="true"]') : null;
        if (!editable) return;
        e.preventDefault();
        var text = (e.clipboardData || window.clipboardData).getData('text/plain');
        if (document.queryCommandSupported && document.queryCommandSupported('insertText')) {
            document.execCommand('insertText', false, text);
        } else {
            var sel = window.getSelection();
            if (sel && sel.rangeCount) {
                sel.deleteFromDocument();
                sel.getRangeAt(0).insertNode(document.createTextNode(text));
                sel.collapseToEnd();
            }
        }
    });

    canvasEl.addEventListener('click', function (e) {
        var cb = e.target.closest('.wf-cb-btn');
        if (cb) {
            // 就地切换（不整页重渲染）：保住正在编辑的单元格光标与未落 state 的输入
            e.preventDefault();
            e.stopPropagation();
            var wasChecked = isCheckedChar(cb.textContent.trim());
            cb.textContent = wasChecked ? UNCHECKED : CHECKED;
            cb.classList.toggle('checked', !wasChecked);
            var cellHost = cb.closest('[data-edit-cell]');
            if (cellHost) {
                state.values[cellHost.getAttribute('data-edit-cell')] = extractEditableText(cellHost);
            }
            return;
        }
        if (state.mode === 'format') {
            if (e.target.closest('.wf-col-resizer') || e.target.closest('.wf-row-resizer')) return;
            var pEl = e.target.closest('.wf-doc-para[data-para-idx]');
            if (pEl) {
                var pi = parseInt(pEl.getAttribute('data-para-idx'), 10);
                var pObj = (state.activeTpl.blocks || [])[pi];
                if (!pObj) return;
                state.selectedParaIdx = pi;
                state.selectedCellId = null;
                document.getElementById('wfFmtLabel').textContent = '已选：公文段落 #' + (pi + 1);
                document.getElementById('wfFmtFontFamily').value = pObj.font_family || "SimSun, '宋体', serif";
                document.getElementById('wfFmtFontPx').value = String(pObj.font_px || Math.max(12, Math.round((pObj.font_pt || 12) * 1.25)));
                document.getElementById('wfFmtBoldChk').checked = !!pObj.bold;
                document.querySelectorAll('#wfFmtHAlign .wf-seg-btn').forEach(function (b) {
                    b.classList.toggle('active', b.getAttribute('data-val') === (pObj.align || 'left'));
                });
                renderPaper(canvasEl, false);
                return;
            }
            var tdF = e.target.closest('td[data-cell-id]');
            if (!tdF) return;
            var idF = tdF.getAttribute('data-cell-id');
            var ctxF = findCellAndTable(idF);
            if (!ctxF) return;
            state.selectedCellId = idF;
            state.selectedParaIdx = null;
            var curRh = (ctxF.tbl.row_heights && (ctxF.tbl.row_heights[String(ctxF.cell.ri)] || ctxF.tbl.row_heights[ctxF.cell.ri]))
                || Math.round(tdF.parentElement.getBoundingClientRect().height) || 34;
            document.getElementById('wfFmtLabel').textContent = '已选：第 ' + (ctxF.cell.ri + 1) + ' 行第 ' + (ctxF.cell.ci + 1) + ' 列';
            document.getElementById('wfFmtFontFamily').value = ctxF.cell.font_family || "SimSun, '宋体', serif";
            document.getElementById('wfFmtFontPx').value = String(ctxF.cell.font_px || 12);
            document.getElementById('wfFmtBoldChk').checked = !!ctxF.cell.bold;
            document.getElementById('wfFmtRowHeight').value = String(curRh);
            document.querySelectorAll('#wfFmtHAlign .wf-seg-btn').forEach(function (b) {
                b.classList.toggle('active', b.getAttribute('data-val') === (ctxF.cell.align || 'center'));
            });
            document.querySelectorAll('#wfFmtVAlign .wf-seg-btn').forEach(function (b) {
                b.classList.toggle('active', b.getAttribute('data-val') === (ctxF.cell.valign || 'middle'));
            });
            renderPaper(canvasEl, false);
            return;
        }
        if (state.mode === 'bind') {
            var tdB = e.target.closest('td[data-cell-id]');
            if (!tdB) return;
            var idB = tdB.getAttribute('data-cell-id');
            var ctxB = findCellAndTable(idB);
            if (!ctxB) return;
            state.selectedCellId = idB;
            document.getElementById('wfBindLabel').textContent = '已选格位：R' + (ctxB.cell.ri + 1) + 'C' + (ctxB.cell.ci + 1);
            document.getElementById('wfBindEditableChk').checked = !!ctxB.cell.editable;
            document.getElementById('wfBindFieldSelect').value = ctxB.cell.bind || '';
            renderPaper(canvasEl, false);
        }
    });

    var colDrag = null;
    var rowDrag = null;
    canvasEl.addEventListener('mousedown', function (e) {
        if (!state.activeTpl) return;
        var ch = e.target.closest('.wf-col-resizer');
        if (ch) {
            e.preventDefault();
            e.stopPropagation();
            var tblIdx = parseInt(ch.getAttribute('data-tbl'), 10);
            var colIdx = parseInt(ch.getAttribute('data-col'), 10);
            var tblObj = (state.activeTpl.blocks || []).filter(function (b) { return b.type === 'table' && b.tbl_idx === tblIdx; })[0];
            var tableEl = ch.closest('table');
            if (!tblObj || !tableEl || colIdx >= tblObj.col_pcts.length - 1) return;
            document.body.classList.add('wf-resizing-col');
            colDrag = {
                tblObj: tblObj,
                tableEl: tableEl,
                colIdx: colIdx,
                startX: e.clientX,
                tableW: tableEl.getBoundingClientRect().width || 700,
                wLeft: Number(tblObj.col_pcts[colIdx]),
                wRight: Number(tblObj.col_pcts[colIdx + 1])
            };
            return;
        }
        var rh = e.target.closest('.wf-row-resizer');
        if (rh) {
            e.preventDefault();
            e.stopPropagation();
            var rTblIdx = parseInt(rh.getAttribute('data-tbl'), 10);
            var ri = parseInt(rh.getAttribute('data-ri'), 10);
            var rTblObj = (state.activeTpl.blocks || []).filter(function (b) { return b.type === 'table' && b.tbl_idx === rTblIdx; })[0];
            var rTableEl = rh.closest('table');
            var trEl = rTableEl ? rTableEl.querySelector('tr[data-ri="' + ri + '"]') : rh.closest('tr');
            if (!rTblObj || !trEl) return;
            if (!rTblObj.row_heights) rTblObj.row_heights = {};
            document.body.classList.add('wf-resizing-row');
            rowDrag = {
                tblObj: rTblObj,
                ri: ri,
                trEl: trEl,
                startY: e.clientY,
                startH: Math.round(trEl.getBoundingClientRect().height) || 34
            };
        }
    });

    document.addEventListener('mousemove', function (e) {
        if (colDrag) {
            var deltaPct = ((e.clientX - colDrag.startX) / colDrag.tableW) * 100;
            var pairSum = colDrag.wLeft + colDrag.wRight;
            var minPct = 2.5;
            var nextLeft = Math.max(minPct, Math.min(pairSum - minPct, colDrag.wLeft + deltaPct));
            var nextRight = pairSum - nextLeft;
            colDrag.tblObj.col_pcts[colDrag.colIdx] = Math.round(nextLeft * 100) / 100;
            colDrag.tblObj.col_pcts[colDrag.colIdx + 1] = Math.round(nextRight * 100) / 100;
            var cols = colDrag.tableEl.querySelectorAll('colgroup col');
            if (cols[colDrag.colIdx]) cols[colDrag.colIdx].style.width = colDrag.tblObj.col_pcts[colDrag.colIdx] + '%';
            if (cols[colDrag.colIdx + 1]) cols[colDrag.colIdx + 1].style.width = colDrag.tblObj.col_pcts[colDrag.colIdx + 1] + '%';
            return;
        }
        if (rowDrag) {
            var nextH = Math.max(20, Math.min(260, Math.round(rowDrag.startH + (e.clientY - rowDrag.startY))));
            rowDrag.tblObj.row_heights[String(rowDrag.ri)] = nextH;
            rowDrag.trEl.style.height = nextH + 'px';
        }
    });

    document.addEventListener('mouseup', function () {
        if (colDrag) {
            document.body.classList.remove('wf-resizing-col');
            colDrag = null;
            updatePaperHint();
        }
        if (rowDrag) {
            document.body.classList.remove('wf-resizing-row');
            document.getElementById('wfFmtRowHeight').value = String(rowDrag.tblObj.row_heights[String(rowDrag.ri)] || 34);
            rowDrag = null;
        }
    });

    // ========== A4 打印预览 ==========
    document.getElementById('wfBtnPrintPreview').addEventListener('click', function () {
        if (!state.activeTpl) { toast('请先选择表单模板', 'warning'); return; }
        renderPaper(document.getElementById('wfPrintPaper'), true);
        document.getElementById('wfPrintOverlay').classList.remove('d-none');
    });
    document.getElementById('wfBtnClosePrint').addEventListener('click', function () {
        document.getElementById('wfPrintOverlay').classList.add('d-none');
    });
    document.getElementById('wfBtnClosePrint2').addEventListener('click', function () {
        document.getElementById('wfPrintOverlay').classList.add('d-none');
    });
    document.getElementById('wfBtnDoPrint').addEventListener('click', function () { window.print(); });

    // Init
    initFieldLabelMap();
    searchResidents('');
    // C-1：支持 /word-form/?tpl_id=xxx 深链（智能传表 Word 分流直达）
    var initTplId = '';
    try { initTplId = new URLSearchParams(window.location.search).get('tpl_id') || ''; } catch (e) {}
    loadTemplates(initTplId || undefined);
})();
