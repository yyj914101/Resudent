-- =====================================================================
-- m003_sensitive_columns.sql — 18 表 40 字段加密迁移 DDL（T08 数据底座）
-- 来源：详细设计 v1.1 §3.3。可重复执行（IF NOT EXISTS）。
-- 通用模式（每字段 X）：X_enc TEXT（Fernet 密文）+ X_hash VARCHAR(64)（SHA-256）+ 索引；
--   迁移完成后原列置 NULL 冻结（见 §3.4）。
-- 唯一约束仅两处：residents.id_card_hash、veteran.id_card_hash。
-- 注：SQLite 无 ADD COLUMN IF NOT EXISTS，需由 run_migration.py 做列存在性检查兜底。
-- 合计：id_card 类 20 + phone 类 15（含 guardian 联系方式 3）+ bank_card 类 5 = 40。
-- =====================================================================

-- 1. residents（3 字段；id_card 唯一约束 + 前缀桶）
ALTER TABLE residents ADD COLUMN id_card_enc TEXT;
ALTER TABLE residents ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE residents ADD COLUMN id_card_prefix_hash VARCHAR(64);   -- sha256(前6位)，模糊候选桶
ALTER TABLE residents ADD COLUMN phone_enc TEXT;
ALTER TABLE residents ADD COLUMN phone_hash VARCHAR(64);
ALTER TABLE residents ADD COLUMN bank_card_enc TEXT;
ALTER TABLE residents ADD COLUMN bank_card_hash VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS idx_residents_id_card_hash ON residents(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_residents_id_card_prefix_hash ON residents(id_card_prefix_hash);
CREATE INDEX IF NOT EXISTS idx_residents_phone_hash ON residents(phone_hash);
CREATE INDEX IF NOT EXISTS idx_residents_bank_card_hash ON residents(bank_card_hash);

-- 2. dibao（4 字段）
ALTER TABLE dibao ADD COLUMN household_id_card_enc TEXT;
ALTER TABLE dibao ADD COLUMN household_id_card_hash VARCHAR(64);
ALTER TABLE dibao ADD COLUMN id_card_enc TEXT;
ALTER TABLE dibao ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE dibao ADD COLUMN account_holder_id_card_enc TEXT;
ALTER TABLE dibao ADD COLUMN account_holder_id_card_hash VARCHAR(64);
ALTER TABLE dibao ADD COLUMN bank_card_enc TEXT;
ALTER TABLE dibao ADD COLUMN bank_card_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_dibao_household_id_card_hash ON dibao(household_id_card_hash);
CREATE INDEX IF NOT EXISTS idx_dibao_id_card_hash ON dibao(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_dibao_account_holder_id_card_hash ON dibao(account_holder_id_card_hash);
CREATE INDEX IF NOT EXISTS idx_dibao_bank_card_hash ON dibao(bank_card_hash);

-- 3. disabled_persons（3 字段，含 guardian 联系方式）
ALTER TABLE disabled_persons ADD COLUMN id_card_enc TEXT;
ALTER TABLE disabled_persons ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE disabled_persons ADD COLUMN phone_enc TEXT;
ALTER TABLE disabled_persons ADD COLUMN phone_hash VARCHAR(64);
ALTER TABLE disabled_persons ADD COLUMN guardian_phone_enc TEXT;
ALTER TABLE disabled_persons ADD COLUMN guardian_phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_disabled_persons_id_card_hash ON disabled_persons(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_disabled_persons_phone_hash ON disabled_persons(phone_hash);
CREATE INDEX IF NOT EXISTS idx_disabled_persons_guardian_phone_hash ON disabled_persons(guardian_phone_hash);

-- 4. care_targets（1 字段，guardian 联系方式，补验⑤同管线）
ALTER TABLE care_targets ADD COLUMN guardian_phone_enc TEXT;
ALTER TABLE care_targets ADD COLUMN guardian_phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_care_targets_guardian_phone_hash ON care_targets(guardian_phone_hash);

-- 5. special_support（4 字段）
ALTER TABLE special_support ADD COLUMN household_id_card_enc TEXT;
ALTER TABLE special_support ADD COLUMN household_id_card_hash VARCHAR(64);
ALTER TABLE special_support ADD COLUMN bank_card_enc TEXT;
ALTER TABLE special_support ADD COLUMN bank_card_hash VARCHAR(64);
ALTER TABLE special_support ADD COLUMN account_holder_id_card_enc TEXT;
ALTER TABLE special_support ADD COLUMN account_holder_id_card_hash VARCHAR(64);
ALTER TABLE special_support ADD COLUMN phone_enc TEXT;
ALTER TABLE special_support ADD COLUMN phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_special_support_household_id_card_hash ON special_support(household_id_card_hash);
CREATE INDEX IF NOT EXISTS idx_special_support_bank_card_hash ON special_support(bank_card_hash);
CREATE INDEX IF NOT EXISTS idx_special_support_account_holder_id_card_hash ON special_support(account_holder_id_card_hash);
CREATE INDEX IF NOT EXISTS idx_special_support_phone_hash ON special_support(phone_hash);

-- 6. veteran（3 字段；id_card 原 NOT NULL + 唯一索引 → 唯一约束迁移至 hash 列）
ALTER TABLE veteran ADD COLUMN id_card_enc TEXT;
ALTER TABLE veteran ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE veteran ADD COLUMN phone_enc TEXT;
ALTER TABLE veteran ADD COLUMN phone_hash VARCHAR(64);
ALTER TABLE veteran ADD COLUMN relative_phone_enc TEXT;
ALTER TABLE veteran ADD COLUMN relative_phone_hash VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS idx_veteran_id_card_hash ON veteran(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_veteran_phone_hash ON veteran(phone_hash);
CREATE INDEX IF NOT EXISTS idx_veteran_relative_phone_hash ON veteran(relative_phone_hash);

-- 7. death_reports（1 字段，原 ix_death_reports_id_card 随原列冻结废弃）
ALTER TABLE death_reports ADD COLUMN id_card_enc TEXT;
ALTER TABLE death_reports ADD COLUMN id_card_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_death_reports_id_card_hash ON death_reports(id_card_hash);

-- 8. subsidies（2 字段；旧表迁移后冻结，加密后原列置 NULL，见 6.2）
ALTER TABLE subsidies ADD COLUMN id_card_enc TEXT;
ALTER TABLE subsidies ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE subsidies ADD COLUMN bank_card_enc TEXT;
ALTER TABLE subsidies ADD COLUMN bank_card_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_subsidies_id_card_hash ON subsidies(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_subsidies_bank_card_hash ON subsidies(bank_card_hash);

-- 9. medical_insurance_payments（3 字段，原 ix 随原列冻结废弃）
ALTER TABLE medical_insurance_payments ADD COLUMN id_card_enc TEXT;
ALTER TABLE medical_insurance_payments ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE medical_insurance_payments ADD COLUMN contact_phone_enc TEXT;
ALTER TABLE medical_insurance_payments ADD COLUMN contact_phone_hash VARCHAR(64);
ALTER TABLE medical_insurance_payments ADD COLUMN mobile_phone_enc TEXT;
ALTER TABLE medical_insurance_payments ADD COLUMN mobile_phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_medical_insurance_payments_id_card_hash ON medical_insurance_payments(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_medical_insurance_payments_contact_phone_hash ON medical_insurance_payments(contact_phone_hash);
CREATE INDEX IF NOT EXISTS idx_medical_insurance_payments_mobile_phone_hash ON medical_insurance_payments(mobile_phone_hash);

-- 10. party_members（2 字段）
ALTER TABLE party_members ADD COLUMN id_card_enc TEXT;
ALTER TABLE party_members ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE party_members ADD COLUMN phone_enc TEXT;
ALTER TABLE party_members ADD COLUMN phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_party_members_id_card_hash ON party_members(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_party_members_phone_hash ON party_members(phone_hash);

-- 11. organization_members（2 字段）
ALTER TABLE organization_members ADD COLUMN id_card_enc TEXT;
ALTER TABLE organization_members ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE organization_members ADD COLUMN phone_enc TEXT;
ALTER TABLE organization_members ADD COLUMN phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_organization_members_id_card_hash ON organization_members(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_organization_members_phone_hash ON organization_members(phone_hash);

-- 12. league_members（2 字段）
ALTER TABLE league_members ADD COLUMN id_card_enc TEXT;
ALTER TABLE league_members ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE league_members ADD COLUMN phone_enc TEXT;
ALTER TABLE league_members ADD COLUMN phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_league_members_id_card_hash ON league_members(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_league_members_phone_hash ON league_members(phone_hash);

-- 13. employment（1 字段）
ALTER TABLE employment ADD COLUMN id_card_enc TEXT;
ALTER TABLE employment ADD COLUMN id_card_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_employment_id_card_hash ON employment(id_card_hash);

-- 14. building_applications（2 字段）
ALTER TABLE building_applications ADD COLUMN id_card_enc TEXT;
ALTER TABLE building_applications ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE building_applications ADD COLUMN phone_enc TEXT;
ALTER TABLE building_applications ADD COLUMN phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_building_applications_id_card_hash ON building_applications(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_building_applications_phone_hash ON building_applications(phone_hash);

-- 15. mediations（2 字段；T09 补 respondent_id_card，修正 §3.3 遗漏）
ALTER TABLE mediations ADD COLUMN applicant_id_card_enc TEXT;
ALTER TABLE mediations ADD COLUMN applicant_id_card_hash VARCHAR(64);
ALTER TABLE mediations ADD COLUMN respondent_id_card_enc TEXT;
ALTER TABLE mediations ADD COLUMN respondent_id_card_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_mediations_applicant_id_card_hash ON mediations(applicant_id_card_hash);
CREATE INDEX IF NOT EXISTS idx_mediations_respondent_id_card_hash ON mediations(respondent_id_card_hash);

-- 16. risk_checks（1 字段；P1-1 整改补 id_card_prefix_hash 供模糊候选桶）
ALTER TABLE risk_checks ADD COLUMN id_card_enc TEXT;
ALTER TABLE risk_checks ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE risk_checks ADD COLUMN id_card_prefix_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_risk_checks_id_card_hash ON risk_checks(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_risk_checks_id_card_prefix_hash ON risk_checks(id_card_prefix_hash);

-- 17. unsupported_children（2 字段）
ALTER TABLE unsupported_children ADD COLUMN id_card_enc TEXT;
ALTER TABLE unsupported_children ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE unsupported_children ADD COLUMN phone_enc TEXT;
ALTER TABLE unsupported_children ADD COLUMN phone_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_unsupported_children_id_card_hash ON unsupported_children(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_unsupported_children_phone_hash ON unsupported_children(phone_hash);

-- 18. welfare_jobs（3 字段；bank_info 为文本列可能含卡号，按 bank_card 类整体加密）
ALTER TABLE welfare_jobs ADD COLUMN id_card_enc TEXT;
ALTER TABLE welfare_jobs ADD COLUMN id_card_hash VARCHAR(64);
ALTER TABLE welfare_jobs ADD COLUMN phone_enc TEXT;
ALTER TABLE welfare_jobs ADD COLUMN phone_hash VARCHAR(64);
ALTER TABLE welfare_jobs ADD COLUMN bank_info_enc TEXT;
ALTER TABLE welfare_jobs ADD COLUMN bank_info_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_welfare_jobs_id_card_hash ON welfare_jobs(id_card_hash);
CREATE INDEX IF NOT EXISTS idx_welfare_jobs_phone_hash ON welfare_jobs(phone_hash);
CREATE INDEX IF NOT EXISTS idx_welfare_jobs_bank_info_hash ON welfare_jobs(bank_info_hash);
