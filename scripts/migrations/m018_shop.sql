-- =====================================================================
-- m018_shop.sql — 店铺信息（居民管理组）
-- 新建 shops 表：店铺名称/街道名/门牌号/商户姓名/身份证号/电话。
-- id_card 接 R12 字段级加密：id_card（明文，写后置 NULL）/ id_card_enc（密文）/ id_card_hash（SHA-256）。
-- 轨 A：备份后手工执行（幂等：CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS）。
-- 新库由 create_all 直接建表；本文件仅用于存量库。
-- =====================================================================

CREATE TABLE IF NOT EXISTS shops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL,
  street VARCHAR(100) DEFAULT '',
  house_number VARCHAR(50) DEFAULT '',
  owner_name VARCHAR(50) DEFAULT '',
  id_card VARCHAR(18),
  id_card_enc TEXT,
  id_card_hash VARCHAR(64),
  phone VARCHAR(30) DEFAULT '',
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_shops_name ON shops (name);
