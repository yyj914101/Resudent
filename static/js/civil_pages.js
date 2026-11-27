/* civil_pages.js — 民政对象 10 页公共交互（059）
 * 只做两件事：
 *   1) 宽表列选择器：按表头生成下拉复选，显隐列并写 localStorage
 *   2) 渲染后保持显隐（tbody 由页面 JS 异步填充，用 MutationObserver 追平）
 * 列选择器默认列在模板上是 data-default-cols（用 | 分隔，空 = 全部显示）。
 * 依赖 jQuery；不依赖 Bootstrap JS 之外任何库。
 */
(function () {
    var LS_PREFIX = 'civil-cols:';

    function splitCols(s) {
        return String(s || '').split('|').map(function (x) { return x.trim(); }).filter(Boolean);
    }

    function initPicker($picker) {
        if ($picker.data('cp-init')) return;
        var $table = $picker.closest('.sc-card').find('table').first();
        if (!$table.length) return;
        $picker.data('cp-init', true);

        var key = String($picker.attr('data-col-picker-key') || '');
        var labels = [];
        $table.find('thead tr').first().children('th,td').each(function () {
            labels.push($(this).text().trim());
        });
        var n = labels.length;
        if (!n) return;

        var defaults = splitCols($picker.attr('data-default-cols'));
        var visible = labels.map(function (label) {
            return defaults.length ? defaults.indexOf(label) >= 0 : true;
        });
        try {
            var saved = JSON.parse(localStorage.getItem(LS_PREFIX + key) || 'null');
            if (saved && saved.length === n) visible = saved.map(Boolean);
        } catch (e) { /* 旧缓存损坏 → 用默认列 */ }

        var items = labels.map(function (label, i) {
            return '<label class="dropdown-item sc-col-opt">' +
                '<input type="checkbox" class="form-check-input sc-col-check" value="' + i + '"' +
                (visible[i] ? ' checked' : '') + '> ' + (window.esc ? esc(label) : label) +
                '</label>';
        }).join('');

        // 074 RC-08：表格上方独立一行 —— 左「已选 X / 共 Y 列」，右「列选择」按钮（样图 v3-01）
        function countLabel() {
            return '已选 ' + visible.filter(Boolean).length + ' / 共 ' + n + ' 列';
        }
        var $count = $picker.closest('.sc-table-toolbar').find('[data-col-count]');

        $picker.html(
            '<div class="dropdown d-inline-block">' +
            '<button type="button" class="sc-btn sc-btn--secondary dropdown-toggle sc-col-toggle" data-bs-toggle="dropdown" aria-expanded="false">列选择</button>' +
            '<ul class="dropdown-menu dropdown-menu-end">' + items + '</ul>' +
            '</div>');
        $count.text(countLabel());

        function apply() {
            $table.find('thead tr').first().children('th,td').each(function (i) {
                $(this).toggle(visible[i] !== false);
            });
            $table.find('tbody tr, tfoot tr').each(function () {
                $(this).children('th,td').each(function (i) {
                    $(this).toggle(visible[i] !== false);
                });
            });
            $count.text(countLabel());
        }

        $picker.on('change', '.sc-col-check', function () {
            visible[parseInt(this.value, 10)] = this.checked;
            apply();
            try { localStorage.setItem(LS_PREFIX + key, JSON.stringify(visible)); } catch (e) { /* 隐私模式忽略 */ }
        });
        // 勾选不收起下拉（Bootstrap 默认点菜单项会关闭）
        $picker.on('click', '.dropdown-menu', function (e) { e.stopPropagation(); });

        apply();
        var tbody = $table.find('tbody')[0];
        if (tbody && window.MutationObserver) {
            new MutationObserver(apply).observe(tbody, { childList: true });
        }
    }

    // ── 074 RC-07：工具条「重置」+ 右侧「共 N 人」────────────────────────
    // 重置不各页实现：清空工具条内 input/select 后点一次既有的查询入口（主按钮 / form submit），
    // 这样新增页面零改动即可复用。
    function initToolbarExtras() {
        $('[data-toolbar-reset]').each(function () {
            var $btn = $(this);
            if ($btn.data('tr-init')) return;
            $btn.data('tr-init', true);
            $btn.on('click', function () {
                var $tb = $btn.closest('.sc-toolbar');
                $tb.find('input.sc-input').val('');
                $tb.find('select.sc-select').prop('selectedIndex', 0);
                $tb.find('.sc-btn--primary').first().trigger('click');
            });
        });

        // 计数从分页统计「共 N 条 · 每页 M 条」抽 N —— 低保/残疾/特困列表一行即一人，口径一致
        var $count = $('[data-toolbar-count]').first();
        var $stat = $('#pageStat').first();
        if (!$count.length || !$stat.length) return;
        function syncCount() {
            var m = /共\s*([\d,]+)\s*条/.exec($stat.text() || '');
            $count.text(m ? '共 ' + m[1] + ' 人' : '');
        }
        syncCount();
        if (window.MutationObserver) {
            new MutationObserver(syncCount).observe($stat[0], { childList: true, characterData: true, subtree: true });
        }
    }

    $(function () {
        $('[data-col-picker-key]').each(function () { initPicker($(this)); });
        initToolbarExtras();
    });
})();
