# 村务综合管理系统（ShuCun）V2.45 · 多智能体宪法 v2.1

> **本文件是该项目唯一的宪法真源（single source of truth）。**
> 读取方：Codex/OpenAI（AGENTS.md）、opencode（AGENTS.md，CLAUDE.md 仅作 fallback）、Claude Code（经 `CLAUDE.md` 的 `@AGENTS.md` 导入）。其他工具入口一律指向本文件，禁止另建平行规则造成漂移。
> v2.0 升级依据：PRD v1.2.3 定稿（2026-08-10，需求权威）+ 详细设计说明书 v1.1（2026-08-11 签发，设计权威）+ 开发技术文档 v1.0（2026-08-11，实施权威）+ M0 精简验证门记录 + Sprint1 补验记录。全部模板适配标识均已替换为项目真实事实；未经实际运行的命令一律标注「未运行」。
> v2.1 升级依据（2026-09-07，代码事实校准）：按当前源码树实测修正陈旧事实——蓝图 30（原误写 29→33）、模型 29 模块/49 表模型（原误写 23）、tests/ 36 文件（原误写「无 tests/」）、`scripts/migrations` 与 `scripts/checks` 均已落地（原误写「未创建」）、T01–T21 已实现（原误写「未实现」）、移除模块 drop 迁移已落地、`config.py` SECRET_KEY 已整改（原误写「硬编码占位」）；同时校准当前快照缺 `.git`/`seed/`/`*.spec`/`*.iss`/`.env.example` 等。未运行的验收命令仍保持「未运行」，不谎报。

---

## 0. 项目事实与证据锚点

- **项目**：村务综合管理系统（ShuCun）V2.45.1 —— Flask + SQLite 的 Windows 桌面单机应用（支持局域网），在 V2.45 基线上**增量开发**「民政台账（一老一小关怀）」模块。
- **仓库边界**：本目录为源码快照（当前**无 `.git`**，非 git 仓库；2026-08-11 曾 init 但 `.git` 未随本快照保留）。需求/设计/原型/记忆文档位于工作区 `D:\workbuddy\ZcodeProject\Residenttext\` 上层目录（已归档至 `D:\workbuddy\ZcodeProject\doc\`），**不在本代码树内**（见 §2 真源清单）。
- **入口链路**：`run_server.py`（服务入口 V2.45.1）→ `app.py::create_app()`（应用工厂，注册 **30 蓝图**）→ Waitress（0.0.0.0:5000；debug 走 Flask dev server）。
- **运行时数据目录**：`%LOCALAPPDATA%\ShucunManagement\`（data/uploads/backups/logs/temp），由 `run_server.py::ensure_user_dirs()` 创建并注入 `SHUCUN_*` 环境变量。
- **授权模型**：`utils/license.py`（`check_license_on_startup`），未授权强制回退 `127.0.0.1`。
- **当前开发进度（2026-09-07 代码事实校准）**：
  - 需求权威 PRD **v1.2.3 定稿**、设计权威**详细设计说明书 v1.1 已签发**、实施文档**开发技术文档 v1.0** 已产出（不变）。
  - **任务清单 T01–T21 已实现落地**：`models/`（29 模块 / 49 表模型）、`routes/`（30 蓝图）、`tests/`（36 文件）、`scripts/migrations/`（m001–m009 + 移除模块 drop）、`scripts/checks/`（scan_mask/scan_flow_judge/scan_child_rows）均已存在。
  - 移除模块（会议通知/耕地/宅基地建房申请）drop 迁移 `m0xx_drop_removed_modules.sql` 已落地（版权信息为模板层移除、无表）。
  - **pytest 全量回归（2026-09-07 实测，本机 Python 3.13.14 venv）**：夹具修复后 **471 通过 / 0 失败 / 5 跳过 / 0 错误（全绿）**，证据 `pytest_results_allgreen_20260907.xml`。修复历程 **239F → 4F → 1F → 0F**，改动：① `app.py` 新增 `SHUCUN_DISABLE_PASSWORD_CHANGE` 开关（默认关闭，仿 DISABLE_CSRF 先例）+ `tests/conftest.py` 默认禁用 P1-2 改密拦截（排除专项测试）；② conftest 改直写 os.environ，防测试内 `monkeypatch.undo()` 连带撤销开关；③ `test_t20_backup.py` strings 平台降级 + `_unlock` 强制重置 key_holder；④ **`_make_app_t20` 补 patch `Config.UPLOAD_FOLDER`/`UPLOAD_DIR` 至本测试 tmp**（根治 roundtrip：`routes/backup.py` 经 `Config.UPLOAD_FOLDER` 固化值定位 uploads，config 首 import 在全量顺序下可能固化到他处共享目录 → uploads_sha256 校验失败；预置污染 env 可复现、补 patch 后消失）。
- **版本记录**：`修改日志/`（2026-06-22 起逐条）；`docs/`（V2.45 release notes / install guide）。
- **增量主线**：R12 字段级 Fernet AES 加密、A1 单机后台任务、脱敏过滤器四通道、R1 镇级名单导入引擎；参考实现见 `../../docs/03-早期原型/`（import-engine-r1e2、m0-verification）。

## 1. 核心原则与红线

- **真源优先**：动手前先审计当前事实；事实冲突按 §2 优先级处理，不凭旧记忆或"应该是这样"。
- **用户是最终决策者**：产品可见后果（范围、成本、停机、迁移、锁定、数据、不可逆操作）必须由用户拍板并留痕（历史决策：8 项基线、Q1–Q4、D1–D4、A3、2026-08-10 Fernet 拍板）。AI 负责给出推荐主线，不让用户从技术名词里盲选。
- **增量开发纪律**：禁止重写、禁止切换框架/数据库/部署形态；扩展走「复用既有蓝图/模型/模板 + 按需新增」路线；**禁止新建 `services/` 目录**（业务以 `utils/` 横切 + 模型方法承载）。
- **设计红线（违反即返工，来源详细设计说明书 §0）**：
  1. R12 = 字段级 Fernet AES（2026-08-10 拍板），**禁止回到 SQLCipher 库级路线**；数据库 URI 保持 `sqlite:///`。
  2. 敏感字段展示/导出/日志必须过脱敏过滤器；密钥不落盘、不硬编码、不写明文日志。
  3. 授权与权限在服务端边界校验（`utils/permissions.py` + `app.py::enforce_role_permissions`），禁止只靠前端。
  4. `seed/shucun_seed.db` 仅脱敏字典；`*.db`/`uploads/`/`backups/`/`*.key`/运行日志不进 git。
  5. 所有版本/性能数字必须与 M0 实测一致；未验证项如实标注「未验证」，不谎报完成。
  6. 无 Alembic：结构变更一律落 DDL 脚本（`scripts/migrations/`）+ 备份验证，禁止手改库文件。
  7. 迁移/导入/级联三类写操作全部**幂等可重跑**（legacy_id / source_hash / cascade_status）。
