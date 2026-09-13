# 可直接交给其他 AI 的提示词

请检查 MangaFlow 的 WPF「流程编排」迁移缺项和流程正确性。仓库：D:\自媒体\漫画工作流。检查时的 HEAD 为 e32cbf3，工作区已有未提交的流程 UI 还原代码，请先检查 git status/diff，保留这些修改，不回滚或覆盖。遵守仓库 AGENTS.md。

当前任务：

1. 优先复现并修复“保存失败后仍继续发布或切换工作流”的风险。apps/desktop/native/Views/WorkflowView.cs 的 SaveNowCoreAsync 捕获异常后仅显示失败，返回的 Task 不向调用方传递失败状态；PublishAsync、ValidateAsync 和工作流选择器调用 SaveNowAsync 后继续下一步。ConfirmLeaveAsync 也始终返回 true。请使用 409、网络中断和失败的离场冲刷构造测试，验证用户草稿不丢失、发布请求不在保存失败后发出、切换失败保持原工作流。不要破坏现有保存串行队列、版本检查和 A→B→C 归属保护。这是源码发现的风险，本轮未运行上述失败路径的真实服务复现。

2. 补齐原生发布版本列表与恢复。网页参考 apps/web/components/workflow-studio.tsx；接口定义在 apps/web/lib/api.ts，后端 apps/api/app/api/routes/workflow_definitions.py 已提供 GET workflows/{id}/versions 和 POST workflow-versions/{versionId}/restore（携带当前 version）。当前 WPF 只显示 published_version_id 是否存在，右侧显示的是运行历史，未实现发布版本列表/恢复。要求正确版本号、读取失败可重试、覆盖草稿前明确确认、409 不覆盖编辑，并隔离工作流切换后的旧响应。

3. 补齐“页面所属章节”选择。原生 LoadScopeTargetsAsync 的 PAGE 分支固定 chapters.FirstOrDefault()，所以无法从后续章节选择页面；网页已有页面所属章节下拉。请支持章节切换、清空旧页选择、乱序响应隔离，验证提交的 scope_id 属于选中章节。

4. 核对默认模板与失败运行重试。网页默认创建 manga_default 与 chapter_export 两个模板；当前原生空列表仅创建 manga_default。网页有失败运行重试入口，对应 POST workflow-runs/{runId}/retry，原生 LoadRunsAsync 目前仅渲染运行中的取消操作。请补充去重、失败可恢复、重新读取后的状态更新，不能自动执行付费重试。

以上是客户端迁移/调用链差距，不等于已确认后端服务损坏。先验证现有接口契约，优先复用接口；不要顺手修改数据库、事务、Worker 生命周期或供应商实现。若确实发现后端缺陷，给出复现证据和单独修复方案后再扩大范围。

本轮 UI 在 WorkflowLayout.cs 与 WorkflowTheme.xaml；新增入口 NativeWorkflowPageChecks.cs / --workflow-page。请维持矩形按钮、原生深色表单、侧栏响应式布局，不通过 WebView 替换 WPF。后续视觉补齐还包括小地图、框选多节点与完整画布手势，不要宣称本轮已全部完成。

必须运行与修复范围匹配的检查，至少保留通过：

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll --workflow-page output/workflow-page-review/verified
```

为每个行为修复添加失败路径回归。若验证真实服务，请报告实际 API/Worker 环境、数据归属和执行结果；缺少 PostgreSQL/Redis/Worker/供应商环境时写 NOT RUN，模拟测试不能替代真实验收。未经明确授权，不发起真实付费生成。生成的临时文件及时清除。最终报告区分已实现、模拟验证、真实验证与未完成项。
