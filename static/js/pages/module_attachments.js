(function () {
'use strict';
// 092 模板附件区：三模块（disabled/care/subsidy）共用挂载脚本（单一真源，禁三份复制漂移）。
// 路由约定（三蓝图一致，见 utils/attachment_service 接线）：
//   GET  <base>                  列表
//   POST <base>/upload           上传（multipart，字段 file）
//   GET  <base>/<id>/download    下载
//   POST <base>/<id>/delete      删除
// 用法（页面 extra_js 内）：initAttachments({ base: '{{ url_for("disabled.api_attachment_list") }}' });
// 可选 cfg.emptyText / cfg.confirmText：空态与删除确认文案（090C 环卫照片区定制；缺省维持原文案）。
// 可选 cfg.emptyHtml：设计稿定制空态（如 089 文明实践站的圆形图标 + 文案，静态常量、无用户输入）；
// 传入时替换整段空态 HTML 并加 cv-empty-cell 修饰，不传则维持原纯文本渲染，三模块共用零复制。
function attSizeText(n) {
    if (!n || n <= 0) return '0 B';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1024 / 1024).toFixed(2) + ' MB';
}

function initAttachments(cfg) {
    cfg = cfg || {};
    var emptyText = cfg.emptyText || '暂无办事流程模板';
    var emptyHtml = cfg.emptyHtml || '';
    var confirmText = cfg.confirmText || '确定删除该模板附件？';
    var $tb = $('#attTbody');
    if (!$tb.length) return;

    function load() {
        $.get(cfg.base, function (r) {
            if (!r.success) { showToast(r.msg || '加载失败', 'danger'); return; }
            var rows = (r.data && r.data.data) || [];
            if (!rows.length) {
                $tb.html('<tr><td class="sc-table__td sc-table__td--center'
                    + (emptyHtml ? ' cv-empty-cell' : '') + '" colspan="5">'
                    + (emptyHtml || esc(emptyText)) + '</td></tr>');
                return;
            }
            $tb.html(rows.map(function (a) {
                return '<tr>'
                    + '<td class="sc-table__td"><a href="' + cfg.base + '/' + a.id + '/download">' + esc(a.original_name) + '</a></td>'
                    + '<td class="sc-table__td sc-table__td--num">' + attSizeText(a.size) + '</td>'
                    + '<td class="sc-table__td">' + esc(a.uploaded_by || '-') + '</td>'
                    + '<td class="sc-table__td">' + esc(a.created_at || '') + '</td>'
                    + '<td class="sc-table__td"><button type="button" class="sc-btn sc-btn--text sc-text-danger" onclick="onAttachmentDelete(' + a.id + ')"><i class="bi bi-trash"></i> 删除</button></td>'
                    + '</tr>';
            }).join(''));
        });
    }

    window.onAttachmentUpload = function () {
        var input = document.getElementById('attFile');
        if (!input || !input.files || !input.files.length) { showToast('请先选择文件', 'warning'); return; }
        var fd = new FormData();
        fd.append('file', input.files[0]);
        $.ajax({
            url: cfg.base + '/upload',
            type: 'POST',
            data: fd,
            processData: false,
            contentType: false,
            success: function (r) {
                if (r.success) { showToast('上传成功', 'success'); input.value = ''; input.dispatchEvent(new Event('change', { bubbles: true })); load(); }
                else showToast(r.msg || '上传失败', 'danger');
            },
            error: function (xhr) {
                xhr.__handled = true;   // 已就地提示，跳过全局 ajaxError 兜底
                if (xhr.status === 401) { window.location.href = '/auth/login'; return; }
                // 092 验收 5：失败 toast 显示后端 msg（白名单/超限/穿越反例的可见反馈）
                showToast((xhr.responseJSON && xhr.responseJSON.msg) || '上传失败', 'danger');
            }
        });
    };

    window.onAttachmentDelete = function (id) {
        if (!confirm(confirmText)) return;
        apiPost(cfg.base + '/' + id + '/delete', {}, load);
    };

    load();
}

/* SPA 伪导航（spa.js executeScripts → new Function 重放）里顶层 function 声明
   只存在于该次函数作用域，不会挂到 window，各页 extra_js 内联的
   initAttachments({...}) 会抛 ReferenceError（附件空态与上传函数均不初始化）。
   显式导出后，直接访问与侧栏 SPA 进页两条路径都成立；三模块共用脚本零复制。 */
window.initAttachments = initAttachments;

// P2-2 IIFE 收口：局部隔离顶层变量，仅导出模板/内联回调所需入口
var _exports = { attSizeText: attSizeText, initAttachments: initAttachments };
if (window.SC && window.SC.exportPageGlobals) {
    window.SC.exportPageGlobals(_exports);
} else {
    Object.keys(_exports).forEach(function (k) { window[k] = _exports[k]; });
}
})();