- **实测数字锚点（引用一律以此为准，来源 M0 门记录 + Sprint1 补验）**：Fernet 密文长度 phone→100 字符、id_card/bank_card→120 字符；加密写入 ≈1.4 万条/s；哈希等值检索 <0.1ms（复核 0.058ms）；单条 CRUD 1.64ms；全量扫描解密 3000×3 字段 0.129s；年龄分组 julianday 1.53ms；PBKDF2-HMAC-SHA256 310k≈162.6ms；Waitress 4 线程+WAL+busy_timeout=30s 并发 800 请求 0 错误；.wbak 1.2MB/0.075s 生成、恢复 800/800；PyInstaller onedir 23.2s、产物 35.46MB（D8 已接受体积增大）；存量迁移 dry-run 1200→823 成功+377 孤儿入日志、G-4 三年对账一致、重跑幂等。
- 禁止补丁式开发（散落 if、临时兼容分支、硬编码、sleep、mock/假数据绕架构）；禁止选项剧场（给一个推荐主线，备选只用于解释为何不选）。
- 用户说「先看/不动代码/只给判断」时，保持只读审查直到用户明确要求执行。

## 2. 真源清单与优先级

事实冲突按以下顺序（1 最高）：

1. 当前源码、测试、脚本、运行日志、实机/用户侧证据、当前 git 状态。
2. 本文件 `AGENTS.md`（宪法真源）；`CLAUDE.md` 仅为 Claude Code 入口，不是独立真源。
3. 工程内文档：`README.md`、`docs/`（V2.45 release notes/install guide）、`修改日志/`。
4. 需求权威（git 外）：`../../docs/06-产品文档·需求与PRD/阶段四·补丁定稿与闭环/prd-minzheng-taizhang-v123-2026-08-10.md`（PRD **v1.2.3 定稿**，含 Fernet 拍板与 R1-E2 落文）。
5. 设计权威（git 外，2026-08-11 签发）：`../../docs/06-产品文档·需求与PRD/阶段五·详细设计/详细设计说明书-minzheng-taizhang-v1.1-2026-08-11.md`（DDL 全文、Owner map、B1–B13、权限矩阵、**任务清单 T01–T21**、评审签发流程、附录 A 共享约定）；实施文档 `开发技术文档-民政台账-v1.0-2026-08-11.md`（Sprint 排期、测试策略、代码红线 10 条、未决 U1–U7）；同目录评审记录（QA 初审/复审/综合评审）与 class/sequence diagram。
6. 知识链与记忆（git 外）：`../00-知识链-民政台账系统.md`（MOC）、`../md文件清单与内容摘要.md`（索引）、`../PROJECT-MEMORY-民政台账系统-2026-08-11.md`（**最新项目集合记忆**，信息截止 2026-08-11 16:00，含维护规范；08-10 版已废弃作历史快照）、`../.workbuddy/memory/`（逐日工作日志 + MEMORY.md）。
7. 原型参考（git 外，**只读参考，非真源**）：`../../docs/03-早期原型/import-engine-r1e2/`（R1-E2 导入引擎，历史 14 测试通过）、`../../docs/03-早期原型/m0-verification/`（M0 门记录 + m0lib crypto/mask 管线）。

