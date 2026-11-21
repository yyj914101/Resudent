/* ==========================================================================
 * safety_archive.js — 安全工作模块档案目录树（030 一期 + 031 二期体验增强）
 *
 * 024 规范：IIFE 封装顶层作用域；事件命名空间 .safety + document 委托绑定，
 * 「off('.safety').on(...)」幂等（spa.js 伪导航脚本重放不重复绑定）。016 教训：
 * 委托不做 stopPropagation，不阻断冒泡（caret/行选中互斥用事件目标守卫实现）。
 * 复用单源（不重造）：esc / escAttr / renderPagination / showToast / getModal（app.js）。
 *
 * 030（一期）：懒加载树（首屏 1 请求 / 展开 1 请求 / 二次展开 0 请求；单层 >50 加载更多）、
 *   20/页列表、上传/下载/预览、新建/删除（二次确认）、300ms 防抖全树搜索、位置记忆 sessionStorage。
 * 031（二期）：
 *   - 三视图切换：目录 / 平铺（子树拉平 /api/flat，路径列 + 定位）/ 图片网格（缩略图 loading=lazy，
 *     一律走 preview 端点，禁止 uploads 直链）+ 灯箱（BS5 modal 手写、Esc/左右键翻看，零新库）；
 *   - 重命名 / 移动（单条与批量）：目标目录选择器复用一期树资产（children 懒加载 + 内存 cache）；
 *   - 批量：跨页多选 Set + 底部批量条；批量删除红字二次确认、批量移动、勾选集打包下载；
 *     服务端对齐 028 惯例（ids[] + batch_done 留痕）；批量删除端点命名含 delete 自动归类 delete 权限；
 *   - 深链？node=：导航 replaceState 写入、刷新/直达读取；spa.js 零改动（标签重开由 sessionStorage 兜底）；
 *   - 打包下载当前目录：/api/zip?node_id=。
 * ========================================================================== */
