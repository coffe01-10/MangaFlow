# Phase 2: PostgreSQL & Redis/RQ 隔离集成验收报告

**Issue**: [#12](https://github.com/coffe01-10/MangaFlow/issues/12)  
**工作区**: `D:/自媒体/漫画工作流-agy`  
**分支**: `codex/phase2-database-queue-acceptance`  
**基线 Commit**: `b7d89c0a0e8b7e80abb7293b561d9213bc79ee3d`  
**执行状态**: **`BLOCKED (Environment Missing: Docker/PostgreSQL/Redis not available on local host)`**  
**报告日期**: 2026-08-28  

---

## 1. 环境定位与诊断结果

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
> 严格遵循仓库安全准则：**严禁擅自安装系统服务、下载第三方二进制服务器或连接外部非受信数据库，严禁使用 SQLite/fakeredis 假装为真实验收**。因此，本轮交付将真实外部服务的实测状态标定为 `BLOCKED`，重点交付完整的隔离测试套件、独立编排配置及自动化复跑入口。

---

## 2. 交付文件与架构设计

为确保在后续具备 Docker/服务环境时可一键复跑真实测试，本轮建立了严格隔离的验收基础设施：

1. **独立编排文件 [`docker-compose.acceptance.yml`](file:///D:/自媒体/漫画工作流-agy/docker-compose.acceptance.yml)**:
   - 项目名: `mangaflow-acceptance`（与默认开发项目完全隔离）。
   - PostgreSQL 容器: 监听 `127.0.0.1:55432`，临时库 `mangaflow_acceptance`，独立测试卷 `mangaflow-acceptance-postgres-data`。
   - Redis 容器: 监听 `127.0.0.1:56379`，启用独立密码认证，独立测试卷 `mangaflow-acceptance-redis-data`。
2. **隔离运行脚本 [`scripts/run-phase2-acceptance.ps1`](file:///D:/自媒体/漫画工作流-agy/scripts/run-phase2-acceptance.ps1)**:
   - 自动执行环境自检与端口探测。
   - 缺少服务时安全降级执行 Harness 校验并输出明确阻塞原因；检测到服务时自动执行 `--run-live-integration` 全量集成测试。
3. **集成安全与隔离夹具 [`tests/integration/conftest.py`](file:///D:/自媒体/漫画工作流-agy/tests/integration/conftest.py)**:
   - 安全门禁：严格强制必须为回环地址（`127.0.0.1` / `localhost`），严禁任何外部 IP 或生产端口。
   - 缓存隔离：Redis 强制限定在非零专用 DB（如 `DB 15`），仅允许 `flushdb`，严禁跨库 `flushall`。
4. **PostgreSQL 真实并发与事务测试 [`tests/integration/test_postgres_acceptance.py`](file:///D:/自媒体/漫画工作流-agy/tests/integration/test_postgres_acceptance.py)**:
   - 验证 PostgreSQL 方言下的 `SELECT ... FOR UPDATE` 行锁。
   - 验证多连接并发生成批次序号（`GenerationBatch.ordinal`）与候选序号（`PageCandidate.ordinal`）单调唯一无碰撞。
   - 验证工作流发布（`WorkflowVersion`）并发竞态与 409 防护。
   - 验证 PostgreSQL 事务完整原子回滚与 0 孤立记录。
5. **Redis/RQ 真实队列与 Worker 测试 [`tests/integration/test_redis_rq_acceptance.py`](file:///D:/自媒体/漫画工作流-agy/tests/integration/test_redis_rq_acceptance.py)**:
   - 真实 Redis 队列入队与 Worker 真实出队执行。
   - 运行中任务状态隔离保护（`QUEUED` 不覆盖 `GENERATING` 等运行态）。
   - 任务租约超时后的自动抢占恢复（`recover_pending_jobs`）。
   - 任务取消（`JobCancelledError`）的安全终止与 0 脏数据残留。
   - 使用确定性本地 Fake 适配器，0 付费/外部供应商调用。
6. **Harness 安全验证单元测试 [`tests/test_integration_harness.py`](file:///D:/自媒体/漫画工作流-agy/tests/test_integration_harness.py)**:
   - 验证安全 URL 白名单、远程 IP 拦截、Redis DB 0 防护等。

---

## 3. 验收矩阵（实测与阻塞状态）

| 模块 / 编号 | 验收项与测试用例 | 预期行为 | 本地实测结果 | 阻塞/前置条件 |
| --- | --- | --- | --- | --- |
| **P1-5** | `test_pg_concurrent_generation_batch_allocation` | 多连接并发分配抽卡批次序号，严格无重复、无间隙泄漏 | `SKIPPED (Blocked)` | 需要 Docker / PostgreSQL (127.0.0.1:55432) |
| **P1-5** | `test_pg_concurrent_page_candidate_allocation` | 多连接并发分配页面候选序号，严格单调递增 | `SKIPPED (Blocked)` | 需要 Docker / PostgreSQL (127.0.0.1:55432) |
| **P1-7** | `test_pg_workflow_version_release_concurrency` | PostgreSQL 真实事务行锁竞争，单方成功推进 V2，另一方受控 409 | `SKIPPED (Blocked)` | 需要 Docker / PostgreSQL (127.0.0.1:55432) |
| **P1-10** | `test_pg_transaction_rollback_and_zero_residual_entities` | 下游任务失败时，PostgreSQL 外层事务完整回滚，0 脏批次/候选 | `SKIPPED (Blocked)` | 需要 Docker / PostgreSQL (127.0.0.1:55432) |
| **P1-11** | `test_redis_connection_and_namespace_isolation` | 真实 Redis 连接可达，限定在 DB 15 命名空间 | `SKIPPED (Blocked)` | 需要 Docker / Redis (127.0.0.1:56379) |
| **P1-12** | `test_redis_rq_enqueue_and_worker_execution` | 真实入队，Worker 真实出队执行并更新数据库状态为 COMPLETED | `SKIPPED (Blocked)` | 需要 Docker / Redis (127.0.0.1:56379) |
| **P1-12** | `test_redis_rq_state_isolation_no_clobber` | 正在执行的 Job 再次调用入队时不被覆盖或重置状态 | `SKIPPED (Blocked)` | 需要 Docker / Redis (127.0.0.1:56379) |
| **P2-8** | `test_redis_rq_lease_expiration_and_recovery` | 租约超时的孤儿 Job 被 `recover_pending_jobs` 自动重新入队调度 | `SKIPPED (Blocked)` | 需要 Docker / Redis (127.0.0.1:56379) |
| **P2-8** | `test_redis_rq_cancellation_protection` | 已取消的任务在 Worker 中抛出 `JobCancelledError` 安全中止，0 外部调用 | `SKIPPED (Blocked)` | 需要 Docker / Redis (127.0.0.1:56379) |
| **SEC** | `test_safe_acceptance_pg_url_allows_loopback` | 允许 127.0.0.1 / localhost 安全回环 | **PASSED** (0.01s) | 无（离线通过） |
| **SEC** | `test_safe_acceptance_pg_url_blocks_remote_hosts` | 拦截远程 IP / 生产主机连接 | **PASSED** (0.01s) | 无（离线通过） |
| **SEC** | `test_safe_acceptance_redis_url_blocks_default_db_zero` | 拦截 Redis DB 0，强制隔离 DB | **PASSED** (0.01s) | 无（离线通过） |

---

## 4. 可复跑执行指南

当宿主机安装 Docker 或启动容器服务后，可通过以下步骤一键复跑真实验收：

```powershell
# 1. 启动隔离验收容器（自动绑定 55432 / 56379）
docker compose -f docker-compose.acceptance.yml up -d

# 2. 运行自动化验收脚本
powershell -ExecutionPolicy Bypass -File .\scripts\run-phase2-acceptance.ps1

# 3. 运行完毕后清理隔离容器
docker compose -f docker-compose.acceptance.yml down
```