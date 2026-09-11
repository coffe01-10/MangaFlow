**已修复（round 3 集成批次）**：web 谓词抽为 `workflowRunsPollInterval`（任一 RUNNING→3000ms；否则任一 PAUSED→10000ms 折中；终态/空→false），`workflow-studio.tsx` 的 runs 查询改用之；桌面 `WorkflowView.cs` 的 `runActive` 并入 PAUSED 并以 `lastPausedFetchTicks` 时间戳把 PAUSED 重取节流到 10s（RUNNING 维持每 tick），1296 区注释块同步新契约。

- 提交：0ab3d86（web + vitest 契约 8 断言）、7699a87（桌面 + NativeWorkflowRunChecks 钉住翻转：窗口内不重取/模拟 10s 流逝后必重取/RUNNING 回归保留）。
- 验证：vitest 定向 18 项过（代理）+ apps/web 全量 484 项过（代理）；`dotnet ... --render` 54 项全过（含该节）。注意该节此前因 #386 未真正运行，本次起为真实执行。
- NOT RUN：真实后端双端并发实机验收、e2e。
