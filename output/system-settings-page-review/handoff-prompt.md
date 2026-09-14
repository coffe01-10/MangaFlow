请修复 MangaFlow WPF「系统设置与运行诊断」的既有流程差距，保留当前 UI 还原。

仓库 D:\自媒体\漫画工作流；本轮基线 d11d55b143c87c7dc0263b9b0000fb70c88c63ef。先检查工作区未提交改动，不覆盖 SettingsLayout.cs、SettingsView.cs 及专项测试。

本轮发现以下源码层面的缺口，尚未做真实后端复现：

1. 保存期间的新运行参数会被覆盖
   SettingsView.SaveRuntime 成功时直接 RenderRuntime，清空并重建全部输入。请求期间仍能编辑，新输入会被提交时旧响应覆盖。网页 settings/page.tsx 使用 submittedDraftRef 保留在途新修改。
   验收：延迟 PATCH，提交并发 2 后输入 5，释放响应，仍保留 5 并标明新增未保存修改；再次保存带新 version。

2. 409 恢复与离开保护
   SaveRuntime 异常只显示消息，version 不更新；RefreshAsync 有 RuntimeDraft/ConnectionDraft 时直接跳过。容易出现提示刷新、但刷新无法更新版本的循环。
   SettingsView 没有覆盖 ConfirmLeaveAsync；页面导航和窗口关闭是否保护未保存草稿，需要沿 MainWindow 与 WorkspaceView 确认。本轮顶部入口调用现有 ConfirmLeaveAsync，但该调用本身不等于完整草稿保护。
   验收：冲突保留本地编辑且能安全恢复；返回项目、进入用量页、侧栏导航和关闭窗口的放弃/取消路径均保护运行设置和连接草稿。

3. 供应商重绘可能清空草稿
   搜索、类型、能力、排序等直接 RenderProviders，清空 providerList 后重建 ConnectionPanel。已有 F5 守卫不覆盖这些路径。网页搜索前会检查 dirtyGroupIds。
   验收：输入半段 API Key 或手工模型字段后切换搜索/筛选/展开状态，需保留输入或明确确认；取消操作不得丢草稿，测试不得把真实密钥写入输出。

4. 状态条更新时序
   LoadAllAsync 并行读取 providers/runtime/diagnostics。healthLabel 只在 LoadDiagnosticsAsync 完成时根据 providers 计算；诊断先完成时可能停在“读取中”。供应商或诊断失败时也可能留下旧值。
   验收：分别延迟三个 GET、为空列表、单项失败，再重试；状态条独立且准确，不能拿上次健康状态冒充本次检查。

其他尚未一比一迁移的交互：
- 网页供应商分组可折叠，当前客户端分组仍是静态标题；不要改层级时破坏 ConnectionDraft 依赖 providerList 直属 ProviderCard 的遍历。
- 网页搜索也考虑模型目录，客户端现有搜索只覆盖供应商名称/预设键/连接协议。
- 网页新增供应商为页内表单，客户端仍采用原生对话框。该交互迁移需单独确认草稿生命周期。

边界：仅修本页客户端与定向测试。发现后端必须修改时先给证据与方案，不改事务、队列、Worker、迁移或供应商执行；不运行真实生成、付费测试、删除用户项目。不可把 mock 或离屏测试说成真实集成通过。不要修改 roadmap/development-progress。

验证：新增失败回归；运行 --system-settings-page；保持 #469 F5 草稿保护、数值钳制、RuntimeSaved/PollInterval 发布通过。报告精确改动与 NOT RUN 范围。

