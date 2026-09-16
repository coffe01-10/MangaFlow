# Grok 原生桌面端交付复查

更新（同日修复轮）：下列四个问题已在当前工作区修复，针对性原生回归与完整 `--render` 回归均通过。正文保留初次审查证据；最新修复记录见文末。

审查基线：`dc901b015378500c059c5c25e2a033d5a675034f`，包含 `9ad1000cef191cc0b29cc42c5067bd89342860e4`。审查前工作树干净；本轮未修改业务代码。

结论：交付补齐了多项入口，但当前不能认定桌面前端与后端已对齐，也不能验收为功能完善。以下前三项已使用 FastAPI TestClient、真实路由和隔离 SQLite 数据库复现；第四项由代码控制流确认，未做运行时复现。

## 已补齐的范围

- 工作流页面选章、失败重试、版本恢复，以及默认漫画和章节导出流程已有实现；本轮原生回归中 B01–B04 输出 PASS。
- 新增分镜内联编辑、剧本服装自动保存、生成页场景继承卡、导演历史操作、设置供应商编辑、用量抽屉等。
- 侧栏及多个视图的离屏渲染、既有行为检查已输出 PASS。不能将控件存在或离屏渲染通过等同于保存闭环正确。

## 必修问题

### 1. P1：分镜“动作与表演”保存返回成功，实际没有保存

位置：`apps/desktop/native/Views/StoryboardView.cs:1089`。

新“保存本格”请求把动作文本放到顶层 `script_action`。后端 `apps/api/app/schemas.py:794` 的 `PanelUpdate` 接受 `actions`，不接受顶层 `script_action`，未知字段被忽略。旧原生弹窗在同文件 2273 行，以及 Web 的 `panel-inspector.tsx` 都通过 `actions.script_action` 保存。

复现：给一个已有动作的分镜提交 `PATCH /api/v1/panels/{id}`，请求包含当前 `version`、`script_action: "NEW ACTION"`、`background: "NEW BG"`。返回 200，背景改为 NEW BG，但 `actions.script_action` 仍是旧值。桌面端随后重新加载页面，用户输入消失。

修复要求：合并存量 actions 后覆写 script_action，保留 source_text 等键；增加真实请求载荷及刷新后文本的回归测试。

### 2. P1：导演历史“撤销 → 重做”链路不可用

位置：`apps/desktop/native/Views/GenerateView.cs:1831`、`:1869`–`:1871`。

历史项只读取 commands 的第一条，并为 SUPERSEDED 原命令显示重做按钮，提交该原命令 ID。后端 `apps/api/app/services/director_commands.py:1302` 要求重做对象具有 `inverse_of_command_id`，即撤销时生成的反向命令；原命令不满足条件。

复现：提出并接受 update_panel_shot → 撤销成功 → 原命令状态为 SUPERSEDED → 按桌面实现对原命令调用 `/redo`，返回 409。改用撤销生成的反向命令 ID 调用相同接口则返回 200。

额外边界：当前也为 ACCEPTED 显示撤销，但后端 undo 只接受 EXECUTED；应一并校正。Web `director-workspace.tsx:195` 按命令链最新有效命令及 inverse_of_command_id 决定动作，原生应遵循同一契约。

修复要求：展示并解析完整命令链，验证执行 → 撤销 → 重做 → 再撤销，以及失效历史不能操作。

### 3. P2：场景继承卡漏读第 51 个起的场景资产

位置：`apps/desktop/native/Views/GenerateView.cs:920`；不可用提示在 `:965` 附近。

新卡片请求 `/projects/{id}/scene-assets` 时没有分页。后端 `apps/api/app/api/routes/scene_assets.py:124` 默认只返回 50 条。卡片在这一页里查找已绑定资产，未找到就显示“场景资产不可用，不能视为已就绪”。

复现：隔离数据库创建 51 个有效场景资产。原生所用的默认请求返回 50 条，遗漏最后一个；`?offset=50` 可以正常返回该资产。因此页面绑定后续资产时，卡片会错误判定有效资产不可用。这是显示和就绪判断错误，本次没有证明实际生成请求丢失该资产。

修复要求：按绑定 ID 获取资产或完整分页；增加超过 50 条和归档资产的边界测试。

### 4. P1：服装自动保存会吞掉在途期间的后续修改

位置：`apps/desktop/native/Views/ScriptView.cs:755`、`:771`–`:784`；保存后刷新在 `:361`。

