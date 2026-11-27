/* demand.js — 090A 民生诉求（一主表 + N 处理记录两级，086 信访同构）
 *
 * 契约（后端并行实现，本文件只消费）：
 *   GET  /demand/api/options              → {success, data:{诉求来源/诉求类别/诉求办理状态}}（value/label 对）
 *   GET  /demand/api/list?keyword=&status=&category=&source=&accept_from=&accept_to=&page=&limit=
 *                                         → {success, msg, data:[...], total}
 *   GET  /demand/api/detail/<id>          → {success, data:{...主表..., handles:[...]}}
 *   POST /demand/api/save                 (FormData: petitioner_name/contact/id_card/address/source/category/
 *                                          summary/accept_date/case_officer/remark/id[, status])
 *   POST /demand/api/delete/<id>          —— 仅管理员
 *   POST /demand/api/transition           (FormData: id, to_status[, close_date|remark]) —— 非法迁移 HTTP 409
 *   POST /demand/api/handle-save          (FormData: demand_id, handle_date, handler, action)
 *   GET  /demand/api/export?...           —— 台账导出
 *
 * 状态口径（一页纸 v1.1）：办理状态不经表单编辑（save 里传 status 会被后端按状态机校验），
 *   只能走 transition；新增一律「待受理」（后端硬约束）。
 *
 * 约束：
 *   - IIFE + 只绑页面内元素（不绑 document），SPA 重放即重置，无全局符号污染。
 *   - CSRF 由 spa.js 包装 window.fetch 自动注入（不手写 X-CSRF-Token）。
 *   - 409/400：必须读 resp.status 再取 msg 展示，不得静默失败。
 *   - 正常/空/加载/错误/无权限 五态全部有明确文案。
 */
