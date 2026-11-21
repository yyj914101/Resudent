-- =====================================================================
-- m012_safety_nodes.sql — 安全工作模块档案目录树（030 交付物 5）
-- 本 DDL 为 safety_nodes 结构唯一真源（宪法 §1.6 / 030 任务书）；
-- 运行时幂等兜底由 routes/safety.py::_apply_m012() 读取本文件执行，
-- 与 ddl_guard 读 m011 先例一致（CREATE TABLE IF NOT EXISTS + 种子守卫插入）。
-- 顺延 m011 之后；m004 历史缺号不顺延。
--
-- 同级唯一性由【应用层】校验：folder 同级不重名；file 同名允许但提示。
-- 不设 DB 层 UNIQUE：SQLite 中 parent_id IS NULL 组在 UNIQUE 下互不相等，
-- 根级管不住，统一走应用层（030 审阅 P1 结论）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS safety_nodes (
    id            INTEGER PRIMARY KEY,
    parent_id     INTEGER,                     -- NULL=根
    name          VARCHAR(120) NOT NULL,
    node_type     VARCHAR(10)  NOT NULL DEFAULT 'folder',  -- folder | file
    sort_order    INTEGER DEFAULT 0,
    stored_name   VARCHAR(80),                 -- 仅 file：UUID 落盘名
    original_name VARCHAR(200),                -- 仅 file：原名
    ext           VARCHAR(20),
    size          INTEGER DEFAULT 0,
    mime          VARCHAR(100),
    created_by    VARCHAR(50),
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME                      -- 应用层维护（重命名/移动/子项变更时刷新）
);

CREATE INDEX IF NOT EXISTS ix_safety_nodes_parent ON safety_nodes(parent_id);

-- 20 类一级节点种子（sort_order 按方案 §一列举顺序；守卫插入幂等可重跑）
INSERT INTO safety_nodes (parent_id, name, node_type, sort_order)
SELECT NULL, seed.name, 'folder', seed.sort_order
FROM (
    SELECT '安全隐患' AS name,  1 AS sort_order UNION ALL
    SELECT '防汛减灾',          2 UNION ALL
    SELECT '工作简报',          3 UNION ALL
    SELECT '急难愁盼',          4 UNION ALL
    SELECT '建筑安全',          5 UNION ALL
    SELECT '交通资料',          6 UNION ALL
    SELECT '矛盾纠纷',          7 UNION ALL
    SELECT '溺水安全',          8 UNION ALL
    SELECT '其他资料',          9 UNION ALL
    SELECT '燃气资料',         10 UNION ALL
    SELECT '人社资料',         11 UNION ALL
    SELECT '人员信息',         12 UNION ALL
    SELECT '森林防火',         13 UNION ALL
    SELECT '社区图片',         14 UNION ALL
    SELECT '食品安全',         15 UNION ALL
    SELECT '水利资料',         16 UNION ALL
    SELECT '通知模板',         17 UNION ALL
    SELECT '消防资料',         18 UNION ALL
    SELECT '信访维稳',         19 UNION ALL
    SELECT '征兵资料',         20
) AS seed
WHERE NOT EXISTS (
    SELECT 1 FROM safety_nodes sn
    WHERE sn.parent_id IS NULL AND sn.name = seed.name
);