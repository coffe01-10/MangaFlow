# Round 7 收尾：#391-#394 修复归档（2026-09-11）

基线 a7b7e55 → PR #395（merge d886f10），四单全关。上一会话子代理因
off-peak-ticket-expired 派发失败，本轮重试成功：三路并行（wt-d/wt-b/wt-e，
文件互斥），组长逐 diff 核实 + BOM 双查 + pytest 独立复跑后集成。

## 修复清单

| Issue | 级别 | 提交 | 内容 |
| --- | --- | --- | --- |
| #391 | P3 桌面 | bdf8d9a | WorkflowView 两处使能谓词要求 drawModel 仍是 imageModels 全目录成员（与 web #387-round-4 同构）；StaleApprovalModelChecks 补 3 钉 + 2 对照 + 1 机制钉 |
| #392 | P4 工具 | 4ad75b3 | recreate_junction 先解析 readlink 原文为绝对路径再做 REPO_ROOT 前缀匹配；悬空目标独立报错；文件型链接以 cp -l 重建 |
| #393 | P4 工具 | 3907f01 | res 缺失 + 同 pid retired 在位时 assemble 拒跑（_stale_retired_error 含手动恢复指引）；pytest 钉 pid 复用场景字节级存活 |
| #394 | P4 web | fc5c5fb | 审批条区分 models.isError && data===undefined（读取失败 + 重试入口）与空目录（#381 指引不变，按钮谓词不动）；vitest +2 |

## 门禁（顺序执行，日志 gate-render/cargo/check-r7b.log）

1. `dotnet run --project apps/desktop/native-tests -- --render`：54 原生检查
   + WPF 导航/视觉，68 PASS / 0 FAIL。
2. `cargo test`（shell-core 含 delivery_contract）：全绿（71+9+6+6+10+17 passed，0 failed）。
3. `npm run check`：ESLint/Ruff 干净；pytest 1520 passed / 37 skipped；
   Vitest 488 passed（45 文件）；web 生产构建绿。

## NOT RUN 边界

- #392：宿主无 SeCreateSymbolicLinkPrivilege，真原生相对符号链接的创建
  未验证（fixture 以 readlink 测试替身注入同样文本，解析/判定/重建副作用
  走真实文件系统）。junction 路径已有 #385 实跑覆盖。
- 其余无。

## 教训（已入 memory）

- 无新教训；off-peak 重试派发成功。
