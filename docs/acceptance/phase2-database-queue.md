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
| PostgreSQL 隔离 | 随机资源归属、失败清理、保留非本次 sentinel、真实 Alembic 迁移 | 修复中；真实环境未运行 |
| PostgreSQL 并发 | 正常并发发布应允许序列化成功；另以确定冲突验证耗尽 409、恢复、持久化 | 现有断言需修复；未验收 |
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