(function () {
    var ROOT_NAME = '安全工作';
    var RIGHT = '<i class="bi bi-caret-right-fill"></i>';
    var DOWN = '<i class="bi bi-caret-down-fill"></i>';
    var INLINE_EXT = { jpg: 1, jpeg: 1, png: 1, gif: 1, bmp: 1, webp: 1, pdf: 1 };
    var IMAGE_EXT = { jpg: 1, jpeg: 1, png: 1, gif: 1, bmp: 1, webp: 1 };

    var cache = {};        // nodeKey(0=根) -> {items:[], total:0} 已取子节点内存缓存
    var expanded = {};     // 已展开节点 id 集合（重建树后恢复展开态）
    var current = { id: 0, name: ROOT_NAME };
    var pathChain = [];    // 当前路径父链（祖先在前；面包屑专用）
    var deleteTarget = null;
    var renameTarget = null;   // {id, type, name}
    var moveTarget = null;     // null | {ids:[], type:'single'|'batch', names:[]}
    var movePickedId = null;   // 目标目录选择器当前选中
    var highlightRowId = null;
    var searchTimer = null;
    var listReq = 0;
    var viewMode = 'tree';     // tree | flat | grid
    var selected = {};         // 跨页多选：id -> {id,name,type}
    var gridImages = [];       // 网格/灯箱图片集合 [{id,name,path}]
    var lightboxIdx = 0;

    // ---------------- 工具 ----------------
    function fmtSize(bytes) {
        if (bytes == null) return '-';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB';
        return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
    }

    function iconFor(n) {
        if (n.node_type === 'folder') return '<i class="bi bi-folder2 text-primary"></i>';
        var e = (n.ext || '').toLowerCase();
        if (INLINE_EXT[e]) return '<i class="bi bi-file-earmark-image text-success"></i>';
        if (e === 'xls' || e === 'xlsx' || e === 'csv' || e === 'et')
            return '<i class="bi bi-file-earmark-spreadsheet text-warning"></i>';
        return '<i class="bi bi-file-earmark-text text-secondary"></i>';
    }

    function skeletonRows(n) {
        n = n || 3;
        var h = '<div class="safety-skeleton">';
        for (var i = 0; i < n; i++) h += '<div class="sk-row"></div>';
        return h + '</div>';
    }

    // ---------------- 树渲染（与 030 一致） ----------------
    function buildTreeItem(n) {
        var $li = $('<li class="tree-li">').attr('data-node-id', n.id).attr('data-node-type', n.node_type);
        var caret = n.node_type === 'folder'
            ? '<span class="caret closed">' + RIGHT + '</span>'
            : '<span class="caret leaf"></span>';
        var count = n.node_type === 'folder'
            ? '<span class="node-count">' + (n.child_count || 0) + '</span>' : '';
        $li.append('<div class="tree-row">' + caret +
            '<span class="node-icon">' + iconFor(n) + '</span>' +
            '<span class="node-name" title="' + escAttr(n.name) + '">' + esc(n.name) + '</span>' +
            count + '</div>');
        $li.append('<ul class="node-children" style="display:none"></ul>');
        $li.data('node', n);
        return $li;
    }

    function renderInto($ul, parentId) {
        var data = cache[parentId];
        if (!data) { $ul.html(''); return; }
        $ul.html('');
        data.items.forEach(function (n) { $ul.append(buildTreeItem(n)); });
        if (data.total > data.items.length) {
            $ul.append(
                '<li class="tree-more" data-parent="' + parentId + '">' +
                '<a href="#" class="tree-more-link">加载更多（剩余 ' +
                (data.total - data.items.length) + ' 项）</a></li>');
        }
    }

    function fetchChildren(parentId, append, cb) {
        var offset = append && cache[parentId] ? cache[parentId].items.length : 0;
        $.get('/safety/api/children', { parent_id: parentId, offset: offset }, function (r) {
            if (!r.success) { showToast(r.msg || '加载失败', 'danger'); cb && cb(false); return; }
            var c = cache[parentId];
            if (append && c) {
                c.items = c.items.concat(r.data.items);
                c.total = r.data.total;
            } else {
                cache[parentId] = { items: r.data.items, total: r.data.total };
            }
            cb && cb(true);
        });
    }

    function findTreeLi(id, $scope) {
        return ($scope || $('#safetyTree')).find('.tree-li[data-node-id="' + id + '"]');
    }

    // forceState: true=展开 / false=收起 / undefined=按当前可见性取反
    function toggleNode($li, forceState) {
        var id = parseInt($li.data('node-id'), 10);
        var $ul = $li.children('.node-children');
        var $caret = $li.children('.tree-row').find('.caret');
        if (forceState === undefined) forceState = !$ul.is(':visible');
        if (forceState) {
            $caret.removeClass('closed').addClass('open').html(DOWN);
            if (cache[id]) { renderInto($ul, id); $ul.show(); }
            else {
                $ul.show().html(skeletonRows(3));
                fetchChildren(id, false, function (ok) {
                    if (ok) { renderInto($ul, id); reflectExpanded($ul); }
                    else { $ul.hide(); }
                });
            }
            expanded[id] = 1;
        } else {
            $caret.removeClass('open').addClass('closed').html(RIGHT);
            $ul.hide();
            delete expanded[id];
        }
    }

    function reflectExpanded($root) {
        ($root || $('#safetyTree')).find('.tree-li[data-node-id]').each(function () {
            var id = parseInt($(this).data('node-id'), 10);
            if (!expanded[id]) return;
            var $li = $(this);
            $li.children('.tree-row').find('.caret').removeClass('closed').addClass('open').html(DOWN);
            var $ul = $li.children('.node-children');
            if (cache[id]) renderInto($ul, id);
            $ul.show();
        });
    }

    // 重建后恢复展开态（P3-2 修复）：重命名/移动/删除/上传重建树后，不再把
    // 用户在树上已展开的节点（含当前节点子树）折叠回初始态。按 expanded 集合
    // 深度优先恢复；cache 已随重建清空，逐层懒加载补齐 children。
    function restoreExpanded() {
        function walk($ul) {
            var pending = [];
            $ul.children('.tree-li').each(function () {
                var $li = $(this);
                var id = parseInt($li.data('node-id'), 10);
                if (!expanded[id]) return;
                var $ci = $li.children('.node-children');
                if (cache[id]) renderInto($ci, id);
                pending.push([$li, $ci]);
            });
            var i = 0;
            function next() {
                if (i >= pending.length) return;
                var pair = pending[i++];
                var $li = pair[0], $ci = pair[1];
                var id = parseInt($li.data('node-id'), 10);
                function show() {
                    $ci.show();
                    $li.children('.tree-row').find('.caret')
                        .removeClass('closed').addClass('open').html(DOWN);
                    walk($ci);
                }
                if (cache[id]) { show(); return; }
                fetchChildren(id, false, function (ok) {
                    if (ok) { renderInto($ci, id); show(); }
                    else { $ci.hide(); }
                });
            }
            next();
        }
        walk($('#safetyTree'));
    }

    // 反向展开定位：逐级展开到目标节点后选中（搜索直达 / 位置记忆 / 深链 / 列表下钻共用）
    function expandPathTo(targetId, done) {
        $.get('/safety/api/path', { id: targetId }, function (r) {
            if (!r.success) { showToast(r.msg || '定位失败', 'danger'); done && done(false); return; }
            var chain = r.data.items || [];
            if (!chain.length) { done && done(false); return; }
            var i = 0;
            function step() {
                if (i >= chain.length) { done && done(true); return; }
                var item = chain[i];
                var isLast = i === chain.length - 1;
                if (isLast) {
                    pathChain = chain;
                    setCurrentNode(item);
                    done && done(true);
                    return;
                }
                if (item.node_type !== 'folder') { i++; step(); return; }
                var $li = findTreeLi(item.id, $('#safetyTree'));
                if (!$li.length) { done && done(false); return; }
                if (!cache[item.id]) {
                    fetchChildren(item.id, false, function (ok) {
                        if (!ok) { done && done(false); return; }
                        renderInto($li.children('.node-children'), item.id);
                        toggleNode($li, true);
                        i++; step();
                    });
                    return;
                }
                renderInto($li.children('.node-children'), item.id);
                toggleNode($li, true);
                i++; step();
            }
            step();
        });
    }

    // ---------------- 当前位置 / 面包屑 / 深链 ----------------
    function setDeepLink(id) {
        if (!window.history || !history.replaceState) return;
        try {
            var q = id > 0 ? '?node=' + id : '';
            history.replaceState({ path: location.pathname }, '', location.pathname + q);
        } catch (e) { /* 隐私模式等场景忽略 */ }
    }

    function setCurrentNode(node) {
        highlightRowId = null;
        if (node.node_type === 'file') {
            var parent = pathChain.length > 1 ? pathChain[pathChain.length - 2] : null;
            current = parent ? { id: parent.id, name: parent.name } : { id: 0, name: ROOT_NAME };
            highlightRowId = node.id;
        } else {
            current = { id: node.id, name: node.name };
        }
        $('#safetyTree .tree-row').removeClass('active');
        findTreeLi(node.id, $('#safetyTree')).children('.tree-row').addClass('active');
        renderBreadcrumb();
        refreshView(1);
        saveLast();
        setDeepLink(node.id);
        $('#mobileCrumb').text(highlightRowId ? (ROOT_NAME + ' › ' + node.name) : current.name);
        // P3-1 修复：根目录（current.id=0）无法打包，禁用按钮并给出提示标题
        $('#btnZipDir').prop('disabled', !current.id)
            .attr('title', current.id ? '打包下载当前目录' : '请先进入具体目录后再打包');
    }

    function renderBreadcrumb() {
        var h = '<li class="breadcrumb-item"><a href="#" class="crumb-link" data-nav-id="0">' +
            esc(ROOT_NAME) + '</a></li>';
        var lastIdx = highlightRowId && viewMode !== 'flat' ? pathChain.length - 2 : pathChain.length - 1;
        if (viewMode === 'flat' || viewMode === 'grid') lastIdx = pathChain.length - 1;
        for (var i = 0; i <= lastIdx && i < pathChain.length; i++) {
            var c = pathChain[i];
            if (i === lastIdx) {
                h += '<li class="breadcrumb-item active" aria-current="page">' + esc(c.name) + '</li>';
            } else {
                h += '<li class="breadcrumb-item"><a href="#" class="crumb-link" data-nav-id="' +
                    c.id + '">' + esc(c.name) + '</a></li>';
            }
        }
        $('#breadcrumb').html(h);
    }

    // 树行点击选中
    function selectFromTree($li) {
        var id = parseInt($li.data('node-id'), 10);
        var node = $li.data('node');
        var idx = -1;
        for (var i = 0; i < pathChain.length; i++) if (pathChain[i].id === id) idx = i;
        if (idx >= 0) {
            pathChain = pathChain.slice(0, idx + 1);
            setCurrentNode(node);
            return;
        }
        $.get('/safety/api/path', { id: id }, function (r) {
            if (r.success && r.data) { pathChain = r.data.items || []; }
            setCurrentNode(node);
        });
    }

    // ---------------- 视图渲染（目录 / 平铺 / 网格） ----------------
    function refreshView(page) {
        if (viewMode === 'flat') loadFlat(page || 1);
        else if (viewMode === 'grid') loadGrid();
        else loadList(page || 1);
    }

    function switchView(mode) {
        viewMode = mode;
        $('#viewSwitch .btn').removeClass('active');
        $('#btnViewTree').toggleClass('active', mode === 'tree');
        $('#btnViewFlat').toggleClass('active', mode === 'flat');
        $('#btnViewGrid').toggleClass('active', mode === 'grid');
        $('#tableWrap').toggleClass('d-none', mode === 'grid');
        $('.flat-col').toggleClass('d-none', mode !== 'flat');
        $('#gridWrap').toggleClass('d-none', mode !== 'grid');
        $('#pagerBar').toggleClass('d-none', mode === 'grid');
        renderBreadcrumb();
        refreshView(1);
    }

    function loadList(page) {
        page = page || 1;
        var myId = ++listReq;
        $('#safetyTbody').html(
            '<tr><td colspan="6" class="text-center py-4 text-muted">加载中…</td></tr>');
        $.get('/safety/api/list', { node_id: current.id, page: page }, function (r) {
            if (myId !== listReq) return;
            if (!r.success) {
                $('#safetyTbody').html(
                    '<tr><td colspan="6" class="text-center py-4 text-muted">' +
                    esc(r.msg || '加载失败') + '</td></tr>');
                return;
            }
            var d = r.data;
            renderList(d);
            $('#pageStat').text('共 ' + d.total + ' 项 · 每页 20 条');
            renderPagination('#pagination', d.pages, d.page, loadList);
        });
    }

    function opButtons(n) {
        var ops = '';
        if (n.node_type === 'folder') {
            ops += '<button type="button" class="sc-btn sc-btn--text btn-open" data-id="' + n.id +
                '" title="打开"><i class="bi bi-folder2-open"></i></button>';
        } else if ((n.ext || '').toLowerCase() in INLINE_EXT) {
            ops += '<a class="sc-btn sc-btn--text" href="/safety/file/' + n.id +
                '/preview" target="_blank" title="预览"><i class="bi bi-eye"></i></a>';
            ops += '<a class="sc-btn sc-btn--text" href="/safety/file/' + n.id +
                '/download" title="下载"><i class="bi bi-download"></i></a>';
        } else {
            ops += '<a class="sc-btn sc-btn--text" href="/safety/file/' + n.id +
                '/download" title="下载"><i class="bi bi-download"></i></a>';
        }
        ops = '<span class="sc-row-actions sc-row-actions--tight">' + ops +
            '<button type="button" class="sc-btn sc-btn--text btn-rename" data-id="' + n.id +
            '" data-type="' + n.node_type + '" data-name="' + escAttr(n.name) +
            '" title="重命名"><i class="bi bi-pencil-square"></i></button>' +
            '<button type="button" class="sc-btn sc-btn--text btn-move" data-id="' + n.id +
            '" data-type="' + n.node_type + '" data-name="' + escAttr(n.name) +
            '" title="移动"><i class="bi bi-arrows-move"></i></button>' +
            '<button type="button" class="sc-btn sc-btn--text is-danger btn-del" title="删除">' +
            '<i class="bi bi-trash"></i></button></span>';
        return ops;
    }

    function rowHtml(n) {
        var checked = selected[n.id] ? ' checked' : '';
        var name = iconFor(n) + ' ' + esc(n.name);
        return '<tr data-row-id="' + n.id + '" data-node-type="' + n.node_type + '">' +
            '<td><input type="checkbox" class="form-check-input row-check"' + checked + '></td>' +
            '<td>' + name + '</td>' +
            '<td class="flat-col' + (viewMode === 'flat' ? '' : ' d-none') + '">-</td>' +
            '<td>' + esc(n.updated_at || n.created_at || '-') + '</td>' +
            '<td>' + (n.node_type === 'file' ? fmtSize(n.size) : '-') + '</td>' +
            '<td class="text-end">' + opButtons(n) + '</td></tr>';
    }

    function renderList(d) {
        var $tb = $('#safetyTbody');
        if (!d.items.length) {
            $tb.html('<tr><td colspan="6"><div class="text-center py-4 text-muted">' +
                '<i class="bi bi-inbox fs-3 d-block mb-1"></i>此目录为空 — 点击「上传文件」上传第一个文件' +
                '</div></td></tr>');
            return;
        }
        $tb.html(d.items.map(rowHtml).join(''));
        if (highlightRowId) {
            $tb.find('tr[data-row-id="' + highlightRowId + '"]').addClass('table-primary');
        }
        syncCheckAll();
    }

    // 平铺：子树全部文件 + 完整路径列 + 定位锚点
    function loadFlat(page) {
        page = page || 1;
        var myId = ++listReq;
        $('#safetyTbody').html(
            '<tr><td colspan="6" class="text-center py-4 text-muted">加载中…</td></tr>');
        $.get('/safety/api/flat', { node_id: current.id, page: page }, function (r) {
            if (myId !== listReq) return;
            if (!r.success) {
                $('#safetyTbody').html(
                    '<tr><td colspan="6" class="text-center py-4 text-muted">' +
                    esc(r.msg || '加载失败') + '</td></tr>');
                return;
            }
            var d = r.data;
            var h = d.items.map(function (n) {
                var checked = selected[n.id] ? ' checked' : '';
                return '<tr data-row-id="' + n.id + '" data-node-type="file">' +
                    '<td><input type="checkbox" class="form-check-input row-check"' + checked + '></td>' +
                    '<td>' + iconFor(n) + ' ' + esc(n.name) + '</td>' +
                    '<td class="flat-col"><a href="#" class="flat-locate" data-id="' + n.id + '">' +
                    esc(n.path) + '</a></td>' +
                    '<td>' + esc(n.updated_at || '-') + '</td>' +
                    '<td>' + fmtSize(n.size) + '</td>' +
                    '<td class="text-end">' + opButtons(n) + '</td></tr>';
            }).join('');
            $('#safetyTbody').html(h || '<tr><td colspan="6" class="text-center py-4 text-muted">' +
                '当前子树无文件</td></tr>');
            $('#pageStat').text('共 ' + d.total + ' 个文件 · 每页 20 条');
            renderPagination('#pagination', d.pages, d.page, loadFlat);
            syncCheckAll();
        });
    }

    // 网格：子树图片（走 preview 端点 + loading=lazy，禁止 uploads 直链）
    function loadGrid() {
        var myId = ++listReq;
        $('#safetyGrid').html('<div class="text-muted small p-3">加载中…</div>');
        $.get('/safety/api/flat', { node_id: current.id, page: 1, size: 500 }, function (r) {
            if (myId !== listReq) return;
            if (!r.success || !r.data) {
                $('#safetyGrid').html('<div class="text-muted small p-3">' +
                    esc((r.msg) || '加载失败') + '</div>');
                return;
            }
            gridImages = r.data.items.filter(function (n) {
                return (n.ext || '').toLowerCase() in IMAGE_EXT;
            });
            if (!gridImages.length) {
                $('#safetyGrid').html('<div class="text-center py-5 text-muted">' +
                    '<i class="bi bi-images fs-1 d-block mb-2"></i>当前子树暂无图片' +
                    '</div>');
                return;
            }
            var h = gridImages.map(function (n, i) {
                return '<div class="grid-card" data-idx="' + i + '">' +
                    '<img loading="lazy" class="grid-thumb" src="/safety/file/' + n.id +
                    '/preview" alt="' + escAttr(n.name) + '">' +
                    '<div class="grid-name text-truncate" title="' + escAttr(n.name) + '">' +
                    esc(n.name) + '</div></div>';
            }).join('');
            $('#safetyGrid').html(h);
            $('#pageStat').text('共 ' + gridImages.length + ' 张图片（子树）');
        });
    }

    // ---------------- 批量多选 ----------------
    function syncBatchBar() {
        var n = Object.keys(selected).length;
        $('#batchCount').text(n);
        $('#batchBar').toggleClass('d-none', !n).removeClass('d-flex');
        if (n) $('#batchBar').addClass('d-flex');
    }

    function syncCheckAll() {
        var $rows = $('#safetyTbody tr[data-row-id]');
        var count = $rows.length && $rows.find('.row-check:checked').length === $rows.length && $rows.length > 0;
        $('#checkAll').prop('checked', count);
    }

    function toggleSelect(id, name, type, checked) {
        if (checked) selected[id] = { id: id, name: name, type: type };
        else delete selected[id];
        syncBatchBar();
    }

    function onRowCheck(id) {
        var $tr = $('#safetyTbody tr[data-row-id="' + id + '"]');
        var name = $tr.find('td:eq(1)').text().trim();
        var type = $tr.data('node-type');
        toggleSelect(id, name, type, $tr.find('.row-check').prop('checked'));
    }

    function clearSelection() {
        selected = {};
        syncBatchBar();
        syncCheckAll();
    }

    // ---------------- 重命名 / 移动 ----------------
    function openRename(id, type, name) {
        renameTarget = { id: id, type: type, name: name };
        $('#renameName').val(name);
        getModal('renameModal').show();
        setTimeout(function () {
            $('#renameName').trigger('focus').select();
        }, 200);
    }

    function openMove(ids, names) {  // ids: [single] 或批量数组
        moveTarget = { ids: ids, names: names };
        movePickedId = null;
        $('#moveTargetLabel').text('未选择');
        $('#btnMoveConfirm').prop('disabled', true);
        $('#moveTree').html(skeletonRows(3));
        fetchChildren(0, false, function (ok) {
            if (ok) renderInto($('#moveTree'), 0);
        });
        getModal('moveModal').show();
    }

    function pickMoveTarget(id, name) {
        movePickedId = id;
        $('#moveTargetLabel').text(name);
        $('#moveTree .tree-row').removeClass('active');
        findTreeLi(id, $('#moveTree')).children('.tree-row').addClass('active');
        $('#btnMoveConfirm').prop('disabled', false);
    }

    function doMove() {
        if (!moveTarget || !movePickedId) return;
        var url = '/safety/api/file/batch_move';
        var ids = moveTarget.ids.slice();
        $.post(url, { ids: ids, target_id: movePickedId }, function (r) {
            if (!r.success) { showToast(r.msg || '移动失败', 'danger'); return; }
            showToast(r.msg, 'success');
            if (r.data && r.data.failed && r.data.failed.length) {
                r.data.failed.forEach(function (f) {
                    showToast(f.name + '：' + f.msg, 'warning');
                });
            }
            getModal('moveModal').hide();
            clearSelection();
            ids.forEach(function (i) { delete cache[i]; });
            resetTree();
        });
    }

    // ---------------- 删除（单条 / 批量共用确认弹窗） ----------------
    function openDelete(id, type, name) {
        deleteTarget = { kind: 'single', ids: [id], type: type, name: name };
        $('#delTargetDesc').html('确定删除「<span class="fw-bold" id="delTargetName">' + esc(name) + '</span>」吗？');
        $('#delTargetHint').text(type === 'folder'
            ? '该目录必须为空才能删除。' : '删除后不可恢复，文件将从磁盘移除。');
        getModal('confirmDeleteModal').show();
    }

    function openBatchDelete() {
        var keys = Object.keys(selected);
        if (!keys.length) return;
        var names = keys.slice(0, 3).map(function (k) { return selected[k].name; }).join('、');
        if (keys.length > 3) names += ' 等';
        deleteTarget = { kind: 'batch', ids: keys.map(Number), type: null, name: names };
        $('#delTargetDesc').html('确定删除选中的 <span class="fw-bold text-danger">' +
            keys.length + '</span> 项（' + esc(names) + '）吗？');
        $('#delTargetHint').text('空目录允许删除；非空目录将被跳过并计入失败回执。');
        getModal('confirmDeleteModal').show();
    }

    function doDelete() {
        if (!deleteTarget) return;
        var isBatch = deleteTarget.kind === 'batch';
        var url = isBatch ? '/safety/api/file/batch_delete' : (
            deleteTarget.type === 'folder' ? '/safety/api/folder' : '/safety/api/file');
        var data = isBatch ? { ids: deleteTarget.ids } : { action: 'delete', id: deleteTarget.ids[0] };
        $.post(url, data, function (r) {
            if (r.success) {
                showToast(r.msg || '已删除', 'success');
                if (r.data && r.data.failed && r.data.failed.length) {
                    r.data.failed.forEach(function (f) {
                        showToast(f.name + '：' + f.msg, 'warning');
                    });
                }
                getModal('confirmDeleteModal').hide();
                clearSelection();
                resetTree();
            } else {
                showToast(r.msg || '删除失败', 'danger');
                getModal('confirmDeleteModal').hide();
            }
        });
    }

    // ---------------- 位置记忆 / 深链 ----------------
    function saveLast() {
        try { sessionStorage.setItem('safety_last_node', JSON.stringify({ id: current.id })); } catch (e) { }
    }

    function restoreLast() {
        var raw = null;
        try { raw = sessionStorage.getItem('safety_last_node'); } catch (e) { }
        if (!raw) return 0;
        try {
            var d = JSON.parse(raw);
            return d && parseInt(d.id, 10) > 0 ? parseInt(d.id, 10) : 0;
        } catch (e) { return 0; }
    }

    function deepLinkNode() {
        var m = (location.search || '').match(/[?&]node=(\d+)/);
        return m ? parseInt(m[1], 10) : 0;
    }

    // ---------------- 搜索（300ms 防抖 · 全树） ----------------
    function doSearch(q, $box) {
        var $res = $box.find('.search-results');
        if (!q) { $res.addClass('d-none').html(''); return; }
        $.get('/safety/api/search', { q: q }, function (r) {
            if (!r.success || !r.data || !r.data.items.length) {
                $res.html('<div class="sr-empty">未找到匹配项</div>').removeClass('d-none');
                return;
            }
            var h = r.data.items.map(function (it) {
                return '<a href="#" class="sr-item" data-search-id="' + it.id + '">' +
                    '<div class="sr-name text-truncate">' + iconFor(it) + ' ' + esc(it.name) + '</div>' +
                    '<div class="sr-path text-truncate">' + esc(it.path) + '</div></a>';
            }).join('');
            $res.html(h).removeClass('d-none');
        });
    }

    function closeSearchPanels() {
        $('.search-results').addClass('d-none').html('');
        $('#treeSearch, #treeSearchMobile').val('');
    }

    // ---------------- 上传 ----------------
    function uploadFiles(files) {
        if (!files || !files.length) return;
        var fd = new FormData();
        var skipped = [], added = 0;
        fd.append('node_id', current.id);
        for (var i = 0; i < files.length; i++) {
            if (files[i].size > 50 * 1024 * 1024) { skipped.push(files[i].name); continue; }
            fd.append('files', files[i]);
            added++;
        }
        if (!added) {
            showToast(skipped.length ? '所选文件均超过 50MB，已全部跳过' : '未选择文件', 'warning');
            return;
        }
        $.ajax({
            url: '/safety/api/file/upload', type: 'POST',
            data: fd, processData: false, contentType: false,
            success: function (r) {
                if (!r.success) {
                    showToast(r.msg || '上传失败', 'danger');
                    if (r.data && r.data.results) {
                        r.data.results.filter(function (x) { return !x.ok; })
                            .forEach(function (x) { showToast(x.name + '：' + x.msg, 'danger'); });
                    }
                    return;
                }
                var results = r.data.results || [];
                var ok = 0, dup = 0, fail = 0;
                results.forEach(function (x) { x.ok ? (ok++, x.duplicate && dup++) : fail++; });
                showToast('上传完成：成功 ' + ok + ' 个' +
                    (dup ? '（含同名 ' + dup + ' 个）' : '') +
                    (fail ? '，失败 ' + fail + ' 个' : '') +
                    (skipped.length ? '，超限跳过 ' + skipped.length + ' 个' : ''),
                    fail || skipped.length ? 'warning' : 'success');
                results.filter(function (x) { return !x.ok; })
                    .forEach(function (x) { showToast(x.name + '：' + x.msg, 'danger'); });
                resetTree();
            },
            error: function (xhr) {
                xhr.__handled = true;
                showToast('上传失败：' + (xhr.responseJSON && xhr.responseJSON.msg || '网络错误'), 'danger');
            }
        });
    }

    // ---------------- 结构变更后整树重建 ----------------
    function resetTree() {
        cache = {};
        $('#safetyTree').html(skeletonRows(3));
        fetchChildren(0, false, function (ok) {
            if (!ok) return;
            renderInto($('#safetyTree'), 0);
            if (current.id > 0) expandPathTo(current.id, restoreExpanded);
            else { refreshView(1); restoreExpanded(); }
        });
    }

    // ---------------- 灯箱 ----------------
    function openLightbox(idx) {
        lightboxIdx = idx;
        showLightbox();
        getModal('lightboxModal').show();
    }

    function showLightbox() {
        var it = gridImages[lightboxIdx];
        if (!it) return;
        $('#lightboxImg').attr('src', '/safety/file/' + it.id + '/preview');
        $('#lightboxName').text(it.name);
        $('#lightboxIndex').text((lightboxIdx + 1) + ' / ' + gridImages.length);
    }

    function lightboxStep(delta) {
        if (!gridImages.length) return;
        lightboxIdx = (lightboxIdx + delta + gridImages.length) % gridImages.length;
        showLightbox();
    }

    // ---------------- 事件注册（.safety 命名空间 · document 委托） ----------------
    function registerEvents() {
        $(document).off('.safety');

        // 树：caret 展开/收起（行选中处理器用事件目标守卫互斥，双方均不 stopPropagation）
        $(document).on('click.safety', '.safety-tree .caret:not(.leaf)', function (e) {
            e.preventDefault();
            toggleNode($(this).closest('.tree-li'));
        });
        // 树：行选中
        $(document).on('click.safety', '.safety-tree .tree-row', function (e) {
            if ($(e.target).closest('.caret').length) return;
            var $li = $(this).closest('.tree-li');
            if (!$li.data('node')) return;
            var inMoveTree = !!$(this).closest('#moveTree').length;
            if (inMoveTree) {
                var n = $li.data('node');
                if (n.node_type === 'folder') pickMoveTarget(n.id, n.name);
                else showToast('请选择目录节点', 'warning');
                return;
            }
            selectFromTree($li);
            var $oc = document.getElementById('treeOffcanvas');
            if ($oc && bootstrap.Offcanvas.getInstance($oc)) bootstrap.Offcanvas.getInstance($oc).hide();
        });
        // 树：加载更多
        $(document).on('click.safety', '.tree-more-link', function (e) {
            e.preventDefault();
            var pid = parseInt($(this).closest('.tree-more').data('parent'), 10);
            var $ul = $(this).closest('.node-children, .safety-tree');
            fetchChildren(pid, true, function (ok) { if (ok) renderInto($ul, pid); });
        });
        // 面包屑回跳
        $(document).on('click.safety', '.crumb-link', function (e) {
            e.preventDefault();
            var id = parseInt($(this).data('nav-id'), 10);
            if (id > 0) expandPathTo(id);
            else { pathChain = []; setCurrentNode({ id: 0, name: ROOT_NAME }); }
        });
        // 全部收起
        $('#btnCollapseAll').on('click.safety', function () {
            expanded = {};
            $('#safetyTree .node-children').hide();
            $('#safetyTree .caret.open').removeClass('open').addClass('closed').html(RIGHT);
        });
        // 视图切换
        $('#btnViewTree').on('click.safety', function () { switchView('tree'); });
        $('#btnViewFlat').on('click.safety', function () { switchView('flat'); });
        $('#btnViewGrid').on('click.safety', function () { switchView('grid'); });
        // 打包下载当前目录
        $('#btnZipDir').on('click.safety', function () {
            window.location.href = '/safety/api/zip?node_id=' + current.id;
        });
        // 搜索输入（桌面 + 移动抽屉）
        function bindSearchInput($input) {
            $input.on('input.safety', function () {
                clearTimeout(searchTimer);
                var q = $(this).val().trim();
                var $box = $(this).closest('.position-relative');
                searchTimer = setTimeout(function () { doSearch(q, $box); }, 300);
            });
        }
        bindSearchInput($('#treeSearch'));
        bindSearchInput($('#treeSearchMobile'));
        $(document).on('click.safety', '.sr-item', function (e) {
            e.preventDefault();
            var id = parseInt($(this).data('search-id'), 10);
            closeSearchPanels();
            var $oc = document.getElementById('treeOffcanvas');
            if ($oc && bootstrap.Offcanvas.getInstance($oc)) bootstrap.Offcanvas.getInstance($oc).hide();
            if (viewMode !== 'tree') switchView('tree');
            expandPathTo(id);
        });
        $(document).on('click.safety', function (e) {
            if (!$(e.target).closest('.position-relative, .search-results').length) closeSearchPanels();
        });
        // 移动端抽屉
        $('#btnTreeDrawer').on('click.safety', function () {
            $('#treeMobile').html($('#safetyTree').html());
            bootstrap.Offcanvas.getOrCreateInstance(document.getElementById('treeOffcanvas')).show();
        });
        // 列表：打开目录 / 双击行下钻
        $(document).on('click.safety', '#safetyTbody .btn-open', function () {
            if (viewMode !== 'tree') switchView('tree');
            expandPathTo(parseInt($(this).data('id'), 10));
        });
        $(document).on('dblclick.safety', '#safetyTbody tr[data-node-type="folder"]', function () {
            if (viewMode !== 'tree') switchView('tree');
            expandPathTo(parseInt($(this).data('row-id'), 10));
        });
        // 平铺定位锚点
        $(document).on('click.safety', '.flat-locate', function (e) {
            e.preventDefault();
            if (viewMode !== 'tree') switchView('tree');
            expandPathTo(parseInt($(this).data('id'), 10));
        });
        // 多选框（行 / 全选）
        $(document).on('change.safety', '#checkAll', function () {
            var on = $(this).prop('checked');
            $('#safetyTbody tr[data-row-id]').each(function () {
                $(this).find('.row-check').prop('checked', on);
                toggleSelect(parseInt($(this).data('row-id'), 10),
                    $(this).find('td:eq(1)').text().trim(),
                    $(this).data('node-type'), on);
            });
        });
        $(document).on('change.safety', '.row-check', function () {
            onRowCheck(parseInt($(this).closest('tr').data('row-id'), 10));
        });
        // 单条重命名 / 移动
        $(document).on('click.safety', '.btn-rename', function () {
            var $b = $(this);
            openRename(parseInt($b.data('id'), 10), $b.data('type'), $b.data('name'));
        });
        $(document).on('click.safety', '.btn-move', function () {
            var $b = $(this);
            openMove([parseInt($b.data('id'), 10)], [$b.data('name')]);
        });
        // 单条删除
        $(document).on('click.safety', '#safetyTbody .btn-del', function () {
            var $tr = $(this).closest('tr');
            openDelete(parseInt($tr.data('row-id'), 10), $tr.data('node-type'),
                $tr.find('td:eq(1)').text().trim());
        });
        $('#btnConfirmDelete').on('click.safety', doDelete);
        // 批量条
        $('#btnBatchMove').on('click.safety', function () {
            var keys = Object.keys(selected);
            openMove(keys.map(Number), keys.map(function (k) { return selected[k].name; }));
        });
        $('#btnBatchDelete').on('click.safety', openBatchDelete);
        $('#btnBatchZip').on('click.safety', function () {
            var keys = Object.keys(selected);
            if (!keys.length) return;
            var q = keys.map(function (k) { return 'ids=' + k; }).join('&');
            window.location.href = '/safety/api/zip?' + q;
        });
        // 重命名提交
        $('#renameForm').on('submit.safety', function (e) {
            e.preventDefault();
            if (!renameTarget) return;
            var name = $('#renameName').val().trim();
            if (!name) return;
            var url = renameTarget.type === 'folder' ? '/safety/api/folder' : '/safety/api/file';
            $.post(url, { action: 'rename', id: renameTarget.id, name: name }, function (r) {
                if (r.success) {
                    showToast('已重命名', 'success');
                    getModal('renameModal').hide();
                    if (viewMode === 'tree') resetTree();
                    else refreshView();
                } else {
                    showToast(r.msg || '重命名失败', 'danger');
                }
            });
        });
        // 移动确认
        $('#btnMoveConfirm').on('click.safety', doMove);
        // 新建目录
        $('#btnNewFolder').on('click.safety', function () {
            $('#newFolderName').val('');
            $('#newFolderParent').text(current.name + (viewMode === 'tree' ? '' : '（当前子树根）'));
            getModal('newFolderModal').show();
            setTimeout(function () { $('#newFolderName').trigger('focus'); }, 200);
        });
        $('#newFolderForm').on('submit.safety', function (e) {
            e.preventDefault();
            var name = $('#newFolderName').val().trim();
            if (!name) return;
            $.post('/safety/api/folder', { action: 'new', parent_id: current.id, name: name }, function (r) {
                if (r.success) {
                    showToast('新建成功', 'success');
                    getModal('newFolderModal').hide();
                    resetTree();
                } else {
                    showToast(r.msg || '新建失败', 'danger');
                }
            });
        });
        // 上传（按钮 + 拖拽）
        $('#btnUpload').on('click.safety', function () {
            $('#uploadInput').val('').trigger('click');
        });
        $('#uploadInput').on('change.safety', function () {
            uploadFiles(this.files);
        });
        var dragDepth = 0;
        $(document).on('dragenter.safety', function (e) {
            var dt = e.originalEvent && e.originalEvent.dataTransfer;
            if (!dt || !dt.types || dt.types.indexOf('Files') < 0) return;
            dragDepth++;
            $('#dropzone').removeClass('d-none').addClass('dropzone-active');
        }).on('dragover.safety', function (e) {
            e.preventDefault();
        }).on('dragleave.safety', function () {
            if (--dragDepth <= 0) {
                dragDepth = 0;
                $('#dropzone').addClass('d-none').removeClass('dropzone-active');
            }
        }).on('drop.safety', function (e) {
            e.preventDefault();
            dragDepth = 0;
            $('#dropzone').addClass('d-none').removeClass('dropzone-active');
            var dt = e.originalEvent && e.originalEvent.dataTransfer;
            if (dt && dt.files && dt.files.length) uploadFiles(dt.files);
        });
        // 网格卡片 → 灯箱
        $(document).on('click.safety', '.grid-card', function () {
            openLightbox(parseInt($(this).data('idx'), 10));
        });
        $('#lightboxPrev').on('click.safety', function () { lightboxStep(-1); });
        $('#lightboxNext').on('click.safety', function () { lightboxStep(1); });
        // 左右翻看 + Esc 关闭（Esc 不依赖 modal 焦点，验收要求键盘可关闭）
        $(document).on('keydown.safety', function (e) {
            var $lb = $('#lightboxModal');
            if (!$lb.hasClass('show')) return;
            if (e.key === 'Escape') { getModal('lightboxModal').hide(); return; }
            if (e.key === 'ArrowLeft') lightboxStep(-1);
            if (e.key === 'ArrowRight') lightboxStep(1);
        });
    }

    // ---------------- 启动 ----------------
    registerEvents();
    var lastId = deepLinkNode() || restoreLast();
    if (lastId) setDeepLink(lastId);
    fetchChildren(0, false, function (ok) {
        if (!ok) {
            $('#safetyTree').html('<div class="text-muted small p-2">加载失败，请刷新重试</div>');
            return;
        }
        renderInto($('#safetyTree'), 0);
        if (lastId > 0) {
            expandPathTo(lastId, function (succ) {
                if (!succ) { pathChain = []; setCurrentNode({ id: 0, name: ROOT_NAME }); }
            });
        } else {
            pathChain = [];
            setCurrentNode({ id: 0, name: ROOT_NAME });
        }
    });
})();
/* 2026-11-02 对齐样图：页头档案统计胶囊（/api/stats）+ 脚注栏联动（页脚统计/已选项） */
(function () {
    function fmtSize(bytes) {
        if (!bytes) return '0 MB';
        var mb = bytes / 1048576;
        return mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb.toFixed(1) + ' MB';
    }
    function refreshStats() {
        $.get('/safety/api/stats', {}, function (r) {
            var d = (r && r.data) || {};
            $('#archivePill').text('共 ' + (d.files || 0) + ' 个文件 · ' + fmtSize(d.bytes || 0));
        });
    }
    function syncFoot() {
        var stat = $('#pageStat').text().replace(' · 每页 20 条', '');
        var picked = $('#batchBar').hasClass('d-none') ? '' : ' · 已选 ' + $('#batchCount').text() + ' 项';
        $('#footStats').text(stat + picked);
    }
    $(function () {
        refreshStats();
        syncFoot();
        // 上传/删除/移动后列表重渲染会改写 pageStat —— 顺势刷新统计胶囊
        new MutationObserver(function () { refreshStats(); })
            .observe(document.querySelector('#pageStat'), { childList: true, characterData: true, subtree: true });
        new MutationObserver(syncFoot)
            .observe(document.querySelector('#pageStat'), { childList: true, characterData: true, subtree: true });
        new MutationObserver(syncFoot)
            .observe(document.querySelector('#batchCount'), { childList: true, characterData: true, subtree: true });
        new MutationObserver(syncFoot)
            .observe(document.querySelector('#batchBar'), { attributes: true, attributeFilter: ['class'] });
    });
})();

/* 2026-11-02：档案目录侧栏收纳开关（默认收起，点「目录」展出） */
(function () {
    $(function () {
        $('#btnSiderToggle').on('click', function () {
            $('#safetyLayout').toggleClass('sider-collapsed');
            $(this).toggleClass('active');
        });
    });
})();
