## 概要

第三轮审查-修复循环的集成批次：4 条修复分支 + 组长直修 2 处，覆盖 7 个 issue（#380 #381 #382 #383 #384 #385 #386）与上一会话完成但未提交的原生参考素材页（`fix/native-reference-page`，2dd1cbb）。

## 用户可见行为

- **编排页在审批等待期间不再停摆**（#380）：run 处于 PAUSED 时 web `workflowRunsPollInterval` 以 10s 折中轮询（RUNNING 仍 3s），桌面 `PollTick` 同契约（RUNNING 每 tick、PAUSED 10s 节流）；另一端审批/取消后本端能刷新页脚与节点徽标。
- **无可用图像模型时的出路提示**（#381）：generator.page 审批条在 `imageModels` 为空时渲染"未配置可用图像模型…请先在设置中启用，或取消本次运行"+ 前往设置链接，不再是无声的永久禁用按钮。
- **深链缺页横幅覆盖零页章节**（#384）：去掉 `pages.data.length > 0` 前置，零页章节深链同样给出可见提示（文案区分"本章还没有任何页面"）。
- **原生参考素材页重做**（2dd1cbb）：双列素材卡、显式用途选择、行内改名、绑定/解绑、待保存服装/风格参考交接；详见 `output/reference-page-review/README.md`。

## 工具与桌面基础设施（无直接用户行为变化）

- **swap 回滚窗口**（#382）：两条 rename 之间落入异步异常（Ctrl-C 等）不再可能删除唯一旧树；回滚也失败时保留 retired 并给出手动恢复指引。
- **dist 锁注释收窄**（#383）：flock⇔fcntl 不变式收窄为同环境来源假设；interlock 测试在混装主机（bash 有 flock + python 无 fcntl）上 skip 并注明原因。
- **静态导出管道修复**（#385）：硬链接克隆感知 junction（此前 HEAD 上 `cp -al` 对 `@mangaflow/web` junction 必然失败，管道整体断裂）；脚本尾部补产物冒烟断言；实测 proxy.ts × output:export 不是硬失败（仅警告），proxy.ts 保持不动；刷新被跟踪的导出产物 index.html。
- **检查接线修复**（#386）：`NativeWorkflowRunChecks.Run()` 是 Task，此前调度未 await——故障被静默吞、套件空转绿灯；补 await 后该节真实执行。附带迁移 NativeAssetsLoopChecks 的参考卡查找到新的 Button 缩略图结构（旧 Border+Hand 查找在参考页重做后必失败）。

## 验证

- `dotnet run --project apps/desktop/native-tests -- --render`：54 项全过（含复活的 workflow run 检查与迁移后的 assets loop 检查）。
- `cargo test`（shell-core）：64 单测 + delivery_contract 9 + log_export 6 + log_rotation 5 + picker_policy 10 + startup_protocol 17，全绿。
- `npm run check`：exit 0（本次 PR 门禁，日志见提交说明）。
- 静态导出端到端实跑 exit 0（junction 重建、10 页生成、冒烟通过）。
- vitest 定向：workflow-studio + project-workspace 18 项（代理跑）；apps/web 全量 484 项（代理跑）。
- assemble 交换测试 4 项（窗口注入 + 回滚失败新增两条）；dist 锁测试 5 项（含 interlock 真实运行）。

## NOT RUN

真实 Vertex 图像调用、Playwright e2e、真实窗口鼠标/DPI 验收、tauri build/NSIS 打包、MSYS2 混装主机实测、CI。

Closes #380, closes #381, closes #382, closes #383, closes #384, closes #385, closes #386.
