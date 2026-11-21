-- m042_election_batch_import.sql — 105 选民批量导入辅助表（轨 A 幂等迁移，ADR-018/ADR-030）
--
-- 两张表（只建辅助表，不改 election_voter / election_round / election_result）：
--   election_batch_import  批次总控（source_hash 幂等、状态 pending→running→done/failed/aborted）
--   election_batch_error   失败行暂存（身份证仅存前 6 后 4 打码串，绝不落明文）
--
-- 与 models/election.py 的 ElectionBatchImport / ElectionBatchError 一一对应：
--   batch_id 的内联 UNIQUE 与模型 unique=True 在 create_all 库生成同列 sqlite_autoindex，
--   两条建库路径 schema 一致（R-093-01 双索引漂移纪律：不再额外建同列唯一索引）。
--   索引统一 idx_* 前缀，模型侧不建 index=True。
--
-- 幂等：全部 CREATE ... IF NOT EXISTS，可重复执行；无 DML、无 ALTER。
-- 执行（先备份库）：
--   sqlite3 shucun.db < scripts/migrations/m042_election_batch_import.sql
-- 新库由 create_all 直建，本脚本仅用于存量库补齐。

CREATE TABLE IF NOT EXISTS election_batch_import (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id VARCHAR(36) NOT NULL UNIQUE,
  round_id INTEGER NOT NULL,
  source_file VARCHAR(200),
  source_path VARCHAR(300),
  source_hash VARCHAR(64),
  operator INTEGER,
  status VARCHAR(10) NOT NULL DEFAULT 'pending',
  total_rows INTEGER,
  success_rows INTEGER,
  fail_rows INTEGER,
  created_at DATETIME,
  finished_at DATETIME,
  FOREIGN KEY (round_id) REFERENCES election_round (id)
);

CREATE INDEX IF NOT EXISTS idx_election_batch_import_round
  ON election_batch_import (round_id);
CREATE INDEX IF NOT EXISTS idx_election_batch_import_hash
  ON election_batch_import (source_hash);

CREATE TABLE IF NOT EXISTS election_batch_error (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id VARCHAR(36) NOT NULL,
  row_no INTEGER NOT NULL,
  id_card_masked VARCHAR(30),
  reason VARCHAR(300),
  created_at DATETIME,
  FOREIGN KEY (batch_id) REFERENCES election_batch_import (batch_id)
);

CREATE INDEX IF NOT EXISTS idx_election_batch_error_batch
  ON election_batch_error (batch_id);
