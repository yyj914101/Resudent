/* dynatable_structure.js — 软表结构修改（078）
 * 改表 = 改元数据；改类型/删字段需二次确认，服务端自动写快照备份。
 */
(function () {
    'use strict';
    var cfg = window.__ST_STRUCT__ || { code: '', fields: [] };
    var baseUrl = '/dynatable';

    function msg(text, type) {
        $('#structMsg').text(text || '');
        if (type) { showToast(text, type); }
    }

    function rowPayload($tr) {
        return {
            display_name: $.trim($tr.find('[data-role="name"]').val()),
            data_type: $tr.find('[data-role="type"]').val(),
            sensitivity: $tr.find('[data-role="sens"]').val(),
            dict_category_code: $tr.find('[data-role="cat"]').val() || null,
            is_required: $tr.find('[data-role="required"]').is(':checked')
        };
    }

    function save($tr, confirmFlag) {
        var fid = $tr.data('fid');
        var payload = rowPayload($tr);
        payload.confirm = !!confirmFlag;
        if (!payload.display_name) { msg('列名不能为空', 'danger'); return; }
        if (payload.data_type === 'enum' && !payload.dict_category_code) {
            msg('枚举列必须选择字典分类', 'danger'); return;
        }
        $.ajax({
            url: baseUrl + '/api/t/' + cfg.code + '/field/' + fid + '/update',
            type: 'POST', contentType: 'application/json', data: JSON.stringify(payload)
        }).done(function (resp) {
            if (!resp.success) {
                if (resp.need_confirm) {
                    if (window.confirm('结构变更将影响 ' + (resp.affected || 0) + ' 行数据，并自动生成备份。确认继续？')) {
                        save($tr, true);
                    }
                    return;
                }
                msg(resp.msg || '保存失败', 'danger');
                return;
            }
            msg('已保存');
            showToast('字段已更新', 'success');
        }).fail(function (xhr) {
            var resp = xhr.responseJSON || {};
            msg(resp.msg || ('保存失败（' + xhr.status + '）'), 'danger');
        });
    }

    function reorder() {
        var order = [];
        $('#fieldBody tr').each(function () { order.push($(this).data('code')); });
        $.ajax({
            url: baseUrl + '/api/t/' + cfg.code + '/field/reorder',
            type: 'POST', contentType: 'application/json',
            data: JSON.stringify({ order: order })
        }).done(function (resp) {
            if (!resp.success) { msg(resp.msg || '排序失败', 'danger'); return; }
            msg('顺序已保存');
        });
    }

    function drop($tr) {
        var fid = $tr.data('fid');
        var name = $.trim($tr.find('[data-role="name"]').val());
        $.ajax({
            url: baseUrl + '/api/t/' + cfg.code + '/field/' + fid + '/delete',
            type: 'POST', contentType: 'application/json', data: JSON.stringify({})
        }).done(function (resp) {
            if (!resp.success) {
                if (resp.need_confirm) {
                    if (window.confirm('删除字段「' + name + '」将影响 ' + (resp.affected || 0) +
                        ' 行数据，并生成备份。确认删除？')) {
                        $.ajax({
                            url: baseUrl + '/api/t/' + cfg.code + '/field/' + fid + '/delete',
                            type: 'POST', contentType: 'application/json',
                            data: JSON.stringify({ confirm: true })
                        }).done(function (r2) {
                            if (!r2.success) { msg(r2.msg || '删除失败', 'danger'); return; }
                            $tr.remove();
                            msg('字段已删除（备份 ' + r2.data.backup + '）');
                        });
                    }
                    return;
                }
                msg(resp.msg || '删除失败', 'danger');
                return;
            }
        });
    }

    function addField() {
        var payload = {
            display_name: $.trim($('#newName').val()),
            data_type: $('#newType').val(),
            sensitivity: $('#newSens').val(),
            dict_category_code: $('#newCat').val() || null,
            is_required: $('#newRequired').is(':checked')
        };
        if (!payload.display_name) { msg('请填写列名', 'danger'); return; }
        $.ajax({
            url: baseUrl + '/api/t/' + cfg.code + '/field/add',
            type: 'POST', contentType: 'application/json', data: JSON.stringify(payload)
        }).done(function (resp) {
            if (!resp.success) { msg(resp.msg || '添加失败', 'danger'); return; }
            msg('字段已添加，正在刷新…');
            setTimeout(function () { location.reload(); }, 500);
        });
    }

    $(function () {
        $('#fieldBody').on('click', '[data-act="save"]', function () {
            save($(this).closest('tr'), false);
        });
        $('#fieldBody').on('click', '[data-act="delete"]', function () {
            drop($(this).closest('tr'));
        });
        $('#fieldBody').on('click', '[data-act="up"]', function () {
            var $tr = $(this).closest('tr');
            var $prev = $tr.prev('tr');
            if ($prev.length) { $tr.insertBefore($prev); reorder(); }
        });
        $('#fieldBody').on('click', '[data-act="down"]', function () {
            var $tr = $(this).closest('tr');
            var $next = $tr.next('tr');
            if ($next.length) { $tr.insertAfter($next); reorder(); }
        });
        $('#addFieldBtn').on('click', addField);
    });
})();
