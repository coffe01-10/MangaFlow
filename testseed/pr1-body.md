# NUI-6/7 实机验收发现的三组真实缺陷修复

实机验收（真实窗口 + 真实运行后端 + 固定种子数据集）发现、离线回归未能覆盖的三组缺陷，每组均附回归测试。

## 1. 分镜页补齐 web 的「旧版分页数据」警告条
- web `getPageStructureIssue` 在页缺 scene/beat 来源或未覆盖原文时按数量警告并引导回剧本页；原生分镜页缺失。
- 新增 `structureBar`（数量 + 文案 + 「前往漫画剧本」导航）。
- 回归：`NativeStoryboardPageChecks`（legacy fixture：横幅出现/计数正确/重载后消失；健康页保持隐藏）。

## 2. ImageBox 无控件模板 → 真机上全 app 图像空白
- `ImageBox` 曾 `DefaultStyleKeyProperty.OverrideMetadata(typeof(ImageBox))` 指向自身但仓库无 Themes/Generic.xaml 样式 → ContentControl 无模板 → Content 永不渲染。素材库/生成/资产所有缩略图在真机空白，而离线检查只断言 URL 不断言像素，从未暴露。
- 修复：移除该 override（ContentControl 默认模板正常呈现 Content）。
- 回归：`NativeVisualChecks` 断言 ImageBox 布局后视觉子树非空。

## 3. `JsonFields.Number()` 对显式 null 数值字段抛异常
- 真实后端把从未验证连接的 `latency_ms` 序列化为 `null`；`TryGetInt32` 对错误类型抛 `InvalidOperationException`（并非返回 false，它只对数值溢出返回 false），导致设置页「供应商列表读取失败」。
- 修复：`Number()` 增加 `ValueKind is Number` 守卫（`Decimal()` 已有同款守卫）。
- 回归：`NativeSystemSettingsPageChecks` fixture 连接增加 `latency_ms: null`。

## 验证方式

- `dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release` 0 错误。
- `MangaFlow.Native.Tests.exe --render <dir>` 全绿（56 项基础断言 + WPF 导航/视觉检查，含新增回归）。
- `MangaFlow.Native.Tests.exe --storyboard-page <dir>`、`--system-settings-page <dir>` 全绿。
- 实机复验：真实客户端中素材库三张候选图正常渲染（修复前后截图对照）、分镜警告条出现、设置页供应商列表 27 家正常加载。

关联：验收过程与逐格台账见 `nui67/acceptance-tooling`、`nui67/acceptance-report` 分支（文档 PR 说明依赖本 PR 的修复）。
