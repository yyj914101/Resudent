(function () {
$(function(){
    $.get('/settings/api/branding/data', function(r){
        if(!r.success) return;
        $('#sysTitle').val(r.data.system_title || '');
        $('#villageName').val(r.data.village_name || '');
        if(r.data.logo_url){
            $('#logoUrl').val(r.data.logo_url);
            $('#logoImg').attr('src', r.data.logo_url).show();
            $('#logoPlaceholder').hide();
        }
    });

    // Preview on file select
    $('#logoFile').on('change', function(){
        var file = this.files[0];
        if(!file) return;
        var reader = new FileReader();
        reader.onload = function(e){
            $('#logoImg').attr('src', e.target.result).show();
            $('#logoPlaceholder').hide();
        };
        reader.readAsDataURL(file);
    });

    $('#removeLogo').on('click', function(){
        $('#logoFile').val('');
        $('#logoImg').hide();
        $('#logoPlaceholder').show();
        $('#logoUrl').val('');
    });

    $('#brandingForm').submit(function(e){
        e.preventDefault();
        var fd = new FormData();
        fd.append('system_title', $('#sysTitle').val());
        fd.append('village_name', $('#villageName').val());
        var file = $('#logoFile')[0].files[0];
        if(file) fd.append('logo_file', file);
        else fd.append('logo_url', $('#logoUrl').val());

        $.ajax({
            url: '/settings/api/branding',
            type: 'POST',
            data: fd,
            processData: false,
            contentType: false,
            success: function(r){
                if(r.success){ showToast('保存成功', 'success'); }
                else showToast(r.msg, 'danger');
            },
            error: function(){ showToast('保存失败', 'danger'); }
        });
    });
});
})();
