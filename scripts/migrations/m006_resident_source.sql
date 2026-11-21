-- =====================================================================
-- m006_resident_source.sql — 居民建档来源 + phone 前缀桶（T11 流动标记/读密文闭环）
-- 来源：Sprint2 T11（R1 常住人口库增强）。可重复执行（IF NOT EXISTS 索引；
-- ALTER TABLE ADD COLUMN 由 run_migration.py 列存在性检查兜底，参照 m002 先例）。
-- 目的：
--   1. residents.source：建档来源（手工/导入），满足 PRD R1「来源可查」+ 按来源筛选
--   2. residents.phone_prefix_hash：phone 模糊搜索候选桶（与 id_card_prefix_hash 对齐，
--      支撑 T11-1 搜索改 hash 后 phone 的 <6 位前缀匹配）
-- =====================================================================

-- residents：建档来源（默认 手工）
ALTER TABLE residents ADD COLUMN source VARCHAR(10) NOT NULL DEFAULT '手工'; -- 手工/导入

-- residents：phone 前缀桶哈希（sha256(前6位)，模糊候选桶，应用层二次解密过滤）
ALTER TABLE residents ADD COLUMN phone_prefix_hash VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_residents_source ON residents(source);
CREATE INDEX IF NOT EXISTS idx_residents_phone_prefix_hash ON residents(phone_prefix_hash);

-- =====================================================================
