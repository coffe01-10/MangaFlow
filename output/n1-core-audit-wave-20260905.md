# N1 Core Reliability Audit Wave — 2026-09-05（night/n1-core，基线 c5179b7 → 合并后 HEAD）

最终全量：**730 passed**（基线 629，+101 个新回归测试），失败集与 Windows 环境基线逐字节一致；ruff 干净。

范围：Job / Queue / Cancellation / Retry / Workflow / Director / Persistence / Idempotency / Candidate / Inspection / Generation Integrity。
方法：8 组 fresh-context Hunter（A–H）→ 独立 Verifier（P1 双验证）→ Fix Worker（独立 worktree/branch、文件互斥、failing-first 回归测试）→ Critic/Fresh Reviewer 终审。
本环境边界：Linux 离线 SQLite 套件；live PostgreSQL/Redis/RQ/真实 provider 验收 **NOT RUN**（无基础设施，按仓库政策标记 BLOCKED）。基线 8 failed + 10 errors 全部为 Windows 专属环境失败（backup_restore_drill / codex_cli / grok_build_cli / e2e_isolation / antigravity_cli），全天保持不变。

## 终审

Fresh Reviewer（最终对抗审查，覆盖 c5179b7..HEAD 全部合并 diff + 交叉修复交互 + 全量套件）：**SHIP，无阻塞项**。全量 707 passed（基线 629），失败集与基线逐字节一致；ruff 干净；每个 job 终态均有已验证的人工出口（cancel → delete → 重建），无三重死锁态。

## 已确认并修复合入（按缺陷计 24 项）

### P1
1. **同一候选并发双份 PAGE_INSPECT（双倍付费）**——create_job 将 FAILED/CANCELLED job 幂等键改写为 `closed:{id}` 后重试不恢复键，且 inspect 路由无 has_active_job 防护；workflow 键空间（`workflow:{run}:{node}:1`）与路由键空间互不可见。修复：路由 409 防护 + reconcile 采用活跃 job + 成功路径经 node 链回溯触发 reconcile（否则采用 job 完成后 run 永久 RUNNING）。双验证确认。
2. **repair 预算绕过 + 并发双份付费修复**——max_auto_repairs 预算读为锁前普通 SELECT，幂等键按次新建永不去重。修复：预算读移入 create_generation_batch 锁窗口 + 经 CandidateLineage 回溯父候选的同类修复/同分辨率放大 409 防护。双验证确认（并纠正：job target 是新子候选，直接 has_active_job 无效）。
3. **审批门被恢复扫描确定性摧毁**——默认图 output.page 的 planning job 依赖为空（唯一上游 inspect 无 planning job），`dependencies_complete` 空真；队列启用（发行默认）下恢复扫描（≥30s 周期）在任意审批暂停期间入队执行它 → PAGE_NOT_PRODUCTION_READY 烧完重试 → run 从 PAUSED 翻 FAILED 且 retry_run 重建后再次落入同一陷阱。双验证确认，且比初报更严重：触发不需要暂停，run 创建约 1 分钟后即被杀死。修复：恢复第二阶段跳过 node_run 仍为 WAITING / run 为 PAUSED 的 workflow job，改为触发 reconcile_run（保留崩溃窗口自愈，reconcile 自身父门拒绝受控节点）。Lead 补充实现修复轮 + failing-first T5。
4. **director undo/redo 为 check-then-write**——无 claim 无锁；并发 undo vs accept 静默丢失已提交写入（layout undo 还会 DELETE+重建全部面板），undo 与 redo 同行竞态数据不确定；单标签页可达（撤销按钮仅在 undo.isPending 时禁用）。修复：accept 同款锁序（page→panel）+ 条件 claim（EXECUTED + sbv 匹配 → SUPERSEDED，同一事务完成恢复），丢失 claim 回滚。双验证确认（D-F2“undo 链单发”同期被两验证者一致判定为合同设计的 tip-undo 模型，非缺陷；残余 P3 为前端对非 tip 组暴露必 409 按钮）。

