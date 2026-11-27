/* crud.js — 列表页 CRUD 泛型骨架
 *
 * 013 S5 收敛：把 16 个列表页重复的「取数 → 渲染行 → 分页」骨架收敛为一处。
 * 027 重构：两段式 API（init + bindEvent）+ formatter 库 + 操作列生成器（渲染层）。
 * 028 交互层：bindEvent 全量接管——刷新 / 跨页多选+底部批量条 / 双击行编辑（默认关）
 *           / 列显隐（localStorage per-page，F9）。批量端点按模块试点（ADR-016 归类）。
 *
 * 接口（向后兼容 013 单函数用法）：
 *
 *   crudTable.init(cfg)            创建表格实例，返回 { reload(page), state }
 *   crudTable.bindEvent(table)     挂事件委托（028 已灌入全量交互，含 rowActions）
 *   crudTable(cfg)                 = init + bindEvent 兼容糖（013 用法不变）
 *
 *   cfg.endpoint     必填  str    列表 API（如 url_for 渲染出的 '/x/api/list'）
 *   cfg.columns      必填  array  列定义（顺序即列序），每列其一：
 *                               { field: 'name' }                  直出
 *                               { field: 'name', esc: true }       HTML 转义
 *                               { field: '__index' } / {index:true} 序号（按页偏移）
 *                               { render: function(item, idx) }    自定义 HTML（最高优先级）
 *   —— 027 列定义扩展（formatter 库）——
 *                               { field, dict: '分类名' }           值→标签（dictStore）
 *                               { field, dict: {'0':'禁用',...} }   内联小字典（免登记）
 *                               { field, dict, tag: true, tagMap }  标签+徽章（色池/覆盖）
 *                               { field, tag: true, tagMap }        徽章（原值）
 *                               { field, switch: true }             只读 switch 样式（行内交互归后续）
 *                               { field, datetime: true }           日期时间格式化（formatDate）
 *                               { field, url: true|fn(item) }       链接列
 *   cfg.rowActions   选填  array  操作列生成器（自动追加最后一列）：
 *                               { name:'edit', onClick: fn(item) }                    自定编辑（页面弹窗）
 *                               { name:'delete', url: str|fn(item), confirm: str|false } 自动 confirm/CSRF/刷新
 *                               { icon, label, cls, onClick }                          自由按钮
 *   —— 028 交互层扩展 ——
 *   cfg.selection    选填  bool  跨页多选：自动注入行首复选框 + 表头全选；
 *                                选中集（key→item）翻页保持；底部条显示「已选 N 条」。
 *   cfg.batchActions 选填  array 批量按钮（出现在底部条）：
 *                               { label, url: str, params: {field,value,...}, confirm: str|false, cls, fn }
 *                               fn 直通；否则 POST url（params 合并 {ids:[...]}）→ toast + 清选 + reload。
 *   cfg.dblclickEdit 选填  bool  双击行触发 rowActions 中 name='edit' 的 fn（默认关，T4）。
 *   cfg.onRowClick   选填  fn(item, $tr)  单击行打开详情（Master–Detail 模态变体，2026-11-17 全站统一）；
 *                                行点击委托已避开复选框/按钮/链接/操作列，页面无需自行绑行事件。
 *   cfg.columnToggle 选填  bool  列显隐：表格上方下拉菜单（columns[i].title 为名），
 *                                localStorage 'crudCols:'+tid 持久化（F9）。
 *   cfg.batchBar     选填  str   底部条挂载容器，默认表格所在 .card（随 SPA 换页回收）。
 *   cfg.limit        选填  int    每页条数，默认 20
 *   cfg.searchForm   选填  str    搜索表单选择器，默认 '#searchForm'
 *   cfg.tableBody    选填  str    tbody 选择器，默认 '#tableBody'
 *   cfg.pagination   选填  str    分页容器选择器，默认 '#pagination'
 *   cfg.emptyColspan 选填  int    空态 colspan，默认 = 列数（含操作列）
 *   cfg.emptyText    选填  str    空态文案，默认 '暂无数据'
 *   cfg.loadingHTML  选填  str    取数中的加载占位 HTML（默认不显示）
 *   cfg.pagesOf      选填  fn(resp) -> int  总页数；默认取 resp.pages
 *   cfg.onLoaded     选填  fn(resp) 取数成功后回调
 *
 * 依赖：jQuery、app.js 的 esc()/escAttr()/renderPagination()/formatDate()/showToast()/dictStore。
 * 约束：事件委托 + 不阻断冒泡（016 教训：stopPropagation 会掐断 SPA 导航冒泡）；
 *       分页沿用 009 的 renderPagination（前端渲染）；页面脚本保持 IIFE（024 规范）。
 *       批量请求走 $.post，CSRF 由 spa.js ajaxSend 全局注入（不另造通道）。
 */
