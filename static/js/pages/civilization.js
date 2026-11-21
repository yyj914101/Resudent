/* static/js/pages/civilization.js — 089 文明实践站门面页（指标卡 + 公约换版流）
 * UI：ADR-022 去噪版（2026-11-13）——公约正文按行渲染为 ol 条款、历史版本收纳折叠、
 *     隐藏 file input 的 change 触发 092 共用上传；启动走 boot + readyState 兼容 SPA。
 *
 * API 契约（routes/civilization.py，MetricsEngine 单一取数入口）：
 * - GET  /civilization/api/stats?year=YYYY            → {sessions:{data:{sessions}}, person_visits:{data:{person_visits}}}
 * - GET  /civilization/api/conventions                → {current:{...}|null, history:[...]}（历史按创建时间倒序）
 * - POST /civilization/api/conventions                → 新增版本（新版本置现行，旧现行同事务转历史）
 * - POST /civilization/api/conventions/<id>           → 编辑（仅现行；历史 403）
 * - POST /civilization/api/conventions/<id>/delete    → 删除（仅历史；现行 400）
 * 人次口径 Q-B：Σ 参与人数逐场累加不去重（服务端计算，页面零二次计算）。
 */
(function () {
    'use strict';

    var API = {
        stats: '/civilization/api/stats',
        list: '/civilization/api/conventions',
        save: '/civilization/api/conventions',
        edit: '/civilization/api/conventions/',
        remove: '/civilization/api/conventions/'
    };

    function request(url, opts) {
        return fetch(url, opts || {}).then(function (resp) {
            if (resp.status === 401) { window.location.href = '/auth/login'; return null; }
            return resp.text().then(function (text) {
                var body = null;
                try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
                if (!body || typeof body !== 'object') {
                    body = { success: false, msg: '返回内容解析失败（HTTP ' + resp.status + '）' };
                }
                body.__status = resp.status;
                return body;
            });
        }, function () {
            return { success: false, msg: '网络异常，请检查连接后重试', __status: 0 };
        });
    }

    function post(url, payload) {
        return request(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        });
    }

    function report(body, okText) {
        if (!body) return;
        if (body.success) { showToast(okText || body.msg || '操作成功', 'success'); return; }
        var tone = (body.__status === 400 || body.__status === 409) ? 'warning' : 'danger';
        showToast(body.msg || ('操作失败（HTTP ' + (body.__status || 0) + '）'), tone);
    }

    function d(v) { return v === null || v === undefined || v === '' ? '—' : v; }

    /* ── 指标卡：年度场次 / 年度人次（默认自然年；口径 Q-A/Q-B 与一页纸 v1.2 §4 同源） ── */
    function loadStats() {
        var year = new Date().getFullYear();
        var statYear = document.getElementById('civStatYear');
        if (statYear) statYear.textContent = year;
        request(API.stats + '?year=' + year).then(function (body) {
            if (!body || !body.success) return;
            var s = (body.data.sessions && body.data.sessions.data) || {};
            var p = (body.data.person_visits && body.data.person_visits.data) || {};
            document.getElementById('civSessions').textContent = s.sessions != null ? s.sessions : 0;
            document.getElementById('civPersonVisits').textContent = p.person_visits != null ? p.person_visits : 0;
        });
    }

    /* ── 公约卡：现行版本（设计稿内容盒 + 有序条款）+ 历史版本折叠 ── */

    /* 正文按行拆为条款：剥离原文「一、」「1.」「(1)」等手写序号，
       序号统一交给 ol 生成（设计稿 list-decimal 口径），正文内容零改写。 */
    function clauseLines(text) {
        return String(text || '').split(/\r?\n/).map(function (line) {
            return line.trim().replace(
                /^(?:[一二三四五六七八九十百]+[、.．]|\d+[、.．)）]|[（(]\d+[)）])\s*/, ''
            ).trim();
        }).filter(function (s) { return s.length > 0; });
    }

    function renderCurrent(row) {
        var box = document.getElementById('currentConvention');
        var editBtn = document.getElementById('editConventionBtn');
        if (!row) {
            box.innerHTML = '<p class="cv-placeholder">尚未发布居民公约：点击「新增版本」发布第一版。</p>';
            editBtn.style.display = 'none';
            return;
        }
        editBtn.style.display = '';
        editBtn.dataset.id = row.id;
        var html = '<div class="cv-convention">' +
            '<div class="cv-convention__meta">现行版本：<strong>' + esc(row.title) + '</strong>' +
            '（更新于 <span class="cv-convention__num">' + esc(d(row.updated_at)) + '</span>' +
            (row.created_by ? ' · 登记人 ' + esc(row.created_by) : '') + '）</div>';
        var lines = clauseLines(row.content);
        if (lines.length) {
            html += '<ol class="cv-convention__list">';
            for (var i = 0; i < lines.length; i++) {
                html += '<li>' + esc(lines[i]) + '</li>';
            }
            html += '</ol>';
        } else {
            html += '<p class="cv-placeholder">本版本未填写正文，可点击「编辑现行版本」补充。</p>';
        }
        if (row.attachment_path) {
            html += '<div class="cv-convention__attachment"><span class="sc-badge sc-badge--muted">' +
                '<i class="bi bi-paperclip"></i> 附件：' + escAttr(row.attachment_path) + '</span></div>';
        }
        html += '</div>';
        box.innerHTML = html;
    }

    function renderHistory(rows) {
        var fold = document.getElementById('conventionHistoryFold');
        var wrap = document.getElementById('conventionHistoryWrap');
        var tbody = document.getElementById('conventionHistoryBody');
        var count = document.getElementById('conventionHistoryCount');
        var hasRows = !!(rows && rows.length);
        if (count) count.textContent = hasRows ? rows.length : 0;
        if (fold) fold.hidden = !hasRows;
        if (!hasRows) { wrap.style.display = 'none'; tbody.innerHTML = ''; return; }
        var html = '';
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            html += '<tr>' +
                '<td>' + esc(d(r.title)) + '</td>' +
                '<td><span class="sc-badge sc-badge--muted">历史</span></td>' +
                '<td>' + esc(d(r.created_at)) + '</td>' +
                '<td>' + esc(d(r.created_by)) + '</td>' +
                '<td><button type="button" class="sc-btn sc-btn--secondary" data-del-id="' + r.id + '">删除</button></td>' +
                '</tr>';
        }
        tbody.innerHTML = html;
        wrap.style.display = '';
    }

    function loadConventions() {
        request(API.list).then(function (body) {
            if (!body || !body.success) return;
            renderCurrent(body.data.current);
            renderHistory(body.data.history);
        });
    }

    /* ── 新增 / 编辑表单（modal）：保存后本版本即为现行，原现行自动转历史 ── */
    function openForm(row) {
        var isEdit = !!row;
        document.getElementById('conventionId').value = isEdit ? row.id : 0;
        document.getElementById('conventionModalTitle').textContent = isEdit ? '编辑现行公约' : '新增公约版本';
        document.getElementById('f_title').value = isEdit ? (row.title || '') : '';
        document.getElementById('f_content').value = isEdit ? (row.content || '') : '';
        document.getElementById('f_attachment_path').value = isEdit ? (row.attachment_path || '') : '';
        getModal('conventionModal').show();
    }

    function saveForm(evt) {
        evt.preventDefault();
        var id = parseInt(document.getElementById('conventionId').value, 10) || 0;
        var payload = {
            title: document.getElementById('f_title').value.trim(),
            content: document.getElementById('f_content').value.trim(),
            attachment_path: document.getElementById('f_attachment_path').value.trim()
        };
        var url = id > 0 ? (API.edit + id) : API.save;
        post(url, payload).then(function (body) {
            report(body, id > 0 ? '已保存' : '已发布，本版本为现行');
            if (body && body.success) {
                getModal('conventionModal').hide();
                loadConventions();
            }
        });
    }

    function deleteHistory(id) {
        post(API.remove + id + '/delete', {}).then(function (body) {
            report(body, '已删除');
            if (body && body.success) loadConventions();
        });
    }

    function boot() {
        loadStats();
        loadConventions();

        /* 上传控件：label 包裹隐藏 input，选完文件即走 092 共用上传函数。
           不在按钮上挂 onclick —— 点击先于选择完成会读到空 files，误报「请先选择文件」，
           且会让「上传附件」按钮看似可点实则无响应（16 节同类交互坑）。 */
        var attFile = document.getElementById('attFile');
        if (attFile) {
            attFile.addEventListener('change', function () {
                if (this.files && this.files.length && typeof window.onAttachmentUpload === 'function') {
                    window.onAttachmentUpload();
                }
            });
        }

        document.getElementById('addConventionBtn').addEventListener('click', function () { openForm(null); });
        document.getElementById('editConventionBtn').addEventListener('click', function () {
            var id = parseInt(this.dataset.id, 10) || 0;
            if (!id) return;
            request(API.list).then(function (body) {
                if (body && body.success && body.data.current) openForm(body.data.current);
            });
        });
        document.getElementById('conventionForm').addEventListener('submit', saveForm);
        document.getElementById('conventionHistoryBody').addEventListener('click', function (evt) {
            var btn = evt.target.closest('[data-del-id]');
            if (btn && window.confirm('确认删除该历史版本？此操作不可恢复。')) {
                deleteHistory(parseInt(btn.dataset.delId, 10));
            }
        });
    }

    /* 直接访问时 DOM 仍在解析 → 等 DOMContentLoaded；SPA 伪导航（spa.js 用 new Function
       执行页面脚本）时 readyState 已 complete → 立即绑定，否则指标/公约不加载、按钮全失效。
       与 demand.js / employment.js / party_activity.js 的启动范式一致。 */
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
