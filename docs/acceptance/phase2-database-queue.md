# Phase 2: PostgreSQL 与 Redis/RQ 验收状态

- Issue: [#12](https://github.com/coffe01-10/MangaFlow/issues/12)
- PR: [#14](https://github.com/coffe01-10/MangaFlow/pull/14)
- 原始派工基线: `b7d89c0a0e8b7e80abb7293b561d9213bc79ee3d`
- 已整合最新 master: `40d861753ff7f96ffeb866bc27ff15ef4030680b`（PR #15 合并后）
- 2026-08-28：成员第二轮返工提交 `20f2419567131375a910d55825ecbd126709b8d3` 仍有阻塞，组长接手修复。
- **代码状态：修复中，未批准合并。真实 PostgreSQL / Redis / RQ 验收：NOT RUN / 环境 BLOCKED。**

本文件取代此前“完整套件”“全部 Validated”“100% 数据安全”等无充分证据的表述。
离线 URL 校验通过不能证明资源清理、进程执行或真实服务集成正确。

## 2026-08-29 合并前独立复验（本节为准）

- 分支已普通合入最新 `master@40d8617`，无冲突、无强推。
- `npm run check`：ESLint/Ruff、**345 Python passed / 23 live skipped / 23 warnings**、**27 Vitest**、TypeScript 与 Next.js production build 全部通过。
- `tests/test_integration_harness.py`：**64 passed**；本机 RQ 2.6.1 的 `Execution.fetch`、`Worker.find_by_key`、监控接口与 `WindowsSpawnWorker` 实现签名一致。
- 默认 `tests/integration`：**23 skipped**，均为明确 NOT RUN；没有连接 PostgreSQL/Redis/RQ。
- 环境复查：Docker/Podman/psql/pg_isready/redis-server/redis-cli 均不存在，WSL 无发行版；55432/56379 无监听。
- 删除了固定容器名、固定卷与默认密码的 `docker-compose.acceptance.yml`。它不满足资源归属标准且 runner 不会使用，保留只会诱导误运行。后续如需自动编排，应另行实现随机项目名/凭据/标签、端口归属验证和仅清理本次资源。

**结论：离线代码审阅与回归通过；真实 PostgreSQL/Redis/RQ 仍 NOT RUN。由于 PR 改动 Windows 生产 Worker，真实服务跑通前不批准合并、不关闭 Issue #12。**

## 当前可用入口

`scripts/run-phase2-acceptance.ps1` 默认只运行离线 harness。
可用 `-Python` 指定已安装的解释器，不安装或修改依赖。

- URL 校验在独立模块中进行，不导入应用或加载 dotenv；禁止 query/fragment、远程主机、
  默认端口、模糊 Redis DB 与不支持的 PostgreSQL 驱动方案。
- `-DryRun` 仅做已提供 URL 的语法校验，不检查服务可达性或资源归属。
  缺 URL 明确输出 NOT_CONFIGURED；没有启动、停止或连接服务。
- 默认运行会清除继承的 live 开关和 pytest 参数，禁用 dotenv，使用临时离线配置。
- **Live 门禁（2026-08-28 组长最终更新）**：pytest 层 live opt-in 已恢复为正常开关——
  未 opt-in 时全部 live 测试以 NOT RUN 明确 skip；显式
  `pytest tests/integration --run-live-integration --pg-url <隔离URL> --redis-url <隔离URL>`
  时先做 URL/归属校验，服务不可用即快速响亮失败（脱敏），不以 skip 计通过。
  `-RunLive`/`-StartContainers`/`-StopContainers` 在 runner 层仍为非零 BLOCKED
  （owner 作用域容器编排未实现），错误信息已指向上述 direct pytest 路径。
- 不安全的旧 Compose 已从本 PR 删除；用户未授权安装/下载 PostgreSQL/Redis/Docker/WSL，真实服务验收保持 BLOCKED。

```powershell
# 离线入口；仅在已有解释器不是本 worktree .venv 时传 -Python
powershell -ExecutionPolicy Bypass -File .\scripts\run-phase2-acceptance.ps1 -Python D:/自媒体/漫画工作流/.venv/Scripts/python.exe

# 仅格式校验，不代表真实验收
powershell -ExecutionPolicy Bypass -File .\scripts\run-phase2-acceptance.ps1 -DryRun -Python D:/自媒体/漫画工作流/.venv/Scripts/python.exe
```

## 尚未完成的验收工作

| 范围 | 缺口 / 下一步 | 状态 |
| --- | --- | --- |
| PostgreSQL 隔离 | 已补归属、失败清理、邻接 sentinel 与 Alembic 入口；真实行为待验证 | 修复中；真实环境未运行 |
| PostgreSQL 并发 | 正常并发发布应允许序列化成功；另以确定冲突验证耗尽 409、恢复、持久化 | 断言已修复；尚未真实 PostgreSQL 验收 |
| 原子性与门禁 | 真实 approve_node 最终 commit 失败与恢复；有效准备数据下的关闭/过期批次 | 覆盖不足；未验收 |
| Redis 清理 | RQ queue/job/registry/result/execution/worker 等资源与全局集合成员的精准归属清理 | 覆盖不足；未验收 |
| RQ 进程 | **已实现**：仓库级 `app/rq_windows.WindowsSpawnWorker`（Popen 马进程 + 句柄监控，无 setpgrp/wait4/killpg）；验收马进程走 `run_rq_horse` 身份校验 | 代码完成；离线控制流回归通过；真实服务执行 NOT RUN |
| 队列业务 | **已实现** live 矩阵（独立 Worker 进程）：成功、RQ 计划重试（新马进程 PID 证据）、终态失败、执行中取消、双 Worker 槽位延迟（CONCURRENCY_LIMIT → enqueue_in）、强退后租约过期恢复、五类检查入队 | 代码完成；默认 skip；真实服务执行 NOT RUN |
| 五类版本门禁 | **已实现**：本地确定性检查适配器（五类 PASS）+ PAGE_INSPECT 经真实队列执行；门禁放行逻辑由现有 e2e/单测覆盖 | 代码完成；真实服务执行 NOT RUN |
| 编排入口 | 不安全的固定 Compose 已删除；随机凭据/本次资源标签的容器编排未实现，`-RunLive` 保持 BLOCKED，live 用 direct pytest 入口 | 未完成（用户明确不安装环境） |
| 主分支验收 | 审阅通过后的合并与 master 独立复验 | 未发生 |

## 2026-08-28 组长最终轮：Windows 独立 Worker 与队列矩阵（本节为准）

提交序列：`e9fd52b`（移植 PR15 的 assigned_to_job HANDLE 退出竞争修复 + 回归）、
`aee3a38`（迁移 env 导入排序）、`d4db025`（WindowsSpawnWorker + 验收 Worker/马进程 +
五类检查本地适配器 + 离线控制流回归）、`4bfa1c4`（live 矩阵测试 + live 门禁解封）、
`e5aa43f`（runner BLOCKED 文案指向 direct pytest live 入口）。

### 本轮实现

1. **`apps/api/app/rq_windows.py`（产品代码）**：`WindowsSpawnWorker(Worker)`。
   rq 2.6.1 的 SpawnWorker 马进程调用 `os.setpgrp`、父进程用 `os.wait4`/`os.killpg`/`os.WTERMSIG`，
   三者在 Windows 均不存在——这是产品级缺口（`run_worker.py` 一直选用的 SpawnWorker 在 Windows 首个任务即崩）。
   本实现用 `subprocess.Popen`（CREATE_NO_WINDOW）拉起马进程、轮询句柄监控、`Popen.kill()` 终止；
   连接凭据经子进程环境变量传递，不出现在命令行。`run_worker.py` 在 win32 改用该类（同步更新
   `test_pr6_regressions` 断言）。death penalty 依赖 rq 在无 SIGALRM 平台自动选择的 `TimerDeathPenalty`。
2. **验收马进程**：`AcceptanceWorker._horse_spawn_command` 拉起 `run_rq_horse`
   （重建 Worker/Job/Execution，校验 Redis 归属、worker 注册、job func/args/ID 归属后才 `perform_job`）。
   Worker 子进程本身由 OwnedProcessTree（Windows Job Object）监督，马进程继承 Job 归属；
   强退即全树退出，无孤儿。
3. **五类检查**：`LocalImageFixture.analyze_multimodal` 输出确定性五类 PASS
   （SPEAKER/CHARACTER/OUTFIT/PROP/CONTINUITY），使 PAGE_INSPECT 能经真实 RQ 路径用本地适配器执行。
4. **live 矩阵测试（默认 skip，真实服务下执行）**：独立 Worker 成功路径（马进程 PID ≠ Worker PID 证据）、
   可重试失败经 RQ 计划重试且两次进入不同马进程、终态失败 FAILED 且零输出、执行中取消零输出、
   双 Worker + 项目并发 1 触发 `CONCURRENCY_LIMIT` → `enqueue_in` 延迟槽位任务 → 释放后完成、
   强杀整树后租约过期 → `recover_pending_jobs` 恢复 → 第二个 Worker 以不同 PID 完成、
   PAGE_GENERATE + PAGE_INSPECT 五类检查经队列完成。
5. **live 门禁解封**：conftest 的 `live_integration_enabled` 恢复为显式开关
   （默认 skip NOT RUN；显式 opt-in 无服务时快速响亮失败，0.21s 实测）。
   harness 断言同步更新为新的开关语义。

### 离线验证（组长独立执行）

- 全量 `pytest`：**315 passed / 23 skipped**（23 = 16 既有 + 7 新 live 测试，skip 原因均为 NOT RUN）/ 23 条既有警告。
- `tests/test_integration_harness.py`：**64 passed**（含新增 assigned_to_job 竞争回归、
  WindowsSpawnWorker 马进程命令/凭据不出 argv、意外退出/超时 kill 监控、AcceptanceWorker 驱动身份）。
- `ruff check apps/api tests`：通过；`git diff b7d89c0..HEAD --check`：干净。
- 显式 opt-in 无服务路径实测：0.21s 快速失败（Security Violation: URL missing），无连接尝试。

### 仍然 BLOCKED / 未完成

- **真实 PostgreSQL / Redis / RQ 执行：NOT RUN。** 用户已明确本轮不授权安装
  PostgreSQL/Redis/Docker/WSL。上述 live 矩阵只能在提供隔离服务（127.0.0.1:55432/56379，
  归属校验通过）后运行；在真实服务上跑通之前，PR #14 不满足合并条件。
- 容器编排（随机凭据/资源标签/端口预检/只清理本次资源）未实现；`-RunLive` 保持 BLOCKED。
- 本轮无任何真实服务连接、无供应商调用、无凭据读取；临时数据已清理。

## 证据边界

本机前次只读环境探测未发现可用 Docker/PostgreSQL/Redis/WSL 环境。
安装/下载服务器或提供隔离服务须另获用户授权。
此前 5 项 harness、纯解析与接口签名探针均为离线检查，不能计为 PostgreSQL 或 Redis 实测。
浏览器/性能由 Issue #13 单独验收；供应商调用、容器安全审计不在本 PR 的完成声明中。
最新独立检查结果记录在 PR 评论，只有实际执行的检查才可标记通过。


## 2026-08-28 组长接管：PostgreSQL 场景修复进展

在入口安全修复 `dd395b0` 之后，继续修复实际测试代码（尚未完成整个 PR）：

- PostgreSQL 使用每次随机 schema 的独立连接池，连接启动参数限定 search_path，
  不含 public，并设置锁/语句超时。schema 创建与归属标记在同一事务；
  引擎创建、迁移或测试体失败均进入归属核对后的清理，标记异常时拒绝删除。
- 通过已有 Alembic 环境注入本次连接并执行 upgrade head，替代 metadata.create_all。
  默认迁移入口仍保留；没有新增 schema revision 或业务数据模型变更。
- 正常并发发布改为验证两个成功版本 1/2；另用数据库真实唯一约束冲突检查有限重试、
  路由 409、已提交版本保持不变与同一 Session 恢复。
- 审批回滚在最终 commit 前先断言 batch/candidate/job/node/run 完整关联，再抛错；
  新 Session 验证无残留，同一 Session 重试成功。
- 行锁用第二连接 NOWAIT 竞争验证；候选并发不再跳过 ensure_page_ready；
  关闭批次场景使用旧实体与独立关闭者。增加邻接 schema sentinel 保留与清理检查。
- PostgreSQL 场景只验证数据库：使用真实 LOCAL 队列配置通过就绪校验，在投递边界
  截住任务，不启动 worker 或供应商。真实队列行为仍由 Redis/RQ 场景负责。

### 本轮实际执行的验证

- 离线 harness：25 passed（1 条 Starlette 弃用警告）。
  新增的归属改变拒删、引擎构造/迁移/测试体失败清理均为 mock 控制流检查，非 PG 实测。
- 既有 SQLite 迁移测试加新连接注入测试：9 passed。
- 三个临时 SQLite 场景控制流探针（发布冲突恢复、最终审批提交失败、关闭批次）：3 passed。
  合计 12 passed / 23 条弃用警告，耗时约 11.35s；**不能计为 PostgreSQL 验收**。
- 初次探针因点号文件名导致 pytest 收集失败；重跑后发现外层 SQLite 事务与
  WORKER_UNAVAILABLE 前置条件问题，修正后上述 12 项通过。没有跳过失败项。
- 本次修改 Python 文件的 Ruff 检查通过。临时探针和临时数据库已清理。
- 真实 PostgreSQL schema / Alembic / NOWAIT / 并发 / sentinel 测试仍 NOT RUN。
  全量 npm check、Redis/RQ 进程矩阵、编排随机资源与凭据仍未完成；live 入口继续封闭。


## 2026-08-28 组长接管：Redis/RQ 清理准备

在 `a3bcdf8` 之后新增 `tests/integration/redis_resources.py`，替代旧的字典加
`except: pass` 清理器。此提交仍是准备工作，**不是 Redis/RQ 集成验收通过**。

- 校验实际 Redis client 的 host/port/db；用随机 token 和 SET NX 建立本次归属。
  队列、job、worker 必须先注册，拒绝接管已存在的资源。
- 根据当前安装的 RQ API 收集 queue/intermediate/first_seen、六类 registry、
  job/依赖、results、executions、worker、scheduler/maintenance key。
  `rq:queues` 与 `rq:workers` 仅移除本次完整 key 成员，不删除全局集合。
- 只发现已登记 application job 派生的 `-slot-<uuid>` 延迟任务，包含 job hash
  过期后仍留在 results/executions 或队列 registry 的资源；不扫描或删除任意 job。
- 清理前 WATCH 并校验归属、job origin、全局集合类型、队列成员、
  worker death 与 scheduler lock。不明 job/外来 worker、活动或未确认终止的
  worker/调度器均拒删；worker key 过期也不等同于确认退出。
- 清理失败不吞异常、不标记完成，保留归属用于重试；确认相关 key 和全局成员已移除后，
  最后删除归属标记。不存在任何 FLUSHDB/FLUSHALL。
- 补充真实 Redis 下使用 RQ Queue、Result、Execution、Worker 注册 API 创建资源后，
  验证本次清理与邻接 namespace/global membership 保留的测试。该测试尚未运行；
  它验证资源清理，不等于独立进程 Worker 执行。

实际执行：离线 harness **39 passed**（1 条既有 Starlette 警告），本次四个 Python
文件 Ruff 通过。默认运行 `tests/integration/` 为 **16 skipped**，明确 NOT RUN；
这些 skip 不能计为通过。新增离线检查是 mock 协议/控制流验证，没有使用 fakeredis，
也没有连接任何真实服务。临时测试配置均已清理。

仍需完成：SpawnWorker 子进程监督与本地适配器、强退后 PID/调度器退出证明、
跨进程清理恢复记录、多 worker 槽位释放/重试/取消失租、五类版本门禁、
随机凭据和本次容器编排、真实 PostgreSQL/Redis 执行及全量复验。
当前 live 入口继续 BLOCKED。


## 2026-08-28 组长接管：Windows 进程监督准备

在 `bdc9254` 之后新增 `tests/integration/process_resources.py`，并扩展已有离线
harness。本次验证的是 Windows 进程树生命周期，**不是 RQ Worker 或队列业务验收**。
该监督器尚未接入真实 RQ Worker/调度器及 Redis 资源清理器，不据此解除 live 阻塞。

- 每次创建随机命名的 Windows Job Object，设置 KILL_ON_JOB_CLOSE，不允许 breakaway。
  Python 启动器用 CREATE_SUSPENDED 创建，绑定本次 Job Object、持久化归属记录后，
  才恢复线程执行；避免 Windows venv 启动器先产生未受监督的执行进程。
- 持有自己创建的进程句柄，不通过保存的 PID 调用 taskkill。停止后查询 Job Object
  的活动进程数，并等待直接启动的进程退出；子进程和孙进程都受本次 Job Object 约束。
- 子进程不复制父进程的应用环境；使用独立临时工作目录，参数通过参数数组传递。
  归属记录只有 token、控制器 PID/创建时间、子进程标签/PID、退出码和错误类型，
  不保存命令正文、环境、数据库 URL 或密码。
- 强退恢复只接受本次目录与 token，核对控制器创建时间以区分 PID 复用。
  活动控制器或仍活动的 Job Object 拒绝接管；不通过伪造 Redis worker death 绕过清理。
- payload 清理失败保留归属记录，不提前标记 cleaned，可在锁释放后重试。
  同时出现测试体和清理异常时保留两者；部分进程句柄已经关闭后仍可重试。
- Job Object 语义参照
  [Microsoft Windows 官方文档](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。
  此模块面向受控的本地 Python 测试进程，不是通用恶意进程沙箱或容器安全审计。

### 实际执行与发现

- 当前本机安装 RQ **2.6.1**。直接读取源码和平台能力探针确认：
  SpawnWorker 子进程仍调用 os.setpgrp，继承的监控/终止调用 os.wait4/os.killpg，
  而本机 Windows 没有这些函数。因此不能仅改类名就声称 Windows SpawnWorker 可用；
  仍需实现并验证兼容的独立 RQ 执行路径，不能退回 SimpleWorker 冒充。
- 初次检查 **47 passed / 1 failed**：发现 venv 启动器 PID 与实际 Python PID 不同。
  据此改用挂起创建，并增加实际执行进程归属检查，未把 PID 不同简单当作测试噪音。
- 最终轻量 harness **51 passed / 1 条既有 Starlette 警告**，约 2.70s。
  本次新增 12 项，包含真实小型 Python 子进程/孙进程、控制器 kill/os._exit、
  实际 SQLite 锁文件清理重试，以及故障注入控制流检查。
- 实际进程测试不连接 Redis/PostgreSQL，不执行供应商请求；这些结果不能计入
  Redis 重试、租约、多 worker 槽位或 PostgreSQL 集成通过数。
  临时目录由隔离入口及监督器清理，没有操作 Grok 的 runtime 或 3000/8000 端口。

下一步仍为：把监督器接入独立 RQ Worker/调度器与退出证明、本地适配器及共享持久化
证据，修复剩余队列业务和五类版本门禁，再完善随机资源/凭据编排。
真实服务环境仍未获安装/下载授权；本 PR 未合并，master 验收尚未发生。


## 2026-08-28 组长接管：子进程配置与本地适配器准备

在 `6086155` 之后，新增 `tests/integration/worker_runtime.py`，继续推进实际执行入口：

- 新进程在导入 app.database / worker_tasks 之前安装隔离 Settings，仅接受显式初始化值，
  不读取父环境、dotenv 或 secrets-directory；配置和存储路径须处于已验证归属的 payload。
- 本地图像适配器在执行进程内安装，不依赖父进程 monkeypatch。进入调用、返回、可重试
  或终态失败事件以独立文件落盘，包含实际执行 PID 和 application job ID，不记录提示词或凭据。
- 首次失败标记用排他创建文件保存，支持新进程读取同一标记；可阻塞的本地调用为后续
  取消/失租交错测试预留入口。未实现的文本操作明确失败，不退回真实供应商。
- 准备了真实 RQ horse 入口：重建 Worker/Job/Execution，核对 Redis owner、队列、
  已登记 application job 与 slot 派生 ID，调用 RQ perform_job。
  PostgreSQL 子进程配置限定本次 schema 并校验 COMMENT/current_schema。
  **该 live 路径尚未执行，兼容的父 Worker/调度器及退出清理仍待接入。**
- 独立 offline application probe 禁止 Python socket 连接，只执行当前 worktree 的
  实际 execute_job。它使用新建临时 SQLite，不是 PostgreSQL 或 Redis 替身验收。

实际检查：离线 harness **56 passed / 1 条既有 Starlette 警告 / 12.01s**。
新增 5 项覆盖子进程实际页面生成成功、终态失败、两个不同进程的失败后再次执行、
假 dotenv/父环境哨兵隔离，以及 live URL 覆盖在配置落盘前被拒绝。
父进程以新 Session 验证 candidate、asset、generation record 与 job 完成状态，
并检查本次生成文件和子进程事件。第二次执行由测试显式启动，**不代表 RQ 自动重试**。
本轮两个 Python 文件 Ruff 通过；临时库、图片和进程目录均由夹具清理。

没有开启 live 入口、连接 PostgreSQL/Redis、安装或更改共享依赖，也没有真实供应商调用。
RQ 2.6.1 的 Windows 父进程兼容问题仍未修复；默认应用 Worker 启动路径也尚未验收。
本 PR 仍不能合并，后续必须接上独立 Worker/调度器和真实队列业务矩阵。
