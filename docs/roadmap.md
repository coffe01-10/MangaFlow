# MangaFlow 主分支后续工作清单

更新时间：2026-08-27
审查基线：`master` / `6e0a534`（2026-08-26 制定）；2026-08-27 起 P0 与 P1-1 已由提交 `d965007`～`390ebdc` 完成

## 当前判断

MangaFlow 已经具备私有单用户漫画生产工作台的主要功能面：原作导入、结构化剧本、动态分页与分镜、人物/服装/风格资产、单页生成、人工采用、视觉检查、修复、多供应商模型平台、DAG 工作流和多种导出均有代码与自动测试。

当前不应再按“补齐 MVP 页面”推进，而应进入一次可靠性收口。页面生产门禁和旧候选复检已经合并，但主分支仍存在会影响付费任务、数据库升级和浏览器验收的问题，因此暂不把它定义为“可放心连续生产”的稳定版本。

本次主分支实测：

- `npm run check` 通过：145 个 Python 测试、9 个 Vitest 测试、Ruff、ESLint、TypeScript 和 Next.js 生产构建全部成功。
- Playwright E2E 共 4 项，2 项通过、2 项失败：导航/深链通过；首页请求预算和 Axe 色彩对比度失败。
- 未执行真实供应商或 Vertex 图片调用，避免产生费用；真实凭据、代理、当前模型可用性和付费响应仍需独立验收。
- PR #2 已把 `b6ebb97`、`3144e18`、`aca731a` 合并到 `master`，完成生产门禁、服装档案布局和旧候选复检；`699c190` 中的 Worker 租约、崩溃恢复和迁移校验仍未进入主分支。

## 2026-08-26 已合并修复

- 已建立统一的页面/章节 `production readiness`，并用于下一页、单页导出、整章导出和工作流输出。
- 生产通过要求候选仍被选中、素材存在、分镜版本已确认，并具备最新且通过的 `SPEAKER`、`CHARACTER`、`OUTFIT`、`PROP`、`CONTINUITY` 五类检查。
- 沿用旧候选会增加候选版本并强制产生新的检查幂等键，不能复用旧分镜的检查任务。
- 默认 DAG 以单页成品结束，整章导出使用独立流程并要求章节全部页面生产通过。

## 2026-08-27 完成项

- **P0-1 任务租约、崩溃恢复与旧 Worker 防覆盖：已完成**。提交 `d965007` 恢复遗留活动任务与过期租约并校验启动迁移版本；`2c17dc7` 修复 Worker token 与数据库并发竞态；`5427529` 为 PostgreSQL Worker 增加方言感知行锁；`33e4693` 在 claim 失败时执行锁内条件更新；`390ebdc` 为 RQ 区分可重试与终态失败。
- **P0-2 数据库启动以 Alembic 为准：已完成**。启动路径已移除 `create_all()`（仅留说明注释），启动时校验当前 revision 与仓库 head 一致。
- **P1-1 修复任务幂等插入竞态：已完成**。`worker_tasks.py` 以 `begin_nested()` savepoint 捕获 `IntegrityError` 后返回已存在任务，调用方事务不再被置为回滚。
- **P1-2 前端活动任务轮询：已完成（本次提交，待审查合并）**。新增共享模块 `apps/web/lib/task-status.ts` 统一活动/终态集合；工作台任务、候选、检查结果和资产生产面板的轮询全部接入，覆盖 `WAITING/QUEUED/PREPARING/UPLOADING_REFERENCES/GENERATING/OCR_CHECKING（历史兼容）/CONSISTENCY_CHECKING/REPAIRING/RUNNING`，检查与修复期间持续刷新、终态停止；新增单元测试与基于 TanStack Query 的轮询行为测试。

## P0：合并前必须完成

### P0-1 任务租约、崩溃恢复与旧 Worker 防覆盖 ✅ 2026-08-27 完成

问题：`GenerationJob` 已有 `lease_owner` / `lease_expires_at` 字段，但主分支执行器没有原子 claim、租约心跳和过期回收。API 或 Worker 在付费调用期间退出后，活动任务可能永久卡住；同一任务被重复执行时，旧 Worker 仍可能覆盖新 Worker 的结果或终态。

工作：

- 以测试为依据，把 `699c190` 中的租约实现重新基于当前 `master` 整合，不直接覆盖已经合并的生产门禁、分镜焦点和生成素材导入改动。
- 原子 claim 任务并递增 `attempt_count`，确保并发 Worker 只有一个获得执行权。
- 长调用期间独立 heartbeat；只允许当前 `lease_owner` 刷新和落库。
- 启动时回收无租约的遗留活动任务和租约过期任务；达到 `max_attempts` 后统一收敛为失败。
- 取消、重试、失败和完成都清空租约；旧 Worker 丢失租约后不得写回候选、节点或工作流状态。
- 增加 `status + lease_expires_at` 索引和 Alembic 迁移。

验收：

