-- =====================================================================
-- m011_ddl_audit.sql — DDL 审计表（025 数据库管理面板·基础版）
-- 本 DDL 为 ddl_audit_log 结构唯一真源（宪法 §1.6 / 任务书 025 交付物 4）；
-- 运行时幂等兜底由 utils/ddl_guard.ensure_audit_table() 读取本文件执行，不另写 DDL。
-- 用途：面板轨 B（受控在线 DDL）全量留痕——操作人/动作/目标/SQL/前后结构摘要/前置备份。
-- =====================================================================

CREATE TABLE IF NOT EXISTS ddl_audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    operator      VARCHAR(50),                 -- 操作人用户名
    action        VARCHAR(30)  NOT NULL,       -- create_table/add_column/create_index/drop_index/rename_table/copy_table/analyze/vacuum
    target        VARCHAR(100) NOT NULL,       -- 目标对象（表名/索引名/「src → dst」）
    sql_text      TEXT,                        -- 实际执行的 DDL SQL（多条语句以换行连接）
    before_schema TEXT,                        -- 变更前目标对象结构摘要（JSON）
    after_schema  TEXT,                        -- 变更后目标对象结构摘要（JSON）
    backup_file   VARCHAR(200),                -- 前置 .wbak 备份包文件名
    result        VARCHAR(10)  NOT NULL DEFAULT 'success',  -- success/failed
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ddl_audit_log_target  ON ddl_audit_log (target);
CREATE INDEX IF NOT EXISTS idx_ddl_audit_log_created ON ddl_audit_log (created_at);