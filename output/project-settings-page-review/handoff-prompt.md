请修复 MangaFlow WPF 项目设置页的既有保存流程差距，保留已还原的 UI，不改其他页面。

仓库：D:\自媒体\漫画工作流
审查基线：5d8730e003fd6aaeab8f85ff79517bc8258009d7；工作区还有本轮项目设置 UI/测试改动，开始前检查 git diff，不要覆盖。
涉及文件：
- apps/desktop/native/Views/ProjectSettingsView.cs（Save、Dirty、RefreshAsync、ConfirmLeaveAsync）
- apps/desktop/native/Models.cs（ModelOption.From）
- 对照 apps/web/app/projects/[id]/settings/page.tsx
- 只读核对 apps/api/app/api/routes/models.py、projects.py 及 schemas.py

以下来自源码核查，未声称完成真实后端复现：

1. 保存期间新增编辑丢失未保存标记
   Save 发出 PATCH 后表单仍可编辑；响应成功无条件 dirty=false 并显示“项目设置已保存”。如果用户在请求期间将并发 2 改为 8，响应仍对应提交时的 2，但当前显示 8；此后离开可能不再提示。网页已通过 submittedDraftRef / sameProjectDraft 区分在途新增编辑。
   验收：延迟 PATCH 响应，提交 2 后输入 8，再释放响应；保留 8、继续标脏，离开有提示。再次保存发送 8 和新 version。注意危险区确认名称也被当前 Dirty 纳入保护。

2. 409 提示要求刷新，但刷新被 dirty 阻挡
   Save 的异常分支仅设置 saveError；version 仍旧值。RefreshAsync 遇 dirty 直接返回，未重读版本。网页 409 会 invalidateQueries 更新服务端版本。
   验收：模拟其他端先保存导致 409；保留本地编辑，提供有效的冲突恢复过程，再次保存能携带新版本。不能简单去掉 dirty 守卫让 F5 丢稿，也不能自动盲目重试覆盖冲突。

3. 文字模型目录 ID 与旧 alias 未充分区分
   ModelOption.From 仅把 logical_alias 放到 Value，丢掉 catalog_id。GET models 对无旧 alias 的模型返回 logical_alias=model.id。项目设置新选模型时，除非值等于原 default_text_model_id，否则 Save 把值写入 text_model_alias，和网页新选 catalog_id -> default_text_model_id 的规则不一致。
   后端 PATCH 对 default_text_model_id 验证 AIModel 类型与可用性；alias 是兼容字段。现有 fallback 可能仍能路由，不能未经真实验证就称所有模型选择都失效。
   验收：覆盖新目录模型、旧 alias、当前模型已隐藏/目录缺失、auto；新目录选择写正确 ID、保留明确旧 alias、auto 清空两字段。不要为修本页破坏 ModelOption 在其他页面的使用。

范围约束：
- 只修项目设置客户端流程和必要的定向测试；如发现必须修改后端，先给出证据和独立方案。
- 不改变数据库事务、Worker/队列、迁移、供应商执行、其他页面 UI。
- 不执行真实生成或删除用户项目。
- 不声称离屏和 mock 等于真实集成。真实服务不可用时记 NOT RUN。
- 临时文件及时清理；不得覆盖其他 AI 的未提交修改。

完成后运行新增失败回归、NativeProjectSettingsPageChecks，以及 #469/#471 的离开/F5/取消保存检查。报告具体改动、测试结果、未验证范围。

