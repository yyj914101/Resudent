-- =====================================================================
-- m013_import_batch_mode.sql — 034 居民导入双通道统一（P1）
-- import_batch 增加四列：mode（template|custom）/ profile_key（P3 预留，暂空）
-- / policy（skip|update_non_empty）/ updated_rows（覆盖更新计数，双模式四计数一致口径）
-- 顺延 m012；轨 A：备份后手工执行本文件（SQLite ALTER TABLE ADD COLUMN）。
-- 模型（models/import_engine.py）已同步；新库由 create_all 直接建齐，本文件仅用于存量库。
-- =====================================================================

ALTER TABLE import_batch ADD COLUMN mode VARCHAR(10) DEFAULT 'custom';
ALTER TABLE import_batch ADD COLUMN profile_key VARCHAR(40);
ALTER TABLE import_batch ADD COLUMN policy VARCHAR(20) DEFAULT 'skip';
ALTER TABLE import_batch ADD COLUMN updated_rows INTEGER DEFAULT 0;