### HIGH/MEDIUM（P2）
5. reset_for_retry check-then-write 可击穿活跃 worker lease / 复活已取消 job → 单条条件 UPDATE claim（双验证：机制成立，窗口毫秒级，定级 P2）。
6. reconcile_run 终态写覆盖并发 CANCELLED（锁等待把窗口拉宽到 cancel 事务尾部，且永久不可自愈）→ 条件 claim + lost-claim 回滚，node 级写 flush 后原子提交。
7. discard_group 无 claim → 并发 accept 后 journal 记 DISCARDED 而效果已应用且 undo 永久 409 → 逐行条件 claim（与 accept/reject 同模式）。
8. 软删资产 sha256 去重把死资产/错类资产系到新付费候选（UNIQUE(project_id,sha256) 下新行不可行；字节相同即触发；选中后 export 409）→ 过滤 deleted_at + source + kind，命中软删同类行时原地复活（镜像 upload_asset 先例），跨类碰撞 raise。
9. 页面卡死 FINAL_CHECKING——PAGE_INSPECT job 无候选行，worker 失败/取消/恢复路径都找不到页面 → 新增 restore_page_after_inspection_exit 并接入 _mark_worker_failure、mark_job_cancelled；后续测试审计再发现恢复终态分支同样缺失（T-G1）→ 补齐。
10. Windows horse 无法 import app（官方 `npm run dev:worker` 路径确定性复现：`python -c` horse cwd=repo 根，rq `--path` 仅改父进程）→ horse_environment 注入 PYTHONPATH；QUEUED 行无任何恢复路径的问题随之大幅收窄。
11. RQ 超时硬杀无任何应用级清理、重试在未过期 lease 内空转、错误面只报 LEASE_EXPIRED、NULL outcome 的 ModelCallAttempt 永不收口 → 监控 kill 前落 JOB_TIMEOUT 标记；恢复分支透传超时原因并在终态收口 NULL attempt。
12. STYLE_ANALYZE 双份付费（version 进幂等键 + 无 active-job 防护，inspect P1 的同族）→ 两个风格路由 409 防护 + 失败/取消/恢复的风格重置全部加兄弟 job 存在性守卫。
13. POST /jobs/{id}/retry 对不可重试状态静默 200 → 409。
14. 吞掉的 reconcile 异常：完成路径异常零日志（run 卡 RUNNING，无自愈）；恢复循环一个坏 run 饿死整轮且启动调用无守卫（可致 API 无法启动）→ 完成路径独立 try/except + 日志；恢复循环逐项隔离 + 回滚；启动调用加守卫。
15. run 终态 FAILED 时滞留的下游 WAITING 子 job/节点永久僵尸并阻塞整项目脚本删除 → FAILED claim 成功后同事务清扫（镜像 cancel_run，含 late-jobs），FAILED 原因保留；重试依赖未完成 → 409（防御）。
16. delete_candidate 不取消活跃 job → worker 全额付费后把软删行复活为 READY；worker 侧无任何 target deleted_at 检查 → 路由侧取消活跃 job + worker 侧 claim 前与持久化前双重检查（付费后中止则回滚附加、用量行保持诚实）。
17. asset_generate 缺 page_generate 的 lease 后参考图复检（静默使用用户已删参考）→ 镜像补齐。
18. delete_asset vs select_candidate 无锁 TOCTOU 产生悬挂选中（readiness 阻断但工作台渲染 404 图）→ 双侧按约定 lock_entity(MangaPage) + 锁后复检（delete 侧）+ select 校验资产存活。
19. favorite 对 AssetCandidate 提交后 500（schema 必填字段缺失）→ 按类型分支返回 asset_candidate_read。
20. undo 接受复合写只校验 scene.version（panel.background 被静默覆盖）→ accept 时对含 background 的场景命令加面板一致性 409（复合检查；单纯 sbv 不够，因手动场景 PATCH 不动 sbv）。
21. alembic fileConfig 默认 disable_existing_loggers 杀掉进程内全部既存 logger（离线套件测试污染的根因）→ disable_existing_loggers=False。
22. 删除漂移死代码 mark_job_failed（无生产调用方、缺兄弟守卫、缺页面恢复、无 claim——未来接线即重引缺陷），其测试断言迁移到活跃路径。

