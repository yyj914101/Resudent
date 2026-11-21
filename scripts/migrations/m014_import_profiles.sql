-- =====================================================================
-- m014_import_profiles.sql — 036 P3 导入方案复用与治理（F1）
-- 新建 import_profiles（映射方案），并写入内置 standard_v1 种子。
-- 轨 A（ADR-018）：备份后手工执行本文件；禁启动期建表/ensure。
-- 幂等：CREATE TABLE IF NOT EXISTS + INSERT OR IGNORE（同一 profile_key 不重复插入）。
-- standard_v1.mapping_json 由 FIELD_CATALOG 派生（24 项，含「人员状态」特列；
-- 模板下载列按 034 口径不含「所在网格」，映射目标仍支持）；catalog_version =
-- 目录结构短 hash，字段增删/改名/别名变化时须重出下一版迁移。
-- 新库由 create_all 建表，但种子同样需执行本文件（服务启动不写库）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS import_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_key VARCHAR(40) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  is_builtin BOOLEAN NOT NULL DEFAULT 0,
  catalog_version VARCHAR(20),
  mapping_json TEXT NOT NULL,
  default_policy VARCHAR(20) DEFAULT 'skip',
  required_permission VARCHAR(60),
  created_by VARCHAR(50),
  created_at DATETIME,
  updated_at DATETIME
);

INSERT OR IGNORE INTO import_profiles
  (profile_key, name, is_builtin, catalog_version, mapping_json,
   default_policy, required_permission, created_by, created_at, updated_at)
VALUES
  ('standard_v1', '标准模板 v1', 1, 'v1-10a834a7',
   '[{"header":"姓名","field":"name","transform":"map"},{"header":"身份证号","field":"id_card","transform":"map"},{"header":"出生日期","field":"birth_date","transform":"map"},{"header":"性别","field":"gender","transform":"map"},{"header":"民族","field":"nation","transform":"map"},{"header":"联系电话","field":"phone","transform":"map"},{"header":"所在组","field":"village_group","transform":"map"},{"header":"所在网格","field":"grid","transform":"map"},{"header":"家庭住址","field":"address","transform":"map"},{"header":"户主姓名","field":"household_name","transform":"map"},{"header":"户编号","field":"household_code","transform":"map"},{"header":"与户主关系","field":"relationship","transform":"map"},{"header":"户口类型","field":"household_type","transform":"map"},{"header":"政治面貌","field":"politics_status","transform":"map"},{"header":"学历","field":"education","transform":"map"},{"header":"职业","field":"occupation","transform":"map"},{"header":"健康状态","field":"health_status","transform":"map"},{"header":"户属性","field":"household_property","transform":"map"},{"header":"开户行","field":"bank_name","transform":"map"},{"header":"银行卡号","field":"bank_card","transform":"map"},{"header":"婚姻状况","field":"marital_status","transform":"map"},{"header":"兵役状况","field":"military_status","transform":"map"},{"header":"人员状态","field":"status","transform":"map"},{"header":"备注","field":"remark","transform":"map"}]',
   'skip', 'resident.edit', 'system', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
