-- =====================================================================
-- m024_dynamic_tables.sql — 软表引擎元数据三表（078 交付物 2，ADR-025）
-- 一次性初始化：dynamic_table / dynamic_field / dynamic_record
-- 此后业务台账的建表与结构变更全程零物理 DDL（只改元数据行）。
--
-- 轨 A（ADR-018）：备份后手工执行；幂等（IF NOT EXISTS + 显式约束名）。
-- 新库由 create_all 直接建表；本文件用于存量库。
--
-- 执行：
--   sqlite3 shucun.db < scripts/migrations/m024_dynamic_tables.sql
-- 校验（第二次执行后 rowid 与 structure 不变即幂等）：
--   PRAGMA integrity_check;  -- 期望 ok
-- =====================================================================

CREATE TABLE IF NOT EXISTS dynamic_table (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  code           VARCHAR(60)  NOT NULL UNIQUE,
  name           VARCHAR(120) NOT NULL,
  module_key     VARCHAR(60)  NOT NULL,
  group_name     VARCHAR(60),
  description    TEXT,
  permission_key VARCHAR(60)  NOT NULL DEFAULT 'dynatable',
  created_by     VARCHAR(50),
  created_at     DATETIME,
  updated_at     DATETIME
);

CREATE TABLE IF NOT EXISTS dynamic_field (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  table_id           INTEGER      NOT NULL,
  code               VARCHAR(60)  NOT NULL,
  display_name       VARCHAR(100) NOT NULL,
  data_type          VARCHAR(10)  NOT NULL DEFAULT 'text',
  dict_category_code VARCHAR(100),
  sensitivity        VARCHAR(10)  NOT NULL DEFAULT 'none',
  is_required        BOOLEAN      NOT NULL DEFAULT 0,
  sort_order         INTEGER      NOT NULL DEFAULT 0,
  created_at         DATETIME,
  CONSTRAINT uq_dynamic_field_table_code UNIQUE (table_id, code)
);

CREATE INDEX IF NOT EXISTS ix_dynamic_field_table ON dynamic_field (table_id);

CREATE TABLE IF NOT EXISTS dynamic_record (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  table_id   INTEGER NOT NULL,
  data       TEXT    NOT NULL,
  digest     TEXT,
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_dynamic_record_table ON dynamic_record (table_id);
CREATE INDEX IF NOT EXISTS ix_dynamic_record_table_created ON dynamic_record (table_id, created_at);
