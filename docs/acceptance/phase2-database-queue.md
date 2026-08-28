# Phase 2: PostgreSQL 与 Redis/RQ 验收状态

- Issue: [#12](https://github.com/coffe01-10/MangaFlow/issues/12)
- PR: [#14](https://github.com/coffe01-10/MangaFlow/pull/14)
- 基线: `b7d89c0a0e8b7e80abb7293b561d9213bc79ee3d`
- 2026-08-28：成员第二轮返工提交 `20f2419567131375a910d55825ecbd126709b8d3` 仍有阻塞，组长接手修复。
- **代码状态：修复中，未批准合并。真实 PostgreSQL / Redis / RQ 验收：NOT RUN / 环境 BLOCKED。**

本文件取代此前“完整套件”“全部 Validated”“100% 数据安全”等无充分证据的表述。
离线 URL 校验通过不能证明资源清理、进程执行或真实服务集成正确。

## 当前可用入口

`scripts/run-phase2-acceptance.ps1` 默认只运行离线 harness。
可用 `-Python` 指定已安装的解释器，不安装或修改依赖。

- URL 校验在独立模块中进行，不导入应用或加载 dotenv；禁止 query/fragment、远程主机、
  默认端口、模糊 Redis DB 与不支持的 PostgreSQL 驱动方案。
- `-DryRun` 仅做已提供 URL 的语法校验，不检查服务可达性或资源归属。
  缺 URL 明确输出 NOT_CONFIGURED；没有启动、停止或连接服务。
- 默认运行会清除继承的 live 开关和 pytest 参数，禁用 dotenv，使用临时离线配置。
- **临时安全措施**：`-RunLive`、`-StartContainers`、`-StopContainers` 当前均非零退出并报告 BLOCKED。
  直接 pytest 的 live opt-in 也会失败。恢复前必须完成归属校验、清理与业务场景修复。
- 旧 `docker-compose.acceptance.yml` 尚未修复固定资源名与凭据，不应直接运行。
  不授权下载镜像、安装系统服务或访问任何外部服务。

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
| RQ 进程 | Windows 可用的独立 SpawnWorker；子进程确定性本地适配器；共享持久化证据 | 现有 SimpleWorker 不满足要求 |
| 队列业务 | 多 worker 槽位释放后继续执行、延迟与重试、取消/失租交错、状态写回竞态 | 覆盖不足；未验收 |
| 五类版本门禁 | 通过真实业务入口建立并验证对应矩阵 | 未完成 |
| 编排入口 | 随机凭据/本次资源标签、端口预检、显式禁用 dotenv、只清理本次资源 | Live 入口暂时封闭 |
| 主分支验收 | 审阅通过后的合并与 master 独立复验 | 未发生 |

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