(function () {
    'use strict';

    var BASE = '/demand';
    var API = {
        options: BASE + '/api/options',
        list: BASE + '/api/list',
        detail: BASE + '/api/detail/',
        save: BASE + '/api/save',
        del: BASE + '/api/delete/',
        transition: BASE + '/api/transition',
        handleSave: BASE + '/api/handle-save',
        export: BASE + '/api/export'
    };
    var PAGE_SIZE = 20;

    // 键名 = 后端 _demand_to_dict 序列化键（models/demand.py 结构真源）
    var COLS = [
        { key: 'accept_date' },
        { key: 'source_label' },
        { key: 'category_label' },
        { key: 'petitioner_name' },
        { key: 'summary', ellipsis: true },
        { key: 'case_officer' },
        { key: 'status', badge: true },
        { key: 'close_date' }
    ];

    var BADGE = [
        { re: /已办结|办结|归档/, cls: 'success' },
        { re: /不予受理|退办|终止/, cls: 'danger' },
        { re: /待受理|已登记/, cls: 'info' }
    ];

    // 推荐流转：与后端 DEMAND_TRANSITIONS（routes/demand.py 唯一真源）逐条对齐（两跳）
    var FLOW = {
        '待受理': ['已办结', '不予受理'],
        '已办结': [],
        '不予受理': []
    };
    var STATUS_FALLBACK = ['待受理', '已办结', '不予受理'];

    var DICT_KEYS = {
        source: ['诉求来源', 'source', 'sources'],
        category: ['诉求类别', 'category', 'categories'],
        status: ['诉求办理状态', 'status', 'statuses']
    };

    var DICT = {};
    var state = { page: 1, total: 0 };
    var rowsById = {};
    var currentDetail = null;

    function byId(id) { return document.getElementById(id); }

    // 本地日期 YYYY-MM-DD。禁止用 toISOString().slice(0,10)：那是 UTC 日期，
    // 东八区每天 00:00–07:59 会返回前一天（跨年凌晨 00:05 会直接落到上一年）。
    function localToday() {
        var d = new Date();
        return d.getFullYear() + '-' +
            ('0' + (d.getMonth() + 1)).slice(-2) + '-' +
            ('0' + d.getDate()).slice(-2);
    }

    function d(v) {
        if (v === null || v === undefined || v === '') return '—';
        var s = String(v);
        return s.length > 10 && s.indexOf('T') > 0 ? s.slice(0, 10) : s;
    }

    function badgeCls(v) {
        var s = String(v == null ? '' : v);
        for (var i = 0; i < BADGE.length; i++) if (BADGE[i].re.test(s)) return BADGE[i].cls;
        return 'muted';
    }

    function toItems(raw) {
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
            if (el.value !== prev) el.value = '';
        }
    }

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
        if (body.success) { showToast(okText || body.msg || '操作成功', 'success'); return; }
        var tone = (body.__status === 409 || body.__status === 400) ? 'warning' : 'danger';
        showToast(body.msg || ('操作失败（HTTP ' + (body.__status || 0) + '）'), tone);
    }

    function queryString(form) {
        var qs = new URLSearchParams();
        if (form) {
            var fd = new FormData(form);
            fd.forEach(function (v, k) {
                if (v !== null && v !== undefined && String(v) !== '') qs.set(k, String(v));
            });
        }
        return qs.toString();
    }

    // ────────────────────────── 列表 ──────────────────────────
    function showState(kind, text) {
        var span = COLS.length + 2;
        var cls = (kind === 'error' || kind === 'forbidden') ? 'text-danger' : 'text-muted';
        var extra = kind === 'error' ? ' <a href="javascript:void(0)" data-retry="1">点击重试</a>' : '';
        var icon = kind === 'loading'
            ? '<span class="spinner-border spinner-border-sm text-primary me-2" role="status"></span>' : '';
        byId('tableBody').innerHTML = '<tr><td colspan="' + span + '" class="text-center ' + cls +
            ' py-4">' + icon + text + extra + '</td></tr>';
    }

    function renderRows(list) {
        var html = '';
        var start = (state.page - 1) * PAGE_SIZE;
        rowsById = {};
        for (var i = 0; i < list.length; i++) {
            var item = list[i];
            if (item.id !== undefined && item.id !== null) rowsById[String(item.id)] = item;
            html += '<tr data-id="' + escAttr(item.id) + '" style="cursor:pointer">';
            html += '<td>' + (start + i + 1) + '</td>';
            for (var c = 0; c < COLS.length; c++) {
                var def = COLS[c];
                var val = item[def.key];
                if (def.badge) {
                    html += '<td><span class="sc-badge sc-badge--' + badgeCls(val) + '">' + esc(d(val)) + '</span></td>';
                } else if (def.key.indexOf('date') >= 0) {
                    html += '<td>' + esc(d(val)) + '</td>';
                } else if (def.ellipsis) {
                    var text = esc(val === null || val === undefined || val === '' ? '—' : val);
                    html += '<td class="sc-ellipsis" style="max-width:220px" title="' + escAttr(val || '') + '">' + text + '</td>';
                } else {
                    html += '<td>' + esc(val === null || val === undefined || val === '' ? '—' : val) + '</td>';
                }
            }
            var id = escAttr(item.id);
            html += '<td><span class="sc-row-actions">' +
                '<button type="button" class="sc-btn sc-btn--text" data-act="transition" data-id="' + id + '">推进</button>' +
                '<button type="button" class="sc-btn sc-btn--text is-danger" data-act="delete" data-id="' + id + '">删除</button>' +
                '</span></td>';
            html += '</tr>';
        }
        byId('tableBody').innerHTML = html;
    }

    function loadList(page) {
        var p = Math.max(1, page || 1);
        state.page = p;
        showState('loading', '加载中…');
        var qs = queryString(byId('searchForm'));
        var url = API.list + '?' + (qs ? qs + '&' : '') + 'page=' + p + '&limit=' + PAGE_SIZE;
        byId('exportLink').setAttribute('href', API.export + '?' + qs);

        return request(url).then(function (body) {
            if (!body) return;
            if (body.__status === 403) { showState('forbidden', '无权限查看民生诉求台账（403）'); return; }
            if (!body.success) {
                showState('error', esc(body.msg || '查询失败，请稍后重试'));
                if (body.__status !== 0) showToast(body.msg || '查询失败', 'danger');
                return;
            }
            var list = body.data && body.data.items ? body.data.items : (body.data || []);
            state.total = (body.data && body.data.total) || body.total || 0;
            if (!list.length) showState('empty', '暂无民生诉求');
            else renderRows(list);
            byId('pageStat').textContent = '共 ' + state.total + ' 条 · 每页 ' + PAGE_SIZE + ' 条';
            var pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
            renderPagination('#pagination', pages, p, function (np) { loadList(np); });
        });
    }

    // ────────────────────────── 新增 / 编辑 ──────────────────────────
    function fillForm(form, data) {
        var els = form.querySelectorAll('[name]');
        for (var i = 0; i < els.length; i++) {
            var el = els[i];
            if (el.getAttribute('name') === 'id') continue;
            var name = el.getAttribute('name');
            var v = data && data[name] !== undefined && data[name] !== null ? data[name] : '';
            el.value = v;
        }
    }

    function openCreate() {
        var form = byId('saveForm');
        form.reset();
        fillForm(form, {});
        byId('recordId').value = '0';
        byId('formTitle').textContent = '新增民生诉求';
        byId('f_accept_date').value = localToday();
        byId('f_status_ro').value = '待受理';   // 只读展示：新增由后端固定为待受理
        getModal('formModal').show();
    }

    function openEdit(id) {
        request(API.detail + encodeURIComponent(id)).then(function (body) {
            if (!body) return;
            if (!body.success) { report(body); return; }
            var data = body.data || {};
            var form = byId('saveForm');
            form.reset();
            fillForm(form, data);
            byId('recordId').value = String(id);
            byId('formTitle').textContent = '编辑民生诉求 · ' + (data.petitioner_name || '');
            byId('f_status_ro').value = data.status || '';
            getModal('formModal').show();
        });
    }

    // ────────────────────────── 详情（2026-11-17 设计稿 v2：紧凑一屏式读写分离） ──────────────────────────
    // 结构真源 ui.md + code_artifact.html：单行头部 / 不予受理警示条 / 01基本(4列) / 02摘要 / 03处理记录 / 超薄底栏；
    // 处理记录录入收进右侧行内抽屉（dd-drawer）。全部渲染函数只写 DOM，不重建容器，事件绑定一次性完成（bind）。
    function ddField(label, value, opts) {
        opts = opts || {};
        var empty = value === null || value === undefined || String(value) === '';
        var cls = 'dd-field__value' + (empty ? ' dd-field__value--empty' : '')
            + (opts.num ? ' dd-field__value--num' : '') + (opts.strong && !empty ? ' dd-field__value--strong' : '');
        return '<div class="dd-field__item"><span class="dd-field__label">' + esc(label) + '</span>'
            + '<span class="' + cls + '">' + esc(empty ? '—' : value) + '</span></div>';
    }

    // 状态 → 徽章语义档（badgeCls 既有映射；圆点色随徽章 currentColor）
    function ddSyncChip(status) {
        var chip = byId('detailStatusChip');
        if (!status) { chip.hidden = true; return; }
        chip.hidden = false;
        chip.className = 'sc-badge sc-badge--' + badgeCls(status) + ' dd-header__chip';
        chip.innerHTML = '<span class="dd-header__dot"></span>' + esc(status);
    }

    // 不予受理警示条：仅「不予受理」展示（依据文案 = 备注，与列表推进时落库口径一致）
    function ddSyncBanner(data) {
        var banner = byId('ddRejectBanner');
        var text = byId('ddRejectText');
        var rejecting = /不予受理|退办|终止/.test(String(data.status || ''));
        banner.hidden = !rejecting;
        if (rejecting) {
            var basis = String(data.remark || '').trim() || '该事项按《信访工作条例》依法不予受理。';
            text.innerHTML = '<strong>不予受理依据：</strong>' + esc(basis);
        }
    }

    // 抽屉状态提示文案随状态切换（读写分离认知：不予受理=存档说明、办理中=进展、已办结=回访归档）
    function ddSyncDrawerTip(status) {
        var tip = byId('ddDrawerTip');
        if (!tip) return;
        var s = String(status || '待受理');
        var html;
        if (/不予受理|退办|终止/.test(s)) {
            html = '提示：当前状态为「<strong>' + esc(s) + '</strong>」，新增记录将作为书面告知或存档备查说明。';
        } else if (/已办结|归档/.test(s)) {
            html = '提示：当前状态为「<strong>' + esc(s) + '</strong>」，新增记录将作为回访归档记录。';
        } else {
            html = '提示：当前状态为「<strong>' + esc(s) + '</strong>」，请及时登记调解、核查或政策解释进展。';
        }
        tip.innerHTML = html;
    }

    function ddOpenDrawer() {
        byId('ddDrawerBackdrop').hidden = false;
        var h = byId('h_handler');
        if (h) h.focus();
    }

    function ddCloseDrawer() {
        byId('ddDrawerBackdrop').hidden = true;
    }

    function renderHandles(handles) {
        var list = handles || [];
        byId('ddHandleCount').textContent = String(list.length);
        var html = '';
        for (var i = 0; i < list.length; i++) {
            var h = list[i] || {};
            html += '<tr>' +
                '<td class="dd-table__date dd-table__num">' + esc(d(h.handle_date)) + '</td>' +
                '<td class="dd-table__handler">' + esc(d(h.handler)) + '</td>' +
                '<td class="dd-measure-cell">' + esc(d(h.action)) + '</td>' +
                '</tr>';
        }
        // 处理记录子表「只增不改」契约（routes/demand.py handle-save 注释），无删除端点 → 操作列不渲染删除钮
        byId('handleBody').innerHTML = html ||
            '<tr><td colspan="3"><div class="dd-empty"><div class="dd-empty__inner">' +
            '<i class="bi bi-file-earmark-text dd-empty__icon"></i>' +
            '<span class="dd-empty__text">暂无处理记录</span>' +
            '<button type="button" class="dd-empty__link" data-handle-add>+ 补录处理记录</button>' +
            '</div></div></td></tr>';
    }

    function openDetail(id) {
        request(API.detail + encodeURIComponent(id)).then(function (body) {
            if (!body) return;
            if (!body.success) { report(body); return; }
            var data = body.data || {};
            currentDetail = data;
            var handles = data.handles || [];

            // Tier 1：单行头部（姓名 + 状态徽章 + 分割线 + 元数据流）
            byId('detailName').textContent = data.petitioner_name || '民生诉求详情';
            ddSyncChip(data.status);
            byId('detailMeta').innerHTML = (data.accept_date
                ? '<span class="dd-meta-num">受理 ' + esc(d(data.accept_date)) + '</span>'
                : '<span>未登记受理日期</span>')
                + ' &nbsp;·&nbsp; <span>' + esc(data.source_label || '—') + '</span>'
                + ' &nbsp;·&nbsp; <span>' + esc(data.category_label || '—') + '</span>';

            // Tier 2：不予受理警示条（依据 = 备注）
            ddSyncBanner(data);

            // Tier 3-01：基本与受理信息（4 列 × 2 行）
            byId('ddBasicGrid').innerHTML =
                ddField('诉求人', data.petitioner_name, { strong: true }) +
                ddField('联系方式', data.contact, { num: true }) +
                ddField('身份证号', data.id_card, { num: true }) +
                ddField('住址 / 涉及地点', data.address) +
                ddField('受理日期', data.accept_date, { num: true }) +
                ddField('责任人', data.case_officer) +
                ddField('办结日期', data.close_date, { num: true }) +
                ddField('处理次数', handles.length + ' 次', { num: true, strong: true });

            // Tier 3-02：诉求内容摘要（备注/依据为弱化次行）
            byId('ddSummaryText').textContent = d(data.summary);
            var remarkEl = byId('ddRemarkText');
            var remark = String(data.remark || '').trim();
            remarkEl.hidden = !remark;
            remarkEl.textContent = remark ? '备注 / 依据：' + remark : '';

            // Tier 3-03：处理记录表 + 底栏同步时间
            renderHandles(handles);
            byId('ddSyncDate').textContent = data.accept_date ? ' · ' + data.accept_date : '';

            // 抽屉：绑定诉求单 + 默认今天 + 状态提示文案（reset 会清 hidden 值，须在 reset 之后再写）
            var hf = byId('handleForm');
            hf.reset();
            byId('handleDemandId').value = String(data.id !== undefined ? data.id : id);
            byId('h_date').value = localToday();
            ddSyncDrawerTip(data.status);
            ddCloseDrawer();

            // 2026-11-17 Master–Detail：编辑入口在头部（操作列已撤编辑钮）
            var editBtn = byId('detailEditBtn');
            if (editBtn) {
                editBtn.hidden = false;
                editBtn.onclick = function () { getModal('detailModal').hide(); openEdit(id); };
            }

            // 2026-11-17 设计稿 v2：头部「变更状态」→ 复用既有推进弹窗（状态机两跳真源不重造）
            var statusBtn = byId('ddStatusBtn');
            if (statusBtn) {
                statusBtn.onclick = function () {
                    getModal('detailModal').hide();
                    openTransition(id);
                };
            }

            getModal('detailModal').show();
        });
    }

    function saveHandle(form) {
        var fd = new FormData(form);
        if (!fd.get('demand_id')) {
            showToast('缺少诉求单 id，请重新打开详情', 'warning');
            return;
        }
        return request(API.handleSave, { method: 'POST', body: fd }).then(function (body) {
            if (!body) return;
            report(body, body.success ? (body.msg || '处理记录已保存') : '');
            if (body.success) {
                ddCloseDrawer();
                openDetail(fd.get('demand_id'));   // 重取详情，处理记录与处理次数同步刷新
            }
        });
    }

    // ────────────────────────── 推进状态（两跳） ──────────────────────────
    // 随迁字段：to_status=已办结 → 露出办结日期；to_status=不予受理 → 露出依据备注。
    // 其余状态显式清值 + 从 FormData 删除，绝不在其它迁移里夹带（后端虽忽略空串，前端不依赖该容错）。
    function syncTransitionFields(id) {
        var sel = byId('transitionTo');
        var closeWrap = byId('transitionCloseWrap');
        var rejectWrap = byId('transitionRejectWrap');
        if (!sel || !closeWrap || !rejectWrap) return;
        var dateEl = byId('t_close_date');
        var remarkEl = byId('t_remark');
        var to = sel.value;
        var closing = to === '已办结';
        var rejecting = to === '不予受理';
        closeWrap.classList.toggle('d-none', !closing);
        rejectWrap.classList.toggle('d-none', !rejecting);
        dateEl.required = closing;     // 隐藏态必须撤掉 required：display:none 的必填控件会让浏览器静默中止提交
        remarkEl.required = rejecting;
        if (!closing) dateEl.value = '';
        if (!rejecting) remarkEl.value = '';
        if (closing && !dateEl.value) dateEl.value = defaultCloseDate(id);
        // 若此前开过同一诉求单的详情，预填库内已有办结日期/依据，避免重复录入
        if (currentDetail && String(currentDetail.id) === String(id)) {
            if (closing && !dateEl.value && currentDetail.close_date) dateEl.value = String(currentDetail.close_date);
            if (rejecting && !remarkEl.value && currentDetail.remark) remarkEl.value = String(currentDetail.remark);
        }
    }

    // 默认办结日期：库内已填过则原样沿用（否则会把用户已落库的办结日期静默改成今天），
    // 未填过才取 max(今天, 受理日期) —— 后端要求 close_date >= accept_date（R-086-04 同款）。
    function defaultCloseDate(id) {
        var item = rowsById[String(id)] || {};
        if (item.close_date) return String(item.close_date);
        var today = localToday();
        var acc = String(item.accept_date || '');
        return (acc.length === 10 && acc > today) ? acc : today;
    }

    function openTransition(id) {
        var item = rowsById[String(id)] || {};
        var cur = item.status || '';
        byId('transitionId').value = String(id);
        byId('transitionName').value = item.petitioner_name || '';
        byId('transitionCurrent').value = cur || '—';

        var all = [];
        var dict = DICT.status || [];
        for (var i = 0; i < dict.length; i++) all.push(dict[i].value);
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
                var recLabel = dictLabel(dict, recommended[a]);
                html += '<option value="' + escAttr(recommended[a]) + '">' + esc(recLabel) + '</option>';
            }
            html += '</optgroup>';
        }
        if (rest.length) {
            html += '<optgroup label="' + (recommended.length ? '其他状态（可能被流程拒绝）' : '可流转状态') + '">';
            for (var b = 0; b < rest.length; b++) {
                html += '<option value="' + escAttr(rest[b]) + '">' + esc(dictLabel(dict, rest[b])) + '</option>';
            }
            html += '</optgroup>';
        }
        var sel = byId('transitionTo');
        sel.innerHTML = html;
        sel.value = '';
        clearTransitionError();
        syncTransitionFields(id);   // 每次打开都按当前选择重置（默认隐藏、清值）
        getModal('transitionModal').show();
    }

    function dictLabel(dict, value) {
        for (var i = 0; i < dict.length; i++) {
            if (String(dict[i].value) === String(value)) return dict[i].label;
        }
        return value;
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
        var to = String(fd.get('to_status') || '');
        // 随迁字段显式摘取：非对应迁移绝不夹带（不是靠后端忽略空串）
        if (to !== '已办结') fd.delete('close_date');
        if (to !== '不予受理') fd.delete('remark');
        return request(API.transition, { method: 'POST', body: fd }).then(function (body) {
            if (!body) return;
            if (body.__status === 409) {
                // 非法迁移：弹窗内错误行 + toast 双通道展示，弹窗保留让用户改选（绝不静默）
                showTransitionError(body.msg);
                showToast(body.msg || '状态流转不合法，请重新选择', 'warning');
                return;
            }
            if (!body.success) showTransitionError(body.msg);
            report(body, body.success ? (body.msg || '状态已推进') : '');
            if (body.success) {
                getModal('transitionModal').hide();
                loadList(state.page);
            }
        });
    }

    // ────────────────────────── 删除 / 导出 ──────────────────────────
    function deleteRow(id) {
        if (!confirm('确定删除该民生诉求吗？关联的处理记录会一并删除，不可恢复。')) return;
        request(API.del + encodeURIComponent(id), { method: 'POST' }).then(function (body) {
            if (!body) return;
            report(body, body.success ? (body.msg || '删除成功') : '');
            if (body.success) {
                var remain = (state.total - 1) - (state.page - 1) * PAGE_SIZE;
                loadList(remain > 0 ? state.page : Math.max(1, state.page - 1));
            }
        });
    }

    function exportLedger() {
        var qs = queryString(byId('searchForm'));
        window.location.href = API.export + (qs ? '?' + qs : '');
    }

    // ────────────────────────── 选项 ──────────────────────────
    function applyOptions(data) {
        var bag = data || {};
        DICT.source = pickDict(bag, 'source');
        DICT.category = pickDict(bag, 'category');
        DICT.status = pickDict(bag, 'status');

        var sels = document.querySelectorAll('[data-dict]');
        for (var i = 0; i < sels.length; i++) {
            var name = sels[i].getAttribute('data-dict');
            fillSelect(sels[i], DICT[name] || []);
        }
    }

    function loadOptions() {
        return request(API.options).then(function (body) {
            if (!body || !body.success) {
                showToast((body && body.msg) || '选项加载失败，字典下拉暂不可用', 'warning');
                return;
            }
            applyOptions(body.data || {});
        });
    }

    // ────────────────────────── 事件绑定（仅页面内元素） ──────────────────────────
    function bind() {
        var form = byId('searchForm');
        if (form) form.addEventListener('submit', function (e) { e.preventDefault(); loadList(1); });

        var reset = byId('resetButton');
        if (reset) reset.addEventListener('click', function () {
            var f = byId('searchForm');
            f.reset();
            var sels = f.querySelectorAll('select.sc-select');
            for (var i = 0; i < sels.length; i++) sels[i].selectedIndex = 0;
            loadList(1);
        });

        var body = byId('tableBody');
        if (body) body.addEventListener('click', function (e) {
            if (e.target.closest('[data-retry]')) { loadList(state.page); return; }
            // 2026-11-17 Master–Detail：按钮命中走按钮语义，否则整行点击开详情
            var btn = e.target.closest('[data-act]');
            if (btn) {
                var act = btn.getAttribute('data-act');
                var id = btn.getAttribute('data-id');
                if (act === 'delete') deleteRow(id);
                else if (act === 'transition') openTransition(id);
                return;
            }
            var tr = e.target.closest('tr[data-id]');
            if (tr) openDetail(tr.getAttribute('data-id'));
        });

        var add = byId('addButton');
        if (add) add.addEventListener('click', openCreate);

        var exp = byId('exportLink');
        if (exp) exp.addEventListener('click', function (e) { e.preventDefault(); exportLedger(); });

        var saveForm = byId('saveForm');
        if (saveForm) saveForm.addEventListener('submit', function (e) {
            e.preventDefault();
            // 下拉为必填，但被 customSelect 隐藏后原生校验会静默中止提交，故前置校验给出可读文案
            if (!String(byId('f_source').value || '').trim()) { showToast('请选择诉求来源', 'warning'); return; }
            if (!String(byId('f_category').value || '').trim()) { showToast('请选择诉求类别', 'warning'); return; }
            var fd = new FormData(saveForm);
            if (!fd.get('id')) fd.set('id', '0');
            request(API.save, { method: 'POST', body: fd }).then(function (r) {
                if (!r) return;
                report(r, r.success ? (r.msg || '保存成功') : '');
                if (r.success) {
                    getModal('formModal').hide();
                    loadList(state.page);
                }
            });
        });

        var hf = byId('handleForm');
        if (hf) hf.addEventListener('submit', function (e) { e.preventDefault(); saveHandle(hf); });

        // ── 详情弹窗 v2 交互（2026-11-17）：抽屉开关 / 快捷标签 / 记录删除 / 补录链接 ──
        // 委托绑定一次完成，渲染函数只替换内部 DOM，不重建容器 → 绑定永不失效（第十六节教训的对立面验证）。
        var addBtn1 = byId('ddAddHandleBtn');
        if (addBtn1) addBtn1.addEventListener('click', ddOpenDrawer);
        var addBtn2 = byId('ddAddHandleBtn2');
        if (addBtn2) addBtn2.addEventListener('click', ddOpenDrawer);
        var dclose1 = byId('ddDrawerClose1');
        if (dclose1) dclose1.addEventListener('click', ddCloseDrawer);
        var dclose2 = byId('ddDrawerClose2');
        if (dclose2) dclose2.addEventListener('click', ddCloseDrawer);

        // 快捷填充标签：向处理措施追加标准话术前缀（设计稿 §5-3）
        var chips = byId('handleForm').querySelector('.dd-drawer__chips');
        if (chips) chips.addEventListener('click', function (e) {
            var chip = e.target.closest('[data-chip]');
            if (!chip) return;
            var ta = byId('h_action');
            var prefix = chip.getAttribute('data-chip');
            var cur = String(ta.value || '').trim();
            ta.value = cur ? cur + '\n' + prefix : prefix;
            ta.focus();
        });

        // 处理记录空态补录链接（tbody 委托，重渲染后仍生效）；删除无后端端点（子表只增不改契约），不提供
        var hb = byId('handleBody');
        if (hb) hb.addEventListener('click', function (e) {
            var addLink = e.target.closest('[data-handle-add]');
            if (addLink) { ddOpenDrawer(); return; }
        });

        var tf = byId('transitionForm');
        if (tf) tf.addEventListener('submit', function (e) { e.preventDefault(); submitTransition(tf); });

        // 目标状态下拉联动随迁字段（customSelect 会把原生 select 的 change 转发过来）
        var tsel = byId('transitionTo');
        if (tsel) tsel.addEventListener('change', function () {
            clearTransitionError();
            syncTransitionFields(byId('transitionId').value);
        });
    }

    function boot() {
        bind();
        loadList(1);
        loadOptions();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
