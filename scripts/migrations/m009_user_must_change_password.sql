-- =====================================================================
-- m009_user_must_change_password.sql — 用户强制改密标记（P1-2 整改）
-- 来源：代码审阅报告 P1-2「默认弱密码 + 无强制改密 + 登录无限流 + 重置明文返回」。
-- 目的：users 表新增 must_change_password 布尔列，支撑「首次登录强制改密」。
-- 可重复执行：run_migration.py 列存在性检查兜底；此处保留给新库一次性建全。
-- =====================================================================

ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT 0;
