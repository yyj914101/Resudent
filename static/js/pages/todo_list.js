(function(){
    var cp = 1, ps = 20, tp = 0, seq = 0, loadingTimer = null, searchTimer = null;


    function toast(msg, type){
        if (typeof showToast === 'function') showToast(msg, type || 'success');
        else alert(msg);
    }

    function modal(){
        var el = document.getElementById('todoFormModal');
        return bootstrap.Modal.getOrCreateInstance(el, {backdrop: 'static'});
    }

    function loadTypes(){
        $.get('/todo/api/types', function(r){
            if(!r.success || !r.data) return;
            var html = '';
            $.each(r.data, function(_, v){ html += '<option value="' + esc(v) + '"></option>'; });
            $('#todoTypeOptions').html(html);
        });
    }

    function renderPager(){
        // R-09：手写分页 → 全局 renderPagination（±2 窗口 + 省略号）
        renderPagination('#pg', tp, cp, loadTodo);
    }

    // 062 P2：批量条「已选 N 项」（字段清单 §三）
    function updateSelStat(){
        $('#selStat').text('已选 ' + $('.cb:checked').length + ' 项');
    }

    function loadTodo(p){
        cp = p || 1;
        var currentSeq = ++seq;
        clearTimeout(loadingTimer);
        loadingTimer = setTimeout(function(){
            if(currentSeq === seq) $('#tb').html('<tr><td colspan="8" class="text-center py-4 text-muted">加载中...</td></tr>');
        }, 120);

        $.get('/todo/api/list', {
            page: cp,
            limit: ps,
            status: $('#filterStatus').val(),
            keyword: $('#searchKeyword').val()
        }).done(function(r){
            if(currentSeq !== seq) return;
            clearTimeout(loadingTimer);
            if(!r.success || !r.data){
                $('#tb').html('<tr><td colspan="8" class="text-center py-4 text-danger">加载失败</td></tr>');
                return;
            }
            var d = r.data, items = d.data || [];
            tp = d.pages || 0;
            $('#pageStat').text('共 ' + d.total + ' 条 · 每页 ' + ps + ' 条');
            var html = '';
            if(!items.length){
                html = '<tr><td colspan="8" class="text-center py-4 text-muted">暂无待办</td></tr>';
            } else {
                $.each(items, function(_, x){
                    // 062 P2 v4.0：徽章换 sc-（组件-handoff §2.4）；行内操作换纯文本按钮（§2.12）
                    var statusLabel = x.status === 'done'
                        ? '<span class="sc-badge sc-badge--success">已完成</span>'
                        : '<span class="sc-badge sc-badge--warning">待办</span>';
                    var overdueBadge = x.overdue ? ' <span class="sc-badge sc-badge--danger">超7天</span>' : '';
                    var completedText = x.completed_by ? esc(x.completed_by) + (x.completed_at ? '<br><small class="text-muted">' + esc(x.completed_at) + '</small>' : '') : '-';
                    html += '<tr id="todo-' + x.id + '">' +
                        '<td><input type="checkbox" class="cb" value="' + x.id + '"></td>' +
                        '<td>' + esc(x.title) + overdueBadge + '</td>' +
                        '<td class="sc-ellipsis" style="max-width:300px">' + esc(x.content) + '</td>' +
                        '<td>' + esc(x.todo_type_label || x.todo_type) + '</td>' +
                        '<td>' + statusLabel + '</td>' +
                        '<td>' + completedText + '</td>' +
                        '<td>' + esc(x.created_at) + '</td>' +
                        '<td><div class="sc-row-actions">' +
                        (x.status === 'todo' ? '<button type="button" class="sc-btn sc-btn--text btn-done" data-id="' + x.id + '">完成</button>' : '') +
                        '<button type="button" class="sc-btn sc-btn--text is-danger btn-del" data-id="' + x.id + '">删除</button>' +
                        '</div></td></tr>';
                });
            }
            $('#tb').html(html);
            $('#checkAll').prop('checked', false);
            updateSelStat();
            renderPager();
        }).fail(function(){
            if(currentSeq === seq) {
                clearTimeout(loadingTimer);
                $('#tb').html('<tr><td colspan="8" class="text-center py-4 text-danger">加载失败</td></tr>');
            }
        });
    }

    function showForm(){
        $('#todoForm')[0].reset();
        $('#todoForm').removeClass('was-validated');
        modal().show();
    }

    function saveTodo(){
        var form = $('#todoForm')[0];
        if(!form.checkValidity()){
            toast('请填写标题', 'danger');
            return;
        }
        $.post('/todo/api/save', $('#todoForm').serialize(), function(r){
            if(r.success){
                toast('新增成功', 'success');
                modal().hide();
                $('#filterStatus').val('todo');
                loadTodo(1);
            } else {
                toast(r.msg || '保存失败', 'danger');
            }
        });
    }

    function batchDone(){
        var ids = $('.cb:checked').map(function(){ return $(this).val(); }).get();
        if(!ids.length){ alert('请选择'); return; }
        $.ajax({url:'/todo/api/batch-done', type:'POST', contentType:'application/json', data:JSON.stringify({ids:ids}), success:function(r){
            if(r.success){ toast(r.msg); loadTodo(1); } else toast(r.msg, 'danger');
        }});
    }

    function batchDelete(){
        var ids = $('.cb:checked').map(function(){ return $(this).val(); }).get();
        if(!ids.length || !confirm('确定删除选中项？')) return;
        $.ajax({url:'/todo/api/batch-delete', type:'POST', contentType:'application/json', data:JSON.stringify({ids:ids}), success:function(r){
            if(r.success){ toast(r.msg); loadTodo(1); } else toast(r.msg, 'danger');
        }});
    }

    function exportTodo(){
        var q = $.param({status:$('#filterStatus').val(), keyword:$('#searchKeyword').val()});
        window.open('/todo/api/export?' + q, '_blank');
    }

    $(document)
        .off('click.todo', '#btnAddTodo').on('click.todo', '#btnAddTodo', showForm)
        .off('click.todo', '#btnSaveTodo').on('click.todo', '#btnSaveTodo', saveTodo)
        .off('change.todo', '#checkAll').on('change.todo', '#checkAll', function(){ $('.cb').prop('checked', $(this).prop('checked')); updateSelStat(); })
        .off('change.todo', '.cb').on('change.todo', '.cb', updateSelStat)
        .off('change.todo', '#filterStatus').on('change.todo', '#filterStatus', function(){ loadTodo(1); })
        .off('input.todo', '#searchKeyword').on('input.todo', '#searchKeyword', function(){
            clearTimeout(searchTimer);
            searchTimer = setTimeout(function(){ loadTodo(1); }, 300);
        })
        .off('click.todo', '.btn-done').on('click.todo', '.btn-done', function(){
            $.post('/todo/api/done/' + $(this).data('id'), function(r){
                if(r.success){ toast('操作成功'); loadTodo(cp); } else toast(r.msg, 'danger');
            });
        })
        .off('click.todo', '.btn-del').on('click.todo', '.btn-del', function(){
            if(!confirm('确定删除？')) return;
            $.post('/todo/api/delete/' + $(this).data('id'), function(r){
                if(r.success){ toast('已删除'); loadTodo(cp); } else toast(r.msg, 'danger');
            });
        });

    window.load = loadTodo;
    window.batchDone = batchDone;
    window.batchDelete = batchDelete;
    window.exportTodo = exportTodo;

    loadTypes();
    loadTodo(1);
})();
