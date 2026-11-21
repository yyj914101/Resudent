-- =====================================================================
-- m017_governance.sql — 综治管理（4 台账：特困走访/社会稳定风险/严重精神障碍/治保委员会）
-- 新建 governance_ledgers 表（配置驱动通用台账，payload 存各类型专属字段）。
-- id_card 接 R12 字段级加密：id_card（明文，写后置 NULL）/ id_card_enc（密文）/ id_card_hash（SHA-256）。
-- 轨 A：备份后手工执行（幂等：CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS）。
-- 新库由 create_all 直接建表；本文件仅用于存量库。
-- =====================================================================

CREATE TABLE IF NOT EXISTS governance_ledgers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category VARCHAR(40) NOT NULL,
  subtype VARCHAR(40) DEFAULT '',
  name VARCHAR(50) DEFAULT '',
  id_card VARCHAR(18),
  id_card_enc TEXT,
  id_card_hash VARCHAR(64),
  phone VARCHAR(30) DEFAULT '',
  record_date DATE,
  payload TEXT DEFAULT '{}',
  created_by VARCHAR(50) DEFAULT '',
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_governance_ledgers_category ON governance_ledgers (category);
CREATE INDEX IF NOT EXISTS ix_governance_ledgers_name ON governance_ledgers (name);
CREATE INDEX IF NOT EXISTS ix_governance_ledgers_record_date ON governance_ledgers (record_date);