真源缺失、互相矛盾或技术上明显错误时：报告冲突、证据与推荐处置，**等用户确认后再动**。

## 3. 多智能体组织、决策权限与信息流转

本项目以多智能体协作推进，角色分工与详细设计说明书 §7.1 评审签发流程一致。**所有跨成员信息流必须经主理人中转，禁止成员互相直连。**

### 3.1 角色职责

| 角色 | 姓名/代号 | 职责 | 产出物 |
|------|-----------|------|--------|
| 主理人（交付总监） | 齐活林 | 编排整体工作流、分派任务、中转全部信息、汇编结论、阶段签发、冲突升级 | 工作流编排、签发记录 |
| 产品经理 | 许清楚 | 需求分析、PRD 撰写与修订、需求符合性确认（R1–R12 映射无遗漏、EARS/验收/拍板项无漂移） | PRD、评审报告、决策日志 |
| 架构师 | 高见远 | 详细设计主笔、技术方案选型（给推荐主线）、Owner map 落位、任务分解（T01–T21）、D3 五项自检 | 详细设计说明书、开发技术文档、DDL/接口设计 |
| 工程师 | 寇豆码 | 按任务清单实现代码与 DDL、工作量复核会签、全局一致性审查（IS_PASS） | 代码、迁移脚本、静态扫描脚本、测试 |
| QA 工程师 | 严过关 | 测试设计与执行、门禁主审（**一票否决**）、验收标准可测试性复核、数字与 M0 实测一致性核查、智能路由判定 | 测试报告、评审核验表、门禁判定 |

### 3.2 决策权限矩阵

| 决策类型 | 决策者 | 边界 |
|----------|--------|------|
| 产品可见后果（范围/成本/停机/迁移/锁定/不可逆/用户体验） | **用户**（拍板留痕） | AI 给推荐主线，用户确认；决策必须写入 PRD/决策日志 |
| 阶段签发、流程编排、升级裁决 | 主理人 | 五阶段门禁（§4）的签发人 |
| 技术方案选型（框架/加密/架构细节） | 架构师 | 给单一推荐主线；技术选型不抛给用户二选一 |
| 实现细节、代码结构（owner 边界内） | 工程师 | 不得越界改其他 owner |
| 测试门禁判定 | QA（一票否决） | 阻断项当轮收掉；QA 否决可推翻阶段"通过"结论 |
| 冲突裁决 | 主理人 → 用户 | 连续两轮评审不通过或重大事实冲突时升级用户裁决 |

### 3.3 信息流转规则

1. **经主理人中转**：任何成员的产出回传主理人，由主理人汇总结转给下一阶段成员；成员间禁止直接交换结论。
2. **全量上下文传递**：分派给下一成员时，始终携带上一阶段的完整产出（如 PRD → 架构师、设计+任务清单 → 工程师、实现+测试 → QA），不做摘要式裁剪。
3. **留痕**：每个阶段决策、评审结论、用户拍板写入对应文档（评审记录/修改日志/工作记忆），不靠对话记忆。
4. **成员结论非真源**：子 agent/成员的结论必须经主理人对照当前代码、测试、文档验收后才可采信。
5. **语言一致**：所有产出使用与用户输入一致的语言（中文），禁止中英混用。

## 4. 开发阶段执行标准（五阶段门禁）

