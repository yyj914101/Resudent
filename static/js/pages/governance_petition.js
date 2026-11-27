/* governance_petition.js — 086 信访维稳（一主表 + N 办理记录两级）
 *
 * 契约（后端并行实现，本文件只消费）：
 *   GET  /governance/petition/api/options          → {success, data:{dicts:{信访来源/信访诉求类别/信访办理状态/信访满意度}}}
 *   GET  /governance/petition/api/list?keyword=&status=&category=&source=&page=&limit=
 *                                                  → {success, msg, data:[...], total}
 *   GET  /governance/petition/api/detail/<id>      → {success, data:{...主表..., handles:[...]}}
 *   POST /governance/petition/api/save             (FormData: petition_no/petitioner_name/contact/id_card/
 *                                                    address/source/category/summary/accept_date/due_date/
 *                                                    close_date/case_officer/satisfaction/reply/remark/id)
 *   POST /governance/petition/api/delete/<id>
 *   POST /governance/petition/api/transition       (FormData: id, to_status)  —— 非法迁移返回 HTTP 409
 *   POST /governance/petition/api/handle-save      (FormData: petition_id, handle_date, handler, action, result, next_plan)
 *   GET  /governance/petition/api/export?...       —— 台账导出
 *
 * 状态口径：办理状态不经表单编辑（save 里传 status 会被后端按状态机校验），只能走 transition；
 *           新增一律「待受理」（后端硬约束）。
 *
 * 约束：
 *   - IIFE + 只绑页面内元素（不绑 document），SPA 重放即重置，无全局符号污染。
 *   - CSRF 由 spa.js 包装 window.fetch 自动注入（不手写 X-CSRF-Token）。
 *   - 409/400：必须读 resp.status 再取 msg 展示，不得静默失败。
 *   - 正常/空/加载/错误/无权限 五态全部有明确文案。
 */
