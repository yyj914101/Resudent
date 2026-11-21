-- =====================================================================
-- m019_device_registry.sql — 局域网设备准入白名单（049）
-- 新建 device_registry 表：每行一台已登记设备（客户机 / 大屏）。
-- status: pending（首次握手待审批）/ approved（放行）/ revoked（立即失效）
-- device_type: client（机器码握手）/ screen（大屏 token，P2）
-- 轨 A：备份后手工执行（幂等：CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS）。
-- 新库由 create_all 直接建表；本文件仅用于存量库。
-- =====================================================================

CREATE TABLE IF NOT EXISTS device_registry (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  machine_code  VARCHAR(16) NOT NULL UNIQUE,
  device_type   VARCHAR(10) DEFAULT 'client',
  credential    TEXT,
  label         VARCHAR(100),
  status        VARCHAR(20) DEFAULT 'pending',
  expire        VARCHAR(20) DEFAULT 'forever',
  first_seen    DATETIME,
  last_seen     DATETIME,
  created_at    DATETIME
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_device_registry_machine_code ON device_registry (machine_code);
CREATE INDEX IF NOT EXISTS ix_device_registry_status ON device_registry (status);
