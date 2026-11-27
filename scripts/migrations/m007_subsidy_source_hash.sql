-- =====================================================================
-- m007_subsidy_source_hash.sql — 补贴发放记录冲正幂等键（T14-3，R5）
-- 来源：Sprint3 T14-3 冲正（PRD 10.5 业务字段只增不改 + R5 幂等三件套）。
-- 目的：
--   1. subsidy_record.source_hash：冲正/批量登记的幂等防重键
--      - 冲正：sha256("{原记录id}:reverse")
--      - 批量登记：复用 import_engine 的 source_hash 语义
--   2. 唯一约束防重复提交（R5）；SQLite 下 NULL 不参与唯一，存量记录不受影响
-- 可重复执行：ALTER TABLE ADD COLUMN 由 run_migration.py 列存在性检查兜底
-- （参照 m002/m006 先例）。
-- =====================================================================

ALTER TABLE subsidy_record ADD COLUMN source_hash VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS idx_subsidy_record_source_hash ON subsidy_record(source_hash);
