(function () {
$(function(){
    $.get('/settings/api/notification/data', function(r){
        if(!r.success) return;
        var d = r.data;
        $('#sendTime').val(d.send_time || '08:00');
        if(d.enable_birthday_reminder === '1') $('#enableBirthday').prop('checked', true);

        var html = '';
        [7,18,60,70,80,90,100].forEach(function(age){
            var enabled = d['enable_age_'+age] === '1';
            var advance = d['age_'+age+'_advance'] || '30';
            var label = d['age_'+age+'_label'] || '年满'+age+'岁';
            html += '<div class="sc-field" style="border:1px solid var(--c-line-3);border-radius:var(--r-card);padding:var(--sp-3);margin-bottom:var(--sp-2)">' +
                '<input class="sc-checkbox" type="checkbox" name="enable_age_'+age+'" value="1"'+(enabled?' checked':'')+'>' +
                '<label class="sc-field__label" style="flex:1;color:var(--c-ink-2);font-weight:600">'+label+'</label>' +
                '<span class="text-muted small">提前</span>' +
                '<input type="number" class="sc-input" name="age_'+age+'_advance" value="'+advance+'" style="flex:none;width:80px" min="1" max="90">' +
                '<span class="text-muted small">天提醒</span></div>';
        });
        $('#ageSettings').html(html);
    });

    $('#notifForm').submit(function(e){
        e.preventDefault();
        $.post('/settings/api/notification', $(this).serialize(), function(r){
            if(r.success){ showToast('保存成功','success'); }
            else showToast(r.msg,'danger');
        });
    });
});
})();
