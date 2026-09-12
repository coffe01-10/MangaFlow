# 分页与分镜页 · 本轮还原记录

对照网页 `storyboard-section.tsx`、`storyboard-editor/index.tsx`、`storyboard-toolbar.tsx` 和 `panel-inspector.tsx`，调整 WPF 原生工作区。

## 已完成

- 左侧纵向页码栏：页码、格数和待复查状态；窄窗口改为页面下拉框。
- 独立保存状态条、页版本、文字/气泡负载及来源场景/情节拍数量。
- 工具栏、页菜单、可拖动分隔条及可隐藏的导演台；专注模式隐藏页导航和负载摘要。
- 导演台顶部编辑入口、几何只读信息、景别/角度/动作/背景/道具/人物状态，以及独立对白编辑区。
- 初次打开适配整页；保持适配模式时随窗口尺寸调整，手动缩放保留用户选择。
- 阅读序角标绑定到对应分镜格，修正缩放后重叠及位置不跟随的问题。
- 当前页重复点击不再重载；窄屏切页遵循草稿离开确认；无修改时保存、撤销、重做正确禁用。
- 采用实色背景、布局取整和 Display 文本排版，未给正文添加位图缩放动画。

## 验证

以下命令均已通过：

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/storyboard-page-review --storyboard-page
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/storyboard-page-review/regression --storyedit
```

最终构建：0 错误，31 个警告。新增检查覆盖 1320/940/650 DIP 布局、接口字段呈现、整页适配、角标位置、专注模式、导演台折叠、切页拒绝与确认。既有回归覆盖格子缩放、撤销、气泡新增与编辑、409 草稿恢复、出血/安全区、重算和刷新保护。

这些是离屏 WPF 渲染与 HTTP 消息处理器夹具检查，不等同于真实后端联调。未修改后端。

## 截图

- [1320 DIP 工作区](native-storyboard-1320.png)
- [940 DIP 工作区](native-storyboard-940.png)
- [650 DIP 工作区](native-storyboard-650.png)

截图使用独立测试数据，只含本页内容区域，不包含主窗口侧边栏和标题栏。

## 尚未验收

- 本格完整属性编辑仍复用原生编辑对话框，网页使用内联编辑；这部分交互还不是逐像素一致。
- 真实窗口的高 DPI 字体表现、窗口缩放与滚动帧率：NOT RUN。
- 本轮未执行真实后端、数据库或模型供应商联调；不声明全项目功能迁移完成。
