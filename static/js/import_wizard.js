/* import_wizard.js — 居民名单导入四步向导（T01/T02）
 * R-22：全站唯一原生 DOM 页脚本（$=getElementById、el() 用 textContent 无 XSS），
 * 不引 jQuery/spa.js —— 有意保留在 static/js/（非 pages/）。
 * 035 R-035-01：独立整页模板 import_wizard.html 已删（「名单导入」冗余入口清理，
 * 向导唯一入口 = 新增居民页 custom tab）；本脚本由 add.html 引用、
 * resident_add.js 懒挂载（ImportWizardMount / ImportWizardStartWithBatch）。 */
(function () {
    'use strict';
    var batchId = null;
    var taskId = null;
    var mappingColumns = [];   // 服务端返回的列映射
    var catalog = [];          // 目标字段目录（下拉）
    var needAdminConfirm = false;

    function $(id) { return document.getElementById(id); }
    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }
    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined) n.textContent = text;
        return n;
    }
    function alertMsg(type, text) {
        var cls = type === 'error' ? 'alert-danger' : (type === 'warn' ? 'alert-warning' : 'alert-success');
        var box = $('alert-zone');
        box.innerHTML = '';
        var d = el('div', 'alert ' + cls + ' py-2');
        d.style.fontSize = '13px';
        d.textContent = text;
        box.appendChild(d);
    }
    function setStep(active) {
        for (var i = 1; i <= 4; i++) {
            $('pane-' + i).classList.toggle('d-none', i !== active);
            var tab = document.querySelector('.step-tab[data-step="' + i + '"]');
            tab.classList.toggle('active', i === active);
            if (i < active) tab.classList.add('done');
        }
    }
    function post(url, body, ok, err) {
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
            body: JSON.stringify(body || {})
        }).then(function (r) {
            if (!r.ok) return { success: false, msg: '请求失败（HTTP ' + r.status + '）' };
            return r.json();
        }).then(function (res) { res && res.success ? ok(res) : (err && err(res)); })
          .catch(function (e) { err && err({ msg: '网络错误: ' + e.message }); });
    }
    function getJSON(url, ok, err) {
        fetch(url).then(function (r) {
            if (!r.ok) return { success: false, msg: '请求失败（HTTP ' + r.status + '）' };
            return r.json();
        }).then(function (res) { res && res.success ? ok(res) : (err && err(res)); })
          .catch(function (e) { err && err({ msg: '网络错误: ' + e.message }); });
    }
    function matchedBadge(kind) {
        var map = { exact: '精确', template: '模板', fuzzy: '模糊', manual: '手动',
                    preset: '方案', unknown: '未匹配' };
        var cls = { exact: 'bg-success', template: 'bg-primary', fuzzy: 'bg-warning text-dark',
                    manual: 'bg-info', preset: 'bg-primary', unknown: 'bg-secondary' };
        return '<span class="badge ' + (cls[kind] || 'bg-secondary') + ' badge-matched">' + (map[kind] || kind) + '</span>';
    }

    // ---- 步2 渲染助手（035 R-034-02：自 _bind() 闭包提升为模块级）----
    // window.ImportWizardStartWithBatch（闭包外）调用 loadMapping 曾报
    // ReferenceError: loadMapping is not defined（034 验收 R-034-02），
    // 「转自定义导入（免重传）」整链断；其依赖 batchId / mappingColumns /
    // catalog / needAdminConfirm 本就是模块级状态，提升无行为变化。
    function loadMapping(profileKey) {
        var url = '/import/' + batchId + '/mapping';
        if (profileKey) url += '?profile_key=' + encodeURIComponent(profileKey);
        getJSON(url, function (res) {
            mappingColumns = res.data.columns;
            catalog = res.data.catalog;
            renderMapping(res.data);
            setStep(2);
            renderProfileBanner(res.data.profile);
            if (res.data.threshold.blocked) {
                needAdminConfirm = true;
                renderBlockBar(res.data.threshold, true);
            } else {
                needAdminConfirm = false;
                renderBlockBar(res.data.threshold, false);
            }
        }, function (res) { alertMsg('error', res.msg || '加载映射失败'); });
    }

    // 036 F2：方案列表（加载/保存）；无 resident.edit 权限时静默降级为不可用提示
    function loadProfiles() {
        var sel = $('profile-select');
        if (!sel) return;
        getJSON('/import/profiles', function (res) {
            var keep = sel.value;
            while (sel.options.length > 1) sel.remove(1);
            (res.data || []).forEach(function (p) {
                var o = el('option', '', p.name + (p.is_builtin ? '（内置）' : ''));
                o.value = p.profile_key;
                sel.appendChild(o);
            });
            if (keep) sel.value = keep;
            // R-037-03：空列表多为 m014 种子未执行（内置 standard_v1 缺失），给出可行动提示
            $('profile-msg').textContent = (res.data || []).length
                ? '' : '内置方案未初始化，请执行 scripts/migrations/m014 导入';
        }, function (res) {
            $('profile-msg').textContent = res.msg || '方案列表不可用（需居民编辑权限）';
        });
    }

    function renderProfileBanner(profile) {
        var box = $('profile-msg');
        if (!profile) { box.textContent = ''; return; }
        var parts = [];
        if (profile.version_mismatch) {
            parts.push('方案绑定版本 ' + (profile.catalog_version || '未知') +
                       ' 与当前目录不一致，请逐列复核后再保存');
        }
        if (profile.unmatched && profile.unmatched.length) {
            parts.push('方案未覆盖表头：' + profile.unmatched.slice(0, 5).join('、'));
        }
        box.textContent = parts.length ? ('方案「' + profile.name + '」：' + parts.join('；')) : '';
        if (parts.length) alertMsg('warn', '已加载方案「' + profile.name + '」：' + parts.join('；'));
    }

    function renderBlockBar(threshold, show) {
        var bar = $('block-bar');
        if (!show) { bar.innerHTML = ''; return; }
        var d = el('div', 'block-bar alert-danger');
        d.textContent = '阈值守卫阻断：' + threshold.reason + '。请逐列确认（映射/忽略/存入扩展列），仍超阈值需管理员确认豁免。';
        bar.innerHTML = '';
        bar.appendChild(d);
    }

    function renderMapping(data) {
        var list = $('mapping-list');
        list.innerHTML = '';
        data.columns.forEach(function (col, i) {
            var row = el('div', 'mapping-row');
            row.appendChild(el('div', '', col.source));

            var sel = el('select', 'form-select form-select-sm');
            var optEmpty = el('option', '', '— 请选择 —');
            optEmpty.value = '';
            sel.appendChild(optEmpty);
            var optExt = el('option', '', '存入扩展列 ext_data');
            optExt.value = '__ext__';
            sel.appendChild(optExt);
            var optIgnore = el('option', '', '忽略此列');
            optIgnore.value = '__ignore__';
            sel.appendChild(optIgnore);
            catalog.forEach(function (f) {
                var o = el('option', '', f.label);
                o.value = f.code;
                if (col.target === f.code && col.action !== 'ext' && col.action !== 'ignore') o.selected = true;
                sel.appendChild(o);
            });
            if (col.action === 'ext') sel.value = '__ext__';
            else if (col.action === 'ignore') sel.value = '__ignore__';
            else if (!col.target) sel.value = '';
            sel.dataset.idx = i;
            row.appendChild(sel);

            var badge = el('div', '', '');
            badge.innerHTML = matchedBadge(col.matched_by);
            if (col.sensitive) {
                badge.appendChild(el('span', 'badge bg-danger ms-1 badge-matched', '敏感'));
            }
            row.appendChild(badge);
            list.appendChild(row);
        });
    }

    // ---- 步1 上传（事件绑定：mount() 首次激活时挂载） ----
    function _bind() {
    $('btn-upload').addEventListener('click', function () {
        var f = $('file').files[0];
        if (!f) { alertMsg('error', '请选择 .xlsx 文件'); return; }
        var fd = new FormData();
        fd.append('file', f);
        $('upload-msg').textContent = '上传中…';
        fetch('/import/upload', { method: 'POST', body: fd, headers: { 'X-CSRF-Token': csrfToken() } })
            .then(function (r) {
                // 035 R-034-04：非 200（如 413 超限）也尝试解析 JSON 体，让服务端
                // 友好文案（「文件超过 50MB 上限，请分批导入」）直达界面，而非笼统 HTTP 码。
                return r.json().catch(function () {
                    return { success: false, msg: '上传失败（HTTP ' + r.status + '）' };
                });
            })
            .then(function (res) {
                if (!res.success) { alertMsg('error', res.msg); return; }
                batchId = res.data.batch_id;
                $('upload-msg').textContent = '批次创建成功：' + batchId + (res.data.duplicate ? '（重复文件，复用既有批次）' : '');
                if (res.data.version_mismatch) {
                    $('upload-msg').textContent += '；模板版本 ' + (res.data.catalog_version || '未知') +
                        ' 与当前 ' + (res.data.current_catalog_version || '') + ' 不一致，请复核映射';
                }
                // 035 R-034-03②：未完成批次（running/aborted/blocked）→「继续导入」动作——
                // 明示状态并确认后续跑；已成功行按身份证 hash 自动跳过，不重复写入。
                if (res.data.duplicate && res.data.resumable) {
                    if (!confirm('该文件存在未完成批次（状态：' + (res.data.status || '未知') +
                                 '）。\n点击「确定」继续导入（已成功行自动跳过），「取消」则留在本页。')) {
                        alertMsg('warn', '已取消继续导入（批次：' + batchId + '）');
                        return;
                    }
                    alertMsg('warn', '检测到未完成批次（状态：' + (res.data.status || '') + '），继续导入中');
                } else {
                    alertMsg('ok', '上传成功，进入字段映射');
                }
                loadProfiles();
                loadMapping();
            })
            .catch(function (e) { alertMsg('error', '上传失败: ' + e.message); });
    });

    $('btn-save-mapping').addEventListener('click', function () {
        var columns = [];
        document.querySelectorAll('#mapping-list select').forEach(function (sel) {
            var col = mappingColumns[parseInt(sel.dataset.idx)];
            var action, target;
            if (sel.value === '') { action = ''; target = null; }
            else if (sel.value === '__ext__') { action = 'ext'; target = null; }
            else if (sel.value === '__ignore__') { action = 'ignore'; target = null; }
            else { action = 'map'; target = sel.value; }
            // R-27：用户手改预选目标（≠ 服务端 fuzzy 初值）→ 血缘标记 manual，不再沿用 fuzzy
            var matched = (action === 'map' && sel.value !== (col.target || '')) ? 'manual' : col.matched_by;
            columns.push({ source: col.source, action: action, target: target, matched_by: matched });
        });
        var body = { columns: columns, admin_confirm: false };
        if (needAdminConfirm) {
            if (!confirm('检测到阈值守卫阻断，是否以管理员身份确认豁免？')) {
                // 重新按未豁免提交，让服务端返回 need_admin
            } else {
                body.admin_confirm = true;
            }
        }
        post('/import/' + batchId + '/mapping', body, function (res) {
            if (res.data && res.data.blocked) {
                alertMsg('warn', '阈值守卫阻断：' + res.data.reason + '（需管理员确认豁免）');
                return;
            }
            alertMsg('ok', '映射已保存');
            loadValidate();
        }, function (res) { alertMsg('error', res.msg || '保存失败'); });
    });

    // 036 F2：加载方案（仍进入确认页，逐列可改）+ 成功导入后保存为方案
    $('btn-apply-profile').addEventListener('click', function () {
        var key = $('profile-select').value;
        if (!key) { alertMsg('warn', '请先选择方案'); return; }
        loadMapping(key);
    });

    $('btn-save-profile').addEventListener('click', function () {
        var name = prompt('方案名称（下次同源名单可一键复用）：');
        if (!name) return;
        post('/import/profiles', { name: name, batch_id: batchId }, function (res) {
            alertMsg('ok', '方案已保存：' + res.data.name + '（' + res.data.profile_key + '）');
            $('btn-save-profile').style.display = 'none';
            loadProfiles();
        }, function (res) { alertMsg('error', res.msg || '保存方案失败'); });
    });

    // ---- 步3 校验 ----
    function loadValidate() {
        getJSON('/import/' + batchId + '/validate', function (res) {
            renderValidate(res.data);
            setStep(3);
        }, function (res) { alertMsg('error', res.msg || '校验失败'); });
    }

    function renderValidate(summary) {
        var box = $('validate-summary');
        box.innerHTML = '';
        var stats = el('div', 'mb-3');
        stats.innerHTML = '<table class="table table-bordered"><tbody>' +
            '<tr><td>总行数</td><td>' + summary.total + '</td><td>有效</td><td>' + summary.valid + '</td></tr>' +
            '<tr><td>失败</td><td class="text-danger">' + summary.fail + '</td>' +
            '<td>库内重复</td><td>' + summary.duplicate + '（名单内重复 ' + summary.list_dup + '）</td></tr>' +
            '</tbody></table>';
        box.appendChild(stats);
        if (summary.errors.length) {
            box.appendChild(el('div', 'fw-bold mb-1', '问题明细（身份证已脱敏）'));
            var tbl = el('table', 'table table-sm table-striped');
            tbl.innerHTML = '<thead><tr><th>行号</th><th>字段</th><th>类型</th><th>原因</th><th>值(脱敏)</th></tr></thead>';
            var tb = el('tbody');
            summary.errors.forEach(function (e) {
                var tr = el('tr');
                tr.appendChild(el('td', '', e.row_no));
                tr.appendChild(el('td', '', e.field || ''));
                tr.appendChild(el('td', '', e.error_type || ''));
                tr.appendChild(el('td', '', e.reason || ''));
                tr.appendChild(el('td', '', e.value || ''));
                tb.appendChild(tr);
            });
            tbl.appendChild(tb);
            box.appendChild(tbl);
            $('btn-export-failed').style.display = '';
            $('btn-export-failed').href = '/import/' + batchId + '/export-failed';
        } else {
            box.appendChild(el('div', 'text-success', '全部校验通过，可进入确认导入'));
        }
    }

    $('btn-back-mapping').addEventListener('click', function () { setStep(2); });

    // ---- 步3 → 步4 导入 ----
    // 「开始导入」按钮位于步3（提交入口），提交成功后切到步4 展示进度
    $('btn-execute').addEventListener('click', function () {
        post('/import/' + batchId + '/execute', {}, function (res) {
            taskId = res.data.task_id;
            setStep(4);
            $('progress-bar').style.width = '0%';
            $('progress-bar').textContent = '0%';
            $('execute-msg').textContent = '导入任务已提交，处理中…';
            poll();
        }, function (res) { alertMsg('error', res.msg || '启动失败'); });
    });

    function poll() {
        if (!taskId) return;
        getJSON('/import/status/' + taskId, function (res) {
            var d = res.data;
            var p = d.progress || 0;
            $('progress-bar').style.width = p + '%';
            $('progress-bar').textContent = p + '%';
            if (typeof window.ImportWizardOnProgress === 'function') {
                window.ImportWizardOnProgress(d.status, p);
            }
            if (d.status === 'done') {
                $('progress-bar').style.width = '100%';
                $('progress-bar').textContent = '100%';
                alertMsg('ok', d.result_summary || '导入完成');
                $('btn-view-report').style.display = '';
                $('btn-save-profile').style.display = '';
                return;
            }
            if (d.status === 'failed') {
                alertMsg('error', '导入失败：' + (d.result_summary || ''));
                return;
            }
            $('execute-msg').textContent = d.result_summary || '';
            setTimeout(poll, 1500);
        }, function () { setTimeout(poll, 2000); });
    }

    $('btn-view-report').addEventListener('click', function () {
        getJSON('/import/' + batchId + '/report', function (res) {
            var d = res.data;
            var msg = '成功 ' + d.success_rows + ' / 失败 ' + d.fail_rows +
                      ' / 跳过(重复) ' + d.skip_rows + ' / 新增字段 ' + d.new_field_count;
            alertMsg('ok', '导入报告：' + msg);
        }, function (res) { alertMsg('error', res.msg || '读取报告失败'); });
    });
    }  // end _bind

    // 034 P2：mount() 幂等挂载（工作台 custom tab 懒初始化与独立单页共用）
    var mounted = false;
    function mount() {
        if (mounted) return;
        mounted = true;
        _bind();
    }
    window.ImportWizardMount = mount;

    // 035 R-034-02 导出对账（023/024 IIFE 教训：跨文件调用须显式对账）——
    // 本 IIFE 对外暴露仅三个符号，均由 resident_add.js（三模式工作台）消费：
    //   ImportWizardMount          —— custom tab 首次激活时懒挂载（幂等）
    //   ImportWizardStartWithBatch —— 模板通道阻断批次免重传转入（loadMapping 已提升模块级，闭包外可调）
    //   ImportWizardOnProgress     —— 导入进度回调（工作台 custom 徽标）

    // 034 P2 目标9：转自定义免重传——模板通道被阻断的批次由工作台递入，
    // 直达步2 逐列映射（不再重传文件）。
    window.ImportWizardStartWithBatch = function (id, reason) {
        mount();
        if (!id) return;
        batchId = id;
        taskId = null;
        if (reason) alertMsg('warn', reason);
        setStep(2);
        loadProfiles();
        loadMapping();
    };

    // 进度挂钩：工作台 tab 徽标等外部监听方自行赋值（导入状态下 (status, p) 回调）
    window.ImportWizardOnProgress = null;

    // 035 R-035-01：独立整页（import_wizard.html，无 base 壳）已删——挂载不再自动触发，
    // 仅由 resident_add.js 按需调用（custom tab 首次激活 → ImportWizardMount；
    // 模板阻断免重传 → ImportWizardStartWithBatch）。

})();
