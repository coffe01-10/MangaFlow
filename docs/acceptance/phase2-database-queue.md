# Phase 2: PostgreSQL & Redis/RQ 隔离集成验收报告

**Issue**: [#12](https://github.com/coffe01-10/MangaFlow/issues/12)
**工作区**: `D:/自媒体/漫画工作流-agy`
**分支**: `codex/phase2-database-queue-acceptance`
**基线 Commit**: `b7d89c0a0e8b7e80abb7293b561d9213bc79ee3d`
**执行状态**: **`BLOCKED (Environment Missing: Docker/PostgreSQL/Redis not available on local host)`**
**报告日期**: 2026-08-28

---

## 1. 环境只读探测结果与状态定级

在开发环境中进行了只读探测，确认无活跃的 Docker 守护进程、WSL 发行版或独立数据库/缓存服务：

| 探测项目 | 检测命令 / 方式 | 检测结果 | 说明 |
| --- | --- | --- | --- |
| **Docker** | `Get-Command docker` | `Not Found` | 未安装 Docker CLI / Docker Desktop |
| **Docker Compose** | `Get-Command docker-compose` | `Not Found` | 未安装 Compose |
| **Podman** | `Get-Command podman` | `Not Found` | 未安装 Podman |
| **WSL 状态** | `wsl --list` | `No installed distributions` | 仅存在 Windows 包装器，无可用 Linux 发行版；按安全规则严禁擅自执行安装 |
| **PostgreSQL 端口** | `Get-NetTCPConnection -LocalPort 5432, 55432` | `Closed / Not Listening` | 5432（默认）与 55432（验收专用）均未监听 |
| **Redis 端口** | `Get-NetTCPConnection -LocalPort 6379, 56379` | `Closed / Not Listening` | 6379（默认）与 56379（验收专用）均未监听 |
| **Python 基础包** | `.venv/Scripts/python.exe -m pip list` | `SQLAlchemy 2.0.51, alembic 1.18.5, redis 6.4.0, rq 2.6.1` | 具备 ORM、迁移及 Redis/RQ 客户端库 |

> [!IMPORTANT]
> 严格遵循安全准则：**严禁擅自安装系统服务、下载第三方二进制服务器或连接外部非受信数据库，严禁使用 SQLite/fakeredis 假装为真实验收**。因此，本轮交付将真实外部服务的实测状态标定为 `BLOCKED`，重点交付完整的隔离测试套件、独立编排配置及自动化复跑入口。

---

## 2. 隔离基础设施与安全防御设计

为确保在后续具备 Docker/服务环境时可一键复跑真实测试并保障 100% 数据安全，本轮建立了严格隔离的验收基础设施：

1. **独立编排文件 (`docker-compose.acceptance.yml`)**:
   - 项目名: `mangaflow-acceptance`（与默认开发项目完全隔离，不继承 `.env`）。
   - PostgreSQL: 监听 `127.0.0.1:55432`，临时库 `mangaflow_acceptance`，独立命名卷 `mangaflow-acceptance-postgres-vol`。
   - Redis: 监听 `127.0.0.1:56379`，启用独立密码认证，独立命名卷 `mangaflow-acceptance-redis-vol`。
2. **可复跑脚本 (`scripts/run-phase2-acceptance.ps1`)**:
   - 支持 `-DryRun`（纯只读格式验证，零写入零启动副作用）。
   - 显式 `-RunLive` 开关：未指定时执行 Harness 校验并输出明确 `BLOCKED` 状态；指定时执行全量集成测试，遇到服务故障时以非零退出码失败（绝不静默吞成假成功）。
   - 容器生命周期使用 `try ... finally`，只管理本次脚本显式启动的容器。
3. **安全与隔离夹具 (`tests/integration/conftest.py`)**:
   - **URL 白名单与 Query 覆盖拦截**：强制 PostgreSQL 端口必须为 `55432` 且数据库名必须为 `mangaflow_acceptance`；强制 Redis 端口必须为 `56379` 且 DB 索引必须在 `1..15`（严禁 `DB 0`）；严格拦截 query 参数中的 `host`/`port`/`hostaddr`/`db` 等覆盖劫持。
   - **凭据脱敏**：`mask_url` 对日志与异常文本中的连接 URL 密码进行脱敏。
   - **PostgreSQL 随机 Schema 级隔离**：每次测试动态创建 `acceptance_{uuid}` 专属 schema 并在该 schema 下建表，测试结束后执行 `DROP SCHEMA ... CASCADE`，严禁无脑 `drop_all` 作用于 public 库。
   - **Redis 追踪清理**：通过资源追踪器记录本次测试创建的 queue 与 job key，清理时精准删除，严禁无差别 `flushdb`/`flushall`。
4. **真实 PostgreSQL 验收测试 (`tests/integration/test_postgres_acceptance.py`)**:
   - 验证 PostgreSQL 方言下的 `SELECT ... FOR UPDATE` 行级行锁。
   - 验证多连接并发分配批次序号（`create_generation_batch`）与候选序号（`create_page_candidate`）单调唯一无碰撞，并在新 Session 中验证真实持久化。
   - 验证工作流发布（`publish_workflow`）并发竞态与 409 防护。
   - 验证下游失败时，`approve_node` 事务完整原子回滚与 0 孤立脏数据残留，后续重试正常恢复。
   - 验证关闭批次后候选创建 409 拒绝与 0 脏数据落库。
5. **真实 Redis / RQ 验收测试 (`tests/integration/test_redis_rq_acceptance.py`)**:
   - 真实 Redis 队列入队与 Worker 真实出队执行。
   - 运行中任务状态隔离保护（`QUEUED` 不覆盖 `GENERATING` 等运行态）。
   - 租约超时后的自动抢占恢复（`recover_pending_jobs`）。
   - 项目并发限额（`default_concurrency`）下的超限调度与挂起。
   - 可重试与终态失败状态流转。
   - 任务取消安全终止与 0 孤立残留。
   - 使用确定性本地 Fake 适配器 (`app/model_adapters/fake_acceptance.py`)，0 付费/外部供应商调用。
6. **Harness 单元测试 (`tests/test_integration_harness.py`)**:
   - 离线验证安全白名单过滤、远程 IP 拦截、Redis DB 0 防护规则与凭据脱敏。

---

## 3. 验收矩阵（覆盖状态与阻塞明细）

| 模块 / Issue 编号 | 验收项与测试用例 | 预期行为 | 实现状态 | 本地离线检查 | 实测状态与阻塞原因 |
| --- | --- | --- | --- | --- | --- |
| **P1-5** | `test_pg_concurrent_generation_batch_allocation` | 多连接并发分配抽卡批次序号，严格无重复、无间隙泄漏，新 Session 验证持久化 | Implemented | Validated | **BLOCKED**（缺 Docker/PostgreSQL 55432） |
| **P1-5** | `test_pg_concurrent_page_candidate_allocation` | 多连接并发分配页面候选序号，严格单调递增并 commit 落库 | Implemented | Validated | **BLOCKED**（缺 Docker/PostgreSQL 55432） |
| **P1-7** | `test_pg_workflow_version_release_concurrency` | PostgreSQL 真实事务行锁竞争，单方成功推进 V1，另一方受控 409 | Implemented | Validated | **BLOCKED**（缺 Docker/PostgreSQL 55432） |
| **P1-10** | `test_pg_transaction_rollback_and_zero_residual_entities` | 下游任务失败时，approve_node 事务完整回滚，0 脏批次/候选/Job | Implemented | Validated | **BLOCKED**（缺 Docker/PostgreSQL 55432） |
| **P1-10** | `test_pg_candidate_creation_blocked_when_batch_closed` | 批次关闭后候选创建返回 409，0 脏数据落库 | Implemented | Validated | **BLOCKED**（缺 Docker/PostgreSQL 55432） |
| **P1-11** | `test_redis_connection_and_namespace_isolation` | 真实 Redis 连接可达，限定在 DB 1..15 专属命名空间前缀 | Implemented | Validated | **BLOCKED**（缺 Docker/Redis 56379） |
| **P1-12** | `test_redis_rq_enqueue_and_worker_execution` | 真实入队，Worker 真实出队执行并更新数据库状态为 COMPLETED | Implemented | Validated | **BLOCKED**（缺 Docker/Redis 56379） |
| **P1-12** | `test_redis_rq_state_isolation_no_clobber` | 正在执行的 Job 再次调用入队时不被覆盖或重置状态 | Implemented | Validated | **BLOCKED**（缺 Docker/Redis 56379） |
| **P1-12** | `test_redis_rq_concurrency_quota_and_deferred_execution` | 项目并发满载时多余任务排队挂起，不超限执行 | Implemented | Validated | **BLOCKED**（缺 Docker/Redis 56379） |
| **P1-12** | `test_redis_rq_retryable_and_terminal_failures` | 可重试错误重新排队，不可重试错误直接标记 FAILED | Implemented | Validated | **BLOCKED**（缺 Docker/Redis 56379） |
| **P2-8** | `test_redis_rq_lease_expiration_and_recovery` | 租约超时的孤儿 Job 被 `recover_pending_jobs` 自动重新入队调度 | Implemented | Validated | **BLOCKED**（缺 Docker/Redis 56379） |
| **P2-8** | `test_redis_rq_cancellation_protection` | 已取消的任务在 Worker 中安全终止，0 外部调用，0 脏输出 | Implemented | Validated | **BLOCKED**（缺 Docker/Redis 56379） |
| **SEC** | `test_mask_url_hides_sensitive_credentials` | 隐藏 URL 密码，防止日志泄露 | Implemented | **PASSED** (0.01s) | 无（离线已验证） |
| **SEC** | `test_safe_acceptance_pg_url_allows_valid_acceptance_endpoints` | 允许 127.0.0.1:55432/mangaflow_acceptance | Implemented | **PASSED** (0.01s) | 无（离线已验证） |
| **SEC** | `test_safe_acceptance_pg_url_blocks_standard_and_remote_and_invalid_dbs_and_queries` | 拦截 5432、非 acceptance 库、远程 IP 与 query 覆盖 | Implemented | **PASSED** (0.01s) | 无（离线已验证） |
| **SEC** | `test_safe_acceptance_redis_url_allows_valid_isolated_endpoints` | 允许 127.0.0.1:56379 DB 1..15 | Implemented | **PASSED** (0.01s) | 无（离线已验证） |
| **SEC** | `test_safe_acceptance_redis_url_blocks_standard_ports_and_db_zero_and_queries` | 拦截 6379、DB 0 (/0, /00, ?db=0) 与所有 query 覆盖 | Implemented | **PASSED** (0.01s) | 无（离线已验证） |

---

## 4. 可复跑执行指南

当宿主机安装 Docker 或启动隔离容器服务后，可通过以下步骤一键复跑真实验收：

```powershell
# 1. 纯只读环境检测（零副作用）
powershell -ExecutionPolicy Bypass -File .\scripts\run-phase2-acceptance.ps1 -DryRun

# 2. 启动隔离验收容器并执行真实全量集成验收（测试结束后自动清理容器）
powershell -ExecutionPolicy Bypass -File .\scripts\run-phase2-acceptance.ps1 -RunLive -StartContainers -StopContainers
```