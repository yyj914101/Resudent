-- =====================================================================
-- m010_resident_sync_log.sql — 主档变更副本同步日志表 DDL（022）
-- 本 DDL 为 resident_sync_log 结构变更唯一真源（宪法 §1.6）；
-- models/resident_sync_log.py 须与本脚本保持一致（db.create_all 为运行时兜底）。
-- 审阅修复 P3-5（2026-10-12）：修正真源表述，模型与 DDL 口径对齐。
-- =====================================================================

CREATE TABLE IF NOT EXISTS resident_sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id     INTEGER,
    field           VARCHAR(30),
    old_value       VARCHAR(100),   -- 旧值（敏感字段脱敏）
    new_value       VARCHAR(100),   -- 新值（敏感字段脱敏）
    affected_tables TEXT,           -- JSON：{表: 该字段实际影响行数}
    status          VARCHAR(10)  NOT NULL DEFAULT 'success',  -- success/failed
    error_message   VARCHAR(200),   -- 失败原因（截断 200，审阅修复 2026-10-12）
    operator        VARCHAR(50),
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_resident_sync_log_rid   ON resident_sync_log (resident_id);
CREATE INDEX IF NOT EXISTS idx_resident_sync_log_field ON resident_sync_log (field);

-- 审阅修复 P3-4：清理 index=True 遗留的同列重复自动索引（存量库幂等）
DROP INDEX IF EXISTS ix_resident_sync_log_resident_id;
