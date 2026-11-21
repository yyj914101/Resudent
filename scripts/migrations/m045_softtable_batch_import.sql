-- m045_softtable_batch_import.sql — 107 软表批量导入辅助表（轨 A 幂等迁移，ADR-018/ADR-031）
--
-- 两张表（只建辅助表，不改 dynamic_table / dynamic_field / dynamic_record 三表，零 ALTER）：
--   softtable_batch_import  批次总控（source_hash 幂等、状态 pending→running→done/failed/aborted）
--   softtable_batch_error   失败行暂存（只存 数据行号+出错列名+原因，零原值落库）
--
-- 与 models/dynamic_table.py 的 SofttableBatchImport / SofttableBatchError 一一对应：
--   batch_id 的内联 UNIQUE 与模型 unique=True 在 create_all 库生成同列 sqlite_autoindex，
--   两条建库路径 schema 一致（R-093-01 双索引漂移纪律：不再额外建同列唯一索引）。
--   索引统一 idx_* 前缀，模型侧不建 index=True。
--   idx_softtable_batch_import_scope 覆盖幂等判重键 (module_key, table_code, source_hash)。
--
-- 幂等：全部 CREATE ... IF NOT EXISTS，可重复执行；无 DML、无 ALTER。
-- 执行（先备份库）：
--   sqlite3 shucun.db < scripts/migrations/m045_softtable_batch_import.sql
-- 新库由 create_all 直建，本脚本仅用于存量库补齐。

CREATE TABLE IF NOT EXISTS softtable_batch_import (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id VARCHAR(36) NOT NULL UNIQUE,
  module_key VARCHAR(60) NOT NULL,
  table_code VARCHAR(60) NOT NULL,
  source_file VARCHAR(200),
  source_path VARCHAR(300),
  source_hash VARCHAR(64),
  operator VARCHAR(50),
  status VARCHAR(10) NOT NULL DEFAULT 'pending',
  total_rows INTEGER,
  success_rows INTEGER,
  fail_rows INTEGER,
  task_id VARCHAR(12),
  created_at DATETIME,
  finished_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_softtable_batch_import_scope
  ON softtable_batch_import (module_key, table_code, source_hash);
CREATE INDEX IF NOT EXISTS idx_softtable_batch_import_status
  ON softtable_batch_import (status);

CREATE TABLE IF NOT EXISTS softtable_batch_error (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id VARCHAR(36) NOT NULL,
  row_no INTEGER NOT NULL,
  field_name VARCHAR(100),
  reason VARCHAR(300),
  created_at DATETIME,
  FOREIGN KEY (batch_id) REFERENCES softtable_batch_import (batch_id)
);

CREATE INDEX IF NOT EXISTS idx_softtable_batch_error_batch
  ON softtable_batch_error (batch_id);
