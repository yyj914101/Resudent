/* notice_list.js — 045 补贴公示工作台（模板中心 + 公示栏三栏表格编辑器 + 月度记录）
 * 零构建链（ADR-006/011）、IIFE（024）、事件委托（016）、jQuery + BS5。
 * 表格数据契约与 models/notice.py 一致：
 * {columns:[{key,label,width}], rows:[[cell,...]], layout, header_title, subtitle_tpl, village_name, footer_notes}
 * 2026-11-25 改版：1:1 对齐真实公示栏三栏并排格式、显式年份/月份切换与改期、表尾备注编辑、
 * 沿用上期名单、记录模式增删行、三栏精准合计、y年m月+台账名导出与打印预览。 */
(function () {
    'use strict';

    var BASE = '/notice';
    var state = { type: '', typeName: '', tplList: [], tpl: null, rec: null, mode: 'record', records: [] };
    var saveTimer = null;

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function toast(msg, type) {
        if (typeof showToast === 'function') { showToast(msg, type || 'success'); }
        else { window.alert(msg); }
    }
    function ajax(url, opts) {
        return $.ajax($.extend({ url: url, dataType: 'json' }, opts));
    }
    function fail(xhr) {
        var msg = '请求失败';
        try { msg = xhr.responseJSON ? xhr.responseJSON.msg : msg; } catch (e) {}
        toast(msg || '请求失败', 'danger');
    }

    function pad2(n) {
        var v = parseInt(n, 10) || 1;
        return v < 10 ? ('0' + v) : String(v);
    }
    function fillYearMonthSelects($y, $m, year, month) {
        var nowY = new Date().getFullYear();
        var minY = Math.min(nowY - 3, (year || nowY) - 2);
        var maxY = Math.max(nowY + 3, (year || nowY) + 2);
        $y.empty();
        for (var y = minY; y <= maxY; y++) {
            $y.append($('<option>').val(String(y)).text(y + '年'));
        }
        $m.empty();
        for (var m = 1; m <= 12; m++) {
            $m.append($('<option>').val(pad2(m)).text(m + '月'));
        }
        $y.val(String(year || nowY));
        $m.val(pad2(month || (new Date().getMonth() + 1)));
    }
    function syncRecPeriodHidden() {
        var y = $('#recYear').val();
        var m = $('#recMonth').val();
        if (y && m) { $('#recPeriod').val(y + '-' + m); }
    }
    function parsePeriodStr(period) {
        var m = /^(\d{4})-(\d{2})$/.exec(String(period || ''));
        if (m) return { year: parseInt(m[1], 10), month: parseInt(m[2], 10) };
        var d = new Date();
        return { year: d.getFullYear(), month: d.getMonth() + 1 };
    }
    function notesToText(notes) {
        if (!Array.isArray(notes)) return '';
        return notes.map(function (s) { return String(s == null ? '' : s); })
            .filter(function (s) { return s.trim() !== ''; })
            .join('\n\n');
    }
    function textToNotes(text) {
        var raw = String(text || '').replace(/\r\n/g, '\n').trim();
        if (!raw) return [];
        return raw.split(/\n{2,}/).map(function (s) { return s.trim(); }).filter(Boolean);
    }

    var PAPER_PRINT_W = { a4_portrait: 734, a4_landscape: 1063 };

    function applyPaperClass(mode) {
        var m = (mode === 'a4_landscape' || mode === 'auto') ? mode : 'a4_portrait';
        $('#paperModeSelect').val(m);
        $('#paperSheet').removeClass('is-a4-portrait is-a4-landscape is-auto').addClass('is-' + m.replace('_', '-'));
        updatePaperWidthHint();
    }

    function updatePaperWidthHint() {
        var mode = $('#paperModeSelect').val() || 'a4_portrait';
        var totalW = 0;
        $('#noticeTable colgroup col[data-col-idx]').each(function () {
            var w = parseInt($(this).attr('data-w'), 10);
            if (!isNaN(w)) totalW += w;
        });
        var $hint = $('#paperWidthHint');
        if (!totalW) {
            $hint.removeClass('is-over').text('表头右边缘可拖拽调整列宽 · 打印默认适配 A4 单页宽');
            return;
        }
        var limit = PAPER_PRINT_W[mode];
        if (!limit) {
            $hint.removeClass('is-over').text('当前表格总宽 ' + totalW + 'px（自适应铺满；拖拽表头右边缘可调列宽）');
        } else if (totalW > limit + 12) {
            $hint.addClass('is-over').text('当前列宽总计 ' + totalW + 'px，超出 A4 可打印宽 ' + limit + 'px（可点「适配 A4 宽度」或拖动列宽）');
        } else {
            $hint.removeClass('is-over').text('当前列宽总计 ' + totalW + 'px / A4 可打印宽 ' + limit + 'px · 打印适配单页宽');
        }
    }

    // ── 公示表抬头 & 表尾备注同步 ────────────────────────────────────
    function renderSheetMeta(data, titleText, periodStr) {
        var d = data || {};
        applyPaperClass(d.page_size || 'a4_portrait');
        $('#sheetHeaderTitle').val(d.header_title || '补助资金公开表');
        $('#sheetSubtitle').val(titleText || '');
        $('#sheetFooterNotes').val(notesToText(d.footer_notes));
        var isRec = state.mode === 'record' && !!state.rec;
        $('#sheetPeriodBar').toggleClass('d-none', !isRec);
        if (isRec) {
            var ym = parsePeriodStr(periodStr || state.rec.period);
            fillYearMonthSelects($('#sheetYear'), $('#sheetMonth'), ym.year, ym.month);
        }
    }

    // ── 表格编辑器（模板与记录共用，兼容 9 列三栏并排与自定义列） ──────
    function isTriColumn(cols) {
        if (!cols || cols.length !== 9) return false;
        var norm = function (s) { return String(s || '').replace(/\s+/g, '').replace(/\(/g, '（').replace(/\)/g, '）'); };
        for (var i = 0; i < 3; i++) {
            if (norm(cols[i].label) !== norm(cols[i + 3].label) ||
                norm(cols[i].label) !== norm(cols[i + 6].label)) {
                return false;
            }
        }
        return true;
    }

    function renderTable(data, opts) {
        var cols = (data && data.columns) || [];
        var rows = (data && data.rows) || [];
        var tri = isTriColumn(cols);
        var $tbl = $('#noticeTable');
        $tbl.toggleClass('is-tri-col', tri);

        var html = '<colgroup><col style="width:28px">';
        cols.forEach(function (c, ci) {
            var w = Math.max(36, Math.min(600, parseInt(c.width, 10) || 82));
            html += '<col data-col-idx="' + ci + '" data-w="' + w + '" style="width:' + w + 'px">';
        });
        html += '</colgroup><thead><tr><th class="idx">#</th>';
        cols.forEach(function (c, ci) {
            html += '<th data-col-idx="' + ci + '"><textarea class="cell-in" rows="2" data-col="' + esc(c.key || '') +
                '">' + esc(c.label) + '</textarea><span class="col-resizer" data-col-idx="' + ci + '" title="拖拽调整列宽"></span></th>';
        });
        html += '</tr></thead><tbody>';
        rows.forEach(function (r, ri) {
            html += '<tr><td class="idx">' + (ri + 1) + '</td>';
            cols.forEach(function (_c, ci) {
                html += '<td><input class="cell-in" data-row="' + ri + '" data-col="' + ci +
                    '" value="' + esc(r[ci]) + '"></td>';
            });
            html += '</tr>';
        });
        html += '</tbody>';

        if (opts && opts.sum && cols.length) {
            var sums = cols.map(function () { return 0; });
            rows.forEach(function (r) {
                cols.forEach(function (_c, ci) {
                    var n = parseFloat(String(r[ci] == null ? '' : r[ci]).replace(/[,，\s]/g, ''));
                    if (!isNaN(n)) sums[ci] += n;
                });
            });
            if (tri) {
                // 三栏并排表精准汇总：按非空姓名格统计总户数，累加 3 栏金额（及保障人口数）
                var nameOffset = /姓名/.test(cols[0].label || '') ? 0 : 1;
                var popOffset = /人口/.test(cols[1].label || '') ? 1 : -1;
                var amtOffset = 2;
                var totalHouseholds = 0;
                var totalPop = 0;
                var totalAmt = 0;
                rows.forEach(function (r) {
                    for (var b = 0; b < 3; b++) {
                        var base = b * 3;
                        var nm = String(r[base + nameOffset] == null ? '' : r[base + nameOffset]).trim();
                        if (nm) totalHouseholds++;
                        if (popOffset >= 0) {
                            var p = parseFloat(String(r[base + popOffset] == null ? '' : r[base + popOffset]).replace(/[,，\s]/g, ''));
                            if (!isNaN(p)) totalPop += p;
                        }
                        var a = parseFloat(String(r[base + amtOffset] == null ? '' : r[base + amtOffset]).replace(/[,，\s]/g, ''));
                        if (!isNaN(a)) totalAmt += a;
                    }
                });
                var summaryText = '合计：' + totalHouseholds + ' 户' +
                    (popOffset >= 0 ? (' · 保障人口 ' + totalPop + ' 人') : '') +
                    ' · 金额总计 ' + totalAmt.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' 元';
                html += '<tfoot><tr><td class="idx"></td><td colspan="' + cols.length + '">' + esc(summaryText) + '</td></tr></tfoot>';
            } else {
                html += '<tfoot><tr><td class="idx"></td><td>合计 ' + rows.length + ' 户</td>';
                cols.forEach(function (c, ci) {
                    if (ci === 0) return;
                    var isAmount = /(金额|元)/.test(c.label || '');
                    html += '<td>' + (isAmount ? sums[ci].toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '') + '</td>';
                });
                html += '</tr></tfoot>';
            }
        }
        $tbl.html(html);
        updatePaperWidthHint();
    }

    function readTable() {
        var base = (state.mode === 'record' && state.rec && state.rec.table_data) ||
                   (state.tpl && state.tpl.schema) || {};
        var cols = [];
        $('#noticeTable thead th').each(function () {
            var $in = $(this).find('.cell-in');
            if (!$in.length) return;
            var idx = cols.length;
            var oldCol = (base.columns && base.columns[idx]) || {};
            var $colEl = $('#noticeTable colgroup col[data-col-idx="' + idx + '"]');
            var w = parseInt($colEl.attr('data-w'), 10) || oldCol.width || 82;
            cols.push({
                key: $in.attr('data-col') || ('c' + (idx + 1)),
                label: $.trim($in.val()) || ('列' + (idx + 1)),
                width: Math.max(36, Math.min(600, w))
            });
        });
        var rows = [];
        $('#noticeTable tbody tr').each(function () {
            var row = [];
            $(this).find('input.cell-in').each(function () { row.push($(this).val()); });
            rows.push(row);
        });
        return {
            columns: cols,
            rows: rows,
            layout: isTriColumn(cols) ? 'tri_column' : 'standard',
            page_size: $('#paperModeSelect').val() || 'a4_portrait',
            header_title: $.trim($('#sheetHeaderTitle').val()) || '补助资金公开表',
            subtitle_tpl: base.subtitle_tpl || '',
            village_name: base.village_name || $.trim($('#defaultVillage').text()) || '石垭社区',
            footer_notes: textToNotes($('#sheetFooterNotes').val())
        };
    }

    function renderCurrent() {
        if (state.mode === 'record') {
            if (state.rec) {
                $('#paperBar, #sheetHeader, #sheetFooterWrap').removeClass('d-none');
                renderSheetMeta(state.rec.table_data, state.rec.title, state.rec.period);
                renderTable(state.rec.table_data, { sum: true });
            } else {
                $('#paperBar, #sheetHeader, #sheetFooterWrap').addClass('d-none');
                renderTable({ columns: [], rows: [] });
            }
        } else if (state.tpl) {
            $('#paperBar, #sheetHeader, #sheetFooterWrap').removeClass('d-none');
            renderSheetMeta(state.tpl.schema, state.tpl.title || state.tpl.name, $('#recPeriod').val());
            renderTable(state.tpl.schema);
        } else {
            $('#paperBar, #sheetHeader, #sheetFooterWrap').addClass('d-none');
            renderTable({ columns: [], rows: [] });
        }
        updateChrome();
    }

    // ── 页头胶囊 / 工作台副题 / 脚注行（模式与状态驱动） ─────────────
    function updateChrome() {
        var $pill = $('#statePill');
        var tpl = state.tpl, rec = state.rec;
        if (state.mode === 'template') {
            $pill.attr('class', 'notice-state-pill').text('模板编辑中 · 未发布');
            if (tpl) {
                $('#wbSub').text('模板：' + (tpl.name || '') + ' · ' +
                    ((tpl.schema && tpl.schema.columns && tpl.schema.columns.length) || 0) + ' 列');
            } else {
                $('#wbSub').text(state.typeName ? '该类型暂无模板，点击「新建公示表」创建' : '请选择左侧公示类型');
            }
            $('#footHint').text('大标题、副标题、三栏表头与表尾备注均可直接编辑，点击「保存模板」生效');
            $('#btnPubRec, #btnWithdraw').addClass('d-none');
            $('#btnPreviewTpl').toggleClass('d-none', !tpl);
        } else {
            if (rec) {
                var published = rec.status === 'published';
                $pill.attr('class', 'notice-state-pill' + (published ? ' is-published' : ''))
                    .text((rec.period_cn || rec.period || '') + ' · ' + (published ? '已发布' : '未发布'));
                $('#wbSub').text((rec.title || state.typeName) + ' · 导出：' + (rec.export_filename || ''));
                $('#btnExportRec').attr('title', '导出文件：' + (rec.export_filename || ''));
                $('#footHint').text('表格、抬头与表尾备注失焦自动保存 · 支持上方随时更改年份/月份');
                $('#btnPubRec').toggleClass('d-none', published);
                $('#btnWithdraw').toggleClass('d-none', !published);
            } else {
                $pill.attr('class', 'notice-state-pill').text('未生成公示记录');
                $('#wbSub').text(state.typeName ? state.typeName + ' · 暂无记录' : '请选择左侧公示类型');
                $('#footHint').text('选择上方年份与月份，点击「生成该月公示」或「沿用上期名单」开始编辑');
                $('#btnPubRec, #btnWithdraw').addClass('d-none');
            }
            $('#btnPreviewTpl').addClass('d-none');
        }
        $('#btnHeadNew').text(state.mode === 'template' ? '新建公示表' : '新建模板');
    }

    // ── 模式页签（默认记录模式） ────────────────────────────────────
    function setMode(mode) {
        state.mode = mode;
        $('#noticeModeTabs .sc-chip').removeClass('active')
            .filter('[data-mode="' + mode + '"]').addClass('active');
        var tpl = mode === 'template';
        $('#tplToolbar').toggleClass('d-none', !tpl);
        $('#recToolbar').toggleClass('d-none', tpl);
        renderCurrent();
    }
    $(document).on('click', '#noticeModeTabs .sc-chip', function () {
        setMode($(this).data('mode'));
    });

    // ── 自定义分类与新建公示表（内联面板，零弹窗） ────────────────────
    var createMode = 'table';

    function collectGroupLabelsFromDom() {
        var labels = [];
        $('#siderGroupsWrap .notice-type-head').each(function () {
            var txt = $.trim($(this).text());
            if (txt && labels.indexOf(txt) < 0) labels.push(txt);
        });
        return labels;
    }

    function populateGroupSelect(selectedGroup) {
        var labels = collectGroupLabelsFromDom();
        var $sel = $('#createGroupSelect').empty();
        labels.forEach(function (g) {
            $sel.append($('<option>').val(g).text(g));
        });
        $sel.append($('<option>').val('__new__').text('＋ 新建其他分类…'));
        if (selectedGroup && labels.indexOf(selectedGroup) >= 0) {
            $sel.val(selectedGroup);
        }
    }

    function setCreatePanelMode(mode, presetGroup) {
        createMode = mode === 'group' ? 'group' : 'table';
        $('#createModeTable').toggleClass('active', createMode === 'table');
        $('#createModeGroup').toggleClass('active', createMode === 'group');
        populateGroupSelect(presetGroup);
        if (createMode === 'group') {
            $('#createGroupSelectWrap').addClass('d-none');
            $('#createNewGroupWrap').removeClass('d-none');
            $('#createTableNameLabel').text('同时新建第一张公示表（选填）');
            $('#createNewGroupInput').val('').trigger('focus');
        } else {
            $('#createGroupSelectWrap').removeClass('d-none');
            var isNew = $('#createGroupSelect').val() === '__new__';
            $('#createNewGroupWrap').toggleClass('d-none', !isNew);
            $('#createTableNameLabel').text('公示表名称');
            $('#createTableNameInput').val('').trigger('focus');
        }
    }

    function openCreatePanel(mode, presetGroup) {
        $('#createCatalogPanel').removeClass('d-none');
        setCreatePanelMode(mode || 'table', presetGroup);
    }

    function renderSiderCatalog(groups, typeCounts) {
        var counts = typeCounts || {};
        var html = '';
        var total = 0;
        (groups || []).forEach(function (g) {
            html += '<div class="notice-type-head-row">' +
                '<div class="notice-type-head">' + esc(g.label) + '</div>' +
                '<button class="notice-group-add-btn" type="button" data-group="' + esc(g.label) +
                '" title="在「' + esc(g.label) + '」下新建公示表">＋ 新建表</button></div>';
            (g.types || []).forEach(function (t) {
                total++;
                var active = state.type === t.key ? ' active' : '';
                html += '<button class="notice-type-btn' + active + '" type="button" data-type="' +
                    esc(t.key) + '" data-type-name="' + esc(t.name) + '">' +
                    '<span>' + esc(t.name) + '</span>' +
                    '<span class="notice-type-count">' + (counts[t.key] || 0) + ' 条</span></button>';
            });
        });
        $('#siderGroupsWrap').html(html);
        $('#totalTypes').text(String(total));
        $('#siderCount').text('共 ' + total + ' 个模板');
    }

    $('#btnOpenCreatePanel').on('click', function () {
        if ($('#createCatalogPanel').hasClass('d-none')) {
            openCreatePanel('table');
        } else {
            $('#createCatalogPanel').addClass('d-none');
        }
    });
    $(document).on('click', '.notice-group-add-btn', function () {
        openCreatePanel('table', $(this).attr('data-group'));
    });
    $('#createModeTable').on('click', function () { setCreatePanelMode('table'); });
    $('#createModeGroup').on('click', function () { setCreatePanelMode('group'); });
    $('#createGroupSelect').on('change', function () {
        var isNew = $(this).val() === '__new__';
        $('#createNewGroupWrap').toggleClass('d-none', !isNew);
        if (isNew) $('#createNewGroupInput').trigger('focus');
    });
    $('#btnCancelCreate').on('click', function () {
        $('#createCatalogPanel').addClass('d-none');
    });
    $('#btnSubmitCreate').on('click', function () {
        var tableName = $.trim($('#createTableNameInput').val());
        var preset = $('#createPresetSelect').val() || 'common';
        if (createMode === 'group') {
            var newGroup = $.trim($('#createNewGroupInput').val());
            if (!newGroup) { toast('请输入新分类名称（如：资产类）', 'warning'); return; }
            ajax(BASE + '/api/group/create', {
                type: 'POST', contentType: 'application/json',
                data: JSON.stringify({ group_name: newGroup, table_name: tableName, preset: preset })
            }).done(function (res) {
                if (!res.success) { toast(res.msg || '创建失败', 'warning'); return; }
                $('#createCatalogPanel').addClass('d-none');
                var d = res.data || {};
                if (d.groups) renderSiderCatalog(d.groups, d.type_counts);
                toast(res.msg || '分类已创建');
                if (d.type && d.type.key) {
                    $('#siderGroupsWrap .notice-type-btn[data-type="' + d.type.key + '"]').trigger('click');
                }
            }).fail(fail);
            return;
        }
        var selGroup = $('#createGroupSelect').val();
        var groupName = selGroup === '__new__' ? $.trim($('#createNewGroupInput').val()) : selGroup;
        if (!groupName) { toast('请选择或输入所属分类', 'warning'); return; }
        if (!tableName) { toast('请输入公示表名称', 'warning'); return; }
        ajax(BASE + '/api/type/create', {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ group_name: groupName, table_name: tableName, preset: preset })
        }).done(function (res) {
            if (!res.success) { toast(res.msg || '创建失败', 'warning'); return; }
            $('#createCatalogPanel').addClass('d-none');
            var d = res.data || {};
            if (d.groups) renderSiderCatalog(d.groups, d.type_counts);
            toast(res.msg || '公示表已创建');
            if (d.type && d.type.key) {
                $('#siderGroupsWrap .notice-type-btn[data-type="' + d.type.key + '"]').trigger('click');
            }
        }).fail(fail);
    });

    // ── 单元格点击即聚焦 & 类 Excel 键盘上下换行录入 ─────────────────
    $(document).on('click', '#noticeTable tbody td, #noticeTable thead th', function (e) {
        if ($(e.target).hasClass('col-resizer') || $(this).hasClass('idx')) return;
        var $in = $(this).find('.cell-in').first();
        if ($in.length && document.activeElement !== $in[0]) {
            $in.trigger('focus');
        }
    });
    $(document).on('keydown', '#noticeTable tbody input.cell-in', function (e) {
        if (e.key !== 'Enter' && e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
        e.preventDefault();
        var rowIdx = parseInt($(this).attr('data-row'), 10);
        var colIdx = parseInt($(this).attr('data-col'), 10);
        var nextRow = e.key === 'ArrowUp' ? (rowIdx - 1) : (rowIdx + 1);
        var $target = $('#noticeTable tbody input.cell-in[data-row="' + nextRow + '"][data-col="' + colIdx + '"]');
        if ($target.length) {
            $target.trigger('focus');
        } else if (e.key === 'Enter') {
            addRowAction();
            $('#noticeTable tbody input.cell-in[data-row="' + nextRow + '"][data-col="' + colIdx + '"]').trigger('focus');
        }
    });

    // ── 左侧类型 ────────────────────────────────────────────────────
    $(document).on('click', '.notice-type-btn', function () {
        $('.notice-type-btn').removeClass('active');
        $(this).addClass('active');
        state.type = $(this).data('type');
        state.typeName = $(this).data('type-name');
        state.rec = null;
        $('#wbBody').removeClass('d-none');
        setMode('record');
        loadTemplates();
    });

    // ── 模板 ────────────────────────────────────────────────────────
    function loadTemplates(cb) {
        ajax(BASE + '/api/templates', { data: { notice_type: state.type, include_inactive: 1 } })
            .done(function (res) {
                state.tplList = (res && res.data) || [];
                if (state.tplList.length) {
                    openTemplate(state.tplList[0].id, cb);
                } else {
                    state.tpl = null;
                    renderCurrent();
                    renderRecList();
                }
            })
            .fail(fail);
    }

    function openTemplate(id, cb) {
        ajax(BASE + '/api/template/' + id).done(function (res) {
            state.tpl = res.data;
            state.rec = null;
            renderCurrent();
            renderRecList();
            if (cb) cb();
        }).fail(fail);
    }

    $('#btnHeadNew').on('click', function () {
        openCreatePanel('table');
    });

    $('#btnSaveTpl').on('click', function () {
        if (!state.tpl) return;
        ajax(BASE + '/api/template/update/' + state.tpl.id, {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({
                name: state.tpl.name,
                title: $.trim($('#sheetSubtitle').val()) || state.tpl.title || state.tpl.name,
                schema: readTable()
            })
        }).done(function () { toast('模板已保存'); loadTemplates(); }).fail(fail);
    });

    $('#btnResetTpl').on('click', function () {
        if (!state.tpl || !window.confirm('确定恢复为标准三栏公示表格式？当前未保存的模板表头修改将被重置。')) return;
        ajax(BASE + '/api/template/reset/' + state.tpl.id, { type: 'POST' })
            .done(function (res) {
                state.tpl = res.data.item;
                renderCurrent();
                toast('已恢复为标准公示表格式');
            }).fail(fail);
    });

    $('#btnToggleTpl').on('click', function () {
        if (!state.tpl) return;
        ajax(BASE + '/api/template/status/' + state.tpl.id, { type: 'POST' })
            .done(function () { toast('状态已更新'); loadTemplates(); }).fail(fail);
    });

    $('#btnDelTpl').on('click', function () {
        if (!state.tpl || !window.confirm('确定删除该模板？')) return;
        ajax(BASE + '/api/template/delete/' + state.tpl.id, { type: 'POST' })
            .done(function () { toast('模板已删除'); loadTemplates(); }).fail(fail);
    });

    // ── 表格编辑（模板模式 & 记录模式均支持增删行） ──────────────────
    function addRowAction() {
        var data = readTable();
        if (!data.columns.length) return;
        data.rows.push(new Array(data.columns.length).fill(''));
        renderTable(data, { sum: state.mode === 'record' });
        if (state.mode === 'record') scheduleAutosave();
    }
    function delRowAction() {
        var data = readTable();
        if (data.rows.length > 1) data.rows.pop();
        renderTable(data, { sum: state.mode === 'record' });
        if (state.mode === 'record') scheduleAutosave();
    }
    $('#btnAddRow, #btnRecAddRow').on('click', addRowAction);
    $('#btnDelRow, #btnRecDelRow').on('click', delRowAction);

    $('#btnAddCol').on('click', function () {
        var data = readTable();
        data.columns.push({ key: 'c' + (data.columns.length + 1),
                            label: '列' + (data.columns.length + 1), width: 110 });
        data.rows.forEach(function (r) { r.push(''); });
        renderTable(data, { sum: state.mode === 'record' });
    });
    $('#btnDelCol').on('click', function () {
        var data = readTable();
        if (data.columns.length <= 1) return;
        data.columns.pop();
        data.rows.forEach(function (r) { r.pop(); });
        renderTable(data, { sum: state.mode === 'record' });
    });

    // ── 失焦自动保存（仅记录模式且已打开记录） ──────────────────────
    function scheduleAutosave() {
        if (state.mode !== 'record' || !state.rec) return;
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(function () {
            var payload = {
                title: $.trim($('#sheetSubtitle').val()) || state.rec.title,
                table_data: readTable()
            };
            ajax(BASE + '/api/record/update/' + state.rec.id, {
                type: 'POST', contentType: 'application/json',
                data: JSON.stringify(payload)
            }).done(function (res) {
                state.rec = res.data.item;
                renderTable(state.rec.table_data, { sum: true });
                updateChrome();
                var d = new Date();
                var t = (d.getHours() < 10 ? '0' : '') + d.getHours() + ':' +
                        (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
                $('#footHint').text('已自动保存 ' + t + ' · 导出文件名：' + (state.rec.export_filename || ''));
            }).fail(fail);
        }, 600);
    }
    $(document).on('change', '#noticeTable .cell-in, #sheetHeaderTitle, #sheetSubtitle, #sheetFooterNotes', scheduleAutosave);

    $('#paperModeSelect').on('change', function () {
        applyPaperClass($(this).val());
        if (state.mode === 'record') scheduleAutosave();
    });

    $('#btnFitA4').on('click', function () {
        var mode = $('#paperModeSelect').val() || 'a4_portrait';
        var target = PAPER_PRINT_W[mode] || PAPER_PRINT_W.a4_portrait;
        var data = readTable();
        if (!data.columns.length) return;
        var sum = data.columns.reduce(function (acc, c) { return acc + (parseInt(c.width, 10) || 80); }, 0) || 1;
        var scale = target / sum;
        data.columns.forEach(function (c) {
            c.width = Math.max(36, Math.round((parseInt(c.width, 10) || 80) * scale));
        });
        renderTable(data, { sum: state.mode === 'record' });
        if (state.mode === 'record') scheduleAutosave();
        toast('已按比例适配 A4 单页宽度');
    });

    // 列宽拖拽调整
    var resizeCtx = null;
    $(document).on('mousedown', '#noticeTable .col-resizer', function (e) {
        e.preventDefault();
        var ci = parseInt($(this).attr('data-col-idx'), 10);
        var $col = $('#noticeTable colgroup col[data-col-idx="' + ci + '"]');
        var startW = parseInt($col.attr('data-w'), 10) || $(this).closest('th').outerWidth() || 80;
        resizeCtx = { ci: ci, $col: $col, startX: e.pageX, startW: startW };
    });
    $(document).on('mousemove', function (e) {
        if (!resizeCtx) return;
        var nextW = Math.max(36, Math.min(600, Math.round(resizeCtx.startW + (e.pageX - resizeCtx.startX))));
        resizeCtx.$col.attr('data-w', nextW).css('width', nextW + 'px');
        updatePaperWidthHint();
    });
    $(document).on('mouseup', function () {
        if (!resizeCtx) return;
        resizeCtx = null;
        if (state.mode === 'record') scheduleAutosave();
    });

    // ── 更改当前记录的年份/月份 ─────────────────────────────────────
    $('#btnApplyPeriod').on('click', function () {
        if (!state.rec) return;
        var y = $('#sheetYear').val();
        var m = $('#sheetMonth').val();
        if (!y || !m) return;
        var newPeriod = y + '-' + m;
        ajax(BASE + '/api/record/update/' + state.rec.id, {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({
                period: newPeriod,
                title: $.trim($('#sheetSubtitle').val()) || state.rec.title,
                table_data: readTable()
            })
        }).done(function (res) {
            if (!res.success) { toast(res.msg || '更改年月失败', 'warning'); return; }
            state.rec = res.data.item;
            renderCurrent();
            renderRecList();
            toast('已更新为 ' + (state.rec.period_cn || newPeriod));
        }).fail(fail);
    });

    // ── 记录列表与生成 ──────────────────────────────────────────────
    function renderRecList() {
        if (!state.type) return;
        ajax(BASE + '/api/records', { data: { notice_type: state.type } }).done(function (res) {
            state.records = (res && res.data) || [];
            var $pick = $('#recPick').empty();
            state.records.forEach(function (r) {
                $pick.append($('<option>').val(r.id)
                    .text((r.period_cn || r.period) + ' · ' + (r.status === 'published' ? '已发布' : '未发布')));
            });
            $pick.toggleClass('d-none', state.records.length <= 1);
            if (state.records.length) {
                if (!state.rec || !state.records.some(function (r) { return r.id === state.rec.id; })) {
                    openRecord(state.records[0].id);
                } else {
                    $pick.val(String(state.rec.id));
                }
            }
        }).fail(fail);
    }

    $('#recPick').on('change', function () {
        if (this.value) openRecord(parseInt(this.value, 10));
    });
    $('#recYear, #recMonth').on('change', syncRecPeriodHidden);

    function openRecord(id) {
        ajax(BASE + '/api/record/' + id).done(function (res) {
            state.rec = res.data;
            setMode('record');
            renderRecList();
        }).fail(fail);
    }

    function generateForSelectedPeriod(copyLatest) {
        if (!state.tpl) { toast('请先选择模板（可在模板模式编辑）', 'warning'); return; }
        syncRecPeriodHidden();
        var period = $('#recPeriod').val();
        if (!period) { toast('请选择年份和月份', 'warning'); return; }
        ajax(BASE + '/api/record/generate', {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({
                template_id: state.tpl.id,
                period: period,
                copy_latest: !!copyLatest
            })
        }).done(function (res) {
            var item = res.data && (res.data.item || res.data);
            toast(res.msg || '公示已生成', res.success ? 'success' : 'warning');
            if (item && item.id) { openRecord(item.id); } else { renderRecList(); }
        }).fail(fail);
    }

    $('#btnGenerate').on('click', function () { generateForSelectedPeriod(false); });
    $('#btnCopyPrev').on('click', function () { generateForSelectedPeriod(true); });

    $('#btnPubRec').on('click', function () {
        if (!state.rec) return;
        ajax(BASE + '/api/record/status/' + state.rec.id, {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ status: 'published' })
        }).done(function (res) {
            state.rec = res.data.item;
            toast('已发布，居民端同步可见');
            renderRecList();
        }).fail(fail);
    });

    $('#btnWithdraw').on('click', function () {
        if (!state.rec) return;
        ajax(BASE + '/api/record/status/' + state.rec.id, {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ status: 'draft' })
        }).done(function (res) {
            state.rec = res.data.item;
            toast('已撤回为草稿');
            renderRecList();
        }).fail(fail);
    });

    $('#btnDelRec').on('click', function () {
        if (!state.rec || !window.confirm('确定删除该公示记录？')) return;
        ajax(BASE + '/api/record/delete/' + state.rec.id, { type: 'POST' }).done(function () {
            toast('记录已删除');
            state.rec = null;
            setMode('record');
            renderRecList();
        }).fail(fail);
    });

    $('#btnExportRec').on('click', function () {
        if (!state.rec) { toast('请先打开一条公示记录', 'warning'); return; }
        var fmt = $('#exportFormat').val() || 'xlsx';
        window.location.href = BASE + '/api/record/export/' + state.rec.id + '?format=' + encodeURIComponent(fmt);
    });

    // ── 打印/预览：1:1 对齐真实公示栏 A4 纵向格式 ───────────────────
    function openPrintWindow(autoprint) {
        var data = readTable();
        var headerTitle = data.header_title || '补助资金公开表';
        var subtitle = $.trim($('#sheetSubtitle').val()) ||
            (state.rec && state.rec.title) || (state.tpl && state.tpl.title) || state.typeName;
        var notes = data.footer_notes || [];

        var totalW = data.columns.reduce(function (acc, c) { return acc + (parseInt(c.width, 10) || 80); }, 0) || 1;
        var colgroup = '<colgroup>' + data.columns.map(function (c) {
            var pct = (((parseInt(c.width, 10) || 80) / totalW) * 100).toFixed(2);
            return '<col style="width:' + pct + '%">';
        }).join('') + '</colgroup>';
        var html = '<div class="pub-h1">' + esc(headerTitle) + '</div>' +
            '<div class="pub-h2">' + esc(subtitle) + '</div>' +
            '<table>' + colgroup + '<thead><tr>';
        data.columns.forEach(function (c) {
            html += '<th>' + esc(c.label).replace(/\n/g, '<br>') + '</th>';
        });
        html += '</tr></thead><tbody>';
        data.rows.forEach(function (r) {
            html += '<tr>';
            r.forEach(function (c) { html += '<td>' + esc(c) + '</td>'; });
            html += '</tr>';
        });
        notes.forEach(function (n) {
            if (String(n || '').trim()) {
                html += '<tr><td class="pub-note" colspan="' + data.columns.length + '">' +
                    esc(n).replace(/\n/g, '<br>') + '</td></tr>';
            }
        });
        html += '</tbody></table>';

        var w = window.open('', '_blank', 'width=960,height=760');
        if (!w) { toast('浏览器拦截了窗口，请允许弹窗', 'warning'); return; }
        w.document.write('<!doctype html><html><head><meta charset="utf-8"><title>' +
            esc(subtitle || '补贴公示') + '</title><style>' +
            '@page{size:A4 ' + (data.page_size === 'a4_landscape' ? 'landscape' : 'portrait') + ';margin:10mm}' +
            'body{font-family:"SimSun","宋体","Microsoft YaHei",serif;color:#000;padding:8px}' +
            '.pub-h1{font-family:"Microsoft YaHei",sans-serif;font-size:24px;font-weight:700;text-align:center;margin:0 0 6px}' +
            '.pub-h2{font-size:16px;font-weight:700;text-align:center;margin:0 0 8px}' +
            'table{width:100%;border-collapse:collapse;table-layout:fixed}' +
            'th,td{border:1px solid #000;padding:4px 3px;font-size:12px;text-align:center;word-break:break-all}' +
            'th{font-weight:700}td.pub-note{text-align:left;padding:6px 8px;line-height:1.5}' +
            '</style></head><body>' + html + '</body></html>');
        w.document.close();
        w.focus();
        if (autoprint) setTimeout(function () { w.print(); }, 200);
    }
    $('#btnPrintRec').on('click', function () { openPrintWindow(true); });
    $('#btnPreviewTpl').on('click', function () { openPrintWindow(false); });

    // ── 本地上传（Excel/CSV → 自动跳过标题行 + 表头匹配 → 覆盖当前表格） ──
    $('#btnUpload').on('click', function () {
        if (!state.rec) { toast('请先生成/打开一条公示记录，再上传数据', 'warning'); return; }
        $('#fileUpload').val('').trigger('click');
    });
    $('#fileUpload').on('change', function () {
        var file = this.files[0];
        if (!file) return;
        var data = readTable();
        var fd = new FormData();
        fd.append('file', file);
        fd.append('columns', JSON.stringify(data.columns));
        $('#btnUpload').prop('disabled', true).text('解析中…');
        $.ajax({
            url: BASE + '/api/record/import', type: 'POST', data: fd,
            processData: false, contentType: false, dataType: 'json'
        }).done(function (res) {
            var d = res.data || {};
            if (d.rows && d.rows.length) {
                data.rows = d.rows;
                if (d.footer_notes && d.footer_notes.length) {
                    data.footer_notes = d.footer_notes;
                    $('#sheetFooterNotes').val(notesToText(d.footer_notes));
                }
                renderTable(data, { sum: true });
                scheduleAutosave();
                toast('已填充 ' + d.rows.length + ' 行' +
                    (d.unmatched && d.unmatched.length ? '；未匹配列已忽略：' + d.unmatched.join('、') : ''));
            } else {
                toast('未解析到可填充的数据行', 'warning');
            }
        }).fail(fail).always(function () {
            $('#btnUpload').prop('disabled', false).text('上传 Excel');
        });
        this.value = '';
    });

    // ── 初始化 ──────────────────────────────────────────────────────
    (function initMonth() {
        var d = new Date();
        fillYearMonthSelects($('#recYear'), $('#recMonth'), d.getFullYear(), d.getMonth() + 1);
        syncRecPeriodHidden();
    })();
    (function initSiderCount() {
        var n = parseInt($('#totalTypes').text(), 10);
        if (!isNaN(n)) $('#siderCount').text('共 ' + n + ' 个模板');
    })();
    (function initDefaultType() {
        var $first = $('.notice-type-btn').first();
        if ($first.length) $first.trigger('click');
    })();
})();
