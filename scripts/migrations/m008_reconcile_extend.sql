-- =====================================================================
-- m008_reconcile_extend.sql — 对账表扩展（T15-4，R5-E3/B4/O3）
-- 来源：Sprint6 T15-4 公安/殡葬死亡名单对账（PRD R5-E3 对账 + B4 季度计划
--       + O3/U5 源名单 ≤30 天限期删除）。
-- 目的：
--   1. reconcile_task.source_hash：导入幂等键（对齐 T14-5 source_hash 语义，
--      NULL 不参与唯一；季度计划任务无文件天然兼容）
--   2. reconcile_result.name / death_date：分类清单展示 + 登记预填
-- 可重复执行：run_migration.py 列存在性检查兜底 + 幂等索引（IF NOT EXISTS）。
-- 注：存量库也可由 utils/reconcile_service.ensure_reconcile_columns() 幂等补列。
-- =====================================================================

ALTER TABLE reconcile_task ADD COLUMN source_hash VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reconcile_task_source_hash ON reconcile_task(source_hash);

ALTER TABLE reconcile_result ADD COLUMN name VARCHAR(50);

ALTER TABLE reconcile_result ADD COLUMN death_date DATE;
