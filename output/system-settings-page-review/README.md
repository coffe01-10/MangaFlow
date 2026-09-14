# 系统设置与运行诊断：本轮 UI 还原

日期：2026-09-14；基线 d11d55b143c87c7dc0263b9b0000fb70c88c63ef。

对照网页 settings/page.tsx、provider-management.tsx 与 globals.css，本轮调整：
- 顶部固定系统标题、用量看板、返回项目、保存入口。
- 五项运行状态，长路径单行省略并可悬停查看；窄屏两列。
- 供应商搜索与矩形筛选按钮、跳到结果、名称/连接计数自适应排版。
- 运行参数双列表格，窄屏单列；文字与输入区对齐。
- 右侧分层诊断和本地存储，低于 1280 DIP 转移到主表单下方。长消息和路径不再横向撑破卡片。
- 连接状态标记改直角；密钥、手工模型表单以及模型操作行适配窄窗口。
- 本地存储卡数据库值绑定到实际 database_backend 字段。

验证命令：

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll --system-settings-page output/system-settings-page-review/screenshots
```

结果：构建 0 errors（仍有 warnings）；专项检查通过。覆盖 1440/1240/760/360 DIP、真实 WPF 控件离屏绘制、非空模型目录、图片类型筛选、手工表单边界、运行设置 PATCH 字段与版本、数值取整、在途去重、保存/冲突反馈、诊断重试和导航回调。同时复用 #469 SettingsRefreshDraftChecks（密码与运行参数草稿的 F5 保护）以及 NativeConsistencyChecks.SettingsPublishChecks（轮询间隔发布）。

screenshots/ 保存最终截图。测试只使用 HTTP fixture，不读取真实凭据，不修改用户项目、不调用供应商。未验收真实后端持久化、供应商连接操作、实体窗口高 DPI 和动画帧率。本轮未修改后端，也没有声称网页所有功能已全部还原。

遗留流程差距和后续提示词见 handoff-prompt.md；下一页可继续用量与成本看板。