## 验证后否决（不修）
- **D-F2“undo 链单发”**：两独立验证者一致——sbv 锚定的 tip-undo + SUPERSEDED 是合同 §5.3 明文设计；undo/redo 链可无限切换，仅非 tip 行不可直接撤销（fail-closed）。残余 P3：前端 historyActionIds 对非 tip 组渲染必 409 的撤销按钮。
- **T-G3 幂等键重挂并发 500**：同值 UPDATE 在行锁下串行且无唯一冲突；真正抛错的是 loser 的 INSERT，恰在 begin_nested + IntegrityError 回退内，依赖/引用行严格在其后添加，无孤儿行。
- **W-1 run_worker 路径计算错误**：HEAD 上 `parents[1]` 数学正确（指向 apps/api，chdir 到 repo 根），REFUTED。
- **E-F5 页面回退 STORYBOARDED**：机制存在但终态良性等价（选择/重掷/就绪全部正常），降级 LOW，暂不修。

## 遗留队列（已定位、未修复，按价值排序）
1. ~~**C-F2/G-F2（P2 家族）**：9 处实体 PATCH 路由仍是"内存读版本→比较→无条件写"~~ → **已修复合并**：全部十处（scene/beat/character/outfit/style×3/project/workflow×2，含 restore_version）改为单条条件 UPDATE 原子 claim（`WHERE version == expected`，rowcount 0 → 409），10 个逐路由 lost-update 回归测试全部 failing-first 验证。
2. ~~**C-F1（P2）**：workflow export 节点重执行时 create_export 非幂等~~ → **已修复合并**：`create_export(reuse_existing=True)` 仅在 export 节点处理器启用，以确定性 storage_key（选中候选集的纯函数）复用已提交 bundle 行；该行本身即幂等标记，可跨 create_export 提交与完成 CAS 之间的崩溃存活。残余（诚实记录）：真正并发的双执行仍可插入两行，彻底关闭需 `(chapter_id, export_type, storage_key)` 唯一约束（迁移，超本轮范围）。
3. ~~**C-F3（P2）**：POST /workflows/{id}/runs 无幂等防护~~ → **已修复合并**：create_workflow_run 在 scope 校验后 lock_entity(WorkflowDefinition) + 同 workflow+scope 存在非终态 run 则 ValueError → 路由 409"该范围已有进行中的运行"。终态（FAILED/CANCELLED/COMPLETED）不阻塞 retry_run 与新起；不同 scope 并行不受影响。请求侧幂等令牌（需迁移+客户端配合）评估为不必要。
4. ~~**F-F4（P2→P3）**：多图响应仅持久化 images[0] 但按 N 计费~~ → **已修复合并（降级 P3，发行预设 n=1 不可达）**：`"n"` 加入 _RESERVED_BODY（extra_body 无法再抬高张数）、images_edit 表单钉 `n=1`、Google 分支 `candidate_count=1`；两个 worker handler 对 N>1 响应打 WARNING。刻意不改账本——供应商确按 N 张计费，N 是诚实的支出记录；缺陷根因是"花钱不用"，已在请求源头钉死。
5. **R-7（P2/P3）**：/jobs/{id}/* 全家无项目归属校验；archived job 可重试；列表硬上限 100 无分页。（API 本就无鉴权，属一致性缺口）
6. **R-8（P2）**：LOCAL 模式无墙钟超时（job_timeout 仅挂在 RQ）；CONCURRENCY_LIMIT 等待者占死 8 线程池。
7. **S-P3（P2）**：request_parameters / input_snapshot JSON 整列写与并发写者互相覆盖（_lease_reference_assets vs reconcile 参数合并）。
8. **P3 前端提示缺口**（Fresh Reviewer 确认）：analyzeStyle 与 job retry/cancel 的 409 无错误呈现；director 非 tip 撤销按钮恒 409。
9. **测试强化**：U-M2 双 approve 测试在 claim 回归时仍会通过；多连接真实并发（PG）覆盖整体缺失（NOT RUN 边界）。

## NOT RUN / BLOCKED 汇总
- tests/integration/test_postgres_acceptance.py、test_redis_rq_acceptance.py（需 live PG/Redis，本环境无）；
- Windows SpawnWorker 真实 TerminateProcess kill 路径、真实 CLI/PowerShell 套件（平台不可达，基线失败集即其体现）；
- Playwright e2e / npm run check（本轮为后端状态机范围）；
- 所有 lock_entity FOR UPDATE 的真实 PostgreSQL 锁语义（离线仅 populate_existing 路径）。