每阶段有明确输入、产出、负责人、执行标准与门禁；**门禁不通过不得进入下一阶段**（用户工作流总纲要求：每阶段完成后告知用户，决策经用户同意后继续）。

### S0 需求与评审（产品经理主导）
- 输入：用户需求、业务源（`resident_system.md`）；产出：PRD（EARS 句式 + 可测试验收标准 + 追踪矩阵）+ 评审报告 + 决策日志。
- 执行标准：需求规划（11 步工作流）→ PRD 撰写 → 评审（本项目历史模式：40 项 ISS + Q1–Q4 拍板）→ 阶段三三路技术审阅 → 批判性审查（21 项模式）→ 用户拍板留痕。
- 门禁：定稿判定（四维闭环审核通过）。**当前状态：已闭合（PRD v1.2.3 定稿）。**

### S1 详细设计评审（架构师主笔，QA 主审）
- 输入：PRD v1.2.3；产出：详细设计说明书（DDL/接口/模块映射/B1–B13/任务清单）+ 开发技术文档。
- 执行标准：**D3 五项强制 checklist**——① B1–B13 闭环 ② 旧表盘点+映射规则 ③ 密钥拍板记录 ④ 脱敏拦截方案 ⑤ uploads 子目录规范；评审角色分工：QA 主审（一票否决）、工程师会签（实现可行性/工作量）、产品经理会签（需求符合性）、主理人汇编签发。
- 门禁：五项全部 IS_PASS=YES ∧ 三方结论通过 → 签发进入 Sprint；任一项 NO → 架构师 3 工作日内出修订版，仅就差距项复审；**连续两轮不通过 → 主理人升级用户裁决**。**当前状态：已闭合（v1.1 签发）。**

### S2 代码实现（Sprint，工程师主导）
- 输入：任务清单 **T01–T21**（详细设计说明书 §8，含依赖与验收标准）+ 开发技术文档（Sprint 0–3 排期）。
- 执行标准：
  - Sprint0 环境搭建（E0-1 版本漂移处理 / E0-2 原型资产定位 / E0-3 git 基线 / E0-4 静态扫描基线）。
  - Sprint1 基建（T05 密钥+A1+口令通道 → T10 权限 v3 → T06 脱敏 → T07 uploads+导出通道 → T03 B1–B13 贯穿 → T08 数据底座 → T09 存量迁移 → T01/T02 用户指定首两项）为数据安全红线基座，**任何业务功能前置依赖**；Sprint2–4 按 R1→R2→R4→R5→R3→R6；Sprint5–6 二期 R7–R10。
  - 代码红线 10 条（开发技术文档 6.4，见 §11）；每任务按验收标准自验；**全局一致性审查 IS_PASS: YES/NO**（不一致 → 修复后重审，最多 2 轮）。
- 门禁：任务级验收 + 静态扫描（`scripts/checks/scan_mask.py`/`scan_flow_judge.py`/`scan_child_rows.py`）+ Sprint 子门禁；MVP 验收门禁 G-1~G-6。**当前状态：已实现（T01–T21 代码落地），门禁验收结论见评审记录（本次未运行复核）。**

### S3 测试验证（QA 主导）
- 输入：实现产物 + 测试计划；产出：测试报告、门禁判定。
- 执行标准：核心逻辑按风险面补 pytest（`tests/` 已落地 36 文件，见 §12）；性能口径 **5000 人上限备数**（11.7 场景，阈值不变）；声称完成必须附实际运行证据；未验证项如实标注。
- 智能路由判定：源码有 Bug → 返回工程师修复（附失败测试与错误信息）；测试代码有 Bug → QA 自修；全部通过 → 报告成功。**最多 2 轮**，2 轮不过输出报告标注遗留问题。
- 门禁：全量回归无退化 + G-4 对账一致 + MVP G1–G6 全过。**当前状态：回归已实测全绿（2026-09-07：471P/0F/5S，证据 `pytest_results_allgreen_20260907.xml`），S3 门禁待按 G-4/MVP 验收清单复核后签发。**

### S4 文档维护（全员，随改随写）
- 执行标准：用户可见变更必落 `修改日志/`（日期+模块+改动+验证）；设计/接口/验收口径变更回写详细设计说明书（升版）；宪法/规则变更须说明"现有规则为何不够、影响哪些行为"并至少一个真实场景验证；文档完成/失效/被吸收 → 归档或标注非当前。
- 门禁：交付前文档索引无缺失、无"以后再说"TODO 残留。