第一次改动触发 PATCH 时，只有保存按钮被禁用，下拉框仍可修改。第二次改动排入 Dispatcher，但 PersistOutfits 发现 saveBusy 后直接返回，没有记录待保存状态。第一次请求完成后 SaveOutfitAssignments 又调用 LoadScriptAsync 重建场景，下拉框第二次输入被服务端第一份结果覆盖。

复现条件：延迟服装 PATCH 响应；先改角色 A，等待 PATCH 开始；再改角色 B，并让 Dispatcher 处理该事件；然后放行第一次响应。当前代码只保存第一份映射。此项为代码审查结论，尚未运行上述受控延迟用例。

修复要求：保存期间禁用相关选择器，或排队合并后续修改并在新版本上继续保存；回归必须把第二次改动安排在请求已开始之后，不能只测试同一 UI tick 的两个选择或双击保存。

## 验证记录与边界

- 临时 API 探针最终结果：3 passed；这里的通过表示成功复现上述三个缺陷，并非功能验收通过。探针按项目要求用后删除。
- 执行 `dotnet run --project apps/desktop/native-tests -- --render output/native-local/grok-review-dc901b0`。基础、侧栏、多个视图、B01–B04 及多项交互输出 PASS；最后输出为 #484 相关检查，随后长时间没有新输出，本轮中断了自己的测试进程。整套结果记为 **INCOMPLETE**，不能宣称全绿；未确定停滞根因。
- 未运行真实模型付费调用、PostgreSQL/Redis/Worker 的线上集成验收；隔离 SQLite 和 HTTP 模拟不替代这些验收。
- 未重新运行完整 npm run check。没有提交、合并或改动 Grok 的业务实现。
- 旧的静态接口路径盘点不是本次 SHA 的行为验收依据。接口路径匹配不能证明字段、状态机和分页语义正确。

建议先修复 1、2、4，再完成 3，并补充相应交互与实际请求契约测试后重新验收。

## 同日修复记录

1. 分镜内联编辑以原有 `actions` 为基础写入 `actions.script_action`，保留原文及其他结构化键；增加重复点击守卫和取消处理。回归实际点击保存、检查请求载荷，再重新读取分镜确认文本仍存在。
2. 导演历史从最新已执行命令沿 `inverse_of_command_id` 回溯，交替提供撤销/重做并提交最新命令 ID。连续六次按钮操作验证通过；ACCEPTED、SUPERSEDED、FAILED 和 regenerate_region 不再误显示可逆操作。
3. 场景卡完整分页读取资产，包含已归档资产用于显示准确原因；场景/资产数据变化也会触发刷新。回归覆盖第 51 条绑定资产及归档状态变化。
4. 服装 PATCH 期间禁用所有相关选择器，成功或失败后恢复；保留失败输入。回归使用挂起的 HTTP 响应检查在途状态、成功回读、新版本提交及 409 后输入保留。修正原测试夹具：挂起请求成功后也更新模拟存储，并在访问离屏视觉树前执行布局。

已通过的原生命令：

```text
dotnet build apps/desktop/native-tests --no-restore
dotnet run --no-build --project apps/desktop/native-tests -- --issue426 output/native-local/fix-contracts-20260916
dotnet run --no-build --project apps/desktop/native-tests -- --storyboard-page output/native-local/fix-contracts-20260916
dotnet run --project apps/desktop/native-tests -- --generate-page output/native-local/fix-contracts-20260916
dotnet run --no-build --project apps/desktop/native-tests -- --render output/native-local/fix-contracts-20260916
```

首次生成页回归捕获了“资产归档但工作台未变化时不刷新卡片”的遗漏；补充刷新判断后重跑通过。完整原生回归最终退出码 0，包含 56 项客户端基础检查、WPF 导航/渲染及交互套件。测试临时图片和配置已清理。

上述为离屏 WPF + HTTP 夹具验证；未进行真实模型付费调用和线上 PostgreSQL/Redis/Worker 验收。

最终整体验证：`npm run check` 退出码 0，供应商中立性、ESLint/Ruff、脚本类型检查、后端 1623 passed / 44 skipped、Web 56 个文件共 609 tests passed、Next.js 生产构建全部通过。跳过的真实集成用例不计为验收通过。

`powershell -NoProfile -ExecutionPolicy Bypass -File apps/desktop/scripts/start-native.ps1 -BuildOnly` 退出码 0；Release 编译 0 错误，并完成 native-host 复制与 SHA256 校验。可运行程序为 `apps/desktop/native/bin/Release/net8.0-windows/MangaFlow.Native.exe`。编译仍有既有的成员隐藏、异步和可空性警告。本轮未自动启动或关闭用户正在使用的应用。