(function () {
    'use strict';

    var BASE = '/governance/petition';
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

    // 键名 = 后端 _petition_to_dict 序列化键（models/governance_petition.py 结构真源）
    // 列结构对齐主理人拍板：无事项编号（后端 keyword 搜索仍覆盖存量编号字段）
    var COLS = [
        { key: 'petitioner_name' },
        { key: 'category' },
        { key: 'summary', ellipsis: true },
        { key: 'accept_date', num: true },
        { key: 'status', badge: true }
    ];

    var BADGE = [
        { re: /已办结|已归档|办结|息访/, cls: 'success' },
        { re: /不予受理|退办|终止/, cls: 'danger' },
        { re: /办理中|受理中|待办/, cls: 'warning' },
        { re: /待受理|已受理|已登记/, cls: 'info' }
    ];

    // 推荐流转：与后端 PETITION_TRANSITIONS（routes/governance.py 唯一真源）逐条对齐
    var FLOW = {
        '待受理': ['办理中', '不予受理'],
        '办理中': ['已办结', '不予受理'],
        '已办结': [],
        '不予受理': []
    };
    var STATUS_FALLBACK = ['待受理', '办理中', '已办结', '不予受理'];

    var DICT_KEYS = {
        source: ['source', 'sources', '信访来源'],
        category: ['category', 'categories', '诉求类别', '信访诉求类别'],
        status: ['status', 'statuses', '办理状态', '信访办理状态'],
        satisfaction: ['satisfaction', 'satisfactions', '满意度', '信访满意度']
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
        var span = COLS.length + 1;   // 原型 7 列口径：COLS + 操作列（无序号列）
        var cls = (kind === 'error' || kind === 'forbidden') ? 'text-danger' : 'text-muted';
        var extra = kind === 'error' ? ' <a href="javascript:void(0)" data-retry="1">点击重试</a>' : '';
        var icon = kind === 'loading'
            ? '<span class="spinner-border spinner-border-sm text-primary me-2" role="status"></span>' : '';
        byId('tableBody').innerHTML = '<tr><td colspan="' + span + '" class="text-center ' + cls +
            ' py-4">' + icon + text + extra + '</td></tr>';
    }

    function renderRows(list) {
        var html = '';
        rowsById = {};
        for (var i = 0; i < list.length; i++) {
            var item = list[i];
            if (item.id !== undefined && item.id !== null) rowsById[String(item.id)] = item;
            html += '<tr data-id="' + escAttr(item.id) + '" style="cursor:pointer">';
            for (var c = 0; c < COLS.length; c++) {
                var def = COLS[c];
                var val = item[def.key];
                if (def.badge) {
                    html += '<td><span class="sc-badge sc-badge--' + badgeCls(val) + '">' + esc(d(val)) + '</span></td>';
                } else if (def.ellipsis) {
                    html += '<td class="sc-ellipsis" style="max-width:260px" title="' + escAttr(val || '') + '">' +
                        esc(val === null || val === undefined || val === '' ? '—' : val) + '</td>';
                } else if (def.num) {
                    html += '<td class="gp-num">' + esc(val === null || val === undefined || val === '' ? '—' : val) + '</td>';
                } else {
                    html += '<td>' + esc(val === null || val === undefined || val === '' ? '—' : val) + '</td>';
                }
            }
            // 操作列 = 原型口径「详情 / 删除」（推进入口收敛在详情头部「变更办理状态」）
            var id = escAttr(item.id);
            html += '<td><span class="sc-row-actions">' +
                '<button type="button" class="sc-btn sc-btn--text" data-act="detail" data-id="' + id + '">详情</button>' +
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
            if (body.__status === 403) { showState('forbidden', '无权限查看信访台账（403）'); return; }
            if (!body.success) {
                showState('error', esc(body.msg || '查询失败，请稍后重试'));
                if (body.__status !== 0) showToast(body.msg || '查询失败', 'danger');
                return;
            }
            var list = body.data || [];
            state.total = body.total || 0;
            if (!list.length) showState('empty', '暂无信访事项');
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

    // 编号 UI 已整体移除（主理人拍板）：新增态自动生成 XF-日期-时间（后端 petition_no 必填+唯一
    // 约束不变，hidden 提交，秒级时间戳单机场景唯一性足够，唯一索引 409 兜底）
    function genPetitionNo() {
        var n = new Date();
        var p = function (x) { return ('0' + x).slice(-2); };
        return 'XF-' + n.getFullYear() + p(n.getMonth() + 1) + p(n.getDate()) +
               '-' + p(n.getHours()) + p(n.getMinutes()) + p(n.getSeconds());
    }

    function openCreate() {
        var form = byId('saveForm');
        form.reset();
        fillForm(form, {});
        byId('recordId').value = '0';
        byId('formTitle').textContent = '新增信访事项';
        byId('f_petition_no').value = genPetitionNo();   // 自动编号 hidden 提交
        byId('f_accept_date').value = localToday();
        getModal('formModal').show();   // 新增由后端固定「待受理」（状态不经表单编辑）
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
            byId('formTitle').textContent = '编辑信访事项';
            getModal('formModal').show();   // 编号不可变：hidden 原值随 FormData 照常提交
        });
    }

    // ────────────────────────── 详情（主表 + 办理记录） ──────────────────────────
    function field(label, value, cls) {
        return '<div class="sc-detail__field">' +
            '<span class="sc-detail__label">' + esc(label) + '</span>' +
            '<span class="sc-detail__value' + (cls ? ' ' + cls : '') + '">' + esc(d(value)) + '</span></div>';
    }

    function fieldFull(label, value, cls) {
        return '<div class="sc-detail__field sc-detail__field--full">' +
            '<span class="sc-detail__label">' + esc(label) + '</span>' +
            '<span class="sc-detail__value' + (cls ? ' ' + cls : '') + '">' + esc(d(value)) + '</span></div>';
    }

    function gpField(label, value, opts) {
        opts = opts || {};
        var empty = value === null || value === undefined || value === '';
        var cls = 'gp-field__value';
        if (empty) cls += ' gp-field__value--empty';
        if (opts.num) cls += ' gp-num';
        if (opts.strong) cls += ' gp-field__value--strong';
        return '<div><span class="gp-field__label">' + esc(label) + '</span>' +
            '<span class="' + cls + '">' + esc(empty ? '未填写' : value) + '</span></div>';
    }

    function openDrawer() {
        var bd = byId('gpDrawerBackdrop');
        if (!bd) return;
        bd.hidden = false;
        var st = currentDetail && currentDetail.status ? String(currentDetail.status) : '';
        var tip = byId('gpDrawerTip');
        if (tip) {
            tip.innerHTML = /不予受理|终止|退办/.test(st)
                ? '提示：当前事项状态为「<strong>' + esc(st) + '</strong>」，本次记录仅作为存档备查留痕，不改变办理状态。'
                : '提示：新增记录将作为办理过程留痕，提交后实时更新办理次数。';
        }
    }

    function closeDrawer() {
        var bd = byId('gpDrawerBackdrop');
        if (bd) bd.hidden = true;
    }

    function renderHandles(handles) {
        var list = handles || [];
        var html = '';
        for (var i = 0; i < list.length; i++) {
            var h = list[i] || {};
            html += '<tr>' +
                '<td class="gp-table__date gp-num">' + esc(d(h.handle_date)) + '</td>' +
                '<td class="gp-table__handler">' + esc(d(h.handler)) + '</td>' +
                '<td>' + esc(d(h.action)) + '</td>' +
                '<td>' + esc(d(h.result)) + '</td>' +
                '<td>' + esc(d(h.next_plan)) + '</td>' +
                '</tr>';
        }
        if (!html) {
            html = '<tr><td colspan="5" class="gp-empty"><span class="gp-empty__inner">' +
                '<i class="bi bi-journal-text gp-empty__icon"></i>' +
                '<span class="gp-empty__text">暂无办理记录</span>' +
                '<button type="button" class="gp-empty__link" data-gp-open-drawer="1">+ 立即新增一条</button>' +
                '</span></td></tr>';
        }
        byId('handleBody').innerHTML = html;
        byId('handleCount').textContent = '共 ' + list.length + ' 条';
    }

    function openDetail(id) {
        request(API.detail + encodeURIComponent(id)).then(function (body) {
            if (!body) return;
            if (!body.success) { report(body); return; }
            var data = body.data || {};
            currentDetail = data;

            byId('detailName').textContent = data.petitioner_name || '信访事项详情';
            byId('detailMeta').innerHTML = '来源：' + esc(d(data.source)) +
                ' · 受理：<span class="gp-num">' + esc(d(data.accept_date)) + '</span>';
            var chip = byId('detailStatusChip');
            if (data.status) {
                chip.hidden = false;
                chip.className = 'sc-badge sc-badge--' + badgeCls(data.status) + ' gp-header__chip';
                chip.innerHTML = '<span class="gp-header__dot"></span>' + esc(data.status);
            } else {
                chip.hidden = true;
            }

            // 警示横幅已按主理人拍板移除（状态语义由头部徽章承载）；
            // 不予受理依据类备注统一走 02 卡备注副行展示（remarkEl 不排除拒绝态）

            // 01 全宽 4 列 × 2 行统一栅格
            var handlesLen = (data.handles || []).length;
            byId('gpBasicGrid').innerHTML =
                gpField('诉求类别', data.category, { strong: true }) +
                gpField('联系方式', data.contact, { num: true }) +
                gpField('身份证号', data.id_card, { num: true }) +
                gpField('办理期限', data.due_date, { num: true }) +
                gpField('住址/涉及地点', data.address) +
                gpField('承办人', data.case_officer) +
                gpField('办结日期 / 满意度', (data.close_date || '—') + ' · ' + (data.satisfaction || '—'), { num: true }) +
                gpField('办理次数', handlesLen + ' 次', { num: true, strong: true });

            // 02 诉求内容与答复摘要
            byId('gpSummaryText').textContent = data.summary || '暂无诉求内容摘要';
            var replyEl = byId('gpReplyText');
            if (replyEl) {
                if (data.reply) { replyEl.hidden = false; replyEl.textContent = '答复情况：' + data.reply; }
                else { replyEl.hidden = true; }
            }
            var remarkEl = byId('gpRemarkText');
            if (remarkEl) {
                if (data.remark) { remarkEl.hidden = false; remarkEl.textContent = '备注：' + data.remark; }
                else { remarkEl.hidden = true; }
            }

            renderHandles(data.handles);
            if (byId('gpSyncDate')) byId('gpSyncDate').textContent = '（' + localToday() + '）';

            // 头部按钮：编辑 & 变更办理状态
            var editBtn = byId('detailEditBtn');
            if (editBtn) {
                editBtn.hidden = false;
                editBtn.onclick = function () { getModal('detailModal').hide(); openEdit(id); };
            }
            var transBtn = byId('detailTransitionBtn');
            if (transBtn) {
                transBtn.hidden = false;
                // ADR-022 原型口径：点击「变更办理状态」在详情内滑出面板，确认后就地重渲染详情
                transBtn.onclick = function () { openTransition(id, true); };
            }

            // 办理记录表单绑定（drawer 内读写分离）
            var hf = byId('handleForm');
            hf.reset();
            byId('handlePetitionId').value = String(data.id !== undefined ? data.id : id);
            byId('h_date').value = localToday();
            closeDrawer();

            getModal('detailModal').show();
        });
    }

    function saveHandle(form) {
        var fd = new FormData(form);
        if (!fd.get('petition_id')) {
            showToast('缺少事项 id，请重新打开详情', 'warning');
            return;
        }
        return request(API.handleSave, { method: 'POST', body: fd }).then(function (body) {
            if (!body) return;
            report(body, body.success ? (body.msg || '办理记录已保存') : '');
            if (body.success) {
                closeDrawer();
                openDetail(fd.get('petition_id'));   // 重取详情，办理记录与办理次数同步刷新
            }
        });
    }

    // ────────────────────────── 推进状态 ──────────────────────────
    // 办结随迁字段：只在 to_status=已办结 时露出 + 置 required。
    // 隐藏时同步清空值并从 FormData 显式删除，绝不在其它迁移里夹带（后端虽把空串等价"未提交"，前端不依赖该容错）。
    function syncCloseFields(id) {
        var sel = byId('transitionTo');
        var wrap = byId('transitionCloseWrap');
        if (!sel || !wrap) return;
        var dateEl = byId('t_close_date');
        var replyEl = byId('t_reply');
        var on = sel.value === '已办结';
        wrap.classList.toggle('d-none', !on);
        dateEl.required = on;      // 隐藏态必须撤掉 required：display:none 的必填控件会让浏览器静默中止提交
        replyEl.required = on;
        if (!on) { dateEl.value = ''; replyEl.value = ''; return; }
        if (!dateEl.value) dateEl.value = defaultCloseDate(id);
        // 若此前开过同一事项的详情，顺手预填已是写过的答复，避免重复录入
        if (!replyEl.value && currentDetail && String(currentDetail.id) === String(id) && currentDetail.reply) {
            replyEl.value = currentDetail.reply;
        }
    }

    // 默认办结日期：库内已填过则原样沿用（否则会把用户已落库的办结日期静默改成今天），
    // 未填过才取 max(今天, 受理日期) —— 后端要求 close_date >= accept_date。
    function defaultCloseDate(id) {
        var item = rowsById[String(id)] || {};
        if (item.close_date) return String(item.close_date);
        var today = localToday();
        var acc = String(item.accept_date || '');
        return (acc.length === 10 && acc > today) ? acc : today;
    }

    function openTransition(id, fromDetail) {
        openTransition._fromDetail = !!fromDetail;
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
        syncCloseFields(id);        // 每次打开都按当前选择重置（默认隐藏、清值）
        openTransitionDrawer();     // ADR-022 原型口径：详情内滑出面板就地变更，不弹独立 modal、详情不关闭
    }

    function openTransitionDrawer() {
        var bd = byId('gpTransitionBackdrop');
        if (bd) bd.hidden = false;
    }

    function closeTransitionDrawer() {
        var bd = byId('gpTransitionBackdrop');
        if (bd) bd.hidden = true;
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

    function submitTransition(form, fromDetail) {
        fromDetail = fromDetail || openTransition._fromDetail;
        openTransition._fromDetail = false;
        var fd = new FormData(form);
        var id = fd.get('id');
        // 非办结迁移：显式摘掉随迁字段（不是靠后端忽略空串）
        if (String(fd.get('to_status') || '') !== '已办结') {
            fd.delete('close_date');
            fd.delete('reply');
        }
        return request(API.transition, { method: 'POST', body: fd }).then(function (body) {
            if (!body) return;
            if (body.__status === 409) {
                // 非法迁移：弹窗内错误行 + toast 双通道展示，弹窗保留让用户改选（绝不静默）
                showTransitionError(body.msg);
                showToast(body.msg || '状态流转不合法，请重新选择', 'warning');
                return;
            }
            if (!body.success) showTransitionError(body.msg);
            report(body, body.success ? (body.msg || '状态已变更') : '');
            if (body.success) {
                closeTransitionDrawer();
                loadList(state.page);
                // ADR-022 原型口径：变更后详情就地重渲染——徽章/横幅随新状态刷新，
                // 用户不回列表也能看到状态变化（从详情头部进入变更时）
                if (fromDetail) openDetail(id);
            }
        });
    }

    // ────────────────────────── 删除 / 导出 ──────────────────────────
    function deleteRow(id) {
        if (!confirm('确定删除该信访事项吗？关联的办理记录会一并删除，不可恢复。')) return;
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
        var dictBag = bag.dicts || bag.dictionaries || bag;
        DICT.source = pickDict(dictBag, 'source');
        DICT.category = pickDict(dictBag, 'category');
        DICT.status = pickDict(dictBag, 'status');
        DICT.satisfaction = pickDict(dictBag, 'satisfaction');

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
                else if (act === 'detail') openDetail(id);
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
            if (!String(byId('f_source').value || '').trim()) { showToast('请选择信访来源', 'warning'); return; }
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

        var tf = byId('transitionForm');
        if (tf) tf.addEventListener('submit', function (e) { e.preventDefault(); submitTransition(tf); });
        // 目标状态下拉联动办结随迁字段（customSelect 会把原生 select 的 change 转发过来）
        var tsel = byId('transitionTo');
        if (tsel) tsel.addEventListener('change', function () {
            clearTransitionError();
            syncCloseFields(byId('transitionId').value);
        });

        // 右侧行内抽屉交互：打开 / 关闭 / 快捷填充词条
        ['gpAddHandleBtn', 'gpAddHandleBtn2'].forEach(function (bid) {
            var b = byId(bid);
            if (b) b.addEventListener('click', openDrawer);
        });
        ['gpDrawerClose1', 'gpDrawerClose2'].forEach(function (bid) {
            var b = byId(bid);
            if (b) b.addEventListener('click', closeDrawer);
        });
        var bd = byId('gpDrawerBackdrop');
        if (bd) bd.addEventListener('click', function (e) {
            if (e.target === bd) closeDrawer();
        });
        var hb = byId('handleBody');
        if (hb) hb.addEventListener('click', function (e) {
            if (e.target.closest('[data-gp-open-drawer]')) openDrawer();
        });
        // 变更状态抽屉：打开/关闭/backdrop 点击关闭
        ['gpTransitionClose1', 'gpTransitionClose2'].forEach(function (bid) {
            var b = byId(bid);
            if (b) b.addEventListener('click', closeTransitionDrawer);
        });
        var tbd = byId('gpTransitionBackdrop');
        if (tbd) tbd.addEventListener('click', function (e) {
            if (e.target === tbd) closeTransitionDrawer();
        });
        var drawerEl = document.querySelector('#gpDrawerBackdrop .gp-drawer__chips');
        if (drawerEl) drawerEl.addEventListener('click', function (e) {
            var chipBtn = e.target.closest('button[data-chip]');
            if (!chipBtn) return;
            var ta = byId('h_action');
            if (!ta) return;
            var tag = chipBtn.getAttribute('data-chip') || '';
            if (ta.value.indexOf(tag) === -1) {
                ta.value = (tag + ' ' + ta.value).trim();
            }
            ta.focus();
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
