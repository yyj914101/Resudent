-- =====================================================================
-- m015_import_profiles_permission_fix.sql — 052 簇 C R-036-05
-- 修正 m014 种子的 required_permission 口径不一致：
--   m014 写 'resident.edit'，而 save_profile 默认值与路由实际判定均为
--   'special.import_batch'（021 API-03 显式装饰器口径）。统一为后者。
-- 轨 A（ADR-018）：m014 历史脚本不动（已执行库不可回滚），本脚本幂等 UPDATE。
-- =====================================================================

UPDATE import_profiles
   SET required_permission = 'special.import_batch',
       updated_at = CURRENT_TIMESTAMP
 WHERE profile_key = 'standard_v1'
   AND required_permission = 'resident.edit';