var crudTable = (function () {

    var TAG_COLORS = ['info', 'success', 'warning', 'danger']; // FastAdmin formatter.flag 色池同源

    var _instances = {}; // tbody selector -> 最新表格实例（SPA 重放覆盖即回收，防注册表无界增长）
    var _bound = false;

    // 028 文案（沿用 \u 转义约定）
    var T = {
        selected: '\u5df2\u9009',          // 已选
        rowsUnit: '\u6761',                // 条
        clearSel: '\u6e05\u9664',          // 清除
        refresh: '\u5237\u65b0',           // 刷新
        cols: '\u5217\u8bbe\u7f6e',        // 列设置
        selFirst: '\u8bf7\u5148\u52fe\u9009\u8bb0\u5f55', // 请先勾选记录
        batchConfirm: '\u786e\u5b9a\u8981\u6279\u91cf\u64cd\u4f5c\u5417\uff1f' // 确定要批量操作吗？
    };

    function truthy(v) {
        return v === true || v === 1 || v === '1' ||
            v === '\u662f' /* 是 */ || v === '\u542f\u7528' /* 启用 */ ||
            String(v).toLowerCase() === 'on' || String(v).toLowerCase() === 'true';
    }

    // 稳定色分配（值→色池；未命中 tagMap 时按字符串哈希，跨会话确定性）
    function tagColor(tagMap, v) {
        var key = String(v == null ? '' : v);
        if (tagMap && Object.prototype.hasOwnProperty.call(tagMap, key)) return tagMap[key];
        var h = 0;
        for (var i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 997;
        return TAG_COLORS[h % TAG_COLORS.length];
    }

    // dict 标签：内联映射优先，其次 dictStore（未加载/未命中返回 null → 原值兜底）
    function dictLabel(cfg, v) {
        var key = String(v == null ? '' : v);
        if (cfg.dict && typeof cfg.dict === 'object') {
            return Object.prototype.hasOwnProperty.call(cfg.dict, key) ? cfg.dict[key] : null;
        }
        if (cfg.dict && window.dictStore) {
            return window.dictStore.label(cfg.dict, v);
        }
        return null;
    }

    function buildCellRenderer(col) {
        if (col.render) return col.render;
        if (col.field === '__index' || col.index) {
            return function (item, rowIdx, start) { void item; return start + rowIdx + 1; };
        }
        if (col.switch) {
            // 027 只读样式；行内点击改值不属 028 范围（28 只做批量/多选/双击/列显隐）
            return function (item) {
                var on = truthy(item[col.field]);
                return '<div class="form-check form-switch d-inline-block mb-0">' +
                    '<input class="form-check-input" type="checkbox" role="switch" disabled' +
                    (on ? ' checked' : '') + '></div>';
            };
        }
        if (col.url) {
            return function (item) {
                var v = typeof col.url === 'function' ? col.url(item) : item[col.field];
                if (v === null || v === undefined || v === '') return '';
                return '<a href="' + escAttr(v) + '" target="_blank" rel="noopener">' + esc(v) + '</a>';
            };
        }
        if (col.datetime) {
            return function (item) {
                var v = item[col.field];
                if (v === null || v === undefined || v === '') return '';
                return formatDate(v); // ISO 才格式化，其余原样返回（既有单源）
            };
        }
        if (col.mask) {
            // 2026-11-02：敏感号段掩码（保留前 4 后 4，中间 ****；短号原样）
            return function (item) {
                var v = String(item[col.field] == null ? '' : item[col.field]);
                if (!v) return '';
                return v.length > 8 ? v.slice(0, 4) + '****' + v.slice(-4) : v;
            };
        }
        if (col.colormap) {
            // 2026-11-02：值→文字色映射（状态语义配色，如医保关联/核对），文字渲染不上胶囊
            return function (item) {
                var v = item[col.field];
                if (v === null || v === undefined || v === '') return '';
                var text = (col.dict ? (dictLabel(col, v) !== null && dictLabel(col, v) !== undefined ? dictLabel(col, v) : v) : v);
                var clr = col.colormap[v];
                return clr ? '<span style="color:' + clr + ';font-weight:500">' + esc(text) + '</span>' : esc(text);
            };
        }
        if (col.dict || col.tag) {
            return function (item) {
                var v = item[col.field];
                if (v === null || v === undefined) return '';
                var label = col.dict ? dictLabel(col, v) : null;
                var text = label !== null && label !== undefined ? label : v;
                if (col.tag) {
                    // 067：徽章换 components.css 通用件（TAG_COLORS 四档均有 sc-badge-- 变体；
                    // 唯一 tagMap 消费方为 medical_insurance，改动随 067 RESULT 声明）
                    return '<span class="sc-badge sc-badge--' + tagColor(col.tagMap, v) + '">' + esc(text) + '</span>';
                }
                return col.esc ? esc(text) : text;
            };
        }
        return function (item) {
            var v = item[col.field];
            if (v === null || v === undefined) return '';
            return col.esc ? esc(v) : v;
        };
    }

    // 028 列显隐持久化
    function readColHidden(tid) {
        try { return JSON.parse(localStorage.getItem('crudCols:' + tid)) || {}; } catch (e) { return {}; }
    }
    function writeColHidden(tid, hidden) {
        try { localStorage.setItem('crudCols:' + tid, JSON.stringify(hidden)); } catch (e) { /* 隐私模式降级 */ }
    }

    function init(cfg) {
        var limit = cfg.limit || 20;
        var $form = $(cfg.searchForm || '#searchForm');
        var $body = $(cfg.tableBody || '#tableBody');
        var state = { page: 1 };
        var reqId = 0; // R-07：请求序号，丢弃过期响应

        var tid = 'tbl@' + (cfg.tableBody || '#tableBody');

        // 028 交互层状态
        var selEnabled = !!cfg.selection;
        var selected = {};                                  // key -> item，翻页保持（T2）
        var dblclickEdit = !!cfg.dblclickEdit;              // T4：默认关
        var onRowClick = typeof cfg.onRowClick === 'function' ? cfg.onRowClick : null; // 行点击详情
        var hiddenCols = cfg.columnToggle ? readColHidden(tid) : null; // F9 {idx:1}
        var batchActions = (cfg.batchActions || []).map(function (b) {
            if (b.fn) return b;
            if (!b.url) return b; // 无处理器：只渲染不动作
            return $.extend({}, b, {
                fn: function () {
                    var keys = Object.keys(selected);
                    if (!keys.length) { showToast(T.selFirst, 'warning'); return; }
                    var msg = b.confirm === false ? null
                        : (typeof b.confirm === 'string' ? b.confirm : T.batchConfirm);
                    if (msg && !confirm(msg)) return;
                    // CSRF 由 spa.js ajaxSend 全局注入（既有通道，不另造）
                    $.post(b.url, $.extend({}, b.params || {}, { ids: keys }), function (resp) {
                        if (resp && resp.success) {
                            showToast(resp.msg || '\u64cd\u4f5c\u6210\u529f', 'success');
                            clearSelection();
                            reload();
                        } else if (resp) {
                            showToast(resp.msg || '\u64cd\u4f5c\u5931\u8d25', 'danger');
                        }
                    });
                }
            });
        });

        // 027 操作列：规范化为处理器集合（fn 由事件委托调用，闭包持有本表状态）
        var rowActions = (cfg.rowActions || []).map(function (a) {
            if (a.fn) return a;
            if (a.name === 'delete' && a.url) {
                return $.extend({}, a, {
                    fn: function (item) {
                        var msg = a.confirm === false ? null
                            : (typeof a.confirm === 'string' ? a.confirm
                                : (typeof a.confirm === 'function' ? a.confirm(item) : '\u786e\u5b9a\u8981\u5220\u9664\u5417\uff1f'));
                        if (msg && !confirm(msg)) return;
                        var url = typeof a.url === 'function' ? a.url(item) : a.url;
                        // CSRF 由 spa.js ajaxSend 全局注入 X-CSRF-Token（既有通道，不另造）
                        $.post(url, function (resp) {
                            if (resp && resp.success) {
                                showToast(resp.msg || '\u5220\u9664\u6210\u529f', 'success');
                                (a.onDone || function () { reload(); })(resp);
                            } else if (resp) {
                                showToast(resp.msg || '\u64cd\u4f5c\u5931\u8d25', 'danger');
                            }
                        });
                    }
                });
            }
            if (a.onClick) {
                return $.extend({}, a, { fn: function (item, $btn, e) { a.onClick(item, $btn, e); } });
            }
            return a; // 无处理器：只渲染不动作
        });

        // 027 渲染器编译一次
        var renderers = cfg.columns.map(buildCellRenderer);
        // 操作列追加在 columns 之后（渲染时单独输出 td）
        var hasActions = rowActions.length > 0;

        var mine = { rowActions: rowActions };
        _instances[tid] = mine; // SPA 重放覆盖旧实例（同选择器唯一活跃），旧实例闭包随之回收

        var needDict = cfg.columns.some(function (c) { return c.dict && typeof c.dict === 'string'; });
        var dictReady = !needDict || !window.dictStore;
        var queuedPage = null;

        var $table = $body.closest('table');
        var selOff = selEnabled ? 1 : 0;

        // ---- 028 表头注入（幂等重入：SPA 重放/重复 init 前先移除旧注入） ----
        $table.find('th.crud-sel-head').remove();
        if (selEnabled) {
            $table.find('thead tr').first().prepend(
                '<th class="crud-sel-head" style="width:34px"><input type="checkbox" class="form-check-input crud-checkall"'
                + ' data-crud-table="' + tid + '"></th>');
        }
        var $colMenu = null;
        if (cfg.columnToggle) {
            $table.closest('.table-responsive').parent().find('.crud-col-menu').remove();
            var menu = '<div class="crud-col-menu text-end mb-1" data-crud-table="' + tid + '"><div class="dropdown d-inline-block">'
                + '<button type="button" class="btn btn-sm btn-outline-secondary dropdown-toggle" data-bs-toggle="dropdown">'
                + '<i class="bi bi-layout-three-columns"></i> ' + T.cols + '</button>'
                + '<ul class="dropdown-menu">';
            $.each(cfg.columns, function (i, c) {
                if (!c.title) return;
                menu += '<li><label class="dropdown-item"><input type="checkbox" class="form-check-input me-1 crud-col-check"'
                    + ' data-crud-col="' + i + '"' + (hiddenCols[i] ? '' : ' checked') + '> ' + esc(c.title) + '</label></li>';
            });
            menu += '</ul></div></div>';
            $colMenu = $(menu);
            $table.closest('.table-responsive').before($colMenu);
        }

        // ---- 028 底部批量条（挂 .card 内：SPA 换页随容器回收，不残留 body） ----
        var $bar = $();
        if (selEnabled) {
            var host = $(cfg.batchBar || '');
            if (!host.length) host = $table.closest('.card');
            if (!host.length) host = $table.parent();
            var bar = '<div class="crud-bar" data-crud-table="' + tid + '"'
                // 2026-11-02：去 d-flex（BS display:flex!important 会压过隐藏 inline style，
                // 导致批量条 0 选中时常驻）；显隐改由 refreshBar 显式 flex/none
                + ' style="position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:1050;'
                + 'background:#fff;border:1px solid #dee2e6;border-radius:.5rem;'
                + 'box-shadow:0 .5rem 1rem rgba(0,0,0,.15);padding:.5rem .75rem;display:none;">'
                + '<span class="crud-bar-count sc-badge sc-badge--primary me-2">' + T.selected + ' 0 ' + T.rowsUnit + '</span>'
                + '<button type="button" class="btn btn-sm btn-outline-secondary me-2" data-crud-act="crud-clear">' + T.clearSel + '</button>'
                + '<button type="button" class="btn btn-sm btn-outline-secondary me-2" data-crud-act="crud-refresh"><i class="bi bi-arrow-clockwise"></i> ' + T.refresh + '</button>';
            $.each(batchActions, function (i, b) {
                bar += '<button type="button" class="btn btn-sm ' + (b.cls || 'btn-primary') + ' me-2"'
                    + ' data-crud-act="crud-batch" data-crud-action="' + i + '">' + esc(b.label || '') + '</button>';
            });
            bar += '</div>';
            $bar = $(bar).appendTo(host);
        }

        function clearSelection() {
            for (var k in selected) delete selected[k];
            refreshBar();
        }

        function refreshBar() {
            if (!$bar.length) return;
            var n = Object.keys(selected).length;
            $bar.find('.crud-bar-count').text(T.selected + ' ' + n + ' ' + T.rowsUnit);
            // 2026-11-02：d-flex 移除后显隐走显式 flex/none（BS !important 类不再干扰）
            $bar.css('display', n > 0 ? 'flex' : 'none');
            $bar.find('[data-crud-act="crud-batch"]').prop('disabled', n === 0);
        }

        function applyColVisibility() {
            if (!hiddenCols) return;
            var $heads = $table.find('thead th');
            var $rows = $body.find('tr');
            $.each(hiddenCols, function (i, off) {
                var idx = selOff + parseInt(i, 10);
                if (off) $heads.eq(idx).hide(); else $heads.eq(idx).show();
                $rows.each(function (k, tr) {
                    $(tr).find('td').eq(idx)[off ? 'hide' : 'show']();
                });
            });
        }

        function setCheckAllState() {
            var $all = $table.find('.crud-checkall');
            if (!$all.length) return;
            var keys = Object.keys(mine.itemsByKey || {});
            var n = 0;
            for (var i = 0; i < keys.length; i++) if (selected[keys[i]]) n++;
            $all.prop('checked', keys.length > 0 && n === keys.length);
        }

        function renderActions(key) {
            // 067：行内操作换 components.css §2.12（sc-btn--text 行内操作组，危险动作恒末位）；
            // 自定义 cls 兼容旧调用（btn-* 前缀一概忽略，delete 恒 is-danger）
            var html = '<div class="action-cell"><span class="sc-row-actions">';
            $.each(rowActions, function (i, a) {
                var cls = a.name === 'delete' ? 'is-danger' : '';
                var icon = a.icon || (a.name === 'delete' ? 'bi-trash' : (a.name === 'edit' ? 'bi-pencil' : ''));
                html += '<button type="button" class="sc-btn sc-btn--text ' + cls + '"'
                    + ' data-crud-act="rowact" data-crud-table="' + tid + '"'
                    + ' data-crud-id="' + escAttr(key) + '" data-crud-action="' + i + '">'
                    + (icon ? '<i class="bi ' + icon + '"></i>' : '') + (a.label || '') + '</button>';
            });
            return html + '</span></div>';
        }

        function doLoad(page) {
            state.page = page;
            var myId = ++reqId;
            var qs = ($form.length ? $form.serialize() : '');
            qs = (qs ? qs + '&' : '') + 'page=' + state.page + '&limit=' + limit;
            var url = cfg.endpoint + (cfg.endpoint.indexOf('?') >= 0 ? '&' : '?') + qs;

            if (cfg.loadingHTML) $body.html(cfg.loadingHTML);
            $.getJSON(url, function (resp) {
                if (myId !== reqId) return; // R-07：过期响应丢弃
                if (_instances[tid] !== mine) return; // 027：切页后实例已换代，迟到的旧响应不得污染新页
                if (!resp || !resp.success) return;
                var start = (state.page - 1) * limit;
                var html = '';
                var itemsByKey = {};
                $.each(resp.data || [], function (i, item) {
                    var key = String(item && item.id !== undefined ? item.id : 'row' + i);
                    itemsByKey[key] = item;
                    html += '<tr data-crud-key="' + escAttr(key) + '" data-crud-table="' + tid + '">';
                    if (selEnabled) {
                        var on = Object.prototype.hasOwnProperty.call(selected, key);
                        html += '<td class="crud-sel"><input type="checkbox" class="form-check-input crud-row-check"'
                            + ' data-crud-table="' + tid + '" value="' + escAttr(key) + '"' + (on ? ' checked' : '') + '></td>';
                    }
                    for (var c = 0; c < renderers.length; c++) {
                        html += '<td>' + renderers[c](item, i, start) + '</td>';
                    }
                    if (hasActions) html += '<td>' + renderActions(key) + '</td>';
                    html += '</tr>';
                });
                if (!html) {
                    var span = cfg.emptyColspan || (renderers.length + (hasActions ? 1 : 0) + selOff);
                    var emptyText = cfg.emptyText || '\u6682\u65e0\u6570\u636e';
                    html = '<tr><td colspan="' + span + '" class="text-center text-muted py-4">' + emptyText + '</td></tr>';
                }
                $body.html(html);
                mine.itemsByKey = itemsByKey;
                setCheckAllState();
                applyColVisibility();
                refreshBar();
                var pages = cfg.pagesOf ? cfg.pagesOf(resp) : resp.pages;
                renderPagination(cfg.pagination || '#pagination', pages, state.page, reload);
                if (cfg.onLoaded) cfg.onLoaded(resp);
            }).fail(function (xhr) {
                if (myId !== reqId) return;
                if (_instances[tid] !== mine) return; // 027：实例已换代，不再渲染失败态
                xhr.__handled = true; // R-08：表格内已提示，防全局 ajaxError 重复 toast
                $body.html('<tr><td colspan="' + (cfg.emptyColspan || (renderers.length + (hasActions ? 1 : 0) + selOff)) + '" class="text-center text-danger py-4">\u52a0\u8f7d\u5931\u8d25</td></tr>');
            });
        }

        function reload(page) {
            if (page !== undefined) state.page = page;
            if (!dictReady) {
                // 字典列首次渲染前等 bundle 就绪（失败也 resolve → 原值兜底，行为等价降级）
                queuedPage = state.page;
                dictReady = true;
                window.dictStore.ready(function () { doLoad(queuedPage); });
                return;
            }
            doLoad(state.page);
        }

        // 028：实例方法暴露给 bindEventOnce 的事件委托用
        mine.selected = selected;
        mine.itemsByKey = {};
        mine.batchActions = batchActions;
        mine.reload = reload;
        mine.applyCols = applyColVisibility;
        mine.hiddenCols = hiddenCols;
        mine.clearSelection = clearSelection;
        mine.refreshBar = refreshBar;
        mine.setCheckAllState = setCheckAllState;
        mine.dblclickEdit = dblclickEdit;
        mine.onRowClick = onRowClick;

        refreshBar();
        return { reload: reload, state: state };
    }

    function bindEventOnce() {
        if (_bound) return;
        _bound = true;

        // 016 教训：document 级委托，且不 stopPropagation/preventDefault——冒泡留给 SPA 导航
        $(document).on('click.crudTable', '[data-crud-act="rowact"]', function (e) {
            var $btn = $(this);
            var inst = _instances[$btn.attr('data-crud-table')];
            if (!inst) return;
            var idx = parseInt($btn.attr('data-crud-action'), 10);
            var def = (inst.rowActions || [])[idx];
            if (!def || !def.fn) return;
            var item = (inst.itemsByKey || {})[$btn.attr('data-crud-id')];
            def.fn(item, $btn, e);
        });

        // ---- 028：跨页多选 ----
        $(document).on('change.crudTable', '.crud-row-check', function () {
            var $box = $(this);
            var inst = _instances[$box.attr('data-crud-table')];
            if (!inst) return;
            var key = $box.val();
            var item = (inst.itemsByKey || {})[key];
            if ($box.prop('checked') && item !== undefined) inst.selected[key] = item;
            else delete inst.selected[key];
            inst.setCheckAllState();
            inst.refreshBar();
        });
        $(document).on('change.crudTable', '.crud-checkall', function () {
            var $all = $(this);
            var inst = _instances[$all.attr('data-crud-table')];
            if (!inst) return;
            var on = $all.prop('checked');
            $.each(inst.itemsByKey || {}, function (k, item) {
                if (on) inst.selected[k] = item; else delete inst.selected[k];
            });
            $all.closest('table').find('.crud-row-check').prop('checked', on);
            inst.refreshBar();
        });

        // ---- 028：底部条（清除 / 刷新 / 批量） ----
        $(document).on('click.crudTable', '[data-crud-act="crud-clear"]', function () {
            var inst = _instances[$(this).closest('.crud-bar').attr('data-crud-table')];
            if (!inst) return;
            inst.clearSelection();
            $('.crud-row-check').prop('checked', false);
            inst.setCheckAllState();
        });
        $(document).on('click.crudTable', '[data-crud-act="crud-refresh"]', function () {
            var inst = _instances[$(this).closest('.crud-bar').attr('data-crud-table')];
            if (inst && inst.reload) inst.reload();
        });
        $(document).on('click.crudTable', '[data-crud-act="crud-batch"]', function () {
            var $btn = $(this);
            var inst = _instances[$btn.closest('.crud-bar').attr('data-crud-table')];
            if (!inst) return;
            var idx = parseInt($btn.attr('data-crud-action'), 10);
            // 批量处理器由 init 闭包持有（依赖 selected/reload）
            var defs = inst.batchActions || [];
            var def = defs[idx];
            if (!def || !def.fn) return;
            def.fn();
        });

        // ---- 028：双击行编辑（T4，默认关；避开控件区域，不误触操作列） ----
        $(document).on('dblclick.crudTable', 'tr[data-crud-key]', function (e) {
            var $tr = $(this);
            if ($(e.target).closest('input, button, a, select, label, .action-cell, .crud-sel').length) return;
            var inst = _instances[$tr.attr('data-crud-table')];
            if (!inst || !inst.dblclickEdit) return;
            var edit = null;
            $.each(inst.rowActions || [], function (i, a) { if (a.name === 'edit' && a.fn) edit = a; });
            if (!edit) return;
            var item = (inst.itemsByKey || {})[$tr.attr('data-crud-key')];
            if (item !== undefined) edit.fn(item, null, e);
        });

        // ---- 2026-11-17：单击行打开详情（Master–Detail 全站统一；避开控件与操作列，不阻断冒泡） ----
        $(document).on('click.crudTable', 'tr[data-crud-key]', function (e) {
            if ($(e.target).closest('input, button, a, select, label, .action-cell, .crud-sel').length) return;
            var inst = _instances[$(this).attr('data-crud-table')];
            if (!inst || !inst.onRowClick) return;
            var item = (inst.itemsByKey || {})[$(this).attr('data-crud-key')];
            if (item !== undefined) inst.onRowClick(item, $(this));
        });

        // ---- 028：列显隐（F9，localStorage per 表格） ----
        $(document).on('change.crudTable', '.crud-col-check', function () {
            var $box = $(this);
            var tid = $box.closest('.crud-col-menu').attr('data-crud-table');
            var inst = _instances[tid];
            if (!inst) return;
            var idx = parseInt($box.attr('data-crud-col'), 10);
            inst.hiddenCols = inst.hiddenCols || {};
            if ($box.prop('checked')) delete inst.hiddenCols[idx]; else inst.hiddenCols[idx] = 1;
            writeColHidden(tid, inst.hiddenCols);
            inst.applyCols();
        });
    }

    function bindEvent(table) {
        bindEventOnce();
        return table;
    }

    // 013 兼容入口：crudTable(cfg) 等价 init + bindEvent
    function crudTableCompat(cfg) {
        return bindEvent(init(cfg));
    }
    crudTableCompat.init = init;
    crudTableCompat.bindEvent = bindEvent;

    return crudTableCompat;
})();