## 5. 必需工作流与任务执行协议

```text
当前真源审计 → 推理闸 → 唯一 owner 与合同设计 → 测试/fixture/门禁计划
→ 核心实现 → 薄 adapter/UI/路由接线 → 针对性验收 → 文档回写（修改日志/）→ git 边界复核
```

- **任务驱动**：Sprint 期一切实现工作以任务清单 T01–T21 为锚（编号、依赖、验收标准见详细设计说明书 §8）；跨任务共享组件（A1 框架、加密管线、脱敏过滤器）必须先于依赖方落地。
- **增量功能**：先定位受影响蓝图与模型（`routes/`、`models/` 同名模块），复用既有风格；涉及跨入口语义（UI/API/后台任务/导出/加密字段）必须先进入共享层（`utils/` 或模型方法），禁止散落在页面/路由里。
- **修 bug**：先复现、确定失败边界、建立一条可证伪假设；3 次修复失败即停止本地打补丁，重新做 owner/架构/真源诊断。
- **数据/加密/授权/权限/迁移相关改动**：先在 `../../docs/03-早期原型/m0-verification/` 或等价 PoC 环境验证再接入主工程，并扩大验收面。
- **并行**：多 agent 仅用于只读审计或互不重叠域实现；禁止同时改写同一 owner/真源文档。

## 6. 推理闸

编码前至少回答：实际要解决的问题？谁创建/调用/消费？当前真源在哪，是否已有同职责模块/模型/合同？唯一 owner 是哪一层，哪些层禁止成为 owner？是否跨 UI/API/CLI/worker 等入口？更简单更保守的方案是什么，为何不够？最大回归风险是什么，用什么证据阻断？——未闭合不开始实现。

## 7. 设计规则与 Owner map

- `models/`（29 模块 / 49 表模型）：**schema 唯一 owner**。全部在 `models/__init__.py` 注册（含 subsidy_project/resident_tag/visit/warn_record/reconcile/cascade/import_engine/schema_governance/migration 等 T01–T21 新增表）。路由/工具层禁止定义表结构。
- `routes/`（30 蓝图）：只做 HTTP 协议映射与接线，**不拥有业务语义**；防误发校验、级联、脱敏判定、权限判断全部下沉 `utils/` 或模型方法。
- `utils/`（横切能力 owner，增量落位）：`utils/crypto.py`（Fernet 管线，移植 m0lib/crypto_utils.py）、`utils/masking.py`（脱敏，移植 m0lib/mask_utils.py）、`utils/async_tasks.py`（A1 任务框架）、`utils/resident_flow.py`（流动性唯一真值，A4）、`utils/agg_cache.py`（聚合缓存）、扩展 `utils/permissions.py`（Schema v3）。
- **禁止新建 `services/`**：业务服务以模型静态方法类承载——`models/subsidy.py` 增 SubsidyService（防误发/登记/冲正）、`models/death_report.py` 增 CascadeService（级联/撤回）。
- `scripts/migrations/`（新增）：DDL 脚本 + 迁移执行器 + 对账校验（无 Alembic 纪律）。
- `scripts/checks/`（新增）：静态扫描脚本（scan_mask / scan_flow_judge / scan_child_rows），并入 Sprint 门禁与安全门禁。
- `app.py`（create_app 工厂，PRAGMA 注入/权限强制/serve_upload 改造/维护模式拦截）、`run_server.py`（入口/环境初始化）：应用组装 owner，不装业务。
- `launcher.py`：启动口令采集与安全传递通道（B13），**禁止口令落盘/写日志**。
- `templates/`（按蓝图子目录）、`static/`：展示层；复用 Bootstrap5/jQuery 与中文文案风格；**视觉方向**在 CONTEXT.md `ADR-006` 授权的范围内演进（**解锁审美方向，不解锁技术栈**——零构建链、不切框架、不改部署形态三条约束继续有效）。
- `seed/shucun_seed.db`：种子数据 owner，仅脱敏字典。
- `config.py`/`config.ini`：配置 owner。✅ `config.py` 中 `SECRET_KEY` 已整改（P0-1）：走「`SHUCUN_SECRET_KEY` 环境变量 → `DATA_DIR/secret.key` 本地密钥文件 → 首启随机生成」三级加载，不再硬编码。
- 打包/安装：`*.spec`×3（PyInstaller）、`setup.iss`/`setup_installer.iss`（Inno Setup）：部署 owner；**生成物只读不手改**；setup.iss 升级不得删数据目录（B12，验证归口 T07）。
- 变更记录：`修改日志/`：每次用户可见变更落一条。