- 覆盖双 Worker 竞争、心跳、API 重启、Worker 崩溃、过期回收、取消竞态、旧 Worker 晚返回、最大尝试次数耗尽和工作流同步失败。
- 本地执行器与 Redis/RQ 使用相同状态语义。
- 任何恢复测试不得触发第二次真实付费调用。

### P0-2 数据库启动必须以 Alembic 为准 ✅ 2026-08-27 完成

问题：API 启动仍调用 `Base.metadata.create_all()`。它会掩盖未初始化数据库，却不会把已有数据库可靠地升级到当前迁移头，容易让“服务能启动”与“数据库结构正确”产生分歧。

工作与验收：

- 应用启动时比较当前 Alembic revision 与仓库 head；不一致时失败并给出明确升级命令。
- 移除生产/开发启动路径中的 `create_all()`；测试夹具继续显式创建隔离 schema。
- 全新数据库、旧数据库逐步升级、降级后再升级和缺失 revision 都有自动测试。

## P1：稳定版本应完成

### P1-1 修复任务幂等插入竞态 ✅ 2026-08-27 完成

当前 `create_job` 采用“先查询、再插入唯一键”。两个并发请求可能都查不到记录，后到者在 `flush()` 处抛出 `IntegrityError`，并把调用方同一事务中的其他待提交数据一起置为回滚状态。

- 使用 nested transaction/savepoint 捕获唯一键竞争，失败后返回已存在任务。
- 回归测试同时验证幂等只生成一个任务，并且调用方在竞争前创建的数据不会被回滚。

### P1-2 修复前端活动任务轮询 ✅ 2026-08-27 完成

主工作台的任务列表已经认识 `UPLOADING_REFERENCES`、`CONSISTENCY_CHECKING`、`REPAIRING` 等状态，但查询轮询只覆盖 `WAITING`、`QUEUED`、`PREPARING`、`GENERATING`、`RUNNING`。任务进入检查或修复阶段后，页面可能停止刷新，直到用户手动刷新才显示结果。

- 提取前端共享的活动状态集合，工作台、候选、检查结果和资产生产面板共用。
- 至少覆盖 `WAITING`、`QUEUED`、`PREPARING`、`UPLOADING_REFERENCES`、`GENERATING`、`OCR_CHECKING`（历史兼容）、`CONSISTENCY_CHECKING`、`REPAIRING`、`RUNNING`。
- 增加组件测试，证明检查/修复期间继续轮询，进入终态后停止轮询。

### P1-3 恢复浏览器验收基线 ✅ 2026-08-27 完成

本次 Playwright 失败证据：

- 首页预算要求不超过 3 个 API 请求，实测 4 个：`/projects/dashboard`、`/settings/vertex/status`、`/models`、`/providers`。
- Axe 在首页发现 1 类严重问题、4 个节点：`.empty-project > small` 对比度 3.75:1；生产闭环第 6/7 步编号 2.32:1；`.honesty-note` 4.49:1，均低于普通文字 4.5:1 要求。

修复与结果：

- `/projects/dashboard` 新增 `ai_overview` 摘要（可用模型数、健康连接数、已配置连接数），首页删除独立的 `/models`、`/providers` 首屏请求，首屏降至 dashboard + vertex status 共 2 个请求。
- 对比度修复覆盖 Axe 实测暴露的全部页面：浅色系灰字统一替换为 `#66604f`（最差背景 5.03:1）；工作流深色面板灰绿统一为 `#a4ada7` / `#b8c0ba`（6.33:1+）；状态徽标琥珀/朱红改为 `#8f6117` / `#b23c25`（5.13:1+）；章节导航序号移除 `opacity:.6` 叠加。
- Workflow 工作台补充可访问名称：3 个 `<select>` 增加 aria-label（选择工作流、运行范围类型、运行目标），节点库/属性面板关闭按钮与校验清除按钮增加 aria-label，设置页手动模型类型下拉增加 aria-label。
- 重跑全部 4 项 Playwright E2E：全部通过。

工作与验收：

- 将首页所需的模型/供应商摘要并入 Dashboard，或明确重构请求，恢复不超过 3 个首屏 API 请求的既定预算。
- 调整对应文字颜色或字号/字重，确保 Axe 的 WCAG 2 AA serious/critical 结果为零。
- 重跑全部 4 项 Playwright E2E；随后再跑 Lighthouse 和 100 节点工作流 FPS 门禁。

### P1-4 把浏览器门禁纳入可见的完整检查 ✅ 2026-08-27 完成

`npm run check` 当前只包含 lint、Python 测试、Vitest 和生产构建，不包含 Playwright、Axe、Lighthouse 或工作流 FPS；文档中的“完整质量门禁”不能继续与 `npm run check` 混为一谈。

