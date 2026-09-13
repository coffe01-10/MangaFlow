# 单页生成页 · 本轮还原记录

对照网页 `generate-section.tsx`、`shared.tsx` 与 `production-readiness.tsx` 调整 WPF 抽卡工作区，保留原生控件和现有后端接口。

## 已完成

- 页标题、居中抽卡/导演切换、方形数字页码、紧邻的批次工具条。
- 上一批、下一批、批次下拉框和新批次入口。新批次使用已有的 POST pages/{id}/batches，不触发模型生成。
- 剧本变更提示与精确跳转、原文摘要、文字/格数/气泡负载。
- 独立生产准备状态、阻塞项跳转及可展开的原文/供应商/执行器诊断。
- 双列矩形模型卡、原生选中标记与模型记忆；窄窗口自动改为单列。
- 正式模型和本次规格信息、参考配置、候选图比例与候选操作区布局。
- 历史批次禁用生成，并在提交入口再次拦截；新批次防重复提交、失败后恢复及跨页返回保护。
- 当前页重复点击不重载；模型切换保留诊断展开状态；正文保持实色背景和 Display 文本排版。

## 验证结果

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/generate-page-review/verified --generate-page
```

最终增量构建：0 错误，15 个警告；本轮包含原生项目重编译时为 31 个警告。

专项回归通过：宽窄布局、摘要字段、变更提示跳转、模型保存、生成请求的模型/1K/分镜版本参数、历史批次拦截、新批次去重/失败恢复/跨页返回、阻塞禁用、候选预览比例与完整操作区。

该入口同时运行既有 NativeInspectionChecks，检查结果版本过滤、逐项修复参数、任务终态刷新、错误与迟到响应保护均通过。测试偏好文件已清理。

离屏测试根节点不在真实窗口中，刷新后需主动重测整棵测试树；测试已补足重测并断言候选区不被旧高度裁剪。

## 最终截图

- [宽窗口](verified/native-generate-1240.png)
- [窄窗口](verified/native-generate-650.png)
- [存在阻塞项](verified/native-generate-blocked.png)
- [候选状态与操作区](verified/native-generate-candidates.png)

截图使用独立测试数据。候选图为空是失败/排队状态夹具，不是一次真实生图结果。

## 验收边界

- 本轮验证是离屏 WPF + HTTP 消息处理器夹具，真实后端、数据库、模型供应商联调 NOT RUN。
- 真实窗口高 DPI 字体表现和滚动/动画帧率 NOT RUN。
- 导演子页、场景继承专用卡仍需继续逐项还原，不声明本页所有子流程或全项目已经一比一迁移完成。
- 未修改后端或供应商配置。
