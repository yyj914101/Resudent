/* dynatable_index.js — 软表 V2.5 补丁 B-2 台账建表分组总览控制器 */
(function () {
    'use strict';
    var BASE = '/dynatable';
    var CORE_MODULES = [
        ['organization', '机构管理'],
        ['civil', '民政对象管理'],
        ['pension', '养老服务'],
        ['safety', '安全管理'],
        ['dynatable', '自定义台账']
    ];
    var state = {
        tables: [],
        query: '',
        moduleFilter: 'all',
        expanded: {},
        pendingDel: null,
        pendingClone: null
    };

    function _esc(s) {
        if (typeof esc === 'function') return esc(s);
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function _toast(msg, type) {
        if (typeof showToast === 'function') showToast(msg, type || 'success');
    }

    function buildGroups(list) {
        var map = {};
        var order = [];
        list.forEach(function (t) {
            var gkey = (t.group_name || '').trim() || ('__single__:' + t.code);
            if (!map[gkey]) {
                map[gkey] = {
                    key: gkey,
                    isGroup: !!(t.group_name || '').trim(),
                    title: (t.group_name || '').trim() || t.name,
                    module_key: t.module_key,
                    module_label: t.module_label || t.module_key,
                    period_tag: t.period_tag || '',
                    tables: []
                };
                order.push(gkey);
            }
            if (!map[gkey].period_tag && t.period_tag) map[gkey].period_tag = t.period_tag;
            map[gkey].tables.push(t);
        });
        return order.map(function (k) { return map[k]; });
    }

    function render() {
        var allGroups = buildGroups(state.tables);
        var totalRecs = state.tables.reduce(function (a, t) { return a + (t.record_count || 0); }, 0);
        var activeMods = {};
        var pillMods = {};
        CORE_MODULES.forEach(function (pair) { pillMods[pair[0]] = pair[1]; });
        state.tables.forEach(function (t) {
            activeMods[t.module_key] = t.module_label || t.module_key;
            pillMods[t.module_key] = t.module_label || t.module_key;
        });

        $('#dtIndexKpi').html(
            '<div class="dtv2-kpi"><div class="dtv2-kpi__label">工作簿 / 分组</div><div class="dtv2-kpi__val">' + allGroups.length + ' 组</div><div class="dtv2-kpi__sub">每组在业务模块仅占 1 枚收敛胶囊</div></div>' +
            '<div class="dtv2-kpi"><div class="dtv2-kpi__label">已建软表子表</div><div class="dtv2-kpi__val">' + state.tables.length + ' 张</div><div class="dtv2-kpi__sub">自定义表均支持安全删除与期次结转</div></div>' +
            '<div class="dtv2-kpi"><div class="dtv2-kpi__label">当前在库记录</div><div class="dtv2-kpi__val">' + totalRecs + ' 条</div><div class="dtv2-kpi__sub">敏感身份列自动加密存储与脱敏展示</div></div>' +
            '<div class="dtv2-kpi"><div class="dtv2-kpi__label">已挂载业务模块</div><div class="dtv2-kpi__val">' + Object.keys(activeMods).length + ' 个</div><div class="dtv2-kpi__sub">含新增机构管理与民政对象管理</div></div>'
        );

        var pillHtml = '<button type="button" class="dtv2-pill' + (state.moduleFilter === 'all' ? ' active' : '') + '" data-mod="all">全部模块</button>';
        Object.keys(pillMods).forEach(function (mk) {
            pillHtml += '<button type="button" class="dtv2-pill' + (state.moduleFilter === mk ? ' active' : '') + '" data-mod="' + _esc(mk) + '">' + _esc(pillMods[mk]) + '</button>';
        });
        $('#dtModulePills').html(pillHtml);

        var q = state.query.trim().toLowerCase();
        var visible = allGroups.filter(function (g) {
            if (state.moduleFilter !== 'all' && g.module_key !== state.moduleFilter) return false;
            if (!q) return true;
            if (g.title.toLowerCase().indexOf(q) >= 0 || (g.period_tag || '').toLowerCase().indexOf(q) >= 0) return true;
            return g.tables.some(function (t) {
                return (t.name || '').toLowerCase().indexOf(q) >= 0 || (t.code || '').toLowerCase().indexOf(q) >= 0;
            });
        });

        if (!visible.length) {
            $('#dtGroupList').html('<div class="sc-card" style="padding:38px;text-align:center;color:var(--c-muted)">没有匹配的工作簿分组，点击右上角「＋ 上传 Excel / 建新台账」创建。</div>');
            return;
        }

        var html = visible.map(function (g) {
            var first = g.tables[0];
            var rowsSum = g.tables.reduce(function (a, t) { return a + (t.record_count || 0); }, 0);
            var hasBuiltin = g.tables.some(function (t) { return !!t.is_builtin; });
            var isOpen = state.expanded[g.key] !== undefined ? state.expanded[g.key] : (g.tables.length > 1);
            var subHtml = isOpen ? (
                '<table class="dtv2-sub-table"><thead><tr><th>子表名称</th><th>表标识</th><th>字段 / 记录</th><th>更新时间</th><th style="text-align:right">操作</th></tr></thead><tbody>' +
                g.tables.map(function (t) {
                    var delBtn = t.is_builtin
                        ? '<button type="button" class="sc-btn sc-btn--secondary" disabled title="系统内置预置台账禁止删除" style="padding:2px 8px;font-size:12px">内置表</button>'
                        : '<button type="button" class="sc-btn sc-btn--secondary" data-del-table="' + _esc(t.code) + '" data-del-name="' + _esc(t.name) + '" style="padding:2px 8px;font-size:12px;color:var(--c-danger,#B91C1C)">删除</button>';
                    return '<tr>' +
                        '<td><strong>' + _esc(t.name) + '</strong></td>' +
                        '<td><code>' + _esc(t.code) + '</code></td>' +
                        '<td>' + (t.field_count || 0) + ' 列 · ' + (t.record_count || 0) + ' 条</td>' +
                        '<td>' + _esc(t.updated_at || '-') + '</td>' +
                        '<td style="text-align:right;white-space:nowrap">' +
                            '<a class="sc-btn sc-btn--secondary" href="' + BASE + '/t/' + encodeURIComponent(t.code) + '" style="padding:2px 8px;font-size:12px">工作台</a> ' +
                            '<a class="sc-btn sc-btn--secondary" href="' + BASE + '/t/' + encodeURIComponent(t.code) + '/structure" style="padding:2px 8px;font-size:12px">结构</a> ' +
                            delBtn +
                        '</td></tr>';
                }).join('') + '</tbody></table>'
            ) : '';

            var groupActions = '';
            if (g.tables.length > 1) {
                groupActions += '<button type="button" class="sc-btn sc-btn--secondary" data-toggle-grp="' + _esc(g.key) + '" style="padding:4px 10px;font-size:12px">' + (isOpen ? '收起子表' : '展开子表') + '</button>';
            }
            groupActions += '<a class="sc-btn sc-btn--primary" href="' + BASE + '/t/' + encodeURIComponent(first.code) + '" style="padding:4px 10px;font-size:12px">进入工作台</a>';
            groupActions += '<button type="button" class="sc-btn sc-btn--secondary" data-clone-code="' + _esc(first.code) + '" data-clone-name="' + _esc(g.title) + '" data-clone-group="' + (g.isGroup && g.tables.length > 1 ? '1' : '0') + '" style="padding:4px 10px;font-size:12px">期次结转</button>';
            if (g.isGroup) {
                groupActions += '<a class="sc-btn sc-btn--secondary" href="' + BASE + '/api/group/' + encodeURIComponent(g.title) + '/export" style="padding:4px 10px;font-size:12px">导出整本</a>';
                if (!hasBuiltin) {
                    groupActions += '<button type="button" class="sc-btn sc-btn--secondary" data-del-group="' + _esc(g.title) + '" style="padding:4px 10px;font-size:12px;color:var(--c-danger,#B91C1C)">整组删除</button>';
                }
            }

            return '<div class="dtv2-group-card">' +
                '<div class="dtv2-group-head">' +
                    '<div>' +
                        '<div class="dtv2-group-title">' +
                            '<span>' + _esc(g.title) + '</span>' +
                            '<span class="dtv2-tag dtv2-tag--info">' + _esc(g.module_label) + '</span>' +
                            (g.period_tag ? '<span class="dtv2-tag">' + _esc(g.period_tag) + '</span>' : '') +
                        '</div>' +
                        '<div class="dtv2-group-meta">' +
                            '<span>含 ' + g.tables.length + ' 张子表</span>' +
                            '<span>共 ' + rowsSum + ' 条记录</span>' +
                            (g.tables.length > 1 ? '<span>模块顶部已收敛为 1 枚胶囊</span>' : '') +
                        '</div>' +
                    '</div>' +
                    '<div class="dtv2-group-actions">' + groupActions + '</div>' +
                '</div>' + subHtml + '</div>';
        }).join('');
        $('#dtGroupList').html(html);
    }

    function load() {
        $.getJSON(BASE + '/api/list', function (resp) {
            if (!resp || !resp.success) { _toast((resp && resp.msg) || '加载失败', 'danger'); return; }
            state.tables = resp.data || [];
            render();
        });
    }

    $(function () {
        load();
        $('#dtIndexSearch').on('input', function () { state.query = this.value; render(); });
        $('#dtModulePills').on('click', '[data-mod]', function () {
            state.moduleFilter = $(this).attr('data-mod');
            render();
        });
        $('#dtGroupList').on('click', '[data-toggle-grp]', function () {
            var k = $(this).attr('data-toggle-grp');
            state.expanded[k] = state.expanded[k] === false ? true : false;
            render();
        });
        $('#dtGroupList').on('click', '[data-del-table]', function () {
            state.pendingDel = { type: 'table', code: $(this).attr('data-del-table'), name: $(this).attr('data-del-name') };
            $('#dtDelTitle').text('确认安全删除单张台账');
            $('#dtDelDesc').text('即将删除「' + state.pendingDel.name + '」（' + state.pendingDel.code + '）。');
            document.getElementById('dtDeleteOverlay').hidden = false;
        });
        $('#dtGroupList').on('click', '[data-del-group]', function () {
            var gname = $(this).attr('data-del-group');
            state.pendingDel = { type: 'group', group: gname, name: gname };
            $('#dtDelTitle').text('确认整组安全删除工作簿');
            $('#dtDelDesc').text('即将删除分组「' + gname + '」下的全部自定义子表。');
            document.getElementById('dtDeleteOverlay').hidden = false;
        });
        $('#dtGroupList').on('click', '[data-clone-code]', function () {
            var hasGrp = $(this).attr('data-clone-group') === '1';
            state.pendingClone = { code: $(this).attr('data-clone-code'), name: $(this).attr('data-clone-name'), hasGroup: hasGrp };
            $('#dtCloneTitle').text('期次结转：' + state.pendingClone.name);
            var now = new Date();
            $('#dtClonePeriod').val(now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0'));
            $('#dtCloneWholeGroup').prop('checked', hasGrp).prop('disabled', !hasGrp);
            document.getElementById('dtCloneOverlay').hidden = false;
        });

        $(document).on('click', '[data-close-overlay]', function () {
            var el = document.getElementById($(this).attr('data-close-overlay'));
            if (el) el.hidden = true;
        });

        $('#dtBtnConfirmDel').on('click', function () {
            if (!state.pendingDel) return;
            var url = state.pendingDel.type === 'group'
                ? (BASE + '/api/group/' + encodeURIComponent(state.pendingDel.group) + '/delete')
                : (BASE + '/api/t/' + encodeURIComponent(state.pendingDel.code) + '/delete');
            var btn = this;
            btn.disabled = true;
            $.ajax({ url: url, method: 'POST', contentType: 'application/json', data: '{}' })
                .done(function (res) {
                    if (!res || !res.success) { _toast((res && res.msg) || '删除失败', 'danger'); return; }
                    _toast(res.msg || '已安全删除');
                    document.getElementById('dtDeleteOverlay').hidden = true;
                    state.pendingDel = null;
                    load();
                })
                .fail(function (xhr) { _toast((xhr.responseJSON && xhr.responseJSON.msg) || '删除请求失败', 'danger'); })
                .always(function () { btn.disabled = false; });
        });

        $('#dtBtnConfirmClone').on('click', function () {
            if (!state.pendingClone) return;
            var pTag = ($('#dtClonePeriod').val() || '').trim();
            if (!pTag) { _toast('请输入新期次标签', 'warning'); return; }
            var btn = this;
            btn.disabled = true;
            $.ajax({
                url: BASE + '/api/t/' + encodeURIComponent(state.pendingClone.code) + '/clone-period',
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    period_tag: pTag,
                    copy_records: $('#dtCloneCopyRows').is(':checked'),
                    clone_group: $('#dtCloneWholeGroup').is(':checked')
                })
            }).done(function (res) {
                if (!res || !res.success) { _toast((res && res.msg) || '期次结转失败', 'danger'); return; }
                _toast(res.msg || '已生成新期次台账');
                document.getElementById('dtCloneOverlay').hidden = true;
                state.pendingClone = null;
                load();
            }).fail(function (xhr) { _toast((xhr.responseJSON && xhr.responseJSON.msg) || '期次结转请求失败', 'danger'); })
              .always(function () { btn.disabled = false; });
        });
    });
})();
