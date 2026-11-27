/* employment.js — 084 就业服务（就业状态登记 + 灵活就业补贴申报，同页两 tab）
 *
 * 契约（后端已实现于 routes/welfare_job.py::employment_*，本文件只消费）：
 *   GET  /welfare/employment/api/options                        → {success, data:{village_groups:[..], dicts:{就业状态/就业类型/就业行业/灵活就业补贴类型/补贴申报状态}}}
 *   GET  /welfare/employment/api/list?tab=&page=&limit=&...     → {success, msg, data:[...], total}
 *     tab=employment 过滤：keyword / village_group / employment_status / employment_type
 *     tab=subsidy   过滤：keyword / apply_year / subsidy_type / apply_status
 *   GET  /welfare/employment/api/detail/<id>?tab=               → {success, data:{...}}
 *   POST /welfare/employment/api/save        (FormData: tab + 字段 + id)
 *   POST /welfare/employment/api/delete/<id>?tab=
 *   POST /welfare/employment/api/transition  (FormData: id, to_status, tab)
 *   GET  /welfare/employment/api/export?tab=&...
 *
 * 字段真源：models/welfare_employment.py（start_date/update_date/industry/work_location…）
 *          models/welfare_flex_subsidy.py（subsidy_type/amount/review_date/issue_date…）
 * 状态口径：apply_status 不在表单内编辑，只走 transition（后端 SUBSIDY_TRANSITIONS 唯一入口，
 *          新建固定「待申报」）。
 *
 * 约束：
 *   - IIFE + 只绑页面内元素（不绑 document），SPA 每次进页在全新作用域重放即重置状态、零全局污染。
 *   - CSRF 由 spa.js 包装 window.fetch 自动注入（不手写 X-CSRF-Token）。
 *   - 409/400 需读 resp.status 后取 msg，不得静默失败。
 *   - 空态/加载态/错误态/无权限态四种非正常态全部有明确文案。
 */
