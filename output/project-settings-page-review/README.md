# WPF 项目设置页还原记录

日期：2026-09-14。基线：5d8730e003fd6aaeab8f85ff79517bc8258009d7。

本轮对照 apps/web/app/projects/[id]/settings/page.tsx 与对应 CSS，还原项目设置页的顶部控制栏、大标题、1.1:1 双列卡片、工作方式矩形选项、清晰度分段选择、方形连续性开关、模型策略说明及底部整行删除区。小窗口改为单列；保存入口与结果反馈固定在顶部。采用原生文本和矢量图标，没有对正文做位图缩放。

本轮修正：分辨率再次点击不能取消全部选中；删除按钮仅在名称精确匹配时启用；读取错误可重试；重复渲染不复用已挂载的标题。保留现有保存与删除接口，不改后端。

## 验证

- Release 构建成功，0 errors；编译器仍有 warnings，不能据此称零警告。
- NativeProjectSettingsPageChecks：1240、940、650、360 DIP 布局；双列/单列和组件边界；固定保存及反馈；分辨率互斥；并发输入校验；PATCH 字段/version；在途防重复提交；冲突提示并保留编辑；名称确认；重复渲染与读取失败重试。
- 复用 #469 项目设置离开/F5 编辑保护及取消保存提示检查。测试定位保存按钮改用 AutomationProperties.Name，以适配图标加文字内容。
- screenshots/：最终离屏截图（四种宽度、保存、冲突、读取失败）。这是原生控件真实绘制，数据使用 HTTP fixture。
- 未运行：真实后端持久化、实际项目删除、真实供应商调用、实体窗口高 DPI、帧率/动画性能验收；不能把 fixture 通过说成全链路连通。

命令：

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll --project-settings-page output/project-settings-page-review/screenshots
```

遗留流程差距见 handoff-prompt.md。本轮 UI 完成不等于项目设置所有行为与网页完全一致。

