(function () {
var householdLookupTimer = null;
var isExistingHousehold = false;    // true=同户新增, false=新增一户
var currentHouseholdCode = '';      // 当前户编号

$(function() {
    // ── 034 P2：三模式工作台 tab 逻辑 ──
    var customMounted = false;
    var customTab = document.getElementById('custom-tab');
    var customBadge = document.getElementById('customBadge');
    if (customTab) {
        customTab.addEventListener('shown.bs.tab', function () {
            // 懒初始化：首次进入自定义 tab 才挂载四步向导（幂等）
            if (!customMounted && window.ImportWizardMount) {
                customMounted = true;
                window.ImportWizardMount();
            }
        });
        // 035：?tab=custom 深链直载时该 tab 服务端已渲染为 active，Bootstrap 不会触发
        // shown.bs.tab —— 若不在首屏挂载，向导步1「上传并创建批次」按钮无监听、点击无声
        // （035 浏览器实测发现）。首页空态按钮正指向 ?tab=custom，故此处必须挂载。
        if (customTab.classList.contains('active') && window.ImportWizardMount) {
            customMounted = true;
            window.ImportWizardMount();
        }
    }
    if (customTab && customBadge) {
        // 自定义导入进度徽标：后台任务继续期间切 tab 仍可见进度（034 验收 6）
        window.ImportWizardOnProgress = function (status, p) {
            if (status === 'done' || status === 'failed') {
                customBadge.classList.add('d-none');
                customBadge.textContent = '';
                return;
            }
            customBadge.classList.remove('d-none');
            customBadge.textContent = (Math.round(p) || 0) + '%';
        };
    }
    // 模板模式阻断 → 页内「转自定义导入」：同一批次免重传进入自定义映射（034 目标9）
    var gotoCustomWithBatch = function (batchId, reason) {
        if (!customTab || !window.ImportWizardStartWithBatch) return;
        if (window.ImportWizardMount) window.ImportWizardMount();
        window.ImportWizardStartWithBatch(batchId, reason);
        customTab.click();
    };
    // 页内 tab 切换 → replaceState 自维护 URL（服务端已按 ?tab= 渲染初始态；spa.js 丢 query 不依赖）
    // 注意：data-bs-target 指向 pane id（manual/import/custom），与服务端契约名
    // （manual/template/custom）不同——须映射后再写 URL，否则刷新会回退手动录入。
    var PANE_TAB = { manual: 'manual', import: 'template', custom: 'custom' };
    $('#addTabs').on('shown.bs.tab', function (e) {
        var t = (e.target && e.target.getAttribute('data-bs-target')) || '';
        var paneId = t.replace('#', '') || 'manual';
        var tabName = PANE_TAB[paneId] || 'manual';
        try { history.replaceState({ path: location.pathname }, '',
            location.pathname + '?tab=' + tabName); } catch (err) {}
    });
    // 手动录入未保存内容离开提醒（034 验收 6）
    window.addEventListener('beforeunload', function (e) {
        var name = $('#rName').val();
        var hh = $('#rHouseholdName').val();
        if ((name && name.trim()) || (hh && hh.trim())) {
            e.preventDefault();
        }
    });

    // Load dictionary dropdowns
    loadDict('所在组', '#rGroup');
    loadDict('所在组', '#rMemberGroup');
    loadDict('户口类型', '#rHouseholdType');
    loadDict('户属性', '#rProperty');
    loadDict('民族', '#rNation');
    loadDict('开户行', '#rBankName');
    loadDict('政治面貌', '#rPolitics');
    loadDict('学历', '#rEducation');
    loadDict('健康状态', '#rHealth');
    loadDict('婚姻状况', '#rMarital');
    loadDict('兵役状况', '#rMilitary');
    loadDict('与户主关系', '#rRelation');

    // ─── 户主名输入：自动查找已有家庭 ───
    $('#rHouseholdName').on('input', function() {
        var name = $(this).val().trim();
        if (name.length < 1) return;

        clearTimeout(householdLookupTimer);
        householdLookupTimer = setTimeout(function() {
            lookupHousehold(name);
        }, 400);  // 400ms 防抖
    });

    $('#rGroup').on('change', function() {
        if ($('#rMemberGroup').val() !== $(this).val()) {
            $('#rMemberGroup').val($(this).val());
        }
    });
    $('#rMemberGroup').on('change', function() {
        $('#rGroup').val($(this).val());
    });

    // ─── 提交表单 ───
    $('#addResidentForm').submit(function(e) {
        e.preventDefault();
        var form = this;
        syncMemberGroupToHousehold();

        // 如果是新增一户且关系为户主本人，校验左侧户主信息必填
        var rel = $('#rRelation').val();
        if (!isExistingHousehold && (rel === '户主本人' || rel === '')) {
            var hhFields = [
                {id:'rHouseholdName', name:'户主名'},
                {id:'rPhone', name:'联系电话'},
                {id:'rHouseholdType', name:'户口类型'},
                {id:'rGroup', name:'所在组'},
                {id:'rProperty', name:'户属性'},
                {id:'rAddress', name:'家庭住址'},
            ];
            var hhOk = true;
            hhFields.forEach(function(f) {
                var val = $('#' + f.id).val();
                if (!val || val.trim() === '') {
                    $('#' + f.id).addClass('is-invalid');
                    hhOk = false;
                } else {
                    $('#' + f.id).removeClass('is-invalid');
                }
            });
            if (!hhOk) {
                showToast('新增一户时，户主信息（户主名、联系电话、户口类型、所在组、户属性、家庭住址）为必填项', 'warning');
                return;
            }
        }

        // HTML5 constraint validation（必填 select 由 app.js csGuardRequired 在捕获阶段
        // 拦下并给 toast + 聚焦触发器；此处只兜可见输入框的剩余校验）
        if (!form.checkValidity()) {
            form.classList.add('was-validated');
            return;
        }

        var $btn = $('#submitBtn');
        $btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span> 保存中...');

        var formData = $(this).serialize();

        $.post('/resident/api/save', $(this).serialize(), function(resp) {
            $btn.prop('disabled', false).html('<i class="bi bi-save"></i> 保存');
            if (resp.success) {
                showToast('保存成功', 'success');
                if (!isExistingHousehold) {
                    // 新增一户 → 记录当前户编号，然后重置页面
                    currentHouseholdCode = resp.data.household_code || '';
                    resetForm(true);
                } else {
                    // 同户新增 → 保留已存在户的信息，清空居民信息部分
                    resetMemberFields();
                }
            } else {
                showToast(resp.msg, 'danger');
            }
        }).fail(function() {
            $btn.prop('disabled', false).html('<i class="bi bi-save"></i> 保存');
            showToast('网络错误，请重试', 'danger');
        });
    });

    // ─── 手动重置按钮 ───
    $('#resetBtn').click(function() {
        if (confirm('确定要重置表单吗？已填写的数据将会清空。')) {
            resetForm(false);
        }
    });

    // ── 批量导入：投放区暂存 → 页脚主按钮提交（UI 对齐 File Drop Zone 设计稿；
    //    端点 /resident/api/import、载荷（file+mode）、校验与结果渲染逻辑与 034 完全一致） ──
    $('#custom .step-tab').each(function () {
        // 步骤条改「编号圆点 + 文案」两段式（import_wizard.js 只切 active/done 类，不读文本）
        var num = this.getAttribute('data-step') || '';
        var label = $.trim($(this).text()).replace(/^\d+\s*/, '');
        this.innerHTML = '<span class="rw-step__num">' + esc(num) + '</span>' +
                         '<span class="rw-step__label">' + esc(label) + '</span>';
    });

    var stagedImportFile = null;
    var $importInput = $('#addImportFile');
    var $importDrop = $('#addImportDrop');
    var $importStaged = $('#addImportStaged');
    var $importSubmit = $('#addImportSubmit');

    function fmtFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(2) + ' MB';
    }

    function stageImportFile(file) {
        // 与服务端护栏同口径（import_engine.start_batch：仅 .xlsx、≤50MB），拦截后再提交
        if (!/\.(xlsx|xls|xlsb|csv|xlsm)$/i.test(file.name)) {
            showToast('仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式', 'warning');
            return;
        }
        if (file.size > 50 * 1024 * 1024) {
            showToast('文件超过 50MB 上限，请分批导入', 'warning');
            return;
        }
        stagedImportFile = file;
        $('#addImportStagedName').text(file.name);
        $('#addImportStagedMeta').text(fmtFileSize(file.size) + ' · 已就绪，等待解析入库');
        $importDrop.addClass('d-none');
        $importStaged.removeClass('d-none');
        $importSubmit.prop('disabled', false);
    }

    function clearStagedImport() {
        stagedImportFile = null;
        $importInput.val('');
        $importStaged.addClass('d-none');
        $importDrop.removeClass('d-none');
        $importSubmit.prop('disabled', true);
        $('#addImportProgress').addClass('d-none');
    }

    $importDrop.on('click', function () { $importInput.trigger('click'); })
        .on('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                $importInput.trigger('click');
            }
        })
        .on('dragover', function (e) {
            e.preventDefault();
            $(this).addClass('is-drag');
        })
        .on('dragleave drop', function () { $(this).removeClass('is-drag'); })
        .on('drop', function (e) {
            e.preventDefault();
            var dt = e.originalEvent && e.originalEvent.dataTransfer;
            if (dt && dt.files && dt.files.length) {
                $importInput[0].files = dt.files;   // 复用同一 input，提交仍走既有读取路径
                stageImportFile(dt.files[0]);
            }
        });
    $importInput.on('change', function () {
        if (this.files[0]) stageImportFile(this.files[0]);
    });
    $('#addImportReselect').on('click', function () { $importInput.trigger('click'); });
    $('#addImportRemove').on('click', clearStagedImport);
    // 清空重置 = 文件 + 导入模式 + 回执三清（与「移除」区分：设计稿页脚动作）
    $('#addImportReset').on('click', function () {
        clearStagedImport();
        $('#addImportResult').html('');
        $('input[name="addImportMode"][value="append"]').prop('checked', true);
    });
    $importSubmit.on('click', function () {
        if (stagedImportFile) uploadTemplateImport(stagedImportFile);
    });

    function uploadTemplateImport(file) {
        var mode = $('input[name="addImportMode"]:checked').val();
        var fd = new FormData();
        fd.append('file', file);
        fd.append('mode', mode);

        $('#addImportProgress').removeClass('d-none');
        $('#addImportResult').html('');
        $importSubmit.prop('disabled', true);

        $.ajax({
            url: '/resident/api/import',
            type: 'POST',
            data: fd,
            processData: false,
            contentType: false,
            success: function(resp) {
                $('#addImportProgress').addClass('d-none');
                var cls = resp.success ? 'alert-success' : 'alert-warning';
                var extra = '';
                if (resp.data && resp.data.async) {
                    extra = '<div class="small text-muted mt-1">大文件已进入后台执行，可稍后刷新本页查看结果（批次：' +
                        esc(resp.data.batch_id || '') + '）</div>';
                }
                var html = '<div class="alert ' + cls + ' py-2 small mb-0">' + esc(resp.msg) + '</div>' + extra;
                // 142：duplicate 且批次 0 行可续（mapped/blocked）→ 提供「继续导入」，
                // 跳到该批次的步3 校验执行页（复用既有批次，免重传）。
                var dupData = (resp.data && resp.data.duplicate && resp.data.batch_id) ? resp.data : null;
                if (dupData && (dupData.status === 'mapped' || dupData.status === 'blocked')) {
                    html += '<div class="mt-2"><a class="btn btn-sm btn-primary" href="/import/' +
                        esc(dupData.batch_id) + '/validate">继续导入（批次 ' + esc(dupData.batch_id) + '，已完成字段映射）</a></div>';
                }
                // 034 目标9：缺必需列/模板外列导致阻断 → 页内转自定义（同批次免重传）
                var blockedData = (resp.data && resp.data.blocked && resp.data.batch_id) ? resp.data : null;
                if (blockedData && customTab) {
                    html += '<button type="button" class="btn btn-sm btn-outline-primary mt-2" id="btnGoCustom">' +
                        '转自定义导入（免重传，逐列映射）</button>';
                }
                $('#addImportResult').html(html);
                if (blockedData) {
                    $('#btnGoCustom').on('click', function () {
                        var missing = (blockedData.missing && blockedData.missing.length)
                            ? '缺失必需列：' + blockedData.missing.join('、') : '';
                        gotoCustomWithBatch(blockedData.batch_id,
                            '已从批量导入转自定义' + (missing ? '——' + missing : '：请逐列确认映射'));
                    });
                }
                if (resp.success) {
                    clearStagedImport();      // 成功后回到投放区；结果区保留回执
                    $('#addImportProgress').addClass('d-none');
                } else {
                    $importSubmit.prop('disabled', false);
                }
            },
            error: function() {
                $('#addImportProgress').addClass('d-none');
                $importSubmit.prop('disabled', false);
                $('#addImportResult').html('<div class="alert alert-danger py-2 small mb-0">导入请求失败</div>');
            }
        });
    }

    // ── 自定义名单：投放区暂存 → 页脚「上传并创建批次」（提交按钮由 import_wizard.js
    //    绑定在 #btn-upload 上，本段只负责选文件/拖放/暂存态与按钮解锁，不改上传流程） ──
    var $customInput = $('#file');
    var $customDrop = $('#customDrop');
    var $customStaged = $('#customStaged');
    var $customBtn = $('#btn-upload');

    function stageCustomFile(file) {
        if (!/\.(xlsx|xls|xlsb|csv|xlsm)$/i.test(file.name)) {
            showToast('仅支持 .xlsx / .xls / .xlsb / .csv / .xlsm 格式', 'warning');
            return;
        }
        if (file.size > 50 * 1024 * 1024) {
            showToast('文件超过 50MB 上限，请分批导入', 'warning');
            return;
        }
        $('#customStagedName').text(file.name);
        $('#customStagedMeta').text(fmtFileSize(file.size) + ' · 已就绪，等待上传解析');
        $customDrop.addClass('d-none');
        $customStaged.removeClass('d-none');
        $customBtn.prop('disabled', false);
    }

    function clearCustomFile() {
        $customInput.val('');
        $customStaged.addClass('d-none');
        $customDrop.removeClass('d-none');
        $customBtn.prop('disabled', true);
        $('#upload-msg').text('');
    }

    if ($customDrop.length) {
        $customDrop.on('click', function () { $customInput.trigger('click'); })
            .on('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    $customInput.trigger('click');
                }
            })
            .on('dragover', function (e) {
                e.preventDefault();
                $(this).addClass('is-drag');
            })
            .on('dragleave drop', function () { $(this).removeClass('is-drag'); })
            .on('drop', function (e) {
                e.preventDefault();
                var dt = e.originalEvent && e.originalEvent.dataTransfer;
                if (dt && dt.files && dt.files.length) {
                    $customInput[0].files = dt.files;
                    stageCustomFile(dt.files[0]);
                }
            });
        $customInput.on('change', function () {
            if (this.files[0]) stageCustomFile(this.files[0]);
        });
        $('#customReselect').on('click', function () { $customInput.trigger('click'); });
        $('#customRemove, #customReset').on('click', clearCustomFile);
    }
});