**Owner 现状（2026-09-07 代码事实校准）**：
- 已落地：`tests/`（36 文件，见 §12）；增量模块代码（T01–T21 已实现，29 模块/49 表模型）。
- 当前源码快照缺失（归档至 `doc/` 或运行时生成，非源码必需）：`.git`/`.gitignore`、`seed/`、`*.spec`×3、`setup.iss`/`setup_installer.iss`、`.env.example`、`uploads/`/`backups/`/`instance/`。
- **待核实（未运行验收）**：subsidy 旧表→新表映射 DDL（T04）、B13 口令通道实测（T05，`launcher.py` 已存在）、setup.iss 升级实测（T07，当前快照无 `*.iss`）。

## 8. 错误分级

- **阻断问题**：破坏核心功能、owner 边界、安全/隐私、数据真相、构建测试、用户关键体验或当前目标链路——当轮必须收掉。数据安全、授权、权限、加密、迁移相关问题一律按阻断处理，不得降级。
- **设计风险**：说明取舍与验收入口。
- **可记录债务**：说明不处理原因与后续入口（未决事项 U1–U7 按最迟时点跟踪）。
- **无关优化**：不进入本轮范围，禁止借机扩大改造。

禁止用情绪强度替代工程优先级；没有证据的担忧不能写成阻断，有证据的阻断不能降级成"后面再说"。

## 9. Stack 适配规则（仅真实使用的技术）

- **Python**（requirements 基线 3.12；M0 venv 实测 3.13.14）：遵循项目既有风格与 import 组织；可恢复问题用结构化错误，禁止裸 `except` 吞错。
- **Flask / Flask-SQLAlchemy**（requirements 3.0.0 / 3.1.1；M0 venv 实测 3.1.3 / 2.0.51）：应用工厂 + 蓝图模式。⚠️ **版本漂移（E0-1 处理项）**：requirements 与 M0 venv 版本不一致，涉及 SQLAlchemy/Flask 行为差异以实际运行环境实测为准并同步 requirements。
- **SQLite（WAL，单写者）**：`connect_args timeout=30`（busy_timeout=30s）；A1 后台任务不得与请求线程并发写冲突；无 Alembic，结构变更落 DDL + 备份验证。
- **加密（cryptography/Fernet，字段级）**：敏感字段 Fernet 加密存储 + 哈希等值检索；启动口令经 PBKDF2（310k 次/16 字节盐）派生密钥；密钥不落盘；存量明文库先 dry-run 再转换；**禁止回到 SQLCipher**。
- **UI（Jinja2 + Bootstrap + jQuery）**：复用现有模板/静态资源。
- **文件处理（openpyxl / python-docx / 二期 reportlab）**：Excel 导入走 R1-E2 管线（映射/校验/容错/血缘/阈值守卫）；导出走独立下载通道（`temp/exports/` token 通道，≤7 天清理），**禁止经 `serve_upload`**（工程师红线，防越权破口）。
- **服务/部署（Waitress / PyInstaller / Inno Setup）**：生产用 Waitress；打包产物只读；spec 变更须回归三件套打包冒烟。

## 10. Adapter 适配块

以下适配块在本项目真实采用对应技术时启用（均已启用），遵循「低于本宪法、高于普通建议」：

### ADAPTER: framework/flask
applies_when: "项目使用 Flask"
verification: `python run_server.py`（未运行）
- 遵循工厂 + 蓝图约定、错误处理、模板渲染；不私造并行体系。

### ADAPTER: language-runtime/python
applies_when: "项目使用 Python"
verification: `python -m py_compile` 全量文件（未运行）
- 遵循 PEP8 与项目既有风格；import 组织与现有模块一致。

### ADAPTER: database/sqlite
applies_when: "项目使用 SQLite"
verification: `python init_db.py`（未运行，仅可对新库执行）
- 单写者、WAL、busy_timeout=30s；不手工改库文件；变更前备份。

### ADAPTER: database/fernet-encryption
applies_when: "R12 字段级加密启用（PRD v1.2.3 拍板）"
verification: `../../docs/03-早期原型/m0-verification/verify/p09_fernet_verify.py`（未运行）
- M0 门六项 PASS + Sprint1 补验闭环；Sprint1 内由 T05 落 `utils/crypto.py`、T09 落存量迁移，密文长度与吞吐必须符合 §1 数字锚点。

