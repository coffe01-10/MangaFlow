# 漫画剧本页还原记录

本轮对照网页 `script-section.tsx`、`script-editor.tsx`、`scene-picker.tsx` 和页面样式调整原生 WPF 剧本页，未修改后端。

## 界面与交互

- 标题改为网页的中英索引和主标题，右侧章节选择与场景数；覆盖统计采用绿色左边线，导演修订提示采用橙色左边线。
- 场景卡按编号、地点/时间、场景目的、编辑按钮排列；窄窗口将场景目的移到下一行。情绪线单独成带。
- 场景资产和环境变体改为有标签的全宽控件；选择资产后更新变体列表，排除归档变体。新增“从地点创建场景资产”，真实调用创建与绑定接口，保留原地点文本。
- 服装指定横向排列并自动换行；场景调度单采用三列基本字段，情节拍表单采用两列说话人/情绪及对白/旁白，窄窗口自动收为单列。
- 修正情节拍动作正文和编号挤在同一列的问题。覆盖信息读取 `coverage.ratio/covered/expected`，来源段数读取 `source_range.segment_ids`。
- 保存场景/情节拍保留现有版本号校验与防重复提交；409 错误在页内显示并保留输入。新增场景创建和绑定按钮的重复点击及已离开页面控件保护。
- 实色底、Display 文本排版及布局像素对齐，未为文字容器增加缩放/透明度动画。

为保留当前原生客户端已经验证过的服装全量映射保存行为，绑定和服装修改后仍出现显式保存按钮；网页使用下拉变更即保存。这一交互差异尚未消除，不宣称本页所有细节已达到像素级或流程完全一致。

## 验证

```powershell
dotnet build apps/desktop/native-tests/MangaFlow.Native.Tests.csproj -c Release --no-restore --nologo -v quiet -clp:ErrorsOnly
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/script-page-review --script
dotnet apps/desktop/native-tests/bin/Release/net8.0-windows/MangaFlow.Native.Tests.dll output/script-page-review/regression --storyedit
```

最终构建 0 错误、31 警告。新增剧本页检查和既有编辑回归通过，覆盖宽窄布局、覆盖/来源结构、正文分列、变体更新与归档过滤、绑定防重、版本保存、409 后保留输入、刷新保留草稿、从地点创建并绑定。

另行使用实际 native-host / Python sidecar 及独立临时 SQLite 验证，15 次真实 HTTP 检查通过：项目创建、原文导入、章节读取、剧本嵌套结构读取、场景修改及过期版本 409、情节拍修改及 409、来源区间不被编辑请求改变、场景资产创建、绑定、资产列表、服装映射保存、解绑、删除剧本与空剧本读取。为避免调用付费文字模型，场景和情节拍样例仅预置到该次可丢弃测试数据库；模型解析不属于本次验收。宿主正常退出，临时用户目录、数据库和脚本均已清理。

## 截图

- [宽窗口](native-script-1100.png)
- [窄窗口](native-script-650.png)
- [场景调度单](native-script-scene-edit.png)
- [情节拍修订](native-script-beat-edit.png)

截图为离屏 WPF 测试样例，不是用户实际项目。未完成真实窗口鼠标逐项验收、各显示器 DPI/字体效果验收或动画帧率测量，未调用图片或文字生成供应商。