(function () {
    'use strict';

    var API = {
        options: '/welfare/employment/api/options',
        list: '/welfare/employment/api/list',
        detail: '/welfare/employment/api/detail/',
        save: '/welfare/employment/api/save',
        del: '/welfare/employment/api/delete/',
        transition: '/welfare/employment/api/transition',
        export: '/welfare/employment/api/export'
    };
    var PAGE_SIZE = 20;

    // 列定义即渲染真源（顺序与模板 thead 一一对应；序号列与操作列由 render 追加）
    // 键名 = 后端 _employment_to_dict / _subsidy_to_dict 序列化键（结构真源 models/*）
    var COLUMNS = {
        employment: [
            { key: 'name' },
            { key: 'village_group' },
            { key: 'id_card' },
            { key: 'phone' },
            { key: 'employment_status' },
            { key: 'employment_type' },
            { key: 'employer' },
            { key: 'start_date' },
            { key: 'update_date' }
        ],
        subsidy: [
            { key: 'name' },
            { key: 'id_card' },
            { key: 'apply_year' },
            { key: 'batch_no' },
            { key: 'subsidy_type' },
            { key: 'amount', money: true },
            { key: 'apply_date' },
            { key: 'apply_status', badge: true },
            { key: 'reject_reason', ellipsis: true }
        ]
    };

    // 状态徽章配色（值→components.css 既有 sc-badge--* 档；未命中原色）
    var BADGE = [
        { re: /通过|已发放|已办结|完成/, cls: 'success' },
        { re: /未通过|不通过|驳回|不予/, cls: 'danger' },
        { re: /审核中|办理中|待审|复核/, cls: 'warning' },
        { re: /已申报|已受理|已提交/, cls: 'info' }
    ];

    // 推荐流转：与后端 SUBSIDY_TRANSITIONS（routes/welfare_job.py 唯一真源）逐条对齐
    var FLOW = {
        '待申报': ['已申报', '未通过'],
        '已申报': ['已审核', '未通过'],
        '已审核': ['已发放', '未通过'],
        '已发放': [],
        '未通过': []
    };
    var STATUS_FALLBACK = ['待申报', '已申报', '已审核', '已发放', '未通过'];

    // 字典键别名（后端 options 的 dicts 以中文分类名为键；英文键名一并兼容，防后端改名前端全挂）
    var DICT_KEYS = {
        village_group: ['village_group', 'village_groups', 'groups', '所在组'],
        employment_status: ['employment_status', '就业状态'],
        employment_type: ['employment_type', 'employment_types', '就业类型'],
        industry: ['industry', 'industries', '就业行业'],
        subsidy_type: ['subsidy_type', 'flex_subsidy_type', '灵活就业补贴类型'],
        apply_status: ['apply_status', '申报状态', '补贴申报状态']
    };

    var currentTab = 'employment';
    var state = {
        employment: { page: 1, loaded: false, total: 0 },
        subsidy: { page: 1, loaded: false, total: 0 }
    };
    var DICT = {};          // { name: [{value,label}] }
    var rowsByTab = { employment: {}, subsidy: {} };

    // ────────────────────────── 基础工具 ──────────────────────────
    function byId(id) { return document.getElementById(id); }

    function toItems(raw) {
        // 兼容 ['A','B'] / [{value,label}] / {'A':'标签'} 三种返回形状
        var out = [];
        if (raw === null || raw === undefined) return out;
        if (Object.prototype.toString.call(raw) === '[object Array]') {
            for (var i = 0; i < raw.length; i++) {
                var it = raw[i];
                if (it === null || it === undefined) continue;
                if (typeof it === 'object') {
                    var v = it.value !== undefined ? it.value : (it.label !== undefined ? it.label : '');
                    var l = it.label !== undefined ? it.label : v;
                    if (v === '' && l === '') continue;
                    out.push({ value: String(v), label: String(l) });
                } else {
                    out.push({ value: String(it), label: String(it) });
                }
            }
            return out;
        }
        if (typeof raw === 'object') {
            for (var k in raw) {
                if (Object.prototype.hasOwnProperty.call(raw, k)) out.push({ value: k, label: String(raw[k]) });
            }
        }
        return out;
    }

    function pickDict(bag, name) {
        var keys = DICT_KEYS[name] || [name];
        for (var i = 0; i < keys.length; i++) {
            if (bag && bag[keys[i]] !== undefined && bag[keys[i]] !== null) {
                var items = toItems(bag[keys[i]]);
                if (items.length) return items;
            }
        }
        return [];
    }

    function fillSelect(el, items) {
        if (!el) return;
        var prev = el.value;
        var first = el.querySelector('option');
        var ph = first ? first.textContent : '';
        var html = ph ? '<option value="">' + esc(ph) + '</option>' : '';
        for (var i = 0; i < items.length; i++) {
            html += '<option value="' + escAttr(items[i].value) + '">' + esc(items[i].label) + '</option>';
        }
        el.innerHTML = html;
        if (prev) {
            el.value = prev;
            if (el.value !== prev) el.value = '';   // 旧值不在新选项里 → 回退占位，不静默改值
        }
    }

    // 统一请求入口：任何非 200 也返回可读 body（带 __status），供 409/403 分支使用
    function request(url, opts) {
        return fetch(url, opts || {}).then(function (resp) {
            if (resp.status === 401) { window.location.href = '/auth/login'; return null; }
            return resp.text().then(function (text) {
                var body = null;
                try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
                if (!body || typeof body !== 'object') {
                    body = { success: false, msg: '返回内容解析失败（HTTP ' + resp.status + '）' };
                }
                body.__status = resp.status;
                return body;
            });
        }, function () {
            return { success: false, msg: '网络异常，请检查连接后重试', __status: 0 };
        });
    }

    function report(body, okText) {
        if (!body) return;
        var st = body.__status;
        if (body.success) { showToast(okText || body.msg || '操作成功', 'success'); return; }
        var tone = (st === 409 || st === 400) ? 'warning' : 'danger';
        if (st === 403) tone = 'danger';
        showToast(body.msg || ('操作失败（HTTP ' + (st || 0) + '）'), tone);
    }

    function money(v) {
        if (v === null || v === undefined || v === '') return '—';
        var n = Number(v);
        return isNaN(n) ? String(v) : n.toFixed(2);
    }

    function badgeCls(v) {
        var s = String(v == null ? '' : v);
        for (var i = 0; i < BADGE.length; i++) if (BADGE[i].re.test(s)) return BADGE[i].cls;
        return 'muted';
    }

    function isReject(v) { return /未通过|不通过|驳回|不予/.test(String(v == null ? '' : v)); }

    function formQuery(form, tab, page) {
        var qs = new URLSearchParams();
        qs.set('tab', tab);
        qs.set('page', String(page));
        qs.set('limit', String(PAGE_SIZE));
        if (form) {
            var fd = new FormData(form);
            fd.forEach(function (v, k) {
                if (v !== null && v !== undefined && String(v) !== '') qs.set(k, String(v));
            });
        }
        return qs.toString();
    }

    // ────────────────────────── 状态渲染 ──────────────────────────
    function showState(tab, kind, text) {
        var body = byId(tab + 'Body');
        if (!body) return;
        var span = COLUMNS[tab].length + 2;
        var cls = (kind === 'error' || kind === 'forbidden') ? 'text-danger' : 'text-muted';
        var extra = (kind === 'error')
            ? ' <a href="javascript:void(0)" data-retry="1">点击重试</a>' : '';
        var icon = kind === 'loading'
            ? '<span class="spinner-border spinner-border-sm text-primary me-2" role="status"></span>' : '';
        body.innerHTML = '<tr><td colspan="' + span + '" class="text-center ' + cls + ' py-4">' +
            icon + text + extra + '</td></tr>';
    }

    function renderRows(tab, list) {
        var cols = COLUMNS[tab];
        var html = '';
        var start = (state[tab].page - 1) * PAGE_SIZE;
        rowsByTab[tab] = {};

        for (var i = 0; i < list.length; i++) {
            var item = list[i];
            var id = item.id;
            if (id !== undefined && id !== null) rowsByTab[tab][String(id)] = item;
            html += '<tr data-id="' + escAttr(id) + '" style="cursor:pointer">';
            html += '<td>' + (start + i + 1) + '</td>';
            for (var c = 0; c < cols.length; c++) {
                var def = cols[c];
                var val = item[def.key];
                if (def.money) {
                    html += '<td>' + esc(money(val)) + '</td>';
                } else if (def.badge) {
                    html += '<td><span class="sc-badge sc-badge--' + badgeCls(val) + '">' +
                        esc(val === null || val === undefined || val === '' ? '—' : val) + '</span></td>';
                } else if (def.ellipsis) {
                    var t = (val === null || val === undefined || val === '') ? '—' : val;
                    html += '<td class="sc-ellipsis" style="max-width:180px" title="' + escAttr(t) + '">' + esc(t) + '</td>';
                } else {
                    html += '<td>' + esc(val === null || val === undefined || val === '' ? '—' : val) + '</td>';
                }
            }
            // 067 §2.12：行内操作组，危险动作恒末位；2026-11-17 编辑钮撤除（编辑入口在详情底部）
            html += '<td><span class="sc-row-actions">' +
                (tab === 'subsidy'
                    ? '<button type="button" class="sc-btn sc-btn--text" data-act="transition" data-id="' + escAttr(id) + '">推进状态</button>' : '') +
                '<button type="button" class="sc-btn sc-btn--text is-danger" data-act="delete" data-id="' + escAttr(id) + '">删除</button>' +
                '</span></td>';
            html += '</tr>';
        }
        byId(tab + 'Body').innerHTML = html;
    }

    // ────────────────────────── 列表 ──────────────────────────
    function loadList(tab, page) {
        var form = byId(tab === 'employment' ? 'employmentSearchForm' : 'subsidySearchForm');
        var p = Math.max(1, page || 1);
        state[tab].page = p;
        showState(tab, 'loading', '加载中…');

        return request(API.list + '?' + formQuery(form, tab, p)).then(function (body) {
            if (!body) return;
            if (body.__status === 403) { showState(tab, 'forbidden', '无权限查看该列表（403）'); return; }
            if (!body.success) {
                showState(tab, 'error', esc(body.msg || '查询失败，请稍后重试'));
                if (body.__status !== 0) showToast(body.msg || '查询失败', 'danger');
                return;
            }
            var list = body.data || [];
            var total = body.total || 0;
            state[tab].loaded = true;
            state[tab].total = total;

            if (!list.length) {
                showState(tab, 'empty', tab === 'employment' ? '暂无就业登记记录' : '暂无补贴申报记录');
            } else {
                renderRows(tab, list);
            }
            byId(tab + 'Stat').textContent = '共 ' + total + ' 条 · 每页 ' + PAGE_SIZE + ' 条';
            var pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
            renderPagination('#' + tab + 'Pagination', pages, p, function (np) { loadList(tab, np); });
        });
    }

    // ────────────────────────── tab 切换 ──────────────────────────
    function switchTab(tab, fromHash) {
        if (tab !== 'employment' && tab !== 'subsidy') tab = 'employment';
        currentTab = tab;

        var links = document.querySelectorAll('#employmentTabs [data-tab]');
        for (var i = 0; i < links.length; i++) {
            var on = links[i].getAttribute('data-tab') === tab;
            links[i].classList.toggle('active', on);
            links[i].setAttribute('aria-selected', on ? 'true' : 'false');
        }
        var panels = document.querySelectorAll('[data-panel]');
        for (var j = 0; j < panels.length; j++) {
            panels[j].classList.toggle('d-none', panels[j].getAttribute('data-panel') !== tab);
        }
        var btn = byId('btnAddEmployment');
        if (btn) btn.innerHTML = '<i class="bi bi-plus-lg"></i> ' + (tab === 'employment' ? '新增登记' : '新增申报');

        if (!state[tab].loaded) loadList(tab, 1);
        if (!fromHash) {
            try { history.replaceState(null, '', '#tab=' + tab); } catch (e) { /* file:// 等环境忽略 */ }
        }
    }

    // ────────────────────────── 表单弹窗 ──────────────────────────
    function fillForm(form, data) {
        var els = form.querySelectorAll('[name]');
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            var name = el.getAttribute('name');
            if (name === 'tab') continue;
            var v = data && data[name] !== undefined && data[name] !== null ? data[name] : '';
            el.value = v;
        }
    }

    // 未通过原因只在「当前状态为未通过」时露出（新增固定待申报，故默认隐藏）
    function syncRejectWrap(status) {
        var wrap = byId('subsidyRejectWrap');
        if (!wrap) return;
        var show = isReject(status);
        wrap.classList.toggle('d-none', !show);
        if (!show) byId('s_reject_reason').value = '';
    }

    // 本地日期 YYYY-MM-DD（禁用 toISOString：UTC 与东八区差一天）
    function today() {
        var d = new Date();
        return d.getFullYear() + '-' +
            ('0' + (d.getMonth() + 1)).slice(-2) + '-' +
            ('0' + d.getDate()).slice(-2);
    }

    function openCreate() {
        var tab = currentTab;
        var form = byId(tab === 'employment' ? 'employmentForm' : 'subsidyForm');
        form.reset();
        fillForm(form, {});
        if (tab === 'employment') {
            byId('employmentId').value = '0';
            byId('employmentFormTitle').textContent = '新增就业登记';
            byId('e_update_date').value = today();     // 状态更新日期默认今天（新建即一次状态登记）
        } else {
            byId('subsidyId').value = '0';
            byId('subsidyFormTitle').textContent = '新增补贴申报';
            byId('s_year').value = String(new Date().getFullYear());
            byId('s_apply_date').value = today();
            syncRejectWrap('');                        // 新建固定「待申报」
        }
        getModal(tab === 'employment' ? 'employmentModal' : 'subsidyModal').show();
    }

    function openEdit(tab, id) {
        request(API.detail + encodeURIComponent(id) + '?tab=' + tab).then(function (body) {
            if (!body) return;
            if (!body.success) { report(body); return; }
            var data = body.data || {};
            if (tab === 'employment') {
                var f1 = byId('employmentForm');
                f1.reset();
                fillForm(f1, data);
                byId('employmentId').value = String(id);
                byId('employmentFormTitle').textContent = '编辑就业登记 · ' + (data.name || '');
                getModal('employmentModal').show();
            } else {
                var f2 = byId('subsidyForm');
                f2.reset();
                fillForm(f2, data);
                byId('subsidyId').value = String(id);
                byId('subsidyFormTitle').textContent = '编辑补贴申报 · ' + (data.name || '');
                syncRejectWrap(data.apply_status);     // 已有记录按当前状态决定是否露出未通过原因
                getModal('subsidyModal').show();
            }
        });
    }

    // 2026-11-17 Master–Detail：行点击 → 只读详情弹窗（复用 detail 接口，双 tab 分流标题）
    function openDetail(tab, id) {
        request(API.detail + encodeURIComponent(id) + '?tab=' + tab).then(function (body) {
            if (!body) return;
            if (!body.success) { report(body); return; }
            var data = body.data || {};
            var shortRows = [], longRows = [];
            Object.keys(data).forEach(function (key) {
                if (key === 'id' || key === 'tab') return;
                var label = FIELD_LABELS[tab][key] || key;
                if (key === 'remark' || key === 'reject_reason') longRows.push([label, data[key]]);
                else shortRows.push([label, data[key]]);
            });
            var html = window.SC.renderDetailCards(shortRows, longRows);
            byId('empDetailBody').innerHTML = html;
            byId('empDetailTitle').textContent = (data.name || '记录') + ' · ' + (tab === 'employment' ? '就业登记详情' : '补贴申报详情');
            byId('empDetailBtns').innerHTML =
                '<button type="button" class="sc-btn sc-btn--secondary" data-bs-dismiss="modal">关闭</button>' +
                '<button type="button" class="sc-btn sc-btn--primary" data-emp-edit="' + escAttr(id) + '" data-emp-tab="' + tab + '"><i class="bi bi-pencil"></i> 编辑</button>';
            getModal('empDetailModal').show();
        });
    }
    // 字段中文标签（detail 接口 key → 展示名；缺失 key 原样展示 key）
    var FIELD_LABELS = {
        employment: { name: '姓名', id_card: '身份证号', gender: '性别', village_group: '所在组', phone: '联系电话',
            employment_status: '就业状态', employer: '用工单位', job_title: '岗位', monthly_income: '月收入（元）', remark: '备注' },
        subsidy: { name: '姓名', id_card: '身份证号', gender: '性别', village_group: '所在组', phone: '联系电话',
            apply_year: '申报年度', subsidy_type: '补贴类型', apply_status: '申报状态', reject_reason: '未通过原因', amount: '金额（元）', remark: '备注' }
    };

    // 后端强校验项前置拦截，避免一次无意义往返（错误文案与后端保持一致口径）
    function validateForm(tab, form) {
        var v = function (name) {
            var el = form.querySelector('[name="' + name + '"]');
            return el ? String(el.value || '').trim() : '';
        };
        if (!v('name')) { showToast('姓名不能为空', 'warning'); return false; }
        // id_card 是防重唯一键的入参：新建时若把打码回显值（含 *）当新值提交，后端会判「格式不正确」
        var idEl = form.querySelector('[name="id"]');
        var isNew = !idEl || !idEl.value || idEl.value === '0';
        if (isNew && v('id_card').indexOf('*') >= 0) {
            showToast('新建请填写完整身份证号（打码值只用于回显，不能作为新记录）', 'warning');
            return false;
        }
        if (tab === 'employment') {
            if (!v('employment_status')) { showToast('请选择就业状态', 'warning'); return false; }
            return true;
        }
        if (!v('apply_year')) { showToast('申报年度不能为空', 'warning'); return false; }
        var year = parseInt(v('apply_year'), 10);
        if (isNaN(year) || year < 2000 || year > 2100) {
            showToast('申报年度应在 2000~2100 之间', 'warning'); return false;
        }
        if (!v('subsidy_type')) { showToast('请选择补贴类型', 'warning'); return false; }
        return true;
    }

    function saveForm(tab, form) {
        if (!validateForm(tab, form)) return;
        var fd = new FormData(form);
        fd.set('tab', tab);
        if (!fd.get('id')) fd.set('id', '0');
        return request(API.save, { method: 'POST', body: fd }).then(function (body) {
            if (!body) return;
            report(body, body.success ? (body.msg || '保存成功') : '');
            if (body.success) {
                getModal(tab === 'employment' ? 'employmentModal' : 'subsidyModal').hide();
                state[tab].loaded = false;
                loadList(tab, tab === currentTab ? state[tab].page : 1);
            }
        });
    }

    function deleteRow(tab, id) {
        if (!confirm('确定删除该条记录吗？删除后不可恢复。')) return;
        request(API.del + encodeURIComponent(id) + '?tab=' + tab, { method: 'POST' }).then(function (body) {
            if (!body) return;
            report(body, body.success ? (body.msg || '删除成功') : '');
            if (body.success) {
                // 删掉本页最后一条时回退一页
                var remain = (state[tab].total - 1) - (state[tab].page - 1) * PAGE_SIZE;
                var next = remain > 0 ? state[tab].page : Math.max(1, state[tab].page - 1);
                loadList(tab, next);
            }
        });
    }

    // ────────────────────────── 推进状态 ──────────────────────────
    function openTransition(id) {
        var item = rowsByTab.subsidy[String(id)] || {};
        var cur = item.apply_status || '';
        byId('transitionId').value = String(id);
        byId('transitionName').value = item.name || '';
        byId('transitionCurrent').value = cur || '—';

        var all = [];
        var statusDict = DICT.apply_status || [];
        for (var i = 0; i < statusDict.length; i++) all.push(statusDict[i].value);
        if (!all.length) all = STATUS_FALLBACK.slice();

        var recommended = FLOW[cur] || [];
        var rest = [];
        for (var k = 0; k < all.length; k++) {
            var v = all[k];
            if (v === cur) continue;
            if (recommended.indexOf(v) >= 0) continue;
            rest.push(v);
        }
        var html = '<option value="">请选择</option>';
        if (recommended.length) {
            html += '<optgroup label="推荐流转">';
            for (var a = 0; a < recommended.length; a++) {
                html += '<option value="' + escAttr(recommended[a]) + '">' + esc(recommended[a]) + '</option>';
            }
            html += '</optgroup>';
        }
        if (rest.length) {
            html += '<optgroup label="' + (recommended.length ? '其他状态（可能被流程拒绝）' : '可流转状态') + '">';
            for (var b = 0; b < rest.length; b++) {
                html += '<option value="' + escAttr(rest[b]) + '">' + esc(rest[b]) + '</option>';
            }
            html += '</optgroup>';
        }
        var sel = byId('transitionTo');
        sel.innerHTML = html;
        sel.value = '';
        clearTransitionError();
        getModal('transitionModal').show();
    }

    // R-103-03：409/失败原因必须渲染在弹窗内（toast 会被打开中的 modal backdrop
    // 遮住——toast 容器 z-index 低于 Bootstrap 弹窗层，实测「弹窗保留但无任何提示」）。
    function showTransitionError(msg) {
        var el = byId('transitionError');
        if (!el) return;
        el.textContent = msg || '状态流转不合法，请重新选择';
        el.hidden = false;
    }

    function clearTransitionError() {
        var el = byId('transitionError');
        if (el) { el.textContent = ''; el.hidden = true; }
    }

    function submitTransition(form) {
        var fd = new FormData(form);
        fd.set('tab', 'subsidy');   // 冗余字段：后端可忽略（不改契约语义，仅防后端按表分流时缺参）
        return request(API.transition, { method: 'POST', body: fd }).then(function (body) {
            if (!body) return;
            if (body.__status === 409) {
                // 非法迁移：弹窗内错误行 + toast 双通道展示，弹窗保留让用户改选
                showTransitionError(body.msg);
                showToast(body.msg || '状态流转不合法，请重新选择', 'warning');
                return;
            }
            if (!body.success) showTransitionError(body.msg);
            report(body, body.success ? (body.msg || '状态已推进') : '');
            if (body.success) {
                getModal('transitionModal').hide();
                loadList('subsidy', state.subsidy.page);
            }
        });
    }

    // ────────────────────────── 导出 ──────────────────────────
    function exportCurrent() {
        var tab = currentTab;
        var form = byId(tab === 'employment' ? 'employmentSearchForm' : 'subsidySearchForm');
        var qs = new URLSearchParams();
        qs.set('tab', tab);
        if (form) {
            var fd = new FormData(form);
            fd.forEach(function (v, k) {
                if (v !== null && v !== undefined && String(v) !== '') qs.set(k, String(v));
            });
        }
        window.location.href = API.export + '?' + qs.toString();
    }

    // ────────────────────────── 选项加载 ──────────────────────────
    function applyOptions(data) {
        var bag = data || {};
        var dictBag = bag.dicts || bag.dictionaries || bag;
        var groups = pickDict(dictBag, 'village_group');
        if (!groups.length) groups = pickDict(bag, 'village_group');
        DICT.village_group = groups;
        DICT.employment_status = pickDict(dictBag, 'employment_status');
        DICT.employment_type = pickDict(dictBag, 'employment_type');
        DICT.industry = pickDict(dictBag, 'industry');
        DICT.subsidy_type = pickDict(dictBag, 'subsidy_type');
        DICT.apply_status = pickDict(dictBag, 'apply_status');

        var sels = document.querySelectorAll('[data-dict]');
        for (var i = 0; i < sels.length; i++) {
            var name = sels[i].getAttribute('data-dict');
            fillSelect(sels[i], DICT[name] || []);
        }
        fillYearSelects();
    }

    // 申报年度无字典源（后端 options 只给 5 个字典），前端按当前年份倒推 6 年生成筛选下拉；
    // 表单里是 number 输入，编辑历史年度不受此列表限制。
    function fillYearSelects() {
        var els = document.querySelectorAll('[data-year]');
        if (!els.length) return;
        var y = new Date().getFullYear();
        var items = [];
        for (var i = 0; i < 6; i++) items.push({ value: String(y - i), label: (y - i) + ' 年' });
        for (var k = 0; k < els.length; k++) fillSelect(els[k], items);
    }

    function loadOptions() {
        return request(API.options).then(function (body) {
            if (!body || !body.success) {
                showToast((body && body.msg) || '选项加载失败，枚举下拉暂不可用', 'warning');
                return;
            }
            applyOptions(body.data || {});
        });
    }

    // ────────────────────────── 事件绑定（仅页面内元素） ──────────────────────────
    function bind() {
        // tab
        var tabs = document.querySelectorAll('#employmentTabs [data-tab]');
        for (var i = 0; i < tabs.length; i++) {
            tabs[i].addEventListener('click', function () {
                switchTab(this.getAttribute('data-tab'));
            });
        }

        var f1 = byId('employmentSearchForm');
        var f2 = byId('subsidySearchForm');
        if (f1) f1.addEventListener('submit', function (e) { e.preventDefault(); loadList('employment', 1); });
        if (f2) f2.addEventListener('submit', function (e) { e.preventDefault(); loadList('subsidy', 1); });

        // 行内操作（委托到各自 tbody，元素级监听随 SPA 换页回收）
        ['employment', 'subsidy'].forEach(function (tab) {
            var body = byId(tab + 'Body');
            if (!body) return;
            body.addEventListener('click', function (e) {
                var retry = e.target.closest('[data-retry]');
                if (retry) { loadList(tab, state[tab].page); return; }
                // 2026-11-17 Master–Detail：按钮命中走按钮语义，否则整行点击开详情
                var btn = e.target.closest('[data-act]');
                if (btn) {
                    var act = btn.getAttribute('data-act');
                    var id = btn.getAttribute('data-id');
                    if (act === 'delete') deleteRow(tab, id);
                    else if (act === 'transition') openTransition(id);
                    return;
                }
                var tr = e.target.closest('tr[data-id]');
                if (tr) openDetail(tab, tr.getAttribute('data-id'));
            });
        });

        // 表单提交
        var ef = byId('employmentForm');
        var sf = byId('subsidyForm');
        if (ef) ef.addEventListener('submit', function (e) { e.preventDefault(); saveForm('employment', ef); });
        if (sf) sf.addEventListener('submit', function (e) { e.preventDefault(); saveForm('subsidy', sf); });

        // 2026-11-17：详情弹窗底部「编辑」按钮（委托，内容重渲染仍生效）
        var eb = byId('empDetailBtns');
        if (eb) eb.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-emp-edit]');
            if (!btn) return;
            getModal('empDetailModal').hide();
            openEdit(btn.getAttribute('data-emp-tab'), btn.getAttribute('data-emp-edit'));
        });

        // 申报状态不在表单内编辑（只走「推进状态」接口），无状态联动监听
        var tf = byId('transitionForm');
        if (tf) tf.addEventListener('submit', function (e) { e.preventDefault(); submitTransition(tf); });

        var add = byId('btnAddEmployment');
        if (add) add.addEventListener('click', openCreate);
        var exp = byId('btnExportEmployment');
        if (exp) exp.addEventListener('click', exportCurrent);
    }

    // ────────────────────────── 启动 ──────────────────────────
    function boot() {
        bind();
        var hash = String(window.location.hash || '');
        var m = /tab=([a-z]+)/.exec(hash);
        var first = (m && (m[1] === 'subsidy' || m[1] === 'employment')) ? m[1] : 'employment';
        switchTab(first, true);
        loadOptions();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
