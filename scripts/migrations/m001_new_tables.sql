-- =====================================================================
-- m001_new_tables.sql — 新表 DDL（T08 数据底座）
-- 来源：详细设计 v1.1 §3.1。可重复执行（IF NOT EXISTS）。
-- 注：async_task 已由 T05 模型（models/task.py）通过 db.create_all 建表，
--     此处 IF NOT EXISTS 仅兜底，二者以模型为准。
-- =====================================================================

-- ============ R4 补贴管理 ============
CREATE TABLE IF NOT EXISTS subsidy_project (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          VARCHAR(100) NOT NULL,
    project_type  VARCHAR(20)  NOT NULL,      -- 高龄津贴/困难老人补助/低保金/残疾补贴/其他
    cycle         VARCHAR(10)  NOT NULL,      -- 月/季/年/一次性
    default_amount NUMERIC(10,2),
    age_min       INTEGER,
    age_max       INTEGER,
    target_tags   TEXT,                        -- JSON 数组（适用标签码）
    status        VARCHAR(10)  NOT NULL DEFAULT '启用',   -- 启用/停用
    remark        VARCHAR(200),
    create_by     INTEGER,
    create_time   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subsidy_record (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id INTEGER NOT NULL REFERENCES residents(id),
    project_id  INTEGER NOT NULL REFERENCES subsidy_project(id),
    issue_date  DATE    NOT NULL,
    period      VARCHAR(7) NOT NULL,          -- YYYY-MM
    amount      NUMERIC(10,2) NOT NULL,
    channel     VARCHAR(10) NOT NULL DEFAULT '镇级直发',
    source      VARCHAR(10) NOT NULL DEFAULT 'normal',  -- normal/legacy
    status      VARCHAR(10) NOT NULL DEFAULT '正常',    -- 正常/停发（D2，状态迁移允许）
    note        VARCHAR(200),
    legacy_id   INTEGER UNIQUE,               -- 存量迁移幂等键 = subsidies.id（M2 拍板）
    create_by   INTEGER,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_subsidy_record_resident ON subsidy_record(resident_id);
CREATE INDEX IF NOT EXISTS idx_subsidy_record_project_period ON subsidy_record(project_id, period);
CREATE INDEX IF NOT EXISTS idx_subsidy_record_status ON subsidy_record(status);

-- ============ R1 标签体系 ============
CREATE TABLE IF NOT EXISTS resident_tag (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id    INTEGER NOT NULL REFERENCES residents(id),
    tag_code       VARCHAR(50) NOT NULL,      -- dict_item（仅政策性标签）
    source         VARCHAR(10) NOT NULL DEFAULT '手动',   -- 自动/手动/导入
    status         VARCHAR(10) NOT NULL DEFAULT '有效',   -- 有效/失效（软失效）
    invalid_reason VARCHAR(100),
    invalid_time   DATETIME,
    operator       INTEGER,
    create_time    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (resident_id, tag_code, status)    -- 同人同标签仅一条有效（软失效后可重打）
);
CREATE INDEX IF NOT EXISTS idx_resident_tag_code_status ON resident_tag(tag_code, status);

-- ============ R7 走访（二期建表，一期随 m001 同批落结构避免二次迁移） ============
CREATE TABLE IF NOT EXISTS visit_plan (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_name    VARCHAR(100) NOT NULL,
    plan_type    VARCHAR(20)  NOT NULL,       -- 按区域/按对象类型/按标签
    scope_params TEXT,                         -- JSON
    target_count INTEGER DEFAULT 0,
    status       VARCHAR(10) NOT NULL DEFAULT '草稿',  -- 草稿/进行中/已完成
    created_by   INTEGER,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at  DATETIME
);

CREATE TABLE IF NOT EXISTS visit_record (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     INTEGER REFERENCES visit_plan(id),
    resident_id INTEGER NOT NULL REFERENCES residents(id),
    visit_date  DATE NOT NULL,
    visitor_id  INTEGER NOT NULL,
    visit_type  VARCHAR(10) NOT NULL,          -- 入户/电话/其他
    content     TEXT,
    has_need    INTEGER NOT NULL DEFAULT 0,    -- 0=无（默认减负）
    need_desc   TEXT,
    photos      TEXT,                           -- JSON 数组：uploads/visit/ 相对路径
    create_by   INTEGER,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_visit_record_resident ON visit_record(resident_id);
CREATE INDEX IF NOT EXISTS idx_visit_record_plan ON visit_record(plan_id);

-- ============ R8 预警（二期） ============
CREATE TABLE IF NOT EXISTS warn_record (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id    INTEGER NOT NULL REFERENCES residents(id),
    project_id     INTEGER REFERENCES subsidy_project(id),
    warn_type      VARCHAR(20) NOT NULL,       -- 周期到期/年龄边界/停发确认
    trigger_reason VARCHAR(200),
    status         VARCHAR(10) NOT NULL DEFAULT '未处置',  -- 未处置/已处置/已关闭
    handled_by     INTEGER,
    handled_at     DATETIME,
    handle_result  VARCHAR(200),
    create_time    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_warn_record_status ON warn_record(status);

-- ============ R5 对账与级联 ============
CREATE TABLE IF NOT EXISTS reconcile_task (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source           VARCHAR(20) NOT NULL,     -- 公安/殡葬/季度计划（B4 双触发）
    import_file      VARCHAR(200),
    task_date        DATE NOT NULL,
    quarter          VARCHAR(7),               -- 2026-Q3（季度计划归口）
    status           VARCHAR(10) NOT NULL DEFAULT 'pending',
    result_summary   TEXT,                     -- JSON：三类结果计数
    operator         INTEGER,
    source_deleted_at DATETIME,                -- 源名单删除时间（≤30 天）
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reconcile_task_quarter ON reconcile_task(quarter, status);

CREATE TABLE IF NOT EXISTS reconcile_result (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES reconcile_task(id),
    id_card_enc  TEXT,                          -- 敏感字段密文（新表密文原生，无明文列）
    id_card_hash VARCHAR(64),                   -- SHA-256 等值检索
    match_type   VARCHAR(20) NOT NULL,          -- 已登记/疑似未登记/名单外
    handle_status VARCHAR(10) NOT NULL DEFAULT '未处置',
    handled_by   INTEGER,
    handled_at   DATETIME,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reconcile_result_task ON reconcile_result(task_id, match_type);
CREATE INDEX IF NOT EXISTS idx_reconcile_result_id_card_hash ON reconcile_result(id_card_hash);

CREATE TABLE IF NOT EXISTS cascade_checklist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    death_report_id INTEGER NOT NULL REFERENCES death_reports(id),
    affected_items  TEXT NOT NULL,              -- JSON：补贴项目/最近发放/标签/台账
    generated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    printed         INTEGER DEFAULT 0,
    status          VARCHAR(10) NOT NULL DEFAULT '有效'   -- 有效/作废（B2 撤回置作废）
);

CREATE TABLE IF NOT EXISTS cascade_notice (      -- 停发告知单（B1）
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    death_report_id INTEGER NOT NULL REFERENCES death_reports(id),
    notice_no       VARCHAR(30) NOT NULL UNIQUE,  -- TZ-YYYYMMDD-####
    resident_name   VARCHAR(50) NOT NULL,
    id_card_enc     TEXT,                          -- 密文原生（打印时按权限解密/脱敏）
    id_card_hash    VARCHAR(64),
    project_ids     TEXT NOT NULL,                 -- JSON 数组
    stop_from       VARCHAR(7) NOT NULL,           -- 停发起始期 YYYY-MM
    reason          VARCHAR(50) NOT NULL,          -- 死亡/注销
    generated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    operator        INTEGER,
    printed_count   INTEGER DEFAULT 0              -- 重新打印留痕（log_operation 同步记）
);

-- ============ A1 后台任务 ============
CREATE TABLE IF NOT EXISTS async_task (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        VARCHAR(36) NOT NULL UNIQUE,   -- UUID
    task_type      VARCHAR(20) NOT NULL,          -- import/export/lazy_check
    ref_id         VARCHAR(50),                   -- 关联业务键（如 import_batch.batch_id）
    status         VARCHAR(10) NOT NULL DEFAULT 'pending', -- pending/running/done/failed/aborted
    progress       INTEGER NOT NULL DEFAULT 0,
    result_summary TEXT,                           -- JSON
    operator       INTEGER,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    finished_at    DATETIME
);
CREATE INDEX IF NOT EXISTS idx_async_task_status ON async_task(task_type, status);

-- ============ R1-E2 导入兼容六表 ============
CREATE TABLE IF NOT EXISTS import_batch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        VARCHAR(36) NOT NULL UNIQUE,
    source_file     VARCHAR(200),
    source_path     VARCHAR(300),                 -- temp/imports/ 内路径（不入 uploads）
    source_hash     VARCHAR(64),                  -- 防重复导入
    operator        INTEGER,
    status          VARCHAR(10) NOT NULL DEFAULT 'pending', -- pending/mapped/running/done/blocked/aborted
    total_rows      INTEGER DEFAULT 0,
    success_rows    INTEGER DEFAULT 0,
    fail_rows       INTEGER DEFAULT 0,
    skip_rows       INTEGER DEFAULT 0,
    new_field_count INTEGER DEFAULT 0,
    thresholds      TEXT,                          -- JSON 守卫阈值快照
    block_reason    VARCHAR(200),
    started_at      DATETIME,
    finished_at     DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS import_field_mapping (   -- 字段血缘
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        VARCHAR(36) NOT NULL REFERENCES import_batch(batch_id),
    source_field    VARCHAR(100) NOT NULL,          -- 规范化后源表头
    target_field    VARCHAR(100),                   -- NULL=未映射
    target_table    VARCHAR(50),
    storage         VARCHAR(10),                    -- column/json_ext
    mapping_kind    VARCHAR(10),                    -- rename/convert/enum/ignore/unknown
    conversion      TEXT,                           -- JSON
    value_domain_map TEXT,                          -- JSON
    required        INTEGER DEFAULT 0,
    is_template     INTEGER DEFAULT 0,              -- 全局可复用模板规则
    matched_by      VARCHAR(10),                    -- exact/fuzzy/template/manual/unknown
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ifm_batch ON import_field_mapping(batch_id);

CREATE TABLE IF NOT EXISTS import_row_error (       -- 异常暂存区
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    VARCHAR(36) NOT NULL REFERENCES import_batch(batch_id),
    row_no      INTEGER NOT NULL,
    field       VARCHAR(100),                       -- 行级错误为 NULL
    error_level VARCHAR(5) NOT NULL,                -- warn/error
    error_type  VARCHAR(10) NOT NULL,               -- required/type/enum/unmapped/row
    reason      VARCHAR(300),
    raw_row     TEXT,                               -- JSON 原文（展示时脱敏）
    resolved    INTEGER DEFAULT 0,
    resolution  VARCHAR(200),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ire_batch ON import_row_error(batch_id, error_level);

CREATE TABLE IF NOT EXISTS import_field_registry (  -- 字段注册中心（方案B）
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         VARCHAR(50) NOT NULL UNIQUE,
    display_name VARCHAR(100),
    data_type    VARCHAR(20),
    source_batch VARCHAR(36),
    definition   TEXT,                              -- JSON：口径说明/统计用途声明
    status       VARCHAR(10) NOT NULL DEFAULT 'draft', -- draft/approved/rejected/active
    approved_by  INTEGER,
    approved_at  DATETIME,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_change_request (  -- 防漂移审批流
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_no   VARCHAR(30) NOT NULL UNIQUE,
    change_type  VARCHAR(20) NOT NULL,              -- add_field/change_mapping/version_bump
    payload      TEXT,
    status       VARCHAR(10) NOT NULL DEFAULT 'pending',
    requested_by INTEGER,
    decided_by   INTEGER,
    decided_at   DATETIME,
    reason       VARCHAR(300),
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_model_version (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name VARCHAR(50) NOT NULL,
    version    INTEGER NOT NULL,
    change_log TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (model_name, version)
);

-- ============ 存量迁移留痕三表（M2/dry-run 拍板） ============
CREATE TABLE IF NOT EXISTS migration_record (       -- 迁移总控（切换点原子化载体）
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_no VARCHAR(30) NOT NULL UNIQUE,
    version      VARCHAR(20) NOT NULL,              -- 迁移版本号
    status       VARCHAR(10) NOT NULL DEFAULT 'running', -- running/done/failed
    row_stats    TEXT,                              -- JSON：各阶段行数
    reconcile_result TEXT,                          -- JSON：对账报告（G-4 引用）
    started_at   DATETIME,
    finished_at  DATETIME
);

CREATE TABLE IF NOT EXISTS migration_log (          -- 行级留痕
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_no VARCHAR(30) NOT NULL,
    source_table VARCHAR(50) NOT NULL,
    source_id    INTEGER NOT NULL,
    target_id    INTEGER,
    migrated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (migration_no, source_table, source_id)
);

CREATE TABLE IF NOT EXISTS migration_orphan_log (   -- 孤儿记录（不静默丢弃）
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_no VARCHAR(30) NOT NULL,
    source_table VARCHAR(50) NOT NULL,
    source_id    INTEGER,
    orphan_type  VARCHAR(30) NOT NULL,              -- id_card_empty / no_resident_match
    raw_row      TEXT NOT NULL,                     -- JSON 整行快照（可对账可补录）
    amount       NUMERIC(10,2),                     -- G-4 孤儿总额口径字段
    year         INTEGER,
    handled      INTEGER NOT NULL DEFAULT 0,        -- 0=待人工核对
    handled_by   INTEGER,
    handled_at   DATETIME,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);
