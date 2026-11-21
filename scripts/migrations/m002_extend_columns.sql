-- =====================================================================
-- m002_extend_columns.sql — 扩展列 DDL（T08 数据底座）
-- 来源：详细设计 v1.1 §3.2。可重复执行（IF NOT EXISTS）。
-- 注：SQLite 无 ADD COLUMN IF NOT EXISTS，需由 run_migration.py 做列存在性检查兜底。
-- =====================================================================

-- residents（R1：A4 流动性单一真值 + 方案A 扩展列）
ALTER TABLE residents ADD COLUMN flow_status VARCHAR(10) NOT NULL DEFAULT '常住'; -- 常住/流入/流出
ALTER TABLE residents ADD COLUMN ext_data TEXT;            -- JSON 扩展列（方案A，不建索引）
CREATE INDEX IF NOT EXISTS idx_residents_flow_status ON residents(flow_status);

-- care_target（R2 八字段组 + 软失效，表名以 V2.45 实际 care_targets 为准）
ALTER TABLE care_targets ADD COLUMN target_type VARCHAR(10);        -- 老人/儿童
ALTER TABLE care_targets ADD COLUMN elder_type VARCHAR(10);         -- 困难（高龄动态不落库）
ALTER TABLE care_targets ADD COLUMN child_type VARCHAR(10);         -- 留守/困境（不设"一般"）
ALTER TABLE care_targets ADD COLUMN source VARCHAR(20);             -- 镇级名单/手工建档
ALTER TABLE care_targets ADD COLUMN guardian_consent VARCHAR(10);   -- 已同意/待补录（C1）
ALTER TABLE care_targets ADD COLUMN guardian_consent_time DATETIME;
ALTER TABLE care_targets ADD COLUMN guardian_consent_way VARCHAR(20);
ALTER TABLE care_targets ADD COLUMN difficulty_reason VARCHAR(200);
ALTER TABLE care_targets ADD COLUMN need VARCHAR(200);
ALTER TABLE care_targets ADD COLUMN age_class_override TEXT;        -- JSON：override_class/reason/by/time（C3）
ALTER TABLE care_targets ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT '有效';   -- 软失效（R5）
ALTER TABLE care_targets ADD COLUMN invalid_reason VARCHAR(100);

-- death_report（R5 级联触发，表名 death_reports）
ALTER TABLE death_reports ADD COLUMN cascade_status VARCHAR(10) NOT NULL DEFAULT 'pending'; -- pending/done/failed
ALTER TABLE death_reports ADD COLUMN confirm_by INTEGER;
ALTER TABLE death_reports ADD COLUMN confirmed_at DATETIME;

-- 软失效字段（R5-E1③，四表统一）
ALTER TABLE dibao ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT '有效';
ALTER TABLE dibao ADD COLUMN invalid_reason VARCHAR(100);
ALTER TABLE disabled_persons ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT '有效';
ALTER TABLE disabled_persons ADD COLUMN invalid_reason VARCHAR(100);
ALTER TABLE special_support ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT '有效';
ALTER TABLE special_support ADD COLUMN invalid_reason VARCHAR(100);
-- care_targets 的 status/invalid_reason 已并入上方扩展

-- =====================================================================
-- 存量 moved_out 迁移（A4）：由 run_migration.py 在 m002 后同批执行
--   UPDATE residents SET flow_status='流出' WHERE status='moved_out';
--   UPDATE residents SET status='normal' WHERE status='moved_out';
-- 并记录行数入 migration_record。
-- =====================================================================
