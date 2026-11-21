/* notice_list.js — 045 补贴公示工作台（模板中心 + 表格编辑器 + 月度记录）
 * 零构建链（ADR-006/011）、IIFE（024）、事件委托（016）、jQuery + BS5。
 * 表格数据契约与 models/notice.py 一致：{columns:[{key,label,width}], rows:[[cell,...]]}
 * 2026-11-02 样图定稿改版：页头状态胶囊、工具行重排（记录/模板两态）、合计行、
 * 失焦自动保存、发布/撤回分离、历史记录下拉、预览公示表。 */
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

    // ── 表格编辑器（模板与记录共用） ────────────────────────────────
    // opts.sum=true（记录模式）：tfoot 合计行——行数 + 列名含「金额」的列求和
    function renderTable(data, opts) {
        var cols = (data && data.columns) || [];
        var rows = (data && data.rows) || [];
        var html = '<thead><tr><th class="idx">#</th>';
        cols.forEach(function (c) {
            html += '<th><input class="cell-in" data-col="' + esc(c.key || '') +
                '" value="' + esc(c.label) + '"></th>';
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
        if (opts && opts.sum) {
            var sums = cols.map(function () { return 0; });
            rows.forEach(function (r) {
                cols.forEach(function (c, ci) {
                    var n = parseFloat(String(r[ci] == null ? '' : r[ci]).replace(/[,，\s]/g, ''));
                    if (!isNaN(n)) sums[ci] += n;
                });
            });
            html += '<tfoot><tr><td class="idx"></td><td>合计 ' + rows.length + ' 户</td>';
            cols.forEach(function (c, ci) {
                if (ci === 0) return;
                var isAmount = /(金额|元)/.test(c.label || '');
                html += '<td>' + (isAmount ? sums[ci].toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '') + '</td>';
            });
            html += '</tr></tfoot>';
        }
        $('#noticeTable').html(html);
    }
    function readTable() {
        var cols = [];
        $('#noticeTable thead th').each(function () {
            var $in = $(this).find('.cell-in');
            if (!$in.length) return;
            cols.push({ key: $in.attr('data-col') || ('c' + (cols.length + 1)),
                        label: $.trim($in.val()) || ('列' + (cols.length + 1)), width: 120 });
        });
        var rows = [];
        $('#noticeTable tbody tr').each(function () {
            var row = [];
            $(this).find('input.cell-in').each(function () { row.push($(this).val()); });
            rows.push(row);
        });
        return { columns: cols, rows: rows };
    }
    function renderCurrent() {
        if (state.mode === 'record') {
            // 记录模式：仅渲染打开的记录；未打开记录时表格留空（不串显模板内容）
            if (state.rec) {
                renderTable(state.rec.table_data, { sum: true });
            } else {
                renderTable({ columns: [], rows: [] });
            }
        } else if (state.tpl) {
            renderTable(state.tpl.schema);
        } else {
            renderTable({ columns: [], rows: [] });
        }
        updateChrome();
    }

    // ── 页头胶囊 / 工作台副题 / 脚注行（模式与状态驱动） ─────────────
    function updateChrome() {
        var $pill = $('#statePill');
        var tpl = state.tpl, rec = state.rec;
        if (state.mode === 'template') {
            $pill.attr('class', 'notice-state-pill').text('○ 模板编辑中 · 未发布');
            if (tpl) {
                $('#wbSub').text('模板：' + (tpl.name || '') + ' · ' +
                    ((tpl.schema && tpl.schema.columns && tpl.schema.columns.length) || 0) + ' 个字段 · 未发布');
            } else {
                $('#wbSub').text(state.typeName ? '该类型暂无模板，点击「新建公示表」创建' : '请选择左侧公示类型');
            }
            $('#footHint').text('表头与单元格可直接编辑，支持 +行 / +列扩展，不支持合并单元格与公式');
            $('#btnPubRec, #btnWithdraw').addClass('d-none');
            $('#btnPreviewTpl').toggleClass('d-none', !tpl);
        } else {
            if (rec) {
                var published = rec.status === 'published';
                $pill.attr('class', 'notice-state-pill' + (published ? ' is-published' : ''))
                    .text((published ? '● ' : '○ ') + (rec.period || '') + ' · ' + (published ? '已发布' : '未发布'));
                $('#wbSub').text((rec.title || state.typeName) + ' · ' +
                    ((rec.table_data && rec.table_data.rows && rec.table_data.rows.length) || 0) + ' 条记录');
                $('#footHint').text('单元格可直接编辑，失焦自动保存 · 发布后居民端同步可见');
                $('#btnPubRec').toggleClass('d-none', published);
                $('#btnWithdraw').toggleClass('d-none', !published);
            } else {
                $pill.attr('class', 'notice-state-pill').text('○ 未生成公示记录');
                $('#wbSub').text(state.typeName ? state.typeName + ' · 暂无记录' : '请选择左侧公示类型');
                $('#footHint').text('点击「生成本月公示」按模板生成当月记录');
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
                    renderTable({ columns: [], rows: [] });
                    renderRecList();
                    updateChrome();
                }
                renderRecList();
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
        if (!state.type) { toast('请先选择左侧公示类型', 'warning'); return; }
        ajax(BASE + '/api/template/create', {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ notice_type: state.type })
        }).done(function (res) {
            toast('模板已创建');
            setMode('template');
            loadTemplates(function () { openTemplate(res.data.item.id); });
        }).fail(fail);
    });

    $('#btnSaveTpl').on('click', function () {
        if (!state.tpl) return;
        ajax(BASE + '/api/template/update/' + state.tpl.id, {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({
                name: state.tpl.name, title: state.tpl.title || state.tpl.name,
                schema: readTable()
            })
        }).done(function () { toast('模板已保存'); loadTemplates(); }).fail(fail);
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

    // ── 表格编辑 ────────────────────────────────────────────────────
    $('#btnAddRow').on('click', function () {
        var data = readTable();
        data.rows.push(new Array(data.columns.length).fill(''));
        renderTable(data, { sum: state.mode === 'record' });
    });
    $('#btnAddCol').on('click', function () {
        var data = readTable();
        data.columns.push({ key: 'c' + (data.columns.length + 1),
                            label: '列' + (data.columns.length + 1), width: 120 });
        data.rows.forEach(function (r) { r.push(''); });
        renderTable(data, { sum: state.mode === 'record' });
    });
    $('#btnDelRow').on('click', function () {
        var data = readTable();
        if (data.rows.length > 1) data.rows.pop();
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
            ajax(BASE + '/api/record/update/' + state.rec.id, {
                type: 'POST', contentType: 'application/json',
                data: JSON.stringify({ table_data: readTable() })
            }).done(function (res) {
                state.rec = res.data.item;
                var d = new Date();
                var t = (d.getHours() < 10 ? '0' : '') + d.getHours() + ':' +
                        (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
                $('#footHint').text('已自动保存 ' + t + ' · 单元格可直接编辑 · 发布后居民端同步可见');
            }).fail(fail);
        }, 700);
    }
    $(document).on('change', '#noticeTable .cell-in', scheduleAutosave);

    // ── 记录 ────────────────────────────────────────────────────────
    function renderRecList() {
        if (!state.type) return;
        ajax(BASE + '/api/records', { data: { notice_type: state.type } }).done(function (res) {
            state.records = (res && res.data) || [];
            // 历史记录下拉（>1 条时显示；自动打开最新一条）
            var $pick = $('#recPick').empty();
            state.records.forEach(function (r) {
                $pick.append($('<option>').val(r.id)
                    .text(r.period + ' · ' + (r.status === 'published' ? '已发布' : '未发布')));
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

    function openRecord(id) {
        ajax(BASE + '/api/record/' + id).done(function (res) {
            state.rec = res.data;
            setMode('record');
            renderRecList();
        }).fail(fail);
    }

    $('#btnGenerate').on('click', function () {
        if (!state.tpl) { toast('请先选择模板（可在模板模式编辑）', 'warning'); return; }
        var period = $('#recPeriod').val();
        if (!period) { toast('公示月份缺失', 'warning'); return; }
        ajax(BASE + '/api/record/generate', {
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ template_id: state.tpl.id, period: period })
        }).done(function (res) {
            var item = res.data && (res.data.item || res.data);
            toast(res.msg || '公示已生成', res.success ? 'success' : 'warning');
            if (item && item.id) { openRecord(item.id); } else { renderRecList(); }
        }).fail(fail);
    });

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
        window.location.href = BASE + '/api/record/export/' + state.rec.id;
    });

    // 打印/预览：把当前表格渲染成只含标题 + 表格的独立窗口（拍板③）
    function openPrintWindow(autoprint) {
        var data = readTable();
        var html = '<h3 style="text-align:center;margin:10px 0;">' +
            esc((state.rec && state.rec.title) || (state.tpl && state.tpl.title) || state.typeName) +
            '</h3><table class="notice-table"><thead><tr>';
        data.columns.forEach(function (c) { html += '<th>' + esc(c.label) + '</th>'; });
        html += '</tr></thead><tbody>';
        data.rows.forEach(function (r) {
            html += '<tr>';
            r.forEach(function (c) { html += '<td>' + esc(c) + '</td>'; });
            html += '</tr>';
        });
        html += '</tbody></table>';
        var w = window.open('', '_blank', 'width=900,height=700');
        if (!w) { toast('浏览器拦截了窗口，请允许弹窗', 'warning'); return; }
        w.document.write('<!doctype html><html><head><meta charset="utf-8"><title>' +
            esc((state.rec && state.rec.title) || '补贴公示') + '</title><style>' +
            'body{font-family:"Microsoft YaHei",sans-serif;color:#000;padding:12px}' +
            'table{width:100%;border-collapse:collapse}th,td{border:1px solid #000;' +
            'padding:5px;font-size:13px;text-align:center}h3{font-size:18px}</style></head><body>' +
            html + '</body></html>');
        w.document.close();
        w.focus();
        if (autoprint) setTimeout(function () { w.print(); }, 200);
    }
    $('#btnPrintRec').on('click', function () { openPrintWindow(true); });
    $('#btnPreviewTpl').on('click', function () { openPrintWindow(false); });

    // ── 本地上传（Excel/CSV → 表头匹配 → 覆盖到当前表格） ─────────────
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
                renderTable(data, { sum: true });
                scheduleAutosave();
                toast('已填充 ' + d.rows.length + ' 行' +
                    (d.unmatched && d.unmatched.length ? '；未匹配列已忽略：' + d.unmatched.join('、') : ''));
            } else {
                toast('未解析到可填充的数据行', 'warning');
            }
        }).fail(fail).always(function () {
            $('#btnUpload').prop('disabled', false).text('上传 Excel/CSV');
        });
        this.value = '';
    });

    // ── 初始化 ──────────────────────────────────────────────────────
    // 默认月份 = 当月（生成本月公示用）
    (function initMonth() {
        var d = new Date();
        var m = d.getMonth() + 1;
        $('#recPeriod').val(d.getFullYear() + '-' + (m < 10 ? '0' + m : m));
    })();
    // 侧栏模板总数（服务端渲染的 10 类）
    (function initSiderCount() {
        var n = parseInt($('#totalTypes').text(), 10);
        if (!isNaN(n)) $('#siderCount').text('共 ' + n + ' 个模板');
    })();
    // 075 RC-16：默认选中首个公示分类（记录模式默认态）
    (function initDefaultType() {
        var $first = $('.notice-type-btn').first();
        if ($first.length) $first.trigger('click');
    })();
})();
