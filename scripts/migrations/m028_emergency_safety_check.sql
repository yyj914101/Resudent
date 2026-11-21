-- =====================================================================
-- m028_emergency_safety_check.sql — 综合安全检查结构化闭环 + 应急档案/枚举种子（087，轨 A）
-- ① 双表：safety_checks（检查单）/ safety_check_hazards（隐患行，整改闭环追踪在行上）。
--    一对多不进软表（动态台账设计 §十.1），归属固定蓝图门面 safety（B-10 口径）。
-- ② 枚举收敛（任务书约束）：检查类型 / 隐患等级 → dict_items 守卫种子；
--    整改状态机不进字典（状态即行为，唯一真源 models/safety_check.py HAZARD_STATUSES）。
-- ③ 应急工作方案归档位：safety_nodes 根层守卫补「应急工作方案」目录（方案走档案，
--    队伍/物资名册待 078 软表，边界见动态台账设计 §十.6）。
-- 轨 A：备份后手工执行（幂等：CREATE TABLE IF NOT EXISTS + 种子 WHERE NOT EXISTS 可重跑）。
-- 新库由 create_all 直接建表 + 蓝图 before_request 种子（routes/safety_emergency.py）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS safety_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title VARCHAR(120) NOT NULL,
  check_date DATE NOT NULL,
  check_type VARCHAR(30) DEFAULT '',
  location VARCHAR(200) DEFAULT '',
  checker VARCHAR(60) DEFAULT '',
  remark VARCHAR(500) DEFAULT '',
  created_by VARCHAR(50) DEFAULT '',
  created_at DATETIME,
  updated_at DATETIME
);

CREATE TABLE IF NOT EXISTS safety_check_hazards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  check_id INTEGER NOT NULL,
  description VARCHAR(500) NOT NULL,
  level VARCHAR(20) DEFAULT '一般',
  location VARCHAR(200) DEFAULT '',
  rectify_owner VARCHAR(60) DEFAULT '',
  rectify_deadline DATE,
  status VARCHAR(10) NOT NULL DEFAULT '待整改',
  rectify_note VARCHAR(500) DEFAULT '',
  rectified_at DATETIME,
  review_note VARCHAR(500) DEFAULT '',
  reviewed_at DATETIME,
  created_at DATETIME,
  updated_at DATETIME
);

CREATE INDEX IF NOT EXISTS ix_safety_check_hazards_check ON safety_check_hazards (check_id);
CREATE INDEX IF NOT EXISTS ix_safety_check_hazards_status ON safety_check_hazards (status);
CREATE INDEX IF NOT EXISTS ix_safety_checks_date ON safety_checks (check_date);

-- 枚举种子：综合安全检查类型（守卫插入，分类与条目均幂等）
INSERT INTO dict_categories (name, description, is_active, sort_order)
SELECT '综合安全检查类型', '087 B-10：综合安全检查单的检查类型口径', 1, 99
WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = '综合安全检查类型');

INSERT INTO dict_items (category_id, label, value, sort_order, is_active)
SELECT c.id, seed.label, seed.value, seed.sort_order, 1
FROM dict_categories c, (
    SELECT '常规检查' AS label, '常规检查' AS value, 1 AS sort_order UNION ALL
    SELECT '专项检查', '专项检查', 2 UNION ALL
    SELECT '节假日检查', '节假日检查', 3 UNION ALL
    SELECT '上级督查', '上级督查', 4
) AS seed
WHERE c.name = '综合安全检查类型'
  AND NOT EXISTS (
    SELECT 1 FROM dict_items di
    WHERE di.category_id = c.id AND di.label = seed.label
);

-- 枚举种子：安全隐患等级
INSERT INTO dict_categories (name, description, is_active, sort_order)
SELECT '安全隐患等级', '087 B-10：隐患行的等级口径', 1, 99
WHERE NOT EXISTS (SELECT 1 FROM dict_categories WHERE name = '安全隐患等级');

INSERT INTO dict_items (category_id, label, value, sort_order, is_active)
SELECT c.id, seed.label, seed.value, seed.sort_order, 1
FROM dict_categories c, (
    SELECT '一般' AS label, '一般' AS value, 1 AS sort_order UNION ALL
    SELECT '较大', '较大', 2 UNION ALL
    SELECT '重大', '重大', 3
) AS seed
WHERE c.name = '安全隐患等级'
  AND NOT EXISTS (
    SELECT 1 FROM dict_items di
    WHERE di.category_id = c.id AND di.label = seed.label
);

-- 档案种子：应急工作方案根目录（方案走档案；守卫插入同 m012 模式）
INSERT INTO safety_nodes (parent_id, name, node_type, sort_order)
SELECT NULL, '应急工作方案', 'folder', 21
WHERE NOT EXISTS (
    SELECT 1 FROM safety_nodes
    WHERE parent_id IS NULL AND name = '应急工作方案'
);
