-- =====================================================================
-- m016_notice.sql — 045 补贴公示（P1 数据层）
-- 新建两表：notice_templates（十类告示模板）/ notice_records（按月公示记录）
-- 轨 A：备份后手工执行（幂等：CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS）。
-- 10 类内置模板种子不写在 SQL 里（单一真源 models/notice.py::DEFAULT_TEMPLATES，
-- 由 ensure_notice_seed() 幂等写入），避免 SQL 与 Python 双份定义漂移。
-- 新库由 create_all 直接建表；本文件仅用于存量库。
-- =====================================================================

CREATE TABLE IF NOT EXISTS notice_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name VARCHAR(100) NOT NULL,
  notice_type VARCHAR(30),
  title VARCHAR(150),
  table_schema TEXT DEFAULT '{}',
  is_custom BOOLEAN DEFAULT 0,
  is_active BOOLEAN DEFAULT 1,
  version INTEGER DEFAULT 1,
  created_by VARCHAR(50),
  updated_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_notice_templates_notice_type
  ON notice_templates (notice_type);

CREATE TABLE IF NOT EXISTS notice_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  template_id INTEGER REFERENCES notice_templates(id),
  notice_type VARCHAR(30),
  period VARCHAR(7),
  title VARCHAR(150),
  table_data TEXT DEFAULT '{}',
  status VARCHAR(20) DEFAULT 'draft',
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_notice_records_template_id ON notice_records (template_id);
CREATE INDEX IF NOT EXISTS ix_notice_records_notice_type ON notice_records (notice_type);
CREATE INDEX IF NOT EXISTS ix_notice_records_period ON notice_records (period);
