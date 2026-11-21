(function () {
    'use strict';
    // 数据库管理面板（025）：IIFE 规范（024）；全部端点 admin 专属（后端强制）
    var PENDING = null; // {action, payload} 待确认执行

    function fmtSize(b) {
        if (b === null || b === undefined) return '—';
        if (b < 1024) return b + ' B';
        if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
        return (b / 1048576).toFixed(2) + ' MB';
    }

    function esc(s) {
        return String(s === null || s === undefined ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function api(url, data, cb) {
        var opts = data ? {
            method: 'POST', contentType: 'application/json',
            data: JSON.stringify(data)
        } : {};
        $.ajax({ url: url, type: data ? 'POST' : 'GET', contentType: opts.contentType, data: opts.data })
            .done(function (resp) { cb(resp); })
            .fail(function (xhr) {
                var msg = '请求失败';
                try { msg = (xhr.responseJSON && xhr.responseJSON.msg) || msg; } catch (e) {}
                showToast(msg, 'danger');
            });
    }

    // ---------- 只读 ----------

    function loadOverview() {
        api('/db-admin/api/overview', null, function (resp) {
            if (!resp.success) return;
            var d = resp.data;
            $('#ovSize').text(fmtSize(d.size_bytes));
            $('#ovTables').text(d.table_count + ' 表 / ' + d.total_rows + ' 行');
            $('#ovPages').text(d.page_count + ' 页 / 碎片 ' + (d.freelist_count || 0));
            $('#ovBackup').text(d.last_backup || '无备份记录');
        });
    }

    function loadTables() {
        api('/db-admin/api/tables', null, function (resp) {
            if (!resp.success) return;
            var html = '';
            $.each(resp.data, function (i, t) {
                var cls = t.is_app ? 'col-app' : 'col-panel';
                var badge = t.is_app
                    ? '<span class="sc-badge sc-badge--info">应用表·只读</span>'
                    : '<span class="sc-badge sc-badge--muted">面板自建表</span>';
                html += '<tr data-table="' + escAttr(t.name) + '" style="cursor:pointer">' +
                    '<td class="' + cls + '">' + esc(t.name) + '</td>' +
                    '<td>' + badge + '</td>' +
                    '<td>' + t.rows + '</td>' +
                    '<td>' + t.cols + '</td>' +
                    '<td>' + t.indexes + (t.automatic_indexes ? ' (+' + t.automatic_indexes + ' 自动)' : '') + '</td>' +
                    '<td>' + fmtSize(t.size_bytes) + '</td>' +
                    '<td></td>' +
                    '</tr>';
            });
            $('#tableBody').html(html || '<tr><td colspan="7" class="text-center text-muted">暂无表</td></tr>');
            // 2026-11-17 Master–Detail：行点击 → 表详情（admin 运维页，操作列原详情按钮撤除）
            $('#tableBody tr[data-table]').on('click', function () { openDetail($(this).data('table')); });
        });
    }

    function openDetail(name) {
        api('/db-admin/api/table/' + encodeURIComponent(name), null, function (resp) {
            if (!resp.success) return;
            var d = resp.data;
            $('#tdTitle').text('表详情：' + d.name + (d.is_app ? '（应用表·只读）' : '（面板自建表）'));
            var meta = [
                '行数 ' + esc(d.rows) + '　体积 ' + fmtSize(d.size_bytes) +
                '　外键 ' + d.foreign_keys.length + ' 个'
            ].join(' | ');
            $('#tdMeta').html('<span class="small text-muted">' + meta + '</span>');
            var ch = '';
            $.each(d.columns, function (i, c) {
                ch += '<tr><td>' + c.cid + '</td><td>' + esc(c.name) + '</td><td>' + esc(c.type) +
                    '</td><td>' + (c.notnull ? '是' : '') + '</td><td>' + esc(c.default === null ? '' : c.default) +
                    '</td><td>' + (c.pk ? '是' : '') + '</td></tr>';
            });
            $('#tdCols').html(ch || '<tr><td colspan="6" class="text-muted">无字段</td></tr>');
            var ih = '';
            $.each(d.indexes, function (i, x) {
                ih += '<tr><td>' + esc(x.name) + '</td><td>' + (x.unique ? '是' : '') + '</td><td>' + esc(x.origin) +
                    '</td><td>' + esc(x.columns.join(', ')) + '</td><td>' + (x.automatic ? '自动' : '') + '</td></tr>';
            });
            $('#tdIdx').html(ih || '<tr><td colspan="5" class="text-muted">无索引</td></tr>');
            $('#tdSql').text(d.create_sql || '（无建表 SQL，或为系统表）');
            var m = bootstrap.Modal.getOrCreateInstance(document.getElementById('tableDetailModal'));
            m.show();
        });
    }

    function btnCheck() {
        api('/db-admin/api/check', null, function (resp) {
            if (!resp.success) return;
            var d = resp.data;
            showReport('完整性检查 — ' + (d.ok ? '通过' : '发现问题'), formatCheck(d), d.ok);
        });
    }

    function formatCheck(d) {
        var lines = [];
        $.each(d.details, function (k, v) { lines.push('[' + k + '] ' + v); });
        if (!d.ok) {
            $.each(d.issues, function (i, s) { lines.push('! ' + s); });
        }
        return lines.join('\n');
    }

    function btnReconcile() {
        api('/db-admin/api/reconcile', null, function (resp) {
            if (!resp.success) return;
            var d = resp.data;
            var lines = ['Schema 对账（field_inventory.json ↔ 实际库）',
                '结果：' + (d.ok ? '一致' : '存在缺失'),
                '检查表数：' + d.checked_tables];
            if (d.missing_tables && d.missing_tables.length) {
                $.each(d.missing_tables, function (i, t) { lines.push('缺表：' + t.table + '（' + t.reason + '）'); });
            }
            if (d.missing_columns && d.missing_columns.length) {
                $.each(d.missing_columns, function (i, c) { lines.push('缺字段：' + c.table + '.' + c.field); });
            }
            showReport('Schema 对账 — ' + (d.ok ? '一致' : '存在缺失'), lines.join('\n'), d.ok);
        });
    }

    function btnAudit() {
        api('/db-admin/api/audit', null, function (resp) {
            if (!resp.success) return;
            var lines = ['DDL 审计台账（最近 ' + resp.data.length + ' 条）'];
            $.each(resp.data, function (i, r) {
                lines.push(['#' + r.id, r.created_at, r.operator,
                    r.action, r.target, r.result, r.backup_file || ''].join(' | '));
            });
            showReport('DDL 审计台账', lines.join('\n'), true);
        });
    }

    function showReport(title, body, ok) {
        $('#reportTitle').text(title);
        $('#reportBody').text(body);
        var card = $('#reportCard');
        card.removeClass('d-none');
        card.removeClass('border-success border-danger');
        card.addClass(ok ? 'border-success' : 'border-danger');
    }

    // ---------- 黄级表单 ----------

    var FORMS = {
        create_table: {
            title: '建表（黄级）', fields: [
                { k: 'name', label: '表名（小写字母/数字/下划线）', ph: '如 demo_roster' },
            ], columns: true
        },
        add_column: {
            title: '加字段（黄级）', fields: [
                { k: 'table', label: '目标表（须为面板自建表）', ph: '如 demo_roster' },
            ], columns: false, singleColumn: true
        },
        create_index: {
            title: '建索引（黄级）', fields: [
                { k: 'table', label: '目标表（须为面板自建表）', ph: '如 demo_roster' },
                { k: 'name', label: '索引名', ph: '如 idx_demo_name' },
                { k: 'columns', label: '索引列（逗号分隔，1~5 列）', ph: '如 name, remark' },
            ], checkbox: { k: 'unique', label: '唯一索引' }
        },
        drop_index: {
            title: '删索引（黄级）', fields: [
                { k: 'name', label: '索引名（自动索引不可删）', ph: '如 idx_demo_name' },
            ]
        },
        copy_table: {
            title: '复制表（黄级）', fields: [
                { k: 'src', label: '源表', ph: '如 demo_roster' },
                { k: 'dst', label: '新表名', ph: '如 demo_roster_copy' },
            ], checkbox: { k: 'with_data', label: '连同数据复制', checked: true }
        },
        rename_table: {
            title: '表重命名（黄级）', fields: [
                { k: 'old', label: '当前表名（须为面板自建表）', ph: '如 demo_roster_copy' },
                { k: 'new', label: '新表名', ph: '如 demo_roster_v2' },
            ]
        },
        analyze: { title: 'ANALYZE（黄级·全库统计优化）', fixed: true },
        vacuum: { title: 'VACUUM（黄级·整理碎片，执行前自动备份）', fixed: true }
    };

    function openOp(action) {
        var cfg = FORMS[action];
        if (!cfg) return;
        var html = '';
        if (cfg.fixed) {
            html = '<div class="small text-muted mb-2">' + cfg.title + '：库级操作，点击「下一步」查看将执行的 SQL。</div>';
        } else {
            $.each(cfg.fields, function (i, f) {
                html += '<div class="mb-2"><label class="form-label small">' + f.label + '</label>' +
                    '<input type="text" class="form-control form-control-sm op-field" data-k="' + f.k + '" placeholder="' + esc(f.ph || '') + '"></div>';
            });
            if (cfg.checkbox) {
                html += '<div class="form-check mb-2"><input class="form-check-input op-check" type="checkbox" data-k="' + cfg.checkbox.k + '"' +
                    (cfg.checkbox.checked ? ' checked' : '') + '><label class="form-check-label small">' + cfg.checkbox.label + '</label></div>';
            }
            if (cfg.columns) {
                html += '<div class="small fw-bold">列定义（每行：名称,类型[,非空,默认值,主键]）</div>' +
                    '<textarea class="form-control form-control-sm mb-2" id="opCols" rows="4" placeholder="id,INTEGER,,,pk\nname,TEXT\nremark,TEXT"></textarea>' +
                    '<div class="small text-muted mb-2">类型白名单：TEXT/INTEGER/REAL/NUMERIC/BLOB/VARCHAR(n)/BOOLEAN/DATETIME/DATE；主键为可选项第 5 列</div>';
            } else if (cfg.singleColumn) {
                html += '<div class="small fw-bold">新列定义（名称,类型[,非空,默认值]）</div>' +
                    '<input type="text" class="form-control form-control-sm mb-2" id="opCols" placeholder="remark,TEXT">' +
                    '<div class="small text-muted mb-2">NOT NULL 列必须提供默认值；类型白名单同上</div>';
            }
        }
        $('#opTitle').text(cfg.title);
        $('#opBody').html(html);
        var m = bootstrap.Modal.getOrCreateInstance(document.getElementById('opModal'));
        m.show();
        $('#opModal').data('action', action);
    }

    function collectOp(action) {
        var payload = {};
        $('.op-field').each(function () {
            payload[$(this).data('k')] = $(this).val().trim();
        });
        $('.op-check').each(function () {
            payload[$(this).data('k')] = $(this).prop('checked');
        });
        var colsRaw = $('#opCols').val() || '';
        if (FORMS[action].columns) {
            payload.columns = parseColumns(colsRaw, false);
        } else if (FORMS[action].singleColumn) {
            payload.column = parseColumns(colsRaw, true)[0] || {};
        }
        if (action === 'create_index') {
            payload.columns = (payload.columns || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
        }
        return payload;
    }

    function parseColumns(raw, singleCell) {
        if (!raw.trim()) return singleCell ? {} : [];
        if (singleCell) {
            var p = raw.split(',').map(function (s) { return s.trim(); });
            var col = { name: p[0], type: p[1] || 'TEXT' };
            col.notnull = p[2] === 'true' || p[2] === '1' || p[2] === '非空';
            if (p[3] !== undefined && p[3] !== '') col.default = p[3];
            return [col];
        }
        var out = [];
        var lines = raw.split('\n');
        $.each(lines, function (i, line) {
            line = line.trim();
            if (!line) return;
            var p = line.split(',').map(function (s) { return s.trim(); });
            if (!p[0]) return;
            var col = { name: p[0], type: p[1] || 'TEXT' };
            col.notnull = p[2] === 'true' || p[2] === '1' || p[2] === '非空';
            if (p[3] !== undefined && p[3] !== '') col.default = p[3];
            col.pk = p[4] === 'pk' || p[4] === '主键' || p[4] === '1' || p[4] === 'true';
            out.push(col);
        });
        return out;
    }

    function postOp(action, execute) {
        var payload = collectOp(action);
        api('/db-admin/api/' + (execute ? 'execute' : 'preview'),
            { action: action, payload: payload }, function (resp) {
                if (!resp.success) return;
                if (!execute) {
                    // 预览成功 → 弹出执行确认（第二层风险提醒）
                    PENDING = { action: action, payload: payload };
                    var d = resp.data;
                    $('#confirmWarn').addClass('d-none').text('');
                    if (d.app_table || d.warning) {
                        $('#confirmWarn').removeClass('d-none').text(
                            (d.app_table ? '目标为应用表，后端将拒绝执行。' : '') + (d.warning || ''));
                    }
                    if (d.app_table) {
                        // 应用表：后端必然拒绝，仅提示不显示执行按钮
                        $('#confirmGo').hide();
                    } else {
                        $('#confirmGo').show();
                    }
                    $('#confirmSql').text(d.sql);
                    $('#confirmAck').prop('checked', false);
                    $('#confirmGo').prop('disabled', true);
                    $('#confirmModal').data('op', action);
                    bootstrap.Modal.getOrCreateInstance(document.getElementById('confirmModal')).show();
                } else {
                    var msg = '操作完成；已自动备份：' + resp.data.backup_file;
                    if (resp.data.size_before !== undefined && resp.data.size_before !== null) {
                        msg += '　库体积：' + fmtSize(resp.data.size_before) + ' → ' + fmtSize(resp.data.size_after);
                    }
                    showToast(msg, 'success');
                    refreshAll();
                }
            });
    }

    function refreshAll() {
        loadOverview(); loadTables(); btnAudit();
    }

    // ---------- 红级操作（026：三重闸） ----------

    var RED_FORMS = {
        drop_column: { title: '删字段（红级）', fields: [
            { k: 'table', label: '目标表（须为面板自建表）', ph: '如 demo_roster' },
            { k: 'column', label: '要删除的列', ph: '如 remark' }] },
        alter_column: { title: '改字段·表重建引擎（红级）', fields: [
            { k: 'table', label: '目标表（须为面板自建表）', ph: '如 demo_roster' },
            { k: 'column', label: '要修改的列', ph: '如 remark' },
            { k: 'type', label: '新类型', ph: '如 VARCHAR(30) / TEXT / INTEGER' },
            { k: 'notnull', label: 'NOT NULL（填 true/1 启用）', ph: '留空 = 不改' },
            { k: 'default', label: '默认值（NOT NULL 必填）', ph: '留空 = 无' }] },
        truncate_table: { title: '截断表·清空全部数据（红级）', fields: [
            { k: 'table', label: '目标表（须为面板自建表）', ph: '如 demo_roster' }] },
        drop_table: { title: '删表（红级）', fields: [
            { k: 'table', label: '目标表（须为面板自建表）', ph: '如 demo_roster' }] }
    };
    var RED_PENDING = null;

    function openRed(action) {
        var cfg = RED_FORMS[action];
        if (!cfg) return;
        var html = '';
        $.each(cfg.fields, function (i, f) {
            html += '<div class="mb-2"><label class="form-label small">' + f.label + '</label>' +
                '<input type="text" class="form-control form-control-sm red-field" data-k="' + f.k + '" placeholder="' + esc(f.ph || '') + '"></div>';
        });
        $('#redTitle').text(cfg.title);
        $('#redBody').html(html);
        $('#redModal').data('action', action);
        bootstrap.Modal.getOrCreateInstance(document.getElementById('redModal')).show();
    }

    function collectRed(action) {
        var payload = {};
        $('.red-field').each(function () {
            payload[$(this).data('k')] = $(this).val().trim();
        });
        if (action === 'alter_column') {
            payload.column = {
                name: payload.column, type: payload.type,
                notnull: payload.notnull === 'true' || payload.notnull === '1' || payload.notnull === '非空',
                default: payload.default || ''
            };
            delete payload.type; delete payload.notnull;
            if (!payload.column.default) delete payload.column.default;
        }
        return payload;
    }

    function postRed(action, execute, confirmName) {
        var payload = collectRed(action);
        var body = { action: action, payload: payload };
        if (execute) body.confirm_name = confirmName;
        api('/db-admin/api/red/' + (execute ? 'execute' : 'preview'), body, function (resp) {
            if (!resp.success) return;
            if (!execute) {
                RED_PENDING = { action: action, payload: payload };
                var d = resp.data;
                $('#redTarget').text(d.target);
                var imp = ['影响行数：<b>' + d.rows + '</b>'];
                if (d.deps && d.deps.length) {
                    $.each(d.deps, function (i, dep) {
                        imp.push('依赖：[' + esc(dep.type) + '] ' + esc(dep.name || dep.from || '') +
                            (dep.columns ? '（' + esc(dep.columns.join(',')) + '）' : '') +
                            (dep.detail ? ' ' + esc(dep.detail) : ''));
                    });
                } else {
                    imp.push('依赖：无');
                }
                $('#redImpact').html(imp.join('<br>'));
                $('#redSql').text(d.sql);
                $('#redConfirmName').val('');
                $('#redGo').prop('disabled', true);
                bootstrap.Modal.getOrCreateInstance(document.getElementById('redModal')).hide();
                bootstrap.Modal.getOrCreateInstance(document.getElementById('confirmRedModal')).show();
            } else {
                bootstrap.Modal.getOrCreateInstance(document.getElementById('confirmRedModal')).hide();
                var d = resp.data;
                $('#redResultBody').html(
                    ['<p class="mb-1"><span class="text-success fw-bold">操作完成。</span></p>',
                     '<p class="mb-1">目标：<b>' + esc(d.target) + '</b>　影响行数：' + esc(d.rows) + '</p>',
                     '<p class="mb-1">前置备份：<code>' + esc(d.backup_file) + '</code>（.wbak）</p>',
                     '<p class="mb-1">审计编号：#' + esc(d.audit_id) + '</p>',
                     '<p class="mb-1 alert alert-warning">' + esc(d.recover_tip) + '</p>'].join(''));
                bootstrap.Modal.getOrCreateInstance(document.getElementById('redResultModal')).show();
                refreshAll();
            }
        });
    }

    /* ---------- 事件绑定（jQuery 委托，不阻断冒泡——016 教训） ---------- */
    $(function () {
        // 入口须知（sessionStorage 记忆）
        if (!sessionStorage.getItem('dbAdminNoticeAck')) {
            bootstrap.Modal.getOrCreateInstance(document.getElementById('noticeModal')).show();
        }
        $('#noticeAck').on('click', function () {
            sessionStorage.setItem('dbAdminNoticeAck', '1');
            bootstrap.Modal.getOrCreateInstance(document.getElementById('noticeModal')).hide();
        });

        $('#btnCheck').on('click', btnCheck);
        $('#btnReconcile').on('click', btnReconcile);
        $('#btnAudit').on('click', btnAudit);
        $('#btnCreateTable').on('click', function () { openOp('create_table'); });
        $('#btnAddColumn').on('click', function () { openOp('add_column'); });
        $('#btnCreateIndex').on('click', function () { openOp('create_index'); });
        $('#btnDropIndex').on('click', function () { openOp('drop_index'); });
        $('#btnCopyTable').on('click', function () { openOp('copy_table'); });
        $('#btnRenameTable').on('click', function () { openOp('rename_table'); });
        $('#btnAnalyze').on('click', function () { openOp('analyze'); });
        $('#btnVacuum').on('click', function () { openOp('vacuum'); });
        $('#btnRedDropCol').on('click', function () { openRed('drop_column'); });
        $('#btnRedAlterCol').on('click', function () { openRed('alter_column'); });
        $('#btnRedTruncate').on('click', function () { openRed('truncate_table'); });
        $('#btnRedDropTable').on('click', function () { openRed('drop_table'); });

        $('#redNext').on('click', function () {
            postRed($('#redModal').data('action'), false);
        });
        $('#redConfirmName').on('input', function () {
            $('#redGo').prop('disabled',
                $(this).val().trim() !== $('#redTarget').text().trim());
        });
        $('#redGo').on('click', function () {
            if (!RED_PENDING) return;
            postRed(RED_PENDING.action, true, $('#redConfirmName').val().trim());
        });

        $('#opModal').on('hidden.bs.modal', function () {
            $('#opBody').html('');
        });

        $('#opModal').on('click', '#opNext', function () {
            postOp($('#opModal').data('action'), false);
        });

        $('#confirmAck').on('change', function () {
            $('#confirmGo').prop('disabled', !$(this).prop('checked'));
        });

        $('#confirmGo').on('click', function () {
            if (!PENDING) return;
            bootstrap.Modal.getOrCreateInstance(document.getElementById('confirmModal')).hide();
            postOp(PENDING.action, true);
        });

        loadOverview();
        loadTables();
    });

    // 暴露给内联 onclick
    window.DBADMIN = { openDetail: openDetail };
})();