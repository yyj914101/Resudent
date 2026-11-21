/* dynatable_upload.js — 台账建表：上传 Excel 建表（078；2026-11-16 随 ADR-022 改版补拖拽）
 * 只做上传与解析；建表动作在确认页完成（强制人工确认）。
 * 2026-11-16：原生 file 域改为拖拽区内透明铺满（点击穿透），故补「拖拽落文件 + 已选文件名回显」。
 */
(function () {
    'use strict';
    var baseUrl = '/dynatable';
    var DEFAULT_ICON = 'bi-file-earmark-plus';

    function msg(text, type) {
        $('#uploadMsg').text(text || '');
        if (type) { showToast(text, type); }
    }

    function fileInput() {
        return document.getElementById('f_file');
    }

    /** 已选文件回显：替换副标题行为「已选：xxx（123KB）」，未选则恢复默认提示。 */
    function renderFileName() {
        var input = fileInput();
        var $name = $('#fileName');
        var file = input && input.files && input.files[0];
        if (!file) {
            $name.prop('hidden', true).text('');
            $('#dropZone').removeClass('has-file');
            return;
        }
        var size = file.size > 1048576
            ? (file.size / 1048576).toFixed(1) + ' MB'
            : Math.max(1, Math.round(file.size / 1024)) + ' KB';
        $name.html('<i class="bi bi-file-earmark-check" aria-hidden="true"></i> '
                   + esc(file.name) + '（' + size + '）')
             .prop('hidden', false);
        $('#dropZone').addClass('has-file');
    }

    /** 拖拽落文件：把 DataTransfer 的文件塞进原生域，后续提交链路零改动。 */
    function assignDroppedFiles(files) {
        var input = fileInput();
        if (!files || !files.length) { return; }
        try {
            var dt = new DataTransfer();
            dt.items.add(files[0]);
            input.files = dt.files;
        } catch (e) {
            /* 老浏览器不支持构造 DataTransfer：退回提示，不让用户误以为已选 */
            msg('当前浏览器不支持拖拽上传，请点击选择文件', 'danger');
            return;
        }
        renderFileName();
        msg('');
    }

    function submit() {
        var input = fileInput();
        if (!input.files || !input.files.length) { msg('请选择要上传的文件', 'danger'); return; }
        var name = $.trim($('#f_name').val());
        var code = $.trim($('#f_code').val());
        if (!name) { msg('请填写台账名称', 'danger'); return; }
        if (!/^[a-z][a-z0-9_]{1,59}$/.test(code)) {
            msg('表标识只能「小写字母开头 + 小写字母/数字/下划线」', 'danger'); return;
        }
        var fd = new FormData();
        fd.append('file', input.files[0]);
        $('#submitBtn').prop('disabled', true);
        msg('正在解析…');
        $.ajax({
            url: baseUrl + '/api/upload', type: 'POST', data: fd,
            processData: false, contentType: false
        }).done(function (resp) {
            if (!resp.success) { msg(resp.msg || '解析失败', 'danger'); return; }
            try {
                sessionStorage.setItem('st_new_table', JSON.stringify({
                    token: resp.data.token,
                    name: name,
                    code: code,
                    module_key: $('#f_module').val(),
                    group_name: $('#f_group').val(),
                    description: $.trim($('#f_desc').val())
                }));
            } catch (e) { /* 隐私模式忽略，确认页会提示 */ }
            location.href = baseUrl + '/confirm?token=' + encodeURIComponent(resp.data.token);
        }).fail(function (xhr) {
            var resp = xhr.responseJSON || {};
            msg(resp.msg || ('上传失败（' + xhr.status + '）'), 'danger');
        }).always(function () {
            $('#submitBtn').prop('disabled', false);
        });
    }

    $(function () {
        var input = fileInput();
        var $zone = $('#dropZone');

        $('#uploadForm').on('submit', function (e) { e.preventDefault(); submit(); });

        $(input).on('change', renderFileName);

        // 拖拽：整块可落（dragenter/over 必须 preventDefault 才会触发 drop）
        $zone.on('dragenter dragover', function (e) {
            e.preventDefault();
            e.stopPropagation();
            $zone.addClass('is-drag');
        }).on('dragleave dragend', function (e) {
            e.preventDefault();
            e.stopPropagation();
            $zone.removeClass('is-drag');
        }).on('drop', function (e) {
            e.preventDefault();
            e.stopPropagation();
            $zone.removeClass('is-drag');
            var oe = e.originalEvent || e;
            var files = (oe.dataTransfer && oe.dataTransfer.files) || null;
            assignDroppedFiles(files);
        });

        $('#resetBtn').on('click', function () {
            $('#uploadForm')[0].reset();
            $zone.removeClass('is-drag');
            renderFileName();
            msg('');
        });
    });
})();
