# 流程编排页面还原记录

2026-09-13。本轮范围为 WPF 编辑器布局、局部深色主题和客户端交互回归。参考网页 workflow-studio.tsx 与 workflow-studio.module.css；没有修改后端或发起真实模型调用。

## 本轮改动

- 顶部操作顺序对齐网页：导出、导入、保存、校验、发布，使用直角按钮和矢量线性图标。
- 分开显示草稿版本、发布状态、保存状态和校验问题；未知状态显示“未校验”，不会伪造已通过或发布版本号。
- 宽窗口采用 238 DIP 节点库、弹性画布、286 DIP 属性面板；画布工具移入中央区域，加入点阵背景和缩放控件。
- 低于 980 DIP 的窗口默认收起侧栏；可打开节点库或属性抽屉，抽屉切换保留输入框实例及编辑内容。
- 属性文本框、下拉框、滚动区使用局部深色主题，数字字段与其他表单字段等宽；修正滚动条交汇处的白色背景。
- 底部范围选择和运行操作支持换行；复制、撤销、重做及节点运行按钮反映当前选中与历史状态。
- 修复节点视觉对象重复 Loaded 时重复插入顶部色条的问题，避免重新挂载时重复添加同一元素。
- 保留既有运行历史与审批队列，不改草稿保存队列、连线契约或运行端点。

## 验证

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll --workflow-page output/workflow-page-review/verified
```

最终构建成功，0 错误、31 警告。专项检查通过：

- 1440/1100/940/650 DIP 布局、画布空间、工具栏边界、侧栏展开互斥。
- 侧栏切换保留编辑器、属性区深色输入、滚动至底部、100% 缩放。
- 复制/撤销/重做和按钮状态，编辑后的草稿请求确实包含修改内容。
- 节点反复 Loaded 不重复添加色条。
- NativeWorkflowChecks：离开时冲刷防抖保存。
- NativeWorkflowConnectionChecks：连线类型、自连、重复连线、边 ID 与复制隔离。
- NativeWorkflowRunChecks：配置项、值域写回、运行历史、审批载荷及失效模型禁用。
- NativeIssue427Checks：乱序载入、A→B→C 切换期间草稿归属、激活只载入一次。

这些检查使用离屏 WPF 与模拟 HTTP 响应。未运行真实 API/Worker/供应商端到端验收、物理鼠标拖拽、高 DPI 实机和帧率测试。

## 剩余差距

本页还不是网页所有功能的 1:1 复刻。发布版本列表/恢复、页面所属章节选择、第二个默认模板和失败运行重试尚待补齐；小地图、框选多节点、画布手势与全屏工作区外壳也未完整还原。保存失败后的调用链存在需要复现修复的风险。

按用户要求，流程问题未在本轮自行改动，已整理为 [交接提示词](handoff-prompt.md)。

## 原生预览

- [宽窗口](verified/native-workflow-1440.png)
- [1100 DIP](verified/native-workflow-1100.png)
- [940 DIP](verified/native-workflow-940.png)
- [650 DIP](verified/native-workflow-650.png)
- [节点属性编辑](verified/native-workflow-inspector.png)
- [节点库抽屉](verified/native-workflow-library-drawer.png)
