(function () {
var selectedCategoryId = null;
// AUD-16：写操作按钮仅 admin 渲染（接口仍由服务端 @admin_required 守护）
var DICT_CAN_MANAGE = !!(window.__SPA_CFG__ && window.__SPA_CFG__.isAdmin);

$(function() {
    loadCategories();
    $('#categoryForm').on('submit', function(e) { e.preventDefault(); saveCategory(); });
    $('#itemForm').on('submit', function(e) { e.preventDefault(); saveItem(); });
});

function loadCategories() {
    $.getJSON('/dict/api/categories', function(resp) {
        if (!resp.success) return;
        var html = '';
        $.each(resp.data, function(i, cat) {
            html += '<div class="sc-list__item' + (selectedCategoryId === cat.id ? ' is-active' : '') + '">' +
                '<a href="#" class="text-decoration-none" style="color:inherit" onclick="selectCategory(' + cat.id + ', \'' + cat.name + '\');return false;">' +
                cat.name + '</a>' +
                (cat.is_active ? '' : ' <span class="sc-badge sc-badge--muted">已停用</span>') +
                '<span class="sc-list__count">' + cat.item_count + '</span>' +
                (DICT_CAN_MANAGE
                    ? '<div class="sc-row-actions">' +
                      '<button type="button" class="sc-btn sc-btn--text" onclick="editCategory(' + cat.id + ')">编辑</button>' +
                      '<button type="button" class="sc-btn sc-btn--text is-danger" onclick="deleteCategory(' + cat.id + ', \'' + cat.name + '\')">删除</button>' +
                      '</div>'
                    : '') +
                '</div>';
        });
        $('#categoryList').html(html || '<div class="p-3 text-muted text-center">暂无分类</div>');
    });
}

function selectCategory(id, name) {
    selectedCategoryId = id;
    $('#currentCategoryName').text(name);
    loadItems(id);
    loadCategories(); // refresh active state
}

function loadItems(categoryId) {
    $.getJSON('/dict/api/items/' + categoryId, function(resp) {
        if (!resp.success) return;
        var html = '';
        $.each(resp.data, function(i, item) {
            var statusLabel = item.is_active
                ? '<span class="sc-badge sc-badge--success">启用</span>'
                : '<span class="sc-badge sc-badge--muted">停用</span>';
            html += '<tr>' +
                '<td>' + item.label + '</td>' +
                '<td>' + item.value + '</td>' +
                '<td>' + item.sort_order + '</td>' +
                '<td>' + statusLabel + '</td>' +
                '<td>' +
                (DICT_CAN_MANAGE
                    ? '<div class="sc-row-actions">' +
                      '<button type="button" class="sc-btn sc-btn--text" onclick="showItemEdit(' + item.id + ')">编辑</button>' +
                      '<button type="button" class="sc-btn sc-btn--text" onclick="toggleItem(' + item.id + ')">' + (item.is_active ? '停用' : '启用') + '</button>' +
                      '<button type="button" class="sc-btn sc-btn--text is-danger" onclick="deleteItem(' + item.id + ')">删除</button>' +
                      '</div>'
                    : '—') +
                '</td></tr>';
        });
        $('#itemBody').html(html || '<tr><td colspan="5" class="text-center text-muted">暂无字典项</td></tr>');
    });
}

// Category operations
function showCategoryAdd() {
    $('#categoryEditId').val('');
    $('#categoryForm')[0].reset();
    $('#categoryFormTitle').text('新增分类');
    $('#categoryModal').modal('show');
}

function saveCategory() {
    $.post('/dict/api/category/save', $('#categoryForm').serialize(), function(resp) {
        if (resp.success) {
            $('#categoryModal').modal('hide');
            loadCategories();
            showToast(resp.msg, 'success');
        } else {
            showToast(resp.msg, 'danger');
        }
    });
}

// Item operations
function showItemAdd() {
    if (!selectedCategoryId) { showToast('请先选择分类', 'warning'); return; }
    $('#itemEditId').val('');
    $('#itemForm')[0].reset();
    $('#itemCategoryId').val(selectedCategoryId);
    $('#itemFormTitle').text('新增字典项');
    $('#itemModal').modal('show');
}

function showItemEdit(id) {
    if (!selectedCategoryId) return;
    $.getJSON('/dict/api/items/' + selectedCategoryId, function(resp) {
        if (!resp.success) return;
        var item = resp.data.find(function(v) { return v.id === id; });
        if (!item) return;
        $('#itemEditId').val(item.id);
        $('#itemCategoryId').val(item.category_id);
        $('[name="label"]').val(item.label);
        $('[name="value"]').val(item.value);
        $('[name="sort_order"]').val(item.sort_order);
        $('#itemFormTitle').text('编辑字典项');
        $('#itemModal').modal('show');
    });
}

function saveItem() {
    $.post('/dict/api/item/save', $('#itemForm').serialize(), function(resp) {
        if (resp.success) {
            $('#itemModal').modal('hide');
            loadItems(selectedCategoryId);
            showToast(resp.msg, 'success');
        } else {
            showToast(resp.msg, 'danger');
        }
    });
}

function toggleItem(id) {
    if (!confirm('确定要切换字典项状态吗？')) return;
    $.post('/dict/api/item/toggle/' + id, function(resp) {
        if (resp.success) { loadItems(selectedCategoryId); showToast(resp.msg, 'success'); }
        else { showToast(resp.msg, 'danger'); }
    });
}

function deleteItem(id) {
    if (!confirm('确定要删除此字典项吗？')) return;
    $.post('/dict/api/item/delete/' + id, function(resp) {
        if (resp.success) { loadItems(selectedCategoryId); showToast(resp.msg, 'success'); }
        else { showToast(resp.msg, 'danger'); }
    });
}

function editCategory(id) {
    $.getJSON('/dict/api/categories', function(resp) {
        if (!resp.success) return;
        var cat = resp.data.find(function(v) { return v.id === id; });
        if (!cat) return;
        $('#categoryEditId').val(cat.id);
        $('[name="name"]').val(cat.name);
        $('[name="description"]').val(cat.description);
        $('[name="sort_order"]').val(cat.sort_order);
        $('#categoryFormTitle').text('编辑分类');
        $('#categoryModal').modal('show');
    });
}

function deleteCategory(id, name) {
    if (!confirm('确定要删除字典分类「' + name + '」吗？\n该分类下的所有字典项将同时被删除！')) return;
    $.post('/dict/api/category/delete/' + id, function(resp) {
        if (resp.success) {
            if (selectedCategoryId === id) {
                selectedCategoryId = null;
                $('#currentCategoryName').text('请选择分类');
                $('#itemBody').html('<tr><td colspan="5" class="text-center text-muted">请先选择左侧分类</td></tr>');
            }
            loadCategories();
            showToast(resp.msg, 'success');
        } else {
            showToast(resp.msg, 'danger');
        }
    });
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.deleteCategory = deleteCategory;
window.deleteItem = deleteItem;
window.editCategory = editCategory;
window.selectCategory = selectCategory;
window.showCategoryAdd = showCategoryAdd;
window.showItemAdd = showItemAdd;
window.showItemEdit = showItemEdit;
window.toggleItem = toggleItem;
})();
