# MangaFlow 原生 Windows 客户端

WPF / C# 界面，复用 FastAPI API 与 Rust shell-core。当前为迁移中的原生版本，
不依赖 WebView、Node 或网页渲染。设计及未迁移边界见
[原生客户端 ADR](../../../../docs/adr/native-windows-client.md)。

## 启动

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File apps/desktop/scripts/start-native.ps1
```

需要 .NET 8 SDK、Rust，以及现有 `.venv-desktop` Python 环境。默认数据目录为
`%LOCALAPPDATA%/MangaFlow/Native`，不会自动搬迁旧客户端数据。
原生客户端尚非可独立分发的安装包，发布前须升级受支持的 .NET LTS 并验证打包。

## 与网页一致的界面

- 首页顶部导航、两行宋体标题、米色纸面、朱红强调色、四项指标。
- 项目封面、竖排标题、制作中印章、采用进度、模式和分辨率。
- 右侧 AI 连接统计及完整生产流程说明。连接数字来自 dashboard API，
  不以本地 API 可达冒充供应商健康。
- 小于 1180 DIP 时右侧面板移到下方；项目封面每页最多 12 个，
  避免大项目列表一次生成过多原生视觉元素。
- 首页/帮助/设置使用顶部导航；进入项目后展示可折叠的项目侧栏。
- 设置、任务、章节、编辑对话框共享纸面色板、直角卡片、字体层级及朱红焦点。

界面是原生控件重绘，不是网页像素级嵌入。供应商编辑、资产编辑、分镜画布、导演、
生成审阅等功能尚未迁移；完整功能仍由旧工作台承接。

## 验证

```powershell
$env:MSBuildEnableWorkloadResolver = 'false'
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release -p:OutputPath=bin/WebAligned/
& apps/desktop/native-tests/bin/WebAligned/MangaFlow.Native.Tests.exe --render output/native-ui-review
```

离屏预览使用明确的样例项目，仅用于视觉检查，不连接 API、不操作现有窗口。
验证覆盖 URL 边界、版本冲突、取消读取、写操作不自动重试、窗口偏好、
分页首尾/空页、Unicode 竖排标题及 1320/940 DIP 原生 XAML 实际布局。
编译和离屏测试不代表真实供应商验收或已达到特定 FPS。

2026-09-07 首次原生实机已验证主窗口启动并连接隔离数据目录的真实 API；
用户接管调试后未继续自动操作窗口。网页风格版本使用离屏渲染验证。
