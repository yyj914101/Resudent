-- =====================================================================
-- m047_domain_batch_import.sql — 通用域异步批量导入批次表（医保/民政等共用）
-- 幂等建表 + 索引（与 m042/m045 同构；失败行表零敏感原值落库）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS domain_batch_import (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id      VARCHAR(36)  NOT NULL UNIQUE,
    domain        VARCHAR(40)  NOT NULL,
    scope_key     VARCHAR(100) NOT NULL DEFAULT '',
    source_file   VARCHAR(200),
    source_path   VARCHAR(300),
    source_hash   VARCHAR(64),
    mode          VARCHAR(20)  NOT NULL DEFAULT 'append',
    operator      VARCHAR(50),
    status        VARCHAR(12)  NOT NULL DEFAULT 'pending',
    total_rows    INTEGER      NOT NULL DEFAULT 0,
    inserted_rows INTEGER      NOT NULL DEFAULT 0,
    updated_rows  INTEGER      NOT NULL DEFAULT 0,
    skip_rows     INTEGER      NOT NULL DEFAULT 0,
    fail_rows     INTEGER      NOT NULL DEFAULT 0,
    task_id       VARCHAR(16),
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    finished_at   DATETIME
);

CREATE INDEX IF NOT EXISTS idx_domain_batch_domain_hash
    ON domain_batch_import (domain, scope_key, source_hash);
CREATE INDEX IF NOT EXISTS idx_domain_batch_created
    ON domain_batch_import (created_at);

CREATE TABLE IF NOT EXISTS domain_batch_error (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id   VARCHAR(36)  NOT NULL REFERENCES domain_batch_import(batch_id),
    row_no     INTEGER      NOT NULL,
    field_name VARCHAR(100),
    reason     VARCHAR(300),
    created_at DATETIME     DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_domain_batch_error_batch
    ON domain_batch_error (batch_id);