// ─── 查询家庭是否存在 ───
function lookupHousehold(name) {
    $.get('/resident/api/lookup', {name: name}, function(resp) {
        if (resp.success && resp.data && resp.data.length > 0) {
            // 找到已存在的户——先找户主本人
            var household = null;
            var headRelations = ['户主本人', '户主', '本人'];
            for (var i = 0; i < resp.data.length; i++) {
                if (resp.data[i].name === name && headRelations.indexOf(resp.data[i].relationship) !== -1) {
                    household = resp.data[i];
                    break;
                }
            }
            if (!household) household = resp.data[0];

            // 再看是否有 household_code
            if (household.household_code) {
                // 是该户已有信息 → 自动填充户信息
                isExistingHousehold = true;
                currentHouseholdCode = household.household_code;
                $('#rHouseholdCode').val(household.household_code);
                $('#rPhone').val(household.phone);
                if (household.village_group) {
                    $('#rGroup').val(household.village_group);
                    $('#rMemberGroup').val(household.village_group);
                }
                if (household.household_type) $('#rHouseholdType').val(household.household_type);
                if (household.household_property) $('#rProperty').val(household.household_property);
                if (household.address) $('#rAddress').val(household.address);
                // 提示用户
                showToast('已找到"' + name + '"的家庭信息，请填写家庭成员信息', 'info');
                return;
            }
        }
        // 没找到 → 新增一户
        isExistingHousehold = false;
        currentHouseholdCode = '';
        $('#rHouseholdCode').val('');
        showToast('未找到"' + name + '"的家庭信息，将作为新增一户处理', 'info');
    }).fail(function() {
        // 查询失败，仍按新增一户处理
        isExistingHousehold = false;
    });
}

