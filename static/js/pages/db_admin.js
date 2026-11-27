(function () {
    'use strict';
    // 数据库管理面板（ADR-022 通俗化表目录 + 零代码可视化表单 + 三标签工作台）
    var PENDING = null;
    var RED_PENDING = null;
    var ALL_TABLES = [];
    var ACTIVE_MOD = 'all';

    // 全站业务表中文词典：{ zh: 中文名称, mod: 所属模块, desc: 通俗用途说明 }
    var TABLE_CATALOG = {
        residents: { zh: '居民基础信息库', mod: '人口户籍', desc: '全村常住与户籍人口的姓名、身份证、家庭户号等核心档案' },
        resident_tag: { zh: '居民特征标签', mod: '人口户籍', desc: '为居民打标分类（如独居、重点关注等）便于快速筛查' },
        resident_sync_log: { zh: '居民信息同步日志', mod: '人口户籍', desc: '记录各业务模块与居民主库联动同步的历史痕迹' },
        death_reports: { zh: '身故登记台账', mod: '人口户籍', desc: '居民身故核销登记及关联业务停发联动记录' },
        cascade_checklist: { zh: '身故联动停发清单', mod: '人口户籍', desc: '身故登记后需逐项核查停发补贴的待办清单' },
        cascade_notice: { zh: '联动提醒通知', mod: '人口户籍', desc: '跨模块状态变动时推送给经办人的联动提醒' },
        dibao: { zh: '低保户台账', mod: '民生保障', desc: '农村最低生活保障对象及家庭保障信息' },
        special_support: { zh: '特困供养人员', mod: '民生保障', desc: '五保及特困集中/分散供养对象档案' },
        care_targets: { zh: '关爱服务对象', mod: '民生保障', desc: '需定期探访与帮扶的重点关爱人员名册' },
        unsupported_children: { zh: '事实无人抚养儿童', mod: '民生保障', desc: '困境与事实无人抚养儿童保障档案' },
        disabled_persons: { zh: '残疾人台账', mod: '民生保障', desc: '持证残疾人等级、类别及基础信息名册' },
        disabled_service_records: { zh: '残疾人服务记录', mod: '民生保障', desc: '面向残疾人的康复、辅具与走访服务台账' },
        elder_service_records: { zh: '老年人关爱服务记录', mod: '民生保障', desc: '高龄与独居老人的日常探访及助老服务记录' },
        veteran: { zh: '退役军人台账', mod: '民生保障', desc: '退役军人优抚信息及服役档案' },
        youshu: { zh: '优抚对象台账', mod: '民生保障', desc: '重点优抚对象及定期抚恤补助名册' },
        medical_insurance_payments: { zh: '城乡居民医保缴费', mod: '民生保障', desc: '年度医保参保与缴费状态核对台账' },
        subsidy_project: { zh: '惠民补贴项目库', mod: '民生保障', desc: '村级各类惠民补贴项目定义与发放标准' },
        subsidy_record: { zh: '补贴发放明细', mod: '民生保障', desc: '历次惠民补贴到户到人的实际发放流水' },
        subsidy_batch_import: { zh: '补贴导入批次', mod: '民生保障', desc: '补贴名单 Excel 批量导入的任务记录' },
        subsidy_batch_error: { zh: '补贴导入异常行', mod: '民生保障', desc: '补贴名单导入时校验未通过的错误明细' },
        welfare_employment: { zh: '就业帮扶台账', mod: '民生保障', desc: '脱贫户与农村劳动力就业去向及帮扶记录' },
        welfare_flex_subsidy: { zh: '灵活就业社保补贴', mod: '民生保障', desc: '灵活就业人员社保补贴申报与发放记录' },
        welfare_jobs: { zh: '公益性岗位台账', mod: '民生保障', desc: '村级保洁、护林等公益岗人员在岗与考核名册' },
        visit_plan: { zh: '入户走访计划', mod: '民生保障', desc: '阶段性入户排查与慰问走访任务安排' },
        visit_record: { zh: '入户走访记录', mod: '民生保障', desc: '村干部实际入户走访情况与反馈记录' },
        demand: { zh: '民生诉求登记', mod: '民生保障', desc: '村民反映的急难愁盼问题及办件主记录' },
        demand_handle: { zh: '民生诉求办理进度', mod: '民生保障', desc: '民生诉求流转、答复与回访的处理明细' },
        party_members: { zh: '党员名册', mod: '党建工作', desc: '村党支部正式党员与预备党员基础档案' },
        party_fees: { zh: '党费收缴台账', mod: '党建工作', desc: '党员按月/按季交纳党费的实收明细' },
        party_transfers: { zh: '党员组织关系转接', mod: '党建工作', desc: '党员转入与转出组织关系的审批记录' },
        party_positions: { zh: '党内职务任职', mod: '党建工作', desc: '支委及党小组职务任免履历记录' },
        party_activity: { zh: '三会一课与主题党日', mod: '党建工作', desc: '党支部会议、党课及主题活动开展记录' },
        party_activity_review: { zh: '党建活动复核记录', mod: '党建工作', desc: '党建活动材料审核与整改反馈记录' },
        party_development: { zh: '党员发展台账', mod: '党建工作', desc: '入党积极分子到预备党员转正的全流程追踪' },
        party_development_log: { zh: '党员发展阶段日志', mod: '党建工作', desc: '发展对象各考察节点的时间与谈话记录' },
        organization_members: { zh: '村两委与组织成员', mod: '党建工作', desc: '村委会、监委会及网格员等班子成员名册' },
        civ_convention: { zh: '村规民约与精神文明', mod: '党建工作', desc: '村规民约积分、红白理事会及文明家庭评选记录' },
        election_round: { zh: '换届选举轮次', mod: '党建工作', desc: '村级换届选举届次、时间与基本配置' },
        election_voter: { zh: '选民名册', mod: '党建工作', desc: '本轮换届选举登记确认的选民资格名单' },
        election_result: { zh: '选举结果记录', mod: '党建工作', desc: '换届选举投票统计与当选人员公示记录' },
        election_batch_import: { zh: '选民导入批次', mod: '党建工作', desc: '选民名单批量导入的历史批次' },
        election_batch_error: { zh: '选民导入异常行', mod: '党建工作', desc: '选民名单导入时未通过校验的记录' },
        governance_petition: { zh: '信访维稳登记', mod: '综治安全', desc: '群众信访事项登记、稳控与包案台账' },
        governance_petition_handle: { zh: '信访办理跟进记录', mod: '综治安全', desc: '信访事项调处过程与化解结案记录' },
        mediations: { zh: '矛盾纠纷调解', mod: '综治安全', desc: '邻里、土地等民间纠纷受理与调解协议记录' },
        safety_nodes: { zh: '安全隐患点位', mod: '综治安全', desc: '村内消防、道路、水域等重点安全巡查点位' },
        safety_checks: { zh: '安全生产排查记录', mod: '综治安全', desc: '定期安全检查任务及现场巡查结论' },
        safety_check_hazards: { zh: '安全隐患整改台账', mod: '综治安全', desc: '排查发现的具体隐患及限期整改闭环记录' },
        shops: { zh: '沿街商铺与九小场所', mod: '综治安全', desc: '村域内小商店、餐饮等经营主体安全管理档案' },
        warn_record: { zh: '业务预警记录', mod: '综治安全', desc: '系统自动巡检触发的异常与超期预警信息' },
        certificate_templates: { zh: '证明开具模板', mod: '综治安全', desc: '居住证明、亲属关系等常用村级证明打印模板' },
        certificate_issues: { zh: '证明开具流水', mod: '综治安全', desc: '已为村民开具各类证明的编号与存档记录' },
        dynamic_table: { zh: '自定义台账定义', mod: '台账导入', desc: '通过「台账建表」功能创建的在线灵活表目录' },
        dynamic_field: { zh: '自定义台账列配置', mod: '台账导入', desc: '自定义台账的字段名、顺序与格式定义' },
        dynamic_record: { zh: '自定义台账数据行', mod: '台账导入', desc: '各自定义台账中录入或导入的具体业务行' },
        softtable_batch_import: { zh: '自定义台账导入批次', mod: '台账导入', desc: '灵活台账 Excel 批量导入的任务记录' },
        softtable_batch_error: { zh: '自定义台账导入异常', mod: '台账导入', desc: '灵活台账导入时出错的明细行' },
        import_batch: { zh: '通用导入批次', mod: '台账导入', desc: '通过导入向导批量上传 Excel 的历史批次' },
        import_field_mapping: { zh: '导入列映射模板', mod: '台账导入', desc: '保存的 Excel 表头与系统字段对应规则' },
        import_row_error: { zh: '导入错误行明细', mod: '台账导入', desc: '通用导入过程中校验失败的具体行原因' },
        import_profiles: { zh: '导入配置方案', mod: '台账导入', desc: '不同业务表格的默认导入参数配置' },
        import_field_registry: { zh: '导入字段审批库', mod: '台账导入', desc: '待审批与已启用的扩展导入字段登记表' },
        reconcile_task: { zh: '数据比对对账任务', mod: '台账导入', desc: '外部名单与村内底库交叉比对的任务列表' },
        reconcile_result: { zh: '数据对账差异结果', mod: '台账导入', desc: '比对产生的多出、缺失或不一致人员清单' },
        users: { zh: '系统用户账号', mod: '系统运维', desc: '登录本系统的村干部账号与状态信息' },
        roles: { zh: '角色与权限配置', mod: '系统运维', desc: '管理员、经办人等角色的功能访问权限定义' },
        device_registry: { zh: '局域网授权终端', mod: '系统运维', desc: '允许访问本系统的办公电脑设备白名单' },
        dict_categories: { zh: '数据字典分类', mod: '系统运维', desc: '下拉选项的分组类别定义（如民族、文化程度）' },
        dict_items: { zh: '数据字典选项值', mod: '系统运维', desc: '各业务表单下拉框使用的标准选项内容' },
        notice_templates: { zh: '通知模板', mod: '系统运维', desc: '短信或站内通知的预设文案模板' },
        notice_records: { zh: '通知发送记录', mod: '系统运维', desc: '系统已发送通知的接收人与状态记录' },
        module_attachments: { zh: '办事流程模板附件', mod: '系统运维', desc: '各模块上传的办事指南与空白表格附件' },
        operation_logs: { zh: '系统操作日志', mod: '系统运维', desc: '用户登录与增删改操作的安全审计日志' },
        todo_items: { zh: '工作台待办事项', mod: '系统运维', desc: '首页展示的个人与村级待办任务' },
        async_task: { zh: '后台异步任务', mod: '系统运维', desc: '批量导入、导出等耗时后台任务的执行进度' },
        migration_record: { zh: '数据结构升级记录', mod: '系统运维', desc: '系统版本迭代时自动执行的数据库迁移流水' },
        migration_log: { zh: '数据迁移日志', mod: '系统运维', desc: '结构升级过程中的详细步骤日志' },
        migration_orphan_log: { zh: '迁移孤立数据日志', mod: '系统运维', desc: '升级校验时发现的未关联旧数据备查记录' },
        schema_change_request: { zh: '结构变更申请单', mod: '系统运维', desc: '数据模型变更的审批与留痕记录' },
        data_model_version: { zh: '数据模型版本', mod: '系统运维', desc: '当前系统数据库结构版本号登记' },
        ddl_audit_log: { zh: '数据库改表审计日志', mod: '系统运维', desc: '记录本面板所有建表、改表、删表操作及前置备份' },
        sqlite_sequence: { zh: '自增编号计数器', mod: '系统运维', desc: 'SQLite 内置表，用于记录各表自动递增编号（只读）' }
    };

    var TYPE_OPTIONS = [
        { val: 'TEXT', label: '长文本（备注/地址）' },
        { val: 'VARCHAR(100)', label: '短文本（姓名/标题）' },
        { val: 'INTEGER', label: '整数（数量/编号）' },
        { val: 'REAL', label: '小数（金额/面积）' },
        { val: 'DATE', label: '日期（年-月-日）' },
        { val: 'DATETIME', label: '日期时间' },
        { val: 'BOOLEAN', label: '是/否开关' }
    ];

    var TABLE_TEMPLATES = {
        roster: [
            { name: 'id', type: 'INTEGER', notnull: false, def: '', pk: true },
            { name: 'name', type: 'VARCHAR(100)', notnull: true, def: "''", pk: false },
            { name: 'phone', type: 'VARCHAR(100)', notnull: false, def: '', pk: false },
            { name: 'remark', type: 'TEXT', notnull: false, def: '', pk: false }
        ],
        material: [
            { name: 'id', type: 'INTEGER', notnull: false, def: '', pk: true },
            { name: 'item_name', type: 'VARCHAR(100)', notnull: true, def: "''", pk: false },
            { name: 'qty', type: 'INTEGER', notnull: true, def: '0', pk: false },
            { name: 'amount', type: 'REAL', notnull: false, def: '0', pk: false },
            { name: 'updated_at', type: 'DATE', notnull: false, def: '', pk: false }
        ]
    };

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

    function getTableMeta(t) {
        var name = typeof t === 'string' ? t : t.name;
        var isApp = typeof t === 'string' ? true : !!t.is_app;
        if (!isApp) {
            return { zh: name + '（自建表）', mod: '自建扩展表', desc: '通过本管理面板创建的扩展数据表，支持可视化加减列与维护' };
        }
        if (TABLE_CATALOG[name]) return TABLE_CATALOG[name];
        return { zh: name, mod: '系统运维', desc: '系统内置数据表（只读保护）' };
    }

    function humanType(raw) {
        var up = String(raw || 'TEXT').toUpperCase();
        if (up.indexOf('INT') >= 0) return '整数 (' + raw + ')';
        if (up.indexOf('REAL') >= 0 || up.indexOf('NUM') >= 0 || up.indexOf('DEC') >= 0 || up.indexOf('FLOAT') >= 0) return '小数 (' + raw + ')';
        if (up.indexOf('BOOL') >= 0) return '是/否 (' + raw + ')';
        if (up.indexOf('DATETIME') >= 0 || up.indexOf('TIMESTAMP') >= 0) return '日期时间 (' + raw + ')';
        if (up === 'DATE') return '日期 (DATE)';
        if (up.indexOf('VARCHAR') >= 0) return '短文本 (' + raw + ')';
        if (up.indexOf('TEXT') >= 0 || up.indexOf('CHAR') >= 0) return '文本 (' + raw + ')';
        return raw || '文本';
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

    function getCustomTables() {
        return ALL_TABLES.filter(function (t) { return !t.is_app; });
    }

    // ---------- 标签页切换 ----------

    function switchTab(tabName) {
        $('.db-tab').each(function () {
            var active = $(this).data('db-tab') === tabName;
            $(this).toggleClass('is-active', active).attr('aria-selected', active ? 'true' : 'false');
        });
        $('.db-pane').attr('hidden', true);
        $('#pane-' + tabName).removeAttr('hidden');
    }

    // ---------- 绿级只读 ----------

    function loadOverview() {
        api('/db-admin/api/overview', null, function (resp) {
            if (!resp.success) return;
            var d = resp.data;
            $('#ovSize').text(fmtSize(d.size_bytes));
            $('#ovTables').text(d.table_count + ' 张 / ' + d.total_rows + ' 条');
            $('#ovPages').text(d.page_count + ' 页 / 碎片 ' + (d.freelist_count || 0));
            $('#ovBackup').text(d.last_backup || '暂无记录');
        });
    }

    function renderTables() {
        var kw = ($('#tableSearch').val() || '').trim().toLowerCase();
        var filtered = ALL_TABLES.filter(function (t) {
            var m = getTableMeta(t);
            if (ACTIVE_MOD === 'custom' && t.is_app) return false;
            if (ACTIVE_MOD !== 'all' && ACTIVE_MOD !== 'custom' && m.mod !== ACTIVE_MOD) return false;
            if (!kw) return true;
            return (t.name.toLowerCase().indexOf(kw) >= 0) ||
                (m.zh.toLowerCase().indexOf(kw) >= 0) ||
                (m.desc.toLowerCase().indexOf(kw) >= 0) ||
                (m.mod.toLowerCase().indexOf(kw) >= 0);
        });
        var html = '';
        $.each(filtered, function (i, t) {
            var m = getTableMeta(t);
            var badge = t.is_app
                ? '<span class="sc-badge sc-badge--info">' + esc(m.mod) + '</span>'
                : '<span class="sc-badge sc-badge--warning">自建扩展表</span>';
            html += '<tr data-table="' + esc(t.name) + '" style="cursor:pointer">' +
                '<td><div class="db-tname__zh">' + esc(m.zh) + '<span class="db-tname__en">' + esc(t.name) + '</span></div>' +
                '<div class="db-tname__desc">' + esc(m.desc) + '</div></td>' +
                '<td>' + badge + '</td>' +
                '<td class="sc-table__td--num">' + t.rows + '</td>' +
                '<td class="sc-table__td--num">' + t.cols + '</td>' +
                '<td class="sc-table__td--num">' + t.indexes + (t.automatic_indexes ? ' (+' + t.automatic_indexes + ' 自动)' : '') + '</td>' +
                '<td class="sc-table__td--num">' + fmtSize(t.size_bytes) + '</td>' +
                '</tr>';
        });
        $('#tableBody').html(html || '<tr><td colspan="6" class="text-center text-muted py-4">没有符合条件的数据表</td></tr>');
        $('#tableFilterStat').text('显示 ' + filtered.length + ' / 共 ' + ALL_TABLES.length + ' 张表');
        $('#tableBody tr[data-table]').on('click', function () { openDetail($(this).data('table')); });
    }

    function loadTables() {
        api('/db-admin/api/tables', null, function (resp) {
            if (!resp.success) return;
            ALL_TABLES = resp.data || [];
            $('#tabCountTables').text(ALL_TABLES.length);
            $('#tabCountCustom').text(getCustomTables().length + ' 张自建');
            renderTables();
        });
    }

    function openDetail(name) {
        api('/db-admin/api/table/' + encodeURIComponent(name), null, function (resp) {
            if (!resp.success) return;
            var d = resp.data;
            var m = getTableMeta({ name: d.name, is_app: d.is_app });
            $('#tdTitle').text(m.zh + '（' + d.name + '）');
            $('#tdMeta').html(
                '<div class="db-summary-box" style="margin-bottom:0">' +
                '<div><b>业务用途：</b>' + esc(m.desc) + '</div>' +
                '<div class="small text-muted" style="margin-top:4px">分类：' + esc(m.mod) +
                (d.is_app ? '（系统内置·只读）' : '（面板自建·可维护）') +
                '　·　记录数：' + esc(d.rows) + ' 条　·　占用空间：' + fmtSize(d.size_bytes) +
                '　·　关联外键：' + d.foreign_keys.length + ' 个</div></div>'
            );
            var ch = '';
            $.each(d.columns, function (i, c) {
                ch += '<tr><td>' + (i + 1) + '</td><td><b>' + esc(c.name) + '</b></td><td>' + esc(humanType(c.type)) +
                    '</td><td>' + (c.notnull ? '<span class="sc-badge sc-badge--warning">必填</span>' : '可选') +
                    '</td><td>' + esc(c.default === null ? '—' : c.default) +
                    '</td><td>' + (c.pk ? '<span class="sc-badge sc-badge--info">主键</span>' : '') + '</td></tr>';
            });
            $('#tdCols').html(ch || '<tr><td colspan="6" class="text-muted">无字段</td></tr>');
            var ih = '';
            $.each(d.indexes, function (i, x) {
                ih += '<tr><td>' + esc(x.name) + '</td><td>' + (x.unique ? '唯一' : '普通') + '</td><td>' + esc(x.origin) +
                    '</td><td>' + esc(x.columns.join('、')) + '</td><td>' + (x.automatic ? '系统自动' : '自定义') + '</td></tr>';
            });
            $('#tdIdx').html(ih || '<tr><td colspan="5" class="text-muted">暂无额外索引</td></tr>');
            $('#tdSql').text(d.create_sql || '（无建表 SQL，或为系统表）');
            bootstrap.Modal.getOrCreateInstance(document.getElementById('tableDetailModal')).show();
        });
    }

    function btnCheck() {
        switchTab('diag');
        api('/db-admin/api/check', null, function (resp) {
            if (!resp.success) return;
            var d = resp.data;
            var lines = [];
            $.each(d.details, function (k, v) { lines.push('[' + k + '] ' + v); });
            if (!d.ok) $.each(d.issues, function (i, s) { lines.push('! ' + s); });
            showReport('完整性体检 — ' + (d.ok ? '全部通过' : '发现异常'), lines.join('\n'), d.ok);
        });
    }

    function btnReconcile() {
        switchTab('diag');
        api('/db-admin/api/reconcile', null, function (resp) {
            if (!resp.success) return;
            var d = resp.data;
            var lines = ['结构完整性对账（标准清单 ↔ 实际数据库）',
                '体检结论：' + (d.ok ? '完全一致，无缺失' : '存在缺失项'),
                '共检查核心表：' + d.checked_tables + ' 张'];
            if (d.missing_tables && d.missing_tables.length) {
                $.each(d.missing_tables, function (i, t) { lines.push('缺少表：' + t.table + '（' + t.reason + '）'); });
            }
            if (d.missing_columns && d.missing_columns.length) {
                $.each(d.missing_columns, function (i, c) { lines.push('缺少字段：' + c.table + '.' + c.field); });
            }
            showReport('结构完整性对账 — ' + (d.ok ? '一致' : '存在缺失'), lines.join('\n'), d.ok);
        });
    }

    function btnAudit() {
        api('/db-admin/api/audit', null, function (resp) {
            if (!resp.success) return;
            var lines = ['最近 ' + resp.data.length + ' 条结构修改留痕记录'];
            $.each(resp.data, function (i, r) {
                lines.push(['#' + r.id, r.created_at, '操作人:' + r.operator,
                    r.action, '目标:' + r.target, r.result, r.backup_file ? ('备份:' + r.backup_file) : ''].filter(Boolean).join(' | '));
            });
            showReport('数据库修改审计台账', lines.join('\n'), true);
        });
    }

    function showReport(title, body, ok) {
        $('#diagPlaceholder').addClass('d-none');
        $('#reportTitle').text(title);
        $('#reportBody').text(body);
        var card = $('#reportCard');
        card.removeClass('d-none border-success border-danger');
        card.addClass(ok ? 'border-success' : 'border-danger');
    }

    // ---------- 零代码可视化表单辅助 ----------

    function typeSelectHtml(selected) {
        var found = false;
        var opts = TYPE_OPTIONS.map(function (o) {
            var isSel = selected && String(selected).toUpperCase() === o.val.toUpperCase();
            if (isSel) found = true;
            return '<option value="' + esc(o.val) + '"' + (isSel ? ' selected' : '') + '>' + esc(o.label) + '</option>';
        });
        if (selected && !found) {
            opts.unshift('<option value="' + esc(selected) + '" selected>' + esc(selected) + '（原类型）</option>');
        }
        return '<select class="sc-select col-type">' + opts.join('') + '</select>';
    }

    function tableSelectHtml(id, tables, placeholder, allowEmpty) {
        if (!tables.length) {
            return '<div class="alert alert-warning mb-2 small">当前暂无「自建扩展表」。系统内置核心表为只读保护状态，如需自定义扩展请先点击「新建数据表」。</div>' +
                '<input type="hidden" id="' + id + '" value="">';
        }
        var opts = allowEmpty ? ['<option value="">' + esc(placeholder || '请选择数据表') + '</option>'] : [];
        tables.forEach(function (t) {
            var m = getTableMeta(t);
            opts.push('<option value="' + esc(t.name) + '">' + esc(m.zh) + '（' + esc(t.name) + ' · ' + t.rows + ' 条）</option>');
        });
        return '<select class="sc-select" id="' + id + '">' + opts.join('') + '</select>';
    }

    function buildColRow(c) {
        c = c || { name: '', type: 'VARCHAR(100)', notnull: false, def: '', pk: false };
        return '<tr class="db-col-row">' +
            '<td><input type="text" class="sc-input col-name" value="' + esc(c.name) + '" placeholder="英文列标识，如 remark"></td>' +
            '<td>' + typeSelectHtml(c.type) + '</td>' +
            '<td><div class="db-col-check"><input type="checkbox" class="form-check-input col-nn"' + (c.notnull ? ' checked' : '') + '></div></td>' +
            '<td><input type="text" class="sc-input col-def" value="' + esc(c.def) + '" placeholder="可选默认值"></td>' +
            '<td><div class="db-col-check"><input type="checkbox" class="form-check-input col-pk"' + (c.pk ? ' checked' : '') + '></div></td>' +
            '<td><button type="button" class="sc-icon-btn btn-del-col-row" title="删除此列"><i class="bi bi-trash"></i></button></td>' +
            '</tr>';
    }

    function renderCreateTableForm() {
        var html =
            '<div class="mb-3">' +
                '<label class="sc-field__label" style="display:block;margin-bottom:4px">新表英文名称（小写字母、数字、下划线，如 demo_roster）</label>' +
                '<input type="text" class="sc-input op-field" data-k="name" placeholder="例如：village_activity_log">' +
            '</div>' +
            '<div class="db-tpl-bar">' +
                '<span>快速套用模板：</span>' +
                '<button type="button" class="sc-btn sc-btn--secondary sc-btn--sm" data-tpl="roster">基础名册模板</button>' +
                '<button type="button" class="sc-btn sc-btn--secondary sc-btn--sm" data-tpl="material">物资数量模板</button>' +
            '</div>' +
            '<table class="db-col-table">' +
                '<thead><tr><th style="width:24%">列名称</th><th style="width:28%">数据类型</th><th style="width:9%;text-align:center">必填</th><th style="width:21%">默认值</th><th style="width:9%;text-align:center">主键</th><th style="width:9%"></th></tr></thead>' +
                '<tbody id="colBuilderBody">' +
                    TABLE_TEMPLATES.roster.map(buildColRow).join('') +
                '</tbody>' +
            '</table>' +
            '<button type="button" class="sc-btn sc-btn--secondary sc-btn--sm" id="btnAddColRow"><i class="bi bi-plus-lg"></i> 添加一列</button>' +
            '<div class="small text-muted" style="margin-top:8px">提示：列名请使用英文字母与下划线（如 name、phone）；勾选「必填」时建议填写默认值。</div>';
        return html;
    }

    function renderAddColumnForm() {
        var customs = getCustomTables();
        return '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择目标自建表</label>' +
            tableSelectHtml('opTargetTable', customs, '请选择自建表', false) + '</div>' +
            '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">新列名称（小写字母/数字/下划线）</label>' +
            '<input type="text" class="sc-input" id="addColName" placeholder="例如：remark 或 contact_phone"></div>' +
            '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">数据类型</label>' +
            typeSelectHtml('TEXT') + '</div>' +
            '<div class="form-check mb-3"><input type="checkbox" class="form-check-input" id="addColNN">' +
            '<label class="form-check-label small" for="addColNN">设为必填项（非空，勾选后须填写下方默认值）</label></div>' +
            '<div class="mb-2"><label class="sc-field__label" style="display:block;margin-bottom:4px">默认值（可选）</label>' +
            '<input type="text" class="sc-input" id="addColDef" placeholder="例如：0 或留空"></div>';
    }

    function loadTableColumnsForIndex(tableName) {
        var box = $('#idxColPicker');
        if (!tableName) { box.html('<span class="small text-muted">请先选择目标表</span>'); return; }
        box.html('<span class="small text-muted">正在加载列清单…</span>');
        api('/db-admin/api/table/' + encodeURIComponent(tableName), null, function (resp) {
            if (!resp.success) return;
            var cols = resp.data.columns || [];
            if (!cols.length) { box.html('<span class="small text-muted">该表无可选列</span>'); return; }
            box.html(cols.map(function (c) {
                return '<label class="db-idx-col-item"><input type="checkbox" class="form-check-input idx-col-cb" value="' + esc(c.name) + '"> ' + esc(c.name) + '</label>';
            }).join(''));
            box.find('.idx-col-cb').on('change', function () {
                var picked = [];
                box.find('.idx-col-cb:checked').each(function () { picked.push($(this).val()); });
                if (picked.length && !$('#idxNameInput').data('manual')) {
                    $('#idxNameInput').val('idx_' + tableName + '_' + picked[0]);
                }
            });
        });
    }

    function loadIndexesForDrop(tableName) {
        var sel = $('#dropIdxSelect');
        if (!tableName) { sel.html('<option value="">请先选择目标表</option>'); return; }
        sel.html('<option value="">正在加载索引…</option>');
        api('/db-admin/api/table/' + encodeURIComponent(tableName), null, function (resp) {
            if (!resp.success) return;
            var idxs = (resp.data.indexes || []).filter(function (x) { return !x.automatic; });
            if (!idxs.length) {
                sel.html('<option value="">该表暂无可删除的自定义索引</option>');
                return;
            }
            sel.html(idxs.map(function (x) {
                return '<option value="' + esc(x.name) + '">' + esc(x.name) + '（列：' + esc(x.columns.join('、')) + '）</option>';
            }).join(''));
        });
    }

    function openOp(action) {
        var titles = {
            create_table: '新建数据表（黄级 · 自动备份）',
            add_column: '新增一列（黄级 · 自动备份）',
            create_index: '建立查询加速索引（黄级 · 自动备份）',
            drop_index: '移除查询加速索引（黄级 · 自动备份）',
            copy_table: '复制数据表（黄级 · 自动备份）',
            rename_table: '修改表名（黄级 · 自动备份）',
            analyze: '全库查询优化 ANALYZE（黄级）',
            vacuum: '空间碎片整理 VACUUM（黄级）'
        };
        var customs = getCustomTables();
        var html = '';
        if (action === 'create_table') {
            html = renderCreateTableForm();
        } else if (action === 'add_column') {
            html = renderAddColumnForm();
        } else if (action === 'create_index') {
            html = '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择目标自建表</label>' +
                tableSelectHtml('idxTableSelect', customs, '请选择自建表', false) + '</div>' +
                '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">勾选需要加速查询的列（1~5 列）</label>' +
                '<div class="db-idx-cols" id="idxColPicker"><span class="small text-muted">请先选择目标表</span></div></div>' +
                '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">索引名称（已自动生成，可微调）</label>' +
                '<input type="text" class="sc-input" id="idxNameInput" placeholder="如 idx_demo_name"></div>' +
                '<div class="form-check"><input type="checkbox" class="form-check-input op-check" data-k="unique" id="idxUnique">' +
                '<label class="form-check-label small" for="idxUnique">设为唯一约束（禁止该列出现重复值）</label></div>';
        } else if (action === 'drop_index') {
            html = '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择所属自建表</label>' +
                tableSelectHtml('dropIdxTableSelect', customs, '请选择自建表', false) + '</div>' +
                '<div class="mb-2"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择要移除的自定义索引</label>' +
                '<select class="sc-select" id="dropIdxSelect"><option value="">请先选择目标表</option></select></div>';
        } else if (action === 'copy_table') {
            html = '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择要复制的源表</label>' +
                tableSelectHtml('copySrcSelect', ALL_TABLES, '请选择源表', false) + '</div>' +
                '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">新表英文名称</label>' +
                '<input type="text" class="sc-input op-field" data-k="dst" placeholder="例如：residents_backup_2026"></div>' +
                '<div class="form-check"><input type="checkbox" class="form-check-input op-check" data-k="with_data" id="copyWithData" checked>' +
                '<label class="form-check-label small" for="copyWithData">连同表内现有数据一并复制</label></div>';
        } else if (action === 'rename_table') {
            html = '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择要重命名的自建表</label>' +
                tableSelectHtml('renameOldSelect', customs, '请选择自建表', false) + '</div>' +
                '<div class="mb-2"><label class="sc-field__label" style="display:block;margin-bottom:4px">新的英文表名</label>' +
                '<input type="text" class="sc-input op-field" data-k="new" placeholder="例如：demo_roster_v2"></div>';
        } else if (action === 'analyze') {
            html = '<div class="db-summary-box">将重新统计全库数据分布，帮助系统自动选择更快的查询路径，过程安全且不修改业务数据。点击「下一步」确认。</div>';
        } else if (action === 'vacuum') {
            html = '<div class="db-summary-box">将整理并压缩数据库文件中的空闲碎片，执行前会自动生成 <b>.wbak</b> 加密备份；整理期间会短暂锁定写入，建议避开业务高峰。</div>';
        }

        $('#opTitle').text(titles[action] || '操作');
        $('#opBody').html(html);
        $('#opModal').data('action', action);

        if (action === 'create_table') {
            $('#opBody').on('click', '#btnAddColRow', function () {
                $('#colBuilderBody').append(buildColRow());
            });
            $('#opBody').on('click', '.btn-del-col-row', function () {
                if ($('#colBuilderBody .db-col-row').length <= 1) {
                    showToast('至少保留一列', 'warning'); return;
                }
                $(this).closest('tr').remove();
            });
            $('#opBody').on('click', '[data-tpl]', function () {
                var tpl = TABLE_TEMPLATES[$(this).data('tpl')] || [];
                $('#colBuilderBody').html(tpl.map(buildColRow).join(''));
            });
        } else if (action === 'create_index') {
            $('#idxNameInput').on('input', function () { $(this).data('manual', true); });
            $('#idxTableSelect').on('change', function () { loadTableColumnsForIndex($(this).val()); });
            if (customs.length) loadTableColumnsForIndex(customs[0].name);
        } else if (action === 'drop_index') {
            $('#dropIdxTableSelect').on('change', function () { loadIndexesForDrop($(this).val()); });
            if (customs.length) loadIndexesForDrop(customs[0].name);
        }

        bootstrap.Modal.getOrCreateInstance(document.getElementById('opModal')).show();
    }

    function collectOp(action) {
        var payload = {};
        $('.op-field').each(function () { payload[$(this).data('k')] = ($(this).val() || '').trim(); });
        $('.op-check').each(function () { payload[$(this).data('k')] = $(this).prop('checked'); });

        if (action === 'create_table') {
            var cols = [];
            $('#colBuilderBody .db-col-row').each(function () {
                var name = ($(this).find('.col-name').val() || '').trim();
                if (!name) return;
                var col = {
                    name: name,
                    type: $(this).find('.col-type').val() || 'TEXT',
                    notnull: $(this).find('.col-nn').prop('checked'),
                    pk: $(this).find('.col-pk').prop('checked')
                };
                var def = ($(this).find('.col-def').val() || '').trim();
                if (def !== '') col.default = def;
                cols.push(col);
            });
            payload.columns = cols;
        } else if (action === 'add_column') {
            payload.table = ($('#opTargetTable').val() || '').trim();
            var c = {
                name: ($('#addColName').val() || '').trim(),
                type: $('#opBody .col-type').val() || 'TEXT',
                notnull: $('#addColNN').prop('checked')
            };
            var d = ($('#addColDef').val() || '').trim();
            if (d !== '') c.default = d;
            payload.column = c;
        } else if (action === 'create_index') {
            payload.table = ($('#idxTableSelect').val() || '').trim();
            payload.name = ($('#idxNameInput').val() || '').trim();
            var picked = [];
            $('#idxColPicker .idx-col-cb:checked').each(function () { picked.push($(this).val()); });
            payload.columns = picked;
        } else if (action === 'drop_index') {
            payload.name = ($('#dropIdxSelect').val() || '').trim();
        } else if (action === 'copy_table') {
            payload.src = ($('#copySrcSelect').val() || '').trim();
        } else if (action === 'rename_table') {
            payload.old = ($('#renameOldSelect').val() || '').trim();
        }
        return payload;
    }

    function summarizeYellow(action, payload) {
        if (action === 'create_table') return '即将新建自建表 <b>' + esc(payload.name) + '</b>，共包含 <b>' + (payload.columns ? payload.columns.length : 0) + '</b> 个字段。';
        if (action === 'add_column') return '即将为表 <b>' + esc(payload.table) + '</b> 新增字段 <b>' + esc(payload.column && payload.column.name) + '</b>（类型：' + esc(humanType(payload.column && payload.column.type)) + '）。';
        if (action === 'create_index') return '即将为表 <b>' + esc(payload.table) + '</b> 的列（' + esc((payload.columns || []).join('、')) + '）建立查询加速索引 <b>' + esc(payload.name) + '</b>。';
        if (action === 'drop_index') return '即将移除自定义索引 <b>' + esc(payload.name) + '</b>。';
        if (action === 'copy_table') return '即将把 <b>' + esc(payload.src) + '</b> 复制为新表 <b>' + esc(payload.dst) + '</b>（' + (payload.with_data ? '包含现有数据' : '仅复制空表结构') + '）。';
        if (action === 'rename_table') return '即将把自建表 <b>' + esc(payload.old) + '</b> 重命名为 <b>' + esc(payload.new) + '</b>。';
        if (action === 'analyze') return '即将刷新全库查询优化统计信息（不改动业务数据）。';
        if (action === 'vacuum') return '即将整理全库空间碎片并自动回收闲置磁盘空间。';
        return '即将执行数据库维护操作。';
    }

    function postOp(action, execute) {
        var payload = execute && PENDING ? PENDING.payload : collectOp(action);
        api('/db-admin/api/' + (execute ? 'execute' : 'preview'),
            { action: action, payload: payload }, function (resp) {
                if (!resp.success) return;
                if (!execute) {
                    PENDING = { action: action, payload: payload };
                    var d = resp.data;
                    $('#confirmWarn').addClass('d-none').text('');
                    if (d.app_table || d.warning) {
                        $('#confirmWarn').removeClass('d-none').text(
                            (d.app_table ? '目标为系统内置表，禁止修改。' : '') + (d.warning || ''));
                    }
                    $('#confirmGo').toggle(!d.app_table);
                    $('#confirmSummary').html(summarizeYellow(action, payload) + '<div class="small text-muted" style="margin-top:4px">执行前将自动生成 <b>.wbak</b> 加密备份，若备份失败将自动中止。</div>');
                    $('#confirmSql').text(d.sql);
                    $('#confirmAck').prop('checked', false);
                    $('#confirmGo').prop('disabled', true);
                    $('#confirmModal').data('op', action);
                    bootstrap.Modal.getOrCreateInstance(document.getElementById('opModal')).hide();
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
        loadOverview();
        loadTables();
        btnAudit();
    }

    // ---------- 红级操作（026：三重闸 + 联动下拉可视化表单） ----------

    var RED_TABLE_COLS = {};

    function loadRedColumns(tableName, forAlter) {
        var sel = $('#redColSelect');
        if (!tableName) { sel.html('<option value="">请先选择自建表</option>'); return; }
        sel.html('<option value="">正在加载字段…</option>');
        api('/db-admin/api/table/' + encodeURIComponent(tableName), null, function (resp) {
            if (!resp.success) return;
            var cols = resp.data.columns || [];
            RED_TABLE_COLS[tableName] = cols;
            if (!cols.length) { sel.html('<option value="">该表无字段</option>'); return; }
            sel.html(cols.map(function (c) {
                return '<option value="' + esc(c.name) + '">' + esc(c.name) + '（' + esc(humanType(c.type)) + (c.notnull ? ' · 必填' : '') + '）</option>';
            }).join(''));
            if (forAlter) syncAlterColDefaults(tableName, cols[0].name);
        });
    }

    function syncAlterColDefaults(tableName, colName) {
        var cols = RED_TABLE_COLS[tableName] || [];
        var found = cols.filter(function (c) { return c.name === colName; })[0];
        if (!found) return;
        $('#redAlterTypeWrap').html(typeSelectHtml(found.type));
        $('#redAlterNN').prop('checked', !!found.notnull);
        $('#redAlterDef').val(found.default === null || found.default === undefined ? '' : found.default);
    }

    function openRed(action) {
        var titles = {
            drop_column: '删除字段（红级 · 三重闸）',
            alter_column: '修改字段属性（红级 · 自动重建表）',
            truncate_table: '清空表内全部数据（红级 · 三重闸）',
            drop_table: '彻底删除整表（红级 · 三重闸）'
        };
        var customs = getCustomTables();
        var html = '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择目标自建表</label>' +
            tableSelectHtml('redTableSelect', customs, '请选择自建表', false) + '</div>';

        if (action === 'drop_column') {
            html += '<div class="mb-2"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择要删除的字段</label>' +
                '<select class="sc-select" id="redColSelect"><option value="">请先选择目标表</option></select></div>';
        } else if (action === 'alter_column') {
            html += '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">选择要修改的字段</label>' +
                '<select class="sc-select" id="redColSelect"><option value="">请先选择目标表</option></select></div>' +
                '<div class="mb-3"><label class="sc-field__label" style="display:block;margin-bottom:4px">调整为数据类型</label>' +
                '<div id="redAlterTypeWrap">' + typeSelectHtml('TEXT') + '</div></div>' +
                '<div class="form-check mb-3"><input type="checkbox" class="form-check-input" id="redAlterNN">' +
                '<label class="form-check-label small" for="redAlterNN">设为必填项（非空，勾选后须提供默认值）</label></div>' +
                '<div class="mb-2"><label class="sc-field__label" style="display:block;margin-bottom:4px">默认值</label>' +
                '<input type="text" class="sc-input" id="redAlterDef" placeholder="留空表示无默认值"></div>';
        } else if (action === 'truncate_table') {
            html += '<div class="small text-muted">将清空所选自建表内的全部数据行，保留表结构。</div>';
        } else if (action === 'drop_table') {
            html += '<div class="small text-muted">将永久删除所选自建表的结构与全部记录，操作前会自动备份。</div>';
        }

        $('#redTitle').text(titles[action] || '高风险操作');
        $('#redBody').html(html);
        $('#redModal').data('action', action);

        if (action === 'drop_column' || action === 'alter_column') {
            $('#redTableSelect').on('change', function () {
                loadRedColumns($(this).val(), action === 'alter_column');
            });
            if (action === 'alter_column') {
                $('#redBody').on('change', '#redColSelect', function () {
                    syncAlterColDefaults($('#redTableSelect').val(), $(this).val());
                });
            }
            if (customs.length) loadRedColumns(customs[0].name, action === 'alter_column');
        }

        bootstrap.Modal.getOrCreateInstance(document.getElementById('redModal')).show();
    }

    function collectRed(action) {
        var payload = { table: ($('#redTableSelect').val() || '').trim() };
        if (action === 'drop_column') {
            payload.column = ($('#redColSelect').val() || '').trim();
        } else if (action === 'alter_column') {
            var def = ($('#redAlterDef').val() || '').trim();
            payload.column = {
                name: ($('#redColSelect').val() || '').trim(),
                type: $('#redAlterTypeWrap .col-type').val() || 'TEXT',
                notnull: $('#redAlterNN').prop('checked')
            };
            if (def !== '') payload.column.default = def;
        }
        return payload;
    }

    function postRed(action, execute, confirmName) {
        var payload = execute && RED_PENDING ? RED_PENDING.payload : collectRed(action);
        var body = { action: action, payload: payload };
        if (execute) body.confirm_name = confirmName;
        api('/db-admin/api/red/' + (execute ? 'execute' : 'preview'), body, function (resp) {
            if (!resp.success) return;
            if (!execute) {
                RED_PENDING = { action: action, payload: payload };
                var d = resp.data;
                $('#redTarget').text(d.target);
                var imp = ['目标表：<b>' + esc(d.target) + '</b>　·　受影响记录数：<b>' + d.rows + '</b> 条'];
                if (d.deps && d.deps.length) {
                    $.each(d.deps, function (i, dep) {
                        imp.push('关联依赖：[' + esc(dep.type) + '] ' + esc(dep.name || dep.from || '') +
                            (dep.columns ? '（' + esc(dep.columns.join('、')) + '）' : '') +
                            (dep.detail ? ' ' + esc(dep.detail) : ''));
                    });
                } else {
                    imp.push('关联依赖：无其他索引或外键依赖');
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
                    ['<p class="mb-2"><span class="text-success fw-bold">操作已完成。</span></p>',
                     '<p class="mb-1">目标表：<b>' + esc(d.target) + '</b>　·　影响行数：' + esc(d.rows) + '</p>',
                     '<p class="mb-1">前置备份：<code>' + esc(d.backup_file) + '</code>（.wbak）</p>',
                     '<p class="mb-2">审计编号：#' + esc(d.audit_id) + '</p>',
                     '<p class="mb-0 alert alert-warning">' + esc(d.recover_tip) + '</p>'].join(''));
                bootstrap.Modal.getOrCreateInstance(document.getElementById('redResultModal')).show();
                refreshAll();
            }
        });
    }

    // ---------- 事件绑定 ----------
    $(function () {
        if (!sessionStorage.getItem('dbAdminNoticeAck')) {
            bootstrap.Modal.getOrCreateInstance(document.getElementById('noticeModal')).show();
        }
        $('#noticeAck').on('click', function () {
            sessionStorage.setItem('dbAdminNoticeAck', '1');
            bootstrap.Modal.getOrCreateInstance(document.getElementById('noticeModal')).hide();
        });
        $('#btnShowNotice').on('click', function () {
            bootstrap.Modal.getOrCreateInstance(document.getElementById('noticeModal')).show();
        });

        $('.db-tab').on('click', function () { switchTab($(this).data('db-tab')); });
        $('#modulePills').on('click', '.db-pill', function () {
            ACTIVE_MOD = $(this).data('mod');
            $('#modulePills .db-pill').removeClass('is-active');
            $(this).addClass('is-active');
            renderTables();
        });
        $('#tableSearch').on('input', renderTables);

        $('#btnCheck').on('click', btnCheck);
        $('#btnReconcile').on('click', btnReconcile);
        $('#btnAudit').on('click', function () { switchTab('diag'); btnAudit(); });

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

        $('#redNext').on('click', function () { postRed($('#redModal').data('action'), false); });
        $('#redConfirmName').on('input', function () {
            $('#redGo').prop('disabled', $(this).val().trim() !== $('#redTarget').text().trim());
        });
        $('#redGo').on('click', function () {
            if (!RED_PENDING) return;
            postRed(RED_PENDING.action, true, $('#redConfirmName').val().trim());
        });

        $('#opModal').on('hidden.bs.modal', function () { $('#opBody').off().html(''); });
        $('#opModal').on('click', '#opNext', function () { postOp($('#opModal').data('action'), false); });
        $('#confirmAck').on('change', function () { $('#confirmGo').prop('disabled', !$(this).prop('checked')); });
        $('#confirmGo').on('click', function () {
            if (!PENDING) return;
            bootstrap.Modal.getOrCreateInstance(document.getElementById('confirmModal')).hide();
            postOp(PENDING.action, true);
        });

        loadOverview();
        loadTables();
    });

    window.DBADMIN = { openDetail: openDetail };
})();
