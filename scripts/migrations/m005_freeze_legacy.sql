-- =====================================================================
-- m005_freeze_legacy.sql — 旧表冻结双保险（T09，Q1 只读冻结）
-- 来源：详细设计 v1.1 §6.2。subsidies 迁移完成后冻结，禁止写。
-- 应用层配合：routes/subsidy.py 写视图检测 migration done → 403。
-- 可重复执行（IF NOT EXISTS）。
-- =====================================================================

CREATE TRIGGER IF NOT EXISTS deny_subsidies_insert BEFORE INSERT ON subsidies
BEGIN SELECT RAISE(ABORT, 'subsidies 已只读冻结（Q1）'); END;

CREATE TRIGGER IF NOT EXISTS deny_subsidies_update BEFORE UPDATE ON subsidies
BEGIN SELECT RAISE(ABORT, 'subsidies 已只读冻结（Q1）'); END;

CREATE TRIGGER IF NOT EXISTS deny_subsidies_delete BEFORE DELETE ON subsidies
BEGIN SELECT RAISE(ABORT, 'subsidies 已只读冻结（Q1）'); END;