### ADAPTER: toolchain/pyinstaller
applies_when: "项目使用 PyInstaller 打包"
verification: `pyinstaller shucun_server.spec`（未运行）
- 打包产物只读；spec 变更须回归三件套打包冒烟；体积增大（35.46MB）已获 D8 知情确认。

### ADAPTER: toolchain/innosetup
applies_when: "项目使用 Inno Setup 安装包"
verification: 编译 `setup.iss` / `setup_installer.iss`（未运行）
- 升级不得删除数据目录（B12，实测验证归口 T07）。

### ADAPTER: platform/localappdata
applies_when: "Windows 桌面单机部署"
verification: 运行 `run_server.py` 后检查 `%LOCALAPPDATA%\ShucunManagement`（未运行）
- 运行时数据与程序目录分离；备份数据目录即备份系统。

## 11. 实现规则与共享约定（工程师红线速查，来源详细设计附录 A + 开发技术文档 6.4）

1. **owner map**：表结构唯一 owner=models/；路由只做接线；业务判断进 `utils/` 或模型静态方法；禁止新建 `services/` 目录。
2. **加密红线**：敏感字段必须走 `utils/crypto.py`；密钥不落盘/不硬编码/不写明文日志；禁止回到 SQLCipher。
3. **脱敏红线**：任何出口（页面/导出/日志/任务摘要）默认脱敏；脱敏唯一拦截点在服务端；明文仅 `view_sensitive` 专用端点。
4. **权限红线**：服务端边界校验（`utils/permissions.py`）；special 权限位一律显式装饰器；儿童行级一律 `@child_rows_guard`。
   - 🔴 **默认拒绝（ADR-016，2026-09-23）**：全局鉴权 `enforce_role_permissions` 对**未归类端点一律按 `__admin__` 拒绝**。**新增任何蓝图/端点必须三选一归类**，否则全部端点 403：① `BLUEPRINT_MODULES`（模块权限键）② `ADMIN_BLUEPRINTS`（admin 专属）③ `PUBLIC_ENDPOINTS`（**免登录 + 免权限**，进入者**必须在端点内自证** —— 装饰器或体内校验）。
   - ⚠️ `PUBLIC_ENDPOINTS` 是 `enforce_role_permissions` 的**唯一豁免名单**，也是**唯一的登录门禁**——进表即跳过登录+权限双校验，务必确认端点自证完备。
5. **视图命名**：新视图名必须含引擎推断关键字清单之一；special 位不依赖命名推断。
6. **幂等**：迁移/导入/级联三类写操作全部幂等可重跑（legacy_id / source_hash / cascade_status）。
7. **数据库纪律**：无 Alembic；结构变更落 DDL 脚本 + 备份验证；禁止手改库文件；单写者模型（busy_timeout=30s）。
8. **导出纪律**：导出文件禁经 serve_upload，走 `temp/exports/` 独立 token 通道；≤7 天清理。
9. **增量纪律**：禁止重写/切换框架/改部署形态；用户可见变更同步 `修改日志/`。
10. **测试纪律**：声称完成必须附实际运行证据；未验证项如实标注。
- 共享约定：JSON 响应 `{"success","msg","data"}`；日期 ISO 格式、period=`YYYY-MM`、金额 NUMERIC(10,2)；业务记录只软失效不物理删除；subsidy_record 业务字段只增不改（冲正），status 允许正常→停发迁移；流动性唯一入口 `utils/resident_flow.py`。
- 用户可见文案、错误提示、报告标题遵守既有中文文案真源，禁止临时硬编码自然语言。

## 12. 测试与验收规则

- ✅ **主工程 `tests/` 已落地（36 文件）**：覆盖加密/脱敏/权限/CSRF/迁移/级联/防误发/导入/报表/缓存/走访/预警/备份/PDF 等（`test_t01_t02.py` ~ `test_t21_pdf.py`）。新增核心逻辑（加密/迁移/级联/防误发等）仍必须按风险面补 pytest；测试未附实际运行证据前不得声称"完成"。
- 静态扫描三脚本（T03/T06 产出）并入 Sprint 子门禁与 G-2/G-5/K8 安全门禁；扫描基线：脱敏视图命中 0 / 明文视图命中 16（M4 参考值）。
- 性能口径：11.7 场景按 **5000 人上限备数**（阈值不变）；P4 导入 3000 行 ≤60s、首页聚合 ≤2s、导出 ≤30s/3000 行。
- 声称"完成/修好/通过"前，必须提供本轮**实际运行**过的命令、测试、日志、截图或实机证据；无法运行关键验收必须说明原因、影响范围与替代证据。
- 发现失败：先复现、收集证据、建立可证伪假设，再修改；禁止无复现地猜测。UI 改动检查真实渲染/交互/加载态/错误态。
- **验收命令 map（2026-09-07 只读体检已执行；运行状态仍均「未运行」）**：

