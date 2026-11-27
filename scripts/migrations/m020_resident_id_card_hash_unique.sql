-- =====================================================================
-- m020_resident_id_card_hash_unique.sql — 052 簇 B GLB-08②
-- residents.id_card_hash 加 DB 层唯一索引（022 R-1 残留收口）：
-- id_card 不可变此前仅靠钩子层固化，DB 无兜底。
-- 轨 A（ADR-018）：备份后手工执行；幂等（IF NOT EXISTS）。
-- 前置：先跑 scripts/audit_id_card_duplicates.py 只读审计；
--       存量若有重复，本 CREATE UNIQUE INDEX 会直接失败报错（天然守卫，
--       不会半生效）——须先人工合并/清理档案再重试。
-- 注：SQLite 唯一索引对 NULL 不去重，未建档证件号（hash 为空）的老数据不受影响。
-- =====================================================================

CREATE UNIQUE INDEX IF NOT EXISTS idx_residents_id_card_hash_unique
    ON residents (id_card_hash);