function syncMemberGroupToHousehold() {
    var memberGroup = $('#rMemberGroup').val();
    if (memberGroup) {
        $('#rGroup').val(memberGroup);
    } else if ($('#rGroup').val()) {
        $('#rMemberGroup').val($('#rGroup').val());
    }
}

// ─── 重置表单 ───
function resetForm(keepCode) {
    var savedCode = currentHouseholdCode;
    $('#addResidentForm')[0].reset();
    $('#residentId').val('');
    $('#rHouseholdCode').val('');
    $('#rRelation').val('户主本人');
    isExistingHousehold = false;
    currentHouseholdCode = '';
    if (keepCode && savedCode) {
        currentHouseholdCode = savedCode;
        $('#rHouseholdCode').val(savedCode);
    }
}

// ─── 仅清空居民信息字段（保存家庭成员后） ───
function resetMemberFields() {
    $('#rName').val('');
    $('#rIdCard').val('');
    $('#rGender').val('');
    $('#rBirthDate').val('');
    $('#rNation').val('');
    $('#rMemberGroup').val($('#rGroup').val() || '');
    $('#rPolitics').val('');
    $('#rEducation').val('');
    $('#rOccupation').val('');
    $('#rHealth').val('');
    $('#rMarital').val('');
    $('#rMilitary').val('');
    $('#rRelation').val('');
    $('#rBankName').val('');
    $('#rBankCard').val('');
    $('#rRemark').val('');
    $('#residentId').val('');
}

// ─── 身份证自动填充 ───
function autoFillIdCard() {
    var card = $('#rIdCard').val().trim();
    if (card.length !== 18) return;
    var genderDigit = parseInt(card.charAt(16));
    if (!isNaN(genderDigit)) {
        $('#rGender').val(genderDigit % 2 === 1 ? '男' : '女');
    }
    var year = card.substring(6, 10);
    var month = card.substring(10, 12);
    var day = card.substring(12, 14);
    if (year && month && day) {
        $('#rBirthDate').val(year + '-' + month + '-' + day);
    }
}

function loadDict(category, selector) {
    dictSelect(category, selector, '请选择');
}

// ── 024 IIFE 导出：SPA 每次进页在本作用域重放，仅以下符号对内联事件/外部全局可见 ──
window.autoFillIdCard = autoFillIdCard;
})();
