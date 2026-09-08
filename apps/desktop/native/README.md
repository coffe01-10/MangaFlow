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

- 全局模式/筛选/选项按钮采用矩形轮廓；资产分类为 44 DIP 下划线导航，图像模型为
  68 DIP 两列矩形卡片。项目选择框按名称显示，不再输出 `ProjectItem` 对象文本。

- 首页顶部导航、两行宋体标题、米色纸面、朱红强调色、四项指标。
- 项目封面、竖排标题、制作中印章、采用进度、模式和分辨率。
- 右侧 AI 连接统计及完整生产流程说明。连接数字来自 dashboard API，
  不以本地 API 可达冒充供应商健康。
- 小于 1180 DIP 时右侧面板移到下方；项目封面每页最多 12 个，
  避免大项目列表一次生成过多原生视觉元素。
- 首页/帮助/设置使用顶部导航；进入项目后展示可折叠的项目侧栏。
- 设置、任务、章节、编辑对话框共享纸面色板、直角卡片、字体层级及朱红焦点。
- 项目侧栏包含与网页一致的九个入口，各入口使用独立原生业务视图；折叠后保留
  52 DIP 图标轨。首页、帮助和全局设置隐藏项目侧栏及任务底栏。
- 首页项目卡片和创建入口共享自适应网格；设置采用主表单与诊断侧栏，窄窗口上下排列。
  标题和操作区空间不足时换行，抽屉覆盖整个正文区域并恢复键盘焦点。
- 新建抽屉的模式卡、等宽矩形清晰度选项、92 DIP 标题栏按网页布局重绘。

完整页面清单与阶段计划见 [原生 UI 迁移清单](../../../../docs/native-ui-migration.md)。
执行顺序与完成状态同步维护于 [roadmap](../../../../docs/roadmap.md) 的 WPF 原生客户端
队列；入口可导航不表示业务功能已迁移。

界面使用 WPF 原生控件。现有实现覆盖供应商、资产、剧本、分镜、生成与导演、素材库、
任务和工作流。2026-09-08 补齐生成参考配置、历史批次、PNG 下载、原生选区编辑以及
用量日期/通道筛选和分页；各项实现与实机验收状态仍需分别看待，尚未通过全功能商业发布验收。

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

2026-09-08：构建输出可使用 `-p:OutputPath=bin/Parity/`，随后运行
`apps/desktop/native-tests/bin/Parity/MangaFlow.Native.Tests.exe --render output/native-parity-review`。
新增 940/1320/1600 DIP 卡片、标题和侧栏边界断言，以及真实 WPF 控件事件配合模拟 HTTP
的项目创建、参考选择提交、重复提交保护和局部编辑预览/确认/撤回检查。
用量回归覆盖分页筛选保持、迟到首屏/续页隔离和重复加载拦截。模拟 HTTP 检查不会调用供应商。

2026-09-08 任务中心续作：`NativeJobsChecks` 随 `--render` 运行，覆盖真实 JobRead
字段、历史切换、迟到读写隔离、单项/批量操作去重与失败恢复、空列表刷新、稳定控件、
日期分组和调用明细分页。任务页预览名为 `native-jobs-{940,1320}.png`，调用详情为
`native-job-details.png`；均为样例数据。完整 Release 编译当前有 36 条既有警告、0 错误。

2026-09-07 首次原生实机已验证主窗口启动并连接隔离数据目录的真实 API；
用户接管调试后未继续自动操作窗口。网页风格版本使用离屏渲染验证。

2026-09-08 素材库续作：日期/完整模型筛选、当前页刷新、收藏/撤回/软删除、章节导出门禁
与 PNG ZIP / PDF / JSON 文件保存已接通。批次按候选数跨列，图片保持 3:4；相同响应保留
卡片控件。`NativeLibraryChecks` 随 `--render` 验证游标、迟到响应、重复操作、跨章节定位、
下载中断保护与原生日期输入。预览 `native-library-populated-{700,1060}.png` 使用样例数据；
完整实机功能与帧时间仍待验收。