- 保留快速 `npm run check`，新增不会调用真实模型的 `npm run check:full` 或 CI 工作流。
- `check:full` 至少运行 Playwright/Axe；Lighthouse 与 FPS 可按稳定环境单独设门禁，避免机器波动导致假失败。
- CI 产物保留失败截图/trace，但本地临时数据库、浏览器结果和测试项目在运行后自动清理。

### P1-5 消除序号分配竞态

多处用 `MAX(ordinal) + 1` 分配章节、批次、候选或工作流 revision，同时数据库又有唯一约束。并发双击、多个浏览器或工作流与手工操作并发时可能产生唯一键冲突。

- 对关键序号使用数据库级分配、受控锁，或捕获唯一键后有限重试。
- 优先覆盖 `GenerationBatch(project_id, ordinal)`、`PageCandidate(batch_id, ordinal)`、`SourceRevision(chapter_id, revision)`、`WorkflowVersion(workflow_id, revision)`。

### P1-6 安全整合剩余可靠性提交

- PR #2 已合并 `b6ebb97`、`3144e18`、`aca731a`；只重新基线化 `699c190` 中仍缺失的租约、幂等、迁移和轮询改动。
- 保留主分支已经合并的 production readiness、旧候选复检和 storyboard focus 修复，不通过一次大范围文件覆盖来“解决冲突”。
- 合并前逐项对应本清单的验收测试；合并后运行 `npm run check`、Playwright E2E、迁移测试和假模型完整 DAG。
- 删除或归档旧分支只能在主分支验证完成后进行。

## P2：可维护性与运营完善

### P2-1 拆分超大模块

当前主要热点：`project-workspace.tsx` 约 1491 行、`workflow-editor.tsx` 约 1018 行、`worker_tasks.py` 约 1606 行、`workflow.py` 约 1576 行、`workflow_engine.py` 约 1342 行。

- 前端按章节选择、资产绑定、生成工作台、质检修复、任务中心拆分组件与 hooks。
- 后端按任务类型拆分 handler，把 claim/lease/failure convergence 留在统一执行外壳。
- 每次只拆一个边界并补回归测试，不与 P0 状态语义修复混在同一提交。

### P2-2 补足前端行为测试

当前前端只有 9 个 Vitest 测试，覆盖面明显低于后端 145 个测试。

- 优先覆盖生产门禁提示、旧候选复检、检查/修复轮询、导出阻塞、任务取消/重试和供应商错误展示。
- 对错误、空数据、慢任务和并发 mutation 建立稳定的用户可见回归测试。

### P2-3 模型调用审计与成本可见性

`GenerationRecord` 能保存成功调用的 usage，但当前没有独立的 `ModelCallAttempt`；任务列表的 `estimated_cost` 仍返回 `None`。失败、重试、路由切换和每次尝试的成本不可完整追溯。

- 记录每次调用尝试的供应商、模型、request id、开始/结束时间、耗时、usage、错误码、是否重试和关联 job。
- 根据供应商定价配置提供“估算”而非伪精确成本，并在 UI 中区分估算值与账单值。
- 日志和导出继续禁止保存密钥、认证头和完整凭据路径。

### P2-4 上传与图片资源硬化

- 以 Pillow 实际识别格式为准规范化扩展名和 MIME，不信任客户端文件名与 `content_type` 的组合。
- 增加最大像素数、宽高边界和解压炸弹测试；20 MB 字节限制不能替代像素限制。
- 对缩略图失败、文件落盘成功但数据库提交失败等路径做孤儿文件清理测试。

### P2-5 备份与恢复演练

- 为 SQLite + `storage/generated` + `uploads` 提供一致性备份脚本和恢复检查清单。
- 至少演练一次新目录恢复、Alembic 升级、外键检查、资产文件存在性抽检和页面导出。

## 推荐执行顺序

1. ~~先在独立修复分支整合 P0-1～P0-2，补齐回归测试。~~（2026-08-27 完成）
2. ~~合并 P1-1、P1-2~~，收口任务/序号并发语义；**剩余 P1-5 序号分配竞态，覆盖范围待确认**。
3. ~~修复 P1-3，建立 P1-4 的完整浏览器门禁。~~（2026-08-27 完成；Lighthouse 与 FPS 门禁仍按稳定环境单独设门槛）
4. 主分支通过全部离线门禁后，再做一次用户授权的单候选真实供应商验收。
5. 最后按独立小提交推进 P2，避免可维护性重构干扰可靠性修复。

## 稳定版本完成定义

只有同时满足以下条件，才把当前私有 MVP 标记为稳定：

- P0 全部完成，且不存在已知的重复付费调用或旧 Worker 覆盖；已合并的生产门禁回归测试持续通过。
- `npm run check` 与全部 Playwright/Axe E2E 通过。
- Alembic head 校验、全新安装和旧库升级均通过。
- 假模型完整 DAG 从导入运行到人工采用、五类检查和输出节点，状态证据一致。
- 在用户明确授权后，真实供应商只生成一个 1K 候选，确认轮询、采用、检查和导出闭环；不自动生成第二页，不自动修复。
