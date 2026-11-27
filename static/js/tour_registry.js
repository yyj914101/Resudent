/* tour_registry.js — Resudent 页面操作指引注册表与 SPA 生命周期控制器（基于 driver.js v1.x）
 *
 * 覆盖 7 个高频复杂业务场景：
 *   1. shell_overview    主壳层与快捷导航（/dashboard/）
 *   2. dynatable_guide   台账建表与动态胶囊（/dynatable/、/dynatable/upload）
 *   3. safety_archive    安全工作混合目录树（/safety/）
 *   4. batch_import_ops  大表批量导入与批次运维（/medical-insurance/、/import/batches）
 *   5. certificate_issue 证明开具全流程（/certificate/）
 *   6. dict_mgmt_guide   字典管理分类与条目维护（/dict/）
 *   7. db_admin_guide    数据库管理面板分级运维（/db-admin/）
 *
 * 依赖离线资产：static/lib/driver.js.iife.js 与 static/lib/driver.css（MIT）。
 * 未放置库文件时优雅降级提示，不抛 JS 异常。
 */
(function () {
    'use strict';

    var activeDriver = null;
    var autoTimer = null;

    var TOURS = [
        {
            id: 'shell_overview',
            title: '系统主界面指引',
            match: function (p) { return p === '/' || p === '/dashboard' || p === '/dashboard/'; },
            steps: [
                {
                    element: '#sidebar',
                    popover: {
                        title: '六大业务分组导航',
                        description: '左侧按综合工作、党建工作、民生保障、社区治理、便民服务与数据管理分区；不同账号仅展示已授权的菜单。',
                        side: 'right',
                        align: 'start'
                    }
                },
                {
                    element: '#globalSearch',
                    popover: {
                        title: '全站快捷搜索（Ctrl+K）',
                        description: '任意页面按 Ctrl+K 即可快速按姓名、户号或身份证号检索居民档案，无需层层切菜单。',
                        side: 'bottom',
                        align: 'center'
                    }
                },
                {
                    element: '#btnPageTour',
                    popover: {
                        title: '随时重播本页指引',
                        description: '进入台账建表、安全工作、批量导入或证明开具等复杂页面时，可随时点击此处查看当前页面的分步指引。',
                        side: 'bottom',
                        align: 'end'
                    }
                }
            ]
        },
        {
            id: 'dynatable_guide',
            title: '台账建表指引',
            match: function (p) { return p === '/dynatable' || p === '/dynatable/' || p === '/dynatable/upload'; },
            steps: [
                {
                    element: '#uploadEntry',
                    popover: {
                        title: '上传 Excel 自动建表',
                        description: '上级下发的新口径名册表无需找开发改表：点击这里上传 .xlsx / .csv 即可自动推断列类型并建表。',
                        side: 'left'
                    }
                },
                {
                    element: '#dropZone',
                    popover: {
                        title: '拖拽或点击选择数据文件',
                        description: '支持 ≤20MB 的 Excel/CSV，首行为列名；系统先解析预览，确认字段无误后才会落库。',
                        side: 'bottom'
                    }
                },
                {
                    element: '#f_module',
                    popover: {
                        title: '选择「菜单归属」自动挂胶囊',
                        description: '选定归属业务模块后，建成的台账会自动作为页内胶囊出现在对应业务页顶部；归属「安全工作」时还可直接指定挂入的安全文件夹。',
                        side: 'top'
                    }
                },
                {
                    element: '#tableList',
                    popover: {
                        title: '已建台账与二次追加导入',
                        description: '进入任意台账页后，既可随时修改字段结构，也可通过页头「批量导入」按列匹配追加后续月份的新数据。',
                        side: 'top'
                    }
                }
            ]
        },
        {
            id: 'safety_archive',
            title: '安全工作指引',
            match: function (p) { return p === '/safety' || p === '/safety/'; },
            steps: [
                {
                    element: '#btnSiderToggle',
                    popover: {
                        title: '展开安全档案目录树',
                        description: '内置 20 类安全工作一级目录（消防、燃气、食品、防汛、应急工作方案等），点击此处展开或收纳左侧目录树。',
                        side: 'bottom'
                    }
                },
                {
                    element: '#btnAddTable',
                    popover: {
                        title: '文件夹内直接添加台账',
                        description: '每个安全文件夹既可存文书/照片，也可直接上传 Excel 新建台账或关联已有安全软表，实现「文件 + 台账」同目录管理。',
                        side: 'bottom'
                    }
                },
                {
                    element: '#btnUpload',
                    popover: {
                        title: '上传文书与现场照片',
                        description: '支持 Word、PDF、表格与图片批量上传（也可直接将文件拖进下方列表区），单文件上限 50MB。',
                        side: 'bottom'
                    }
                },
                {
                    element: '#btnZipDir',
                    popover: {
                        title: '一键整目录打包下载',
                        description: '迎检或向上级报送材料时点击打包下载，系统会将目录内的文书档案与挂载软表（自动导出为 .xlsx）一并打进 ZIP。',
                        side: 'bottom'
                    }
                }
            ]
        },
        {
            id: 'batch_import_ops',
            title: '批量导入与批次运维指引',
            match: function (p) {
                return p === '/import/batches' || p === '/import/batches/' ||
                    p === '/medical-insurance' || p === '/medical-insurance/';
            },
            steps: [
                {
                    element: 'select[name="source_module"]',
                    popover: {
                        title: '全站导入批次统一筛选',
                        description: '居民、软表、选民、补贴、医保参保与民政对象的后台异步导入批次全部汇总在此，可按业务模块一键过滤。',
                        side: 'bottom'
                    }
                },
                {
                    element: 'select[name="status"]',
                    popover: {
                        title: '按执行状态排查',
                        description: '大表导入在后台分批提交不阻塞页面；若某批次存在格式错误或并发失败，可按「失败 / 已中止」快速定位。',
                        side: 'bottom'
                    }
                },
                {
                    element: '#dataTable',
                    popover: {
                        title: '查看报告与下载失败行',
                        description: '点击行末「报告」查看新增/更新/失败汇总；有错误行时点「失败行」下载脱敏错误清单（仅含行号、列名与原因）。',
                        side: 'top'
                    }
                }
            ]
        },
        {
            id: 'certificate_issue',
            title: '证明开具指引',
            match: function (p) { return p === '/certificate' || p === '/certificate/'; },
            steps: [
                {
                    element: '#issueTemplateSelect',
                    popover: {
                        title: '第一步：选择证明模板',
                        description: '先选择要开具的证明类型（居住、亲属关系、死亡证明等）；如需自定义公文版式，可切换至「模板管理」页签维护。',
                        side: 'bottom'
                    }
                },
                {
                    element: '#issueDynamicFields',
                    popover: {
                        title: '第二步：关联居民并补全字段',
                        description: '按模板变量填写居民信息，支持从居民库检索自动回填，减少手工录错身份证号或住址。',
                        side: 'right'
                    }
                },
                {
                    element: '#issuePurpose',
                    popover: {
                        title: '第三步：核对开具人与申请用途',
                        description: '确认开具人、开具单位、打印份数及申请用途，这些信息会自动记入开具留痕台账。',
                        side: 'top'
                    }
                },
                {
                    element: '#issuePaper',
                    popover: {
                        title: '第四步：A4 实时预览排版',
                        description: '右侧按真实 A4 纸张比例实时渲染正文与页边距，确认无错字漏项后再正式开具。',
                        side: 'left'
                    }
                },
                {
                    element: '#btnIssueCertificate',
                    popover: {
                        title: '第五步：确认开具并入台账',
                        description: '点击后系统自动生成唯一证明编号并归档到开具记录台账，可直接打印或补打。',
                        side: 'bottom'
                    }
                }
            ]
        },
        {
            id: 'dict_mgmt_guide',
            title: '字典管理指引',
            match: function (p) { return p === '/dict' || p === '/dict/'; },
            steps: [
                {
                    element: '#categoryList',
                    popover: {
                        title: '第一步：选择或新增字典分类',
                        description: '左侧列出全站共用的下拉选项分类（如村组、民族、政治面貌、安全隐患等级等）；超管可点上方「新增分类」供软表枚举列引用。',
                        side: 'right',
                        align: 'start'
                    }
                },
                {
                    element: '#currentCategoryName',
                    popover: {
                        title: '第二步：确认当前维护的分类',
                        description: '点击左侧任意分类后，此处显示当前选中的字典分类名称，右侧表格同步加载该分类下的全部条目。',
                        side: 'bottom'
                    }
                },
                {
                    element: '.sc-grid__main .sc-table-wrap',
                    popover: {
                        title: '第三步：维护字典项与启停状态',
                        description: '支持编辑标签、取值、排序号及停用/启用；修改后全站表单下拉框与软表枚举列会即时生效（无需改代码）。',
                        side: 'top'
                    }
                },
                {
                    element: '.sc-page-head__actions .sc-btn--primary',
                    popover: {
                        title: '第四步：新增字典条目',
                        description: '选中分类后点此按钮即可追加新选项，填完标签与排序保存即可生效。',
                        side: 'left'
                    }
                }
            ]
        },
        {
            id: 'db_admin_guide',
            title: '数据库管理面板指引',
            match: function (p) { return p === '/db-admin' || p === '/db-admin/'; },
            steps: [
                {
                    element: '.db-stat-grid',
                    popover: {
                        title: '第一步：实时库况与最近加密备份',
                        description: '顶部四联卡展示数据库体积、表与总记录数、存储页碎片及最近一次 .wbak 加密备份时间，操作前先确认备份正常。',
                        side: 'bottom'
                    }
                },
                {
                    element: '#btnShowNotice',
                    popover: {
                        title: '第二步：分级安全护栏说明',
                        description: '核心业务表一律只读防误改；所有黄级与红级结构变更执行前都会自动生成 .wbak 加密备份，点此可随时查看安全须知。',
                        side: 'left'
                    }
                },
                {
                    element: '.db-tabs',
                    popover: {
                        title: '第三步：三大功能分区切换',
                        description: '「数据表目录」只读查阅表结构；「建表与结构维护」执行黄/红级运维（加列、建索引、压缩整理）；「系统体检与审计」执行完整性体检、结构对账与审计日志查阅。',
                        side: 'bottom'
                    }
                },
                {
                    element: '#tableSearch',
                    popover: {
                        title: '第四步：按中文名或模块快速查表',
                        description: '输入中文用途或英文表名，或配合右侧模块筛选胶囊快速定位数据表；点击任意表行即可展开字段说明与数据概况。',
                        side: 'bottom'
                    }
                }
            ]
        }
    ];

    function currentUserKey() {
        var name = $.trim($('.sc-usermenu__name').first().text() || '');
        return name || 'anon';
    }

    function doneStorageKey(tourId) {
        return 'resudent_tour_done:' + currentUserKey() + ':' + tourId;
    }

    function isTourDone(tourId) {
        try { return localStorage.getItem(doneStorageKey(tourId)) === '1'; } catch (e) { return false; }
    }

    function markTourDone(tourId) {
        try { localStorage.setItem(doneStorageKey(tourId), '1'); } catch (e) {}
    }

    function findTourForPath(pathname) {
        var p = pathname || window.location.pathname || '/';
        for (var i = 0; i < TOURS.length; i++) {
            if (TOURS[i].match(p)) return TOURS[i];
        }
        return null;
    }

    function isVisibleElement(selector) {
        if (!selector) return true;
        var el = document.querySelector(selector);
        if (!el) return false;
        var style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function getDriverFactory() {
        if (window.driver && window.driver.js && typeof window.driver.js.driver === 'function') {
            return window.driver.js.driver;
        }
        return null;
    }

    function destroyActive() {
        if (autoTimer) {
            clearTimeout(autoTimer);
            autoTimer = null;
        }
        if (activeDriver) {
            try { activeDriver.destroy(); } catch (e) {}
            activeDriver = null;
        }
    }

    function startTour(tour, isManual) {
        if (!tour) return;
        destroyActive();
        var factory = getDriverFactory();
        if (!factory) {
            if (isManual && typeof window.showToast === 'function') {
                window.showToast('未检测到 driver.js 离线库，请将 driver.js.iife.js 与 driver.css 放入 static/lib/', 'warning');
            }
            return;
        }
        var validSteps = tour.steps.filter(function (s) {
            return isVisibleElement(s.element);
        });
        if (!validSteps.length) {
            if (isManual && typeof window.showToast === 'function') {
                window.showToast('当前页面暂无可引导的操作节点', 'info');
            }
            return;
        }
        activeDriver = factory({
            showProgress: true,
            allowClose: true,
            smoothScroll: true,
            popoverClass: 'resudent-tour-popover',
            nextBtnText: '下一步',
            prevBtnText: '上一步',
            doneBtnText: '完成',
            progressText: '第 {{current}} / {{total}} 步',
            steps: validSteps,
            onDestroyed: function () {
                markTourDone(tour.id);
                activeDriver = null;
            }
        });
        activeDriver.drive();
    }

    function syncTopbarButton(tour) {
        var btn = document.getElementById('btnPageTour');
        if (!btn) return;
        if (!tour) {
            btn.hidden = true;
            btn.removeAttribute('data-tour-id');
            return;
        }
        btn.hidden = false;
        btn.setAttribute('data-tour-id', tour.id);
        btn.setAttribute('title', '查看' + tour.title);
    }

    function onRouteChange() {
        destroyActive();
        var tour = findTourForPath(window.location.pathname);
        syncTopbarButton(tour);
        if (tour && !isTourDone(tour.id) && getDriverFactory()) {
            autoTimer = setTimeout(function () {
                startTour(tour, false);
            }, 600);
        }
    }

    $(function () {
        $(document).on('click.resudentTour', '#btnPageTour', function () {
            var tour = findTourForPath(window.location.pathname);
            if (tour) startTour(tour, true);
        });
        onRouteChange();
    });

    window.ResudentTour = {
        destroy: destroyActive,
        onRouteChange: onRouteChange,
        startCurrent: function () { startTour(findTourForPath(window.location.pathname), true); },
        listIds: function () { return TOURS.map(function (t) { return t.id; }); }
    };
})();
