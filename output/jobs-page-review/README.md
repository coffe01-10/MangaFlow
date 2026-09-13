# 任务中心原生页面还原记录

2026-09-13。本轮只修改 WPF 任务中心及客户端检查；没有修改后端、数据库或供应商调用逻辑。

## 已完成

- 对照网页 jobs-section.tsx 和用户提供的任务中心截图，调整标题、计数、近期/历史切换、顶部批量归档按钮。
- 失败任务使用默认展开的红边分组；结束任务按本地日期倒序排列，日期分组默认折叠。
- 任务类型与状态上下排列，展示模型、进度、耗时、费用状态、错误原因；保留调用与成本详情入口。
- 操作按钮使用直角边框和原生矢量图标；静止文字不依赖缩放或位移动画。禁用、悬停和键盘焦点有独立反馈。
- 1240/940 DIP 使用多列任务行，650/360 DIP 堆叠详情和操作；标题和工具栏随宽度换行。
- 勾选直接更新顶部计数，不重建任务行；轮询数据改变时保留分组展开状态，数据不变时保留控件。
- 查看结果使用独立按钮，避免点击行内其他控件时冒泡打开图片。

## 验证

最终执行：

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll --jobs-page output/jobs-page-review/verified
git diff --check
```

构建成功：0 错误，16 警告。专项检查通过：

- 四种宽度的任务行边界与内容重叠检查。
- 勾选任务后顶部按钮计数、控件实例保留、批量归档请求中的任务 ID。
- 数据更新后的失败分组折叠、日期分组展开状态保留。
- 历史请求 archived=true、隐藏批量按钮、恢复入口及禁止历史任务重试。
- 历史空状态、读取错误与重试入口。
- 既有 NativeJobsChecks：接口字段解析、费用未知值、晚到读写响应隔离、归档请求去重与失败恢复、轮询、调用账本分页和关闭后的响应隔离。

截图由离屏 WPF 和模拟 HTTP 响应生成，不是正在运行的用户项目；截图里的权限错误是测试数据。已目视检查宽版、窄版和历史页。

未验收：真实后端/Worker/模型服务、真实取消与付费重试、操作系统弹窗点击流程、结果图片加载、完整桌面窗口、高 DPI 和动画帧率。不能据此宣称所有网页功能或像素级 1:1 已完成。

## 截图

- [宽版](verified/native-jobs-page-1240.png)
- [中等宽度](verified/native-jobs-page-940.png)
- [窄版](verified/native-jobs-page-650.png)
- [最窄布局](verified/native-jobs-page-360.png)
- [日期展开](verified/native-jobs-page-expanded.png)
- [历史记录](verified/native-jobs-page-history.png)
- [空状态](verified/native-jobs-page-empty.png)
- [读取失败](verified/native-jobs-page-error.png)
- [调用与成本详情](verified/native-job-details.png)