| 命令 | 类型 | 位置 | 状态 |
|------|------|------|------|
| `python run_server.py` | dev server | 本目录 | 未运行 |
| `python -m pip install -r requirements.txt`（`requirements-build.txt`） | install | 本目录 | 未运行 |
| `python -m py_compile` 全量文件 | 语法检查 | 本目录 | 未运行 |
| `pyinstaller shucun_server.spec` / `ShuCunLauncher.spec` / `ShuCunClient.spec` | build | 本目录（当前快照无 `*.spec`） | 未运行 |
| Inno Setup `setup.iss` / `setup_installer.iss` | deploy | 本目录（当前快照无 `*.iss`） | 未运行 |
| `pytest`（`../../docs/03-早期原型/import-engine-r1e2/`，历史 14 通过） | test | 工作区原型 | 未运行 |
| `pytest`（主工程 `tests/` 全量 36 文件） | test | 本目录 | 2026-09-07 已运行：**471P/0F/5S/0E（全绿）**，证据 `pytest_results_allgreen_20260907.xml` |
| M0 验证脚本（`../../docs/03-早期原型/m0-verification/verify/*.py` + `qa/qa_independent_check.py`） | test | 工作区原型 | 未运行 |
| `scripts/checks/scan_mask.py` 等静态扫描 | 安全门禁 | 本目录 `scripts/checks/` | 已存在（未运行） |
| `scripts/migrations/run_migration.py` 等 | 迁移 | 本目录 `scripts/migrations/` | 已存在（未运行） |
| format / lint / typecheck / CI | — | 未找到配置 | 不存在 |

## 13. 文档维护标准

- 内部真源/治理 → 本宪法 + `修改日志/`；需求 → PRD v1.2.3（§2.4）；设计 → 详细设计说明书 v1.1（§2.5）；外部用户文档 → `docs/`。
- 同一变更同时影响用户行为与内部机制时，分别写入对应层。
- 设计/接口/验收口径变更 → 回写详细设计说明书并升版（含附录 C 变更记录），QA 复审。
- 文档完成/失效/被吸收/一次性 → 归档或标注非当前，不留误导主目录。

## 14. Git 规则

- 仓库边界 = 本目录。工作区文档（`../../docs/06-产品文档·需求与PRD/` 等）与原型（`../../docs/03-早期原型/`）**不在本仓库内**，禁止用本仓库 git 操作它们。
- 提交前确认 `git status --short`、staged file list、ignored 边界（`.gitignore` 已覆盖 `*.db`/`uploads/`/`backups/`/`*.key`/`*.log` 等）。
- 禁止 `git add .`；只 stage 与本任务相关的文件。禁止 force-add ignored 文件；dirty worktree 中不得回滚、覆盖或吸入用户未授权改动。
- 当前源码树**无 `.git`**（非 git 仓库；2026-08-11 曾 init 但 `.git` 未随本快照保留）：如需版本管理须重新 `git init`，提交范围须用户确认（建议仅源码/宪法/入口文件，排除 `*.db`/`secret.key`/运行日志）。若推送远程**必须设为私有**（含授权校验逻辑）。
- 不使用破坏性 git 命令，除非用户明确要求并确认风险。

## 15. 必须停止并询问

用户需求、当前代码、宪法、真源文档或验收结果互相冲突；需要无意义兼容/保留旧产品残影/同时支持互斥路线；需要创建、改写或废弃宪法/核心 API/schema/权限模型/部署流程；需要删除旧 API/字段/路由/配置/迁移历史；证据表明目标会损害架构、数据、安全、权限、用户体验或长期可维护性；owner 边界不足以判断且猜测不可逆；评审连续两轮不通过（升级用户裁决）。停止时给出：冲突证据、可选处理方向、推荐方向、需要用户确认的问题。

## 16. 交接规则

上下文过大/换窗口/交给其他 agent/任务未闭合时，产出可直接复制的交接文本，包含：当前目标与已确认的产品/架构边界；当前开发阶段（S0–S4）与 T01–T21 进度；当前 git 状态、已改/未提交文件；已完成工作与实际验收命令和结果；未闭合风险、漂移警告与禁止触碰的用户改动；下一步最安全命令与停止条件。
